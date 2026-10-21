# 網路設定（v0.5＋v0.9-A＋v0.10 RequestHandler）

全域基礎設施：限速、代理、自訂 User-Agent、可選 aria2 RPC、多連線分段下載。  
適用於所有站點的 API 請求與檔案下載（含外部 MEGA 工具可傳遞的旗標）。

v0.10 起 `udl.network` 採 yt-dlp 風格的 **RequestHandler** 抽象（Unlicense 參考，見根目錄 `NOTICE`）：同一介面包 **urllib**／**requests**／可選 **curl_cffi**／可選 **websockets**；proxy、UA、限速統一由此層套用。既有 `apply_to_session` 仍可用於 `requests.Session`。

## 設定項目

| 項目 | 預設 | 說明 |
|------|------|------|
| 全域限速 | 關閉 | 整個 `udl` 行程共用的下載速度上限 |
| 單任務限速 | 關閉 | 可選；與全域同時啟用時取較嚴者 |
| 代理模式 | `none` | `none` / `system` / `custom` |
| 自訂代理 | — | HTTP／HTTPS／SOCKS5；帳密可選 |
| 代理密碼 | OS keychain | 經 `udl.secrets`（`keyring`）存放，不寫入 settings.json |
| User-Agent | 內建 Chrome UA | 共用引擎與各 extractor session |
| RequestHandler | `auto` | `auto`／`requests`／`urllib`／`curl_cffi` |
| curl_cffi 指紋模擬 | **關閉** | 選用；需安裝 `curl_cffi`；政策同 Cloudflare 繞過 |
| aria2 RPC | 關閉 | 啟用後檔案下載交給 aria2；連不上則回退內建引擎 |
| 多連線分段 | 開啟 | IDM 風格動態分段；無 Range 時單連線 |
| 每檔連線數 | 8 | 上限 32；並受 per-host 上限約束 |
| 站點也用分段 | 關閉 | 純 HTTP 檔案的站點 extractor；Gofile／Bunkr／Iwara／MEGA **永遠排除** |
| 片段後端 | `native` | HLS／DASH／HTTP 分段列表：`native`／`aria2c`／`ffmpeg`（見 `docs/fragment-download.md`） |
| 並行片段數 | 1 | native 後端同時下載的片段數（上限 32） |
| Bilibili CDN 主機 | 空 | 逗號分隔偏好 CDN hostname；改寫 playurl netloc |
| 優先偏好 CDN | 關閉 | 開啟後置換節點排在原始 URL 前 |
| 自訂 Host | 空 | 媒體下載可選 Host 標頭（通常留空） |

## RequestHandler（v0.10）

```python
from udl.network import request, send, available_handlers, build_director

# 行程設定（proxy／UA／限速）已由 configure_network 套用
resp = send(request("https://example.com/"))
print(resp.status, resp.read(64))
print(available_handlers())  # urllib / requests / curl_cffi / websockets
```

| 後端 | 依賴 | 說明 |
|------|------|------|
| `UrllibRH` | stdlib | 永遠可用的後備 |
| `RequestsRH` | `requests`（核心依賴） | 預設 HTTP 後端 |
| `CurlCffiRH` | 選用 `udl[curl-cffi]` | 僅在 `--impersonate` 或強制 `curl_cffi` 時優先 |
| `WebsocketsRH` | 選用 `udl[websockets]` | `ws://`／`wss://` |

```bash
# 選用指紋模擬（預設關；需 curl_cffi）
uv run udl get URL -o ./out --impersonate
uv run udl get URL -o ./out --impersonate chrome131 --request-handler curl_cffi
```

**不做**：付費牆／VIP／ToS／DRM 繞過；指紋模擬與 Cloudflare 繞過一樣必須使用者明確開啟。

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

- 內建引擎（單連線與分段）：token-bucket（全域 + 可選 per-job）
- **megatools**：`--limit-speed`（KiB/s）與 `--proxy`
- **MEGAcmd `mega-get`**：透過 `HTTP(S)_PROXY`／`ALL_PROXY` 環境變數傳代理；CLI 本身無穩定的限速旗標（見下方限制）

## 多連線分段下載（v0.9-A）

內建 HTTP 引擎在 **支援 HTTP Range（Accept-Ranges / 206）** 時可啟用 IDM 風格動態分段：

1. 依連線數把檔案切成初始區段並並行下載
2. 任一連線完成後，將**最大未完成區段**對半切開，空出的連線接手後半
3. 區段狀態寫在 `.part.udl-seg.json`（與 `.part` 同目錄），重啟後可續傳
4. 續傳前以 ETag／Last-Modified（及大小）驗證；遠端變更則刪除 partial 重下
5. 伺服器不支援 Range → 自動回退單連線（既有 `.part` Range 續傳）

優先順序：

1. aria2 啟用且 RPC 可連 → aria2
2. 否則若允許分段且連線數 > 1 且支援 Range → 分段引擎
3. 否則 → 單連線內建引擎

**誰會用分段**

| 來源 | 預設 |
|------|------|
| `direct` 通用直連 extractor | 跟隨「多連線分段」開關 |
| 其他站點（Gofile／Bunkr／Iwara／MEGA） | **永不**分段（簽章／Cookie／外部工具） |
| 其他站點（例如未來產出純 HTTP 檔的 extractor） | 需另開「站點也用分段」 |

CLI：

```bash
uv run udl get URL -o ./out --connections 8
uv run udl get URL -o ./out --no-segmented
uv run udl get URL -o ./out --segmented-for-sites
```

`progress` 事件可帶 `segments`（各區段起迄／已下載）與 `connections`；GUI 任務列會顯示分段進度條。詳見 `docs/events.md`。

## 片段下載（v0.10）

HLS／DASH／HTTP 分段列表走 `udl.download.fragment`；外部 aria2c／ffmpeg 與 native 同一介面。整檔 aria2 **RPC** 與 aria2c **子行程**不同。詳見 `docs/fragment-download.md`。

```bash
uv run udl get URL -o ./out --external-downloader aria2c
uv run udl get URL -o ./out --concurrent-fragments 4
```

## 通用直連（v0.9-A）

router 在站點 extractor 都未匹配時，落到 `direct`：

- 探測：HEAD，失敗再 Range GET `bytes=0-0`
- 檔名：`Content-Disposition` → URL path → `host.bin`
- 寫入統一 `item_metadata`（`item_id=direct-<sha256前綴>`），走歷史／去重／命名／規則／佇列

```bash
uv run udl resolve 'https://example.com/files/a.zip' --json
uv run udl get 'https://example.com/files/a.zip' -o ./out --json
```

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

# 分段連線數
uv run udl get URL -o ./out --connections 12
```

`resolve`／`probe` 同樣接受 `--proxy-mode`／`--proxy`／`--user-agent`，以便 API 請求走相同網路路徑。

## GUI

設定頁「網路」區塊（zh-TW）：全域／單任務限速、代理三模式、自訂 URL／帳密、User-Agent、**curl_cffi 指紋模擬（預設關）**、RequestHandler 後端、aria2、**每檔連線數／分段開關／站點也用分段**。  
密碼欄僅在儲存時寫入 keychain；不會留在 `settings.json`。  
下載佇列任務詳情在多連線時顯示分段進度條。

## 模組對照

| 模組 | 職責 |
|------|------|
| `udl.network` | 行程設定、RequestHandler 導演、代理／UA、`apply_to_session`、連線數／分段旗標 |
| `udl.network._urllib`／`_requests`／`_curlcffi`／`_websockets` | 各後端 RequestHandler |
| `udl.secrets` | `keyring` 讀寫（`proxy.custom`、`aria2.rpc_secret`、日後登入） |
| `udl.download.rate` | token-bucket 限速（Response.read 亦經此層） |
| `udl.download.http` | 共用引擎（限速／代理／UA／aria2 回退／呼叫分段） |
| `udl.download.segment` | 固定／MEGA 協定 chunk 規劃器（`.udl.segments.json`） |
| `udl.download.segmented` | HTTP IDM 動態分段＋狀態持久化（`.part.udl-seg.json`） |
| `udl.download.aria2` | JSON-RPC 後端 |
| `udl.extractors.direct` | 通用直連 fallback |

## 時段限速與流量配額（v0.9-C）

排程模組可依時段覆寫有效 `--speed-limit`，並以 `throttle()` 累計位元組做每小時／每日配額。  
詳見 [`docs/scheduler.md`](scheduler.md)。

## 已知限制

- 單一 GUI 任務通常是一個 `udl` sidecar 行程；「全域限速」作用於該行程內。多任務並發時，GUI 需把相同的 `--speed-limit` 傳給每個 sidecar（目前如此），整體吞吐約為「單行程上限 × 並發數」的近似。v0.9-C 由 GUI 依排程狀態傳入較嚴的有效限速；硬性跨行程總上限仍屬近似。
- MEGAcmd `mega-get` 無對等 `--limit-speed`；僅能傳代理環境變數。
- SOCKS 需 `PySocks`（已列入核心依賴）。
- keyring 在無圖形／無密鑰環的 CI 或無頭環境會改寫入使用者資料目錄下的 `secrets.json`（權限 0600）並在 stderr 提示；正式桌面環境優先用 OS keychain。CLI 仍可用 `--proxy-password` 一次性傳入。
- 分段引擎不取代 aria2；兩者擇一（aria2 優先）。
- 不做 DRM／加密串流繞過，也不做系統級下載攔截。
