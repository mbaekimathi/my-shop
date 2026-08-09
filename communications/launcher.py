"""Auto-start the local WhatsApp bridge when Django can reach it on localhost."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_last_start_attempt = 0.0
_START_COOLDOWN_SECONDS = 20.0


def _bridge_dir() -> Path:
    return Path(settings.BASE_DIR) / "whatsapp-bridge"


def _pid_path() -> Path:
    return _bridge_dir() / ".bridge.pid"


def _log_path() -> Path:
    return _bridge_dir() / "bridge.log"


def autostart_enabled() -> bool:
    raw = (os.getenv("WHATSAPP_BRIDGE_AUTOSTART") or "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    # Default: on for local bridge URLs.
    from .bridge import bridge_is_local

    return bridge_is_local()


def _port_open(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid() -> int | None:
    path = _pid_path()
    if not path.is_file():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return None
    return pid or None


def _write_pid(pid: int) -> None:
    try:
        _pid_path().write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write bridge pid file: %s", exc)


def _resolve_node() -> str | None:
    return shutil.which("node")


def _ensure_dependencies(bridge_dir: Path) -> str | None:
    """Return error string if node_modules cannot be prepared."""
    if (bridge_dir / "node_modules" / "whatsapp-web.js").exists():
        return None
    npm = shutil.which("npm")
    if not npm:
        return "npm is not installed on this server."
    try:
        completed = subprocess.run(
            [npm, "install", "--omit=dev"],
            cwd=str(bridge_dir),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        return f"npm install failed: {exc}"
    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        return f"npm install failed: {err[:400] or 'unknown error'}"
    return None


def ensure_bridge_running(*, force: bool = False) -> dict[str, Any]:
    """
    Start the local Node WhatsApp helper if it is down.
    Safe to call often; uses a cooldown and pid/port checks.
    """
    from .bridge import bridge_is_local, _bridge_secret

    if not bridge_is_local():
        return {"ok": False, "started": False, "reason": "remote"}
    if not autostart_enabled():
        return {"ok": False, "started": False, "reason": "disabled"}

    port = int(getattr(settings, "WHATSAPP_BRIDGE_PORT", 3100) or 3100)
    if _port_open(port):
        return {"ok": True, "started": False, "running": True}

    pid = _read_pid()
    if pid and _pid_running(pid):
        # Process exists but port not open yet — still booting.
        return {"ok": True, "started": False, "running": True, "booting": True}

    global _last_start_attempt
    now = time.monotonic()
    with _lock:
        if not force and (now - _last_start_attempt) < _START_COOLDOWN_SECONDS:
            return {"ok": True, "started": False, "booting": True, "reason": "cooldown"}
        _last_start_attempt = now

        bridge_dir = _bridge_dir()
        server_js = bridge_dir / "server.js"
        if not server_js.is_file():
            return {
                "ok": False,
                "started": False,
                "error": "whatsapp-bridge/server.js is missing on this server.",
            }

        node = _resolve_node()
        if not node:
            return {
                "ok": False,
                "started": False,
                "error": "Node.js is not installed on this server (needed for WhatsApp).",
            }

        dep_error = _ensure_dependencies(bridge_dir)
        if dep_error:
            return {"ok": False, "started": False, "error": dep_error}

        env = os.environ.copy()
        env["WHATSAPP_BRIDGE_PORT"] = str(port)
        env["WHATSAPP_BRIDGE_HOST"] = "127.0.0.1"
        secret = _bridge_secret()
        if secret:
            env["WHATSAPP_BRIDGE_SECRET"] = secret

        log_file = _log_path()
        try:
            log_handle = open(log_file, "a", encoding="utf-8")
        except OSError:
            log_handle = subprocess.DEVNULL

        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            start_new_session = True

        try:
            proc = subprocess.Popen(
                [node, "server.js"],
                cwd=str(bridge_dir),
                env=env,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=start_new_session,
                close_fds=os.name != "nt",
            )
        except Exception as exc:
            logger.exception("Failed to auto-start WhatsApp bridge")
            return {"ok": False, "started": False, "error": str(exc)}
        finally:
            if log_handle not in (None, subprocess.DEVNULL):
                try:
                    log_handle.close()
                except Exception:
                    pass

        _write_pid(proc.pid)
        logger.info("Auto-started WhatsApp bridge pid=%s port=%s", proc.pid, port)
        return {"ok": True, "started": True, "pid": proc.pid, "booting": True}


def maybe_autostart_in_background() -> None:
    """Fire-and-forget start used from AppConfig.ready / status polls."""
    if not autostart_enabled():
        return

    def _run():
        try:
            ensure_bridge_running()
        except Exception:
            logger.exception("Background WhatsApp bridge autostart failed")

    threading.Thread(target=_run, name="wa-bridge-autostart", daemon=True).start()
