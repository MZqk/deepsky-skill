"""Shared pytest bootstrap: put the skill's scripts/ on sys.path.

Each test module used to repeat this insert at import time, which made the
suite order-dependent: a module that skipped it only worked because an
earlier module had already mutated sys.path.
"""
from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]

for entry in (SKILL_ROOT / "scripts", SKILL_ROOT):
    path = str(entry)
    if path not in sys.path:
        sys.path.insert(0, path)

from .test_recomposition_sources_v11 import linear_case
