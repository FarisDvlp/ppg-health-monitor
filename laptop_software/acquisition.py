"""Serial acquisition module for the laptop-side PPG application.

Expected input: one JSON object per serial line, matching the ESP32 firmware:
    {"t": 939567, "ir": 38875, "red": 29989, "f": 1}

Responsibilities:
- Open/read the serial port on a background thread.
- Parse and validate PPG messages.
- Add host receive timestamps.
- Publish samples to a bounded sample queue.
- Publish infrequent status/error messages to a separate event queue.

This module does NOT write CSV files, draw waveforms, or assess signal quality.
Those responsibilities belong in separate modules.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


UINT32_MAX = 0xFFFFFFFF
DEFAULT_SAMPLE_QUEUE_SIZE = 5000


@dataclass(frozen=True)
class PPGSample:
    """One validated PPG sample from the device plus host receive timestamps.

    timestamp_ms:
        Milliseconds from the device firmware (not necessarily wall-clock time).
    host_timestamp_s:
        Host monotonic timestamp in seconds, captured when readline() returns.
        Useful for inter-arrival timing; do not interpret it as Unix time.
    host_timestamp_utc:
        Human-readable UTC receipt time captured by the laptop.
    """

    timestamp_ms: int
    ir_value: int
    red_value: int
    finger_status: int
    host_timestamp_s: float
    host_timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        """Return the sample as a dictionary for downstream modules."""
        return asdict(self)


def _parse_uint(value: Any, field_name: str) -> int:
    """Accept non-negative integers (or digit strings), rejecting bool/floats."""
    if isinstance(value, bool):
        raise ValueError(f"Field '{field_name}' must be an integer, not bool.")

    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise ValueError(f"Field '{field_name}' must be a non-negative integer.")

    if not 0 <= result <= UINT32_MAX:
        raise ValueError(f"Field '{field_name}' is outside the uint32 range.")
    return result


def parse_ppg_line(
    line: str,
    *,
    host_timestamp_s: Optional[float] = None,
    host_timestamp_utc: Optional[str] = None,
) -> Tuple[Optional[PPGSample], str]:
    """Parse one JSON line.

    Returns:
        (sample, "valid") when the line contains a valid PPG sample.
        (None, "ignored") for blank lines or ordinary debug/startup text.
        (None, "invalid") for malformed JSON-like data or invalid fields.

    The JSON keys t, ir, red, and f match the ESP32 main.cpp supplied earlier.
    Host timestamps can be passed in by SerialReader so they are captured before
    JSON parsing and queueing. If omitted, they are generated during parsing.
    """
    stripped = line.strip()
    if not stripped:
        return None, "ignored"

    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        # Plain-text ESP32 debug messages are ignored. A line starting with '{'
        # was likely intended as JSON, so malformed content is counted invalid.
        if stripped.startswith("{"):
            return None, "invalid"
        return None, "ignored"

    if not isinstance(payload, dict):
        return None, "invalid"

    required_fields = ("t", "ir", "red", "f")
    if any(field not in payload for field in required_fields):
        return None, "invalid"

    try:
        timestamp_ms = _parse_uint(payload["t"], "t")
        ir_value = _parse_uint(payload["ir"], "ir")
        red_value = _parse_uint(payload["red"], "red")
        finger_status = _parse_uint(payload["f"], "f")
    except ValueError:
        return None, "invalid"

    if finger_status not in (0, 1):
        return None, "invalid"

    if host_timestamp_s is None:
        host_timestamp_s = time.monotonic()
    if host_timestamp_utc is None:
        host_timestamp_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    return PPGSample(
        timestamp_ms=timestamp_ms,
        ir_value=ir_value,
        red_value=red_value,
        finger_status=finger_status,
        host_timestamp_s=host_timestamp_s,
        host_timestamp_utc=host_timestamp_utc,
    ), "valid"


class SerialReader:
    """Read PPG serial data on a non-blocking background thread.

    Queue interface for the future main.py / GUI:
        reader.samples -> PPGSample objects (bounded queue, max size 5000 by default)
        reader.events  -> status/error/stopped dictionaries (rare control events)

    When the sample queue is full, the newest sample is dropped rather than
    blocking serial reading. This avoids unbounded RAM growth and prevents a
    slow GUI from indefinitely blocking the serial reader. The number of drops
    is exposed through get_stats().

    Example:
        reader = SerialReader("COM3", baud_rate=115200)
        reader.start()

        # In a GUI timer callback, drain reader.events for status messages and
        # reader.samples for PPGSample instances. Avoid long work in that callback.

        # To stop: reader.stop(); reader.join(timeout=1.0)
    """

    def __init__(
        self,
        port: str,
        baud_rate: int = 115200,
        timeout: float = 0.2,
        sample_queue_size: int = DEFAULT_SAMPLE_QUEUE_SIZE,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("A serial port is required, e.g. 'COM3'.")
        if isinstance(baud_rate, bool) or not isinstance(baud_rate, int) or baud_rate <= 0:
            raise ValueError("baud_rate must be a positive integer.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        if (
            isinstance(sample_queue_size, bool)
            or not isinstance(sample_queue_size, int)
            or sample_queue_size <= 0
        ):
            raise ValueError("sample_queue_size must be a positive integer.")

        self.port = port.strip()
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.samples: "queue.Queue[PPGSample]" = queue.Queue(maxsize=sample_queue_size)
        # Only rare control messages go here, not every sample.
        self.events: "queue.Queue[Dict[str, str]]" = queue.Queue()

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._stats_lock = threading.Lock()
        self._stats = {
            "valid_samples": 0,
            "samples_enqueued": 0,
            "samples_dropped_queue_full": 0,
            "ignored_lines": 0,
            "invalid_lines": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, int]:
        """Return a thread-safe snapshot of receive statistics."""
        with self._stats_lock:
            return dict(self._stats)

    def start(self) -> None:
        """Start serial acquisition on a daemon background thread."""
        if self.is_running:
            raise RuntimeError("Serial acquisition is already running.")

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="PPGSerialReader",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Request the reader to stop after the current serial read timeout."""
        self._stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for the reader thread to finish if it has been started."""
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _emit(self, event_type: str, message: str) -> None:
        self.events.put({"type": event_type, "message": message})

    def _increment_stat(self, name: str) -> None:
        with self._stats_lock:
            self._stats[name] += 1

    def _run(self) -> None:
        self._emit("status", f"Connecting to {self.port} at {self.baud_rate} baud...")

        # Lazy import allows parser tests to run even if PySerial is not installed.
        try:
            import serial
        except ImportError:
            self._emit(
                "error",
                "PySerial is not installed. Run: python -m pip install pyserial",
            )
            self._emit("stopped", "Serial acquisition stopped.")
            return

        try:
            with serial.Serial(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.timeout,
            ) as serial_port:
                # Clear bytes buffered before this acquisition session begins.
                serial_port.reset_input_buffer()
                self._emit("status", f"Connected to {self.port}. Waiting for PPG data...")

                while not self._stop_event.is_set():
                    raw_line = serial_port.readline()
                    if not raw_line:
                        # A serial timeout is normal when no complete line arrives.
                        continue

                    # Capture host receipt timestamps immediately after readline()
                    # and before decoding/parsing or placing the sample in a queue.
                    received_monotonic = time.monotonic()
                    received_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    line = raw_line.decode("utf-8", errors="replace")
                    sample, parse_status = parse_ppg_line(
                        line,
                        host_timestamp_s=received_monotonic,
                        host_timestamp_utc=received_utc,
                    )

                    if parse_status == "valid" and sample is not None:
                        self._increment_stat("valid_samples")
                        try:
                            self.samples.put_nowait(sample)
                        except queue.Full:
                            # Explicit policy: drop the newest sample and count it.
                            self._increment_stat("samples_dropped_queue_full")
                        else:
                            self._increment_stat("samples_enqueued")
                    elif parse_status == "invalid":
                        self._increment_stat("invalid_lines")
                    else:
                        self._increment_stat("ignored_lines")

        except (serial.SerialException, OSError, ValueError) as exc:
            self._emit(
                "error",
                f"Serial connection failed for {self.port}: {exc}. Check the port, "
                "cable, baud rate, and whether another application is using the port.",
            )
        finally:
            self._emit("stopped", "Serial acquisition stopped.")
