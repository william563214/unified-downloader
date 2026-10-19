# Extractor 介面（給後續代理）

本文件說明如何為 `udl` 新增站點模組。v0.1 已實作 **Gofile**、**Bunkr**；**Iwara**、**Bilibili** 僅保留 registry 槽位。

## 設計原則

- 仿 gallery-dl「每站一個 extractor + URL pattern 集中註冊」的**概念**，**禁止**複製或 `import gallery-dl`（GPL-2.0-only，與本專案 GPL-3.0-or-later 不相容）。
- 共用下載邏輯放在 `udl.download.http`（續傳、重試、並行、取消、JSON-lines 進度）。
- Extractor 只負責：**辨識 URL → 解析遠端結構 → 產出直接下載連結清單**。
- 不要實作付費牆／配額／ToS 繞過（見研究報告與 README）。

## 必做步驟

1. 在 `core/udl/extractors/<site>.py` 實作：
   - `matches(url: str) -> bool`
   - `class <site>_extractor(extractor)`，設定 `site`、`label`，實作 `resolve()`
2. 在 `core/udl/router.py` 的 `registry` 把 `status` 從 `"stub"` 改成 `"implemented"`（或新增 entry）。
3. 更新根目錄 `NOTICE`：上游 repo URL、授權、**移植當下的 commit SHA**。
4. 更新 `ROADMAP.md` 勾選項目。

## 介面契約

```python
from udl.extractors.base import extractor, extract_result, extracted_file

class my_extractor(extractor):
    site = "mysite"       # registry / JSON 用的穩定 id
    label = "My Site"     # UI 徽章

    def resolve(self, url: str, *, password: str | None = None) -> extract_result:
        # 回傳 title + files（含直接下載 URL、檔名、相對目錄、選用 size/headers）
        ...
```

預設的 `extractor.download()` 已足夠：把 `extracted_file` 轉成 `download_item` 並呼叫共用引擎。若站點需要特殊傳輸（例如 Bilibili DASH + FFmpeg），可覆寫 `download()`，但仍須透過 `emitter` 發出相同事件。

### `extracted_file` 欄位

| 欄位 | 說明 |
|------|------|
| `url` | 可 GET 的直接連結（含簽章 query） |
| `name` | 安全檔名 |
| `relative_dir` | 相對輸出目錄的子路徑（相簿名等） |
| `size` | 已知大小（位元組），未知則 `None` |
| `headers` | 額外 request headers（例如 Bunkr Referer） |
| `meta` | 自由 metadata，不進事件協定 |

### `extract_result` 欄位

| 欄位 | 說明 |
|------|------|
| `site` | 必須等於 `self.site` |
| `title` | 任務標題（相簿／影片名） |
| `files` | 非空列表 |
| `kind` | 例如 `album` / `file` / `video` |
| `meta` | 自由 metadata |

## 槽位現況

| site | 檔案 | status | 上游 |
|------|------|--------|------|
| gofile | `gofile.py` | implemented | ltsdw/gofile-downloader GPL-3.0 |
| bunkr | `bunkr.py` | implemented | Lysagxra/BunkrDownloader GPL-3.0 |
| iwara | `iwara.py` | stub | Moeary/IwaraTool MIT |
| bilibili | `bilibili.py` | stub | ScottSloan/Bili23-Downloader GPL-3.0 |

## Iwara 移植提示（v0.2）

- 從 `app/core/api/` 抽出，**拆掉 PySide6 / QSettings 依賴**。
- 匿名公開影片優先；登入延後。
- `X-Version` salt 會輪替，失效時要有清楚錯誤。
- **不要**預設開啟 cloudscraper Cloudflare 挑戰繞過。

## Bilibili 移植提示（v0.3）

- 只取 BV/av 單一影片、匿名畫質、DASH + FFmpeg 合併子集。
- **不要**移植大會員／付費／區域繞過；cheese 僅已購買帳號。
- Qt worker／thread 模型不要搬，改用共用 `download` 或子行程 FFmpeg。

## 測試建議（本版暫略）

- Router pattern 單元測試
- Extractor 用錄製的 HTML／JSON fixture（CI 不打真實網站）
