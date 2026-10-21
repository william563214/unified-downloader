export type DuplicateMode = "skip" | "overwrite" | "rename";
export type CoverNaming = "poster" | "folder";
export type NameConflictMode = "rename" | "overwrite" | "error";
export type ProxyMode = "none" | "system" | "custom";

export type SiteId =
  | "gofile"
  | "bunkr"
  | "iwara"
  | "bilibili"
  | "mega"
  | "gallerydl"
  | "direct";

export const site_ids: SiteId[] = [
  "gofile",
  "bunkr",
  "iwara",
  "bilibili",
  "mega",
  "gallerydl",
  "direct",
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
  /** file extensions without dot e.g. mp4,zip */
  extensions?: string[];
  mime_types?: string[];
  filename?: string[];
  /** download / page host patterns (download_classify site matchers) */
  hosts?: string[];
  source_hosts?: string[];
  source_page?: string[];
  url_patterns?: string[];
  /** site rule: source | download | either */
  match_target?: "source" | "download" | "either";
};

export type RuleAction = {
  type: "folder" | "template" | "folder_and_template" | "skip";
  folder?: string;
  template?: string;
};

export type ClassifyRuleKind = "custom" | "site" | "extension";

export type ClassifyRule = {
  id: string;
  name: string;
  enabled: boolean;
  priority?: number;
  kind?: ClassifyRuleKind;
  match: RuleMatch;
  action: RuleAction;
};

/** download_classify SiteClassificationRule */
export type SiteClassifyRule = {
  id: string;
  name: string;
  host: string;
  target_folder: string;
  match_target: "source" | "download" | "either";
  priority: number;
  enabled: boolean;
};

/** download_classify ExtensionRule */
export type ExtensionClassifyRule = {
  id?: string;
  name: string;
  extension: string;
  target_folder: string;
  priority?: number;
  enabled?: boolean;
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
  siteRules: SiteClassifyRule[];
  extensionRules: ExtensionClassifyRule[];
  // networking (v0.5)
  speedLimit: string;
  jobSpeedLimit: string;
  proxyMode: ProxyMode;
  proxyUrl: string;
  proxyUsername: string;
  userAgent: string;
  // curl_cffi browser impersonation: opt-in / off by default
  curlCffiImpersonate: boolean;
  curlCffiImpersonateTarget: string;
  requestHandler: "auto" | "requests" | "urllib" | "curl_cffi";
  aria2Enabled: boolean;
  aria2RpcUrl: string;
  aria2RpcSecret: string;
  // multi-connection segmented (v0.9-A)
  connections: number;
  segmentedEnabled: boolean;
  segmentedForSites: boolean;
  // fragment backend (v0.10): native | aria2c | ffmpeg
  externalDownloader: "native" | "aria2c" | "ffmpeg";
  concurrentFragments: number;
  // bilibili CDN / Host (v0.7-B)
  cdnHosts: string;
  preferCdn: boolean;
  customHost: string;
  // MEGA built-in / external fallback
  megaBackend: "builtin" | "external";
  megaToolPath: string;
  // gallery-dl external (GPL-2.0-only subprocess)
  gallerydlEnabled: boolean;
  gallerydlToolPath: string;
  gallerydlCookiesPath: string;
  gallerydlOptions: string;
  gallerydlUseArchive: boolean;
  // tools / shell (v0.8)
  clipboardWatcher: boolean;
  notificationsEnabled: boolean;
  minimizeToTray: boolean;
  themeMode: "system" | "light" | "dark";
  // v0.9-D checksum / post-download hook
  checksumAuto: boolean;
  avHookEnabled: boolean;
  avHookPreset: "" | "defender" | "clamav";
  avHookCommand: string;
  avHookTimeout: number;
  // auto-extract ZIP after download (default off)
  autoExtractZip: boolean;
  extractTo: "subfolder" | "same_folder";
  extractDeleteZip: boolean;
  extractMaxUncompressed: number;
  extractMaxRatio: number;
  extractPromotePassword: boolean;
};

export type AutoExtractOverride = "inherit" | "on" | "off";
export type ExtractPasswordMode = "list" | "single" | "off";

export type ZipPasswordEntry = {
  id: string;
  label: string;
  index: number;
  length: number;
};

export const default_site_flags = (): Record<SiteId, boolean> => ({
  gofile: true,
  bunkr: true,
  iwara: true,
  bilibili: true,
  mega: true,
  gallerydl: true,
  direct: true,
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
  siteRules: [],
  extensionRules: [],
  speedLimit: "",
  jobSpeedLimit: "",
  proxyMode: "none",
  proxyUrl: "",
  proxyUsername: "",
  userAgent: "",
  curlCffiImpersonate: false,
  curlCffiImpersonateTarget: "chrome",
  requestHandler: "auto",
  aria2Enabled: false,
  aria2RpcUrl: "http://127.0.0.1:6800/jsonrpc",
  aria2RpcSecret: "",
  connections: 8,
  segmentedEnabled: true,
  segmentedForSites: false,
  externalDownloader: "native",
  concurrentFragments: 1,
  cdnHosts: "",
  preferCdn: false,
  customHost: "",
  megaBackend: "builtin",
  megaToolPath: "",
  gallerydlEnabled: true,
  gallerydlToolPath: "",
  gallerydlCookiesPath: "",
  gallerydlOptions: "",
  gallerydlUseArchive: false,
  clipboardWatcher: false,
  notificationsEnabled: true,
  minimizeToTray: false,
  themeMode: "system",
  checksumAuto: true,
  avHookEnabled: false,
  avHookPreset: "",
  avHookCommand: "",
  avHookTimeout: 120,
  autoExtractZip: false,
  extractTo: "subfolder",
  extractDeleteZip: false,
  extractMaxUncompressed: 4 * 1024 * 1024 * 1024,
  extractMaxRatio: 100,
  extractPromotePassword: true,
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
    siteRules: parsed.siteRules ?? current.siteRules,
    extensionRules: parsed.extensionRules ?? current.extensionRules,
  };
  // never import secrets from file
  merged.aria2RpcSecret = current.aria2RpcSecret;
  if (!merged.megaToolPath) merged.megaToolPath = "";
  if (merged.gallerydlEnabled == null) merged.gallerydlEnabled = true;
  if (!merged.gallerydlToolPath) merged.gallerydlToolPath = "";
  if (!merged.gallerydlCookiesPath) merged.gallerydlCookiesPath = "";
  if (!merged.gallerydlOptions) merged.gallerydlOptions = "";
  if (merged.gallerydlUseArchive == null) merged.gallerydlUseArchive = false;
  if (merged.clipboardWatcher == null) merged.clipboardWatcher = false;
  if (merged.notificationsEnabled == null) merged.notificationsEnabled = true;
  if (merged.minimizeToTray == null) merged.minimizeToTray = false;
  if (!merged.themeMode) merged.themeMode = "system";
  if (merged.autoExtractZip == null) merged.autoExtractZip = false;
  if (merged.extractTo !== "same_folder") merged.extractTo = "subfolder";
  if (merged.extractDeleteZip == null) merged.extractDeleteZip = false;
  if (!merged.extractMaxUncompressed) {
    merged.extractMaxUncompressed = 4 * 1024 * 1024 * 1024;
  }
  if (!merged.extractMaxRatio) merged.extractMaxRatio = 100;
  if (merged.extractPromotePassword == null) merged.extractPromotePassword = true;
  if (merged.curlCffiImpersonate == null) merged.curlCffiImpersonate = false;
  if (!merged.curlCffiImpersonateTarget) merged.curlCffiImpersonateTarget = "chrome";
  if (
    merged.requestHandler !== "requests" &&
    merged.requestHandler !== "urllib" &&
    merged.requestHandler !== "curl_cffi"
  ) {
    merged.requestHandler = "auto";
  }
  if (!Array.isArray(merged.siteRules)) merged.siteRules = [];
  if (!Array.isArray(merged.extensionRules)) merged.extensionRules = [];
  if (
    merged.externalDownloader !== "aria2c" &&
    merged.externalDownloader !== "ffmpeg"
  ) {
    merged.externalDownloader = "native";
  }
  if (!merged.concurrentFragments || merged.concurrentFragments < 1) {
    merged.concurrentFragments = 1;
  }
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
  queue_name?: string | null;
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
    priority: 50,
    kind: "custom",
    match: { site: "" },
    action: { type: "folder", folder: "" },
  };
}

export function emptySiteRule(): SiteClassifyRule {
  return {
    id: newRuleId(),
    name: "新網站規則",
    host: "",
    target_folder: "Sites/",
    match_target: "either",
    priority: 10,
    enabled: true,
  };
}

export function emptyExtensionRule(): ExtensionClassifyRule {
  return {
    id: newRuleId(),
    name: "副檔名",
    extension: "",
    target_folder: "Others",
    priority: 900,
    enabled: true,
  };
}

/** mirrors william563214/download_classify default extension table */
export function defaultExtensionRules(): ExtensionClassifyRule[] {
  const rows: Array<[string, string, string]> = [
    ["文件", "pdf", "Documents"],
    ["文件", "doc", "Documents"],
    ["文件", "docx", "Documents"],
    ["文件", "xlsx", "Documents"],
    ["文件", "pptx", "Documents"],
    ["文件", "txt", "Documents"],
    ["壓縮檔", "zip", "Archives"],
    ["壓縮檔", "rar", "Archives"],
    ["壓縮檔", "7z", "Archives"],
    ["壓縮檔", "tar", "Archives"],
    ["壓縮檔", "gz", "Archives"],
    ["圖片", "png", "Media/Images"],
    ["圖片", "jpg", "Media/Images"],
    ["圖片", "jpeg", "Media/Images"],
    ["圖片", "gif", "Media/Images"],
    ["圖片", "webp", "Media/Images"],
    ["圖片", "svg", "Media/Images"],
    ["影片", "mp4", "Media/Video"],
    ["影片", "mkv", "Media/Video"],
    ["影片", "avi", "Media/Video"],
    ["影片", "mov", "Media/Video"],
    ["影片", "webm", "Media/Video"],
    ["音樂", "mp3", "Media/Audio"],
    ["音樂", "flac", "Media/Audio"],
    ["音樂", "wav", "Media/Audio"],
    ["音樂", "aac", "Media/Audio"],
    ["程式", "exe", "Programs"],
    ["程式", "msi", "Programs"],
    ["程式", "dmg", "Programs"],
    ["程式", "pkg", "Programs"],
    ["程式", "apk", "Programs"],
  ];
  return rows.map(([name, extension, target_folder], index) => ({
    id: `ext-${extension}`,
    name,
    extension,
    target_folder,
    priority: 900 + index,
    enabled: true,
  }));
}

export type DownloadClassifyExport = {
  rules: Array<{
    id: string;
    name: string;
    target_folder: string;
    priority: number;
    enabled: boolean;
    matchers: Record<string, string[]>;
  }>;
  site_rules: SiteClassifyRule[];
  extension_rules: ExtensionClassifyRule[];
};

export function exportDownloadClassifyJson(settings: AppSettings): string {
  const rules = (settings.classificationRules || []).map((rule) => {
    const matchers: Record<string, string[]> = {};
    if (rule.match?.hosts?.length) matchers.site = [...rule.match.hosts];
    if (rule.match?.source_hosts?.length) {
      matchers.source_site = [...rule.match.source_hosts];
    }
    if (rule.match?.source_page?.length) {
      matchers.source_page = [...rule.match.source_page];
    }
    if (rule.match?.filename?.length) matchers.filename = [...rule.match.filename];
    if (rule.match?.extensions?.length) {
      matchers.extension = [...rule.match.extensions];
    }
    return {
      id: rule.id,
      name: rule.name,
      target_folder: rule.action?.folder || "Others",
      priority: rule.priority ?? 50,
      enabled: rule.enabled !== false,
      matchers,
    };
  });
  const payload: DownloadClassifyExport = {
    rules,
    site_rules: settings.siteRules || [],
    extension_rules: settings.extensionRules || [],
  };
  return `${JSON.stringify(payload, null, 2)}\n`;
}

export function importDownloadClassifyJson(
  raw: string,
  current: AppSettings,
): AppSettings {
  const parsed = JSON.parse(raw) as Partial<DownloadClassifyExport> & {
    classificationRules?: ClassifyRule[];
  };
  if (!parsed || typeof parsed !== "object") {
    throw new Error("規則 JSON 格式無效");
  }
  const next = { ...current };
  if (Array.isArray(parsed.site_rules)) {
    next.siteRules = parsed.site_rules.map((r) => ({
      id: r.id || newRuleId(),
      name: r.name || r.host || "網站規則",
      host: r.host || "",
      target_folder: r.target_folder || "",
      match_target: r.match_target || "either",
      priority: r.priority ?? 10,
      enabled: r.enabled !== false,
    }));
  }
  if (Array.isArray(parsed.extension_rules)) {
    next.extensionRules = parsed.extension_rules.map((r, index) => ({
      id: r.id || `ext-${r.extension || index}`,
      name: r.name || r.extension || "副檔名",
      extension: (r.extension || "").replace(/^\./, "").toLowerCase(),
      target_folder: r.target_folder || "Others",
      priority: r.priority ?? 900 + index,
      enabled: r.enabled !== false,
    }));
  }
  if (Array.isArray(parsed.rules)) {
    next.classificationRules = parsed.rules.map((r) => {
      const matchers = r.matchers || {};
      return {
        id: r.id || newRuleId(),
        name: r.name || "規則",
        enabled: r.enabled !== false,
        priority: r.priority ?? 50,
        kind: "custom" as const,
        match: {
          hosts: matchers.site,
          source_hosts: matchers.source_site,
          source_page: matchers.source_page,
          filename: matchers.filename,
          extensions: matchers.extension,
        },
        action: {
          type: "folder" as const,
          folder: r.target_folder || "",
        },
      };
    });
  } else if (Array.isArray(parsed.classificationRules)) {
    next.classificationRules = parsed.classificationRules;
  }
  return next;
}

export function settingsToConfigPayload(settings: AppSettings): Record<string, unknown> {
  return {
    name_template: settings.nameTemplate,
    site_templates: settings.siteTemplates,
    type_templates: settings.typeTemplates,
    name_conflict: settings.nameConflict,
    rules: settings.classificationRules,
    site_rules: settings.siteRules,
    extension_rules: settings.extensionRules,
    checksum_auto: settings.checksumAuto,
    post_cmd_enabled: settings.avHookEnabled,
    post_cmd: settings.avHookCommand,
    post_cmd_preset: settings.avHookPreset,
    post_cmd_timeout: settings.avHookTimeout,
    auto_extract_zip: settings.autoExtractZip,
    extract_to: settings.extractTo,
    extract_delete_zip: settings.extractDeleteZip,
    extract_max_uncompressed: settings.extractMaxUncompressed,
    extract_max_ratio: settings.extractMaxRatio,
    extract_promote_password: settings.extractPromotePassword,
  };
}
