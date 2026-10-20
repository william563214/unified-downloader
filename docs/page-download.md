# 頁面擷取（Download All）— v0.9-D

貼上網頁網址後，核心以共用 HTTP 引擎（代理／UA）抓取 HTML，列出可下載候選，供 CLI 或 GUI 勾選入佇列。

**不做：** 瀏覽器擴充功能、DRM、加密 HLS key 擷取。僅未加密直連媒體與既有支援站點。

## CLI

```bash
udl page URL --json
udl page URL --ext mp4,mkv --type video,site --same-domain --size-min 1M
udl page URL --images --regex 'episode' --glob '*.mp4|*.mkv'
udl page URL --no-head   # 略過 HEAD 大小探測
```

輸出（`--json` 為 JSON-lines `page` 事件）：

```json
{
  "ev": "page",
  "url": "https://…",
  "title": "…",
  "count": 2,
  "candidates": [
    {
      "url": "https://…/a.mp4",
      "name": "a.mp4",
      "source": "a",
      "kind": "video",
      "extension": "mp4",
      "size": 123456,
      "site": null,
      "site_label": null
    }
  ]
}
```

## 候選來源

| source | 說明 |
|--------|------|
| `a` | `<a href>` 且副檔名像檔案，或命中站點 extractor |
| `video` / `audio` / `source` | 媒體標籤 |
| `img` | `<img>`（需 `--images`／GUI「包含圖片」） |

站點比對沿用 router：Gofile／Bunkr／Iwara／Bilibili／MEGA（及 v0.9-A 通用直連）。

## 篩選

- `--ext`：副檔名
- `--type`：`video`／`audio`／`image`／`archive`／`document`／`file`／`site`
- `--size-min`／`--size-max`：經 HEAD `Content-Length`（可 `--no-head`）
- `--regex`／`--glob`：比對 URL／檔名／連結文字
- `--same-domain`：僅頁面主機

## GUI

導覽「頁面」：貼網址 → 掃描 → 核取清單（全選／依類型）→ 加入佇列。直連下載依賴 v0.9-A 通用 extractor。

## 相關

- 校驗／掃毒見 `docs/checksum-hook.md`
- 事件協定見 `docs/events.md`
