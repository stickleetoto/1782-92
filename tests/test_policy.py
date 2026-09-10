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

    def test_allows_compact_modeling_helpers(self) -> None:
        tree = validate_code('M.ellipsoid("Head", (0,0,1), (.1,.08,.12), "Body", "Skin")')
        self.assertIsNotNone(tree)

    def test_blocks_oversized_generated_batch(self) -> None:
        # One syntactically-valid statement whose payload alone exceeds the
        # byte budget, so this exercises size before AST-complexity limits.
        code = 'x = "' + ("a" * (POLICY.MAX_CODE_BYTES + 1)) + '"'
        self.assert_blocked(code, "batch_too_large")

    def test_blocks_overcomplex_generated_batch(self) -> None:
        code = "\n".join(f"x{i} = {i}" for i in range(500))
        self.assertLess(len(code.encode("utf-8")), POLICY.MAX_CODE_BYTES)
        self.assert_blocked(code, "batch_too_complex")

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

    def test_blocks_render_file_write(self) -> None:
        self.assert_blocked('bpy.context.scene.render.filepath="/tmp/x.png"', "attribute_blocked:filepath")
        self.assert_blocked('bpy.ops.render.render(write_still=True)', "api_blocked:bpy.ops.render")

    def test_blocks_undo_escape(self) -> None:
        self.assert_blocked('bpy.ops.ed.undo()', "api_blocked:bpy.ops.ed")

    def test_blocks_reference_retarget_reload(self) -> None:
        self.assert_blocked('REF("r1").reload()', "attribute_blocked:reload")


if __name__ == "__main__":
    unittest.main()
