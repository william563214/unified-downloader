# 後處理串接（v0.10）

下載完成後，NFO、封面、校驗、防毒、ZIP 解壓、搬移、exec 以**可設定順序**的 postprocessor 鏈執行。設計參考 [yt-dlp `yt_dlp/postprocessor`](https://github.com/yt-dlp/yt-dlp/tree/8973412/yt_dlp/postprocessor)（Unlicense，commit `8973412`；見 `NOTICE`）。

## 步驟

| 鍵 | 說明 | 預設啟用條件 |
|----|------|----------------|
| `checksum` | MD5／SHA-1／SHA-256 校驗 | 有預期摘要時 |
| `av` | 防毒／掃毒 hook（`--post-cmd`／preset） | hook 開啟時 |
| `extract` | 自動解壓 ZIP | `auto_extract_zip` 或 per-job on |
| `nfo` | 寫入 `.nfo` | `--write-nfo`／設定 |
| `cover` | 下載／嵌入封面 | `--write-cover`／`--embed-cover` |
| `move` | 依分類規則或 `move_template` 搬移 | 規則或模板有目標時 |
| `exec` | 一般外部指令（與 AV hook 分開） | `exec_enabled`＋指令 |

預設順序（與舊行為一致，校驗／掃毒通過後才解壓）：

```text
checksum,av,extract,nfo,cover,move,exec
```

硬失敗（checksum／av／extract／move／exec）會中止該檔後續步驟；NFO／封面失敗只記事件。

## CLI

```bash
udl get URL -o DIR --pp-order checksum,av,nfo,cover,extract,move,exec
udl get URL -o DIR --move-template '{site}/{type}/{title}.{ext}'
udl get URL -o DIR --exec-cmd 'echo done {path}' --exec-timeout 60
udl get URL -o DIR --outtmpl-na NA
```

## 設定（udl.config.json）

```json
{
  "pp_order": ["checksum", "av", "extract", "nfo", "cover", "move", "exec"],
  "move_template": "",
  "exec_enabled": false,
  "exec_cmd": "",
  "exec_timeout": 120,
  "outtmpl_na_placeholder": ""
}
```

GUI：設定 →「後處理串接」可上下調整順序、設定搬移模板與 Exec。

## 與 ZIP／分類整合

- `extract` 步驟呼叫既有 `udl.postprocess.zip_extract`
- 解壓成功後 `after_extract_classify_hook` 會依分類規則／`move_template` 搬移解出的檔案（#27 規則就緒時即生效）
- 鏈上的 `move` 步驟對下載本體再套用一次分類／搬移模板

## 事件

- `postprocess`：`step=chain|nfo|cover|move|exec|extracting|…`
- 既有 `checksum`／`hook`／`extracting`／`extracted` 不變
- 新增 `exec` 事件（與 `hook` 類似）
