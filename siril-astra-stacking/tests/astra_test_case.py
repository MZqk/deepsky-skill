"""Base test case and test utilities for siril-astra-stacking."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / "scripts", _SKILL / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


class AstraTestCase(unittest.TestCase):
    """Base test case providing isolated temporary directory and check assertion."""

    def setUp(self):
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="astra-test-")
        self.tmp = self._temp_dir.name

    def tearDown(self):
        self._temp_dir.cleanup()
        super().tearDown()

    def check(self, label: str, condition: bool, detail: object = ""):
        """Record one assertion with optional detail string."""
        if detail:
            self.assertTrue(condition, f"{label}: {detail}")
        else:
            self.assertTrue(condition, label)


def section(title: str):
    """Section marker (kept for readability and log output compatibility)."""
    pass


def run_main(argv: list[str], entry=None) -> tuple[int, str, str]:
    """Call ``astra_stack.main`` in-process and capture both output streams.

    Args:
        argv: Argument list for ``main``.
        entry: The imported ``astra_stack`` module. Defaults to a local import.

    Returns:
        ``(exit_code, stdout_text, stderr_text)``.
    """
    if entry is None:
        from astra_stack import main as pipeline_main
    else:
        pipeline_main = entry.main
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = pipeline_main(argv)
    return code, out.getvalue(), err.getvalue()
