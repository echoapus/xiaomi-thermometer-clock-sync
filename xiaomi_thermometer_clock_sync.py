#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=3.0,<4", "tzdata; sys_platform == 'win32'"]
# ///
"""Sync the clock of Xiaomi LYWSD02 temperature/humidity monitors over BLE.

A single self-contained file: copy it anywhere and run it with Python 3.10+.

    uv run xiaomi_thermometer_clock_sync.py --scan     # uv installs bleak automatically
    pip install bleak && python3 xiaomi_thermometer_clock_sync.py --scan

To run without arguments, list device addresses one per line in
xiaomi-clock-devices.txt next to this file. See --help for all options.
"""

import argparse
import asyncio
import datetime
import importlib.metadata
import logging
import os
import platform
import re
import struct
import subprocess
import sys
import time
import warnings
import zoneinfo
from dataclasses import dataclass
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # keep pure helpers importable; main() reports how to install it
    BleakClient = BleakScanner = None

__version__ = "0.2.0"


# --- protocol: UUIDs and pure (device-free) encoding/decoding helpers ---

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


# --- device: BLE operations against a LYWSD02 ---

log = logging.getLogger("xiaomi_thermometer_clock_sync")

SCAN_TIMEOUT = 20
CONNECT_TIMEOUT = 30
SENSOR_TIMEOUT = 15
MAX_DRIFT = 60


def ms(t0):
    return f"{(time.monotonic() - t0) * 1000:.0f}ms"


def is_xiaomi(dev, adv):
    return MIBEACON_UUID in adv.service_data or (adv.local_name or dev.name or "").startswith(("LYWSD", "MHO"))


async def scan(timeout):
    """Return [(device, advertisement)] for Xiaomi-looking devices, strongest signal first."""
    log.info("scanning for Xiaomi devices (%ss)...", timeout)
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    hits = [(d, a) for d, a in found.values() if is_xiaomi(d, a)]
    return sorted(hits, key=lambda h: h[1].rssi, reverse=True)


def log_advertisement(dev, adv):
    props = dev.details.get("props", {}) if isinstance(dev.details, dict) else {}
    log.info(
        "adv: name=%r address_type=%s rssi=%sdBm tx_power=%s",
        adv.local_name,
        props.get("AddressType", "?"),
        adv.rssi,
        adv.tx_power,
    )
    log.info("adv: service_uuids=%s", ", ".join(short_uuid(u) for u in adv.service_uuids) or "-")
    for uuid, data in adv.service_data.items():
        log.info("adv: service_data[%s] = %s", short_uuid(uuid), data.hex() or "(empty)")
        beacon = parse_mibeacon(data) if uuid == MIBEACON_UUID else None
        if beacon:
            log.info(
                "adv: MiBeacon frame_ctrl=0x%04x product_id=0x%04x counter=%d encrypted=%s",
                beacon.frame_ctrl,
                beacon.product_id,
                beacon.counter,
                beacon.encrypted,
            )
    for company, data in adv.manufacturer_data.items():
        log.info("adv: manufacturer_data[0x%04x] = %s", company, data.hex())


async def read(client, uuid, label):
    t0 = time.monotonic()
    raw = bytes(await client.read_gatt_char(uuid))
    log.debug("read %s [%s] = %s (%s)", label, short_uuid(uuid), raw.hex(), ms(t0))
    return raw


async def read_sensor(client):
    """The sensor characteristic reads empty; wait for its next notification instead."""
    got = asyncio.get_running_loop().create_future()

    def on_notify(_, data):
        if not got.done():
            got.set_result(bytes(data))

    await client.start_notify(SENSOR_CHAR, on_notify)
    try:
        raw = await asyncio.wait_for(got, SENSOR_TIMEOUT)
    finally:
        await client.stop_notify(SENSOR_CHAR)
    log.debug("notify sensor [%s] = %s", short_uuid(SENSOR_CHAR), raw.hex())
    return raw


async def run(mac, tz, read_only=False):
    """Connect to one device, log its state and (unless read_only) sync its clock."""
    started = time.monotonic()
    steps = 5 if read_only else 7
    step = iter(range(1, steps + 1))

    log.info("[%d/%d] scanning for %s (timeout %ss)...", next(step), steps, mac, SCAN_TIMEOUT)
    t0 = time.monotonic()
    advs = {}

    def match(d, adv):
        if d.address.upper() == mac.upper():
            advs["adv"] = adv
            return True
        return False

    dev = await BleakScanner.find_device_by_filter(match, timeout=SCAN_TIMEOUT)
    if dev is None:
        raise RuntimeError(f"device {mac} not found (is it powered on and in range?)")
    log.info("found %s name=%r in %s", dev.address, dev.name, ms(t0))
    log_advertisement(dev, advs["adv"])

    log.info("[%d/%d] connecting...", next(step), steps)
    t0 = time.monotonic()
    async with BleakClient(dev, timeout=CONNECT_TIMEOUT) as client:
        services = list(client.services)
        n_chars = sum(len(svc.characteristics) for svc in services)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # BlueZ backend warns when MTU was not negotiated yet
            mtu = client.mtu_size
        log.info("connected in %s, mtu=%s, %d services / %d characteristics", ms(t0), mtu, len(services), n_chars)
        for svc in services:
            log.debug("service %s (%s)", svc.uuid, svc.description)
            for ch in svc.characteristics:
                log.debug(
                    "    char %s handle=0x%04x [%s] %s", ch.uuid, ch.handle, ",".join(ch.properties), ch.description
                )

        log.info("[%d/%d] reading device information", next(step), steps)
        for uuid, label in DEVICE_INFO.items():
            try:
                raw = await read(client, uuid, label)
                text = raw.hex() if label == "system id" else raw.decode(errors="replace").strip("\x00")
                log.info("  %-13s %s", label + ":", text)
            except Exception as e:
                log.warning("  %-13s unavailable (%s)", label + ":", e)
        try:
            t0 = time.monotonic()
            raw = await read(client, BATTERY_CHAR, "battery")
            log.info("  %-13s %d%% (raw %s, %s)", "battery:", raw[0], raw.hex(), ms(t0))
            if raw[0] < 20:
                log.warning("battery low (%d%%), replace soon", raw[0])
        except Exception as e:
            log.warning("  %-13s unavailable (%s)", "battery:", e)

        log.info("[%d/%d] reading temperature / humidity", next(step), steps)
        try:
            t0 = time.monotonic()
            unit = UNITS.get((await read(client, UNITS_CHAR, "units"))[0], "?")
            raw = await read_sensor(client)
            reading = unpack_sensor(raw)
            log.info("  temperature:  %.2f°C (display unit °%s)", reading.temperature, unit)
            log.info("  humidity:     %d%% (raw %s, %s)", reading.humidity, raw.hex(), ms(t0))
        except Exception as e:
            log.warning("  sensor:       unavailable (%s: %s)", type(e).__name__, e)

        log.info("[%d/%d] reading device time", next(step), steps)
        t0 = time.monotonic()
        raw = await read(client, TIME_CHAR, "time")
        before, before_tz = unpack_time(raw)
        now = int(time.time())
        log.info(
            "device time: %s (raw %s, unix %d, drift %+ds, tz %+d, %s)",
            fmt(before, before_tz),
            raw.hex(),
            before,
            before - now,
            before_tz,
            ms(t0),
        )

        if not read_only:
            if before_tz != tz:
                log.info("timezone will change: UTC%+d -> UTC%+d", before_tz, tz)
            log.info("[%d/%d] writing host time", next(step), steps)
            payload = pack_time(now, tz)
            t0 = time.monotonic()
            await client.write_gatt_char(TIME_CHAR, payload, response=True)
            log.info(
                "wrote %s (raw %s, unix %d, %d bytes, with response, %s)",
                fmt(now, tz),
                payload.hex(),
                now,
                len(payload),
                ms(t0),
            )

            log.info("[%d/%d] verifying", next(step), steps)
            t0 = time.monotonic()
            raw = await read(client, TIME_CHAR, "time")
            after, after_tz = unpack_time(raw)
            drift = after - int(time.time())
            log.info(
                "device time after: %s (raw %s, unix %d, drift %+ds, tz %+d, %s)",
                fmt(after, after_tz),
                raw.hex(),
                after,
                drift,
                after_tz,
                ms(t0),
            )
        log.info("disconnecting")
        t0 = time.monotonic()
    log.info("disconnected in %s", ms(t0))

    total = time.monotonic() - started
    if read_only:
        log.info("%s: read-only, clock %+ds off, UTC%+d, total %.1fs", mac, before - now, before_tz, total)
        return
    if abs(drift) > MAX_DRIFT or after_tz != tz:
        raise RuntimeError(f"verification failed: drift {drift}s, tz {after_tz}")
    log.info("%s: synced (was %+ds off), UTC%+d, total %.1fs", mac, before - now, tz, total)


# --- command-line interface ---

MAC_ENV = "XIAOMI_CLOCK_MAC"
LEGACY_MAC_ENV = "LYWSD02_MAC"  # name used before the project was renamed
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "xiaomi-thermometer-clock-sync"
# One address per line, '#' starts a comment. The first file that lists any address wins:
# a list next to the script travels with it, the per-user one survives reinstalls.
DEVICES_FILES = [Path(__file__).resolve().with_name("xiaomi-clock-devices.txt"), CONFIG_DIR / "devices"]
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
# macOS (CoreBluetooth) never exposes MACs; bleak identifies devices by a per-host UUID instead
UUID_RE = re.compile(r"^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$", re.IGNORECASE)

EPILOG = f"""\
MAC addresses can also be given in ${MAC_ENV} (comma-separated), or listed one
per line ('#' starts a comment) in the first of these files that exists:
{chr(10).join("  " + str(f) for f in DEVICES_FILES)}
On macOS, use the device UUID that --scan prints instead of a MAC address.
ZONE is an IANA name (Asia/Taipei, America/New_York) or a whole-hour UTC offset
(8, +8, -5, UTC+8). The device stores whole hours only, so DST zones are resolved
to their current offset and need a re-sync when DST changes.

examples:
  python3 xiaomi_thermometer_clock_sync.py --scan
  python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF
  python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Tokyo
  python3 xiaomi_thermometer_clock_sync.py --read-only -v
"""


def mac_address(value):
    if not (MAC_RE.match(value) or UUID_RE.match(value)):
        raise argparse.ArgumentTypeError(f"invalid MAC address {value!r} (expected AA:BB:CC:DD:EE:FF)")
    return value.upper()


def read_devices_file(path):
    """Return the addresses listed in path, or [] if it does not exist."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        return []
    return [m.strip() for line in text.splitlines() for m in line.split("#", 1)[0].split(",") if m.strip()]


def read_devices_files(paths):
    """Return (addresses, path) from the first file that lists any, or ([], None)."""
    for path in paths:
        macs = read_devices_file(path)
        if macs:
            return macs, path
    return [], None


def run_quiet(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def log_environment():
    log.info(
        "env: xiaomi-thermometer-clock-sync %s, python %s, bleak %s, %s %s%s",
        __version__,
        platform.python_version(),
        importlib.metadata.version("bleak"),
        platform.system(),
        platform.release(),
        f", {run_quiet(['bluetoothctl', '--version'])}" if sys.platform.startswith("linux") else "",
    )


async def do_scan(timeout):
    hits = await scan(timeout)
    if not hits:
        hint = " try: rfkill unblock bluetooth" if sys.platform.startswith("linux") else ""
        log.warning("no Xiaomi devices found (is Bluetooth on?%s)", hint)
        return 1
    width = max(len(dev.address) for dev, _ in hits)
    print(f"{'MAC':<{width}}  {'NAME':<12}  {'RSSI':>5}  {'PRODUCT':<7}  ENCRYPTED")
    for dev, adv in hits:
        beacon = parse_mibeacon(adv.service_data.get(MIBEACON_UUID, b""))
        product = f"0x{beacon.product_id:04x}" if beacon else "-"
        encrypted = ("yes" if beacon.encrypted else "no") if beacon else "-"
        name = adv.local_name or dev.name or "-"
        print(f"{dev.address:<{width}}  {name:<12}  {adv.rssi:>5}  {product:<7}  {encrypted}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Sync the clock of Xiaomi LYWSD02 temperature/humidity monitors over BLE.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "macs",
        nargs="*",
        type=mac_address,
        metavar="MAC",
        help=f"device address(es); default: ${MAC_ENV}, then the devices files below",
    )
    p.add_argument(
        "--tz",
        metavar="ZONE",
        help="IANA timezone (e.g. Asia/Tokyo) or UTC offset in hours (e.g. +8, UTC-5); default: system",
    )
    p.add_argument(
        "--scan",
        nargs="?",
        const=10,
        type=float,
        metavar="SECONDS",
        help="list nearby Xiaomi devices and exit (default 10s)",
    )
    p.add_argument("--read-only", action="store_true", help="report time, battery and sensor values without writing")
    p.add_argument(
        "-v", "--verbose", action="store_true", help="also show GATT table, raw reads and bleak/BlueZ debug logs"
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    if args.scan is None and not args.macs:
        value = os.environ.get(MAC_ENV) or os.environ.get(LEGACY_MAC_ENV, "")
        env = [m.strip() for m in value.split(",") if m.strip()]
        source = f"${MAC_ENV}"
        if not env:
            env, path = read_devices_files(DEVICES_FILES)
            source = str(path)
        try:
            args.macs = [mac_address(m) for m in env]
        except argparse.ArgumentTypeError as e:
            p.error(f"{source}: {e}")
        if not args.macs:
            p.error(
                f"no device given: pass a MAC address, set ${MAC_ENV}, list it in {DEVICES_FILES[0]}, "
                "or run --scan to find one"
            )
    try:
        args.tz_hours = local_tz_hours() if args.tz is None else parse_tz(args.tz)
    except argparse.ArgumentTypeError as e:
        p.error(f"argument --tz: {e}")
    return args


def main(argv=None):
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # e.g. a cp950 Windows console cannot print every character
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if BleakScanner is None:
        sys.exit(
            "error: the 'bleak' Bluetooth library is not installed.\n"
            f"  install it:  {Path(sys.executable).name} -m pip install bleak\n"
            f"  or run with: uv run {Path(sys.argv[0]).name}  (installs it automatically)"
        )
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("bleak").setLevel(logging.WARNING)
    log_environment()

    if args.scan is not None:
        sys.exit(asyncio.run(do_scan(args.scan)))

    tz = args.tz_hours
    log.info(
        "host time %s (%s, ntp synced: %s)",
        fmt(int(time.time()), tz),
        time.strftime("%Z"),
        run_quiet(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]),
    )
    log.info(
        "targets %s, tz %+d (from %s), mode %s",
        ", ".join(args.macs),
        tz,
        f"--tz {args.tz}" if args.tz else "system timezone",
        "read-only" if args.read_only else "sync",
    )

    failed = []
    for i, mac in enumerate(args.macs, 1):
        if len(args.macs) > 1:
            log.info("=== device %d/%d: %s ===", i, len(args.macs), mac)
        try:
            asyncio.run(run(mac, tz, read_only=args.read_only))
        except Exception as e:
            log.error("%s: %s: %s", mac, type(e).__name__, e)
            log.debug("traceback", exc_info=True)
            failed.append(mac)
    if len(args.macs) > 1:
        log.info(
            "done: %d ok, %d failed%s",
            len(args.macs) - len(failed),
            len(failed),
            f" ({', '.join(failed)})" if failed else "",
        )
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
