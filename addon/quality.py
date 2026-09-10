from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import bmesh
import bpy

MAX_BM_FACES = 250_000
EPS = 1e-8


def _warn(code: str, count: int | None = None) -> str:
    return code if count is None else f"{code}:{count}"


def _mesh_warnings(obj: bpy.types.Object) -> list[str]:
    mesh = obj.data
    warnings: list[str] = []

    if any(abs(float(v) - 1.0) > 1e-5 for v in obj.scale):
        warnings.append("SCL")
    if obj.matrix_world.to_3x3().determinant() < 0:
        warnings.append("NEG")
    if len(mesh.uv_layers) == 0:
        warnings.append("UV0")
    if len(obj.material_slots) == 0:
        warnings.append("MAT0")

    armature_mods = [m for m in obj.modifiers if m.type == "ARMATURE" and getattr(m, "object", None)]
    if armature_mods:
        unweighted = sum(1 for vertex in mesh.vertices if not vertex.groups)
        if unweighted:
            warnings.append(_warn("UW", unweighted))

    if len(mesh.polygons) > MAX_BM_FACES:
        warnings.append(_warn("BIG", len(mesh.polygons)))
        return warnings

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        loose_v = sum(1 for vertex in bm.verts if not vertex.link_edges)
        loose_e = sum(1 for edge in bm.edges if not edge.link_faces)
        non_manifold = sum(1 for edge in bm.edges if len(edge.link_faces) != 2)
        zero_e = sum(1 for edge in bm.edges if edge.calc_length() <= EPS)
        zero_f = sum(1 for face in bm.faces if face.calc_area() <= EPS)
    finally:
        bm.free()

    if loose_v:
        warnings.append(_warn("LV", loose_v))
    if loose_e:
        warnings.append(_warn("LE", loose_e))
    if non_manifold:
        warnings.append(_warn("NM", non_manifold))
    if zero_e:
        warnings.append(_warn("ZE", zero_e))
    if zero_f:
        warnings.append(_warn("ZF", zero_f))
    return warnings


def inspect_quality(
    objects: Iterable[bpy.types.Object],
    id_for: Callable[[bpy.types.Object | None], str | None],
) -> dict[str, Any]:
    issues: dict[str, list[str]] = {}
    checked = 0
    for obj in objects:
        if obj.type != "MESH" or obj.data is None:
            continue
        checked += 1
        warnings = _mesh_warnings(obj)
        if warnings:
            short_id = id_for(obj)
            if short_id:
                issues[short_id] = warnings
    return {"ok": True, "checked": checked, "issues": issues}
