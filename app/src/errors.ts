/** Map udl / sidecar errors to zh-TW strings for the queue UI. */

const code_messages: Record<string, string> = {
  unsupported: "此網址尚未支援或無法辨識",
  resolve_failed: "解析失敗，請確認連結是否有效",
  download_failed: "下載失敗，請稍後重試",
  ffmpeg_missing: "找不到 FFmpeg，請至設定下載或指定路徑",
  ffmpeg_provision_failed: "FFmpeg 自動下載失敗，請檢查網路後重試",
  merge_failed: "影音合併失敗（FFmpeg）",
  login_required: "需要登入才能取得此內容（本應用不繞過付費牆）",
  paywall: "此內容受付費／大會員限制，無法下載",
  region_locked: "此內容有地區限制，無法下載",
  cloudflare: "遇到 Cloudflare 挑戰；可在設定中選用繞過（預設關閉）",
  mega_tool_missing: "找不到 MEGA 工具，請安裝 megatools 或 MEGAcmd",
  cancelled: "已取消",
};

const text_patterns: Array<[RegExp, string]> = [
  [/ffmpeg not found/i, code_messages.ffmpeg_missing],
  [/ffmpeg_missing/i, code_messages.ffmpeg_missing],
  [/checksum mismatch/i, "FFmpeg 校驗失敗，檔案可能損壞或被竄改"],
  [/unsupported/i, code_messages.unsupported],
  [/cloudflare/i, code_messages.cloudflare],
  [/paywall|premium|大会员|大會員/i, code_messages.paywall],
  [/login|cookie|認証|認證/i, code_messages.login_required],
  [/timed?\s*out|timeout/i, "連線逾時，請檢查網路後重試"],
  [/connection|network|dns/i, "網路連線失敗，請稍後重試"],
  [/permission|denied|access/i, "沒有權限存取檔案或資料夾"],
  [/no space|disk/i, "磁碟空間不足"],
];

export function humanizeError(
  message?: string | null,
  code?: string | null,
): string {
  if (code && code_messages[code]) {
    const base = code_messages[code];
    if (message && message.trim() && !message.includes(code)) {
      return `${base}（${message.trim()}）`;
    }
    return base;
  }
  const raw = (message || "").trim();
  if (!raw) return "發生未知錯誤";
  for (const [re, text] of text_patterns) {
    if (re.test(raw)) return text;
  }
  return raw;
}

export function statusLabel(
  status: string,
): string {
  switch (status) {
    case "queued":
      return "排隊中";
    case "running":
    case "active":
      return "下載中";
    case "paused":
      return "已暫停";
    case "merging":
      return "合併中";
    case "done":
      return "完成";
    case "error":
      return "失敗";
    case "cancelled":
      return "已取消";
    case "skipped":
      return "已下載（略過）";
    default:
      return status;
  }
}
