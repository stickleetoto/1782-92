from __future__ import annotations

import ast
import base64
import contextlib
import io
import json
import math
import os
import queue
import secrets
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

VERSION = "0.1.0"
STATE_PATH = Path(tempfile.gettempdir()) / "1782-92-bridge.json"

_SERVER: ThreadingHTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None
_TOKEN: str | None = None
_QUEUE: queue.Queue[dict[str, Any]] = queue.Queue()
_RUNNING = False
_REV = 0
_LAST_CHANGED: list[str] = []
_NEXT_ID = 1
_OBJ_IDS: dict[int, str] = {}


def _compact(value: float) -> float:
    return round(float(value), 5)


def _chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


_BANNED_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}

_BANNED_PREFIXES = (
    "bpy.app",
    "bpy.context.preferences",
    "bpy.data.libraries",
    "bpy.ops.console",
    "bpy.ops.export",
    "bpy.ops.import",
    "bpy.ops.preferences",
    "bpy.ops.script",
    "bpy.ops.wm",
    "bpy.path",
    "bpy.utils",
)

_BANNED_ATTRS = {
    "load",
    "load_post",
    "load_pre",
    "save",
    "save_post",
    "save_pre",
    "save_render",
    "write",
}

_SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "print": print,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


def _validate_code(code: str) -> ast.AST:
    if len(code) > 65_536:
        raise ValueError("code_too_large")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"syntax:{exc.lineno}:{exc.offset}:{exc.msg}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("import_blocked")
        if isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            raise ValueError(f"name_blocked:{node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError("private_attribute_blocked")
            if node.attr in _BANNED_ATTRS:
                raise ValueError(f"attribute_blocked:{node.attr}")
            chain = _chain(node)
            if any(chain == prefix or chain.startswith(prefix + ".") for prefix in _BANNED_PREFIXES):
                raise ValueError(f"api_blocked:{chain}")
    return tree


def _sync_ids() -> None:
    global _NEXT_ID
    live = {obj.as_pointer() for obj in bpy.data.objects}
    for pointer in list(_OBJ_IDS):
        if pointer not in live:
            del _OBJ_IDS[pointer]
    for obj in bpy.data.objects:
        pointer = obj.as_pointer()
        if pointer not in _OBJ_IDS:
            _OBJ_IDS[pointer] = f"o{_NEXT_ID}"
            _NEXT_ID += 1


def _id_for(obj: bpy.types.Object | None) -> str | None:
    if obj is None:
        return None
    _sync_ids()
    return _OBJ_IDS.get(obj.as_pointer())


def _find_id(value: str) -> bpy.types.Object | None:
    _sync_ids()
    for obj in bpy.data.objects:
        if _OBJ_IDS.get(obj.as_pointer()) == value:
            return obj
    return None


def _object_signature(obj: bpy.types.Object) -> tuple[Any, ...]:
    matrix = tuple(_compact(v) for row in obj.matrix_world for v in row)
    dims = tuple(_compact(v) for v in obj.dimensions)
    bounds: tuple[float, ...] = ()
    try:
        bounds = tuple(_compact(v) for corner in obj.bound_box for v in corner)
    except Exception:
        pass
    mesh_stats: tuple[int, int, int] | None = None
    if obj.type == "MESH" and obj.data:
        mesh_stats = (len(obj.data.vertices), len(obj.data.edges), len(obj.data.polygons))
    return (
        obj.name,
        obj.type,
        _id_for(obj.parent),
        matrix,
        dims,
        bounds,
        mesh_stats,
        tuple(slot.material.name if slot.material else "" for slot in obj.material_slots),
        bool(obj.hide_viewport),
        bool(obj.hide_render),
    )


def _snapshot() -> dict[str, tuple[Any, ...]]:
    _sync_ids()
    return {str(_id_for(obj)): _object_signature(obj) for obj in bpy.data.objects}


def _inspect(payload: dict[str, Any]) -> dict[str, Any]:
    _sync_ids()
    q = str(payload.get("q") or "summary")
    scene = bpy.context.scene
    active = bpy.context.view_layer.objects.active

    if q == "summary":
        counts: dict[str, int] = {}
        for obj in scene.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
        return {
            "ok": True,
            "rev": _REV,
            "file": Path(bpy.data.filepath).name if bpy.data.filepath else "<unsaved>",
            "counts": counts,
            "active": _id_for(active),
            "selected": [_id_for(obj) for obj in bpy.context.selected_objects],
            "changed": _LAST_CHANGED,
        }

    if q == "objects":
        return {
            "ok": True,
            "rev": _REV,
            "objects": [[_id_for(obj), obj.name, obj.type] for obj in scene.objects],
        }

    if q == "selection":
        return {
            "ok": True,
            "rev": _REV,
            "objects": [[_id_for(obj), obj.name, obj.type] for obj in bpy.context.selected_objects],
        }

    if q == "materials":
        return {
            "ok": True,
            "rev": _REV,
            "materials": [[index, mat.name] for index, mat in enumerate(bpy.data.materials)],
        }

    obj = _find_id(q)
    if obj is None:
        return {"ok": False, "error": "object_not_found"}

    detail: dict[str, Any] = {
        "ok": True,
        "rev": _REV,
        "id": q,
        "name": obj.name,
        "type": obj.type,
        "loc": [_compact(v) for v in obj.location],
        "rot": [_compact(v) for v in obj.rotation_euler],
        "scale": [_compact(v) for v in obj.scale],
        "dim": [_compact(v) for v in obj.dimensions],
        "parent": _id_for(obj.parent),
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
    }
    if obj.type == "MESH" and obj.data:
        detail["mesh"] = {
            "v": len(obj.data.vertices),
            "e": len(obj.data.edges),
            "f": len(obj.data.polygons),
        }
    return detail


def _checkpoint(rev: int) -> str | None:
    try:
        if bpy.data.filepath:
            root = Path(bpy.data.filepath).parent / ".1782-92" / "checkpoints"
            stem = Path(bpy.data.filepath).stem
        else:
            root = Path(tempfile.gettempdir()) / "1782-92" / "checkpoints"
            stem = "unsaved"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{stem}.r{rev:05d}.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
        old = sorted(root.glob(f"{stem}.r*.blend"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in old[12:]:
            try:
                stale.unlink()
            except OSError:
                pass
        return path.name
    except Exception:
        return None


def _apply(payload: dict[str, Any]) -> dict[str, Any]:
    global _REV, _LAST_CHANGED
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code_required"}

    try:
        tree = _validate_code(code)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    before = _snapshot()
    output = io.StringIO()
    started = time.perf_counter()
    env = {
        "__builtins__": _SAFE_BUILTINS,
        "bpy": bpy,
        "bmesh": bmesh,
        "math": math,
        "Vector": Vector,
        "Matrix": Matrix,
        "Euler": Euler,
        "Quaternion": Quaternion,
    }

    try:
        compiled = compile(tree, "<1782-92.apply>", "exec")
        with contextlib.redirect_stdout(output):
            exec(compiled, env, {})
        bpy.context.view_layer.update()
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
        }

    after = _snapshot()
    changed = sorted(key for key, value in after.items() if before.get(key) != value)
    removed = sorted(key for key in before if key not in after)
    _REV += 1
    _LAST_CHANGED = changed + [f"-{value}" for value in removed]
    checkpoint = _checkpoint(_REV)
    reply: dict[str, Any] = {
        "ok": True,
        "rev": _REV,
        "changed": _LAST_CHANGED,
        "checkpoint": checkpoint,
        "ms": round((time.perf_counter() - started) * 1000),
    }
    printed = output.getvalue().strip()
    if printed:
        reply["out"] = printed[-1000:]
    return reply


def _targets(ids: Any) -> list[bpy.types.Object]:
    allowed = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
    if isinstance(ids, list) and ids:
        result = []
        for value in ids:
            if not isinstance(value, str):
                continue
            obj = _find_id(value)
            if obj and obj.type in allowed:
                result.append(obj)
        return result
    return [obj for obj in bpy.context.scene.objects if obj.type in allowed and not obj.hide_render]


def _world_bounds(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    points: list[Vector] = []
    for obj in objects:
        try:
            points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
        except Exception:
            continue
    if not points:
        raise ValueError("no_renderable_objects")
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return lo, hi


def _look_at(camera: bpy.types.Object, center: Vector) -> None:
    direction = center - camera.location
    camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def _render(payload: dict[str, Any]) -> dict[str, Any]:
    views = payload.get("views") or ["front", "side", "back", "three_quarter"]
    valid_views = {"front", "side", "back", "three_quarter"}
    if not isinstance(views, list) or any(view not in valid_views for view in views):
        return {"ok": False, "error": "views_invalid"}
    size = int(payload.get("size") or 512)
    size = max(128, min(size, 1024))

    objects = _targets(payload.get("ids"))
    if not objects:
        return {"ok": False, "error": "no_renderable_objects"}

    try:
        lo, hi = _world_bounds(objects)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    center = (lo + hi) * 0.5
    ext = hi - lo
    horizontal = max(ext.x, ext.y, 0.5)
    height = max(ext.z, 0.5)
    ortho = max(height, horizontal * 1.2) * 1.18
    distance = max(ext.length * 2.0, 10.0)

    scene = bpy.context.scene
    old_camera = scene.camera
    old_engine = scene.render.engine
    old_x, old_y = scene.render.resolution_x, scene.render.resolution_y
    old_pct = scene.render.resolution_percentage
    old_path = scene.render.filepath
    old_format = scene.render.image_settings.file_format
    old_transparent = scene.render.film_transparent

    cam_data = bpy.data.cameras.new("__1782_camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho
    camera = bpy.data.objects.new("__1782_camera", cam_data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    for candidate in ("BLENDER_WORKBENCH", "BLENDER_WORKBENCH_NEXT"):
        try:
            scene.render.engine = candidate
            break
        except TypeError:
            continue

    try:
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "MATERIAL"
        shading.show_shadows = True
        shading.show_cavity = True
    except Exception:
        pass

    scene.render.resolution_x = size
    scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = True

    directions = {
        "front": Vector((0.0, -1.0, 0.0)),
        "side": Vector((1.0, 0.0, 0.0)),
        "back": Vector((0.0, 1.0, 0.0)),
        "three_quarter": Vector((0.707106, -0.707106, 0.0)),
    }

    images: list[dict[str, str]] = []
    try:
        for view in views:
            camera.location = center + directions[view] * distance
            _look_at(camera, center)
            fd, temp_name = tempfile.mkstemp(prefix=f"1782-{view}-", suffix=".png")
            os.close(fd)
            try:
                scene.render.filepath = temp_name
                bpy.ops.render.render(write_still=True)
                data = Path(temp_name).read_bytes()
                images.append({
                    "view": view,
                    "mime": "image/png",
                    "data": base64.b64encode(data).decode("ascii"),
                })
            finally:
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
    except Exception as exc:
        return {"ok": False, "error": f"render:{type(exc).__name__}:{exc}"}
    finally:
        scene.camera = old_camera
        try:
            scene.render.engine = old_engine
        except Exception:
            pass
        scene.render.resolution_x = old_x
        scene.render.resolution_y = old_y
        scene.render.resolution_percentage = old_pct
        scene.render.filepath = old_path
        scene.render.image_settings.file_format = old_format
        scene.render.film_transparent = old_transparent
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(cam_data)

    return {"ok": True, "rev": _REV, "images": images}


def _dispatch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path == "/inspect":
        return _inspect(payload)
    if path == "/apply":
        return _apply(payload)
    if path == "/render":
        return _render(payload)
    return {"ok": False, "error": "route_not_found"}


def _pump() -> float | None:
    if not _RUNNING:
        return None
    for _ in range(8):
        try:
            job = _QUEUE.get_nowait()
        except queue.Empty:
            break
        try:
            job["result"] = _dispatch(job["path"], job["payload"])
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
    server_version = "1782-92/0.1"

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

        timeout = 250.0 if self.path == "/render" else 190.0 if self.path == "/apply" else 20.0
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
    payload = {
        "host": "127.0.0.1",
        "port": port,
        "token": _TOKEN,
        "pid": os.getpid(),
        "version": VERSION,
    }
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
        if not bpy.app.timers.is_registered(_pump):
            bpy.app.timers.register(_pump, first_interval=0.03, persistent=True)
        _SERVER_THREAD = threading.Thread(target=_SERVER.serve_forever, name="1782-92-http", daemon=True)
        _SERVER_THREAD.start()
        _write_state(int(_SERVER.server_address[1]))
        return True, f"127.0.0.1:{_SERVER.server_address[1]}"
    except Exception as exc:
        _RUNNING = False
        return False, f"{type(exc).__name__}:{exc}"


def stop_bridge() -> None:
    global _SERVER, _SERVER_THREAD, _TOKEN, _RUNNING
    _RUNNING = False
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


class P178292_OT_start(bpy.types.Operator):
    bl_idname = "p178292.start"
    bl_label = "Start"

    def execute(self, context: bpy.types.Context):
        ok, message = start_bridge()
        self.report({"INFO" if ok else "ERROR"}, message)
        return {"FINISHED" if ok else "CANCELLED"}


class P178292_OT_stop(bpy.types.Operator):
    bl_idname = "p178292.stop"
    bl_label = "Stop"

    def execute(self, context: bpy.types.Context):
        stop_bridge()
        self.report({"INFO"}, "stopped")
        return {"FINISHED"}


class P178292_PT_panel(bpy.types.Panel):
    bl_label = "1782-92"
    bl_idname = "P178292_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "1782-92"

    def draw(self, context: bpy.types.Context) -> None:
        layout = self.layout
        layout.label(text="RUNNING" if _RUNNING else "STOPPED")
        layout.operator("p178292.stop" if _RUNNING else "p178292.start")


_CLASSES = (P178292_OT_start, P178292_OT_stop, P178292_PT_panel)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    stop_bridge()
    if bpy.app.timers.is_registered(_pump):
        try:
            bpy.app.timers.unregister(_pump)
        except Exception:
            pass
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
