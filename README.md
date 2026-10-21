# 整合下載器（unified-downloader）v0.7.0

桌面應用：貼上網址 → 辨識站點 → 用統一 Python 核心下載。  
授權：**GPL-3.0-or-later**（見 `LICENSE`、`NOTICE`）。

## 架構

```
unified-downloader/
├─ core/udl/              # Python 核心套件
│  ├─ router.py           # URL → extractor 註冊表（lazy stubs）
│  ├─ extractors/         # 每站 `_VALID_URL`；lazy_extractors 建置產生
│  ├─ download/           # 共用 HTTP／分段／片段基底／aria2／external(aria2c|ffmpeg)
│  ├─ network.py          # 代理／UA／限速設定
│  ├─ secrets.py          # OS keychain（keyring）
│  ├─ postprocess/        # FFmpeg 合併／佈建；NFO／封面（v0.5-D）
│  ├─ metadata.py         # 統一 item_metadata（全站）
│  ├─ store/              # SQLite 歷史／佇列／去重（migrations）
│  ├─ events.py           # JSON-lines 事件（含 metadata / duplicate / postprocess）
│  └─ cli.py              # resolve / probe / get / history / queue / iwara / secrets / …
├─ app/                   # Tauri v2 + React/TS GUI（sidecar 叫 udl）
├─ docs/                  # 事件、metadata、networking、scheduler、page-download、checksum-hook…
├─ packaging/             # PyInstaller spec
└─ scripts/               # sidecar 建置；make_lazy_extractors.py
```

GUI 透過 `tauri-plugin-shell` 啟動 `udl` sidecar，解析 stdout 的 JSON-lines（見 `docs/events.md`）。  
全域歷史 DB 在應用資料目錄（與 FFmpeg 佈建同根），由核心擁有。

**不做：** gallery-dl **import／vendor**（GPL-2.0-only；僅允許獨立子行程）、Pixeldrain、付費牆／配額／VIP／區域繞過、簽章、DRM／加密串流繞過、系統級下載攔截。

## 目前支援

| 站點 | 狀態 | 來源 |
|------|------|------|
| Gofile | ✅ | ltsdw/gofile-downloader（訪客 token，公開連結） |
| Bunkr | ✅ | Lysagxra/BunkrDownloader（相簿／單檔） |
| Iwara | ✅ v0.6-B | Moeary/IwaraTool（瀏覽／訂閱／登入／私有片；見 `docs/iwara-browse.md`） |
| Bilibili | ✅ v0.7-B | ScottSloan/Bili23-Downloader（解析／登入／帳號清單／CDN；見 `docs/bilibili.md`） |
| MEGA | ✅ 內建＋外部備援 | 內建協定客戶端；可選 megatools／MEGAcmd 子行程 |
| gallery-dl | ✅ 外部子行程 | mikf/gallery-dl（GPL-2.0-only；見 `docs/gallery-dl.md`） |
| Direct | ✅ | 通用直連（router 最後） |

上游 commit 與授權細節見 `NOTICE`。介面約定見 `docs/extractor-interface.md`。路線圖見 `ROADMAP.md`。

### Iwara 備註

- URL：`/video/<id>`、`/playlist/<id>`、`/profile/<user>`（含 `/videos`）
- 畫質：`--quality Source|540|360`（預設 Source，自動往下回退）
- 公開內容匿名可抓；帳號：`udl iwara login|logout|whoami`（憑證進 OS keychain）。`--token` 可覆寫 session
- **不**預設開 Cloudflare 繞過。需要時：`udl get … --cloudflare-bypass`（需 `uv sync --extra iwara` 或安裝 cloudscraper），GUI 有對應核取方塊且預設關閉
- `X-Version` salt 在 `core/udl/extractors/iwara_xversion.py`；全部失敗會拋清楚錯誤
- **v0.6：** `udl iwara search|tag|…|sync|login|import-following`；GUI「Iwara／首頁／訂閱」＋設定帳號區。詳見 `docs/iwara-browse.md`

### Bilibili 備註（v0.7-B）

- 類型：BV／多 P、bangumi、cheese、互動節點、au／am、UP 空間、公開收藏夾、每周必看、合集／系列；登入後：稍後再看／歷史／追番／訂閱合集／自己的收藏夾。
- 登入：`udl bili login`（QR／Cookie；SMS 經使用者完成 GeeTest，GUI 用 Tauri webview）；session 進 OS keychain（`bilibili.session`）。
- 畫質／`--codec avc|hevc|av1`／`--audio-quality`；登入後可取得帳號有權之 8K／4K／HDR 等，**不**繞過 VIP／付費／區域。
- CDN／Host：`--cdn-hosts`／`--prefer-cdn`／`--custom-host`（與全域 UA／代理共用）。
- 彈幕優先 protobuf `seg.so` 分段（XML 回退）；`--expand-all` 一鍵展開清單；`--danmaku`／`--subtitle`／章節／`--container mp4|mkv`；詳見 `docs/bilibili.md`。
- GUI「Bilibili」解析頁＋「帳號」頁（QR／狀態／登出）。
- 需要 FFmpeg（PATH、`UDL_FFMPEG`、`--ffmpeg`，或 GUI 首次下載）。

### MEGA 備註（v0.4）

- 公開連結：`mega.nz/file/…`、`/folder/…`、舊格式 `#!`／`#F!`（亦接受 `mega.co.nz`／`mega.io`）。
- **工具選擇：** 優先 `megatools`（`megatools dl`／`megadl`）— 單一無頭二進位、進度可解析、macOS／Windows／Linux 皆可；找不到再試 MEGAcmd 的 `mega-get`（可能需要背景 `mega-cmd-server`）。
- 授權：megatools 為 GPL-2.0（含 OpenSSL 例外）→ **只以子行程呼叫，絕不 import／連結**；MEGAcmd 多為 BSD-2。詳見 `NOTICE`。
- 缺少工具時發出 `mega_tool_missing` 與安裝指引（brew／scoop／apt／[mega.io/cmd](https://mega.io/cmd)）。可用 `--mega-tool` 或 `UDL_MEGA_TOOL` 指定路徑。
- 取消會結束子行程（行程組 SIGTERM→SIGKILL）。
- **不**實作 SmartProxy／Quota Recovery／換 IP 等配額繞過；遇到 509 只回報清楚錯誤。

### GUI 設定與佇列（v0.8 殼層）

- **統一佇列**：跨站進行中／排隊／暫停／失敗；每任務進度、速度、ETA；暫停／繼續／取消／重試、排序、並發、批次操作；失敗可「開啟修復」。
- **輸入**：多網址貼上、拖放連結到視窗、可選剪貼簿監看（預設關）；偵測時顯示站點 badge。
- **系統**：完成／失敗通知（可關）、系統列圖示狀態、關閉視窗最小化到托盤（可關）；主題跟隨系統（亦可強制淺／深色）。
- **設定頁（單頁分區）**：下載目錄、命名／規則、NFO／封面、網路、Iwara／Bilibili 帳號相關、FFmpeg、MEGA 工具路徑；可匯入／匯出 JSON（不含密鑰）。寫入應用資料目錄 `settings.json`（並同步 `udl.config.json`；代理密碼進 OS keychain）。
- **自動更新**：接 GitHub Releases；**未設定簽章公鑰時停用**（見 `docs/updater.md`）。未簽章安裝包仍可手動安裝。
- **歷史頁**：搜尋、站點／狀態／日期／標籤篩選、排序、刪除條目（可選刪檔）；有本機封面時顯示縮圖。
- **修復頁**：掃描缺檔／`.part`／大小／hash／checksum／orphan，可重新排隊、重新連結、清殘檔、刪過期紀錄。
- **頁面擷取（v0.9-D）**：貼網頁 URL → 列出直連／站點候選 → 勾選入佇列；CLI `udl page`（見 `docs/page-download.md`）。
- **校驗／掃毒（v0.9-D）**：下載後 MD5／SHA-1／SHA-256；可選 Defender／ClamAV 指令（預設關）；見 `docs/checksum-hook.md`。
- **FFmpeg 首次佈建**：偵測不到時可下載目前 OS／arch 靜態建置並驗證 checksum（見 `NOTICE`）。CLI：`udl ffmpeg status` / `udl ffmpeg install`。
- **網路**：見 `docs/networking.md`（全域限速、代理三模式、可選 aria2 RPC、自訂 UA、v0.10 RequestHandler）。
- **排程／配額**：見 `docs/scheduler.md`（具名佇列、時段、完成後動作、流量配額、時段限速）。
- **MEGA**：內建 Python 客戶端（分段＋CBC-MAC；見 `docs/mega.md`）；可選 megatools／MEGAcmd 外部後端。

### 截圖（佔位）

> 以下為 GUI 截圖佔位，釋出前請替換成實際畫面（淺色／深色各一較佳）。
>
> - `docs/screenshots/queue-dashboard.png` — 統一佇列（進度／ETA／批次操作）
> - `docs/screenshots/settings.png` — 整合設定頁（分區＋匯入匯出）
> - `docs/screenshots/theme-dark.png` — 跟隨系統深色主題

### 命名／規則／修復 CLI（v0.5）

```bash
uv run udl get "URL" -o ./out --name-template '{site}/{author}/{title}.{ext}'
uv run udl naming preview --template '{site}/{title}.{ext}' --metadata '{"site":"iwara","title":"demo"}'
uv run udl rules test "URL" --json
uv run udl repair scan --json
uv run udl repair fix requeue --job-id <id> --json
```

詳見 `docs/naming-rules.md`。

### 歷史／去重／NFO／封面 CLI（v0.5）

```bash
uv run udl history list --json --site iwara --query "keyword"
uv run udl history delete <job-id> --delete-files
uv run udl queue list --unfinished --json
uv run udl queue schedule status --json
uv run udl queue schedule set night --start 22:00 --end 06:00 --days weekdays --after notify
uv run udl get "URL" -o ./out --json --on-duplicate skip
uv run udl get "URL" -o ./out --json --write-nfo --write-cover --embed-cover
```

NFO／封面細節見 `docs/metadata.md`；事件見 `docs/events.md`。

## 安裝未簽章建置（Release）

GitHub Actions `release.yml` 產出**未簽章**安裝包：

| 平台 | 產物 | sidecar 檔名 |
|------|------|----------------|
| macOS arm64 | `unified-downloader-macos-arm64.dmg`（另可有 `.app.zip`） | `udl-aarch64-apple-darwin` |
| Windows x64 | `unified-downloader-windows-x64-setup.exe`（NSIS；另可有 `.msi`） | `udl-x86_64-pc-windows-msvc.exe` |

Tauri `externalBin` 使用 `binaries/udl`，建置腳本必須把 PyInstaller 產物命名為 `udl-<target-triple>[.exe]`。

### macOS（Gatekeeper）

1. 從 Release 下載 `.dmg`，拖到「應用程式」。
2. 若出現「無法驗證開發者」：對 App **按右鍵 → 打開**（比雙擊更容易通過）；或系統設定 → 隱私權與安全性 → 仍要打開；或  
   `xattr -dr com.apple.quarantine "/Applications/整合下載器.app"`
3. 本專案**不做** Apple 公證／簽章（見 ROADMAP）。

### Windows（SmartScreen）

1. 執行 `*-setup.exe`。若 SmartScreen 提示「Windows 已保護你的電腦」：選「更多資訊」→「仍要執行」。
2. 本專案**不做** Authenticode 簽章。企業環境可能需允許未知發行者或改用本機建置。

## 開發環境

### 需求

- Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20、Rust（stable）
- FFmpeg（Bilibili DASH 合併）
- megatools 或 MEGAcmd（MEGA；建議 megatools）
- 桌面開發另需 [Tauri 前置](https://tauri.app/start/prerequisites/)（Linux：webkit2gtk 等）

### Python 核心

```bash
cd core
uv sync --extra dev
# 可選：Iwara Cloudflare 繞過
# uv sync --extra iwara
uv run udl sites
uv run udl resolve "https://www.iwara.tv/video/<id>"
uv run udl get "https://www.iwara.tv/video/<id>" -o ./out --json --quality Source
uv run udl resolve "https://www.bilibili.com/video/BV1xx411c7mD"
uv run udl probe "https://www.bilibili.com/video/BV1xx411c7mD"
uv run udl get "https://www.bilibili.com/video/BV1xx411c7mD" -o ./out --json
uv run udl resolve "https://mega.nz/file/<id>#<key>"
uv run udl get "https://mega.nz/file/<id>#<key>" -o ./out --json
uv run udl ffmpeg status --json
# uv run udl ffmpeg install   # macOS arm64 / Windows x64 靜態建置
uv run udl page "https://example.com/" --json --ext mp4,zip
uv run udl get "URL" -o ./out --json --sha256 <hex>
uv run udl get "URL" -o ./out --json --post-cmd-preset clamav
```

### GUI

```bash
# 準備開發用 sidecar stub（轉發到 uv run udl）
chmod +x scripts/dev-sidecar-stub.sh scripts/build-sidecar.sh
triple="$(rustc -vV | sed -n 's/^host: //p')"
mkdir -p app/src-tauri/binaries
cp scripts/dev-sidecar-stub.sh "app/src-tauri/binaries/udl-${triple}"

cd app
npm install
UDL_DEV=1 npm run tauri dev
```

正式 sidecar（PyInstaller onefile）：

```bash
./scripts/build-sidecar.sh
cd app && npm run tauri build
```

### CI 產物

- `.github/workflows/ci.yml`：Python lint + 前端 build
- `.github/workflows/release.yml`：**未簽章** macOS arm64 `.dmg`、Windows x64 NSIS／MSI，並上傳對應 triple 命名的 `udl` sidecar；缺產物時失敗

正式打包請用 `./scripts/build-sidecar.sh <target-triple>`（CI matrix 會傳入），再 `npm run tauri -- build --target <triple>`。

## 注意事項

- 僅下載你有權存取的內容，並遵守各站服務條款。
- Gofile 訪客帳號與 `X-Website-Token` 沿用上游公開連結行為；salt 可能失效。
- Bunkr 網域與簽章 API 常變，需追上游。
- Iwara `X-Version` salt 與 Cloudflare 策略會變；預設不用 cloudscraper。
- Bilibili WBI 簽章與 playurl 行為可能隨站方變更；區域／會員限制會以錯誤事件回報。
- MEGA 傳輸配額由站方決定；本專案不會提供繞過手段。
- 自動化測試仍刻意延後。

## 授權

GPL-3.0-or-later。移植模組保留上游著作權，詳見 `NOTICE`。
