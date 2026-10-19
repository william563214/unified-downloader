import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { humanizeError, statusLabel } from "./errors";
import {
  type AppSettings,
  type CoverNaming,
  type DuplicateMode,
  type FfmpegStatus,
  type HistoryEntry,
  type QueueEntry,
  default_settings,
  default_site_flags,
  extractUrl,
  history_cover_path,
  site_ids,
} from "./settings";
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

type ProbePart = {
  page: number;
  title: string;
  cid?: number;
  badge?: string;
};

type ProbeQuality = {
  qn: number;
  label: string;
};

type ProbeResult = {
  site: string | null;
  kind: string | null;
  title: string | null;
  url: string;
  parts: ProbePart[];
  qualities: ProbeQuality[];
  default_part: number | null;
  default_quality: number | null;
  message: string | null;
};

type FileRow = {
  id: string;
  name: string;
  path?: string;
  total?: number;
  bytes: number;
  bps: number;
  status:
    | "queued"
    | "active"
    | "merging"
    | "done"
    | "error"
    | "cancelled"
    | "skipped";
  message?: string;
  alreadyDownloaded?: boolean;
};

type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "done"
  | "error"
  | "cancelled";

type JobRow = {
  id: string;
  clientId: string;
  url: string;
  site: string;
  title: string;
  output: string;
  quality: string | null;
  part: string | null;
  cloudflareBypass: boolean | null;
  files: FileRow[];
  ok?: number;
  failed?: number;
  status: JobStatus;
  message?: string;
};

type UdlEventPayload = {
  job_id: string;
  line: Record<string, unknown>;
};

type Page = "download" | "history" | "settings";

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
    case "mega":
      return "tone-mega";
    default:
      return "tone-unknown";
  }
}

function newClientId(): string {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export default function App() {
  const [page, setPage] = useState<Page>("download");
  const [url, setUrl] = useState("");
  const [output, setOutput] = useState("");
  const [resolveInfo, setResolveInfo] = useState<ResolveResult | null>(null);
  const [probeInfo, setProbeInfo] = useState<ProbeResult | null>(null);
  const [selectedPart, setSelectedPart] = useState<string>("all");
  const [selectedQuality, setSelectedQuality] = useState<string>("");
  const [resolving, setResolving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<AppSettings>(default_settings);
  const [settingsDraft, setSettingsDraft] = useState<AppSettings>(default_settings);
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [ffmpegInfo, setFfmpegInfo] = useState<FfmpegStatus | null>(null);
  const [ffmpegBusy, setFfmpegBusy] = useState(false);
  const [ffmpegPrompt, setFfmpegPrompt] = useState(false);
  const [clipboardHint, setClipboardHint] = useState<string | null>(null);
  const [historyRows, setHistoryRows] = useState<HistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historySite, setHistorySite] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyTag, setHistoryTag] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyCovers, setHistoryCovers] = useState<Record<string, string>>({});
  const [selectedHistory, setSelectedHistory] = useState<Set<string>>(new Set());
  const startingRef = useRef(false);
  const resumedRef = useRef(false);

  const isBilibili = resolveInfo?.site === "bilibili" && resolveInfo.supported;

  const canDownload = useMemo(
    () =>
      Boolean(url.trim()) &&
      Boolean(output) &&
      resolveInfo?.supported === true &&
      !resolving &&
      !probing,
    [url, output, resolveInfo, resolving, probing],
  );

  const refreshFfmpeg = useCallback(async () => {
    try {
      const status = await invoke<FfmpegStatus>("ffmpeg_status");
      setFfmpegInfo(status);
      if (!status.found && status.provisionSupported) {
        setFfmpegPrompt(true);
      }
      return status;
    } catch (e) {
      setFfmpegInfo({
        found: false,
        path: null,
        source: null,
        platform: "unknown",
        provisionSupported: false,
        message: String(e),
      });
      return null;
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const loaded = await invoke<AppSettings>("get_settings");
        const merged: AppSettings = {
          ...default_settings,
          ...loaded,
          onDuplicate: loaded.onDuplicate || default_settings.onDuplicate,
          coverNaming: loaded.coverNaming || default_settings.coverNaming,
          nfoSites: { ...default_site_flags(), ...(loaded.nfoSites || {}) },
          coverSites: { ...default_site_flags(), ...(loaded.coverSites || {}) },
          writeNfo: Boolean(loaded.writeNfo),
          writeCover: Boolean(loaded.writeCover),
          embedCover: Boolean(loaded.embedCover),
        };
        setSettings(merged);
        setSettingsDraft(merged);
        if (loaded.downloadDir) {
          setOutput(loaded.downloadDir);
        }
        if (loaded.iwaraQuality) {
          // applied when iwara is selected via settings defaults in enqueue
        }
        setSelectedPart(loaded.bilibiliPart || "all");
        if (loaded.bilibiliQuality) {
          setSelectedQuality(loaded.bilibiliQuality);
        }
      } catch {
        /* defaults */
      }
      await refreshFfmpeg();
    })();
  }, [refreshFfmpeg]);

  const runResolve = useCallback(async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) {
      setResolveInfo(null);
      setProbeInfo(null);
      return;
    }
    setResolving(true);
    setError(null);
    try {
      const result = await invoke<ResolveResult>("resolve_url", { url: trimmed });
      setResolveInfo(result);
      if (!(result.site === "bilibili" && result.supported)) {
        setProbeInfo(null);
      }
    } catch (e) {
      setResolveInfo(null);
      setProbeInfo(null);
      setError(humanizeError(String(e)));
    } finally {
      setResolving(false);
    }
  }, []);

  const runProbe = useCallback(async (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) {
      setProbeInfo(null);
      return;
    }
    setProbing(true);
    setError(null);
    try {
      const result = await invoke<ProbeResult>("probe_url", { url: trimmed });
      const parts = (result.parts || []).map((p) => ({
        page: Number((p as ProbePart).page),
        title: String((p as ProbePart).title ?? ""),
        cid: (p as ProbePart).cid,
        badge: (p as ProbePart).badge,
      }));
      const qualities = (result.qualities || []).map((q) => ({
        qn: Number((q as ProbeQuality).qn),
        label: String((q as ProbeQuality).label ?? `qn${(q as ProbeQuality).qn}`),
      }));
      setProbeInfo({ ...result, parts, qualities });
      if (result.kind === "bangumi" || parts.length <= 1) {
        setSelectedPart(String(result.default_part ?? parts[0]?.page ?? 1));
      } else {
        setSelectedPart(settings.bilibiliPart || "all");
      }
      setSelectedQuality(
        settings.bilibiliQuality ||
          (result.default_quality != null ? String(result.default_quality) : ""),
      );
    } catch (e) {
      setProbeInfo(null);
      setError(humanizeError(String(e)));
    } finally {
      setProbing(false);
    }
  }, [settings.bilibiliPart, settings.bilibiliQuality]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      void runResolve(url);
    }, 350);
    return () => window.clearTimeout(handle);
  }, [url, runResolve]);

  useEffect(() => {
    if (!isBilibili) return;
    const handle = window.setTimeout(() => {
      void runProbe(url);
    }, 450);
    return () => window.clearTimeout(handle);
  }, [url, isBilibili, runProbe]);

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
            const already = line.already_downloaded === true;
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
                  status: already ? "skipped" : "queued",
                  alreadyDownloaded: already,
                  message: already ? "已下載" : undefined,
                },
              ],
            };
          }
          if (ev === "progress") {
            const fid = String(line.file);
            return {
              ...job,
              files: job.files.map((f) =>
                f.id === fid || fid.startsWith(`${f.id}:`)
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
          if (ev === "merging") {
            const fid = line.file ? String(line.file) : null;
            return {
              ...job,
              files: job.files.map((f) =>
                fid && (f.id === fid || fid.startsWith(`${f.id}`))
                  ? {
                      ...f,
                      status: "merging",
                      message: line.message
                        ? String(line.message)
                        : "合併影音中…",
                    }
                  : f,
              ),
            };
          }
          if (ev === "duplicate") {
            const fid = line.file ? String(line.file) : null;
            const message =
              line.already_downloaded === true
                ? "已下載"
                : line.message
                  ? String(line.message)
                  : "重複項目";
            if (!fid) {
              return { ...job, message };
            }
            return {
              ...job,
              message,
              files: job.files.map((f) =>
                f.id === fid || fid.startsWith(`${f.id}:`)
                  ? {
                      ...f,
                      status:
                        line.action === "skip" ? "skipped" : f.status,
                      path: line.path ? String(line.path) : f.path,
                      alreadyDownloaded: Boolean(line.already_downloaded),
                      message,
                    }
                  : f,
              ),
            };
          }
          if (ev === "done") {
            if (line.file) {
              const fid = String(line.file);
              const skipped = line.skipped === true || line.already_downloaded === true;
              return {
                ...job,
                files: job.files.map((f) =>
                  f.id === fid
                    ? {
                        ...f,
                        status: skipped ? "skipped" : "done",
                        path: line.path ? String(line.path) : f.path,
                        bytes: f.total ?? f.bytes,
                        alreadyDownloaded: Boolean(line.already_downloaded),
                        message: skipped ? "已下載" : undefined,
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
              message:
                line.already_downloaded === true
                  ? "已下載（略過）"
                  : job.message,
            };
          }
          if (ev === "error") {
            const code = line.code ? String(line.code) : null;
            const message = humanizeError(
              line.message ? String(line.message) : null,
              code,
            );
            const fid = line.file ? String(line.file) : null;
            if (fid) {
              return {
                ...job,
                message,
                files: job.files.map((f) =>
                  f.id === fid || fid.startsWith(`${f.id}:`)
                    ? {
                        ...f,
                        status: "error",
                        message,
                      }
                    : f,
                ),
              };
            }
            return {
              ...job,
              status: "error",
              message,
            };
          }
          if (ev === "cancel") {
            return {
              ...job,
              status: job.status === "paused" ? "paused" : "cancelled",
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
          if (job.status === "paused") return job;
          if (job.status !== "running" && job.status !== "queued") return job;
          if (code === 130) return { ...job, status: "cancelled" };
          if (code === 0) return { ...job, status: "done" };
          return {
            ...job,
            status: "error",
            message: job.message || humanizeError(null, "download_failed"),
          };
        }),
      );
    }).then((u) => unsubs.push(u));

    return () => {
      for (const u of unsubs) u();
    };
  }, []);

  const launchJob = useCallback(
    async (job: JobRow) => {
      // mark running before await so the scheduler cannot double-start
      setJobs((prev) =>
        prev.map((j) =>
          j.clientId === job.clientId
            ? { ...j, status: "running", message: undefined, files: [] }
            : j,
        ),
      );
      try {
        const jobId = await invoke<string>("start_download", {
          url: job.url,
          output: job.output,
          quality: job.quality,
          part: job.part,
          cloudflareBypass: job.cloudflareBypass,
          workers: settings.workers,
          ffmpeg: settings.ffmpegPath || null,
          onDuplicate: settings.onDuplicate || "skip",
          jobId: job.id || null,
        });
        setJobs((prev) =>
          prev.map((j) =>
            j.clientId === job.clientId ? { ...j, id: jobId } : j,
          ),
        );
      } catch (e) {
        setJobs((prev) =>
          prev.map((j) =>
            j.clientId === job.clientId
              ? {
                  ...j,
                  status: "error",
                  message: humanizeError(String(e)),
                }
              : j,
          ),
        );
      }
    },
    [settings.ffmpegPath, settings.workers, settings.onDuplicate],
  );

  useEffect(() => {
    if (resumedRef.current) return;
    resumedRef.current = true;
    void (async () => {
      try {
        const rows = await invoke<QueueEntry[]>("queue_list", {
          unfinished: true,
        });
        if (!Array.isArray(rows) || rows.length === 0) return;
        setJobs((prev) => {
          const existing = new Set(prev.map((j) => j.id).filter(Boolean));
          const restored: JobRow[] = rows
            .filter((r) => r.url && r.output_dir && !existing.has(r.id))
            .map((r) => ({
              id: r.id,
              clientId: newClientId(),
              url: r.url,
              site: r.site || "unknown",
              title: r.title || r.site || "恢復任務",
              output: r.output_dir,
              quality: r.quality || null,
              part: r.part || null,
              cloudflareBypass: r.cloudflare_bypass ?? null,
              files: [],
              status: "queued",
              message: "應用重啟後自動恢復",
            }));
          return restored.length ? [...restored, ...prev] : prev;
        });
      } catch {
        /* queue restore optional when sidecar missing */
      }
    })();
  }, []);

  const refreshHistory = useCallback(async () => {
    setHistoryBusy(true);
    setError(null);
    try {
      const rows = await invoke<HistoryEntry[]>("history_list", {
        site: historySite || null,
        status: historyStatus || null,
        tag: historyTag || null,
        query: historyQuery || null,
        sort: "created_at",
        order: "desc",
        limit: 200,
      });
      setHistoryRows(Array.isArray(rows) ? rows : []);
      setSelectedHistory(new Set());
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setHistoryBusy(false);
    }
  }, [historyQuery, historySite, historyStatus, historyTag]);

  useEffect(() => {
    if (page !== "history") return;
    void refreshHistory();
  }, [page, refreshHistory]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const next: Record<string, string> = {};
      for (const row of historyRows) {
        const local = history_cover_path(row);
        if (!local) continue;
        try {
          const dataUrl = await invoke<string>("read_local_image", { path: local });
          if (!cancelled) next[row.id] = dataUrl;
        } catch {
          /* missing or unreadable cover */
        }
      }
      if (!cancelled) setHistoryCovers(next);
    })();
    return () => {
      cancelled = true;
    };
  }, [historyRows]);

  useEffect(() => {
    const running = jobs.filter((j) => j.status === "running").length;
    const slots = Math.max(1, settings.maxConcurrent) - running;
    if (slots <= 0 || startingRef.current) return;
    const next = jobs.filter((j) => j.status === "queued").slice(0, slots);
    if (next.length === 0) return;
    startingRef.current = true;
    void (async () => {
      for (const job of next) {
        await launchJob(job);
      }
      startingRef.current = false;
    })();
  }, [jobs, settings.maxConcurrent, launchJob]);

  useEffect(() => {
    const onFocus = () => {
      void (async () => {
        try {
          if (!navigator.clipboard?.readText) return;
          const text = await navigator.clipboard.readText();
          const found = extractUrl(text);
          if (!found) return;
          if (found === url.trim()) return;
          setClipboardHint(found);
        } catch {
          /* clipboard permission optional */
        }
      })();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [url]);

  async function chooseFolder(forSettings = false) {
    const selected = await open({
      directory: true,
      multiple: false,
      title: "選擇輸出資料夾",
    });
    if (typeof selected === "string") {
      if (forSettings) {
        setSettingsDraft((s) => ({ ...s, downloadDir: selected }));
        setSettingsDirty(true);
      } else {
        setOutput(selected);
      }
    }
  }

  async function chooseFfmpeg() {
    const selected = await open({
      multiple: false,
      title: "選擇 FFmpeg 執行檔",
    });
    if (typeof selected === "string") {
      setSettingsDraft((s) => ({ ...s, ffmpegPath: selected }));
      setSettingsDirty(true);
    }
  }

  function enqueueDownload(overrides?: Partial<JobRow>) {
    if (!canDownload && !overrides?.url) return;
    setError(null);
    const site = overrides?.site ?? resolveInfo?.site ?? "unknown";
    const job: JobRow = {
      id: "",
      clientId: newClientId(),
      url: overrides?.url ?? url.trim(),
      site,
      title:
        overrides?.title ??
        probeInfo?.title ??
        resolveInfo?.label ??
        "下載中",
      output: overrides?.output ?? output,
      quality:
        overrides?.quality ??
        (site === "bilibili"
          ? selectedQuality || settings.bilibiliQuality || null
          : site === "iwara"
            ? settings.iwaraQuality || "Source"
            : null),
      part:
        overrides?.part ??
        (site === "bilibili" ? selectedPart || settings.bilibiliPart : null),
      cloudflareBypass:
        overrides?.cloudflareBypass ??
        (site === "iwara" ? settings.iwaraCloudflareBypass : null),
      files: [],
      status: "queued",
    };
    setJobs((prev) => [job, ...prev]);
  }

  async function cancelJob(job: JobRow) {
    try {
      if (job.id) {
        await invoke("cancel_download", { jobId: job.id });
      }
      setJobs((prev) =>
        prev.map((j) =>
          j.clientId === job.clientId
            ? { ...j, status: "cancelled" as const }
            : j,
        ),
      );
    } catch (e) {
      setError(humanizeError(String(e)));
    }
  }

  async function pauseJob(job: JobRow) {
    try {
      if (job.id) {
        await invoke("cancel_download", { jobId: job.id });
      }
      setJobs((prev) =>
        prev.map((j) =>
          j.clientId === job.clientId
            ? {
                ...j,
                status: "paused" as const,
                message: "已暫停（可重試接續）",
              }
            : j,
        ),
      );
    } catch (e) {
      setError(humanizeError(String(e)));
    }
  }

  function retryJob(job: JobRow) {
    setJobs((prev) =>
      prev.map((j) =>
        j.clientId === job.clientId
          ? {
              ...j,
              id: "",
              status: "queued",
              files: [],
              message: undefined,
              ok: undefined,
              failed: undefined,
            }
          : j,
      ),
    );
  }

  async function openFolder(path: string) {
    try {
      await invoke("open_path", { path });
    } catch (e) {
      setError(humanizeError(String(e)));
    }
  }

  async function saveSettingsNow() {
    try {
      const saved = await invoke<AppSettings>("save_settings", {
        settings: settingsDraft,
      });
      setSettings(saved);
      setSettingsDraft(saved);
      setSettingsDirty(false);
      if (saved.downloadDir) setOutput(saved.downloadDir);
      await refreshFfmpeg();
    } catch (e) {
      setError(humanizeError(String(e)));
    }
  }

  async function deleteSelectedHistory(deleteFiles: boolean) {
    if (selectedHistory.size === 0) return;
    setHistoryBusy(true);
    try {
      await invoke("history_delete", {
        ids: Array.from(selectedHistory),
        deleteFiles,
      });
      await refreshHistory();
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setHistoryBusy(false);
    }
  }

  async function installFfmpegNow() {
    setFfmpegBusy(true);
    setError(null);
    try {
      const status = await invoke<FfmpegStatus>("install_ffmpeg");
      setFfmpegInfo(status);
      setFfmpegPrompt(false);
      if (status.path) {
        setSettingsDraft((s) => ({ ...s, ffmpegPath: status.path || s.ffmpegPath }));
        setSettings((s) => ({ ...s, ffmpegPath: status.path || s.ffmpegPath }));
      }
    } catch (e) {
      setError(humanizeError(String(e), "ffmpeg_provision_failed"));
    } finally {
      setFfmpegBusy(false);
      await refreshFfmpeg();
    }
  }

  const iwaraQuality =
    resolveInfo?.site === "iwara"
      ? settings.iwaraQuality || "Source"
      : settings.iwaraQuality;

  return (
    <div className="app">
      <header className="hero">
        <div className="brand-mark" aria-hidden />
        <div className="brand-copy">
          <p className="brand">整合下載器</p>
          <h1>貼上網址，辨識站點，一次下載</h1>
          <p className="lede">
            統一核心支援 Gofile、Bunkr、Iwara、Bilibili 與 MEGA。
          </p>
        </div>
        <nav className="top-nav" aria-label="主要導覽">
          <button
            type="button"
            className={page === "download" ? "nav active" : "nav"}
            onClick={() => setPage("download")}
          >
            下載
          </button>
          <button
            type="button"
            className={page === "history" ? "nav active" : "nav"}
            onClick={() => setPage("history")}
          >
            歷史
          </button>
          <button
            type="button"
            className={page === "settings" ? "nav active" : "nav"}
            onClick={() => setPage("settings")}
          >
            設定
          </button>
        </nav>
      </header>

      {ffmpegPrompt && ffmpegInfo && !ffmpegInfo.found && (
        <section className="banner" role="status">
          <div>
            <strong>尚未找到 FFmpeg</strong>
            <p>
              Bilibili 等站點需要 FFmpeg 合併影音。可下載目前系統的靜態建置（會驗證
              checksum），並安裝到應用資料目錄。
            </p>
          </div>
          <div className="banner-actions">
            <button
              type="button"
              className="primary"
              disabled={ffmpegBusy || !ffmpegInfo.provisionSupported}
              onClick={() => void installFfmpegNow()}
            >
              {ffmpegBusy ? "下載中…" : "下載 FFmpeg"}
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => setFfmpegPrompt(false)}
            >
              稍後
            </button>
          </div>
        </section>
      )}

      {page === "history" ? (
        <section className="composer settings" aria-label="下載歷史">
          <div className="queue-head">
            <h2>下載歷史</h2>
            <p>搜尋、篩選、清理；資料由核心 SQLite 保存</p>
          </div>
          <div className="settings-grid">
            <label className="field compact">
              <span>關鍵字</span>
              <input
                value={historyQuery}
                onChange={(e) => setHistoryQuery(e.target.value)}
                placeholder="標題／作者／URL"
              />
            </label>
            <label className="field compact">
              <span>站點</span>
              <select
                value={historySite}
                onChange={(e) => setHistorySite(e.target.value)}
              >
                <option value="">全部</option>
                <option value="gofile">Gofile</option>
                <option value="bunkr">Bunkr</option>
                <option value="iwara">Iwara</option>
                <option value="bilibili">Bilibili</option>
                <option value="mega">MEGA</option>
              </select>
            </label>
            <label className="field compact">
              <span>狀態</span>
              <select
                value={historyStatus}
                onChange={(e) => setHistoryStatus(e.target.value)}
              >
                <option value="">全部</option>
                <option value="done">完成</option>
                <option value="skipped">已略過</option>
                <option value="error">失敗</option>
                <option value="cancelled">取消</option>
                <option value="running">進行中</option>
              </select>
            </label>
            <label className="field compact">
              <span>標籤</span>
              <input
                value={historyTag}
                onChange={(e) => setHistoryTag(e.target.value)}
                placeholder="精確標籤"
              />
            </label>
          </div>
          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={historyBusy}
              onClick={() => void refreshHistory()}
            >
              {historyBusy ? "載入中…" : "查詢"}
            </button>
            <button
              type="button"
              className="ghost danger"
              disabled={historyBusy || selectedHistory.size === 0}
              onClick={() => void deleteSelectedHistory(false)}
            >
              刪除條目
            </button>
            <button
              type="button"
              className="ghost danger"
              disabled={historyBusy || selectedHistory.size === 0}
              onClick={() => void deleteSelectedHistory(true)}
            >
              刪除條目與檔案
            </button>
          </div>
          {error && <p className="error">{error}</p>}
          {historyRows.length === 0 ? (
            <div className="empty">
              <p>尚無歷史紀錄，完成下載後會出現在這裡。</p>
            </div>
          ) : (
            <ul className="jobs">
              {historyRows.map((row) => (
                <li key={row.id} className="job">
                  <div className="job-top">
                    <label className="check">
                      <input
                        type="checkbox"
                        checked={selectedHistory.has(row.id)}
                        onChange={(e) => {
                          setSelectedHistory((prev) => {
                            const next = new Set(prev);
                            if (e.target.checked) next.add(row.id);
                            else next.delete(row.id);
                            return next;
                          });
                        }}
                      />
                    </label>
                    {(historyCovers[row.id] || row.cover_url || row.thumbnail_url) && (
                      <img
                        className="history-cover"
                        src={
                          historyCovers[row.id] ||
                          row.cover_url ||
                          row.thumbnail_url ||
                          ""
                        }
                        alt=""
                        loading="lazy"
                      />
                    )}
                    <span className={`badge ${siteTone(row.site)}`}>{row.site}</span>
                    <div className="job-title">
                      <strong>{row.title || row.item_id || row.id}</strong>
                      <span>
                        {row.author ? `${row.author} · ` : ""}
                        {statusLabel(row.status)}
                        {row.created_at ? ` · ${row.created_at}` : ""}
                      </span>
                      <span>{row.canonical_url || row.source_url || ""}</span>
                    </div>
                    {row.output_dir && (
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => void openFolder(row.output_dir!)}
                      >
                        開啟資料夾
                      </button>
                    )}
                  </div>
                  {(row.tags || []).length > 0 && (
                    <p className="hint">標籤：{(row.tags || []).join(", ")}</p>
                  )}
                  {(row.files || []).length > 0 && (
                    <ul className="files">
                      {(row.files || []).slice(0, 8).map((f, idx) => (
                        <li key={`${row.id}-${idx}`} className="file muted">
                          <div className="file-meta">
                            <span className="name">
                              {f.role ? `[${f.role}] ` : ""}
                              {f.name || f.path || f.file_id}
                            </span>
                            <span className="stats">
                              {f.size != null ? formatBytes(f.size) : "—"}
                              {" · "}
                              {statusLabel(f.status || "done")}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : page === "settings" ? (
        <section className="composer settings" aria-label="設定">
          <div className="queue-head">
            <h2>設定</h2>
            <p>偏好會儲存在本機應用資料目錄</p>
          </div>

          <label className="field">
            <span>預設下載資料夾</span>
            <div className="folder-row">
              <input
                value={settingsDraft.downloadDir}
                readOnly
                placeholder="尚未設定"
              />
              <button
                type="button"
                className="ghost"
                onClick={() => void chooseFolder(true)}
              >
                瀏覽
              </button>
            </div>
          </label>

          <div className="settings-grid">
            <label className="field">
              <span>同時下載任務數</span>
              <input
                type="number"
                min={1}
                max={8}
                value={settingsDraft.maxConcurrent}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    maxConcurrent: Number(e.target.value) || 1,
                  }));
                  setSettingsDirty(true);
                }}
              />
            </label>
            <label className="field">
              <span>單一任務檔案並發</span>
              <input
                type="number"
                min={1}
                max={16}
                value={settingsDraft.workers}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    workers: Number(e.target.value) || 1,
                  }));
                  setSettingsDirty(true);
                }}
              />
            </label>
            <label className="field">
              <span>重複項目策略</span>
              <select
                value={settingsDraft.onDuplicate}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    onDuplicate: e.target.value as DuplicateMode,
                  }));
                  setSettingsDirty(true);
                }}
              >
                <option value="skip">略過（已下載）</option>
                <option value="overwrite">覆蓋</option>
                <option value="rename">重新命名</option>
              </select>
            </label>
          </div>

          <div className="settings-block">
            <h3>後處理（NFO／封面）</h3>
            <p className="hint">
              全域開關開啟後，再依站點勾選是否輸出；封面可選嵌入 mp4／mkv
            </p>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.writeNfo}
                onChange={(e) => {
                  setSettingsDraft((s) => ({ ...s, writeNfo: e.target.checked }));
                  setSettingsDirty(true);
                }}
              />
              <span>寫入 .nfo（Kodi／Jellyfin／Emby）</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.writeCover}
                onChange={(e) => {
                  setSettingsDraft((s) => ({ ...s, writeCover: e.target.checked }));
                  setSettingsDirty(true);
                }}
              />
              <span>下載封面／縮圖</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.embedCover}
                onChange={(e) => {
                  const on = e.target.checked;
                  setSettingsDraft((s) => ({
                    ...s,
                    embedCover: on,
                    writeCover: on ? true : s.writeCover,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>嵌入封面到影音檔（需 FFmpeg）</span>
            </label>
            <label className="field compact">
              <span>封面檔名</span>
              <select
                value={settingsDraft.coverNaming}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    coverNaming: e.target.value as CoverNaming,
                  }));
                  setSettingsDirty(true);
                }}
              >
                <option value="poster">{"<檔名>-poster.jpg"}</option>
                <option value="folder">folder.jpg</option>
              </select>
            </label>
            <div className="site-toggle-grid" aria-label="站點 NFO／封面開關">
              {site_ids.map((site) => (
                <div key={site} className="site-toggle-row">
                  <span className="site-toggle-name">{site}</span>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={settingsDraft.nfoSites[site] !== false}
                      disabled={!settingsDraft.writeNfo}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setSettingsDraft((s) => ({
                          ...s,
                          nfoSites: { ...s.nfoSites, [site]: checked },
                        }));
                        setSettingsDirty(true);
                      }}
                    />
                    <span>NFO</span>
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={settingsDraft.coverSites[site] !== false}
                      disabled={!settingsDraft.writeCover && !settingsDraft.embedCover}
                      onChange={(e) => {
                        const checked = e.target.checked;
                        setSettingsDraft((s) => ({
                          ...s,
                          coverSites: { ...s.coverSites, [site]: checked },
                        }));
                        setSettingsDirty(true);
                      }}
                    />
                    <span>封面</span>
                  </label>
                </div>
              ))}
            </div>
          </div>

          <div className="settings-block">
            <h3>Iwara</h3>
            <label className="field compact">
              <span>預設畫質</span>
              <select
                value={settingsDraft.iwaraQuality}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    iwaraQuality: e.target.value,
                  }));
                  setSettingsDirty(true);
                }}
              >
                <option value="Source">Source（最佳）</option>
                <option value="540">540</option>
                <option value="360">360</option>
              </select>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.iwaraCloudflareBypass}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    iwaraCloudflareBypass: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>Cloudflare 繞過（選用，預設關閉）</span>
            </label>
          </div>

          <div className="settings-block">
            <h3>Bilibili</h3>
            <div className="settings-grid">
              <label className="field compact">
                <span>預設分 P</span>
                <input
                  value={settingsDraft.bilibiliPart}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      bilibiliPart: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="all 或 1"
                />
              </label>
              <label className="field compact">
                <span>預設畫質 qn</span>
                <input
                  value={settingsDraft.bilibiliQuality}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      bilibiliQuality: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="空白＝最佳可匿名畫質"
                />
              </label>
            </div>
          </div>

          <div className="settings-block">
            <h3>FFmpeg</h3>
            <p className="hint">
              {ffmpegInfo?.found
                ? `已找到：${ffmpegInfo.path}（${ffmpegInfo.source || "unknown"}）`
                : ffmpegInfo?.message || "尚未找到 FFmpeg"}
            </p>
            <label className="field">
              <span>FFmpeg 路徑</span>
              <div className="folder-row">
                <input
                  value={settingsDraft.ffmpegPath}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      ffmpegPath: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="空白＝自動偵測／應用目錄"
                  spellCheck={false}
                />
                <button type="button" className="ghost" onClick={() => void chooseFfmpeg()}>
                  瀏覽
                </button>
              </div>
            </label>
            <div className="actions">
              <button
                type="button"
                className="ghost"
                disabled={ffmpegBusy || !ffmpegInfo?.provisionSupported}
                onClick={() => void installFfmpegNow()}
              >
                {ffmpegBusy ? "下載中…" : "下載靜態建置"}
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => void refreshFfmpeg()}
              >
                重新偵測
              </button>
            </div>
          </div>

          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={!settingsDirty}
              onClick={() => void saveSettingsNow()}
            >
              儲存設定
            </button>
          </div>
        </section>
      ) : (
        <>
          <section className="composer" aria-label="下載表單">
            <label className="field">
              <span>網址</span>
              <div className="url-row">
                <input
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onPaste={(e) => {
                    const text = e.clipboardData.getData("text");
                    const found = extractUrl(text);
                    if (found && found !== text.trim()) {
                      e.preventDefault();
                      setUrl(found);
                    }
                  }}
                  placeholder="gofile / bunkr / iwara / bilibili / mega.nz 連結"
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

            {clipboardHint && clipboardHint !== url.trim() && (
              <div className="clipboard-hint">
                <span>剪貼簿偵測到連結</span>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setUrl(clipboardHint);
                    setClipboardHint(null);
                  }}
                >
                  貼上
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => setClipboardHint(null)}
                >
                  忽略
                </button>
              </div>
            )}

            {resolveInfo?.site === "mega" && resolveInfo.supported && (
              <div className="site-options" aria-label="MEGA 選項">
                <p className="hint">
                  需本機已安裝 megatools（優先）或 MEGAcmd 的 mega-get。可用環境變數
                  UDL_MEGA_TOOL 指定路徑。不支援配額繞過。
                </p>
              </div>
            )}

            {resolveInfo?.site === "iwara" && resolveInfo.supported && (
              <div className="site-options" aria-label="Iwara 選項">
                <label className="field compact">
                  <span>畫質（目前使用設定預設：{iwaraQuality}）</span>
                  <select
                    value={settings.iwaraQuality}
                    onChange={(e) => {
                      const value = e.target.value;
                      setSettings((s) => ({ ...s, iwaraQuality: value }));
                      setSettingsDraft((s) => ({ ...s, iwaraQuality: value }));
                      setSettingsDirty(true);
                    }}
                  >
                    <option value="Source">Source（最佳）</option>
                    <option value="540">540</option>
                    <option value="360">360</option>
                  </select>
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={settings.iwaraCloudflareBypass}
                    onChange={(e) => {
                      const value = e.target.checked;
                      setSettings((s) => ({
                        ...s,
                        iwaraCloudflareBypass: value,
                      }));
                      setSettingsDraft((s) => ({
                        ...s,
                        iwaraCloudflareBypass: value,
                      }));
                      setSettingsDirty(true);
                    }}
                  />
                  <span>
                    Cloudflare 繞過（選用，預設關閉；需安裝 cloudscraper）
                  </span>
                </label>
              </div>
            )}

            {isBilibili && (
              <div className="bili-options" aria-label="Bilibili 選項">
                <label className="field">
                  <span>分 P / 集數</span>
                  <select
                    value={selectedPart}
                    onChange={(e) => setSelectedPart(e.target.value)}
                    disabled={probing || !probeInfo}
                  >
                    {probeInfo && probeInfo.parts.length > 1 && (
                      <option value="all">全部（{probeInfo.parts.length}）</option>
                    )}
                    {(probeInfo?.parts ?? []).map((p) => (
                      <option key={p.page} value={String(p.page)}>
                        P{p.page} · {p.title}
                        {p.badge ? ` （${p.badge}）` : ""}
                      </option>
                    ))}
                    {!probeInfo && (
                      <option value="all">{probing ? "解析中…" : "—"}</option>
                    )}
                  </select>
                </label>
                <label className="field">
                  <span>畫質</span>
                  <select
                    value={selectedQuality}
                    onChange={(e) => setSelectedQuality(e.target.value)}
                    disabled={probing || !probeInfo?.qualities?.length}
                  >
                    {(probeInfo?.qualities ?? []).map((q) => (
                      <option key={q.qn} value={String(q.qn)}>
                        {q.label}（qn {q.qn}）
                      </option>
                    ))}
                    {!probeInfo?.qualities?.length && (
                      <option value="">
                        {probing ? "偵測可匿名畫質…" : "尚無可用畫質"}
                      </option>
                    )}
                  </select>
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
                <button
                  type="button"
                  className="ghost"
                  onClick={() => void chooseFolder(false)}
                >
                  瀏覽
                </button>
              </div>
            </label>

            {probeInfo?.title && isBilibili && (
              <p className="hint">
                {probeInfo.title}
                {probing ? " · 更新中…" : ""}
              </p>
            )}
            {(probeInfo?.message || resolveInfo?.message) && (
              <p className="hint">{probeInfo?.message || resolveInfo?.message}</p>
            )}
            {error && <p className="error">{error}</p>}

            <div className="actions">
              <button
                type="button"
                className="primary"
                disabled={!canDownload}
                onClick={() => enqueueDownload()}
              >
                加入佇列
              </button>
            </div>
          </section>

          <section className="queue" aria-label="下載清單">
            <div className="queue-head">
              <h2>下載清單</h2>
              <p>
                {jobs.length === 0
                  ? "尚未有任務"
                  : `${jobs.length} 個任務 · 同時 ${settings.maxConcurrent} 個`}
              </p>
            </div>

            {jobs.length === 0 ? (
              <div className="empty">
                <p>貼上公開連結後加入佇列，進度與速度會顯示在這裡。</p>
              </div>
            ) : (
              <ul className="jobs">
                {jobs.map((job) => (
                  <li key={job.clientId} className="job">
                    <div className="job-top">
                      <span className={`badge ${siteTone(job.site)}`}>
                        {job.site}
                      </span>
                      <div className="job-title">
                        <strong>{job.title}</strong>
                        <span>{job.url}</span>
                      </div>
                      <div className="job-actions">
                        {job.status === "running" && (
                          <>
                            <button
                              type="button"
                              className="ghost"
                              onClick={() => void pauseJob(job)}
                            >
                              暫停
                            </button>
                            <button
                              type="button"
                              className="ghost danger"
                              onClick={() => void cancelJob(job)}
                            >
                              取消
                            </button>
                          </>
                        )}
                        {job.status === "queued" && (
                          <button
                            type="button"
                            className="ghost danger"
                            onClick={() => void cancelJob(job)}
                          >
                            取消
                          </button>
                        )}
                        {(job.status === "error" ||
                          job.status === "cancelled" ||
                          job.status === "paused") && (
                          <button
                            type="button"
                            className="ghost"
                            onClick={() => retryJob(job)}
                          >
                            重試
                          </button>
                        )}
                        {job.files.some((f) => f.path) && (
                          <button
                            type="button"
                            className="ghost"
                            onClick={() => {
                              const file = job.files.find((f) => f.path);
                              if (file?.path) void openFolder(file.path);
                            }}
                          >
                            開啟檔案
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
                      {job.files.length === 0 && job.status === "queued" && (
                        <li className="file muted">排隊等待中…</li>
                      )}
                      {job.files.map((file) => {
                        const pct =
                          file.total && file.total > 0
                            ? Math.min(100, (file.bytes / file.total) * 100)
                            : file.status === "done"
                              ? 100
                              : file.status === "merging"
                                ? 95
                                : 0;
                        return (
                          <li key={file.id} className="file">
                            <div className="file-meta">
                              <span className="name">{file.name}</span>
                              <span className="stats">
                                {formatBytes(file.bytes)}
                                {file.total != null
                                  ? ` / ${formatBytes(file.total)}`
                                  : ""}
                                {" · "}
                                {formatSpeed(file.bps)}
                                {" · "}
                                {file.alreadyDownloaded
                                  ? "已下載"
                                  : statusLabel(file.status)}
                              </span>
                            </div>
                            <div className="bar" aria-hidden>
                              <i
                                style={{
                                  width: `${
                                    file.status === "skipped" || file.alreadyDownloaded
                                      ? 100
                                      : pct
                                  }%`,
                                }}
                              />
                            </div>
                            {file.message && file.status === "error" && (
                              <p className="error">{file.message}</p>
                            )}
                            {file.message &&
                              (file.status === "skipped" || file.alreadyDownloaded) && (
                              <p className="hint">{file.message}</p>
                            )}
                            {(file.status === "done" || file.status === "skipped") &&
                              file.path && (
                              <button
                                type="button"
                                className="linkish"
                                onClick={() => void openFolder(file.path!)}
                              >
                                開啟此檔
                              </button>
                            )}
                          </li>
                        );
                      })}
                    </ul>

                    {job.status !== "running" && (
                      <p className="job-foot">
                        狀態：{statusLabel(job.status)}
                        {job.ok != null ? ` · 成功 ${job.ok}` : ""}
                        {job.failed != null ? ` · 失敗 ${job.failed}` : ""}
                        {job.message ? ` · ${job.message}` : ""}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
