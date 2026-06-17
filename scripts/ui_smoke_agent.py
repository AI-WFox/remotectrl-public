from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pywinauto import Desktop


ROOT = Path(__file__).resolve().parents[1]
AGENT_DIR = ROOT / "agent"
AGENT_PYTHON = AGENT_DIR / ".venv" / "Scripts" / "python.exe"


def main() -> int:
    if not AGENT_PYTHON.exists():
        print(f"Missing agent venv Python: {AGENT_PYTHON}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["APPDATA"] = tempfile.mkdtemp(prefix="remotectrl-agent-ui-")
    process = subprocess.Popen(
        [str(AGENT_PYTHON), "-m", "remotectrl_agent"],
        cwd=str(AGENT_DIR),
        env=env,
    )
    try:
        window = Desktop(backend="uia").window(title="RemoteCtrl Agent")
        window.wait("visible", timeout=15)
        if not window.exists():
            raise RuntimeError("RemoteCtrl Agent window did not appear")
        print("Agent UI smoke test passed")
        print(f"pid={process.pid}")
        return 0
    finally:
        try:
            Desktop(backend="uia").window(title="RemoteCtrl Agent").close()
            time.sleep(0.5)
        except Exception:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
