# Iwara 瀏覽／訂閱（v0.6-A）

本版對齊 Moeary/IwaraTool 的**公開**瀏覽能力（MIT；上游 commit 見根目錄 `NOTICE`）。**不含帳號登入**、私有影片、匯入 following；那些會等到 `udl.secrets` 落地後另開 PR。

## CLI

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

- 僅本機訂閱作者／播放清單（SQLite `iwara_subscriptions`）。
- `udl iwara sync -o DIR` 依 `last_seen_video_id` 增量抓新片，套用訂閱上的篩選後寫入 `iwara_subscription_items`，並可 enqueue 到持久化佇列（GUI 重啟會恢復）。
- Cloudflare bypass 仍為 opt-in（`--cloudflare-bypass`／設定開關），預設關閉。

## GUI

- **Iwara**：搜尋／標籤／作者／播放清單網格、本地下載徽章、多選入佇列、HTML5 預覽（串流 URL）。
- **訂閱**：新增／移除、立即同步、新內容列表。
- **首頁**：推薦流、SFW／NSFW、自訂區塊（已存查詢）。

## 已知限制

- Agent／部分 VM 對 `api.iwara.tv` 可能收到 Cloudflare 403；本機有正常網路或代理時再驗證。
- 預覽依賴遠端 `view`／`download` URL；若被防盜鏈或 CF 擋下，播放器會失敗但不影響入佇列下載。
- 帳號登入、私有片、following 匯入 → 後續 PR。
