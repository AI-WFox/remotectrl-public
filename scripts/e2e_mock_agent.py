from __future__ import annotations

import asyncio
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path

import httpx
import websockets


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
BASE_URL = "http://127.0.0.1:8765"
WS_URL = "ws://127.0.0.1:8765/ws/agent"


def wait_for_gateway(timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = httpx.get(f"{BASE_URL}/api/health", timeout=1.0)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.25)
    raise RuntimeError("Gateway did not become ready")


async def run_agent_session_ready(agent_token: str, command_type: str, ready: threading.Event) -> dict:
    async with websockets.connect(f"{WS_URL}?token={agent_token}") as websocket:
        hello = json.loads(await websocket.recv())
        if hello.get("role") != "agent":
            raise AssertionError(f"Unexpected hello: {hello}")
        ready.set()
        routed = json.loads(await websocket.recv())
        if routed.get("command_type") != command_type:
            raise AssertionError(f"Unexpected command: {routed}")

        command_id = routed["command_id"]
        agent_id = routed["agent_id"]
        if routed.get("requires_approval"):
            await websocket.send(
                json.dumps(
                    {
                        "type": "approval_response",
                        "command_id": command_id,
                        "agent_id": agent_id,
                        "approved": True,
                    }
                )
            )
        await websocket.send(
            json.dumps(
                {
                    "type": "command_result",
                    "command_id": command_id,
                    "agent_id": agent_id,
                    "ok": True,
                    "payload": {
                        "count": 2,
                        "items": [
                            {"pid": 101, "name": "chrome.exe", "cpu": 2.5, "memory_mb": 300},
                            {"pid": 202, "name": "Code.exe", "cpu": 6.0, "memory_mb": 720},
                        ],
                        "source": "mock-agent",
                    },
                }
            )
        )
        await asyncio.sleep(0.2)
        return {"agent_id": agent_id, "command_id": command_id}


def start_agent_thread(agent_token: str, command_type: str) -> tuple[threading.Event, queue.Queue, threading.Thread]:
    ready = threading.Event()
    results: queue.Queue = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            results.put(asyncio.run(run_agent_session_ready(agent_token, command_type, ready)))
        except BaseException as exc:
            results.put(exc)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return ready, results, thread


def main() -> int:
    if not PYTHON.exists():
        print(f"Missing backend venv Python: {PYTHON}", file=sys.stderr)
        return 2

    db_path = Path(tempfile.mkdtemp(prefix="remotectrl-e2e-")) / "e2e.db"
    env = os.environ.copy()
    env["REMOTECTRL_DB"] = str(db_path)
    env["REMOTECTRL_SECRET_KEY"] = "e2e-secret"

    process = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    try:
        wait_for_gateway()
        with httpx.Client(base_url=BASE_URL, timeout=5.0) as client:
            login = client.post(
                "/api/auth/login",
                json={"email": "admin@remotectrl.local", "password": "admin12345"},
            )
            login.raise_for_status()
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            enrollment = client.post(
                "/api/enrollment-tokens",
                headers=headers,
                json={"label": "E2E mock agent", "reusable": False},
            )
            enrollment.raise_for_status()

            enrolled = client.post(
                "/api/agents/enroll",
                json={
                    "enrollment_token": enrollment.json()["token"],
                    "name": "E2E Mock Agent",
                    "hostname": "e2e-host",
                    "os": "Windows 11 Mock",
                },
            )
            enrolled.raise_for_status()
            agent_token = enrolled.json()["agent_token"]
            agent_id = enrolled.json()["agent_id"]

            ready, results, thread = start_agent_thread(agent_token, "process.list")
            if not ready.wait(timeout=8):
                raise RuntimeError("Mock agent did not connect to gateway")
            command = client.post(
                "/api/commands",
                headers=headers,
                json={"agent_id": agent_id, "type": "process.list", "payload": {}},
            )
            command.raise_for_status()
            thread.join(timeout=8)
            if thread.is_alive():
                raise RuntimeError("Mock agent did not finish command")
            result = results.get_nowait()
            if isinstance(result, BaseException):
                raise result
            commands = client.get(f"/api/agents/{agent_id}/commands", headers=headers)
            commands.raise_for_status()
            latest = commands.json()[0]
            assert latest["id"] == result["command_id"]
            assert latest["status"] == "succeeded"
            assert latest["result"]["source"] == "mock-agent"

        print("E2E mock agent flow passed")
        print(f"agent_id={agent_id}")
        print(f"command_id={result['command_id']}")
        return 0
    finally:
        if process.poll() is None:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
                time.sleep(0.5)
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
