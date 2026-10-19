export type AppSettings = {
  downloadDir: string;
  maxConcurrent: number;
  workers: number;
  ffmpegPath: string;
  iwaraQuality: string;
  iwaraCloudflareBypass: boolean;
  bilibiliQuality: string;
  bilibiliPart: string;
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
};

export type FfmpegStatus = {
  found: boolean;
  path: string | null;
  source: string | null;
  platform: string;
  provisionSupported: boolean;
  message: string;
};

const url_pattern =
  /https?:\/\/[^\s<>"'`]+/i;

export function extractUrl(text: string): string | null {
  const match = text.trim().match(url_pattern);
  if (!match) return null;
  return match[0].replace(/[.,);\]}>]+$/, "");
}
