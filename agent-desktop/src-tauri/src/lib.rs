use tauri::{Emitter, Manager, WindowEvent};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;

pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_shell::init())
    .plugin(tauri_plugin_dialog::init())
    .setup(|app| {
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
          "quit" => app.exit(0),
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