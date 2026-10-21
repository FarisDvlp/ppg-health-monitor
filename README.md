# Cardiovascular Health Multi Parameter Monitoring Based on PPG

Sistem akuisisi data sinyal *Photoplethysmogram* (PPG) berbasis **ESP32** dan sensor optik **MAX30102** dengan arsitektur **FreeRTOS Dual-Core**, pengiriman data stream ke **Raspberry Pi 5** via UART, serta visualisasi sinyal real-time melalui **Web Application (WebSocket)**.

---

## 1. Arsitektur Dual-Core (FreeRTOS)

ESP32 memiliki prosesor 2 inti (Core 0 dan Core 1). Pada firmware ini, tugas dipisahkan secara terisolasi untuk menjamin kestabilan sampling sensor:

| Core | Task | Prioritas | Tanggung Jawab |
| :--- | :--- | :--- | :--- |
| **Core 1** | `SensorTask` | High (Priority 2) | Membaca I2C FIFO MAX30102 (100 Hz), filter DC removal, deteksi jari, dan push data ke FreeRTOS Queue. |
| **Core 0** | `CommTask` | Normal (Priority 1) | Mengambil data dari Queue, mengirim stream via UART (Serial2) ke Raspberry Pi 5, serta broadcast via WebSocket ke Web Dashboard. |

---

## 2. Skema Wiring / Pengkabelan

### A. ESP32-S3 ke Sensor MAX30102 (I2C)

| Pin Sensor MAX30102 | Pin ESP32-S3 | Keterangan |
| :--- | :--- | :--- |
| **VIN / VCC** | **3.3V** | Tegangan operasional (Gunakan 3.3V stabil) |
| **GND** | **GND** | Ground bersama |
| **SDA** | **GPIO 8** | I2C Data line (Fast Mode 400kHz) |
| **SCL** | **GPIO 9** | I2C Clock line |

> **Catatan Modul MAX30102**: Pastikan modul Anda menggunakan pull-up resistor yang terhubung ke 3.3V.

---

### B. ESP32-S3 ke Raspberry Pi 5 (UART)

| Pin ESP32-S3 | Pin Raspberry Pi 5 | Keterangan |
| :--- | :--- | :--- |
| **GPIO 17 (TX2)** | **Pin 10 (GPIO 15 / UART0_RXD)** | ESP32-S3 mengirim data ke RPi 5 |
| **GPIO 16 (RX2)** | **Pin 8 (GPIO 14 / UART0_TXD)** | *(Opsional)* RPi 5 mengirim perintah balik |
| **GND** | **Pin 6 / 9 / 14 (GND)** | **Wajib dihubungkan (Common Ground)** |

---

## 3. Format Protokol UART (ESP32-S3 ➔ Raspberry Pi 5)

ESP32-S3 mengirimkan data baris demi baris dalam format **JSON Lines** dengan baudrate **115200 bps** (8 data bits, no parity, 1 stop bit):

```json
{"t":145230,"ir":85412,"red":82150,"f":1}
```

### Penjelasan Field:
- `t` (*integer*): Timestamp ESP32 dalam milidetik (`millis()`).
- `ir` (*integer*): Nilai ADC mentah kanal Infrared (Raw IR).
- `red` (*integer*): Nilai ADC mentah kanal Red (Raw Red).
- `f` (*integer: 0 atau 1*): Status deteksi jari (`1` = ada jari, `0` = tidak ada jari).

---

## 4. Cara Mengakses Web App Visualizer

1. Nyalakan ESP32 yang telah terprogram.
2. Pada Laptop atau Smartphone, cari jaringan Wi-Fi bernama:
   - **SSID**: `ESP32_PPG_MONITOR`
   - **Password**: `12345678`
3. Buka browser (Chrome / Edge / Safari), lalu akses alamat:
   ```
   http://192.168.4.1
   ```
4. Fitur Web Dashboard:
   - **Oscilloscope Waveform**: Menampilkan trace sinyal IR (Cyan) dan RED (Coral) dengan auto-scaling 60 FPS.
   - **Mode Filter**: Pilihan tampilan sinyal pulsasi AC (denyut nadi terpusat) atau sinyal Raw DC+AC.
   - **Vital Stats Cards**: Menampilkan estimasi BPM, status kontak jari, dan nilai mentah ADC.
   - **UART Terminal Preview**: Memantau paket data yang sedang dialirkan ke Raspberry Pi 5.

---

## 5. Contoh Script Python Receiver (Untuk Raspberry Pi 5)

Berikut adalah contoh skrip Python sederhana yang dapat dijalankan di Raspberry Pi 5 untuk membaca stream UART dari ESP32:

```python
import serial
import json
import time

# Konfigurasi UART pada Raspberry Pi 5 (/dev/ttyAMA0 atau /dev/serial0)
SERIAL_PORT = '/dev/serial0'
BAUD_RATE = 115200

def main():
    print(f"Membuka port serial {SERIAL_PORT} pada {BAUD_RATE} bps...")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1.0)
        ser.reset_input_buffer()
        print("Mulai menerima data PPG dari ESP32:")

        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith('{') and line.endswith('}'):
                    try:
                        data = json.loads(line)
                        timestamp = data.get('t', 0)
                        raw_ir = data.get('ir', 0)
                        raw_red = data.get('red', 0)
                        finger = data.get('f', 0)
                        bpm = data.get('bpm', 0.0)

                        print(f"[{timestamp} ms] Finger: {finger} | IR: {raw_ir:6d} | RED: {raw_red:6d} | BPM: {bpm:.1f}")
                    except json.JSONDecodeError:
                        pass
    except serial.SerialException as e:
        print(f"Error Serial: {e}")
    except KeyboardInterrupt:
        print("\nProgram dihentikan.")
    finally:
        if 'ser' in locals() and ser.is_open:
            ser.close()

if __name__ == '__main__':
    main()
```

---

## 6. Cara Upload / Flash Firmware dengan PlatformIO

### Menggunakan VS Code:
1. Buka folder proyek ini di VS Code yang sudah terinstal extension **PlatformIO IDE**.
2. Hubungkan ESP32 ke port USB PC/Laptop.
3. Klik tombol **PlatformIO: Upload** (ikon panah ke kanan `→` di status bar bawah).
4. Buka **PlatformIO: Serial Monitor** dengan baudrate 115200.

### Menggunakan Command Line (CLI):
```bash
# Compile proyek
pio run

# Upload ke ESP32
pio run --target upload

# Buka Serial Monitor
pio device monitor -b 115200
```

