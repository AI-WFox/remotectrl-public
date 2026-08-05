from __future__ import annotations

import ctypes
import threading
from collections import deque
from datetime import datetime
from typing import Any, Callable
from uuid import uuid4


class ActivityCapture:
    """Visible, session-scoped activity collection owned by the Agent user."""

    def __init__(self, emit: Callable[[str, dict[str, Any]], None]) -> None:
        self.emit = emit
        self.events: deque[dict[str, Any]] = deque(maxlen=1000)
        self.mouse_listener = None
        self.keyboard_listener = None
        self.window_timer: threading.Timer | None = None
        self.active = False
        self._modifiers: set[str] = set()
        self._typed = ""
        self._typed_window: dict[str, Any] | None = None
        self._typed_segment: str | None = None
        self._last_window = ""
        self._lock = threading.RLock()

    def start(self) -> str:
        with self._lock:
            if self.active:
                return "already_running"
            self.active = True
            self._typed = ""
            self._typed_window = None
            self._typed_segment = None
            self._last_window = ""
        self._record("session.started", {})
        self._start_mouse()
        self._start_keyboard()
        self._poll_window()
        return "started"

    def stop(self) -> str:
        self._flush_text("session_stopped")
        with self._lock:
            if not self.active:
                return "not_running"
            self.active = False
            listeners = (self.mouse_listener, self.keyboard_listener)
            self.mouse_listener = self.keyboard_listener = None
            timers = (self.window_timer,)
            self.window_timer = None
            self._modifiers.clear()
        for timer in timers:
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

    @staticmethod
    def _window_signature(window: dict[str, Any]) -> str:
        return f"{window.get('pid')}|{window.get('process')}|{window.get('title')}"

    def _append_text(self, value: str, window: dict[str, Any]) -> None:
        flush_previous = False
        with self._lock:
            if not self.active:
                return
            if self._typed and self._typed_window and self._window_signature(window) != self._window_signature(self._typed_window):
                flush_previous = True
        if flush_previous:
            self._flush_text("window_changed")
        with self._lock:
            if not self.active:
                return
            if not self._typed_segment:
                self._typed_segment = str(uuid4())
            self._typed += value
            self._typed = self._typed[-160:]
            self._typed_window = window
        self._emit_draft()

    def _erase_text(self, window: dict[str, Any]) -> None:
        with self._lock:
            if not self.active or not self._typed_segment:
                return
            self._typed = self._typed[:-1]
            self._typed_window = window
        self._emit_draft()

    def _emit_draft(self) -> None:
        with self._lock:
            text = self._typed
            window = self._typed_window
            segment_id = self._typed_segment
        if segment_id:
            self.emit(
                "activity.event",
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "type": "keyboard.text.draft",
                    "detail": {"segment_id": segment_id, "text": text, "window": window or {}},
                },
            )

    def _flush_text(self, boundary: str) -> None:
        with self._lock:
            text = self._typed
            window = self._typed_window
            segment_id = self._typed_segment
            self._typed = ""
            self._typed_window = None
            self._typed_segment = None
        if text:
            self._record(
                "keyboard.text",
                {"segment_id": segment_id, "text": text, "window": window or {}, "boundary": boundary},
            )

    def _poll_window(self) -> None:
        with self._lock:
            if not self.active:
                return
        window = self.active_window()
        signature = self._window_signature(window)
        with self._lock:
            changed = signature != self._last_window
            self._last_window = signature
        if changed:
            self._flush_text("window_changed")
            self._record("active_window.changed", window)
        timer = threading.Timer(1.0, self._poll_window)
        timer.daemon = True
        with self._lock:
            self.window_timer = timer
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
                self._flush_text("mouse_click")
                self._record("mouse.clicked", {"x": x, "y": y, "button": str(button), "window": self.active_window()})

        self.mouse_listener = mouse.Listener(on_click=on_click)
        self.mouse_listener.daemon = True
        self.mouse_listener.start()

    @staticmethod
    def _raw_printable_key(key: Any, shift: bool = False) -> str | None:
        """Translate the physical Windows key into text without using IME output."""
        vk = getattr(key, "vk", None)
        if isinstance(vk, int):
            if 0x41 <= vk <= 0x5A:
                value = chr(vk)
                return value if shift else value.lower()
            if 0x30 <= vk <= 0x39:
                normal = "0123456789"
                shifted = ")!@#$%^&*("
                return shifted[vk - 0x30] if shift else normal[vk - 0x30]
            punctuation = {
                0xBA: (";", ":"), 0xBB: ("=", "+"), 0xBC: (",", "<"),
                0xBD: ("-", "_"), 0xBE: (".", ">"), 0xBF: ("/", "?"),
                0xC0: (chr(96), "~"), 0xDB: ("[", "{"), 0xDC: ("\\", "|"),
                0xDD: ("]", "}"), 0xDE: ("'", '"'),
            }
            if vk in punctuation:
                return punctuation[vk][1 if shift else 0]
        char = getattr(key, "char", None)
        if isinstance(char, str) and len(char) == 1 and 32 <= ord(char) < 127:
            return char.upper() if shift and char.isalpha() else char
        return None

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

        def special_label(key) -> str:
            return str(getattr(key, "name", None) or str(key).replace("Key.", "")).replace("_", " ").title()

        def on_press(key) -> None:
            modifier = modifiers.get(key)
            if modifier:
                if modifier != "Shift":
                    self._flush_text("modifier")
                self._modifiers.add(modifier)
                return
            window = self.active_window()
            printable = self._raw_printable_key(key, shift="Shift" in self._modifiers)
            shortcut_modifiers = self._modifiers.intersection({"Ctrl", "Alt", "Win"})
            if shortcut_modifiers:
                self._flush_text("shortcut")
                value = printable or special_label(key)
                combo = " + ".join(sorted(self._modifiers) + [value.upper() if len(value) == 1 else value])
                self._record("keyboard.shortcut", {"keys": combo, "window": window})
            elif printable is not None:
                self._append_text(printable, window)
            else:
                value = special_label(key)
                if value == "Space":
                    self._append_text(" ", window)
                elif value == "Backspace":
                    self._erase_text(window)
                else:
                    self._flush_text(value.lower().replace(" ", "_"))
                    self._record("keyboard.key", {"key": value, "window": window})

        def on_release(key) -> None:
            modifier = modifiers.get(key)
            if modifier:
                self._modifiers.discard(modifier)

        self.keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.keyboard_listener.daemon = True
        self.keyboard_listener.start()