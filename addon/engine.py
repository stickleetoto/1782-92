from __future__ import annotations

import ast
import base64
import contextlib
import io
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

CHECKPOINT_EVERY = 5
CHECKPOINT_MAX_AGE_S = 180.0
CHECKPOINT_KEEP = 8

_REV = 0
_LAST_CHANGED: list[str] = []
_NEXT_ID = 1
_OBJ_IDS: dict[int, str] = {}
_ID_TO_UID: dict[str, int] = {}
_UID_OBJECTS: dict[int, bpy.types.Object] = {}
_TRACKING = False
_DIRTY_UIDS: set[int] = set()
_TOUCHED_UIDS: set[int] = set()
_DATA_OWNERS: dict[int, set[int]] = {}
_LAST_CHECKPOINT_REV = 0
_LAST_CHECKPOINT_AT = 0.0
_RENDER_CACHE_KEY: tuple[Any, ...] | None = None
_RENDER_CACHE_IMAGES: list[dict[str, str]] | None = None


def _uid(value: Any) -> int:
    uid = getattr(value, "session_uid", None)
    if isinstance(uid, int) and uid > 0:
        return uid
    return int(value.as_pointer())


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
    "__import__", "breakpoint", "compile", "delattr", "eval", "exec", "getattr",
    "globals", "help", "input", "locals", "open", "setattr", "vars",
}
_BANNED_PREFIXES = (
    "bpy.app", "bpy.context.preferences", "bpy.data.libraries", "bpy.ops.console",
    "bpy.ops.export", "bpy.ops.import", "bpy.ops.preferences", "bpy.ops.script",
    "bpy.ops.wm", "bpy.path", "bpy.utils",
)
_BANNED_ATTRS = {"load", "load_post", "load_pre", "save", "save_post", "save_pre", "save_render", "write"}
_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len, "list": list,
    "max": max, "min": min, "print": print, "range": range, "reversed": reversed,
    "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip,
}


def _validate_code(code: str) -> ast.AST:
    if len(code) > 65_536:
        raise ValueError("code_too_large")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"syntax@{exc.lineno}:{exc.offset}:{exc.msg}") from exc
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


def _id_for(obj: bpy.types.Object | None) -> str | None:
    global _NEXT_ID
    if obj is None:
        return None
    uid = _uid(obj)
    short = _OBJ_IDS.get(uid)
    if short is None:
        short = f"o{_NEXT_ID}"
        _NEXT_ID += 1
        _OBJ_IDS[uid] = short
        _ID_TO_UID[short] = uid
    _UID_OBJECTS[uid] = obj
    return short


def _refresh_ids(prune: bool = True) -> set[int]:
    live: dict[int, bpy.types.Object] = {}
    for obj in bpy.data.objects:
        uid = _uid(obj)
        live[uid] = obj
        _id_for(obj)
    _UID_OBJECTS.clear()
    _UID_OBJECTS.update(live)
    if prune:
        for uid in [uid for uid in _OBJ_IDS if uid not in live]:
            short = _OBJ_IDS.pop(uid)
            _ID_TO_UID.pop(short, None)
    return set(live)


def _find_id(value: str) -> bpy.types.Object | None:
    uid = _ID_TO_UID.get(value)
    return _UID_OBJECTS.get(uid) if uid is not None else None


def _object(value: str) -> bpy.types.Object:
    obj = _find_id(value)
    if obj is None:
        raise KeyError(f"object_not_found:{value}")
    if _TRACKING:
        _TOUCHED_UIDS.add(_uid(obj))
    return obj


def _build_owner_index() -> None:
    _DATA_OWNERS.clear()
    for obj in bpy.data.objects:
        owner = _uid(obj)
        data = getattr(obj, "data", None)
        if data is not None:
            _DATA_OWNERS.setdefault(_uid(data), set()).add(owner)
        for slot in getattr(obj, "material_slots", ()):
            material = getattr(slot, "material", None)
            if material is not None:
                _DATA_OWNERS.setdefault(_uid(material), set()).add(owner)


def _invalidate_render_cache() -> None:
    global _RENDER_CACHE_KEY, _RENDER_CACHE_IMAGES
    _RENDER_CACHE_KEY = None
    _RENDER_CACHE_IMAGES = None


def _depsgraph_handler(_scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    if not _TRACKING:
        _invalidate_render_cache()
        return
    for update in depsgraph.updates:
        block = update.id
        if isinstance(block, bpy.types.Object):
            _DIRTY_UIDS.add(_uid(block))
        else:
            _DIRTY_UIDS.update(_DATA_OWNERS.get(_uid(block), ()))


def _load_post(_dummy: Any) -> None:
    reset_session()


def install_handlers() -> None:
    if _depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_handler)
    if _load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post)


def remove_handlers() -> None:
    if _depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_handler)
    if _load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post)


def reset_session() -> None:
    global _REV, _LAST_CHANGED, _NEXT_ID, _LAST_CHECKPOINT_REV, _LAST_CHECKPOINT_AT
    _REV = 0
    _LAST_CHANGED = []
    _NEXT_ID = 1
    _OBJ_IDS.clear()
    _ID_TO_UID.clear()
    _UID_OBJECTS.clear()
    _DIRTY_UIDS.clear()
    _TOUCHED_UIDS.clear()
    _DATA_OWNERS.clear()
    _LAST_CHECKPOINT_REV = 0
    _LAST_CHECKPOINT_AT = 0.0
    _invalidate_render_cache()
    _refresh_ids()


def inspect(payload: dict[str, Any]) -> dict[str, Any]:
    _refresh_ids()
    q = str(payload.get("q") or "summary")
    scene = bpy.context.scene
    active = bpy.context.view_layer.objects.active
    if q == "summary":
        counts: dict[str, int] = {}
        for obj in scene.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
        return {
            "ok": True, "rev": _REV,
            "file": Path(bpy.data.filepath).name if bpy.data.filepath else "<unsaved>",
            "counts": counts, "active": _id_for(active),
            "selected": [_id_for(obj) for obj in bpy.context.selected_objects],
            "changed": _LAST_CHANGED,
        }
    if q == "objects":
        return {"ok": True, "rev": _REV, "objects": [[_id_for(obj), obj.name, obj.type] for obj in scene.objects]}
    if q == "selection":
        return {"ok": True, "rev": _REV, "objects": [[_id_for(obj), obj.name, obj.type] for obj in bpy.context.selected_objects]}
    if q == "materials":
        return {"ok": True, "rev": _REV, "materials": [[i, mat.name] for i, mat in enumerate(bpy.data.materials)]}
    obj = _find_id(q)
    if obj is None:
        return {"ok": False, "error": "object_not_found"}
    detail: dict[str, Any] = {
        "ok": True, "rev": _REV, "id": q, "name": obj.name, "type": obj.type,
        "loc": [_compact(v) for v in obj.location], "rot": [_compact(v) for v in obj.rotation_euler],
        "scale": [_compact(v) for v in obj.scale], "dim": [_compact(v) for v in obj.dimensions],
        "parent": _id_for(obj.parent),
        "materials": [slot.material.name for slot in obj.material_slots if slot.material],
    }
    if obj.type == "MESH" and obj.data:
        detail["mesh"] = {"v": len(obj.data.vertices), "e": len(obj.data.edges), "f": len(obj.data.polygons)}
    return detail


def _checkpoint(rev: int) -> str | None:
    global _LAST_CHECKPOINT_AT, _LAST_CHECKPOINT_REV
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
        for stale in old[CHECKPOINT_KEEP:]:
            try:
                stale.unlink()
            except OSError:
                pass
        _LAST_CHECKPOINT_REV = rev
        _LAST_CHECKPOINT_AT = time.monotonic()
        return path.name
    except Exception:
        return None


def _checkpoint_if_due(rev: int) -> str | None:
    age = time.monotonic() - _LAST_CHECKPOINT_AT if _LAST_CHECKPOINT_AT else float("inf")
    if rev == 1 or rev - _LAST_CHECKPOINT_REV >= CHECKPOINT_EVERY or age >= CHECKPOINT_MAX_AGE_S:
        return _checkpoint(rev)
    return None


def _push_undo(label: str) -> bool:
    try:
        return "FINISHED" in bpy.ops.ed.undo_push(message=label)
    except Exception:
        return False


def _rollback_once() -> bool:
    try:
        result = bpy.ops.ed.undo()
        bpy.context.view_layer.update()
        return "FINISHED" in result
    except Exception:
        return False


def _error_location(exc: Exception) -> int | None:
    tb = exc.__traceback__
    line: int | None = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<1782-92.apply>":
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    global _REV, _LAST_CHANGED, _TRACKING
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code_required"}
    try:
        tree = _validate_code(code)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    before_uids = _refresh_ids(prune=False)
    before_ids = {uid: _OBJ_IDS[uid] for uid in before_uids}
    _build_owner_index()
    _DIRTY_UIDS.clear()
    _TOUCHED_UIDS.clear()
    output = io.StringIO()
    started = time.perf_counter()
    undo_ready = _push_undo(f"1782-92 r{_REV + 1}")
    env = {
        "__builtins__": _SAFE_BUILTINS, "bpy": bpy, "bmesh": bmesh, "math": math,
        "Vector": Vector, "Matrix": Matrix, "Euler": Euler, "Quaternion": Quaternion,
        "O": _object,
    }
    try:
        compiled = compile(tree, "<1782-92.apply>", "exec")
        _TRACKING = True
        with contextlib.redirect_stdout(output):
            exec(compiled, env, {})
        bpy.context.view_layer.update()
    except Exception as exc:
        _TRACKING = False
        rolled_back = _rollback_once() if undo_ready else False
        _refresh_ids()
        line = _error_location(exc)
        where = f"@{line}" if line is not None else ""
        suffix = ":rb" if rolled_back else ""
        return {"ok": False, "error": f"{type(exc).__name__}{where}:{exc}{suffix}"}
    finally:
        _TRACKING = False

    after_uids = _refresh_ids(prune=False)
    created = after_uids - before_uids
    removed = before_uids - after_uids
    changed_uids = ((_DIRTY_UIDS | _TOUCHED_UIDS) & after_uids) | created
    changed = sorted(_OBJ_IDS[uid] for uid in changed_uids if uid in _OBJ_IDS)
    removed_ids = sorted(before_ids[uid] for uid in removed if uid in before_ids)
    _refresh_ids(prune=True)
    _REV += 1
    _LAST_CHANGED = changed + [f"-{value}" for value in removed_ids]
    _invalidate_render_cache()

    reply: dict[str, Any] = {"ok": True, "rev": _REV}
    if _LAST_CHANGED:
        reply["changed"] = _LAST_CHANGED
    checkpoint = _checkpoint_if_due(_REV)
    if checkpoint:
        reply["checkpoint"] = checkpoint
    elapsed = round((time.perf_counter() - started) * 1000)
    if elapsed >= 10:
        reply["ms"] = elapsed
    printed = output.getvalue().strip()
    if printed:
        reply["out"] = printed[-800:]
    return reply


def _targets(ids: Any) -> list[bpy.types.Object]:
    _refresh_ids()
    allowed = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
    if isinstance(ids, list) and ids:
        result = []
        for value in ids:
            obj = _find_id(value) if isinstance(value, str) else None
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
    return (
        Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points))),
        Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points))),
    )


def _look_at(camera: bpy.types.Object, center: Vector) -> None:
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()


def render(payload: dict[str, Any]) -> dict[str, Any]:
    global _RENDER_CACHE_KEY, _RENDER_CACHE_IMAGES
    views = payload.get("views") or ["front", "three_quarter"]
    valid = {"front", "side", "back", "three_quarter"}
    if not isinstance(views, list) or not views or any(view not in valid for view in views):
        return {"ok": False, "error": "views_invalid"}
    size = max(128, min(int(payload.get("size") or 512), 1024))
    ids = payload.get("ids")
    key = (_REV, tuple(views), tuple(ids) if isinstance(ids, list) else (), size)
    if key == _RENDER_CACHE_KEY and _RENDER_CACHE_IMAGES is not None:
        return {"ok": True, "rev": _REV, "images": _RENDER_CACHE_IMAGES}

    objects = _targets(ids)
    if not objects:
        return {"ok": False, "error": "no_renderable_objects"}
    try:
        lo, hi = _world_bounds(objects)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    center = (lo + hi) * 0.5
    ext = hi - lo
    ortho = max(max(ext.z, 0.5), max(ext.x, ext.y, 0.5) * 1.2) * 1.18
    distance = max(ext.length * 2.0, 10.0)

    scene = bpy.context.scene
    old = (
        scene.camera, scene.render.engine, scene.render.resolution_x, scene.render.resolution_y,
        scene.render.resolution_percentage, scene.render.filepath, scene.render.image_settings.file_format,
        scene.render.film_transparent,
    )
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
        "front": Vector((0.0, -1.0, 0.0)), "side": Vector((1.0, 0.0, 0.0)),
        "back": Vector((0.0, 1.0, 0.0)), "three_quarter": Vector((0.707106, -0.707106, 0.0)),
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
                images.append({"view": view, "mime": "image/png", "data": base64.b64encode(data).decode("ascii")})
            finally:
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
    except Exception as exc:
        return {"ok": False, "error": f"render:{type(exc).__name__}:{exc}"}
    finally:
        (
            scene.camera, old_engine, scene.render.resolution_x, scene.render.resolution_y,
            scene.render.resolution_percentage, scene.render.filepath, scene.render.image_settings.file_format,
            scene.render.film_transparent,
        ) = old
        try:
            scene.render.engine = old_engine
        except Exception:
            pass
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
    _RENDER_CACHE_KEY = key
    _RENDER_CACHE_IMAGES = images
    return {"ok": True, "rev": _REV, "images": images}


def dispatch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path == "/inspect":
        return inspect(payload)
    if path == "/apply":
        return apply(payload)
    if path == "/render":
        return render(payload)
    return {"ok": False, "error": "route_not_found"}
