"""BLE operations against a LYWSD02 device."""

import asyncio
import logging
import time
import warnings

from bleak import BleakClient, BleakScanner

from .protocol import (
    BATTERY_CHAR,
    DEVICE_INFO,
    MIBEACON_UUID,
    SENSOR_CHAR,
    TIME_CHAR,
    UNITS,
    UNITS_CHAR,
    fmt,
    pack_time,
    parse_mibeacon,
    short_uuid,
    unpack_sensor,
    unpack_time,
)

log = logging.getLogger("lywsd02_sync")

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
