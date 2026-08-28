#!/usr/bin/env python3
"""Validate independent Skill governance and the repository README catalog."""

from __future__ import annotations

import argparse
import re
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
REQUIRED_FILES = ("CHANGELOG.md", "RELEASING.md", "requirements-dev.txt")


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
        if f"{slug}/vX.Y.Z" not in releasing_text:
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


def validate_repository(
    repo_root: Path = REPO_ROOT,
    selected_skills: Iterable[str] | None = None,
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
        errors.extend(validate_readme(repo_root, skills))

    for skill_dir in targets:
        errors.extend(validate_skill_dir(skill_dir))
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = validate_repository(args.repo_root, args.skills)
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
