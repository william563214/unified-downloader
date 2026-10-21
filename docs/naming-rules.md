# 命名模板與分類規則（v0.5）

跨站基礎設施：依統一 `item_metadata` 產生輸出路徑／檔名，並以有序規則覆寫資料夾、模板或略過下載。

## 命名模板

### 語法

路徑以 `/` 分隔多層目錄。佔位符：

| 佔位符 | 來源 |
|--------|------|
| `{site}` | metadata.site |
| `{author}` / `{uploader}` | metadata.author |
| `{title}` | metadata.title |
| `{id}` / `{item_id}` | metadata.item_id |
| `{date}` / `{date:%Y-%m}` | metadata.upload_date（strftime） |
| `{collection}` | 相簿／播放清單 |
| `{resolution}` / `{quality}` | 畫質標籤 |
| `{tags}` | 逗號分隔標籤 |
| `{ext}` | 原始副檔名（不含點） |
| `{name}` | 原始檔名（不含副檔名） |
| `{filename}` | 原始完整檔名 |
| `{type}` | 粗分媒體類型 video／audio／image／archive／document／program／other |

空白模板＝沿用各站 extractor 原本的 `relative_dir` + `name`。

範例：

```text
{site}/{author}/{date:%Y-%m}/{title} [{id}].{ext}
```

### 安全清理（macOS + Windows）

每個路徑元件會：

- 移除非法字元 `<>:"/\|?*` 與控制字元
- 避開 Windows 保留名（`CON`／`PRN`／`AUX`／`NUL`／`COM1`–`9`／`LPT1`–`9`）
- 去除尾端空白與點
- 限制單一元件長度（約 200）與整體路徑長度

### 衝突處理

設定／config 的 `name_conflict`：

- `rename`（預設）：`name.ext` → `name (1).ext`
- `overwrite`：交給下載引擎覆蓋（常與 `--on-duplicate overwrite` 併用）
- `error`：若目標已存在則失敗

去重策略 `--on-duplicate skip|overwrite|rename` 仍優先於「是否下載」。

### CLI

```bash
udl get URL -o ./out --name-template '{site}/{author}/{title}.{ext}'
udl naming preview --template '{site}/{title}.{ext}' \
  --metadata '{"site":"iwara","title":"demo","author":"alice"}' --json
udl config show --json
```

環境變數 `UDL_CONFIG` 可指向 JSON／TOML 設定檔（預設：應用資料目錄 `udl.config.json`）。

### 設定檔欄位

```json
{
  "name_template": "{site}/{author}/{title}.{ext}",
  "site_templates": {
    "iwara": "{author}/{date:%Y-%m}/{title}.{ext}",
    "bilibili": "bilibili/{author}/{title} [{id}].{ext}"
  },
  "type_templates": {
    "image": "{site}/images/{title}.{ext}"
  },
  "name_conflict": "rename",
  "rules": []
}
```

優先序：CLI `--name-template` → 規則覆寫 → `site_templates[site]` → `type_templates[type]` → `name_template`。

## 分類規則

依 **priority**（數字越小越先）排序；同分再依設定檔順序。命中第一個啟用規則即生效。

層級與 [william563214/download_classify](https://github.com/william563214/download_classify) 對齊，方便與未來瀏覽器擴充共用 JSON：

| 層級 | 設定鍵 | 預設 priority |
|------|--------|---------------|
| 網站規則 | `site_rules` | 10 |
| 自訂規則 | `rules` | 50+ |
| 副檔名對照 | `extension_rules` | 900+ |

下載路徑規劃（`plan_file_destinations`）與 ZIP 解壓後（`after_extract_classify_hook`）都會套用同一套有效規則。

### 比對欄位（`match`）

- `site`／`sites`（udl 站點 id，如 iwara）
- `author`（精確、不分大小寫）／`author_regex`
- `tags_any`／`tags_all`
- `title_regex`／`collection_regex`
- `duration_min`／`duration_max`（秒）
- `size_min`／`size_max`（位元組）
- `media_type`／`media_types`（video／audio／image／archive／document／program／other）
- `extensions`（副檔名，不含點）
- `mime_types`
- `hosts`／`source_hosts`／`source_page`／`url_patterns`／`filename`（download_classify 式；同規則內多組為 **OR**）
- `match_target`：`source`／`download`／`either`（網站規則用）

### 動作（`action`）

| type | 說明 |
|------|------|
| `folder` | 放到指定相對資料夾（可同時帶 `template`） |
| `template` | 覆寫命名模板（可同時帶 `folder`） |
| `folder_and_template` | 兩者皆套用 |
| `skip` | 略過此檔（寫入歷史 status=`skipped`） |

範例（自訂 + 網站 + 副檔名）：

```json
{
  "rules": [
    {
      "id": "iwara-long",
      "name": "Iwara 長片",
      "enabled": true,
      "priority": 40,
      "match": {
        "site": "iwara",
        "duration_min": 600,
        "media_type": "video"
      },
      "action": {
        "type": "folder_and_template",
        "folder": "Iwara/Long",
        "template": "{author}/{title}.{ext}"
      }
    }
  ],
  "site_rules": [
    {
      "id": "site-iwara",
      "name": "Iwara",
      "host": "iwara.tv",
      "target_folder": "Sites/iwara.tv",
      "match_target": "either",
      "priority": 10,
      "enabled": true
    }
  ],
  "extension_rules": [
    { "name": "影片", "extension": "mp4", "target_folder": "Media/Video" },
    { "name": "壓縮檔", "extension": "zip", "target_folder": "Archives" }
  ]
}
```

download_classify 匯出／匯入 JSON 僅含 `rules`／`site_rules`／`extension_rules`（自訂規則用 `matchers` + `target_folder`）。

### CLI

```bash
udl rules list --json
udl rules test "https://www.iwara.tv/video/…" --json
udl rules test --metadata '{"site":"bunkr","title":"x","tags":["a"]}' --name demo.mp4 --json
udl config save --data '{"name_template":"…","rules":[…],"site_rules":[…],"extension_rules":[…]}'
```

GUI 設定頁可編輯三層規則（新增／排序／刪除）、匯入／匯出 download_classify JSON、載入預設副檔名表，並以 URL 測試預覽目標路徑。

## 修復中心

比對 SQLite 歷史與檔案系統：

| 問題 | 說明 | 建議動作 |
|------|------|----------|
| `missing_file` | DB 有路徑但檔案不存在 | requeue／relink／remove-stale |
| `partial` | `.part` 殘留 | cleanup-partials／requeue |
| `size_mismatch` | 大小與歷史不符 | requeue／remove-stale |
| `hash_mismatch` | `--verify-hash` 時 sha256 不符 | requeue／remove-stale |
| `orphan_file` | 歷史 output 目錄內、符合已知副檔名且不在忽略清單、但 DB 無紀錄 | relink |

orphan 掃描僅限歷史 job 的 `output_dir`（或 `--dir`），且只考慮常見媒體／sidecar 副檔名；預設忽略 `.DS_Store`、`Thumbs.db`、`desktop.ini`、`folder.jpg` 等。
| `stale_record` | 歷史列缺路徑等 | remove-stale |

```bash
udl repair scan --json
udl repair scan --dir ~/Downloads/udl --verify-hash --json
udl repair fix requeue --job-id <id> --json
udl repair fix relink --file-row-id 12 --relink-path /path/to/file --json
udl repair fix remove-stale --file-row-id 12 --json
udl repair fix cleanup-partials --issues '[…]' --json
```

GUI「修復」頁可掃描並對選取項目執行上述動作。
