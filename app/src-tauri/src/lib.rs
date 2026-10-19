//! Tauri shell for 整合下載器 — spawns the `udl` Python sidecar and forwards JSON-lines.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Mutex;

use serde::Serialize;
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

fn use_sidecar_binary() -> bool {
    // Prefer packaged sidecar unless UDL_DEV=1
    std::env::var("UDL_DEV").ok().as_deref() != Some("1")
        && std::env::var("UDL_FORCE_SIDECAR").ok().as_deref() != Some("0")
}

fn spawn_udl(
    app: &AppHandle,
    args: Vec<String>,
) -> Result<(tauri::async_runtime::Receiver<CommandEvent>, CommandChild), String> {
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

#[tauri::command]
async fn resolve_url(app: AppHandle, url: String) -> Result<ResolveResult, String> {
    let (mut rx, _child) = spawn_udl(&app, vec!["resolve".into(), url.clone()])?;
    let mut stdout = String::new();
    let mut stderr = String::new();
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
                if payload.code.unwrap_or(1) > 2 {
                    return Err(format!(
                        "udl resolve exited {:?}: {stderr}",
                        payload.code
                    ));
                }
                break;
            }
            _ => {}
        }
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
        message: value
            .get("message")
            .and_then(|v| {
                if v.is_null() {
                    None
                } else {
                    v.as_str().map(str::to_string)
                }
            }),
    })
}

#[tauri::command]
async fn start_download(
    app: AppHandle,
    state: State<'_, JobState>,
    url: String,
    output: String,
    quality: Option<String>,
    cloudflare_bypass: Option<bool>,
) -> Result<String, String> {
    let job_id = Uuid::new_v4().to_string();
    let mut args = vec![
        "get".into(),
        url,
        "-o".into(),
        output,
        "--json".into(),
        "--job-id".into(),
        job_id.clone(),
    ];
    if let Some(q) = quality {
        let q = q.trim();
        if !q.is_empty() {
            args.push("--quality".into());
            args.push(q.to_string());
        }
    }
    if cloudflare_bypass.unwrap_or(false) {
        args.push("--cloudflare-bypass".into());
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
            resolve_url,
            start_download,
            cancel_download,
            open_path
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
