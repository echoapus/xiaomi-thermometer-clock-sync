#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["bleak>=3.1,<4", "tzdata; sys_platform == 'win32'"]
# ///
"""Sync the clock of Xiaomi LYWSD02 temperature/humidity monitors over BLE.

A single self-contained file: copy it anywhere and run it with Python 3.10+.

    uv run xiaomi_thermometer_clock_sync.py --scan     # uv installs bleak automatically
    python3 -m pip install "bleak>=3.1,<4" && python3 xiaomi_thermometer_clock_sync.py --scan

To run without arguments, list device addresses one per line in
xiaomi-clock-devices.txt next to this file. See --help for all options.

MIT License

Copyright (c) 2026 xiaomi-thermometer-clock-sync contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import argparse
import asyncio
import datetime
import importlib.metadata
import logging
import math
import os
import platform
import re
import struct
import subprocess
import sys
import time
import zoneinfo
from dataclasses import dataclass
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except ImportError:  # keep pure helpers importable; main() reports how to install it
    BleakClient = BleakScanner = None

__version__ = "0.3.0"
BLEAK_REQUIREMENT = "bleak>=3.1,<4"


# --- protocol: UUIDs and pure (device-free) encoding/decoding helpers ---

TIME_CHAR = "ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6"  # <uint32 unix time><int8 UTC offset hours>
BATTERY_CHAR = "ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6"  # <uint8 percent>
SENSOR_CHAR = "ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6"  # notify only: <int16 temp/100 °C><uint8 humidity %>
UNITS_CHAR = "ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6"  # display unit: 0xff = °C, 0x01 = °F
MIBEACON_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"
BT_BASE_SUFFIX = "-0000-1000-8000-00805f9b34fb"
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
TIME_LEN = 5
TZ_MIN, TZ_MAX = -12, 14


def pack_time(ts, tz):
    return struct.pack("<Ib", ts, tz)


def unpack_time(raw):
    """Return (unix_time, tz_hours) from the 5-byte time characteristic value."""
    if len(raw) != TIME_LEN:
        raise ValueError(f"time value must be {TIME_LEN} bytes, got {raw.hex()!r}")
    return struct.unpack("<Ib", raw)


@dataclass
class SensorReading:
    temperature: float  # °C
    humidity: int  # %


def unpack_sensor(raw):
    if len(raw) < 3:
        raise ValueError(f"sensor value too short: {raw.hex()!r}")
    temp, humidity = struct.unpack("<hB", raw[:3])
    if humidity > 100:
        raise ValueError(f"humidity {humidity}% out of range: {raw.hex()!r}")
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


def whole_hours(hours, source):
    """Validate a UTC offset for the device's whole-hour field and return it as an int."""
    if not math.isfinite(hours):
        raise argparse.ArgumentTypeError(f"{source} is not a valid UTC offset")
    if hours != int(hours):
        raise argparse.ArgumentTypeError(f"{source} is UTC{hours:+g}; the device only supports whole-hour offsets")
    if not TZ_MIN <= hours <= TZ_MAX:
        raise argparse.ArgumentTypeError(f"{source}: offset {hours:+g} out of range ({TZ_MIN}..+{TZ_MAX})")
    return int(hours)


def local_tz_hours():
    """Return the system's current UTC offset in whole hours."""
    offset = datetime.datetime.now().astimezone().utcoffset()
    return whole_hours(offset.total_seconds() / 3600, "the system timezone")


def parse_tz(value):
    """Return the current UTC offset in whole hours for an IANA name or numeric offset."""
    text = value.strip()
    if not text:
        raise argparse.ArgumentTypeError("empty timezone (try Asia/Taipei or +8)")
    num = text[3:] if text.upper().startswith(("UTC", "GMT")) else text
    try:
        hours = float(num) if num else 0.0  # num is only empty for a bare "UTC" / "GMT"
    except ValueError:
        try:
            offset = datetime.datetime.now(zoneinfo.ZoneInfo(text)).utcoffset()
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
            raise argparse.ArgumentTypeError(f"unknown timezone {value!r} (try Asia/Taipei or +8)") from None
        hours = offset.total_seconds() / 3600
    return whole_hours(hours, repr(value))


def resolve_tz(spec):
    """Current offset for a --tz value, or for the system timezone when spec is None."""
    return local_tz_hours() if spec is None else parse_tz(spec)


def fmt(ts, tz):
    """ISO time for logs. Never raises: the values may come from a corrupted device."""
    if not TZ_MIN <= tz <= TZ_MAX:
        return f"invalid (unix {ts}, UTC{tz:+d} out of range)"
    try:
        return datetime.datetime.fromtimestamp(ts, datetime.timezone(datetime.timedelta(hours=tz))).isoformat()
    except (ValueError, OverflowError, OSError):
        return f"invalid (unix {ts}, UTC{tz:+d})"


def utc(tz):
    """Label a UTC offset for logs, flagging values the device should never hold."""
    return f"UTC{tz:+d}" + ("" if TZ_MIN <= tz <= TZ_MAX else " (out of range)")


def short_uuid(u):
    return u[4:8] if u.startswith("0000") and u.endswith(BT_BASE_SUFFIX) else u


def clean(text):
    """Escape control characters in peripheral-supplied text before printing or logging it."""
    return "".join(c if c.isprintable() else ascii(c)[1:-1] for c in text)


# --- device: BLE operations against a LYWSD02 ---

log = logging.getLogger("xiaomi_thermometer_clock_sync")

SCAN_TIMEOUT = 20
CONNECT_TIMEOUT = 30
GATT_TIMEOUT = 10  # each read/write/subscribe; BlueZ can otherwise keep retrying indefinitely
SENSOR_TIMEOUT = 15
DEVICE_TIMEOUT = 180  # backstop for one whole device, including disconnect
CLEANUP_TIMEOUT = 10  # after DEVICE_TIMEOUT, how long a cancelled device may take to clean up
MAX_DRIFT = 3  # seconds; the device keeps whole seconds and the write lands within a round trip
SUPPORTED_MODELS = ("LYWSD02",)


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
    raw = bytes(await asyncio.wait_for(client.read_gatt_char(uuid), GATT_TIMEOUT))
    log.debug("read %s [%s] = %s (%s)", label, short_uuid(uuid), raw.hex(), ms(t0))
    return raw


async def stop_notify(client, uuid):
    """Unsubscribe without letting a cleanup failure replace the reading or the original error."""
    if not client.is_connected:
        return
    try:
        await asyncio.wait_for(client.stop_notify(uuid), GATT_TIMEOUT)
    except Exception as e:
        log.debug("stop_notify [%s] failed (%s: %s)", short_uuid(uuid), type(e).__name__, e)


async def read_sensor(client):
    """The sensor characteristic reads empty; wait for its next notification instead."""
    got = asyncio.get_running_loop().create_future()

    def on_notify(_, data):
        if not got.done():
            got.set_result(bytes(data))

    await asyncio.wait_for(client.start_notify(SENSOR_CHAR, on_notify), GATT_TIMEOUT)
    try:
        raw = await asyncio.wait_for(got, SENSOR_TIMEOUT)
    finally:
        await stop_notify(client, SENSOR_CHAR)
    log.debug("notify sensor [%s] = %s", short_uuid(SENSOR_CHAR), raw.hex())
    return raw


async def read_device_info(client, diagnostics):
    """Log the Device Information Service (only the model unless diagnostics); return the model or None."""
    log.info("reading device information")
    model = None
    for uuid, label in DEVICE_INFO.items():
        if not diagnostics and label != "model":
            continue
        try:
            raw = await read(client, uuid, label)
        except Exception as e:
            log.warning("  %-13s unavailable (%s: %s)", label + ":", type(e).__name__, e)
            continue
        text = raw.hex() if label == "system id" else clean(raw.decode(errors="replace").strip("\x00").strip())
        log.info("  %-13s %s", label + ":", text)
        if label == "model":
            model = text
    return model


async def read_battery(client):
    t0 = time.monotonic()
    try:
        raw = await read(client, BATTERY_CHAR, "battery")
        if not raw or raw[0] > 100:
            raise ValueError(f"unexpected value {raw.hex()!r}")
    except Exception as e:
        log.warning("  %-13s unavailable (%s: %s)", "battery:", type(e).__name__, e)
        return
    log.info("  %-13s %d%% (raw %s, %s)", "battery:", raw[0], raw.hex(), ms(t0))
    if raw[0] < 20:
        log.warning("battery low (%d%%), replace soon", raw[0])


async def read_sensor_values(client):
    log.info("reading temperature / humidity")
    t0 = time.monotonic()
    try:  # the display unit is informational; its absence must not hide the sensor reading
        units = await read(client, UNITS_CHAR, "units")
        unit = UNITS.get(units[0], "?") if units else "?"
    except Exception as e:
        log.debug("display unit unavailable (%s: %s)", type(e).__name__, e)
        unit = "?"
    try:
        raw = await read_sensor(client)
        reading = unpack_sensor(raw)
    except Exception as e:
        log.warning("  sensor:       unavailable (%s: %s)", type(e).__name__, e)
        return
    log.info("  temperature:  %.2f°C (display unit °%s)", reading.temperature, unit)
    log.info("  humidity:     %d%% (raw %s, %s)", reading.humidity, raw.hex(), ms(t0))


def check_writable(client, model):
    """Refuse to write the clock of anything but a supported model with a writable time characteristic."""
    if model is None:
        raise RuntimeError("model unavailable, cannot confirm this is a supported device; clock not written")
    if model not in SUPPORTED_MODELS:
        raise RuntimeError(f"model {model!r} is not supported ({', '.join(SUPPORTED_MODELS)}); clock not written")
    char = client.services.get_characteristic(TIME_CHAR)
    if char is None or "write" not in char.properties:
        raise RuntimeError("device has no writable time characteristic; clock not written")


async def run(mac, tz_spec, read_only=False, diagnostics=False):
    """Connect to one device, log its state and (unless read_only) sync its clock.

    tz_spec is the --tz value (None for the system timezone); it is resolved right
    before the write so a run that crosses a DST change uses the current offset.
    Full device information and the sensor reading are only collected with
    diagnostics; a plain sync reads just the model, battery and clock.
    """
    started = time.monotonic()

    log.info("scanning for %s (timeout %ss)...", mac, SCAN_TIMEOUT)
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

    log.info("connecting...")
    t0 = time.monotonic()
    async with BleakClient(dev, timeout=CONNECT_TIMEOUT) as client:
        services = list(client.services)
        n_chars = sum(len(svc.characteristics) for svc in services)
        log.info("connected in %s, %d services / %d characteristics", ms(t0), len(services), n_chars)
        for svc in services:
            log.debug("service %s (%s)", svc.uuid, svc.description)
            for ch in svc.characteristics:
                log.debug(
                    "    char %s handle=0x%04x [%s] %s", ch.uuid, ch.handle, ",".join(ch.properties), ch.description
                )

        model = await read_device_info(client, diagnostics)
        await read_battery(client)
        if diagnostics:
            await read_sensor_values(client)

        log.info("reading device time")
        t0 = time.monotonic()
        raw = await read(client, TIME_CHAR, "time")
        before, before_tz = unpack_time(raw)
        offset_before = before - round(time.time())
        log.info(
            "device time: %s (raw %s, unix %d, drift %+ds, tz %+d, %s)",
            fmt(before, before_tz),
            raw.hex(),
            before,
            offset_before,
            before_tz,
            ms(t0),
        )

        if not read_only:
            check_writable(client, model)
            tz = resolve_tz(tz_spec)
            if before_tz != tz:
                log.info("timezone will change: %s -> %s", utc(before_tz), utc(tz))
            log.info("writing host time")
            now = round(time.time())
            payload = pack_time(now, tz)
            t0 = time.monotonic()
            await asyncio.wait_for(client.write_gatt_char(TIME_CHAR, payload, response=True), GATT_TIMEOUT)
            log.info(
                "wrote %s (raw %s, unix %d, %d bytes, with response, %s)",
                fmt(now, tz),
                payload.hex(),
                now,
                len(payload),
                ms(t0),
            )

            log.info("verifying")
            t0 = time.monotonic()
            raw = await read(client, TIME_CHAR, "time")
            after, after_tz = unpack_time(raw)
            drift = after - round(time.time())
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
        log.info("%s: read-only, clock %+ds off, %s, total %.1fs", mac, offset_before, utc(before_tz), total)
        return
    if abs(drift) > MAX_DRIFT or after_tz != tz:
        raise RuntimeError(f"verification failed: drift {drift}s, {utc(after_tz)}")
    log.info("%s: synced (was %+ds off), UTC%+d, total %.1fs", mac, offset_before, tz, total)


def describe_error(e):
    detail = str(e) or ("timed out" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else "no details")
    return f"{type(e).__name__}: {clean(detail)}"


async def run_bounded(coro, abandoned):
    """Await coro for at most DEVICE_TIMEOUT. Unlike asyncio.wait_for, do not wait indefinitely for a
    cancelled device to clean up: a hung disconnect is cancelled again, then added to abandoned."""
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait({task}, timeout=DEVICE_TIMEOUT)
    if done:
        return task.result()
    # whatever the abandoned run raises while cleaning up is superseded by the timeout below
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    for _ in range(2):  # the first cancel lands in the stuck call, a second one in its cleanup
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=CLEANUP_TIMEOUT)
        if done:
            break
    else:
        log.warning("device cleanup did not finish; continuing")
        abandoned.append(task)
    raise TimeoutError(f"gave up after {DEVICE_TIMEOUT}s")


async def run_all(macs, tz_spec, read_only=False, diagnostics=False, abandoned=None):
    """Process each device in turn on one event loop; return the addresses that failed.

    Devices whose cleanup never finished are appended to abandoned as still-running tasks."""
    abandoned = [] if abandoned is None else abandoned
    failed = []
    for i, mac in enumerate(macs, 1):
        if len(macs) > 1:
            log.info("=== device %d/%d: %s ===", i, len(macs), mac)
        try:
            await run_bounded(run(mac, tz_spec, read_only, diagnostics), abandoned)
        except Exception as e:
            log.error("%s: %s", mac, describe_error(e))
            log.debug("traceback", exc_info=True)
            failed.append(mac)
    return failed


def force_exit(code):
    """Exit now, without asyncio.run() waiting at shutdown for a disconnect that ignores
    cancellation (which would keep a cron job, and its flock, alive indefinitely)."""
    for handler in logging.getLogger().handlers:
        handler.flush()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


async def sync_all(args):
    """Sync or read every target and return the exit code."""
    abandoned = []
    failed = await run_all(
        args.macs, args.tz, read_only=args.read_only, diagnostics=args.read_only or args.verbose, abandoned=abandoned
    )
    if len(args.macs) > 1:
        log.info(
            "done: %d ok, %d failed%s",
            len(args.macs) - len(failed),
            len(failed),
            f" ({', '.join(failed)})" if failed else "",
        )
    code = 1 if failed else 0
    if any(not task.done() for task in abandoned):
        log.warning("Bluetooth cleanup of a timed-out device is still stuck; exiting without waiting for it")
        force_exit(code)
    return code


# --- command-line interface ---

MAC_ENV = "XIAOMI_CLOCK_MAC"
LEGACY_MAC_ENV = "LYWSD02_MAC"  # name used before the project was renamed


def config_dir():
    """The per-user config directory, or None if there is no home directory to put it in."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg and Path(xdg).is_absolute():  # the XDG spec says relative values are invalid and must be ignored
        base = Path(xdg)
    else:
        try:
            base = Path.home() / ".config"
        except RuntimeError:  # minimal service environments without HOME or a passwd entry
            return None
    return base / "xiaomi-thermometer-clock-sync"


def devices_files():
    """Device lists, in order. One address per line, '#' starts a comment; the first file that
    lists any address wins: a list next to the script travels with it, the per-user one survives
    reinstalls."""
    files = [Path(__file__).resolve().with_name("xiaomi-clock-devices.txt")]
    config = config_dir()
    if config is not None:
        files.append(config / "devices")
    return files


MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)
# macOS (CoreBluetooth) never exposes MACs; bleak identifies devices by a per-host UUID instead
UUID_RE = re.compile(r"^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$", re.IGNORECASE)

EPILOG = """\
MAC addresses can also be given in ${MAC_ENV} (comma-separated), or listed one
per line ('#' starts a comment) in the first of these files that lists any:
{files}
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


def scan_seconds(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid duration {value!r}") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError(f"duration must be a positive number of seconds, got {value!r}")
    return seconds


def read_devices_file(path):
    """Return the addresses listed in path, or [] if it does not exist. Raises ValueError if unreadable."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read {path}: {e}") from None
    return [m.strip() for line in text.splitlines() for m in line.split("#", 1)[0].split(",") if m.strip()]


def read_devices_files(paths):
    """Return (addresses, path) from the first file that lists any, or ([], None)."""
    for path in paths:
        macs = read_devices_file(path)
        if macs:
            return macs, path
    return [], None


def macs_from_env():
    """Return (addresses, '$NAME') from the first address variable that lists any, or ([], None)."""
    for name in (MAC_ENV, LEGACY_MAC_ENV):
        macs = [m.strip() for m in os.environ.get(name, "").split(",") if m.strip()]
        if macs:
            return macs, f"${name}"
    return [], None


def run_quiet(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except Exception:
        return "unknown"
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else "unknown"


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
        name = clean(adv.local_name or dev.name or "-")
        print(f"{dev.address:<{width}}  {name:<12}  {adv.rssi:>5}  {product:<7}  {encrypted}")
    return 0


def parse_args(argv=None):
    files = devices_files()
    p = argparse.ArgumentParser(
        description="Sync the clock of Xiaomi LYWSD02 temperature/humidity monitors over BLE.",
        epilog=EPILOG.format(MAC_ENV=MAC_ENV, files="\n".join(f"  {f}" for f in files)),
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
        const=10.0,
        type=scan_seconds,
        metavar="SECONDS",
        help="list nearby Xiaomi devices and exit (default 10s)",
    )
    p.add_argument(
        "--read-only",
        action="store_true",
        help="leave the clock unchanged; report time, battery, device info and sensor values",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="also show GATT table, raw reads and bleak/BlueZ debug logs"
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    args.tz_hours = None
    if args.scan is None and not args.macs:
        env, source = macs_from_env()
        if not env:
            try:
                env, path = read_devices_files(files)
            except ValueError as e:
                p.error(str(e))
            source = str(path)
        try:
            args.macs = [mac_address(m) for m in env]
        except argparse.ArgumentTypeError as e:
            p.error(f"{source}: {e}")
        if not args.macs:
            p.error(
                f"no device given: pass a MAC address, set ${MAC_ENV}, list it in {files[0]}, "
                "or run --scan to find one"
            )
    if args.tz is not None:
        try:
            args.tz_hours = parse_tz(args.tz)
        except argparse.ArgumentTypeError as e:
            p.error(f"argument --tz: {e}")
    elif args.scan is None and not args.read_only:  # read-only and scan runs never write an offset
        try:
            args.tz_hours = local_tz_hours()
        except argparse.ArgumentTypeError as e:
            p.error(f"{e}; pass --tz with a whole-hour offset")
    return args


def main(argv=None):
    args = parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # e.g. a cp950 Windows console cannot print every character
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    if BleakScanner is None:
        sys.exit(
            "error: the 'bleak' Bluetooth library is not installed.\n"
            f'  install it:  {Path(sys.executable).name} -m pip install "{BLEAK_REQUIREMENT}"\n'
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
        try:
            code = asyncio.run(do_scan(args.scan))
        except Exception as e:
            hint = " (is Bluetooth on? try: rfkill unblock bluetooth)" if sys.platform.startswith("linux") else ""
            log.error("scan failed: %s%s", describe_error(e), hint)
            log.debug("traceback", exc_info=True)
            code = 1
        sys.exit(code)

    tz = args.tz_hours
    ntp = "unknown"
    if sys.platform.startswith("linux"):
        ntp = run_quiet(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    host = fmt(round(time.time()), tz) if tz is not None else datetime.datetime.now().astimezone().isoformat()
    log.info("host time %s (%s, ntp synced: %s)", host, time.strftime("%Z"), ntp)
    log.info(
        "targets %s, tz %s (from %s), mode %s",
        ", ".join(args.macs),
        "-" if tz is None else f"{tz:+d}",
        f"--tz {args.tz}" if args.tz else "system timezone",
        "read-only" if args.read_only else "sync",
    )

    sys.exit(asyncio.run(sync_all(args)))


if __name__ == "__main__":
    main()
