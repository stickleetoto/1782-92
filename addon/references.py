from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy

ROOT_PROP = "p178292_reference_root"
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
MAX_REFERENCES = 128
MAX_REFERENCE_BYTES = 64 * 1024 * 1024

_NEXT_ID = 1
_PATH_TO_ID: dict[str, str] = {}
_ID_TO_PATH: dict[str, Path] = {}


def reset() -> None:
    global _NEXT_ID
    _NEXT_ID = 1
    _PATH_TO_ID.clear()
    _ID_TO_PATH.clear()


def root_path() -> Path | None:
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return None
    raw = str(getattr(scene, ROOT_PROP, "") or "").strip()
    if not raw:
        return None
    try:
        root = Path(bpy.path.abspath(raw)).expanduser().resolve()
    except Exception:
        return None
    return root if root.is_dir() else None


def _safe_files() -> list[Path]:
    root = root_path()
    if root is None:
        return []
    result: list[Path] = []
    try:
        candidates = sorted(root.rglob("*"), key=lambda p: str(p).lower())
    except OSError:
        return []
    for candidate in candidates:
        if len(result) >= MAX_REFERENCES:
            break
        try:
            if not candidate.is_file() or candidate.suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            resolved = candidate.resolve()
            resolved.relative_to(root)
            if resolved.stat().st_size > MAX_REFERENCE_BYTES:
                continue
        except (OSError, ValueError):
            continue
        result.append(resolved)
    return result


def _refresh() -> None:
    global _NEXT_ID
    files = _safe_files()
    live = {str(path) for path in files}
    for key in [key for key in _PATH_TO_ID if key not in live]:
        rid = _PATH_TO_ID.pop(key)
        _ID_TO_PATH.pop(rid, None)
    for path in files:
        key = str(path)
        if key not in _PATH_TO_ID:
            rid = f"r{_NEXT_ID}"
            _NEXT_ID += 1
            _PATH_TO_ID[key] = rid
            _ID_TO_PATH[rid] = path


def inspect_refs() -> dict[str, Any]:
    root = root_path()
    if root is None:
        return {"ok": True, "refs": [], "root": None}
    _refresh()
    rows: list[list[Any]] = []
    for rid, path in sorted(_ID_TO_PATH.items(), key=lambda item: int(item[0][1:])):
        try:
            rel = path.relative_to(root).as_posix()
            size_kb = max(1, (path.stat().st_size + 1023) // 1024)
        except (OSError, ValueError):
            continue
        rows.append([rid, rel, size_kb])
    return {"ok": True, "refs": rows, "root": root.name}


def resolve(reference_id: str) -> Path:
    _refresh()
    path = _ID_TO_PATH.get(reference_id)
    if path is None:
        raise KeyError(f"reference_not_found:{reference_id}")
    root = root_path()
    if root is None:
        raise KeyError("reference_root_missing")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise PermissionError("reference_escape") from exc
    return resolved


def image(reference_id: str) -> bpy.types.Image:
    path = resolve(reference_id)
    existing = next((img for img in bpy.data.images if Path(bpy.path.abspath(img.filepath)).resolve() == path), None)
    if existing is not None:
        return existing
    return bpy.data.images.load(str(path), check_existing=True)
