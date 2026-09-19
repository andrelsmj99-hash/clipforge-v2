"""
Dev server lifecycle and health check for Canva Apps SDK.
"""

from __future__ import annotations
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from clipforge.core.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


def resolve_canva_app_dir() -> Path:
    """Dynamically resolve the Canva Apps SDK directory across dev and PyInstaller builds."""
    # 1. Environment variable override
    env_dir = os.getenv("CLIPFORGE_CANVA_APP_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / "package.json").exists():
            return p

    # 2. Check candidates
    candidates = [
        PROJECT_ROOT / "canva_app",
        Path.cwd() / "canva_app",
    ]

    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        candidates.extend([
            exe_path.parent / "canva_app",
            exe_path.parent.parent / "canva_app",
        ])
    else:
        candidates.append(Path(__file__).resolve().parents[3] / "canva_app")

    for candidate in candidates:
        if candidate and candidate.exists() and (candidate / "package.json").exists():
            return candidate

    return PROJECT_ROOT / "canva_app"


CANVA_APP_DIR = resolve_canva_app_dir()
DEFAULT_PORT = 8080


def is_dev_server_running(host: str = "localhost", port: int = DEFAULT_PORT, timeout: float = 1.0) -> bool:
    """Check if the Canva dev server is listening on the expected port."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


class CanvaDevServer:
    def __init__(
        self,
        app_dir: Optional[Path | str] = None,
        port: int = DEFAULT_PORT,
    ):
        self.app_dir = Path(app_dir) if app_dir else resolve_canva_app_dir()
        self.port = port
        self.process: Optional[subprocess.Popen] = None

    def is_healthy(self) -> bool:
        return is_dev_server_running(port=self.port)

    def start(self, wait_timeout: float = 15.0) -> bool:
        """Start the dev server if it is not already running."""
        if self.is_healthy():
            logger.info(f"Canva dev server already running on port {self.port}.")
            return True

        if not (self.app_dir / "package.json").exists():
            raise FileNotFoundError(f"Canva app directory does not exist or missing package.json: {self.app_dir}")

        npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
        logger.info(f"Starting Canva dev server in {self.app_dir}...")

        env = os.environ.copy()
        if os.name == "nt":
            if "C:\\Program Files\\nodejs" not in env.get("PATH", ""):
                env["PATH"] = f"C:\\Program Files\\nodejs;{env.get('PATH', '')}"

        self.process = subprocess.Popen(
            [npm_cmd, "run", "start"],
            cwd=str(self.app_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        start_time = time.time()
        while time.time() - start_time < wait_timeout:
            if self.is_healthy():
                logger.info(f"Canva dev server is up and healthy on port {self.port}!")
                return True
            time.sleep(1.0)

        # Process diagnostics if timed out or failed to start
        if self.process and self.process.poll() is not None:
            _, stderr = self.process.communicate()
            err_msg = stderr.decode(errors="replace").strip()
            logger.error(f"Canva dev server process exited with code {self.process.returncode}: {err_msg}")
        else:
            logger.warning(f"Canva dev server failed to respond within {wait_timeout}s.")
        return False

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
            logger.info("Canva dev server stopped.")
