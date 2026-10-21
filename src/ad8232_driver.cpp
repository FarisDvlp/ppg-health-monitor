#include "ad8232_driver.h"

AD8232Driver ecgSensor;

AD8232Driver::AD8232Driver() {
    initialized = false;
}

bool AD8232Driver::begin() {
    pinMode(AD8232_LO_MINUS_PIN, INPUT_PULLDOWN);
    pinMode(AD8232_LO_PLUS_PIN, INPUT_PULLDOWN);
    pinMode(AD8232_OUTPUT_PIN, INPUT);

    // Konfigurasi ADC pada ESP32-S3: 12-bit (0-4095) dan 11dB attenuation (~0 - 3.1V range)
    analogSetPinAttenuation(AD8232_OUTPUT_PIN, ADC_11db);
    analogReadResolution(12);

    initialized = true;
    Serial.printf("# [AD8232] Output=GPIO%d, LO-=GPIO%d, LO+=GPIO%d @ %d Hz\n",
                  AD8232_OUTPUT_PIN, AD8232_LO_MINUS_PIN, AD8232_LO_PLUS_PIN, AD8232_SAMPLE_RATE);
    return true;
}

bool AD8232Driver::isLeadsOff(bool &lo_plus, bool &lo_minus) {
    lo_minus = (digitalRead(AD8232_LO_MINUS_PIN) == HIGH);
    lo_plus = (digitalRead(AD8232_LO_PLUS_PIN) == HIGH);
    return (lo_plus || lo_minus);
}

bool AD8232Driver::sample(ECGPacket &packet) {
    if (!initialized) return false;

    bool lo_p = false, lo_m = false;
    bool lo = isLeadsOff(lo_p, lo_m);

    packet.timestamp_ms = millis();
    packet.raw_adc = (uint16_t)analogRead(AD8232_OUTPUT_PIN);
    packet.leads_off = lo;
    packet.lo_plus = lo_p;
    packet.lo_minus = lo_m;

    return true;
}
