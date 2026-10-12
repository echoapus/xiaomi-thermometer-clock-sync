# Xiaomi 電子溫濕度計 Pro 連線實作

## 需求

需要模擬小米APP 連線，並且執行時間同步

- 產品頁：https://www.mi.com/tw/product/xiaomi-temperature-and-humidity-monitor-clock/

## 目標

1. 透過 BLE 連線至溫濕度計，模擬米家 APP 的連線流程（含必要的認證）。
2. 將裝置時間同步為主機（Raspberry Pi）目前時間，並設定時區。

## 待確認事項

| # | 項目 | 說明 | 狀態 |
|---|------|------|------|
| 1 | 裝置型號 | 使用者告知為 **LYWSD02MMC**；但裝置經藍牙回報為 model `LYWSD02`、製造商 `miaomiaoce.com`、硬體 `F2_LN`、MiBeacon product_id `0x045b`，且廣播未加密。這解釋了為何不需認證。 | 已確認 |
| 2 | 加密 / 綁定 | 裝置已綁定米家帳號（使用者確認），但 token 擷取工具在 cn/de/us/ru/tw/sg/in/i2 皆未列出此裝置（僅列出掃地機）。**實測：韌體 1.1.2_0097 可免認證直接讀寫時間特徵值，不需 token。** | 已解決 |
| 3 | 同步頻率 | 一次性同步，或定期同步（例如在 Pi 上用 cron / systemd timer）？ | 待確認 |
| 4 | 時區 | 預設使用系統時區（Pi 為 Asia/Taipei, UTC+8），可用 `--tz` 指定。 | 已實作 |
| 5 | 額外功能 | 電量（`ebe0ccc4…`，1 byte 百分比）、溫度與濕度（`ebe0ccc1…` notify，`<int16 溫度/100><uint8 濕度>`）皆已實作；`--read-only` 可只讀不寫。 | 已實作 |
| 6 | 交付形式 | Python 套件 `lywsd02-sync`（CLI），附 systemd timer / cron 範例；支援多台裝置、`--scan`。 | 已實作 |
| 7 | 裝置數量與 MAC | 目前一台：`AA:BB:CC:DD:EE:FF`（廣播名稱 `LYWSD02`，public address）。 | 已確認 |

## 實測結果（2026-10-12）

- 裝置：`AA:BB:CC:DD:EE:FF`，廣播名稱 `LYWSD02`，韌體 `1.1.2_0097`。
- 時間特徵值：服務 `ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6`，特徵值 `ebe0ccb7-7a0a-4b0c-8a1a-6ff2997da3a6`。
  格式：`<uint32 little-endian Unix 時間><int8 UTC 偏移小時>`，共 5 bytes。
- 未經小米認證即可讀取與寫入。寫入前裝置慢 58 秒，寫入後讀回誤差 0 秒，時區 +8。

## 驗收標準

- [x] 腳本可掃描並連線至指定 MAC 的裝置。
- [x] ~~若型號需要認證，可使用提供的 token 完成登入。~~ 此韌體不需認證。
- [x] 執行後裝置時間與主機時間誤差 ≤ 1 分鐘，時區正確（腳本寫入後會讀回驗證）。
- [x] 失敗時輸出明確錯誤訊息並回傳非零 exit code（已測試找不到裝置的情況）。

## 執行環境

- 主機：Raspberry Pi（Linux 6.18, aarch64），系統時間已透過 NTP 同步。
- 藍牙控制器：`<adapter MAC>`（BlueZ）。原本被 rfkill soft block，已執行 `rfkill unblock bluetooth` 並開啟電源。
- Python 3.13.5；`bleak` 安裝於專案 venv `.venv/`。

## 使用方式

請見 [README](../README.md)。

## 下一步

1. 決定同步頻率（第 3 項）；cron 與 systemd timer 範例已放在 `contrib/`。
2. 2026-10-12 實測電量僅 16%，建議盡快更換電池。
3. ~~確認重開機後藍牙不會再被 rfkill 封鎖。~~ 已確認：systemd-rfkill 已儲存為解除封鎖狀態。
