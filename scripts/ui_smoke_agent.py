"""Smoke test the compiled Tauri desktop shell without using the user profile."""
from __future__ import annotations

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
    env["APPDATA"] = tempfile.mkdtemp(prefix="remotectrl-tauri-smoke-")
    process = subprocess.Popen([str(EXE)], cwd=str(EXE.parent), env=env)
    try:
        window = Desktop(backend="uia").window(title="RemoteCtrl Agent")
        window.wait("visible", timeout=25)
        for label in ["Overview", "Access & Privacy", "Activity", "Settings"]:
            window.child_window(title=label, control_type="Button").wait("exists", timeout=8)
        window.child_window(title="Access & Privacy", control_type="Button").click()
        power_toggle = window.child_window(title="Allow real power actions", control_type="Button")
        power_toggle.wait("enabled", timeout=8)
        power_toggle.click_input()
        window.child_window(title="Click the power toggle again to confirm real power actions. Every shutdown, restart, and sleep request will still need local approval.", control_type="Text").wait("visible", timeout=8)
        power_toggle.click_input()
        window.child_window(title="Refresh state", control_type="Button").click_input()
        time.sleep(1)
        control_texts = [control.window_text() for control in window.descendants() if control.window_text()]
        if "enabled on this device" not in control_texts:
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