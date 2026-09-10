from __future__ import annotations

from typing import Any

import bpy

from . import engine, field_ops


def _transform_signature(obj: bpy.types.Object) -> tuple[Any, ...]:
    """Cheap object-level state used only to verify an apparent apply no-op."""
    matrix = tuple(round(float(value), 12) for row in obj.matrix_basis for value in row)
    return (
        matrix,
        bool(obj.hide_viewport),
        bool(obj.hide_render),
        bool(obj.hide_get()),
    )


def _transform_state() -> dict[int, tuple[Any, ...]]:
    return {engine._uid(obj): _transform_signature(obj) for obj in bpy.data.objects}


def _promote_false_noop(reply: dict[str, Any], before: dict[int, tuple[Any, ...]]) -> dict[str, Any]:
    """Turn a false noop into a real revision when object transforms actually changed.

    Blender can defer depsgraph notifications for simple RNA transform assignments such as
    ``O("o1").location.x = 1``. field_ops intentionally stopped treating mere O() access as a
    mutation in v0.1.3, so this verifier catches only the cheap object-level changes that can be
    missed by depsgraph timing without reintroducing read-only false positives.
    """
    if reply.get("ok") is not True or reply.get("noop") != 1:
        return reply

    after = _transform_state()
    changed_uids = {
        uid
        for uid in before.keys() & after.keys()
        if before[uid] != after[uid]
    }
    if not changed_uids:
        return reply

    engine._refresh_ids(prune=True)
    changed = sorted(
        engine._OBJ_IDS[uid]
        for uid in changed_uids
        if uid in engine._OBJ_IDS
    )

    engine._REV += 1
    engine._LAST_CHANGED = changed
    engine._EXTERNAL_DIRTY = False
    engine._invalidate_render_cache()

    promoted: dict[str, Any] = {"ok": True, "rev": engine._REV}
    if changed:
        promoted["changed"] = changed
    checkpoint = field_ops._checkpoint_if_due(engine._REV)
    if checkpoint:
        promoted["checkpoint"] = checkpoint
    if "ms" in reply:
        promoted["ms"] = reply["ms"]
    if "out" in reply:
        promoted["out"] = reply["out"]
    return promoted


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    before = _transform_state()
    reply = field_ops.apply(payload)
    return _promote_false_noop(reply, before)


def dispatch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path == "/apply":
        return apply(payload)
    return field_ops.dispatch(path, payload)
