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

### foundation（metadata／history／queue）

- [x] 統一 `item_metadata` 模型；所有既有 extractor（Gofile／Bunkr／Iwara／Bilibili／MEGA）填入可取得欄位
- [x] JSON-lines 事件攜帶 `metadata`；新增 `duplicate` 事件（見 `docs/metadata.md`、`docs/events.md`）
- [x] 核心擁有 SQLite（app data dir）＋ schema migrations：歷史 job／file（路徑、狀態、時間、hash／size）
- [x] CLI：`udl history list|show|delete`、`udl queue list|show|delete|set-status`
- [x] GUI 歷史頁：搜尋、篩選（站點／狀態／日期／標籤）、排序、清理（可選刪檔）
- [x] 下載前去重：`(site,item_id)` → URL → size／hash；`--on-duplicate skip|overwrite|rename`；GUI 顯示已下載
- [x] 持久化任務佇列（SQLite）；重啟後未完成 job 自動恢復；HTTP Range 續傳沿用既有引擎

### naming／rules／repair

- [x] 命名模板引擎：per-site／per-type、統一 metadata 欄位、跨平台檔名清理、多層目錄、衝突處理；CLI `--name-template`／config；GUI 設定即時預覽
- [x] 分類規則引擎：有序規則（site／author／tags／title regex／duration／size／media type）→ 資料夾／模板覆寫／skip；JSON／TOML config；GUI 編輯與 URL 測試
- [x] 修復中心：掃描歷史 DB vs 檔案系統（缺檔、`.part`、大小／hash 不符、orphan）；`udl repair scan|fix`（JSON）；GUI 修復頁
- [x] 文件：`docs/naming-rules.md`

### NFO／cover

- [x] NFO／sidecar 資訊檔輸出（`udl/postprocess/nfo.py`；CLI／GUI 全域＋站點開關）
- [x] 封面／縮圖下載（`udl/postprocess/cover.py`；poster／folder.jpg；可選 FFmpeg 嵌入）

### 網路（下載引擎）

- [x] 全域／可選 per-job 限速（共用引擎；megatools `--limit-speed`）
- [x] 代理三模式：none／system／custom（HTTP／HTTPS／SOCKS5＋可選帳密）
- [x] `udl.secrets`（keyring）存放代理密碼，供日後登入重用
- [x] 可選 aria2 JSON-RPC 後端（headers／cookies、進度事件、取消；連不上回退內建）
- [x] 全域自訂 User-Agent
- [x] GUI 設定「網路」區塊（zh-TW）＋ `docs/networking.md`


## v0.6 — Iwara 對齊 Moeary／IwaraTool

### v0.6-A（公開瀏覽／本機訂閱，無登入）

- [x] 搜尋／標籤／作者／播放清單／首頁推薦 browse API（`udl iwara search|tag|author|playlist|home`）
- [x] 結果標註本地下載狀態（SQLite history／queue → downloaded／queued／not_downloaded）
- [x] 本機訂閱作者／播放清單＋`udl iwara sync` 增量入持久化佇列
- [x] 批次篩選：讚數、觀看、日期、標籤包含／排除、標題關鍵字
- [x] GUI：Iwara 瀏覽網格、訂閱頁、首頁自訂區塊、SFW／NSFW、HTML5 串流預覽
- [x] 文件：`docs/iwara-browse.md`；NOTICE 保留 Moeary/IwaraTool MIT 出處

### v0.6-B（帳號登入／私有片／Following 匯入）

- [x] 登入＋ OS keychain（`udl.secrets`）保管 email／password／user＋access token；`udl iwara login|logout|whoami`
- [x] Session 用於私有／好友可見片與 browse／search／sync（結果反映帳號）
- [x] 匯入帳號 Following → `iwara_subscriptions`＋可選批次 sync
- [x] 修復中心：Iwara 過期 URL 以 `repair fix requeue` 重 resolve（沿用 session）
- [x] GUI 設定／訂閱頁帳號區塊；文件與 NOTICE 更新

### 後續

- [ ] 進階篩選（時長等）與預覽強化

## v0.7 — Bilibili 對齊 Bili23-Downloader

### v0.7-A（本版／匿名＋可插 session cookie）

- [x] 解析類型：普通多 P、bangumi ep/ss/md、cheese、互動 graph、au/am、UP 空間、公開收藏夾、每周必看、合集／系列
- [x] 帳號專屬清單（稍後再看／歷史／追番／訂閱合集）→ `requires_login` stub（等登入 PR）
- [x] 批次多連結、UP／收藏夾 keyword、條件自動勾選（日期／時長／標題／P 範圍）
- [x] 畫質列表＋AVC／HEVC／AV1、音訊品質（含 Hi-Res／Dolby **僅 API 有提供時**）
- [x] 彈幕 xml／ass／json、字幕 srt／lrc／txt／ass／json、章節、mp4／mkv、可選 ASS 嵌入
- [x] 統一 metadata＋per-type 命名模板預設；接入 NFO／封面／規則／去重
- [x] GUI「Bilibili」解析頁（樹狀勾選 → 佇列）
- [x] CDN／自訂 Host：TODO（避開與 PR #6 UA／代理衝突；見 `docs/bilibili.md`）
- [ ] QR／SMS／Cookie 登入（憑證進 OS keychain；設計已預留 `set_session_cookies`／`--sessdata`）

### 仍屬 v0.7 follow-up

- [ ] 登入後帳號清單與更高畫質（仍不繞過大會員／付費／區域）
- [ ] protobuf 分段彈幕（目前走經典 XML comment API）

## 不做／延後

- gallery-dl 當 library（授權衝突）；若備援只允許獨立子行程
- Pixeldrain（明確排除）
- 通用「任意網站」fallback
- 程式碼簽章／公證（v0.1 明確跳過）
- 付費牆／VIP／區域限制／MEGA 配額繞過
- 移植 MegaBasterd（改走 megatools／MEGAcmd 子行程）
