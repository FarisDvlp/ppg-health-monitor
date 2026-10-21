"""Entry point for the laptop-side PPG acquisition application.

This module connects the existing modular components:
- acquisition.py: reads and validates PPG samples from a serial port
- recording.py: validates subject information and records samples to CSV
- visualization.py: displays raw IR and RED waveforms
- signal_quality.py: reports basic technical signal checks

Run from this folder with:
    python main.py

An explicitly labeled SIMULATION source is available for software testing only.
Simulation samples are synthetic and must not be treated as real measurements.
"""

from __future__ import annotations

import queue
import sys
from typing import List, Optional, Union

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from acquisition import PPGSample, SerialReader
from mock_acquisition import MockPPGReader
from recording import CSVRecorder, RecordingError, SubjectInfo, SubjectInfoError
from signal_quality import SignalQualityChecker, SignalQualityResult
from visualization import PPGWaveformWidget


DEFAULT_BAUD_RATE = 115200
EXPECTED_SAMPLE_RATE_HZ = 100.0
GUI_POLL_INTERVAL_MS = 20
MAX_SAMPLES_PER_GUI_TICK = 2000
SIMULATION_OPTION = "SIMULATION — synthetic data only"


class PPGMainWindow(QMainWindow):
    """Main GUI that coordinates acquisition, recording, visualization, and QC."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PPG Acquisition & Signal Monitor")
        self.resize(1180, 820)
        self.setMinimumSize(900, 650)

        self.reader: Optional[Union[SerialReader, MockPPGReader]] = None
        self._simulation_mode = False
        self.serial_connected = False
        self.latest_sample: Optional[PPGSample] = None
        self.total_samples_processed = 0
        self._connection_started_at: Optional[float] = None
        self._no_ppg_warning_shown = False
        self._closing = False

        self.recorder = CSVRecorder(output_dir="data_ppg")
        self.quality_checker = SignalQualityChecker(
            expected_sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ
        )

        self._build_ui()
        self._refresh_serial_ports()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(GUI_POLL_INTERVAL_MS)
        self.poll_timer.timeout.connect(self._poll_reader)
        self.poll_timer.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("PPG Acquisition & Signal Monitor")
        title.setObjectName("appTitle")
        subtitle = QLabel(
            "Raw IR/RED acquisition • CSV recording • real-time waveform • basic signal checks"
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        # Serial connection controls
        connection_box = QGroupBox("1. Device connection")
        connection_layout = QHBoxLayout(connection_box)

        connection_layout.addWidget(QLabel("Serial port:"))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(150)
        self.port_combo.setToolTip("Choose a detected port or type one manually, e.g. COM3.")
        connection_layout.addWidget(self.port_combo)

        self.refresh_ports_button = QPushButton("Refresh")
        self.refresh_ports_button.clicked.connect(self._refresh_serial_ports)
        connection_layout.addWidget(self.refresh_ports_button)

        connection_layout.addWidget(QLabel("Baud rate:"))
        self.baud_combo = QComboBox()
        self.baud_combo.setEditable(True)
        self.baud_combo.addItems(["115200", "9600", "57600", "230400", "460800"])
        self.baud_combo.setCurrentText(str(DEFAULT_BAUD_RATE))
        self.baud_combo.setMaximumWidth(125)
        connection_layout.addWidget(self.baud_combo)

        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primaryButton")
        self.connect_button.clicked.connect(self._connect_serial)
        connection_layout.addWidget(self.connect_button)

        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.clicked.connect(self._disconnect_serial)
        self.disconnect_button.setEnabled(False)
        connection_layout.addWidget(self.disconnect_button)
        root.addWidget(connection_box)

        # Subject metadata and recording controls
        recording_box = QGroupBox("2. Subject and recording")
        recording_layout = QHBoxLayout(recording_box)
        form = QFormLayout()
        self.subject_id_input = QLineEdit()
        self.subject_id_input.setPlaceholderText("e.g. SUBJ_001 (use a coded ID)")
        self.age_input = QSpinBox()
        self.age_input.setRange(1, 120)
        self.age_input.setValue(21)
        form.addRow("Subject ID:", self.subject_id_input)
        form.addRow("Age (years):", self.age_input)
        recording_layout.addLayout(form, stretch=2)

        self.start_record_button = QPushButton("Start recording")
        self.start_record_button.setObjectName("primaryButton")
        self.start_record_button.setEnabled(False)
        self.start_record_button.clicked.connect(self._start_recording)
        recording_layout.addWidget(self.start_record_button)

        self.stop_record_button = QPushButton("Stop recording")
        self.stop_record_button.setEnabled(False)
        self.stop_record_button.clicked.connect(self._stop_recording)
        recording_layout.addWidget(self.stop_record_button)
        root.addWidget(recording_box)

        # Connection and sample counters
        status_box = QGroupBox("System status")
        status_layout = QVBoxLayout(status_box)
        self.connection_status_label = QLabel("Disconnected. Connect to a device to begin.")
        self.connection_status_label.setObjectName("connectionStatus")
        self.connection_status_label.setWordWrap(True)
        self.sample_status_label = QLabel("Valid samples: 0 | Invalid lines: 0 | Ignored lines: 0 | Queue drops: 0")
        self.sample_status_label.setWordWrap(True)
        self.recording_status_label = QLabel("Recording: inactive")
        self.recording_status_label.setWordWrap(True)
        status_layout.addWidget(self.connection_status_label)
        status_layout.addWidget(self.sample_status_label)
        status_layout.addWidget(self.recording_status_label)
        root.addWidget(status_box)

        # Quality check panel
        quality_box = QGroupBox("3. Basic signal quality checks")
        quality_layout = QVBoxLayout(quality_box)
        self.quality_status_label = QLabel("WAITING — Waiting for PPG samples.")
        self.quality_status_label.setObjectName("qualityStatus")
        self.quality_status_label.setWordWrap(True)
        self.quality_detail_label = QLabel(
            "Checks include finger status, near-flat channels, timestamp gaps, and configured raw-count range."
        )
        self.quality_detail_label.setWordWrap(True)
        self.quality_detail_label.setObjectName("mutedText")
        quality_layout.addWidget(self.quality_status_label)
        quality_layout.addWidget(self.quality_detail_label)
        root.addWidget(quality_box)

        # Real-time waveform widget
        waveform_box = QGroupBox("4. Real-time PPG waveform")
        waveform_layout = QVBoxLayout(waveform_box)
        self.waveform_widget = PPGWaveformWidget(window_seconds=10.0)
        waveform_layout.addWidget(self.waveform_widget)
        root.addWidget(waveform_box, stretch=1)

        self.setCentralWidget(central)
        self._apply_styles()

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            "QMainWindow, QWidget { background: #0F172A; color: #E2E8F0; }"
            "QGroupBox { border: 1px solid #334155; border-radius: 8px; "
            "margin-top: 8px; padding: 10px; font-weight: 600; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }"
            "QLabel#appTitle { font-size: 22px; font-weight: 700; color: #F8FAFC; }"
            "QLabel#subtitle, QLabel#mutedText { color: #94A3B8; }"
            "QLabel#connectionStatus { font-weight: 600; color: #CBD5E1; }"
            "QLabel#qualityStatus { font-weight: 700; padding: 5px; }"
            "QLineEdit, QComboBox, QSpinBox { background: #111827; color: #E2E8F0; "
            "border: 1px solid #475569; border-radius: 5px; padding: 6px; min-height: 22px; }"
            "QPushButton { background: #334155; color: #F8FAFC; border: none; "
            "border-radius: 5px; padding: 8px 12px; font-weight: 600; }"
            "QPushButton:hover { background: #475569; }"
            "QPushButton:disabled { background: #1E293B; color: #64748B; }"
            "QPushButton#primaryButton { background: #2563EB; }"
            "QPushButton#primaryButton:hover { background: #1D4ED8; }"
        )

    # ------------------------------------------------------------------
    # Serial port handling
    # ------------------------------------------------------------------
    def _refresh_serial_ports(self) -> None:
        current_text = self.port_combo.currentText().strip()
        detected_ports: List[str] = []

        try:
            from serial.tools import list_ports

            detected_ports = [port.device for port in list_ports.comports()]
        except ImportError:
            self._set_connection_status(
                "PySerial is not installed. Install dependencies using requirements.txt.",
                "warning",
            )

        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(detected_ports)
        # Explicitly labeled test source; it never reads from a physical device.
        self.port_combo.addItem(SIMULATION_OPTION)

        if current_text and (current_text in detected_ports or current_text == SIMULATION_OPTION):
            self.port_combo.setCurrentText(current_text)
        elif current_text and current_text not in detected_ports:
            # Preserve a manually typed serial port such as COM3.
            self.port_combo.setCurrentText(current_text)
        elif detected_ports:
            self.port_combo.setCurrentText(detected_ports[0])
        else:
            # No hardware detected: choose the clearly labeled simulation option.
            self.port_combo.setCurrentText(SIMULATION_OPTION)

        self.port_combo.blockSignals(False)
        self.port_combo.setToolTip(
            "Choose a physical serial port, type a port such as COM3, or choose "
            "SIMULATION to test the GUI with synthetic data only."
        )

    def _connect_serial(self) -> None:
        if self.reader is not None and self.reader.is_running:
            QMessageBox.information(self, "Already connected", "Serial acquisition is already running.")
            return

        port = self.port_combo.currentText().strip()
        if not port:
            QMessageBox.warning(
                self,
                "Serial port required",
                "Select a detected port or enter the port name manually (for example COM3).",
            )
            return

        try:
            baud_rate = int(self.baud_combo.currentText().strip())
            if baud_rate <= 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Invalid baud rate", "Enter a positive integer baud rate.")
            return

        self._stop_recording(silent=True)
        self.serial_connected = False
        self.latest_sample = None
        self.total_samples_processed = 0
        self._connection_started_at = None
        self._no_ppg_warning_shown = False
        self.quality_checker.reset()
        self.waveform_widget.clear()
        self._render_quality_result(self.quality_checker.latest_result)

        self._simulation_mode = (port == SIMULATION_OPTION)
        if self._simulation_mode:
            # The mock implements the same queue/events/stats API as SerialReader.
            self.reader = MockPPGReader(sample_rate_hz=EXPECTED_SAMPLE_RATE_HZ, profile="normal")
            starting_message = "Starting SIMULATION — synthetic data only; not sensor measurements."
        else:
            self.reader = SerialReader(port=port, baud_rate=baud_rate, timeout=0.2)
            starting_message = f"Connecting to {port} at {baud_rate} baud…"

        self.reader.start()
        import time
        self._connection_started_at = time.monotonic()

        self.connect_button.setEnabled(False)
        self.disconnect_button.setEnabled(True)
        self.start_record_button.setEnabled(False)
        self._set_connection_status(starting_message, "warning" if self._simulation_mode else "working")

    def _disconnect_serial(self) -> None:
        if self.recorder.is_recording:
            self._stop_recording()

        reader = self.reader
        if reader is not None:
            reader.stop()
            reader.join(timeout=1.0)
            # Drain samples that arrived just before the reader stopped.
            while not reader.samples.empty():
                self._drain_samples()

        self.reader = None
        self.serial_connected = False
        self._simulation_mode = False
        self.connect_button.setEnabled(True)
        self.disconnect_button.setEnabled(False)
        self.start_record_button.setEnabled(False)
        self._set_connection_status("Disconnected.", "neutral")

    # ------------------------------------------------------------------
    # Recording controls
    # ------------------------------------------------------------------
    def _start_recording(self) -> None:
        if not self.serial_connected or self.reader is None or not self.reader.is_running:
            QMessageBox.warning(
                self,
                "Device not connected",
                "Connect to a serial device before starting a recording.",
            )
            return

        try:
            subject_id = self.subject_id_input.text().strip()
            if self._simulation_mode:
                # Make test files unambiguously distinguishable from recordings.
                subject_id = f"SIM_{subject_id}"
            subject = SubjectInfo.from_inputs(
                subject_id,
                str(self.age_input.value()),
            )
            filepath = self.recorder.start(subject)
        except (SubjectInfoError, RecordingError, OSError) as exc:
            QMessageBox.warning(self, "Cannot start recording", str(exc))
            return

        self.start_record_button.setEnabled(False)
        self.stop_record_button.setEnabled(True)
        self.recording_status_label.setText(f"Recording: ACTIVE — {filepath}")

    def _stop_recording(self, silent: bool = False) -> None:
        if not self.recorder.is_recording:
            self.stop_record_button.setEnabled(False)
            self.start_record_button.setEnabled(self.serial_connected)
            if not silent:
                self.recording_status_label.setText("Recording: inactive")
            return

        # Process queued samples already received before closing the CSV file.
        while self.reader is not None and not self.reader.samples.empty():
            self._drain_samples()
        try:
            filepath = self.recorder.stop()
            self.recording_status_label.setText(
                f"Recording: STOPPED — {filepath} | Samples saved: {self.recorder.samples_written}"
            )
        except RecordingError as exc:
            self.recording_status_label.setText(f"Recording error: {exc}")
            if not silent:
                QMessageBox.critical(self, "Recording error", str(exc))
        finally:
            self.stop_record_button.setEnabled(False)
            self.start_record_button.setEnabled(self.serial_connected)

    # ------------------------------------------------------------------
    # Main application data loop
    # ------------------------------------------------------------------
    def _poll_reader(self) -> None:
        reader = self.reader
        if reader is None:
            return

        while True:
            try:
                event = reader.events.get_nowait()
            except queue.Empty:
                break
            self._handle_reader_event(event)

        self._drain_samples()
        self._update_reader_stats()

        if not reader.is_running and self.serial_connected:
            # Covers unexpected thread termination if an event was missed.
            self.serial_connected = False
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            self.start_record_button.setEnabled(False)

    def _handle_reader_event(self, event: dict) -> None:
        event_type = event.get("type", "status")
        message = event.get("message", "")

        if event_type == "status":
            if message.startswith("Connected to "):
                self.serial_connected = True
                self.connect_button.setEnabled(False)
                self.disconnect_button.setEnabled(True)
                self.start_record_button.setEnabled(not self.recorder.is_recording)
                self._set_connection_status(message, "warning" if self._simulation_mode else "good")
            else:
                self._set_connection_status(message, "working")
        elif event_type == "error":
            self.serial_connected = False
            self._set_connection_status(message, "error")
            if self.recorder.is_recording:
                self._stop_recording(silent=True)
            self.connect_button.setEnabled(True)
            self.disconnect_button.setEnabled(False)
            self.start_record_button.setEnabled(False)
        elif event_type == "stopped":
            if not self._closing and self.reader is not None and not self.reader.is_running:
                self.serial_connected = False
                self.connect_button.setEnabled(True)
                self.disconnect_button.setEnabled(False)
                self.start_record_button.setEnabled(False)
                if "error" not in self.connection_status_label.text().lower():
                    self._set_connection_status(message, "neutral")

    def _drain_samples(self) -> None:
        reader = self.reader
        if reader is None:
            return

        batch: List[PPGSample] = []
        for _ in range(MAX_SAMPLES_PER_GUI_TICK):
            try:
                sample = reader.samples.get_nowait()
            except queue.Empty:
                break

            batch.append(sample)
            self.latest_sample = sample
            self.total_samples_processed += 1

            if self.recorder.is_recording:
                try:
                    self.recorder.write_sample(sample)
                except RecordingError as exc:
                    # Stop the file directly here instead of calling
                    # _stop_recording(), which drains the queue and could recurse.
                    self._set_connection_status(f"CSV recording error: {exc}", "error")
                    try:
                        self.recorder.stop()
                    except RecordingError as stop_exc:
                        self.recording_status_label.setText(
                            f"Recording close error: {stop_exc}"
                        )
                    else:
                        self.recording_status_label.setText(
                            f"Recording stopped after a write error: {exc}"
                        )
                    self.stop_record_button.setEnabled(False)
                    self.start_record_button.setEnabled(self.serial_connected)

            self.quality_checker.update(sample)

        if batch:
            # Plotting happens on the GUI thread, and only once per batch.
            self.waveform_widget.add_samples(batch)
            if self.latest_sample is not None:
                self._render_quality_result(self.quality_checker.latest_result)

    # ------------------------------------------------------------------
    # Status presentation
    # ------------------------------------------------------------------
    def _update_reader_stats(self) -> None:
        reader = self.reader
        if reader is None:
            return

        stats = reader.get_stats()
        source_label = "SIMULATION DATA ONLY | " if self._simulation_mode else ""
        self.sample_status_label.setText(
            source_label +
            f"Samples received: {stats['valid_samples']} | "
            f"Processed by app: {self.total_samples_processed} | "
            f"Invalid JSON/data lines: {stats['invalid_lines']} | "
            f"Ignored debug/blank lines: {stats['ignored_lines']} | "
            f"Queue drops: {stats['samples_dropped_queue_full']}"
        )

        # Be explicit when a USB serial connection is open but only debug text
        # is arriving, a known possibility with the current ESP32 firmware.
        if (
            self.serial_connected
            and stats["valid_samples"] == 0
            and self._connection_started_at is not None
            and not self._no_ppg_warning_shown
        ):
            import time
            if time.monotonic() - self._connection_started_at >= 5.0:
                self._set_connection_status(
                    "Serial port is open, but no valid PPG JSON has arrived yet. "
                    "Check that this port actually outputs lines with t, ir, red, and f; "
                    "the ESP32 USB port may be outputting debug logs only.",
                    "warning",
                )
                self._no_ppg_warning_shown = True

    def _render_quality_result(self, result: SignalQualityResult) -> None:
        self.quality_status_label.setText(f"{result.status} — {result.message}")

        if result.status == "CHECKS_PASSED":
            color = "#86EFAC"
        elif result.status in {"WARNING", "POOR_SIGNAL", "NO_FINGER"}:
            color = "#FCA5A5"
        elif result.status == "COLLECTING":
            color = "#FDE68A"
        else:
            color = "#CBD5E1"
        self.quality_status_label.setStyleSheet(f"font-weight: 700; color: {color}; padding: 5px;")

        sample_rate = (
            f"{result.estimated_sample_rate_hz:.1f} Hz"
            if result.estimated_sample_rate_hz is not None
            else "not available"
        )
        details = (
            f"Window samples: {result.sample_count} | "
            f"IR range: {result.ir_range_counts if result.ir_range_counts is not None else '—'} | "
            f"RED range: {result.red_range_counts if result.red_range_counts is not None else '—'} | "
            f"Estimated rate: {sample_rate} | "
            f"Timestamp gaps: {result.timestamp_gap_count} | "
            f"Timestamp resets: {result.timestamp_discontinuity_count}"
        )
        if result.warnings:
            details += "\nWarnings: " + " | ".join(result.warnings)
        self.quality_detail_label.setText(details)

    def _set_connection_status(self, message: str, level: str = "neutral") -> None:
        color_map = {
            "good": "#86EFAC",
            "working": "#93C5FD",
            "warning": "#FDE68A",
            "error": "#FCA5A5",
            "neutral": "#CBD5E1",
        }
        color = color_map.get(level, color_map["neutral"])
        self.connection_status_label.setText(message)
        self.connection_status_label.setStyleSheet(f"font-weight: 600; color: {color};")

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's event name
        if self.recorder.is_recording:
            answer = QMessageBox.question(
                self,
                "Recording in progress",
                "A recording is active. Stop recording and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self._closing = True
        self.poll_timer.stop()
        if self.recorder.is_recording:
            try:
                self.recorder.stop()
            except RecordingError:
                pass

        if self.reader is not None:
            self.reader.stop()
            self.reader.join(timeout=1.0)
            self.reader = None

        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PPG Acquisition & Signal Monitor")
    window = PPGMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
