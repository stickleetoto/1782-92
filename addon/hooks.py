from __future__ import annotations

from typing import Any

import bpy
from bpy.app.handlers import persistent

from . import engine


@persistent
def depsgraph_update(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph) -> None:
    engine._depsgraph_handler(scene, depsgraph)


@persistent
def load_post(_dummy: Any) -> None:
    engine.reset_session()


def install() -> None:
    if depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(depsgraph_update)
    if load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(load_post)


def remove() -> None:
    if depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update)
    if load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post)
