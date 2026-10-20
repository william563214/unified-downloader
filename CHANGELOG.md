# Changelog

## Unreleased (v0.9-D)

- Download All：`udl page URL` 掃描網頁候選連結；GUI「頁面」勾選入佇列
- 下載後校驗 MD5／SHA-1／SHA-256（CLI／metadata／sidecar）；不符進修復中心
- 可選下載後掃毒／指令 hook（Defender／ClamAV 預設集，預設關閉）
- 文件：`docs/page-download.md`、`docs/checksum-hook.md`

## 0.7.0

- Bilibili v0.7 對齊 Bili23：多類型解析、彈幕／字幕／章節、codec、CDN／Host
- Bilibili QR／Cookie 登入與帳號清單（憑證進 OS keychain）
- Iwara v0.6 瀏覽／訂閱／登入／Following 匯入（含於本版安裝包）
- v0.5 全域基礎設施：metadata、歷史／佇列、命名／規則／修復、NFO／封面、網路限速／代理／aria2
- 桌面安裝包版本統一為 0.7.0（Python sidecar、Tauri、npm、Cargo）
- PyInstaller sidecar 補齊 keyring 平台後端、qrcode／Pillow 與 v0.7 模組 hidden imports
