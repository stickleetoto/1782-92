from __future__ import annotations

import base64
import math
import os
import tempfile
from pathlib import Path
from typing import Any

import bpy

from . import engine

MAX_TREE_ROWS = 64
MAX_FIND_ROWS = 16
MAX_NEAR_ROWS = 8
# Keep the encoded MCP image comfortably below the bridge-wide 8 MiB base64
# response budget. PNG base64 expands raw bytes by about 4/3.
MAX_VIEWPORT_BYTES = 6 * 1024 * 1024
_SPATIAL_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}


def _compact(value: float) -> float:
    return round(float(value), 5)


def _tree_info() -> dict[str, Any]:
    engine._refresh_ids()
    objects = sorted(
        bpy.context.scene.objects,
        key=lambda obj: ((obj.parent.name.casefold() if obj.parent else ""), obj.name.casefold(), obj.type),
    )
    rows = [
        [engine._id_for(obj), obj.name, obj.type, engine._id_for(obj.parent)]
        for obj in objects[:MAX_TREE_ROWS]
    ]
    reply: dict[str, Any] = {"ok": True, "rev": engine._REV, "count": len(objects), "tree": rows}
    if len(objects) > MAX_TREE_ROWS:
        reply["more"] = len(objects) - MAX_TREE_ROWS
        reply["hint"] = "use find:TERM or collection:NAME"
    return reply


def _find_info(term: str) -> dict[str, Any]:
    query = term.strip()
    if not query:
        return {"ok": False, "error": "find_query_required"}
    needle = query.casefold()
    engine._refresh_ids()
    matches: list[tuple[int, str, bpy.types.Object]] = []
    for obj in bpy.context.scene.objects:
        name = obj.name.casefold()
        if needle not in name:
            continue
        rank = 0 if name == needle else 1 if name.startswith(needle) else 2
        matches.append((rank, name, obj))
    matches.sort(key=lambda row: (row[0], row[1], row[2].type))
    rows = [[engine._id_for(obj), obj.name, obj.type] for _, _, obj in matches[:MAX_FIND_ROWS]]
    reply: dict[str, Any] = {"ok": True, "rev": engine._REV, "q": query, "matches": rows}
    if len(matches) > MAX_FIND_ROWS:
        reply["more"] = len(matches) - MAX_FIND_ROWS
    return reply


def _bounds(obj: bpy.types.Object):
    try:
        return engine._world_bounds([obj])
    except ValueError:
        return None


def _axis_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    if a1 < b0:
        return b0 - a1
    if b1 < a0:
        return a0 - b1
    return 0.0


def _spatial_info(object_id: str) -> dict[str, Any]:
    engine._refresh_ids()
    target = engine._find_id(object_id)
    if target is None:
        return {"ok": False, "error": "object_not_found"}
    target_bounds = _bounds(target)
    if target_bounds is None:
        return {"ok": False, "error": "object_has_no_bounds"}

    lo, hi = target_bounds
    center = (lo + hi) * 0.5
    near: list[tuple[float, float, str, bpy.types.Object]] = []
    overlaps = 0
    for obj in bpy.context.scene.objects:
        if obj is target or obj.type not in _SPATIAL_TYPES or obj.hide_viewport:
            continue
        bounds = _bounds(obj)
        if bounds is None:
            continue
        other_lo, other_hi = bounds
        other_center = (other_lo + other_hi) * 0.5
        dx = _axis_gap(lo.x, hi.x, other_lo.x, other_hi.x)
        dy = _axis_gap(lo.y, hi.y, other_lo.y, other_hi.y)
        dz = _axis_gap(lo.z, hi.z, other_lo.z, other_hi.z)
        gap = math.sqrt(dx * dx + dy * dy + dz * dz)
        center_distance = (other_center - center).length
        if gap <= 1e-8:
            overlaps += 1
        near.append((gap, center_distance, obj.name.casefold(), obj))

    near.sort(key=lambda row: (row[0], row[1], row[2]))
    rows = [
        [engine._id_for(obj), obj.name, _compact(gap), _compact(center_distance)]
        for gap, center_distance, _, obj in near[:MAX_NEAR_ROWS]
    ]
    return {
        "ok": True,
        "rev": engine._REV,
        "id": object_id,
        "center": [_compact(value) for value in center],
        "bounds": [[_compact(value) for value in lo], [_compact(value) for value in hi]],
        "near": rows,
        "overlaps": overlaps,
    }


def inspect(payload: dict[str, Any]) -> dict[str, Any] | None:
    q = str(payload.get("q") or "summary")
    if q == "tree":
        return _tree_info()
    if q.startswith("find:"):
        return _find_info(q[5:])
    if q.endswith(":spatial"):
        return _spatial_info(q[:-8])
    return None


def _viewport_capture() -> dict[str, Any]:
    if bpy.app.background:
        return {"ok": False, "error": "viewport_unavailable:background"}

    window = None
    area = None
    for candidate_window in bpy.context.window_manager.windows:
        for candidate_area in candidate_window.screen.areas:
            if candidate_area.type == "VIEW_3D":
                window = candidate_window
                area = candidate_area
                break
        if area is not None:
            break
    if window is None or area is None:
        return {"ok": False, "error": "viewport_unavailable:no_view3d"}

    handle, raw_path = tempfile.mkstemp(prefix="1782-92-viewport-", suffix=".png")
    os.close(handle)
    path = Path(raw_path)
    try:
        try:
            path.unlink()
        except OSError:
            pass
        with bpy.context.temp_override(window=window, screen=window.screen, area=area):
            result = bpy.ops.screen.screenshot_area(filepath=str(path), check_existing=False, hide_props_region=True)
        if "FINISHED" not in result or not path.exists():
            return {"ok": False, "error": "viewport_capture_failed"}
        size = path.stat().st_size
        if size > MAX_VIEWPORT_BYTES:
            return {"ok": False, "error": f"viewport_too_large:{size}>{MAX_VIEWPORT_BYTES}"}
        data = path.read_bytes()
        return {
            "ok": True,
            "rev": engine._REV,
            "viewport": 1,
            "images": [{"view": "viewport", "mime": "image/png", "data": base64.b64encode(data).decode("ascii")}],
        }
    except Exception as exc:
        return {"ok": False, "error": f"viewport:{type(exc).__name__}:{exc}"}
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def render(payload: dict[str, Any]) -> dict[str, Any] | None:
    if str(payload.get("mode") or "fast") != "viewport":
        return None
    if payload.get("ref") is not None or payload.get("refs") is not None:
        return {"ok": False, "error": "viewport_reference_conflict"}
    if payload.get("ids"):
        return {"ok": False, "error": "viewport_ids_unsupported"}
    return _viewport_capture()
