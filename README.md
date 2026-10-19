# 整合下載器（unified-downloader）v0.5

桌面應用：貼上網址 → 辨識站點 → 用統一 Python 核心下載。  
授權：**GPL-3.0-or-later**（見 `LICENSE`、`NOTICE`）。

## 架構

```
unified-downloader/
├─ core/udl/              # Python 核心套件
│  ├─ router.py           # URL → extractor 註冊表
│  ├─ extractors/         # gofile、bunkr、iwara、bilibili/、external/mega
│  ├─ download/http.py    # 共用 HTTP：續傳、重試、並行、取消
│  ├─ postprocess/        # FFmpeg 合併／佈建；NFO／封面（v0.5-D）
│  ├─ metadata.py         # 統一 item_metadata（全站）
│  ├─ store/              # SQLite 歷史／佇列／去重（migrations）
│  ├─ events.py           # JSON-lines 事件（含 metadata / duplicate / postprocess）
│  └─ cli.py              # resolve / probe / get / history / queue / …
├─ app/                   # Tauri v2 + React/TS GUI（sidecar 叫 udl）
├─ docs/                  # 事件協定、metadata、extractor 介面
├─ packaging/             # PyInstaller spec
└─ scripts/               # sidecar 建置腳本
```

GUI 透過 `tauri-plugin-shell` 啟動 `udl` sidecar，解析 stdout 的 JSON-lines（見 `docs/events.md`）。  
全域歷史 DB 在應用資料目錄（與 FFmpeg 佈建同根），由核心擁有。

**不做：** gallery-dl 程式碼／import（GPL-2.0-only 衝突）、Pixeldrain、通用任意站 fallback、付費牆／配額／VIP／區域繞過、完整登入保管、簽章。

## 目前支援

| 站點 | 狀態 | 來源 |
|------|------|------|
| Gofile | ✅ | ltsdw/gofile-downloader（訪客 token，公開連結） |
| Bunkr | ✅ | Lysagxra/BunkrDownloader（相簿／單檔） |
| Iwara | ✅ | Moeary/IwaraTool（公開影片／播放清單／作者頁） |
| Bilibili | ✅ | ScottSloan/Bili23-Downloader（BV/av、多分 P、b23、可匿名 bangumi；DASH+FFmpeg） |
| MEGA | ✅ 外部工具 | **megatools**（優先）／MEGAcmd `mega-get`（備援）；子行程，不移植 MegaBasterd |

上游 commit 與授權細節見 `NOTICE`。介面約定見 `docs/extractor-interface.md`。路線圖見 `ROADMAP.md`。

### Iwara 備註

- URL：`/video/<id>`、`/playlist/<id>`、`/profile/<user>`（含 `/videos`）
- 畫質：`--quality Source|540|360`（預設 Source，自動往下回退）
- 公開內容匿名可抓；`--token` 僅記憶體暫存（尚未實作帳密登入／keychain）
- **不**預設開 Cloudflare 繞過。需要時：`udl get … --cloudflare-bypass`（需 `uv sync --extra iwara` 或安裝 cloudscraper），GUI 有對應核取方塊且預設關閉
- `X-Version` salt 在 `core/udl/extractors/iwara_xversion.py`；全部失敗會拋清楚錯誤

### Bilibili 備註

- 預設使用**無需登入即可取得的最高畫質**；`--quality`（qn 數字）／GUI 可改選。
- 多分 P：未帶 `?p=` 時預設全部；bangumi 季節連結預設當前／第一集，可用 `--part all`。
- 需要 FFmpeg（PATH、`UDL_FFMPEG`、`--ffmpeg`，或 GUI 首次下載到應用資料目錄）。缺少時會發出 `ffmpeg_missing`。
- **不會**繞過大會員、付費牆或地區限制；QR 登入僅 stub。

### MEGA 備註（v0.4）

- 公開連結：`mega.nz/file/…`、`/folder/…`、舊格式 `#!`／`#F!`（亦接受 `mega.co.nz`／`mega.io`）。
- **工具選擇：** 優先 `megatools`（`megatools dl`／`megadl`）— 單一無頭二進位、進度可解析、macOS／Windows／Linux 皆可；找不到再試 MEGAcmd 的 `mega-get`（可能需要背景 `mega-cmd-server`）。
- 授權：megatools 為 GPL-2.0（含 OpenSSL 例外）→ **只以子行程呼叫，絕不 import／連結**；MEGAcmd 多為 BSD-2。詳見 `NOTICE`。
- 缺少工具時發出 `mega_tool_missing` 與安裝指引（brew／scoop／apt／[mega.io/cmd](https://mega.io/cmd)）。可用 `--mega-tool` 或 `UDL_MEGA_TOOL` 指定路徑。
- 取消會結束子行程（行程組 SIGTERM→SIGKILL）。
- **不**實作 SmartProxy／Quota Recovery／換 IP 等配額繞過；遇到 509 只回報清楚錯誤。

### GUI 設定與佇列

- **設定頁**：預設下載資料夾、同時任務數、單一任務檔案並發、重複檔策略（skip／overwrite／rename）、命名模板（即時預覽）／分類規則、NFO／封面後處理（全域＋站點）、Iwara 畫質／Cloudflare（預設關）、Bilibili 分 P／畫質、FFmpeg 路徑；寫入應用資料目錄 `settings.json`（並同步 `udl.config.json`）。
- **佇列**：並發下載、暫停／取消／重試、zh-TW 錯誤訊息、開啟檔案／資料夾、焦點時偵測剪貼簿連結；狀態寫入 SQLite，重啟後未完成任務自動恢復。
- **歷史頁**：搜尋、站點／狀態／日期／標籤篩選、排序、刪除條目（可選刪檔）；有本機封面時顯示縮圖。
- **修復頁**：掃描缺檔／`.part`／大小／hash／orphan，可重新排隊、重新連結、清殘檔、刪過期紀錄。
- **FFmpeg 首次佈建**：偵測不到時可下載目前 OS／arch 靜態建置並驗證 checksum（見 `NOTICE`）。CLI：`udl ffmpeg status` / `udl ffmpeg install`。

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
2. 若出現「無法驗證開發者」：系統設定 → 隱私權與安全性 → 仍要打開；或在終端機對 App 執行  
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
