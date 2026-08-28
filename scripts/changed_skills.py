#!/usr/bin/env python3
"""Emit the JSON list of Skills affected by a Git diff."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ZERO_SHA = "0" * 40
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def discover_skill_names(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    names = []
    for child in sorted(repo_root.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or not child.is_dir() or child.is_symlink():
            continue
        skill_md = child / "SKILL.md"
        if (
            SKILL_NAME_PATTERN.fullmatch(child.name)
            and skill_md.is_file()
            and not skill_md.is_symlink()
        ):
            names.append(child.name)
    return tuple(names)


def select_skills_for_paths(
    paths: Iterable[str], skill_names: Iterable[str]
) -> list[str]:
    """Select only changed Skill roots; any root/unknown path selects all."""

    known = set(skill_names)
    selected: set[str] = set()
    saw_path = False
    for raw_path in paths:
        normalized = raw_path.strip().replace("\\", "/")
        if not normalized:
            continue
        saw_path = True
        parts = PurePosixPath(normalized).parts
        if not parts or parts[0] not in known:
            return sorted(known)
        selected.add(parts[0])
    return sorted(selected) if saw_path else []


def unusable_base(base: str | None) -> bool:
    return not base or base == ZERO_SHA or set(base) == {"0"}


def git_changed_paths(repo_root: Path, base: str, head: str) -> list[str] | None:
    completed = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", base, head],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return None
    return [os.fsdecode(item) for item in completed.stdout.split(b"\0") if item]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--base", help="base Git revision")
    parser.add_argument("--head", default="HEAD", help="head Git revision")
    parser.add_argument("--all", action="store_true", help="select every discovered Skill")
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="changed path for deterministic testing; repeat as needed",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    skills = discover_skill_names(repo_root)
    if not skills:
        print("error: no top-level Skills were discovered", file=sys.stderr)
        return 2

    if args.all:
        selected = list(skills)
    elif args.paths is not None:
        selected = select_skills_for_paths(args.paths, skills)
    elif unusable_base(args.base):
        selected = list(skills)
    else:
        paths = git_changed_paths(repo_root, args.base, args.head)
        selected = list(skills) if paths is None else select_skills_for_paths(paths, skills)

    print(json.dumps(selected, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
