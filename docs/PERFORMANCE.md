# Performance and token budget

1782-92 optimizes for **total agent cost**, not just local CPU time. A change is useful when it reduces repeated tool-schema reading, model-visible text, Blender work, image transfer, or failed/redundant round trips without making the agent less capable.

## v0.1.1 changes

### Dirty tracking instead of full fingerprints

v0.1 captured a rich signature for every object before and after every `apply`. That scaled with scene size and still could miss edits that changed vertex positions without changing mesh counts.

v0.1.1 uses three lightweight signals:

1. Blender depsgraph update IDs for object/datablock changes.
2. Short IDs explicitly resolved through `O("oN")` are marked touched.
3. A cheap before/after set of object runtime IDs detects creation and deletion.

This keeps change reporting useful while removing matrix/bounds/material fingerprint construction for the full scene on each edit.

### Stable compact IDs

Short IDs (`o1`, `o2`, ...) are backed by Blender `session_uid` where available, with pointer fallback for compatibility. A reverse map gives direct lookup. Generated code can use:

```python
O("o17").location.z += 0.02
```

instead of repeating long object names or scanning `bpy.data.objects`.

### Adaptive checkpoints

Full `.blend` copies are expensive as projects grow. v0.1.1 checkpoints:

- revision 1,
- every 5 successful revisions,
- or after 180 seconds without a checkpoint.

Only the newest 8 are retained. A lightweight Blender undo marker is still attempted before each edit; on failure the bridge attempts one undo. This rollback is best-effort and not an ACID transaction.

### Render budget

The default validation request is now `front + three_quarter`, which is usually enough for rapid silhouette work. Side/back remain available explicitly. Identical render requests for the same scene revision are cached in memory and invalidated by `apply` or manual Blender depsgraph changes.

### Compact protocol output

Successful MCP calls no longer duplicate the same JSON in both text and `structuredContent`. The HTTP bridge still uses explicit `{ok:...}` internally, but the MCP layer strips `ok:true` before exposing success metadata to the model. Errors use a compact `{"e":"..."}` payload.

### Discovery cache and safe retry behavior

The Node process caches Blender's ephemeral connection state for one second. Read-only calls may re-discover and retry after a transport failure. `apply` is deliberately **not** retried after an ambiguous transport failure because replaying a mutating batch could duplicate geometry edits. A 403 caused by a stale per-run token is safe to re-discover and retry because Blender rejected the original request before execution.

## External API rule

Optimization work must not grow the model-visible surface casually. The default target remains exactly:

```text
inspect
apply
render
```

New capabilities should first be implemented behind those operations. Add another MCP tool only if measured evidence shows it lowers total round trips or materially improves reliability.
