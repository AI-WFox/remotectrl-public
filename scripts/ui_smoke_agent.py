"""Smoke test the compiled Tauri desktop shell without using the user profile."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
from pywinauto import Desktop

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "agent-desktop" / "src-tauri" / "target" / "release" / "remotectrl-agent-desktop.exe"


def main() -> int:
    if not EXE.exists():
        print(f"Missing Tauri executable: {EXE}", file=sys.stderr)
        return 2
    env = os.environ.copy()
    appdata = Path(tempfile.mkdtemp(prefix="remotectrl-tauri-smoke-"))
    config_path = appdata / "RemoteCtrlAgent" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_bytes(b"\x00" * 128)
    env["APPDATA"] = str(appdata)
    process = subprocess.Popen([str(EXE)], cwd=str(EXE.parent), env=env)
    try:
        window = Desktop(backend="uia").window(title="RemoteCtrl Agent")
        window.wait("visible", timeout=25)
        recovered_config = json.loads(config_path.read_text(encoding="utf-8"))
        if recovered_config.get("server_url") != "https://remotectrl-public-demo.onrender.com":
            raise RuntimeError("Agent core did not recover the corrupt local config")
        for label in ["Overview", "Access & Privacy", "Activity", "Settings"]:
            window.child_window(title=label, control_type="Button").wait("exists", timeout=8)
        window.child_window(title="Access & Privacy", control_type="Button").click()
        power_toggle = window.child_window(title="Allow real power actions", control_type="Button")
        power_toggle.wait("enabled", timeout=8)
        power_toggle.click_input()
        window.child_window(title="Confirm that this device may perform real shutdown, restart, and sleep actions. Every request will still need local approval.", control_type="Text").wait("visible", timeout=8)
        confirm_text = window.child_window(title="Confirm that this device may perform real shutdown, restart, and sleep actions. Every request will still need local approval.", control_type="Text")
        core_processes = [child for child in psutil.Process(process.pid).children(recursive=True) if "remotectrl-agent-core" in child.name().lower()]
        if not core_processes:
            raise RuntimeError("Packaged Agent core process was not running")
        for core_process in core_processes:
            core_process.kill()
        psutil.wait_procs(core_processes, timeout=5)
        window.child_window(title="Enable real mode", control_type="Button").invoke()
        confirm_text.wait_not("visible", timeout=8)
        deadline = time.monotonic() + 8
        control_texts = []
        while time.monotonic() < deadline:
            control_texts = [control.window_text() for control in window.descendants() if control.window_text()]
            if any("enabled on this device" in text for text in control_texts):
                break
            time.sleep(0.2)
        if not any("enabled on this device" in text for text in control_texts):
            raise RuntimeError(f"Power safety did not update after confirmation: {control_texts[-60:]}")
        child_pids = {child.pid for child in psutil.Process(process.pid).children(recursive=True)}
        console_windows = []
        for candidate in Desktop(backend="win32").windows():
            try:
                if candidate.process_id() in child_pids and candidate.class_name() == "ConsoleWindowClass" and candidate.is_visible():
                    console_windows.append(candidate.window_text())
            except Exception:
                continue
        if console_windows:
            raise RuntimeError(f"Agent core opened a visible terminal window: {console_windows}")
        print("Tauri Agent UI smoke test passed (no visible Agent terminal)")
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=8)


if __name__ == "__main__":
    raise SystemExit(main())
