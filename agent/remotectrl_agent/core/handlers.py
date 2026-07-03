from __future__ import annotations

import base64
import csv
import io
import os
import subprocess
import sys
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
    "notepad": ["notepad", "untitled"],
    "calculator": ["calculator"],
    "paint": ["paint"],
    "explorer": ["file explorer", "this pc", "downloads", "documents"],
    "chrome": ["chrome"],
    "brave": ["brave"],
}


class CommandHandlers:
    def __init__(self, config: AgentConfig, keycapture_provider):
        self.config = config
        self.keycapture_provider = keycapture_provider

    def handle(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        routes = {
            "process.list": self.process_list,
            "process.kill": self.process_kill,
            "app.list": self.app_list,
            "app.start": self.app_start,
            "app.stop": self.process_kill,
            "screen.screenshot": self.screen_screenshot,
            "screen.live.start": self.screen_screenshot,
            "files.roots": self.files_roots,
            "files.list": self.files_list,
            "files.download": self.files_download,
            "webcam.list": self.webcam_list,
            "webcam.snapshot": self.webcam_snapshot,
            "webcam.live.start": self.webcam_snapshot,
            "power.shutdown": self.power_shutdown,
            "power.restart": self.power_restart,
            "power.logout": self.power_logout,
            "power.sleep": self.power_sleep,
            "power.status": self.power_status,
            "keycapture.start": self.keycapture_start,
            "keycapture.stop": self.keycapture_stop,
            "keycapture.export": self.keycapture_export,
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

            proc = psutil.Process(pid)
            name = proc.name().lower()
            if name in SYSTEM_PROCESS_NAMES:
                raise PermissionError(f"Refusing to stop protected process: {name}")
            proc.terminate()
            return {"pid": pid, "name": name, "status": "terminate_requested"}
        except ImportError:
            raise RuntimeError("psutil is required for guarded process kill")

    def app_list(self, _payload: dict[str, Any]) -> dict[str, Any]:
        if os.name != "nt":
            return {"items": [], "count": 0, "source": "unsupported"}
        try:
            items = self._visible_windows()
            return {"items": items, "count": len(items), "source": "win32"}
        except Exception as exc:
            fallback = self._app_list_powershell()
            fallback["fallback_error"] = str(exc)
            return fallback

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
        restore_after = not payload.get("_screen_hidden")
        if restore_after:
            self.keycapture_provider("screen_capture_hide")
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
            if restore_after:
                self.keycapture_provider("screen_capture_restore")

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
        target = self._safe_path(raw_path)
        entries = []
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            try:
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
        return {"path": str(target), "entries": entries[:500]}

    def files_download(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self._safe_path(payload.get("path"))
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

    def webcam_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is unavailable in this Agent. Install agent/requirements.txt or run the latest packaged Agent EXE with OpenCV bundled.") from exc
        camera_index = int(payload.get("camera_index", 0))
        cap = cv2.VideoCapture(camera_index)
        try:
            if not cap.isOpened():
                raise RuntimeError(f"Camera {camera_index} is not available")
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Unable to read webcam frame")
            quality = min(max(int(payload.get("quality", 75)), 35), 90)
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                raise RuntimeError("Unable to encode webcam frame")
            return {"mime": "image/jpeg", "image": base64.b64encode(encoded.tobytes()).decode(), "camera_index": camera_index}
        finally:
            cap.release()

    def webcam_list(self, _payload: dict[str, Any]) -> dict[str, Any]:
        packaged = bool(getattr(sys, "frozen", False))
        try:
            import cv2
        except ImportError as exc:
            return {
                "items": [],
                "count": 0,
                "opencv_available": False,
                "cv2_available": False,
                "agent_packaged": packaged,
                "error": "Packaged Agent EXE is missing OpenCV. Download and run the latest RemoteCtrlAgent.exe build.",
                "packaging_error": "cv2 import failed inside packaged EXE" if packaged else "Source-run Agent is missing cv2; packaged EXE includes it.",
                "import_error": str(exc),
            }
        items = []
        errors = []
        for index in range(4):
            cap = cv2.VideoCapture(index)
            try:
                if cap.isOpened():
                    items.append({"index": index, "label": f"Camera {index}"})
            except Exception as exc:
                errors.append(f"Camera {index}: {exc}")
            finally:
                cap.release()
        result = {"items": items, "count": len(items), "opencv_available": True, "cv2_available": True, "agent_packaged": packaged, "cv2_version": getattr(cv2, "__version__", "unknown"), "packaging_error": None}
        if not items:
            result["error"] = "No camera was detected on the Windows agent"
        if errors:
            result["diagnostics"] = errors
        return result

    def power_shutdown(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/s", "/t", "5"], "shutdown")

    def power_restart(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/r", "/t", "5"], "restart")

    def power_logout(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/l"], "logout")

    def power_sleep(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], "sleep")

    def power_status(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status: dict[str, Any] = {
            "action": "status",
            "status": "ok",
            "dry_run_power": self.config.dry_run_power,
            "supported_actions": ["shutdown", "restart", "logout", "sleep"],
            "system_uptime_seconds": None,
            "battery_percent": None,
            "battery_plugged": None,
            "temperature_celsius": None,
        }
        try:
            import psutil

            status["system_uptime_seconds"] = max(0, int(time.time() - psutil.boot_time()))
            battery = psutil.sensors_battery()
            if battery is not None:
                status["battery_percent"] = round(float(battery.percent), 1)
                status["battery_plugged"] = bool(battery.power_plugged)
            try:
                temperatures = psutil.sensors_temperatures()
            except Exception:
                temperatures = {}
            for sensors in temperatures.values():
                for sensor in sensors:
                    current = getattr(sensor, "current", None)
                    if current is not None:
                        status["temperature_celsius"] = round(float(current), 1)
                        raise StopIteration
        except StopIteration:
            pass
        except Exception as exc:
            status["diagnostic_error"] = str(exc)
        return status

    def keycapture_start(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status = self.keycapture_provider("start") or "started"
        return {"status": status, "mode": "visible_demo_window"}

    def keycapture_stop(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status = self.keycapture_provider("stop") or "stopped"
        return {"status": status}

    def keycapture_export(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"text": self.keycapture_provider("export"), "mode": "visible_demo_window"}

    def activity_start(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status = self.keycapture_provider("activity_start") or "started"
        return {"status": status, "mode": "visible_activity_session"}

    def activity_stop(self, _payload: dict[str, Any]) -> dict[str, Any]:
        status = self.keycapture_provider("activity_stop") or "stopped"
        return {"status": status}

    def activity_export(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"events": self.keycapture_provider("activity_export"), "mode": "visible_activity_session"}

    def _power(self, args: list[str], action: str) -> dict[str, Any]:
        if self.config.dry_run_power:
            return {"action": action, "status": "dry_run", "message": "Real power actions are disabled on the Agent."}
        subprocess.Popen(args)
        return {"action": action, "status": "requested", "message": f"{action} requested on the Agent."}

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
        for item in self.app_list({}).get("items", []):
            name = self._normalize_process_name(str(item.get("name") or ""))
            title = str(item.get("title") or "").lower()
            if name in target_names or any(alias in title for alias in target_titles):
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

    def _safe_path(self, raw_path: Any) -> Path:
        if not raw_path:
            raise ValueError("path is required")
        target = Path(str(raw_path)).expanduser().resolve()
        allowed = [Path(folder).expanduser().resolve() for folder in self.config.allowed_folders]
        if not any(target == root or root in target.parents for root in allowed):
            raise PermissionError("Path is outside allowed folders")
        return target

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
