# Architecture

## Design rule

**Spend model tokens on 3D judgment, not tool ceremony.**

1782-92 keeps the model-visible surface to three operations while retaining Blender's expressive Python API behind `apply`.

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

The HTTP thread never calls `bpy`. It parses/authenticates/enqueues and waits. The timer drains the queue on Blender's main thread.

## Module split

```text
addon/__init__.py       UI + registration only
addon/bridge_server.py  loopback server, auth, queue, discovery state
addon/engine.py         inspect/apply/render and Blender state tracking
src/index.ts            three model-visible MCP tools
src/bridge.ts           local transport client + short discovery cache
```

Keeping transport/UI separate from modeling state makes the Blender-side hot path easier to profile and change without expanding the MCP schema.

## `inspect`

Default output is intentionally small: revision, file, type counts, active/selected short IDs, and last changed IDs. Deeper reads are opt-in:

- `q="objects"` -> `[id, name, type]`
- `q="selection"`
- `q="materials"`
- `q="o17"` -> details for one object

Object short IDs are backed by Blender `ID.session_uid` when available. A reverse map makes `O("o17")` direct inside `apply`.

## `apply`

One batch replaces dozens of narrow manipulation tools. Before execution, the extension AST-checks generated Python and exposes a reduced environment containing Blender APIs plus `O(short_id)`.

Change tracking does not serialize a full scene fingerprint. During execution:

1. depsgraph updates collect changed object/datablock owners,
2. calls to `O()` mark referenced objects as touched,
3. before/after runtime-ID sets detect object creation/deletion.

After success the scene revision increments, render cache is invalidated, and a disk checkpoint is created only when the adaptive policy says it is due.

A Blender undo marker is attempted before the batch. If the batch throws, one undo is attempted. This is a recovery guardrail, not a strict transaction.

## `render`

`render` creates a temporary orthographic Workbench camera and returns PNG bytes directly as MCP image content. Default views are front + three-quarter; full turnaround is explicit.

The last render is cached by `(revision, views, target IDs, size)`. Manual Blender depsgraph updates invalidate the cache so human edits do not return stale validation images.

## Discovery

Blender binds to `127.0.0.1` on an ephemeral port and writes `{host, port, token, pid, version}` into an OS-temp state file. The Node bridge caches valid discovery state for one second.

Read-only calls can re-discover after transport failure. Mutating `apply` calls are never blindly retried after an ambiguous failure.

## Future optimization rule

Prioritize improvements that lower total model/tool round trips: better geometry diagnostics, render overlays, safer rollback, compact reference handling, and measured scene-scale benchmarks. The public tool count should remain three unless evidence strongly argues otherwise.
