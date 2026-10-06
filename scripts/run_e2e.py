"""Run the synthetic HTTP and worker E2E suite in an isolated local environment."""
from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "apps" / "api"
PYTHON = ROOT / ".venv" / "bin" / "python"


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def await_ready(url: str, process: subprocess.Popen) -> None:
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before becoming ready: {url}")
        try:
            if httpx.get(url, timeout=0.2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError(f"Service did not become ready: {url}")


def main() -> None:
    if not PYTHON.exists():
        raise SystemExit("Install API requirements in the repository .venv first")
    with tempfile.TemporaryDirectory(prefix="reach-crm-e2e-") as directory:
        database = Path(directory) / "e2e.db"
        api_port, gateway_port = free_port(), free_port()
        while gateway_port == api_port:
            gateway_port = free_port()
        api_url = f"http://127.0.0.1:{api_port}"
        gateway_url = f"http://127.0.0.1:{gateway_port}"
        environment = os.environ.copy()
        environment.update({
            "DATABASE_URL": f"sqlite:///{database}",
            "JWT_SECRET": "e2e-local-jwt-secret-at-least-32-bytes",
            "ENGINE_WEBHOOK_SECRET": "e2e-webhook-secret",
            "WHATSAPP_ENGINE_URL": gateway_url,
            "VOICE_ENGINE_URL": gateway_url,
            "ENGINE_API_TOKEN": "e2e-gateway-token",
            "RAG_MODEL_BASE_URL": "",
            "RAG_MODEL_NAME": "",
            "RAG_MODEL_KEY_FILE": "",
            "RAG_MODEL_API_KEY": "",
        })
        subprocess.run([str(PYTHON), "-m", "alembic", "upgrade", "head"], cwd=API_DIR, env=environment, check=True)
        with open(Path(directory) / "gateway.log", "w") as gateway_log, open(Path(directory) / "api.log", "w") as api_log:
            gateway = subprocess.Popen([str(PYTHON), str(ROOT / "scripts" / "fake_gateway.py"), str(gateway_port)], cwd=ROOT, env=environment, stdout=gateway_log, stderr=subprocess.STDOUT)
            api = subprocess.Popen([str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(api_port)], cwd=API_DIR, env=environment, stdout=api_log, stderr=subprocess.STDOUT)
            try:
                await_ready(gateway_url + "/events", gateway)
                await_ready(api_url + "/health", api)
                subprocess.run([str(PYTHON), str(ROOT / "scripts" / "e2e_local.py"), api_url, gateway_url, str(database)], cwd=ROOT, env=environment, check=True)
            finally:
                for process in (api, gateway):
                    process.terminate()
                for process in (api, gateway):
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


if __name__ == "__main__":
    main()
