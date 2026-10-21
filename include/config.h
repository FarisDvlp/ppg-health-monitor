#ifndef CONFIG_H
#define CONFIG_H

#include <Arduino.h>

// ==========================================
// PINOUT CONFIGURATION (ESP32-S3)
// ==========================================
// I2C Pins for MAX30102 Sensor
#define I2C_SDA_PIN             8
#define I2C_SCL_PIN             9
#define I2C_CLOCK_SPEED         400000  // 400 kHz Fast Mode

// Hardware UART2 for Raspberry Pi 5 Communication
#define RPI_UART_NUM            2
#define RPI_UART_TX_PIN         17      // ESP32 TX2 -> RPi 5 RX (GPIO 15 / UART0_RXD)
#define RPI_UART_RX_PIN         16      // ESP32 RX2 <- RPi 5 TX (GPIO 14 / UART0_TXD)
#define RPI_UART_BAUD           115200  // Baudrate ke Raspberry Pi 5

// Debug Serial (USB Serial0)
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
// Mode: 2 = Red + IR (SpO2 & Heart Rate Mode)
#define MAX30102_LED_MODE       2
// Sample Average: 1 (tanpa averaging agar rate aktual sesuai dengan sample rate)
#define MAX30102_DEFAULT_AVG    1
// ADC Range: 16384 (18-bit full dynamic range)
#define MAX30102_ADC_RANGE      16384
// Default Sample Rate: 100 Hz
#define MAX30102_DEFAULT_SRATE  100
// Pulse Width: 411 us (18-bit ADC resolution)
#define MAX30102_PULSE_WIDTH    411
// LED Brightness: 0x24 (~7.2mA)
#define MAX30102_LED_POWER_RED  0x24
#define MAX30102_LED_POWER_IR   0x24

// Threshold raw IR value untuk deteksi jari (10,000)
#define FINGER_DETECT_THRESHOLD 10000

// ==========================================
// FREERTOS DUAL-CORE CONFIGURATION
// ==========================================
#define TASK_SENSOR_CORE        1       // Core 1: Khusus pembacaan sensor I2C
#define TASK_COMM_CORE          0       // Core 0: Khusus WiFi, WebServer, WebSocket, dan UART

#define TASK_SENSOR_PRIORITY    2       // High Priority
#define TASK_COMM_PRIORITY      1       // Normal Priority

#define SENSOR_TASK_STACK_SIZE  4096
#define COMM_TASK_STACK_SIZE    8192

#define PPG_QUEUE_SIZE          256     // Buffer besar hingga 256 sampel

// Struktur data paket PPG yang ditransfer antar core & dikirim ke RPi 5
struct PPGPacket {
    uint32_t timestamp_ms;
    uint32_t raw_ir;        // RAW MURNI untuk UART RPi 5
    uint32_t raw_red;       // RAW MURNI untuk UART RPi 5
    bool finger_present;
    float filtered_ac_ir;   // AC Component (DC removed)
    float filtered_ac_red;
    float smooth_ac_ir;     // Denoised Low-Pass Filtered untuk Web Display
    float smooth_ac_red;    // Denoised Low-Pass Filtered untuk Web Display
};

#endif // CONFIG_H
