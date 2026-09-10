from __future__ import annotations

import re
import tempfile
from pathlib import Path

_REV_SUFFIX = re.compile(r"(?:\.r\d{5})+$")


def canonical_stem(path: str | Path) -> str:
    stem = Path(path).stem
    clean = _REV_SUFFIX.sub("", stem)
    return clean or "unsaved"


def project_root(path: str | Path) -> Path:
    parent = Path(path).parent
    # A restored checkpoint already lives in <project>/.1782-92/checkpoints.
    # Also unwind accidental nested checkpoint roots created by older builds.
    while parent.name == "checkpoints" and parent.parent.name == ".1782-92":
        parent = parent.parent.parent
    return parent


def checkpoint_parts(filepath: str | Path | None) -> tuple[Path, str]:
    if filepath:
        path = Path(filepath)
        return project_root(path) / ".1782-92" / "checkpoints", canonical_stem(path)
    return Path(tempfile.gettempdir()) / "1782-92" / "checkpoints", "unsaved"
