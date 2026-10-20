# 統一 Metadata 模型（v0.5）

所有站點 extractor（含外部工具如 MEGA）在 `resolve()`／`probe()` 時應填入
`udl.metadata.item_metadata`，欄位有就填、沒有就留空。核心與 GUI 以此做
歷史、去重、顯示，不依賴各站私有 `meta` 字典（`meta` 仍可放實作細節）。

## 欄位

| 欄位 | 型別 | 說明 |
|------|------|------|
| `site` | string | 穩定站點 id（`gofile`／`bunkr`／`iwara`／`bilibili`／`mega`） |
| `item_id` | string? | 站內主鍵（content id、BV、handle、video id…） |
| `canonical_url` | string? | 正規化後的內容 URL |
| `title` | string? | 標題 |
| `author` | string? | 作者／上傳者 |
| `upload_date` | string? | ISO-8601 日期或日期時間 |
| `tags` | list[str] | 標籤 |
| `description` | string? | 描述／簡介 |
| `duration` | float? | 秒 |
| `resolution` | string? | 解析度或畫質標籤（`1920x1080`、`Source`、qn…） |
| `thumbnail_url` | string? | 縮圖 URL |
| `cover_url` | string? | 封面 URL |
| `view_count` | int? | 播放／瀏覽數 |
| `like_count` | int? | 讚／喜歡數 |
| `collection` | string? | 相簿／播放清單／資料夾標題 |
| `collection_id` | string? | 集合 id |
| `extra` | object | 站點專屬欄位（不進通用篩選） |

Python：`from udl.metadata import item_metadata`。  
序列化：`metadata.to_dict(omit_empty=True)`。

## 掛載位置

- `extract_result.metadata` — 整個 job 的代表 metadata
- `extracted_file.metadata` — 單一檔案（相簿多檔時各自填 `item_id`）
- JSON-lines：`job`／`file`／`probe`／`done`／`duplicate` 的 `metadata` 物件（見 `docs/events.md`）

## 各站填入慣例

| 站點 | `item_id` | 常見其他欄位 |
|------|-----------|--------------|
| Gofile | content／file id | title、collection（資料夾） |
| Bunkr | album／media slug | title、canonical_url、collection |
| Iwara | video id | author、tags、duration、thumbnail、views／likes |
| Bilibili | bvid／`bvid/pN`／au／season | author、pubdate、tags、desc、cover、views／likes、collection；`extra.kind`／`nfo_type` |
| MEGA | node handle（`extra.node_handle`） | canonical_url、kind（file／folder）；其餘常為空 |

## 去重鍵

優先順序（見 `udl.store.duplicates`）：

1. `(site, item_id)` — **MEGA** 在已知 size 時改為 `(site, node handle, size)`，避免資料夾級 handle 誤判兄弟檔
2. `canonical_url`／`source_url`
3. `sha256`（下載完成後寫入歷史；大檔預設 fast partial，可用 `UDL_HASH_MODE=full|fast|auto|off`）
4. 本機既有輸出路徑

策略：`--on-duplicate skip|overwrite|rename`（GUI 設定同步）。

## NFO／封面後處理（v0.5-D）

下載完成後可選擇輸出媒體庫 sidecar，實作在 `udl/postprocess/`（不改各站 extractor）：

| 模組 | 作用 |
|------|------|
| `nfo.py` | 依 metadata 產生 Kodi／Jellyfin／Emby 相容 `.nfo` |
| `cover.py` | 經共用 HTTP 引擎抓 `cover_url`／`thumbnail_url`，webp→jpg，可嵌入 mp4／mkv |
| `artifacts.py` | 串接事件與 SQLite 歷史 artifact 紀錄 |
| `options.py` | 全域＋站點開關 |

### NFO 根元素

依 metadata／`kind` 選擇（缺欄位則略過該 XML 節點）：

- `movie`（預設）
- `episodedetails`（bangumi／episode／`extra.ep_id` 等）
- `musicvideo`（音樂類 hint 或 `extra.nfo_type`）

常用對應：`title`、`director`／`artist`（author）、`premiered`（upload_date）、`tag`／`genre`、`plot`（description）、`rating`（`extra.rating`／`score`）、`runtime`（duration）、`website`／`uniqueid`（url／site／item_id）。

檔名：與媒體同 stem 的 `.nfo`（`video.mp4` → `video.nfo`）。

### 封面

- 來源：`cover_url` 優先，否則 `thumbnail_url`
- 檔名：`--cover-name poster` → `<stem>-poster.jpg`（預設）；`folder` → `folder.jpg`（同目錄只寫一次，不覆寫）
- HTTP：走 `udl.download.http`，帶站點 Referer（與下載引擎一致）
- webp／其他格式：優先以 FFmpeg 轉成 jpg
- `--embed-cover`：mp4 用 `attached_pic`；mkv 用 attachment（需 FFmpeg）

### CLI／GUI

```text
udl get URL -o DIR --write-nfo --write-cover --embed-cover \
  --cover-name poster --nfo-sites all,-mega --cover-sites all
```

GUI 設定頁有全域開關與各站 NFO／封面勾選；歷史頁若有本機 `cover_path` 或 `role=cover` 檔案會顯示縮圖。

### 事件與歷史

- 事件：`postprocess`；sidecar 另發 `file`（`role`=`nfo`｜`cover`）與 `done`（見 `docs/events.md`）
- SQLite：`history_files.role`、`history_jobs.cover_path`（供修復中心辨識產物）
