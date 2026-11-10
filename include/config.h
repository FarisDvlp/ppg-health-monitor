#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// ==========================================
// PINOUT CONFIGURATION (ESP32-S3)
// ==========================================
// I2C MAX30102
#define I2C_SDA_PIN             8
#define I2C_SCL_PIN             9
#define I2C_CLOCK_SPEED         400000

// AD8232 ECG: pastikan pin sesuai wiring fisik yang sudah diverifikasi
#define ECG_OUTPUT_PIN          5
#define ECG_LO_PLUS_PIN         17
#define ECG_LO_MINUS_PIN        18
#define ECG_SAMPLE_RATE         500
#define ECG_QUEUE_SIZE          1000
#define ECG_BATCH_SIZE          25
#define ECG_TASK_STACK_SIZE     4096

// USB Serial ke laptop
#define DEBUG_SERIAL_BAUD       115200

// ==========================================
// WIFI ACCESS POINT CONFIGURATION
// ==========================================
#define WIFI_AP_SSID            "ESP32_PPG_MONITOR"
#define WIFI_AP_PASSWORD        "12345678"
#define WIFI_AP_CHANNEL         1
#define WIFI_AP_MAX_CONN        4
#define HTTP_SERVER_PORT        80
#define WS_SERVER_PATH          "/ws"

// ==========================================
// SENSOR MAX30102 CONFIGURATION
// ==========================================
#define MAX30102_LED_MODE       2
#define MAX30102_DEFAULT_AVG    1
#define MAX30102_ADC_RANGE      16384
#define MAX30102_DEFAULT_SRATE  100
#define MAX30102_PULSE_WIDTH    411
#define MAX30102_LED_POWER_RED  0x24
#define MAX30102_LED_POWER_IR   0x24
#define FINGER_DETECT_THRESHOLD 20000

// ==========================================
// FREERTOS CONFIGURATION
// ==========================================
#define TASK_SENSOR_CORE        1
#define TASK_COMM_CORE          0
#define TASK_SENSOR_PRIORITY    2
#define TASK_COMM_PRIORITY      1
#define SENSOR_TASK_STACK_SIZE  4096
#define COMM_TASK_STACK_SIZE    8192
#define PPG_QUEUE_SIZE          256

// Struktur data MAX30102; dipertahankan sesuai driver yang sudah ada.
struct PPGPacket {
    uint32_t timestamp_ms;
    uint32_t raw_ir;
    uint32_t raw_red;
    bool finger_present;
    float filtered_ac_ir;
    float filtered_ac_red;
    float smooth_ac_ir;
    float smooth_ac_red;
};

// Satu sampel mentah AD8232. Timestamp berasal dari esp_timer_get_time().
// lead_off bit 0 = LO+, bit 1 = LO- (1 menandakan pin HIGH / lead-off).
struct ECGPacket {
    uint64_t timestamp_us;
    uint16_t raw_value;
    uint8_t lead_off;
};

#endif // CONFIG_H
