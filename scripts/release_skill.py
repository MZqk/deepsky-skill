#!/usr/bin/env python3
"""Package a Skill into standalone release archives (.zip / .tar.gz), extract its release notes, and optionally create a GitHub Release."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

FRONTMATTER_PATTERN = re.compile(
    r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)
VERSION_LINE_PATTERN = re.compile(
    r'^\s*version\s*:\s*["\']?([^"\']+)["\']?\s*$', re.MULTILINE
)

# Common patterns to exclude when archiving a skill
EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".git",
}
EXCLUDE_EXTS = {".pyc", ".pyo", ".pyd", ".DS_Store"}


def parse_tag(tag_str: str) -> tuple[str, str]:
    """Parse tag like 'skill/slug/v1.0.0' or 'slug/v1.0.0' or 'slug/1.0.0' into (slug, version)."""
    parts = tag_str.strip().strip("/").split("/")
    if len(parts) == 3 and parts[0] == "skill":
        slug = parts[1]
        ver = parts[2].lstrip("v")
        return slug, ver
    if len(parts) == 2:
        slug = parts[0]
        ver = parts[1].lstrip("v")
        return slug, ver
    raise ValueError(
        f"unrecognized tag format {tag_str!r}; expected 'skill/{{slug}}/v{{version}}' or '{{slug}}/v{{version}}'"
    )


def get_skill_version_from_skill_md(skill_dir: Path) -> str:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"missing SKILL.md in {skill_dir}")
    content = skill_md.read_text(encoding="utf-8")
    fm = FRONTMATTER_PATTERN.match(content)
    if not fm:
        raise ValueError(f"missing YAML frontmatter in {skill_md}")
    v_match = VERSION_LINE_PATTERN.search(fm.group("yaml"))
    if not v_match:
        raise ValueError(f"missing version field in frontmatter of {skill_md}")
    return v_match.group(1).strip()


def extract_changelog_notes(skill_dir: Path, version: str) -> str:
    """Extract release notes section for given version from CHANGELOG.md."""
    changelog_path = skill_dir / "CHANGELOG.md"
    if not changelog_path.is_file():
        return f"- Release {skill_dir.name} v{version}\n"

    text = changelog_path.read_text(encoding="utf-8")
    # Match heading like: ## [1.0.4] - 2026-10-28 or ## [0.3.3] - 2026-10-08 — description
    pattern = rf"^##\s+\[{re.escape(version)}\](?:[^\n]*)\n(.*?)(?=\n##\s+\[|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    if match:
        body = match.group(1).strip()
        if body:
            return body + "\n"

    return f"- Release {skill_dir.name} v{version}\n"


def should_exclude(rel_path: Path) -> bool:
    for part in rel_path.parts:
        if part in EXCLUDE_DIRS:
            return True
    if rel_path.name.startswith(".DS_Store"):
        return True
    if rel_path.suffix in EXCLUDE_EXTS:
        return True
    return False


def create_zip_archive(skill_dir: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    slug = skill_dir.name

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(skill_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                file_path = Path(root) / file
                rel_to_skill = file_path.relative_to(skill_dir)
                if should_exclude(rel_to_skill):
                    continue
                # Archive name starts with slug directory
                arcname = Path(slug) / rel_to_skill
                zf.write(file_path, arcname.as_posix())

    return out_path


def create_tar_archive(skill_dir: Path, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    slug = skill_dir.name

    def tar_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        rel = Path(tarinfo.name)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            return None
        if rel.suffix in EXCLUDE_EXTS or rel.name.startswith(".DS_Store"):
            return None
        return tarinfo

    with tarfile.open(out_path, "w:gz") as tf:
        tf.add(skill_dir, arcname=slug, filter=tar_filter)

    return out_path


def package_skill(
    slug: str,
    version: str | None = None,
    output_dir: Path | None = None,
    repo_root: Path = REPO_ROOT,
) -> tuple[Path, Path, Path, str]:
    """Build zip & tar.gz archives and dump release notes.

    Returns: (zip_path, tar_path, notes_path, version)
    """
    skill_dir = repo_root / slug
    if not skill_dir.is_dir():
        raise FileNotFoundError(f"skill directory not found: {skill_dir}")

    if not version:
        version = get_skill_version_from_skill_md(skill_dir)

    out_dir = output_dir or (repo_root / "dist")
    out_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{slug}-{version}"
    zip_path = out_dir / f"{base_name}.zip"
    tar_path = out_dir / f"{base_name}.tar.gz"
    notes_path = out_dir / f"{base_name}-notes.md"

    # 1. Zip
    create_zip_archive(skill_dir, zip_path)
    # 2. Tar
    create_tar_archive(skill_dir, tar_path)
    # 3. Notes
    notes_text = extract_changelog_notes(skill_dir, version)
    notes_path.write_text(notes_text, encoding="utf-8")

    return zip_path, tar_path, notes_path, version


def create_gh_release(
    slug: str,
    version: str,
    tag: str,
    zip_path: Path,
    tar_path: Path,
    notes_path: Path,
    draft: bool = False,
    prerelease: bool = False,
    repo: str | None = None,
) -> None:
    title = f"{slug} v{version}"
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        str(zip_path),
        str(tar_path),
        "--title",
        title,
        "--notes-file",
        str(notes_path),
    ]
    if draft:
        cmd.append("--draft")
    if prerelease:
        cmd.append("--prerelease")
    if repo:
        cmd.extend(["--repo", repo])

    print(f"Executing: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        help="Skill slug (e.g. 'deep-sky-processor') or tag (e.g. 'skill/deep-sky-processor/v0.1.41' or 'deep-sky-processor/v0.1.41')",
    )
    parser.add_argument(
        "--version",
        help="explicit version string; defaults to tag version or SKILL.md version",
    )
    parser.add_argument(
        "--tag",
        help="explicit git tag name to associate with the release (defaults to skill/{slug}/v{version})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "dist",
        help="directory to place built archives (default: ./dist)",
    )
    parser.add_argument(
        "--gh-release",
        action="store_true",
        help="call 'gh release create' to publish on GitHub",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repository target for 'gh' CLI (e.g. 'MZqk/deepsky-skill')",
    )
    parser.add_argument(
        "--draft",
        action="store_true",
        help="create GitHub release as draft",
    )
    parser.add_argument(
        "--prerelease",
        action="store_true",
        help="mark GitHub release as prerelease",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    target_str = args.target.strip()
    slug: str
    version: str | None = args.version

    if "/" in target_str:
        tag_slug, tag_ver = parse_tag(target_str)
        slug = tag_slug
        if not version:
            version = tag_ver
        tag = args.tag or target_str
    else:
        slug = target_str
        tag = args.tag

    zip_path, tar_path, notes_path, resolved_version = package_skill(
        slug=slug,
        version=version,
        output_dir=args.output_dir,
    )

    if not tag:
        tag = f"skill/{slug}/v{resolved_version}"

    print(f"Successfully packaged {slug} v{resolved_version}:")
    print(f"  - Zip:   {zip_path} ({zip_path.stat().st_size:,} bytes)")
    print(f"  - Tar:   {tar_path} ({tar_path.stat().st_size:,} bytes)")
    print(f"  - Notes: {notes_path}")

    if args.gh_release:
        create_gh_release(
            slug=slug,
            version=resolved_version,
            tag=tag,
            zip_path=zip_path,
            tar_path=tar_path,
            notes_path=notes_path,
            draft=args.draft,
            prerelease=args.prerelease,
            repo=args.repo,
        )
        print(f"Published GitHub Release for {tag}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
