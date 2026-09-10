from __future__ import annotations

import ast

# The bridge is deliberately optimized for short, reviewable modeling batches.
# Bigger generated programs waste model context and are harder to recover safely.
MAX_CODE_BYTES = 24_576
MAX_AST_NODES = 1_200

BANNED_NAMES = {
    "__import__", "breakpoint", "compile", "delattr", "eval", "exec", "getattr",
    "globals", "help", "input", "locals", "open", "setattr", "vars",
}

BANNED_PREFIXES = (
    "bpy.app", "bpy.context.preferences", "bpy.data.libraries", "bpy.ops.console",
    "bpy.ops.ed", "bpy.ops.export", "bpy.ops.file", "bpy.ops.image", "bpy.ops.import",
    "bpy.ops.preferences", "bpy.ops.render", "bpy.ops.script", "bpy.ops.wm",
    "bpy.path", "bpy.utils",
)

BANNED_ATTRS = {
    "filepath", "filepath_raw", "load", "load_post", "load_pre", "reload", "save",
    "save_post", "save_pre", "save_render", "write",
}


def _chain(node: ast.AST) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def validate_code(code: str) -> ast.AST:
    size = len(code.encode("utf-8"))
    if size > MAX_CODE_BYTES:
        raise ValueError(f"batch_too_large:{size}>{MAX_CODE_BYTES}:split_batch")
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"syntax@{exc.lineno}:{exc.offset}:{exc.msg}") from exc

    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_AST_NODES:
        raise ValueError(f"batch_too_complex:{len(nodes)}>{MAX_AST_NODES}:split_batch")

    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("import_blocked")
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise ValueError(f"name_blocked:{node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_"):
                raise ValueError("private_attribute_blocked")
            if node.attr in BANNED_ATTRS:
                raise ValueError(f"attribute_blocked:{node.attr}")
            chain = _chain(node)
            if any(chain == prefix or chain.startswith(prefix + ".") for prefix in BANNED_PREFIXES):
                raise ValueError(f"api_blocked:{chain}")
    return tree
