from __future__ import annotations

import ctypes
import threading
from collections import deque
from datetime import datetime
from typing import Any, Callable


class ActivityCapture:
    """Visible, session-scoped activity collection owned by the Agent user."""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self.emit = emit
        self.events: deque[dict[str, Any]] = deque(maxlen=1000)
        self.mouse_listener = None
        self.keyboard_listener = None
        self.timer: threading.Timer | None = None
        self.active = False
        self._modifiers: set[str] = set()
        self._typed = ""
        self._last_window = ""
        self._lock = threading.RLock()

    def start(self) -> str:
        with self._lock:
            if self.active:
                return "already_running"
            self.active = True
            self._typed = ""
            self._last_window = ""
        self._record("session.started", {})
        self._start_mouse()
        self._start_keyboard()
        self._poll_window()
        return "started"

    def stop(self) -> str:
        with self._lock:
            if not self.active:
                return "not_running"
            self.active = False
            listeners = (self.mouse_listener, self.keyboard_listener)
            self.mouse_listener = self.keyboard_listener = None
            timer = self.timer
            self.timer = None
            self._modifiers.clear()
        if timer:
            timer.cancel()
        for listener in listeners:
            if listener:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._record("session.stopped", {})
        return "stopped"

    def export(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.events)

    def _record(self, event_type: str, detail: dict[str, Any]) -> None:
        event = {"time": datetime.now().isoformat(timespec="seconds"), "type": event_type, "detail": detail}
        with self._lock:
            self.events.append(event)
        self.emit("activity.event", event)

    def _poll_window(self) -> None:
        with self._lock:
            if not self.active:
                return
        window = self.active_window()
        signature = f"{window.get('process')}|{window.get('title')}"
        with self._lock:
            changed = signature != self._last_window
            self._last_window = signature
        if changed:
            self._record("active_window.changed", window)
        timer = threading.Timer(1.0, self._poll_window)
        timer.daemon = True
        with self._lock:
            self.timer = timer
        timer.start()

    @staticmethod
    def active_window() -> dict[str, Any]:
        try:
            import psutil
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = psutil.Process(pid.value).name() if pid.value else "unknown"
            return {"pid": int(pid.value), "process": process, "title": buffer.value}
        except Exception as exc:
            return {"process": "unknown", "title": "unavailable", "error": str(exc)}

    def _start_mouse(self) -> None:
        try:
            from pynput import mouse
        except Exception as exc:
            self._record("mouse.listener_unavailable", {"error": str(exc)})
            return

        def on_click(x, y, button, pressed):
            if pressed:
                self._record("mouse.clicked", {"x": x, "y": y, "button": str(button), "window": self.active_window()})

        self.mouse_listener = mouse.Listener(on_click=on_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

    def _start_keyboard(self) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:
            self._record("keyboard.listener_unavailable", {"error": str(exc)})
            return
        modifiers = {
            keyboard.Key.ctrl_l: "Ctrl", keyboard.Key.ctrl_r: "Ctrl",
            keyboard.Key.alt_l: "Alt", keyboard.Key.alt_r: "Alt",
            keyboard.Key.shift_l: "Shift", keyboard.Key.shift_r: "Shift",
            keyboard.Key.cmd: "Win", keyboard.Key.cmd_l: "Win", keyboard.Key.cmd_r: "Win",
        }

        def label(key) -> str:
            char = getattr(key, "char", None)
            if char:
                if len(char) == 1 and ord(char) < 32 and "Ctrl" in self._modifiers:
                    return chr(ord(char) + 96).upper()
                return char.upper() if "Shift" in self._modifiers and len(char) == 1 else char
            return str(getattr(key, "name", None) or str(key).replace("Key.", "")).replace("_", " ").title()

        def on_press(key) -> None:
            modifier = modifiers.get(key)
            if modifier:
                self._modifiers.add(modifier)
                return
            value, window = label(key), self.active_window()
            if self._modifiers:
                combo = " + ".join(sorted(self._modifiers) + [value.upper() if len(value) == 1 else value])
                self._record("keyboard.shortcut", {"keys": combo, "window": window})
            elif len(value) == 1 and ord(value) >= 32:
                self._typed += value
                self._record("keyboard.text", {"text": self._typed[-160:], "window": window})
            elif value == "Space":
                self._typed += " "
                self._record("keyboard.text", {"text": self._typed[-160:], "window": window})
            elif value == "Backspace":
                self._typed = self._typed[:-1]
                self._record("keyboard.text", {"text": self._typed[-160:], "key": "Backspace", "window": window})
            elif value == "Enter":
                self._record("keyboard.key", {"key": "Enter", "text": self._typed[-160:], "window": window})
            else:
                self._record("keyboard.key", {"key": value, "window": window})

        def on_release(key) -> None:
            modifier = modifiers.get(key)
            if modifier:
                self._modifiers.discard(modifier)

        self.keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()