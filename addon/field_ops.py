from __future__ import annotations

import base64
import contextlib
import io
import os
import time
from pathlib import Path
from typing import Any

import bpy

from . import checkpoint_paths, engine, references

MAX_DIRECT_REFERENCE_BYTES = 16 * 1024 * 1024
MAX_RING_VERTICES = 100_000
MAX_RINGS = 64

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _compact(value: float) -> float:
    return round(float(value), 5)


def _object_structure() -> dict[int, tuple[Any, ...]]:
    rows: dict[int, tuple[Any, ...]] = {}
    for obj in bpy.data.objects:
        uid = engine._uid(obj)
        data = getattr(obj, "data", None)
        parent = getattr(obj, "parent", None)
        collections = tuple(sorted(engine._uid(collection) for collection in obj.users_collection))
        materials = tuple(
            engine._uid(slot.material) if slot.material is not None else 0
            for slot in getattr(obj, "material_slots", ())
        )
        rows[uid] = (
            obj.name,
            engine._uid(data) if data is not None else 0,
            engine._uid(parent) if parent is not None else 0,
            collections,
            materials,
        )
    return rows


def _collection_signature() -> tuple[tuple[Any, ...], ...]:
    return tuple(
        sorted(
            (
                engine._uid(collection),
                collection.name,
                tuple(sorted(engine._uid(obj) for obj in collection.objects)),
            )
            for collection in bpy.data.collections
        )
    )


def _scene_meta_signature() -> tuple[tuple[str, str], ...]:
    scene = bpy.context.scene
    return tuple(sorted((str(key), repr(value)[:256]) for key, value in scene.items()))


def _checkpoint(rev: int) -> str | None:
    try:
        root, stem = checkpoint_paths.checkpoint_parts(bpy.data.filepath or None)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{stem}.r{rev:05d}.blend"
        engine._SUPPRESS_EXTERNAL = True
        bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
        old = sorted(root.glob(f"{stem}.r*.blend"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in old[engine.CHECKPOINT_KEEP:]:
            try:
                stale.unlink()
            except OSError:
                pass
        engine._LAST_CHECKPOINT_REV = rev
        engine._LAST_CHECKPOINT_AT = time.monotonic()
        return path.name
    except Exception:
        return None
    finally:
        engine._SUPPRESS_EXTERNAL = False


def _checkpoint_if_due(rev: int) -> str | None:
    age = time.monotonic() - engine._LAST_CHECKPOINT_AT if engine._LAST_CHECKPOINT_AT else float("inf")
    if (
        rev == 1
        or rev - engine._LAST_CHECKPOINT_REV >= engine.CHECKPOINT_EVERY
        or age >= engine.CHECKPOINT_MAX_AGE_S
    ):
        return _checkpoint(rev)
    return None


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code_required"}
    try:
        tree = engine.policy.validate_code(code)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    context_before = engine._capture_context()
    if not engine._object_mode():
        return {"ok": False, "error": "context_mode"}

    before_uids = engine._refresh_ids(prune=False)
    before_ids = {uid: engine._OBJ_IDS[uid] for uid in before_uids}
    before_structure = _object_structure()
    before_collections = _collection_signature()
    before_scene_meta = _scene_meta_signature()

    engine._build_owner_index()
    engine._DIRTY_UIDS.clear()
    engine._TOUCHED_UIDS.clear()
    output = io.StringIO()
    started = time.perf_counter()
    undo_ready = engine._push_undo(f"1782-92 r{engine._REV + 1}")
    env = {
        "__builtins__": engine._SAFE_BUILTINS,
        "bpy": bpy,
        "bmesh": engine.bmesh,
        "math": engine.math,
        "Vector": engine.Vector,
        "Matrix": engine.Matrix,
        "Euler": engine.Euler,
        "Quaternion": engine.Quaternion,
        "O": engine._object,
        "REF": engine._reference,
    }

    try:
        compiled = compile(tree, "<1782-92.apply>", "exec")
        engine._TRACKING = True
        with contextlib.redirect_stdout(output):
            exec(compiled, env, {})
        bpy.context.view_layer.update()
        engine._object_mode()
    except Exception as exc:
        engine._TRACKING = False
        rolled_back = engine._rollback_once() if undo_ready else False
        engine._refresh_ids()
        engine._restore_context(context_before)
        line = engine._error_location(exc)
        where = f"@{line}" if line is not None else ""
        suffix = ":rb" if rolled_back else ""
        return {"ok": False, "error": f"{type(exc).__name__}{where}:{exc}{suffix}"}
    finally:
        engine._TRACKING = False

    after_uids = engine._refresh_ids(prune=False)
    after_structure = _object_structure()
    after_collections = _collection_signature()
    after_scene_meta = _scene_meta_signature()

    created = after_uids - before_uids
    removed = before_uids - after_uids
    structural_changed = {
        uid
        for uid in before_uids & after_uids
        if before_structure.get(uid) != after_structure.get(uid)
    }
    changed_uids = (engine._DIRTY_UIDS & after_uids) | structural_changed | created
    changed = sorted(engine._OBJ_IDS[uid] for uid in changed_uids if uid in engine._OBJ_IDS)
    removed_ids = sorted(before_ids[uid] for uid in removed if uid in before_ids)

    structural_scene_change = (
        before_collections != after_collections
        or before_scene_meta != after_scene_meta
    )
    mutated = bool(changed_uids or removed or structural_scene_change)
    engine._refresh_ids(prune=True)

    elapsed = round((time.perf_counter() - started) * 1000)
    printed = output.getvalue().strip()

    if not mutated:
        engine._LAST_CHANGED = []
        engine._EXTERNAL_DIRTY = False
        engine._restore_context(context_before)
        reply: dict[str, Any] = {"ok": True, "rev": engine._REV, "noop": 1}
        if elapsed >= 10:
            reply["ms"] = elapsed
        if printed:
            reply["out"] = printed[-800:]
        return reply

    engine._REV += 1
    engine._LAST_CHANGED = changed + [f"-{value}" for value in removed_ids]
    engine._EXTERNAL_DIRTY = False
    engine._invalidate_render_cache()

    reply = {"ok": True, "rev": engine._REV}
    if engine._LAST_CHANGED:
        reply["changed"] = engine._LAST_CHANGED
    checkpoint = _checkpoint_if_due(engine._REV)
    if checkpoint:
        reply["checkpoint"] = checkpoint
    if elapsed >= 10:
        reply["ms"] = elapsed
    if printed:
        reply["out"] = printed[-800:]
    return reply


def _reference_ids(payload: dict[str, Any]) -> list[str] | None:
    one = payload.get("ref")
    many = payload.get("refs")
    if one is not None and many is not None:
        raise ValueError("reference_conflict")
    if one is None and many is None:
        return None
    if one is not None:
        if not isinstance(one, str):
            raise ValueError("reference_invalid")
        values = [one]
    else:
        if not isinstance(many, list) or not many or len(many) > 4:
            raise ValueError("references_invalid")
        values = many
    if any(not isinstance(value, str) or not value.startswith("r") or not value[1:].isdigit() for value in values):
        raise ValueError("reference_invalid")
    return values


def _render_references(payload: dict[str, Any], ids: list[str]) -> dict[str, Any]:
    if payload.get("ids"):
        return {"ok": False, "error": "reference_scene_conflict"}
    images: list[dict[str, str]] = []
    try:
        for reference_id in ids:
            path = references.resolve(reference_id)
            stat = path.stat()
            if stat.st_size > MAX_DIRECT_REFERENCE_BYTES:
                return {"ok": False, "error": f"reference_preview_too_large:{reference_id}"}
            mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
            if mime is None:
                return {"ok": False, "error": f"reference_mime:{reference_id}"}
            data = path.read_bytes()
            images.append(
                {
                    "view": reference_id,
                    "mime": mime,
                    "data": base64.b64encode(data).decode("ascii"),
                }
            )
    except (KeyError, OSError, PermissionError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "rev": engine._REV, "reference": 1, "images": images}


def render(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        reference_ids = _reference_ids(payload)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if reference_ids is not None:
        return _render_references(payload, reference_ids)
    return engine.render(payload)


def _reference_info(reference_id: str) -> dict[str, Any]:
    try:
        path = references.resolve(reference_id)
        stat = path.stat()
        image = references.image(reference_id)
        width, height = int(image.size[0]), int(image.size[1])
    except (KeyError, OSError, PermissionError) as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "rev": engine._REV,
        "ref": [reference_id, path.name, width, height, max(1, (stat.st_size + 1023) // 1024)],
    }


def _bounds(object_id: str) -> dict[str, Any]:
    engine._refresh_ids()
    obj = engine._find_id(object_id)
    if obj is None:
        return {"ok": False, "error": "object_not_found"}
    try:
        lo, hi = engine._world_bounds([obj])
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "rev": engine._REV,
        "id": object_id,
        "bounds": [[_compact(v) for v in lo], [_compact(v) for v in hi]],
    }


def _rings(object_id: str) -> dict[str, Any]:
    engine._refresh_ids()
    obj = engine._find_id(object_id)
    if obj is None:
        return {"ok": False, "error": "object_not_found"}
    if obj.type != "MESH" or obj.data is None:
        return {"ok": True, "rev": engine._REV, "id": object_id, "rings": []}
    if len(obj.data.vertices) > MAX_RING_VERTICES:
        return {"ok": False, "error": "rings_too_large"}

    grouped: dict[float, list[Any]] = {}
    for vertex in obj.data.vertices:
        point = obj.matrix_world @ vertex.co
        grouped.setdefault(round(float(point.z), 5), []).append(point)

    rows: list[list[Any]] = []
    for z in sorted(grouped):
        points = grouped[z]
        rows.append(
            [
                _compact(z),
                len(points),
                _compact(min(point.x for point in points)),
                _compact(max(point.x for point in points)),
                _compact(min(point.y for point in points)),
                _compact(max(point.y for point in points)),
            ]
        )
    reply: dict[str, Any] = {"ok": True, "rev": engine._REV, "id": object_id, "rings": rows[:MAX_RINGS]}
    if len(rows) > MAX_RINGS:
        reply["more"] = len(rows) - MAX_RINGS
    return reply


def _collection_info(name: str) -> dict[str, Any]:
    if not name:
        return {
            "ok": True,
            "rev": engine._REV,
            "collections": [[collection.name, len(collection.objects)] for collection in bpy.data.collections[:128]],
        }
    collection = bpy.data.collections.get(name)
    if collection is None:
        return {"ok": False, "error": "collection_not_found"}
    engine._refresh_ids()
    rows = [[engine._id_for(obj), obj.name, obj.type] for obj in collection.objects[:128]]
    return {"ok": True, "rev": engine._REV, "collection": name, "count": len(collection.objects), "objects": rows}


def inspect(payload: dict[str, Any]) -> dict[str, Any]:
    q = str(payload.get("q") or "summary")
    if q.startswith("ref:"):
        return _reference_info(q[4:])
    if q.startswith("refs:r"):
        return _reference_info(q[5:])
    if q == "collections":
        return _collection_info("")
    if q.startswith("collection:"):
        return _collection_info(q[len("collection:"):])
    if q.endswith(":bounds"):
        return _bounds(q[:-7])
    if q.endswith(":rings"):
        return _rings(q[:-6])
    return engine.inspect(payload)


def dispatch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path == "/inspect":
        return inspect(payload)
    if path == "/apply":
        return apply(payload)
    if path == "/render":
        return render(payload)
    return {"ok": False, "error": "route_not_found"}
