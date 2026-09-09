# 1782-92

**Minimum tokens, maximum agency.**

1782-92 is a deliberately small MCP control layer for AI-driven 3D work. Blender is the first backend; the model sees only three tools so context is spent on 3D judgment rather than tool ceremony.

## Three tools

| Tool | Purpose |
|---|---|
| `inspect` | Read compact scene state or one short object ID |
| `apply` | Execute one guarded `bpy` batch; `O("o7")` resolves short IDs |
| `render` | Return fast orthographic validation PNGs for visual self-review |

```text
inspect -> apply -> render -> apply -> ...
```

## v0.1.1 optimization pass

The public API is still exactly three tools. Internally v0.1.1 reduces repeated work:

- Blender `session_uid` backs short stable object IDs when available.
- `O("oN")` gives generated code direct short-ID object access instead of repeating long names.
- Depsgraph updates + touched IDs replace expensive full-scene geometry fingerprints around every `apply`.
- Disk checkpoints are adaptive: first edit, then every 5 revisions or 3 minutes, rather than every edit.
- A best-effort Blender undo marker is pushed before `apply`; failed batches attempt one rollback.
- Default validation is two views (`front`, `three_quarter`); side/back are opt-in.
- Identical renders at the same revision are cached in memory.
- MCP success results are emitted once as compact JSON instead of duplicating text + structured output.
- The Node bridge caches discovery state briefly and never retries a mutating `apply` after an ambiguous transport failure.

See [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## Requirements

- Blender 4.2+
- Node.js 20+

## Install

### 1. MCP server

```powershell
npm install
npm run check
```

### 2. Blender extension

```powershell
.\scripts\build-addon.ps1
```

In Blender: **Edit -> Preferences -> Get Extensions -> Install from Disk** and select the generated ZIP. Enable **1782-92 Bridge**.

Open the 3D View sidebar (`N`) -> **1782-92** -> **Start**.

Blender binds to `127.0.0.1` on an ephemeral port and writes a short-lived authenticated connection file into the OS temp directory. No manual port/token setup is required.

### 3. Add to an MCP host

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

Exact configuration placement depends on the MCP host.

## Tool usage

```text
inspect()
inspect({ q: "objects" })
inspect({ q: "o3" })
```

`apply` receives one Python batch. Imports are intentionally unnecessary and blocked. The execution environment exposes `bpy`, `bmesh`, `math`, common `mathutils` types, and `O(short_id)`.

```python
head = O("o3")
head.scale.z *= 1.02
```

Or create new geometry directly:

```python
bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(0, 0, 1.7))
obj = bpy.context.active_object
obj.name = "Head_Blockout"
obj.scale = (0.55, 0.48, 0.62)
```

Fast validation:

```text
render()
```

Full turnaround when needed:

```text
render({ views: ["front", "side", "back", "three_quarter"] })
```

`render` returns actual PNG image content, not model-visible temp paths.

## Safety model

`apply` is **not a hostile-code sandbox**. It is a guardrail for trusted local AI clients. Imports, common dynamic execution/file APIs, private attribute traversal, and sensitive Blender APIs are blocked; the bridge is loopback-only and authenticated with a per-run random token.

The v0.1.1 rollback is best-effort, not a transactional guarantee. Keep valuable `.blend` work under normal backups/versioned storage.

See [docs/SECURITY.md](docs/SECURITY.md).

## Architecture

```text
MCP host / model
      |
      | stdio: inspect / apply / render
      v
1782-92 MCP server
      |
      | authenticated loopback HTTP
      v
Blender extension
      |
      | queue + bpy.app.timers
      v
Blender main thread / bpy
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Status

`v0.1.1` is an experimental performance foundation aimed at repeated agent-driven modeling loops.

MIT licensed.
