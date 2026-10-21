#ifndef AD8232_DRIVER_H
#define AD8232_DRIVER_H

#include <Arduino.h>
#include "config.h"

class AD8232Driver {
public:
    AD8232Driver();
    bool begin();
    bool sample(ECGPacket &packet);
    bool isLeadsOff(bool &lo_plus, bool &lo_minus);

private:
    bool initialized;
};

extern AD8232Driver ecgSensor;

#endif // AD8232_DRIVER_H
