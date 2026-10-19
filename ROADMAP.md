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

## v0.3 — Bilibili（本版）

- [x] BV/av 影片解析（含多分 P）
- [x] b23.tv / bili2233.cn 短鏈解析
- [x] bangumi／episode（僅可匿名取得的集數；拒絕會員／付費 badge）
- [x] 匿名畫質 playurl（預設最佳可取得 qn，不做登入牆繞過）
- [x] DASH + FFmpeg 合併（`udl/postprocess/ffmpeg.py`；PATH / `UDL_FFMPEG` / `--ffmpeg`）
- [x] `merging` JSON-lines 事件 + `udl probe`（分 P／畫質）
- [x] GUI Bilibili 徽章＋分 P／畫質選擇
- [x] 明確拒絕付費牆／大會員／區域繞過（清楚錯誤碼／訊息）
- [ ] QR／Cookie 登入（stub／設計註解；憑證進 OS keychain — 延後）
- [ ] 首次執行自動下載 FFmpeg（延後；目前缺則 `ffmpeg_missing`）

## v0.4 — MEGA 與強化

- [ ] MEGA 經外部工具（建議 megatools 或 MEGAcmd `mega-get`）
- [ ] `ExternalExtractor` 介面統一事件
- [ ] **不**實作 SmartProxy／Quota Recovery
- [ ] 佇列持久化、限速、剪貼簿監看（可選）

## 不做／延後

- gallery-dl 當 library（授權衝突）；若備援只允許獨立子行程
- 通用「任意網站」fallback
- 程式碼簽章／公證（v0.1 明確跳過）
- 付費牆／VIP／區域限制繞過
- 完整 Iwara 帳號登入流程與憑證保管（僅 token stub）
