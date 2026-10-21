"""Synthetic PPG sample source for GUI and pipeline testing only.

IMPORTANT:
    This module generates artificial data. It is NOT a physiological simulator,
    is not medically validated, and must never be used as real subject data.

The public interface intentionally mirrors acquisition.SerialReader so that
main.py can switch between a real serial reader and this mock reader.
"""

from __future__ import annotations

import math
import queue
import random
import threading
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from acquisition import PPGSample

DEFAULT_SAMPLE_QUEUE_SIZE = 5000
SUPPORTED_PROFILES = {"normal", "no_finger", "flatline", "intermittent"}


class MockPPGReader:
    """Produce synthetic PPG-like samples on a background thread.

    Parameters
    ----------
    sample_rate_hz:
        Nominal generated sampling rate. The device timestamp advances from
        this nominal rate; host receipt timestamps reflect actual generation.
    profile:
        ``normal``: synthetic pulsatile IR/RED values, finger present.
        ``no_finger``: low values with finger status 0.
        ``flatline``: nearly constant values to exercise the flatline checker.
        ``intermittent``: alternates between finger present and absent.

    The generated waveform is only intended to test wiring between modules,
    GUI responsiveness, CSV writing and basic signal-check behavior. It is not
    intended to mimic a specific subject or validate an algorithm.
    """

    def __init__(
        self,
        sample_rate_hz: float = 100.0,
        profile: str = "normal",
        sample_queue_size: int = DEFAULT_SAMPLE_QUEUE_SIZE,
        seed: Optional[int] = None,
    ) -> None:
        if isinstance(sample_rate_hz, bool) or not isinstance(sample_rate_hz, (int, float)):
            raise ValueError("sample_rate_hz must be a positive number.")
        if not math.isfinite(float(sample_rate_hz)) or sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be a positive finite number.")
        if profile not in SUPPORTED_PROFILES:
            raise ValueError(f"profile must be one of: {', '.join(sorted(SUPPORTED_PROFILES))}.")
        if isinstance(sample_queue_size, bool) or not isinstance(sample_queue_size, int) or sample_queue_size <= 0:
            raise ValueError("sample_queue_size must be a positive integer.")

        self.sample_rate_hz = float(sample_rate_hz)
        self.profile = profile
        self.port = "SIMULATION"
        self.baud_rate = 0
        self.timeout = 0.0
        self.samples: "queue.Queue[PPGSample]" = queue.Queue(maxsize=sample_queue_size)
        self.events: "queue.Queue[Dict[str, str]]" = queue.Queue()

        self._rng = random.Random(seed)
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
        self._sample_index = 0
        self._start_monotonic: Optional[float] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_stats(self) -> Dict[str, int]:
        """Return the same counter keys as acquisition.SerialReader."""
        with self._stats_lock:
            return dict(self._stats)

    def start(self) -> None:
        if self.is_running:
            raise RuntimeError("Mock acquisition is already running.")
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="MockPPGReader",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self, timeout: Optional[float] = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _emit(self, event_type: str, message: str) -> None:
        self.events.put({"type": event_type, "message": message})

    def _increment(self, name: str) -> None:
        with self._stats_lock:
            self._stats[name] += 1

    def _finger_status(self, elapsed_s: float) -> int:
        if self.profile == "no_finger":
            return 0
        if self.profile == "intermittent":
            # Absent for 2 seconds of each 10-second cycle.
            return 0 if (elapsed_s % 10.0) >= 8.0 else 1
        return 1

    def _make_values(self, elapsed_s: float, finger_status: int) -> tuple[int, int]:
        if self.profile == "flatline":
            return 90000, 72000
        if self.profile == "no_finger" or finger_status == 0:
            # Low but valid ADC counts; status flag is the decisive indicator.
            return 2500 + self._rng.randrange(0, 5), 1800 + self._rng.randrange(0, 5)

        # Synthetic asymmetric pulse morphology: a sharp systolic component,
        # a smaller shoulder, and a broader late component. This is illustrative
        # waveform data, not a model of an individual or a calibrated PPG source.
        phase = (elapsed_s * 1.2) % 1.0  # nominally 72 pulses/minute
        systolic = math.exp(-0.5 * ((phase - 0.18) / 0.055) ** 2)
        shoulder = 0.22 * math.exp(-0.5 * ((phase - 0.42) / 0.045) ** 2)
        late_component = 0.36 * math.exp(-0.5 * ((phase - 0.62) / 0.095) ** 2)
        pulse_shape = systolic + shoulder + late_component - 0.24

        baseline_wander = 240.0 * math.sin(2.0 * math.pi * 0.12 * elapsed_s)
        amplitude_drift = 1.0 + 0.035 * math.sin(2.0 * math.pi * 0.035 * elapsed_s)
        ir = int(90000 + baseline_wander + 4800 * amplitude_drift * pulse_shape
                 + self._rng.gauss(0.0, 70.0))
        red_shape = (0.92 * systolic
                     + 0.25 * math.exp(-0.5 * ((phase - 0.44) / 0.05) ** 2)
                     + 0.30 * math.exp(-0.5 * ((phase - 0.64) / 0.10) ** 2)
                     - 0.22)
        red = int(72000 + 160.0 * math.sin(2.0 * math.pi * 0.10 * elapsed_s)
                  + 3500 * amplitude_drift * red_shape
                  + self._rng.gauss(0.0, 60.0))

        # Keep values in the expected raw-count range for this test fixture.
        ir = max(0, min((1 << 18) - 1, ir))
        red = max(0, min((1 << 18) - 1, red))
        return ir, red

    def _run(self) -> None:
        self._start_monotonic = time.monotonic()
        self._emit("status", "Connecting to SIMULATION mode...")
        self._emit(
            "status",
            "Connected to SIMULATION mode — SYNTHETIC DATA ONLY; not sensor measurements.",
        )

        sample_period = 1.0 / self.sample_rate_hz
        next_sample_time = time.monotonic()
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                elapsed_s = self._sample_index / self.sample_rate_hz
                finger_status = self._finger_status(elapsed_s)
                ir_value, red_value = self._make_values(elapsed_s, finger_status)

                # Device timestamp is a nominal simulated millis() counter.
                device_timestamp_ms = int(round(self._sample_index * sample_period * 1000.0)) & 0xFFFFFFFF
                sample = PPGSample(
                    timestamp_ms=device_timestamp_ms,
                    ir_value=ir_value,
                    red_value=red_value,
                    finger_status=finger_status,
                    host_timestamp_s=now,
                    host_timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                )

                self._increment("valid_samples")
                try:
                    self.samples.put_nowait(sample)
                except queue.Full:
                    # Match SerialReader's policy: drop newest when full, count it.
                    self._increment("samples_dropped_queue_full")
                else:
                    self._increment("samples_enqueued")

                self._sample_index += 1
                next_sample_time += sample_period
                wait_s = next_sample_time - time.monotonic()
                if wait_s > 0:
                    self._stop_event.wait(wait_s)
                else:
                    # Avoid a bursty catch-up loop after a temporary scheduling lag.
                    next_sample_time = time.monotonic()
        except Exception as exc:  # keep unexpected mock failures visible to the GUI
            self._emit("error", f"Mock acquisition failed: {exc}")
        finally:
            self._emit("stopped", "Simulation stopped. All generated samples are synthetic.")
