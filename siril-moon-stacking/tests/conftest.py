from __future__ import annotations

import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
for path in (SKILL_ROOT / "scripts", SKILL_ROOT / "tests"):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
