from __future__ import annotations

import base64
import csv
import io
import os
import platform
import subprocess
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
            "files.list": self.files_list,
            "files.download": self.files_download,
            "webcam.list": self.webcam_list,
            "webcam.snapshot": self.webcam_snapshot,
            "webcam.live.start": self.webcam_snapshot,
            "power.shutdown": self.power_shutdown,
            "power.restart": self.power_restart,
            "power.logout": self.power_logout,
            "keycapture.start": self.keycapture_start,
            "keycapture.stop": self.keycapture_stop,
            "keycapture.export": self.keycapture_export,
        }
        handler = routes.get(command_type)
        if not handler:
            raise ValueError(f"Unsupported command: {command_type}")
        return handler(payload)

    def process_list(self, _payload: dict[str, Any]) -> dict[str, Any]:
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
            return {"items": items[:250], "count": len(items), "source": "psutil"}
        except Exception:
            output = subprocess.check_output(["tasklist", "/fo", "csv"], text=True, errors="ignore")
            rows = list(csv.DictReader(io.StringIO(output)))
            return {
                "items": [{"pid": row.get("PID"), "name": row.get("Image Name"), "status": "unknown"} for row in rows],
                "count": len(rows),
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
        if platform.system() != "Windows":
            return {"items": [], "count": 0, "source": "unsupported"}
        ps = (
            "Get-Process | Where-Object {$_.MainWindowTitle} | "
            "Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json"
        )
        try:
            output = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps], text=True, errors="ignore")
            import json

            parsed = json.loads(output) if output.strip() else []
            if isinstance(parsed, dict):
                parsed = [parsed]
            items = [
                {"pid": item["Id"], "name": item["ProcessName"], "title": item["MainWindowTitle"]}
                for item in parsed
            ]
            return {"items": items, "count": len(items), "source": "powershell"}
        except Exception as exc:
            return {"items": [], "count": 0, "source": "powershell", "error": str(exc)}

    def app_start(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self._resolve_app_path(payload)
        if not path:
            raise ValueError("path or preset is required")
        if not self._is_allowed_app(path):
            raise PermissionError("App path is not allowlisted for demo start")
        subprocess.Popen([path], close_fds=True)
        return {"path": path, "preset": payload.get("preset"), "status": "started"}

    def screen_screenshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        from PIL import ImageGrab

        quality = min(max(int(payload.get("quality", 75)), 35), 90)
        image = ImageGrab.grab()
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality)
        return {
            "mime": "image/jpeg",
            "image": base64.b64encode(buf.getvalue()).decode(),
            "width": image.width,
            "height": image.height,
        }

    def files_list(self, payload: dict[str, Any]) -> dict[str, Any]:
        target = self._safe_path(payload.get("path") or self.config.allowed_folders[0])
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
            raise RuntimeError("opencv-python is required for webcam snapshot") from exc
        camera_index = int(payload.get("camera_index", 0))
        cap = cv2.VideoCapture(camera_index)
        try:
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError("Unable to read webcam frame")
            quality = min(max(int(payload.get("quality", 75)), 35), 90)
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                raise RuntimeError("Unable to encode webcam frame")
            return {"mime": "image/jpeg", "image": base64.b64encode(encoded.tobytes()).decode()}
        finally:
            cap.release()

    def webcam_list(self, _payload: dict[str, Any]) -> dict[str, Any]:
        try:
            import cv2
        except ImportError:
            return {"items": [], "count": 0, "error": "opencv-python is not installed"}
        items = []
        for index in range(4):
            cap = cv2.VideoCapture(index)
            try:
                if cap.isOpened():
                    items.append({"index": index, "label": f"Camera {index}"})
            finally:
                cap.release()
        return {"items": items, "count": len(items)}

    def power_shutdown(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/s", "/t", "5"], "shutdown")

    def power_restart(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/r", "/t", "5"], "restart")

    def power_logout(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return self._power(["shutdown", "/l"], "logout")

    def keycapture_start(self, _payload: dict[str, Any]) -> dict[str, Any]:
        self.keycapture_provider("start")
        return {"status": "started", "mode": "visible_demo_window"}

    def keycapture_stop(self, _payload: dict[str, Any]) -> dict[str, Any]:
        self.keycapture_provider("stop")
        return {"status": "stopped"}

    def keycapture_export(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"text": self.keycapture_provider("export"), "mode": "visible_demo_window"}

    def _power(self, args: list[str], action: str) -> dict[str, Any]:
        if self.config.dry_run_power:
            return {"action": action, "status": "dry_run", "message": "Set dry_run_power=false to execute."}
        subprocess.Popen(args)
        return {"action": action, "status": "requested"}

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
