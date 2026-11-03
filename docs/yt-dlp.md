# yt-dlp 備援（外部子行程）

本專案為 **GPL-3.0-or-later**。yt-dlp 為 **Unlicense**（公有領域奉獻）。**不得** `import yt_dlp` 或 vendor 其 extractor 原始碼進 `udl` 行程。

整合方式：呼叫獨立可執行檔（PATH／使用者路徑／managed app-data binary／`python -m yt_dlp`），經 CLI／JSON 溝通（mere aggregation）。詳見根目錄 `NOTICE`。

概念靈感來自 [OpenSelena/omniget](https://github.com/OpenSelena/omniget)（GPL-3.0）的 managed yt-dlp 備援；**未**複製其原始碼。

## Router 優先級（站點類型路由）

順序：**原生 extractors → gallery-dl → yt-dlp → Direct**。

| 決策 | 說明 |
|------|------|
| 相對「native → yt-dlp → gallery-dl → direct」調整 | 圖站／相簿仍優先 gallery-dl（`-E` 辨識），避免 yt-dlp 搶走更適合 gallery-dl 的網址 |
| yt-dlp 角色 | gallery-dl 不認的影音／串流長尾 |
| Direct | 兩者都不認的純 HTTP 檔案 |

`matches()` 使用 `yt-dlp --print "%(extractor)s" --skip-download`。停用、找不到工具、或不支援的 URL 會落到 Direct（或 unsupported）。

## Managed binary（比照 FFmpeg）

- CLI：`udl ytdlp status`／`install`／`update-check`
- GUI：設定「工具 · yt-dlp」一鍵下載／檢查更新；首次找不到時可提示下載
- 來源：官方 [yt-dlp/yt-dlp releases](https://github.com/yt-dlp/yt-dlp/releases)
  - macOS arm64：`yt-dlp_macos`
  - Windows x64：`yt-dlp.exe`
- 安裝前／後以同 release 的 **SHA2-256SUMS** 驗證 SHA-256；不符則拒跑
- 亦可使用系統 PATH 或設定／`UDL_YTDLP` 自訂路徑

## CLI

```bash
udl ytdlp status --json
udl ytdlp install --json
udl ytdlp update-check --json
udl ytdlp update-check --install --json

udl resolve 'https://…'
udl get 'https://…' -o ./out --json \
  --ytdlp-tool /path/to/yt-dlp \
  --ytdlp-cookies ./cookies.txt \
  --ytdlp-format 'bv*+ba/b' \
  --ytdlp-audio-only --ytdlp-audio-format m4a

# 停用備援
udl get URL -o ./out --no-ytdlp
```

環境變數：`UDL_YTDLP`、`UDL_YTDLP_DIR`、`UDL_YTDLP_ENABLED`、`UDL_YTDLP_COOKIES`、`UDL_YTDLP_FORMAT`。

## 安全

- **僅**在使用者明確設定 cookies 檔時傳 `--cookies`；**永不**從其他站點 OS keychain 帶入憑證
- `--no-allow-unplayable-formats`：不做付費牆／DRM 繞過
- **不**傳 `--impersonate`（Cloudflare／browser impersonation 預設關）
- 不做 SmartProxy／配額／區域繞過

## 進度／metadata

- resolve：`-J --skip-download --flat-playlist` → 統一 `item_metadata`／佇列／去重／命名
- download：`--progress-template`＋`--print after_move:…` JSON → 統一 JSON-lines 事件；後處理鏈可用

## 模組位置

| 路徑 | 說明 |
|------|------|
| `udl.extractors.external.ytdlp` | 子行程 extractor |
| `udl.postprocess.ytdlp_provision` | 官方 binary 下載／校驗／更新檢查 |
