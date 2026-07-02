from __future__ import annotations

import queue
from datetime import datetime
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from remotectrl_agent.core.client import AgentClient
from remotectrl_agent.core.config import AgentConfig, load_config, save_config
from remotectrl_agent.core.handlers import CommandHandlers


class AgentApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RemoteCtrl Agent")
        self.geometry("760x520")
        self.minsize(700, 480)
        self.configure(bg="#f7f8fb")
        self.config_data = load_config()
        self.status_var = tk.StringVar(value="Not connected")
        self.session_var = tk.StringVar(value="Screen: idle | Webcam: idle | Activity: idle")
        self.keycapture_text = ""
        self.keycapture_window: tk.Toplevel | None = None
        self.activity_events: list[dict] = []
        self.activity_window: tk.Toplevel | None = None
        self.activity_listbox: tk.Listbox | None = None
        self.activity_poll_job: str | None = None
        self.activity_last_window = ""
        self.activity_mouse_listener = None
        self.activity_keyboard_listener = None
        self.activity_pressed_modifiers: set[str] = set()
        self.activity_text_buffer = ""
        self.ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.handlers = CommandHandlers(self.config_data, self.keycapture_provider)
        self.client = AgentClient(self.config_data, self.handlers, self.set_status_threadsafe, self.request_approval_threadsafe)
        self._build()
        self.refresh_session_status()
        self.after(200, self.process_ui_queue)
        if self.config_data.agent_token:
            self.client.start()

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f7f8fb")
        style.configure("Card.TFrame", background="#ffffff", relief="flat")
        style.configure("Title.TLabel", background="#f7f8fb", foreground="#111827", font=("Segoe UI", 20, "bold"))
        style.configure("Sub.TLabel", background="#f7f8fb", foreground="#667085", font=("Segoe UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#111827", font=("Segoe UI", 10))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        outer = ttk.Frame(self, padding=24)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="RemoteCtrl Agent", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Visible, consent-first Windows agent for LAN remote support demos.", style="Sub.TLabel").pack(anchor="w", pady=(4, 20))

        card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        card.pack(fill="x")

        self.server_entry = self._field(card, "Gateway URL", self.config_data.server_url)
        self.name_entry = self._field(card, "Agent name", self.config_data.agent_name)
        self.token_entry = self._field(card, "Enrollment token", "")

        controls = ttk.Frame(card, style="Card.TFrame")
        controls.pack(fill="x", pady=(16, 0))
        ttk.Button(controls, text="Enroll", style="Accent.TButton", command=self.enroll).pack(side="left")
        ttk.Button(controls, text="Connect", command=self.connect).pack(side="left", padx=8)
        ttk.Button(controls, text="Pause / Resume", command=self.toggle_pause).pack(side="left", padx=8)
        ttk.Button(controls, text="Allow folder for Web Files", command=self.add_folder).pack(side="left", padx=8)
        ttk.Button(controls, text="Reset session approvals", command=self.reset_session_approvals).pack(side="left", padx=8)

        status_card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        status_card.pack(fill="both", expand=True, pady=(18, 0))
        ttk.Label(status_card, text="Status", style="Card.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(status_card, textvariable=self.status_var, style="Card.TLabel").pack(anchor="w", pady=(6, 14))
        self.identity_var = tk.StringVar(value=self.identity_text())
        ttk.Label(status_card, textvariable=self.identity_var, style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(status_card, textvariable=self.session_var, style="Card.TLabel").pack(anchor="w", pady=(0, 14))

        safety = ttk.Frame(status_card, style="Card.TFrame")
        safety.pack(fill="x", pady=(0, 14))
        for text in [
            "Local approval required by default for every remote action",
            "Power commands default to dry-run mode",
            "Activity capture is visible, approved, and session-scoped",
            "Allowed folders are the only places Web Files can browse or download",
        ]:
            ttk.Label(safety, text=f"[ok] {text}", style="Card.TLabel").pack(anchor="w", pady=2)

        ttk.Label(status_card, text="Allowed folders for Web Files", style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(status_card, text="The dashboard can browse/download only these folders, after local approval.", style="Card.TLabel").pack(anchor="w", pady=(4, 0))
        self.folder_list = tk.Listbox(status_card, height=7, borderwidth=0, highlightthickness=1, highlightbackground="#e5e7eb")
        self.folder_list.pack(fill="x", pady=(8, 8))
        ttk.Button(status_card, text="Remove selected allowed folder", command=self.remove_selected_folder).pack(anchor="w", pady=(0, 12))
        ttk.Label(status_card, text="Agent log", style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.log_box = tk.Text(status_card, height=6, borderwidth=0, highlightthickness=1, highlightbackground="#e5e7eb")
        self.log_box.configure(state="disabled")
        self.log_box.pack(fill="both", expand=True, pady=(8, 0))
        self.refresh_folders()
        self.append_log("Agent UI ready")

    def _field(self, parent, label: str, value: str) -> ttk.Entry:
        ttk.Label(parent, text=label, style="Card.TLabel").pack(anchor="w")
        entry = ttk.Entry(parent)
        entry.insert(0, value)
        entry.pack(fill="x", pady=(4, 12))
        return entry

    def enroll(self) -> None:
        self.config_data.server_url = self.server_entry.get().strip()
        self.config_data.agent_name = self.name_entry.get().strip() or "RemoteCtrl Agent"
        token = self.token_entry.get().strip()
        if not token:
            messagebox.showerror("Enrollment token required", "Paste an enrollment token from the dashboard.")
            return
        try:
            self.client.enroll(token)
            self.identity_var.set(self.identity_text())
            messagebox.showinfo("Enrolled", "Agent enrolled successfully.")
            self.client.start()
        except Exception as exc:
            messagebox.showerror("Enrollment failed", str(exc))

    def connect(self) -> None:
        self.config_data.server_url = self.server_entry.get().strip()
        self.config_data.agent_name = self.name_entry.get().strip()
        save_config(self.config_data)
        self.client.start()

    def toggle_pause(self) -> None:
        self.config_data.paused = not self.config_data.paused
        save_config(self.config_data)
        self.status_var.set("Paused" if self.config_data.paused else "Resuming")
        if not self.config_data.paused:
            self.client.start()

    def add_folder(self) -> None:
        folder = filedialog.askdirectory(title="Allow this folder for Web Files")
        if folder and folder not in self.config_data.allowed_folders:
            self.config_data.allowed_folders.append(folder)
            save_config(self.config_data)
            self.refresh_folders()
            self.append_log(f"Allowed folder added: {folder}")

    def remove_selected_folder(self) -> None:
        selection = list(self.folder_list.curselection())
        if not selection:
            messagebox.showinfo("No folder selected", "Select a folder to remove from Web Files access.")
            return
        index = selection[0]
        if 0 <= index < len(self.config_data.allowed_folders):
            removed = self.config_data.allowed_folders.pop(index)
            save_config(self.config_data)
            self.refresh_folders()
            self.append_log(f"Allowed folder removed: {removed}")

    def refresh_folders(self) -> None:
        self.folder_list.delete(0, tk.END)
        for folder in self.config_data.allowed_folders:
            self.folder_list.insert(tk.END, folder)

    def set_status_threadsafe(self, status: str) -> None:
        self.ui_queue.put(("status", status))

    def request_approval_threadsafe(self, message: dict) -> dict:
        response: queue.Queue[dict] = queue.Queue(maxsize=1)
        self.ui_queue.put(("approval", (message, response)))
        try:
            return response.get(timeout=90)
        except queue.Empty:
            command_type = message.get("command_type", "remote action")
            self.ui_queue.put(("log", f"Approval timed out: {command_type}"))
            return {"approved": False, "approval_mode": "prompt_timeout", "policy_scope": "single_command"}

    def process_ui_queue(self) -> None:
        while True:
            try:
                kind, payload = self.ui_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status_var.set(str(payload))
                self.append_log(str(payload))
            elif kind == "approval":
                message, response = payload
                command_type = message.get("command_type", "remote action")
                decision = self.show_approval_dialog(message)
                response.put(decision)
                self.append_log(f"Approval {'granted' if decision.get('approved') else 'denied'}: {command_type}")
            elif kind == "log":
                self.append_log(str(payload))
        self.refresh_session_status()
        self.after(200, self.process_ui_queue)

    def present_for_approval(self) -> None:
        try:
            if self.state() == "iconic":
                self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.focus_force()
            self.after(700, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass

    def center_child_window(self, win: tk.Toplevel) -> None:
        self.update_idletasks()
        win.update_idletasks()
        parent_width = max(self.winfo_width(), 1)
        parent_height = max(self.winfo_height(), 1)
        child_width = win.winfo_width()
        child_height = win.winfo_height()
        if parent_width <= 1 or parent_height <= 1:
            x = max(0, (win.winfo_screenwidth() - child_width) // 2)
            y = max(0, (win.winfo_screenheight() - child_height) // 2)
        else:
            x = self.winfo_rootx() + max(0, (parent_width - child_width) // 2)
            y = self.winfo_rooty() + max(0, (parent_height - child_height) // 2)
        win.geometry(f"+{x}+{y}")

    def show_approval_dialog(self, message: dict) -> dict:
        command_type = str(message.get("command_type", "remote action"))
        payload = message.get("payload") or {}
        decision = {"approved": False, "approval_mode": "prompt_once", "policy_scope": "single_command"}
        self.present_for_approval()
        self.append_log(f"Approval prompt shown: {command_type}")
        win = tk.Toplevel(self)
        win.title("RemoteCtrl Approval")
        win.geometry("560x340")
        win.transient(self)
        win.grab_set()
        win.configure(bg="#ffffff")
        win.attributes("-topmost", True)
        ttk.Label(win, text="Allow remote action?", font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=20, pady=(18, 6))
        ttk.Label(win, text=command_type, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=20)
        ttk.Label(win, text=self.approval_summary(payload), wraplength=510).pack(anchor="w", padx=20, pady=(10, 14))
        ttk.Label(win, text="This decision will be audited by the gateway.").pack(anchor="w", padx=20, pady=(0, 14))
        countdown = tk.StringVar(value="Auto-deny in 90s if no response.")
        ttk.Label(win, textvariable=countdown, foreground="#b42318").pack(anchor="w", padx=20, pady=(0, 8))
        buttons = ttk.Frame(win)
        buttons.pack(fill="x", padx=20, pady=(8, 18))
        closed = {"done": False}
        seconds_left = {"value": 90}

        def choose(approved: bool, scope: str, mode: str = "prompt_once") -> None:
            if closed["done"]:
                return
            closed["done"] = True
            decision["approved"] = approved
            decision["approval_mode"] = mode
            decision["policy_scope"] = scope
            try:
                win.grab_release()
            except tk.TclError:
                pass
            try:
                win.destroy()
            except tk.TclError:
                pass

        def tick() -> None:
            if closed["done"]:
                return
            seconds_left["value"] -= 1
            if seconds_left["value"] <= 0:
                choose(False, "single_command", "prompt_timeout")
                return
            countdown.set(f"Auto-deny in {seconds_left['value']}s if no response.")
            win.after(1000, tick)

        ttk.Button(buttons, text="Deny", command=lambda: choose(False, "single_command")).pack(side="left")
        ttk.Button(buttons, text="Allow once", command=lambda: choose(True, "single_command")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Allow this action for this session", command=lambda: choose(True, "current_session")).pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", lambda: choose(False, "single_command"))
        self.center_child_window(win)
        win.after(50, lambda: (win.lift(), win.focus_force()))
        win.after(700, lambda: win.attributes("-topmost", False))
        win.after(1000, tick)
        self.wait_window(win)
        return decision

    def approval_summary(self, payload: dict) -> str:
        if not payload:
            return "Payload: none"
        safe_keys = ["pid", "path", "preset", "fps", "quality", "camera_index", "action"]
        parts = [f"{key}: {payload[key]}" for key in safe_keys if key in payload]
        return "Payload: " + (", ".join(parts) if parts else "provided")

    def reset_session_approvals(self) -> None:
        self.client.reset_session_approvals()
        self.append_log("Session approvals reset")

    def refresh_session_status(self) -> None:
        screen = "running" if "screen" in self.client.active_streams else "idle"
        webcam = "running" if "webcam" in self.client.active_streams else "idle"
        activity = "running" if self.activity_window and self.activity_window.winfo_exists() else "idle"
        self.session_var.set(f"Screen: {screen} | Webcam: {webcam} | Activity: {activity}")
    def identity_text(self) -> str:
        if not self.config_data.agent_id:
            return "Not enrolled yet"
        return f"Agent ID: {self.config_data.agent_id}"

    def append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{timestamp}] {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def keycapture_provider(self, action: str):
        if action == "start":
            already_running = self.client.keycapture_active
            self.after(0, self.open_keycapture_window)
            return "already_running" if already_running else "started"
        if action == "stop":
            self.after(0, self.close_keycapture_window)
            return "stopped"
        if action == "export":
            return self.keycapture_text
        if action == "activity_start":
            already_running = self.activity_window is not None and self.activity_window.winfo_exists()
            self.after(0, self.open_activity_window)
            return "already_running" if already_running else "started"
        if action == "activity_stop":
            self.after(0, self.close_activity_window)
            return "stopped"
        if action == "activity_export":
            return list(self.activity_events)
        raise ValueError(action)

    def open_keycapture_window(self) -> None:
        if self.keycapture_window and self.keycapture_window.winfo_exists():
            self.keycapture_window.lift()
            return
        win = tk.Toplevel(self)
        win.title("RemoteCtrl Visible Key Capture")
        win.geometry("560x360")
        ttk.Label(win, text="Visible key-capture demo session", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ttk.Label(win, text="Only text typed inside this window is captured. This is not a background keylogger.").pack(anchor="w", padx=16)
        text = tk.Text(win, height=10)
        text.pack(fill="both", expand=True, padx=16, pady=16)

        def sync(_event=None) -> None:
            self.keycapture_text = text.get("1.0", "end-1c")

        text.bind("<KeyRelease>", sync)
        win.protocol("WM_DELETE_WINDOW", self.close_keycapture_window)
        self.keycapture_window = win
        self.client.keycapture_active = True
        self.refresh_session_status()

    def close_keycapture_window(self) -> None:
        if self.keycapture_window and self.keycapture_window.winfo_exists():
            self.keycapture_window.destroy()
        self.client.keycapture_active = False
        self.refresh_session_status()


    def open_activity_window(self) -> None:
        if self.activity_window and self.activity_window.winfo_exists():
            self.activity_window.lift()
            return
        win = tk.Toplevel(self)
        win.title("RemoteCtrl Activity Capture Active")
        win.geometry("700x420")
        win.attributes("-topmost", True)
        ttk.Label(win, text="Activity Capture Active", font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        ttk.Label(
            win,
            text="Approved visible session: records active window changes, mouse clicks, keyboard shortcuts, and typed text until stopped.",
            wraplength=650,
        ).pack(anchor="w", padx=16)
        ttk.Button(win, text="Stop local capture now", command=self.close_activity_window).pack(anchor="w", padx=16, pady=(10, 6))
        self.activity_listbox = tk.Listbox(win, height=13, borderwidth=0, highlightthickness=1, highlightbackground="#e5e7eb")
        self.activity_listbox.pack(fill="both", expand=True, padx=16, pady=(8, 16))
        win.protocol("WM_DELETE_WINDOW", self.close_activity_window)
        self.activity_window = win
        self.client.activity_active = True
        self.activity_last_window = ""
        self.activity_text_buffer = ""
        self.activity_pressed_modifiers.clear()
        self._append_activity_event("session.started", {})
        self.start_mouse_listener()
        self.start_keyboard_listener()
        self.poll_activity_window()
        self.refresh_session_status()

    def close_activity_window(self) -> None:
        if self.activity_poll_job:
            try:
                self.after_cancel(self.activity_poll_job)
            except Exception:
                pass
            self.activity_poll_job = None
        if self.activity_mouse_listener:
            try:
                self.activity_mouse_listener.stop()
            except Exception:
                pass
            self.activity_mouse_listener = None
        if self.activity_keyboard_listener:
            try:
                self.activity_keyboard_listener.stop()
            except Exception:
                pass
            self.activity_keyboard_listener = None
        self.activity_pressed_modifiers.clear()
        self._append_activity_event("session.stopped", {})
        if self.activity_window and self.activity_window.winfo_exists():
            self.activity_window.destroy()
        self.activity_window = None
        self.client.activity_active = False
        self.refresh_session_status()

    def sync_activity_text(self, _event=None) -> None:
        # Kept for backward compatibility with older queued UI events. Activity capture no longer uses a demo text box.
        return

    def poll_activity_window(self) -> None:
        if not self.activity_window or not self.activity_window.winfo_exists():
            return
        current = self.current_active_window()
        label = f"{current.get('process', 'unknown')} | {current.get('title', '')}"
        if label and label != self.activity_last_window:
            self.activity_last_window = label
            self._append_activity_event("active_window.changed", current)
        self.activity_poll_job = self.after(1000, self.poll_activity_window)

    def current_active_window(self) -> dict:
        try:
            import ctypes
            import psutil

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process_name = psutil.Process(pid.value).name() if pid.value else "unknown"
            return {"pid": int(pid.value), "process": process_name, "title": buffer.value}
        except Exception as exc:
            return {"process": "unknown", "title": "unavailable", "error": str(exc)}

    def start_mouse_listener(self) -> None:
        try:
            from pynput import mouse
        except Exception as exc:
            self._append_activity_event("mouse.listener_unavailable", {"error": str(exc)})
            return

        def on_click(x, y, button, pressed):
            if pressed:
                self.after(0, lambda: self._append_activity_event("mouse.clicked", {"x": x, "y": y, "button": str(button), "window": self.current_active_window()}))

        self.activity_mouse_listener = mouse.Listener(on_click=on_click)
        self.activity_mouse_listener.daemon = True
        self.activity_mouse_listener.start()

    def start_keyboard_listener(self) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:
            self._append_activity_event("keyboard.listener_unavailable", {"error": str(exc)})
            return

        modifier_names = {
            keyboard.Key.ctrl_l: "Ctrl",
            keyboard.Key.ctrl_r: "Ctrl",
            keyboard.Key.alt_l: "Alt",
            keyboard.Key.alt_r: "Alt",
            keyboard.Key.shift_l: "Shift",
            keyboard.Key.shift_r: "Shift",
            keyboard.Key.cmd: "Win",
            keyboard.Key.cmd_l: "Win",
            keyboard.Key.cmd_r: "Win",
        }

        def display_key(key) -> str:
            char = getattr(key, "char", None)
            if char:
                if len(char) == 1 and ord(char) < 32 and "Ctrl" in self.activity_pressed_modifiers:
                    return chr(ord(char) + 96).upper()
                return char.upper() if "Shift" in self.activity_pressed_modifiers and len(char) == 1 else char
            name = getattr(key, "name", None) or str(key).replace("Key.", "")
            return str(name).replace("_", " ").title()

        def append_text(value: str) -> None:
            self.activity_text_buffer += value
            self._append_activity_event("keyboard.text", {"text": self.activity_text_buffer[-160:], "window": self.current_active_window()})

        def on_press(key):
            modifier = modifier_names.get(key)
            if modifier:
                self.activity_pressed_modifiers.add(modifier)
                return
            label = display_key(key)
            if self.activity_pressed_modifiers:
                combo = " + ".join(sorted(self.activity_pressed_modifiers) + [label.upper() if len(label) == 1 else label])
                self.after(0, lambda: self._append_activity_event("keyboard.shortcut", {"keys": combo, "window": self.current_active_window()}))
                return
            if len(label) == 1 and ord(label) >= 32:
                self.after(0, lambda value=label: append_text(value))
                return
            if label == "Space":
                self.after(0, lambda: append_text(" "))
                return
            if label == "Backspace":
                self.activity_text_buffer = self.activity_text_buffer[:-1]
                self.after(0, lambda: self._append_activity_event("keyboard.text", {"text": self.activity_text_buffer[-160:], "key": "Backspace", "window": self.current_active_window()}))
                return
            if label == "Enter":
                self.after(0, lambda: self._append_activity_event("keyboard.key", {"key": "Enter", "text": self.activity_text_buffer[-160:], "window": self.current_active_window()}))
                return
            self.after(0, lambda: self._append_activity_event("keyboard.key", {"key": label, "window": self.current_active_window()}))

        def on_release(key):
            modifier = modifier_names.get(key)
            if modifier:
                self.activity_pressed_modifiers.discard(modifier)

        self.activity_keyboard_listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        self.activity_keyboard_listener.daemon = True
        self.activity_keyboard_listener.start()

    def _append_activity_event(self, event_type: str, detail: dict) -> None:
        event = {"time": datetime.now().isoformat(timespec="seconds"), "type": event_type, "detail": detail}
        self.activity_events.append(event)
        self.activity_events = self.activity_events[-1000:]
        if self.activity_listbox and self.activity_listbox.winfo_exists():
            self.activity_listbox.insert(tk.END, f"{event['time']}  {event_type}  {detail}")
            self.activity_listbox.see(tk.END)


def main() -> None:
    app = AgentApp()
    app.mainloop()
