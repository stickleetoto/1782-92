from __future__ import annotations

import json
import os
import queue
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import bpy

from . import engine, field_ops, hooks

VERSION = "0.1.3"
STATE_PATH = Path(tempfile.gettempdir()) / "1782-92-bridge.json"

_SERVER: ThreadingHTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None
_TOKEN: str | None = None
_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue()
_RUNNING = False


def is_running() -> bool:
    return _RUNNING


def _pump() -> float | None:
    if not _RUNNING:
        return None
    for _ in range(8):
        try:
            job = _QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            job["result"] = field_ops.dispatch(job["path"], job["payload"])
        except Exception as exc:
            job["result"] = {"ok": False, "error": f"internal:{type(exc).__name__}:{exc}"}
        finally:
            job["event"].set()
    return 0.03


def _submit(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    event = threading.Event()
    job: dict[str, Any] = {"path": path, "payload": payload, "event": event, "result": None}
    _QUEUE.put(job)
    if not event.wait(timeout):
        return {"ok": False, "error": "blender_timeout"}
    return job["result"]


class _Handler(BaseHTTPRequestHandler):
    server_version = "1782-92/0.1.3"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def do_POST(self) -> None:
        token = self.headers.get("x-1782-token", "")
        if not _TOKEN or not secrets.compare_digest(token, _TOKEN):
            self._reply(403, {"ok": False, "error": "forbidden"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self._reply(400, {"ok": False, "error": "bad_length"})
            return
        if length < 0 or length > 1_000_000:
            self._reply(413, {"ok": False, "error": "request_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except Exception:
            self._reply(400, {"ok": False, "error": "bad_json"})
            return
        timeout = 230.0 if self.path == "/render" else 170.0 if self.path == "/apply" else 12.0
        result = _submit(self.path, payload, timeout)
        self._reply(200 if result.get("ok") else 400, result)

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _write_state(port: int) -> None:
    assert _TOKEN is not None
    payload = {"host": "127.0.0.1", "port": port, "token": _TOKEN, "pid": os.getpid(), "version": VERSION}
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    os.replace(temp, STATE_PATH)


def start_bridge() -> tuple[bool, str]:
    global _SERVER, _SERVER_THREAD, _TOKEN, _RUNNING
    if _RUNNING:
        return True, "already_running"
    try:
        _TOKEN = secrets.token_urlsafe(32)
        _SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        _SERVER.daemon_threads = True
        _RUNNING = True
        engine.reset_session()
        hooks.install()
        if not bpy.app.timers.is_registered(_pump):
            bpy.app.timers.register(_pump, first_interval=0.03, persistent=True)
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, name="1782-92-http", daemon=True)
        _SERVER_THREAD.start()
        _write_state(int(_SERVER.server_address[1]))
        return True, f"127.0.0.1:{_SERVER.server_address[1]}"
    except Exception as exc:
        _RUNNING = False
        hooks.remove()
        return False, f"{type(exc).__name__}:{exc}"


def stop_bridge() -> None:
    global _SERVER, _SERVER_THREAD, _TOKEN, _RUNNING
    _RUNNING = False
    hooks.remove()
    if _SERVER is not None:
        try:
            _SERVER.shutdown()
            _SERVER.server_close()
        except Exception:
            pass
    _SERVER = None
    _SERVER_THREAD = None
    _TOKEN = None
    try:
        STATE_PATH.unlink()
    except OSError:
        pass
