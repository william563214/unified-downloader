//! Tauri shell for 整合下載器 — spawns the `udl` Python sidecar and forwards JSON-lines.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, State};
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
    default_part: Option<i64>,
    default_quality: Option<i64>,
    message: Option<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
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

fn default_site_flags() -> HashMap<String, bool> {
    let mut map = HashMap::new();
    for site in ["gofile", "bunkr", "iwara", "bilibili", "mega"] {
        map.insert(site.into(), true);
    }
    map
}

fn sites_cli_list(flags: &HashMap<String, bool>) -> String {
    let sites = ["gofile", "bunkr", "iwara", "bilibili", "mega"];
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

fn spawn_udl(
    app: &AppHandle,
    args: Vec<String>,
) -> Result<(tauri::async_runtime::Receiver<CommandEvent>, CommandChild), String> {
    // FFmpeg path is passed via --ffmpeg from start_download / ffmpeg_status
    // Managed install dir matches tauri app_data_dir/ffmpeg (Python app_data_name)
    let shell = app.shell();

    if use_sidecar_binary() {
        match shell.sidecar("udl") {
            Ok(cmd) => {
                return cmd
                    .args(args)
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
    shell
        .command("uv")
        .args(full_args)
        .spawn()
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
    let conflict = settings.name_conflict.trim();
    settings.name_conflict = match conflict {
        "overwrite" | "error" | "rename" => conflict.to_string(),
        _ => default_name_conflict(),
    };
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
fn get_app_paths(app: AppHandle) -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({
        "appData": app_data_dir(&app)?.to_string_lossy(),
        "ffmpegDir": ffmpeg_install_dir(&app)?.to_string_lossy(),
        "settings": settings_path(&app)?.to_string_lossy(),
    }))
}

#[tauri::command]
async fn resolve_url(app: AppHandle, url: String) -> Result<ResolveResult, String> {
    let (mut rx, _child) = spawn_udl(&app, vec!["resolve".into(), url.clone()])?;
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
    let (mut rx, _child) = spawn_udl(&app, vec!["probe".into(), url.clone()])?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl probe exited {:?}: {stderr}", code));
    }
    let value: serde_json::Value = serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse probe JSON: {e}; raw={stdout}; err={stderr}"))?;
    Ok(ProbeResult {
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
            .unwrap_or(&url)
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
        default_part: value.get("default_part").and_then(|v| v.as_i64()),
        default_quality: value.get("default_quality").and_then(|v| v.as_i64()),
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
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
    }
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
    if let Ok(cfg_path) = naming_config_path(&app) {
        if cfg_path.is_file() {
            args.push("--config".into());
            args.push(cfg_path.to_string_lossy().into_owned());
        }
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
) -> Result<serde_json::Value, String> {
    let mut args = vec!["queue".into(), "list".into(), "--json".into()];
    if unfinished.unwrap_or(false) {
        args.push("--unfinished".into());
    }
    if let Some(v) = status.filter(|s| !s.trim().is_empty()) {
        args.push("--status".into());
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
    let (mut rx, _child) = spawn_udl(&app, args)?;
    let (stdout, stderr, code) = collect_stdout_json(&mut rx).await?;
    if code.unwrap_or(1) != 0 {
        return Err(format!("udl repair fix exited {:?}: {stderr}", code));
    }
    serde_json::from_str(stdout.trim())
        .map_err(|e| format!("parse repair fix: {e}; raw={stdout}; err={stderr}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .manage(JobState {
            children: Mutex::new(HashMap::new()),
        })
        .invoke_handler(tauri::generate_handler![
            get_settings,
            save_settings,
            get_app_paths,
            resolve_url,
            probe_url,
            ffmpeg_status,
            install_ffmpeg,
            start_download,
            cancel_download,
            open_path,
            read_local_image,
            history_list,
            history_delete,
            queue_list,
            naming_preview,
            rules_test,
            repair_scan,
            repair_fix
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
