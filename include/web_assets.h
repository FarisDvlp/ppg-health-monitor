#ifndef WEB_ASSETS_H
#define WEB_ASSETS_H

#include <Arduino.h>

const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="id">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ESP32-S3 PPG Monitor (MAX30102)</title>
    <style>
        :root {
            --bg-primary: #080d1a;
            --bg-card: #111a2e;
            --bg-card-hover: #18243f;
            --border-color: #213253;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --cyan-ir: #00f2fe;
            --cyan-glow: rgba(0, 242, 254, 0.4);
            --red-led: #ff3366;
            --red-glow: rgba(255, 51, 102, 0.4);
            --green-active: #10b981;
            --yellow-warn: #f59e0b;
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
            max-width: 1360px;
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
            color: var(--cyan-ir);
            font-size: 24px;
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
            color: var(--green-active);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }

        .badge-ws.disconnected {
            background: rgba(239, 68, 68, 0.15);
            color: #ef4444;
            border-color: rgba(239, 68, 68, 0.3);
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
            grid-template-columns: 2fr 3fr;
            gap: 14px;
            margin-bottom: 14px;
        }

        @media (max-width: 992px) {
            .top-row { grid-template-columns: 1fr; }
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
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
            font-size: 24px;
            font-weight: 700;
            color: #fff;
            display: flex;
            align-items: baseline;
            gap: 4px;
        }

        .val-ir { color: var(--cyan-ir); }
        .val-red { color: var(--red-led); }

        .finger-status {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            font-size: 16px;
            font-weight: 600;
        }

        .finger-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background-color: var(--yellow-warn);
        }

        .finger-detected .finger-dot {
            background-color: var(--green-active);
            box-shadow: 0 0 10px var(--green-active);
        }

        .finger-detected { color: var(--green-active); }
        .finger-undetected { color: var(--yellow-warn); }

        /* SENSOR CONFIG CARD */
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
            padding: 7px 12px;
            border-radius: 6px;
            font-size: 13px;
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

        /* OSCILLOSCOPE / CHART SECTION */
        .chart-section {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 14px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.4);
        }

        .chart-toolbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 10px;
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
            height: 480px;
            background: #050811;
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #1a253d;
        }

        canvas {
            position: absolute;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
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
            height: 90px;
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
            <span class="signal-icon">📈</span>
            <div class="header-title">
                <h1>ESP32-S3 PPG Monitor (MAX30102)</h1>
                <p>Dual-Channel Optical Biosensor • I2C SDA: GPIO 8, SCL: GPIO 9</p>
            </div>
        </div>
        <div class="header-badges">
            <span id="rateBadge" class="badge badge-rate">Sampling: 100 Hz</span>
            <span id="wsBadge" class="badge badge-ws">● Connected</span>
        </div>
    </header>

    <!-- TOP ROW: METRICS & SENSOR CONFIG -->
    <div class="top-row">
        <!-- VITAL METRICS -->
        <div class="metrics-grid">
            <div class="card">
                <div class="card-label">
                    <span>Finger Contact</span>
                    <span>👆</span>
                </div>
                <div class="card-value">
                    <div id="fingerStatus" class="finger-status finger-undetected">
                        <span class="finger-dot"></span>
                        <span id="fingerText">No Finger</span>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-label">
                    <span>Raw IR (ADC - UART)</span>
                    <span style="color: var(--cyan-ir)">■</span>
                </div>
                <div class="card-value val-ir">
                    <span id="irValue">0</span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">
                    <span>Raw RED (ADC - UART)</span>
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
                <span>⚙️ Konfigurasi Hardware Sampling Rate Sensor</span>
            </div>
            <div class="config-form">
                <div class="form-group">
                    <label>Sampling Rate:</label>
                    <select id="selectSampleRate">
                        <option value="50">50 Hz</option>
                        <option value="100" selected>100 Hz (Standard PPG)</option>
                        <option value="200">200 Hz (High Precision)</option>
                        <option value="400">400 Hz (Ultra High)</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Averaging:</label>
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

    <!-- OSCILLOSCOPE GRAPH SECTION -->
    <div class="chart-section">
        <div class="chart-toolbar">
            <div class="toolbar-group">
                <select id="layoutSelect" onchange="changeLayout(this.value)">
                    <option value="split" selected>Layout: Split Screen (IR Atas / RED Bawah)</option>
                    <option value="overlay">Layout: Overlay (Tumpuk di Tengah)</option>
                    <option value="ir_only">Layout: IR Channel Saja</option>
                    <option value="red_only">Layout: RED Channel Saja</option>
                </select>
                <select id="signalModeSelect" onchange="changeSignalMode(this.value)">
                    <option value="smooth" selected>Filter: Denoised PPG (Bersih Tanpa Noise)</option>
                    <option value="raw">Filter: Raw ADC (Unfiltered)</option>
                </select>
            </div>
            <div class="toolbar-group">
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
                <button id="btnPause" onclick="togglePause()">Pause</button>
                <button onclick="clearWaveform()">Clear</button>
            </div>
        </div>

        <div class="canvas-wrapper">
            <canvas id="ppgCanvas"></canvas>
        </div>
    </div>

    <!-- UART OUTPUT PREVIEW (FOR RPI 5) -->
    <div class="log-section">
        <div class="log-header" onclick="toggleLog()">
            <h3>
                <span>⚡</span>
                <span>Data RAW Dikirim ke Raspberry Pi 5 (Serial2 @ 115200 bps)</span>
            </h3>
            <span id="logToggleIcon" style="color: var(--text-muted)">▼</span>
        </div>
        <div id="terminalBox" class="terminal">Menunggu stream data dari ESP32-S3...</div>
    </div>

    <footer>
        ESP32-S3 Dual-Core PPG Monitor • Core 1: MAX30102 FIFO • Core 0: Web Server & UART
    </footer>
</div>

<script>
    // ============================================================
    // STATE & BUFFERS
    // ============================================================
    let isPaused = false;
    let layoutMode = 'split'; // 'split', 'overlay', 'ir_only', 'red_only'
    let signalMode = 'smooth'; // 'smooth' or 'raw'
    let maxPoints = 500;
    let gainMultiplier = 1.0;
    let autoGain = true;
    
    let rawIrBuffer = [];
    let rawRedBuffer = [];
    let smoothIrBuffer = [];
    let smoothRedBuffer = [];
    
    // Sample Rate Tracking
    let sampleCounter = 0;
    let lastRateCheck = Date.now();
    let hwSampleRate = 100;
    let measuredRate = 100;

    // Smoothed Auto-Scale Ranges
    let irMinSmooth = 0, irMaxSmooth = 100;
    let redMinSmooth = 0, redMaxSmooth = 100;

    // Canvas Setup
    const canvas = document.getElementById('ppgCanvas');
    const ctx = canvas.getContext('2d');
    let canvasWidth = 0;
    let canvasHeight = 0;

    function resizeCanvas() {
        const rect = canvas.getBoundingClientRect();
        canvas.width = rect.width * window.devicePixelRatio;
        canvas.height = rect.height * window.devicePixelRatio;
        ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
        canvasWidth = rect.width;
        canvasHeight = rect.height;
    }
    window.addEventListener('resize', resizeCanvas);
    resizeCanvas();

    // ============================================================
    // WEBSOCKET CLIENT (ROCK SOLID)
    // ============================================================
    let ws = null;
    let reconnectTimer = null;
    const wsBadge = document.getElementById('wsBadge');
    const irElem = document.getElementById('irValue');
    const redElem = document.getElementById('redValue');
    const fingerStatus = document.getElementById('fingerStatus');
    const fingerText = document.getElementById('fingerText');
    const rateBadge = document.getElementById('rateBadge');
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
                
                if (pkt.r) {
                    hwSampleRate = pkt.r;
                }

                if (pkt.d && Array.isArray(pkt.d)) {
                    for (let i = 0; i < pkt.d.length; i++) {
                        const item = pkt.d[i];
                        processSample(item[0], item[1], item[2], item[3], item[4], item[5]);
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

    function processSample(t, ir, red, sm_ir, sm_red, f) {
        sampleCounter++;

        if (!isPaused) {
            rawIrBuffer.push(-ir); // Inversi raw agar sistolik ke atas
            rawRedBuffer.push(-red);
            smoothIrBuffer.push(sm_ir);
            smoothRedBuffer.push(sm_red);

            if (rawIrBuffer.length > maxPoints) rawIrBuffer.shift();
            if (rawRedBuffer.length > maxPoints) rawRedBuffer.shift();
            if (smoothIrBuffer.length > maxPoints) smoothIrBuffer.shift();
            if (smoothRedBuffer.length > maxPoints) smoothRedBuffer.shift();
        }

        // Update UI Cards selalu real-time
        irElem.textContent = ir ? ir.toLocaleString() : '0';
        redElem.textContent = red ? red.toLocaleString() : '0';

        if (f) {
            fingerStatus.className = 'finger-status finger-detected';
            fingerText.textContent = 'Detected';
        } else {
            fingerStatus.className = 'finger-status finger-undetected';
            fingerText.textContent = 'No Finger';
        }
    }

    // Sampling Rate Counter (Diperbarui setiap 1 Detik)
    setInterval(() => {
        const now = Date.now();
        const elapsed = (now - lastRateCheck) / 1000;
        if (elapsed > 0) {
            measuredRate = Math.round(sampleCounter / elapsed);
            if (isPaused) {
                rateBadge.textContent = `[PAUSED] Sampling: ${measuredRate} Hz (HW: ${hwSampleRate} Hz)`;
            } else {
                rateBadge.textContent = `Sampling: ${measuredRate} Hz (HW: ${hwSampleRate} Hz)`;
            }
            sampleCounter = 0;
            lastRateCheck = now;
        }
    }, 1000);

    // ============================================================
    // DYNAMIC SENSOR CONFIGURATION VIA WEB
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
    // RENDERING OSCILLOSCOPE (INDEPENDENT CHANNEL AUTO-SCALE)
    // ============================================================
    function drawGrid(yStart, height, labelText, labelColor, minVal, maxVal) {
        ctx.strokeStyle = '#141d30';
        ctx.lineWidth = 1;

        const hSteps = 4;
        for (let i = 0; i <= hSteps; i++) {
            const y = yStart + (height / hSteps) * i;
            ctx.beginPath();
            ctx.moveTo(0, y);
            ctx.lineTo(canvasWidth, y);
            ctx.stroke();
        }

        const vSteps = 10;
        for (let i = 0; i <= vSteps; i++) {
            const x = (canvasWidth / vSteps) * i;
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
        ctx.lineTo(canvasWidth, yStart + height / 2);
        ctx.stroke();
        ctx.setLineDash([]);

        // Channel Tag & Range Labels
        ctx.fillStyle = labelColor;
        ctx.font = 'bold 12px -apple-system, BlinkMacSystemFont, sans-serif';
        ctx.fillText(labelText, 14, yStart + 20);

        ctx.fillStyle = '#64748b';
        ctx.font = '11px Consolas, monospace';
        ctx.fillText(`Max: ${Math.round(maxVal)}`, canvasWidth - 100, yStart + 20);
        ctx.fillText(`Min: ${Math.round(minVal)}`, canvasWidth - 100, yStart + height - 10);
        ctx.fillText(`Δ: ${Math.round(maxVal - minVal)}`, canvasWidth - 180, yStart + 20);
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

    function renderTrace(buffer, yStart, height, color, glowColor, minVal, maxVal) {
        if (buffer.length < 2) return;

        let range = (maxVal - minVal);
        if (range < 1) range = 1;

        const paddingY = 16;
        const usableHeight = height - (paddingY * 2);

        function getY(val) {
            let norm = (val - minVal) / range;
            if (!autoGain) {
                norm = (norm - 0.5) * gainMultiplier + 0.5;
            }
            return (yStart + height) - paddingY - (norm * usableHeight);
        }

        const stepX = canvasWidth / (maxPoints - 1);
        const startOffset = maxPoints - buffer.length;

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2.4;
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

    function renderWaveform() {
        ctx.clearRect(0, 0, canvasWidth, canvasHeight);

        const irData = (signalMode === 'smooth') ? smoothIrBuffer : rawIrBuffer;
        const redData = (signalMode === 'smooth') ? smoothRedBuffer : rawRedBuffer;

        const irB = getBounds(irData);
        const redB = getBounds(redData);

        irMinSmooth = (0.92 * irMinSmooth) + (0.08 * irB.min);
        irMaxSmooth = (0.92 * irMaxSmooth) + (0.08 * irB.max);
        redMinSmooth = (0.92 * redMinSmooth) + (0.08 * redB.min);
        redMaxSmooth = (0.92 * redMaxSmooth) + (0.08 * redB.max);

        if (layoutMode === 'split') {
            const halfH = canvasHeight / 2;

            // 1. Top Half: IR Channel (Cyan)
            drawGrid(0, halfH, '● INFRARED CHANNEL (DENOISED PPG)', '#00f2fe', irMinSmooth, irMaxSmooth);
            renderTrace(irData, 0, halfH, '#00f2fe', 'rgba(0, 242, 254, 0.6)', irMinSmooth, irMaxSmooth);

            // 2. Bottom Half: RED Channel (Coral)
            drawGrid(halfH, halfH, '● RED CHANNEL (DENOISED PPG)', '#ff3366', redMinSmooth, redMaxSmooth);
            renderTrace(redData, halfH, halfH, '#ff3366', 'rgba(255, 51, 102, 0.6)', redMinSmooth, redMaxSmooth);

        } else if (layoutMode === 'overlay') {
            drawGrid(0, canvasHeight, '● OVERLAY: IR (Cyan) & RED (Coral)', '#f8fafc', irMinSmooth, irMaxSmooth);
            renderTrace(irData, 0, canvasHeight, '#00f2fe', 'rgba(0, 242, 254, 0.5)', irMinSmooth, irMaxSmooth);
            renderTrace(redData, 0, canvasHeight, '#ff3366', 'rgba(255, 51, 102, 0.5)', redMinSmooth, redMaxSmooth);

        } else if (layoutMode === 'ir_only') {
            drawGrid(0, canvasHeight, '● INFRARED CHANNEL ONLY', '#00f2fe', irMinSmooth, irMaxSmooth);
            renderTrace(irData, 0, canvasHeight, '#00f2fe', 'rgba(0, 242, 254, 0.6)', irMinSmooth, irMaxSmooth);

        } else if (layoutMode === 'red_only') {
            drawGrid(0, canvasHeight, '● RED CHANNEL ONLY', '#ff3366', redMinSmooth, redMaxSmooth);
            renderTrace(redData, 0, canvasHeight, '#ff3366', 'rgba(255, 51, 102, 0.6)', redMinSmooth, redMaxSmooth);
        }

        requestAnimationFrame(renderWaveform);
    }

    // ============================================================
    // USER CONTROLS
    // ============================================================
    function togglePause() {
        isPaused = !isPaused;
        const btn = document.getElementById('btnPause');
        btn.textContent = isPaused ? 'Resume' : 'Pause';
        btn.style.background = isPaused ? '#0284c7' : '#18233c';
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
        maxPoints = parseInt(points);
        clearWaveform();
    }

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

    // Start WebSocket and Render Loop
    connectWebSocket();
    requestAnimationFrame(renderWaveform);
</script>

</body>
</html>
)rawliteral";

#endif // WEB_ASSETS_H
