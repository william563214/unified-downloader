# 校驗碼與下載後指令（v0.9-D）

## 校驗（MD5／SHA-1／SHA-256）

下載完成後可比對預期摘要。來源（優先序）：

1. CLI／工作參數：`--checksum algo:hex` 或 `--md5`／`--sha1`／`--sha256`
2. 站點 metadata（例如 Gofile `extra.md5`）
3. 旁檔：`file.sha256`／`file.md5`／`file.sha1`（GNU `hash  name` 格式亦可）

```bash
udl get URL -o DIR --sha256 deadbeef… --json
udl get URL -o DIR --checksum md5:… 
udl get URL -o DIR --no-checksum-auto   # 關閉自動 metadata／sidecar
```

成功／失敗皆發 `checksum` 事件；不符時：

- 檔案／工作標記失敗（`code=checksum_mismatch`）
- 寫入歷史（`extra.checksum`）
- 修復中心掃描可見 `checksum_mismatch`（亦可搭配 `--verify-hash` 的 `hash_mismatch`）

## 下載後指令／掃毒

預設**關閉**。可設自訂指令（`{path}` = 本機檔案）或預設集：

| preset | 指令 |
|--------|------|
| `defender` | `MpCmdRun.exe -Scan -ScanType 3 -File {path}` |
| `clamav` | `clamscan {path}` |

```bash
udl get URL -o DIR --post-cmd-preset clamav --post-cmd-timeout 120
udl get URL -o DIR --post-cmd 'clamscan --no-summary {path}'
```

GUI：設定 →「校驗與掃毒」。結果寫入 `hook`／`postprocess`（`step=hook`）事件與歷史 `extra.hook`；非零結束碼或逾時視為失敗。

## 設定（udl.config.json）

```json
{
  "checksum_auto": true,
  "post_cmd_enabled": false,
  "post_cmd": "",
  "post_cmd_preset": "",
  "post_cmd_timeout": 120
}
```
