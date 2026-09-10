# 1782-92

**Minimum tokens, maximum agency.**

1782-92 is a deliberately small MCP control layer for AI-driven 3D work. Blender is the first backend; the model sees only three tools so context is spent on 3D judgment rather than tool ceremony.

## Three tools

| Tool | Purpose |
|---|---|
| `inspect` | Compact state, deep object inspection, references, measurements, quality checks, API probes |
| `apply` | One guarded `bpy` batch with short resolvers and reusable modeling helpers |
| `render` | Scene validation or direct approved-reference images |

```text
inspect -> apply -> render -> apply -> ...
```

## v0.1.5 crash-safe / token-budget pass

v0.1.5 is driven by long real GPT-6 character-modeling sessions where giant generated Python batches could consume tens of thousands of tokens, block Blender's main thread, time out, and leave the bridge unreachable.

The public MCP surface remains exactly three tools.

- **Short-batch guard:** apply code is bounded by source size and AST complexity before Blender runs it.
- **Cooperative execution deadline:** generated Python gets a 15-second budget and attempts rollback/context recovery on expiry. This cannot pre-empt one long native Blender C call, but it stops the common runaway-Python-loop failure mode.
- **No stale queued mutation:** abandoned queued jobs are marked cancelled instead of silently executing later.
- **Shared exec scope:** generated functions and comprehensions now share one globals/locals namespace, removing the repeated `NameError` pattern seen in real modeling traces.
- **Reusable `M` helpers:** `M.material`, `M.mesh`, `M.tube`, `M.clump`, `M.panel`, and `M.ellipsoid` replace repeated hundreds-of-lines geometry boilerplate inside prompts.
- **Token-bounded inspect:** large object lists, collection lists, quality reports, revision deltas, and ring profiles are compacted or sampled with follow-up hints.
- **Recovery-first checkpoints:** successful applies taking at least two seconds checkpoint automatically, and `apply(..., checkpoint:true)` can force a recovery copy.
- **Version mismatch signal:** a new MCP process refuses to silently drive an older Blender bridge and asks for Blender/MCP restart instead.

See [docs/PRODUCTION_LOOP.md](docs/PRODUCTION_LOOP.md) for the crash-safe workflow and `M` helper vocabulary.

## v0.1.4 runtime hotfix

A real Blender 5.2.1 smoke caught one regression in v0.1.3: a simple RNA transform assignment such as `O("o1").location.x = 1.25` could be applied in Blender but incorrectly reported as `noop:1` because the depsgraph notification was not observed in time.

v0.1.4 adds a cheap object transform verifier around apparent no-op batches. Read-only `O()` access still stays revision-free, while real location/rotation/scale changes are promoted to a normal revision and invalidate render cache/checkpoint state correctly. No public MCP tool was added.

## v0.1.3 field-fix pass

v0.1.3 is driven by the first real GPT-6 character-modeling trace. The public MCP surface remains exactly three tools.

- **Direct reference preview:** `render({ ref: "r5" })` or up to four `refs` returns approved source images directly. No temporary plane/material and no scene revision are required.
- **Reference metadata:** `inspect("ref:r5")` returns compact name, dimensions, and file size. The older `refs:r5` spelling is accepted as an alias.
- **Read-only apply detection:** batches that only inspect/print state return `noop:1` and do not consume a revision or checkpoint.
- **Measurement inspect:** `oN:bounds`, `oN:rings`, `collections`, and `collection:NAME` reduce the need to use `apply` for geometry bookkeeping.
- **Canonical checkpoint recovery:** reopening `Speaki.r00013.blend` now continues as `Speaki.rNNNNN.blend` in the original project checkpoint directory instead of producing nested roots or names such as `Speaki.r00013.r00001.blend`.

## v0.1.2 production-loop pass

The external tool count is still exactly three. v0.1.2 added the missing pieces for longer modeling sessions:

- **Reference workspace:** user chooses one folder; `inspect("refs")` returns `rN` IDs and `REF("rN")` safely loads only those images.
- **Deep inspect:** `oN:mesh`, `oN:uv`, `oN:mat`, and `oN:rig` expose detail only when requested.
- **Production validator:** `inspect("quality")` checks common topology/UV/material/rigging problems with compact warning codes.
- **Context guard:** each `apply` starts/ends in Object mode; failed batches attempt undo and best-effort context restoration.
- **Three render modes:** `fast`, `lookdev`, and `wire`, still behind the single `render` tool.
- **Current-Blender API probe:** `inspect("api:bpy.ops...")` returns compact RNA operator arguments to reduce stale-API retries.
- **External-edit signal:** `inspect("summary")` includes `ext:1` once after Blender changed outside an `apply` call.
- **Guard policy unit tests** plus a real-Blender headless smoke script.

v0.1.1 optimizations remain: `session_uid`-backed short IDs, `O("oN")`, depsgraph dirty tracking, adaptive checkpoints, compact MCP results, render caching, and no blind replay of mutating calls.

See [docs/PRODUCTION_LOOP.md](docs/PRODUCTION_LOOP.md) and [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## Requirements

- Blender 4.2+
- Node.js 20+

## Install

```powershell
npm install
npm run check
.\scripts\build-addon.ps1
```

In Blender: **Edit -> Preferences -> Get Extensions -> Install from Disk**. Install the generated ZIP, enable **1782-92 Bridge**, then open **3D View -> N -> 1782-92** and press **Start**.

For image references, choose the approved folder in the **Refs** field in the same panel.

### Windows development link

When actively developing 1782-92, the repository `addon` directory can be linked directly into Blender's user extension directory. This removes the repeated build-ZIP-install cycle.

Save your `.blend` work and close Blender first, then run from the repository root:

```powershell
.\scripts\link-addon-dev.ps1 -BlenderVersion 5.2
```

The script backs up an existing packaged `p1782_92` install and creates a Windows directory junction from Blender's extension directory to this repository's `addon` folder.

After the one-time setup, future source updates are simply:

```powershell
git pull
```

Restart Blender after pulling so the Python modules are reloaded. No add-on ZIP rebuild is needed for normal source iteration.

To leave development-link mode and restore the previous packaged install:

```powershell
.\scripts\link-addon-dev.ps1 -BlenderVersion 5.2 -Remove
```

If `-BlenderVersion` is omitted, the script selects the newest numeric Blender user-config directory it can find. The script refuses to change the extension directory while Blender is running.

## MCP host

```json
{
  "mcpServers": {
    "1782-92": {
      "command": "npm",
      "args": ["--prefix", "C:\\path\\to\\1782-92", "start"]
    }
  }
}
```

## Compact usage

```text
inspect()
inspect({ q: "refs" })
inspect({ q: "ref:r5" })
inspect({ q: "collections" })
inspect({ q: "collection:Speaky_Hair" })
inspect({ q: "o3:mesh" })
inspect({ q: "o3:bounds" })
inspect({ q: "o3:rings" })
inspect({ q: "quality:o3" })
inspect({ q: "api:bpy.ops.mesh.primitive_cube_add" })
```

`apply` exposes `bpy`, `bmesh`, `math`, common `mathutils` types, `O(short_id)`, `REF(reference_id)`, and the compact `M` modeling vocabulary:

```python
head = O("o3")
head.scale.z *= 1.02

M.material("Hair", (0.95, 0.90, 0.82, 1))
M.clump("Bang.L", [
    (-0.03, -0.08, 1.64, 0.025, 0.012),
    (-0.04, -0.09, 1.59, 0.020, 0.010),
    (-0.05, -0.10, 1.55, 0.002, 0.002),
], "Hair", "Hair")
```

For an important small mutation, force a checkpoint with the same tool rather than adding another MCP surface:

```text
apply({ code: "...", checkpoint: true })
```

A read-only batch remains valid and does not create a false edit revision:

```python
head = O("o3")
print(head.name, tuple(head.dimensions))
```

Validation and reference viewing:

```text
render()
render({ mode: "wire", ids: ["o3"] })
render({ mode: "lookdev", views: ["front", "side", "back", "three_quarter"] })
render({ ref: "r5" })
render({ refs: ["r1", "r4", "r5"] })
```

Reference preview returns the approved source image bytes directly; scene `size`, `views`, and render mode are not used in reference mode. A single direct preview is capped at 16 MiB to avoid oversized MCP payloads.

## Real Blender smoke

After installing Blender or adding it to PATH:

```powershell
blender -b --python scripts/blender-smoke.py
```

The smoke checks create/mutate/no-op behavior, the shared generated-code scope, the `M` helper path, forced recovery checkpointing, bounds/rings/quality inspection, and a 128 px render.

## Safety

`apply` is a guardrail for trusted local AI clients, **not a hostile-code sandbox**. The bridge is loopback-only and authenticated. Direct arbitrary file loading remains blocked; references are exposed only from the folder explicitly selected by the user.

The cooperative apply deadline can interrupt generated Python, but cannot safely pre-empt a single long native Blender C call. After any ambiguous transport failure, inspect actual state before retrying a mutation.

See [docs/SECURITY.md](docs/SECURITY.md).

## Status

`v0.1.5` is the crash-safe/token-budget candidate driven by real long-session GPT-6 character-modeling failures. GitHub CI covers TypeScript, Python syntax, policy budgets, checkpoint paths, and the cooperative execution budget; the real Blender smoke remains the runtime gate before treating v0.1.5 as validated.

MIT licensed.
