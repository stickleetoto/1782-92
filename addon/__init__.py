from __future__ import annotations

import bpy

from .bridge_server import is_running, start_bridge, stop_bridge


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


_CLASSES = (P178292_OT_start, P178292_OT_stop, P178292_PT_panel)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    stop_bridge()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
