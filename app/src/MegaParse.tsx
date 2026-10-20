import { useCallback, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import type { AppSettings } from "./settings";

export type MegaTreeNode = {
  handle: string;
  parent?: string | null;
  name: string;
  kind: "file" | "folder";
  size?: number | null;
  path?: string;
};

export type MegaProbe = {
  site: string | null;
  kind: string | null;
  title?: string | null;
  name?: string | null;
  url: string;
  backend?: string;
  tree?: MegaTreeNode[];
  files?: Array<{
    index: number;
    name: string;
    size?: number | null;
    path?: string;
    handle?: string;
  }>;
  message?: string | null;
  error?: string;
  code?: string;
  email?: string | null;
};

type Props = {
  settings: AppSettings;
  output: string;
  onEnqueue: (job: {
    url: string;
    selectedHandles: string[];
  }) => void;
};

function formatSize(n?: number | null): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KiB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MiB`;
  return `${(n / 1024 ** 3).toFixed(2)} GiB`;
}

export default function MegaParse({ settings, output, onEnqueue }: Props) {
  const [paste, setPaste] = useState("");
  const [probe, setProbe] = useState<MegaProbe | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewName, setPreviewName] = useState<string | null>(null);

  const tree = probe?.tree || [];
  const files = useMemo(
    () => tree.filter((n) => n.kind === "file"),
    [tree],
  );

  const runProbe = useCallback(async () => {
    const url = paste.trim();
    if (!url) return;
    setBusy(true);
    setError(null);
    setPreviewUrl(null);
    try {
      const result = await invoke<MegaProbe>("mega_browse", { url });
      if (result.error) {
        setError(result.error);
        setProbe(null);
        return;
      }
      const probe: MegaProbe = {
        site: "mega",
        kind: result.kind || null,
        title: result.title || result.name || null,
        url,
        backend: settings.megaBackend || "builtin",
        tree: result.tree || [],
        message: null,
      };
      setProbe(probe);
      const handles = new Set(
        (probe.tree || [])
          .filter((n) => n.kind === "file")
          .map((n) => n.handle),
      );
      setChecked(handles);
    } catch (e) {
      setError(String(e));
      setProbe(null);
    } finally {
      setBusy(false);
    }
  }, [paste, settings.megaBackend]);

  const toggle = (handle: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(handle)) next.delete(handle);
      else next.add(handle);
      return next;
    });
  };

  const toggleAll = (on: boolean) => {
    if (on) setChecked(new Set(files.map((f) => f.handle)));
    else setChecked(new Set());
  };

  const selectedSize = useMemo(() => {
    return files
      .filter((f) => checked.has(f.handle))
      .reduce((acc, f) => acc + (f.size || 0), 0);
  }, [files, checked]);

  const enqueue = () => {
    if (checked.size === 0) return;
    const url = paste.trim();
    // cloud drive: enqueue each selected node as mega://node/HANDLE
    if (!url && probe?.kind === "cloud") {
      for (const handle of checked) {
        onEnqueue({ url: `mega://node/${handle}`, selectedHandles: [handle] });
      }
      return;
    }
    if (!url) return;
    onEnqueue({ url, selectedHandles: [...checked] });
  };

  const browseCloud = async () => {
    setPaste("");
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<MegaProbe>("mega_browse", { url: null });
      if (result.error) {
        setError(result.error);
        return;
      }
      const probe: MegaProbe = {
        site: "mega",
        kind: result.kind || "cloud",
        title: result.email || "Cloud drive",
        url: "",
        backend: settings.megaBackend || "builtin",
        tree: result.tree || [],
        message: null,
      };
      setProbe(probe);
      setChecked(
        new Set(
          (probe.tree || [])
            .filter((n) => n.kind === "file")
            .map((n) => n.handle),
        ),
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const startPreview = async (node: MegaTreeNode) => {
    if (node.kind !== "file") return;
    // rebuild a file URL is hard from folder; use folder URL + select via stream CLI
    // only enable for single-file links
    if (probe?.kind !== "file") {
      setError("資料夾內檔案預覽請先用單一 file 連結，或於下載後播放");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await invoke<{
        url?: string;
        name?: string;
        error?: string;
      }>("mega_stream", { url: paste.trim() });
      if (result.error || !result.url) {
        setError(result.error || "無法啟動串流");
        return;
      }
      setPreviewUrl(result.url);
      setPreviewName(result.name || node.name);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  void settings;
  void output;

  return (
    <section className="panel mega-parse" aria-label="MEGA 資料夾瀏覽">
      <h2>MEGA</h2>
      <p className="hint">
        內建 Python 客戶端（預設）。可貼公開 file／folder 連結；資料夾以樹狀勾選下載。
        不支援配額繞過。
      </p>
      <div className="row">
        <input
          type="url"
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
          placeholder="https://mega.nz/folder/…#… 或 /file/…"
          aria-label="MEGA 連結"
        />
        <button type="button" onClick={runProbe} disabled={busy || !paste.trim()}>
          {busy ? "解析中…" : "解析"}
        </button>
        <button type="button" className="ghost" onClick={() => void browseCloud()} disabled={busy}>
          雲端硬碟
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {probe && (
        <>
          <div className="meta-row">
            <strong>{probe.title || "MEGA"}</strong>
            <span className="muted">
              {probe.kind} · 後端 {probe.backend || settings.megaBackend || "builtin"}
            </span>
          </div>
          {files.length > 0 && (
            <div className="row compact">
              <button type="button" className="ghost" onClick={() => toggleAll(true)}>
                全選
              </button>
              <button type="button" className="ghost" onClick={() => toggleAll(false)}>
                清除
              </button>
              <span className="muted">
                已選 {checked.size}／{files.length} · {formatSize(selectedSize)}
              </span>
              <button
                type="button"
                onClick={enqueue}
                disabled={checked.size === 0}
              >
                加入佇列
              </button>
            </div>
          )}
          <ul className="mega-tree">
            {tree.map((node) => {
              const depth = (node.path || node.name).split("/").length - 1;
              const isFile = node.kind === "file";
              return (
                <li
                  key={node.handle}
                  style={{ paddingLeft: `${Math.min(depth, 8) * 12}px` }}
                >
                  {isFile ? (
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={checked.has(node.handle)}
                        onChange={() => toggle(node.handle)}
                      />
                      <span>{node.name}</span>
                      <span className="muted">{formatSize(node.size)}</span>
                      {probe.kind === "file" && (
                        <button
                          type="button"
                          className="ghost"
                          onClick={() => startPreview(node)}
                        >
                          預覽
                        </button>
                      )}
                    </label>
                  ) : (
                    <span className="folder-label">[資料夾] {node.name}</span>
                  )}
                </li>
              );
            })}
          </ul>
          {previewUrl && (
            <div className="preview-block">
              <p className="hint">本機串流預覽（僅 localhost）：{previewName}</p>
              <video src={previewUrl} controls style={{ maxWidth: "100%" }} />
            </div>
          )}
        </>
      )}
    </section>
  );
}
