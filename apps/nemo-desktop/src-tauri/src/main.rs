// Tauri backend for Nemocode Desktop
// 
// Responsibilities:
// 1. Window management and lifecycle
// 2. Python backend process management (start/stop/monitor)
// 3. Settings persistence (platform-specific paths)
// 4. Deep linking and protocol handling

use std::sync::{Arc, Mutex};
use std::process::{Command, Child};
use std::thread;
use std::time::Duration;
use tauri::{generate_context, generate_handler};

const APP_DIR_NAME: &str = "nemocode";
const SETTINGS_FILE_NAME: &str = "settings.json";

/// Shared state for backend process management
struct BackendState {
    process: Option<Child>,
    port: u16,
    started_at: Option<std::time::Instant>,
}

/// Response for backend info
#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct BackendInfo {
    pid: u32,
    port: u16,
    status: String,
}

/// Response for health status
#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct HealthStatus {
    status: String,
    reachable: bool,
    message: String,
}

/// Settings for the Nemocode Desktop application
#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
pub struct AppSettings {
    backend_port: u16,
    lm_studio_url: String,
    nemo_database_path: String,
    auto_start_backend: bool,
    theme: String,
}

/// Start the Mission Control backend process
#[tauri::command]
async fn start_backend(state: tauri::State<'_, Arc<Mutex<BackendState>>>) -> Result<BackendInfo, String> {
    let mut backend = state.lock().map_err(|e| format!("Failed to acquire lock: {}", e))?;

    // Check if already running
    if let Some(proc) = &mut backend.process {
        if proc.try_wait().map_err(|e| format!("Failed to check process: {}", e))?.is_none() {
            return Ok(BackendInfo {
                pid: proc.id(),
                port: backend.port,
                status: "already_running".to_string(),
            });
        }
    }

    // Try to find Python executable
    let python_exe = find_python_executable()
        .ok_or_else(|| "Python not found in PATH or venv".to_string())?;

    // Build command to start Mission Control
    let mut cmd = Command::new(&python_exe);
    cmd.arg("-m")
        .arg("nemo_coding_platform.mission_control")
        .env("PYTHONUNBUFFERED", "1");

    // Start the process
    let mut child = cmd.spawn()
        .map_err(|e| format!("Failed to start backend: {}", e))?;

    let pid = child.id();
    backend.process = Some(child);
    backend.started_at = Some(std::time::Instant::now());

    // Wait for backend to become healthy (with timeout)
    for attempt in 0..20 {
        thread::sleep(Duration::from_millis(500));
        
        if is_backend_healthy(backend.port) {
            return Ok(BackendInfo {
                pid,
                port: backend.port,
                status: "running".to_string(),
            });
        }
        
        if attempt == 19 {
            return Err("Backend started but failed to become healthy within 10 seconds".to_string());
        }
    }

    Ok(BackendInfo {
        pid,
        port: backend.port,
        status: "starting".to_string(),
    })
}

/// Stop the Mission Control backend process
#[tauri::command]
async fn stop_backend(state: tauri::State<'_, Arc<Mutex<BackendState>>>) -> Result<(), String> {
    let mut backend = state.lock().map_err(|e| format!("Failed to acquire lock: {}", e))?;

    if let Some(mut proc) = backend.process.take() {
        // Try graceful termination first
        #[cfg(windows)]
        {
            // On Windows, use CTRL_C_EVENT via taskkill
            let pid = proc.id();
            let _ = Command::new("taskkill")
                .args(&["/PID", &pid.to_string(), "/F"])
                .output();
        }

        #[cfg(not(windows))]
        {
            let _ = proc.kill();
        }

        for _ in 0..50 {
            match proc.try_wait() {
                Ok(Some(_)) => return Ok(()),
                Ok(None) => thread::sleep(Duration::from_millis(100)),
                Err(error) => return Err(format!("Failed to wait for process: {}", error)),
            }
        }

        let _ = proc.kill();
        let _ = proc.wait();
    }

    Ok(())
}

/// Query the current backend status
#[tauri::command]
async fn query_backend_status(port: u16) -> Result<HealthStatus, String> {
    if is_backend_healthy(port) {
        Ok(HealthStatus {
            status: "healthy".to_string(),
            reachable: true,
            message: "Backend is running and healthy".to_string(),
        })
    } else {
        Ok(HealthStatus {
            status: "unreachable".to_string(),
            reachable: false,
            message: "Backend is not responding to health checks".to_string(),
        })
    }
}

/// Get application health status
/// Called by frontend to verify backend connectivity
#[tauri::command]
async fn health_check() -> Result<HealthStatus, String> {
    Ok(HealthStatus {
        status: "ok".to_string(),
        reachable: true,
        message: "Tauri backend is running".to_string(),
    })
}

/// Get current application settings
/// Reads from platform-specific settings directory
#[tauri::command]
async fn get_settings() -> Result<AppSettings, String> {
    let path = settings_file_path()?;
    if !path.exists() {
        return Ok(default_settings());
    }

    let raw = std::fs::read_to_string(&path)
        .map_err(|e| format!("Failed to read settings file {}: {}", path.display(), e))?;
    serde_json::from_str::<AppSettings>(&raw)
        .map_err(|e| format!("Failed to parse settings file {}: {}", path.display(), e))
}

/// Update application settings
/// Persists to platform-specific settings directory
#[tauri::command]
async fn save_settings(settings: AppSettings) -> Result<(), String> {
    let path = settings_file_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("Failed to create settings directory {}: {}", parent.display(), e))?;
    }

    let payload = serde_json::to_string_pretty(&settings)
        .map_err(|e| format!("Failed to serialize settings: {}", e))?;
    std::fs::write(&path, payload)
        .map_err(|e| format!("Failed to write settings file {}: {}", path.display(), e))?;
    log::info!("Settings saved: {:?}", settings);
    Ok(())
}

/// Proxy HTTP request to backend
/// Forwards all /api/* requests to localhost:8787
#[tauri::command]
async fn proxy_backend_request(
    path: String,
    method: String,
    body: Option<String>,
) -> Result<String, String> {
    let url = format!("http://localhost:8787{}", path);

    let client = reqwest::Client::new();
    let request = match method.to_uppercase().as_str() {
        "GET" => client.get(&url),
        "POST" => client.post(&url),
        "PUT" => client.put(&url),
        "DELETE" => client.delete(&url),
        _ => return Err("Unsupported HTTP method".to_string()),
    };

    let response = request
        .send()
        .await
        .map_err(|e| format!("Backend request failed: {}", e))?;

    let text = response
        .text()
        .await
        .map_err(|e| format!("Failed to read response: {}", e))?;

    Ok(text)
}

/// Helper: Find Python executable
fn find_python_executable() -> Option<String> {
    // Try to find python in PATH
    let which_result = if cfg!(windows) {
        Command::new("where").arg("python").output().ok()
    } else {
        Command::new("which").arg("python").output().ok()
    };

    if let Some(output) = which_result {
        if output.status.success() {
            let path = String::from_utf8(output.stdout)
                .ok()?
                .trim()
                .to_string();
            if !path.is_empty() {
                return Some(path);
            }
        }
    }

    // Try python3
    let which_result = if cfg!(windows) {
        Command::new("where").arg("python3").output().ok()
    } else {
        Command::new("which").arg("python3").output().ok()
    };

    if let Some(output) = which_result {
        if output.status.success() {
            let path = String::from_utf8(output.stdout)
                .ok()?
                .trim()
                .to_string();
            if !path.is_empty() {
                return Some(path);
            }
        }
    }

    None
}

fn default_settings() -> AppSettings {
    AppSettings {
        backend_port: 8787,
        lm_studio_url: "http://localhost:1234/v1".to_string(),
        nemo_database_path: ".nemo-memory.db".to_string(),
        auto_start_backend: true,
        theme: "light".to_string(),
    }
}

fn settings_file_path() -> Result<std::path::PathBuf, String> {
    let config_dir = dirs::config_dir()
        .ok_or_else(|| "Could not resolve platform config directory".to_string())?;
    Ok(config_dir.join(APP_DIR_NAME).join(SETTINGS_FILE_NAME))
}

/// Helper: Check if backend is healthy
fn is_backend_healthy(port: u16) -> bool {
    let url = format!("http://localhost:{}/api/health", port);
    
    match reqwest::blocking::Client::new()
        .get(&url)
        .timeout(Duration::from_secs(2))
        .send()
    {
        Ok(response) => response.status().is_success(),
        Err(_) => false,
    }
}

fn main() {
    env_logger::init();

    let backend_state = Arc::new(Mutex::new(BackendState {
        process: None,
        port: 8787,
        started_at: None,
    }));

    tauri::Builder::default()
        .manage(backend_state)
        .invoke_handler(generate_handler![
            health_check,
            get_settings,
            save_settings,
            proxy_backend_request,
            start_backend,
            stop_backend,
            query_backend_status,
        ])
        .run(generate_context!())
        .expect("error while running tauri application");
}
