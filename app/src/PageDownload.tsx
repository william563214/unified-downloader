import { useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";

export type PageCandidate = {
  url: string;
  name: string;
  source: string;
  kind: string;
  extension?: string | null;
  size?: number | null;
  mime?: string | null;
  site?: string | null;
  site_label?: string | null;
  text?: string | null;
  selected?: boolean;
};

export type PageScanResult = {
  url: string;
  title?: string | null;
  count: number;
  candidates: PageCandidate[];
  message?: string | null;
};

type Props = {
  output: string;
  onEnqueue: (jobs: Array<{ url: string; site: string; title: string }>) => void;
};

function formatSize(size?: number | null): string {
  if (size == null || Number.isNaN(size)) return "—";
  if (size < 1000) return `${size} B`;
  if (size < 1_000_000) return `${(size / 1000).toFixed(1)} KB`;
  if (size < 1_000_000_000) return `${(size / 1_000_000).toFixed(1)} MB`;
  return `${(size / 1_000_000_000).toFixed(2)} GB`;
}

const kind_labels: Record<string, string> = {
  video: "影片",
  audio: "音訊",
  image: "圖片",
  archive: "壓縮檔",
  document: "文件",
  file: "檔案",
  site: "站點",
};

export default function PageDownload({ output, onEnqueue }: Props) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PageScanResult | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [ext, setExt] = useState("");
  const [types, setTypes] = useState("");
  const [sizeMin, setSizeMin] = useState("");
  const [sizeMax, setSizeMax] = useState("");
  const [regex, setRegex] = useState("");
  const [glob, setGlob] = useState("");
  const [sameDomain, setSameDomain] = useState(false);
  const [includeImages, setIncludeImages] = useState(false);
  const [noHead, setNoHead] = useState(false);

  const candidates = result?.candidates ?? [];

  const selectedCount = useMemo(
    () => candidates.filter((c) => checked.has(c.url)).length,
    [candidates, checked],
  );

  async function scan() {
    const target = url.trim();
    if (!target) return;
    setBusy(true);
    setError(null);
    setResult(null);
    setChecked(new Set());
    try {
      const scanned = await invoke<PageScanResult>("page_scan", {
        url: target,
        ext: ext.trim() || null,
        types: types.trim() || null,
        sizeMin: sizeMin.trim() || null,
        sizeMax: sizeMax.trim() || null,
        regex: regex.trim() || null,
        glob: glob.trim() || null,
        sameDomain,
        includeImages,
        noHead,
      });
      setResult(scanned);
      const initial = new Set(
        (scanned.candidates || [])
          .filter((c) => c.kind === "site" || c.kind === "video" || c.kind === "audio")
          .map((c) => c.url),
      );
      if (initial.size === 0) {
        for (const c of scanned.candidates || []) initial.add(c.url);
      }
      setChecked(initial);
      if (scanned.message && (scanned.candidates || []).length === 0) {
        setError(scanned.message);
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function toggle(urlValue: string) {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(urlValue)) next.delete(urlValue);
      else next.add(urlValue);
      return next;
    });
  }

  function selectAll(on: boolean) {
    if (!on) {
      setChecked(new Set());
      return;
    }
    setChecked(new Set(candidates.map((c) => c.url)));
  }

  function selectByType(kind: string) {
    setChecked(new Set(candidates.filter((c) => c.kind === kind).map((c) => c.url)));
  }

  function enqueueSelected() {
    if (!output.trim()) {
      setError("請先設定下載目錄");
      return;
    }
    const jobs = candidates
      .filter((c) => checked.has(c.url))
      .map((c) => ({
        url: c.url,
        site: c.site || "direct",
        title: c.text || c.name || c.site_label || c.url,
      }));
    if (jobs.length === 0) {
      setError("請至少勾選一個連結");
      return;
    }
    onEnqueue(jobs);
  }

  const kindsPresent = useMemo(() => {
    const set = new Set(candidates.map((c) => c.kind));
    return Array.from(set);
  }, [candidates]);

  return (
    <section className="panel page-download">
      <header className="panel-head">
        <h2>頁面擷取（Download All）</h2>
        <p>貼上網頁網址，列出可下載的直連媒體與支援站點連結，勾選後加入佇列。</p>
      </header>

      <label className="field">
        <span>網頁網址</span>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://example.com/gallery"
          onKeyDown={(e) => {
            if (e.key === "Enter") void scan();
          }}
        />
      </label>

      <div className="row wrap filters">
        <label className="field compact">
          <span>副檔名</span>
          <input value={ext} onChange={(e) => setExt(e.target.value)} placeholder="mp4,mkv,zip" />
        </label>
        <label className="field compact">
          <span>類型</span>
          <input
            value={types}
            onChange={(e) => setTypes(e.target.value)}
            placeholder="video,audio,site"
          />
        </label>
        <label className="field compact">
          <span>最小大小</span>
          <input value={sizeMin} onChange={(e) => setSizeMin(e.target.value)} placeholder="1M" />
        </label>
        <label className="field compact">
          <span>最大大小</span>
          <input value={sizeMax} onChange={(e) => setSizeMax(e.target.value)} placeholder="2G" />
        </label>
        <label className="field compact">
          <span>Regex</span>
          <input value={regex} onChange={(e) => setRegex(e.target.value)} placeholder="episode" />
        </label>
        <label className="field compact">
          <span>Glob</span>
          <input value={glob} onChange={(e) => setGlob(e.target.value)} placeholder="*.mp4|*.mkv" />
        </label>
      </div>

      <div className="row wrap gap">
        <label className="check">
          <input
            type="checkbox"
            checked={sameDomain}
            onChange={(e) => setSameDomain(e.target.checked)}
          />
          僅同網域
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={includeImages}
            onChange={(e) => setIncludeImages(e.target.checked)}
          />
          包含圖片
        </label>
        <label className="check">
          <input type="checkbox" checked={noHead} onChange={(e) => setNoHead(e.target.checked)} />
          略過 HEAD 大小探測
        </label>
        <button type="button" className="primary" disabled={busy || !url.trim()} onClick={() => void scan()}>
          {busy ? "掃描中…" : "掃描頁面"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <>
          <div className="row wrap gap">
            <span className="muted">
              {result.title || result.url} — {result.count} 個候選，已選 {selectedCount}
            </span>
            <button type="button" onClick={() => selectAll(true)}>
              全選
            </button>
            <button type="button" onClick={() => selectAll(false)}>
              清除
            </button>
            {kindsPresent.map((kind) => (
              <button key={kind} type="button" onClick={() => selectByType(kind)}>
                選{kind_labels[kind] || kind}
              </button>
            ))}
            <button
              type="button"
              className="primary"
              disabled={selectedCount === 0}
              onClick={enqueueSelected}
            >
              加入佇列（{selectedCount}）
            </button>
          </div>

          {candidates.length === 0 ? (
            <p className="muted">沒有符合篩選的連結。</p>
          ) : (
            <ul className="page-candidate-list">
              {candidates.map((c) => (
                <li key={c.url}>
                  <label className="page-candidate">
                    <input
                      type="checkbox"
                      checked={checked.has(c.url)}
                      onChange={() => toggle(c.url)}
                    />
                    <span className="badge">{kind_labels[c.kind] || c.kind}</span>
                    {c.site_label && <span className="badge site">{c.site_label}</span>}
                    <span className="name" title={c.url}>
                      {c.name || c.text || c.url}
                    </span>
                    <span className="meta">
                      {c.extension || "—"} · {formatSize(c.size)}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}
