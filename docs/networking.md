# 網路設定（v0.5）

全域基礎設施：限速、代理、自訂 User-Agent、可選 aria2 RPC。  
適用於所有站點的 API 請求與檔案下載（含外部 MEGA 工具可傳遞的旗標）。

## 設定項目

| 項目 | 預設 | 說明 |
|------|------|------|
| 全域限速 | 關閉 | 整個 `udl` 行程共用的下載速度上限 |
| 單任務限速 | 關閉 | 可選；與全域同時啟用時取較嚴者 |
| 代理模式 | `none` | `none` / `system` / `custom` |
| 自訂代理 | — | HTTP／HTTPS／SOCKS5；帳密可選 |
| 代理密碼 | OS keychain | 經 `udl.secrets`（`keyring`）存放，不寫入 settings.json |
| User-Agent | 內建 Chrome UA | 共用引擎與各 extractor session |
| aria2 RPC | 關閉 | 啟用後檔案下載交給 aria2；連不上則回退內建引擎 |

## 代理模式

- **none**：不使用代理；忽略環境變數中的 `HTTP(S)_PROXY`
- **system**：尊重行程環境的系統／環境代理（`requests` `trust_env=True`）
- **custom**：使用設定的 URL（例如 `http://127.0.0.1:7890`、`socks5://127.0.0.1:1080`）  
  使用者名稱可寫在設定；密碼建議：

```bash
uv run udl secrets set proxy.custom 'your-password'
```

## 限速

人類可讀單位（大小寫不拘）：`512K`、`1M`、`1.5M`，或純位元組數字。  
`0` / `off` / 空白 = 不限速。

- 內建引擎：token-bucket（全域 + 可選 per-job）
- **megatools**：`--limit-speed`（KiB/s）與 `--proxy`
- **MEGAcmd `mega-get`**：透過 `HTTP(S)_PROXY`／`ALL_PROXY` 環境變數傳代理；CLI 本身無穩定的限速旗標（見下方限制）

## aria2 JSON-RPC（選用）

啟用條件：設定開啟，且 RPC 可連線（`aria2.getVersion` 成功）。

- URL 預設：`http://127.0.0.1:6800/jsonrpc`
- Secret：CLI `--aria2-secret`、設定欄位，或 keychain 鍵 `aria2.rpc_secret`
- 會把 `User-Agent`、Referer、Cookie 等 headers 傳給 `aria2.addUri`
- 輪詢 `tellStatus` → 標準 `progress`／`done` JSON-lines
- 取消：`forceRemove`；暫停：`aria2.pause`（GUI 目前仍以結束 sidecar 行程為主）
- **連不上或未啟用 → 自動使用內建 HTTP 引擎**，不中斷任務

建議本機 aria2 範例：

```bash
aria2c --enable-rpc --rpc-listen-all=false --rpc-secret=TOKEN
```

## CLI

```bash
# 限速 + 自訂 UA
uv run udl get URL -o ./out --speed-limit 1M --user-agent "MyUA/1.0"

# 系統代理
uv run udl get URL -o ./out --proxy-mode system

# 自訂 SOCKS5（密碼已進 keychain）
uv run udl secrets set proxy.custom 'secret'
uv run udl get URL -o ./out --proxy-mode custom --proxy socks5://127.0.0.1:1080 --proxy-user alice

# aria2
uv run udl get URL -o ./out --aria2 --aria2-rpc http://127.0.0.1:6800/jsonrpc --aria2-secret TOKEN
```

`resolve`／`probe` 同樣接受 `--proxy-mode`／`--proxy`／`--user-agent`，以便 API 請求走相同網路路徑。

## GUI

設定頁「網路」區塊（zh-TW）：全域／單任務限速、代理三模式、自訂 URL／帳密、User-Agent、aria2 開關與 RPC。  
密碼欄僅在儲存時寫入 keychain；不會留在 `settings.json`。

## 模組對照

| 模組 | 職責 |
|------|------|
| `udl.network` | 行程內設定、代理 URL、套用到 `requests.Session` |
| `udl.secrets` | `keyring` 讀寫（`proxy.custom`、`aria2.rpc_secret`、日後登入） |
| `udl.download.rate` | token-bucket 限速 |
| `udl.download.http` | 共用引擎（限速／代理／UA／aria2 回退） |
| `udl.download.aria2` | JSON-RPC 後端 |

## 已知限制

- 單一 GUI 任務通常是一個 `udl` sidecar 行程；「全域限速」作用於該行程內。多任務並發時，GUI 需把相同的 `--speed-limit` 傳給每個 sidecar（目前如此），整體吞吐約為「單行程上限 × 並發數」的近似；若需要跨行程硬上限，需日後由 GUI 侧協調。
- MEGAcmd `mega-get` 無對等 `--limit-speed`；僅能傳代理環境變數。
- SOCKS 需 `PySocks`（已列入核心依賴）。
- keyring 在無圖形／無密鑰環的 CI 或無頭環境會改寫入使用者資料目錄下的 `secrets.json`（權限 0600）並在 stderr 提示；正式桌面環境優先用 OS keychain。CLI 仍可用 `--proxy-password` 一次性傳入。
