"""Real-time PPG waveform visualization for the laptop-side application.

Responsibilities:
- Plot raw IR and RED samples received from acquisition.PPGSample.
- Keep a rolling time window so plots remain responsive during long sessions.
- Handle ESP32 millisecond timestamp rollover and device timestamp resets.

This module does NOT open a serial port, write CSV files, or assess signal
quality. The GUI/main module should drain SerialReader.samples on its own QTimer
and pass batches here using add_samples(...). Call widget methods only from the
Qt GUI thread; do not update a Qt widget from the serial reader's background
thread.

Dependencies:
    python -m pip install PyQt6 pyqtgraph
"""

from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, Optional, Tuple

import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from acquisition import PPGSample


# ESP32 Arduino millis() is typically a 32-bit unsigned millisecond counter.
UINT32_ROLLOVER_MS = 1 << 32
ROLLOVER_HIGH_WATERMARK = 0xF0000000
ROLLOVER_LOW_WATERMARK = 0x0FFFFFFF


class PPGWaveformWidget(QWidget):
    """Widget containing live raw IR and RED waveform plots.

    Typical use from main_window.py / main.py::

        waveform = PPGWaveformWidget(window_seconds=10.0)

        # In a GUI-thread QTimer callback, after draining the reader queue:
        samples = []
        while not reader.samples.empty():
            try:
                samples.append(reader.samples.get_nowait())
            except queue.Empty:
                break
        waveform.add_samples(samples)

    Args:
        window_seconds: Duration of the visible time window. Default is 10 s.
        max_buffer_samples: Upper bound on retained sample points. The rolling
            time window usually trims the data sooner; this is a safety limit.
        parent: Optional Qt parent widget.
    """

    def __init__(
        self,
        window_seconds: float = 10.0,
        max_buffer_samples: int = 10000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)

        if window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero.")
        if isinstance(max_buffer_samples, bool) or not isinstance(max_buffer_samples, int):
            raise ValueError("max_buffer_samples must be a positive integer.")
        if max_buffer_samples < 2:
            raise ValueError("max_buffer_samples must be at least 2.")

        self.window_seconds = float(window_seconds)
        self.max_buffer_samples = max_buffer_samples

        # Entries are (device_time_ms_adjusted, ir_value, red_value).
        self._samples: Deque[Tuple[int, int, int]] = deque(maxlen=max_buffer_samples)
        self._last_device_timestamp_ms: Optional[int] = None
        self._timestamp_offset_ms = 0
        self._session_start_ms: Optional[int] = None
        self._timestamp_resets = 0

        self._build_ui()

    def _build_ui(self) -> None:
        """Create the plot widgets and basic presentation."""
        pg.setConfigOptions(antialias=True)

        self.setObjectName("PPGWaveformWidget")
        self.setMinimumSize(600, 500)

        self.status_label = QLabel("Waiting for PPG samples...")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "QLabel { color: #CBD5E1; background: #111827; "
            "padding: 8px; border-radius: 6px; }"
        )

        self.ir_plot = pg.PlotWidget()
        self.ir_plot.setBackground("#0B1220")
        self.ir_plot.setTitle("Infrared (IR) — Raw PPG", color="#E2E8F0", size="12pt")
        self.ir_plot.setLabel("left", "Raw sensor count", color="#CBD5E1")
        self.ir_plot.setLabel("bottom", "Time", units="s", color="#CBD5E1")
        self.ir_plot.showGrid(x=True, y=True, alpha=0.22)
        self.ir_plot.setMenuEnabled(False)
        self.ir_plot.setMouseEnabled(x=False, y=False)
        self.ir_plot.getViewBox().setDefaultPadding(0.02)
        self.ir_curve = self.ir_plot.plot(
            pen=pg.mkPen(color="#38BDF8", width=1.6),
            name="IR",
        )

        self.red_plot = pg.PlotWidget()
        self.red_plot.setBackground("#0B1220")
        self.red_plot.setTitle("Red (RED) — Raw PPG", color="#E2E8F0", size="12pt")
        self.red_plot.setLabel("left", "Raw sensor count", color="#CBD5E1")
        self.red_plot.setLabel("bottom", "Time", units="s", color="#CBD5E1")
        self.red_plot.showGrid(x=True, y=True, alpha=0.22)
        self.red_plot.setMenuEnabled(False)
        self.red_plot.setMouseEnabled(x=False, y=False)
        self.red_plot.getViewBox().setDefaultPadding(0.02)
        self.red_curve = self.red_plot.plot(
            pen=pg.mkPen(color="#FB7185", width=1.6),
            name="RED",
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.status_label)
        layout.addWidget(self.ir_plot, stretch=1)
        layout.addWidget(self.red_plot, stretch=1)

    @property
    def sample_count(self) -> int:
        """Number of samples currently retained in the visible buffer."""
        return len(self._samples)

    @property
    def timestamp_reset_count(self) -> int:
        """Number of detected device timestamp resets (not normal rollovers)."""
        return self._timestamp_resets

    def add_sample(self, sample: PPGSample) -> None:
        """Add one sample and refresh the plots.

        For better efficiency at 100 Hz or higher, prefer add_samples() with a
        batch of items drained from SerialReader.samples.
        """
        self.add_samples((sample,))

    def add_samples(self, samples: Iterable[PPGSample]) -> None:
        """Append a batch of samples and redraw once.

        The method expects validated PPGSample objects produced by
        acquisition.parse_ppg_line / SerialReader. It must be called from the
        Qt GUI thread.
        """
        changed = False
        for sample in samples:
            if not isinstance(sample, PPGSample):
                raise TypeError("samples must contain PPGSample objects from acquisition.py.")

            device_ms = sample.timestamp_ms
            adjusted_ms = self._adjust_device_timestamp(device_ms)
            if self._session_start_ms is None:
                self._session_start_ms = adjusted_ms

            self._samples.append((adjusted_ms, sample.ir_value, sample.red_value))
            changed = True

        if changed:
            self._trim_to_time_window()
            self._redraw()

    def _adjust_device_timestamp(self, device_ms: int) -> int:
        """Make device timestamps monotonic across millis() rollover/reboot."""
        if self._last_device_timestamp_ms is not None and device_ms < self._last_device_timestamp_ms:
            is_rollover = (
                self._last_device_timestamp_ms >= ROLLOVER_HIGH_WATERMARK
                and device_ms <= ROLLOVER_LOW_WATERMARK
            )
            if is_rollover:
                self._timestamp_offset_ms += UINT32_ROLLOVER_MS
            else:
                # Device was likely rebooted or its timebase restarted.
                # Start a new visible waveform segment to avoid a misleading jump.
                self._samples.clear()
                self._timestamp_offset_ms = 0
                self._session_start_ms = None
                self._timestamp_resets += 1

        self._last_device_timestamp_ms = device_ms
        return device_ms + self._timestamp_offset_ms

    def _trim_to_time_window(self) -> None:
        """Keep samples inside the requested trailing time window."""
        if not self._samples:
            return
        latest_ms = self._samples[-1][0]
        cutoff_ms = latest_ms - int(self.window_seconds * 1000)
        while self._samples and self._samples[0][0] < cutoff_ms:
            self._samples.popleft()

    @staticmethod
    def _padded_y_range(values) -> Tuple[float, float]:
        """Return a padded Y range so small raw signal changes remain visible."""
        minimum = float(min(values))
        maximum = float(max(values))
        span = maximum - minimum
        if span <= 0:
            # A perfectly flat signal should still produce a visible axis range.
            padding = max(abs(minimum) * 0.001, 1.0)
        else:
            padding = max(span * 0.08, 1.0)
        return minimum - padding, maximum + padding

    def _redraw(self) -> None:
        """Refresh curves and labels from the retained rolling buffer."""
        if not self._samples or self._session_start_ms is None:
            return

        rows = list(self._samples)
        latest_ms = rows[-1][0]
        x_values = [(row[0] - self._session_start_ms) / 1000.0 for row in rows]
        ir_values = [row[1] for row in rows]
        red_values = [row[2] for row in rows]

        self.ir_curve.setData(x_values, ir_values)
        self.red_curve.setData(x_values, red_values)

        latest_time_s = x_values[-1]
        # Fill the full plot width during startup, then scroll a trailing window.
        x_min = max(0.0, latest_time_s - self.window_seconds)
        x_max = max(self.window_seconds, latest_time_s)
        if x_max <= x_min:
            x_max = x_min + self.window_seconds

        self.ir_plot.setXRange(x_min, x_max, padding=0)
        self.red_plot.setXRange(x_min, x_max, padding=0)
        self.ir_plot.setYRange(*self._padded_y_range(ir_values), padding=0)
        self.red_plot.setYRange(*self._padded_y_range(red_values), padding=0)

        latest = rows[-1]
        self.status_label.setText(
            f"Samples in view: {len(rows)} | "
            f"Latest device time: {latest[0] / 1000.0:.3f} s | "
            f"IR: {latest[1]} | RED: {latest[2]} | "
            f"Display window: {self.window_seconds:g} s"
        )

    def set_window_seconds(self, seconds: float) -> None:
        """Change the visible time window and redraw the current samples."""
        if seconds <= 0:
            raise ValueError("seconds must be greater than zero.")
        self.window_seconds = float(seconds)
        self._trim_to_time_window()
        self._redraw()

    def clear(self) -> None:
        """Clear all displayed samples and reset timestamp tracking."""
        self._samples.clear()
        self._last_device_timestamp_ms = None
        self._timestamp_offset_ms = 0
        self._session_start_ms = None
        self.ir_curve.setData([], [])
        self.red_curve.setData([], [])
        self.status_label.setText("Waiting for PPG samples...")
        self.ir_plot.setXRange(0, self.window_seconds, padding=0)
        self.red_plot.setXRange(0, self.window_seconds, padding=0)
