# ROADMAP — 整合下載器

## v0.1

- [x] GPL-3.0-or-later + NOTICE（上游 commit）
- [x] Python 核心套件 `udl`：router、共用 HTTP 引擎、JSON-lines 事件
- [x] CLI：`udl resolve`、`udl get -o DIR --json`
- [x] Gofile extractor（訪客 token、公開連結）
- [x] Bunkr extractor（相簿＋單檔、簽章 API）
- [x] Iwara / Bilibili registry 槽位＋介面文件
- [x] Tauri v2 + React/TS GUI（sidecar、貼網址、進度、取消、開資料夾）
- [x] PyInstaller sidecar＋GitHub Actions（macOS arm64、Windows x64，未簽章）
- [ ] 自動化測試（刻意延後）

## v0.2 — Iwara

- [x] 移植 Moeary/IwaraTool API／下載邏輯（拆 Qt）
- [x] 匿名公開影片、播放清單、作者頁；畫質回退 Source→540→360
- [x] X-Version salt 失效錯誤提示（`iwara_xversion.py`）
- [x] 不預設 Cloudflare 繞過（`--cloudflare-bypass` / GUI 選用）
- [x] 登入延後（可選 Bearer token stub，不寫入磁碟）

## v0.3 — Bilibili

- [x] BV/av 影片解析（含多分 P）
- [x] b23.tv / bili2233.cn 短鏈解析
- [x] bangumi／episode（僅可匿名取得的集數；拒絕會員／付費 badge）
- [x] 匿名畫質 playurl（預設最佳可取得 qn，不做登入牆繞過）
- [x] DASH + FFmpeg 合併（`udl/postprocess/ffmpeg.py`；PATH / `UDL_FFMPEG` / `--ffmpeg`）
- [x] `merging` JSON-lines 事件 + `udl probe`（分 P／畫質）
- [x] GUI Bilibili 徽章＋分 P／畫質選擇
- [x] 明確拒絕付費牆／大會員／區域繞過（清楚錯誤碼／訊息）
- [ ] QR／Cookie 登入（stub／設計註解；憑證進 OS keychain — 延後）
- [x] 首次執行可下載 FFmpeg（checksum；macOS arm64 / Windows x64）
- [x] GUI 設定頁（下載目錄、並發、站點選項、FFmpeg 路徑）
- [x] 佇列打磨（並發／暫停／取消／重試／zh-TW 錯誤／開檔／剪貼簿）
- [x] Release 產未簽章 macOS arm64 `.dmg` + Windows x64 installer（sidecar triple）

## v0.4 — MEGA（本版）

- [x] MEGA 經外部工具：優先 **megatools**（`megatools dl`），備援 MEGAcmd `mega-get`
- [x] `external_extractor` 介面＋進度轉成標準 JSON-lines
- [x] 公開 file／folder 與舊 `#!`／`#F!` 連結
- [x] 缺少工具 → `mega_tool_missing`＋安裝指引；`--mega-tool` / `UDL_MEGA_TOOL`
- [x] 取消時乾淨結束子行程
- [x] GUI MEGA 徽章與工具提示
- [x] **不**實作 SmartProxy／Quota Recovery／換 IP
- [ ] 佇列持久化、限速（剪貼簿偵測已在 GUI 提供基本版）
- [ ] 首次執行自動下載 megatools（延後；目前缺則清楚錯誤）

## 不做／延後

- gallery-dl 當 library（授權衝突）；若備援只允許獨立子行程
- 通用「任意網站」fallback
- 程式碼簽章／公證（v0.1 明確跳過）
- 付費牆／VIP／區域限制／MEGA 配額繞過
- 完整 Iwara 帳號登入流程與憑證保管（僅 token stub）
- 移植 MegaBasterd（改走 megatools／MEGAcmd 子行程）
