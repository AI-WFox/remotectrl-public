from __future__ import annotations

import base64
import csv
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from remotectrl_agent.core.config import AgentConfig


SYSTEM_PROCESS_NAMES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "explorer.exe",
    "applicationframehost.exe",
}

APP_PRESETS = {
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
}

APP_TITLE_ALIASES = {
    "notepad": ["notepad"],
    "calculator": ["calculator"],
    "paint": ["paint"],
    "explorer": ["file explorer", "this pc", "downloads", "documents"],
    "chrome": ["chrome"],
    "brave": ["brave"],
}


class CommandHandlers:
    def __init__(self, config: AgentConfig, desktop_provider):
        self.config = config
        self.desktop_provider = desktop_provider

    def handle(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        routes = {
            "process.list": self.process_list,
            "process.kill": self.process_kill,
            "app.list": self.app_list,
            "app.start": self.app_start,
            "app.stop": self.app_stop,
            "screen.screenshot": self.screen_screenshot,
            "screen.live.start": self.screen_screenshot,
            "files.roots": self.files_roots,
            "files.list": self.files_list,
            "files.download": self.files_download,
            "power.shutdown": self.power_shutdown,
            "power.restart": self.power_restart,
            "power.sleep": self.power_sleep,
            "power.status": self.power_status,

            "activity.start": self.activity_start,
            "activity.stop": self.activity_stop,
            "activity.export": self.activity_export,
        }
        handler = routes.get(command_type)
        if not handler:
            raise ValueError(f"Unsupported command: {command_type}")
        return handler(payload)

    def process_list(self, _payload: dict[str, Any]) -> dict[str, Any]:
        apps = self.app_list({}).get("items", [])
        try:
            import psutil

            items = []
            for proc in psutil.process_iter(["pid", "name", "status", "cpu_percent", "memory_info"]):
                info = proc.info
                mem = info.get("memory_info")
                items.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "status": info.get("status"),
                        "cpu": info.get("cpu_percent") or 0,
                        "memory_mb": round((mem.rss if mem else 0) / (1024 * 1024), 1),
                    }
                )
            return {"items": items[:250], "apps": apps, "count": len(items), "app_count": len(apps), "source": "psutil"}
        except Exception:
            output = subprocess.check_output(["tasklist", "/fo", "csv"], text=True, errors="ignore")
            rows = list(csv.DictReader(io.StringIO(output)))
            return {
                "items": [{"pid": row.get("PID"), "name": row.get("Image Name"), "status": "unknown"} for row in rows],
                "apps": apps,
                "count": len(rows),
                "app_count": len(apps),
                "source": "tasklist",
            }

    def process_kill(self, payload: dict[str, Any]) -> dict[str, Any]:
        pid = int(payload.get("pid", 0))
        if pid <= 0:
            raise ValueError("pid is required")
        try:
            import psutil

            try:
                proc = psutil.Process(pid)
                name = proc.name().lower()
            except psutil.NoSuchProcess:
                return {"pid": pid, "name": "unknown", "status": "already_stopped"}
            if name in SYSTEM_PROCESS_NAMES:
                raise PermissionError(f"Refusing to stop protected process: {name}")
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except psutil.NoSuchProcess:
                pass
            except psutil.AccessDenied as exc:
                raise PermissionError(f"Windows denied permission to stop {name} (PID {pid})") from exc
            except psutil.TimeoutExpired as exc:
                raise RuntimeError(f"Process {name} (PID {pid}) did not stop within 2 seconds") from exc
            return {"pid": pid, "name": name, "status": "stopped"}
        except ImportError:
            raise RuntimeError("psutil is required for guarded process kill")
    def app_list(self, _payload: dict[str, Any]) -> dict[str, Any]:
        if os.name != "nt":
            return {"items": [], "count": 0, "window_count": 0, "source": "unsupported"}
        try:
            windows = self._visible_windows()
            source = "win32"
            fallback_error = None
        except Exception as exc:
            fallback = self._app_list_powershell()
            windows = fallback.get("items", [])
            source = str(fallback.get("source") or "powershell")
            fallback_error = str(exc)
        items = self._group_visible_apps(windows)
        result: dict[str, Any] = {
            "items": items,
            "count": len(items),
            "window_count": len(windows),
            "source": source,
        }
        if fallback_error:
            result["fallback_error"] = fallback_error
        return result

    def _group_visible_apps(self, windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for window in windows:
            app_key = self._app_identity(window)
            if not app_key:
                continue
            item = grouped.setdefault(
                app_key,
                {"app_key": app_key, "name": self._app_display_name(app_key), "window_count": 0, "process_names": []},
            )
            item["window_count"] = int(item["window_count"]) + 1
            process_name = str(window.get("name") or "").strip()
            if process_name and process_name not in item["process_names"]:
                item["process_names"].append(process_name)
        return sorted(grouped.values(), key=lambda item: str(item["name"]).lower())

    def _app_identity(self, window: dict[str, Any]) -> str:
        process_name = self._normalize_process_name(str(window.get("name") or ""))
        title = str(window.get("title") or "").strip().lower()
        for preset, candidates in APP_PRESETS.items():
            candidate_names = {
                self._normalize_process_name(Path(candidate).name)
                for candidate in candidates
            }
            if process_name in candidate_names or (process_name == "applicationframehost" and any(alias in title for alias in APP_TITLE_ALIASES.get(preset, []))):
                return preset
        return process_name

    @staticmethod
    def _app_display_name(app_key: str) -> str:
        names = {
            "notepad": "Notepad",
            "calculator": "Calculator",
            "paint": "Paint",
            "explorer": "File Explorer",
            "chrome": "Chrome",
            "brave": "Brave",
        }
        return names.get(app_key, app_key.replace("-", " ").replace("_", " ").title())

    def app_stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        app_key = str(payload.get("app_key") or "").strip().lower()
        if not app_key:
            raise ValueError("app_key is required")
        windows = [window for window in self._visible_windows() if self._app_identity(window) == app_key]
        if not windows:
            return {"app_key": app_key, "name": self._app_display_name(app_key), "status": "already_stopped", "closed_windows": 0, "terminated_processes": 0}

        process_names = sorted({
            str(window.get("name") or "").strip()
            for window in windows
            if str(window.get("name") or "").strip()
        })
        closed_windows = 0
        if os.name == "nt":
            import ctypes
            user32 = ctypes.windll.user32
            WM_CLOSE = 0x0010
            for window in windows:
                hwnd = int(window.get("hwnd") or 0)
                if hwnd and user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
                    closed_windows += 1

        remaining = self._wait_for_app_windows_to_close(app_key, timeout=2.0)
        remaining_before_terminate = len(remaining)
        terminated_processes = 0
        if remaining and app_key != "explorer":
            try:
                import psutil

                remaining_process_names = {
                    str(window.get("name") or "").strip().lower()
                    for window in remaining
                    if str(window.get("name") or "").strip()
                }
                for proc in psutil.process_iter(["pid", "name"]):
                    name = str(proc.info.get("name") or "").strip().lower()
                    if name in remaining_process_names and name not in SYSTEM_PROCESS_NAMES:
                        proc.terminate()
                        terminated_processes += 1
            except ImportError as exc:
                raise RuntimeError("psutil is required to close all processes for an application") from exc
            remaining = self._wait_for_app_windows_to_close(app_key, timeout=2.0)

        if remaining:
            raise RuntimeError(
                f"{self._app_display_name(app_key)} still has {len(remaining)} visible window(s) after the close request"
            )
        return {
            "app_key": app_key,
            "name": self._app_display_name(app_key),
            "status": "stopped",
            "closed_windows": closed_windows,
            "terminated_processes": terminated_processes,
            "process_names": process_names,
            "remaining_windows_before_terminate": remaining_before_terminate,
            "remaining_windows": 0,
        }

    def _wait_for_app_windows_to_close(self, app_key: str, timeout: float) -> list[dict[str, Any]]:
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            remaining = [window for window in self._visible_windows() if self._app_identity(window) == app_key]
            if not remaining or time.monotonic() >= deadline:
                return remaining
            time.sleep(0.1)
    def _app_list_powershell(self) -> dict[str, Any]:
        ps = (
            "Get-Process | Where-Object {$_.MainWindowTitle} | "
            "Select-Object Id,ProcessName,MainWindowTitle,MainWindowHandle | ConvertTo-Json"
        )
        try:
            output = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps], text=True, errors="ignore")
            import json

            parsed = json.loads(output) if output.strip() else []
            if isinstance(parsed, dict):
                parsed = [parsed]
            items = []
            for item in parsed:
                title = str(item.get("MainWindowTitle") or "")
                name = str(item.get("ProcessName") or "")
                if title and not self._is_agent_window(name, title):
                    items.append(
                        {
                            "pid": item.get("Id"),
                            "name": name,
                            "title": title,
                            "hwnd": item.get("MainWindowHandle"),
                        }
                    )
            return {"items": items, "count": len(items), "source": "powershell"}
        except Exception as exc:
            return {"items": [], "count": 0, "source": "powershell", "error": str(exc)}

    def _visible_windows(self) -> list[dict[str, Any]]:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        items: list[dict[str, Any]] = []

        try:
            import psutil
        except Exception:
            psutil = None

        try:
            dwmapi = ctypes.windll.dwmapi
        except Exception:
            dwmapi = None

        def is_cloaked(hwnd: int) -> bool:
            if not dwmapi:
                return False
            cloaked = ctypes.c_int(0)
            # DWMWA_CLOAKED = 14. Cloaked windows are not visible to the user.
            result = dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            return result == 0 and cloaked.value != 0

        enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_proc(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd) or is_cloaked(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)
            title = title_buffer.value.strip()
            if not title:
                return True
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, 256)
            class_name = class_buffer.value
            if title.lower() == "program manager" or class_name in {"Progman", "WorkerW", "Shell_TrayWnd"}:
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_name = "unknown"
            if psutil and pid.value:
                try:
                    process_name = psutil.Process(pid.value).name()
                except Exception:
                    process_name = "unknown"
            if self._is_agent_window(process_name, title):
                return True
            items.append({"pid": int(pid.value), "name": process_name, "title": title, "hwnd": int(hwnd)})
            return True

        callback = enum_proc_type(enum_proc)
        user32.EnumWindows(callback, 0)
        items.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("title") or "").lower()))
        return items[:250]

    def app_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_app_path(payload)
        if not path:
            raise ValueError("path or preset is required")
        if not self._is_allowed_app(path):
            raise PermissionError("App path is not allowlisted for demo start")
        mode = str(payload.get("mode") or "focus_existing").strip().lower()
        if mode not in {"focus_existing", "new_instance"}:
            raise ValueError("mode must be focus_existing or new_instance")
        if mode == "focus_existing":
            existing = self._find_existing_app(payload, path)
            if existing:
                focused = self._focus_window(existing.get("hwnd"))
                return {
                    "path": path,
                    "preset": payload.get("preset"),
                    "mode": mode,
                    "status": "focused_existing" if focused else "existing_found",
                    "window": existing,
                }
        subprocess.Popen([path], close_fds=True)
        return {
            "path": path,
            "preset": payload.get("preset"),
            "mode": mode,
            "status": "started_new" if mode == "new_instance" else "fallback_started",
        }

    def screen_screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        from PIL import ImageGrab

        quality = min(max(int(payload.get("quality", 75)), 35), 90)
        hide_approval_windows = bool(payload.get("_hide_approval_windows"))
        if hide_approval_windows:
            self.desktop_provider("screen_capture_hide_approval")
        try:
            image = ImageGrab.grab()
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=quality)
            return {
                "mime": "image/jpeg",
                "image": base64.b64encode(buf.getvalue()).decode(),
                "width": image.width,
                "height": image.height,
            }
        finally:
            if hide_approval_windows:
                self.desktop_provider("screen_capture_restore_approval")

    def files_roots(self, _payload: dict[str, Any]) -> dict[str, Any]:
        roots = []
        for raw in self.config.allowed_folders:
            path = Path(raw).expanduser()
            roots.append(
                {
                    "name": path.name or str(path),
                    "path": str(path),
                    "exists": path.exists(),
                    "is_dir": path.is_dir(),
                }
            )
        return {"roots": roots, "count": len(roots), "requires_selection": True}

    def files_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_path = payload.get("path")
        if not raw_path:
            roots = self.files_roots({})
            roots["entries"] = []
            return roots
        target, allowed_root = self._safe_path_with_root(raw_path)
        entries = []
        skipped_hidden = 0
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
                if self._should_hide_file_entry(entry):
                    skipped_hidden += 1
                    continue
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size,
                    }
                )
            except OSError:
                continue
        return {"path": str(target), "allowed_root": str(allowed_root), "entries": entries[:500], "hidden_filtered": skipped_hidden}

    def files_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self._safe_path(payload.get("path"))
        if self._should_hide_file_entry(target):
            raise PermissionError("Hidden/system files are not exposed by Web Files")
        if not target.is_file():
            raise ValueError("path must be a file")
        if target.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("demo download limit is 10MB")
        return {
            "name": target.name,
            "path": str(target),
            "mime": "application/octet-stream",
            "data": base64.b64encode(target.read_bytes()).decode(),
        }

    def power_shutdown(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/s", "/t", "5"], "shutdown")

    def power_restart(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/r", "/t", "5"], "restart")

    def power_sleep(self, _payload: dict[str, Any]) -> dict[str, Any]:
        # Request normal suspend without forcing a critical state; forced suspend can behave like hibernate on some Windows drivers.
        return self._power(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,0,0"], "sleep")

    def power_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status: dict[str, Any] = {
            "action": "status",
            "status": "ok",
            "dry_run_power": self.config.dry_run_power,
            "supported_actions": ["shutdown", "restart", "sleep"],
            "system_uptime_seconds": None,
            "battery_percent": None,
            "battery_plugged": None,
            "cpu_percent": None,
        }
        try:
            import psutil

            status["system_uptime_seconds"] = max(0, int(time.time() - psutil.boot_time()))
            status["cpu_percent"] = round(float(psutil.cpu_percent(interval=0.2)), 1)
            battery = psutil.sensors_battery()
            if battery is not None:
                status["battery_percent"] = round(float(battery.percent), 1)
                status["battery_plugged"] = bool(battery.power_plugged)
        except Exception as exc:
            status["diagnostic_error"] = str(exc)
        return status


    def activity_start(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status = self.desktop_provider("activity_start") or "started"
        return {"status": status, "mode": "visible_activity_session"}

    def activity_stop(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status = self.desktop_provider("activity_stop") or "stopped"
        return {"status": status}

    def activity_export(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"events": self.desktop_provider("activity_export"), "mode": "visible_activity_session"}

    def _power(self, args: list[str], action: str) -> dict[str, Any]:
        if self.config.dry_run_power:
            return {"action": action, "status": "dry_run", "power_mode": "dry_run", "message": "Real power actions are disabled on the Agent."}
        try:
            # shell=False preserves the explicit Windows invocation and avoids command-shell injection.
            subprocess.Popen(args)
        except OSError as exc:
            raise RuntimeError(f"Windows could not request {action}: {exc}") from exc
        return {"action": action, "status": "requested", "power_mode": "real", "message": f"{action} was requested on the Agent."}

    def _resolve_app_path(self, payload: dict[str, Any]) -> str:
        preset = str(payload.get("preset", "")).strip().lower()
        if preset in APP_PRESETS:
            for candidate in APP_PRESETS[preset]:
                if "\\" not in candidate and "/" not in candidate:
                    found = self._find_on_path(candidate)
                    if found:
                        return found
                    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / candidate
                    if system32.exists():
                        return str(system32)
                    continue
                path = Path(candidate)
                if path.exists():
                    return str(path)
            raise FileNotFoundError(f"Preset app is not available: {preset}")
        return str(payload.get("path", "")).strip()

    def _find_on_path(self, executable: str) -> str | None:
        for raw_folder in os.environ.get("PATH", "").split(os.pathsep):
            if not raw_folder:
                continue
            candidate = Path(raw_folder) / executable
            if candidate.exists():
                return str(candidate)
        return None

    def _find_existing_app(self, payload: dict[str, Any], path: str) -> dict[str, Any] | None:
        if os.name != "nt":
            return None
        target_names = {self._normalize_process_name(Path(path).name), self._normalize_process_name(Path(path).stem)}
        preset = str(payload.get("preset") or "").strip().lower()
        if preset:
            target_names.add(self._normalize_process_name(preset))
            for candidate in APP_PRESETS.get(preset, []):
                target_names.add(self._normalize_process_name(Path(candidate).name))
                target_names.add(self._normalize_process_name(Path(candidate).stem))
        target_names.discard("")
        target_titles = {str(payload.get("title") or "").strip().lower()}
        if preset:
            target_titles.update(APP_TITLE_ALIASES.get(preset, []))
        target_titles.discard("")
        for item in self._visible_windows():
            name = self._normalize_process_name(str(item.get("name") or ""))
            title = str(item.get("title") or "").lower()
            if name in target_names or (name == "applicationframehost" and any(alias in title for alias in target_titles)):
                return item
        return None

    def _normalize_process_name(self, name: str) -> str:
        normalized = name.strip().lower()
        if normalized.endswith(".exe"):
            normalized = normalized[:-4]
        return normalized

    def _is_agent_window(self, process_name: str, title: str) -> bool:
        process = process_name.strip().lower()
        window_title = title.strip().lower()
        agent_titles = (
            "remotectrl agent",
            "remotectrl approval",
            "remotectrl activity capture",
            "remotectrl visible key capture",
        )
        return process in {"remotectrlagent.exe", "remotectrlagent"} or window_title.startswith(agent_titles)

    def _focus_window(self, hwnd: Any) -> bool:
        if os.name != "nt" or not hwnd:
            return False
        try:
            import ctypes

            handle = int(hwnd)
            user32 = ctypes.windll.user32
            SW_RESTORE = 9
            if user32.IsIconic(handle):
                user32.ShowWindow(handle, SW_RESTORE)
            else:
                user32.ShowWindow(handle, SW_RESTORE)
            user32.BringWindowToTop(handle)
            user32.SetForegroundWindow(handle)
            return int(user32.GetForegroundWindow()) == handle or bool(user32.IsWindowVisible(handle))
        except Exception:
            return False

    def _allowed_roots(self) -> list[Path]:
        return [Path(folder).expanduser().resolve() for folder in self.config.allowed_folders]

    def _safe_path_with_root(self, raw_path: Any) -> tuple[Path, Path]:
        if not raw_path:
            raise ValueError("path is required")
        target = Path(str(raw_path)).expanduser().resolve()
        for root in self._allowed_roots():
            if target == root or root in target.parents:
                return target, root
        raise PermissionError("Path is outside allowed folders")

    def _safe_path(self, raw_path: Any) -> Path:
        target, _root = self._safe_path_with_root(raw_path)
        return target

    def _should_hide_file_entry(self, path: Path) -> bool:
        name = path.name.lower()
        if name in {"desktop.ini", "thumbs.db"} or name.startswith("~$"):
            return True
        if os.name != "nt":
            return name.startswith(".")
        try:
            import ctypes

            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs == 0xFFFFFFFF:
                return False
            hidden = 0x2
            system = 0x4
            return bool(attrs & (hidden | system))
        except Exception:
            return False

    def _is_allowed_app(self, path: str) -> bool:
        resolved = Path(path).expanduser().resolve()
        preset_paths = []
        for candidates in APP_PRESETS.values():
            for candidate in candidates:
                if "\\" not in candidate and "/" not in candidate:
                    found = self._find_on_path(candidate)
                    if found:
                        preset_paths.append(Path(found).resolve())
                    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / candidate
                    if system32.exists():
                        preset_paths.append(system32.resolve())
                elif Path(candidate).exists():
                    preset_paths.append(Path(candidate).resolve())
        if any(resolved == preset for preset in preset_paths):
            return True
        allowed_roots = [Path(folder).expanduser().resolve() for folder in self.config.allowed_folders]
        program_files = [Path(os.environ.get("ProgramFiles", "C:/Program Files")).resolve()]
        roots = allowed_roots + program_files
        return any(resolved == root or root in resolved.parents for root in roots)
