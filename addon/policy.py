from __future__ import annotations

import ast

# The bridge is deliberately optimized for short, reviewable modeling batches.
# Bigger generated programs waste model context and are harder to recover safely.
MAX_CODE_BYTES = 24_576
MAX_AST_NODES = 1_200
MAX_LITERAL_RANGE = 256
MAX_ESTIMATED_BPY_OPS = 96

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


def _const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return int(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _const_int(node.operand)
        return -value if value is not None else None
    return None


def _literal_range_iterations(node: ast.AST) -> int | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id != "range":
        return None
    if node.keywords or not 1 <= len(node.args) <= 3:
        return None
    values = [_const_int(arg) for arg in node.args]
    if any(value is None for value in values):
        return None
    ints = [int(value) for value in values if value is not None]
    if len(ints) == 1:
        start, stop, step = 0, ints[0], 1
    elif len(ints) == 2:
        start, stop, step = ints[0], ints[1], 1
    else:
        start, stop, step = ints
    if step == 0:
        return None
    return len(range(start, stop, step))


def _direct_bpy_ops(node: ast.AST) -> int:
    count = 0
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            chain = _chain(child.func)
            if chain == "bpy.ops" or chain.startswith("bpy.ops."):
                count += 1
    return count


def _estimated_bpy_ops(tree: ast.AST) -> int:
    """Conservative static estimate for literal-range loops around bpy operators.

    The Python deadline cannot pre-empt one long-running C call in Blender. Bounding
    obviously large generated operator loops prevents the common case from ever
    reaching Blender's main thread. Unknown/dynamic iterables are left alone and
    remain protected by the normal source/AST/deadline guards.
    """
    total = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if not (chain == "bpy.ops" or chain.startswith("bpy.ops.")):
            continue
        multiplier = 1
        parent = getattr(node, "_p1782_parent", None)
        while parent is not None:
            if isinstance(parent, (ast.For, ast.comprehension)):
                iterations = _literal_range_iterations(parent.iter)
                if iterations is not None:
                    multiplier *= max(0, iterations)
            parent = getattr(parent, "_p1782_parent", None)
        total += multiplier
        if total > MAX_ESTIMATED_BPY_OPS:
            return total
    return total


def _attach_parents(tree: ast.AST) -> None:
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "_p1782_parent", parent)


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
        if isinstance(node, (ast.For, ast.comprehension)):
            iterations = _literal_range_iterations(node.iter)
            if iterations is not None and iterations > MAX_LITERAL_RANGE:
                raise ValueError(f"loop_too_large:{iterations}>{MAX_LITERAL_RANGE}:split_batch")

    _attach_parents(tree)
    estimated_ops = _estimated_bpy_ops(tree)
    if estimated_ops > MAX_ESTIMATED_BPY_OPS:
        raise ValueError(f"operator_work_too_large:{estimated_ops}>{MAX_ESTIMATED_BPY_OPS}:split_batch")
    return tree
