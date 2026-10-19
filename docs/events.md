# JSON-lines 事件協定（v0.1）

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
{"ev":"resolve","site":"gofile","kind":"folder|file","url":"https://…","supported":true,"message":null}
```

### `job`

一個下載任務開始（已解析出檔案清單後）。

```json
{"ev":"job","id":"<job-id>","site":"gofile","title":"Album","url":"https://…","files":3}
```

### `file`

即將下載的單一檔案。

```json
{"ev":"file","job":"<job-id>","id":"<job-id>:0","name":"a.jpg","total":12345,"path":"/out/…","index":0}
```

### `progress`

下載進度（約每 0.25s 節流一次）。

```json
{"ev":"progress","job":"<job-id>","file":"<file-id>","bytes":4096,"total":12345,"bps":1048576.0}
```

- `bytes`：已下載位元組（含續傳起點）
- `total`：總大小（未知時省略）
- `bps`：目前速率（bytes/sec）

### `done`

單一檔案完成，或整個 job 結束。

檔案完成：

```json
{"ev":"done","job":"<job-id>","file":"<file-id>","path":"/out/a.jpg"}
```

Job 結束：

```json
{"ev":"done","job":"<job-id>","path":"/out","ok":3,"failed":0}
```

### `error`

```json
{"ev":"error","job":"<job-id>","file":"<file-id>","message":"…","code":"download_failed"}
```

常見 `code`：`unsupported`、`resolve_failed`、`download_failed`。

### `cancel`

```json
{"ev":"cancel","job":"<job-id>"}
```

## GUI 注意

- 一行一個 JSON；忽略空行與非 JSON 行（寫入日誌即可）
- 取消：對 sidecar 行程送 SIGTERM／結束 stdin；核心監聽 SIGINT／SIGTERM 設 cancel flag
- 結束碼：`0` 全成功、`1` 有失敗、`2` 不支援、`130` 取消
