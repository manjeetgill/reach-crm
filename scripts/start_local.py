"""Start the existing local CRM preview: python scripts/start_local.py."""
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"
DATABASE = Path(os.environ.get("REACH_LOCAL_DATABASE", "/private/tmp/reach-crm-seed.db"))


def main():
    if not DATABASE.is_file():
        raise SystemExit(f"Existing preview database not found: {DATABASE}")
    environment = os.environ.copy()
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if key.strip() in {"JWT_SECRET", "ENGINE_WEBHOOK_SECRET", "ENGINE_API_TOKEN", "REACH_WHATSAPP_BUSINESS_NUMBER", "REACH_AGENT_PLATFORM_FILE"}:
                parts = shlex.split(value)
                environment[key.strip()] = parts[0] if parts else ""
    environment.update({
        "DATABASE_URL": f"sqlite:///{DATABASE.resolve()}",
        "WEB_ORIGIN": "http://localhost:13000",
        "NEXT_PUBLIC_API_URL": "http://localhost:18000",
        "WHATSAPP_ENGINE_URL": "", "VOICE_ENGINE_URL": "",
        "RAG_MODEL_BASE_URL": "", "RAG_MODEL_NAME": "",
        "RAG_MODEL_API_KEY": "", "RAG_MODEL_KEY_FILE": "",
    })
    for port in (13000, 18000):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as error:
                raise SystemExit(f"Cannot start preview on port {port}: {error}")
    services = []
    commands = [
        ([str(PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "18000"], ROOT / "apps/api"),
        ([str(PYTHON), "-m", "app.worker"], ROOT / "apps/api"),
        (["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", "13000"], ROOT / "apps/web"),
    ]
    try:
        for command, directory in commands:
            services.append(subprocess.Popen(command, cwd=directory, env=environment, start_new_session=True))
        print("Local CRM: http://localhost:13000/ — keep this terminal open; Ctrl+C stops it.", flush=True)
        while all(service.poll() is None for service in services):
            time.sleep(1)
        raise SystemExit("A preview service stopped. See the error above.")
    except KeyboardInterrupt:
        pass
    finally:
        for service in services:
            if service.poll() is None:
                os.killpg(service.pid, signal.SIGTERM)
        for service in services:
            try:
                service.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(service.pid, signal.SIGKILL)
                service.wait()


if __name__ == "__main__":
    main()
