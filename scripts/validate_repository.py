#!/usr/bin/env python3
"""Validate independent Skill governance and the repository README catalog."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

import yaml
from yaml.constructor import ConstructorError


REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
}
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
FRONTMATTER_PATTERN = re.compile(
    r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)
VERSION_QUOTED_PATTERN = re.compile(
    r'^\s*version\s*:\s*"([^"]+)"\s*$', re.MULTILINE
)
REQUIRED_FILES = ("CHANGELOG.md", "RELEASING.md", "requirements-dev.txt")


def parse_semver(version_str: str) -> tuple[int, int, int]:
    match = SEMVER_PATTERN.match(version_str.strip())
    if not match:
        raise ValueError(f"invalid SemVer: {version_str!r}")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))



class FrontmatterError(ValueError):
    """Raised when SKILL.md frontmatter cannot be parsed safely."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_frontmatter(path: Path) -> dict[str, Any]:
    """Load complete YAML frontmatter, including multiline descriptions."""

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise FrontmatterError(f"cannot read UTF-8 frontmatter: {exc}") from exc
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise FrontmatterError("missing or unterminated YAML frontmatter")
    try:
        value = yaml.load(match.group("yaml"), Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise FrontmatterError(f"invalid YAML frontmatter: {exc}") from exc
    if not isinstance(value, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return value


def discover_skill_dirs(repo_root: Path = REPO_ROOT) -> dict[str, Path]:
    """Discover top-level Skill directories without a second manifest."""

    skills: dict[str, Path] = {}
    for child in sorted(repo_root.iterdir(), key=lambda item: item.name):
        if child.name.startswith(".") or not child.is_dir() or child.is_symlink():
            continue
        skill_md = child / "SKILL.md"
        if skill_md.is_file() and not skill_md.is_symlink():
            skills[child.name] = child
    return skills


def _non_comment_lines(path: Path) -> set[str]:
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def validate_skill_dir(skill_dir: Path) -> list[str]:
    """Return governance and frontmatter errors for one Skill directory."""

    errors: list[str] = []
    slug = skill_dir.name
    prefix = f"{slug}:"
    skill_md = skill_dir / "SKILL.md"
    try:
        frontmatter = load_frontmatter(skill_md)
    except FrontmatterError as exc:
        return [f"{prefix} {exc}"]

    unexpected = sorted(set(frontmatter) - ALLOWED_FRONTMATTER_KEYS)
    if unexpected:
        errors.append(f"{prefix} unexpected frontmatter keys: {', '.join(unexpected)}")

    name = frontmatter.get("name")
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        errors.append(f"{prefix} name must be a lowercase hyphenated slug")
    elif name != slug:
        errors.append(f"{prefix} frontmatter name {name!r} must match its directory")

    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append(f"{prefix} description must be a non-empty string")

    if frontmatter.get("license") != "Proprietary":
        errors.append(f"{prefix} license must be Proprietary")

    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"{prefix} metadata must be a mapping")
        metadata = {}

    if metadata.get("slug") != slug:
        errors.append(f"{prefix} metadata.slug must match its directory")
    version = metadata.get("version")
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        errors.append(f"{prefix} metadata.version must be a SemVer string")
        version = None
    else:
        try:
            raw_text = skill_md.read_text(encoding="utf-8")
            if not VERSION_QUOTED_PATTERN.search(raw_text):
                errors.append(
                    f"{prefix} metadata.version in SKILL.md must be enclosed in double quotes (e.g. \"{version}\")"
                )
        except Exception:
            pass
    for key in ("displayName", "summary"):
        value = metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{prefix} metadata.{key} must be a non-empty string")
    tags = metadata.get("tags")
    if not isinstance(tags, list) or not tags or not all(
        isinstance(tag, str) and tag.strip() for tag in tags
    ):
        errors.append(f"{prefix} metadata.tags must be a non-empty string list")
    homepage = metadata.get("homepage")
    if not isinstance(homepage, str) or not homepage.startswith("https://github.com/MZqk/"):
        errors.append(f"{prefix} metadata.homepage must be an MZqk GitHub HTTPS URL")

    for filename in REQUIRED_FILES:
        path = skill_dir / filename
        if not path.is_file() or path.is_symlink():
            errors.append(f"{prefix} missing regular governance file {filename}")

    license_files: list[Path] = []
    for name in ("LICENSE.md", "NOTICE.md"):
        path = skill_dir / name
        if path.is_symlink():
            errors.append(f"{prefix} {name} must not be a symbolic link")
        elif path.is_file():
            license_files.append(path)
    if not license_files:
        errors.append(f"{prefix} requires LICENSE.md or NOTICE.md")
    for path in license_files:
        try:
            license_text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeError) as exc:
            errors.append(f"{prefix} cannot read {path.name} as UTF-8: {exc}")
            continue
        if "all rights reserved" not in license_text:
            errors.append(f"{prefix} {path.name} must reserve all rights")
        if path.name == "LICENSE.md" and "copyright 2026 mzqk" not in license_text:
            errors.append(f"{prefix} LICENSE.md must identify Copyright 2026 MZqk")
        if path.name == "NOTICE.md" and "proprietary" not in license_text:
            errors.append(f"{prefix} NOTICE.md must preserve the proprietary boundary")

    changelog = skill_dir / "CHANGELOG.md"
    if version and changelog.is_file():
        changelog_text = changelog.read_text(encoding="utf-8")
        if f"## [{version}]" not in changelog_text:
            errors.append(f"{prefix} CHANGELOG.md must record version {version}")

    releasing = skill_dir / "RELEASING.md"
    if releasing.is_file():
        releasing_text = releasing.read_text(encoding="utf-8")
        if f"{slug}/vX.Y.Z" not in releasing_text and f"skill/{slug}/vX.Y.Z" not in releasing_text:
            errors.append(f"{prefix} RELEASING.md must document namespaced tags")

    requirements_dev = skill_dir / "requirements-dev.txt"
    if requirements_dev.is_file():
        lines = _non_comment_lines(requirements_dev)
        for requirement in ("pytest>=8,<10", "PyYAML>=6,<7"):
            if requirement not in lines:
                errors.append(f"{prefix} requirements-dev.txt must contain {requirement}")
        runtime_requirements = skill_dir / "requirements.txt"
        if runtime_requirements.is_file() and "-r requirements.txt" not in lines:
            errors.append(
                f"{prefix} requirements-dev.txt must include its local requirements.txt"
            )

    return errors


def _marked_section(text: str, marker: str) -> tuple[str | None, list[str]]:
    start = f"<!-- {marker}:start -->"
    end = f"<!-- {marker}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        return None, [f"README.md must contain one {start} and one {end}"]
    start_index = text.index(start) + len(start)
    try:
        end_index = text.index(end, start_index)
    except ValueError:
        return None, [f"README.md marker {end} must follow {start}"]
    return text[start_index:end_index], []


def _set_error(label: str, actual: Iterable[str], expected: Iterable[str]) -> str | None:
    actual_set = set(actual)
    expected_set = set(expected)
    if actual_set == expected_set:
        return None
    missing = sorted(expected_set - actual_set)
    extra = sorted(actual_set - expected_set)
    return f"README.md {label} mismatch; missing={missing}, extra={extra}"


def _duplicate_error(label: str, values: Iterable[str]) -> str | None:
    items = list(values)
    duplicates = sorted({item for item in items if items.count(item) > 1})
    if not duplicates:
        return None
    return f"README.md {label} contains duplicates: {duplicates}"


def validate_readme(repo_root: Path, skill_names: Iterable[str]) -> list[str]:
    """Validate catalog, directory tree, install commands, usage, and local links."""

    expected = set(skill_names)
    readme_path = repo_root / "README.md"
    try:
        text = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [f"README.md cannot be read as UTF-8: {exc}"]

    errors: list[str] = []
    index, marker_errors = _marked_section(text, "skills-index")
    errors.extend(marker_errors)
    if index is not None:
        rows = re.findall(
            r"\|\s*\[`([a-z0-9-]+)`\]\(([a-z0-9-]+)/\)\s*\|", index
        )
        for label, target in rows:
            if label != target:
                errors.append(f"README.md catalog label {label!r} must match {target!r}")
        mismatch = _set_error("catalog", (label for label, _ in rows), expected)
        if mismatch:
            errors.append(mismatch)
        duplicate = _duplicate_error("catalog", (label for label, _ in rows))
        if duplicate:
            errors.append(duplicate)

    tree, marker_errors = _marked_section(text, "skills-tree")
    errors.extend(marker_errors)
    if tree is not None:
        tree_names = re.findall(r"^[├└]──\s+([a-z0-9-]+)/", tree, re.MULTILINE)
        mismatch = _set_error("directory tree", tree_names, expected)
        if mismatch:
            errors.append(mismatch)
        duplicate = _duplicate_error("directory tree", tree_names)
        if duplicate:
            errors.append(duplicate)

    install, marker_errors = _marked_section(text, "skills-install")
    errors.extend(marker_errors)
    if install is not None:
        links = re.findall(
            r'ln -s "\$\(pwd\)/([a-z0-9-]+)" "\$CODEX_SKILLS_DIR/([a-z0-9-]+)"',
            install,
        )
        for source, destination in links:
            if source != destination:
                errors.append(
                    f"README.md install source {source!r} must match destination {destination!r}"
                )
        mismatch = _set_error("install commands", (source for source, _ in links), expected)
        if mismatch:
            errors.append(mismatch)
        duplicate = _duplicate_error("install commands", (source for source, _ in links))
        if duplicate:
            errors.append(duplicate)

    usage, marker_errors = _marked_section(text, "skills-usage")
    errors.extend(marker_errors)
    if usage is not None:
        invocations = re.findall(r"\$([a-z0-9]+(?:-[a-z0-9]+)+)", usage)
        mismatch = _set_error("usage examples", invocations, expected)
        if mismatch:
            errors.append(mismatch)
        duplicate = _duplicate_error("usage examples", invocations)
        if duplicate:
            errors.append(duplicate)

    for raw_target in re.findall(r"\]\(([^)]+)\)", text):
        raw_target = raw_target.strip().removeprefix("<").removesuffix(">")
        parsed = urlsplit(raw_target)
        if raw_target.startswith("#") or (parsed.scheme and parsed.scheme != "file") or parsed.netloc:
            continue
        local_target = unquote(parsed.path)
        pure_target = PurePosixPath(local_target)
        if pure_target.is_absolute() or ".." in pure_target.parts or parsed.scheme == "file":
            errors.append(f"README.md local link escapes the repository: {raw_target}")
            continue
        candidate = (repo_root / local_target).resolve()
        try:
            candidate.relative_to(repo_root.resolve())
        except ValueError:
            errors.append(f"README.md local link escapes the repository: {raw_target}")
            continue
        if local_target and not candidate.exists():
            errors.append(f"README.md link target does not exist: {raw_target}")

    return errors


def validate_manifest(repo_root: Path, skill_names: Iterable[str]) -> list[str]:
    """Validate root skills.manifest.json structure and consistency with SKILL.md files."""
    manifest_path = repo_root / "skills.manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return ["missing regular root manifest skills.manifest.json"]
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"skills.manifest.json is not valid JSON: {exc}"]

    if not isinstance(data, dict):
        return ["skills.manifest.json root must be a JSON object"]

    skills_dict = data.get("skills")
    if not isinstance(skills_dict, dict):
        return ["skills.manifest.json must contain a 'skills' object mapping slugs to configurations"]

    errors: list[str] = []
    expected_slugs = set(skill_names)
    manifest_slugs = set(skills_dict.keys())

    if manifest_slugs != expected_slugs:
        missing = sorted(expected_slugs - manifest_slugs)
        extra = sorted(manifest_slugs - expected_slugs)
        errors.append(f"skills.manifest.json skills mismatch; missing={missing}, extra={extra}")

    for slug in sorted(manifest_slugs & expected_slugs):
        entry = skills_dict[slug]
        prefix = f"skills.manifest.json[{slug}]:"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} value must be an object")
            continue
        path_val = entry.get("path")
        if path_val != slug:
            errors.append(f"{prefix} path must match slug {slug!r}, got {path_val!r}")
        manifest_ver = entry.get("version")
        if not isinstance(manifest_ver, str) or not SEMVER_PATTERN.fullmatch(manifest_ver):
            errors.append(f"{prefix} version must be a SemVer string, got {manifest_ver!r}")
            continue

        skill_md = repo_root / slug / "SKILL.md"
        if skill_md.is_file():
            try:
                fm = load_frontmatter(skill_md)
                skill_ver = (fm.get("metadata") or {}).get("version")
                if manifest_ver != skill_ver:
                    errors.append(
                        f"{prefix} version {manifest_ver!r} does not match {slug}/SKILL.md version {skill_ver!r}"
                    )
            except Exception:
                pass

    return errors


def validate_version_bump(
    repo_root: Path,
    base: str,
    head: str = "HEAD",
) -> list[str]:
    """Validate that any modified Skill has monotonically increased its version and documented it in CHANGELOG."""
    errors: list[str] = []
    diff_proc = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--diff-filter=ACDMRTUXB", base, head],
        cwd=repo_root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if diff_proc.returncode != 0:
        return [f"git diff failed against base {base!r}: {diff_proc.stderr.decode().strip()}"]

    raw_paths = [os.fsdecode(p) for p in diff_proc.stdout.split(b"\0") if p]
    skills = discover_skill_dirs(repo_root)

    changed_slugs: set[str] = set()
    for raw_p in raw_paths:
        parts = PurePosixPath(raw_p.replace("\\", "/")).parts
        if parts and parts[0] in skills:
            changed_slugs.add(parts[0])

    for slug in sorted(changed_slugs):
        prefix = f"{slug}:"
        skill_dir = skills[slug]
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue

        try:
            curr_fm = load_frontmatter(skill_md)
            curr_ver_str = (curr_fm.get("metadata") or {}).get("version")
            if not curr_ver_str:
                continue
            curr_ver = parse_semver(curr_ver_str)
        except Exception as exc:
            errors.append(f"{prefix} cannot parse current version: {exc}")
            continue

        # Fetch old version from git base
        old_proc = subprocess.run(
            ["git", "show", f"{base}:{slug}/SKILL.md"],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if old_proc.returncode == 0:
            try:
                old_text = old_proc.stdout.decode("utf-8")
                old_fm_match = FRONTMATTER_PATTERN.match(old_text)
                if old_fm_match:
                    old_fm = yaml.safe_load(old_fm_match.group("yaml")) or {}
                    old_ver_str = (old_fm.get("metadata") or {}).get("version") or old_fm.get("version")
                    if old_ver_str and SEMVER_PATTERN.fullmatch(old_ver_str):
                        old_ver = parse_semver(old_ver_str)
                        if curr_ver <= old_ver:
                            errors.append(
                                f"{prefix} version was not bumped after modifications; "
                                f"current={curr_ver_str}, base {base}={old_ver_str}"
                            )
            except Exception:
                pass

        # Verify CHANGELOG has entry with content for curr_ver_str
        changelog_path = skill_dir / "CHANGELOG.md"
        if changelog_path.is_file():
            cl_text = changelog_path.read_text(encoding="utf-8")
            pattern = re.compile(
                rf"^##\s+\[{re.escape(curr_ver_str)}\][^\n]*\n(?P<bodycontent>(?:(?!^##\s+\[).|\n)*)",
                re.MULTILINE,
            )
            m = pattern.search(cl_text)
            if not m:
                errors.append(f"{prefix} CHANGELOG.md missing entry for version {curr_ver_str}")
            else:
                body = m.group("bodycontent").strip()
                non_empty_lines = [
                    l for l in body.splitlines() if l.strip() and not l.strip().startswith("#")
                ]
                if not non_empty_lines:
                    errors.append(
                        f"{prefix} CHANGELOG.md section ## [{curr_ver_str}] has no change description items"
                    )

    return errors


def validate_repository(
    repo_root: Path = REPO_ROOT,
    selected_skills: Iterable[str] | None = None,
    diff_base: str | None = None,
    diff_head: str = "HEAD",
) -> list[str]:
    """Validate selected Skills, or the entire repository when selection is omitted."""

    repo_root = repo_root.resolve()
    skills = discover_skill_dirs(repo_root)
    if not skills:
        return ["no top-level Skill directories were discovered"]

    errors: list[str] = []
    if selected_skills:
        selected = list(dict.fromkeys(selected_skills))
        unknown = sorted(set(selected) - set(skills))
        if unknown:
            errors.append(f"unknown Skill selection: {', '.join(unknown)}")
        targets = [skills[name] for name in selected if name in skills]
    else:
        targets = list(skills.values())
        errors.extend(validate_manifest(repo_root, skills))
        errors.extend(validate_readme(repo_root, skills))

    for skill_dir in targets:
        errors.extend(validate_skill_dir(skill_dir))

    if diff_base:
        errors.extend(validate_version_bump(repo_root, diff_base, diff_head))

    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="repository root (defaults to the parent of this script)",
    )
    parser.add_argument(
        "--skill",
        action="append",
        dest="skills",
        help="validate one Skill; repeat for more, omit to validate the whole repository",
    )
    parser.add_argument(
        "--diff-base",
        help="verify modified skills bumped version and updated CHANGELOG compared to git base",
    )
    parser.add_argument(
        "--diff-head",
        default="HEAD",
        help="git head revision for diff comparison (default: HEAD)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_repository(
        args.repo_root,
        args.skills,
        diff_base=args.diff_base,
        diff_head=args.diff_head,
    )
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if args.skills:
        labels = ", ".join(dict.fromkeys(args.skills))
    else:
        labels = ", ".join(discover_skill_dirs(args.repo_root))
    print(f"validated: {labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
