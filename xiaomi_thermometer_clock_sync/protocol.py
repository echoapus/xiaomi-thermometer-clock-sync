"""LYWSD02 BLE protocol: UUIDs and pure (device-free) encoding/decoding helpers."""

import argparse
import datetime
import struct
import time
import zoneinfo
from dataclasses import dataclass

TIME_CHAR = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"  # <uint32 unix time><int8 UTC offset hours>
BATTERY_CHAR = "ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6"  # <uint8 percent>
SENSOR_CHAR = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"  # notify only: <int16 temp/100 °C><uint8 humidity %>
UNITS_CHAR = "ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6"  # display unit: 0xff = °C, 0x01 = °F
MIBEACON_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
DEVICE_INFO = {  # Device Information Service (0x180A)
    "00002a29-0000-1000-8000-00805f9b34fb": "manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "model",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial",
    "00002a27-0000-1000-8000-00805f9b34fb": "hardware rev",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware rev",
    "00002a28-0000-1000-8000-00805f9b34fb": "software rev",
    "00002a23-0000-1000-8000-00805f9b34fb": "system id",
}
UNITS = {0xFF: "C", 0x01: "F"}


def pack_time(ts, tz):
    return struct.pack("<Ib", ts, tz)


def unpack_time(raw):
    """Return (unix_time, tz_hours) from the 5-byte time characteristic value."""
    if len(raw) < 5:
        raise ValueError(f"time value too short: {raw.hex()!r}")
    return struct.unpack("<Ib", raw[:5])


@dataclass
class SensorReading:
    temperature: float  # °C
    humidity: int  # %


def unpack_sensor(raw):
    if len(raw) < 3:
        raise ValueError(f"sensor value too short: {raw.hex()!r}")
    temp, humidity = struct.unpack("<hB", raw[:3])
    return SensorReading(temp / 100, humidity)


@dataclass
class MiBeacon:
    frame_ctrl: int
    product_id: int
    counter: int

    @property
    def encrypted(self):
        return bool(self.frame_ctrl & 0x08)


def parse_mibeacon(data):
    """Parse the header of a Xiaomi MiBeacon (service data 0xFE95); None if too short."""
    if len(data) < 5:
        return None
    return MiBeacon(*struct.unpack("<HHB", data[:5]))


def local_tz_hours():
    return round(-(time.altzone if time.localtime().tm_isdst > 0 else time.timezone) / 3600)


def parse_tz(value):
    """Return the current UTC offset in whole hours for an IANA name or numeric offset."""
    text = value.strip()
    num = text[3:] if text.upper().startswith(("UTC", "GMT")) else text
    try:
        hours = float(num) if num else 0.0
    except ValueError:
        try:
            offset = datetime.datetime.now(zoneinfo.ZoneInfo(text)).utcoffset()
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            raise argparse.ArgumentTypeError(f"unknown timezone {value!r} (try Asia/Taipei or +8)") from None
        hours = offset.total_seconds() / 3600
    if hours != int(hours):
        raise argparse.ArgumentTypeError(f"{value!r} is UTC{hours:+g}; the device only supports whole-hour offsets")
    if not -12 <= hours <= 14:
        raise argparse.ArgumentTypeError(f"offset {hours:+g} out of range (-12..+14)")
    return int(hours)


def fmt(ts, tz):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=tz))).isoformat()


def short_uuid(u):
    return u[4:8] if u.endswith("-0000-1000-8000-00805f9b34fb") else u
