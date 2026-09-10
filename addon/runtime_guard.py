from __future__ import annotations

from typing import Any

import bpy

from . import engine, field_ops, references, scene_intel

MAX_REFERENCE_RAW_BYTES = 8 * 1024 * 1024
RISKY_APPLY_CODE_BYTES = 8 * 1024
RISKY_APPLY_BPY_OP_TOKENS = 12


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


def _risky_apply(payload: dict[str, Any]) -> bool:
    code = payload.get("code")
    if not isinstance(code, str):
        return False
    if payload.get("checkpoint") is True:
        return True
    if len(code.encode("utf-8")) >= RISKY_APPLY_CODE_BYTES:
        return True
    return code.count("bpy.ops.") >= RISKY_APPLY_BPY_OP_TOKENS


def _pre_checkpoint_if_risky(payload: dict[str, Any]) -> str | None:
    if not _risky_apply(payload):
        return None
    # Validate before doing checkpoint I/O. Invalid generated code should be
    # rejected cheaply without saving the user's scene.
    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        return None
    try:
        engine.policy.validate_code(code)
    except ValueError:
        return None
    return field_ops._checkpoint(engine._REV)


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    before = _transform_state()
    safe_checkpoint = _pre_checkpoint_if_risky(payload)
    reply = field_ops.apply(payload)
    reply = _promote_false_noop(reply, before)
    if reply.get("ok") is not True and safe_checkpoint:
        # On failure this tells the caller exactly which pre-mutation recovery
        # copy is known-good. Successful replies stay compact; field_ops already
        # reports any post-mutation checkpoint that matters.
        reply["safe_checkpoint"] = safe_checkpoint
    return reply


def _reference_budget_error(payload: dict[str, Any]) -> dict[str, Any] | None:
    one = payload.get("ref")
    many = payload.get("refs")
    if one is None and many is None:
        return None
    values: list[str]
    if isinstance(one, str) and many is None:
        values = [one]
    elif one is None and isinstance(many, list) and all(isinstance(value, str) for value in many):
        values = list(many)
    else:
        return None  # Let field_ops return the canonical schema/error message.

    total = 0
    try:
        for value in values:
            total += references.resolve(value).stat().st_size
    except (KeyError, OSError, PermissionError):
        return None
    if total > MAX_REFERENCE_RAW_BYTES:
        return {
            "ok": False,
            "error": f"reference_payload_too_large:{total}>{MAX_REFERENCE_RAW_BYTES}:render_fewer_refs",
        }
    return None


def dispatch(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    if path == "/apply":
        return apply(payload)
    if path == "/inspect":
        reply = scene_intel.inspect(payload)
        if reply is not None:
            return reply
    if path == "/render":
        reply = scene_intel.render(payload)
        if reply is not None:
            return reply
        budget_error = _reference_budget_error(payload)
        if budget_error is not None:
            return budget_error
    return field_ops.dispatch(path, payload)
