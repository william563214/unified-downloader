import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import "./App.css";

type ResolveResult = {
  site: string | null;
  label: string | null;
  kind: string | null;
  url: string;
  supported: boolean;
  status: string | null;
  message: string | null;
};

type FileRow = {
  id: string;
  name: string;
  path?: string;
  total?: number;
  bytes: number;
  bps: number;
  status: "queued" | "active" | "done" | "error" | "cancelled";
  message?: string;
};

type JobRow = {
  id: string;
  url: string;
  site: string;
  title: string;
  output: string;
  files: FileRow[];
  ok?: number;
  failed?: number;
  status: "running" | "done" | "error" | "cancelled";
};

type UdlEventPayload = {
  job_id: string;
  line: Record<string, unknown>;
};

function formatBytes(n?: number): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function formatSpeed(bps?: number): string {
  if (bps == null || bps <= 0) return "—";
  return `${formatBytes(bps)}/s`;
}

function siteTone(site: string | null | undefined): string {
  switch (site) {
    case "gofile":
      return "tone-gofile";
    case "bunkr":
      return "tone-bunkr";
    case "iwara":
      return "tone-iwara";
    case "bilibili":
      return "tone-bili";
    default:
      return "tone-unknown";
  }
}

export default function App() {
  const [url, setUrl] = useState("");
  const [output, setOutput] = useState("");
  const [resolveInfo, setResolveInfo] = useState<ResolveResult | null>(null);
  const [resolving, setResolving] = useState(false);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [iwaraQuality, setIwaraQuality] = useState("Source");
  const [iwaraCloudflareBypass, setIwaraCloudflareBypass] = useState(false);

  const canDownload = useMemo(
    () =>
      Boolean(url.trim()) &&
      Boolean(output) &&
      resolveInfo?.supported === true &&
      !resolving,
    [url, output, resolveInfo, resolving],
  );

  const runResolve = useCallback(async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) {
      setResolveInfo(null);
      return;
    }
    setResolving(true);
    setError(null);
    try {
      const result = await invoke<ResolveResult>("resolve_url", { url: trimmed });
      setResolveInfo(result);
    } catch (e) {
      setResolveInfo(null);
      setError(String(e));
    } finally {
      setResolving(false);
    }
  }, []);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void runResolve(url);
    }, 350);
    return () => window.clearTimeout(handle);
  }, [url, runResolve]);

  useEffect(() => {
    const unsubs: Array<() => void> = [];

    void listen<UdlEventPayload>("udl-event", (event) => {
      const { job_id, line } = event.payload;
      const ev = String(line.ev ?? "");
      setJobs((prev) =>
        prev.map((job) => {
          if (job.id !== job_id) return job;
          if (ev === "job") {
            return {
              ...job,
              site: String(line.site ?? job.site),
              title: String(line.title ?? job.title),
            };
          }
          if (ev === "file") {
            const id = String(line.id);
            if (job.files.some((f) => f.id === id)) return job;
            return {
              ...job,
              files: [
                ...job.files,
                {
                  id,
                  name: String(line.name ?? id),
                  path: line.path ? String(line.path) : undefined,
                  total: typeof line.total === "number" ? line.total : undefined,
                  bytes: 0,
                  bps: 0,
                  status: "queued",
                },
              ],
            };
          }
          if (ev === "progress") {
            const fid = String(line.file);
            return {
              ...job,
              files: job.files.map((f) =>
                f.id === fid
                  ? {
                      ...f,
                      bytes: Number(line.bytes ?? f.bytes),
                      total:
                        typeof line.total === "number" ? line.total : f.total,
                      bps: Number(line.bps ?? f.bps),
                      status: "active",
                    }
                  : f,
              ),
            };
          }
          if (ev === "done") {
            if (line.file) {
              const fid = String(line.file);
              return {
                ...job,
                files: job.files.map((f) =>
                  f.id === fid
                    ? {
                        ...f,
                        status: "done",
                        path: line.path ? String(line.path) : f.path,
                        bytes: f.total ?? f.bytes,
                      }
                    : f,
                ),
              };
            }
            return {
              ...job,
              status: "done",
              ok: typeof line.ok === "number" ? line.ok : job.ok,
              failed: typeof line.failed === "number" ? line.failed : job.failed,
            };
          }
          if (ev === "error") {
            const fid = line.file ? String(line.file) : null;
            if (fid) {
              return {
                ...job,
                files: job.files.map((f) =>
                  f.id === fid
                    ? {
                        ...f,
                        status: "error",
                        message: String(line.message ?? "error"),
                      }
                    : f,
                ),
              };
            }
            return {
              ...job,
              status: "error",
            };
          }
          if (ev === "cancel") {
            return {
              ...job,
              status: "cancelled",
              files: job.files.map((f) =>
                f.status === "done" || f.status === "error"
                  ? f
                  : { ...f, status: "cancelled" },
              ),
            };
          }
          return job;
        }),
      );
    }).then((u) => unsubs.push(u));

    void listen<{ job_id: string; code: number | null }>("udl-exit", (event) => {
      const { job_id, code } = event.payload;
      setJobs((prev) =>
        prev.map((job) => {
          if (job.id !== job_id) return job;
          if (job.status !== "running") return job;
          if (code === 130) return { ...job, status: "cancelled" };
          if (code === 0) return { ...job, status: "done" };
          return { ...job, status: "error" };
        }),
      );
    }).then((u) => unsubs.push(u));

    return () => {
      for (const u of unsubs) u();
    };
  }, []);

  async function chooseFolder() {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "選擇輸出資料夾",
    });
    if (typeof selected === "string") {
      setOutput(selected);
    }
  }

  async function startDownload() {
    if (!canDownload) return;
    setError(null);
    try {
      const jobId = await invoke<string>("start_download", {
        url: url.trim(),
        output,
        quality: resolveInfo?.site === "iwara" ? iwaraQuality : null,
        cloudflareBypass:
          resolveInfo?.site === "iwara" ? iwaraCloudflareBypass : null,
      });
      setJobs((prev) => [
        {
          id: jobId,
          url: url.trim(),
          site: resolveInfo?.site ?? "unknown",
          title: resolveInfo?.label ?? "下載中",
          output,
          files: [],
          status: "running",
        },
        ...prev,
      ]);
    } catch (e) {
      setError(String(e));
    }
  }

  async function cancelJob(jobId: string) {
    try {
      await invoke("cancel_download", { jobId });
      setJobs((prev) =>
        prev.map((j) =>
          j.id === jobId ? { ...j, status: "cancelled" as const } : j,
        ),
      );
    } catch (e) {
      setError(String(e));
    }
  }

  async function openFolder(path: string) {
    try {
      await invoke("open_path", { path });
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="app">
      <header className="hero">
        <div className="brand-mark" aria-hidden />
        <div className="brand-copy">
          <p className="brand">整合下載器</p>
          <h1>貼上網址，辨識站點，一次下載</h1>
          <p className="lede">
            統一核心支援 Gofile、Bunkr 與 Iwara；Bilibili 槽位已預留。
          </p>
        </div>
      </header>

      <section className="composer" aria-label="下載表單">
        <label className="field">
          <span>網址</span>
          <div className="url-row">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://gofile.io/d/…、bunkr、或 iwara.tv/video/…"
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
            />
            {resolveInfo?.site ? (
              <span className={`badge ${siteTone(resolveInfo.site)}`}>
                {resolveInfo.label ?? resolveInfo.site}
                {resolveInfo.status === "stub" ? " · 即將推出" : ""}
              </span>
            ) : resolving ? (
              <span className="badge tone-unknown">辨識中</span>
            ) : url.trim() ? (
              <span className="badge tone-unknown">不支援</span>
            ) : null}
          </div>
        </label>

        {resolveInfo?.site === "iwara" && resolveInfo.supported && (
          <div className="site-options" aria-label="Iwara 選項">
            <label className="field compact">
              <span>畫質</span>
              <select
                value={iwaraQuality}
                onChange={(e) => setIwaraQuality(e.target.value)}
              >
                <option value="Source">Source（最佳）</option>
                <option value="540">540</option>
                <option value="360">360</option>
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={iwaraCloudflareBypass}
                onChange={(e) => setIwaraCloudflareBypass(e.target.checked)}
              />
              <span>
                Cloudflare 繞過（選用，預設關閉；需安裝 cloudscraper）
              </span>
            </label>
          </div>
        )}

        <label className="field">
          <span>輸出資料夾</span>
          <div className="folder-row">
            <input
              value={output}
              readOnly
              placeholder="選擇要儲存的資料夾"
            />
            <button type="button" className="ghost" onClick={() => void chooseFolder()}>
              瀏覽
            </button>
          </div>
        </label>

        {resolveInfo?.message && (
          <p className="hint">{resolveInfo.message}</p>
        )}
        {error && <p className="error">{error}</p>}

        <div className="actions">
          <button
            type="button"
            className="primary"
            disabled={!canDownload}
            onClick={() => void startDownload()}
          >
            開始下載
          </button>
        </div>
      </section>

      <section className="queue" aria-label="下載清單">
        <div className="queue-head">
          <h2>下載清單</h2>
          <p>{jobs.length === 0 ? "尚未有任務" : `${jobs.length} 個任務`}</p>
        </div>

        {jobs.length === 0 ? (
          <div className="empty">
            <p>貼上公開連結後按下開始下載，進度與速度會顯示在這裡。</p>
          </div>
        ) : (
          <ul className="jobs">
            {jobs.map((job) => (
              <li key={job.id} className="job">
                <div className="job-top">
                  <span className={`badge ${siteTone(job.site)}`}>{job.site}</span>
                  <div className="job-title">
                    <strong>{job.title}</strong>
                    <span>{job.url}</span>
                  </div>
                  <div className="job-actions">
                    {job.status === "running" && (
                      <button
                        type="button"
                        className="ghost danger"
                        onClick={() => void cancelJob(job.id)}
                      >
                        取消
                      </button>
                    )}
                    <button
                      type="button"
                      className="ghost"
                      onClick={() => void openFolder(job.output)}
                    >
                      開啟資料夾
                    </button>
                  </div>
                </div>

                <ul className="files">
                  {job.files.length === 0 && job.status === "running" && (
                    <li className="file muted">解析中…</li>
                  )}
                  {job.files.map((file) => {
                    const pct =
                      file.total && file.total > 0
                        ? Math.min(100, (file.bytes / file.total) * 100)
                        : file.status === "done"
                          ? 100
                          : 0;
                    return (
                      <li key={file.id} className="file">
                        <div className="file-meta">
                          <span className="name">{file.name}</span>
                          <span className="stats">
                            {formatBytes(file.bytes)}
                            {file.total != null ? ` / ${formatBytes(file.total)}` : ""}
                            {" · "}
                            {formatSpeed(file.bps)}
                            {" · "}
                            {file.status}
                          </span>
                        </div>
                        <div className="bar" aria-hidden>
                          <i style={{ width: `${pct}%` }} />
                        </div>
                        {file.message && <p className="error">{file.message}</p>}
                      </li>
                    );
                  })}
                </ul>

                {job.status !== "running" && (
                  <p className="job-foot">
                    狀態：{job.status}
                    {job.ok != null ? ` · 成功 ${job.ok}` : ""}
                    {job.failed != null ? ` · 失敗 ${job.failed}` : ""}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
