# Bilibili（v0.7-A）

對齊 [ScottSloan/Bili23-Downloader](https://github.com/ScottSloan/Bili23-Downloader)（GPL-3.0）的公開／匿名能力子集。  
上游 commit 見根目錄 `NOTICE`。**本版不含 QR／SMS／Cookie 登入**；帳號專屬清單回傳 `requires_login`。  
之後可經 `bilibili_client.set_session_cookies`／CLI `--sessdata` 插入 `SESSDATA`（設計預留給 `udl.secrets`）。

## 支援的解析類型

| 類型 | URL 例 | 備註 |
|------|--------|------|
| 普通影片（多 P） | `/video/BVxxxx`、`av`、`b23.tv` | 含互動影片 graph 節點展開 |
| 番劇／電影 | `ep`／`ss`／`md` | 僅目前 session 可取得的集；拒絕會員／付費 badge |
| 課程 cheese | `/cheese/play/ep`／`ss` | 同上，不繞過付費 |
| 音訊 au／歌單 am | `/audio/au`／`am` | 輸出 m4a |
| UP 空間 | `space.bilibili.com/{mid}/video` | 可 `?keyword=` 伺服器搜尋；分頁 `--page` |
| 公開收藏夾 | `favlist?fid=`／`ml{id}` | 可 keyword；**自己的收藏夾列表**需登入 |
| 每周必看 | `/v/popular/weekly?num=` | |
| 合集／系列 | `space/.../lists/{id}?type=season\|series`、`/list/{mid}?sid=` | |
| 稍後再看／歷史／追番／訂閱合集 | — | **stub → `requires_login`** |

明確**不做**：VIP／付費／區域鎖繞過；只下載 API 回傳給目前 session 的流。

## 批次與自動勾選

- 多連結：CLI `udl probe URL1 URL2 …`；GUI「Bilibili」頁可多行貼上
- UP／收藏夾關鍵字：`--keyword` 或 URL `?keyword=`
- 條件篩選：`--filter-title`、`--filter-date-from/to`、`--filter-duration-min/max`、`--part-range 1-5`

## 畫質／編碼／音訊

- 畫質列表來自 playurl DASH（必要時參考 `support_formats`，但**不高於** session 實際拿到的最高 qn）
- `--codec avc|hevc|av1`（或 7／12／13）
- `--audio-quality`：30251 Hi-Res／30250 Dolby／30280／30232／30216（僅 API 有提供時）

## 彈幕／字幕／章節／封裝

| 選項 | CLI |
|------|-----|
| 彈幕 xml／ass／json | `--danmaku` |
| 字幕 srt／lrc／txt／ass／json | `--subtitle`、`--subtitle-lang` |
| 嵌入 ASS | `--embed-danmaku`、`--embed-subtitle` |
| 章節（ffmetadata） | 預設開；`--no-chapters` 關閉 |
| 封裝 | `--container mp4\|mkv` |

彈幕目前走經典 XML comment API（不依賴 protobuf）。Agent／CI 環境若遇區域網路限制，遠端 API 可能失敗——以本機可連 B 站的環境驗證為準。

## 統一 metadata／命名

- 各類型填入 `item_metadata`（author、pubdate、tags、cover、collection…）
- 預設 type template 見 `udl.extractors.bilibili.templates`（`bilibili.video`、`bilibili.bangumi`…）
- 與 NFO／封面／規則引擎／去重共用 v0.5 基礎設施

## CDN／UA／Host

- **自訂 User-Agent**：與 PR #6 網路設定共用；本 PR 不另開通道
- **CDN 節點選擇／自訂 Host**：TODO — 等 PR #6（限速／代理／aria2／UA）合併後再接，避免 session／header 衝突

## GUI

導覽「Bilibili」：貼連結 → 解析樹（checkbox）→ 畫質／編碼／彈幕字幕選項 → 加入佇列。

## 登入後續（非本 PR）

- QR／SMS／Cookie → OS keychain via `udl.secrets`
- 帳號清單：稍後再看、歷史、追番、訂閱合集、自己的收藏夾目錄
- 插口已預留：`set_session_cookies`／`--sessdata`
