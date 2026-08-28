from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.validate_repository import (
    discover_skill_dirs,
    load_frontmatter,
    validate_readme,
    validate_repository,
    validate_skill_dir,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
ROUTER = REPO_ROOT / "deep-sky-capture-advisor" / "scripts" / "query_knowledge.py"


def test_repository_structure_and_readme_are_valid() -> None:
    assert validate_repository(REPO_ROOT) == []


@pytest.mark.parametrize(
    ("query", "reason", "route"),
    (
        (
            "Analyze this FITS image file for gradients",
            "file_backed_image_analysis",
            "$deep-sky-advisor",
        ),
        (
            "Process this deep-sky image and produce an image",
            "file_backed_pixel_processing",
            "$deep-sky-processor",
        ),
    ),
)
def test_capture_advisor_public_cli_routes_to_existing_skill(
    query: str,
    reason: str,
    route: str,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROUTER),
            query,
            "--top",
            "1",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    guidance = json.loads(completed.stdout)["guidance"]
    assert guidance["skill_scope"] == "out_of_scope"
    assert guidance["should_exit_skill"] is True
    assert reason in guidance["scope_reasons"]
    assert guidance["recommended_route"] == route

    target = route.removeprefix("$")
    skills = discover_skill_dirs(REPO_ROOT)
    assert target in skills
    assert load_frontmatter(skills[target] / "SKILL.md")["name"] == target


def _copy_governance_fixture(tmp_path: Path) -> Path:
    destination = tmp_path / "fixture-skill"
    destination.mkdir()
    files = {
        "SKILL.md": """---
name: fixture-skill
description: Validate a deterministic test fixture for repository governance.
license: Proprietary
metadata:
  slug: fixture-skill
  version: "0.1.0"
  displayName: Fixture Skill
  summary: Deterministic repository-governance fixture.
  tags: [fixture, test]
  homepage: https://github.com/MZqk/deepsky-skill
---

# Fixture Skill
""",
        "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - 2026-08-28\n",
        "LICENSE.md": "Copyright 2026 MZqk. All rights reserved.\n",
        "RELEASING.md": "Use the `fixture-skill/vX.Y.Z` tag.\n",
        "requirements.txt": "fixture-runtime>=1\n",
        "requirements-dev.txt": (
            "-r requirements.txt\n\npytest>=8,<10\nPyYAML>=6,<7\n"
        ),
    }
    for filename, content in files.items():
        (destination / filename).write_text(content, encoding="utf-8")
    return destination


def test_structure_validation_rejects_missing_governance_file(tmp_path: Path) -> None:
    skill = _copy_governance_fixture(tmp_path)
    (skill / "CHANGELOG.md").unlink()
    assert any("missing regular governance file CHANGELOG.md" in error for error in validate_skill_dir(skill))


def _replace_skill_text(skill: Path, pattern: str, replacement: str) -> None:
    skill_md = skill / "SKILL.md"
    text, count = re.subn(
        pattern,
        replacement,
        skill_md.read_text(encoding="utf-8"),
        count=1,
        flags=re.MULTILINE,
    )
    assert count == 1
    skill_md.write_text(text, encoding="utf-8")


def test_structure_validation_rejects_invalid_version(tmp_path: Path) -> None:
    skill = _copy_governance_fixture(tmp_path)
    version = load_frontmatter(skill / "SKILL.md")["metadata"]["version"]
    _replace_skill_text(
        skill,
        rf'(^\s*version:\s*)["\']?{re.escape(version)}["\']?\s*$',
        r'\g<1>"01.0.0"',
    )
    assert any("SemVer" in error for error in validate_skill_dir(skill))


def test_structure_validation_rejects_license_mismatch(tmp_path: Path) -> None:
    skill = _copy_governance_fixture(tmp_path)
    _replace_skill_text(skill, r"^license: Proprietary$", "license: MIT")
    assert any("license must be Proprietary" in error for error in validate_skill_dir(skill))


def test_structure_validation_rejects_name_mismatch(tmp_path: Path) -> None:
    skill = _copy_governance_fixture(tmp_path)
    _replace_skill_text(skill, rf"^name: {re.escape(skill.name)}$", "name: renamed-advisor")
    assert any("must match its directory" in error for error in validate_skill_dir(skill))


def test_structure_validation_rejects_duplicate_yaml_key(tmp_path: Path) -> None:
    skill = _copy_governance_fixture(tmp_path)
    _replace_skill_text(
        skill,
        rf"^name: {re.escape(skill.name)}$",
        f"name: {skill.name}\nname: {skill.name}",
    )
    assert any("duplicate key 'name'" in error for error in validate_skill_dir(skill))


def test_readme_validation_rejects_nonexistent_catalog_entry(tmp_path: Path) -> None:
    skill_names = discover_skill_dirs(REPO_ROOT)
    for skill_name in skill_names:
        (tmp_path / skill_name).mkdir()
    (tmp_path / "ghost-skill").mkdir()
    text = README.read_text(encoding="utf-8").replace(
        "<!-- skills-index:end -->",
        "| [`ghost-skill`](ghost-skill/) | 不存在 | 不存在 |\n<!-- skills-index:end -->",
    )
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    errors = validate_readme(tmp_path, skill_names)
    assert any("README.md catalog mismatch" in error and "ghost-skill" in error for error in errors)


def test_readme_validation_rejects_path_escape(tmp_path: Path) -> None:
    skill_names = discover_skill_dirs(REPO_ROOT)
    for skill_name in skill_names:
        (tmp_path / skill_name).mkdir()
    outside = tmp_path.parent / "outside.md"
    outside.write_text("outside\n", encoding="utf-8")
    text = README.read_text(encoding="utf-8") + "\n[escape](../outside.md)\n"
    (tmp_path / "README.md").write_text(text, encoding="utf-8")
    assert any(
        "local link escapes the repository" in error
        for error in validate_readme(tmp_path, skill_names)
    )
