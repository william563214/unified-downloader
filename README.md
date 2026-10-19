# 整合下載器（unified-downloader）v0.3

桌面應用：貼上網址 → 辨識站點 → 用統一 Python 核心下載。  
授權：**GPL-3.0-or-later**（見 `LICENSE`、`NOTICE`）。

## 架構

```
unified-downloader/
├─ core/udl/              # Python 核心套件
│  ├─ router.py           # URL → extractor 註冊表
│  ├─ extractors/         # gofile、bunkr、iwara、bilibili/
│  ├─ download/http.py    # 共用 HTTP：續傳、重試、並行、取消
│  ├─ postprocess/ffmpeg.py  # DASH 合併（Bilibili 等可重用）
│  ├─ events.py           # JSON-lines 事件（含 merging / probe）
│  └─ cli.py              # udl resolve / probe / get / sites / schema
├─ app/                   # Tauri v2 + React/TS GUI（sidecar 叫 udl）
├─ docs/                  # 事件協定、extractor 介面
├─ packaging/             # PyInstaller spec
└─ scripts/               # sidecar 建置腳本
```

GUI 透過 `tauri-plugin-shell` 啟動 `udl` sidecar，解析 stdout 的 JSON-lines（見 `docs/events.md`）。

**不做：** gallery-dl 程式碼／import（GPL-2.0-only 衝突）、通用任意站 fallback、付費牆／配額／VIP／區域繞過、完整登入保管、簽章。

## 目前支援

| 站點 | 狀態 | 來源 |
|------|------|------|
| Gofile | ✅ | ltsdw/gofile-downloader（訪客 token，公開連結） |
| Bunkr | ✅ | Lysagxra/BunkrDownloader（相簿／單檔） |
| Iwara | ✅ | Moeary/IwaraTool（公開影片／播放清單／作者頁） |
| Bilibili | ✅ | ScottSloan/Bili23-Downloader（BV/av、多分 P、b23、可匿名 bangumi；DASH+FFmpeg） |
| MEGA | 延後 | v0.4 外部工具 |

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
- 需要 FFmpeg（PATH、`UDL_FFMPEG` 或 `--ffmpeg`）。尚未內建自動下載；缺少時會發出 `ffmpeg_missing`。
- **不會**繞過大會員、付費牆或地區限制；QR 登入僅 stub。

## 開發環境

### 需求

- Python ≥ 3.11、[uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20、Rust（stable）
- FFmpeg（Bilibili DASH 合併）
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
- `.github/workflows/release.yml`：**未簽章** macOS arm64、Windows x64 安裝包與 sidecar

## 注意事項

- 僅下載你有權存取的內容，並遵守各站服務條款。
- Gofile 訪客帳號與 `X-Website-Token` 沿用上游公開連結行為；salt 可能失效。
- Bunkr 網域與簽章 API 常變，需追上游。
- Iwara `X-Version` salt 與 Cloudflare 策略會變；預設不用 cloudscraper。
- Bilibili WBI 簽章與 playurl 行為可能隨站方變更；區域／會員限制會以錯誤事件回報。
- 自動化測試仍刻意延後。

## 授權

GPL-3.0-or-later。移植模組保留上游著作權，詳見 `NOTICE`。
