from __future__ import annotations

import json
import os
import queue
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import bpy

from . import engine, hooks, runtime_guard

VERSION = "0.1.6"
STATE_PATH = Path(tempfile.gettempdir()) / "1782-92-bridge.json"

# Blender mutation/render work is serialized onto the main thread. A queue of
# stale work after a timeout is more dangerous than rejecting the extra work, so
# keep exactly one non-status job in flight at a time.
MAX_PENDING_JOBS = 1
MAX_IMAGE_B64_BYTES = 8 * 1024 * 1024
MAX_INSPECT_REQUEST_BYTES = 4 * 1024
MAX_APPLY_REQUEST_BYTES = 32 * 1024
MAX_RENDER_REQUEST_BYTES = 16 * 1024
BLENDER_VERSION = ".".join(str(value) for value in bpy.app.version)
BACKGROUND = bool(bpy.app.background)

_SERVER: ThreadingHTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None
_TOKEN: str | None = None
_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue()
_RUNNING = False
_ACTIVE_JOB: dict[str, Any] | None = None
_LAST_JOB: dict[str, Any] | None = None
_STARTED_AT = 0.0
_NEXT_JOB_ID = 1
_RECOVERY_REQUIRED = False


def is_running() -> bool:
    return _RUNNING


def _active_age_ms() -> int | None:
    active = _ACTIVE_JOB
    if active is None:
        return None
    started = active.get("started_at")
    if not isinstance(started, (int, float)):
        return None
    return round(max(0.0, time.monotonic() - float(started)) * 1000)


def _status_reply() -> dict[str, Any]:
    reply: dict[str, Any] = {
        "ok": True,
        "rev": engine._REV,
        "bridge": VERSION,
        "blender": BLENDER_VERSION,
        "bg": int(BACKGROUND),
        "pid": os.getpid(),
        "queued": _QUEUE.qsize(),
    }
    if _STARTED_AT:
        reply["up_s"] = round(max(0.0, time.monotonic() - _STARTED_AT), 1)
    if _RECOVERY_REQUIRED:
        reply["recover"] = 1
    active = _ACTIVE_JOB
    if active is not None:
        reply["busy"] = str(active.get("path") or "").lstrip("/")
        reply["job"] = active.get("id")
        age = _active_age_ms()
        if age is not None:
            reply["busy_ms"] = age
    last = _LAST_JOB
    if last is not None:
        reply["last"] = [
            last.get("id"),
            str(last.get("path") or "").lstrip("/"),
            int(last.get("ms") or 0),
            1 if last.get("ok") else 0,
        ]
        if last.get("error"):
            reply["last_e"] = str(last["error"])[:160]
    return reply


def _cancel_job(job: dict[str, Any]) -> None:
    job["cancelled"] = True


def _drain_queue() -> None:
    while True:
        try:
            job = _QUEUE.get_nowait()
        except queue.Empty:
            return
        _cancel_job(job)
        event = job.get("event")
        if isinstance(event, threading.Event):
            job["result"] = {"ok": False, "error": "job_cancelled"}
            event.set()


def _finish_job(job: dict[str, Any]) -> None:
    global _LAST_JOB
    started = job.get("started_at")
    elapsed = 0
    if isinstance(started, (int, float)):
        elapsed = round(max(0.0, time.monotonic() - float(started)) * 1000)
    result = job.get("result")
    ok = isinstance(result, dict) and result.get("ok") is True
    error = result.get("error") if isinstance(result, dict) else "bridge_result_missing"
    _LAST_JOB = {
        "id": job.get("id"),
        "path": job.get("path"),
        "ms": elapsed,
        "ok": ok,
        "error": error,
    }


def _pump() -> float | None:
    global _ACTIVE_JOB, _RECOVERY_REQUIRED
    if not _RUNNING:
        return None
    try:
        job = _QUEUE.get_nowait()
    except queue.Empty:
        return 0.03

    event = job["event"]
    if job.get("cancelled"):
        job["result"] = {"ok": False, "error": "job_cancelled"}
        _finish_job(job)
        event.set()
        return 0.03

    job["started"] = True
    job["started_at"] = time.monotonic()
    if job.get("cancelled"):
        job["result"] = {"ok": False, "error": "job_cancelled"}
        _finish_job(job)
        event.set()
        return 0.03

    _ACTIVE_JOB = job
    try:
        job["result"] = runtime_guard.dispatch(job["path"], job["payload"])
        # A timed-out mutating request is ambiguous: the client does not know
        # whether Blender eventually committed it. Require one successful
        # read-only inspect after the active job has completed before accepting
        # another apply. This prevents a cascade of blind follow-up mutations.
        if (
            _RECOVERY_REQUIRED
            and job["path"] == "/inspect"
            and isinstance(job["result"], dict)
            and job["result"].get("ok") is True
        ):
            _RECOVERY_REQUIRED = False
    except Exception as exc:
        job["result"] = {"ok": False, "error": f"internal:{type(exc).__name__}:{exc}"}
    finally:
        _finish_job(job)
        _ACTIVE_JOB = None
        event.set()
    return 0.03


def _busy_reply() -> dict[str, Any]:
    active = _ACTIVE_JOB
    if active is not None:
        kind = str(active.get("path") or "").lstrip("/") or "job"
        job_id = active.get("id")
        age = _active_age_ms()
        suffix = f":{age}ms" if age is not None else ""
        return {"ok": False, "error": f"bridge_busy:{kind}:j{job_id}{suffix}"}
    if not _QUEUE.empty():
        return {"ok": False, "error": "bridge_busy:queued"}
    return {"ok": False, "error": "bridge_busy"}


def _submit(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    global _NEXT_JOB_ID, _RECOVERY_REQUIRED
    if path == "/apply" and _RECOVERY_REQUIRED:
        return {"ok": False, "error": "recover_required:inspect_state_before_apply"}

    # Single-flight admission control. Sequential MCP clients never notice this
    # in the healthy case, but stale callers cannot pile work onto Blender after
    # a timeout or long-running C operator.
    if _ACTIVE_JOB is not None or not _QUEUE.empty() or _QUEUE.qsize() >= MAX_PENDING_JOBS:
        return _busy_reply()

    event = threading.Event()
    job_id = _NEXT_JOB_ID
    _NEXT_JOB_ID += 1
    job: dict[str, Any] = {
        "id": job_id,
        "path": path,
        "payload": payload,
        "event": event,
        "result": None,
        "started": False,
        "started_at": None,
        "cancelled": False,
        "client_timed_out": False,
    }
    _QUEUE.put(job)
    if not event.wait(timeout):
        _cancel_job(job)
        job["client_timed_out"] = True
        phase = "running" if job.get("started") else "queued"
        if phase == "running":
            # If this was an apply, its completion state is ambiguous to the MCP
            # client. Stop accepting mutations until a fresh inspect observes
            # the actual scene after the job returns.
            if path == "/apply":
                _RECOVERY_REQUIRED = True
            _drain_queue()
        suffix = ":recover_required" if path == "/apply" and phase == "running" else ""
        return {"ok": False, "error": f"blender_timeout:{phase}{suffix}"}
    result = job.get("result")
    return result if isinstance(result, dict) else {"ok": False, "error": "bridge_result_missing"}


def _request_limit(path: str) -> int:
    if path == "/apply":
        return MAX_APPLY_REQUEST_BYTES
    if path == "/render":
        return MAX_RENDER_REQUEST_BYTES
    return MAX_INSPECT_REQUEST_BYTES


def _image_payload_bytes(payload: dict[str, Any]) -> int:
    total = 0
    images = payload.get("images")
    if not isinstance(images, list):
        return 0
    for image in images:
        if isinstance(image, dict):
            data = image.get("data")
            if isinstance(data, str):
                total += len(data.encode("ascii", errors="ignore"))
    return total


class _Handler(BaseHTTPRequestHandler):
    server_version = "1782-92/0.1.6"

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
        limit = _request_limit(self.path)
        if length < 0 or length > limit:
            self._reply(413, {"ok": False, "error": f"request_too_large:{length}>{limit}"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise ValueError
        except Exception:
            self._reply(400, {"ok": False, "error": "bad_json"})
            return

        # Status deliberately bypasses Blender's main-thread queue. If an apply or
        # render is currently wedged, the client can still learn that the bridge is
        # alive, what is busy, whether recovery is required, and queue depth.
        if self.path == "/inspect" and payload.get("q") == "status":
            self._reply(200, _status_reply())
            return

        # The Python apply deadline is cooperative and cannot pre-empt a long C
        # operator. Transport limits plus single-flight admission keep that one
        # slow operation from becoming a queue avalanche.
        timeout = 180.0 if self.path == "/render" else 25.0 if self.path == "/apply" else 10.0
        result = _submit(self.path, payload, timeout)
        self._reply(200 if result.get("ok") else 400, result)

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        image_bytes = _image_payload_bytes(payload)
        if image_bytes > MAX_IMAGE_B64_BYTES:
            status = 413
            payload = {
                "ok": False,
                "error": f"response_too_large:images:{image_bytes}>{MAX_IMAGE_B64_BYTES}",
            }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The MCP client may have timed out first. Never let a dead HTTP
            # socket take Blender's bridge thread down with it.
            pass


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
    global _SERVER, _SERVER_THREAD, _TOKEN, _RUNNING, _STARTED_AT
    global _ACTIVE_JOB, _LAST_JOB, _NEXT_JOB_ID, _RECOVERY_REQUIRED
    if _RUNNING:
        return True, "already_running"
    try:
        _drain_queue()
        _ACTIVE_JOB = None
        _LAST_JOB = None
        _NEXT_JOB_ID = 1
        _RECOVERY_REQUIRED = False
        _TOKEN = secrets.token_urlsafe(32)
        _SERVER = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        _SERVER.daemon_threads = True
        _RUNNING = True
        _STARTED_AT = time.monotonic()
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
        _STARTED_AT = 0.0
        _ACTIVE_JOB = None
        _RECOVERY_REQUIRED = False
        hooks.remove()
        return False, f"{type(exc).__name__}:{exc}"


def stop_bridge() -> None:
    global _SERVER, _SERVER_THREAD, _TOKEN, _RUNNING, _ACTIVE_JOB, _LAST_JOB
    global _STARTED_AT, _RECOVERY_REQUIRED
    _RUNNING = False
    _ACTIVE_JOB = None
    _LAST_JOB = None
    _RECOVERY_REQUIRED = False
    _STARTED_AT = 0.0
    hooks.remove()
    _drain_queue()
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
