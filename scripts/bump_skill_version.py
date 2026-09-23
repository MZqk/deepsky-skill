#!/usr/bin/env python3
"""Bump a Skill's independent SemVer version, update its metadata and CHANGELOG, and sync the manifest."""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

FRONTMATTER_PATTERN = re.compile(
    r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)

VERSION_LINE_PATTERN = re.compile(
    r'^(?P<indent>\s*version\s*:\s*)["\']?(?P<ver>[^"\']+)["\']?(?P<rest>.*)$',
    re.MULTILINE,
)

DEFAULT_TAG_FORMAT = "skill/{slug}/v{version}"


class SemVer(NamedTuple):
    major: int
    minor: int
    patch: int
    prerelease: str = ""
    build: str = ""

    @classmethod
    def parse(cls, version_str: str) -> SemVer:
        match = SEMVER_PATTERN.match(version_str.strip())
        if not match:
            raise ValueError(f"invalid SemVer string: {version_str!r}")
        major, minor, patch = (int(x) for x in match.groups()[:3])
        return cls(major, minor, patch)

    def bump(self, bump_type: str) -> SemVer:
        bump_type = bump_type.lower()
        if bump_type == "patch":
            return SemVer(self.major, self.minor, self.patch + 1)
        if bump_type == "minor":
            return SemVer(self.major, self.minor + 1, 0)
        if bump_type == "major":
            return SemVer(self.major + 1, 0, 0)
        raise ValueError(f"unsupported bump type: {bump_type!r}, must be major, minor, or patch")

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base = f"{base}-{self.prerelease}"
        if self.build:
            base = f"{base}+{self.build}"
        return base

    def compare_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def get_current_skill_version(skill_dir: Path) -> str:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"missing SKILL.md in {skill_dir}")
    content = skill_md.read_text(encoding="utf-8")
    fm_match = FRONTMATTER_PATTERN.match(content)
    if not fm_match:
        raise ValueError(f"invalid or missing YAML frontmatter in {skill_md}")
    yaml_text = fm_match.group("yaml")
    v_match = VERSION_LINE_PATTERN.search(yaml_text)
    if not v_match:
        raise ValueError(f"cannot find version field in frontmatter of {skill_md}")
    return v_match.group("ver").strip()


def update_skill_md_version(skill_dir: Path, new_version: str, dry_run: bool = False) -> str:
    skill_md = skill_dir / "SKILL.md"
    content = skill_md.read_text(encoding="utf-8")
    fm_match = FRONTMATTER_PATTERN.match(content)
    if not fm_match:
        raise ValueError(f"invalid or missing YAML frontmatter in {skill_md}")
    yaml_text = fm_match.group("yaml")

    def _replace_version(m: re.Match[str]) -> str:
        indent = m.group("indent")
        rest = m.group("rest")
        return f'{indent}"{new_version}"{rest}'

    new_yaml, count = VERSION_LINE_PATTERN.subn(_replace_version, yaml_text, count=1)
    if count == 0:
        raise ValueError(f"failed to substitute version in {skill_md}")
    new_content = content[: fm_match.start("yaml")] + new_yaml + content[fm_match.end("yaml") :]
    if not dry_run:
        skill_md.write_text(new_content, encoding="utf-8")
    return new_content


def update_changelog(
    skill_dir: Path,
    new_version: str,
    message: str | None = None,
    dry_run: bool = False,
    date_str: str | None = None,
) -> str:
    changelog_path = skill_dir / "CHANGELOG.md"
    if not changelog_path.is_file():
        raise FileNotFoundError(f"missing CHANGELOG.md in {skill_dir}")
    content = changelog_path.read_text(encoding="utf-8")

    if f"## [{new_version}]" in content:
        raise ValueError(f"CHANGELOG.md in {skill_dir.name} already contains section ## [{new_version}]")

    date_label = date_str or datetime.date.today().isoformat()
    entry_text = f"- {message.strip()}\n" if message and message.strip() else "- 待补充变更条目。\n"
    new_section = f"## [{new_version}] - {date_label}\n\n{entry_text}\n"

    # Insert before the first existing release heading "## ["
    first_heading = re.search(r"^##\s+\[", content, re.MULTILINE)
    if first_heading:
        pos = first_heading.start()
        new_content = content[:pos] + new_section + content[pos:]
    else:
        new_content = content.rstrip() + "\n\n" + new_section

    if not dry_run:
        changelog_path.write_text(new_content, encoding="utf-8")
    return new_content


def update_manifest(
    repo_root: Path,
    slug: str,
    new_version: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    manifest_path = repo_root / "skills.manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    skills = data.setdefault("skills", {})
    if slug not in skills:
        skills[slug] = {"path": slug, "version": new_version}
    else:
        skills[slug]["version"] = new_version

    formatted = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if not dry_run:
        manifest_path.write_text(formatted, encoding="utf-8")
    return data


def bump_skill(
    slug: str,
    target: str,
    message: str | None = None,
    repo_root: Path = REPO_ROOT,
    dry_run: bool = False,
    do_commit: bool = False,
    do_tag: bool = False,
    tag_format: str = DEFAULT_TAG_FORMAT,
    date_str: str | None = None,
) -> tuple[str, str]:
    """Execute full bump workflow. Returns (old_version, new_version)."""
    skill_dir = repo_root / slug
    if not skill_dir.is_dir() or not (skill_dir / "SKILL.md").is_file():
        raise ValueError(f"unknown or invalid Skill directory: {slug}")

    old_version_str = get_current_skill_version(skill_dir)
    old_semver = SemVer.parse(old_version_str)

    target_clean = target.strip().lower()
    if target_clean in ("patch", "minor", "major"):
        new_semver = old_semver.bump(target_clean)
        new_version_str = str(new_semver)
    else:
        new_semver = SemVer.parse(target)
        if new_semver.compare_tuple() <= old_semver.compare_tuple():
            raise ValueError(
                f"new version {new_semver} must be strictly greater than current version {old_semver}"
            )
        new_version_str = str(new_semver)

    # 1. Update SKILL.md
    update_skill_md_version(skill_dir, new_version_str, dry_run=dry_run)

    # 2. Update CHANGELOG.md
    update_changelog(skill_dir, new_version_str, message=message, dry_run=dry_run, date_str=date_str)

    # 3. Update skills.manifest.json
    update_manifest(repo_root, slug, new_version_str, dry_run=dry_run)

    tag_name = tag_format.format(slug=slug, version=new_version_str)

    if not dry_run and (do_commit or do_tag):
        # Stage files
        files_to_add = [
            str(skill_dir / "SKILL.md"),
            str(skill_dir / "CHANGELOG.md"),
            str(repo_root / "skills.manifest.json"),
        ]
        subprocess.run(["git", "add", *files_to_add], cwd=repo_root, check=True)
        commit_msg = f"chore(release): bump {slug} to v{new_version_str}"
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_root, check=True)

        if do_tag:
            subprocess.run(
                ["git", "tag", "-a", tag_name, "-m", f"Release {slug} v{new_version_str}"],
                cwd=repo_root,
                check=True,
            )

    return old_version_str, new_version_str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="Skill slug/directory name to bump")
    parser.add_argument(
        "target",
        help="new SemVer (e.g. 1.0.2) or bump keyword: patch, minor, major",
    )
    parser.add_argument("-m", "--message", help="summary of changes for CHANGELOG.md")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="repository root")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="preview changes without modifying files or git state",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="automatically commit the version bump files",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="create annotated git tag on the bump commit",
    )
    parser.add_argument(
        "--tag-format",
        default=DEFAULT_TAG_FORMAT,
        help=f"git tag format string (default: {DEFAULT_TAG_FORMAT})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        old_v, new_v = bump_skill(
            slug=args.slug,
            target=args.target,
            message=args.message,
            repo_root=args.repo_root.resolve(),
            dry_run=args.dry_run,
            do_commit=args.commit or args.tag,
            do_tag=args.tag,
            tag_format=args.tag_format,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    action_label = "[dry-run] would bump" if args.dry_run else "bumped"
    print(f"{action_label} {args.slug}: {old_v} -> {new_v}")
    if args.tag and not args.dry_run:
        tag_name = args.tag_format.format(slug=args.slug, version=new_v)
        print(f"created git tag: {tag_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
