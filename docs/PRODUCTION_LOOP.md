# Production loop

1782-92 keeps the model-visible API at exactly three tools while moving repeatable production mechanics behind them.

## v0.1.5 crash-safe / token-budget mode

The default long-session loop is deliberately small:

```text
inspect(summary/target) -> apply(short batch) -> inspect(target) -> render(milestone) -> ...
```

Rules built into v0.1.5:

- `apply` source is bounded by bytes and AST complexity before Blender executes it.
- generated Python receives a cooperative 15-second execution deadline; deadline failures attempt the same best-effort undo/context recovery as other exceptions.
- the HTTP apply wait is no longer allowed to sit on a wedged Blender batch for minutes.
- timed-out queued jobs are marked cancelled so a request that the client abandoned cannot silently execute later while still waiting in the queue.
- a completed apply taking at least 2 seconds gets a recovery checkpoint automatically; `checkpoint:true` can force one for an important smaller batch.
- global `objects`, `quality`, `summary.changed`, collection listings, and `rings` are bounded/compacted so large scenes do not flood model context.
- `apply` now executes with one shared Python globals/locals namespace, fixing generated helper-function/comprehension `NameError` retries caused by split `exec()` scopes.

A transport timeout is still ambiguous if Blender is stuck inside one long native C operation; never blindly replay a mutating request after a timeout. Inspect actual state first.

## Reusable modeling vocabulary

`apply` exposes `M` in addition to `bpy`, `bmesh`, `math`, mathutils types, `O()` and `REF()`.

`M` exists to keep generated batches short instead of redefining hundreds of lines of geometry boilerplate:

```python
M.material("Hair", (0.95, 0.9, 0.8, 1))
M.ellipsoid("Head", (0, 0, 1.5), (0.1, 0.08, 0.12), "Body", "Skin")
M.clump("Bang.L", [
    (-0.03, -0.08, 1.64, 0.025, 0.012),
    (-0.04, -0.09, 1.59, 0.020, 0.010),
    (-0.05, -0.10, 1.55, 0.002, 0.002),
], "Hair", "Hair")
```

Available helpers:

- `M.material(name, color, roughness=.7, metallic=0)`
- `M.mesh(name, vertices, faces, collection=None, material=None, smooth=True)`
- `M.tube(name, points, radius, collection=None, material=None, sides=8)`
- `M.clump(name, profiles, collection=None, material=None, sides=8)` where profiles are `[x,y,z,width,depth]`
- `M.panel(name, points, depth, collection=None, material=None, axis="y")`
- `M.ellipsoid(name, center, radii, collection=None, material=None, segments=16, rings=8)`

These helpers replace an existing mesh object of the same name or create it if missing. They do not add new MCP tools.

## Reference workspace

Choose a folder in **3D View -> N -> 1782-92 -> Refs**. The model can then call `inspect({q:"refs"})` and receives compact IDs such as `r1`, `r2`.

Inside `apply`, `REF("r1")` resolves only an image found under that user-approved folder. For visual review, prefer direct reference mode instead of creating preview geometry:

```text
render({ref:"r1"})
render({refs:["r1","r4","r5"]})
```

Reference discovery is recursive, capped at 128 images, resolves symlinks, rejects paths that escape the chosen root, and ignores files over 64 MiB.

## Deep inspect dialect

The single `inspect` tool supports:

```text
summary
objects
selection
materials
refs
ref:r5
quality
quality:o7
collections
collection:Character_Hair
o7
o7:mesh
o7:uv
o7:mat
o7:rig
o7:bounds
o7:rings
api:bpy.ops.mesh.primitive_cube_add
```

Prefer `collections` -> `collection:NAME` -> targeted object inspection on large scenes. Global `objects` is intentionally capped and points the model toward collection queries. `rings` returns an evenly sampled vertical profile instead of dumping every distinct Z row.

## Quality codes

For a small issue set, `inspect("quality")` returns object-level issues directly. On a large scene it switches to code counts plus a small actionable sample; use `quality:oN` when full detail is needed for one object.

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

Each `apply` captures the user's active object, selection, and mode. Generated work is normalized to Object mode before execution and again after success. If execution raises or the cooperative deadline fires, 1782-92 attempts one undo and restores the captured context best-effort.

This prevents an agent from accidentally inheriting an Edit/Sculpt/Pose state from the previous step.

## Render modes

`render` remains one tool:

- `fast` — Workbench silhouette/material-color review.
- `lookdev` — Eevee with temporary studio lighting for shader/material review.
- `wire` — Workbench validation with wire display enabled on targets.

The default remains `fast`, front + three-quarter, 512 px. Prefer 512-768 px during iteration and 1024 px only for final review. Identical renders at the same revision are cached.

## API probe

`inspect("api:bpy.ops.<group>.<operator>")` reads the currently installed Blender operator RNA and returns a compact description and argument list. It exists to reduce retries caused by stale model knowledge without introducing a fourth documentation tool.
