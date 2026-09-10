# Production loop

v0.1.2 keeps the model-visible API at three tools while adding production-oriented depth behind them.

## Reference workspace

Choose a folder in **3D View -> N -> 1782-92 -> Refs**. The model can then call `inspect({q:"refs"})` and receives compact IDs such as `r1`, `r2`.

Inside `apply`, `REF("r1")` loads only an image found under that user-approved folder. Direct model-supplied file loading remains blocked.

Example:

```python
img = REF("r1")
bpy.ops.object.empty_add(type="IMAGE", location=(0, 0, 1.5))
bpy.context.object.data = img
```

Reference discovery is recursive, capped at 128 images, resolves symlinks, rejects paths that escape the chosen root, and ignores files over 64 MiB.

## Deep inspect dialect

The single `inspect` tool now supports:

```text
summary
objects
selection
materials
refs
quality
quality:o7
o7
o7:mesh
o7:uv
o7:mat
o7:rig
api:bpy.ops.mesh.primitive_cube_add
```

Deep output is opt-in so normal loops stay compact.

## Quality codes

`inspect("quality")` returns only objects with issues. Codes are intentionally short:

| Code | Meaning |
|---|---|
| `SCL` | unapplied object scale |
| `NEG` | negative transform determinant |
| `UV0` | no UV layer |
| `MAT0` | no material slot |
| `UW:n` | n unweighted vertices on an armature-driven mesh |
| `LV:n` | n loose vertices |
| `LE:n` | n loose edges |
| `NM:n` | n non-manifold/boundary edges |
| `ZE:n` | n zero-length edges |
| `ZF:n` | n zero-area faces |
| `BIG:n` | topology scan skipped because face count exceeded the quality budget |

These are warnings, not universal modeling errors. Open clothing, cards, hair planes, and deliberate non-manifold assets may legitimately report `NM`.

## Context guard

Each `apply` captures the user's active object, selection, and mode. Generated work is normalized to Object mode before execution and again after success. If execution raises, 1782-92 attempts one undo and restores the captured context best-effort.

This prevents an agent from accidentally inheriting an Edit/Sculpt/Pose state from the previous step.

## Render modes

`render` remains one tool:

- `fast` — Workbench silhouette/material-color review.
- `lookdev` — Eevee with temporary studio lighting for shader/material review.
- `wire` — Workbench validation with wire display enabled on targets.

The default remains `fast`, front + three-quarter, 512 px. Identical renders at the same revision are cached.

## API probe

`inspect("api:bpy.ops.<group>.<operator>")` reads the currently installed Blender operator RNA and returns a compact description and argument list. It exists to reduce retries caused by stale model knowledge without introducing a fourth documentation tool.
