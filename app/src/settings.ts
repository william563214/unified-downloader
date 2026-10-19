export type DuplicateMode = "skip" | "overwrite" | "rename";
export type CoverNaming = "poster" | "folder";

export type SiteId = "gofile" | "bunkr" | "iwara" | "bilibili" | "mega";

export const site_ids: SiteId[] = [
  "gofile",
  "bunkr",
  "iwara",
  "bilibili",
  "mega",
];

export type AppSettings = {
  downloadDir: string;
  maxConcurrent: number;
  workers: number;
  ffmpegPath: string;
  iwaraQuality: string;
  iwaraCloudflareBypass: boolean;
  bilibiliQuality: string;
  bilibiliPart: string;
  onDuplicate: DuplicateMode;
  writeNfo: boolean;
  writeCover: boolean;
  embedCover: boolean;
  coverNaming: CoverNaming;
  nfoSites: Record<SiteId, boolean>;
  coverSites: Record<SiteId, boolean>;
};

export const default_site_flags = (): Record<SiteId, boolean> => ({
  gofile: true,
  bunkr: true,
  iwara: true,
  bilibili: true,
  mega: true,
});

export const default_settings: AppSettings = {
  downloadDir: "",
  maxConcurrent: 2,
  workers: 3,
  ffmpegPath: "",
  iwaraQuality: "Source",
  iwaraCloudflareBypass: false,
  bilibiliQuality: "",
  bilibiliPart: "all",
  onDuplicate: "skip",
  writeNfo: false,
  writeCover: false,
  embedCover: false,
  coverNaming: "poster",
  nfoSites: default_site_flags(),
  coverSites: default_site_flags(),
};

export type FfmpegStatus = {
  found: boolean;
  path: string | null;
  source: string | null;
  platform: string;
  provisionSupported: boolean;
  message: string;
};

export type HistoryEntry = {
  id: string;
  site: string;
  status: string;
  title?: string | null;
  item_id?: string | null;
  canonical_url?: string | null;
  author?: string | null;
  upload_date?: string | null;
  tags?: string[];
  description?: string | null;
  thumbnail_url?: string | null;
  cover_url?: string | null;
  cover_path?: string | null;
  source_url?: string | null;
  output_dir?: string | null;
  ok?: number;
  failed?: number;
  created_at?: string | null;
  updated_at?: string | null;
  finished_at?: string | null;
  files?: Array<{
    file_id?: string | null;
    name?: string | null;
    path?: string | null;
    size?: number | null;
    sha256?: string | null;
    status?: string | null;
    role?: string | null;
  }>;
};

export type QueueEntry = {
  id: string;
  url: string;
  output_dir: string;
  status: string;
  site?: string | null;
  title?: string | null;
  quality?: string | null;
  part?: string | null;
  cloudflare_bypass?: boolean;
  on_duplicate?: string;
};

const url_pattern =
  /https?:\/\/[^\s<>"'`]+/i;

export function extractUrl(text: string): string | null {
  const match = text.trim().match(url_pattern);
  if (!match) return null;
  return match[0].replace(/[.,);\]}>]+$/, "");
}

export function history_cover_path(entry: HistoryEntry): string | null {
  if (entry.cover_path) return entry.cover_path;
  const cover_file = (entry.files || []).find((f) => f.role === "cover" && f.path);
  return cover_file?.path || null;
}

export function sites_cli_list(flags: Record<string, boolean>): string {
  const entries = site_ids.map((id) => [id, flags[id] !== false] as const);
  const enabled = entries.filter(([, on]) => on).map(([id]) => id);
  const disabled = entries.filter(([, on]) => !on).map(([id]) => id);
  if (enabled.length === site_ids.length) return "all";
  if (enabled.length === 0) return "none";
  if (disabled.length <= enabled.length) {
    return ["all", ...disabled.map((id) => `-${id}`)].join(",");
  }
  return enabled.join(",");
}
