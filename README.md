# xiaomi-thermometer-clock-sync

Sync the clock of Xiaomi **LYWSD02** e-ink temperature/humidity monitors over Bluetooth LE from Linux (e.g. a Raspberry Pi), and read their battery, temperature and humidity. No Mi Home app, Mi account or token is needed.

[繁體中文說明](#繁體中文)

## Supported devices

| Device | Firmware | Status |
|---|---|---|
| Xiaomi Temperature and Humidity Monitor Clock (`LYWSD02`, manufacturer `miaomiaoce.com`, hardware `F2_LN`, MiBeacon product id `0x045b`) | `1.1.2_0097` | ✅ tested |
| Devices with encrypted MiBeacon / newer firmware that require Mi authentication | — | ❌ not supported |

Run `xiaomi-thermometer-clock-sync --scan`: if your device shows `ENCRYPTED no` it should work.

## Install

Requires Linux with BlueZ and Python 3.10+.

```bash
git clone https://github.com/echoapus/xiaomi-thermometer-clock-sync.git
cd xiaomi-thermometer-clock-sync
python3 -m venv .venv
.venv/bin/pip install .
```

## Usage

```bash
# 1. Find your device
.venv/bin/xiaomi-thermometer-clock-sync --scan
# MAC                NAME           RSSI  PRODUCT  ENCRYPTED
# AA:BB:CC:DD:EE:FF  LYWSD02         -58  0x045b   no

# 2. Sync its clock to this machine's time and timezone
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF

# Several devices, a specific timezone
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Tokyo

# Only read time, battery, temperature and humidity
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --read-only

# MAC addresses from the environment
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF .venv/bin/xiaomi-thermometer-clock-sync
```

| Option | Description |
|---|---|
| `MAC ...` | Device address(es). Default: `$XIAOMI_CLOCK_MAC` (comma-separated; the old name `$LYWSD02_MAC` also works). |
| `--tz ZONE` | IANA name (`Asia/Taipei`, `America/New_York`) or whole-hour offset (`8`, `+8`, `-5`, `UTC+8`). Default: system timezone. |
| `--scan [SECONDS]` | List nearby Xiaomi devices (default 10 s) and exit. |
| `--read-only` | Report state without writing the clock. |
| `-v`, `--verbose` | Also print the GATT table, raw reads and bleak/BlueZ debug logs. |

Exit codes: `0` success, `1` a device failed (not found, connection error, verification failed), `2` invalid arguments.

**Timezones:** the device stores a whole-hour UTC offset, so half-hour zones such as `Asia/Kolkata` are rejected. Daylight-saving zones are resolved to their current offset; a scheduled daily sync picks up DST changes automatically.

### Sample output

```
INFO  xiaomi_thermometer_clock_sync: [1/7] scanning for AA:BB:CC:DD:EE:FF (timeout 20s)...
INFO  xiaomi_thermometer_clock_sync: found AA:BB:CC:DD:EE:FF name='LYWSD02' in 5244ms
INFO  xiaomi_thermometer_clock_sync: adv: MiBeacon frame_ctrl=0x2070 product_id=0x045b counter=198 encrypted=False
INFO  xiaomi_thermometer_clock_sync: [2/7] connecting...
INFO  xiaomi_thermometer_clock_sync: connected in 4277ms, mtu=23, 8 services / 47 characteristics
INFO  xiaomi_thermometer_clock_sync: [3/7] reading device information
INFO  xiaomi_thermometer_clock_sync:   model:        LYWSD02
INFO  xiaomi_thermometer_clock_sync:   firmware rev: 1.1.2_0097
INFO  xiaomi_thermometer_clock_sync:   battery:      16% (raw 10, 75ms)
WARNING xiaomi_thermometer_clock_sync: battery low (16%), replace soon
INFO  xiaomi_thermometer_clock_sync: [4/7] reading temperature / humidity
INFO  xiaomi_thermometer_clock_sync:   temperature:  24.65°C (display unit °C)
INFO  xiaomi_thermometer_clock_sync:   humidity:     56% (raw a10938, 5066ms)
INFO  xiaomi_thermometer_clock_sync: [5/7] reading device time
INFO  xiaomi_thermometer_clock_sync: device time: 2026-10-12T11:50:04+08:00 (raw ec58cc6a08, unix 1791776940, drift -58s, tz +8, 74ms)
INFO  xiaomi_thermometer_clock_sync: [6/7] writing host time
INFO  xiaomi_thermometer_clock_sync: wrote 2026-10-12T11:51:02+08:00 (raw 2659cc6a08, unix 1791777062, 5 bytes, with response, 73ms)
INFO  xiaomi_thermometer_clock_sync: [7/7] verifying
INFO  xiaomi_thermometer_clock_sync: AA:BB:CC:DD:EE:FF: synced (was -58s off), UTC+8, total 17.1s
```

## Run on a schedule

**systemd timer** (daily at 03:00):

```bash
sudo git clone https://github.com/echoapus/xiaomi-thermometer-clock-sync.git /opt/xiaomi-thermometer-clock-sync && cd /opt/xiaomi-thermometer-clock-sync
sudo python3 -m venv .venv && sudo .venv/bin/pip install .
sudo cp deploy/systemd/xiaomi-thermometer-clock-sync.default /etc/default/xiaomi-thermometer-clock-sync   # set XIAOMI_CLOCK_MAC
sudo cp deploy/systemd/xiaomi-thermometer-clock-sync.service deploy/systemd/xiaomi-thermometer-clock-sync.timer /etc/systemd/system/  # adjust User=
sudo systemctl daemon-reload && sudo systemctl enable --now xiaomi-thermometer-clock-sync.timer
journalctl -u xiaomi-thermometer-clock-sync   # logs
```

**cron:** see [`deploy/cron/crontab.example`](deploy/cron/crontab.example).

## Troubleshooting

- **No devices found at all:** Bluetooth may be blocked. Check `rfkill list bluetooth` and `bluetoothctl show`; fix with `sudo rfkill unblock bluetooth && bluetoothctl power on`.
- **Device not found:** keep it within a few metres, make sure the screen is on, and check the battery. Connections get unreliable when it is low.
- **Connecting takes 5–15 s:** normal for this device.
- **Anything else:** run with `-v` and include the output in an issue.

## Protocol notes

See [the BLE protocol reference](docs/protocol.md) for characteristic UUIDs, payload formats, and verification behavior.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/pytest
```

Unit tests cover CLI argument parsing and protocol encoding/decoding and need no device. Dependencies are declared in `pyproject.toml`.

## Project layout

```text
xiaomi_thermometer_clock_sync/  Python package and CLI
tests/                        Device-free unit tests
docs/protocol.md              BLE protocol reference
deploy/systemd/               Service, timer, and environment file
deploy/cron/                  Crontab example
pyproject.toml                Package metadata, dependencies, and tool settings
```

The CLI can also be run with `python -m xiaomi_thermometer_clock_sync`.
Local virtual environments, installation metadata, caches, and logs are ignored by Git.

## License

[MIT](LICENSE). Not affiliated with Xiaomi.

---

## 繁體中文

在 Linux（例如 Raspberry Pi）上透過藍牙同步小米 **LYWSD02** 電子墨水溫濕度計的時間，並讀取電量、溫度、濕度。不需要米家 APP、小米帳號或 token。

```bash
python3 -m venv .venv && .venv/bin/pip install .
.venv/bin/xiaomi-thermometer-clock-sync --scan                          # 找出裝置 MAC
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF               # 同步時間（系統時區）
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --tz Asia/Tokyo
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --read-only   # 只讀取，不寫入
```

- `--tz` 接受時區名稱或整數小時偏移；裝置只支援整點時區，`Asia/Kolkata`（+5.5）等會被拒絕。
- 掃描不到任何裝置時，先執行 `sudo rfkill unblock bluetooth`。
- 定期同步請參考上方「Run on a schedule」與 `deploy/`。
- 只支援 `--scan` 顯示 `ENCRYPTED no` 的裝置；需要小米加密認證的新韌體不支援。
