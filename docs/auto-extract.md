# 下載後自動解壓 ZIP

預設**關閉**。下載完成且校驗／掃毒 hook（若有設定）通過後，可將 ZIP 解壓到同目錄旁資料夾。

僅使用 Python 標準庫 `zipfile`（不新增依賴）。加密成員僅支援 stdlib 能解的 ZipCrypto／（若環境支援）AES；不破解、不做字典攻擊。

## 偵測

- 檔名以 `.zip` 結尾，或
- 檔頭 magic bytes 為 `PK\x03\x04`

## CLI

```bash
udl get URL -o DIR --auto-extract --json
udl get URL -o DIR --no-auto-extract
udl get URL -o DIR --auto-extract --extract-to subfolder   # 預設：archive 同名子資料夾
udl get URL -o DIR --auto-extract --extract-to same_folder
udl get URL -o DIR --auto-extract --extract-delete-zip     # 成功後刪原 ZIP（預設關）
udl get URL -o DIR --auto-extract --extract-max-uncompressed 4294967296 --extract-max-ratio 100
udl get URL -o DIR --auto-extract --extract-password-mode list
udl get URL -o DIR --auto-extract --extract-password-mode single --extract-password '…'
udl get URL -o DIR --auto-extract --extract-password-mode off
udl get URL -o DIR --auto-extract --no-extract-promote-password
```

### 有序密碼表（僅 udl.secrets）

```bash
udl zip-passwords list --json          # 不輸出明文
udl zip-passwords add 'secret' --label home
udl zip-passwords set <id> --password 'new' --label work
udl zip-passwords remove <id>
udl zip-passwords reorder id1,id2,id3
udl zip-passwords clear
```

修復中心重試：

```bash
udl repair fix retry-extract --issues '[…]' --extract-password '…' \
  --add-password-to-list --password-label home --json
```

## 設定（udl.config.json）

```json
{
  "auto_extract_zip": false,
  "extract_to": "subfolder",
  "extract_delete_zip": false,
  "extract_max_uncompressed": 4294967296,
  "extract_max_ratio": 100.0,
  "extract_promote_password": true
}
```

密碼**從不**寫入此檔；只存在 `udl.secrets` 鍵 `zip.passwords`（舊鍵 `zip.password` 會遷移進清單）。

## 安全

| 防護 | 行為 |
|------|------|
| Zip-slip | 拒絕 `..`、絕對路徑、跳脫目標目錄 |
| Symlink | 拒絕 Unix symlink 成員 |
| Zip bomb | 未壓縮總大小與壓縮比上限（可設定） |
| 加密 ZIP | 依有序密碼表嘗試；上限＝清單長度；失敗 `needs_password` |

歷史／事件僅可記 `extracted with password N of M`（或同等訊息），**永不**記錄明文密碼。

## 檔名編碼

1. ZIP UTF-8 flag（bit 11）→ UTF-8  
2. 否則以 cp437 還原位元組，再嘗試 utf-8／cp932／gbk（日／中文檔名常見）

## 事件

見 `docs/events.md`：`extracting`／`extracted`／`failed`（`phase=extract`）。失敗亦發 `error`，並寫入歷史 `extra.extract`；修復中心可 `retry-extract`。

## 分類規則 hook（#27）

解壓成功後呼叫 `after_extract_classify_hook`。若 #27 尚未實作，此 hook 為 no-op 預留。

## GUI

- 設定 →「自動解壓 ZIP」：全域開關、位置、刪除原檔、bomb 上限、成功後置頂密碼  
- 設定 →「ZIP 密碼表」：新增／編輯／排序／刪除（密文只走 keychain）  
- 新增下載：解壓覆寫 inherit／on／off；密碼模式 list／single／off  
- 佇列顯示「解壓中」；失敗可開修復中心一次性密碼重試，並可選加入清單  
