# Security

1782-92 is designed for a trusted local AI client controlling a local Blender session. It is not a security boundary for hostile Python.

## Current protections

- Blender bridge binds only to `127.0.0.1`.
- Every bridge start creates a new random bearer token.
- The token is discovered through an OS-temp state file rather than MCP arguments.
- `apply` parses Python AST before execution.
- Imports and common dynamic-execution/file APIs are blocked.
- Private/dunder attribute traversal is blocked.
- Blender save/load/import/export/script/preferences APIs are blocked from model-supplied code.
- A reduced builtin set is supplied to executed code.
- Request size and wait times are bounded.
- Disk checkpoints are retained adaptively.
- The Node bridge never automatically replays `apply` after an ambiguous transport failure.
- Reference files are exposed only through a user-selected root and `REF(rN)`; resolved paths must remain under that root.
- Reference enumeration is capped and large files are ignored.

## Rollback limitation

v0.1.2 pushes a best-effort Blender undo marker before `apply`. If generated code raises, one undo is attempted and the prior active object, selection, and mode are restored when possible. Blender undo is not an ACID transaction mechanism, so complex operator behavior can still make rollback incomplete.

## Reference workspace limitation

Choosing a reference root explicitly grants the local bridge permission to load supported images from that folder. Do not point it at a directory containing images you do not want an attached agent to access. Symlinks resolving outside the selected root are rejected.

## Important limitation

Python object-capability sandboxing is difficult. The AST guard prevents accidental and obvious unsafe behavior; it is not designed to contain adversarial payloads.

Therefore:

1. Never expose the bridge on a non-loopback interface.
2. Do not use `apply` with untrusted agents or arbitrary third-party prompts.
3. Keep important Blender projects under normal versioned/backed-up storage.
4. Treat generated `bpy` code as code execution with Blender-scoped authority.

Hardening should reduce authority inside the bridge rather than add model-visible approval chatter, because a small low-token control surface is a core project goal.
