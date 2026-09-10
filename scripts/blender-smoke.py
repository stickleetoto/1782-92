from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import addon  # noqa: E402
from addon import engine, field_ops  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    addon.register()
    try:
        if bpy.context.object and bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        engine.reset_session()

        summary = field_ops.inspect({"q": "summary"})
        require(summary.get("ok") is True, "summary_failed")

        created = field_ops.apply({"code": 'bpy.ops.mesh.primitive_cube_add(size=2)\no=bpy.context.object\no.name="SmokeCube"'})
        require(created.get("ok") is True and created.get("rev") == 1, f"apply_create_failed:{created}")

        objects = field_ops.inspect({"q": "objects"}).get("objects", [])
        cube_id = next((row[0] for row in objects if row[1] == "SmokeCube"), None)
        require(isinstance(cube_id, str), "cube_id_missing")

        noop = field_ops.apply({"code": f'o=O("{cube_id}")\nprint(o.name, tuple(o.dimensions))'})
        require(noop.get("ok") is True and noop.get("rev") == 1 and noop.get("noop") == 1, f"noop_revision_failed:{noop}")

        bounds = field_ops.inspect({"q": f"{cube_id}:bounds"})
        require(bounds.get("ok") is True and len(bounds.get("bounds", [])) == 2, f"bounds_failed:{bounds}")

        rings = field_ops.inspect({"q": f"{cube_id}:rings"})
        require(rings.get("ok") is True and len(rings.get("rings", [])) >= 2, f"rings_failed:{rings}")

        moved = field_ops.apply({"code": f'O("{cube_id}").location.x = 1.25'})
        require(moved.get("ok") is True and moved.get("rev") == 2, f"apply_resolver_failed:{moved}")

        deep = field_ops.inspect({"q": f"{cube_id}:mesh"})
        require(deep.get("mesh", {}).get("v") == 8, f"deep_inspect_failed:{deep}")

        quality = field_ops.inspect({"q": f"quality:{cube_id}"})
        require(quality.get("ok") is True, f"quality_failed:{quality}")

        rendered = field_ops.render({"views": ["front"], "size": 128, "mode": "fast", "ids": [cube_id]})
        images = rendered.get("images", [])
        require(rendered.get("ok") is True and len(images) == 1 and len(images[0].get("data", "")) > 100, "render_failed")

        print("1782-92 blender smoke: PASS")
    finally:
        try:
            addon.unregister()
        except Exception:
            pass


if __name__ == "__main__":
    main()
