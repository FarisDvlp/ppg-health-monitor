#include <Arduino.h>
#include <WiFi.h>
#include <esp_timer.h>

#include "config.h"
#include "max30102_driver.h"
#include "ad8232_driver.h"

// FreeRTOS Handles
QueueHandle_t sampleQueue = NULL;
TaskHandle_t sensorTaskHandle = NULL;
TaskHandle_t commTaskHandle = NULL;
esp_timer_handle_t ecgTimerHandle = NULL;

// Shared PPG State (diperbarui oleh Core 1, dibaca oleh Timer 500 Hz)
volatile uint32_t latestPpgIr = 0;
volatile uint32_t latestPpgRed = 0;
volatile uint8_t latestFingerStatus = 0;

// Konfigurasi dinamis MAX30102 yang dapat diubah via Serial dari aplikasi Python
volatile uint16_t activeSampleRate = MAX30102_DEFAULT_SRATE;
volatile uint8_t activeSampleAvg = MAX30102_DEFAULT_AVG;
volatile bool configNeedsUpdate = false;

// ============================================================
// CALLBACK HARDWARE TIMER 500 HZ (SETIAP 2000 US)
// ============================================================
static void ecgTimerCallback(void* arg) {
    ECGPacket ecgPkt;
    if (ecgSensor.sample(ecgPkt)) {
        CombinedSamplePacket combined;
        combined.timestamp_ms = ecgPkt.timestamp_ms;
        combined.ecg_raw = ecgPkt.raw_adc;
        combined.lo_status = (ecgPkt.lo_plus ? 1 : 0) | (ecgPkt.lo_minus ? 2 : 0);
        combined.ppg_ir = latestPpgIr;
        combined.ppg_red = latestPpgRed;
        combined.finger_status = latestFingerStatus;

        xQueueSend(sampleQueue, &combined, 0);
    }
}

// ============================================================
// CORE 1: MAX30102 I2C SENSOR TASK
// ============================================================
void vTaskSensorLoop(void *pvParameters) {
    ppgSensor.begin();

    PPGPacket ppgSample;

    for (;;) {
        if (configNeedsUpdate) {
            configNeedsUpdate = false;
            ppgSensor.setSampleRate(activeSampleRate, activeSampleAvg);
        }

        // Kuras seluruh sampel baru di FIFO MAX30102
        while (ppgSensor.readSample(ppgSample)) {
            latestPpgIr = ppgSample.raw_ir;
            latestPpgRed = ppgSample.raw_red;
            latestFingerStatus = ppgSample.finger_present ? 1 : 0;
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

// ============================================================
// CORE 0: USB SERIAL STREAMING & COMMAND HANDLER TASK
// ============================================================
void vTaskCommLoop(void *pvParameters) {
    CombinedSamplePacket pkt;
    char txBuf[512];
    int txLen = 0;
    String cmdBuffer = "";

    for (;;) {
        // 1. Kuras Queue dan kirim dalam blok buffer agar USB CDC maksimal (500 Hz mulus)
        txLen = 0;
        int count = 0;
        while (count < 8 && xQueueReceive(sampleQueue, &pkt, 0) == pdPASS) {
            int n = snprintf(txBuf + txLen, sizeof(txBuf) - txLen,
                             "%lu,%u,%u,%lu,%lu,%u\n",
                             (unsigned long)pkt.timestamp_ms,
                             (unsigned int)pkt.ecg_raw,
                             (unsigned int)pkt.lo_status,
                             (unsigned long)pkt.ppg_ir,
                             (unsigned long)pkt.ppg_red,
                             (unsigned int)pkt.finger_status);
            if (n > 0 && (txLen + n) < (int)sizeof(txBuf)) {
                txLen += n;
            }
            count++;
        }

        if (txLen > 0) {
            Serial.write((const uint8_t*)txBuf, txLen);
        } else {
            // Jika queue kosong, tunggu hingga 2ms untuk sampel berikutnya
            if (xQueueReceive(sampleQueue, &pkt, pdMS_TO_TICKS(2)) == pdPASS) {
                int n = snprintf(txBuf, sizeof(txBuf),
                                 "%lu,%u,%u,%lu,%lu,%u\n",
                                 (unsigned long)pkt.timestamp_ms,
                                 (unsigned int)pkt.ecg_raw,
                                 (unsigned int)pkt.lo_status,
                                 (unsigned long)pkt.ppg_ir,
                                 (unsigned long)pkt.ppg_red,
                                 (unsigned int)pkt.finger_status);
                if (n > 0) {
                    Serial.write((const uint8_t*)txBuf, n);
                }
            }
        }

        // 2. Cek apakah ada perintah konfigurasi dari aplikasi Python (format: "RATE:100,AVG:1\n")
        while (Serial.available() > 0) {
            char c = (char)Serial.read();
            if (c == '\n' || c == '\r') {
                cmdBuffer.trim();
                if (cmdBuffer.startsWith("RATE:")) {
                    int commaIdx = cmdBuffer.indexOf(',');
                    int rateVal = 100;
                    int avgVal = 1;
                    if (commaIdx > 5) {
                        rateVal = cmdBuffer.substring(5, commaIdx).toInt();
                        int avgIdx = cmdBuffer.indexOf("AVG:");
                        if (avgIdx > 0) {
                            avgVal = cmdBuffer.substring(avgIdx + 4).toInt();
                        }
                    } else {
                        rateVal = cmdBuffer.substring(5).toInt();
                    }
                    if (rateVal >= 50 && rateVal <= 800) {
                        activeSampleRate = (uint16_t)rateVal;
                        activeSampleAvg = (uint8_t)((avgVal > 0) ? avgVal : 1);
                        configNeedsUpdate = true;
                    }
                }
                cmdBuffer = "";
            } else if (cmdBuffer.length() < 64) {
                cmdBuffer += c;
            }
        }
    }
}

// ============================================================
// MAIN SETUP & LOOP
// ============================================================
void setup() {
    // 1. Matikan total Radio Wi-Fi & Bluetooth untuk mengeliminasi noise RF pada ADC & beban CPU
    WiFi.mode(WIFI_OFF);
    btStop();

    // 2. Inisialisasi High-Speed USB Serial (921600 bps)
    Serial.begin(DEBUG_SERIAL_BAUD);
    Serial.setTxBufferSize(2048);
    delay(500);

    // 3. Buat FreeRTOS Queue untuk stream 500 Hz
    sampleQueue = xQueueCreate(COMBINED_QUEUE_SIZE, sizeof(CombinedSamplePacket));
    if (sampleQueue == NULL) {
        while (1) { delay(1000); }
    }

    // 4. Inisialisasi Hardware Sensor AD8232 (ECG)
    ecgSensor.begin();

    // 5. Jalankan Task Pembacaan MAX30102 pada Core 1
    xTaskCreatePinnedToCore(
        vTaskSensorLoop,
        "SensorTask",
        SENSOR_TASK_STACK_SIZE,
        NULL,
        TASK_SENSOR_PRIORITY,
        &sensorTaskHandle,
        TASK_SENSOR_CORE
    );

    // 6. Jalankan Task Pengiriman Serial pada Core 0
    xTaskCreatePinnedToCore(
        vTaskCommLoop,
        "CommTask",
        COMM_TASK_STACK_SIZE,
        NULL,
        TASK_COMM_PRIORITY,
        &commTaskHandle,
        TASK_COMM_CORE
    );

    // 7. Mulai Hardware Timer 500 Hz (2000 us) untuk AD8232 + Sinkronisasi Paket
    const esp_timer_create_args_t ecgTimerArgs = {
        .callback = &ecgTimerCallback,
        .arg = NULL,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ad8232_500hz",
        .skip_unhandled_events = true
    };
    if (esp_timer_create(&ecgTimerArgs, &ecgTimerHandle) == ESP_OK) {
        esp_timer_start_periodic(ecgTimerHandle, AD8232_INTERVAL_US);
    }
}

void loop() {
    vTaskDelete(NULL);
}
