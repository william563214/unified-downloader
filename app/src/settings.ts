export type DuplicateMode = "skip" | "overwrite" | "rename";

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
};

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
