# Bilibili（v0.7）

對齊 [ScottSloan/Bili23-Downloader](https://github.com/ScottSloan/Bili23-Downloader)（GPL-3.0）。  
上游 commit 見根目錄 `NOTICE`。

**v0.7-A**：公開／匿名解析與下載。  
**v0.7-B**：QR／Cookie 登入（簡訊為 best-effort）、帳號清單、登入後畫質、CDN／Host。

明確**不做**：VIP／付費／區域鎖繞過；只下載 API 回傳給目前 session 的流。

## 登入與 session

| 方式 | 狀態 | 說明 |
|------|------|------|
| QR 掃碼 | 完整 | `udl bili login qr` → `qr-poll --key`；GUI「帳號」頁顯示 QR |
| Cookie 貼上 | 完整 | 需含 `SESSDATA`；可分號／換行／JSON |
| 簡訊 SMS | best-effort | GeeTest 需瀏覽器；`sms-captcha` → 外部解驗證 → `sms-send`／`sms` |
| 一次性 `--sessdata` | 完整 | 僅記憶體，不寫入 keychain |

Session 存於 OS keychain 鍵 `bilibili.session`（`udl.secrets`；無 keyring 時 0600 檔案後備）。**憑證從不寫入日誌。**

```bash
uv run udl bili login status --json
uv run udl bili login refresh
uv run udl bili login logout
```

登入後 nav 顯示使用者名稱、mid、等級、大會員標籤（GUI 帳號頁）。

## 支援的解析類型

| 類型 | URL 例 | 備註 |
|------|--------|------|
| 普通影片（多 P） | `/video/BVxxxx`、`av`、`b23.tv` | 含互動影片 graph 節點展開 |
| 番劇／電影 | `ep`／`ss`／`md` | 僅目前 session 可取得的集 |
| 課程 cheese | `/cheese/play/ep`／`ss` | 同上，不繞過付費 |
| 音訊 au／歌單 am | `/audio/au`／`am` | 輸出 m4a |
| UP 空間 | `space.bilibili.com/{mid}/video` | 可 `?keyword=`；`--page` |
| 公開收藏夾 | `favlist?fid=`／`ml{id}` | 可 keyword |
| 每周必看 | `/v/popular/weekly?num=` | |
| 合集／系列 | `space/.../lists/{id}?type=season\|series` | |
| 稍後再看 | `/watchlater` | **需登入**；keyword=`key` |
| 觀看歷史 | `/account/history` | **需登入**；可 keyword |
| 追番／追劇 | `bangumi/list`／`pgc/mine` | **需登入** |
| 訂閱合集 | space `/lists` + `type=collect` | **需登入** |
| 自己的收藏夾目錄 | 無 `fid` 的 favlist 索引 | **需登入**；有 `fid` 仍可匿名 |

未登入呼叫帳號清單 → `requires_login`。

## 畫質／編碼／音訊

- 畫質列表來自 playurl DASH（必要時參考 `support_formats`，但**不高於** session 實際拿到的最高 qn）
- 登入後若帳號有權限，API 可能回傳 8K／4K／HDR／Dolby Vision／Hi-Res／Dolby Atmos；**不會**偽造或繞過大會員／付費牆
- `--codec avc|hevc|av1`（或 7／12／13）
- `--audio-quality`：30251 Hi-Res／30250 Dolby／30280／30232／30216（僅 API 有提供時）

## CDN／UA／Host

與全域網路設定共用（見 `docs/networking.md`）：

| 設定 | CLI | 說明 |
|------|-----|------|
| User-Agent | `--user-agent` | API 與下載 |
| 偏好 CDN 主機 | `--cdn-hosts a,b` | 改寫 playurl netloc |
| 優先偏好 CDN | `--prefer-cdn` | 置換節點排在原始 URL 前 |
| 自訂 Host | `--custom-host` | 媒體下載額外 Host 標頭 |

劣質 mcdn／pcdn 節點會過濾；探測失敗的主機會短暫降權。

## 批次與自動勾選

- 多連結：CLI `udl probe URL1 URL2 …`；GUI「Bilibili」頁可多行貼上
- UP／收藏夾／稍後再看／歷史關鍵字：`--keyword` 或 URL `?keyword=`
- 條件篩選：`--filter-title`、`--filter-date-from/to`、`--filter-duration-min/max`、`--part-range 1-5`

## 彈幕／字幕／章節／封裝

| 選項 | CLI |
|------|-----|
| 彈幕 xml／ass／json | `--danmaku` |
| 字幕 srt／lrc／txt／ass／json | `--subtitle`、`--subtitle-lang` |
| 嵌入 ASS | `--embed-danmaku`、`--embed-subtitle` |
| 章節（ffmetadata） | 預設開；`--no-chapters` 關閉 |
| 封裝 | `--container mp4\|mkv` |

彈幕目前走經典 XML comment API（protobuf 分段彈幕仍屬 follow-up）。

## GUI

- **Bilibili**：貼連結 → 解析樹 → 畫質／編碼／彈幕字幕 → 佇列
- **帳號**：QR 顯示與輪詢、Cookie 貼上、狀態（使用者／VIP）、登出；簡訊標為實驗／best-effort
- **設定 → 網路**：UA、CDN 主機、prefer CDN、自訂 Host

## 統一 metadata／命名

- 各類型填入 `item_metadata`；登入清單有對應 type template
- 與 NFO／封面／規則引擎／去重共用 v0.5 基礎設施
