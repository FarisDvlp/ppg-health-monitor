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

// USB Serial ke PC / Laptop (High-Speed 921600 bps)
#define DEBUG_SERIAL_BAUD       921600

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
#define TASK_SENSOR_CORE        1       // Core 1: Khusus pembacaan sensor I2C MAX30102
#define TASK_COMM_CORE          0       // Core 0: Khusus pengiriman stream Serial ke PC

#define TASK_SENSOR_PRIORITY    2       // High Priority
#define TASK_COMM_PRIORITY      2       // High Priority

#define SENSOR_TASK_STACK_SIZE  4096
#define COMM_TASK_STACK_SIZE    4096

#define COMBINED_QUEUE_SIZE     1024    // Buffer 1024 sampel (~2 detik pada 500 Hz)

// ==========================================
// SENSOR AD8232 (ECG) CONFIGURATION
// ==========================================
#define AD8232_OUTPUT_PIN       5       // Analog Output ECG (ESP32-S3 ADC1_CH4)
#define AD8232_LO_MINUS_PIN     12      // Leads-Off Detect - (Digital Input)
#define AD8232_LO_PLUS_PIN      11      // Leads-Off Detect + (Digital Input)

#define AD8232_SAMPLE_RATE      500     // 500 Hz ECG Sampling
#define AD8232_INTERVAL_US      2000    // 2000 us = 500 Hz

// Struktur data paket ECG
struct ECGPacket {
    uint32_t timestamp_ms;
    uint16_t raw_adc;       // RAW ADC 12-bit (0 - 4095)
    bool leads_off;         // true jika LO+ atau LO- terdeteksi lepas
    bool lo_plus;           // Status pin LO+
    bool lo_minus;          // Status pin LO-
};

// Struktur data paket PPG
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

// Struktur data paket terpadu 500 Hz untuk dikirim via Serial ke PC
struct CombinedSamplePacket {
    uint32_t timestamp_ms;
    uint16_t ecg_raw;       // 0 - 4095
    uint8_t lo_status;      // 0 = Normal, 1 = LO+, 2 = LO-, 3 = LO+ & LO-
    uint32_t ppg_ir;        // Raw IR MAX30102
    uint32_t ppg_red;       // Raw RED MAX30102
    uint8_t finger_status;  // 0 = No Finger, 1 = Finger Detected
};

#endif // CONFIG_H
