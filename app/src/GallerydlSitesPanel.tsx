import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";

type GdlStatus = {
  found?: boolean;
  path?: string | null;
  kind?: string | null;
  version?: string | null;
  enabled?: boolean;
  message?: string;
};

type GdlModuleRow = { module?: string; category?: string; subcategory?: string; example?: string };

export function GallerydlSitesPanel() {
  const [status, setStatus] = useState<GdlStatus | null>(null);
  const [modules, setModules] = useState<GdlModuleRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshStatus() {
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<GdlStatus>("gallerydl_status");
      setStatus(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function loadModules() {
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<GdlModuleRow[]>("gallerydl_sites", {
        extractors: false,
      });
      setModules(Array.isArray(result) ? result : []);
    } catch (e) {
      setError(String(e));
      setModules(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="gallerydl-sites" aria-label="gallery-dl 狀態與站點">
      <div className="actions">
        <button type="button" className="ghost" disabled={busy} onClick={() => void refreshStatus()}>
          {busy ? "偵測中…" : "偵測 gallery-dl"}
        </button>
        <button type="button" className="ghost" disabled={busy} onClick={() => void loadModules()}>
          列出支援模組
        </button>
      </div>
      {status && (
        <p className="hint">
          {status.found
            ? `已找到：${status.path || "?"}（${status.kind || "?"}）version=${status.version || "?"}`
            : status.message || "未找到 gallery-dl"}
        </p>
      )}
      {error && <p className="hint error-text">{error}</p>}
      {modules && (
        <details open={modules.length > 0}>
          <summary>支援模組（{modules.length}）</summary>
          <ul className="gallerydl-module-list">
            {modules.slice(0, 200).map((row) => (
              <li key={row.module || row.category || JSON.stringify(row)}>
                {row.module ||
                  `${row.category || ""} / ${row.subcategory || ""} ${row.example || ""}`}
              </li>
            ))}
            {modules.length > 200 && <li>…其餘 {modules.length - 200} 項省略</li>}
          </ul>
        </details>
      )}
    </div>
  );
}
