# MEGA

內建 Python 客戶端（預設），協定／加解密／分段邏輯參考 [tonikelope/megabasterd](https://github.com/tonikelope/megabasterd)（GPL-3.0）。  
可選後端：本機 **megatools**／**MEGAcmd** 子行程（見設定 `megaBackend=external`）。

## 功能

| 項目 | 說明 |
|------|------|
| 公開連結 | `/file/`、`/folder/`，舊版 `#!`／`#F!`，資料夾子路徑 |
| 分段下載 | 多連線、chunk 對齊 AES-CTR 解密、重啟後續傳（`.udl.segments.json`） |
| 完整性 | 下載結束 CBC-MAC 驗證（可用 `--mega-no-verify` 關閉） |
| 網路 | 共用全域／任務限速、代理、UA（`udl.network`） |
| 帳號 | `udl mega login` → OS keychain `mega.session`；雲端硬碟瀏覽 |
| 串流預覽 | `udl mega stream URL --wait`：僅 localhost＋隨機 token 即時解密 |
| 歷史／去重 | `item_id`＝node handle；與 NFO／封面／命名模板共用基礎設施 |

## CLI

```bash
# 內建下載（預設）
uv run udl get 'https://mega.nz/file/HANDLE#KEY' -o ./out --json

# 資料夾勾選（node handle 逗號分隔）
uv run udl get 'https://mega.nz/folder/H#K' -o ./out --mega-select HANDLE1,HANDLE2

# 外部工具後端
uv run udl get URL -o ./out --mega-backend external --mega-tool /path/to/megatools

# 帳號
uv run udl mega login --email you@example.com --password '…' --json
uv run udl mega whoami --json
uv run udl mega logout --json
uv run udl mega browse                 # 雲端硬碟（需登入）
uv run udl mega browse 'https://mega.nz/folder/…#…' --json

# 本機串流預覽（Ctrl+C 結束）
uv run udl mega stream 'https://mega.nz/file/…#…' --json --wait
```

環境變數：`UDL_MEGA_BACKEND=builtin|external`、`UDL_MEGA_TOOL=…`。

## 配額（509）

遇到 HTTP 509／over-quota 時會顯示清楚錯誤與建議等待時間。  
**不實作** SmartProxy、IP 輪替、或任何配額繞過。

## GUI

- 「MEGA」頁：連結解析、樹狀勾選（含大小）、單檔 localhost 串流預覽
- 「帳號」頁：MEGA email／password 登入
- 「設定 → 網路／MEGA」：後端切換、外部工具路徑

## 模組

| 路徑 | 職責 |
|------|------|
| `udl.extractors.mega` | 內建 extractor／API／crypto／download／stream／auth |
| `udl.download.segment` | 通用分段規劃與續傳狀態（供日後 v0.9-A 共用） |
| `udl.extractors.external.mega` | megatools／MEGAcmd 子行程備援 |
