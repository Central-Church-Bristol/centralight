"""Small checks on the code itself."""

from __future__ import annotations

import ast
import collections
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CodeHealthTests(unittest.TestCase):
    def test_no_method_is_defined_twice(self) -> None:
        """A second definition silently replaces the first (that once switched off the window's saved splits)."""
        found = []
        for path in sorted((ROOT / "centralight").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    counts = collections.Counter(n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
                    found += [f"{path.relative_to(ROOT)}: {node.name}.{name}" for name, count in counts.items() if count > 1]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
