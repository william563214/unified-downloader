# Changelog

## Unreleased

- MCP：`udl mcp` stdio 伺服器（官方 `mcp` SDK；optional `--extra mcp`）供 Claude Desktop／Cursor 控制佇列與歷史
- Tools：add_download／list_queue／get_status／pause／resume／cancel／search_history／list_supported_sites／resolve_url；唯讀 queue／history resources
- 安全：不暴露憑證；MCP 無法開 Cloudflare／impersonation；輸出路徑限制在 download root；GUI 開關預設關＋可複製客戶端 JSON（closes #39）
- v0.10 共用片段下載基底（HLS／DASH／HTTP 分段）：續傳／重試／合併；接 v0.9-A 分段與 aria2 RPC
- 外部 aria2c／ffmpeg 子行程同一介面；加密串流清楚拒絕（closes #25）
- 文件：`docs/fragment-download.md`；NOTICE 記載 yt-dlp Unlicense commit `8973412`
- v0.10 自動分資料夾：檔案類型（副檔名／MIME／媒體類型）＋來源網址（網域／路徑）規則；priority 排序
- 規則 JSON 對齊 download_classify（`rules`／`site_rules`／`extension_rules`）；GUI 編輯／預覽／匯入匯出；下載路徑與 ZIP 解壓後套用（closes #27）
- v0.10：`udl.network` 抽象成 RequestHandler（urllib／requests／可選 curl_cffi／websockets）
- proxy、UA、限速集中於網路層；curl_cffi 指紋模擬選用預設關（同 Cloudflare）
- CLI `--impersonate`／`--request-handler`；GUI 網路設定；NOTICE 記載 yt-dlp Unlicense `8973412`（closes #24）
- Extractor 註冊表＋lazy 載入：每站 `_VALID_URL`；`scripts/make_lazy_extractors.py` 產生 stub；sidecar 啟動不 import 全部模組（closes #23；參考 yt-dlp Unlicense commit 8973412）
- v0.10：可設定順序的 postprocessor 鏈（checksum／av／extract／nfo／cover／move／exec）
- 輸出模板語法補強：`{field|default}`、替代欄位、`>` strftime、`%(…)s`（對齊 yt-dlp OUTPUT TEMPLATE）
- ZIP 解壓後分類 hook 與 move 步驟整合既有規則；GUI 可調 PP 順序
- 文件：`docs/postprocessor-chain.md`、naming-rules／NOTICE／ROADMAP（closes #26）
- ZIP 有序密碼表：依序試密（僅 udl.secrets）；成功可置頂；per-job list／single／off
- 修復中心一次性密碼重試並可選加入清單；歷史僅記 password N of M（closes #32）
- 下載後可選自動解壓 ZIP（全域預設關；per-job inherit／on／off）
- 核心 postprocessor：zip-slip／symlink／bomb 防護、日／中文檔名編碼、加密 ZIP needs_password
- 事件 `extracting`／`extracted`／`failed`；歷史與修復中心 `retry-extract`
- 文件：`docs/auto-extract.md`；ROADMAP／events 更新（closes #30）
- v0.9-C 排程：具名佇列、時段、完成後動作、流量配額、時段限速檔
- CLI `udl queue schedule …`；GUI「排程」頁；`docs/scheduler.md`
- gallery-dl 備援：GPL-2.0-only 僅子行程整合（禁止 import）；router 在 Direct 之前
- GUI 設定／佇列來源／支援模組清單；cookies／secrets／proxy／限速／可選 archive
- Release mere aggregation 打包獨立 gallery-dl（附 LICENSE／SOURCE）
- 文件：`docs/gallery-dl.md`、TESTING／NOTICE／ROADMAP 更新

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
