"""Command-line entry point: xiaomi-thermometer-clock-sync."""

import argparse
import asyncio
import importlib.metadata
import logging
import os
import platform
import re
import subprocess
import sys
import time

from . import __version__
from .device import log, run, scan
from .protocol import MIBEACON_UUID, fmt, local_tz_hours, parse_mibeacon, parse_tz

MAC_ENV = "XIAOMI_CLOCK_MAC"
LEGACY_MAC_ENV = "LYWSD02_MAC"  # name used before the project was renamed
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", re.IGNORECASE)

EPILOG = f"""\
MAC addresses can also be given in ${MAC_ENV} (comma-separated).
ZONE is an IANA name (Asia/Taipei, America/New_York) or a whole-hour UTC offset
(8, +8, -5, UTC+8). The device stores whole hours only, so DST zones are resolved
to their current offset and need a re-sync when DST changes.

examples:
  xiaomi-thermometer-clock-sync --scan
  xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF
  xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Tokyo
  xiaomi-thermometer-clock-sync --read-only -v
"""


def mac_address(value):
    if not MAC_RE.match(value):
        raise argparse.ArgumentTypeError(f"invalid MAC address {value!r} (expected AA:BB:CC:DD:EE:FF)")
    return value.upper()


def run_quiet(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def log_environment():
    log.info(
        "env: xiaomi-thermometer-clock-sync %s, python %s, bleak %s, %s, kernel %s",
        __version__,
        platform.python_version(),
        importlib.metadata.version("bleak"),
        run_quiet(["bluetoothctl", "--version"]),
        platform.release(),
    )


async def do_scan(timeout):
    hits = await scan(timeout)
    if not hits:
        log.warning("no Xiaomi devices found (is Bluetooth on? try: rfkill unblock bluetooth)")
        return 1
    print(f"{'MAC':<17}  {'NAME':<12}  {'RSSI':>5}  {'PRODUCT':<7}  ENCRYPTED")
    for dev, adv in hits:
        beacon = parse_mibeacon(adv.service_data.get(MIBEACON_UUID, b""))
        product = f"0x{beacon.product_id:04x}" if beacon else "-"
        encrypted = ("yes" if beacon.encrypted else "no") if beacon else "-"
        print(f"{dev.address:<17}  {(adv.local_name or dev.name or '-'):<12}  {adv.rssi:>5}  {product:<7}  {encrypted}")
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="xiaomi-thermometer-clock-sync",
        description="Sync the clock of Xiaomi LYWSD02 temperature/humidity monitors over BLE.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("macs", nargs="*", type=mac_address, metavar="MAC", help=f"device address(es); default: ${MAC_ENV}")
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
        try:
            args.macs = [mac_address(m) for m in env]
        except argparse.ArgumentTypeError as e:
            p.error(f"${MAC_ENV}: {e}")
        if not args.macs:
            p.error(f"no device given: pass a MAC address, set ${MAC_ENV}, or run --scan to find one")
    try:
        args.tz_hours = local_tz_hours() if args.tz is None else parse_tz(args.tz)
    except argparse.ArgumentTypeError as e:
        p.error(f"argument --tz: {e}")
    return args


def main(argv=None):
    args = parse_args(argv)
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
