"""Subject metadata and CSV recording for the laptop-side PPG application.

Responsibilities:
- Validate subject ID and age supplied by the GUI (or another caller).
- Create a unique, safely named CSV file for one recording session.
- Save validated PPGSample objects provided by acquisition.SerialReader.

This module does not open the serial port, build the GUI, draw waveforms,
or assess signal quality.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

from acquisition import PPGSample


CSV_COLUMNS = (
    "timestamp_ms",
    "host_timestamp_utc",
    "host_timestamp_s",
    "ir_value",
    "red_value",
    "finger_status",
)


class SubjectInfoError(ValueError):
    """Raised when subject metadata is missing or invalid."""


class RecordingError(RuntimeError):
    """Raised when a CSV recording operation cannot be completed."""


def _safe_filename_part(value: str) -> str:
    """Replace characters that are unsafe in common operating systems."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", value.strip())
    cleaned = cleaned.strip(" ._")
    # Windows reserves some device names even when an extension is added.
    if cleaned.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        cleaned = f"subject_{cleaned}"
    return cleaned


class SubjectInfo:
    """Validated subject metadata entered through the application's GUI.

    Use a coded study ID rather than a subject's name or other direct
    identifying information. Age is kept for the session filename and is not
    repeated in every CSV sample row.
    """

    __slots__ = ("subject_id", "age_years")

    def __init__(self, subject_id: str, age_years: int) -> None:
        clean_id = subject_id.strip() if isinstance(subject_id, str) else ""
        if not clean_id:
            raise SubjectInfoError("Subject ID cannot be empty.")

        safe_id = _safe_filename_part(clean_id)
        if not safe_id:
            raise SubjectInfoError("Subject ID contains no usable filename characters.")

        if isinstance(age_years, bool) or not isinstance(age_years, int):
            raise SubjectInfoError("Age must be a whole number of years.")
        if not 1 <= age_years <= 120:
            raise SubjectInfoError("Age must be between 1 and 120 years.")

        self.subject_id = clean_id
        self.age_years = age_years

    @classmethod
    def from_inputs(cls, subject_id: str, age_input: str) -> "SubjectInfo":
        """Build SubjectInfo from raw GUI text-field values."""
        if not isinstance(age_input, str) or not age_input.strip().isdigit():
            raise SubjectInfoError("Age must be entered as a whole number.")
        return cls(subject_id, int(age_input.strip()))

    @property
    def filename_id(self) -> str:
        return _safe_filename_part(self.subject_id)


class CSVRecorder:
    """Write PPG samples to a unique CSV file for a recording session.

    Typical integration from main.py:
        subject = SubjectInfo.from_inputs(subject_id_text, age_text)
        recorder = CSVRecorder(output_dir="data_ppg")
        path = recorder.start(subject)
        # For each sample drained from SerialReader.samples:
        recorder.write_sample(sample)
        # When recording ends:
        recorder.stop()

    CSV rows include device timestamp and both host receipt timestamps. Subject
    ID and age are included in the filename, not repeated in every row.
    """

    def __init__(self, output_dir: str | Path = "data_ppg", flush_every: int = 50) -> None:
        if isinstance(flush_every, bool) or not isinstance(flush_every, int) or flush_every < 1:
            raise ValueError("flush_every must be a positive integer.")

        self.output_dir = Path(output_dir)
        self.flush_every = flush_every
        self.filepath: Optional[Path] = None
        self._file = None
        self._writer = None
        self._samples_since_flush = 0
        self.samples_written = 0
        self._lock = Lock()

    @property
    def is_recording(self) -> bool:
        return self._file is not None and not self._file.closed

    def _make_unique_path(self, subject: SubjectInfo) -> Path:
        now = datetime.now().astimezone()
        stamp = now.strftime("%Y%m%d_%H%M%S_%f")
        base_name = f"PPG_{subject.filename_id}_age{subject.age_years}_{stamp}"
        candidate = self.output_dir / f"{base_name}.csv"
        suffix = 1
        while candidate.exists():
            candidate = self.output_dir / f"{base_name}_{suffix:02d}.csv"
            suffix += 1
        return candidate

    def start(self, subject: SubjectInfo) -> Path:
        """Create the CSV file and write its header; return the file path."""
        if not isinstance(subject, SubjectInfo):
            raise TypeError("subject must be a SubjectInfo instance.")

        with self._lock:
            if self.is_recording:
                raise RecordingError("A recording session is already active.")

            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                path = self._make_unique_path(subject)
                # Exclusive creation prevents an existing file from being overwritten.
                csv_file = path.open("x", newline="", encoding="utf-8-sig")
                writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
                writer.writeheader()
                csv_file.flush()
            except OSError as exc:
                raise RecordingError(f"Unable to create CSV file: {exc}") from exc

            self.filepath = path
            self._file = csv_file
            self._writer = writer
            self._samples_since_flush = 0
            self.samples_written = 0
            return path

    def write_sample(self, sample: PPGSample) -> None:
        """Append one PPG sample to the active session."""
        if not isinstance(sample, PPGSample):
            raise TypeError("sample must be a PPGSample from acquisition.py.")

        with self._lock:
            if not self.is_recording or self._writer is None:
                raise RecordingError("Cannot write a sample: no active recording.")

            row = {
                "timestamp_ms": sample.timestamp_ms,
                "host_timestamp_utc": sample.host_timestamp_utc,
                "host_timestamp_s": f"{sample.host_timestamp_s:.9f}",
                "ir_value": sample.ir_value,
                "red_value": sample.red_value,
                "finger_status": sample.finger_status,
            }

            try:
                self._writer.writerow(row)
                self.samples_written += 1
                self._samples_since_flush += 1
                if self._samples_since_flush >= self.flush_every:
                    self._file.flush()
                    self._samples_since_flush = 0
            except (OSError, csv.Error) as exc:
                raise RecordingError(f"Unable to write CSV sample: {exc}") from exc

    def flush(self) -> None:
        """Flush buffered CSV data to the operating system."""
        with self._lock:
            if self.is_recording:
                try:
                    self._file.flush()
                    self._samples_since_flush = 0
                except OSError as exc:
                    raise RecordingError(f"Unable to flush CSV data: {exc}") from exc

    def stop(self) -> Optional[Path]:
        """Flush and close the current file; return its path if one was open."""
        with self._lock:
            if not self.is_recording:
                return self.filepath

            path = self.filepath
            try:
                self._file.flush()
                self._file.close()
            except OSError as exc:
                raise RecordingError(f"Unable to close CSV recording cleanly: {exc}") from exc
            finally:
                self._file = None
                self._writer = None
                self._samples_since_flush = 0
            return path

    def __enter__(self) -> "CSVRecorder":
        """Allow recorder resource management with a context manager."""
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop()
