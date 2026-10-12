import argparse

import pytest

from lywsd02_sync.protocol import pack_time, parse_mibeacon, parse_tz, unpack_sensor, unpack_time


@pytest.mark.parametrize(
    ("value", "hours"),
    [
        ("8", 8),
        ("+8", 8),
        ("-5", -5),
        ("UTC+8", 8),
        ("utc-5", -5),
        ("GMT+1", 1),
        ("UTC", 0),
        ("0", 0),
        ("Asia/Taipei", 8),
        ("Asia/Tokyo", 9),
        ("Etc/GMT-3", 3),
        ("14", 14),
        ("-12", -12),
    ],
)
def test_parse_tz(value, hours):
    assert parse_tz(value) == hours


@pytest.mark.parametrize("value", ["Asia/Kolkata", "5.5", "Mars/Base", "15", "-13", "abc"])
def test_parse_tz_rejects(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_tz(value)


def test_time_roundtrip():
    # captured from a real device: 2026-10-12T11:51:02+08:00
    raw = bytes.fromhex("2659cc6a08")
    assert unpack_time(raw) == (1791777062, 8)
    assert pack_time(1791777062, 8) == raw


def test_time_negative_tz():
    assert unpack_time(pack_time(1_700_000_000, -5)) == (1_700_000_000, -5)


def test_unpack_time_too_short():
    with pytest.raises(ValueError):
        unpack_time(b"\x00\x01")


def test_unpack_sensor():
    # captured from a real device: 24.56 °C, 57 %
    reading = unpack_sensor(bytes.fromhex("980939"))
    assert reading.temperature == pytest.approx(24.56)
    assert reading.humidity == 57


def test_unpack_sensor_below_zero():
    assert unpack_sensor(bytes.fromhex("0cfe28")).temperature == pytest.approx(-5.0)


def test_parse_mibeacon():
    # captured from a real device advertisement
    beacon = parse_mibeacon(bytes.fromhex("70205b04c65940c1100315090610023a02"))
    assert (beacon.frame_ctrl, beacon.product_id, beacon.counter) == (0x2070, 0x045B, 198)
    assert not beacon.encrypted


def test_parse_mibeacon_too_short():
    assert parse_mibeacon(b"\x70\x20") is None
