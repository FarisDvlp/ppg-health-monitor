import sys
import os
import csv
import time
import math
from datetime import datetime
from collections import deque

import numpy as np
import serial
import serial.tools.list_ports

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui, QtWidgets


# =====================================================================
# DIGITAL FILTERS FOR REAL-TIME BIOMEDICAL SIGNAL VISUALIZATION
# =====================================================================
class NotchFilter50Hz:
    """IIR Biquad 50 Hz Notch Filter at Fs = 500 Hz."""
    def __init__(self, fs=500.0, f0=50.0, r=0.975):
        w0 = 2.0 * math.pi * f0 / fs
        c = math.cos(w0)
        self.b0 = (1.0 - 2.0 * r * c + r * r) / (2.0 - 2.0 * c)
        self.b1 = -2.0 * c * self.b0
        self.b2 = self.b0
        self.a1 = -2.0 * r * c
        self.a2 = r * r
        self.reset()

    def reset(self):
        self.x1 = 0.0
        self.x2 = 0.0
        self.y1 = 0.0
        self.y2 = 0.0

    def process(self, x):
        y = (self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2
             - self.a1 * self.y1 - self.a2 * self.y2)
        self.x2 = self.x1
        self.x1 = x
        self.y2 = self.y1
        self.y1 = y
        return y


class EcgBandpassFilter:
    """Multi-harmonic powerline suppressor (50/100/150 Hz) + 0.67-35 Hz Bandpass at Fs = 500 Hz."""
    def __init__(self, fs=500.0):
        self.notch100 = NotchFilter50Hz(fs=fs, f0=100.0, r=0.96)
        self.notch150 = NotchFilter50Hz(fs=fs, f0=150.0, r=0.96)
        self.comb_buf = deque(maxlen=10)  # 10 samples @ 500Hz = exact 1 period of 50Hz

        dt = 1.0 / fs
        rc_hp = 1.0 / (2.0 * math.pi * 0.67)
        self.alpha_hp = rc_hp / (rc_hp + dt)

        rc_lp = 1.0 / (2.0 * math.pi * 35.0)
        self.alpha_lp = dt / (rc_lp + dt)
        self.strong_comb = False
        self.reset()

    def reset(self):
        self.notch100.reset()
        self.notch150.reset()
        self.comb_buf.clear()
        self.prev_x = 0.0
        self.prev_hp = 0.0
        self.prev_lp1 = 0.0
        self.prev_lp2 = 0.0

    def process(self, x):
        # Optional synchronous 10-sample comb filter (exact zeros at 50, 100, 150, 200, 250 Hz)
        self.comb_buf.append(x)
        if self.strong_comb and len(self.comb_buf) == 10:
            v = sum(self.comb_buf) / 10.0
        else:
            v = self.notch150.process(self.notch100.process(x))

        # High-pass filter (0.67 Hz) to remove DC offset & respiratory baseline drift
        hp = self.alpha_hp * (self.prev_hp + v - self.prev_x)
        self.prev_x = v
        self.prev_hp = hp

        # 2-Stage cascaded Low-pass filter (35 Hz)
        self.prev_lp1 = self.prev_lp1 + self.alpha_lp * (hp - self.prev_lp1)
        self.prev_lp2 = self.prev_lp2 + self.alpha_lp * (self.prev_lp1 - self.prev_lp2)
        return self.prev_lp2


class PpgFilter:
    """DC Removal + Polarity Inversion + 6 Hz Low-Pass Filter for PPG."""
    def __init__(self, fs=500.0):
        dt = 1.0 / fs
        rc_hp = 1.0 / (2.0 * math.pi * 0.5)
        self.alpha_dc = rc_hp / (rc_hp + dt)

        rc_lp = 1.0 / (2.0 * math.pi * 6.0)
        self.alpha_lp = dt / (rc_lp + dt)
        self.reset()

    def reset(self):
        self.dc = 0.0
        self.lp = 0.0
        self.initialized = False

    def process(self, raw_val, finger_present):
        if not finger_present or raw_val < 5000:
            self.reset()
            return 0.0
        if not self.initialized:
            self.dc = float(raw_val)
            self.lp = 0.0
            self.initialized = True
        self.dc = self.alpha_dc * self.dc + (1.0 - self.alpha_dc) * float(raw_val)
        ac = self.dc - float(raw_val)  # Inverted so systolic peak is positive
        self.lp = self.lp + self.alpha_lp * (ac - self.lp)
        return self.lp


# =====================================================================
# BACKGROUND SERIAL WORKER THREAD (500 HZ ZERO-DROP ACQUISITION)
# =====================================================================
class SerialWorker(QtCore.QThread):
    batch_received = QtCore.pyqtSignal(list)
    connection_changed = QtCore.pyqtSignal(bool, str)
    recording_progress = QtCore.pyqtSignal(int, float)
    recording_finished = QtCore.pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.port = "COM5"
        self.baudrate = 921600
        self.running = False
        self.ser = None

        # Command queue to send to ESP32
        self.pending_commands = deque()

        # CSV Recording state
        self.recording = False
        self.csv_file = None
        self.csv_writer = None
        self.csv_filepath = ""
        self.record_count = 0
        self.record_target_samples = 0  # 0 = manual unlimited
        self.record_start_time = 0.0
        self.lock = QtCore.QMutex()

    def configure(self, port, baudrate=921600):
        self.port = port
        self.baudrate = baudrate

    def send_command(self, cmd_str):
        self.pending_commands.append(cmd_str)

    def start_recording(self, filepath, duration_sec=0):
        self.lock.lock()
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            self.csv_file = open(filepath, mode="w", newline="", encoding="utf-8")
            self.csv_writer = csv.writer(self.csv_file)
            # Header kolom Raw sesuai kesepakatan
            self.csv_writer.writerow([
                "timestamp_ms",
                "ecg_raw",
                "lo_status",
                "ppg_ir_raw",
                "ppg_red_raw",
                "finger_status"
            ])
            self.csv_filepath = filepath
            self.record_count = 0
            self.record_target_samples = int(duration_sec * 500) if duration_sec > 0 else 0
            self.record_start_time = time.time()
            self.recording = True
            return True, ""
        except Exception as e:
            return False, str(e)
        finally:
            self.lock.unlock()

    def stop_recording(self):
        self.lock.lock()
        try:
            self._close_csv_internal()
        finally:
            self.lock.unlock()

    def _close_csv_internal(self):
        if self.recording:
            self.recording = False
            filepath = self.csv_filepath
            total = self.record_count
            if self.csv_file:
                try:
                    self.csv_file.flush()
                    self.csv_file.close()
                except Exception:
                    pass
                self.csv_file = None
                self.csv_writer = None
            self.recording_finished.emit(filepath, total)

    def stop(self):
        self.running = False
        self.wait(2000)

    def run(self):
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=0.02)
            self.ser.dtr = True
            self.ser.rts = True
            time.sleep(0.1)
            self.ser.reset_input_buffer()
            self.running = True
            self.connection_changed.emit(True, f"Terhubung ke {self.port} @ {self.baudrate} bps")
        except Exception as e:
            self.connection_changed.emit(False, f"Gagal membuka {self.port}: {e}")
            return

        batch = []
        rx_buf = b""
        last_emit = time.time()
        last_rec_update = time.time()

        while self.running:
            try:
                # Kirim command jika ada
                while self.pending_commands:
                    cmd = self.pending_commands.popleft()
                    self.ser.write((cmd + "\n").encode("utf-8"))

                # Baca secara chunk sekaligus agar mencapai tepat 500 Hz tanpa bottleneck syscall
                waiting = self.ser.in_waiting
                chunk = self.ser.read(waiting if waiting > 0 else 64)
                if chunk:
                    rx_buf += chunk
                    while b"\n" in rx_buf:
                        raw_line, rx_buf = rx_buf.split(b"\n", 1)
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if line and not line.startswith("#"):
                            parts = line.split(",")
                            if len(parts) == 6:
                                try:
                                    t_ms = int(parts[0])
                                    ecg_raw = int(parts[1])
                                    lo_stat = int(parts[2])
                                    ppg_ir = int(parts[3])
                                    ppg_red = int(parts[4])
                                    finger = int(parts[5])

                                    sample = (t_ms, ecg_raw, lo_stat, ppg_ir, ppg_red, finger)
                                    batch.append(sample)

                                    # Simpan ke CSV jika sedang merekam
                                    if self.recording:
                                        self.lock.lock()
                                        try:
                                            if self.recording and self.csv_writer:
                                                self.csv_writer.writerow(sample)
                                                self.record_count += 1
                                                if self.record_count % 250 == 0:
                                                    self.csv_file.flush()
                                                if (self.record_target_samples > 0 and
                                                        self.record_count >= self.record_target_samples):
                                                    self._close_csv_internal()
                                        finally:
                                            self.lock.unlock()
                                except ValueError:
                                    pass

                now = time.time()
                if batch and (now - last_emit >= 0.025 or len(batch) >= 20):
                    self.batch_received.emit(batch)
                    batch = []
                    last_emit = now

                if self.recording and (now - last_rec_update >= 0.2):
                    elapsed = now - self.record_start_time
                    self.recording_progress.emit(self.record_count, elapsed)
                    last_rec_update = now

            except serial.SerialException as e:
                self.connection_changed.emit(False, f"Koneksi serial terputus: {e}")
                break
            except Exception:
                pass

        # Pastikan CSV ditutup dengan aman
        self.lock.lock()
        try:
            if self.recording:
                self._close_csv_internal()
        finally:
            self.lock.unlock()

        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.connection_changed.emit(False, "Terputus")


# =====================================================================
# MAIN CLINICAL DESKTOP WINDOW (LIGHT-MODE MEDIS)
# =====================================================================
class BiosignalMonitorWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ESP32-S3 Clinical Biosignal Monitor — AD8232 (ECG 500Hz) & MAX30102 (PPG)")
        self.resize(1380, 880)

        # Configure PyQtGraph global Light-Mode Medical Options
        pg.setConfigOption("background", "#FFFFFF")
        pg.setConfigOption("foreground", "#334155")
        pg.setConfigOptions(antialias=True)

        # Signal Filters
        self.ecg_notch = NotchFilter50Hz(fs=500.0)
        self.ecg_bandpass = EcgBandpassFilter(fs=500.0)
        self.ppg_ir_filter = PpgFilter(fs=500.0)
        self.ppg_red_filter = PpgFilter(fs=500.0)

        # Buffers (Default 1500 points = 3 seconds @ 500 Hz)
        self.window_points = 1500
        self.ecg_raw_buf = deque(maxlen=self.window_points)
        self.ecg_filt_buf = deque(maxlen=self.window_points)
        self.ppg_ir_raw_buf = deque(maxlen=self.window_points)
        self.ppg_red_raw_buf = deque(maxlen=self.window_points)
        self.ppg_ir_filt_buf = deque(maxlen=self.window_points)
        self.ppg_red_filt_buf = deque(maxlen=self.window_points)

        # State flags
        self.is_paused = False
        self.ecg_mode = "filtered"   # 'filtered' or 'raw'
        self.ppg_mode = "filtered"   # 'filtered' or 'raw'
        self.ppg_channel = "both"    # 'both', 'ir', 'red'
        self.save_dir = os.path.join(os.getcwd(), "recordings")
        os.makedirs(self.save_dir, exist_ok=True)

        # Sample rate calculation
        self.sample_counter = 0
        self.last_rate_time = time.time()
        self.measured_hz = 0

        # Worker Thread
        self.worker = SerialWorker(self)
        self.worker.batch_received.connect(self.on_batch_received)
        self.worker.connection_changed.connect(self.on_connection_changed)
        self.worker.recording_progress.connect(self.on_recording_progress)
        self.worker.recording_finished.connect(self.on_recording_finished)

        self._build_ui()
        self._apply_medical_light_stylesheet()

        # Render timer (~60 FPS)
        self.render_timer = QtCore.QTimer(self)
        self.render_timer.timeout.connect(self.update_plots)
        self.render_timer.start(16)

        # Rate counter timer (1 Hz)
        self.rate_timer = QtCore.QTimer(self)
        self.rate_timer.timeout.connect(self.update_rate_badge)
        self.rate_timer.start(1000)

        # Refresh port list and auto-connect COM5 if available
        self.refresh_ports()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_layout = QtWidgets.QVBoxLayout(central)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(10)

        # -------------------------------------------------------------
        # 1. HEADER & SERIAL CONNECTION BAR
        # -------------------------------------------------------------
        header_card = QtWidgets.QFrame()
        header_card.setObjectName("Card")
        header_layout = QtWidgets.QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 10, 16, 10)

        title_box = QtWidgets.QVBoxLayout()
        lbl_title = QtWidgets.QLabel("🫀 Clinical Biosignal Acquisition System — AD8232 ECG (500 Hz) & MAX30102 PPG")
        lbl_title.setObjectName("HeaderTitle")
        lbl_sub = QtWidgets.QLabel("Mode Akuisisi Deterministik UART • Tanpa Interferensi RF Wi-Fi • Pin ECG: GPIO 5 (Out), GPIO 12 (LO-), GPIO 11 (LO+)")
        lbl_sub.setObjectName("HeaderSub")
        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        header_layout.addLayout(title_box, stretch=1)

        # Serial Controls
        conn_layout = QtWidgets.QHBoxLayout()
        conn_layout.setSpacing(8)

        conn_layout.addWidget(QtWidgets.QLabel("Port Serial:"))
        self.cmb_port = QtWidgets.QComboBox()
        self.cmb_port.setMinimumWidth(160)
        conn_layout.addWidget(self.cmb_port)

        self.btn_refresh = QtWidgets.QPushButton("🔄 Refresh")
        self.btn_refresh.clicked.connect(self.refresh_ports)
        conn_layout.addWidget(self.btn_refresh)

        self.btn_connect = QtWidgets.QPushButton("🔌 Connect (921600)")
        self.btn_connect.setObjectName("PrimaryButton")
        self.btn_connect.clicked.connect(self.toggle_connection)
        conn_layout.addWidget(self.btn_connect)

        self.lbl_rate_badge = QtWidgets.QLabel("Sampling: 0 Hz")
        self.lbl_rate_badge.setObjectName("BadgeInfo")
        conn_layout.addWidget(self.lbl_rate_badge)

        self.lbl_conn_badge = QtWidgets.QLabel("● Disconnected")
        self.lbl_conn_badge.setObjectName("BadgeWarn")
        conn_layout.addWidget(self.lbl_conn_badge)

        header_layout.addLayout(conn_layout)
        main_layout.addWidget(header_card)

        # -------------------------------------------------------------
        # 2. VITAL METRIC CARDS ROW
        # -------------------------------------------------------------
        metrics_layout = QtWidgets.QHBoxLayout()
        metrics_layout.setSpacing(10)

        # Card 1: ECG Leads Status
        self.card_leads, self.val_leads, self.sub_leads = self._create_metric_card(
            "STATUS ELEKTRODA ECG", "Waiting...", "LO+ (Pin 11) & LO- (Pin 12)", "#059669"
        )
        metrics_layout.addWidget(self.card_leads)

        # Card 2: Raw ECG ADC
        self.card_ecg, self.val_ecg, self.sub_ecg = self._create_metric_card(
            "RAW ECG (AD8232)", "0", "Tegangan: 0.00 V (12-bit ADC)", "#059669"
        )
        metrics_layout.addWidget(self.card_ecg)

        # Card 3: PPG Finger Status
        self.card_finger, self.val_finger, self.sub_finger = self._create_metric_card(
            "KONTAK JARI PPG", "No Finger", "Threshold IR >= 10,000", "#D97706"
        )
        metrics_layout.addWidget(self.card_finger)

        # Card 4: PPG Raw IR
        self.card_ir, self.val_ir, self.sub_ir = self._create_metric_card(
            "PPG RAW INFRARED", "0", "Kanal Optik IR (MAX30102)", "#0284C7"
        )
        metrics_layout.addWidget(self.card_ir)

        # Card 5: PPG Raw RED
        self.card_red, self.val_red, self.sub_red = self._create_metric_card(
            "PPG RAW RED", "0", "Kanal Optik RED (MAX30102)", "#E11D48"
        )
        metrics_layout.addWidget(self.card_red)

        main_layout.addLayout(metrics_layout)

        # -------------------------------------------------------------
        # 3. CHART 1: AD8232 ECG WAVEFORM (500 HZ)
        # -------------------------------------------------------------
        ecg_card = QtWidgets.QFrame()
        ecg_card.setObjectName("Card")
        ecg_layout = QtWidgets.QVBoxLayout(ecg_card)
        ecg_layout.setContentsMargins(14, 10, 14, 10)
        ecg_layout.setSpacing(6)

        ecg_toolbar = QtWidgets.QHBoxLayout()
        lbl_ecg_chart = QtWidgets.QLabel("📈 GRAFIK 1: ELEKTROKARDIOGRAM (AD8232 ECG — 500 Hz)")
        lbl_ecg_chart.setObjectName("ChartTitleEcg")
        ecg_toolbar.addWidget(lbl_ecg_chart)
        ecg_toolbar.addStretch()

        ecg_toolbar.addWidget(QtWidgets.QLabel("Mode Tampilan ECG:"))
        self.cmb_ecg_mode = QtWidgets.QComboBox()
        self.cmb_ecg_mode.addItems([
            "Filtered: Denoised P-QRS-T (Notch 50/100/150Hz + Bandpass)",
            "Filtered: Strong 50Hz Comb (Anti-Interferensi PLN Kuat)",
            "Raw ADC Murni (0 - 4095)"
        ])
        self.cmb_ecg_mode.currentIndexChanged.connect(self.on_ecg_mode_changed)
        ecg_toolbar.addWidget(self.cmb_ecg_mode)

        ecg_toolbar.addWidget(QtWidgets.QLabel("Rentang Waktu:"))
        self.cmb_window = QtWidgets.QComboBox()
        self.cmb_window.addItems([
            "2 Detik (1000 Sampel)",
            "3 Detik (1500 Sampel)",
            "5 Detik (2500 Sampel)",
            "10 Detik (5000 Sampel)"
        ])
        self.cmb_window.setCurrentIndex(1)
        self.cmb_window.currentIndexChanged.connect(self.on_window_changed)
        ecg_toolbar.addWidget(self.cmb_window)

        self.btn_pause = QtWidgets.QPushButton("⏸ Pause Grafik")
        self.btn_pause.clicked.connect(self.toggle_pause)
        ecg_toolbar.addWidget(self.btn_pause)

        self.btn_clear = QtWidgets.QPushButton("🗑 Clear")
        self.btn_clear.clicked.connect(self.clear_buffers)
        ecg_toolbar.addWidget(self.btn_clear)

        ecg_layout.addLayout(ecg_toolbar)

        self.plot_ecg = pg.PlotWidget()
        self.plot_ecg.showGrid(x=True, y=True, alpha=0.35)
        self.plot_ecg.setLabel("left", "Amplitudo ECG")
        self.plot_ecg.setLabel("bottom", "Sampel (500 Hz)")
        self.plot_ecg.getAxis("left").setPen(pg.mkPen("#64748B"))
        self.plot_ecg.getAxis("bottom").setPen(pg.mkPen("#64748B"))
        self.curve_ecg = self.plot_ecg.plot(pen=pg.mkPen(color="#059669", width=2.0))
        ecg_layout.addWidget(self.plot_ecg)

        main_layout.addWidget(ecg_card, stretch=1)

        # -------------------------------------------------------------
        # 4. CHART 2: MAX30102 PPG WAVEFORM
        # -------------------------------------------------------------
        ppg_card = QtWidgets.QFrame()
        ppg_card.setObjectName("Card")
        ppg_layout = QtWidgets.QVBoxLayout(ppg_card)
        ppg_layout.setContentsMargins(14, 10, 14, 10)
        ppg_layout.setSpacing(6)

        ppg_toolbar = QtWidgets.QHBoxLayout()
        lbl_ppg_chart = QtWidgets.QLabel("💓 GRAFIK 2: PHOTOPLETHYSMOGRAM (MAX30102 OPTIK IR & RED)")
        lbl_ppg_chart.setObjectName("ChartTitlePpg")
        ppg_toolbar.addWidget(lbl_ppg_chart)
        ppg_toolbar.addStretch()

        ppg_toolbar.addWidget(QtWidgets.QLabel("Kanal PPG:"))
        self.cmb_ppg_channel = QtWidgets.QComboBox()
        self.cmb_ppg_channel.addItems([
            "Dual Channel (IR Biru & RED Merah)",
            "Hanya Infrared (IR)",
            "Hanya Red LED (RED)"
        ])
        self.cmb_ppg_channel.currentIndexChanged.connect(self.on_ppg_channel_changed)
        ppg_toolbar.addWidget(self.cmb_ppg_channel)

        ppg_toolbar.addWidget(QtWidgets.QLabel("Mode Tampilan PPG:"))
        self.cmb_ppg_mode = QtWidgets.QComboBox()
        self.cmb_ppg_mode.addItems([
            "Filtered: Denoised AC Pulsasi (Sistolik ke Atas)",
            "Raw ADC Murni (Unfiltered)"
        ])
        self.cmb_ppg_mode.currentIndexChanged.connect(self.on_ppg_mode_changed)
        ppg_toolbar.addWidget(self.cmb_ppg_mode)

        ppg_toolbar.addWidget(QtWidgets.QLabel("HW Rate MAX30102:"))
        self.cmb_hw_rate = QtWidgets.QComboBox()
        self.cmb_hw_rate.addItems(["100 Hz", "200 Hz", "400 Hz", "50 Hz"])
        self.cmb_hw_rate.currentIndexChanged.connect(self.on_hw_rate_changed)
        ppg_toolbar.addWidget(self.cmb_hw_rate)

        ppg_layout.addLayout(ppg_toolbar)

        self.plot_ppg = pg.PlotWidget()
        self.plot_ppg.showGrid(x=True, y=True, alpha=0.35)
        self.plot_ppg.setLabel("left", "Amplitudo PPG")
        self.plot_ppg.setLabel("bottom", "Sampel (Sinkron 500 Hz)")
        self.plot_ppg.getAxis("left").setPen(pg.mkPen("#64748B"))
        self.plot_ppg.getAxis("bottom").setPen(pg.mkPen("#64748B"))
        self.plot_ppg.addLegend(offset=(10, 10))
        self.curve_ppg_ir = self.plot_ppg.plot(
            pen=pg.mkPen(color="#0284C7", width=2.0), name="Infrared (IR)"
        )
        self.curve_ppg_red = self.plot_ppg.plot(
            pen=pg.mkPen(color="#E11D48", width=1.8), name="Red Channel (RED)"
        )
        ppg_layout.addWidget(self.plot_ppg)

        main_layout.addWidget(ppg_card, stretch=1)

        # -------------------------------------------------------------
        # 5. CSV RECORDING & DATA LOGGER PANEL
        # -------------------------------------------------------------
        rec_card = QtWidgets.QFrame()
        rec_card.setObjectName("Card")
        rec_layout = QtWidgets.QHBoxLayout(rec_card)
        rec_layout.setContentsMargins(16, 10, 16, 10)
        rec_layout.setSpacing(12)

        lbl_rec_icon = QtWidgets.QLabel("💾 PEREKAMAN DATA CSV (RAW):")
        lbl_rec_icon.setStyleSheet("font-weight: 700; color: #0F172A; font-size: 13px;")
        rec_layout.addWidget(lbl_rec_icon)

        rec_layout.addWidget(QtWidgets.QLabel("Nama Subjek / File:"))
        self.txt_subject = QtWidgets.QLineEdit("Subjek_01")
        self.txt_subject.setFixedWidth(150)
        rec_layout.addWidget(self.txt_subject)

        rec_layout.addWidget(QtWidgets.QLabel("Durasi Rekam:"))
        self.cmb_duration = QtWidgets.QComboBox()
        self.cmb_duration.addItems([
            "Manual (Start / Stop)",
            "10 Detik (5.000 baris)",
            "30 Detik (15.000 baris)",
            "60 Detik / 1 Menit (30.000 baris)",
            "300 Detik / 5 Menit (150.000 baris)"
        ])
        rec_layout.addWidget(self.cmb_duration)

        self.btn_folder = QtWidgets.QPushButton("📂 Pilih Folder")
        self.btn_folder.clicked.connect(self.choose_save_folder)
        rec_layout.addWidget(self.btn_folder)

        self.btn_record = QtWidgets.QPushButton("🔴 Mulai Rekam CSV")
        self.btn_record.setObjectName("RecordButton")
        self.btn_record.clicked.connect(self.toggle_recording)
        rec_layout.addWidget(self.btn_record)

        self.lbl_rec_status = QtWidgets.QLabel(f"Siap merekam ke: {self.save_dir}")
        self.lbl_rec_status.setStyleSheet("color: #475569; font-size: 12px; font-weight: 600;")
        rec_layout.addWidget(self.lbl_rec_status, stretch=1)

        main_layout.addWidget(rec_card)

    def _create_metric_card(self, title, main_val, sub_val, accent_color):
        card = QtWidgets.QFrame()
        card.setObjectName("Card")
        v = QtWidgets.QVBoxLayout(card)
        v.setContentsMargins(14, 10, 14, 10)
        v.setSpacing(3)

        lbl_t = QtWidgets.QLabel(title)
        lbl_t.setStyleSheet("font-size: 11px; font-weight: 700; color: #64748B; letter-spacing: 0.5px;")

        lbl_v = QtWidgets.QLabel(main_val)
        lbl_v.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {accent_color};")

        lbl_s = QtWidgets.QLabel(sub_val)
        lbl_s.setStyleSheet("font-size: 11px; color: #64748B;")

        v.addWidget(lbl_t)
        v.addWidget(lbl_v)
        v.addWidget(lbl_s)
        return card, lbl_v, lbl_s

    def _apply_medical_light_stylesheet(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #F1F5F9;
                color: #0F172A;
                font-family: 'Segoe UI', -apple-system, sans-serif;
                font-size: 12px;
            }
            QFrame#Card {
                background-color: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 10px;
            }
            QLabel {
                background-color: transparent;
            }
            QLabel#HeaderTitle {
                font-size: 16px;
                font-weight: 800;
                color: #0F172A;
            }
            QLabel#HeaderSub {
                font-size: 11px;
                color: #475569;
            }
            QLabel#ChartTitleEcg {
                font-size: 13px;
                font-weight: 800;
                color: #059669;
            }
            QLabel#ChartTitlePpg {
                font-size: 13px;
                font-weight: 800;
                color: #0284C7;
            }
            QLabel#BadgeOk {
                background-color: #DCFCE7;
                color: #15803D;
                border: 1px solid #86EFAC;
                border-radius: 12px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel#BadgeWarn {
                background-color: #FEE2E2;
                color: #B91C1C;
                border: 1px solid #FCA5A5;
                border-radius: 12px;
                padding: 4px 10px;
                font-weight: 700;
            }
            QLabel#BadgeInfo {
                background-color: #E0F2FE;
                color: #0369A1;
                border: 1px solid #7DD3FC;
                border-radius: 12px;
                padding: 4px 10px;
                font-weight: 700;
                font-family: 'Consolas', monospace;
            }
            QPushButton {
                background-color: #F8FAFC;
                color: #0F172A;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #E2E8F0;
                border-color: #94A3B8;
            }
            QPushButton#PrimaryButton {
                background-color: #0284C7;
                color: #FFFFFF;
                border: 1px solid #0369A1;
            }
            QPushButton#PrimaryButton:hover {
                background-color: #0369A1;
            }
            QPushButton#RecordButton {
                background-color: #DC2626;
                color: #FFFFFF;
                border: 1px solid #B91C1C;
                font-weight: 700;
                padding: 6px 14px;
            }
            QPushButton#RecordButton:hover {
                background-color: #B91C1C;
            }
            QComboBox, QLineEdit {
                background-color: #FFFFFF;
                color: #0F172A;
                border: 1px solid #CBD5E1;
                border-radius: 6px;
                padding: 5px 8px;
                font-weight: 500;
            }
            QComboBox:hover, QLineEdit:focus {
                border-color: #0284C7;
            }
        """)

    # =================================================================
    # SERIAL CONNECTION & HARDWARE HANDLERS
    # =================================================================
    def refresh_ports(self):
        self.cmb_port.clear()
        ports = serial.tools.list_ports.comports()
        com5_idx = -1
        for i, p in enumerate(ports):
            label = f"{p.device} — {p.description}"
            self.cmb_port.addItem(label, p.device)
            if p.device.upper() == "COM5":
                com5_idx = i

        if com5_idx >= 0:
            self.cmb_port.setCurrentIndex(com5_idx)
            # Auto-connect on startup if COM5 is found and not connected yet
            if not self.worker.isRunning():
                self.toggle_connection()

    def toggle_connection(self):
        if self.worker.isRunning():
            self.worker.stop()
            self.btn_connect.setText("🔌 Connect (921600)")
        else:
            port = self.cmb_port.currentData()
            if not port:
                port = "COM5"
            self.clear_buffers()
            self.worker.configure(port, 921600)
            self.worker.start()
            self.btn_connect.setText("❌ Disconnect")

    def on_connection_changed(self, connected, msg):
        if connected:
            self.lbl_conn_badge.setText(f"● Connected ({self.worker.port})")
            self.lbl_conn_badge.setObjectName("BadgeOk")
            self.btn_connect.setText("❌ Disconnect")
        else:
            self.lbl_conn_badge.setText("● Disconnected")
            self.lbl_conn_badge.setObjectName("BadgeWarn")
            self.btn_connect.setText("🔌 Connect (921600)")
        self.lbl_conn_badge.style().unpolish(self.lbl_conn_badge)
        self.lbl_conn_badge.style().polish(self.lbl_conn_badge)
        self.statusBar().showMessage(msg, 5000)

    def on_batch_received(self, batch):
        self.sample_counter += len(batch)

        for t_ms, ecg_raw, lo_stat, ppg_ir, ppg_red, finger in batch:
            # Filter calculations
            notched = self.ecg_notch.process(float(ecg_raw))
            ecg_filt = self.ecg_bandpass.process(notched)

            ir_filt = self.ppg_ir_filter.process(ppg_ir, finger == 1)
            red_filt = self.ppg_red_filter.process(ppg_red, finger == 1)

            if not self.is_paused:
                self.ecg_raw_buf.append(ecg_raw)
                self.ecg_filt_buf.append(ecg_filt)
                self.ppg_ir_raw_buf.append(ppg_ir)
                self.ppg_red_raw_buf.append(ppg_red)
                self.ppg_ir_filt_buf.append(ir_filt)
                self.ppg_red_filt_buf.append(red_filt)

        # Update numeric metric cards from batch statistics
        if batch:
            t_ms, ecg_raw, lo_stat, ppg_ir, ppg_red, finger = batch[-1]
            avg_ecg = int(round(sum(item[1] for item in batch) / len(batch)))
            volt = (avg_ecg * 3.3) / 4095.0
            self.val_ecg.setText(f"{avg_ecg:,}")
            self.sub_ecg.setText(f"Rata-rata: {volt:.2f} V (Sampel terakhir: {ecg_raw})")

            # Cek apakah ada sampel dalam batch yang mengalami Leads-Off atau saturasi 50Hz
            max_lo = max(item[2] for item in batch)
            clipped_count = sum(1 for item in batch if item[1] == 0 or item[1] >= 4095)

            if max_lo == 0 and clipped_count < len(batch) * 0.3:
                self.val_leads.setText("Normal (OK)")
                self.val_leads.setStyleSheet("font-size: 20px; font-weight: 800; color: #059669;")
                self.sub_leads.setText("LO+: 0 | LO-: 0 (Elektroda Terpasang Stabil)")
            elif max_lo > 0 and any(item[2] == 0 for item in batch):
                self.val_leads.setText("Saturasi 50Hz PLN")
                self.val_leads.setStyleSheet("font-size: 18px; font-weight: 800; color: #D97706;")
                self.sub_leads.setText("Cabut Charger Laptop / Cek Elektroda RL (Hijau)")
            else:
                detail = "LO+ & LO-" if max_lo == 3 else ("LO+" if max_lo == 1 else "LO-")
                self.val_leads.setText(f"Leads Off ({detail})")
                self.val_leads.setStyleSheet("font-size: 20px; font-weight: 800; color: #DC2626;")
                self.sub_leads.setText(f"Peringatan: Periksa kontak elektroda ({detail})")

            self.val_ir.setText(f"{ppg_ir:,}")
            self.val_red.setText(f"{ppg_red:,}")

            if finger == 1:
                self.val_finger.setText("Detected")
                self.val_finger.setStyleSheet("font-size: 20px; font-weight: 800; color: #059669;")
            else:
                self.val_finger.setText("No Finger")
                self.val_finger.setStyleSheet("font-size: 20px; font-weight: 800; color: #D97706;")

    def update_plots(self):
        if self.is_paused:
            return

        # 1. Update ECG Plot
        ecg_data = self.ecg_filt_buf if self.ecg_mode == "filtered" else self.ecg_raw_buf
        if len(ecg_data) > 2:
            self.curve_ecg.setData(np.array(ecg_data, dtype=np.float32))

        # 2. Update PPG Plot
        ir_data = self.ppg_ir_filt_buf if self.ppg_mode == "filtered" else self.ppg_ir_raw_buf
        red_data = self.ppg_red_filt_buf if self.ppg_mode == "filtered" else self.ppg_red_raw_buf

        if self.ppg_channel in ("both", "ir") and len(ir_data) > 2:
            self.curve_ppg_ir.setData(np.array(ir_data, dtype=np.float32))
        else:
            self.curve_ppg_ir.clear()

        if self.ppg_channel in ("both", "red") and len(red_data) > 2:
            self.curve_ppg_red.setData(np.array(red_data, dtype=np.float32))
        else:
            self.curve_ppg_red.clear()

    def update_rate_badge(self):
        now = time.time()
        dt = now - self.last_rate_time
        if dt > 0:
            self.measured_hz = int(round(self.sample_counter / dt))
            self.lbl_rate_badge.setText(f"Sampling: {self.measured_hz} Hz (Target: 500 Hz)")
            self.sample_counter = 0
            self.last_rate_time = now

    # =================================================================
    # UI CONTROLS & RECORDING HANDLERS
    # =================================================================
    def on_ecg_mode_changed(self, idx):
        if idx == 0:
            self.ecg_mode = "filtered"
            self.ecg_bandpass.strong_comb = False
        elif idx == 1:
            self.ecg_mode = "filtered"
            self.ecg_bandpass.strong_comb = True
        else:
            self.ecg_mode = "raw"
        self.clear_buffers()

    def on_ppg_mode_changed(self, idx):
        self.ppg_mode = "filtered" if idx == 0 else "raw"

    def on_ppg_channel_changed(self, idx):
        self.ppg_channel = ["both", "ir", "red"][idx]

    def on_window_changed(self, idx):
        pts_map = [1000, 1500, 2500, 5000]
        self.window_points = pts_map[idx]
        self.ecg_raw_buf = deque(self.ecg_raw_buf, maxlen=self.window_points)
        self.ecg_filt_buf = deque(self.ecg_filt_buf, maxlen=self.window_points)
        self.ppg_ir_raw_buf = deque(self.ppg_ir_raw_buf, maxlen=self.window_points)
        self.ppg_red_raw_buf = deque(self.ppg_red_raw_buf, maxlen=self.window_points)
        self.ppg_ir_filt_buf = deque(self.ppg_ir_filt_buf, maxlen=self.window_points)
        self.ppg_red_filt_buf = deque(self.ppg_red_filt_buf, maxlen=self.window_points)

    def on_hw_rate_changed(self, idx):
        rates = [100, 200, 400, 50]
        r = rates[idx]
        self.worker.send_command(f"RATE:{r},AVG:1")

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.btn_pause.setText("▶ Resume Grafik" if self.is_paused else "⏸ Pause Grafik")

    def clear_buffers(self):
        self.ecg_raw_buf.clear()
        self.ecg_filt_buf.clear()
        self.ppg_ir_raw_buf.clear()
        self.ppg_red_raw_buf.clear()
        self.ppg_ir_filt_buf.clear()
        self.ppg_red_filt_buf.clear()
        self.ecg_notch.reset()
        self.ecg_bandpass.reset()
        self.ppg_ir_filter.reset()
        self.ppg_red_filter.reset()
        self.curve_ecg.clear()
        self.curve_ppg_ir.clear()
        self.curve_ppg_red.clear()

    def choose_save_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Pilih Folder Penyimpanan CSV", self.save_dir
        )
        if folder:
            self.save_dir = folder
            self.lbl_rec_status.setText(f"Siap merekam ke: {self.save_dir}")

    def toggle_recording(self):
        if self.worker.recording:
            self.worker.stop_recording()
        else:
            if not self.worker.isRunning():
                QtWidgets.QMessageBox.warning(
                    self, "Belum Terhubung", "Hubungkan ke port Serial ESP32-S3 terlebih dahulu!"
                )
                return

            subj = self.txt_subject.text().strip() or "Rekaman"
            safe_subj = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in subj)
            ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_subj}_{ts_str}.csv"
            filepath = os.path.join(self.save_dir, filename)

            dur_map = [0, 10, 30, 60, 300]
            dur_sec = dur_map[self.cmb_duration.currentIndex()]

            ok, err = self.worker.start_recording(filepath, dur_sec)
            if ok:
                self.btn_record.setText("⏹ Hentikan Rekaman")
                self.btn_record.setStyleSheet(
                    "background-color: #0F172A; color: #FFFFFF; font-weight: 700; padding: 6px 14px;"
                )
                self.lbl_rec_status.setText(f"🔴 MEREKAM: {filename} (0 sampel)")
                self.lbl_rec_status.setStyleSheet("color: #DC2626; font-weight: 700;")
            else:
                QtWidgets.QMessageBox.critical(self, "Gagal Merekam", f"Error: {err}")

    def on_recording_progress(self, count, elapsed_sec):
        mins = int(elapsed_sec) // 60
        secs = int(elapsed_sec) % 60
        self.lbl_rec_status.setText(
            f"🔴 MEREKAM [{mins:02d}:{secs:02d}] — {count:,} baris tersimpan ({os.path.basename(self.worker.csv_filepath)})"
        )

    def on_recording_finished(self, filepath, total_rows):
        self.btn_record.setText("🔴 Mulai Rekam CSV")
        self.btn_record.setStyleSheet("")
        self.lbl_rec_status.setText(
            f"✅ Selesai! {total_rows:,} baris disimpan di: {filepath}"
        )
        self.lbl_rec_status.setStyleSheet("color: #059669; font-weight: 700;")

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.worker.stop()
        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    win = BiosignalMonitorWindow()
    win.show()
    sys.exit(app.exec() if hasattr(app, "exec") else app.exec_())
