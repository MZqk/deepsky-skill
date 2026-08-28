from __future__ import annotations

from pathlib import Path

from scripts.changed_skills import (
    ZERO_SHA,
    discover_skill_names,
    select_skills_for_paths,
    unusable_base,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_single_skill_path_selects_only_that_skill() -> None:
    skills = discover_skill_names(REPO_ROOT)
    selected = skills[0]
    assert select_skills_for_paths([f"{selected}/SKILL.md"], skills) == [selected]


def test_multiple_skill_paths_select_only_changed_skills() -> None:
    skills = discover_skill_names(REPO_ROOT)
    selected = (skills[0], skills[-1])
    assert select_skills_for_paths(
        [f"{selected[0]}/tests/test_a.py", f"{selected[1]}/SKILL.md"], skills
    ) == sorted(selected)


def test_root_governance_change_selects_every_skill() -> None:
    skills = discover_skill_names(REPO_ROOT)
    assert select_skills_for_paths(["README.md"], skills) == list(skills)
    assert select_skills_for_paths([".github/workflows/skills-ci.yml"], skills) == list(skills)


def test_unknown_or_deleted_top_level_directory_selects_every_skill() -> None:
    skills = discover_skill_names(REPO_ROOT)
    assert select_skills_for_paths(["retired-skill/SKILL.md"], skills) == list(skills)


def test_empty_valid_diff_selects_no_skill() -> None:
    assert select_skills_for_paths([], discover_skill_names(REPO_ROOT)) == []


def test_missing_or_zero_base_requires_full_matrix() -> None:
    assert unusable_base(None)
    assert unusable_base("")
    assert unusable_base(ZERO_SHA)
    assert not unusable_base("a" * 40)
