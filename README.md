# xiaomi-thermometer-clock-sync

Sync Xiaomi LYWSD02 thermometer clocks over Bluetooth LE from Linux / 在 Linux 上透過藍牙同步小米 LYWSD02 溫濕度計時鐘。

[English](#english) · [繁體中文](#繁體中文)

## English

### Overview

A Python command-line tool for syncing the clock of Xiaomi **LYWSD02** e-ink temperature/humidity monitors from Linux, including Raspberry Pi. It also reads device information, battery level, temperature, and humidity. The tested firmware needs no Mi Home app, Mi account, or token.

Each invocation runs once and exits. Use systemd or cron for recurring syncs. Multiple devices are processed sequentially; a failure on one device does not stop the remaining devices.

### Compatibility and requirements

| Item | Requirement / tested configuration |
|---|---|
| Host | Linux with BlueZ, an enabled BLE adapter, and Python 3.10+ |
| Python dependency | `bleak>=3.0,<4`, installed automatically with the package |
| Tested model | `LYWSD02`, manufacturer `miaomiaoce.com`, hardware `F2_LN` |
| Tested firmware | `1.1.2_0097` |
| MiBeacon product ID | `0x045b` |
| Authentication | Firmware requiring Mi authentication is unsupported |
| Host clock | Must already have the correct time; NTP synchronization is recommended |

Discovery lists Xiaomi-like advertisements, including names beginning with `LYWSD` or `MHO`. A scan result is not a compatibility guarantee. `ENCRYPTED no` describes the MiBeacon advertisement only; it does not prove that a device exposes the supported clock characteristics. Other models and firmware are unverified.

### Installation

On Debian / Raspberry Pi OS, install the system prerequisites if needed:

```bash
sudo apt update
sudo apt install git python3 python3-venv bluez tzdata
```

Clone and install the package:

```bash
git clone https://github.com/echoapus/xiaomi-thermometer-clock-sync.git
cd xiaomi-thermometer-clock-sync
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/xiaomi-thermometer-clock-sync --version
```

The examples use the virtual environment directly; activation is optional. Run them from the project directory. After updating the source, run `.venv/bin/pip install .` again for a regular installation. For development, use the editable installation below.

### Usage

Replace the example MAC addresses with those reported by your scan.

```bash
# Discover nearby devices (10 seconds by default)
.venv/bin/xiaomi-thermometer-clock-sync --scan
.venv/bin/xiaomi-thermometer-clock-sync --scan 20

# Read device state without changing its clock
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --read-only

# Sync to the host time and system timezone
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF

# Specify a timezone; multiple devices are processed in order
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Taipei

# Use a numeric UTC offset
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --tz UTC-5

# Read addresses from an environment variable
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66 .venv/bin/xiaomi-thermometer-clock-sync

# Enable diagnostic logs
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --read-only -v
```

Equivalent module entry point:

```bash
.venv/bin/python -m xiaomi_thermometer_clock_sync --help
```

| Argument | Behavior |
|---|---|
| `MAC ...` | One or more colon-separated MAC addresses; normalized to uppercase. |
| `--tz ZONE` | IANA timezone such as `Asia/Taipei`, or whole-hour offset such as `8`, `+8`, `-5`, `UTC+8`, or `GMT+1`. Defaults to the system timezone. |
| `--scan [SECONDS]` | Discover devices and exit; default 10 seconds. Results are ordered by signal strength. No MAC is required. |
| `--read-only` | Read device state without writing the clock. |
| `-v`, `--verbose` | Include the GATT table, raw reads, tracebacks for device failures, and Bleak/BlueZ debug logs. |
| `--version` | Print the package version and exit. |
| `-h`, `--help` | Print usage and exit. |

Explicit MAC arguments take precedence over environment variables. If none are given, the CLI reads comma-separated addresses from `XIAOMI_CLOCK_MAC`, then falls back to the legacy name `LYWSD02_MAC` when the first variable is empty or unset. The CLI does not automatically load `.env` files.

| Exit code | Meaning |
|---|---|
| `0` | Requested device operations succeeded, or discovery found matching devices. Help/version also exit successfully. |
| `1` | At least one device failed, or discovery found no matching devices. Unhandled runtime errors can also exit with this code. |
| `2` | Invalid command-line arguments, including missing addresses outside scan mode. |

### Timezones and sync behavior

The device stores a Unix timestamp and a signed **whole-hour UTC offset**, not a timezone name. Explicit `--tz` values must resolve to an integer between UTC−12 and UTC+14. Fractional offsets such as `5.5` or `Asia/Kolkata` are rejected.

**Current limitation:** without `--tz`, the implementation rounds the system UTC offset to a whole hour. It does not reject fractional system timezones. Such local times cannot be represented accurately by this device format. Use an explicit supported timezone when the host timezone differs from the desired device timezone.

IANA names are resolved to their current offset at invocation time. For daylight-saving zones, run another sync after an offset change; a recurring schedule applies the new offset on its next run.

A normal sync performs these steps:

1. Find the target MAC (scan timeout: 20 seconds).
2. Connect over BLE (connection timeout: 30 seconds).
3. Read device information and battery level; warn below 20% battery.
4. Read the display unit and wait for a temperature/humidity notification (timeout: 15 seconds).
5. Read the clock and report its difference from the host clock.
6. Write the host Unix time and selected UTC offset with a GATT write response.
7. Read the clock back and verify that the offset matches and absolute drift is at most 60 seconds.

Read-only mode stops after step 5. Device information, battery, and sensor read failures produce warnings and allow the clock operation to continue. Clock read/write or verification failures fail that device. There is no automatic retry within an invocation.

The program logs the host's NTP status but neither synchronizes the host clock nor refuses to run when NTP is unsynchronized. Verification compares against the host clock, not an external time source.

Example discovery output (illustrative):

```text
MAC                NAME           RSSI  PRODUCT  ENCRYPTED
AA:BB:CC:DD:EE:FF   LYWSD02         -58  0x045b   no
```

Example log excerpts (timestamps omitted):

```text
INFO  xiaomi_thermometer_clock_sync: [1/7] scanning for AA:BB:CC:DD:EE:FF (timeout 20s)...
INFO  xiaomi_thermometer_clock_sync:   temperature:  24.65°C (display unit °C)
INFO  xiaomi_thermometer_clock_sync: [7/7] verifying
INFO  xiaomi_thermometer_clock_sync: AA:BB:CC:DD:EE:FF: synced (was -58s off), UTC+8, total 17.1s
```

A negative drift means the device clock is behind the host. Temperature is reported in Celsius even when the device display uses Fahrenheit. Logs go to stderr; discovery's table goes to stdout. The CLI itself does not create a log file.

### Run on a schedule

Choose systemd or cron for a given device to avoid overlapping jobs. The examples below install a schedule only when you execute them; installing the Python package alone does not enable one.

#### systemd timer

The supplied [service and timer](deploy/systemd/) use `/opt/xiaomi-thermometer-clock-sync`, run as `pi`, and load addresses from `/etc/default/xiaomi-thermometer-clock-sync`. Adjust the service user and paths to your host. The user must have access to Bluetooth through BlueZ.

```bash
sudo git clone https://github.com/echoapus/xiaomi-thermometer-clock-sync.git /opt/xiaomi-thermometer-clock-sync
cd /opt/xiaomi-thermometer-clock-sync
sudo python3 -m venv .venv
sudo .venv/bin/pip install .

sudo cp deploy/systemd/xiaomi-thermometer-clock-sync.default /etc/default/xiaomi-thermometer-clock-sync
sudoedit /etc/default/xiaomi-thermometer-clock-sync

sudo cp deploy/systemd/xiaomi-thermometer-clock-sync.service deploy/systemd/xiaomi-thermometer-clock-sync.timer /etc/systemd/system/
sudoedit /etc/systemd/system/xiaomi-thermometer-clock-sync.service
sudo systemctl daemon-reload

# Test the service before enabling the timer
sudo systemctl start xiaomi-thermometer-clock-sync.service
sudo journalctl -u xiaomi-thermometer-clock-sync.service -n 100 --no-pager
sudo systemctl enable --now xiaomi-thermometer-clock-sync.timer
systemctl list-timers xiaomi-thermometer-clock-sync.timer
```

Set real addresses in the environment file:

```ini
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66
```

The timer runs daily at 03:00 in the system timezone with up to five minutes of randomized delay. `Persistent=true` allows a missed calendar run to be caught up when the timer becomes active again. The service has a five-minute overall timeout; adjust `TimeoutStartSec` if many devices need more time.

To specify the device timezone, append `--tz Asia/Taipei` to the service's `ExecStart` command and run `sudo systemctl daemon-reload`. To change the schedule, edit the timer's `OnCalendar`, reload systemd, and restart the timer. To disable recurring syncs:

```bash
sudo systemctl disable --now xiaomi-thermometer-clock-sync.timer
```

#### cron

Run `crontab -e` and add the following for a checkout in `$HOME/xiaomi-thermometer-clock-sync`:

```cron
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF
0 3 * * * $HOME/xiaomi-thermometer-clock-sync/.venv/bin/xiaomi-thermometer-clock-sync >> $HOME/xiaomi-thermometer-clock-sync/sync.log 2>&1
```

This is also provided in [deploy/cron/crontab.example](deploy/cron/crontab.example). Change the paths to your installation. For hourly execution, replace `0 3 * * *` with `0 * * * *`. Append `--tz Asia/Taipei` before the output redirection if needed. Remove the crontab entry to disable it.

The example appends both output streams to `sync.log`; arrange log rotation for long-running deployments. If jobs might overlap, use `flock` with a lock file writable by the job's user, for example:

```cron
0 * * * * /usr/bin/flock -n /tmp/xiaomi-clock-sync.lock $HOME/xiaomi-thermometer-clock-sync/.venv/bin/xiaomi-thermometer-clock-sync >> $HOME/xiaomi-thermometer-clock-sync/sync.log 2>&1
```

Keep the `XIAOMI_CLOCK_MAC` line above this job. Confirm `/usr/bin/flock` exists on your host. With `-n`, an overlapping run is skipped instead of waiting.

### Troubleshooting

| Symptom | What to check |
|---|---|
| No devices found | Run `rfkill list bluetooth` and `bluetoothctl show`. If blocked, use `sudo rfkill unblock bluetooth`, then `bluetoothctl power on`. |
| Target MAC not found | Scan again, confirm the address, move the device closer, and check its battery. |
| Slow or failed connection | Connections can take several seconds. Check signal strength and low battery; retry the command. |
| Battery warning | The program warns below 20%; replace the battery when appropriate. |
| Sensor unavailable | The sensor uses notifications rather than a normal read. A timeout is logged as a warning; clock sync can still succeed. |
| Incorrect time after sync | Check the host clock with `timedatectl status`, the selected UTC offset, and the fractional-timezone limitation above. |
| Unknown IANA timezone | Check spelling and that the system timezone database (`tzdata`) is installed. |
| Characteristic/authentication error | Check model and firmware compatibility; Mi authentication is not implemented. |
| Works manually but fails on a schedule | Check the service/cron user, absolute executable path, configured MAC addresses, Bluetooth access, and logs. |

For diagnostics, rerun with `--read-only -v`. An issue report should include the command, package version, host environment, model/firmware, and relevant error output. Verbose logs may contain device MAC addresses and serial numbers; redact them before sharing.

### BLE protocol

| Characteristic UUID | Access | Payload |
|---|---|---|
| `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6` | Time: read/write | Little-endian `uint32` Unix timestamp + `int8` UTC offset hours |
| `ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6` | Battery: read | `uint8` percentage |
| `ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6` | Sensor: notify | Little-endian `int16` temperature in hundredths of °C + `uint8` humidity % |
| `ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6` | Display unit: read/write | `0xff` = °C, `0x01` = °F; this CLI only reads it |

See [docs/protocol.md](docs/protocol.md) and [protocol.py](xiaomi_thermometer_clock_sync/protocol.py) for protocol details and encoding helpers.

### Development and project layout

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
```

The existing unit tests cover CLI argument parsing, timezone handling, and protocol encoding/decoding. They do not require Bluetooth hardware and do not exercise the live BLE connection flow. CI runs lint, formatting checks, and tests on Python 3.10–3.13.

```text
.github/workflows/ci.yml       CI workflow
xiaomi_thermometer_clock_sync/
  __init__.py                 Package version
  __main__.py                 python -m entry point
  cli.py                      Arguments, logging, and device orchestration
  device.py                   BLE discovery, reads, writes, and verification
  protocol.py                 UUIDs, binary formats, and timezone helpers
tests/                        Device-free unit tests
docs/protocol.md              BLE protocol reference
deploy/systemd/               Service, timer, and environment example
deploy/cron/                  Crontab example
pyproject.toml                Dependencies, package metadata, and tool settings
```

Dependencies are declared in `pyproject.toml`. Use the installed CLI or module entry point; the legacy `sync_time.py` wrapper has been removed. Virtual environments, package installation metadata, caches, logs, and local `.env` files are ignored by Git; `.env.example` remains trackable.

### License

[MIT](LICENSE). This project is not affiliated with Xiaomi.

---

## 繁體中文

### 專案簡介

這是一個 Python 命令列工具，可在 Linux（包含 Raspberry Pi）上，透過低功耗藍牙（BLE）同步小米 **LYWSD02** 電子墨水溫濕度計的時鐘，並讀取裝置資訊、電量、溫度與濕度。已測試的韌體不需要米家 APP、小米帳號或 token。

每次執行完成後就會結束；定期同步可搭配 systemd 或 cron。多台裝置會依序處理，其中一台失敗不會中止其餘裝置的操作。

### 相容性與環境需求

| 項目 | 需求／已測試組合 |
|---|---|
| 主機 | Linux、BlueZ、已啟用的 BLE 藍牙介面，以及 Python 3.10 以上 |
| Python 依賴 | `bleak>=3.0,<4`，安裝套件時會自動安裝 |
| 已測試型號 | `LYWSD02`，製造商 `miaomiaoce.com`，硬體 `F2_LN` |
| 已測試韌體 | `1.1.2_0097` |
| MiBeacon 產品識別碼 | `0x045b` |
| 認證限制 | 不支援需要小米認證的韌體 |
| 主機時間 | 必須先確保時間正確，建議透過 NTP 同步 |

掃描功能會列出具有小米特徵的廣播，也包含名稱以 `LYWSD` 或 `MHO` 開頭的裝置。**掃描得到不代表一定相容。** `ENCRYPTED no` 只表示 MiBeacon 廣播未加密，無法保證裝置提供本工具支援的時鐘特徵值。其他型號與韌體尚未驗證。

### 安裝

Debian／Raspberry Pi OS 若尚未安裝必要的系統套件，可先執行：

```bash
sudo apt update
sudo apt install git python3 python3-venv bluez tzdata
```

下載專案並安裝：

```bash
git clone https://github.com/echoapus/xiaomi-thermometer-clock-sync.git
cd xiaomi-thermometer-clock-sync
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/xiaomi-thermometer-clock-sync --version
```

範例直接使用虛擬環境內的執行檔，不必先啟用虛擬環境。請在專案目錄內執行。一般安裝在更新原始碼後，需再次執行 `.venv/bin/pip install .`；開發用途則可使用下方的可編輯安裝方式。

### 使用方式

請將範例 MAC 位址替換成實際掃描到的裝置位址。

```bash
# 掃描附近裝置，預設持續 10 秒
.venv/bin/xiaomi-thermometer-clock-sync --scan
.venv/bin/xiaomi-thermometer-clock-sync --scan 20

# 只讀取裝置狀態，不修改時鐘
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --read-only

# 同步為主機時間與系統時區
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF

# 指定時區；多台裝置會依序處理
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF 11:22:33:44:55:66 --tz Asia/Taipei

# 使用數字形式的 UTC 偏移
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --tz UTC-5

# 從環境變數讀取裝置位址
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66 .venv/bin/xiaomi-thermometer-clock-sync

# 開啟詳細診斷紀錄
.venv/bin/xiaomi-thermometer-clock-sync AA:BB:CC:DD:EE:FF --read-only -v
```

也可以透過 Python 模組入口執行：

```bash
.venv/bin/python -m xiaomi_thermometer_clock_sync --help
```

| 參數 | 說明 |
|---|---|
| `MAC ...` | 一個或多個以冒號分隔的 MAC 位址，程式會統一轉成大寫。 |
| `--tz ZONE` | IANA 時區名稱，例如 `Asia/Taipei`；或整數小時偏移，例如 `8`、`+8`、`-5`、`UTC+8`、`GMT+1`。預設使用系統時區。 |
| `--scan [SECONDS]` | 掃描後結束，預設 10 秒，依訊號強度排序；不需要指定 MAC。 |
| `--read-only` | 只讀取裝置狀態，不寫入時鐘。 |
| `-v`、`--verbose` | 顯示 GATT 表、原始讀值、裝置操作失敗的 traceback，以及 Bleak／BlueZ 除錯紀錄。 |
| `--version` | 顯示套件版本後結束。 |
| `-h`、`--help` | 顯示使用說明後結束。 |

命令列指定的 MAC 優先於環境變數。未指定時，程式會讀取 `XIAOMI_CLOCK_MAC`，多台位址以逗號分隔；若此變數為空或未設定，則使用舊名稱 `LYWSD02_MAC`。CLI 不會自動載入 `.env` 檔案。

| 結束代碼 | 意義 |
|---|---|
| `0` | 指定的裝置操作成功，或掃描找到符合條件的裝置；顯示說明與版本也會正常結束。 |
| `1` | 至少一台裝置操作失敗，或掃描未找到符合條件的裝置；未捕捉的執行期錯誤也可能使用此代碼。 |
| `2` | 命令列參數錯誤，包含非掃描模式下未提供裝置位址。 |

### 時區與同步流程

裝置儲存的是 Unix 時間戳記與帶正負號的**整數小時 UTC 偏移**，不會儲存時區名稱。明確指定的 `--tz` 必須解析為 UTC−12 至 UTC+14 之間的整數。`5.5`、`Asia/Kolkata` 等非整點偏移會被拒絕。

**目前限制：**未提供 `--tz` 時，程式會將系統 UTC 偏移取整數，不會拒絕非整點的系統時區。因此，這類地區的當地時間無法以本裝置的格式精確表示。若主機時區與預期的裝置時區不同，請明確指定支援的時區。

IANA 時區名稱會在每次執行時換算成當下的 UTC 偏移。採用日光節約時間的地區，需在偏移變更後再次同步；定期排程會在下一次執行時套用新的偏移。

一般同步依序執行：

1. 搜尋指定 MAC，掃描逾時為 20 秒。
2. 建立 BLE 連線，連線逾時為 30 秒。
3. 讀取裝置資訊與電量；低於 20% 時發出警告。
4. 讀取顯示單位，等待溫濕度通知，通知逾時為 15 秒。
5. 讀取裝置時鐘，顯示與主機時間的差距。
6. 寫入主機 Unix 時間與指定 UTC 偏移，並要求 GATT 寫入回應。
7. 重新讀取時鐘，確認 UTC 偏移相符，且與主機時間的絕對誤差不超過 60 秒。

唯讀模式在第 5 步後結束。裝置資訊、電量或感測器讀取失敗時，程式會記錄警告並繼續處理時鐘；時鐘讀寫或驗證失敗則視為該台裝置操作失敗。單次執行不會自動重試。

程式會記錄主機的 NTP 同步狀態，但不會替主機校時，也不會因 NTP 未同步而拒絕執行。驗證基準是主機時鐘，不是外部時間來源。

掃描輸出範例（示意）：

```text
MAC                NAME           RSSI  PRODUCT  ENCRYPTED
AA:BB:CC:DD:EE:FF   LYWSD02         -58  0x045b   no
```

執行紀錄節錄（省略時間戳記）：

```text
INFO  xiaomi_thermometer_clock_sync: [1/7] scanning for AA:BB:CC:DD:EE:FF (timeout 20s)...
INFO  xiaomi_thermometer_clock_sync:   temperature:  24.65°C (display unit °C)
INFO  xiaomi_thermometer_clock_sync: [7/7] verifying
INFO  xiaomi_thermometer_clock_sync: AA:BB:CC:DD:EE:FF: synced (was -58s off), UTC+8, total 17.1s
```

負的時間差代表裝置比主機慢。即使裝置螢幕使用華氏，程式仍以攝氏回報溫度。執行紀錄輸出至 stderr，掃描表格輸出至 stdout；CLI 本身不會建立紀錄檔。

### 定期執行

同一台裝置請選用 systemd 或 cron 其中一種方式，避免重複執行。以下範例需要實際執行設定才會建立排程；單純安裝 Python 套件不會啟用排程。

#### systemd 計時器

專案提供的 [service 與 timer](deploy/systemd/) 使用 `/opt/xiaomi-thermometer-clock-sync` 路徑，以 `pi` 使用者執行，並從 `/etc/default/xiaomi-thermometer-clock-sync` 載入裝置位址。請依主機修改使用者與路徑，並確保該使用者能透過 BlueZ 操作藍牙。

```bash
sudo git clone https://github.com/echoapus/xiaomi-thermometer-clock-sync.git /opt/xiaomi-thermometer-clock-sync
cd /opt/xiaomi-thermometer-clock-sync
sudo python3 -m venv .venv
sudo .venv/bin/pip install .

sudo cp deploy/systemd/xiaomi-thermometer-clock-sync.default /etc/default/xiaomi-thermometer-clock-sync
sudoedit /etc/default/xiaomi-thermometer-clock-sync

sudo cp deploy/systemd/xiaomi-thermometer-clock-sync.service deploy/systemd/xiaomi-thermometer-clock-sync.timer /etc/systemd/system/
sudoedit /etc/systemd/system/xiaomi-thermometer-clock-sync.service
sudo systemctl daemon-reload

# 先手動測試服務，再啟用計時器
sudo systemctl start xiaomi-thermometer-clock-sync.service
sudo journalctl -u xiaomi-thermometer-clock-sync.service -n 100 --no-pager
sudo systemctl enable --now xiaomi-thermometer-clock-sync.timer
systemctl list-timers xiaomi-thermometer-clock-sync.timer
```

在環境設定檔填入實際位址：

```ini
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF,11:22:33:44:55:66
```

計時器預設依系統時區於每天 03:00 執行，另加最多 5 分鐘的隨機延遲。`Persistent=true` 可在計時器重新啟用時補執行錯過的日曆排程。服務整體逾時為 5 分鐘；若有多台裝置需要較長時間，可調整 `TimeoutStartSec`。

若要指定裝置時區，在 service 的 `ExecStart` 指令末尾加入 `--tz Asia/Taipei`，再執行 `sudo systemctl daemon-reload`。若要調整頻率，修改 timer 的 `OnCalendar`、重新載入 systemd，再重新啟動 timer。停用定期同步：

```bash
sudo systemctl disable --now xiaomi-thermometer-clock-sync.timer
```

#### cron 排程

執行 `crontab -e`，若專案位於 `$HOME/xiaomi-thermometer-clock-sync`，可加入：

```cron
XIAOMI_CLOCK_MAC=AA:BB:CC:DD:EE:FF
0 3 * * * $HOME/xiaomi-thermometer-clock-sync/.venv/bin/xiaomi-thermometer-clock-sync >> $HOME/xiaomi-thermometer-clock-sync/sync.log 2>&1
```

相同範例位於 [deploy/cron/crontab.example](deploy/cron/crontab.example)。請依實際安裝位置修改路徑。若要每小時執行，將 `0 3 * * *` 改成 `0 * * * *`；需要指定時區時，在輸出重新導向之前加入 `--tz Asia/Taipei`。刪除該 crontab 項目即可停用。

範例會將兩個輸出串流追加至 `sync.log`；長期部署請安排紀錄檔輪替。若執行時間可能重疊，可使用 `flock` 搭配排程使用者可寫入的鎖定檔，例如：

```cron
0 * * * * /usr/bin/flock -n /tmp/xiaomi-clock-sync.lock $HOME/xiaomi-thermometer-clock-sync/.venv/bin/xiaomi-thermometer-clock-sync >> $HOME/xiaomi-thermometer-clock-sync/sync.log 2>&1
```

此項目前仍需保留 `XIAOMI_CLOCK_MAC` 設定。請確認主機有 `/usr/bin/flock`；`-n` 會在前一次工作尚未完成時跳過本次執行，而非等待。

### 疑難排解

| 狀況 | 檢查方式 |
|---|---|
| 完全掃描不到裝置 | 執行 `rfkill list bluetooth` 與 `bluetoothctl show`。若被封鎖，執行 `sudo rfkill unblock bluetooth`，再執行 `bluetoothctl power on`。 |
| 找不到指定 MAC | 重新掃描並核對位址、縮短距離，以及檢查裝置電量。 |
| 連線緩慢或失敗 | 連線可能需要數秒；檢查訊號與電量後重新執行。 |
| 低電量警告 | 程式在電量低於 20% 時警告，請視情況更換電池。 |
| 無法讀取感測器 | 感測器使用通知而非一般讀取；等待逾時會記錄警告，但時鐘仍可能同步成功。 |
| 同步後時間不正確 | 使用 `timedatectl status` 檢查主機時間，並確認 UTC 偏移與前述非整點時區限制。 |
| 無法辨識 IANA 時區 | 檢查名稱拼字，以及系統是否已安裝時區資料庫 `tzdata`。 |
| 特徵值或認證錯誤 | 核對型號與韌體；本工具未實作小米認證流程。 |
| 手動成功、排程失敗 | 檢查 service／cron 使用者、執行檔絕對路徑、MAC 設定、藍牙存取權限與執行紀錄。 |

需要診斷時，以 `--read-only -v` 重新執行。回報問題請附上指令、套件版本、主機環境、型號／韌體與相關錯誤紀錄。詳細紀錄可能包含裝置 MAC 與序號，分享前請先遮蔽。

### BLE 協定

| 特徵值 UUID | 存取方式 | 資料格式 |
|---|---|---|
| `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6` | 時間：讀取／寫入 | 小端序 `uint32` Unix 時間戳記，加上 `int8` UTC 偏移小時 |
| `ebe0ccc4-7a0a-4b0c-8a1a-6ff2997da3a6` | 電量：讀取 | `uint8` 百分比 |
| `ebe0ccc1-7a0a-4b0c-8a1a-6ff2997da3a6` | 感測器：通知 | 小端序 `int16` 溫度，單位為 0.01°C，加上 `uint8` 濕度百分比 |
| `ebe0ccbe-7a0a-4b0c-8a1a-6ff2997da3a6` | 顯示單位：讀取／寫入 | `0xff` 為攝氏、`0x01` 為華氏；本 CLI 只讀取此值 |

協定補充資料與編解碼函式請參閱 [docs/protocol.md](docs/protocol.md) 及 [protocol.py](xiaomi_thermometer_clock_sync/protocol.py)。

### 開發與目錄結構

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
```

現有單元測試涵蓋命令列參數、時區處理與協定編解碼，不需要藍牙硬體，也不涵蓋實際 BLE 連線流程。CI 在 Python 3.10–3.13 上執行 lint、格式檢查與測試。

```text
.github/workflows/ci.yml       CI 工作流程
xiaomi_thermometer_clock_sync/
  __init__.py                 套件版本
  __main__.py                 python -m 入口
  cli.py                      參數、紀錄與多裝置流程
  device.py                   BLE 掃描、讀寫與驗證
  protocol.py                 UUID、二進位格式與時區函式
tests/                        不需裝置的單元測試
docs/protocol.md              BLE 協定參考
deploy/systemd/               服務、計時器與環境設定範例
deploy/cron/                  Crontab 範例
pyproject.toml                依賴、套件資訊與工具設定
```

依賴統一宣告於 `pyproject.toml`。請使用安裝後的 CLI 或模組入口；舊版 `sync_time.py` 已移除。虛擬環境、套件安裝資訊、快取、執行紀錄與本機 `.env` 設定檔不納入 Git；`.env.example` 可正常追蹤。

### 授權

採用 [MIT 授權](LICENSE)。本專案與小米公司無隸屬關係。
