# LYWSD02 BLE protocol

| Characteristic | Access | Format |
|---|---|---|
| `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6` time | read/write | `<uint32 LE unix time><int8 UTC offset hours>` |
| `ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6` battery | read | `<uint8 percent>` |
| `ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6` sensor | notify (read returns empty) | `<int16 LE temp × 100 °C><uint8 humidity %>` |
| `ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6` display unit | read/write | `0xff` °C, `0x01` °F |

The time payload is five bytes: a Unix timestamp followed by a signed whole-hour UTC offset. Sensor temperature is always reported in Celsius, regardless of the display unit.

The implementation is in [`protocol.py`](../xiaomi_thermometer_clock_sync/protocol.py). Clock writes are read back and verified: the UTC offset must match and the time must be within 60 seconds of the host clock.

Tested with LYWSD02 firmware `1.1.2_0097`, hardware `F2_LN`, manufacturer `miaomiaoce.com`, MiBeacon product ID `0x045b`. This firmware permits direct time reads and writes without Mi authentication. Firmware requiring encrypted authentication is unsupported.
