export type SegmentInfo = {
  id: number;
  start: number;
  end: number;
  current: number;
  downloaded: number;
  total: number;
  done: boolean;
};

export type FileRow = {
  id: string;
  name: string;
  path?: string;
  total?: number;
  bytes: number;
  bps: number;
  segments?: SegmentInfo[];
  connections?: number;
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

export type JobStatus =
  | "queued"
  | "running"
  | "paused"
  | "done"
  | "error"
  | "cancelled";

export type JobRow = {
  id: string;
  clientId: string;
  url: string;
  site: string;
  title: string;
  output: string;
  quality: string | null;
  part: string | null;
  cloudflareBypass: boolean | null;
  codec?: string | null;
  audioQuality?: string | null;
  container?: string | null;
  danmaku?: string | null;
  subtitle?: string | null;
  subtitleLang?: string | null;
  embedDanmaku?: boolean;
  embedSubtitle?: boolean;
  filterTitle?: string | null;
  partRange?: string | null;
  expandAll?: boolean;
  nodeNames?: string | null;
  megaSelect?: string | null;
  queueName?: string;
  files: FileRow[];
  ok?: number;
  failed?: number;
  status: JobStatus;
  message?: string;
};

export type QueueFilter =
  | "all"
  | "active"
  | "queued"
  | "paused"
  | "failed"
  | "done";

export function newClientId(): string {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
