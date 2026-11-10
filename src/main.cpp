#include <Arduino.h>
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoJson.h>
#include <esp_timer.h>

#include "config.h"
#include "max30102_driver.h"
#include "web_assets.h"

// FreeRTOS Handles
QueueHandle_t ppgQueue;
QueueHandle_t ecgQueue;
TaskHandle_t sensorTaskHandle = NULL;
TaskHandle_t commTaskHandle = NULL;
TaskHandle_t ecgTaskHandle = NULL;
esp_timer_handle_t ecgTimerHandle = nullptr;
volatile uint32_t ecgQueueDropCount = 0;
volatile uint32_t ecgTimerMissedCount = 0;

// Web Server & WebSocket Instances
AsyncWebServer server(HTTP_SERVER_PORT);
AsyncWebSocket ws(WS_SERVER_PATH);

// UART2 untuk Raspberry Pi dinonaktifkan sementara; GPIO17 dipakai LO+ AD8232.

// Konfigurasi dinamis yang dapat diubah dari Web
volatile uint16_t activeSampleRate = MAX30102_DEFAULT_SRATE;
volatile uint8_t activeSampleAvg = MAX30102_DEFAULT_AVG;
volatile bool configNeedsUpdate = false;

// ============================================================
// WEBSOCKET EVENT HANDLER
// ============================================================
void onWsEvent(AsyncWebSocket *server, AsyncWebSocketClient *client, AwsEventType type, void *arg, uint8_t *data, size_t len) {
    switch (type) {
        case WS_EVT_CONNECT:
            // Tidak menulis log ke USB Serial saat akuisisi berlangsung agar baris data tidak terganggu.
            break;
        case WS_EVT_DISCONNECT:
            // Tidak menulis log ke USB Serial saat akuisisi berlangsung agar baris data tidak terganggu.
            break;
        case WS_EVT_DATA: {
            AwsFrameInfo *info = (AwsFrameInfo*)arg;
            if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT) {
                JsonDocument doc;
                DeserializationError err = deserializeJson(doc, data, len);
                if (!err) {
                    const char* cmd = doc["cmd"] | "";
                    if (strcmp(cmd, "set_rate") == 0) {
                        uint16_t newRate = doc["rate"] | 100;
                        uint8_t newAvg = doc["avg"] | 1;
                        if (newRate >= 50 && newRate <= 100) {
                            activeSampleRate = newRate;
                            activeSampleAvg = (newAvg > 0) ? newAvg : 1;
                            configNeedsUpdate = true;
                            // Log runtime dinonaktifkan agar stream PPG/ECG tidak tercampur teks debug.
                        }
                    }
                }
            }
            break;
        }
        case WS_EVT_PONG:
        case WS_EVT_ERROR:
            break;
    }
}

// ============================================================
// CORE 1: SENSOR TASK (HIGH PRIORITY)
// ============================================================
void vTaskSensorLoop(void *pvParameters) {
    Serial.printf("[Core 1] Sensor Task berjalan pada Core %d\n", xPortGetCoreID());

    if (!ppgSensor.begin()) {
        Serial.println("[Core 1] Sensor MAX30102 belum terdeteksi. Mencoba ulang...");
    }

    PPGPacket sample;

    for (;;) {
        // Update konfigurasi sensor jika ada request dari web
        if (configNeedsUpdate) {
            configNeedsUpdate = false;
            ppgSensor.setSampleRate(activeSampleRate, activeSampleAvg);
            // Jangan menulis debug ke Serial setelah stream akuisisi dimulai.
        }

        // Kuras SEMUA data yang ada di FIFO sensor seketika
        while (ppgSensor.readSample(sample)) {
            if (xQueueSend(ppgQueue, &sample, 0) != pdPASS) {
                // Drop jika queue penuh
            }
        }

        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

// ============================================================
// CORE 0: COMMUNICATION & WEB SERVER TASK
// ============================================================
void vTaskCommLoop(void *pvParameters) {
    Serial.printf("[Core 0] Communication Task berjalan pada Core %d\n", xPortGetCoreID());

    // UART2 sengaja tidak diinisialisasi selama pengambilan data melalui USB.
    // GPIO17 saat ini digunakan oleh AD8232 LO+.

    // 1. Inisialisasi Wi-Fi Access Point
    WiFi.mode(WIFI_AP);
    WiFi.setSleep(false); // MATIKAN POWER SAVE WIFI AGAR KONEKSI STABIL 100% TANPA PUTUS
    WiFi.softAP(WIFI_AP_SSID, WIFI_AP_PASSWORD, WIFI_AP_CHANNEL, 0, WIFI_AP_MAX_CONN);

    IPAddress apIP = WiFi.softAPIP();
    Serial.printf("[WiFi] Access Point Aktif!\n");
    Serial.printf("       SSID : %s\n", WIFI_AP_SSID);
    Serial.printf("       IP   : %s\n", apIP.toString().c_str());

    // 3. Konfigurasi HTTP Web Server & WebSocket
    ws.onEvent(onWsEvent);
    server.addHandler(&ws);

    // Route halaman utama (Web UI)
    server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", INDEX_HTML);
    });

    // Endpoint status device (JSON API)
    server.on("/api/status", HTTP_GET, [](AsyncWebServerRequest *request) {
        JsonDocument doc;
        doc["device"] = "ESP32-S3-PPG-Monitor";
        doc["sensor_connected"] = ppgSensor.isConnected();
        doc["sample_rate"] = ppgSensor.getSampleRate();
        doc["sample_avg"] = ppgSensor.getSampleAverage();
        doc["free_heap"] = ESP.getFreeHeap();
        doc["uptime_s"] = millis() / 1000;
        doc["ws_clients"] = ws.count();
        doc["ecg_queue_drops"] = (uint32_t)ecgQueueDropCount;
        doc["ecg_timer_missed"] = (uint32_t)ecgTimerMissedCount;

        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });

    // Endpoint set sample rate via HTTP: /api/set_rate?rate=100&avg=1
    server.on("/api/set_rate", HTTP_GET, [](AsyncWebServerRequest *request) {
        if (request->hasParam("rate")) {
            uint16_t r = request->getParam("rate")->value().toInt();
            uint8_t a = request->hasParam("avg") ? request->getParam("avg")->value().toInt() : 1;
            if (r >= 50 && r <= 100) {
                activeSampleRate = r;
                activeSampleAvg = (a > 0) ? a : 1;
                configNeedsUpdate = true;
                request->send(200, "application/json", "{\"status\":\"ok\",\"rate\":" + String(r) + "}");
                return;
            }
        }
        request->send(400, "application/json", "{\"error\":\"invalid_parameters\"}");
    });

    server.begin();
    Serial.println("[Web Server] Web dashboard aktif pada port 80.");

    // Buffer batching untuk WebSocket
    PPGPacket sample;
    const uint8_t MAX_BATCH = 20;
    PPGPacket batchBuffer[MAX_BATCH];
    uint8_t batchCount = 0;
    uint32_t lastWsSendTime = millis();
    uint32_t lastWsCleanup = millis();

    for (;;) {
        // Ambil data dari Queue
        if (xQueueReceive(ppgQueue, &sample, pdMS_TO_TICKS(10)) == pdPASS) {
            
            // Format ringkas untuk menghemat bandwidth serial saat ECG 500 Hz aktif.
            // Format: P,timestamp_ms,raw_ir,raw_red,finger_present
            Serial.printf("P,%lu,%lu,%lu,%d\n",
                          (unsigned long)sample.timestamp_ms,
                          (unsigned long)sample.raw_ir,
                          (unsigned long)sample.raw_red,
                          sample.finger_present ? 1 : 0);

            // Simpan ke batch WebSocket jika ada client aktif
            if (ws.count() > 0 && batchCount < MAX_BATCH) {
                batchBuffer[batchCount++] = sample;
            }
        }

        // -------------------------------------------------------------
        // Ambil sampel ECG dari queue tanpa blocking. Kirim per batch untuk
        // mengurangi overhead serial dan mempertahankan timestamp tiap sampel.
        // Format: E,t0_us,n,raw0,flags0,dt1,raw1,flags1,...
        // dt adalah selang timestamp aktual dalam mikrodetik.
        // -------------------------------------------------------------
        static ECGPacket ecgBatch[ECG_BATCH_SIZE];
        static uint8_t ecgBatchCount = 0;
        static uint32_t lastEcgFlushMs = millis();
        ECGPacket ecgSample;

        while (ecgBatchCount < ECG_BATCH_SIZE &&
               xQueueReceive(ecgQueue, &ecgSample, 0) == pdPASS) {
            ecgBatch[ecgBatchCount++] = ecgSample;
        }

        const uint32_t now = millis();
        if (ecgBatchCount > 0 &&
            (ecgBatchCount >= ECG_BATCH_SIZE || (now - lastEcgFlushMs) >= 50)) {
            Serial.printf("E,%llu,%u",
                (unsigned long long)ecgBatch[0].timestamp_us,
                (unsigned int)ecgBatchCount);

            // Sampel pertama: raw, flags. Sampel berikutnya: delta_us, raw, flags.
            Serial.printf(",%u,%u",
                (unsigned int)ecgBatch[0].raw_value,
                (unsigned int)ecgBatch[0].lead_off);
            for (uint8_t i = 1; i < ecgBatchCount; ++i) {
                uint64_t delta64 = ecgBatch[i].timestamp_us - ecgBatch[i - 1].timestamp_us;
                uint32_t deltaUs = (delta64 > 0xFFFFFFFFULL) ? 0xFFFFFFFFUL : (uint32_t)delta64;
                Serial.printf(",%lu,%u,%u",
                    (unsigned long)deltaUs,
                    (unsigned int)ecgBatch[i].raw_value,
                    (unsigned int)ecgBatch[i].lead_off);
            }
            Serial.println();
            ecgBatchCount = 0;
            lastEcgFlushMs = now;
        }

        // -------------------------------------------------------------
        // BROADCAST KE WEBSOCKET DENGAN INTERVAL STABIL (~40ms / 25 FPS)
        // -------------------------------------------------------------
        if (now - lastWsSendTime >= 40) {
            lastWsSendTime = now;

            if (ws.count() > 0 && batchCount > 0) {
                JsonDocument doc;
                doc["r"] = (int)activeSampleRate;
                JsonArray dataArr = doc["d"].to<JsonArray>();

                for (uint8_t i = 0; i < batchCount; i++) {
                    JsonArray item = dataArr.add<JsonArray>();
                    item.add(batchBuffer[i].timestamp_ms);
                    item.add(batchBuffer[i].raw_ir);
                    item.add(batchBuffer[i].raw_red);
                    item.add(round(batchBuffer[i].smooth_ac_ir * 10.0f) / 10.0f);
                    item.add(round(batchBuffer[i].smooth_ac_red * 10.0f) / 10.0f);
                    item.add(batchBuffer[i].finger_present ? 1 : 0);
                }

                String jsonString;
                serializeJson(doc, jsonString);

                // Kirim secara aman ke client tanpa memicu buffer congestion
                for (AsyncWebSocketClient &c : ws.getClients()) {
                    if (c.status() == WS_CONNECTED && !c.queueIsFull()) {
                        c.text(jsonString);
                    }
                }

                batchCount = 0;
            }
        }

        // Cleanup client tidak aktif setiap 10 detik
        if (now - lastWsCleanup > 10000) {
            lastWsCleanup = now;
            ws.cleanupClients();
        }
    }
}

// ============================================================
// ECG SAMPLING TIMER + TASK
// esp_timer memicu task tiap 1/ECG_SAMPLE_RATE detik. ADC dibaca di task,
// bukan di callback timer, agar tidak menjalankan analogRead di context timer.
// ============================================================
static void ecgSampleTimerCallback(void *arg) {
    (void)arg;
    if (ecgTaskHandle != NULL) {
        xTaskNotifyGive(ecgTaskHandle);
    }
}

void vTaskEcgLoop(void *pvParameters) {
    (void)pvParameters;
    pinMode(ECG_OUTPUT_PIN, INPUT);
    pinMode(ECG_LO_PLUS_PIN, INPUT);
    pinMode(ECG_LO_MINUS_PIN, INPUT);
    analogReadResolution(12);

    Serial.printf("[ECG] Task aktif pada Core %d, target %d Hz, ADC GPIO%d\n",
                  xPortGetCoreID(), ECG_SAMPLE_RATE, ECG_OUTPUT_PIN);

    for (;;) {
        // Jumlah notifikasi >1 menandakan task sempat tertinggal dari timer.
        uint32_t notifications = ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        if (notifications == 0) {
            continue;
        }
        if (notifications > 1) {
            ecgTimerMissedCount += (notifications - 1);
        }

        ECGPacket sample;
        sample.timestamp_us = (uint64_t)esp_timer_get_time();
        int adcValue = analogRead(ECG_OUTPUT_PIN);
        int loPlus = digitalRead(ECG_LO_PLUS_PIN);
        int loMinus = digitalRead(ECG_LO_MINUS_PIN);

        sample.raw_value = (uint16_t)adcValue;
        sample.lead_off = (uint8_t)((loPlus ? 0x01 : 0x00) |
                                    (loMinus ? 0x02 : 0x00));

        if (xQueueSend(ecgQueue, &sample, 0) != pdPASS) {
            ecgQueueDropCount++;
        }
    }
}

// ============================================================
// MAIN SETUP & ARDUINO LOOP
// ============================================================
void setup() {
    Serial.begin(DEBUG_SERIAL_BAUD);
    delay(1000);

    Serial.println("\n==================================================");
    Serial.println("  ESP32-S3 PPG HEALTH MONITOR - MAX30102         ");
    Serial.println("==================================================");

    // Inisialisasi FreeRTOS Queue
    ppgQueue = xQueueCreate(PPG_QUEUE_SIZE, sizeof(PPGPacket));
    if (ppgQueue == NULL) {
        Serial.println("[Error] Gagal mengalokasikan FreeRTOS PPG Queue!");
        while (1) { delay(1000); }
    }

    ecgQueue = xQueueCreate(ECG_QUEUE_SIZE, sizeof(ECGPacket));
    if (ecgQueue == NULL) {
        Serial.println("[Error] Gagal mengalokasikan FreeRTOS ECG Queue!");
        while (1) { delay(1000); }
    }

    // 1. Task Sensor pada Core 1
    xTaskCreatePinnedToCore(
        vTaskSensorLoop,
        "SensorTask",
        SENSOR_TASK_STACK_SIZE,
        NULL,
        TASK_SENSOR_PRIORITY,
        &sensorTaskHandle,
        TASK_SENSOR_CORE
    );

    // 2. Task ECG pada Core 1. Sampling timer baru dimulai setelah handle tersedia.
    BaseType_t ecgTaskCreated = xTaskCreatePinnedToCore(
        vTaskEcgLoop,
        "ECGTask",
        ECG_TASK_STACK_SIZE,
        NULL,
        TASK_SENSOR_PRIORITY,
        &ecgTaskHandle,
        TASK_SENSOR_CORE
    );
    if (ecgTaskCreated != pdPASS) {
        Serial.println("[Error] Gagal membuat ECG task!");
        while (1) { delay(1000); }
    }

    esp_timer_create_args_t ecgTimerArgs = {};
    ecgTimerArgs.callback = &ecgSampleTimerCallback;
    ecgTimerArgs.arg = nullptr;
    ecgTimerArgs.dispatch_method = ESP_TIMER_TASK;
    ecgTimerArgs.name = "ecg_sample_timer";
    esp_err_t timerCreateResult = esp_timer_create(&ecgTimerArgs, &ecgTimerHandle);
    if (timerCreateResult != ESP_OK) {
        Serial.printf("[Error] Gagal membuat ECG timer: %d\n", (int)timerCreateResult);
        while (1) { delay(1000); }
    }

    const uint64_t ecgIntervalUs = 1000000ULL / ECG_SAMPLE_RATE;
    esp_err_t timerStartResult = esp_timer_start_periodic(ecgTimerHandle, ecgIntervalUs);
    if (timerStartResult != ESP_OK) {
        Serial.printf("[Error] Gagal memulai ECG timer: %d\n", (int)timerStartResult);
        while (1) { delay(1000); }
    }

    // Cetak semua pesan setup sebelum task komunikasi mulai mengirim stream.
    Serial.printf("[System] Task PPG + ECG siap. ECG target %d Hz; UART2 nonaktif.\n", ECG_SAMPLE_RATE);

    // Task komunikasi dibuat terakhir agar log setup tidak menyela baris data serial.
    BaseType_t commTaskCreated = xTaskCreatePinnedToCore(
        vTaskCommLoop,
        "CommTask",
        COMM_TASK_STACK_SIZE,
        NULL,
        TASK_COMM_PRIORITY,
        &commTaskHandle,
        TASK_COMM_CORE
    );
    if (commTaskCreated != pdPASS) {
        Serial.println("[Error] Gagal membuat communication task!");
        while (1) { delay(1000); }
    }
}

void loop() {
    vTaskDelete(NULL);
}
