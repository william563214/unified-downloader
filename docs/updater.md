# 應用程式更新（Tauri updater）

整合下載器透過 `tauri-plugin-updater` 對接 **GitHub Releases** 的 `latest.json`：

```
https://github.com/william563214/unified-downloader/releases/latest/download/latest.json
```

## 預設行為：無公鑰則停用

Tauri 更新器**必須**驗證簽章公鑰，無法在生產環境接受完全未簽章產物。

因此本專案採：

1. `tauri.conf.json` 的 `plugins.updater.endpoints` 已指向上述 GitHub Releases。
2. `plugins.updater.pubkey` 預設為空字串。
3. 執行期僅在存在公鑰時註冊 updater plugin：
   - 環境變數 `TAURI_UPDATER_PUBKEY`（建置／執行時），或
   - 編譯期 `UDL_UPDATER_PUBKEY`
4. GUI「設定 → 應用程式更新」會顯示目前是否啟用；未啟用時請手動從 Releases 下載安裝包。

未簽章的 macOS／Windows 安裝包仍可手動安裝（見 README Gatekeeper／SmartScreen 說明）。

## 啟用自動更新（維護者）

```bash
# 在安全環境產生金鑰對（私鑰勿提交）
cd app
npx tauri signer generate -w ~/.tauri/unified-downloader.key
```

1. 將 `~/.tauri/unified-downloader.key.pub` 內容寫入 `tauri.conf.json` → `plugins.updater.pubkey`，或於 CI／本機 export `TAURI_UPDATER_PUBKEY`／`UDL_UPDATER_PUBKEY`。
2. Release 流程設定 secrets：
   - `TAURI_SIGNING_PRIVATE_KEY`
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`（若有）
3. 建置時開啟 updater artifacts（`bundle.createUpdaterArtifacts: true`）並上傳 `latest.json` 與簽章檔到 GitHub Release。

在公鑰就緒前，自動更新保持停用，避免誤導使用者。
