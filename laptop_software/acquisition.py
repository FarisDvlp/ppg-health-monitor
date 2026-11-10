"""Serial acquisition for simultaneous MAX30102 PPG and AD8232 ECG.

Accepted lines:
  PPG JSON (legacy-compatible): {"t": 1234, "ir": 32000, "red": 30000, "f": 1}
  ECG batch: E,t0_us,n,raw0,flags0,dt1,raw1,flags1,...

The ECG batch records the first device timestamp plus actual timestamp deltas
(in microseconds) for subsequent samples. The GUI can continue consuming
reader.samples for PPG and consume reader.ecg_samples for ECG.
"""
from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

UINT32_MAX = 0xFFFFFFFF
UINT64_MAX = 0xFFFFFFFFFFFFFFFF
DEFAULT_SAMPLE_QUEUE_SIZE = 5000
DEFAULT_ECG_QUEUE_SIZE = 20000


def _parse_uint(value: Any, field_name: str, maximum: int = UINT32_MAX) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Field '{field_name}' must be an integer, not bool.")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise ValueError(f"Field '{field_name}' must be a non-negative integer.")
    if not 0 <= result <= maximum:
        raise ValueError(f"Field '{field_name}' is outside the allowed range.")
    return result


@dataclass(frozen=True)
class PPGSample:
    timestamp_ms: int
    ir_value: int
    red_value: int
    finger_status: int
    host_timestamp_s: float
    host_timestamp_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ECGSample:
    """One raw AD8232 ADC reading; lead_off bit 0=LO+, bit 1=LO-."""
    timestamp_us: int
    raw_value: int
    lead_off: int

    @property
    def lo_plus_off(self) -> int:
        return self.lead_off & 0x01

    @property
    def lo_minus_off(self) -> int:
        return (self.lead_off >> 1) & 0x01

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_ppg_line(
    line: str,
    *,
    host_timestamp_s: Optional[float] = None,
    host_timestamp_utc: Optional[str] = None,
) -> Tuple[Optional[PPGSample], str]:
    stripped = line.strip()
    if not stripped:
        return None, "ignored"
    # Compact CSV-style line used by the dual-sensor firmware:
    # P,timestamp_ms,raw_ir,raw_red,finger_present
    if stripped.startswith("P,"):
        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) != 5 or parts[0] != "P":
            return None, "invalid"
        try:
            timestamp_ms = _parse_uint(parts[1], "t")
            ir_value = _parse_uint(parts[2], "ir")
            red_value = _parse_uint(parts[3], "red")
            finger_status = _parse_uint(parts[4], "f")
        except ValueError:
            return None, "invalid"
    else:
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            if stripped.startswith("{"):
                return None, "invalid"
            return None, "ignored"
        if not isinstance(payload, dict) or any(k not in payload for k in ("t", "ir", "red", "f")):
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
    return PPGSample(timestamp_ms, ir_value, red_value, finger_status,
                     host_timestamp_s, host_timestamp_utc), "valid"


def parse_ecg_batch_line(line: str) -> Tuple[Optional[List[ECGSample]], str]:
    """Parse an E batch line emitted by main.cpp."""
    stripped = line.strip()
    if not stripped or not stripped.startswith("E,"):
        return None, "ignored"
    try:
        parts = [part.strip() for part in stripped.split(",")]
        if len(parts) < 5 or parts[0] != "E":
            return None, "invalid"
        t0_us = _parse_uint(parts[1], "t0_us", UINT64_MAX)
        count = _parse_uint(parts[2], "count", 1000)
        if count < 1 or len(parts) != 5 + 3 * (count - 1):
            return None, "invalid"

        raw0 = _parse_uint(parts[3], "raw0", 4095)
        flags0 = _parse_uint(parts[4], "flags0", 3)
        samples: List[ECGSample] = [ECGSample(t0_us, raw0, flags0)]
        timestamp_us = t0_us
        index = 5
        for _ in range(1, count):
            delta_us = _parse_uint(parts[index], "delta_us", 5_000_000)
            raw_value = _parse_uint(parts[index + 1], "raw_value", 4095)
            flags = _parse_uint(parts[index + 2], "flags", 3)
            if delta_us == 0:
                return None, "invalid"
            timestamp_us += delta_us
            if timestamp_us > UINT64_MAX:
                return None, "invalid"
            samples.append(ECGSample(timestamp_us, raw_value, flags))
            index += 3
        return samples, "valid"
    except (ValueError, IndexError):
        return None, "invalid"


class SerialReader:
    """Read both data streams in one background thread without blocking the GUI."""
    def __init__(
        self,
        port: str,
        baud_rate: int = 115200,
        timeout: float = 0.2,
        sample_queue_size: int = DEFAULT_SAMPLE_QUEUE_SIZE,
        ecg_sample_queue_size: int = DEFAULT_ECG_QUEUE_SIZE,
    ) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("A serial port is required, e.g. 'COM6'.")
        if isinstance(baud_rate, bool) or not isinstance(baud_rate, int) or baud_rate <= 0:
            raise ValueError("baud_rate must be a positive integer.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        if sample_queue_size <= 0 or ecg_sample_queue_size <= 0:
            raise ValueError("Queue sizes must be positive integers.")

        self.port = port.strip()
        self.baud_rate = baud_rate
        self.timeout = timeout
        self.samples: "queue.Queue[PPGSample]" = queue.Queue(maxsize=sample_queue_size)
        self.ecg_samples: "queue.Queue[ECGSample]" = queue.Queue(maxsize=ecg_sample_queue_size)
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
            "valid_ecg_samples": 0,
            "ecg_samples_enqueued": 0,
            "ecg_samples_dropped_queue_full": 0,
            "invalid_ecg_lines": 0,
        }

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def start(self) -> None:
        if self.is_running:
            raise RuntimeError("Serial acquisition is already running.")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="DualSerialReader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _emit(self, event_type: str, message: str) -> None:
        self.events.put({"type": event_type, "message": message})

    def _increment_stat(self, name: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[name] += amount

    def _run(self) -> None:
        self._emit("status", f"Connecting to {self.port} at {self.baud_rate} baud...")
        try:
            import serial
        except ImportError:
            self._emit("error", "PySerial is not installed. Run: python -m pip install pyserial")
            self._emit("stopped", "Serial acquisition stopped.")
            return

        try:
            with serial.Serial(self.port, self.baud_rate, timeout=self.timeout) as serial_port:
                serial_port.reset_input_buffer()
                self._emit("status", f"Connected to {self.port}. Waiting for PPG and ECG data...")
                while not self._stop_event.is_set():
                    raw_line = serial_port.readline()
                    if not raw_line:
                        continue
                    received_monotonic = time.monotonic()
                    received_utc = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                    line = raw_line.decode("utf-8", errors="replace").strip()

                    if line.startswith("E,"):
                        ecg_batch, parse_status = parse_ecg_batch_line(line)
                        if parse_status == "valid" and ecg_batch is not None:
                            self._increment_stat("valid_ecg_samples", len(ecg_batch))
                            for sample in ecg_batch:
                                try:
                                    self.ecg_samples.put_nowait(sample)
                                except queue.Full:
                                    self._increment_stat("ecg_samples_dropped_queue_full")
                                else:
                                    self._increment_stat("ecg_samples_enqueued")
                        else:
                            self._increment_stat("invalid_ecg_lines")
                            self._increment_stat("invalid_lines")
                        continue

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
                            self._increment_stat("samples_dropped_queue_full")
                        else:
                            self._increment_stat("samples_enqueued")
                    elif parse_status == "invalid":
                        self._increment_stat("invalid_lines")
                    else:
                        self._increment_stat("ignored_lines")

        except (serial.SerialException, OSError, ValueError) as exc:
            self._emit("error", f"Serial connection failed for {self.port}: {exc}. Check the port, cable, baud rate, and whether another application is using it.")
        finally:
            self._emit("stopped", "Serial acquisition stopped.")
