# JSON-lines 事件協定（v0.4）

`udl get URL -o DIR --json` 與 Tauri sidecar 透過 **stdout 每行一個 JSON 物件** 溝通。
人類可读訊息寫入 stderr；GUI 只解析 stdout。

查機器可讀 schema：`udl schema`。

## 通用欄位

| 欄位 | 型別 | 說明 |
|------|------|------|
| `ev` | string | 事件種類 |

## 事件一覽

### `resolve`（`udl resolve URL --json`）

```json
{"ev":"resolve","site":"bilibili","kind":"video|bangumi","url":"https://…","supported":true,"message":null}
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
  "message": null
}
```

### `job`

一個下載任務開始（已解析出檔案清單後）。

```json
{"ev":"job","id":"<job-id>","site":"bilibili","title":"…","url":"https://…","files":1}
```

### `file`

即將下載的單一檔案。

```json
{"ev":"file","job":"<job-id>","id":"<job-id>:0","name":"a.mp4","total":null,"path":"/out/…","index":0}
```

### `progress`

下載進度（約每 0.25s 節流一次）。Bilibili DASH 影／音分軌下載時，`file` 可能帶 `:v`／`:a` 後綴。

```json
{"ev":"progress","job":"<job-id>","file":"<file-id>","bytes":4096,"total":12345,"bps":1048576.0}
```

### `merging`（v0.3）

後處理合併階段（例如 FFmpeg 合併 DASH 影＋音）。GUI 可顯示「合併中」。

```json
{"ev":"merging","job":"<job-id>","file":"<file-id>","path":"/out/a.mp4","message":"merging DASH video+audio with FFmpeg"}
```

### `done`

單一檔案完成，或整個 job 結束。

檔案完成：

```json
{"ev":"done","job":"<job-id>","file":"<file-id>","path":"/out/a.mp4"}
```

Job 結束：

```json
{"ev":"done","job":"<job-id>","path":"/out","ok":1,"failed":0}
```

### `error`

```json
{"ev":"error","job":"<job-id>","file":"<file-id>","message":"…","code":"download_failed"}
```

常見 `code`：`unsupported`、`resolve_failed`、`download_failed`、`ffmpeg_missing`、`merge_failed`、`mega_tool_missing`。

### `cancel`

```json
{"ev":"cancel","job":"<job-id>"}
```

## GUI 注意

- 一行一個 JSON；忽略空行與非 JSON 行（寫入日誌即可）
- 取消：對 sidecar 行程送 SIGTERM／結束 stdin；核心監聽 SIGINT／SIGTERM 設 cancel flag
- 結束碼：`0` 全成功、`1` 有失敗、`2` 不支援、`130` 取消
- Bilibili：先 `resolve` 再 `probe`，把選中的 `--quality`／`--part` 傳給 `get`
