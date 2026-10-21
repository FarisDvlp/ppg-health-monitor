#ifndef WEB_ASSETS_H
#define WEB_ASSETS_H

#include <Arduino.h>

const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP32-S3 Biosignal Monitor (AD8232 ECG + MAX30102 PPG)</title>
    <style>
        :root {
            --bg-primary: #080d1a;
            --bg-card: #111a2e;
            --bg-card-hover: #18243f;
            --border-color: #213253;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --ecg-green: #10b981;
            --ecg-glow: rgba(16, 185, 129, 0.4);
            --cyan-ir: #00f2fe;
            --cyan-glow: rgba(0, 242, 254, 0.4);
            --red-led: #ff3366;
            --red-glow: rgba(255, 51, 102, 0.4);
            --yellow-warn: #f59e0b;
            --red-danger: #ef4444;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        }

        body {
            background-color: var(--bg-primary);
            color: var(--text-main);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            padding: 16px;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            width: 100%;
        }

        /* HEADER */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 14px 20px;
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            margin-bottom: 14px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        }

        .logo-title {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .signal-icon {
            display: inline-block;
            font-size: 26px;
        }

        .header-title h1 {
            font-size: 18px;
            font-weight: 700;
            color: #fff;
            letter-spacing: 0.5px;
        }

        .header-title p {
            font-size: 12px;
            color: var(--text-muted);
        }

        .header-badges {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }

        .badge {
            font-size: 12px;
            padding: 5px 12px;
            border-radius: 20px;
            font-weight: 600;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .badge-ws {
            background: rgba(16, 185, 129, 0.15);
            color: var(--ecg-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-ws.disconnected {
            background: rgba(239, 68, 68, 0.15);
            color: var(--red-danger);
            border-color: rgba(239, 68, 68, 0.3);
        }

        .badge-ecg {
            background: rgba(16, 185, 129, 0.12);
            color: var(--ecg-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
            font-family: 'Consolas', monospace;
        }

        .badge-rate {
            background: rgba(0, 242, 254, 0.1);
            color: var(--cyan-ir);
            border: 1px solid rgba(0, 242, 254, 0.3);
            font-family: 'Consolas', monospace;
        }

        /* METRICS CARDS & SENSOR CONTROL BAR */
        .top-row {
            display: grid;
            grid-template-columns: 3fr 2fr;
            gap: 14px;
            margin-bottom: 14px;
        }

        @media (max-width: 992px) {
            .top-row { grid-template-columns: 1fr; }
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 12px;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        }

        .card-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            margin-bottom: 6px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .card-value {
            font-size: 22px;
            font-weight: 700;
            color: #fff;
            display: flex;
            align-items: baseline;
            gap: 4px;
        }

        .val-sub {
            font-size: 12px;
            color: var(--text-muted);
            font-weight: normal;
        }

        .val-ecg { color: var(--ecg-green); }
        .val-ir { color: var(--cyan-ir); }
        .val-red { color: var(--red-led); }

        .status-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }

        .status-ok .status-dot {
            background-color: var(--ecg-green);
            box-shadow: 0 0 10px var(--ecg-green);
        }

        .status-ok { color: var(--ecg-green); }

        .status-warn .status-dot {
            background-color: var(--red-danger);
            box-shadow: 0 0 10px var(--red-danger);
            animation: pulse-warn 1s infinite;
        }

        .status-warn { color: var(--red-danger); }

        @keyframes pulse-warn {
            0% { opacity: 1; }
            50% { opacity: 0.3; }
            100% { opacity: 1; }
        }

        /* CONFIG CARD */
        .config-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        }

        .config-header {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }

        .config-form {
            display: flex;
            gap: 10px;
            align-items: center;
            flex-wrap: wrap;
        }

        .form-group {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }

        .form-group label {
            font-size: 11px;
            color: var(--text-muted);
        }

        select, button {
            background: #18233c;
            color: var(--text-main);
            border: 1px solid var(--border-color);
            padding: 6px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
        }

        select:hover, button:hover {
            background: #233457;
            border-color: #3b527e;
        }

        button.btn-primary {
            background: #0284c7;
            border-color: #0369a1;
            color: #fff;
            font-weight: 600;
        }

        button.btn-primary:hover {
            background: #0ea5e9;
        }

        /* OSCILLOSCOPE / CHART SECTIONS */
        .chart-section {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 14px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        }

        .chart-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            flex-wrap: wrap;
            gap: 10px;
        }

        .chart-title-tag {
            font-size: 13px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 8px;
            letter-spacing: 0.5px;
        }

        .toolbar-group {
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }

        .canvas-wrapper {
            position: relative;
            width: 100%;
            background: #050811;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #1a253d;
        }

        .canvas-ecg-wrapper {
            height: 280px;
        }

        .canvas-ppg-wrapper {
            height: 280px;
        }

        canvas {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
        }

        .leads-off-banner {
            position: absolute;
            top: 10px;
            right: 14px;
            background: rgba(239, 68, 68, 0.88);
            color: #fff;
            padding: 6px 12px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 11px;
            box-shadow: 0 0 12px rgba(239, 68, 68, 0.5);
            display: none;
            pointer-events: none;
            z-index: 10;
            letter-spacing: 0.5px;
        }

        /* UART MONITOR ACCORDION */
        .log-section {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px;
        }

        .log-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            user-select: none;
        }

        .log-header h3 {
            font-size: 13px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .terminal {
            margin-top: 10px;
            background: #04060c;
            border: 1px solid #1a253d;
            border-radius: 6px;
            padding: 10px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 12px;
            color: #10b981;
            height: 80px;
            overflow-y: auto;
            white-space: pre-wrap;
            line-height: 1.4;
        }

        footer {
            margin-top: auto;
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
            padding-top: 16px;
        }
    </style>
</head>
<body>

<div class="container">
    <!-- HEADER -->
    <header>
        <div class="logo-title">
            <span class="signal-icon">🫀</span>
            <div class="header-title">
                <h1>ESP32-S3 Biomedical Monitor (AD8232 ECG + MAX30102 PPG)</h1>
                <p>Dual Biosignal Platform • ECG: GPIO 5 (ADC1_CH4 @ 500 Hz) • PPG: I2C (SDA 8, SCL 9 @ 100 Hz)</p>
            </div>
        </div>
        <div class="header-badges">
            <span id="ecgRateBadge" class="badge badge-ecg">ECG: 500 Hz</span>
            <span id="rateBadge" class="badge badge-rate">PPG: 100 Hz</span>
            <span id="wsBadge" class="badge badge-ws">● Connected</span>
        </div>
    </header>

    <!-- TOP ROW: METRICS & SENSOR CONFIG -->
    <div class="top-row">
        <!-- VITAL METRICS -->
        <div class="metrics-grid">
            <!-- ECG LEADS STATUS -->
            <div class="card">
                <div class="card-label">
                    <span>ECG Leads (LO+/LO-)</span>
                    <span>⚡</span>
                </div>
                <div class="card-value">
                    <div id="leadsStatus" class="status-ok">
                        <span class="status-dot"></span>
                        <span id="leadsText" style="font-size: 16px;">Normal</span>
                    </div>
                </div>
            </div>

            <!-- ECG RAW ADC -->
            <div class="card">
                <div class="card-label">
                    <span>Raw ECG (AD8232)</span>
                    <span style="color: var(--ecg-green)">■</span>
                </div>
                <div class="card-value val-ecg">
                    <span id="ecgValue">0</span>
                    <span id="ecgVoltage" class="val-sub">0.00 V</span>
                </div>
            </div>

            <!-- PPG FINGER CONTACT -->
            <div class="card">
                <div class="card-label">
                    <span>PPG Finger</span>
                    <span>👆</span>
                </div>
                <div class="card-value">
                    <div id="fingerStatus" class="status-warn">
                        <span class="status-dot"></span>
                        <span id="fingerText" style="font-size: 16px;">No Finger</span>
                    </div>
                </div>
            </div>

            <!-- PPG RAW IR -->
            <div class="card">
                <div class="card-label">
                    <span>PPG Raw IR</span>
                    <span style="color: var(--cyan-ir)">■</span>
                </div>
                <div class="card-value val-ir">
                    <span id="irValue">0</span>
                </div>
            </div>

            <!-- PPG RAW RED -->
            <div class="card">
                <div class="card-label">
                    <span>PPG Raw RED</span>
                    <span style="color: var(--red-led)">■</span>
                </div>
                <div class="card-value val-red">
                    <span id="redValue">0</span>
                </div>
            </div>
        </div>

        <!-- SENSOR CONFIGURATION PANEL -->
        <div class="config-card">
            <div class="config-header">
                <span>⚙️ Konfigurasi Hardware MAX30102</span>
            </div>
            <div class="config-form">
                <div class="form-group">
                    <label>PPG Sampling Rate:</label>
                    <select id="selectSampleRate">
                        <option value="50">50 Hz</option>
                        <option value="100" selected>100 Hz (Standard PPG)</option>
                        <option value="200">200 Hz (High Precision)</option>
                        <option value="400">400 Hz (Ultra High)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>PPG Averaging:</label>
                    <select id="selectSampleAvg">
                        <option value="1" selected>None (1x Full Rate)</option>
                        <option value="2">2x Average</option>
                        <option value="4">4x Average</option>
                    </select>
                </div>
                <div class="form-group" style="justify-content: flex-end;">
                    <label>&nbsp;</label>
                    <button class="btn-primary" onclick="applySensorConfig()">Terapkan ke Sensor</button>
                </div>
            </div>
        </div>
    </div>

    <!-- 1. ECG OSCILLOSCOPE (AD8232 @ 500 Hz) -->
    <div class="chart-section">
        <div class="chart-header">
            <div class="chart-title-tag" style="color: var(--ecg-green)">
                <span>🫀</span>
                <span>AD8232 ECG REAL-TIME WAVEFORM (500 Hz)</span>
            </div>
            <div class="toolbar-group">
                <select id="ecgFilterSelect" onchange="changeEcgFilter(this.value)">
                    <option value="filtered" selected>Filter: Denoised P-QRS-T (Notch 50Hz + Bandpass)</option>
                    <option value="raw">Filter: Raw ADC (Unfiltered 500 Hz)</option>
                </select>
                <select id="ecgGainSelect" onchange="changeEcgGain(this.value)">
                    <option value="auto" selected>Gain: Auto-Scale</option>
                    <option value="1.0">Gain: 1.0x</option>
                    <option value="1.5">Gain: 1.5x</option>
                    <option value="2.0">Gain: 2.0x</option>
                    <option value="3.0">Gain: 3.0x</option>
                </select>
                <select id="ecgTimeWindowSelect" onchange="changeEcgTimeWindow(this.value)">
                    <option value="1000">Window: 2 Detik (1000 Pts)</option>
                    <option value="1500" selected>Window: 3 Detik (1500 Pts)</option>
                    <option value="2500">Window: 5 Detik (2500 Pts)</option>
                </select>
                <label style="font-size:11px; color:var(--text-muted); display:inline-flex; align-items:center; gap:4px; cursor:pointer;">
                    <input type="checkbox" id="chkIgnoreLeadsOff" onchange="toggleIgnoreLeadsOff(this.checked)"> Abaikan Leads-Off
                </label>
                <button id="btnPauseEcg" onclick="togglePauseEcg()">Pause ECG</button>
                <button onclick="clearEcgWaveform()">Clear</button>
            </div>
        </div>

        <div class="canvas-wrapper canvas-ecg-wrapper">
            <canvas id="ecgCanvas"></canvas>
            <div id="leadsOffBanner" class="leads-off-banner">
                ⚠️ ELEKTRODA TERLEPAS (LEADS OFF: PERIKSA ELEKTRODA)
            </div>
        </div>
    </div>

    <!-- 2. PPG OSCILLOSCOPE (MAX30102 @ 100 Hz) -->
    <div class="chart-section">
        <div class="chart-header">
            <div class="chart-title-tag" style="color: var(--cyan-ir)">
                <span>📈</span>
                <span>MAX30102 PPG REAL-TIME WAVEFORM (100 Hz)</span>
            </div>
            <div class="toolbar-group">
                <select id="layoutSelect" onchange="changeLayout(this.value)">
                    <option value="split" selected>Layout: Split Screen (IR Atas / RED Bawah)</option>
                    <option value="overlay">Layout: Overlay (Tumpuk)</option>
                    <option value="ir_only">Layout: IR Channel Saja</option>
                    <option value="red_only">Layout: RED Channel Saja</option>
                </select>
                <select id="signalModeSelect" onchange="changeSignalMode(this.value)">
                    <option value="smooth" selected>Filter: Denoised PPG (Bersih)</option>
                    <option value="raw">Filter: Raw ADC (Unfiltered)</option>
                </select>
                <select id="gainSelect" onchange="changeGain(this.value)">
                    <option value="auto" selected>Gain: Auto-Scale</option>
                    <option value="1.5">Gain: 1.5x</option>
                    <option value="2.0">Gain: 2.0x</option>
                    <option value="3.0">Gain: 3.0x</option>
                </select>
                <select id="timeWindowSelect" onchange="changeTimeWindow(this.value)">
                    <option value="300">Window: 3 Detik</option>
                    <option value="500" selected>Window: 5 Detik</option>
                    <option value="800">Window: 8 Detik</option>
                </select>
                <button id="btnPausePpg" onclick="togglePausePpg()">Pause PPG</button>
                <button onclick="clearWaveform()">Clear</button>
            </div>
        </div>

        <div class="canvas-wrapper canvas-ppg-wrapper">
            <canvas id="ppgCanvas"></canvas>
        </div>
    </div>

    <!-- UART OUTPUT PREVIEW (FOR RPI 5) -->
    <div class="log-section">
        <div class="log-header" onclick="toggleLog()">
            <h3>
                <span>⚡</span>
                <span>Stream Data PPG MAX30102 ke Raspberry Pi 5 (Serial2 @ 115200 bps)</span>
            </h3>
            <span id="logToggleIcon" style="color: var(--text-muted)">▼</span>
        </div>
        <div id="terminalBox" class="terminal">Menunggu stream data dari ESP32-S3...</div>
    </div>

    <footer>
        ESP32-S3 Dual-Core Biosignal Monitor • Core 1: MAX30102 FIFO & Hardware Timer AD8232 (500 Hz) • Core 0: Web Server & UART
    </footer>
</div>

<script>
    // ============================================================
    // STATE & BUFFERS
    // ============================================================
    let isEcgPaused = false;
    let isPpgPaused = false;

    // ECG State
    let ecgFilterMode = 'filtered'; // 'filtered' (default) or 'raw'
    let ignoreLeadsOff = false;
    let ecgMaxPoints = 1500;   // 3 seconds @ 500 Hz
    let ecgAutoGain = true;
    let ecgGainMultiplier = 1.0;
    let rawEcgBuffer = [];
    let filteredEcgBuffer = [];
    let ecgMinSmooth = 0, ecgMaxSmooth = 4095;
    let isLeadsOffActive = false;

    // PPG State
    let layoutMode = 'split';  // 'split', 'overlay', 'ir_only', 'red_only'
    let signalMode = 'smooth';  // 'smooth' or 'raw'
    let ppgMaxPoints = 500;
    let gainMultiplier = 1.0;
    let autoGain = true;
    let rawIrBuffer = [];
    let rawRedBuffer = [];
    let smoothIrBuffer = [];
    let smoothRedBuffer = [];
    let irMinSmooth = 0, irMaxSmooth = 100;
    let redMinSmooth = 0, redMaxSmooth = 100;

    // Sample Rate Tracking
    let ppgSampleCounter = 0;
    let ecgSampleCounter = 0;
    let lastRateCheck = Date.now();
    let hwSampleRate = 100;
    let measuredPpgRate = 100;
    let measuredEcgRate = 500;

    // Canvas Elements
    const ecgCanvas = document.getElementById('ecgCanvas');
    const ecgCtx = ecgCanvas.getContext('2d');
    let ecgCanvasWidth = 0, ecgCanvasHeight = 0;

    const ppgCanvas = document.getElementById('ppgCanvas');
    const ppgCtx = ppgCanvas.getContext('2d');
    let ppgCanvasWidth = 0, ppgCanvasHeight = 0;

    function resizeCanvases() {
        const rEcg = ecgCanvas.getBoundingClientRect();
        ecgCanvas.width = rEcg.width * window.devicePixelRatio;
        ecgCanvas.height = rEcg.height * window.devicePixelRatio;
        ecgCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
        ecgCanvasWidth = rEcg.width;
        ecgCanvasHeight = rEcg.height;

        const rPpg = ppgCanvas.getBoundingClientRect();
        ppgCanvas.width = rPpg.width * window.devicePixelRatio;
        ppgCanvas.height = rPpg.height * window.devicePixelRatio;
        ppgCtx.scale(window.devicePixelRatio, window.devicePixelRatio);
        ppgCanvasWidth = rPpg.width;
        ppgCanvasHeight = rPpg.height;
    }
    window.addEventListener('resize', resizeCanvases);
    resizeCanvases();

    // ============================================================
    // DIGITAL FILTERS FOR 500 HZ ECG
    // ============================================================
    // 1. 50Hz Notch Filter (IIR Biquad @ Fs = 500 Hz)
    class Notch50Hz {
        constructor() {
            const w0 = 2.0 * Math.PI * 50.0 / 500.0; // 0.2 * PI
            const r = 0.975;
            const c = Math.cos(w0);
            this.b0 = (1.0 - 2.0 * r * c + r * r) / (2.0 - 2.0 * c);
            this.b1 = -2.0 * c * this.b0;
            this.b2 = this.b0;
            this.a1 = -2.0 * r * c;
            this.a2 = r * r;
            this.x1 = 0; this.x2 = 0;
            this.y1 = 0; this.y2 = 0;
        }
        process(x) {
            const y = this.b0 * x + this.b1 * this.x1 + this.b2 * this.x2 - this.a1 * this.y1 - this.a2 * this.y2;
            this.x2 = this.x1; this.x1 = x;
            this.y2 = this.y1; this.y1 = y;
            return y;
        }
        reset() {
            this.x1 = 0; this.x2 = 0; this.y1 = 0; this.y2 = 0;
        }
    }

    // 2. Bandpass Filter (0.5Hz Highpass + 40Hz Lowpass @ Fs = 500 Hz)
    class BandpassFilter {
        constructor() {
            const dt = 1.0 / 500.0;
            const rc_hp = 1.0 / (2.0 * Math.PI * 0.5);
            this.alpha_hp = rc_hp / (rc_hp + dt);

            const rc_lp = 1.0 / (2.0 * Math.PI * 40.0);
            this.alpha_lp = dt / (rc_lp + dt);

            this.prev_x = 0;
            this.prev_hp = 0;
            this.prev_lp = 0;
        }
        process(x) {
            // High-pass DC baseline removal
            const hp = this.alpha_hp * (this.prev_hp + x - this.prev_x);
            this.prev_x = x;
            this.prev_hp = hp;

            // Low-pass noise removal
            const lp = this.prev_lp + this.alpha_lp * (hp - this.prev_lp);
            this.prev_lp = lp;
            return lp;
        }
        reset() {
            this.prev_x = 0; this.prev_hp = 0; this.prev_lp = 0;
        }
    }

    const ecgNotch = new Notch50Hz();
    const ecgBandpass = new BandpassFilter();

    // ============================================================
    // WEBSOCKET CLIENT
    // ============================================================
    let ws = null;
    let reconnectTimer = null;
    const wsBadge = document.getElementById('wsBadge');
    const ecgRateBadge = document.getElementById('ecgRateBadge');
    const rateBadge = document.getElementById('rateBadge');

    const leadsStatus = document.getElementById('leadsStatus');
    const leadsText = document.getElementById('leadsText');
    const leadsOffBanner = document.getElementById('leadsOffBanner');
    const ecgValueElem = document.getElementById('ecgValue');
    const ecgVoltageElem = document.getElementById('ecgVoltage');

    const irElem = document.getElementById('irValue');
    const redElem = document.getElementById('redValue');
    const fingerStatus = document.getElementById('fingerStatus');
    const fingerText = document.getElementById('fingerText');
    const terminalBox = document.getElementById('terminalBox');

    function connectWebSocket() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.hostname || '192.168.4.1'}/ws`;
        
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            wsBadge.textContent = '● Connected';
            wsBadge.className = 'badge badge-ws';
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
        };

        ws.onclose = () => {
            wsBadge.textContent = '● Reconnecting...';
            wsBadge.className = 'badge badge-ws disconnected';
            if (!reconnectTimer) {
                reconnectTimer = setTimeout(() => {
                    reconnectTimer = null;
                    connectWebSocket();
                }, 1000);
            }
        };

        ws.onerror = () => {
            ws.close();
        };

        ws.onmessage = (event) => {
            try {
                const pkt = JSON.parse(event.data);
                
                if (pkt.r) hwSampleRate = pkt.r;

                // Process ECG Batch
                if (pkt.e && Array.isArray(pkt.e)) {
                    for (let i = 0; i < pkt.e.length; i++) {
                        const item = pkt.e[i]; // [t, raw_adc, leads_off, lo_plus, lo_minus]
                        processEcgSample(item[0], item[1], item[2], item[3], item[4]);
                    }
                }

                // Process PPG Batch
                if (pkt.d && Array.isArray(pkt.d)) {
                    for (let i = 0; i < pkt.d.length; i++) {
                        const item = pkt.d[i];
                        processPpgSample(item[0], item[1], item[2], item[3], item[4], item[5]);
                    }
                    if (pkt.d.length > 0) {
                        const last = pkt.d[pkt.d.length - 1];
                        updateUARTLog(`{"t":${last[0]},"ir":${last[1]},"red":${last[2]},"f":${last[5]}}`);
                    }
                }
            } catch (e) {
                console.error("Parse error:", e);
            }
        };
    }

    function processEcgSample(t, raw_adc, leads_off, lo_p, lo_m) {
        ecgSampleCounter++;

        // Filter calculation
        const notched = ecgNotch.process(raw_adc);
        const filtered = ecgBandpass.process(notched);

        if (!isEcgPaused) {
            rawEcgBuffer.push(raw_adc);
            filteredEcgBuffer.push(filtered);

            if (rawEcgBuffer.length > ecgMaxPoints) rawEcgBuffer.shift();
            if (filteredEcgBuffer.length > ecgMaxPoints) filteredEcgBuffer.shift();
        }

        // Update ECG Metrics Card
        ecgValueElem.textContent = raw_adc ? raw_adc.toLocaleString() : '0';
        const volt = (raw_adc * 3.3 / 4095.0).toFixed(2);
        ecgVoltageElem.textContent = `${volt} V`;

        isLeadsOffActive = !!leads_off;
        if (leads_off) {
            leadsStatus.className = 'status-warn';
            let detail = 'Leads Off';
            if (lo_p && lo_m) detail = 'Leads Off (LO+ & LO-)';
            else if (lo_p) detail = 'Leads Off (LO+)';
            else if (lo_m) detail = 'Leads Off (LO-)';
            leadsText.textContent = detail;
            leadsOffBanner.style.display = ignoreLeadsOff ? 'none' : 'block';
        } else {
            leadsStatus.className = 'status-ok';
            leadsText.textContent = 'Normal';
            leadsOffBanner.style.display = 'none';
        }
    }

    function processPpgSample(t, ir, red, sm_ir, sm_red, f) {
        ppgSampleCounter++;

        if (!isPpgPaused) {
            rawIrBuffer.push(-ir); // Inversi raw agar sistolik ke atas
            rawRedBuffer.push(-red);
            smoothIrBuffer.push(sm_ir);
            smoothRedBuffer.push(sm_red);

            if (rawIrBuffer.length > ppgMaxPoints) rawIrBuffer.shift();
            if (rawRedBuffer.length > ppgMaxPoints) rawRedBuffer.shift();
            if (smoothIrBuffer.length > ppgMaxPoints) smoothIrBuffer.shift();
            if (smoothRedBuffer.length > ppgMaxPoints) smoothRedBuffer.shift();
        }

        // Update PPG UI Cards
        irElem.textContent = ir ? ir.toLocaleString() : '0';
        redElem.textContent = red ? red.toLocaleString() : '0';

        if (f) {
            fingerStatus.className = 'status-ok';
            fingerText.textContent = 'Detected';
        } else {
            fingerStatus.className = 'status-warn';
            fingerText.textContent = 'No Finger';
        }
    }

    // Sampling Rate Counter (1 detik interval)
    setInterval(() => {
        const now = Date.now();
        const elapsed = (now - lastRateCheck) / 1000.0;
        if (elapsed > 0) {
            measuredPpgRate = Math.round(ppgSampleCounter / elapsed);
            measuredEcgRate = Math.round(ecgSampleCounter / elapsed);

            if (isPpgPaused) {
                rateBadge.textContent = `[PAUSED] PPG: ${measuredPpgRate} Hz (HW: ${hwSampleRate} Hz)`;
            } else {
                rateBadge.textContent = `PPG: ${measuredPpgRate} Hz (HW: ${hwSampleRate} Hz)`;
            }

            if (isEcgPaused) {
                ecgRateBadge.textContent = `[PAUSED] ECG: ${measuredEcgRate} Hz`;
            } else {
                ecgRateBadge.textContent = `ECG: ${measuredEcgRate} Hz (500 Hz Target)`;
            }

            ppgSampleCounter = 0;
            ecgSampleCounter = 0;
            lastRateCheck = now;
        }
    }, 1000);

    // ============================================================
    // HARDWARE SENSOR CONFIGURATION VIA WEB
    // ============================================================
    function applySensorConfig() {
        const rate = parseInt(document.getElementById('selectSampleRate').value);
        const avg = parseInt(document.getElementById('selectSampleAvg').value);

        if (ws && ws.readyState === WebSocket.OPEN) {
            const msg = JSON.stringify({ cmd: "set_rate", rate: rate, avg: avg });
            ws.send(msg);
        }
        
        fetch(`/api/set_rate?rate=${rate}&avg=${avg}`)
            .then(r => r.json())
            .then(d => {
                hwSampleRate = rate;
                clearWaveform();
            })
            .catch(e => console.log(e));
    }

    // ============================================================
    // RENDERING ENGINE
    // ============================================================
    function drawGrid(ctx, w, yStart, height, labelText, labelColor, minVal, maxVal) {
        ctx.strokeStyle = '#141d30';
        ctx.lineWidth = 1;

        const hSteps = 4;
        for (let i = 0; i <= hSteps; i++) {
            const y = yStart + (height / hSteps) * i;
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(w, y);
            ctx.stroke();
        }

        const vSteps = 10;
        for (let i = 0; i <= vSteps; i++) {
            const x = (w / vSteps) * i;
            ctx.beginPath();
            ctx.moveTo(x, yStart);
            ctx.lineTo(x, yStart + height);
            ctx.stroke();
        }

        // Center Baseline (Dashed)
        ctx.strokeStyle = '#223456';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(0, yStart + height / 2);
        ctx.lineTo(w, yStart + height / 2);
        ctx.stroke();
        ctx.setLineDash([]);

        // Channel Tag & Range Labels
        ctx.fillStyle = labelColor;
        ctx.font = 'bold 12px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.fillText(labelText, 14, yStart + 20);

        ctx.fillStyle = '#64748b';
        ctx.font = '11px Consolas, monospace';
        ctx.fillText(`Max: ${Math.round(maxVal)}`, w - 95, yStart + 20);
        ctx.fillText(`Min: ${Math.round(minVal)}`, w - 95, yStart + height - 10);
        ctx.fillText(`Δ: ${Math.round(maxVal - minVal)}`, w - 165, yStart + 20);
    }

    function getBounds(buffer) {
        if (buffer.length === 0) return { min: -10, max: 10 };
        let min = Infinity, max = -Infinity;
        for (let i = 0; i < buffer.length; i++) {
            if (buffer[i] < min) min = buffer[i];
            if (buffer[i] > max) max = buffer[i];
        }
        if (max - min < 5) {
            max += 5;
            min -= 5;
        }
        return { min, max };
    }

    function renderTrace(ctx, buffer, maxPts, w, yStart, height, color, glowColor, minVal, maxVal, isAutoGain, gainMult) {
        if (buffer.length < 2) return;

        let range = (maxVal - minVal);
        if (range < 1) range = 1;

        const paddingY = 16;
        const usableHeight = height - (paddingY * 2);

        function getY(val) {
            let norm = (val - minVal) / range;
            if (!isAutoGain) {
                norm = (norm - 0.5) * gainMult + 0.5;
            }
            return (yStart + height) - paddingY - (norm * usableHeight);
        }

        const stepX = w / (maxPts - 1);
        const startOffset = maxPts - buffer.length;

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.2;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.shadowColor = glowColor;
        ctx.shadowBlur = 8;

        for (let i = 0; i < buffer.length; i++) {
            const x = (startOffset + i) * stepX;
            const y = getY(buffer[i]);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.shadowBlur = 0;
    }

    // --- RENDER ECG WAVEFORM ---
    function renderEcg() {
        ecgCtx.clearRect(0, 0, ecgCanvasWidth, ecgCanvasHeight);

        const ecgData = (ecgFilterMode === 'filtered') ? filteredEcgBuffer : rawEcgBuffer;
        const bounds = getBounds(ecgData);

        ecgMinSmooth = (0.92 * ecgMinSmooth) + (0.08 * bounds.min);
        ecgMaxSmooth = (0.92 * ecgMaxSmooth) + (0.08 * bounds.max);

        const title = (ecgFilterMode === 'filtered')
            ? '● AD8232 ECG: DENOISED P-QRS-T (NOTCH 50Hz + BANDPASS 0.5-40Hz)'
            : '● AD8232 ECG: RAW ADC UNFILTERED (0-4095)';

        drawGrid(ecgCtx, ecgCanvasWidth, 0, ecgCanvasHeight, title, '#10b981', ecgMinSmooth, ecgMaxSmooth);
        renderTrace(ecgCtx, ecgData, ecgMaxPoints, ecgCanvasWidth, 0, ecgCanvasHeight,
                    '#10b981', 'rgba(16, 185, 129, 0.6)',
                    ecgMinSmooth, ecgMaxSmooth, ecgAutoGain, ecgGainMultiplier);
    }

    // --- RENDER PPG WAVEFORM ---
    function renderPpg() {
        ppgCtx.clearRect(0, 0, ppgCanvasWidth, ppgCanvasHeight);

        const irData = (signalMode === 'smooth') ? smoothIrBuffer : rawIrBuffer;
        const redData = (signalMode === 'smooth') ? smoothRedBuffer : rawRedBuffer;

        const irB = getBounds(irData);
        const redB = getBounds(redData);

        irMinSmooth = (0.92 * irMinSmooth) + (0.08 * irB.min);
        irMaxSmooth = (0.92 * irMaxSmooth) + (0.08 * irB.max);
        redMinSmooth = (0.92 * redMinSmooth) + (0.08 * redB.min);
        redMaxSmooth = (0.92 * redMaxSmooth) + (0.08 * redB.max);

        if (layoutMode === 'split') {
            const halfH = ppgCanvasHeight / 2;

            // Top Half: IR Channel (Cyan)
            drawGrid(ppgCtx, ppgCanvasWidth, 0, halfH, '● INFRARED CHANNEL', '#00f2fe', irMinSmooth, irMaxSmooth);
            renderTrace(ppgCtx, irData, ppgMaxPoints, ppgCanvasWidth, 0, halfH, '#00f2fe', 'rgba(0, 242, 254, 0.6)', irMinSmooth, irMaxSmooth, autoGain, gainMultiplier);

            // Bottom Half: RED Channel (Coral)
            drawGrid(ppgCtx, ppgCanvasWidth, halfH, halfH, '● RED CHANNEL', '#ff3366', redMinSmooth, redMaxSmooth);
            renderTrace(ppgCtx, redData, ppgMaxPoints, ppgCanvasWidth, halfH, halfH, '#ff3366', 'rgba(255, 51, 102, 0.6)', redMinSmooth, redMaxSmooth, autoGain, gainMultiplier);

        } else if (layoutMode === 'overlay') {
            drawGrid(ppgCtx, ppgCanvasWidth, 0, ppgCanvasHeight, '● OVERLAY: IR (Cyan) & RED (Coral)', '#f8fafc', irMinSmooth, irMaxSmooth);
            renderTrace(ppgCtx, irData, ppgMaxPoints, ppgCanvasWidth, 0, ppgCanvasHeight, '#00f2fe', 'rgba(0, 242, 254, 0.5)', irMinSmooth, irMaxSmooth, autoGain, gainMultiplier);
            renderTrace(ppgCtx, redData, ppgMaxPoints, ppgCanvasWidth, 0, ppgCanvasHeight, '#ff3366', 'rgba(255, 51, 102, 0.5)', redMinSmooth, redMaxSmooth, autoGain, gainMultiplier);

        } else if (layoutMode === 'ir_only') {
            drawGrid(ppgCtx, ppgCanvasWidth, 0, ppgCanvasHeight, '● INFRARED CHANNEL ONLY', '#00f2fe', irMinSmooth, irMaxSmooth);
            renderTrace(ppgCtx, irData, ppgMaxPoints, ppgCanvasWidth, 0, ppgCanvasHeight, '#00f2fe', 'rgba(0, 242, 254, 0.6)', irMinSmooth, irMaxSmooth, autoGain, gainMultiplier);

        } else if (layoutMode === 'red_only') {
            drawGrid(ppgCtx, ppgCanvasWidth, 0, ppgCanvasHeight, '● RED CHANNEL ONLY', '#ff3366', redMinSmooth, redMaxSmooth);
            renderTrace(ppgCtx, redData, ppgMaxPoints, ppgCanvasWidth, 0, ppgCanvasHeight, '#ff3366', 'rgba(255, 51, 102, 0.6)', redMinSmooth, redMaxSmooth, autoGain, gainMultiplier);
        }
    }

    function animationLoop() {
        renderEcg();
        renderPpg();
        requestAnimationFrame(animationLoop);
    }

    // ============================================================
    // USER CONTROLS: ECG
    // ============================================================
    function togglePauseEcg() {
        isEcgPaused = !isEcgPaused;
        const btn = document.getElementById('btnPauseEcg');
        btn.textContent = isEcgPaused ? 'Resume ECG' : 'Pause ECG';
        btn.style.background = isEcgPaused ? '#0284c7' : '#18233c';
    }

    function toggleIgnoreLeadsOff(checked) {
        ignoreLeadsOff = checked;
        if (ignoreLeadsOff) {
            leadsOffBanner.style.display = 'none';
        } else if (isLeadsOffActive) {
            leadsOffBanner.style.display = 'block';
        }
    }

    function clearEcgWaveform() {
        rawEcgBuffer = [];
        filteredEcgBuffer = [];
        ecgNotch.reset();
        ecgBandpass.reset();
    }

    function changeEcgFilter(mode) {
        ecgFilterMode = mode;
        clearEcgWaveform();
    }

    function changeEcgGain(val) {
        if (val === 'auto') {
            ecgAutoGain = true;
        } else {
            ecgAutoGain = false;
            ecgGainMultiplier = parseFloat(val);
        }
    }

    function changeEcgTimeWindow(points) {
        ecgMaxPoints = parseInt(points);
        clearEcgWaveform();
    }

    // ============================================================
    // USER CONTROLS: PPG
    // ============================================================
    function togglePausePpg() {
        isPpgPaused = !isPpgPaused;
        const btn = document.getElementById('btnPausePpg');
        btn.textContent = isPpgPaused ? 'Resume PPG' : 'Pause PPG';
        btn.style.background = isPpgPaused ? '#0284c7' : '#18233c';
    }

    function clearWaveform() {
        rawIrBuffer = [];
        rawRedBuffer = [];
        smoothIrBuffer = [];
        smoothRedBuffer = [];
    }

    function changeLayout(mode) {
        layoutMode = mode;
    }

    function changeSignalMode(mode) {
        signalMode = mode;
        clearWaveform();
    }

    function changeGain(val) {
        if (val === 'auto') {
            autoGain = true;
        } else {
            autoGain = false;
            gainMultiplier = parseFloat(val);
        }
    }

    function changeTimeWindow(points) {
        ppgMaxPoints = parseInt(points);
        clearWaveform();
    }

    // UART LOG ACCORDION
    let logLines = [];
    function updateUARTLog(line) {
        logLines.push(line);
        if (logLines.length > 5) logLines.shift();
        terminalBox.textContent = logLines.join('\n');
    }

    function toggleLog() {
        const box = document.getElementById('terminalBox');
        const icon = document.getElementById('logToggleIcon');
        if (box.style.display === 'none') {
            box.style.display = 'block';
            icon.textContent = '▼';
        } else {
            box.style.display = 'none';
            icon.textContent = '▶';
        }
    }

    // Start WebSocket and Animation Loop
    connectWebSocket();
    requestAnimationFrame(animationLoop);
</script>

</body>
</html>
)rawliteral";

#endif // WEB_ASSETS_H
