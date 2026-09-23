#!/usr/bin/env python3
"""Install or uninstall Git pre-commit hook for repository and Skill contracts."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / ".git" / "hooks"
PRE_COMMIT_FILE = HOOKS_DIR / "pre-commit"

HOOK_CONTENT = """#!/bin/sh
# Git pre-commit hook managed by deepsky-skill
# Runs repository governance, manifest, and contract validation before commit.

set -e

echo "[pre-commit] Validating repository governance and skills.manifest.json..."
python3 scripts/validate_repository.py

echo "[pre-commit] All repository checks passed."
"""


def install_hook() -> int:
    if not (REPO_ROOT / ".git").is_dir():
        print("error: .git directory not found. Must be run within a Git repository.", file=sys.stderr)
        return 1

    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    PRE_COMMIT_FILE.write_text(HOOK_CONTENT, encoding="utf-8")
    current_mode = PRE_COMMIT_FILE.stat().st_mode
    PRE_COMMIT_FILE.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed pre-commit hook at: {PRE_COMMIT_FILE}")
    return 0


def uninstall_hook() -> int:
    if PRE_COMMIT_FILE.is_file():
        PRE_COMMIT_FILE.unlink()
        print(f"removed pre-commit hook at: {PRE_COMMIT_FILE}")
    else:
        print("no pre-commit hook found to remove.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="uninstall the pre-commit hook",
    )
    args = parser.parse_args(argv)
    if args.uninstall:
        return uninstall_hook()
    return install_hook()


if __name__ == "__main__":
    raise SystemExit(main())
