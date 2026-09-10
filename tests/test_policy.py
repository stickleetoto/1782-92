from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[1] / "addon" / "policy.py"
SPEC = importlib.util.spec_from_file_location("p178292_policy", POLICY_PATH)
assert SPEC is not None and SPEC.loader is not None
POLICY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POLICY)
validate_code = POLICY.validate_code


class PolicyTests(unittest.TestCase):
    def assert_blocked(self, code: str, prefix: str) -> None:
        with self.assertRaisesRegex(ValueError, f"^{prefix}"):
            validate_code(code)

    def test_allows_normal_bpy_modeling(self) -> None:
        tree = validate_code('o = O("o1")\no.scale.z *= 1.1\nbpy.ops.mesh.primitive_cube_add(size=2)')
        self.assertIsNotNone(tree)

    def test_blocks_imports(self) -> None:
        self.assert_blocked("import os", "import_blocked")

    def test_blocks_file_io(self) -> None:
        self.assert_blocked('open("x", "w")', "name_blocked:open")

    def test_blocks_dynamic_exec(self) -> None:
        self.assert_blocked('exec("print(1)")', "name_blocked:exec")

    def test_blocks_private_traversal(self) -> None:
        self.assert_blocked("x.__class__", "private_attribute_blocked")

    def test_blocks_sensitive_bpy_prefix(self) -> None:
        self.assert_blocked('bpy.ops.wm.open_mainfile(filepath="x")', "api_blocked:bpy.ops.wm")

    def test_blocks_image_load_directly(self) -> None:
        self.assert_blocked('bpy.data.images.load("x.png")', "attribute_blocked:load")


if __name__ == "__main__":
    unittest.main()
