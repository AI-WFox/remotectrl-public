"""Browser-to-desktop E2E test for the consent-first RemoteCtrl flow.

Runs an isolated FastAPI gateway, Vite dashboard and the packaged Tauri Agent.
The default suite uses only read-only or dry-run actions. Pass --extended to also
exercise Notepad, an additional screenshot and Activity Capture on this test machine.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import requests
from playwright.sync_api import Page, sync_playwright
from pywinauto import Desktop


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
WEB_DIR = ROOT / "web"
BACKEND_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
AGENT_EXE = ROOT / "agent-desktop" / "src-tauri" / "target" / "release" / "remotectrl-agent-desktop.exe"
NPM = ROOT / "tools" / "node-v24.16.0-win-x64" / "npm.cmd"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
GATEWAY_PORT = 8788
WEB_PORT = 5175
BASE_URL = f"http://127.0.0.1:{GATEWAY_PORT}"
WEB_URL = f"http://127.0.0.1:{WEB_PORT}"
AGENT_NAME = "E2E Desktop Agent"
APPROVAL_HANDLES_BEFORE: set[int] = set()


def log(message: str) -> None:
    print(f"[e2e] {message}", flush=True)


class E2EFailure(RuntimeError):
    pass


def wait_for_http(url: str, timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=1).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(0.25)
    raise E2EFailure(f"Service did not become ready: {url}")


def terminate(process: subprocess.Popen[object] | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        # Do not broadcast CTRL_BREAK_EVENT: GUI children can share a console
        # with the desktop host and a broadcast may interrupt unrelated apps.
        process.terminate()
        process.wait(timeout=8)
    except (subprocess.TimeoutExpired, OSError):
        process.kill()


def start_gateway(work_dir: Path) -> subprocess.Popen[object]:
    env = os.environ.copy()
    env.update(
        {
            "REMOTECTRL_DB": str(work_dir / "gateway.db"),
            "REMOTECTRL_SECRET_KEY": "desktop-e2e-secret",
            "REMOTECTRL_CORS_ORIGINS": WEB_URL,
        }
    )
    return subprocess.Popen(
        [str(BACKEND_PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(GATEWAY_PORT)],
        cwd=BACKEND_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def start_dashboard() -> subprocess.Popen[object]:
    env = os.environ.copy()
    env["VITE_API_BASE"] = BASE_URL
    env["PATH"] = f"{NPM.parent};{env['PATH']}"
    return subprocess.Popen(
        [str(NPM), "run", "dev", "--", "--host", "127.0.0.1", "--port", str(WEB_PORT)],
        cwd=WEB_DIR,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )


def write_agent_config(appdata: Path, allowed_folder: Path, agent_id: str, agent_token: str) -> None:
    config_dir = appdata / "RemoteCtrlAgent"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        "{\n"
        f'  "server_url": "{BASE_URL}",\n'
        f'  "agent_id": "{agent_id}",\n'
        f'  "agent_token": "{agent_token}",\n'
        f'  "agent_name": "{AGENT_NAME}",\n'
        f'  "allowed_folders": ["{allowed_folder.as_posix()}"],\n'
        "  \"paused\": false,\n"
        "  \"dry_run_power\": true,\n"
        "  \"ui_theme\": \"light\",\n"
        "  \"privacy_defaults_version\": 2\n"
        "}\n",
        encoding="utf-8",
    )


def enroll_fixture(enrollment_token: str) -> tuple[str, str]:
    response = requests.post(
        f"{BASE_URL}/api/agents/enroll",
        json={
            "enrollment_token": enrollment_token,
            "name": AGENT_NAME,
            "hostname": "e2e-desktop-host",
            "os": "Windows 11 E2E",
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    return str(data["agent_id"]), str(data["agent_token"])


def start_agent(appdata: Path) -> subprocess.Popen[object]:
    if not AGENT_EXE.exists():
        raise E2EFailure(f"Missing packaged Agent executable: {AGENT_EXE}")
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    return subprocess.Popen([str(AGENT_EXE)], cwd=AGENT_EXE.parent, env=env)


def agent_window(process: subprocess.Popen[object]):
    deadline = time.monotonic() + 30
    desktop = Desktop(backend="uia")
    while time.monotonic() < deadline:
        for window in desktop.windows():
            try:
                if window.process_id() == process.pid and window.window_text() == "RemoteCtrl Agent" and window.is_visible():
                    return desktop.window(handle=window.handle)
            except Exception:
                continue
        time.sleep(0.2)
    windows = [(window.window_text(), window.process_id()) for window in desktop.windows() if window.window_text()]
    raise E2EFailure(f"RemoteCtrl Agent window did not appear for PID {process.pid}; visible windows: {windows}")


def wait_for_online_agent(access_token: str, timeout: float = 25.0) -> dict:
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {access_token}"}
    latest: list[dict] = []
    while time.monotonic() < deadline:
        response = requests.get(f"{BASE_URL}/api/agents", headers=headers, timeout=3)
        response.raise_for_status()
        latest = response.json()
        agent = next((item for item in latest if item.get("name") == AGENT_NAME), None)
        if agent and agent.get("status") == "online":
            return agent
        time.sleep(0.5)
    raise E2EFailure(f"Agent did not connect to Gateway. Gateway agents: {latest}")


def wait_for_terminal_command(access_token: str, command_type: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    headers = {"Authorization": f"Bearer {access_token}"}
    last: dict | None = None
    while time.monotonic() < deadline:
        response = requests.get(f"{BASE_URL}/api/commands", headers=headers, timeout=3)
        response.raise_for_status()
        matches = [item for item in response.json() if item.get("type") == command_type]
        if matches:
            last = matches[0]
            if last.get("status") in {"succeeded", "failed", "denied"}:
                return last
        time.sleep(0.3)
    raise E2EFailure(f"Command did not reach a terminal state: {command_type}; last={last}")


def approve_next(process: subprocess.Popen[object], expected_command: str) -> None:
    deadline = time.monotonic() + 20
    desktop = Desktop(backend="uia")
    while time.monotonic() < deadline:
        for approval in reversed(desktop.windows()):
            if approval.window_text() != "RemoteCtrl Approval":
                continue
            try:
                if not approval.is_visible() or approval.process_id() != process.pid:
                    continue
                labels = [item.window_text() for item in approval.descendants() if item.window_text()]
                if expected_command not in labels:
                    continue
                buttons = approval.descendants(control_type="Button", title="Allow once")
                if not buttons:
                    continue
                button = buttons[0]
                try:
                    button.click_input()
                except Exception:
                    button.iface_invoke.Invoke()
                return
            except Exception:
                continue
        time.sleep(0.2)
    titles = [window.window_text() for window in desktop.windows() if window.window_text()]
    approval_controls: list[str] = []
    for approval in desktop.windows():
        if approval.window_text() == "RemoteCtrl Approval":
            try:
                approval_controls.extend(item.window_text() for item in approval.descendants() if item.window_text())
            except Exception:
                pass
    raise E2EFailure(f"Approval window did not appear for {expected_command}; visible windows: {titles}; approval controls: {approval_controls}")
def run_approved(page: Page, agent_process: subprocess.Popen[object], module: str, action: str, command: str, expected: str, requires_approval: bool = True, result_timeout_ms: int = 25_000) -> None:
    page.get_by_role("button", name=module, exact=True).click()
    page.get_by_role("button", name=action, exact=True).click()
    if requires_approval:
        approve_next(agent_process, command)
    dashboard_token = page.evaluate("localStorage.getItem('rt_token')")
    if not isinstance(dashboard_token, str):
        raise E2EFailure("Dashboard session token disappeared during command test.")
    terminal = wait_for_terminal_command(dashboard_token, command, timeout=result_timeout_ms / 1000)
    if terminal.get("status") != "succeeded":
        raise E2EFailure(f"Command failed: {terminal}")
    page.get_by_title("Refresh").click()
    page.get_by_text(expected, exact=True).first.wait_for(timeout=12_000)
    if requires_approval:
        page.get_by_text("approval.response", exact=True).first.wait_for(timeout=12_000)


def wait_for_activity_indicator(process: subprocess.Popen[object], visible: bool, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    desktop = Desktop(backend="uia")
    while time.monotonic() < deadline:
        shown = False
        for window in desktop.windows():
            try:
                shown = shown or (
                    window.process_id() == process.pid
                    and window.window_text() == "RemoteCtrl Activity Capture"
                    and window.is_visible()
                )
            except Exception:
                continue
        if shown == visible:
            return
        time.sleep(0.2)
    expected = "open" if visible else "closed"
    raise E2EFailure(f"Activity indicator did not become {expected}")

def run_extended(page: Page, agent_process: subprocess.Popen[object]) -> None:
    log("checking Web command routing and approval dialogs")
    run_approved(page, agent_process, "Applications", "Notepad", "app.start", "app.start")
    run_approved(page, agent_process, "Screen", "Capture Still", "screen.screenshot", "Screenshot")
    run_approved(page, agent_process, "Activity Capture", "Start Activity Session", "activity.start", "activity.start")
    wait_for_activity_indicator(agent_process, True)
    run_approved(page, agent_process, "Activity Capture", "Stop Session", "activity.stop", "activity.stop")
    wait_for_activity_indicator(agent_process, False)


def assert_no_horizontal_overflow(page: Page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.wait_for_timeout(250)
    metrics = page.evaluate("() => ({ viewport: window.innerWidth, scrollWidth: document.documentElement.scrollWidth })")
    if metrics["scrollWidth"] > metrics["viewport"] + 1:
        raise E2EFailure(f"Dashboard has horizontal overflow at {width}x{height}: {metrics}")


def run_browser_flow(appdata: Path, allowed_folder: Path, extended: bool) -> None:
    if not CHROME.exists():
        raise E2EFailure(f"Chrome was not found: {CHROME}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        agent_process: subprocess.Popen[object] | None = None
        try:
            log("opening dashboard in Chrome")
            page.goto(WEB_URL, wait_until="networkidle")
            page.get_by_label("Email").fill("admin@remotectrl.local")
            page.get_by_label("Password").fill("admin12345")
            page.get_by_role("button", name="Sign in", exact=True).click()
            page.get_by_role("button", name="Create enrollment token", exact=True).wait_for(timeout=12_000)
            page.get_by_role("button", name="Create enrollment token", exact=True).click()
            enrollment = page.locator(".token-strip code")
            enrollment.wait_for(timeout=12_000)

            # Tauri WebView inputs are not exposed to UI Automation. Enrollment is
            # seeded through the same public Gateway endpoint; subsequent commands
            # traverse the packaged Agent and its approval child windows.
            log("enrolling isolated fixture and starting packaged Agent")
            agent_id, agent_token = enroll_fixture(enrollment.inner_text())
            write_agent_config(appdata, allowed_folder, agent_id, agent_token)
            global APPROVAL_HANDLES_BEFORE
            APPROVAL_HANDLES_BEFORE = {window.handle for window in Desktop(backend="uia").windows() if window.window_text() == "RemoteCtrl Approval"}
            agent_process = start_agent(appdata)
            desktop_window = agent_window(agent_process)
            connect_button = desktop_window.child_window(title="Reconnect", control_type="Button")
            try:
                connect_button.wait("enabled", timeout=45)
            except Exception as exc:
                controls = [item.window_text() for item in desktop_window.descendants() if item.window_text()]
                raise E2EFailure(f"Agent core did not become ready: {exc}; controls={controls[:80]}") from exc
            connect_button.click()
            time.sleep(1)
            immediate_controls = [item.window_text() for item in desktop_window.descendants() if item.window_text()]
            log(f"Agent controls after Reconnect: {immediate_controls[-20:]}")
            dashboard_token = page.evaluate("localStorage.getItem('rt_token')")
            if not isinstance(dashboard_token, str):
                raise E2EFailure("Dashboard did not store an authenticated session token.")
            try:
                wait_for_online_agent(dashboard_token)
            except E2EFailure as exc:
                controls = [item.window_text() for item in desktop_window.descendants() if item.window_text()]
                raise E2EFailure(f"{exc}; Agent controls: {controls[:80]}") from exc
            page.get_by_title("Refresh").click()
            agent_button = page.get_by_role("button", name=AGENT_NAME, exact=False)
            agent_button.wait_for(timeout=15_000)
            agent_button.click()
            page.get_by_text("Gateway connected. Live agent data is active.", exact=True).wait_for(timeout=15_000)
            assert_no_horizontal_overflow(page, 1366, 768)
            assert_no_horizontal_overflow(page, 1920, 1080)

            log("checking Web command routing and approval dialogs")
            run_approved(page, agent_process, "Applications", "Refresh Applications", "app.list", "Visible windows")
            run_approved(page, agent_process, "Processes", "Refresh Processes", "process.list", "Background processes")
            run_approved(page, agent_process, "Files", "Choose Folder", "files.roots", "Choose an allowed folder")
            run_approved(page, agent_process, "Screen", "Capture Still", "screen.screenshot", "Screenshot")
            run_approved(page, agent_process, "Webcam", "Check Cameras", "webcam.list", "Camera diagnostics", result_timeout_ms=60_000)
            run_approved(page, agent_process, "Power", "Refresh Power Status", "power.status", "System uptime")

            if extended:
                run_extended(page, agent_process)

            print("Web -> Gateway -> Tauri Agent -> approval -> result E2E flow passed")
        finally:
            browser.close()
            terminate(agent_process)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--extended", action="store_true", help="Also start Notepad, an additional screenshot and Activity Capture.")
    args = parser.parse_args()
    if not BACKEND_PYTHON.exists() or not NPM.exists():
        raise E2EFailure("Missing backend environment or bundled Node runtime.")

    temp_root = Path(tempfile.mkdtemp(prefix="remotectrl-web-agent-e2e-"))
    allowed_folder = temp_root / "allowed-files"
    allowed_folder.mkdir()
    (allowed_folder / "e2e.txt").write_text("RemoteCtrl E2E fixture", encoding="utf-8")
    appdata = temp_root / "appdata"
    gateway = dashboard = None
    try:
        log("starting isolated gateway")
        gateway = start_gateway(temp_root)
        wait_for_http(f"{BASE_URL}/api/health")
        log("starting dashboard")
        dashboard = start_dashboard()
        wait_for_http(WEB_URL)
        log("running browser and approval workflow")
        run_browser_flow(appdata, allowed_folder, args.extended)
        return 0
    finally:
        terminate(dashboard)
        terminate(gateway)
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EFailure as exc:
        print(f"E2E failure: {exc}", file=sys.stderr)
        raise SystemExit(1)
