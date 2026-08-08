#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
  remotectrl_agent_desktop_lib::run();
}
