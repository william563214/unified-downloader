# ROADMAP — 整合下載器

## v0.1（本版）

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

- [ ] 移植 Moeary/IwaraTool API／下載邏輯（拆 Qt）
- [ ] 匿名公開影片、畫質回退 Source→540→360
- [ ] X-Version salt 失效錯誤提示
- [ ] 不預設 Cloudflare 繞過
- [ ] 登入延後

## v0.3 — Bilibili

- [ ] BV/av 單一影片解析（子集）
- [ ] 匿名畫質 playurl
- [ ] DASH + FFmpeg 合併（子行程或首次下載 FFmpeg）
- [ ] 明確拒絕付費牆／大會員繞過
- [ ] QR／Cookie 登入（可選，憑證進 OS keychain）

## v0.4 — MEGA 與強化

- [ ] MEGA 經外部工具（建議 megatools 或 MEGAcmd `mega-get`）
- [ ] `ExternalExtractor` 介面統一事件
- [ ] **不**實作 SmartProxy／Quota Recovery
- [ ] 佇列持久化、限速、剪貼簿監看（可選）

## 不做／延後

- gallery-dl 當 library（授權衝突）；若備援只允許獨立子行程
- 通用「任意網站」fallback
- 程式碼簽章／公證（v0.1 明確跳過）
- 登入流程（v0.1 無）
