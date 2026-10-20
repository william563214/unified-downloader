# v0.7.0 手動測試清單

William 手動驗證用。對應 GitHub issue：[v0.7.0 手動測試清單](https://github.com/william563214/unified-downloader/issues/15)。

本版為**未簽章** draft；安裝步驟含 Gatekeeper／SmartScreen 繞過說明。

---

## 安裝（未簽章）

### macOS arm64（.dmg）

- [ ] 下載並開啟 dmg，將 App 拖入 Applications
- [ ] 若 Gatekeeper 阻擋：對 App **按右鍵 → 打開**，或執行  
      `xattr -cr "/Applications/整合下載器.app"`（實際 App 名稱以安裝包為準）後再開啟
- [ ] App 可正常啟動，版本顯示為 0.7.0

### Windows x64（NSIS）

- [ ] 執行安裝程式
- [ ] 若 SmartScreen 出現：「More info」→「Run anyway」
- [ ] 安裝完成後可啟動，版本顯示為 0.7.0

---

## 基本下載（貼上網址）

對下列站點各貼至少一組公開 URL，確認解析、入隊、下載完成與歷史紀錄：

- [ ] Gofile
- [ ] Bunkr
- [ ] Iwara（公開片）
- [ ] Bilibili（匿名可看）
- [ ] MEGA（公開 file／folder）

---

## Bilibili

- [ ] QR 掃碼登入成功，帳號頁顯示使用者／VIP
- [ ] Cookie 貼上登入成功
- [ ] 登出／重新整理 session
- [ ] 帳號清單：稍後再看／歷史／追番／訂閱合集／自己的收藏夾
- [ ] 畫質選擇（含登入後較高畫質；不可繞過 VIP／付費）
- [ ] 彈幕匯出（xml／ass／json）
- [ ] 字幕匯出（srt／lrc／txt／ass／json）

---

## Iwara

- [ ] 登入（email／password；憑證進 OS keychain）
- [ ] 私有／好友可見片可下載
- [ ] 匯入 Following
- [ ] 訂閱同步入佇列（sync）
- [ ] 瀏覽／標籤搜尋結果顯示已下載徽章（downloaded／queued／not_downloaded）

---

## 歷史／佇列／去重／修復

- [ ] 歷史：搜尋、篩選（站點／狀態／日期）、刪除（可選刪檔）
- [ ] 去重：已下載項目以 skip 略過並顯示提示
- [ ] 佇列：App 重啟後未完成任務自動恢復／續傳
- [ ] 修復：scan 找出缺檔／`.part`／大小不符
- [ ] 修復：fix（requeue／relink／cleanup／remove-stale）行為正確
- [ ] orphan 掃描不會把輸出目錄內非本程式檔案大量誤報（僅已知副檔名＋忽略清單）

---

## 後處理／命名／網路

- [ ] NFO 寫入
- [ ] 封面：預設 per-file `*-poster.jpg`；若選 `folder.jpg`，同目錄多檔只寫一次
- [ ] 可選封面嵌入（mp4／mkv）
- [ ] 命名模板與分類規則生效（含衝突處理）
- [ ] 限速
- [ ] 代理三模式：none／system／custom
- [ ] aria2 JSON-RPC（可連則用；連不上回退內建）

---

## 外部工具

- [ ] 首次執行 FFmpeg 自動下載／校驗（checksum）後可用於合併
- [ ] MEGA：已安裝 megatools 或 MEGAcmd 時可下載；缺少工具時錯誤碼／安裝指引清楚
- [ ] MEGA 去重以 node handle＋size 判斷，不因粗粒度 handle 誤 skip

---

## 簽核

| 欄位 | 內容 |
|------|------|
| 測試者 | |
| 平台／建置 | |
| 日期 | |
| 結果摘要 | |
