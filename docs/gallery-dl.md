# gallery-dl 備援（外部子行程）

本專案為 **GPL-3.0-or-later**。gallery-dl 為 **GPL-2.0-only**，**不得** `import gallery_dl` 或 vendor 其原始碼進 `udl` 行程。

整合方式：呼叫獨立可執行檔（或 `python -m gallery_dl`），經 CLI／JSON 溝通（mere aggregation）。詳見根目錄 `NOTICE` 與 `packaging/gallery-dl/`。

## 優先級

Router 順序：Gofile → Bunkr → Iwara → Bilibili → MEGA → **gallerydl** → Direct。

`matches()` 使用 `gallery-dl -E URL`（gallery-dl 自身辨識）。停用、找不到工具、或不支援的 URL 會落到 Direct（或 unsupported）。

## CLI

```bash
udl gallerydl status --json
udl gallerydl sites --json
udl gallerydl sites --extractors --json

udl resolve 'https://…'
udl get 'https://…' -o ./out --json \
  --gallerydl-tool /path/to/gallery-dl \
  --gallerydl-cookies ./cookies.txt \
  --gallerydl-option extractor.twitter.retweets=false \
  --gallerydl-archive

# 停用備援
udl get URL -o ./out --no-gallerydl
```

環境變數：`UDL_GALLERY_DL`、`UDL_GALLERY_DL_ENABLED`、`UDL_GALLERY_DL_COOKIES`、`UDL_GALLERY_DL_ARCHIVE`。

## 設定／登入

- GUI「設定 → 工具 · gallery-dl」：啟用、路徑、cookies、per-site `-o` 透傳、可選 archive。
- 帳密：`udl secrets set gallerydl.username`／`gallerydl.password`（可選 `gallerydl.username.<category>`）。**不會**寫入 log。
- 代理／全域限速：轉成 `--proxy`／`--limit-rate`（與 `udl.network` 一致）。
- Cloudflare／付費牆／DRM：**不**做繞過。

## 發行打包

Release workflow 以 PyInstaller 凍結獨立 `gallery-dl-<target-triple>`，透過 Tauri `externalBin` 與 `udl` 一併打包，並附上 `LICENSE.gallery-dl`／`SOURCE.txt`。

若本機未打包：

```bash
pip install gallery-dl
# 或
brew install gallery-dl
```

然後在設定填路徑，或確保 PATH 可找到 `gallery-dl`。

## 模組位置

| 路徑 | 說明 |
|------|------|
| `udl.extractors.external.gallerydl` | 子行程 extractor |
| `scripts/bundle-gallery-dl.sh` | 凍結獨立程式 |
| `packaging/gallery-dl/` | 授權與來源 commit |
