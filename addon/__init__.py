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


def register() -> None:
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
        bpy.utils.register_class(cls)


def unregister() -> None:
    stop_bridge()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, ROOT_PROP):
        delattr(bpy.types.Scene, ROOT_PROP)
