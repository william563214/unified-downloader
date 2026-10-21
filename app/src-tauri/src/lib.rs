//! Tauri shell for 整合下載器 — spawns the `udl` Python sidecar and forwards JSON-lines.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

struct JobState {
    children: Mutex<HashMap<String, CommandChild>>,
}

#[derive(Clone, Serialize)]
struct UdlEventPayload {
    job_id: String,
    line: serde_json::Value,
}

#[derive(Clone, Serialize)]
struct ResolveResult {
    site: Option<String>,
    label: Option<String>,
    kind: Option<String>,
    url: String,
    supported: bool,
    status: Option<String>,
    message: Option<String>,
}

#[derive(Clone, Serialize)]
struct ProbeResult {
    site: Option<String>,
    kind: Option<String>,
    title: Option<String>,
    url: String,
    parts: Vec<serde_json::Value>,
    qualities: Vec<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    codecs: Vec<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    audio_qualities: Vec<serde_json::Value>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    subtitle_tracks: Vec<serde_json::Value>,
    default_part: Option<i64>,
    default_quality: Option<i64>,
    message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pagination: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    naming_template: Option<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", default)]
struct AppSettings {
    download_dir: String,
    max_concurrent: u32,
    workers: u32,
    ffmpeg_path: String,
    iwara_quality: String,
    iwara_cloudflare_bypass: bool,
    bilibili_quality: String,
    bilibili_part: String,
    #[serde(default = "default_on_duplicate")]
    on_duplicate: String,
    #[serde(default)]
    write_nfo: bool,
    #[serde(default)]
    write_cover: bool,
    #[serde(default)]
    embed_cover: bool,
    #[serde(default = "default_cover_naming")]
    cover_naming: String,
    #[serde(default = "default_site_flags")]
    nfo_sites: HashMap<String, bool>,
    #[serde(default = "default_site_flags")]
    cover_sites: HashMap<String, bool>,
    #[serde(default)]
    name_template: String,
    #[serde(default)]
    site_templates: HashMap<String, String>,
    #[serde(default)]
    type_templates: HashMap<String, String>,
    #[serde(default = "default_name_conflict")]
    name_conflict: String,
    #[serde(default)]
    classification_rules: Vec<serde_json::Value>,
    #[serde(default)]
    site_rules: Vec<serde_json::Value>,
    #[serde(default)]
    extension_rules: Vec<serde_json::Value>,
    // networking v0.5
    #[serde(default)]
    speed_limit: String,
    #[serde(default)]
    job_speed_limit: String,
    #[serde(default = "default_proxy_mode")]
    proxy_mode: String,
    #[serde(default)]
    proxy_url: String,
    #[serde(default)]
    proxy_username: String,
    #[serde(default)]
    user_agent: String,
    // curl_cffi browser impersonation: opt-in / off by default
    #[serde(default)]
    curl_cffi_impersonate: bool,
    #[serde(default = "default_impersonate_target")]
    curl_cffi_impersonate_target: String,
    #[serde(default = "default_request_handler")]
    request_handler: String,
    #[serde(default)]
    aria2_enabled: bool,
    #[serde(default = "default_aria2_rpc_url")]
    aria2_rpc_url: String,
    #[serde(default)]
    aria2_rpc_secret: String,
    // multi-connection segmented (v0.9-A)
    #[serde(default = "default_connections")]
    connections: u32,
    #[serde(default = "default_true")]
    segmented_enabled: bool,
    #[serde(default)]
    segmented_for_sites: bool,
    // bilibili CDN / Host (v0.7-B networking)
    #[serde(default)]
    cdn_hosts: String,
    #[serde(default)]
    prefer_cdn: bool,
    #[serde(default)]
    custom_host: String,
    // MEGA built-in (default) or external megatools/MEGAcmd fallback
    #[serde(default = "default_mega_backend")]
    mega_backend: String,
    #[serde(default)]
    mega_tool_path: String,
    // gallery-dl external CLI (GPL-2.0-only subprocess; mere aggregation)
    #[serde(default = "default_true")]
    gallerydl_enabled: bool,
    #[serde(default)]
    gallerydl_tool_path: String,
    #[serde(default)]
    gallerydl_cookies_path: String,
    #[serde(default)]
    gallerydl_options: String,
    #[serde(default)]
    gallerydl_use_archive: bool,
    // shell / UX (v0.8)
    #[serde(default)]
    clipboard_watcher: bool,
    #[serde(default = "default_true")]
    notifications_enabled: bool,
    #[serde(default)]
    minimize_to_tray: bool,
    #[serde(default = "default_theme_mode")]
    theme_mode: String,
    // v0.9-D checksum / post-download hook
    #[serde(default = "default_checksum_auto")]
    checksum_auto: bool,
    #[serde(default)]
    av_hook_enabled: bool,
    #[serde(default)]
    av_hook_preset: String,
    #[serde(default)]
    av_hook_command: String,
    #[serde(default = "default_av_hook_timeout")]
    av_hook_timeout: u32,
    // auto-extract ZIP after download (default off)
    #[serde(default)]
    auto_extract_zip: bool,
    #[serde(default = "default_extract_to")]
    extract_to: String,
    #[serde(default)]
    extract_delete_zip: bool,
    #[serde(default = "default_extract_max_uncompressed")]
    extract_max_uncompressed: u64,
    #[serde(default = "default_extract_max_ratio")]
    extract_max_ratio: f64,
    #[serde(default = "default_true")]
    extract_promote_password: bool,
}

fn default_true() -> bool {
    true
}

fn default_theme_mode() -> String {
    "system".into()
}

fn default_checksum_auto() -> bool {
    true
}

fn default_av_hook_timeout() -> u32 {
    120
}

fn default_extract_to() -> String {
    "subfolder".into()
}

fn default_extract_max_uncompressed() -> u64 {
    4 * 1024 * 1024 * 1024
}

fn default_extract_max_ratio() -> f64 {
    100.0
}

fn default_on_duplicate() -> String {
    "skip".into()
}

fn default_cover_naming() -> String {
    "poster".into()
}

fn default_name_conflict() -> String {
    "rename".into()
}

fn default_proxy_mode() -> String {
    "none".into()
}

fn default_impersonate_target() -> String {
    "chrome".into()
}

fn default_request_handler() -> String {
    "auto".into()
}

fn default_aria2_rpc_url() -> String {
    "http://127.0.0.1:6800/jsonrpc".into()
}

fn default_mega_backend() -> String {
    "builtin".into()
}

fn default_connections() -> u32 {
    8
}

fn default_site_flags() -> HashMap<String, bool> {
    let mut map = HashMap::new();
    for site in [
        "gofile",
        "bunkr",
        "iwara",
        "bilibili",
        "mega",
        "gallerydl",
        "direct",
    ] {
        map.insert(site.into(), true);
    }
    map
}

fn sites_cli_list(flags: &HashMap<String, bool>) -> String {
    let sites = [
        "gofile",
        "bunkr",
        "iwara",
        "bilibili",
        "mega",
        "gallerydl",
        "direct",
    ];
    let enabled: Vec<&str> = sites
        .iter()
        .copied()
        .filter(|s| *flags.get(*s).unwrap_or(&true))
        .collect();
    let disabled: Vec<&str> = sites
        .iter()
        .copied()
        .filter(|s| !*flags.get(*s).unwrap_or(&true))
        .collect();
    if enabled.len() == sites.len() {
        return "all".into();
    }
    if enabled.is_empty() {
        return "none".into();
    }
    if disabled.len() <= enabled.len() {
        let mut parts = vec!["all".to_string()];
        for site in disabled {
            parts.push(format!("-{site}"));
        }
        return parts.join(",");
    }
    enabled.join(",")
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            download_dir: String::new(),
            max_concurrent: 2,
            workers: 3,
            ffmpeg_path: String::new(),
            iwara_quality: "Source".into(),
            iwara_cloudflare_bypass: false,
            bilibili_quality: String::new(),
            bilibili_part: "all".into(),
            on_duplicate: default_on_duplicate(),
            write_nfo: false,
            write_cover: false,
            embed_cover: false,
            cover_naming: default_cover_naming(),
            nfo_sites: default_site_flags(),
            cover_sites: default_site_flags(),
            name_template: String::new(),
            site_templates: HashMap::new(),
            type_templates: HashMap::new(),
            name_conflict: default_name_conflict(),
            classification_rules: Vec::new(),
            site_rules: Vec::new(),
            extension_rules: Vec::new(),
            speed_limit: String::new(),
            job_speed_limit: String::new(),
            proxy_mode: default_proxy_mode(),
            proxy_url: String::new(),
            proxy_username: String::new(),
            user_agent: String::new(),
            curl_cffi_impersonate: false,
            curl_cffi_impersonate_target: default_impersonate_target(),
            request_handler: default_request_handler(),
            aria2_enabled: false,
            aria2_rpc_url: default_aria2_rpc_url(),
            aria2_rpc_secret: String::new(),
            connections: default_connections(),
            segmented_enabled: true,
            segmented_for_sites: false,
            cdn_hosts: String::new(),
            prefer_cdn: false,
            custom_host: String::new(),
            mega_backend: default_mega_backend(),
            mega_tool_path: String::new(),
            gallerydl_enabled: true,
            gallerydl_tool_path: String::new(),
            gallerydl_cookies_path: String::new(),
            gallerydl_options: String::new(),
            gallerydl_use_archive: false,
            clipboard_watcher: false,
            notifications_enabled: true,
            minimize_to_tray: false,
            theme_mode: default_theme_mode(),
            checksum_auto: default_checksum_auto(),
            av_hook_enabled: false,
            av_hook_preset: String::new(),
            av_hook_command: String::new(),
            av_hook_timeout: default_av_hook_timeout(),
            auto_extract_zip: false,
            extract_to: default_extract_to(),
            extract_delete_zip: false,
            extract_max_uncompressed: default_extract_max_uncompressed(),
            extract_max_ratio: default_extract_max_ratio(),
            extract_promote_password: true,
        }
    }
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct FfmpegStatus {
    found: bool,
    path: Option<String>,
    source: Option<String>,
    platform: String,
    provision_supported: bool,
    message: String,
}

fn core_dir(app: &AppHandle) -> PathBuf {
    // repo layout: app/src-tauri -> ../../core
    if let Ok(cwd) = std::env::current_dir() {
        let candidate = cwd.join("../core");
        if candidate.join("udl").is_dir() {
            return candidate;
        }
        let candidate = cwd.join("../../core");
        if candidate.join("udl").is_dir() {
            return candidate;
        }
    }
    if let Ok(res) = app.path().resource_dir() {
        return res;
    }
    PathBuf::from(".")
}

fn app_data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    app.path()
        .app_data_dir()
        .map_err(|e| format!("app data dir: {e}"))
}

fn settings_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_dir(app)?.join("settings.json"))
}

fn ffmpeg_install_dir(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_dir(app)?.join("ffmpeg"))
}

fn load_settings_file(app: &AppHandle) -> AppSettings {
    let Ok(path) = settings_path(app) else {
        return AppSettings::default();
    };
    let Ok(raw) = fs::read_to_string(path) else {
        return AppSettings::default();
    };
    serde_json::from_str(&raw).unwrap_or_default()
}

fn use_sidecar_binary() -> bool {
    // Prefer packaged sidecar unless UDL_DEV=1
    std::env::var("UDL_DEV").ok().as_deref() != Some("1")
        && std::env::var("UDL_FORCE_SIDECAR").ok().as_deref() != Some("0")
}

fn append_proxy_ua_args(args: &mut Vec<String>, settings: &AppSettings) {
    let mode = settings.proxy_mode.trim().to_lowercase();
    let mode = match mode.as_str() {
        "system" | "custom" => mode,
        _ => "none".into(),
    };
    args.push("--proxy-mode".into());
    args.push(mode.clone());
    if mode == "custom" {
        let proxy = settings.proxy_url.trim();
        if !proxy.is_empty() {
            args.push("--proxy".into());
            args.push(proxy.to_string());
        }
        let user = settings.proxy_username.trim();
        if !user.is_empty() {
            args.push("--proxy-user".into());
            args.push(user.to_string());
        }
    }
    let ua = settings.user_agent.trim();
    if !ua.is_empty() {
        args.push("--user-agent".into());
        args.push(ua.to_string());
    }
    let handler = settings.request_handler.trim().to_lowercase();
    let handler = match handler.as_str() {
        "requests" | "urllib" | "curl_cffi" => handler,
        _ => "auto".into(),
    };
    if handler != "auto" {
        args.push("--request-handler".into());
        args.push(handler);
    }
    if settings.curl_cffi_impersonate {
        args.push("--impersonate".into());
        let target = settings.curl_cffi_impersonate_target.trim();
        if !target.is_empty() {
            args.push(target.to_string());
        }
    }
}

fn append_gallerydl_args(args: &mut Vec<String>, settings: &AppSettings) {
    if !settings.gallerydl_enabled {
        args.push("--no-gallerydl".into());
    }
    let tool = settings.gallerydl_tool_path.trim();
    if !tool.is_empty() {
        args.push("--gallerydl-tool".into());
        args.push(tool.to_string());
    }
    let cookies = settings.gallerydl_cookies_path.trim();
    if !cookies.is_empty() {
        args.push("--gallerydl-cookies".into());
        args.push(cookies.to_string());
    }
    for line in settings.gallerydl_options.split('\n') {
        let opt = line.trim();
        if opt.is_empty() {
            continue;
        }
        args.push("--gallerydl-option".into());
        args.push(opt.to_string());
    }
    if settings.gallerydl_use_archive {
        args.push("--gallerydl-archive".into());
    }
}

fn append_network_args(
    args: &mut Vec<String>,
    settings: &AppSettings,
    speed_limit_override: Option<&str>,
) {
    append_proxy_ua_args(args, settings);
    let override_speed = speed_limit_override
        .map(str::trim)
        .filter(|s| !s.is_empty());
    let speed = override_speed.unwrap_or_else(|| settings.speed_limit.trim());
    if !speed.is_empty() {
        args.push("--speed-limit".into());
        args.push(speed.to_string());
    }
    let job_speed = settings.job_speed_limit.trim();
    if !job_speed.is_empty() {
        args.push("--job-speed-limit".into());
        args.push(job_speed.to_string());
    }
    if settings.aria2_enabled {
        args.push("--aria2".into());
        let rpc = settings.aria2_rpc_url.trim();
        if !rpc.is_empty() {
            args.push("--aria2-rpc".into());
            args.push(rpc.to_string());
        }
        let secret = settings.aria2_rpc_secret.trim();
        if !secret.is_empty() {
            args.push("--aria2-secret".into());
            args.push(secret.to_string());
        }
    }
    let conns = settings.connections.max(1).min(32);
    args.push("--connections".into());
    args.push(conns.to_string());
    if !settings.segmented_enabled {
        args.push("--no-segmented".into());
    }
    if settings.segmented_for_sites {
        args.push("--segmented-for-sites".into());
    }
    let cdn = settings.cdn_hosts.trim();
    if !cdn.is_empty() {
        args.push("--cdn-hosts".into());
        args.push(cdn.to_string());
    }
    if settings.prefer_cdn {
        args.push("--prefer-cdn".into());
    }
    let host = settings.custom_host.trim();
    if !host.is_empty() {
        args.push("--custom-host".into());
        args.push(host.to_string());
    }
}

fn bundled_gallerydl_path(app: &AppHandle) -> Option<PathBuf> {
    // Tauri externalBin sibling of udl sidecar (mere aggregation)
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            for name in ["gallery-dl", "gallery-dl.exe"] {
                let candidate = dir.join(name);
                if candidate.is_file() {
                    return Some(candidate);
                }
            }
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    let fname = path
                        .file_name()
                        .and_then(|s| s.to_str())
                        .unwrap_or_default();
                    if fname.starts_with("gallery-dl-") && path.is_file() {
                        return Some(path);
                    }
                }
            }
        }
    }
    if let Ok(res) = app.path().resource_dir() {
        for name in ["gallery-dl", "gallery-dl.exe"] {
            let candidate = res.join(name);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        let nested = res.join("gallery-dl").join(if cfg!(target_os = "windows") {
            "gallery-dl.exe"
        } else {
            "gallery-dl"
        });
        if nested.is_file() {
            return Some(nested);
        }
    }
    None
}

fn spawn_udl(
    app: &AppHandle,
    args: Vec<String>,
) -> Result<(tauri::async_runtime::Receiver<CommandEvent>, CommandChild), String> {
    // FFmpeg path is passed via --ffmpeg from start_download / ffmpeg_status
    // Managed install dir matches tauri app_data_dir/ffmpeg (Python app_data_name)
    let shell = app.shell();
    let settings = load_settings_file(app);
    let mut env_pairs: Vec<(String, String)> = Vec::new();
    let tool_setting = settings.gallerydl_tool_path.trim();
    if !tool_setting.is_empty() {
        env_pairs.push(("UDL_GALLERY_DL".into(), tool_setting.to_string()));
    } else if let Some(bundled) = bundled_gallerydl_path(app) {
        env_pairs.push((
            "UDL_GALLERY_DL".into(),
            bundled.to_string_lossy().into_owned(),
        ));
    }
    if !settings.gallerydl_enabled {
        env_pairs.push(("UDL_GALLERY_DL_ENABLED".into(), "0".into()));
    }

    if use_sidecar_binary() {
        match shell.sidecar("udl") {
            Ok(cmd) => {
                let mut cmd = cmd.args(args);
                for (k, v) in &env_pairs {
                    cmd = cmd.env(k, v);
                }
                return cmd
                    .spawn()
                    .map_err(|e| format!("spawn sidecar udl: {e}"));
            }
            Err(e) => {
                // Fall through to uv in development when binaries/ are missing
                eprintln!("sidecar unavailable ({e}); falling back to uv run");
            }
        }
    }

    let core = core_dir(app);
    let mut full_args = vec![
        "run".into(),
        "--directory".into(),
        core.to_string_lossy().into_owned(),
        "udl".into(),
    ];
    full_args.extend(args);
    let mut cmd = shell.command("uv").args(full_args);
    for (k, v) in &env_pairs {
        cmd = cmd.env(k, v);
    }
    cmd.spawn()
        .map_err(|e| format!("spawn uv udl: {e}"))
}

async fn collect_stdout_json(
    rx: &mut tauri::async_runtime::Receiver<CommandEvent>,
) -> Result<(String, String, Option<i32>), String> {
    let mut stdout = String::new();
    let mut stderr = String::new();
    let mut code = None;
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                stdout.push_str(&String::from_utf8_lossy(&line));
            }
            CommandEvent::Stderr(line) => {
                stderr.push_str(&String::from_utf8_lossy(&line));
            }
            CommandEvent::Error(err) => return Err(err),
            CommandEvent::Terminated(payload) => {
                code = payload.code;
                break;
            }
            _ => {}
        }
    }
    Ok((stdout, stderr, code))
}

#[tauri::command]
fn get_settings(app: AppHandle) -> Result<AppSettings, String> {
    Ok(load_settings_file(&app))
}

fn naming_config_path(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(app_data_dir(app)?.join("udl.config.json"))
}

fn settings_to_udl_config(settings: &AppSettings) -> serde_json::Value {
    serde_json::json!({
        "name_template": settings.name_template,
        "site_templates": settings.site_templates,
        "type_templates": settings.type_templates,
        "name_conflict": settings.name_conflict,
        "rules": settings.classification_rules,
        "site_rules": settings.site_rules,
        "extension_rules": settings.extension_rules,
        "checksum_auto": settings.checksum_auto,
        "post_cmd_enabled": settings.av_hook_enabled,
        "post_cmd": settings.av_hook_command,
        "post_cmd_preset": settings.av_hook_preset,
        "post_cmd_timeout": settings.av_hook_timeout,
        "auto_extract_zip": settings.auto_extract_zip,
        "extract_to": settings.extract_to,
        "extract_delete_zip": settings.extract_delete_zip,
        "extract_max_uncompressed": settings.extract_max_uncompressed,
        "extract_max_ratio": settings.extract_max_ratio,
        "extract_promote_password": settings.extract_promote_password,
    })
}

#[tauri::command]
fn save_settings(app: AppHandle, settings: AppSettings) -> Result<AppSettings, String> {
    let mut settings = settings;
    if settings.max_concurrent == 0 {
        settings.max_concurrent = 1;
    }
    if settings.max_concurrent > 8 {
        settings.max_concurrent = 8;
    }
    if settings.workers == 0 {
        settings.workers = 1;
    }
    if settings.workers > 16 {
        settings.workers = 16;
    }
    if settings.connections == 0 {
        settings.connections = 1;
    }
    if settings.connections > 32 {
        settings.connections = 32;
    }
    let conflict = settings.name_conflict.trim();
    settings.name_conflict = match conflict {
        "overwrite" | "error" | "rename" => conflict.to_string(),
        _ => default_name_conflict(),
    };
    let mode = settings.proxy_mode.trim().to_lowercase();
    settings.proxy_mode = match mode.as_str() {
        "system" | "custom" => mode,
        _ => "none".into(),
    };
    let theme = settings.theme_mode.trim().to_lowercase();
    settings.theme_mode = match theme.as_str() {
        "light" | "dark" | "system" => theme,
        _ => default_theme_mode(),
    };
    if settings.aria2_rpc_url.trim().is_empty() {
        settings.aria2_rpc_url = default_aria2_rpc_url();
    }
    let path = settings_path(&app)?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let raw = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
    fs::write(&path, raw).map_err(|e| e.to_string())?;
    // mirror naming/rules into core config for CLI parity
    let cfg_path = naming_config_path(&app)?;
    let cfg_raw =
        serde_json::to_string_pretty(&settings_to_udl_config(&settings)).map_err(|e| e.to_string())?;
    fs::write(&cfg_path, cfg_raw).map_err(|e| e.to_string())?;
    Ok(settings)
}

#[tauri::command]
async fn set_proxy_password(app: AppHandle, password: String) -> Result<(), String> {
    // store via udl secrets so Python keyring backend stays the single source
    let args = vec![
        "secrets".into(),
        "set".into(),
        "proxy.custom".into(),
        password,
    ];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (_stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("store proxy password failed: {stderr}"));
    }
    Ok(())
}

#[tauri::command]
async fn clear_proxy_password(app: AppHandle) -> Result<(), String> {
    let args = vec!["secrets".into(), "delete".into(), "proxy.custom".into()];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (_stdout, _stderr, _code) = collect_stdout_json(&mut rx).await?;
    Ok(())
}

#[tauri::command]
async fn set_zip_password(
    app: AppHandle,
    password: String,
    label: Option<String>,
) -> Result<serde_json::Value, String> {
    // append to ordered password table in udl.secrets; never logged
    let mut args = vec![
        "zip-passwords".into(),
        "add".into(),
        "--json".into(),
        password,
    ];
    if let Some(lbl) = label.filter(|s| !s.trim().is_empty()) {
        args.push("--label".into());
        args.push(lbl);
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("store zip password failed: {stderr}"));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse zip-passwords add: {e}; raw={stdout}"))
}

#[tauri::command]
async fn clear_zip_password(app: AppHandle) -> Result<serde_json::Value, String> {
    let args = vec!["zip-passwords".into(), "clear".into(), "--json".into()];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("clear zip passwords failed: {stderr}"));
    }
    serde_json::from_str(stdout.trim()).or_else(|_| Ok(serde_json::json!({"ok": true})))
}

#[tauri::command]
async fn zip_passwords_list(app: AppHandle) -> Result<serde_json::Value, String> {
    let args = vec!["zip-passwords".into(), "list".into(), "--json".into()];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("zip-passwords list failed: {stderr}"));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse zip-passwords list: {e}; raw={stdout}"))
}

#[tauri::command]
async fn zip_passwords_add(
    app: AppHandle,
    password: String,
    label: Option<String>,
) -> Result<serde_json::Value, String> {
    set_zip_password(app, password, label).await
}

#[tauri::command]
async fn zip_passwords_update(
    app: AppHandle,
    id: String,
    password: Option<String>,
    label: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "zip-passwords".into(),
        "set".into(),
        id,
        "--json".into(),
    ];
    if let Some(pw) = password.filter(|s| !s.is_empty()) {
        args.push("--password".into());
        args.push(pw);
    }
    if let Some(lbl) = label {
        args.push("--label".into());
        args.push(lbl);
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("zip-passwords set failed: {stderr}"));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse zip-passwords set: {e}; raw={stdout}"))
}

#[tauri::command]
async fn zip_passwords_remove(app: AppHandle, id: String) -> Result<serde_json::Value, String> {
    let args = vec![
        "zip-passwords".into(),
        "remove".into(),
        id,
        "--json".into(),
    ];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("zip-passwords remove failed: {stderr}"));
    }
    serde_json::from_str(stdout.trim()).or_else(|_| Ok(serde_json::json!({"ok": true})))
}

#[tauri::command]
async fn zip_passwords_reorder(
    app: AppHandle,
    ids: Vec<String>,
) -> Result<serde_json::Value, String> {
    let joined = ids
        .into_iter()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join(",");
    let args = vec![
        "zip-passwords".into(),
        "reorder".into(),
        joined,
        "--json".into(),
    ];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("zip-passwords reorder failed: {stderr}"));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse zip-passwords reorder: {e}; raw={stdout}"))
}

async fn run_bili_login_json(
    app: &AppHandle,
    mut extra: Vec<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec!["bili".into(), "login".into()];
    args.append(&mut extra);
    args.push("--json".into());
    let settings = load_settings_file(app);
    append_proxy_ua_args(&mut args, &settings);
    let (mut rx, _child) = spawn_udl(app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 && stdout.trim().is_empty() {
        return Err(format!("udl bili login exited {:?}: {stderr}", code));
    }
    let value: serde_json::Value = serde_json::from_str(stdout.trim()).map_err(|e| {
        format!("parse bili login JSON: {e}; raw={stdout}; err={stderr}")
    })?;
    if let Some(err) = value.get("error").and_then(|v| v.as_str()) {
        return Err(err.to_string());
    }
    Ok(value)
}

#[tauri::command]
async fn bili_login_status(app: AppHandle) -> Result<serde_json::Value, String> {
    // status exit 1 when logged out is OK — still return JSON profile
    let mut args = vec![
        "bili".into(),
        "login".into(),
        "status".into(),
        "--json".into(),
    ];
    let settings = load_settings_file(&app);
    append_proxy_ua_args(&mut args, &settings);
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, _code) = collect_stdout_json(&mut rx).await?;
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse login status: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn bili_login_qr(app: AppHandle) -> Result<serde_json::Value, String> {
    run_bili_login_json(&app, vec!["qr".into()]).await
}

#[tauri::command]
async fn bili_login_qr_poll(app: AppHandle, key: String) -> Result<serde_json::Value, String> {
    run_bili_login_json(
        &app,
        vec!["qr-poll".into(), "--key".into(), key],
    )
    .await
}

#[tauri::command]
async fn bili_login_cookie(app: AppHandle, text: String) -> Result<serde_json::Value, String> {
    run_bili_login_json(
        &app,
        vec!["cookie".into(), "--text".into(), text],
    )
    .await
}

#[tauri::command]
async fn bili_login_refresh(app: AppHandle) -> Result<serde_json::Value, String> {
    run_bili_login_json(&app, vec!["refresh".into()]).await
}

#[tauri::command]
async fn bili_login_logout(app: AppHandle) -> Result<serde_json::Value, String> {
    run_bili_login_json(&app, vec!["logout".into()]).await
}

#[tauri::command]
async fn bili_login_sms_captcha(app: AppHandle) -> Result<serde_json::Value, String> {
    run_bili_login_json(&app, vec!["sms-captcha".into()]).await
}

#[tauri::command]
async fn bili_login_sms_send(
    app: AppHandle,
    tel: String,
    cid: Option<String>,
    token: String,
    challenge: String,
    validate: String,
    seccode: String,
) -> Result<serde_json::Value, String> {
    run_bili_login_json(
        &app,
        vec![
            "sms-send".into(),
            "--tel".into(),
            tel,
            "--cid".into(),
            cid.unwrap_or_else(|| "86".into()),
            "--token".into(),
            token,
            "--challenge".into(),
            challenge,
            "--validate".into(),
            validate,
            "--seccode".into(),
            seccode,
        ],
    )
    .await
}

#[tauri::command]
async fn bili_login_sms(
    app: AppHandle,
    tel: String,
    code: String,
    captcha_key: String,
    cid: Option<String>,
) -> Result<serde_json::Value, String> {
    run_bili_login_json(
        &app,
        vec![
            "sms".into(),
            "--tel".into(),
            tel,
            "--cid".into(),
            cid.unwrap_or_else(|| "86".into()),
            "--code".into(),
            code,
            "--captcha-key".into(),
            captcha_key,
        ],
    )
    .await
}

#[tauri::command]
async fn open_geetest_window(
    app: AppHandle,
    gt: String,
    challenge: String,
    token: String,
) -> Result<(), String> {
    let gt = gt.trim().to_string();
    let challenge = challenge.trim().to_string();
    let token = token.trim().to_string();
    if gt.is_empty() || challenge.is_empty() {
        return Err("gt and challenge required".into());
    }
    // close previous geetest window if any
    if let Some(existing) = app.get_webview_window("geetest") {
        let _ = existing.close();
    }
    let query = format!(
        "gt={}&challenge={}&token={}",
        urlencoding_encode(&gt),
        urlencoding_encode(&challenge),
        urlencoding_encode(&token)
    );
    let path = format!("geetest.html?{query}");
    WebviewWindowBuilder::new(&app, "geetest", WebviewUrl::App(path.into()))
        .title("Bilibili GeeTest")
        .inner_size(440.0, 520.0)
        .resizable(true)
        .build()
        .map_err(|e| format!("open geetest window: {e}"))?;
    Ok(())
}

#[tauri::command]
fn close_geetest_window(app: AppHandle) -> Result<(), String> {
    if let Some(existing) = app.get_webview_window("geetest") {
        existing.close().map_err(|e| format!("close geetest: {e}"))?;
    }
    Ok(())
}

fn urlencoding_encode(value: &str) -> String {
    let mut out = String::with_capacity(value.len() * 3);
    for b in value.as_bytes() {
        match *b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(*b as char);
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

#[tauri::command]
fn get_app_paths(app: AppHandle) -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({
        "appData": app_data_dir(&app)?.to_string_lossy(),
        "ffmpegDir": ffmpeg_install_dir(&app)?.to_string_lossy(),
        "settings": settings_path(&app)?.to_string_lossy(),
    }))
}

#[tauri::command]
async fn page_scan(
    app: AppHandle,
    url: String,
    ext: Option<String>,
    types: Option<String>,
    size_min: Option<String>,
    size_max: Option<String>,
    regex: Option<String>,
    glob: Option<String>,
    same_domain: Option<bool>,
    include_images: Option<bool>,
    no_head: Option<bool>,
) -> Result<serde_json::Value, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["page".into(), url, "--json".into()];
    if let Some(v) = ext.filter(|s| !s.trim().is_empty()) {
        args.push("--ext".into());
        args.push(v);
    }
    if let Some(v) = types.filter(|s| !s.trim().is_empty()) {
        args.push("--type".into());
        args.push(v);
    }
    if let Some(v) = size_min.filter(|s| !s.trim().is_empty()) {
        args.push("--size-min".into());
        args.push(v);
    }
    if let Some(v) = size_max.filter(|s| !s.trim().is_empty()) {
        args.push("--size-max".into());
        args.push(v);
    }
    if let Some(v) = regex.filter(|s| !s.trim().is_empty()) {
        args.push("--regex".into());
        args.push(v);
    }
    if let Some(v) = glob.filter(|s| !s.trim().is_empty()) {
        args.push("--glob".into());
        args.push(v);
    }
    if same_domain.unwrap_or(false) {
        args.push("--same-domain".into());
    }
    if include_images.unwrap_or(false) {
        args.push("--images".into());
    }
    if no_head.unwrap_or(false) {
        args.push("--no-head".into());
    }
    append_proxy_ua_args(&mut args, &settings);
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 && stdout.trim().is_empty() {
        return Err(format!("udl page exited {:?}: {stderr}", code));
    }
    // page --json may emit JSON-lines; take last non-empty line
    let line = stdout
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .last()
        .unwrap_or("");
    serde_json::from_str(line)
        .map_err(|e| format!("parse page JSON: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn resolve_url(app: AppHandle, url: String) -> Result<ResolveResult, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["resolve".into(), url.clone()];
    append_proxy_ua_args(&mut args, &settings);
    append_gallerydl_args(&mut args, &settings);
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) > 2 {
        return Err(format!("udl resolve exited {:?}: {stderr}", code));
    }
    let value: serde_json::Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse resolve JSON: {e}; raw={stdout}; err={stderr}"))?;
    Ok(ResolveResult {
        site: value
            .get("site")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        label: value
            .get("label")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        kind: value
            .get("kind")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        url: value
            .get("url")
            .and_then(|v| v.as_str())
            .unwrap_or(&url)
            .to_string(),
        supported: value
            .get("supported")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        status: value
            .get("status")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        message: value.get("message").and_then(|v| {
            if v.is_null() {
                None
            } else {
                v.as_str().map(str::to_string)
            }
        }),
    })
}

#[tauri::command]
async fn probe_url(app: AppHandle, url: String) -> Result<ProbeResult, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["probe".into(), url.clone()];
    append_proxy_ua_args(&mut args, &settings);
    append_gallerydl_args(&mut args, &settings);
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl probe exited {:?}: {stderr}", code));
    }
    let value: serde_json::Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse probe JSON: {e}; raw={stdout}; err={stderr}"))?;
    Ok(probe_result_from_value(value, &url))
}

fn probe_result_from_value(value: serde_json::Value, fallback_url: &str) -> ProbeResult {
    ProbeResult {
        site: value
            .get("site")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        kind: value
            .get("kind")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        title: value
            .get("title")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        url: value
            .get("url")
            .and_then(|v| v.as_str())
            .unwrap_or(fallback_url)
            .to_string(),
        parts: value
            .get("parts")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        qualities: value
            .get("qualities")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        codecs: value
            .get("codecs")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        audio_qualities: value
            .get("audio_qualities")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        subtitle_tracks: value
            .get("subtitle_tracks")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        default_part: value.get("default_part").and_then(|v| v.as_i64()),
        default_quality: value.get("default_quality").and_then(|v| v.as_i64()),
        message: value.get("message").and_then(|v| {
            if v.is_null() {
                None
            } else {
                v.as_str().map(str::to_string)
            }
        }),
        pagination: value.get("pagination").cloned().filter(|v| !v.is_null()),
        naming_template: value
            .get("naming_template")
            .and_then(|v| v.as_str())
            .map(str::to_string),
    }
}

#[tauri::command]
async fn probe_bilibili(
    app: AppHandle,
    urls: Vec<String>,
    page: Option<u32>,
    keyword: Option<String>,
    expand_all: Option<bool>,
    filter_title: Option<String>,
    filter_duration_min: Option<f64>,
    filter_duration_max: Option<f64>,
    part_range: Option<String>,
) -> Result<serde_json::Value, String> {
    let expand = expand_all.unwrap_or(false);
    let mut args = vec!["probe".into()];
    for u in &urls {
        let trimmed = u.trim();
        if !trimmed.is_empty() {
            args.push(trimmed.to_string());
        }
    }
    if args.len() == 1 {
        return Err("no urls".into());
    }
    if expand {
        // JSON-lines so expand progress events can stream to the GUI
        args.push("--json".into());
        args.push("--expand-all".into());
    }
    if let Some(p) = page {
        args.push("--page".into());
        args.push(p.max(1).to_string());
    }
    if let Some(kw) = keyword {
        let kw = kw.trim();
        if !kw.is_empty() {
            args.push("--keyword".into());
            args.push(kw.to_string());
        }
    }
    if let Some(ft) = filter_title {
        let ft = ft.trim();
        if !ft.is_empty() {
            args.push("--filter-title".into());
            args.push(ft.to_string());
        }
    }
    if let Some(dmin) = filter_duration_min {
        args.push("--filter-duration-min".into());
        args.push(dmin.to_string());
    }
    if let Some(dmax) = filter_duration_max {
        args.push("--filter-duration-max".into());
        args.push(dmax.to_string());
    }
    if let Some(pr) = part_range {
        let pr = pr.trim();
        if !pr.is_empty() {
            args.push("--part-range".into());
            args.push(pr.to_string());
        }
    }

    if !expand {
        let (mut rx, _child) = spawn_udl(&app, args)?;
        let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
        if code.unwrap_or(1) != 0 {
            return Err(format!("udl probe exited {:?}: {stderr}", code));
        }
        return serde_json::from_str(stdout.trim())
            .map_err(|e| format!("parse probe JSON: {e}; raw={stdout}; err={stderr}"));
    }

    let (mut rx, _child) = spawn_udl(&app, args)?;
    let mut probe_payload: Option<serde_json::Value> = None;
    let mut stderr = String::new();
    let mut code: Option<i32> = None;
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line_bytes) => {
                let text = String::from_utf8_lossy(&line_bytes);
                for raw in text.split('\n') {
                    let raw = raw.trim();
                    if raw.is_empty() {
                        continue;
                    }
                    if let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) {
                        let ev = value.get("ev").and_then(|v| v.as_str()).unwrap_or("");
                        if ev == "expand" {
                            let _ = app.emit("bili-expand", value);
                        } else if ev == "probe" {
                            let mut cleaned = value;
                            if let Some(obj) = cleaned.as_object_mut() {
                                obj.remove("ev");
                            }
                            probe_payload = Some(cleaned);
                        } else if ev == "error" {
                            let msg = value
                                .get("message")
                                .and_then(|v| v.as_str())
                                .unwrap_or("probe failed");
                            return Err(msg.to_string());
                        }
                    }
                }
            }
            CommandEvent::Stderr(line_bytes) => {
                stderr.push_str(&String::from_utf8_lossy(&line_bytes));
            }
            CommandEvent::Terminated(payload) => {
                code = payload.code;
                break;
            }
            CommandEvent::Error(err) => return Err(err),
            _ => {}
        }
    }
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl probe exited {:?}: {stderr}", code));
    }
    probe_payload.ok_or_else(|| format!("probe expand missing payload; err={stderr}"))
}

#[tauri::command]
async fn gallerydl_status(app: AppHandle) -> Result<serde_json::Value, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["gallerydl".into(), "status".into(), "--json".into()];
    let tool = settings.gallerydl_tool_path.trim();
    if !tool.is_empty() {
        args.push("--gallerydl-tool".into());
        args.push(tool.to_string());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn gallerydl_sites(
    app: AppHandle,
    extractors: Option<bool>,
) -> Result<serde_json::Value, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["gallerydl".into(), "sites".into(), "--json".into()];
    if extractors.unwrap_or(false) {
        args.push("--extractors".into());
    }
    let tool = settings.gallerydl_tool_path.trim();
    if !tool.is_empty() {
        args.push("--gallerydl-tool".into());
        args.push(tool.to_string());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn ffmpeg_status(app: AppHandle) -> Result<FfmpegStatus, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["ffmpeg".into(), "status".into(), "--json".into()];
    if !settings.ffmpeg_path.trim().is_empty() {
        args.push("--ffmpeg".into());
        args.push(settings.ffmpeg_path.trim().to_string());
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, _code) = collect_stdout_json(&mut rx).await?;
    let value: serde_json::Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse ffmpeg status: {e}; raw={stdout}; err={stderr}"))?;
    Ok(FfmpegStatus {
        found: value
            .get("found")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        path: value
            .get("path")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        source: value
            .get("source")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        platform: value
            .get("platform")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string(),
        provision_supported: value
            .get("provision_supported")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        message: value
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
    })
}

#[tauri::command]
async fn install_ffmpeg(app: AppHandle) -> Result<FfmpegStatus, String> {
    let dir = ffmpeg_install_dir(&app)?;
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let args = vec![
        "ffmpeg".into(),
        "install".into(),
        "--json".into(),
        "--dir".into(),
        dir.to_string_lossy().into_owned(),
    ];
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    let value: serde_json::Value = serde_json::from_str(stdout.trim()).map_err(|e| {
        format!("parse ffmpeg install: {e}; raw={stdout}; err={stderr}; code={code:?}")
    })?;
    if !value.get("ok").and_then(|v| v.as_bool()).unwrap_or(false) {
        return Err(value
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("FFmpeg 安裝失敗")
            .to_string());
    }
    // persist discovered path into settings when empty
    let mut settings = load_settings_file(&app);
    if settings.ffmpeg_path.trim().is_empty() {
        if let Some(path) = value.get("path").and_then(|v| v.as_str()) {
            settings.ffmpeg_path = path.to_string();
            let _ = save_settings(app.clone(), settings);
        }
    }
    ffmpeg_status(app).await
}

#[tauri::command]
async fn start_download(
    app: AppHandle,
    state: State<'_, JobState>,
    url: String,
    output: String,
    quality: Option<String>,
    part: Option<String>,
    cloudflare_bypass: Option<bool>,
    workers: Option<u32>,
    ffmpeg: Option<String>,
    on_duplicate: Option<String>,
    job_id: Option<String>,
    codec: Option<String>,
    audio_quality: Option<String>,
    container: Option<String>,
    danmaku: Option<String>,
    subtitle: Option<String>,
    subtitle_lang: Option<String>,
    embed_danmaku: Option<bool>,
    embed_subtitle: Option<bool>,
    filter_title: Option<String>,
    part_range: Option<String>,
    expand_all: Option<bool>,
    node_names: Option<String>,
    mega_select: Option<String>,
    speed_limit: Option<String>,
    auto_extract_override: Option<String>,
    extract_password: Option<String>,
    extract_password_mode: Option<String>,
) -> Result<String, String> {
    let settings = load_settings_file(&app);
    let job_id = job_id
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| Uuid::new_v4().to_string());
    let mut args = vec![
        "get".into(),
        url,
        "-o".into(),
        output,
        "--json".into(),
        "--job-id".into(),
        job_id.clone(),
        "--workers".into(),
        workers.unwrap_or(settings.workers).max(1).to_string(),
    ];
    let dup = on_duplicate
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(settings.on_duplicate.trim());
    let dup = match dup {
        "overwrite" | "rename" | "skip" => dup,
        _ => "skip",
    };
    args.push("--on-duplicate".into());
    args.push(dup.to_string());
    if let Some(q) = quality {
        let q = q.trim();
        if !q.is_empty() {
            args.push("--quality".into());
            args.push(q.to_string());
        }
    }
    if let Some(p) = part {
        let p = p.trim();
        if !p.is_empty() {
            args.push("--part".into());
            args.push(p.to_string());
        }
    }
    if let Some(c) = codec {
        let c = c.trim();
        if !c.is_empty() {
            args.push("--codec".into());
            args.push(c.to_string());
        }
    }
    if let Some(aq) = audio_quality {
        let aq = aq.trim();
        if !aq.is_empty() {
            args.push("--audio-quality".into());
            args.push(aq.to_string());
        }
    }
    if let Some(cont) = container {
        let cont = cont.trim();
        if cont == "mp4" || cont == "mkv" {
            args.push("--container".into());
            args.push(cont.to_string());
        }
    }
    if let Some(d) = danmaku {
        let d = d.trim();
        if !d.is_empty() {
            args.push("--danmaku".into());
            args.push(d.to_string());
        }
    }
    if let Some(s) = subtitle {
        let s = s.trim();
        if !s.is_empty() {
            args.push("--subtitle".into());
            args.push(s.to_string());
        }
    }
    if let Some(sl) = subtitle_lang {
        let sl = sl.trim();
        if !sl.is_empty() {
            args.push("--subtitle-lang".into());
            args.push(sl.to_string());
        }
    }
    if embed_danmaku.unwrap_or(false) {
        args.push("--embed-danmaku".into());
    }
    if embed_subtitle.unwrap_or(false) {
        args.push("--embed-subtitle".into());
    }
    if let Some(ft) = filter_title {
        let ft = ft.trim();
        if !ft.is_empty() {
            args.push("--filter-title".into());
            args.push(ft.to_string());
        }
    }
    if let Some(pr) = part_range {
        let pr = pr.trim();
        if !pr.is_empty() {
            args.push("--part-range".into());
            args.push(pr.to_string());
        }
    }
    if expand_all.unwrap_or(false) {
        args.push("--expand-all".into());
    }
    if let Some(nn) = node_names {
        let nn = nn.trim();
        if !nn.is_empty() {
            args.push("--node-names".into());
            args.push(nn.to_string());
        }
    }
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
    let mega_backend = settings.mega_backend.trim();
    if mega_backend == "external" || mega_backend == "builtin" {
        args.push("--mega-backend".into());
        args.push(mega_backend.to_string());
    }
    let mega_tool = settings.mega_tool_path.trim();
    if !mega_tool.is_empty() {
        args.push("--mega-tool".into());
        args.push(mega_tool.to_string());
    }
    if let Some(sel) = mega_select {
        let sel = sel.trim();
        if !sel.is_empty() {
            args.push("--mega-select".into());
            args.push(sel.to_string());
        }
    }
    append_gallerydl_args(&mut args, &settings);
    let ffmpeg_path = ffmpeg
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .or_else(|| {
            let trimmed = settings.ffmpeg_path.trim();
            if trimmed.is_empty() {
                None
            } else {
                Some(trimmed)
            }
        });
    if let Some(path) = ffmpeg_path {
        args.push("--ffmpeg".into());
        args.push(path.to_string());
    }
    if settings.write_nfo {
        args.push("--write-nfo".into());
        args.push("--nfo-sites".into());
        args.push(sites_cli_list(&settings.nfo_sites));
    }
    let write_cover = settings.write_cover || settings.embed_cover;
    if write_cover {
        args.push("--write-cover".into());
        args.push("--cover-sites".into());
        args.push(sites_cli_list(&settings.cover_sites));
        let naming = match settings.cover_naming.trim() {
            "folder" => "folder",
            _ => "poster",
        };
        args.push("--cover-name".into());
        args.push(naming.into());
    }
    if settings.embed_cover {
        args.push("--embed-cover".into());
    }
    let name_tpl = settings.name_template.trim();
    if !name_tpl.is_empty() {
        args.push("--name-template".into());
        args.push(name_tpl.to_string());
    }
    if !settings.checksum_auto {
        args.push("--no-checksum-auto".into());
    }
    if settings.av_hook_enabled {
        let preset = settings.av_hook_preset.trim().to_lowercase();
        if preset == "defender" || preset == "clamav" || preset == "windows_defender" || preset == "clamscan"
        {
            args.push("--post-cmd-preset".into());
            args.push(preset);
        }
        let cmd = settings.av_hook_command.trim();
        if !cmd.is_empty() {
            args.push("--post-cmd".into());
            args.push(cmd.to_string());
        }
        if settings.av_hook_timeout > 0 {
            args.push("--post-cmd-timeout".into());
            args.push(settings.av_hook_timeout.to_string());
        }
    }
    let extract_override = auto_extract_override
        .as_deref()
        .map(str::trim)
        .unwrap_or("inherit");
    let extract_on = match extract_override {
        "on" => true,
        "off" => false,
        _ => settings.auto_extract_zip,
    };
    if extract_on {
        args.push("--auto-extract".into());
    } else {
        args.push("--no-auto-extract".into());
    }
    let extract_to = settings.extract_to.trim();
    if extract_to == "same_folder" || extract_to == "subfolder" {
        args.push("--extract-to".into());
        args.push(extract_to.to_string());
    }
    if settings.extract_delete_zip {
        args.push("--extract-delete-zip".into());
    }
    if settings.extract_max_uncompressed > 0 {
        args.push("--extract-max-uncompressed".into());
        args.push(settings.extract_max_uncompressed.to_string());
    }
    if settings.extract_max_ratio > 0.0 {
        args.push("--extract-max-ratio".into());
        args.push(settings.extract_max_ratio.to_string());
    }
    if !settings.extract_promote_password {
        args.push("--no-extract-promote-password".into());
    }
    let pw_mode = extract_password_mode
        .as_deref()
        .map(str::trim)
        .unwrap_or("list");
    let pw_mode = match pw_mode {
        "single" | "one" => "single",
        "off" | "none" => "off",
        _ => "list",
    };
    args.push("--extract-password-mode".into());
    args.push(pw_mode.into());
    if let Some(pw) = extract_password {
        let pw = pw.trim();
        if !pw.is_empty() {
            args.push("--extract-password".into());
            args.push(pw.to_string());
        }
    }
    if let Ok(cfg_path) = naming_config_path(&app) {
        if cfg_path.is_file() {
            args.push("--config".into());
            args.push(cfg_path.to_string_lossy().into_owned());
        }
    }
    append_network_args(&mut args, &settings, speed_limit.as_deref());
    let mega = settings.mega_tool_path.trim();
    if !mega.is_empty() {
        args.push("--mega-tool".into());
        args.push(mega.to_string());
    }
    let (mut rx, child) = spawn_udl(&app, args)?;

    state
        .children
        .lock()
        .map_err(|e| e.to_string())?
        .insert(job_id.clone(), child);

    let app_handle = app.clone();
    let jid = job_id.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line_bytes) => {
                    let text = String::from_utf8_lossy(&line_bytes);
                    for raw in text.split('\n') {
                        let raw = raw.trim();
                        if raw.is_empty() {
                            continue;
                        }
                        if let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) {
                            let _ = app_handle.emit(
                                "udl-event",
                                UdlEventPayload {
                                    job_id: jid.clone(),
                                    line: value,
                                },
                            );
                        }
                    }
                }
                CommandEvent::Stderr(line_bytes) => {
                    let text = String::from_utf8_lossy(&line_bytes);
                    let _ = app_handle.emit(
                        "udl-stderr",
                        serde_json::json!({ "job_id": jid, "message": text }),
                    );
                }
                CommandEvent::Terminated(payload) => {
                    let _ = app_handle.emit(
                        "udl-exit",
                        serde_json::json!({
                            "job_id": jid,
                            "code": payload.code,
                        }),
                    );
                    if let Some(jobs) = app_handle.try_state::<JobState>() {
                        if let Ok(mut map) = jobs.children.lock() {
                            map.remove(&jid);
                        }
                    }
                    break;
                }
                CommandEvent::Error(err) => {
                    let _ = app_handle.emit(
                        "udl-stderr",
                        serde_json::json!({ "job_id": jid, "message": err }),
                    );
                }
                _ => {}
            }
        }
    });

    Ok(job_id)
}

#[tauri::command]
fn cancel_download(state: State<'_, JobState>, job_id: String) -> Result<(), String> {
    let mut map = state.children.lock().map_err(|e| e.to_string())?;
    if let Some(child) = map.remove(&job_id) {
        child.kill().map_err(|e| format!("kill job: {e}"))?;
    }
    Ok(())
}

#[tauri::command]
fn open_path(app: AppHandle, path: String) -> Result<(), String> {
    tauri_plugin_opener::OpenerExt::opener(&app)
        .open_path(path, None::<&str>)
        .map_err(|e| e.to_string())
}

fn encode_base64(data: &[u8]) -> String {
    const table: &[u8] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((data.len() + 2) / 3 * 4);
    let mut i = 0;
    while i + 3 <= data.len() {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8) | (data[i + 2] as u32);
        out.push(table[((n >> 18) & 63) as usize] as char);
        out.push(table[((n >> 12) & 63) as usize] as char);
        out.push(table[((n >> 6) & 63) as usize] as char);
        out.push(table[(n & 63) as usize] as char);
        i += 3;
    }
    let rem = data.len() - i;
    if rem == 1 {
        let n = (data[i] as u32) << 16;
        out.push(table[((n >> 18) & 63) as usize] as char);
        out.push(table[((n >> 12) & 63) as usize] as char);
        out.push('=');
        out.push('=');
    } else if rem == 2 {
        let n = ((data[i] as u32) << 16) | ((data[i + 1] as u32) << 8);
        out.push(table[((n >> 18) & 63) as usize] as char);
        out.push(table[((n >> 12) & 63) as usize] as char);
        out.push(table[((n >> 6) & 63) as usize] as char);
        out.push('=');
    }
    out
}

#[tauri::command]
fn read_local_image(path: String) -> Result<String, String> {
    let path_buf = PathBuf::from(&path);
    let ext = path_buf
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let mime = match ext.as_str() {
        "jpg" | "jpeg" => "image/jpeg",
        "png" => "image/png",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "bmp" => "image/bmp",
        _ => return Err(format!("unsupported image type: {ext}")),
    };
    if !path_buf.is_file() {
        return Err(format!("image not found: {path}"));
    }
    let meta = fs::metadata(&path_buf).map_err(|e| e.to_string())?;
    if meta.len() > 8 * 1024 * 1024 {
        return Err("image too large (>8MB)".into());
    }
    let buf = fs::read(&path_buf).map_err(|e| e.to_string())?;
    Ok(format!("data:{mime};base64,{}", encode_base64(&buf)))
}

#[tauri::command]
async fn history_list(
    app: AppHandle,
    site: Option<String>,
    status: Option<String>,
    tag: Option<String>,
    query: Option<String>,
    date_from: Option<String>,
    date_to: Option<String>,
    sort: Option<String>,
    order: Option<String>,
    limit: Option<u32>,
) -> Result<serde_json::Value, String> {
    let mut args = vec!["history".into(), "list".into(), "--json".into()];
    if let Some(v) = site.filter(|s| !s.trim().is_empty()) {
        args.push("--site".into());
        args.push(v);
    }
    if let Some(v) = status.filter(|s| !s.trim().is_empty()) {
        args.push("--status".into());
        args.push(v);
    }
    if let Some(v) = tag.filter(|s| !s.trim().is_empty()) {
        args.push("--tag".into());
        args.push(v);
    }
    if let Some(v) = query.filter(|s| !s.trim().is_empty()) {
        args.push("--query".into());
        args.push(v);
    }
    if let Some(v) = date_from.filter(|s| !s.trim().is_empty()) {
        args.push("--from".into());
        args.push(v);
    }
    if let Some(v) = date_to.filter(|s| !s.trim().is_empty()) {
        args.push("--to".into());
        args.push(v);
    }
    if let Some(v) = sort.filter(|s| !s.trim().is_empty()) {
        args.push("--sort".into());
        args.push(v);
    }
    if let Some(v) = order.filter(|s| !s.trim().is_empty()) {
        args.push("--order".into());
        args.push(v);
    }
    args.push("--limit".into());
    args.push(limit.unwrap_or(100).to_string());
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl history list exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse history JSON: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn history_delete(
    app: AppHandle,
    ids: Vec<String>,
    delete_files: Option<bool>,
) -> Result<serde_json::Value, String> {
    if ids.is_empty() {
        return Ok(serde_json::json!({"removed_entries": 0, "removed_files": [], "missing_files": []}));
    }
    let mut args = vec!["history".into(), "delete".into(), "--json".into()];
    if delete_files.unwrap_or(false) {
        args.push("--delete-files".into());
    }
    args.extend(ids);
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl history delete exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse history delete JSON: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn queue_list(
    app: AppHandle,
    unfinished: Option<bool>,
    status: Option<String>,
    queue_name: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec!["queue".into(), "list".into(), "--json".into()];
    if unfinished.unwrap_or(false) {
        args.push("--unfinished".into());
    }
    if let Some(v) = status.filter(|s| !s.trim().is_empty()) {
        args.push("--status".into());
        args.push(v);
    }
    if let Some(v) = queue_name.filter(|s| !s.trim().is_empty()) {
        args.push("--queue".into());
        args.push(v);
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl queue list exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse queue JSON: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn schedule_status(
    app: AppHandle,
    speed_limit: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "queue".into(),
        "schedule".into(),
        "status".into(),
        "--json".into(),
    ];
    if let Some(v) = speed_limit.filter(|s| !s.trim().is_empty()) {
        args.push("--speed-limit".into());
        args.push(v);
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn schedule_list(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "queue".into(),
            "schedule".into(),
            "list".into(),
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn schedule_set(
    app: AppHandle,
    name: String,
    enabled: Option<bool>,
    max_concurrent: Option<u32>,
    days: Option<String>,
    start: Option<String>,
    end: Option<String>,
    always: Option<bool>,
    repeat_mode: Option<String>,
    after: Option<String>,
    speed_profile: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "queue".into(),
        "schedule".into(),
        "set".into(),
        name,
        "--json".into(),
    ];
    if let Some(v) = enabled {
        args.push(if v {
            "--enabled".into()
        } else {
            "--no-enabled".into()
        });
    }
    if let Some(v) = max_concurrent {
        args.push("--max-concurrent".into());
        args.push(v.max(1).to_string());
    }
    if let Some(v) = days.filter(|s| !s.trim().is_empty()) {
        args.push("--days".into());
        args.push(v);
    }
    if always.unwrap_or(false) {
        args.push("--always".into());
    } else {
        if let Some(v) = start {
            args.push("--start".into());
            args.push(v);
        }
        if let Some(v) = end {
            args.push("--end".into());
            args.push(v);
        }
    }
    match repeat_mode.as_deref().map(str::trim) {
        Some("once") => args.push("--once".into()),
        Some("repeat") => args.push("--repeat".into()),
        _ => {}
    }
    if let Some(v) = after.filter(|s| !s.trim().is_empty()) {
        args.push("--after".into());
        args.push(v);
    }
    if let Some(v) = speed_profile {
        args.push("--speed-profile".into());
        args.push(v);
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn schedule_delete(app: AppHandle, name: String) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "queue".into(),
            "schedule".into(),
            "delete".into(),
            name,
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn schedule_reset_once(app: AppHandle, name: String) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "queue".into(),
            "schedule".into(),
            "reset-once".into(),
            name,
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn schedule_complete_once(app: AppHandle, name: String) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "queue".into(),
            "schedule".into(),
            "complete-once".into(),
            name,
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn schedule_profile_list(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "queue".into(),
            "schedule".into(),
            "profile".into(),
            "list".into(),
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn schedule_profile_set(
    app: AppHandle,
    name: String,
    default_limit: Option<String>,
    windows: Option<Vec<String>>,
    clear_windows: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "queue".into(),
        "schedule".into(),
        "profile".into(),
        "set".into(),
        name,
        "--json".into(),
    ];
    if let Some(v) = default_limit {
        args.push("--default-limit".into());
        args.push(v);
    }
    if clear_windows.unwrap_or(false) {
        args.push("--clear-windows".into());
    }
    for window in windows.unwrap_or_default() {
        let w = window.trim();
        if w.is_empty() {
            continue;
        }
        args.push("--window".into());
        args.push(w.to_string());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn schedule_profile_delete(
    app: AppHandle,
    name: String,
) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "queue".into(),
            "schedule".into(),
            "profile".into(),
            "delete".into(),
            name,
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn schedule_quota(
    app: AppHandle,
    enable: Option<bool>,
    hour_mb: Option<f64>,
    day_mb: Option<f64>,
    clear_pause: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "queue".into(),
        "schedule".into(),
        "quota".into(),
        "--json".into(),
    ];
    if let Some(v) = enable {
        args.push(if v {
            "--enable".into()
        } else {
            "--no-enable".into()
        });
    }
    if let Some(v) = hour_mb {
        args.push("--hour-mb".into());
        args.push(v.to_string());
    }
    if let Some(v) = day_mb {
        args.push("--day-mb".into());
        args.push(v.to_string());
    }
    if clear_pause.unwrap_or(false) {
        args.push("--clear-pause".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
fn system_power_action(action: String) -> Result<serde_json::Value, String> {
    // macOS + Windows primary; Linux best-effort for local/dev
    let action = action.trim().to_lowercase();
    let os = std::env::consts::OS;
    match action.as_str() {
        "notify" => Ok(serde_json::json!({
            "ok": true,
            "action": "notify",
            "message": "排程佇列已完成"
        })),
        "quit" => Ok(serde_json::json!({
            "ok": true,
            "action": "quit",
            "needs_frontend": true
        })),
        "sleep" => {
            let status = match os {
                "macos" => std::process::Command::new("pmset")
                    .arg("sleepnow")
                    .status(),
                "windows" => std::process::Command::new("powershell")
                    .args([
                        "-NoProfile",
                        "-Command",
                        "Add-Type -AssemblyName System.Windows.Forms; \
                         [System.Windows.Forms.Application]::SetSuspendState(\
                         'Suspend',$false,$false)",
                    ])
                    .status(),
                _ => std::process::Command::new("systemctl")
                    .args(["suspend"])
                    .status(),
            }
            .map_err(|e| format!("sleep failed: {e}"))?;
            if !status.success() {
                return Err(format!("sleep exited with {status}"));
            }
            Ok(serde_json::json!({"ok": true, "action": "sleep"}))
        }
        "shutdown" => {
            let status = match os {
                "macos" => std::process::Command::new("osascript")
                    .args(["-e", "tell application \"System Events\" to shut down"])
                    .status(),
                "windows" => std::process::Command::new("shutdown")
                    .args(["/s", "/t", "0"])
                    .status(),
                _ => std::process::Command::new("systemctl")
                    .args(["poweroff"])
                    .status(),
            }
            .map_err(|e| format!("shutdown failed: {e}"))?;
            if !status.success() {
                return Err(format!("shutdown exited with {status}"));
            }
            Ok(serde_json::json!({"ok": true, "action": "shutdown"}))
        }
        other => Err(format!("unsupported action: {other}")),
    }
}

#[tauri::command]
async fn naming_preview(
    app: AppHandle,
    template: String,
    metadata: Option<serde_json::Value>,
    name: Option<String>,
) -> Result<serde_json::Value, String> {
    let meta = metadata.unwrap_or_else(|| {
        serde_json::json!({
            "site": "iwara",
            "author": "作者",
            "title": "範例標題",
            "item_id": "abc123",
            "upload_date": "2026-03-15",
            "tags": ["demo", "tag"]
        })
    });
    let mut args = vec![
        "naming".into(),
        "preview".into(),
        "--json".into(),
        "--template".into(),
        template,
        "--metadata".into(),
        meta.to_string(),
    ];
    if let Some(n) = name.filter(|s| !s.trim().is_empty()) {
        args.push("--name".into());
        args.push(n);
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl naming preview exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse naming preview: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn rules_test(
    app: AppHandle,
    url: Option<String>,
    metadata: Option<serde_json::Value>,
) -> Result<serde_json::Value, String> {
    let cfg = naming_config_path(&app)?;
    let mut args = vec!["rules".into(), "test".into(), "--json".into()];
    if cfg.is_file() {
        args.push("--config".into());
        args.push(cfg.to_string_lossy().into_owned());
    }
    if let Some(u) = url.filter(|s| !s.trim().is_empty()) {
        args.push(u);
    } else if let Some(meta) = metadata {
        args.push("--metadata".into());
        args.push(meta.to_string());
    } else {
        return Err("請提供 URL 或 metadata".into());
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl rules test exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse rules test: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn repair_scan(
    app: AppHandle,
    verify_hash: Option<bool>,
    no_orphans: Option<bool>,
    dirs: Option<Vec<String>>,
) -> Result<serde_json::Value, String> {
    let mut args = vec!["repair".into(), "scan".into(), "--json".into()];
    if verify_hash.unwrap_or(false) {
        args.push("--verify-hash".into());
    }
    if no_orphans.unwrap_or(false) {
        args.push("--no-orphans".into());
    }
    if let Some(list) = dirs {
        for d in list {
            if !d.trim().is_empty() {
                args.push("--dir".into());
                args.push(d);
            }
        }
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl repair scan exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse repair scan: {e}; raw={stdout}; err={stderr}"))
}

#[tauri::command]
async fn repair_fix(
    app: AppHandle,
    action: String,
    job_ids: Option<Vec<String>>,
    file_row_ids: Option<Vec<i64>>,
    relink_path: Option<String>,
    output: Option<String>,
    issues: Option<serde_json::Value>,
    extract_password: Option<String>,
    add_password_to_list: Option<bool>,
    password_label: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "repair".into(),
        "fix".into(),
        action,
        "--json".into(),
    ];
    if let Some(ids) = job_ids {
        for id in ids {
            args.push("--job-id".into());
            args.push(id);
        }
    }
    if let Some(ids) = file_row_ids {
        for id in ids {
            args.push("--file-row-id".into());
            args.push(id.to_string());
        }
    }
    if let Some(path) = relink_path.filter(|s| !s.trim().is_empty()) {
        args.push("--relink-path".into());
        args.push(path);
    }
    if let Some(out) = output.filter(|s| !s.trim().is_empty()) {
        args.push("--output".into());
        args.push(out);
    }
    if let Some(iss) = issues {
        args.push("--issues".into());
        args.push(iss.to_string());
    }
    if let Some(pw) = extract_password.filter(|s| !s.trim().is_empty()) {
        args.push("--extract-password".into());
        args.push(pw);
    }
    if add_password_to_list.unwrap_or(false) {
        args.push("--add-password-to-list".into());
    }
    if let Some(label) = password_label.filter(|s| !s.trim().is_empty()) {
        args.push("--password-label".into());
        args.push(label);
    }
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl repair fix exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse repair fix: {e}; raw={stdout}; err={stderr}"))
}

async fn run_udl_json(app: &AppHandle, args: Vec<String>) -> Result<serde_json::Value, String> {
    let (mut rx, _child) = spawn_udl(app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    // browse commands may return exit 1 with JSON error payload (e.g. CF 403)
    let trimmed = stdout.trim();
    if trimmed.is_empty() {
        return Err(format!("udl exited {:?}: {stderr}", code));
    }
    serde_json::from_str(trimmed).map_err(|e| {
        format!("parse udl JSON: {e}; raw={stdout}; err={stderr}; code={code:?}")
    })
}

fn push_opt(args: &mut Vec<String>, flag: &str, value: Option<String>) {
    if let Some(v) = value.filter(|s| !s.trim().is_empty()) {
        args.push(flag.into());
        args.push(v);
    }
}

#[tauri::command]
async fn iwara_browse(
    app: AppHandle,
    action: String,
    query: Option<String>,
    page: Option<u32>,
    limit: Option<u32>,
    sort: Option<String>,
    rating: Option<String>,
    cloudflare_bypass: Option<bool>,
    min_likes: Option<i64>,
    min_views: Option<i64>,
    exclude_tags: Option<String>,
    title: Option<String>,
) -> Result<serde_json::Value, String> {
    let action = action.trim().to_lowercase();
    let q = query.unwrap_or_default();
    let mut args = vec!["iwara".into(), action.clone(), "--json".into()];
    match action.as_str() {
        "home" => {}
        "search" | "tag" | "author" | "playlist" => {
            if q.trim().is_empty() && action != "home" {
                return Err("query required".into());
            }
            args.push(q);
        }
        _ => return Err(format!("unsupported iwara browse action: {action}")),
    }
    args.push("--page".into());
    args.push(page.unwrap_or(0).to_string());
    args.push("--limit".into());
    args.push(limit.unwrap_or(32).to_string());
    push_opt(&mut args, "--sort", sort);
    push_opt(&mut args, "--rating", rating);
    push_opt(&mut args, "--exclude-tags", exclude_tags);
    push_opt(&mut args, "--title", title);
    if let Some(v) = min_likes {
        args.push("--min-likes".into());
        args.push(v.to_string());
    }
    if let Some(v) = min_views {
        args.push("--min-views".into());
        args.push(v.to_string());
    }
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_preview(
    app: AppHandle,
    video_id: String,
    cloudflare_bypass: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "iwara".into(),
        "preview".into(),
        video_id,
        "--json".into(),
    ];
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_enqueue(
    app: AppHandle,
    video_ids: Vec<String>,
    output: String,
    cloudflare_bypass: Option<bool>,
    quality: Option<String>,
) -> Result<serde_json::Value, String> {
    if video_ids.is_empty() {
        return Ok(serde_json::json!({"enqueued": 0, "skipped": 0, "jobs": []}));
    }
    let settings = load_settings_file(&app);
    let mut args = vec![
        "iwara".into(),
        "enqueue".into(),
        "--json".into(),
        "-o".into(),
        output,
    ];
    let q = quality
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| settings.iwara_quality.clone());
    if !q.trim().is_empty() {
        args.push("--quality".into());
        args.push(q);
    }
    if cloudflare_bypass.unwrap_or(settings.iwara_cloudflare_bypass) {
        args.push("--cloudflare-bypass".into());
    }
    args.extend(video_ids);
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_subscribe(
    app: AppHandle,
    target: String,
    kind: Option<String>,
    title: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "iwara".into(),
        "subscribe".into(),
        target,
        "--json".into(),
    ];
    push_opt(&mut args, "--kind", kind);
    push_opt(&mut args, "--title", title);
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_unsubscribe(app: AppHandle, id: i64) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "iwara".into(),
            "unsubscribe".into(),
            id.to_string(),
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn iwara_subscriptions(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec!["iwara".into(), "subscriptions".into(), "--json".into()],
    )
    .await
}

#[tauri::command]
async fn iwara_sync(
    app: AppHandle,
    output: String,
    cloudflare_bypass: Option<bool>,
    max_pages: Option<u32>,
    quality: Option<String>,
) -> Result<serde_json::Value, String> {
    let settings = load_settings_file(&app);
    let mut args = vec![
        "iwara".into(),
        "sync".into(),
        "--json".into(),
        "-o".into(),
        output,
        "--max-pages".into(),
        max_pages.unwrap_or(5).to_string(),
    ];
    let q = quality
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| settings.iwara_quality.clone());
    if !q.trim().is_empty() {
        args.push("--quality".into());
        args.push(q);
    }
    if cloudflare_bypass.unwrap_or(settings.iwara_cloudflare_bypass) {
        args.push("--cloudflare-bypass".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_new_items(
    app: AppHandle,
    source_id: Option<i64>,
    limit: Option<u32>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "iwara".into(),
        "new".into(),
        "--json".into(),
        "--limit".into(),
        limit.unwrap_or(100).to_string(),
    ];
    if let Some(id) = source_id {
        args.push("--source-id".into());
        args.push(id.to_string());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_mark_seen(
    app: AppHandle,
    source_id: Option<i64>,
) -> Result<serde_json::Value, String> {
    let mut args = vec!["iwara".into(), "new".into(), "--json".into(), "--mark-seen".into()];
    if let Some(id) = source_id {
        args.push("--source-id".into());
        args.push(id.to_string());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_sections(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(&app, vec!["iwara".into(), "sections".into(), "--json".into()]).await
}

#[tauri::command]
async fn iwara_section_add(
    app: AppHandle,
    name: String,
    kind: String,
    value: Option<String>,
    sort: Option<String>,
    rating: Option<String>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "iwara".into(),
        "section-add".into(),
        name,
        "--kind".into(),
        kind,
        "--json".into(),
    ];
    push_opt(&mut args, "--value", value);
    push_opt(&mut args, "--sort", sort);
    push_opt(&mut args, "--rating", rating);
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_section_remove(app: AppHandle, id: i64) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "iwara".into(),
            "section-remove".into(),
            id.to_string(),
            "--json".into(),
        ],
    )
    .await
}

#[tauri::command]
async fn iwara_section_fetch(
    app: AppHandle,
    id: i64,
    page: Option<u32>,
    limit: Option<u32>,
    cloudflare_bypass: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "iwara".into(),
        "section-fetch".into(),
        id.to_string(),
        "--json".into(),
        "--page".into(),
        page.unwrap_or(0).to_string(),
        "--limit".into(),
        limit.unwrap_or(32).to_string(),
    ];
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn mega_browse(app: AppHandle, url: Option<String>) -> Result<serde_json::Value, String> {
    let settings = load_settings_file(&app);
    let mut args = vec!["mega".into(), "browse".into(), "--json".into()];
    if let Some(u) = url {
        let u = u.trim();
        if !u.is_empty() {
            args.push(u.to_string());
        }
    }
    append_network_args(&mut args, &settings, None);
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn mega_login(
    app: AppHandle,
    email: String,
    password: String,
) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec![
            "mega".into(),
            "login".into(),
            "--json".into(),
            "--email".into(),
            email,
            "--password".into(),
            password,
        ],
    )
    .await
}

#[tauri::command]
async fn mega_logout(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec!["mega".into(), "logout".into(), "--json".into()],
    )
    .await
}

#[tauri::command]
async fn mega_whoami(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec!["mega".into(), "whoami".into(), "--json".into()],
    )
    .await
}

#[tauri::command]
async fn mega_stream(
    app: AppHandle,
    state: State<'_, JobState>,
    url: String,
) -> Result<serde_json::Value, String> {
    // keep sidecar alive after returning URL so localhost decrypt stream stays up
    let mut args = vec![
        "mega".into(),
        "stream".into(),
        url,
        "--json".into(),
        "--wait".into(),
    ];
    append_network_args(&mut args, &load_settings_file(&app), None);
    let (mut rx, child) = spawn_udl(&app, args)?;
    let stream_job = format!("mega-stream-{}", Uuid::new_v4());
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line_bytes) => {
                let text = String::from_utf8_lossy(&line_bytes);
                for raw in text.split('\n') {
                    let raw = raw.trim();
                    if raw.is_empty() {
                        continue;
                    }
                    if let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) {
                        state
                            .children
                            .lock()
                            .map_err(|e| e.to_string())?
                            .insert(stream_job.clone(), child);
                        let mut out = value;
                        if let Some(obj) = out.as_object_mut() {
                            obj.insert("job_id".into(), serde_json::json!(stream_job));
                        }
                        return Ok(out);
                    }
                }
            }
            CommandEvent::Terminated(_) => {
                return Err("MEGA stream process exited early".into());
            }
            _ => {}
        }
    }
    Err("MEGA stream produced no output".into())
}

#[tauri::command]
async fn iwara_login(
    app: AppHandle,
    email: String,
    password: String,
    cloudflare_bypass: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mut args = vec![
        "iwara".into(),
        "login".into(),
        "--json".into(),
        "--email".into(),
        email,
        "--password".into(),
        password,
    ];
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_logout(app: AppHandle) -> Result<serde_json::Value, String> {
    run_udl_json(
        &app,
        vec!["iwara".into(), "logout".into(), "--json".into()],
    )
    .await
}

#[tauri::command]
async fn iwara_whoami(
    app: AppHandle,
    cloudflare_bypass: Option<bool>,
) -> Result<serde_json::Value, String> {
    let mut args = vec!["iwara".into(), "whoami".into(), "--json".into()];
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
    run_udl_json(&app, args).await
}

#[tauri::command]
async fn iwara_import_following(
    app: AppHandle,
    cloudflare_bypass: Option<bool>,
    max_pages: Option<u32>,
    sync: Option<bool>,
    output: Option<String>,
) -> Result<serde_json::Value, String> {
    let settings = load_settings_file(&app);
    let mut args = vec![
        "iwara".into(),
        "import-following".into(),
        "--json".into(),
        "--max-pages".into(),
        max_pages.unwrap_or(100).to_string(),
    ];
    if cloudflare_bypass.unwrap_or(settings.iwara_cloudflare_bypass) {
        args.push("--cloudflare-bypass".into());
    }
    if sync.unwrap_or(false) {
        args.push("--sync".into());
        let out = output
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| settings.download_dir.clone());
        if out.trim().is_empty() {
            return Err("請先設定下載目錄再同步".into());
        }
        args.push("-o".into());
        args.push(out);
    }
    run_udl_json(&app, args).await
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdaterInfo {
    enabled: bool,
    reason: String,
    pubkey_present: bool,
}

fn updater_pubkey() -> String {
    std::env::var("TAURI_UPDATER_PUBKEY")
        .ok()
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .or_else(|| {
            // optional baked key from tauri.conf.json plugins.updater.pubkey
            None
        })
        .unwrap_or_default()
}

#[tauri::command]
fn updater_status() -> UpdaterInfo {
    let key = updater_pubkey();
    let baked = option_env!("UDL_UPDATER_PUBKEY").unwrap_or("").trim();
    let pubkey_present = !key.is_empty() || !baked.is_empty();
    if pubkey_present {
        UpdaterInfo {
            enabled: true,
            reason: "已設定簽章公鑰，可檢查 GitHub Releases 更新".into(),
            pubkey_present: true,
        }
    } else {
        UpdaterInfo {
            enabled: false,
            reason: "未設定更新簽章公鑰（TAURI_UPDATER_PUBKEY／UDL_UPDATER_PUBKEY），自動更新已停用；請從 GitHub Releases 手動安裝。詳見 docs/updater.md".into(),
            pubkey_present: false,
        }
    }
}

#[tauri::command]
fn show_main_window(app: AppHandle) -> Result<(), String> {
    if let Some(win) = app.get_webview_window("main") {
        win.show().map_err(|e| e.to_string())?;
        win.set_focus().map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[tauri::command]
fn set_tray_tooltip(app: AppHandle, text: String) -> Result<(), String> {
    if let Some(tray) = app.tray_by_id("main") {
        tray.set_tooltip(Some(text)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn setup_tray(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    let show_i = MenuItem::with_id(app, "show", "顯示視窗", true, None::<&str>)?;
    let quit_i = MenuItem::with_id(app, "quit", "結束", true, None::<&str>)?;
    let sep = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&show_i, &sep, &quit_i])?;

    let _tray = TrayIconBuilder::with_id("main")
        .tooltip("整合下載器")
        .icon(app.default_window_icon().cloned().ok_or("no default icon")?)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                let _ = show_main_window(app.clone());
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let app = tray.app_handle();
                let _ = show_main_window(app.clone());
            }
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let updater_enabled = {
        let env_key = updater_pubkey();
        let baked = option_env!("UDL_UPDATER_PUBKEY").unwrap_or("").trim();
        !env_key.is_empty() || !baked.is_empty()
    };

    let mut builder = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .manage(JobState {
            children: Mutex::new(HashMap::new()),
        });

    if updater_enabled {
        builder = builder.plugin(tauri_plugin_updater::Builder::new().build());
    }

    builder
        .setup(|app| {
            if let Err(e) = setup_tray(app.handle()) {
                eprintln!("tray setup skipped: {e}");
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let settings = load_settings_file(window.app_handle());
                if settings.minimize_to_tray {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            set_proxy_password,
            clear_proxy_password,
            set_zip_password,
            clear_zip_password,
            zip_passwords_list,
            zip_passwords_add,
            zip_passwords_update,
            zip_passwords_remove,
            zip_passwords_reorder,
            get_app_paths,
            resolve_url,
            page_scan,
            probe_url,
            probe_bilibili,
            bili_login_status,
            bili_login_qr,
            bili_login_qr_poll,
            bili_login_cookie,
            bili_login_refresh,
            bili_login_logout,
            bili_login_sms_captcha,
            bili_login_sms_send,
            bili_login_sms,
            open_geetest_window,
            close_geetest_window,
            ffmpeg_status,
            install_ffmpeg,
            gallerydl_status,
            gallerydl_sites,
            start_download,
            cancel_download,
            open_path,
            read_local_image,
            history_list,
            history_delete,
            queue_list,
            schedule_status,
            schedule_list,
            schedule_set,
            schedule_delete,
            schedule_reset_once,
            schedule_complete_once,
            schedule_profile_list,
            schedule_profile_set,
            schedule_profile_delete,
            schedule_quota,
            system_power_action,
            naming_preview,
            rules_test,
            repair_scan,
            repair_fix,
            iwara_browse,
            iwara_preview,
            iwara_enqueue,
            iwara_subscribe,
            iwara_unsubscribe,
            iwara_subscriptions,
            iwara_sync,
            iwara_new_items,
            iwara_mark_seen,
            iwara_sections,
            iwara_section_add,
            iwara_section_remove,
            iwara_section_fetch,
            iwara_login,
            iwara_logout,
            iwara_whoami,
            iwara_import_following,
            mega_browse,
            mega_login,
            mega_logout,
            mega_whoami,
            mega_stream,
            updater_status,
            show_main_window,
            set_tray_tooltip
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
