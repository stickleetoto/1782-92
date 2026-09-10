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


def _is_registered(cls: type) -> bool:
    return getattr(cls, "bl_rna", None) is not None


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
        if not _is_registered(cls):
            bpy.utils.register_class(cls)


def unregister() -> None:
    stop_bridge()
    for cls in reversed(_CLASSES):
        # Blender can replace an RNA class when an add-on is reloaded from a
        # different package path. In that case the old Python class loses
        # bl_rna; attempting to unregister it raises during Blender shutdown.
        if _is_registered(cls):
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
    if hasattr(bpy.types.Scene, ROOT_PROP):
        delattr(bpy.types.Scene, ROOT_PROP)
