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
| Bilibili | bvid 或 `bvid/pN` | author、pubdate、tags、desc、cover、views／likes |
| MEGA | handle | canonical_url、kind（file／folder）；其餘常為空 |

## 去重鍵

優先順序（見 `udl.store.duplicates`）：

1. `(site, item_id)`
2. `canonical_url`／`source_url`
3. `sha256`（下載完成後寫入歷史）
4. 本機既有輸出路徑

策略：`--on-duplicate skip|overwrite|rename`（GUI 設定同步）。
