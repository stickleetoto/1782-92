from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "addon" / "checkpoint_paths.py"
SPEC = importlib.util.spec_from_file_location("p178292_checkpoint_paths", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class CheckpointPathTests(unittest.TestCase):
    def test_strips_restored_revision_suffix(self) -> None:
        self.assertEqual(MOD.canonical_stem("Speaki.r00013.blend"), "Speaki")

    def test_strips_repeated_revision_suffixes(self) -> None:
        self.assertEqual(MOD.canonical_stem("Speaki.r00013.r00001.blend"), "Speaki")

    def test_keeps_normal_stem(self) -> None:
        self.assertEqual(MOD.canonical_stem("Speaki.blend"), "Speaki")

    def test_restored_checkpoint_reuses_project_root(self) -> None:
        path = Path("/project/.1782-92/checkpoints/Speaki.r00013.blend")
        root, stem = MOD.checkpoint_parts(path)
        self.assertEqual(root, Path("/project/.1782-92/checkpoints"))
        self.assertEqual(stem, "Speaki")

    def test_unwinds_accidental_nested_checkpoint_root(self) -> None:
        path = Path("/project/.1782-92/checkpoints/.1782-92/checkpoints/Speaki.r00013.r00001.blend")
        root, stem = MOD.checkpoint_parts(path)
        self.assertEqual(root, Path("/project/.1782-92/checkpoints"))
        self.assertEqual(stem, "Speaki")


if __name__ == "__main__":
    unittest.main()
