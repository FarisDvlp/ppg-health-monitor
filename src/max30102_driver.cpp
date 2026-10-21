#include "max30102_driver.h"

MAX30102Driver ppgSensor;

MAX30102Driver::MAX30102Driver() {
    connected = false;
    currentSampleRate = MAX30102_DEFAULT_SRATE;
    currentSampleAverage = MAX30102_DEFAULT_AVG;
    dc_ir = 0.0f;
    dc_red = 0.0f;
    lp_ir = 0.0f;
    lp_red = 0.0f;
    alpha_dc = 0.96f;
    alpha_lp = 0.28f;
}

bool MAX30102Driver::begin() {
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_CLOCK_SPEED);

    if (!sensor.begin(Wire, I2C_SPEED_FAST)) {
        Serial.printf("[MAX30102] Gagal menemukan sensor pada SDA=%d, SCL=%d! Periksa wiring & VCC (3.3V).\n",
                      I2C_SDA_PIN, I2C_SCL_PIN);
        connected = false;
        return false;
    }

    return setSampleRate(currentSampleRate, currentSampleAverage);
}

bool MAX30102Driver::setSampleRate(uint16_t sampleRate, uint8_t sampleAverage) {
    if (!sensor.begin(Wire, I2C_SPEED_FAST)) {
        connected = false;
        return false;
    }

    currentSampleRate = sampleRate;
    currentSampleAverage = sampleAverage;

    float dt = 1.0f / (float)currentSampleRate;

    // 1. High-Pass Filter (DC Baseline Tracking ~0.5Hz)
    float rc_hp = 1.0f / (2.0f * 3.14159265f * 0.5f);
    alpha_dc = rc_hp / (rc_hp + dt);
    if (alpha_dc < 0.90f) alpha_dc = 0.90f;
    if (alpha_dc > 0.99f) alpha_dc = 0.99f;

    // 2. Low-Pass Filter (Noise & Jitter Filter ~6.0Hz)
    float rc_lp = 1.0f / (2.0f * 3.14159265f * 6.0f);
    alpha_lp = dt / (rc_lp + dt);
    if (alpha_lp < 0.05f) alpha_lp = 0.05f;
    if (alpha_lp > 0.60f) alpha_lp = 0.60f;

    // Konfigurasi register MAX30102
    sensor.setup(
        MAX30102_LED_POWER_RED,
        currentSampleAverage,
        MAX30102_LED_MODE,
        currentSampleRate,
        MAX30102_PULSE_WIDTH,
        MAX30102_ADC_RANGE
    );

    sensor.setPulseAmplitudeRed(MAX30102_LED_POWER_RED);
    sensor.setPulseAmplitudeIR(MAX30102_LED_POWER_IR);
    sensor.setPulseAmplitudeGreen(0);
    sensor.enableFIFORollover();
    sensor.clearFIFO();

    // Reset filter states
    dc_ir = 0.0f;
    dc_red = 0.0f;
    lp_ir = 0.0f;
    lp_red = 0.0f;

    connected = true;
    Serial.printf("[MAX30102] Set Rate: %d Hz (Avg: %dx, AlphaLP: %.3f)\n",
                  currentSampleRate, currentSampleAverage, alpha_lp);
    return true;
}

bool MAX30102Driver::isConnected() {
    return connected;
}

void MAX30102Driver::setLedPower(uint8_t redPower, uint8_t irPower) {
    if (!connected) return;
    sensor.setPulseAmplitudeRed(redPower);
    sensor.setPulseAmplitudeIR(irPower);
}

void MAX30102Driver::reset() {
    if (!connected) return;
    sensor.softReset();
    delay(50);
    begin();
}

bool MAX30102Driver::readSample(PPGPacket &packet) {
    if (!connected) {
        static uint32_t last_retry = 0;
        if (millis() - last_retry > 2000) {
            last_retry = millis();
            begin();
        }
        return false;
    }

    sensor.check();

    if (!sensor.available()) {
        return false;
    }

    // Ambil data RAW MURNI dari FIFO sensor
    uint32_t raw_red = sensor.getFIFORed();
    uint32_t raw_ir = sensor.getFIFOIR();
    sensor.nextSample();

    uint32_t now = millis();

    // Deteksi keberadaan jari (threshold 10,000)
    bool finger_present = (raw_ir >= FINGER_DETECT_THRESHOLD);

    float ac_ir = 0.0f;
    float ac_red = 0.0f;
    float smooth_ir = 0.0f;
    float smooth_red = 0.0f;

    if (finger_present) {
        if (dc_ir < 500.0f) {
            dc_ir = (float)raw_ir;
            lp_ir = 0.0f;
        }
        if (dc_red < 500.0f) {
            dc_red = (float)raw_red;
            lp_red = 0.0f;
        }

        // 1. High-Pass DC Baseline Tracking (Low-pass filter on DC)
        dc_ir = (alpha_dc * dc_ir) + ((1.0f - alpha_dc) * (float)raw_ir);
        dc_red = (alpha_dc * dc_red) + ((1.0f - alpha_dc) * (float)raw_red);

        // 2. Inversi polaritas sinyal optik: -(Raw - DC) = DC - Raw
        // Karena serapan cahaya naik saat sistol (nilai photodiode turun),
        // inversi ini membuat Systolic Peak muncul mengarah ke atas (puncak positif),
        // diikuti oleh Dicrotic Notch dan Diastolic Peak sesuai standar medis fisiologis.
        ac_ir = dc_ir - (float)raw_ir;
        ac_red = dc_red - (float)raw_red;

        // 3. Low-Pass Filter untuk menghilangkan noise getaran optik/ADC
        lp_ir = lp_ir + alpha_lp * (ac_ir - lp_ir);
        lp_red = lp_red + alpha_lp * (ac_red - lp_red);

        smooth_ir = lp_ir;
        smooth_red = lp_red;
    } else {
        dc_ir = 0.0f;
        dc_red = 0.0f;
        lp_ir = 0.0f;
        lp_red = 0.0f;
    }

    // 1. RAW DATA (100% ASLI TANPA FILTER/INVERSI - UNTUK RASPBERRY PI 5 VIA UART)
    packet.timestamp_ms = now;
    packet.raw_ir = raw_ir;
    packet.raw_red = raw_red;
    packet.finger_present = finger_present;

    // 2. FILTERED & PHYSIOLOGICALLY ORIENTED DATA (UNTUK WEB APP VISUALIZER)
    packet.filtered_ac_ir = ac_ir;
    packet.filtered_ac_red = ac_red;
    packet.smooth_ac_ir = smooth_ir;
    packet.smooth_ac_red = smooth_red;

    return true;
}
