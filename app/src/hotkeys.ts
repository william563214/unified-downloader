/** clipboard global hotkey helpers (CmdOrCtrl+Shift+D / M) */

export const default_download_binding = "CmdOrCtrl+Shift+D";
export const default_audio_binding = "CmdOrCtrl+Shift+M";

export type HotkeyRegisterResult = {
  ok: boolean;
  downloadRegistered: boolean;
  audioRegistered: boolean;
  conflicts: string[];
  errors: string[];
};

export function map_key_name(key: string): string | null {
  if (key.length === 1 && /[a-zA-Z]/.test(key)) return key.toUpperCase();
  if (key.length === 1 && /[0-9]/.test(key)) return key;
  if (/^F([1-9]|1[0-2])$/.test(key)) return key;
  const map: Record<string, string> = {
    " ": "Space",
    ArrowUp: "Up",
    ArrowDown: "Down",
    ArrowLeft: "Left",
    ArrowRight: "Right",
    Enter: "Enter",
    Tab: "Tab",
    Escape: "Escape",
    Backspace: "Backspace",
    Delete: "Delete",
    Home: "Home",
    End: "End",
    PageUp: "PageUp",
    PageDown: "PageDown",
    Insert: "Insert",
  };
  return map[key] ?? null;
}

export function binding_from_event(e: KeyboardEvent): string | null {
  if (["Control", "Shift", "Alt", "Meta"].includes(e.key)) return null;
  const key_name = map_key_name(e.key);
  if (!key_name) return null;
  const parts: string[] = [];
  if (e.ctrlKey || e.metaKey) parts.push("CmdOrCtrl");
  if (e.shiftKey) parts.push("Shift");
  if (e.altKey) parts.push("Alt");
  parts.push(key_name);
  return parts.join("+");
}

/** sites where --audio-only is honoured by the core extractor */
export function site_supports_audio_only(site: string | null | undefined): boolean {
  return (site || "").toLowerCase() === "bilibili";
}

export function hotkey_conflict_message(
  download_enabled: boolean,
  download_binding: string,
  audio_enabled: boolean,
  audio_binding: string,
): string | null {
  if (
    download_enabled &&
    audio_enabled &&
    download_binding.trim() &&
    download_binding.trim() === audio_binding.trim()
  ) {
    return `下載與僅音訊快捷鍵衝突：${download_binding}`;
  }
  return null;
}
