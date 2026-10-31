from pathlib import Path
import sys

SKILL_ROOT = Path(__file__).resolve().parents[1]
for entry in (SKILL_ROOT / "scripts", SKILL_ROOT):
    path = str(entry)
    if path not in sys.path:
        sys.path.insert(0, path)
