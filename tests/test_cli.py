import pytest

from xiaomi_thermometer_clock_sync.cli import LEGACY_MAC_ENV, MAC_ENV, parse_args


def test_mac_argument_normalized():
    assert parse_args(["aa:bb:cc:dd:ee:ff"]).macs == ["AA:BB:CC:DD:EE:FF"]


def test_multiple_macs():
    assert parse_args(["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"]).macs == ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"]


def test_mac_from_env(monkeypatch):
    monkeypatch.setenv(MAC_ENV, "aa:bb:cc:dd:ee:ff, 11:22:33:44:55:66")
    assert parse_args([]).macs == ["AA:BB:CC:DD:EE:FF", "11:22:33:44:55:66"]


@pytest.mark.parametrize("argv", [[], ["not-a-mac"], ["AA:BB:CC:DD:EE:FF", "--tz", "Asia/Kolkata"]])
def test_invalid_args_exit(monkeypatch, argv):
    monkeypatch.delenv(MAC_ENV, raising=False)
    monkeypatch.delenv(LEGACY_MAC_ENV, raising=False)
    with pytest.raises(SystemExit) as e:
        parse_args(argv)
    assert e.value.code == 2


def test_scan_needs_no_mac(monkeypatch):
    monkeypatch.delenv(MAC_ENV, raising=False)
    assert parse_args(["--scan"]).scan == 10


def test_tz_option():
    assert parse_args(["AA:BB:CC:DD:EE:FF", "--tz", "UTC-5"]).tz_hours == -5


def test_mac_from_legacy_env(monkeypatch):
    monkeypatch.delenv(MAC_ENV, raising=False)
    monkeypatch.setenv(LEGACY_MAC_ENV, "aa:bb:cc:dd:ee:ff")
    assert parse_args([]).macs == ["AA:BB:CC:DD:EE:FF"]
