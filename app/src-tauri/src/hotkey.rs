//! Global clipboard hotkeys (download / audio-only).
//! Inspired by OpenSelena/omniget. Only enqueued URLs may be logged — never raw clipboard text.

use serde::Serialize;
use tauri::{AppHandle, Emitter};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use crate::{load_settings_file, AppSettings};

const default_download_accel: &str = "CmdOrCtrl+Shift+D";
const default_audio_accel: &str = "CmdOrCtrl+Shift+M";

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HotkeyRegisterResult {
    pub ok: bool,
    pub download_registered: bool,
    pub audio_registered: bool,
    pub conflicts: Vec<String>,
    pub errors: Vec<String>,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct ClipboardHotkeyPayload {
    mode: String,
    urls: Vec<String>,
    message: Option<String>,
}

pub fn default_download_binding() -> String {
    default_download_accel.into()
}

pub fn default_audio_binding() -> String {
    default_audio_accel.into()
}

pub fn normalize_binding(raw: &str, fallback: &str) -> String {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return fallback.to_string();
    }
    if trimmed.parse::<Shortcut>().is_ok() {
        return trimmed.to_string();
    }
    fallback.to_string()
}

fn extract_urls(text: &str) -> Vec<String> {
    let mut urls = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for line in text.lines() {
        let candidate = line.trim().trim_matches(|c: char| {
            matches!(c, '"' | '\'' | '<' | '>' | '(' | ')' | '[' | ']' | '{' | '}' | ',')
        });
        if !(candidate.starts_with("http://") || candidate.starts_with("https://")) {
            continue;
        }
        let cleaned = candidate
            .trim_end_matches(|c: char| matches!(c, '.' | ',' | ';' | ')' | ']' | '}' | '>'));
        if cleaned.is_empty() || !seen.insert(cleaned.to_string()) {
            continue;
        }
        if url_looks_valid(cleaned) {
            urls.push(cleaned.to_string());
        }
    }
    if urls.is_empty() {
        // fallback: scan whole text for http(s) tokens
        for token in text.split_whitespace() {
            let cleaned = token
                .trim_matches(|c: char| {
                    matches!(
                        c,
                        '"' | '\'' | '<' | '>' | '(' | ')' | '[' | ']' | '{' | '}' | ',' | '.'
                    )
                })
                .trim_end_matches(|c: char| matches!(c, '.' | ',' | ';' | ')' | ']' | '}' | '>'));
            if !(cleaned.starts_with("http://") || cleaned.starts_with("https://")) {
                continue;
            }
            if cleaned.is_empty() || !seen.insert(cleaned.to_string()) {
                continue;
            }
            if url_looks_valid(cleaned) {
                urls.push(cleaned.to_string());
            }
        }
    }
    urls
}

fn url_looks_valid(raw: &str) -> bool {
    // avoid pulling in the url crate; light validation is enough for enqueue
    let rest = raw
        .strip_prefix("https://")
        .or_else(|| raw.strip_prefix("http://"))
        .unwrap_or("");
    let host = rest.split(['/', '?', '#']).next().unwrap_or("");
    !host.is_empty() && host.contains('.')
}

fn matches_binding(pressed: &Shortcut, binding: &str) -> bool {
    binding
        .parse::<Shortcut>()
        .map(|s| s == *pressed)
        .unwrap_or(false)
}

fn emit_result(app: &AppHandle, mode: &str, urls: Vec<String>, message: Option<&str>) {
    let _ = app.emit(
        "clipboard-hotkey",
        ClipboardHotkeyPayload {
            mode: mode.into(),
            urls,
            message: message.map(|s| s.to_string()),
        },
    );
}

fn handle_clipboard_mode(app: &AppHandle, mode: &str) {
    let text = match app.clipboard().read_text() {
        Ok(t) => t,
        Err(_) => {
            emit_result(app, mode, Vec::new(), Some("無法讀取剪貼簿"));
            return;
        }
    };
    let urls = extract_urls(&text);
    // privacy: never log raw clipboard; only the URLs we enqueue
    if urls.is_empty() {
        emit_result(app, mode, Vec::new(), Some("剪貼簿沒有可下載的網址"));
        return;
    }
    for url in &urls {
        eprintln!("[hotkey] enqueue {mode}: {url}");
    }
    emit_result(app, mode, urls, None);
}

pub fn on_shortcut(app: &AppHandle, shortcut: &Shortcut, state: ShortcutState) {
    if state != ShortcutState::Pressed {
        return;
    }
    let settings = load_settings_file(app);
    let download_binding = normalize_binding(
        &settings.hotkey_download_binding,
        default_download_accel,
    );
    let audio_binding =
        normalize_binding(&settings.hotkey_audio_binding, default_audio_accel);

    if settings.hotkey_audio_enabled && matches_binding(shortcut, &audio_binding) {
        handle_clipboard_mode(app, "audio");
        return;
    }
    if settings.hotkey_download_enabled && matches_binding(shortcut, &download_binding) {
        handle_clipboard_mode(app, "download");
    }
}

fn register_one(
    app: &AppHandle,
    binding: &str,
    label: &str,
    conflicts: &mut Vec<String>,
    errors: &mut Vec<String>,
) -> bool {
    let shortcut = match binding.parse::<Shortcut>() {
        Ok(s) => s,
        Err(_) => {
            errors.push(format!("{label}: 無效快捷鍵「{binding}」"));
            return false;
        }
    };
    match app.global_shortcut().register(shortcut) {
        Ok(()) => true,
        Err(e) => {
            let msg = e.to_string();
            conflicts.push(format!("{label}: {binding}（{msg}）"));
            false
        }
    }
}

pub fn reregister(app: &AppHandle) -> HotkeyRegisterResult {
    let _ = app.global_shortcut().unregister_all();
    let settings = load_settings_file(app);
    apply_bindings(app, &settings)
}

fn apply_bindings(app: &AppHandle, settings: &AppSettings) -> HotkeyRegisterResult {
    let mut conflicts = Vec::new();
    let mut errors = Vec::new();
    let download_binding = normalize_binding(
        &settings.hotkey_download_binding,
        default_download_accel,
    );
    let audio_binding =
        normalize_binding(&settings.hotkey_audio_binding, default_audio_accel);

    if settings.hotkey_download_enabled
        && settings.hotkey_audio_enabled
        && download_binding == audio_binding
    {
        conflicts.push(format!(
            "下載與僅音訊快捷鍵相同（{download_binding}）"
        ));
        return HotkeyRegisterResult {
            ok: false,
            download_registered: false,
            audio_registered: false,
            conflicts,
            errors,
        };
    }

    let mut download_registered = false;
    let mut audio_registered = false;

    if settings.hotkey_download_enabled {
        download_registered = register_one(
            app,
            &download_binding,
            "下載",
            &mut conflicts,
            &mut errors,
        );
    }
    if settings.hotkey_audio_enabled {
        audio_registered =
            register_one(app, &audio_binding, "僅音訊", &mut conflicts, &mut errors);
    }

    let ok = errors.is_empty()
        && conflicts.is_empty()
        && (!settings.hotkey_download_enabled || download_registered)
        && (!settings.hotkey_audio_enabled || audio_registered);

    HotkeyRegisterResult {
        ok,
        download_registered,
        audio_registered,
        conflicts,
        errors,
    }
}

/// Try registering a candidate binding temporarily to detect OS-level conflicts.
#[tauri::command]
pub fn check_hotkey_binding(
    app: AppHandle,
    binding: String,
) -> Result<HotkeyRegisterResult, String> {
    let binding = binding.trim().to_string();
    if binding.is_empty() {
        return Ok(HotkeyRegisterResult {
            ok: false,
            download_registered: false,
            audio_registered: false,
            conflicts: Vec::new(),
            errors: vec!["快捷鍵不可為空".into()],
        });
    }
    let shortcut = binding
        .parse::<Shortcut>()
        .map_err(|_| format!("無效快捷鍵「{binding}」"))?;

    // unregister all then try the candidate; restore afterwards
    let _ = app.global_shortcut().unregister_all();
    let available = app.global_shortcut().register(shortcut).is_ok();
    let _ = app.global_shortcut().unregister_all();
    let restored = reregister(&app);

    if !available {
        return Ok(HotkeyRegisterResult {
            ok: false,
            download_registered: restored.download_registered,
            audio_registered: restored.audio_registered,
            conflicts: vec![format!("{binding} 可能已被其他程式占用")],
            errors: restored.errors,
        });
    }
    Ok(HotkeyRegisterResult {
        ok: restored.ok,
        download_registered: restored.download_registered,
        audio_registered: restored.audio_registered,
        conflicts: restored.conflicts,
        errors: restored.errors,
    })
}

#[tauri::command]
pub fn register_clipboard_hotkeys(app: AppHandle) -> Result<HotkeyRegisterResult, String> {
    Ok(reregister(&app))
}

pub fn init_plugin() -> tauri_plugin_global_shortcut::Builder {
    tauri_plugin_global_shortcut::Builder::new().with_handler(|app, shortcut, event| {
        on_shortcut(app, shortcut, event.state);
    })
}
