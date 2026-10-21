#include <Arduino.h>
#include <WiFi.h>
#include <AsyncTCP.h>
#include <ESPAsyncWebServer.h>
#include <ArduinoJson.h>

#include "config.h"
#include "max30102_driver.h"
#include "web_assets.h"

// FreeRTOS Handles
QueueHandle_t ppgQueue;
TaskHandle_t sensorTaskHandle = NULL;
TaskHandle_t commTaskHandle = NULL;

// Web Server & WebSocket Instances
AsyncWebServer server(HTTP_SERVER_PORT);
AsyncWebSocket ws(WS_SERVER_PATH);

// Hardware Serial untuk Raspberry Pi 5
HardwareSerial SerialRPi(RPI_UART_NUM);

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
            Serial.printf("[WebSocket] Client connected: #%u from %s\n", client->id(), client->remoteIP().toString().c_str());
            break;
        case WS_EVT_DISCONNECT:
            Serial.printf("[WebSocket] Client disconnected: #%u\n", client->id());
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
                        activeSampleRate = newRate;
                        activeSampleAvg = newAvg;
                        configNeedsUpdate = true;
                        Serial.printf("[Web Command] Set Rate requested: %d Hz (Avg: %dx)\n", newRate, newAvg);
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
            Serial.printf("[Core 1] Sensor berhasil diubah ke %d Hz\n", activeSampleRate);
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

    // 1. Inisialisasi UART2 untuk Raspberry Pi 5
    SerialRPi.begin(RPI_UART_BAUD, SERIAL_8N1, RPI_UART_RX_PIN, RPI_UART_TX_PIN);
    Serial.printf("[UART] Serial2 aktif: TX=GPIO%d, RX=GPIO%d @ %d bps\n", 
                  RPI_UART_TX_PIN, RPI_UART_RX_PIN, RPI_UART_BAUD);

    // 2. Inisialisasi Wi-Fi Access Point
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

        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });

    // Endpoint set sample rate via HTTP: /api/set_rate?rate=100&avg=1
    server.on("/api/set_rate", HTTP_GET, [](AsyncWebServerRequest *request) {
        if (request->hasParam("rate")) {
            uint16_t r = request->getParam("rate")->value().toInt();
            uint8_t a = request->hasParam("avg") ? request->getParam("avg")->value().toInt() : 1;
            if (r >= 50 && r <= 800) {
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
            
            // -------------------------------------------------------------
            // A. RAW DATA 100% MURNI KE RASPBERRY PI 5 VIA UART (SERIAL2)
            // -------------------------------------------------------------
            SerialRPi.printf("{\"t\":%lu,\"ir\":%lu,\"red\":%lu,\"f\":%d}\n",
                             sample.timestamp_ms,
                             sample.raw_ir,
                             sample.raw_red,
                             sample.finger_present ? 1 : 0);

            // Simpan ke batch WebSocket jika ada client aktif
            if (ws.count() > 0 && batchCount < MAX_BATCH) {
                batchBuffer[batchCount++] = sample;
            }
        }

        // -------------------------------------------------------------
        // B. BROADCAST KE WEBSOCKET DENGAN INTERVAL STABIL (~40ms / 25 FPS)
        // -------------------------------------------------------------
        uint32_t now = millis();
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

    // 2. Task Komunikasi & Web pada Core 0
    xTaskCreatePinnedToCore(
        vTaskCommLoop,
        "CommTask",
        COMM_TASK_STACK_SIZE,
        NULL,
        TASK_COMM_PRIORITY,
        &commTaskHandle,
        TASK_COMM_CORE
    );

    Serial.println("[System] FreeRTOS Dual-Core Tasks siap.");
}

void loop() {
    vTaskDelete(NULL);
}
