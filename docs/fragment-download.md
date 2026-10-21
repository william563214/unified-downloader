# 共用片段下載基底（v0.10）

HLS／DASH／HTTP 分段列表的共用下載層：續傳、重試、合併寫在同一基底；可接 v0.9-A 多連線分段與 aria2 RPC；外部 **aria2c**／**ffmpeg** 子行程走同一介面。

**不做：** DRM、加密 HLS key 擷取、Widevine／PlayReady／SAMPLE-AES 繞過。

參考 yt-dlp `yt_dlp/downloader/fragment.py`、`external.py`（Unlicense，commit `8973412`）；出處見 `NOTICE`。

## 模組

| 模組 | 說明 |
|------|------|
| `udl.download.fragment` | 片段列表下載、`.part.udl.frag.json` 續傳、m3u8 media playlist 解析（拒加密） |
| `udl.download.external` | `aria2c`／`ffmpeg` 子行程，與 native 同一 `download_fragments` 入口 |
| `udl.download.segmented` | v0.9-A 直連多連線分段（單檔 HTTP Range） |
| `udl.download.aria2` | 可選 aria2 **JSON-RPC**（整檔；與 aria2c 子行程不同） |

## 後端選擇

| 值 | 行為 |
|----|------|
| `native`（預設） | 每片段走共用 HTTP 引擎；可選分段；aria2 RPC 仍可接手整檔 |
| `aria2c` | 子行程 `aria2c`；多片段寫 `.frag.urls` 後合併 |
| `ffmpeg` | 子行程 `ffmpeg -c copy`（適合單一 m3u8／dash URL）；缺二進位則回退 native |

缺少外部二進位時自動回退 `native`。

CLI：

```bash
uv run udl get URL -o ./out --external-downloader native
uv run udl get URL -o ./out --external-downloader aria2c
uv run udl get URL -o ./out --concurrent-fragments 4
```

環境變數：`UDL_ARIA2C`（aria2c 路徑）；ffmpeg 仍用 `UDL_FFMPEG`／PATH／佈建目錄。

## API（extractor／呼叫端）

```python
from udl.download import (
    download_fragments,
    download_http_segmented_file,
    fragment_options_from_network,
    parse_m3u8_media_fragments,
)
from udl.download.fragment import fetch_text

# 未加密 HLS media playlist
text = fetch_text(variant_url)
frags = parse_m3u8_media_fragments(text, base_url=variant_url)
opts = fragment_options_from_network()
download_fragments(frags, dest, job_id=job, file_id=fid, opts=opts, emit=emit)

# 單檔 HTTP（v0.9-A 分段／aria2 RPC）
download_http_segmented_file(url, dest, job_id=job, file_id=fid, emit=emit)
```

進度事件可帶 `fragment_index`／`fragment_count`（見 `docs/events.md`）。

## 加密串流

playlist 或錯誤輸出若出現 `#EXT-X-KEY`、SAMPLE-AES、Widevine／PlayReady 等標記，會拋 `encrypted_stream`（`encrypted_stream_error`），**不會**下載或使用金鑰。
