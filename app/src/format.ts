import type { SegmentInfo } from "./jobTypes";

export function formatBytes(n?: number): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function formatSpeed(bps?: number): string {
  if (bps == null || bps <= 0) return "—";
  return `${formatBytes(bps)}/s`;
}

export function formatEta(seconds?: number | null): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.ceil(seconds)} 秒`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = Math.ceil(seconds % 60);
    return `${m} 分 ${s} 秒`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h} 時 ${m} 分`;
}

export function siteTone(site: string | null | undefined): string {
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
    case "direct":
      return "tone-direct";
    default:
      return "tone-unknown";
  }
}

export function parseSegments(raw: unknown): SegmentInfo[] | undefined {
  if (!Array.isArray(raw) || raw.length === 0) return undefined;
  return raw.map((s, i) => {
    const row = (s ?? {}) as Record<string, unknown>;
    return {
      id: Number(row.id ?? i),
      start: Number(row.start ?? 0),
      end: Number(row.end ?? 0),
      current: Number(row.current ?? row.start ?? 0),
      downloaded: Number(row.downloaded ?? 0),
      total: Number(row.total ?? 0),
      done: Boolean(row.done),
    };
  });
}

export function jobProgress(files: Array<{ bytes: number; total?: number; bps?: number; status: string; alreadyDownloaded?: boolean }>): {
  bytes: number;
  total: number;
  bps: number;
  pct: number;
  etaSec: number | null;
} {
  let bytes = 0;
  let total = 0;
  let bps = 0;
  let known = 0;
  for (const f of files) {
    bytes += f.bytes || 0;
    if (f.total != null && f.total > 0) {
      total += f.total;
      known += 1;
    } else if (f.status === "done" || f.status === "skipped" || f.alreadyDownloaded) {
      total += f.bytes || 0;
      known += 1;
    }
    if (f.status === "active") bps += f.bps || 0;
  }
  const pct =
    total > 0 ? Math.min(100, (bytes / total) * 100) : files.some((f) => f.status === "done") ? 100 : 0;
  const remain = total > bytes ? total - bytes : 0;
  const etaSec = bps > 0 && remain > 0 ? remain / bps : null;
  return { bytes, total: known > 0 ? total : 0, bps, pct, etaSec };
}
