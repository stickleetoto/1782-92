from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Exercise the repository runtime directly. Blender may already have the
# installed/linked p1782_92 extension enabled in user preferences. Registering
# this source-tree package a second time would replace those RNA classes and
# leave the installed module with stale class objects during shutdown.
from addon import engine, field_ops, runtime_guard  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    if bpy.context.object and bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    engine.reset_session()

    summary = field_ops.inspect({"q": "summary"})
    require(summary.get("ok") is True, "summary_failed")

    created = runtime_guard.apply({"code": 'bpy.ops.mesh.primitive_cube_add(size=2)\no=bpy.context.object\no.name="SmokeCube"'})
    require(created.get("ok") is True and created.get("rev") == 1, f"apply_create_failed:{created}")

    objects = field_ops.inspect({"q": "objects"}).get("objects", [])
    cube_id = next((row[0] for row in objects if row[1] == "SmokeCube"), None)
    require(isinstance(cube_id, str), "cube_id_missing")

    noop = runtime_guard.apply({"code": f'o=O("{cube_id}")\nprint(o.name, tuple(o.dimensions))'})
    require(noop.get("ok") is True and noop.get("rev") == 1 and noop.get("noop") == 1, f"noop_revision_failed:{noop}")

    scoped = runtime_guard.apply({"code": 'value=7\ndef f():\n return value\nprint(f())'})
    require(scoped.get("ok") is True and scoped.get("noop") == 1, f"shared_scope_failed:{scoped}")
    require(scoped.get("out") == "7", f"shared_scope_output_failed:{scoped}")

    bounds = field_ops.inspect({"q": f"{cube_id}:bounds"})
    require(bounds.get("ok") is True and len(bounds.get("bounds", [])) == 2, f"bounds_failed:{bounds}")

    rings = field_ops.inspect({"q": f"{cube_id}:rings"})
    require(rings.get("ok") is True and len(rings.get("rings", [])) >= 2, f"rings_failed:{rings}")

    moved = runtime_guard.apply({"code": f'O("{cube_id}").location.x = 1.25'})
    require(moved.get("ok") is True and moved.get("rev") == 2 and moved.get("noop") is None, f"apply_resolver_failed:{moved}")
    require(abs(bpy.data.objects["SmokeCube"].location.x - 1.25) < 1e-6, "transform_not_applied")

    helper = runtime_guard.apply({
        "code": 'M.material("SmokeMat",(0.8,0.4,0.2,1))\nM.ellipsoid("HelperBall",(2.5,0,0.5),(0.2,0.15,0.25),"SmokeHelpers","SmokeMat")',
        "checkpoint": True,
    })
    require(helper.get("ok") is True and helper.get("rev") == 3, f"helper_apply_failed:{helper}")
    require(helper.get("checkpoint"), f"forced_checkpoint_failed:{helper}")
    require(bpy.data.objects.get("HelperBall") is not None, "helper_object_missing")

    tree = runtime_guard.dispatch("/inspect", {"q": "tree"})
    require(tree.get("ok") is True and tree.get("count", 0) >= 2, f"tree_failed:{tree}")

    found = runtime_guard.dispatch("/inspect", {"q": "find:Helper"})
    matches = found.get("matches", [])
    require(found.get("ok") is True and any(row[1] == "HelperBall" for row in matches), f"find_failed:{found}")

    spatial = runtime_guard.dispatch("/inspect", {"q": f"{cube_id}:spatial"})
    near = spatial.get("near", [])
    require(spatial.get("ok") is True and any(row[1] == "HelperBall" for row in near), f"spatial_failed:{spatial}")

    deep = field_ops.inspect({"q": f"{cube_id}:mesh"})
    require(deep.get("mesh", {}).get("v") == 8, f"deep_inspect_failed:{deep}")

    quality = field_ops.inspect({"q": f"quality:{cube_id}"})
    require(quality.get("ok") is True, f"quality_failed:{quality}")

    rendered = field_ops.render({"views": ["front"], "size": 128, "mode": "fast", "ids": [cube_id]})
    images = rendered.get("images", [])
    require(rendered.get("ok") is True and len(images) == 1 and len(images[0].get("data", "")) > 100, "render_failed")

    viewport = runtime_guard.dispatch("/render", {"mode": "viewport"})
    if bpy.app.background:
        require(viewport.get("error") == "viewport_unavailable:background", f"viewport_background_failed:{viewport}")

    print("1782-92 blender smoke: PASS")


if __name__ == "__main__":
    main()
