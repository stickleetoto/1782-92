from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "addon" / "execution_budget.py"
SPEC = importlib.util.spec_from_file_location("p178292_execution_budget", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BUDGET = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUDGET)


class ExecutionBudgetTests(unittest.TestCase):
    def test_deadline_interrupts_python_loop(self) -> None:
        with self.assertRaises(BUDGET.ApplyBudgetExceeded):
            with BUDGET.deadline(0.001):
                total = 0
                for value in range(1_000_000):
                    total += value
        self.assertGreaterEqual(total, 0)

    def test_rejects_non_positive_deadline(self) -> None:
        with self.assertRaisesRegex(ValueError, "deadline_seconds"):
            with BUDGET.deadline(0):
                pass


if __name__ == "__main__":
    unittest.main()
