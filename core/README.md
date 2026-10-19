# udl — 整合下載器核心

Python 套件 `udl`：URL 路由、站點 extractor、共用 HTTP 下載引擎、CLI。

```bash
cd core
uv sync
uv run udl resolve "https://www.bilibili.com/video/BV…"
uv run udl probe "https://www.bilibili.com/video/BV…"
uv run udl get "https://www.bilibili.com/video/BV…" -o ./out --json
```

Bilibili 需要系統 FFmpeg（或 `--ffmpeg` / `UDL_FFMPEG`）。詳見倉庫根目錄 `README.md`。
