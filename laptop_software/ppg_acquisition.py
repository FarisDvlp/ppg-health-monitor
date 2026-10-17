import csv
import json
import re
import os
from datetime import datetime

import serial
from serial import SerialException


# ==================================================
# KONFIGURASI SERIAL
# ==================================================
SERIAL_PORT = "COM3"       # Sesuaikan dengan port perangkat
BAUD_RATE = 115200         # Harus sama dengan pengirim data
SERIAL_TIMEOUT = 1         # Detik

OUTPUT_DIR = "data_ppg"


# ==================================================
# FUNGSI BANTU
# ==================================================
def sanitize_filename(text):
    """Menghapus karakter yang tidak aman untuk nama file."""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", text)
    return text.strip(" ._")


def parse_ppg_line(line):
    """
    Membaca satu baris JSON dari ESP32 atau perantara
    Raspberry Pi.

    Format yang diharapkan:
    {"t":939567,"ir":38875,"red":29989,"f":1}
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None, "not_json"

    if not isinstance(data, dict):
        return None, "invalid"

    required_keys = ("t", "ir", "red", "f")

    if not all(key in data for key in required_keys):
        return None, "invalid"

    try:
        timestamp_ms = int(data["t"])
        ir_value = int(data["ir"])
        red_value = int(data["red"])
        finger_status = int(data["f"])
    except (TypeError, ValueError, OverflowError):
        return None, "invalid"

    if timestamp_ms < 0 or ir_value < 0 or red_value < 0:
        return None, "invalid"

    if finger_status not in (0, 1):
        return None, "invalid"

    sample = {
        "timestamp_ms": timestamp_ms,
        "ir_value": ir_value,
        "red_value": red_value,
        "finger_status": finger_status,
    }

    return sample, "valid"


# ==================================================
# INPUT SUBJEK
# ==================================================
subject_id = input("Masukkan ID subjek: ").strip()

if not subject_id:
    raise SystemExit("ID subjek tidak boleh kosong.")

age_input = input("Masukkan usia subjek (tahun): ").strip()

try:
    subject_age = int(age_input)
    if subject_age <= 0:
        raise ValueError
except ValueError:
    raise SystemExit("Usia harus berupa bilangan bulat positif.")


safe_id = sanitize_filename(subject_id)

if not safe_id:
    raise SystemExit("ID subjek tidak valid untuk nama file.")

os.makedirs(OUTPUT_DIR, exist_ok=True)

start_time = datetime.now()
file_time = start_time.strftime("%Y%m%d_%H%M%S")

filename = (
    f"PPG_{safe_id}_{subject_age}th_{file_time}.csv"
)

file_path = os.path.join(OUTPUT_DIR, filename)


# ==================================================
# AKUISISI DAN PENCATATAN DATA
# ==================================================
valid_count = 0
invalid_count = 0
ignored_count = 0

print("\n======================================")
print("       PPG DATA ACQUISITION")
print("======================================")
print(f"ID subjek : {subject_id}")
print(f"Usia      : {subject_age} tahun")
print(f"Port      : {SERIAL_PORT}")
print(f"Baud rate : {BAUD_RATE}")
print("Tekan Ctrl+C untuk menghentikan.")
print("======================================\n")

try:
    with serial.Serial(
        port=SERIAL_PORT,
        baudrate=BAUD_RATE,
        timeout=SERIAL_TIMEOUT
    ) as ser:

        # Bersihkan data lama di buffer saat mulai.
        ser.reset_input_buffer()

        print("Port serial terhubung.")
        print("Menunggu data PPG...\n")

        with open(
            file_path,
            mode="w",
            newline="",
            encoding="utf-8"
        ) as csv_file:

            writer = csv.DictWriter(
                csv_file,
                fieldnames=[
                    "timestamp_ms",
                    "ir_value",
                    "red_value",
                    "finger_status",
                ]
            )

            writer.writeheader()
            csv_file.flush()

            try:
                while True:
                    raw_line = ser.readline()

                    # Tidak ada data selama periode timeout.
                    if not raw_line:
                        continue

                    line = raw_line.decode(
                        "utf-8", errors="replace"
                    ).strip()

                    if not line:
                        continue

                    sample, status = parse_ppg_line(line)

                    if status == "not_json":
                        # Pesan debug ESP32 diabaikan.
                        ignored_count += 1
                        continue

                    if status != "valid":
                        invalid_count += 1
                        continue

                    writer.writerow(sample)
                    valid_count += 1

                    # Simpan ke disk setiap sampel.
                    csv_file.flush()

                    if valid_count % 50 == 0:
                        print(
                            f"Data tersimpan: {valid_count} | "
                            f"IR: {sample['ir_value']} | "
                            f"RED: {sample['red_value']} | "
                            f"Jari: {sample['finger_status']}"
                        )

            except KeyboardInterrupt:
                print("\nPerekaman dihentikan oleh pengguna.")

except SerialException as error:
    print(f"\nKesalahan komunikasi serial: {error}")
    print(
        "Periksa port COM, kabel, baud rate, "
        "dan pastikan port tidak dipakai aplikasi lain."
    )

finally:
    print("\n========== RINGKASAN ==========")
    print(f"Data valid   : {valid_count}")
    print(f"Data invalid : {invalid_count}")
    print(f"Baris lain   : {ignored_count}")

    if valid_count > 0:
        print(f"File CSV     : {file_path}")
    else:
        print(
            "Belum ada sampel PPG valid. "
            "Periksa format dan jalur data serial."
        )

    print("===============================")
