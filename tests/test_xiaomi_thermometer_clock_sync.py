"""Tests for xiaomi_thermometer_clock_sync. Standard library only; bleak is not needed.

    python3 -m unittest discover tests
"""

import argparse
import asyncio
import contextlib
import io
import os
import sys
import tempfile
import time
import unittest
import zoneinfo
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import xiaomi_thermometer_clock_sync as sync  # noqa: E402

try:  # IANA data comes from the OS or the tzdata package (needed on Windows)
    zoneinfo.ZoneInfo("Asia/Taipei")
    HAS_TZDATA = True
except zoneinfo.ZoneInfoNotFoundError:
    HAS_TZDATA = False
needs_tzdata = unittest.skipUnless(HAS_TZDATA, "no IANA timezone database (pip install tzdata)")


@contextlib.contextmanager
def system_tz(test, name, std_offset_hours):
    """Switch the process timezone; skip the test if the OS cannot resolve the zone."""
    if not hasattr(time, "tzset"):
        test.skipTest("needs time.tzset")
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        if time.timezone != -std_offset_hours * 3600:
            test.skipTest(f"the system timezone database cannot resolve {name}")
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()

MAC = "AA:BB:CC:DD:EE:FF"
OTHER = "11:22:33:44:55:66"
SENSOR_RAW = bytes.fromhex("980939")  # captured: 24.56 °C, 57 %


class ProtocolTests(unittest.TestCase):
    def test_parse_tz(self):
        cases = {"8": 8, "+8": 8, "-5": -5, "UTC+8": 8, "utc-5": -5, "GMT+1": 1, "UTC": 0, "0": 0, "14": 14, "-12": -12}
        if HAS_TZDATA:
            cases.update({"Asia/Taipei": 8, "Asia/Tokyo": 9, "Etc/GMT-3": 3})
        for value, hours in cases.items():
            with self.subTest(value=value):
                self.assertEqual(sync.parse_tz(value), hours)

    def test_parse_tz_rejects(self):
        bad = ["5.5", "Mars/Base", "15", "-13", "abc", "", "   ", "nan", "inf", "UTC+inf", "-inf"]
        if HAS_TZDATA:
            bad.append("Asia/Kolkata")
        if Path("/usr/share/zoneinfo/America").is_dir():
            bad.append("America")  # a zoneinfo directory, not a zone
        for value in bad:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                sync.parse_tz(value)

    def test_time_roundtrip(self):
        raw = bytes.fromhex("2659cc6a08")  # captured: 2026-10-12T11:51:02+08:00
        self.assertEqual(sync.unpack_time(raw), (1791777062, 8))
        self.assertEqual(sync.pack_time(1791777062, 8), raw)

    def test_time_negative_tz(self):
        self.assertEqual(sync.unpack_time(sync.pack_time(1_700_000_000, -5)), (1_700_000_000, -5))

    def test_unpack_time_wrong_length(self):
        for raw in (b"\x00\x01", bytes(6)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                sync.unpack_time(raw)

    def test_fmt_never_raises_on_corrupt_values(self):
        self.assertIn("invalid", sync.fmt(1_700_000_000, 30))
        self.assertIn("invalid", sync.fmt(1_700_000_000, -128))
        self.assertIn("out of range", sync.fmt(1_700_000_000, 15))
        self.assertEqual(sync.fmt(1791777062, 8), "2026-10-12T11:51:02+08:00")

    def test_unpack_sensor(self):
        reading = sync.unpack_sensor(SENSOR_RAW)
        self.assertAlmostEqual(reading.temperature, 24.56)
        self.assertEqual(reading.humidity, 57)
        self.assertAlmostEqual(sync.unpack_sensor(bytes.fromhex("0cfe28")).temperature, -5.0)

    def test_unpack_sensor_rejects_bad_humidity(self):
        with self.assertRaises(ValueError):
            sync.unpack_sensor(bytes.fromhex("980965"))  # 101 %

    def test_parse_mibeacon(self):
        beacon = sync.parse_mibeacon(bytes.fromhex("70205b04c65940c1100315090610023a02"))  # captured
        self.assertEqual((beacon.frame_ctrl, beacon.product_id, beacon.counter), (0x2070, 0x045B, 198))
        self.assertFalse(beacon.encrypted)
        self.assertIsNone(sync.parse_mibeacon(b"\x70\x20"))

    def test_short_uuid(self):
        self.assertEqual(sync.short_uuid(sync.MIBEACON_UUID), "fe95")
        long_base = "1234fe95-0000-1000-8000-00805f9b34fb"  # 32-bit alias: must not be truncated
        self.assertEqual(sync.short_uuid(long_base), long_base)

    def test_clean_escapes_control_characters(self):
        self.assertEqual(sync.clean("LYWSD02\x1b[2J\nfake"), "LYWSD02\\x1b[2J\\nfake")
        self.assertEqual(sync.clean("客廳 sensor"), "客廳 sensor")

    def test_local_tz_hours(self):
        with system_tz(self, "Asia/Taipei", 8):
            self.assertEqual(sync.local_tz_hours(), 8)
        with system_tz(self, "America/New_York", -5):
            self.assertIn(sync.local_tz_hours(), (-5, -4))
        with system_tz(self, "Asia/Kolkata", 5.5), self.assertRaises(argparse.ArgumentTypeError):
            sync.local_tz_hours()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.devices = Path(self.tmp.name) / "devices.txt"
        patches = [
            mock.patch.object(sync, "devices_files", lambda: [self.devices]),
            mock.patch.dict(os.environ, {}, clear=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(sync.MAC_ENV, None)
        os.environ.pop(sync.LEGACY_MAC_ENV, None)

    def exit_code(self, argv):
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()) as err:
            sync.parse_args(argv)
        return e.exception.code, err.getvalue()

    def test_macs(self):
        self.assertEqual(sync.parse_args(["aa:bb:cc:dd:ee:ff", "--tz", "8"]).macs, [MAC])
        self.assertEqual(sync.parse_args([MAC, OTHER, "--tz", "8"]).macs, [MAC, OTHER])

    def test_mac_from_env(self):
        os.environ[sync.MAC_ENV] = "aa:bb:cc:dd:ee:ff, 11:22:33:44:55:66"
        self.assertEqual(sync.parse_args(["--tz", "8"]).macs, [MAC, OTHER])

    def test_mac_from_legacy_env_and_error_names_it(self):
        os.environ[sync.LEGACY_MAC_ENV] = "aa:bb:cc:dd:ee:ff"
        self.assertEqual(sync.parse_args(["--tz", "8"]).macs, [MAC])
        os.environ[sync.LEGACY_MAC_ENV] = "bogus"
        code, err = self.exit_code(["--tz", "8"])
        self.assertEqual(code, 2)
        self.assertIn(f"${sync.LEGACY_MAC_ENV}", err)

    def test_devices_file_with_bom_and_comments(self):
        self.devices.write_text("﻿# 客廳\naa:bb:cc:dd:ee:ff  # living room\n\n", encoding="utf-8")
        self.assertEqual(sync.parse_args(["--tz", "8"]).macs, [MAC])

    def test_unreadable_devices_file_is_a_usage_error(self):
        self.devices.mkdir()
        code, err = self.exit_code(["--tz", "8"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_invalid_args_exit_2(self):
        for argv in ([], ["not-a-mac"], [MAC, "--tz", "5.5"], [MAC, "--tz", "nan"], [MAC, "--tz", ""],
                     ["--scan", "0"], ["--scan", "-5"], ["--scan", "inf"], ["--scan", "nan"],
                     ["--scan", "--tz", "Mars/Base"], [MAC, "--read-only", "--tz", "+15"]):  # fmt: skip
            with self.subTest(argv=argv):
                self.assertEqual(self.exit_code(argv)[0], 2)

    def test_scan(self):
        self.assertEqual(sync.parse_args(["--scan"]).scan, 10)
        self.assertEqual(sync.parse_args(["--scan", "2.5"]).scan, 2.5)

    def test_fractional_system_zone_only_blocks_sync(self):
        with system_tz(self, "Asia/Kolkata", 5.5):
            self.assertIsNone(sync.parse_args([MAC, "--read-only"]).tz_hours)
            self.assertIsNone(sync.parse_args(["--scan"]).tz_hours)
            self.assertEqual(sync.parse_args([MAC, "--tz", "+5"]).tz_hours, 5)
            code, err = self.exit_code([MAC])
            self.assertEqual(code, 2)
            self.assertIn("pass --tz", err)

    def test_tz_option(self):
        self.assertEqual(sync.parse_args([MAC, "--tz", "UTC-5"]).tz_hours, -5)

    def test_relative_xdg_config_home_is_ignored(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative/dir"}):
            self.assertEqual(sync.config_dir(), Path.home() / ".config" / "xiaomi-thermometer-clock-sync")
        absolute = Path(self.tmp.name).resolve()  # has a drive on Windows
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(absolute)}):
            self.assertEqual(sync.config_dir(), absolute / "xiaomi-thermometer-clock-sync")


# --- fake BLE stack for run() / run_all() ---


class FakeServices:
    def __init__(self, time_props):
        self.time_char = SimpleNamespace(properties=time_props) if time_props else None

    def __iter__(self):
        return iter([])

    def get_characteristic(self, uuid):
        return self.time_char if uuid == sync.TIME_CHAR else None


class FakeClient:
    def __init__(self, clock, model=b"LYWSD02", accept_write=True, time_props=("read", "write")):
        self.values = {
            sync.TIME_CHAR: clock,
            sync.BATTERY_CHAR: b"\x50",
            "00002a24-0000-1000-8000-00805f9b34fb": model,
        }
        self.accept_write = accept_write
        self.services = FakeServices(list(time_props))
        self.is_connected = True
        self.writes = []
        self.stall = None
        self.hang_on_exit = False
        self.exit_error = None  # raised by a disconnect that ignores cancellation
        self.exit_ignores_cancels = 0  # a disconnect that swallows this many cancellations
        self.fail_stop_notify = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        while self.exit_ignores_cancels:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.exit_ignores_cancels -= 1
        if self.exit_error:
            for _ in range(3):  # outlive the cleanup window, ignoring cancellation, then fail
                try:
                    await asyncio.sleep(0.1)
                except asyncio.CancelledError:
                    pass
            raise self.exit_error
        if self.hang_on_exit:
            await asyncio.sleep(3600)
        self.is_connected = False

    async def read_gatt_char(self, uuid):
        if uuid == self.stall:
            await asyncio.sleep(3600)
        if uuid not in self.values:
            raise RuntimeError("characteristic not found")
        return bytearray(self.values[uuid])

    async def write_gatt_char(self, uuid, data, response):
        self.writes.append(bytes(data))
        if self.accept_write:
            self.values[uuid] = bytes(data)

    async def start_notify(self, uuid, callback):
        asyncio.get_running_loop().call_soon(callback, None, bytearray(SENSOR_RAW))

    async def stop_notify(self, uuid):
        if self.fail_stop_notify:
            raise RuntimeError("disconnected")


def fake_scanner(present):
    async def find_device_by_filter(match, timeout):
        for address in present:
            dev = SimpleNamespace(address=address, name="LYWSD02", details={})
            adv = SimpleNamespace(
                local_name="LYWSD02", rssi=-60, tx_power=None, service_uuids=[], service_data={}, manufacturer_data={}
            )
            if match(dev, adv):
                return dev
        return None

    return SimpleNamespace(find_device_by_filter=find_device_by_filter)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.now = round(time.time())

    def go(self, client, macs=(MAC,), tz="+8", **kw):
        with mock.patch.object(sync, "BleakScanner", fake_scanner([MAC])), mock.patch.object(
            sync, "BleakClient", lambda dev, timeout: client
        ), self.assertLogs(sync.log, "DEBUG") as logs:
            failed = asyncio.run(sync.run_all(list(macs), tz, **kw))
        return failed, "\n".join(logs.output)

    def test_sync_writes_and_verifies(self):
        client = FakeClient(sync.pack_time(self.now - 3600, 0))
        failed, out = self.go(client)
        self.assertEqual(failed, [])
        self.assertEqual(len(client.writes), 1)
        self.assertEqual(sync.unpack_time(client.writes[0])[1], 8)
        self.assertIn("synced", out)
        self.assertNotIn("temperature", out)  # sensor is diagnostics-only

    def test_read_only_does_not_write(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        failed, out = self.go(client, read_only=True, diagnostics=True)
        self.assertEqual((failed, client.writes), ([], []))
        self.assertIn("temperature:  24.56", out)

    def test_rejected_write_fails_verification(self):
        client = FakeClient(sync.pack_time(self.now - 3600, 8), accept_write=False)
        failed, out = self.go(client)
        self.assertEqual(failed, [MAC])
        self.assertIn("verification failed", out)

    def test_unsupported_model_is_not_written(self):
        client = FakeClient(sync.pack_time(self.now, 8), model=b"MHO-C401")
        failed, out = self.go(client)
        self.assertEqual((failed, client.writes), ([MAC], []))
        self.assertIn("not supported", out)

    def test_read_only_time_characteristic_is_not_written(self):
        client = FakeClient(sync.pack_time(self.now, 8), time_props=("read",))
        self.assertEqual(self.go(client)[0], [MAC])
        self.assertEqual(client.writes, [])

    def test_missing_model_is_not_written(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        del client.values["00002a24-0000-1000-8000-00805f9b34fb"]
        failed, out = self.go(client)
        self.assertEqual((failed, client.writes), ([MAC], []))
        self.assertIn("model unavailable", out)

    def test_rejected_write_close_to_correct_still_fails(self):
        client = FakeClient(sync.pack_time(self.now - 5, 8), accept_write=False)
        self.assertEqual(self.go(client)[0], [MAC])

    def test_hung_disconnect_does_not_block_the_batch(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        client.stall = sync.TIME_CHAR
        client.hang_on_exit = True
        with mock.patch.multiple(sync, GATT_TIMEOUT=3600, DEVICE_TIMEOUT=0.1, CLEANUP_TIMEOUT=0.05):
            t0 = time.monotonic()
            failed, out = self.go(client, macs=(MAC, OTHER))
        self.assertLess(time.monotonic() - t0, 2)
        self.assertEqual(failed, [MAC, OTHER])
        self.assertIn("gave up after", out)

    def test_stuck_cleanup_forces_exit_instead_of_hanging(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        client.stall = sync.TIME_CHAR
        client.exit_ignores_cancels = 2  # 1st cancel hits the stuck read; outlasts the 2nd
        args = SimpleNamespace(macs=[MAC], tz="+8", read_only=False, verbose=False)
        exits = []

        def fake_exit(code):
            exits.append(code)
            raise SystemExit(code)

        with mock.patch.multiple(sync, GATT_TIMEOUT=3600, DEVICE_TIMEOUT=0.05, CLEANUP_TIMEOUT=0.01), mock.patch.object(
            sync, "BleakScanner", fake_scanner([MAC])
        ), mock.patch.object(sync, "BleakClient", lambda dev, timeout: client), mock.patch.object(
            sync.os, "_exit", fake_exit
        ), self.assertLogs(sync.log) as logs, self.assertRaises(SystemExit):
            asyncio.run(sync.sync_all(args))
        self.assertEqual(exits, [1])
        self.assertIn("exiting without waiting", "\n".join(logs.output))

    def test_clean_run_returns_exit_code(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        args = SimpleNamespace(macs=[MAC], tz="+8", read_only=False, verbose=False)
        with mock.patch.object(sync, "BleakScanner", fake_scanner([MAC])), mock.patch.object(
            sync, "BleakClient", lambda dev, timeout: client
        ), self.assertLogs(sync.log):
            self.assertEqual(asyncio.run(sync.sync_all(args)), 0)

    def test_abandoned_cleanup_error_is_retrieved(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        client.stall = sync.TIME_CHAR
        client.exit_error = RuntimeError("disconnect failed")
        handler_calls = []

        async def main():
            asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: handler_calls.append(ctx))
            failed = await sync.run_all([MAC], "+8")
            await asyncio.sleep(0.4)  # let the abandoned task finish
            return failed

        with mock.patch.multiple(sync, GATT_TIMEOUT=3600, DEVICE_TIMEOUT=0.05, CLEANUP_TIMEOUT=0.01), mock.patch.object(
            sync, "BleakScanner", fake_scanner([MAC])
        ), mock.patch.object(sync, "BleakClient", lambda dev, timeout: client), self.assertLogs(sync.log):
            self.assertEqual(asyncio.run(main()), [MAC])
        import gc

        gc.collect()
        self.assertEqual(handler_calls, [])

    def test_corrupt_device_timezone_is_still_fixed(self):
        client = FakeClient(sync.pack_time(self.now, 30))
        failed, out = self.go(client)
        self.assertEqual(failed, [])
        self.assertIn("invalid (unix", out)
        self.assertIn("UTC+30 (out of range) -> UTC+8", out)

    def test_stalled_read_times_out_and_batch_continues(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        client.stall = sync.TIME_CHAR
        with mock.patch.object(sync, "GATT_TIMEOUT", 0.05):
            failed, out = self.go(client, macs=(MAC, OTHER))
        self.assertEqual(failed, [MAC, OTHER])  # MAC stalls, OTHER is not present
        self.assertIn("TimeoutError: timed out", out)
        self.assertIn("not found", out)

    def test_sensor_survives_missing_units_and_cleanup_failure(self):
        client = FakeClient(sync.pack_time(self.now, 8))
        client.fail_stop_notify = True
        failed, out = self.go(client, read_only=True, diagnostics=True)
        self.assertEqual(failed, [])
        self.assertIn("display unit °?", out)
        self.assertIn("humidity:     57%", out)


if __name__ == "__main__":
    unittest.main()
