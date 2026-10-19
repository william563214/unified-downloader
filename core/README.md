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
```

詳見倉庫根目錄 `README.md`。
