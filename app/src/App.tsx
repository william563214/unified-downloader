import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { humanizeError, statusLabel } from "./errors";
import { formatBytes, parseSegments, siteTone } from "./format";
import { type JobRow, type QueueFilter, newClientId } from "./jobTypes";
import { QueueDashboard } from "./QueueDashboard";
import {
  type AppSettings,
  type ClassifyRule,
  type CoverNaming,
  type DuplicateMode,
  type FfmpegStatus,
  type HistoryEntry,
  type NameConflictMode,
  type NamingPreview,
  type QueueEntry,
  type RepairIssue,
  type RepairScanResult,
  default_settings,
  default_site_flags,
  emptyRule,
  exportSettingsJson,
  extractUrls,
  detectSite,
  history_cover_path,
  importSettingsJson,
  site_ids,
} from "./settings";
import { applyTheme, type ThemeMode } from "./theme";
import BilibiliAccount from "./BilibiliAccount";
import BilibiliParse from "./BilibiliParse";
import MegaAccount from "./MegaAccount";
import MegaParse from "./MegaParse";
import { GallerydlSitesPanel } from "./GallerydlSitesPanel";
import PageDownload from "./PageDownload";
import { AccountSection } from "./iwara/AccountSection";
import { BrowsePage } from "./iwara/BrowsePage";
import { HomePage } from "./iwara/HomePage";
import { SubscriptionsPage } from "./iwara/SubscriptionsPage";
import { SchedulerPage } from "./SchedulerPage";
import {
  type AfterCompletion,
  type PendingPowerAction,
  type SchedulerStatus,
  after_completion_labels,
} from "./scheduler";
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

type UdlEventPayload = {
  job_id: string;
  line: Record<string, unknown>;
};

type UpdaterInfo = {
  enabled: boolean;
  reason: string;
  pubkeyPresent: boolean;
};

type Page =
  | "download"
  | "page"
  | "bilibili"
  | "mega"
  | "account"
  | "history"
  | "repair"
  | "settings"
  | "schedule"
  | "iwara"
  | "iwara-subs"
  | "iwara-home";

async function notifyJob(title: string, body: string): Promise<void> {
  try {
    let granted = await isPermissionGranted();
    if (!granted) {
      const permission = await requestPermission();
      granted = permission === "granted";
    }
    if (granted) {
      sendNotification({ title, body });
      return;
    }
  } catch {
    /* fall through to web Notification */
  }
  try {
    if (typeof Notification !== "undefined") {
      if (Notification.permission === "granted") {
        new Notification(title, { body });
      } else if (Notification.permission !== "denied") {
        const p = await Notification.requestPermission();
        if (p === "granted") new Notification(title, { body });
      }
    }
  } catch {
    /* notifications optional */
  }
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
  const [proxyPassword, setProxyPassword] = useState("");
  const [clearProxyPasswordOnSave, setClearProxyPasswordOnSave] = useState(false);
  const [ffmpegInfo, setFfmpegInfo] = useState<FfmpegStatus | null>(null);
  const [ffmpegBusy, setFfmpegBusy] = useState(false);
  const [ffmpegPrompt, setFfmpegPrompt] = useState(false);
  const [clipboardHint, setClipboardHint] = useState<string | null>(null);
  const [clipboardUrls, setClipboardUrls] = useState<string[]>([]);
  const [queueFilter, setQueueFilter] = useState<QueueFilter>("all");
  const [selectedJobs, setSelectedJobs] = useState<Set<string>>(new Set());
  const [dragOver, setDragOver] = useState(false);
  const [updaterInfo, setUpdaterInfo] = useState<UpdaterInfo | null>(null);
  const [urlDraft, setUrlDraft] = useState("");
  const [historyRows, setHistoryRows] = useState<HistoryEntry[]>([]);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historySite, setHistorySite] = useState("");
  const [historyStatus, setHistoryStatus] = useState("");
  const [historyTag, setHistoryTag] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [historyCovers, setHistoryCovers] = useState<Record<string, string>>({});
  const [selectedHistory, setSelectedHistory] = useState<Set<string>>(new Set());
  const [namingPreview, setNamingPreview] = useState<string>("");
  const [namingPreviewBusy, setNamingPreviewBusy] = useState(false);
  const [rulesTestUrl, setRulesTestUrl] = useState("");
  const [rulesTestResult, setRulesTestResult] = useState<string>("");
  const [rulesTestBusy, setRulesTestBusy] = useState(false);
  const [repairResult, setRepairResult] = useState<RepairScanResult | null>(null);
  const [repairBusy, setRepairBusy] = useState(false);
  const [selectedRepair, setSelectedRepair] = useState<Set<string>>(new Set());
  const [repairVerifyHash, setRepairVerifyHash] = useState(false);
  const [relinkPath, setRelinkPath] = useState("");
  const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(
    null,
  );
  const [scheduleNotice, setScheduleNotice] = useState<string | null>(null);
  const [pendingPower, setPendingPower] = useState<PendingPowerAction | null>(
    null,
  );
  const startingRef = useRef(false);
  const resumedRef = useRef(false);
  const jobsRef = useRef<JobRow[]>([]);
  const settingsRef = useRef(settings);
  const notifiedRef = useRef<Set<string>>(new Set());
  const afterFiredRef = useRef<Set<string>>(new Set());
  const powerTimerRef = useRef<number | null>(null);

  const isBilibili = resolveInfo?.site === "bilibili" && resolveInfo.supported;
  const detectedUrls = useMemo(() => extractUrls(urlDraft || url), [urlDraft, url]);
  const primaryDetected = detectedUrls[0] ? detectSite(detectedUrls[0]) : null;

  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  useEffect(() => {
    applyTheme((settings.themeMode || "system") as ThemeMode);
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme((settingsRef.current.themeMode || "system") as ThemeMode);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [settings.themeMode]);

  useEffect(() => {
    void invoke<UpdaterInfo>("updater_status")
      .then(setUpdaterInfo)
      .catch(() =>
        setUpdaterInfo({
          enabled: false,
          reason: "無法讀取更新狀態",
          pubkeyPresent: false,
        }),
      );
  }, []);

  useEffect(() => {
    const active = jobs.filter((j) => j.status === "running" || j.status === "queued").length;
    const failed = jobs.filter((j) => j.status === "error").length;
    const tip = `整合下載器 · 進行 ${active} · 失敗 ${failed}`;
    void invoke("set_tray_tooltip", { text: tip }).catch(() => {});
  }, [jobs]);

  const canDownload = useMemo(() => {
    const out = Boolean(output || settings.downloadDir);
    if (!out || resolving || probing) return false;
    if (detectedUrls.length > 1) {
      return detectedUrls.some((u) => detectSite(u) != null);
    }
    return Boolean((urlDraft || url).trim()) && resolveInfo?.supported === true;
  }, [
    url,
    urlDraft,
    output,
    settings.downloadDir,
    resolveInfo,
    resolving,
    probing,
    detectedUrls,
  ]);

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
          nameTemplate: loaded.nameTemplate ?? default_settings.nameTemplate,
          siteTemplates: loaded.siteTemplates || {},
          typeTemplates: loaded.typeTemplates || {},
          nameConflict: loaded.nameConflict || default_settings.nameConflict,
          classificationRules: Array.isArray(loaded.classificationRules)
            ? loaded.classificationRules
            : [],
          megaBackend:
            loaded.megaBackend === "external" ? "external" : "builtin",
          megaToolPath: loaded.megaToolPath ?? "",
          gallerydlEnabled:
            loaded.gallerydlEnabled == null ? true : Boolean(loaded.gallerydlEnabled),
          gallerydlToolPath: loaded.gallerydlToolPath ?? "",
          gallerydlCookiesPath: loaded.gallerydlCookiesPath ?? "",
          gallerydlOptions: loaded.gallerydlOptions ?? "",
          gallerydlUseArchive: Boolean(loaded.gallerydlUseArchive),
          clipboardWatcher: Boolean(loaded.clipboardWatcher),
          notificationsEnabled:
            loaded.notificationsEnabled == null
              ? true
              : Boolean(loaded.notificationsEnabled),
          minimizeToTray: Boolean(loaded.minimizeToTray),
          themeMode: loaded.themeMode || "system",
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
            const segments = parseSegments(line.segments);
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
                      segments: segments ?? f.segments,
                      connections:
                        typeof line.connections === "number"
                          ? line.connections
                          : f.connections,
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

  const refreshScheduler = useCallback(async () => {
    try {
      const status = await invoke<SchedulerStatus>("schedule_status", {
        speedLimit: settings.speedLimit || null,
      });
      setSchedulerStatus(status);
    } catch {
      /* schedule optional when sidecar missing */
    }
  }, [settings.speedLimit]);

  useEffect(() => {
    void refreshScheduler();
    const timer = window.setInterval(() => {
      void refreshScheduler();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refreshScheduler]);

  const cancelPendingPower = useCallback(() => {
    if (powerTimerRef.current != null) {
      window.clearInterval(powerTimerRef.current);
      powerTimerRef.current = null;
    }
    setPendingPower(null);
  }, []);

  const beginAfterAction = useCallback(
    (action: AfterCompletion, queueName: string) => {
      if (action === "none") return;
      if (action === "notify") {
        setScheduleNotice(`佇列「${queueName}」已完成全部任務`);
        return;
      }
      cancelPendingPower();
      setPendingPower({ action, queueName, seconds: 30 });
      powerTimerRef.current = window.setInterval(() => {
        setPendingPower((prev) => {
          if (!prev) return null;
          if (prev.seconds <= 1) {
            if (powerTimerRef.current != null) {
              window.clearInterval(powerTimerRef.current);
              powerTimerRef.current = null;
            }
            void (async () => {
              try {
                if (prev.action === "quit") {
                  const { getCurrentWindow } = await import(
                    "@tauri-apps/api/window"
                  );
                  await getCurrentWindow().close();
                  return;
                }
                await invoke("system_power_action", { action: prev.action });
              } catch (e) {
                setError(humanizeError(String(e)));
              }
            })();
            return null;
          }
          return { ...prev, seconds: prev.seconds - 1 };
        });
      }, 1000);
    },
    [cancelPendingPower],
  );

  const launchJob = useCallback(
    async (job: JobRow) => {
      const queueName = job.queueName || "default";
      const runtime = schedulerStatus?.queues.find((q) => q.name === queueName);
      const speedLimit =
        runtime?.speed_limit ||
        schedulerStatus?.global_speed_limit ||
        settings.speedLimit ||
        null;
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
          codec: job.codec ?? null,
          audioQuality: job.audioQuality ?? null,
          container: job.container ?? null,
          danmaku: job.danmaku ?? null,
          subtitle: job.subtitle ?? null,
          subtitleLang: job.subtitleLang ?? null,
          embedDanmaku: job.embedDanmaku ?? false,
          embedSubtitle: job.embedSubtitle ?? false,
          filterTitle: job.filterTitle ?? null,
          partRange: job.partRange ?? null,
          expandAll: job.expandAll ?? false,
          nodeNames: job.nodeNames ?? null,
          megaSelect: job.megaSelect ?? null,
          speedLimit,
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
    [
      settings.ffmpegPath,
      settings.workers,
      settings.onDuplicate,
      settings.speedLimit,
      schedulerStatus,
    ],
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
              queueName: r.queue_name || "default",
              files: [],
              status: "queued" as const,
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
    if (schedulerStatus?.quota?.exceeded) return;
    const queued = jobs.filter((j) => j.status === "queued");
    if (queued.length === 0 || startingRef.current) return;

    const byQueue = new Map<string, JobRow[]>();
    for (const job of queued) {
      const name = job.queueName || "default";
      const list = byQueue.get(name) || [];
      list.push(job);
      byQueue.set(name, list);
    }

    const toStart: JobRow[] = [];
    for (const [name, list] of byQueue) {
      const runtime = schedulerStatus?.queues.find((q) => q.name === name);
      // without scheduler data fall back to settings; inactive queues wait
      if (schedulerStatus && runtime && !runtime.active) continue;
      const maxConc = runtime?.max_concurrent ?? settings.maxConcurrent;
      const running = jobs.filter(
        (j) => j.status === "running" && (j.queueName || "default") === name,
      ).length;
      const slots = Math.max(1, maxConc) - running;
      if (slots <= 0) continue;
      toStart.push(...list.slice(0, slots));
    }
    if (toStart.length === 0) return;
    startingRef.current = true;
    void (async () => {
      for (const job of toStart) {
        await launchJob(job);
      }
      startingRef.current = false;
    })();
  }, [jobs, settings.maxConcurrent, launchJob, schedulerStatus]);

  useEffect(() => {
    if (!schedulerStatus) return;
    for (const runtime of schedulerStatus.queues) {
      if (runtime.after_completion === "none") continue;
      const name = runtime.name;
      const related = jobs.filter((j) => (j.queueName || "default") === name);
      if (related.length === 0) continue;
      const unfinished = related.some(
        (j) => j.status === "queued" || j.status === "running" || j.status === "paused",
      );
      if (unfinished) {
        afterFiredRef.current.delete(name);
        continue;
      }
      const allTerminal = related.every((j) =>
        ["done", "error", "cancelled"].includes(j.status),
      );
      if (!allTerminal || afterFiredRef.current.has(name)) continue;
      afterFiredRef.current.add(name);
      beginAfterAction(runtime.after_completion, name);
      if (runtime.queue.repeat_mode === "once") {
        void invoke("schedule_complete_once", { name })
          .then(() => refreshScheduler())
          .catch(() => {
            /* ignore */
          });
      }
    }
  }, [jobs, schedulerStatus, beginAfterAction, refreshScheduler]);

  useEffect(() => {
    for (const job of jobs) {
      if (job.status !== "done" && job.status !== "error") continue;
      const key = `${job.clientId}:${job.status}:${job.id || ""}`;
      if (notifiedRef.current.has(key)) continue;
      notifiedRef.current.add(key);
      if (!settings.notificationsEnabled) continue;
      if (job.status === "done") {
        void notifyJob("下載完成", `${job.site} · ${job.title}`);
      } else {
        void notifyJob("下載失敗", `${job.site} · ${job.message || job.title}`);
      }
    }
  }, [jobs, settings.notificationsEnabled]);

  useEffect(() => {
    const offerClipboard = async () => {
      if (!settings.clipboardWatcher) return;
      try {
        if (!navigator.clipboard?.readText) return;
        const text = await navigator.clipboard.readText();
        const found = extractUrls(text);
        if (found.length === 0) return;
        const known = new Set(
          [url, urlDraft, ...jobsRef.current.map((j) => j.url)].map((s) => s.trim()),
        );
        const fresh = found.filter((u) => !known.has(u));
        if (fresh.length === 0) return;
        setClipboardUrls(fresh);
        setClipboardHint(fresh[0]);
      } catch {
        /* clipboard permission optional */
      }
    };
    // watcher off by default; when on, poll + focus
    void offerClipboard();
    const interval = settings.clipboardWatcher
      ? window.setInterval(() => void offerClipboard(), 4000)
      : 0;
    const onFocus = () => {
      if (settings.clipboardWatcher) void offerClipboard();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      if (interval) window.clearInterval(interval);
    };
  }, [settings.clipboardWatcher, url, urlDraft]);

  useEffect(() => {
    const onDragOver = (e: DragEvent) => {
      if (!e.dataTransfer) return;
      const types = Array.from(e.dataTransfer.types || []);
      if (types.includes("text/uri-list") || types.includes("text/plain") || types.includes("Files")) {
        e.preventDefault();
        setDragOver(true);
      }
    };
    const onDragLeave = (e: DragEvent) => {
      if (e.target === document.documentElement || e.target === document.body) {
        setDragOver(false);
      }
    };
    const onDrop = (e: DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const text =
        e.dataTransfer?.getData("text/uri-list") ||
        e.dataTransfer?.getData("text/plain") ||
        "";
      const found = extractUrls(text);
      if (found.length === 0) return;
      setUrlDraft(found.join("\n"));
      setUrl(found[0]);
      setPage("download");
    };
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, []);

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

  function enqueueOne(targetUrl: string, overrides?: Partial<JobRow>) {
    const detected = detectSite(targetUrl);
    const site =
      overrides?.site ??
      (targetUrl === url.trim() ? resolveInfo?.site : null) ??
      detected?.site ??
      "unknown";
    const job: JobRow = {
      id: "",
      clientId: newClientId(),
      url: targetUrl,
      site,
      title:
        overrides?.title ??
        (targetUrl === url.trim() ? probeInfo?.title : null) ??
        (targetUrl === url.trim() ? resolveInfo?.label : null) ??
        detected?.label ??
        "下載中",
      output: overrides?.output ?? (output || settings.downloadDir),
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
      codec: overrides?.codec ?? null,
      audioQuality: overrides?.audioQuality ?? null,
      container: overrides?.container ?? null,
      danmaku: overrides?.danmaku ?? null,
      subtitle: overrides?.subtitle ?? null,
      subtitleLang: overrides?.subtitleLang ?? null,
      embedDanmaku: overrides?.embedDanmaku ?? false,
      embedSubtitle: overrides?.embedSubtitle ?? false,
      filterTitle: overrides?.filterTitle ?? null,
      partRange: overrides?.partRange ?? null,
      expandAll: overrides?.expandAll ?? false,
      nodeNames: overrides?.nodeNames ?? null,
      megaSelect: overrides?.megaSelect ?? null,
      queueName: overrides?.queueName ?? "default",
      files: [],
      status: "queued",
    };
    setJobs((prev) => [job, ...prev]);
  }

  function enqueueDownload(overrides?: Partial<JobRow>) {
    const targets = overrides?.url
      ? [overrides.url]
      : detectedUrls.length > 0
        ? detectedUrls
        : url.trim()
          ? [url.trim()]
          : [];
    if (targets.length === 0) return;
    if (!overrides?.url && !canDownload) return;
    setError(null);
    for (const target of targets) {
      enqueueOne(target, overrides);
    }
    setClipboardHint(null);
    setClipboardUrls([]);
  }

  function reorderJob(clientId: string, direction: -1 | 1) {
    setJobs((prev) => {
      const index = prev.findIndex((j) => j.clientId === clientId);
      if (index < 0) return prev;
      const next = index + direction;
      if (next < 0 || next >= prev.length) return prev;
      const copy = [...prev];
      const tmp = copy[index];
      copy[index] = copy[next];
      copy[next] = tmp;
      return copy;
    });
  }

  function toggleJobSelect(clientId: string) {
    setSelectedJobs((prev) => {
      const next = new Set(prev);
      if (next.has(clientId)) next.delete(clientId);
      else next.add(clientId);
      return next;
    });
  }

  async function persistConcurrency(n: number) {
    const value = Math.min(8, Math.max(1, n || 1));
    setSettings((s) => ({ ...s, maxConcurrent: value }));
    setSettingsDraft((s) => ({ ...s, maxConcurrent: value }));
    try {
      const saved = await invoke<AppSettings>("save_settings", {
        settings: { ...settings, maxConcurrent: value },
      });
      setSettings(saved);
      setSettingsDraft(saved);
    } catch (e) {
      setError(humanizeError(String(e)));
    }
  }

  function enqueueFromMegaParse(opts: {
    url: string;
    selectedHandles: string[];
  }) {
    enqueueDownload({
      url: opts.url,
      site: "mega",
      title: "MEGA",
      output,
      megaSelect:
        opts.selectedHandles.length > 0
          ? opts.selectedHandles.join(",")
          : null,
    });
    setPage("download");
  }

  function enqueueFromBilibiliParse(opts: {
    url: string;
    quality: string | null;
    part: string | null;
    codec: string | null;
    audioQuality: string | null;
    container: string;
    danmaku: string | null;
    subtitle: string | null;
    subtitleLang: string | null;
    embedDanmaku: boolean;
    embedSubtitle: boolean;
    filterTitle: string | null;
    partRange: string | null;
    checkedPages: number[];
    expandAll?: boolean;
    nodeNames?: string | null;
  }) {
    const part =
      opts.expandAll || (opts.partRange && opts.checkedPages.length > 1)
        ? "all"
        : opts.part;
    enqueueDownload({
      url: opts.url,
      site: "bilibili",
      title: "Bilibili",
      output,
      quality: opts.quality,
      part,
      codec: opts.codec,
      audioQuality: opts.audioQuality,
      container: opts.container,
      danmaku: opts.danmaku,
      subtitle: opts.subtitle,
      subtitleLang: opts.subtitleLang,
      embedDanmaku: opts.embedDanmaku,
      embedSubtitle: opts.embedSubtitle,
      filterTitle: opts.filterTitle,
      partRange: opts.partRange,
      expandAll: opts.expandAll ?? false,
      nodeNames: opts.nodeNames ?? null,
    });
    setPage("download");
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

  function resumeJob(job: JobRow) {
    retryJob(job);
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
      if (clearProxyPasswordOnSave) {
        await invoke("clear_proxy_password");
      } else if (proxyPassword.trim()) {
        await invoke("set_proxy_password", { password: proxyPassword });
      }
      const saved = await invoke<AppSettings>("save_settings", {
        settings: settingsDraft,
      });
      setSettings(saved);
      setSettingsDraft(saved);
      setSettingsDirty(false);
      setProxyPassword("");
      setClearProxyPasswordOnSave(false);
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

  async function refreshNamingPreview(template?: string) {
    const tpl =
      template ??
      (settingsDraft.nameTemplate ||
        "{site}/{author}/{date:%Y-%m}/{title} [{id}].{ext}");
    setNamingPreviewBusy(true);
    try {
      const result = await invoke<NamingPreview>("naming_preview", {
        template: tpl,
        name: "example.mp4",
      });
      setNamingPreview(result.relative || "");
    } catch (e) {
      setNamingPreview(humanizeError(String(e)));
    } finally {
      setNamingPreviewBusy(false);
    }
  }

  async function runRulesTest() {
    const target = rulesTestUrl.trim() || url.trim();
    if (!target) {
      setRulesTestResult("請輸入要測試的 URL");
      return;
    }
    setRulesTestBusy(true);
    try {
      // persist draft rules first so core config matches editor
      await invoke("save_settings", { settings: settingsDraft });
      const result = await invoke<{
        decision?: { matched?: boolean; rule_name?: string; skip?: boolean; folder?: string; template?: string; message?: string };
        evaluated?: Array<{ name?: string; matches?: boolean }>;
      }>("rules_test", { url: target });
      const d = result.decision;
      if (!d?.matched) {
        setRulesTestResult("沒有規則命中");
      } else {
        setRulesTestResult(
          [
            d.message || `命中：${d.rule_name || ""}`,
            d.skip ? "動作：略過" : null,
            d.folder ? `資料夾：${d.folder}` : null,
            d.template ? `模板：${d.template}` : null,
          ]
            .filter(Boolean)
            .join(" · "),
        );
      }
    } catch (e) {
      setRulesTestResult(humanizeError(String(e)));
    } finally {
      setRulesTestBusy(false);
    }
  }

  function updateRule(index: number, patch: Partial<ClassifyRule>) {
    setSettingsDraft((s) => {
      const rules = [...(s.classificationRules || [])];
      rules[index] = { ...rules[index], ...patch };
      return { ...s, classificationRules: rules };
    });
    setSettingsDirty(true);
  }

  function moveRule(index: number, delta: number) {
    setSettingsDraft((s) => {
      const rules = [...(s.classificationRules || [])];
      const next = index + delta;
      if (next < 0 || next >= rules.length) return s;
      const tmp = rules[index];
      rules[index] = rules[next];
      rules[next] = tmp;
      return { ...s, classificationRules: rules };
    });
    setSettingsDirty(true);
  }

  async function runRepairScan() {
    setRepairBusy(true);
    setError(null);
    try {
      const result = await invoke<RepairScanResult>("repair_scan", {
        verifyHash: repairVerifyHash,
        noOrphans: false,
        dirs: settings.downloadDir ? [settings.downloadDir] : [],
      });
      setRepairResult(result);
      setSelectedRepair(new Set());
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setRepairBusy(false);
    }
  }

  async function runRepairFix(action: string) {
    if (!repairResult) return;
    const selected = repairResult.issues.filter((i) => selectedRepair.has(i.id));
    if (selected.length === 0) {
      setError("請先勾選要處理的項目");
      return;
    }
    setRepairBusy(true);
    setError(null);
    try {
      await invoke("repair_fix", {
        action,
        issues: selected,
        jobIds: selected.map((i) => i.job_id).filter(Boolean),
        fileRowIds: selected
          .map((i) => i.file_row_id)
          .filter((v): v is number => typeof v === "number"),
        relinkPath: action === "relink" ? relinkPath : undefined,
        output: settings.downloadDir || output || undefined,
      });
      await runRepairScan();
    } catch (e) {
      setError(humanizeError(String(e)));
    } finally {
      setRepairBusy(false);
    }
  }

  useEffect(() => {
    if (page !== "settings") return;
    const handle = window.setTimeout(() => {
      void refreshNamingPreview();
    }, 280);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, settingsDraft.nameTemplate]);

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
    <div className={dragOver ? "app drag-over" : "app"}>
      {dragOver && (
        <div className="drop-overlay" aria-hidden>
          <p>放開以加入連結</p>
        </div>
      )}
      <header className="hero shell-hero">
        <div className="brand-mark" aria-hidden />
        <div className="brand-copy">
          <p className="brand">整合下載器</p>
          <h1>統一佇列，跨站下載</h1>
          <p className="lede">
            貼上或多筆拖放網址；進度、暫停與修復集中管理。
          </p>
        </div>
        <nav className="top-nav" aria-label="主要導覽">
          <button
            type="button"
            className={page === "download" ? "nav active" : "nav"}
            onClick={() => setPage("download")}
          >
            佇列
          </button>
          <button
            type="button"
            className={page === "page" ? "nav active" : "nav"}
            onClick={() => setPage("page")}
          >
            頁面
          </button>
          <button
            type="button"
            className={page === "bilibili" ? "nav active" : "nav"}
            onClick={() => setPage("bilibili")}
          >
            Bilibili
          </button>
          <button
            type="button"
            className={page === "mega" ? "nav active" : "nav"}
            onClick={() => setPage("mega")}
          >
            MEGA
          </button>
          <button
            type="button"
            className={page === "account" ? "nav active" : "nav"}
            onClick={() => setPage("account")}
          >
            帳號
          </button>
          <button
            type="button"
            className={page === "iwara" ? "nav active" : "nav"}
            onClick={() => setPage("iwara")}
          >
            Iwara
          </button>
          <button
            type="button"
            className={page === "iwara-home" ? "nav active" : "nav"}
            onClick={() => setPage("iwara-home")}
          >
            首頁
          </button>
          <button
            type="button"
            className={page === "iwara-subs" ? "nav active" : "nav"}
            onClick={() => setPage("iwara-subs")}
          >
            訂閱
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
            className={page === "schedule" ? "nav active" : "nav"}
            onClick={() => setPage("schedule")}
          >
            排程
          </button>
          <button
            type="button"
            className={page === "repair" ? "nav active" : "nav"}
            onClick={() => setPage("repair")}
          >
            修復
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

      {scheduleNotice && (
        <section className="banner" role="status">
          <div>
            <strong>排程通知</strong>
            <p>{scheduleNotice}</p>
          </div>
          <button type="button" className="ghost" onClick={() => setScheduleNotice(null)}>
            關閉
          </button>
        </section>
      )}

      {pendingPower && (
        <div className="power-modal" role="alertdialog" aria-modal="true">
          <div className="power-dialog">
            <h2>確認{after_completion_labels[pendingPower.action]}</h2>
            <p>
              佇列「{pendingPower.queueName}」已完成。將在 {pendingPower.seconds}{" "}
              秒後執行「{after_completion_labels[pendingPower.action]}」。
            </p>
            <div className="row gap">
              <button type="button" className="danger" onClick={cancelPendingPower}>
                取消
              </button>
            </div>
          </div>
        </div>
      )}

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

      {page === "page" ? (
        <div className="page-shell">
          <PageDownload
            output={output || settings.downloadDir}
            onEnqueue={(jobs) => {
              for (const job of jobs) {
                enqueueDownload({
                  url: job.url,
                  site: job.site,
                  title: job.title,
                  output: output || settings.downloadDir,
                });
              }
              setPage("download");
            }}
          />
        </div>
      ) : page === "schedule" ? (
        <div className="page-shell">
          <SchedulerPage onStatus={setSchedulerStatus} />
        </div>
      ) : page === "bilibili" ? (
        <div className="page-shell">
          <BilibiliParse
            settings={settings}
            output={output || settings.downloadDir}
            onEnqueue={enqueueFromBilibiliParse}
          />
        </div>
      ) : page === "mega" ? (
        <div className="page-shell">
          <MegaParse
            settings={settings}
            output={output || settings.downloadDir}
            onEnqueue={enqueueFromMegaParse}
          />
        </div>
      ) : page === "account" ? (
        <div className="page-shell">
          <BilibiliAccount />
          <MegaAccount />
        </div>
      ) : page === "iwara" ? (
        <div className="page-shell">
          <BrowsePage
            downloadDir={settings.downloadDir}
            cloudflareBypass={Boolean(settings.iwaraCloudflareBypass)}
            onEnqueueStarted={() => setPage("download")}
          />
        </div>
      ) : page === "iwara-home" ? (
        <div className="page-shell">
          <HomePage
            downloadDir={settings.downloadDir}
            cloudflareBypass={Boolean(settings.iwaraCloudflareBypass)}
            onEnqueueStarted={() => setPage("download")}
          />
        </div>
      ) : page === "iwara-subs" ? (
        <div className="page-shell">
          <SubscriptionsPage
            downloadDir={settings.downloadDir}
            cloudflareBypass={Boolean(settings.iwaraCloudflareBypass)}
            onEnqueueStarted={() => setPage("download")}
          />
        </div>
      ) : page === "history" ? (
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
                <option value="gallerydl">gallery-dl</option>
                <option value="direct">Direct</option>
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
      ) : page === "repair" ? (
        <section className="composer settings" aria-label="修復中心">
          <div className="queue-head">
            <h2>修復中心</h2>
            <p>比對歷史資料庫與檔案系統：缺檔、半成品、大小／hash 不符、orphan</p>
          </div>
          <label className="check">
            <input
              type="checkbox"
              checked={repairVerifyHash}
              onChange={(e) => setRepairVerifyHash(e.target.checked)}
            />
            <span>掃描時驗證 sha256（較慢）</span>
          </label>
          <div className="actions">
            <button
              type="button"
              className="primary"
              disabled={repairBusy}
              onClick={() => void runRepairScan()}
            >
              {repairBusy ? "掃描中…" : "開始掃描"}
            </button>
            <button
              type="button"
              className="ghost"
              disabled={repairBusy || selectedRepair.size === 0}
              onClick={() => void runRepairFix("requeue")}
            >
              重新排隊
            </button>
            <button
              type="button"
              className="ghost"
              disabled={repairBusy || selectedRepair.size === 0}
              onClick={() => void runRepairFix("cleanup-partials")}
            >
              清理 .part
            </button>
            <button
              type="button"
              className="ghost danger"
              disabled={repairBusy || selectedRepair.size === 0}
              onClick={() => void runRepairFix("remove-stale")}
            >
              移除過期紀錄
            </button>
          </div>
          <label className="field">
            <span>重新連結路徑（relink）</span>
            <div className="folder-row">
              <input
                value={relinkPath}
                onChange={(e) => setRelinkPath(e.target.value)}
                placeholder="選取後填入實際檔案路徑"
                spellCheck={false}
              />
              <button
                type="button"
                className="ghost"
                disabled={repairBusy || selectedRepair.size === 0 || !relinkPath.trim()}
                onClick={() => void runRepairFix("relink")}
              >
                重新連結
              </button>
            </div>
          </label>
          {error && <p className="error">{error}</p>}
          {repairResult ? (
            <>
              <p className="hint">
                掃描 jobs={repairResult.scanned_jobs}、files=
                {repairResult.scanned_files}、問題={repairResult.issue_count}
              </p>
              {repairResult.issues.length === 0 ? (
                <div className="empty">
                  <p>沒有發現問題。</p>
                </div>
              ) : (
                <ul className="jobs">
                  {repairResult.issues.map((issue: RepairIssue) => (
                    <li key={issue.id} className="job">
                      <div className="job-top">
                        <label className="check">
                          <input
                            type="checkbox"
                            checked={selectedRepair.has(issue.id)}
                            onChange={(e) => {
                              setSelectedRepair((prev) => {
                                const next = new Set(prev);
                                if (e.target.checked) next.add(issue.id);
                                else next.delete(issue.id);
                                return next;
                              });
                            }}
                          />
                        </label>
                        <span className={`badge ${siteTone(issue.site)}`}>
                          {issue.kind}
                        </span>
                        <div className="job-title">
                          <strong>{issue.title || issue.path || issue.id}</strong>
                          <span>
                            {issue.site ? `${issue.site} · ` : ""}
                            {issue.message || ""}
                          </span>
                          <span>{issue.path || issue.source_url || ""}</span>
                        </div>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <div className="empty">
              <p>尚未掃描。點「開始掃描」檢查歷史與檔案是否一致。</p>
            </div>
          )}
        </section>
      ) : page === "settings" ? (
        <section className="composer settings" aria-label="設定">
          <div className="queue-head">
            <h2>設定</h2>
            <p>單一設定頁：下載目錄、命名、後處理、網路、帳號、工具、外觀與更新</p>
          </div>

          <div className="settings-toc" aria-label="設定區塊">
            <a href="#settings-download">下載</a>
            <a href="#settings-naming">命名／規則</a>
            <a href="#settings-postprocess">後處理</a>
            <a href="#settings-network">網路</a>
            <a href="#settings-accounts">帳號</a>
            <a href="#settings-tools">工具</a>
            <a href="#settings-shell">通知／托盤</a>
            <a href="#settings-import">匯入匯出</a>
          </div>

          <div className="settings-block" id="settings-download">
            <h3>下載目錄與並發</h3>
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

          </div>

          <div className="settings-block" id="settings-postprocess">
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

          <div className="settings-block" id="settings-accounts">
            <h3>帳號 · Iwara</h3>
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
            <AccountSection
              cloudflareBypass={Boolean(settingsDraft.iwaraCloudflareBypass)}
            />
          </div>

          <div className="settings-block">
            <h3>帳號 · Bilibili</h3>
            <p className="hint">
              QR／Cookie 登入請到導覽「帳號」頁；此處為下載預設。
            </p>
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

          <div className="settings-block" id="settings-tools">
            <h3>工具 · FFmpeg</h3>
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

          <div className="settings-block">
            <h3>工具 · MEGA</h3>
            <p className="hint">
              預設內建 Python 客戶端。外部後端需 megatools／MEGAcmd（--mega-tool／UDL_MEGA_TOOL）。不支援配額繞過。
            </p>
            <label className="field compact">
              <span>後端</span>
              <select
                value={settingsDraft.megaBackend}
                onChange={(e) => {
                  const value = e.target.value === "external" ? "external" : "builtin";
                  setSettingsDraft((s) => ({ ...s, megaBackend: value }));
                  setSettingsDirty(true);
                }}
              >
                <option value="builtin">內建（預設）</option>
                <option value="external">外部工具（megatools／MEGAcmd）</option>
              </select>
            </label>
            {settingsDraft.megaBackend === "external" && (
              <label className="field">
                <span>MEGA 工具路徑</span>
                <div className="folder-row">
                  <input
                    value={settingsDraft.megaToolPath}
                    onChange={(e) => {
                      setSettingsDraft((s) => ({
                        ...s,
                        megaToolPath: e.target.value,
                      }));
                      setSettingsDirty(true);
                    }}
                    placeholder="空白＝自動尋找 PATH"
                    spellCheck={false}
                  />
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => {
                      void (async () => {
                        const selected = await open({
                          multiple: false,
                          title: "選擇 MEGA 工具",
                        });
                        if (typeof selected === "string") {
                          setSettingsDraft((s) => ({
                            ...s,
                            megaToolPath: selected,
                          }));
                          setSettingsDirty(true);
                        }
                      })();
                    }}
                  >
                    瀏覽
                  </button>
                </div>
              </label>
            )}
          </div>

          <div className="settings-block">
            <h3>工具 · gallery-dl</h3>
            <p className="hint">
              GPL-2.0-only，僅以獨立子行程呼叫（不 import）。原生站點（Gofile／Bunkr／Iwara／Bilibili／MEGA）優先；其餘由 gallery-dl 辨識的網址走此備援，再其次才是 Direct。帳密請用
              <code> udl secrets set gallerydl.username </code>
              等 keychain 鍵，勿寫入設定檔。不做付費牆／DRM／Cloudflare 繞過。
            </p>
            <label className="field compact">
              <span>啟用</span>
              <select
                value={settingsDraft.gallerydlEnabled ? "on" : "off"}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    gallerydlEnabled: e.target.value === "on",
                  }));
                  setSettingsDirty(true);
                }}
              >
                <option value="on">啟用</option>
                <option value="off">停用</option>
              </select>
            </label>
            <label className="field">
              <span>gallery-dl 路徑</span>
              <div className="folder-row">
                <input
                  value={settingsDraft.gallerydlToolPath}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      gallerydlToolPath: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="空白＝自動偵測（bundled／PATH／python -m）"
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    void (async () => {
                      const selected = await open({
                        multiple: false,
                        title: "選擇 gallery-dl",
                      });
                      if (typeof selected === "string") {
                        setSettingsDraft((s) => ({
                          ...s,
                          gallerydlToolPath: selected,
                        }));
                        setSettingsDirty(true);
                      }
                    })();
                  }}
                >
                  瀏覽
                </button>
              </div>
            </label>
            <label className="field">
              <span>Cookies 檔（Netscape）</span>
              <div className="folder-row">
                <input
                  value={settingsDraft.gallerydlCookiesPath}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      gallerydlCookiesPath: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="選用 cookies.txt"
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    void (async () => {
                      const selected = await open({
                        multiple: false,
                        title: "選擇 cookies.txt",
                      });
                      if (typeof selected === "string") {
                        setSettingsDraft((s) => ({
                          ...s,
                          gallerydlCookiesPath: selected,
                        }));
                        setSettingsDirty(true);
                      }
                    })();
                  }}
                >
                  瀏覽
                </button>
              </div>
            </label>
            <label className="field">
              <span>per-site 選項（每行 KEY=VALUE → gallery-dl -o）</span>
              <textarea
                value={settingsDraft.gallerydlOptions}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    gallerydlOptions: e.target.value,
                  }));
                  setSettingsDirty(true);
                }}
                rows={4}
                placeholder={"extractor.twitter.retweets=false\nextractor.reddit.comments=0"}
                spellCheck={false}
              />
            </label>
            <label className="field compact">
              <span>使用 gallery-dl archive（選用／對應去重）</span>
              <select
                value={settingsDraft.gallerydlUseArchive ? "on" : "off"}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    gallerydlUseArchive: e.target.value === "on",
                  }));
                  setSettingsDirty(true);
                }}
              >
                <option value="off">關閉</option>
                <option value="on">開啟</option>
              </select>
            </label>
            <GallerydlSitesPanel />
          </div>

          <div className="settings-block" id="settings-naming">
            <h3>命名模板</h3>
            <p className="hint">
              使用 metadata 欄位，例如{" "}
              {"{site}/{author}/{date:%Y-%m}/{title} [{id}].{ext}"}；空白＝沿用站點預設檔名
            </p>
            <label className="field">
              <span>預設模板</span>
              <input
                value={settingsDraft.nameTemplate}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    nameTemplate: e.target.value,
                  }));
                  setSettingsDirty(true);
                }}
                placeholder="{site}/{author}/{title}.{ext}"
                spellCheck={false}
              />
            </label>
            <label className="field compact">
              <span>檔名衝突</span>
              <select
                value={settingsDraft.nameConflict}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    nameConflict: e.target.value as NameConflictMode,
                  }));
                  setSettingsDirty(true);
                }}
              >
                <option value="rename">重新命名（加序號）</option>
                <option value="overwrite">覆蓋</option>
                <option value="error">視為錯誤</option>
              </select>
            </label>
            <p className="hint">
              預覽：{namingPreviewBusy ? "計算中…" : namingPreview || "—"}
            </p>
            <div className="settings-grid">
              <label className="field compact">
                <span>Iwara 模板</span>
                <input
                  value={settingsDraft.siteTemplates?.iwara || ""}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      siteTemplates: {
                        ...s.siteTemplates,
                        iwara: e.target.value,
                      },
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="可空白"
                  spellCheck={false}
                />
              </label>
              <label className="field compact">
                <span>Bilibili 模板</span>
                <input
                  value={settingsDraft.siteTemplates?.bilibili || ""}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      siteTemplates: {
                        ...s.siteTemplates,
                        bilibili: e.target.value,
                      },
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="可空白"
                  spellCheck={false}
                />
              </label>
              <label className="field compact">
                <span>video 類型模板</span>
                <input
                  value={settingsDraft.typeTemplates?.video || ""}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      typeTemplates: {
                        ...s.typeTemplates,
                        video: e.target.value,
                      },
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="可空白"
                  spellCheck={false}
                />
              </label>
            </div>
          </div>

          <div className="settings-block">
            <h3>分類規則</h3>
            <p className="hint">由上到下比對，第一個命中的規則生效（資料夾／模板／略過）</p>
            {(settingsDraft.classificationRules || []).map((rule, index) => (
              <div key={rule.id} className="rule-editor">
                <div className="settings-grid">
                  <label className="field compact">
                    <span>名稱</span>
                    <input
                      value={rule.name}
                      onChange={(e) => updateRule(index, { name: e.target.value })}
                    />
                  </label>
                  <label className="field compact">
                    <span>站點</span>
                    <input
                      value={rule.match?.site || ""}
                      onChange={(e) =>
                        updateRule(index, {
                          match: { ...rule.match, site: e.target.value },
                        })
                      }
                      placeholder="iwara / bunkr / …"
                    />
                  </label>
                  <label className="field compact">
                    <span>動作</span>
                    <select
                      value={rule.action?.type || "folder"}
                      onChange={(e) =>
                        updateRule(index, {
                          action: {
                            ...rule.action,
                            type: e.target.value as ClassifyRule["action"]["type"],
                          },
                        })
                      }
                    >
                      <option value="folder">資料夾</option>
                      <option value="template">模板</option>
                      <option value="folder_and_template">資料夾＋模板</option>
                      <option value="skip">略過</option>
                    </select>
                  </label>
                </div>
                <div className="settings-grid">
                  <label className="field compact">
                    <span>標題 regex</span>
                    <input
                      value={rule.match?.title_regex || ""}
                      onChange={(e) =>
                        updateRule(index, {
                          match: { ...rule.match, title_regex: e.target.value },
                        })
                      }
                      spellCheck={false}
                    />
                  </label>
                  <label className="field compact">
                    <span>作者</span>
                    <input
                      value={rule.match?.author || ""}
                      onChange={(e) =>
                        updateRule(index, {
                          match: { ...rule.match, author: e.target.value },
                        })
                      }
                    />
                  </label>
                  <label className="field compact">
                    <span>媒體類型</span>
                    <input
                      value={rule.match?.media_type || ""}
                      onChange={(e) =>
                        updateRule(index, {
                          match: { ...rule.match, media_type: e.target.value },
                        })
                      }
                      placeholder="video / image"
                    />
                  </label>
                </div>
                <div className="settings-grid">
                  <label className="field compact">
                    <span>目標資料夾</span>
                    <input
                      value={rule.action?.folder || ""}
                      onChange={(e) =>
                        updateRule(index, {
                          action: { ...rule.action, folder: e.target.value },
                        })
                      }
                      spellCheck={false}
                    />
                  </label>
                  <label className="field compact">
                    <span>覆寫模板</span>
                    <input
                      value={rule.action?.template || ""}
                      onChange={(e) =>
                        updateRule(index, {
                          action: { ...rule.action, template: e.target.value },
                        })
                      }
                      spellCheck={false}
                    />
                  </label>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={rule.enabled !== false}
                      onChange={(e) => updateRule(index, { enabled: e.target.checked })}
                    />
                    <span>啟用</span>
                  </label>
                </div>
                <div className="actions">
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => moveRule(index, -1)}
                    disabled={index === 0}
                  >
                    上移
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => moveRule(index, 1)}
                    disabled={
                      index >= (settingsDraft.classificationRules || []).length - 1
                    }
                  >
                    下移
                  </button>
                  <button
                    type="button"
                    className="ghost danger"
                    onClick={() => {
                      setSettingsDraft((s) => ({
                        ...s,
                        classificationRules: (s.classificationRules || []).filter(
                          (_, i) => i !== index,
                        ),
                      }));
                      setSettingsDirty(true);
                    }}
                  >
                    刪除
                  </button>
                </div>
              </div>
            ))}
            <div className="actions">
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  setSettingsDraft((s) => ({
                    ...s,
                    classificationRules: [...(s.classificationRules || []), emptyRule()],
                  }));
                  setSettingsDirty(true);
                }}
              >
                新增規則
              </button>
            </div>
            <label className="field">
              <span>以 URL 測試規則</span>
              <div className="folder-row">
                <input
                  value={rulesTestUrl}
                  onChange={(e) => setRulesTestUrl(e.target.value)}
                  placeholder="貼上內容網址"
                  spellCheck={false}
                />
                <button
                  type="button"
                  className="ghost"
                  disabled={rulesTestBusy}
                  onClick={() => void runRulesTest()}
                >
                  {rulesTestBusy ? "測試中…" : "測試"}
                </button>
              </div>
            </label>
            {rulesTestResult && <p className="hint">{rulesTestResult}</p>}
          </div>

<div className="settings-block" id="settings-network">
            <h3>網路</h3>
            <p className="hint">
              限速、代理與 User-Agent 套用至所有站點；aria2 預設關閉，連不上時自動改用內建引擎。詳見 docs/networking.md。
            </p>
            <div className="settings-grid">
              <label className="field compact">
                <span>全域限速</span>
                <input
                  value={settingsDraft.speedLimit}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      speedLimit: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="空白＝不限（例 1M、512K）"
                  spellCheck={false}
                />
              </label>
              <label className="field compact">
                <span>單任務限速（選用）</span>
                <input
                  value={settingsDraft.jobSpeedLimit}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      jobSpeedLimit: e.target.value,
                    }));
                    setSettingsDirty(true);
                  }}
                  placeholder="空白＝不限"
                  spellCheck={false}
                />
              </label>
            </div>
            <label className="field compact">
              <span>User-Agent</span>
              <input
                value={settingsDraft.userAgent}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    userAgent: e.target.value,
                  }));
                  setSettingsDirty(true);
                }}
                placeholder="空白＝內建預設"
                spellCheck={false}
              />
            </label>
            <label className="field compact">
              <span>代理模式</span>
              <select
                value={settingsDraft.proxyMode}
                onChange={(e) => {
                  const value = e.target.value as AppSettings["proxyMode"];
                  setSettingsDraft((s) => ({ ...s, proxyMode: value }));
                  setSettingsDirty(true);
                }}
              >
                <option value="none">不使用代理</option>
                <option value="system">系統代理</option>
                <option value="custom">自訂代理</option>
              </select>
            </label>
            {settingsDraft.proxyMode === "custom" && (
              <>
                <label className="field compact">
                  <span>代理 URL</span>
                  <input
                    value={settingsDraft.proxyUrl}
                    onChange={(e) => {
                      setSettingsDraft((s) => ({
                        ...s,
                        proxyUrl: e.target.value,
                      }));
                      setSettingsDirty(true);
                    }}
                    placeholder="http://127.0.0.1:7890 或 socks5://127.0.0.1:1080"
                    spellCheck={false}
                  />
                </label>
                <div className="settings-grid">
                  <label className="field compact">
                    <span>代理帳號（選用）</span>
                    <input
                      value={settingsDraft.proxyUsername}
                      onChange={(e) => {
                        setSettingsDraft((s) => ({
                          ...s,
                          proxyUsername: e.target.value,
                        }));
                        setSettingsDirty(true);
                      }}
                      spellCheck={false}
                      autoComplete="username"
                    />
                  </label>
                  <label className="field compact">
                    <span>代理密碼（寫入系統鑰匙圈）</span>
                    <input
                      type="password"
                      value={proxyPassword}
                      onChange={(e) => {
                        setProxyPassword(e.target.value);
                        setClearProxyPasswordOnSave(false);
                        setSettingsDirty(true);
                      }}
                      placeholder="空白＝保留既有密碼"
                      autoComplete="new-password"
                    />
                  </label>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={clearProxyPasswordOnSave}
                    onChange={(e) => {
                      setClearProxyPasswordOnSave(e.target.checked);
                      if (e.target.checked) setProxyPassword("");
                      setSettingsDirty(true);
                    }}
                  />
                  <span>儲存時清除已存的代理密碼</span>
                </label>
              </>
            )}
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.aria2Enabled}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    aria2Enabled: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>使用 aria2 RPC 下載（選用，預設關閉）</span>
            </label>
            {settingsDraft.aria2Enabled && (
              <div className="settings-grid">
                <label className="field compact">
                  <span>aria2 RPC URL</span>
                  <input
                    value={settingsDraft.aria2RpcUrl}
                    onChange={(e) => {
                      setSettingsDraft((s) => ({
                        ...s,
                        aria2RpcUrl: e.target.value,
                      }));
                      setSettingsDirty(true);
                    }}
                    placeholder="http://127.0.0.1:6800/jsonrpc"
                    spellCheck={false}
                  />
                </label>
                <label className="field compact">
                  <span>aria2 RPC 密鑰</span>
                  <input
                    type="password"
                    value={settingsDraft.aria2RpcSecret}
                    onChange={(e) => {
                      setSettingsDraft((s) => ({
                        ...s,
                        aria2RpcSecret: e.target.value,
                      }));
                      setSettingsDirty(true);
                    }}
                    placeholder="選用"
                    autoComplete="off"
                  />
                </label>
              </div>
            )}
            <h4>多連線分段下載</h4>
            <p className="hint">
              IDM 風格動態分段（連線完成後切分最大剩餘區塊）；不支援 Range 時自動單連線。Gofile／Bunkr／Iwara／MEGA 不使用。aria2 啟用且可連線時仍優先 aria2。
            </p>
            <div className="settings-grid">
              <label className="field compact">
                <span>每檔連線數</span>
                <input
                  type="number"
                  min={1}
                  max={32}
                  value={settingsDraft.connections}
                  onChange={(e) => {
                    setSettingsDraft((s) => ({
                      ...s,
                      connections: Number(e.target.value) || 1,
                    }));
                    setSettingsDirty(true);
                  }}
                />
              </label>
            </div>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.segmentedEnabled}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    segmentedEnabled: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>啟用多連線分段（直連與可選站點）</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.segmentedForSites}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    segmentedForSites: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>站點 extractor 的純 HTTP 檔案也使用分段（排除 Gofile／Bunkr／Iwara／MEGA）</span>
            </label>
            <h4>Bilibili CDN／Host</h4>
            <p className="hint">
              偏好 CDN 節點會改寫 playurl 主機名並依健康度排序；自訂 Host 僅加在媒體下載請求。不繞過區域鎖。
            </p>
            <label className="field">
              <span>偏好 CDN 主機（逗號分隔）</span>
              <input
                value={settingsDraft.cdnHosts}
                onChange={(e) => {
                  setSettingsDraft((s) => ({ ...s, cdnHosts: e.target.value }));
                  setSettingsDirty(true);
                }}
                placeholder="upos-sz-mirrorcos.bilivideo.com,upos-sz-mirrorhw.bilivideo.com"
                spellCheck={false}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.preferCdn}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    preferCdn: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>優先使用偏好 CDN（否則原始 playurl 在前）</span>
            </label>
            <label className="field compact">
              <span>自訂 Host 標頭（選用）</span>
              <input
                value={settingsDraft.customHost}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    customHost: e.target.value,
                  }));
                  setSettingsDirty(true);
                }}
                placeholder="通常留空"
                spellCheck={false}
              />
            </label>

            <h3>校驗與掃毒</h3>
            <p className="hint">
              下載後可比對 MD5／SHA-1／SHA-256（站點 metadata 或 .sha256 旁檔）；不符會標記失敗並出現在修復中心。掃毒指令預設關閉。
            </p>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.checksumAuto !== false}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    checksumAuto: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>自動套用站點／sidecar 校驗碼</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={Boolean(settingsDraft.avHookEnabled)}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    avHookEnabled: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>啟用下載後指令／掃毒（預設關閉）</span>
            </label>
            {settingsDraft.avHookEnabled && (
              <>
                <label className="field compact">
                  <span>預設集</span>
                  <select
                    value={settingsDraft.avHookPreset || ""}
                    onChange={(e) => {
                      const value = e.target.value as AppSettings["avHookPreset"];
                      setSettingsDraft((s) => ({ ...s, avHookPreset: value }));
                      setSettingsDirty(true);
                    }}
                  >
                    <option value="">自訂指令</option>
                    <option value="defender">Windows Defender</option>
                    <option value="clamav">ClamAV clamscan</option>
                  </select>
                </label>
                <label className="field">
                  <span>指令（可用 {"{path}"}）</span>
                  <input
                    value={settingsDraft.avHookCommand || ""}
                    onChange={(e) => {
                      setSettingsDraft((s) => ({
                        ...s,
                        avHookCommand: e.target.value,
                      }));
                      setSettingsDirty(true);
                    }}
                    placeholder="clamscan {path}"
                    spellCheck={false}
                  />
                </label>
                <label className="field compact">
                  <span>逾時（秒）</span>
                  <input
                    type="number"
                    min={1}
                    value={settingsDraft.avHookTimeout || 120}
                    onChange={(e) => {
                      setSettingsDraft((s) => ({
                        ...s,
                        avHookTimeout: Number(e.target.value) || 120,
                      }));
                      setSettingsDirty(true);
                    }}
                  />
                </label>
              </>
            )}
          </div>


          <div className="settings-block" id="settings-shell">
            <h3>通知／托盤／外觀</h3>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.notificationsEnabled}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    notificationsEnabled: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>任務完成／失敗時系統通知</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.minimizeToTray}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    minimizeToTray: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>關閉視窗時最小化到系統列／選單列</span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={settingsDraft.clipboardWatcher}
                onChange={(e) => {
                  setSettingsDraft((s) => ({
                    ...s,
                    clipboardWatcher: e.target.checked,
                  }));
                  setSettingsDirty(true);
                }}
              />
              <span>剪貼簿監看（預設關閉；偵測到支援網址時詢問是否加入）</span>
            </label>
            <label className="field compact">
              <span>主題</span>
              <select
                value={settingsDraft.themeMode}
                onChange={(e) => {
                  const value = e.target.value as ThemeMode;
                  setSettingsDraft((s) => ({ ...s, themeMode: value }));
                  setSettingsDirty(true);
                  applyTheme(value);
                }}
              >
                <option value="system">跟隨系統</option>
                <option value="light">淺色</option>
                <option value="dark">深色</option>
              </select>
            </label>
            <p className="hint">
              系統列圖示會顯示進行中／失敗數量；左鍵點擊可顯示主視窗。
            </p>
          </div>

          <div className="settings-block" id="settings-updater">
            <h3>應用程式更新</h3>
            <p className="hint">
              {updaterInfo?.reason ||
                "檢查 GitHub Releases；未設定簽章公鑰時停用自動更新。"}
            </p>
            <p className="hint">
              狀態：{updaterInfo?.enabled ? "已啟用" : "已停用"} · 詳見 docs/updater.md
            </p>
          </div>

          <div className="settings-block" id="settings-import">
            <h3>匯入／匯出設定</h3>
            <p className="hint">
              匯出為 JSON，不含密鑰（代理密碼、aria2 secret、帳號憑證）。
            </p>
            <div className="actions">
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  const blob = new Blob([exportSettingsJson(settingsDraft)], {
                    type: "application/json",
                  });
                  const a = document.createElement("a");
                  a.href = URL.createObjectURL(blob);
                  a.download = "unified-downloader-settings.json";
                  a.click();
                  URL.revokeObjectURL(a.href);
                }}
              >
                匯出 JSON
              </button>
              <button
                type="button"
                className="ghost"
                onClick={() => {
                  const input = document.createElement("input");
                  input.type = "file";
                  input.accept = "application/json,.json";
                  input.onchange = () => {
                    const file = input.files?.[0];
                    if (!file) return;
                    const reader = new FileReader();
                    reader.onload = () => {
                      try {
                        const next = importSettingsJson(
                          String(reader.result || ""),
                          settingsDraft,
                        );
                        setSettingsDraft(next);
                        setSettingsDirty(true);
                      } catch (e) {
                        setError(humanizeError(String(e)));
                      }
                    };
                    reader.readAsText(file);
                  };
                  input.click();
                }}
              >
                匯入 JSON
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
              <span>網址（可多筆，一行一個；亦可拖放到視窗）</span>
              <div className="url-row url-row-multi">
                <textarea
                  value={urlDraft || url}
                  rows={Math.min(6, Math.max(2, detectedUrls.length || 2))}
                  onChange={(e) => {
                    const value = e.target.value;
                    setUrlDraft(value);
                    const urls = extractUrls(value);
                    setUrl(urls[0] || value.trim());
                  }}
                  onPaste={(e) => {
                    const text = e.clipboardData.getData("text");
                    const found = extractUrls(text);
                    if (found.length > 1) {
                      e.preventDefault();
                      const next = found.join("\n");
                      setUrlDraft(next);
                      setUrl(found[0]);
                    } else if (found.length === 1 && found[0] !== text.trim()) {
                      e.preventDefault();
                      setUrlDraft(found[0]);
                      setUrl(found[0]);
                    }
                  }}
                  placeholder={"https://…\nhttps://…"}
                  spellCheck={false}
                  autoCapitalize="off"
                  autoCorrect="off"
                />
                <div className="url-badges">
                  {detectedUrls.slice(0, 5).map((u) => {
                    const site = detectSite(u);
                    return (
                      <span
                        key={u}
                        className={`badge ${siteTone(site?.site || resolveInfo?.site)}`}
                        title={u}
                      >
                        {site?.label || resolveInfo?.label || "辨識中"}
                      </span>
                    );
                  })}
                  {detectedUrls.length > 5 && (
                    <span className="badge tone-unknown">+{detectedUrls.length - 5}</span>
                  )}
                  {detectedUrls.length === 0 && resolving && (
                    <span className="badge tone-unknown">辨識中</span>
                  )}
                  {detectedUrls.length === 1 &&
                    !resolving &&
                    resolveInfo &&
                    !resolveInfo.supported && (
                      <span className="badge tone-unknown">不支援</span>
                    )}
                  {primaryDetected && detectedUrls.length === 0 && (
                    <span className={`badge ${siteTone(primaryDetected.site)}`}>
                      {primaryDetected.label}
                    </span>
                  )}
                </div>
              </div>
            </label>

            {clipboardHint && settings.clipboardWatcher && (
              <div className="clipboard-hint">
                <span>
                  剪貼簿偵測到 {clipboardUrls.length || 1} 個支援連結
                  {detectSite(clipboardHint) ? (
                    <span className={`badge ${siteTone(detectSite(clipboardHint)?.site)}`}>
                      {detectSite(clipboardHint)?.label}
                    </span>
                  ) : null}
                </span>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    const list = clipboardUrls.length > 0 ? clipboardUrls : [clipboardHint];
                    setUrlDraft(list.join("\n"));
                    setUrl(list[0]);
                    setClipboardHint(null);
                    setClipboardUrls([]);
                  }}
                >
                  加入輸入框
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() => {
                    const list = clipboardUrls.length > 0 ? clipboardUrls : [clipboardHint];
                    for (const u of list) enqueueOne(u);
                    setClipboardHint(null);
                    setClipboardUrls([]);
                  }}
                >
                  直接入隊
                </button>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    setClipboardHint(null);
                    setClipboardUrls([]);
                  }}
                >
                  忽略
                </button>
              </div>
            )}

            {resolveInfo?.site === "mega" && resolveInfo.supported && (
              <div className="site-options" aria-label="MEGA 選項">
                <p className="hint">
                  預設使用內建 Python 客戶端（多連線分段＋CBC-MAC）。可在設定改為
                  megatools／MEGAcmd 外部後端。資料夾建議用「MEGA」頁勾選下載。不支援配額繞過。
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
                {detectedUrls.length > 1
                  ? `加入佇列（${detectedUrls.length}）`
                  : "加入佇列"}
              </button>
            </div>
          </section>

          {schedulerStatus && (
            <p className="hint scheduler-inline-status" role="status">
              排程：
              {schedulerStatus.quota?.exceeded
                ? "流量配額已滿，佇列暫停"
                : !schedulerStatus.active
                  ? "視窗外／待機"
                  : "可執行"}
              {schedulerStatus.global_speed_limit
                ? ` · 限速 ${schedulerStatus.global_speed_limit}`
                : ""}
              {" · "}
              <button
                type="button"
                className="linkish"
                onClick={() => setPage("schedule")}
              >
                開啟排程設定
              </button>
            </p>
          )}
          <QueueDashboard
            jobs={jobs}
            filter={queueFilter}
            selected={selectedJobs}
            maxConcurrent={settings.maxConcurrent}
            onFilterChange={setQueueFilter}
            onMaxConcurrentChange={(n) => void persistConcurrency(n)}
            onToggleSelect={toggleJobSelect}
            onSelectAllVisible={() => {
              const ids = jobs
                .filter((j) => {
                  switch (queueFilter) {
                    case "active":
                      return j.status === "running";
                    case "queued":
                      return j.status === "queued";
                    case "paused":
                      return j.status === "paused";
                    case "failed":
                      return j.status === "error" || j.status === "cancelled";
                    case "done":
                      return j.status === "done";
                    default:
                      return true;
                  }
                })
                .map((j) => j.clientId);
              setSelectedJobs(new Set(ids));
            }}
            onClearSelection={() => setSelectedJobs(new Set())}
            onPause={(job) => void pauseJob(job)}
            onResume={resumeJob}
            onCancel={(job) => void cancelJob(job)}
            onRetry={retryJob}
            onReorder={reorderJob}
            onOpenFolder={(path) => void openFolder(path)}
            onOpenRepair={() => setPage("repair")}
            onBulkPause={() => {
              for (const job of jobs) {
                if (selectedJobs.has(job.clientId) && job.status === "running") {
                  void pauseJob(job);
                }
              }
            }}
            onBulkCancel={() => {
              for (const job of jobs) {
                if (
                  selectedJobs.has(job.clientId) &&
                  (job.status === "running" || job.status === "queued")
                ) {
                  void cancelJob(job);
                }
              }
            }}
            onBulkRetry={() => {
              for (const job of jobs) {
                if (
                  selectedJobs.has(job.clientId) &&
                  (job.status === "error" ||
                    job.status === "cancelled" ||
                    job.status === "paused")
                ) {
                  retryJob(job);
                }
              }
            }}
            onBulkClearFinished={() => {
              setJobs((prev) => prev.filter((j) => j.status !== "done"));
              setSelectedJobs(new Set());
            }}
          />
        </>
      )}
    </div>
  );
}
