from __future__ import annotations

import bpy
from bpy.props import StringProperty

from .bridge_server import is_running, start_bridge, stop_bridge
from .references import ROOT_PROP


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
        layout.label(text="RUNNING" if is_running() else "STOPPED")
        layout.operator("p178292.stop" if is_running() else "p178292.start")
        layout.separator()
        layout.prop(context.scene, ROOT_PROP, text="Refs")


_CLASSES = (P178292_OT_start, P178292_OT_stop, P178292_PT_panel)


def _registered_class(name: str):
    """Return Blender's currently registered RNA class with this Python name.

    Do not use ``cls.bl_rna`` as a registration test: RNA attributes can be
    inherited or stale across extension reloads. Blender's bpy.types registry
    is the source of truth for UI/operator registration.
    """
    return getattr(bpy.types, name, None)


def _is_registered(cls: type) -> bool:
    return _registered_class(cls.__name__) is cls


def _unregister_stale_name(cls: type) -> None:
    """Remove a stale class registered under the same name, if possible.

    This makes linked-development reloads deterministic without blindly
    unregistering our fresh class. Failure is intentionally non-fatal: Blender
    may already have detached an old RNA wrapper during extension reload.
    """
    stale = _registered_class(cls.__name__)
    if stale is None or stale is cls:
        return
    try:
        bpy.utils.unregister_class(stale)
    except (RuntimeError, ValueError):
        pass


def register() -> None:
    if not hasattr(bpy.types.Scene, ROOT_PROP):
        setattr(
            bpy.types.Scene,
            ROOT_PROP,
            StringProperty(
                name="Reference Workspace",
                description="Folder AI may access through inspect(refs) and REF(rN)",
                subtype="DIR_PATH",
                default="",
            ),
        )

    for cls in _CLASSES:
        if _is_registered(cls):
            continue
        _unregister_stale_name(cls)
        try:
            bpy.utils.register_class(cls)
        except ValueError as exc:
            # A same-bl_idname class can survive a linked dev reload even when
            # bpy.types no longer exposes it by Python class name. Surface a
            # useful error instead of silently leaving the add-on "enabled"
            # without its panel/operators.
            raise RuntimeError(f"1782-92 register failed for {cls.__name__}: {exc}") from exc


def unregister() -> None:
    stop_bridge()
    for cls in reversed(_CLASSES):
        registered = _registered_class(cls.__name__)
        if registered is None:
            continue
        try:
            bpy.utils.unregister_class(registered)
        except (RuntimeError, ValueError):
            pass
    if hasattr(bpy.types.Scene, ROOT_PROP):
        delattr(bpy.types.Scene, ROOT_PROP)
