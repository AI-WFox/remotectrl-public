use std::{
  io::{BufRead, BufReader, Write},
  process::{Child, ChildStdin, Command, Stdio},
  sync::Mutex,
  thread,
};

use tauri::{AppHandle, Emitter, Manager, State, WindowEvent};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

#[derive(Default)]
struct AgentCore {
  stdin: Mutex<Option<ChildStdin>>,
  child: Mutex<Option<Child>>,
}

fn sidecar_path(app: &AppHandle) -> Result<std::path::PathBuf, String> {
  let resource = app
    .path()
    .resource_dir()
    .map_err(|error| error.to_string())?
    .join("remotectrl-agent-core.exe");
  if resource.exists() {
    return Ok(resource);
  }

  let beside_host = std::env::current_exe()
    .map_err(|error| error.to_string())?
    .parent()
    .ok_or_else(|| "Cannot resolve the Agent executable directory".to_string())?
    .join("remotectrl-agent-core.exe");
  if beside_host.exists() {
    return Ok(beside_host);
  }

  Err("RemoteCtrl Agent core is missing from this installation. Reinstall the latest setup package.".to_string())
}

fn forward_pipe<R: std::io::Read + Send + 'static>(app: AppHandle, reader: R, event: &'static str) {
  thread::spawn(move || {
    for line in BufReader::new(reader).lines() {
      match line {
        Ok(line) if !line.trim().is_empty() => {
          let _ = app.emit_to("main", event, format!("{line}\n"));
        }
        Ok(_) => {}
        Err(_) => break,
      }
    }
  });
}

fn start_agent_core(app: &AppHandle) -> Result<(), String> {
  let state = app.state::<AgentCore>();
  if state.child.lock().map_err(|_| "Agent core lock failed")?.is_some() {
    return Ok(());
  }

  let mut command = Command::new(sidecar_path(app)?);
  command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
  #[cfg(windows)]
  command.creation_flags(CREATE_NO_WINDOW);

  let mut child = command.spawn().map_err(|error| format!("Cannot start Agent core: {error}"))?;
  let stdin = child.stdin.take().ok_or_else(|| "Agent core stdin is unavailable".to_string())?;
  let stdout = child.stdout.take().ok_or_else(|| "Agent core stdout is unavailable".to_string())?;
  let stderr = child.stderr.take().ok_or_else(|| "Agent core stderr is unavailable".to_string())?;

  forward_pipe(app.clone(), stdout, "agent-bridge-message");
  forward_pipe(app.clone(), stderr, "agent-bridge-stderr");
  *state.stdin.lock().map_err(|_| "Agent core stdin lock failed")? = Some(stdin);
  *state.child.lock().map_err(|_| "Agent core process lock failed")? = Some(child);
  Ok(())
}

#[tauri::command]
fn agent_bridge_write(payload: String, state: State<'_, AgentCore>) -> Result<(), String> {
  let result = (|| {
    let mut guard = state.stdin.lock().map_err(|_| "Agent core stdin lock failed")?;
    let stdin = guard.as_mut().ok_or_else(|| "Agent core is not running".to_string())?;
    stdin
      .write_all(payload.as_bytes())
      .and_then(|_| stdin.write_all(b"\n"))
      .and_then(|_| stdin.flush())
      .map_err(|error| format!("Cannot send request to Agent core: {error}"))
  })();
  result
}

#[tauri::command]
fn agent_core_shutdown(state: State<'_, AgentCore>) -> Result<(), String> {
  if let Some(mut child) = state.child.lock().map_err(|_| "Agent core process lock failed")?.take() {
    let _ = child.kill();
    let _ = child.wait();
  }
  *state.stdin.lock().map_err(|_| "Agent core stdin lock failed")? = None;
  Ok(())
}

pub fn run() {
  tauri::Builder::default()
    .manage(AgentCore::default())
    .plugin(tauri_plugin_dialog::init())
    .invoke_handler(tauri::generate_handler![agent_bridge_write, agent_core_shutdown])
    .setup(|app| {
      start_agent_core(&app.handle()).map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
      let open = MenuItemBuilder::with_id("open", "Open Agent").build(app)?;
      let pause = MenuItemBuilder::with_id("pause", "Pause / Resume").build(app)?;
      let reset = MenuItemBuilder::with_id("reset", "Reset session approvals").build(app)?;
      let quit = MenuItemBuilder::with_id("quit", "Quit").build(app)?;
      let menu = MenuBuilder::new(app)
        .item(&open)
        .item(&pause)
        .item(&reset)
        .separator()
        .item(&quit)
        .build()?;
      TrayIconBuilder::with_id("agent-tray")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("RemoteCtrl Agent")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
          "open" => {
            if let Some(window) = app.get_webview_window("main") {
              let _ = window.show();
              let _ = window.set_focus();
            }
          }
          "pause" => { let _ = app.emit("tray-command", "agent.pause_toggle"); }
          "reset" => { let _ = app.emit("tray-command", "agent.reset_approvals"); }
          "quit" => {
            let state = app.state::<AgentCore>();
            if let Some(mut child) = state.child.lock().ok().and_then(|mut guard| guard.take()) {
              let _ = child.kill();
            }
            app.exit(0)
          }
          _ => {}
        })
        .build(app)?;
      Ok(())
    })
    .on_window_event(|window, event| {
      if window.label() == "main" {
        if let WindowEvent::CloseRequested { api, .. } = event {
          api.prevent_close();
          let _ = window.hide();
        }
      }
    })
    .run(tauri::generate_context!())
    .expect("error while running RemoteCtrl Agent");
}