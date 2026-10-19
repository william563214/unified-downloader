# Iwara 瀏覽／訂閱／帳號（v0.6）

對齊 Moeary/IwaraTool（MIT；上游 commit 見根目錄 `NOTICE`）。

- **v0.6-A**：公開搜尋／標籤／作者／播放清單／首頁、本機訂閱、篩選、預覽
- **v0.6-B**：帳號登入（email／password → token）、OS keychain、私有／好友可見片、Following 匯入＋批次同步

## 帳號（v0.6-B）

```bash
udl iwara login [--email E] [--password P] --json
udl iwara logout --json
udl iwara whoami --json
udl iwara import-following [--sync -o DIR] --json
```

- 登入後會把 **email／password／user token／access token** 寫入 `udl.secrets`（OS keychain；無後端時才用 mode-0600 檔案後備）。**不會**寫進 `settings.json` 或日誌。
- Access token 過期時會用 user token 呼叫 `POST /user/token` 刷新；user token 也失效則用保存的帳密重新登入。
- CLI／GUI 下載與 browse／search／preview／sync 在未傳 `--token` 時會自動帶上 keychain session，因此帳號可見的私有／好友影片與搜尋結果會反映登入態。
- Cloudflare bypass 仍為 opt-in（`--cloudflare-bypass`／設定），預設關閉。

## CLI（瀏覽／訂閱）

```bash
udl iwara search KEYWORD [--type videos|users|playlists] [--sort …] [--rating all|ecchi|general] --json
udl iwara tag TAG [--sort date|trending|popularity|views|likes] [--rating …] --json
udl iwara author USERNAME [--sort …] [--rating …] --json
udl iwara playlist PLAYLIST_ID --json
udl iwara home [--sort …] [--rating …] --json
udl iwara preview VIDEO_ID --json
udl iwara enqueue VIDEO_ID… -o DIR --json

udl iwara subscribe TARGET [--kind auto|author|playlist] --json
udl iwara subscriptions --json
udl iwara unsubscribe ID --json
udl iwara sync -o DIR --json
udl iwara new [--mark-seen] --json

udl iwara section-add NAME --kind home|search|tag|author|playlist [--value …] --json
udl iwara sections --json
udl iwara section-fetch ID --json
udl iwara section-remove ID --json
```

共用篩選旗標（browse／batch）：`--min-likes`、`--max-likes`、`--min-views`、`--max-views`、`--date-from`、`--date-to`、`--include-tags`、`--exclude-tags`、`--title`、`--title-mode all|any`。

結果預設會標註 `local_status`：`downloaded`／`queued`／`not_downloaded`（讀 SQLite history + 持久化 queue）。`--no-status` 可關閉。

## 訂閱同步

- 本機訂閱作者／播放清單（SQLite `iwara_subscriptions`）。
- `udl iwara import-following` 把帳號 Following 寫入同一張表；`--sync -o DIR` 可接著增量入佇列。
- `udl iwara sync -o DIR` 依 `last_seen_video_id` 增量抓新片，套用訂閱篩選後寫入 `iwara_subscription_items`，並可 enqueue。

## 修復中心（Iwara）

過期下載 URL 不需站點專用修補：對缺失／半成品／hash 不符的 history 列執行 `udl repair fix requeue`（GUI「修復」→ 重新入佇列）即可。佇列重跑時 extractor 會重新 resolve，並自動使用 keychain session（含私有片）。

## GUI

- **設定 → Iwara**：畫質、Cloudflare、帳號登入／登出／檢查工作階段
- **訂閱**：匯入 Following、匯入並同步、本機訂閱列表、新內容
- **Iwara／首頁**：瀏覽網格、SFW／NSFW、自訂區塊、HTML5 預覽

## 已知限制

- Agent／部分 VM 對 `api.iwara.tv` 可能收到 Cloudflare 403；登入／browse／私有片在此環境**未驗證**，本機有正常網路或代理時再驗證。
- 預覽依賴遠端 `view`／`download` URL；若被防盜鏈或 CF 擋下，播放器會失敗但不影響入佇列下載。
- 進階篩選（時長等）與預覽強化仍列於 ROADMAP 後續。
