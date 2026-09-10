# 1782-92

**Minimum tokens, maximum agency.**

1782-92 is a deliberately small MCP control layer for AI-driven 3D work. Blender is the first backend; the model sees only three tools so context is spent on 3D judgment rather than tool ceremony.

## Three tools

| Tool | Purpose |
|---|---|
| `inspect` | Compact state, deep object inspection, references, quality checks, API probes |
| `apply` | One guarded `bpy` batch with short object/reference resolvers |
| `render` | Fast, lookdev, or wire validation PNGs |

```text
inspect -> apply -> render -> apply -> ...
```

## v0.1.2 production-loop pass

The external tool count is still exactly three. Internally v0.1.2 adds the missing pieces for longer modeling sessions:

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
inspect({ q: "o3:mesh" })
inspect({ q: "quality:o3" })
inspect({ q: "api:bpy.ops.mesh.primitive_cube_add" })
```

`apply` exposes `bpy`, `bmesh`, `math`, common `mathutils` types, `O(short_id)`, and `REF(reference_id)`:

```python
head = O("o3")
head.scale.z *= 1.02

img = REF("r1")
```

Validation:

```text
render()
render({ mode: "wire", ids: ["o3"] })
render({ mode: "lookdev", views: ["front", "side", "back", "three_quarter"] })
```

## Real Blender smoke

After installing Blender or adding it to PATH:

```powershell
blender -b --python scripts/blender-smoke.py
```

The smoke creates a cube, resolves it by `O("oN")`, deep-inspects it, runs quality checks, and returns a 128 px validation render.

## Safety

`apply` is a guardrail for trusted local AI clients, **not a hostile-code sandbox**. The bridge is loopback-only and authenticated. Direct file loading remains blocked; references are exposed only from the folder explicitly selected by the user.

See [docs/SECURITY.md](docs/SECURITY.md).

## Status

`v0.1.2` is an experimental production-loop foundation. The next milestone should be driven by real Blender character-modeling traces rather than adding more public tools.

MIT licensed.
