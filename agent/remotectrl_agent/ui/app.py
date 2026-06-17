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
        self.keycapture_text = ""
        self.keycapture_window: tk.Toplevel | None = None
        self.ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.handlers = CommandHandlers(self.config_data, self.keycapture_provider)
        self.client = AgentClient(self.config_data, self.handlers, self.set_status_threadsafe, self.request_approval_threadsafe)
        self._build()
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
        ttk.Button(controls, text="Add Allowed Folder", command=self.add_folder).pack(side="left", padx=8)

        status_card = ttk.Frame(outer, style="Card.TFrame", padding=20)
        status_card.pack(fill="both", expand=True, pady=(18, 0))
        ttk.Label(status_card, text="Status", style="Card.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(status_card, textvariable=self.status_var, style="Card.TLabel").pack(anchor="w", pady=(6, 14))
        self.identity_var = tk.StringVar(value=self.identity_text())
        ttk.Label(status_card, textvariable=self.identity_var, style="Card.TLabel").pack(anchor="w", pady=(0, 14))

        safety = ttk.Frame(status_card, style="Card.TFrame")
        safety.pack(fill="x", pady=(0, 14))
        for text in [
            "Local approval required for screen, webcam, files, key capture, and power",
            "Power commands default to dry-run mode",
            "Key capture is visible and limited to the demo typing window",
        ]:
            ttk.Label(safety, text=f"[ok] {text}", style="Card.TLabel").pack(anchor="w", pady=2)

        ttk.Label(status_card, text="Allowed folders", style="Card.TLabel", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.folder_list = tk.Listbox(status_card, height=7, borderwidth=0, highlightthickness=1, highlightbackground="#e5e7eb")
        self.folder_list.pack(fill="x", pady=(8, 12))
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
        folder = filedialog.askdirectory()
        if folder and folder not in self.config_data.allowed_folders:
            self.config_data.allowed_folders.append(folder)
            save_config(self.config_data)
            self.refresh_folders()

    def refresh_folders(self) -> None:
        self.folder_list.delete(0, tk.END)
        for folder in self.config_data.allowed_folders:
            self.folder_list.insert(tk.END, folder)

    def set_status_threadsafe(self, status: str) -> None:
        self.ui_queue.put(("status", status))

    def request_approval_threadsafe(self, message: dict) -> bool:
        response: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.ui_queue.put(("approval", (message, response)))
        return response.get()

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
                approved = messagebox.askyesno(
                    "RemoteCtrl Approval",
                    f"Allow remote action?\n\n{command_type}\n\nThis action will be audited by the gateway.",
                )
                response.put(approved)
                self.append_log(f"Approval {'granted' if approved else 'denied'}: {command_type}")
        self.after(200, self.process_ui_queue)

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
            self.after(0, self.open_keycapture_window)
            return None
        if action == "stop":
            self.after(0, self.close_keycapture_window)
            return None
        if action == "export":
            return self.keycapture_text
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
        self.keycapture_window = win

    def close_keycapture_window(self) -> None:
        if self.keycapture_window and self.keycapture_window.winfo_exists():
            self.keycapture_window.destroy()


def main() -> None:
    app = AgentApp()
    app.mainloop()
