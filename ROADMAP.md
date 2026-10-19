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
- [ ] QR／Cookie 登入（stub／設計註解；憑證進 OS keychain — 延後至 v0.7）
- [x] 首次執行可下載 FFmpeg（checksum；macOS arm64 / Windows x64）
- [x] GUI 設定頁（下載目錄、並發、站點選項、FFmpeg 路徑）
- [x] 佇列打磨（並發／暫停／取消／重試／zh-TW 錯誤／開檔／剪貼簿）
- [x] Release 產未簽章 macOS arm64 `.dmg` + Windows x64 installer（sidecar triple）

## v0.4 — MEGA

- [x] MEGA 經外部工具：優先 **megatools**（`megatools dl`），備援 MEGAcmd `mega-get`
- [x] `external_extractor` 介面＋進度轉成標準 JSON-lines
- [x] 公開 file／folder 與舊 `#!`／`#F!` 連結
- [x] 缺少工具 → `mega_tool_missing`＋安裝指引；`--mega-tool` / `UDL_MEGA_TOOL`
- [x] 取消時乾淨結束子行程
- [x] GUI MEGA 徽章與工具提示
- [x] **不**實作 SmartProxy／Quota Recovery／換 IP
- [ ] 首次執行自動下載 megatools（延後；目前缺則清楚錯誤）

## v0.5 — 全域基礎設施（適用所有站點）

本版為**跨站**基礎設施（含外部工具 extractor），非單一站點功能。

### 本 PR（foundation）

- [x] 統一 `item_metadata` 模型；所有既有 extractor（Gofile／Bunkr／Iwara／Bilibili／MEGA）填入可取得欄位
- [x] JSON-lines 事件攜帶 `metadata`；新增 `duplicate` 事件（見 `docs/metadata.md`、`docs/events.md`）
- [x] 核心擁有 SQLite（app data dir）＋ schema migrations：歷史 job／file（路徑、狀態、時間、hash／size）
- [x] CLI：`udl history list|show|delete`、`udl queue list|show|delete|set-status`
- [x] GUI 歷史頁：搜尋、篩選（站點／狀態／日期／標籤）、排序、清理（可選刪檔）
- [x] 下載前去重：`(site,item_id)` → URL → size／hash；`--on-duplicate skip|overwrite|rename`；GUI 顯示已下載
- [x] 持久化任務佇列（SQLite）；重啟後未完成 job 自動恢復；HTTP Range 續傳沿用既有引擎

### 後續 follow-ups（仍屬 v0.5 範圍，可分 PR）

- [ ] 命名／分類規則引擎（依 metadata 產生路徑與檔名）
- [x] NFO／sidecar 資訊檔輸出（`udl/postprocess/nfo.py`；CLI／GUI 全域＋站點開關）
- [x] 封面／縮圖下載（`udl/postprocess/cover.py`；poster／folder.jpg；可選 FFmpeg 嵌入）
- [ ] 修復中心（缺檔、hash 不符、半成品 `.part`；可讀 history `role`／`cover_path`）
- [ ] aria2 RPC 後端（與另一代理的下載引擎工作協調；本 foundation 不改引擎內部）
- [ ] 全域限速／Proxy（同上，下載引擎平行工作）

## v0.6 — Iwara 對齊 Moeary／IwaraTool

- [ ] 登入＋ OS keychain 保管憑證
- [ ] 搜尋／標籤瀏覽
- [ ] 訂閱（following）同步下載
- [ ] 推薦流（SFW／NSFW 可選）
- [ ] 篩選器（畫質、時長、標籤排除…）
- [ ] 預覽（縮圖／短預覽）

## v0.7 — Bilibili 對齊 Bili23-Downloader

- [ ] QR／SMS／Cookie 登入（憑證進 OS keychain；僅使用者自有帳號權限）
- [ ] 全解析類型覆蓋（在帳號權限內）
- [ ] 批次任務
- [ ] 8K／HDR／Dolby（僅限帳號可得流，不繞過大會員／付費／區域）
- [ ] 彈幕／字幕
- [ ] 章節
- [ ] 封裝 mp4／mkv 選項

## 不做／延後

- gallery-dl 當 library（授權衝突）；若備援只允許獨立子行程
- Pixeldrain（明確排除）
- 通用「任意網站」fallback
- 程式碼簽章／公證（v0.1 明確跳過）
- 付費牆／VIP／區域限制／MEGA 配額繞過
- 移植 MegaBasterd（改走 megatools／MEGAcmd 子行程）
