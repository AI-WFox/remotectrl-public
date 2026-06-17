from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
BACKEND_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"
AGENT_DIR = ROOT / "agent"
BASE_URL = "http://127.0.0.1:8766"

sys.path.insert(0, str(AGENT_DIR))

from remotectrl_agent.core.client import AgentClient  # noqa: E402
from remotectrl_agent.core.config import AgentConfig  # noqa: E402
from remotectrl_agent.core.handlers import CommandHandlers  # noqa: E402


def wait_for_gateway(timeout_seconds: float = 15.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            response = requests.get(f"{BASE_URL}/api/health", timeout=1.0)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.25)
    raise RuntimeError("Gateway did not become ready")


def wait_for_command(headers: dict[str, str], agent_id: str, timeout_seconds: float = 15.0) -> dict:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = requests.get(f"{BASE_URL}/api/agents/{agent_id}/commands", headers=headers, timeout=5)
        response.raise_for_status()
        commands = response.json()
        if commands and commands[0]["status"] in {"succeeded", "failed", "denied"}:
            return commands[0]
        time.sleep(0.25)
    raise RuntimeError("Command did not finish")


def main() -> int:
    if not BACKEND_PYTHON.exists():
        print(f"Missing backend venv Python: {BACKEND_PYTHON}", file=sys.stderr)
        return 2

    db_path = Path(tempfile.mkdtemp(prefix="remotectrl-headless-e2e-")) / "e2e.db"
    env = os.environ.copy()
    env["REMOTECTRL_DB"] = str(db_path)
    env["REMOTECTRL_SECRET_KEY"] = "headless-e2e-secret"

    process = subprocess.Popen(
        [
            str(BACKEND_PYTHON),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8766",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    agent_client: AgentClient | None = None
    try:
        wait_for_gateway()
        login = requests.post(
            f"{BASE_URL}/api/auth/login",
            json={"email": "admin@remotectrl.local", "password": "admin12345"},
            timeout=5,
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        enrollment = requests.post(
            f"{BASE_URL}/api/enrollment-tokens",
            headers=headers,
            json={"label": "Headless real agent", "reusable": False},
            timeout=5,
        )
        enrollment.raise_for_status()

        config = AgentConfig(
            server_url=BASE_URL,
            agent_name="Headless Real Agent",
            allowed_folders=[str(Path.home())],
        )
        handlers = CommandHandlers(config, lambda action: "")
        statuses: list[str] = []
        agent_client = AgentClient(
            config,
            handlers,
            statuses.append,
            lambda _message: True,
        )
        agent_client.enroll(enrollment.json()["token"])
        if not config.agent_id:
            raise RuntimeError("Agent did not receive an id during enrollment")
        agent_client.start()

        deadline = time.time() + 10
        while "Connected" not in statuses and time.time() < deadline:
            time.sleep(0.1)
        if "Connected" not in statuses:
            raise RuntimeError(f"Headless agent did not connect. Statuses: {statuses}")

        command = requests.post(
            f"{BASE_URL}/api/commands",
            headers=headers,
            json={"agent_id": config.agent_id, "type": "process.list", "payload": {}},
            timeout=5,
        )
        command.raise_for_status()
        finished = wait_for_command(headers, config.agent_id)
        if finished["status"] != "succeeded":
            raise RuntimeError(f"Command failed: {finished}")
        result = finished["result"]
        if not result or result.get("count", 0) < 1:
            raise RuntimeError(f"Unexpected process.list result: {result}")

        print("E2E headless real-agent flow passed")
        print(f"agent_id={config.agent_id}")
        print(f"command_id={finished['id']}")
        print(f"process_count={result['count']}")
        return 0
    finally:
        if agent_client:
            agent_client.stop()
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

