# Extractor 介面（給後續代理）

本文件說明如何為 `udl` 新增站點模組。已實作 **Gofile**、**Bunkr**、**Iwara**、**Bilibili**、**MEGA（外部工具）**。

## 設計原則

- 仿 gallery-dl「每站一個 extractor + URL pattern 集中註冊」的**概念**，**禁止**複製或 `import gallery-dl`（GPL-2.0-only，與本專案 GPL-3.0-or-later 不相容）。
- 共用下載邏輯放在 `udl.download.http`（續傳、重試、並行、取消、限速、可選 aria2、JSON-lines 進度）。
- 代理／UA 經 `udl.network.apply_to_session`；機密經 `udl.secrets`（見 `docs/networking.md`）。
- 影音合併等後處理放在 `udl.postprocess`（目前 `ffmpeg.py`）。
- 外部 CLI（GPL-2.0-only 等）放在 `udl.extractors.external`，只以**子行程**呼叫，對應 `status="external"`。
- Extractor 只負責：**辨識 URL → 解析遠端結構 → 產出直接下載連結清單**（或覆寫 `download()` 做特殊傳輸）。
- 不要實作付費牆／配額／ToS／VIP／區域繞過（見研究報告與 README）。

## 必做步驟

1. 在 `core/udl/extractors/<site>.py`（或套件目錄）實作：
   - `matches(url: str) -> bool`
   - `class <site>_extractor(extractor)`，設定 `site`、`label`，實作 `resolve()`
2. 在 `core/udl/router.py` 的 `registry` 把 `status` 從 `"stub"` 改成 `"implemented"` 或 `"external"`（或新增 entry）。
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

預設的 `extractor.download()` 已足夠：把 `extracted_file` 轉成 `download_item` 並呼叫共用引擎。若站點需要特殊傳輸（例如 Bilibili DASH + FFmpeg），可覆寫 `download()`，但仍須透過 `emitter` 發出相同事件；合併階段請發 `merging`。

可選：實作 `probe(url) -> probe_info`（或相容物件）供 `udl probe`／GUI 選項使用。可選：實作 `configure(**kwargs)` 接收 CLI／GUI 旗標（見 Iwara）。

### `extracted_file` 欄位

| 欄位 | 說明 |
|------|------|
| `url` | 可 GET 的直接連結（含簽章 query）；特殊協定可用 placeholder |
| `name` | 安全檔名 |
| `relative_dir` | 相對輸出目錄的子路徑（相簿名等） |
| `size` | 已知大小（位元組），未知則 `None` |
| `headers` | 額外 request headers（例如 Bunkr Referer） |
| `meta` | 實作細節（不進通用歷史篩選） |
| `metadata` | 統一 `item_metadata`（見 `docs/metadata.md`） |

### `extract_result` 欄位

| 欄位 | 說明 |
|------|------|
| `site` | 必須等於 `self.site` |
| `title` | 任務標題（相簿／影片名） |
| `files` | 非空列表 |
| `kind` | 例如 `album` / `file` / `video` / `bangumi` |
| `meta` | 實作細節 |
| `metadata` | 整個 job 的統一 `item_metadata` |

`download()` 可接受 `skip_indexes`／`file_dest_overrides`（去重 skip／rename）；覆寫時請一併支援。

## 槽位現況

| site | 檔案 | status | 上游 |
|------|------|--------|------|
| gofile | `gofile.py` | implemented | ltsdw/gofile-downloader GPL-3.0 |
| bunkr | `bunkr.py` | implemented | Lysagxra/BunkrDownloader GPL-3.0 |
| iwara | `iwara.py` + `iwara_xversion.py` | implemented | Moeary/IwaraTool MIT |
| bilibili | `bilibili/` | implemented | ScottSloan/Bili23-Downloader GPL-3.0 |
| mega | `external/mega.py` | external | megatools（優先）／MEGAcmd `mega-get`（子行程） |

## MEGA（v0.4 外部工具）

- 繼承 `external_extractor`，覆寫 `download()` 跑 CLI，把進度轉成標準事件。
- 優先 megatools（可解析進度、無頭、跨平台）；找不到再試 `mega-get`。
- 缺少工具 → `mega_tool_missing`＋安裝指引；可用 `--mega-tool`／`UDL_MEGA_TOOL`。
- **不要**實作配額繞過或移植 MegaBasterd。

## Iwara（v0.2 已完成）

- 從 `app/core/api/` 抽出，**拆掉 PySide6 / QSettings 依賴**。
- 支援 `/video/`、`/playlist/`、`/profile|user/`；匿名公開內容。
- 畫質預設 Source，回退 540→360；CLI `--quality`／GUI 選單。
- `X-Version` 集中在 `iwara_xversion.py`；全部 salt 失敗 → `iwara_xversion_error`。
- Cloudflare 繞過需明確 `--cloudflare-bypass`（optional extra `iwara`）；預設關閉。
- 可選 `--token`（記憶體暫存，不寫入磁碟）。

## Bilibili（v0.3 已完成子集）

- 套件布局：`core/udl/extractors/bilibili/`（client / wbi / ids / quality / extractor）。
- FFmpeg：`udl/postprocess/ffmpeg.py`（可供其他 extractor 重用）。
- 已支援：BV/av、多分 P、b23、可匿名 bangumi／ep；匿名最佳畫質；DASH+FFmpeg。
- **不要**移植大會員／付費／區域繞過；cheese 僅已購買帳號（本版未做 cheese）。
- Qt worker／thread 模型不要搬。
- QR 登入可 stub，不要求本版完成。

## 測試建議（本版暫略）

- Router pattern 單元測試
- Extractor 用錄製的 HTML／JSON fixture（CI 不打真實網站）
