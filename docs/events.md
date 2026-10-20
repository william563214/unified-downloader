# JSON-lines 事件協定（v0.5）

`udl get URL -o DIR --json` 與 Tauri sidecar 透過 **stdout 每行一個 JSON 物件** 溝通。
人類可读訊息寫入 stderr；GUI 只解析 stdout。

查機器可讀 schema：`udl schema`。

統一 metadata 欄位見 `docs/metadata.md`；事件可用選用物件 `metadata` 攜帶。

## 通用欄位

| 欄位 | 型別 | 說明 |
|------|------|------|
| `ev` | string | 事件種類 |

## 事件一覽

### `resolve`（`udl resolve URL --json`）

```json
{"ev":"resolve","site":"bilibili","kind":"video|bangumi","url":"https://…","supported":true,"message":null}
```

### `expand`（`udl probe URL --json --expand-all`）

Bilibili 清單翻頁／巢狀展開進度（在最終 `probe` 之前可能多筆）。

```json
{"ev":"expand","phase":"space","current":2,"total":5,"message":"UP space page 2/5"}
```

### `probe`（`udl probe URL --json`）

不下載，只取中繼資料（供 GUI 分 P／畫質選擇）。

```json
{
  "ev": "probe",
  "site": "bilibili",
  "kind": "video",
  "title": "…",
  "url": "https://…",
  "parts": [{"page": 1, "cid": 123, "title": "P1", "bvid": "BV…"}],
  "qualities": [{"qn": 32, "label": "480P"}],
  "default_part": 1,
  "default_quality": 32,
  "message": null,
  "metadata": {"site":"bilibili","item_id":"BV…","title":"…","author":"…"}
}
```

### `job`

一個下載任務開始（已解析出檔案清單、套用去重策略後）。

```json
{"ev":"job","id":"<job-id>","site":"bilibili","title":"…","url":"https://…","files":1,"metadata":{…},"already_downloaded":false}
```

### `file`

即將下載的單一檔案，或後處理產出的 sidecar（NFO／封面）。

```json
{"ev":"file","job":"<job-id>","id":"<job-id>:0","name":"a.mp4","total":null,"path":"/out/…","index":0,"metadata":{…},"already_downloaded":false}
```

後處理產物會帶 `role`：

```json
{"ev":"file","job":"<job-id>","id":"<job-id>:0:nfo","name":"a.nfo","path":"/out/a.nfo","role":"nfo","metadata":{…}}
{"ev":"file","job":"<job-id>","id":"<job-id>:0:cover","name":"a-poster.jpg","path":"/out/a-poster.jpg","role":"cover","metadata":{…}}
```

### `duplicate`（v0.5）

歷史或本機路徑判定為重複時發出（策略：skip／overwrite／rename）。

```json
{
  "ev": "duplicate",
  "job": "<job-id>",
  "file": "<file-id>",
  "action": "skip",
  "already_downloaded": true,
  "message": "already downloaded",
  "path": "/out/a.mp4",
  "matched_by": "site_item_id",
  "metadata": {…}
}
```

### `progress`

下載進度（約每 0.25s 節流一次）。Bilibili DASH 影／音分軌下載時，`file` 可能帶 `:v`／`:a` 後綴。

```json
{"ev":"progress","job":"<job-id>","file":"<file-id>","bytes":4096,"total":12345,"bps":1048576.0}
```

多連線分段（v0.9-A）時可附帶各區段資訊：

```json
{
  "ev": "progress",
  "job": "<job-id>",
  "file": "<file-id>",
  "bytes": 1048576,
  "total": 10485760,
  "bps": 2097152.0,
  "connections": 4,
  "segments": [
    {"id": 0, "start": 0, "end": 2621439, "current": 2621440, "downloaded": 2621440, "total": 2621440, "done": true},
    {"id": 1, "start": 2621440, "end": 5242879, "current": 3145728, "downloaded": 524288, "total": 2621440, "done": false}
  ]
}
```

### `merging`

後處理合併階段（例如 FFmpeg 合併 DASH 影＋音）。GUI 可顯示「合併中」。

```json
{"ev":"merging","job":"<job-id>","file":"<file-id>","path":"/out/a.mp4","message":"merging DASH video+audio with FFmpeg"}
```

### `postprocess`（v0.5-D）

NFO／封面下載／嵌入等下載後步驟。成功或失敗都會發出；失敗另有 `error`。

```json
{"ev":"postprocess","job":"<job-id>","file":"<job-id>:0","step":"nfo","path":"/out/a.nfo","role":"nfo","message":"nfo written","ok":true}
{"ev":"postprocess","job":"<job-id>","file":"<job-id>:0","step":"cover","path":"/out/a-poster.jpg","role":"cover","message":"cover saved","ok":true}
{"ev":"postprocess","job":"<job-id>","file":"<job-id>:0","step":"embed_cover","path":"/out/a.mp4","role":"cover","message":"cover embedded","ok":true}
```

`step`：`nfo`／`cover`／`embed_cover`／`skip`。

### `done`

單一檔案完成，或整個 job 結束。

檔案完成：

```json
{"ev":"done","job":"<job-id>","file":"<file-id>","path":"/out/a.mp4","skipped":false,"already_downloaded":false,"metadata":{…},"sha256":null,"size":null}
```

Job 結束：

```json
{"ev":"done","job":"<job-id>","path":"/out","ok":1,"failed":0,"metadata":{…}}
```

### `error`

```json
{"ev":"error","job":"<job-id>","file":"<file-id>","message":"…","code":"download_failed"}
```

常見 `code`：`unsupported`、`resolve_failed`、`download_failed`、`ffmpeg_missing`、`merge_failed`、`mega_tool_missing`、`nfo_failed`、`cover_failed`、`postprocess_failed`。

### `cancel`

```json
{"ev":"cancel","job":"<job-id>"}
```

## GUI 注意

- 一行一個 JSON；忽略空行與非 JSON 行（寫入日誌即可）
- 取消：對 sidecar 行程送 SIGTERM／結束 stdin；核心監聽 SIGINT／SIGTERM 設 cancel flag
- 結束碼：`0` 全成功、`1` 有失敗、`2` 不支援、`130` 取消
- Bilibili：先 `resolve` 再 `probe`，把選中的 `--quality`／`--part` 傳給 `get`
- 歷史／佇列：`udl history …`、`udl queue …`（SQLite 在 app data dir）
- 去重：`udl get … --on-duplicate skip|overwrite|rename`
