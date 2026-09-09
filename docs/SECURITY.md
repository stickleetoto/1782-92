# Security

1782-92 is designed for a trusted local AI client controlling a local Blender session. It is not a security boundary for hostile Python.

## Current protections

- Blender bridge binds only to `127.0.0.1`.
- Every bridge start creates a new random bearer token.
- The token is discovered through an OS-temp state file rather than embedded in MCP tool arguments.
- `apply` parses Python AST before execution.
- Python imports and common dynamic-execution/file APIs are blocked.
- Private/dunder attribute traversal is blocked.
- Blender save/load/import/export/script/preferences APIs are blocked from model-supplied code.
- A reduced builtin set is supplied to executed code.
- Request size and execution wait times are bounded.
- Successful edits receive automatic checkpoint copies.

## Important limitation

Python object-capability sandboxing is difficult. The AST guard is meant to prevent accidental or obvious unsafe behavior, not to safely execute adversarial code. A sufficiently determined hostile payload may find a bypass.

Therefore:

1. Never expose the bridge on a non-loopback interface.
2. Do not use `apply` with untrusted agents or arbitrary third-party prompts.
3. Keep important Blender projects under normal versioned/backed-up storage.
4. Treat generated `bpy` code as code execution with Blender-scoped authority.

Security hardening should prefer reducing authority inside the bridge rather than adding more model-visible approval chatter, because the project's purpose is a small, low-token control surface.
