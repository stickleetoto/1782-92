from __future__ import annotations

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

from . import policy, quality, references

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
_EXTERNAL_DIRTY = False
_SUPPRESS_EXTERNAL = False

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len, "list": list,
    "max": max, "min": min, "print": print, "range": range, "reversed": reversed,
    "round": round, "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip,
}


def _uid(value: Any) -> int:
    uid = getattr(value, "session_uid", None)
    if isinstance(uid, int) and uid > 0:
        return uid
    return int(value.as_pointer())


def _compact(value: float) -> float:
    return round(float(value), 5)


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
        _refresh_ids()
        obj = _find_id(value)
    if obj is None:
        raise KeyError(f"object_not_found:{value}")
    if _TRACKING:
        _TOUCHED_UIDS.add(_uid(obj))
    return obj


def _reference(value: str) -> bpy.types.Image:
    return references.image(value)


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
    global _EXTERNAL_DIRTY
    if not _TRACKING:
        _invalidate_render_cache()
        if not _SUPPRESS_EXTERNAL:
            _EXTERNAL_DIRTY = True
        return
    for update in depsgraph.updates:
        block = update.id
        if isinstance(block, bpy.types.Object):
            _DIRTY_UIDS.add(_uid(block))
        else:
            _DIRTY_UIDS.update(_DATA_OWNERS.get(_uid(block), ()))


def reset_session() -> None:
    global _REV, _LAST_CHANGED, _NEXT_ID, _LAST_CHECKPOINT_REV, _LAST_CHECKPOINT_AT, _EXTERNAL_DIRTY
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
    _EXTERNAL_DIRTY = False
    references.reset()
    _invalidate_render_cache()
    _refresh_ids()


def _basic_detail(obj: bpy.types.Object, q: str) -> dict[str, Any]:
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


def _mesh_detail(obj: bpy.types.Object) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "rev": _REV, "id": _id_for(obj), "type": obj.type}
    if obj.type != "MESH" or obj.data is None:
        result["mesh"] = None
        return result
    mesh = obj.data
    modifiers = [[m.type, m.name, int(m.show_viewport), int(m.show_render)] for m in obj.modifiers]
    keys: list[str] = []
    shape_keys = getattr(mesh, "shape_keys", None)
    if shape_keys is not None:
        keys = [block.name for block in shape_keys.key_blocks[:32]]
    result["mesh"] = {
        "v": len(mesh.vertices), "e": len(mesh.edges), "f": len(mesh.polygons),
        "mods": modifiers[:32], "keys": keys,
    }
    return result


def _uv_detail(obj: bpy.types.Object) -> dict[str, Any]:
    result: dict[str, Any] = {"ok": True, "rev": _REV, "id": _id_for(obj)}
    if obj.type != "MESH" or obj.data is None:
        result["uv"] = []
        return result
    mesh = obj.data
    active = mesh.uv_layers.active.name if mesh.uv_layers.active else None
    result["uv"] = [[layer.name, int(layer.active_render)] for layer in mesh.uv_layers[:32]]
    result["active"] = active
    return result


def _material_detail(obj: bpy.types.Object) -> dict[str, Any]:
    rows: list[list[Any]] = []
    for slot in obj.material_slots[:32]:
        mat = slot.material
        if mat is None:
            rows.append([None])
            continue
        node_counts: dict[str, int] = {}
        if mat.use_nodes and mat.node_tree is not None:
            for node in mat.node_tree.nodes:
                node_counts[node.type] = node_counts.get(node.type, 0) + 1
        compact_nodes = sorted(node_counts.items(), key=lambda item: (-item[1], item[0]))[:12]
        rows.append([mat.name, int(mat.use_nodes), compact_nodes])
    return {"ok": True, "rev": _REV, "id": _id_for(obj), "mat": rows}


def _rig_detail(obj: bpy.types.Object) -> dict[str, Any]:
    if obj.type == "ARMATURE" and obj.data is not None:
        bones = obj.data.bones
        roots = [bone.name for bone in bones if bone.parent is None][:24]
        return {"ok": True, "rev": _REV, "id": _id_for(obj), "rig": {"bones": len(bones), "roots": roots}}
    if obj.type == "MESH":
        armatures: list[str | None] = []
        for modifier in obj.modifiers:
            if modifier.type == "ARMATURE":
                armatures.append(_id_for(getattr(modifier, "object", None)))
        groups = [group.name for group in obj.vertex_groups[:32]]
        return {
            "ok": True, "rev": _REV, "id": _id_for(obj),
            "rig": {"arm": armatures, "groups": len(obj.vertex_groups), "names": groups},
        }
    return {"ok": True, "rev": _REV, "id": _id_for(obj), "rig": None}


def _api_info(path: str) -> dict[str, Any]:
    if not path.startswith("bpy.ops."):
        return {"ok": False, "error": "api_scope"}
    parts = path.split(".")
    if len(parts) != 4:
        return {"ok": False, "error": "api_path"}
    try:
        group = getattr(bpy.ops, parts[2])
        op = getattr(group, parts[3])
        rna = op.get_rna_type()
        args: list[list[Any]] = []
        for prop in rna.properties:
            if prop.identifier == "rna_type":
                continue
            default: Any = getattr(prop, "default", None)
            if isinstance(default, set):
                default = sorted(default)
            elif not isinstance(default, (str, int, float, bool, type(None))):
                default = str(default)
            args.append([prop.identifier, prop.type, default])
        desc = str(getattr(rna, "description", "") or "")[:240]
        return {"ok": True, "api": path, "desc": desc, "args": args[:40]}
    except Exception as exc:
        return {"ok": False, "error": f"api:{type(exc).__name__}:{exc}"}


def inspect(payload: dict[str, Any]) -> dict[str, Any]:
    global _EXTERNAL_DIRTY
    _refresh_ids()
    q = str(payload.get("q") or "summary")
    scene = bpy.context.scene
    active = bpy.context.view_layer.objects.active

    if q == "summary":
        counts: dict[str, int] = {}
        for obj in scene.objects:
            counts[obj.type] = counts.get(obj.type, 0) + 1
        reply: dict[str, Any] = {
            "ok": True, "rev": _REV,
            "file": Path(bpy.data.filepath).name if bpy.data.filepath else "<unsaved>",
            "counts": counts, "active": _id_for(active),
            "selected": [_id_for(obj) for obj in bpy.context.selected_objects],
            "changed": _LAST_CHANGED,
        }
        if _EXTERNAL_DIRTY:
            reply["ext"] = 1
            _EXTERNAL_DIRTY = False
        return reply
    if q == "objects":
        return {"ok": True, "rev": _REV, "objects": [[_id_for(obj), obj.name, obj.type] for obj in scene.objects]}
    if q == "selection":
        return {"ok": True, "rev": _REV, "objects": [[_id_for(obj), obj.name, obj.type] for obj in bpy.context.selected_objects]}
    if q == "materials":
        return {"ok": True, "rev": _REV, "materials": [[i, mat.name] for i, mat in enumerate(bpy.data.materials)]}
    if q == "refs":
        result = references.inspect_refs()
        result["rev"] = _REV
        return result
    if q.startswith("api:"):
        return _api_info(q[4:])
    if q == "quality":
        result = quality.inspect_quality(scene.objects, _id_for)
        result["rev"] = _REV
        return result
    if q.startswith("quality:"):
        obj = _find_id(q[8:])
        if obj is None:
            return {"ok": False, "error": "object_not_found"}
        result = quality.inspect_quality([obj], _id_for)
        result["rev"] = _REV
        return result

    object_id, sep, section = q.partition(":")
    obj = _find_id(object_id)
    if obj is None:
        return {"ok": False, "error": "object_not_found"}
    if not sep:
        return _basic_detail(obj, object_id)
    if section == "mesh":
        return _mesh_detail(obj)
    if section == "uv":
        return _uv_detail(obj)
    if section == "mat":
        return _material_detail(obj)
    if section == "rig":
        return _rig_detail(obj)
    return {"ok": False, "error": "inspect_section"}


def _checkpoint(rev: int) -> str | None:
    global _LAST_CHECKPOINT_AT, _LAST_CHECKPOINT_REV, _SUPPRESS_EXTERNAL
    try:
        if bpy.data.filepath:
            root = Path(bpy.data.filepath).parent / ".1782-92" / "checkpoints"
            stem = Path(bpy.data.filepath).stem
        else:
            root = Path(tempfile.gettempdir()) / "1782-92" / "checkpoints"
            stem = "unsaved"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{stem}.r{rev:05d}.blend"
        _SUPPRESS_EXTERNAL = True
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
    finally:
        _SUPPRESS_EXTERNAL = False


def _checkpoint_if_due(rev: int) -> str | None:
    age = time.monotonic() - _LAST_CHECKPOINT_AT if _LAST_CHECKPOINT_AT else float("inf")
    if rev == 1 or rev - _LAST_CHECKPOINT_REV >= CHECKPOINT_EVERY or age >= CHECKPOINT_MAX_AGE_S:
        return _checkpoint(rev)
    return None


def _capture_context() -> dict[str, Any]:
    active = bpy.context.view_layer.objects.active
    return {
        "mode": bpy.context.mode,
        "active": _uid(active) if active is not None else None,
        "selected": [_uid(obj) for obj in bpy.context.selected_objects],
    }


def _object_mode() -> bool:
    if bpy.context.mode == "OBJECT":
        return True
    active = bpy.context.view_layer.objects.active
    if active is None:
        return False
    try:
        bpy.ops.object.mode_set(mode="OBJECT")
        return bpy.context.mode == "OBJECT"
    except Exception:
        return False


def _restore_context(snapshot: dict[str, Any]) -> None:
    _object_mode()
    _refresh_ids()
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    selected = set(snapshot.get("selected") or [])
    for uid in selected:
        obj = _UID_OBJECTS.get(uid)
        if obj is not None:
            obj.select_set(True)
    active_uid = snapshot.get("active")
    active = _UID_OBJECTS.get(active_uid) if isinstance(active_uid, int) else None
    if active is not None:
        bpy.context.view_layer.objects.active = active
    mode = str(snapshot.get("mode") or "OBJECT")
    mode_map = {
        "EDIT_MESH": "EDIT", "EDIT_CURVE": "EDIT", "EDIT_SURFACE": "EDIT",
        "EDIT_TEXT": "EDIT", "EDIT_ARMATURE": "EDIT", "EDIT_METABALL": "EDIT",
        "POSE": "POSE", "SCULPT": "SCULPT", "PAINT_WEIGHT": "WEIGHT_PAINT",
        "PAINT_VERTEX": "VERTEX_PAINT", "PAINT_TEXTURE": "TEXTURE_PAINT",
    }
    target = mode_map.get(mode)
    if active is not None and target:
        try:
            bpy.ops.object.mode_set(mode=target)
        except Exception:
            pass


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
    global _REV, _LAST_CHANGED, _TRACKING, _EXTERNAL_DIRTY
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return {"ok": False, "error": "code_required"}
    try:
        tree = policy.validate_code(code)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    context_before = _capture_context()
    if not _object_mode():
        return {"ok": False, "error": "context_mode"}

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
        "O": _object, "REF": _reference,
    }
    try:
        compiled = compile(tree, "<1782-92.apply>", "exec")
        _TRACKING = True
        with contextlib.redirect_stdout(output):
            exec(compiled, env, {})
        bpy.context.view_layer.update()
        _object_mode()
    except Exception as exc:
        _TRACKING = False
        rolled_back = _rollback_once() if undo_ready else False
        _refresh_ids()
        _restore_context(context_before)
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
    _EXTERNAL_DIRTY = False
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


def _set_render_engine(scene: bpy.types.Scene, candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        try:
            scene.render.engine = candidate
            return candidate
        except (TypeError, ValueError):
            continue
    raise RuntimeError("render_engine_unavailable")


def _studio_world(center: Vector, distance: float) -> tuple[bpy.types.World, list[bpy.types.Object]]:
    world = bpy.data.worlds.new("__1782_world")
    world.use_nodes = True
    if world.node_tree is not None:
        bg = world.node_tree.nodes.get("Background")
        if bg is not None:
            bg.inputs["Color"].default_value = (0.035, 0.035, 0.04, 1.0)
            bg.inputs["Strength"].default_value = 0.55
    lights: list[bpy.types.Object] = []
    setups = [
        ((-0.7, -0.8, 0.9), 900.0, 5.0),
        ((0.8, -0.25, 0.45), 550.0, 4.0),
        ((0.2, 0.9, 0.7), 700.0, 4.0),
    ]
    for index, (direction, energy, size) in enumerate(setups):
        data = bpy.data.lights.new(f"__1782_light_{index}", type="AREA")
        data.energy = energy
        data.shape = "DISK"
        data.size = size
        obj = bpy.data.objects.new(f"__1782_light_{index}", data)
        bpy.context.scene.collection.objects.link(obj)
        vec = Vector(direction).normalized()
        obj.location = center + vec * max(distance * 0.7, 4.0)
        _look_at(obj, center)
        lights.append(obj)
    return world, lights


def render(payload: dict[str, Any]) -> dict[str, Any]:
    global _RENDER_CACHE_KEY, _RENDER_CACHE_IMAGES, _SUPPRESS_EXTERNAL
    views = payload.get("views") or ["front", "three_quarter"]
    valid = {"front", "side", "back", "three_quarter"}
    if not isinstance(views, list) or not views or any(view not in valid for view in views):
        return {"ok": False, "error": "views_invalid"}
    mode = str(payload.get("mode") or "fast")
    if mode not in {"fast", "lookdev", "wire"}:
        return {"ok": False, "error": "render_mode"}
    size = max(128, min(int(payload.get("size") or 512), 1024))
    ids = payload.get("ids")
    key = (_REV, tuple(views), tuple(ids) if isinstance(ids, list) else (), size, mode)
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
    old_camera = scene.camera
    old_engine = scene.render.engine
    old_world = scene.world
    old_x, old_y = scene.render.resolution_x, scene.render.resolution_y
    old_pct = scene.render.resolution_percentage
    old_path = scene.render.filepath
    old_format = scene.render.image_settings.file_format
    old_transparent = scene.render.film_transparent
    shading = scene.display.shading
    shading_state: dict[str, Any] = {}
    for attr in ("light", "color_type", "show_shadows", "show_cavity", "show_object_outline", "show_specular_highlight"):
        if hasattr(shading, attr):
            shading_state[attr] = getattr(shading, attr)
    wire_state = [(obj, obj.show_wire, obj.show_all_edges) for obj in objects]

    cam_data = bpy.data.cameras.new("__1782_camera")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = ortho
    camera = bpy.data.objects.new("__1782_camera", cam_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    temp_world: bpy.types.World | None = None
    temp_lights: list[bpy.types.Object] = []
    _SUPPRESS_EXTERNAL = True

    try:
        if mode == "lookdev":
            _set_render_engine(scene, ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"))
            temp_world, temp_lights = _studio_world(center, distance)
            scene.world = temp_world
        else:
            _set_render_engine(scene, ("BLENDER_WORKBENCH", "BLENDER_WORKBENCH_NEXT"))
            if hasattr(shading, "light"):
                shading.light = "STUDIO" if mode == "fast" else "FLAT"
            if hasattr(shading, "color_type"):
                shading.color_type = "MATERIAL"
            if hasattr(shading, "show_shadows"):
                shading.show_shadows = mode == "fast"
            if hasattr(shading, "show_cavity"):
                shading.show_cavity = True
            if hasattr(shading, "show_object_outline"):
                shading.show_object_outline = mode == "wire"
            if mode == "wire":
                for obj in objects:
                    obj.show_wire = True
                    obj.show_all_edges = True

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
        for view in views:
            camera.location = center + directions[view] * distance
            _look_at(camera, center)
            fd, temp_name = tempfile.mkstemp(prefix=f"1782-{mode}-{view}-", suffix=".png")
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
        scene.camera = old_camera
        scene.world = old_world
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
        for attr, value in shading_state.items():
            try:
                setattr(shading, attr, value)
            except Exception:
                pass
        for obj, show_wire, show_all_edges in wire_state:
            try:
                obj.show_wire = show_wire
                obj.show_all_edges = show_all_edges
            except Exception:
                pass
        for light in temp_lights:
            data = light.data
            bpy.data.objects.remove(light, do_unlink=True)
            if data is not None:
                bpy.data.lights.remove(data)
        if temp_world is not None:
            bpy.data.worlds.remove(temp_world)
        bpy.data.objects.remove(camera, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        _SUPPRESS_EXTERNAL = False

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
