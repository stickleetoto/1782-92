# Architecture

## Design rule

**Spend model tokens on 3D judgment, not tool ceremony.**

1782-92 deliberately keeps the model-visible surface to three operations while allowing Blender's native Python API to remain expressive behind `apply`.

## Data path

```text
Model
  -> MCP stdio server
      -> inspect | apply | render
          -> authenticated 127.0.0.1 HTTP
              -> Blender extension network thread
                  -> queue
                      -> bpy.app.timers callback
                          -> Blender main thread
```

Blender's Python API is not thread-safe. The HTTP thread never calls `bpy`; it only parses JSON, authenticates, enqueues a job, and waits. A registered application timer drains the queue on Blender's main thread.

## `inspect`

Default output is intentionally tiny: revision, file name, counts, active object, selection, and objects changed by the last `apply`.

Deeper state is opt-in:

- `q="objects"`: compact `[id, name, type]` rows
- `q="selection"`: selected object rows
- `q="materials"`: compact material rows
- `q="o17"`: detail for one stable object ID

Object IDs are short (`o1`, `o2`, ...) and kept in bridge memory for the Blender session so the model does not need to repeatedly send long object names.

## `apply`

`apply` accepts one batch of Blender Python. This replaces dozens of narrowly-scoped manipulation tools.

Before execution the extension parses the code with Python AST and rejects imports, dangerous builtins, private attribute traversal, and file/process/network-adjacent Blender APIs. Execution receives a reduced builtin set plus `bpy`, `bmesh`, `math`, and selected `mathutils` types.

After a successful call:

1. Blender updates the view layer.
2. A compact before/after scene fingerprint identifies likely changed objects.
3. The scene revision increments.
4. A `.blend` checkpoint is saved automatically.
5. Only compact result metadata is returned.

The changed-object detector is intentionally cheap and is not a forensic diff.

## `render`

`render` creates a temporary orthographic camera, frames the target geometry, and renders requested validation views. It prefers Blender Workbench for predictable, fast modeling previews. The original render settings and camera are restored afterward.

PNG bytes are returned directly as MCP image content. There is no model-visible temp-file path protocol.

## Discovery

The Blender extension binds to `127.0.0.1` on an ephemeral port and generates a random token on each start. It writes `{host, port, token, pid, version}` into `1782-92-bridge.json` in the OS temp directory. The local MCP process reads that file on each call.

This avoids manual port/token configuration while allowing Blender restarts without changing MCP configuration.

## Future work

Only add features when they reduce total model/tool round trips or improve reliability. Candidate internal improvements include better mesh change fingerprints, render overlays, transaction rollback, target collections, and compact geometry diagnostics. The external tool count should remain three unless strong evidence shows otherwise.
