export type DuplicateMode = "skip" | "overwrite" | "rename";
export type CoverNaming = "poster" | "folder";
export type NameConflictMode = "rename" | "overwrite" | "error";
export type ProxyMode = "none" | "system" | "custom";

export type SiteId = "gofile" | "bunkr" | "iwara" | "bilibili" | "mega";

export const site_ids: SiteId[] = [
  "gofile",
  "bunkr",
  "iwara",
  "bilibili",
  "mega",
];

export type RuleMatch = {
  site?: string;
  sites?: string[];
  author?: string;
  author_regex?: string;
  tags_any?: string[];
  tags_all?: string[];
  title_regex?: string;
  duration_min?: number | null;
  duration_max?: number | null;
  size_min?: number | null;
  size_max?: number | null;
  media_type?: string;
  media_types?: string[];
  collection_regex?: string;
};

export type RuleAction = {
  type: "folder" | "template" | "folder_and_template" | "skip";
  folder?: string;
  template?: string;
};

export type ClassifyRule = {
  id: string;
  name: string;
  enabled: boolean;
  match: RuleMatch;
  action: RuleAction;
};

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
  nameTemplate: string;
  siteTemplates: Record<string, string>;
  typeTemplates: Record<string, string>;
  nameConflict: NameConflictMode;
  classificationRules: ClassifyRule[];
  // networking (v0.5)
  speedLimit: string;
  jobSpeedLimit: string;
  proxyMode: ProxyMode;
  proxyUrl: string;
  proxyUsername: string;
  userAgent: string;
  aria2Enabled: boolean;
  aria2RpcUrl: string;
  aria2RpcSecret: string;
  // bilibili CDN / Host (v0.7-B)
  cdnHosts: string;
  preferCdn: boolean;
  customHost: string;
  // tools / shell (v0.8)
  megaToolPath: string;
  clipboardWatcher: boolean;
  notificationsEnabled: boolean;
  minimizeToTray: boolean;
  themeMode: "system" | "light" | "dark";
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
  nameTemplate: "",
  siteTemplates: {},
  typeTemplates: {},
  nameConflict: "rename",
  classificationRules: [],
  speedLimit: "",
  jobSpeedLimit: "",
  proxyMode: "none",
  proxyUrl: "",
  proxyUsername: "",
  userAgent: "",
  aria2Enabled: false,
  aria2RpcUrl: "http://127.0.0.1:6800/jsonrpc",
  aria2RpcSecret: "",
  cdnHosts: "",
  preferCdn: false,
  customHost: "",
  megaToolPath: "",
  clipboardWatcher: false,
  notificationsEnabled: true,
  minimizeToTray: false,
  themeMode: "system",
};

/** fields never written to exported settings JSON */
export const secret_setting_keys = [
  "aria2RpcSecret",
  "proxyPassword",
  "proxyUsername",
] as const;

export function exportSettingsJson(settings: AppSettings): string {
  const payload: Record<string, unknown> = { ...settings };
  for (const key of secret_setting_keys) {
    delete payload[key];
  }
  // aria2 secret is on settings object
  delete payload.aria2RpcSecret;
  return `${JSON.stringify(payload, null, 2)}\n`;
}

export function importSettingsJson(
  raw: string,
  current: AppSettings,
): AppSettings {
  const parsed = JSON.parse(raw) as Partial<AppSettings>;
  if (!parsed || typeof parsed !== "object") {
    throw new Error("設定 JSON 格式無效");
  }
  const merged: AppSettings = {
    ...current,
    ...parsed,
    nfoSites: { ...current.nfoSites, ...(parsed.nfoSites || {}) },
    coverSites: { ...current.coverSites, ...(parsed.coverSites || {}) },
    siteTemplates: { ...current.siteTemplates, ...(parsed.siteTemplates || {}) },
    typeTemplates: { ...current.typeTemplates, ...(parsed.typeTemplates || {}) },
    classificationRules: parsed.classificationRules ?? current.classificationRules,
  };
  // never import secrets from file
  merged.aria2RpcSecret = current.aria2RpcSecret;
  if (!merged.megaToolPath) merged.megaToolPath = "";
  if (merged.clipboardWatcher == null) merged.clipboardWatcher = false;
  if (merged.notificationsEnabled == null) merged.notificationsEnabled = true;
  if (merged.minimizeToTray == null) merged.minimizeToTray = false;
  if (!merged.themeMode) merged.themeMode = "system";
  return merged;
}

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
    id?: number;
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

export type RepairIssue = {
  id: string;
  kind: string;
  job_id?: string | null;
  file_row_id?: number | null;
  file_id?: string | null;
  path?: string | null;
  expected_size?: number | null;
  actual_size?: number | null;
  expected_sha256?: string | null;
  actual_sha256?: string | null;
  site?: string | null;
  source_url?: string | null;
  title?: string | null;
  message?: string | null;
  suggested_actions?: string[];
};

export type RepairScanResult = {
  scanned_jobs: number;
  scanned_files: number;
  scanned_dirs: string[];
  issue_count: number;
  issues: RepairIssue[];
};

export type NamingPreview = {
  template: string;
  relative: string;
  media_type: string;
  original_name: string;
  metadata: Record<string, unknown>;
};

export { extractUrl, extractUrls, detectSite } from "./urls";

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

export function newRuleId(): string {
  return `rule-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

export function emptyRule(): ClassifyRule {
  return {
    id: newRuleId(),
    name: "新規則",
    enabled: true,
    match: { site: "" },
    action: { type: "folder", folder: "" },
  };
}

export function settingsToConfigPayload(settings: AppSettings): Record<string, unknown> {
  return {
    name_template: settings.nameTemplate,
    site_templates: settings.siteTemplates,
    type_templates: settings.typeTemplates,
    name_conflict: settings.nameConflict,
    rules: settings.classificationRules,
  };
}
