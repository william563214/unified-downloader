const url_pattern = /https?:\/\/[^\s<>"'`]+/gi;

export function extractUrl(text: string): string | null {
  const match = text.trim().match(/https?:\/\/[^\s<>"'`]+/i);
  if (!match) return null;
  return match[0].replace(/[.,);\]}>]+$/, "");
}

export function extractUrls(text: string): string[] {
  const found: string[] = [];
  const seen = new Set<string>();
  const matches = text.match(url_pattern) || [];
  for (const raw of matches) {
    const cleaned = raw.replace(/[.,);\]}>]+$/, "");
    if (!cleaned || seen.has(cleaned)) continue;
    seen.add(cleaned);
    found.push(cleaned);
  }
  // also accept one-url-per-line without scheme for bilibili short forms is out of scope
  return found;
}

export function detectSite(url: string): { site: string; label: string } | null {
  const u = url.toLowerCase();
  try {
    const host = new URL(url).hostname.toLowerCase();
    if (host.includes("gofile.io")) return { site: "gofile", label: "Gofile" };
    if (host.includes("bunkr") || host.includes("bunkr.")) return { site: "bunkr", label: "Bunkr" };
    if (host.includes("iwara.tv")) return { site: "iwara", label: "Iwara" };
    if (
      host.includes("bilibili.com") ||
      host.includes("b23.tv") ||
      host.includes("bili2233.cn") ||
      host.includes("bili23")
    ) {
      return { site: "bilibili", label: "Bilibili" };
    }
    if (host.includes("mega.nz") || host.includes("mega.co.nz") || host.includes("mega.io")) {
      return { site: "mega", label: "MEGA" };
    }
  } catch {
    if (u.includes("gofile")) return { site: "gofile", label: "Gofile" };
    if (u.includes("bunkr")) return { site: "bunkr", label: "Bunkr" };
    if (u.includes("iwara")) return { site: "iwara", label: "Iwara" };
    if (u.includes("bilibili") || u.includes("b23.tv")) return { site: "bilibili", label: "Bilibili" };
    if (u.includes("mega.")) return { site: "mega", label: "MEGA" };
  }
  return null;
}
