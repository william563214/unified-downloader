# udl — 整合下載器核心

Python 套件 `udl`：URL 路由、站點 extractor、共用 HTTP 下載引擎、CLI。

```bash
cd core
uv sync
# optional Iwara Cloudflare bypass dependency
# uv sync --extra iwara
uv run udl resolve "https://gofile.io/d/XXXX"
uv run udl get "https://gofile.io/d/XXXX" -o ./out --json
uv run udl get "https://www.iwara.tv/video/XXXX" -o ./out --json --quality Source
uv run udl resolve "https://www.bilibili.com/video/BV…"
uv run udl probe "https://www.bilibili.com/video/BV…"
uv run udl get "https://www.bilibili.com/video/BV…" -o ./out --json
uv run udl get "https://mega.nz/file/…#…" -o ./out --json
uv run udl iwara search "keyword" --json
uv run udl iwara home --rating general --json
uv run udl iwara subscribe some_author --json
uv run udl iwara sync -o ./out --json
```

Bilibili 需要系統 FFmpeg（或 `--ffmpeg` / `UDL_FFMPEG`）。  
MEGA 需要本機 **megatools**（優先）或 MEGAcmd `mega-get`（`--mega-tool`／`UDL_MEGA_TOOL`）。  
Iwara 瀏覽／訂閱見根目錄 `docs/iwara-browse.md`。詳見倉庫根目錄 `README.md`。
