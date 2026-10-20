# 排程與流量配額（v0.9-C）

具名佇列、每日／平日／指定日時段、完成後動作、流量配額與時段限速檔。  
設定與用量寫入核心 SQLite（與歷史／佇列同一資料庫），應用重啟後保留。

## 具名佇列

每個佇列可獨立設定：

| 欄位 | 說明 |
|------|------|
| 名稱 | 唯一；內建 `default` 不可刪除 |
| 啟用 | 關閉後不啟動任務 |
| 最大並發 | 該佇列同時執行的 job 數 |
| 日期 | `daily`／`weekdays`／`weekend`／ISO 星期 `0=一…6=日` |
| 時段 | 本機 `HH:MM`–`HH:MM`；可跨午夜；留空＝全天 |
| 重複 | `repeat` 每逢時段執行；`once` 完成一輪後標記完成 |
| 完成後 | `none`／`notify`／`quit`／`sleep`／`shutdown` |
| 限速檔 | 可選，綁定時段限速 profile |

GUI 下載清單依佇列並發與時段閘控；超出時段或配額暫停時不啟動新任務。

## 完成後動作

佇列內任務皆進入終態（完成／錯誤／取消）時觸發：

- **notify**：應用內橫幅通知
- **quit／sleep／shutdown**：倒數 30 秒對話框（可取消）；確認後執行  
  - macOS：`pmset sleepnow`／`osascript` 關機  
  - Windows：PowerShell Suspend／`shutdown /s`  
  - Linux：`systemctl suspend|poweroff`（best-effort）

## 流量配額

| 項目 | 說明 |
|------|------|
| 每小時 MB | 超過後暫停至下一個整點 |
| 每日 MB | 超過後暫停至次日 00:00 |
| 計量 | 共用引擎 `udl.download.rate.throttle` 累計下載位元組 |

暫停狀態寫入 `traffic_quota.paused_until`；視窗過期後自動恢復，亦可 CLI／GUI 清除暫停。

## 時段限速檔

Profile 含預設限速與多個視窗（日期＋時段＋限速）。  
評估時取「符合視窗」中最嚴者，否則用預設；再與設定頁全域限速取較嚴者，經 `--speed-limit` 傳入各 sidecar。

## CLI

```bash
# 狀態
uv run udl queue schedule status --json
uv run udl queue schedule list --json

# 具名佇列
uv run udl queue schedule set night --start 22:00 --end 06:00 --days weekdays \
  --max-concurrent 3 --after notify --speed-profile daytime
uv run udl queue schedule set night --no-enabled
uv run udl queue schedule reset-once night
uv run udl queue schedule complete-once night
uv run udl queue schedule delete night

# 限速檔
uv run udl queue schedule profile set daytime --default-limit 2M \
  --window 'weekdays:09:00-18:00:512K' --clear-windows
uv run udl queue schedule profile list --json

# 配額
uv run udl queue schedule quota --enable --hour-mb 500 --day-mb 5000
uv run udl queue schedule quota --clear-pause
```

## 模組對照

| 模組 | 職責 |
|------|------|
| `udl.store.schedule` | SQLite CRUD（佇列／profile／配額／用量） |
| `udl.scheduler` | 時段／配額評估、有效限速 |
| `udl.download.rate` | token-bucket＋流量累計 hook |
| `udl.cli_schedule` | `udl queue schedule …` |
| GUI「排程」頁 | 編輯與即時狀態；倒數確認完成後動作 |

## 與其他版本的關係

- v0.8-C 佇列儀表板若已合併：排程頁與儀表板共用同一 SQLite 佇列狀態。
- v0.9-A 分段引擎若已合併：限速／配額仍經 `throttle`；GUI 傳入的有效 `--speed-limit` 不變。
