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
- [x] QR／Cookie 登入（v0.7-B；憑證進 OS keychain）
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

## MEGA built-in（全域基礎設施之後）

移植 tonikelope/megabasterd（GPL-3.0）協定能力為內建 Python 實作；外部 megatools／MEGAcmd 改為可選備援。

- [x] MEGA API 客戶端（cs、sequence id、錯誤碼、重試／backoff）
- [x] AES-CTR 解密、key／IV／meta-MAC、屬性解密
- [x] 公開 file／folder（含舊 `#!`／`#F!` 與新 `/file/`／`/folder/`、子路徑）
- [x] 多連線分段下載、chunk 對齊解密、重啟續傳、結束 CBC-MAC
- [x] 接全域限速／代理／UA；`udl.download.segment` 規劃器；HTTP 引擎見 v0.9-A `udl.download.segmented`
- [x] GUI 資料夾樹勾選（含大小）＋設定後端切換
- [x] 帳號登入（含 v2）＋ `udl.secrets`／`mega.session`；雲端硬碟
- [x] localhost 串流預覽（隨機 token、即時解密）
- [x] 509／over-quota 清楚提示；**不**做 SmartProxy／換 IP／配額繞過
- [x] 接入 metadata／history／去重／NFO／封面／命名
- [x] `docs/mega.md`；NOTICE 保留 MegaBasterd 出處與上游 commit

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

### v0.7-A（匿名＋可插 session cookie）

- [x] 解析類型：普通多 P、bangumi ep/ss/md、cheese、互動 graph、au/am、UP 空間、公開收藏夾、每周必看、合集／系列
- [x] 帳號專屬清單 stub → `requires_login`（由 v0.7-B 接上）
- [x] 批次多連結、UP／收藏夾 keyword、條件自動勾選（日期／時長／標題／P 範圍）
- [x] 畫質列表＋AVC／HEVC／AV1、音訊品質（含 Hi-Res／Dolby **僅 API 有提供時**）
- [x] 彈幕 xml／ass／json、字幕 srt／lrc／txt／ass／json、章節、mp4／mkv、可選 ASS 嵌入
- [x] 統一 metadata＋per-type 命名模板預設；接入 NFO／封面／規則／去重
- [x] GUI「Bilibili」解析頁（樹狀勾選 → 佇列）

### v0.7-B（本版／登入＋帳號清單＋CDN）

- [x] QR 掃碼登入（poll）＋手動 Cookie 貼上；session 進 `udl.secrets`（`bilibili.session`）
- [x] 簡訊登入 best-effort（GeeTest 需瀏覽器；文件與 GUI 標明）
- [x] 驗證／重新整理／登出；GUI「帳號」頁（QR、使用者名稱、VIP）
- [x] session 接入所有 Bilibili API；帳號清單：稍後再看／歷史／追番／訂閱合集／自己的收藏夾＋keyword
- [x] 登入後暴露帳號有權之畫質（8K／4K／HDR／DV／Hi-Res／Atmos）— **不**繞過 VIP／付費／區域
- [x] CDN 節點偏好＋自訂 UA／Host（接 PR #6 `udl.network`）
- [x] 更新 `docs/bilibili.md`／本 ROADMAP；保留 NOTICE

### v0.7 follow-up（Bili23 對齊補完）

- [x] protobuf 分段彈幕（`seg.so` 6 分鐘包；合併去重；ASS／XML／JSON；XML 回退）
- [x] 一鍵展開並入佇列：追番／收藏夾目錄／訂閱合集／UP 空間（翻頁＋既有篩選；GUI 進度）
- [x] 互動影片全圖遍歷（questions＋skips、cid 去重；可選 per-node 命名）
- [x] 簡訊登入 GeeTest：Tauri webview 供使用者本人完成挑戰並回傳 validate／seccode（不自動解碼）
- [x] 更新 `docs/bilibili.md`／本 ROADMAP；保留 NOTICE／GPL-3.0

### v0.7.0 安裝包

- [x] 版本號統一 0.7.0（Python／Tauri／npm／Cargo）
- [x] PyInstaller sidecar：keyring 平台後端、qrcode／Pillow、v0.7 模組
- [ ] GitHub Actions `release-artifacts`：未簽章 macOS arm64 `.dmg`＋Windows x64 NSIS

### v0.7.0 手動測試與已知風險修復

- [x] 手動測試清單：[`docs/TESTING.md`](docs/TESTING.md)＋ GitHub issue [#15](https://github.com/william563214/unified-downloader/issues/15)（建議標籤 `testing`）
- [x] `folder.jpg`：同目錄多檔只寫一次；預設仍為 per-file `*-poster.jpg`
- [x] 大檔 hash：完成時用 fast partial（`UDL_HASH_MODE`），並可背景補算 full sha256
- [x] 修復 orphan：僅掃歷史 output 根＋已知副檔名＋忽略清單
- [x] MEGA 去重：node handle＋size（避免粗粒度 handle 誤 skip）

## v0.8 — GUI 殼層／統一佇列（本線）

跨站 GUI 與 Tauri 殼層打磨；**不**改動 Bilibili extractor／解析頁核心（交 v0.8-B），**不**搶 v0.8-A 的測試文件／postprocess／history／repair 修補。合併前 rebase 到 `main`。

### 統一佇列儀表板

- [x] 跨站 active／queued／paused／failed 一覽
- [x] 每任務進度／速度／ETA；暫停／繼續／取消／重試；排序；並發設定；批次操作
- [x] 失敗任務顯示錯誤＋「開啟修復」

### 輸入 UX

- [x] 多網址貼上；視窗拖放文字／連結；可選剪貼簿監看（預設關）並提示加入
- [x] 偵測時顯示站點 badge

### 系統整合

- [x] 任務完成／失敗系統通知（可關）
- [x] 系統列／選單列圖示＋快捷狀態；關閉視窗最小化到托盤（可關）

### 設定整合

- [x] 單一設定頁分區：下載目錄、命名／規則、後處理 NFO／封面、網路、帳號（Iwara／Bilibili）、FFmpeg、MEGA 工具路徑
- [x] 匯入／匯出設定 JSON（排除密鑰）

### 更新／主題／文件

- [x] Tauri updater 接 GitHub Releases；無簽章公鑰則停用（見 `docs/updater.md`）
- [x] 深／淺色跟隨系統；站點頁共用殼層版面
- [x] 更新 ROADMAP 與 README 截圖佔位說明

### 平行線（其他 agent）

- v0.8-A：測試文件＋ postprocess／history／repair 修補
- v0.8-B：Bilibili extractor／解析頁

## v0.9 — 直連／分段／排程／批次

依賴順序：v0.9-A（通用直連＋分段引擎）→ 其餘可並行；合併前需 rebase 到含 A 的 main。

### v0.9-A — 通用直連＋多連線分段

- [x] 通用直接連結 extractor：未被站點匹配的 HTTP／HTTPS URL（router 最低優先）
- [x] HEAD／GET 探測：大小、檔名（Content-Disposition／URL）、content-type、Accept-Ranges、ETag／Last-Modified
- [x] 接入統一 metadata／歷史／去重／命名規則／佇列續傳（續傳時驗證 ETag／Last-Modified；變更則重下）
- [x] 共用 HTTP 層 IDM 風格動態分段（`udl.download.segmented`；與 MEGA 的 `udl.download.segment` 規劃器並存）：最多 N 連線（預設 8、可設定、per-host 上限）；連線完成後切分最大剩餘區塊；分段狀態可持久化以重啟續傳；無 Range 則單連線
- [x] 尊重 `udl.network` 全域限速與代理；aria2 仍為可選後端
- [x] 可選讓產出純 HTTP 檔案的站點 extractor 也走分段（Gofile／Bunkr／Iwara／MEGA 排除）
- [x] `progress` 事件帶 per-segment；GUI：連線數設定、任務詳情分段進度條
- [x] `docs/networking.md` 更新

### v0.9-C — 排程器／配額／時段限速

- [x] 具名佇列＋每日／平日／指定日時段、每佇列並發、repeat／once（SQLite 持久化，重啟保留）
- [x] 完成後動作：none／notify／quit／sleep／shutdown（macOS＋Windows；倒數確認可取消）
- [x] 流量配額（每小時／每日 MB）：超量暫停、下一個視窗恢復；經 `udl.download.rate` 計量
- [x] 時段限速 profile（白天較慢等）；與全域限速取較嚴者
- [x] GUI「排程」頁＋每佇列設定；與 v0.8 佇列儀表板共用狀態；CLI `udl queue schedule …`
- [x] 文件：`docs/scheduler.md`；本 ROADMAP 更新

### v0.9-D（本版／頁面擷取＋校驗＋掃毒）

- [x] Download All：`udl page URL` 抓 HTML，列出 `<a>`／`<video|audio|source>`／可選 `<img>`＋既有站點 extractor 命中
- [x] 篩選：副檔名／類型、HEAD 大小、regex／glob、同網域
- [x] GUI「頁面」核取清單（全選／依類型）→ 入佇列；CLI `--json`
- [x] 校驗：MD5／SHA-1／SHA-256（CLI／metadata／`.sha256` sidecar）；事件＋歷史；不符 → 失敗＋修復中心
- [x] 可選下載後指令（預設關；Windows Defender／ClamAV 預設集、逾時）
- [x] 文件：`docs/page-download.md`、`docs/checksum-hook.md`；更新 events／ROADMAP

### gallery-dl 備援（子行程）

- [x] 授權確認：GPL-2.0-only → **禁止** import／vendor；僅獨立子行程（NOTICE 記載 commit）
- [x] `udl.extractors.external.gallerydl`：CLI／JSON（`-j -s`、`--Print`、proxy／限速／cookies／archive）
- [x] Router：native 站點之後、`direct` 之前；以 gallery-dl `-E` 辨識 URL
- [x] 對接 metadata／JSON-lines／history／去重／命名／NFO／cover／佇列續傳
- [x] GUI 設定（啟用、路徑、cookies、per-site `-o`、archive）＋佇列來源 `gallery-dl`＋模組清單
- [x] Release mere aggregation 打包獨立 `gallery-dl`（附 LICENSE／SOURCE）；失敗則改提示安裝
- [x] `docs/gallery-dl.md`、`docs/TESTING.md` 手動項目；更新 ROADMAP／NOTICE

### 自動解壓 ZIP（postprocessor）

- [x] 全域設定「下載後自動解壓 ZIP」（預設關）＋新增下載／佇列 per-job 覆寫 inherit／on／off（[#30](https://github.com/william563214/unified-downloader/issues/30)）
- [x] 偵測：`.zip` 或 magic `PK\x03\x04`；僅在下載完成且 checksum／AV hook 通過後執行
- [x] 核心 `udl.postprocess.zip_extract`（stdlib `zipfile`）：同名子資料夾／同目錄、可選刪原檔；zip-slip／symlink／zip bomb；UTF-8／cp437→cp932／gbk；加密 ZIP → `needs_password`（可選 `udl.secrets` zip.password）
- [x] 事件 `extracting`／`extracted`／`failed`＋歷史／修復中心 `retry-extract`；GUI 狀態顯示
- [x] 分類規則 hook 預留（接 [#27](https://github.com/william563214/unified-downloader/issues/27)）；文件 `docs/auto-extract.md`

### 規劃中（非本 PR）

- [ ] **瀏覽器擴充功能**（Chrome／Edge MV3）：重用 [william563214/download_classify](https://github.com/william563214/download_classify) — 右鍵選單、下載接管、native messaging／localhost＋token
  - **不做**：DRM／加密串流繞過、系統級下載攔截（system-wide download hooking）

## 不做／延後

- gallery-dl 當 **library／import／vendored 原始碼**（GPL-2.0-only 與 GPL-3.0-or-later 連結不相容）；子行程備援已實作
- Pixeldrain（明確排除）
- 通用「任意網站」fallback（v0.9-A 僅直連檔案；站點解析交給 gallery-dl 子行程）
- DRM／加密 HLS key 擷取
- 程式碼簽章／公證（v0.1 明確跳過）
- 付費牆／VIP／區域限制／MEGA 配額繞過（含 SmartProxy／換 IP）
- MegaBasterd GUI／SmartProxy 不移植（協定能力已內建於 `udl.extractors.mega`）
- DRM／加密串流繞過；系統級下載攔截
