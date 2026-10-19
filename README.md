# xiaomi-thermometer-clock-sync

Sync Xiaomi LYWSD02 thermometer clocks over Bluetooth LE with a single Python file / 用單一 Python 檔透過藍牙同步小米 LYWSD02 溫濕度計時鐘。

[English](#english) · [繁體中文](#繁體中文)

## English

### Overview

`xiaomi_thermometer_clock_sync.py` syncs the clock of Xiaomi **LYWSD02** e-ink temperature/humidity monitors and reads their device information, battery, temperature and humidity. It is one self-contained file: copy it to any computer with Python 3.10+ and Bluetooth and run it. The tested firmware needs no Mi Home app, Mi account or token.

Each run syncs once and exits; use cron (or another scheduler) for recurring syncs. Multiple devices are processed in order, and a failure on one does not stop the others.

| Item | Requirement / tested configuration |
|---|---|
| Host | Python 3.10+ and an enabled BLE adapter. Tested on Linux (Raspberry Pi, BlueZ); Windows 10+ and macOS are supported by `bleak` but untested |
| Dependency | `bleak>=3.0,<4` (plus `tzdata` on Windows) |
| Tested model | `LYWSD02`, hardware `F2_LN`, firmware `1.1.2_0097`, MiBeacon product ID `0x045b` |
| Not supported | Firmware that requires Mi authentication |
| Host clock | Must already be correct; NTP is recommended |

### Running it

With [uv](https://docs.astral.sh/uv/), dependencies are read from the file header ([PEP 723](https://peps.python.org/pep-0723/)) and installed automatically:

```bash
uv run xiaomi_thermometer_clock_sync.py --scan
uv run xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF
```

Without uv, install `bleak` once (on Windows use `py` instead of `python3`, and also install `tzdata`):

```bash
python3 -m pip install bleak
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF
```

On Debian / Raspberry Pi OS, a virtual environment avoids the system-wide pip restriction:

```bash
sudo apt install python3-venv bluez
python3 -m venv .venv
.venv/bin/pip install bleak
.venv/bin/python xiaomi_thermometer_clock_sync.py --scan
```

### Usage

```bash
python3 xiaomi_thermometer_clock_sync.py --scan                        # find devices (10 s; --scan 20 for longer)
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF --read-only  # read state, don't change the clock
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF              # sync to host time and timezone
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Taipei
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF --read-only -v  # diagnostic logs
```

| Argument | Behavior |
|---|---|
| `MAC ...` | One or more device addresses. On macOS, use the UUID that `--scan` prints; macOS does not expose Bluetooth MAC addresses. |
| `--tz ZONE` | IANA name (`Asia/Taipei`) or whole-hour offset (`8`, `+8`, `-5`, `UTC+8`). Defaults to the system timezone. |
| `--scan [SECONDS]` | List nearby Xiaomi devices, strongest signal first, and exit. |
| `--read-only` | Read time, battery and sensor values without writing. |
| `-v`, `--verbose` | Also show the GATT table, raw reads, tracebacks and bleak debug logs. |
| `--version`, `-h` | Print the version or usage and exit. |

**Where addresses come from**, in order: command-line arguments → `$XIAOMI_CLOCK_MAC` (comma-separated; legacy `$LYWSD02_MAC`) → the first devices file that lists any address:

1. `xiaomi-clock-devices.txt` next to the script, which travels with the script
2. `~/.config/xiaomi-thermometer-clock-sync/devices` (or under `$XDG_CONFIG_HOME`)

Devices files hold one address per line; `#` starts a comment. With a devices file, the script runs with no arguments and no environment variables.

Exit codes: `0` success, `1` at least one device failed (or a scan found nothing), `2` invalid arguments or no address given.

### Timezones and sync behavior

The device stores a Unix timestamp and a **whole-hour** UTC offset (−12 to +14). Fractional zones such as `Asia/Kolkata` are rejected with `--tz`; without `--tz` the system offset is rounded. IANA names resolve to their current offset, so daylight-saving zones need another sync after a change; a recurring schedule handles that.

A sync scans for the device (20 s timeout), connects (30 s), reads device info and battery (warns below 20%), waits for a temperature/humidity notification (15 s), reads the clock, writes the host time, then reads it back and fails if the offset differs or drift exceeds 60 s. `--read-only` stops after reading the clock. There are no automatic retries. Logs go to stderr; the scan table goes to stdout.

### Schedule with cron

```cron
20 */2 * * * /usr/bin/flock -n /tmp/xiaomi-clock-sync.lock /path/to/.venv/bin/python /path/to/xiaomi_thermometer_clock_sync.py >> /path/to/sync.log 2>&1
```

Use absolute paths and put the addresses in a devices file. `flock -n` skips a run if the previous one is still going. Append `--tz Asia/Taipei` before `>>` if needed, and rotate `sync.log` for long-running setups.

### Troubleshooting

| Symptom | What to check |
|---|---|
| No devices found | Bluetooth is on. On Linux: `rfkill list bluetooth`, `sudo rfkill unblock bluetooth`, `bluetoothctl power on`. |
| Target not found / connection fails | Scan again, confirm the address, move closer, check the battery, retry. |
| `bleak` is not installed | Run `pip install bleak` for the Python you use, or run the script with `uv run`. |
| Wrong time after sync | Host clock (`timedatectl status` on Linux), the chosen offset, and the whole-hour limitation. |
| Unknown IANA timezone | Spelling, and that `tzdata` is installed (always needed on Windows). |
| Works manually, fails in cron | Absolute paths, the devices file, the cron user's Bluetooth access, and `sync.log`. |

Rerun with `--read-only -v` for diagnostics. Verbose logs contain MAC addresses and serial numbers; redact them before sharing.

### BLE protocol

| Characteristic UUID | Access | Payload |
|---|---|---|
| `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6` | Time: read/write | Little-endian `uint32` Unix time + `int8` UTC offset hours |
| `ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6` | Battery: read | `uint8` percent |
| `ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6` | Sensor: notify | Little-endian `int16` temperature (0.01 °C) + `uint8` humidity % |
| `ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6` | Display unit: read/write | `0xff` = °C, `0x01` = °F (read only by this tool) |

### Files

```text
xiaomi_thermometer_clock_sync.py   The whole tool: protocol helpers, BLE operations and CLI
xiaomi-clock-devices.txt           Optional device list next to the script (git-ignored)
README.md                          This document
LICENSE                            MIT license
```

`.venv/` and `sync.log` are local to a cron setup and are git-ignored.

[MIT](LICENSE). This project is not affiliated with Xiaomi.

---

## 繁體中文

### 簡介

`xiaomi_thermometer_clock_sync.py` 可透過低功耗藍牙（BLE）同步小米 **LYWSD02** 電子墨水溫濕度計的時鐘，並讀取裝置資訊、電量、溫度與濕度。整個工具只有這一個檔案，複製到任何有 Python 3.10 以上與藍牙的電腦即可執行。已測試的韌體不需要米家 APP、小米帳號或 token。

每次執行同步一次後結束；定期同步請搭配 cron 等排程工具。多台裝置會依序處理，其中一台失敗不影響其他裝置。

| 項目 | 需求／已測試組合 |
|---|---|
| 主機 | Python 3.10 以上與已啟用的 BLE 藍牙。已在 Linux（Raspberry Pi、BlueZ）測試；Windows 10 以上與 macOS 由 `bleak` 支援，但未測試 |
| 依賴 | `bleak>=3.0,<4`（Windows 另需 `tzdata`） |
| 已測試型號 | `LYWSD02`，硬體 `F2_LN`，韌體 `1.1.2_0097`，MiBeacon 產品識別碼 `0x045b` |
| 不支援 | 需要小米認證的韌體 |
| 主機時間 | 必須先確保正確，建議使用 NTP |

### 執行方式

使用 [uv](https://docs.astral.sh/uv/) 時，會讀取檔案開頭宣告的依賴（[PEP 723](https://peps.python.org/pep-0723/)）並自動安裝：

```bash
uv run xiaomi_thermometer_clock_sync.py --scan
uv run xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF
```

不使用 uv 時，先安裝一次 `bleak`（Windows 請將 `python3` 改為 `py`，並另外安裝 `tzdata`）：

```bash
python3 -m pip install bleak
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF
```

Debian／Raspberry Pi OS 可使用虛擬環境，避開系統 pip 的限制：

```bash
sudo apt install python3-venv bluez
python3 -m venv .venv
.venv/bin/pip install bleak
.venv/bin/python xiaomi_thermometer_clock_sync.py --scan
```

### 使用方式

```bash
python3 xiaomi_thermometer_clock_sync.py --scan                        # 掃描裝置（預設 10 秒，可用 --scan 20）
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF --read-only  # 只讀取，不修改時鐘
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF              # 同步為主機時間與時區
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Taipei
python3 xiaomi_thermometer_clock_sync.py AA:BB:CC:DD:EE:FF --read-only -v  # 診斷紀錄
```

| 參數 | 說明 |
|---|---|
| `MAC ...` | 一個或多個裝置位址。macOS 不提供藍牙 MAC，請改用 `--scan` 顯示的 UUID。 |
| `--tz ZONE` | IANA 名稱（`Asia/Taipei`）或整數小時偏移（`8`、`+8`、`-5`、`UTC+8`），預設為系統時區。 |
| `--scan [秒數]` | 依訊號強度列出附近的小米裝置後結束。 |
| `--read-only` | 只讀取時間、電量與感測值，不寫入。 |
| `-v`, `--verbose` | 另外顯示 GATT 表、原始讀值、錯誤追蹤與 bleak 除錯紀錄。 |
| `--version`, `-h` | 顯示版本或用法後結束。 |

**位址來源**依序為：命令列參數 → `$XIAOMI_CLOCK_MAC`（逗號分隔；舊名 `$LYWSD02_MAC`）→ 第一個列有位址的裝置清單檔：

1. 腳本旁的 `xiaomi-clock-devices.txt`，會跟著腳本一起移動
2. `~/.config/xiaomi-thermometer-clock-sync/devices`（或 `$XDG_CONFIG_HOME` 下）

清單檔每行一個位址，`#` 之後為註解。有清單檔時，不帶參數、不設環境變數即可執行。

結束代碼：`0` 成功，`1` 至少一台失敗（或掃描無結果），`2` 參數錯誤或未指定位址。

### 時區與同步流程

裝置儲存 Unix 時間與**整數小時**的 UTC 偏移（−12 至 +14）。使用 `--tz` 時會拒絕非整數時區（如 `Asia/Kolkata`）；未指定時則將系統偏移四捨五入。IANA 名稱會換算為當下的偏移，日光節約時間切換後需再同步一次，定期排程會自動處理。

同步流程：掃描裝置（逾時 20 秒）→ 連線（30 秒）→ 讀取裝置資訊與電量（低於 20% 會警告）→ 等待溫濕度通知（15 秒）→ 讀取時鐘 → 寫入主機時間 → 讀回驗證，偏移不符或誤差超過 60 秒即視為失敗。`--read-only` 在讀取時鐘後即停止。不會自動重試。紀錄輸出至 stderr，掃描表格輸出至 stdout。

### cron 排程

```cron
20 */2 * * * /usr/bin/flock -n /tmp/xiaomi-clock-sync.lock /path/to/.venv/bin/python /path/to/xiaomi_thermometer_clock_sync.py >> /path/to/sync.log 2>&1
```

請使用絕對路徑，並將位址寫入裝置清單檔。`flock -n` 會在前一次尚未結束時跳過本次執行。需要指定時區時，在 `>>` 前加上 `--tz Asia/Taipei`；長期執行請安排 `sync.log` 輪替。

### 疑難排解

| 症狀 | 檢查項目 |
|---|---|
| 找不到任何裝置 | 藍牙是否開啟。Linux：`rfkill list bluetooth`、`sudo rfkill unblock bluetooth`、`bluetoothctl power on`。 |
| 找不到目標或連線失敗 | 重新掃描確認位址、靠近裝置、檢查電池後重試。 |
| 未安裝 `bleak` | 對所使用的 Python 執行 `pip install bleak`，或改用 `uv run`。 |
| 同步後時間錯誤 | 主機時間（Linux：`timedatectl status`）、選用的偏移與整數小時限制。 |
| 無法辨識 IANA 時區 | 拼字，以及是否已安裝 `tzdata`（Windows 必裝）。 |
| 手動正常、cron 失敗 | 絕對路徑、裝置清單檔、cron 使用者的藍牙權限與 `sync.log`。 |

診斷時請加上 `--read-only -v`。詳細紀錄含有 MAC 位址與序號，分享前請先遮蔽。

### BLE 協定

| 特徵值 UUID | 存取 | 內容 |
|---|---|---|
| `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6` | 時間：讀／寫 | 小端序 `uint32` Unix 時間 + `int8` UTC 偏移小時 |
| `ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6` | 電量：讀 | `uint8` 百分比 |
| `ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6` | 感測值：通知 | 小端序 `int16` 溫度（0.01 °C）+ `uint8` 濕度 % |
| `ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6` | 顯示單位：讀／寫 | `0xff` = °C、`0x01` = °F（本工具只讀取） |

### 檔案

```text
xiaomi_thermometer_clock_sync.py   完整工具：協定函式、BLE 操作與命令列介面
xiaomi-clock-devices.txt           腳本旁的裝置清單（選用，不納入 Git）
README.md                          本文件
LICENSE                            MIT 授權
```

`.venv/` 與 `sync.log` 僅供本機 cron 使用，不納入 Git。

採用 [MIT 授權](LICENSE)。本專案與小米公司無隸屬關係。
