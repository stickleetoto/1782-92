# 1782-92

**Minimum tokens, maximum agency.**

1782-92 is a deliberately small MCP control layer for AI-driven 3D work. The first backend is Blender, but the public tool surface is kept generic and tiny so a model spends context on modeling decisions instead of learning a large tool API.

## v0.1: three tools

| Tool | Purpose |
|---|---|
| `inspect` | Read compact scene state, object lists, or one object's details |
| `apply` | Execute guarded Blender Python (`bpy`) and checkpoint the result |
| `render` | Return orthographic validation PNGs for visual self-review |

The MCP server uses the official TypeScript MCP SDK v2 and stdio. Blender exposes a loopback-only HTTP bridge and marshals every `bpy` operation onto Blender's main thread through `bpy.app.timers` + a queue.

## Why only three tools?

A 3D agent already knows how to plan modeling work. Giving it dozens or hundreds of tiny RPCs makes it repeatedly spend tokens selecting tools, restating object state, and moving data between calls. 1782-92 instead provides a thin execution surface:

```text
inspect -> apply -> render -> inspect/apply -> ...
```

The bridge handles IDs, checkpoints, transport, validation renders, and guardrails internally.

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

Build the extension ZIP with Blender itself:

```powershell
.\scripts\build-addon.ps1
```

Then in Blender use **Edit -> Preferences -> Get Extensions -> Install from Disk** and select the generated ZIP. Enable **1782-92 Bridge**.

Open the 3D View sidebar (`N`) -> **1782-92** -> **Start**.

No port or token setup is required. Blender binds to `127.0.0.1` on an ephemeral port and writes a short-lived connection file to the OS temporary directory; the MCP process discovers it automatically.

### 3. Add to an MCP host

Example local configuration:

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

`apply` receives a single Python snippet. `bpy`, `bmesh`, `math`, and common `mathutils` types are already available, so imports are intentionally unnecessary and blocked.

```python
# Example payload for apply(code=...)
bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, location=(0, 0, 1.7))
obj = bpy.context.active_object
obj.name = "Head_Blockout"
obj.scale = (0.55, 0.48, 0.62)
```

Validation:

```text
render({ views: ["front", "side", "back", "three_quarter"] })
```

`render` returns actual PNG image content to the model instead of making it reason about local file paths.

## Safety model

`apply` is **not a hostile-code sandbox**. It is a guardrail for trusted local AI clients. The bridge blocks imports, file/process/network-oriented APIs, private/dunder attribute traversal, Blender save/load/import/export operators, and unsafe Python builtins. It also binds only to localhost and authenticates each request with a random per-run token.

Do not expose the Blender bridge port to a network and do not connect untrusted models or prompts to `apply`.

See [docs/SECURITY.md](docs/SECURITY.md).

## Architecture

```text
MCP host / model
      |
      | stdio, 3 tools
      v
1782-92 MCP server
      |
      | loopback HTTP + ephemeral token
      v
Blender extension
      |
      | queue + bpy.app.timers
      v
Blender main thread / bpy
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Status

`v0.1` is an experimental foundation. The immediate goal is reliable agent-driven blockout and iterative visual validation, not a giant Blender automation suite.

MIT licensed.
