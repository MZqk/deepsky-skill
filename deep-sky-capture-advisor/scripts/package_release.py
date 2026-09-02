#!/usr/bin/env python3
"""Build a deterministic release ZIP for the authorized SkillHub snapshot.

The archive is assembled from an explicit runtime allowlist.  This command does
not upload, publish, or sign the resulting artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from knowledge_common import BundleIntegrityError, ValidatedBundle, load_validated_bundle


SKILL_ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# This builder is deliberately release-specific.  A later version must receive
# a new authorization lock instead of inheriting permission from this one.
LOCKED_RELEASE = {
    "slug": "deep-sky-capture-advisor",
    "version": "1.0.1",
    "displayName": "深空摄影知识顾问",
    "license": "Proprietary",
    "summary": "面向 SkillHub 公开分发的中文深空摄影知识顾问非权威测试版，基于内置可追溯快照回答规划、拍摄、后期与排障问题。",
    "tags": ["astronomy", "astrophotography", "deep-sky", "siril", "chinese"],
    "homepage": "https://github.com/MZqk/deepsky-skill",
    "source_commit": "d4094fb5e7811f0cea072344f3d1dfae08d3a2b5",
    "catalog_sha256": "5ec7724359b9ec9062b9fac42f87e136dec0238677eec5ed23b5f341b426c012",
    "knowledge_sha256": "419443bbb2aa84a1766a5e9e834e975dd60be2a324a14a7699186bf9b98d3ddb",
    "authority": "nonauthoritative",
    "non_authoritative_disclosure": "非权威参考：内置依据尚未完成人工签署、已过期或超出核验范围。",
    "distribution_target": "SkillHub public beta",
    "authorized_on": "2026-08-30",
    "authorization_basis": "explicit-user-instruction",
    "future_changes_automatically_authorized": False,
}

STATIC_RUNTIME_FILES = (
    "SKILL.md",
    "NOTICE.md",
    "release-authorization.json",
    "agents/openai.yaml",
    "scripts/knowledge_common.py",
    "scripts/query_knowledge.py",
    "references/catalog.json",
    "references/manifest.json",
)


class ReleasePackageError(RuntimeError):
    """Raised when the requested release does not satisfy its lock."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReleasePackageError(f"Missing {label}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReleasePackageError(f"Invalid {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleasePackageError(f"{label} must be a JSON object: {path}")
    return value


def _plain_file(skill_root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ReleasePackageError(f"Release path is not package-relative: {relative}")

    candidate = skill_root.joinpath(*pure.parts)
    cursor = skill_root
    for part in pure.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ReleasePackageError(f"Release allowlist cannot contain symlinks: {relative}")
    if not candidate.is_file():
        raise ReleasePackageError(f"Missing release runtime file: {relative}")
    try:
        candidate.resolve().relative_to(skill_root.resolve())
    except ValueError as exc:
        raise ReleasePackageError(f"Release file escapes the Skill root: {relative}") from exc
    return candidate


def _knowledge_files(skill_root: Path) -> list[tuple[str, Path]]:
    knowledge_root = skill_root / "references" / "knowledge"
    if knowledge_root.is_symlink() or not knowledge_root.is_dir():
        raise ReleasePackageError(f"Missing or unsafe knowledge root: {knowledge_root}")

    files: list[tuple[str, Path]] = []
    for path in sorted(knowledge_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ReleasePackageError(f"Bundled knowledge cannot contain symlinks: {path}")
        if not path.is_file():
            continue
        relative_to_knowledge = path.relative_to(knowledge_root)
        marker_parts = {part.lower() for part in relative_to_knowledge.parts}
        if any(
            part.startswith(".")
            or "transaction" in part
            or part.endswith((".tmp", ".partial", ".backup", ".staging"))
            for part in marker_parts
        ):
            raise ReleasePackageError(f"Knowledge tree contains a transaction marker: {path}")
        if path.suffix.lower() != ".md":
            raise ReleasePackageError(f"Knowledge tree contains a non-Markdown file: {path}")
        relative = path.relative_to(skill_root).as_posix()
        files.append((relative, _plain_file(skill_root, relative)))
    if not files:
        raise ReleasePackageError("No bundled Markdown knowledge was found")
    return files


def collect_release_files(skill_root: Path) -> list[tuple[str, Path]]:
    """Return the exact, sorted runtime allowlist for a release archive."""

    skill_root = skill_root.expanduser().resolve()
    files = [(relative, _plain_file(skill_root, relative)) for relative in STATIC_RUNTIME_FILES]
    files.extend(_knowledge_files(skill_root))
    files.sort(key=lambda item: item[0])
    names = [relative for relative, _ in files]
    if len(names) != len(set(names)):
        raise ReleasePackageError("Release allowlist contains duplicate archive paths")
    return files


def _knowledge_tree_sha(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    prefix = "references/knowledge/"
    knowledge = [(name[len(prefix) :], path) for name, path in files if name.startswith(prefix)]
    for relative, path in sorted(knowledge):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_tree_sha(files: list[tuple[str, Path]]) -> str:
    """Hash exact package-relative paths and bytes without claiming platform identity."""

    digest = hashlib.sha256()
    for relative, path in files:
        data = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _skillhub_content_hash(files: list[tuple[str, Path]]) -> str:
    """Apply SkillHub's verified path:sha256 newline aggregation algorithm."""

    digest = hashlib.sha256()
    for relative, path in files:
        record = f"{relative}:{_sha256(path.read_bytes())}\n"
        digest.update(record.encode("utf-8"))
    return digest.hexdigest()


def _yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        if value[0] == '"':
            try:
                decoded = json.loads(value)
                if isinstance(decoded, str):
                    return decoded
            except json.JSONDecodeError:
                pass
        return value[1:-1]
    return value


def _yaml_value(value: str) -> Any:
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_yaml_scalar(item) for item in inner.split(",")]
    return _yaml_scalar(value)


def _skill_metadata(skill_root: Path) -> dict[str, Any]:
    text = _plain_file(skill_root, "SKILL.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReleasePackageError("SKILL.md is missing YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise ReleasePackageError("SKILL.md frontmatter is not terminated") from exc

    frontmatter = lines[1:end]
    top_level: dict[str, Any] = {}
    metadata: dict[str, Any] = {}
    metadata_start: int | None = None
    metadata_indent: int | None = None

    for index, line in enumerate(frontmatter):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent != 0 or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        if key == "metadata":
            if raw.strip():
                raise ReleasePackageError("SKILL.md metadata must be a YAML mapping")
            metadata_start = index + 1
            metadata_indent = None
            top_level[key] = metadata
            continue
        if raw.strip():
            top_level[key] = _yaml_value(raw)

    if metadata_start is None:
        raise ReleasePackageError("SKILL.md is missing the metadata mapping")
    active_list: str | None = None
    for line in frontmatter[metadata_start:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        if metadata_indent is None:
            metadata_indent = indent
        if indent < metadata_indent:
            break
        stripped = line.strip()
        if stripped.startswith("-"):
            if active_list is None or indent <= metadata_indent:
                raise ReleasePackageError("SKILL.md metadata contains an unbound list item")
            metadata[active_list].append(_yaml_scalar(stripped[1:]))
            continue
        if indent != metadata_indent or ":" not in stripped:
            raise ReleasePackageError("SKILL.md metadata must contain flat scalar or list fields")
        key, raw = stripped.split(":", 1)
        key = key.strip()
        if key in metadata:
            raise ReleasePackageError(f"SKILL.md metadata repeats {key}")
        if raw.strip():
            metadata[key] = _yaml_value(raw)
            active_list = None
        else:
            metadata[key] = []
            active_list = key

    release_metadata_fields = {
        "slug",
        "version",
        "displayName",
        "summary",
        "tags",
        "homepage",
    }
    misplaced = sorted(release_metadata_fields & top_level.keys())
    if misplaced:
        raise ReleasePackageError(
            "SkillHub release metadata must be nested under frontmatter.metadata: "
            + ", ".join(misplaced)
        )
    if "license" in metadata:
        raise ReleasePackageError("SKILL.md license must remain a top-level frontmatter field")
    if top_level.get("name") != LOCKED_RELEASE["slug"]:
        raise ReleasePackageError("SKILL.md name does not match the locked slug")

    values = {field: metadata.get(field) for field in release_metadata_fields}
    values["license"] = top_level.get("license")
    return values


def _validate_authorization(skill_root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    authorization = _load_json(skill_root / "release-authorization.json", "release authorization")
    for field, expected in LOCKED_RELEASE.items():
        if authorization.get(field) != expected:
            raise ReleasePackageError(
                f"Authorization lock mismatch for {field}: expected {expected!r}, "
                f"got {authorization.get(field)!r}"
            )
    if authorization.get("skill_name") != LOCKED_RELEASE["slug"]:
        raise ReleasePackageError("Authorization lock targets a different Skill")
    if (
        authorization.get("authorization_scope")
        != "skillhub-publication:deep-sky-capture-advisor@1.0.1"
    ):
        raise ReleasePackageError("Authorization scope does not match the locked SkillHub release")
    if authorization.get("public_publication_authorized") is not True:
        raise ReleasePackageError("Public publication is not authorized by this lock")
    if authorization.get("skillhub_publication_authorized") is not True:
        raise ReleasePackageError("SkillHub publication is not authorized by this lock")
    if authorization.get("other_publication_channels_authorized") is not False:
        raise ReleasePackageError("Authorization unexpectedly covers another publication channel")
    if {
        "skillhub_content_hash",
        "official_skillhub_content_hash",
        "content_hash",
    } & authorization.keys():
        raise ReleasePackageError(
            "Authorization must not contain a self-referential release content hash"
        )

    metadata = _skill_metadata(skill_root)
    for field in ("slug", "version", "displayName", "license", "summary", "tags", "homepage"):
        if metadata.get(field) != LOCKED_RELEASE[field]:
            raise ReleasePackageError(
                f"SKILL.md metadata mismatch for {field}: expected {LOCKED_RELEASE[field]!r}, "
                f"got {metadata.get(field)!r}"
            )

    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    observed = {
        "source_commit": source.get("git_commit"),
        "catalog_sha256": bundle.get("catalog_sha256"),
        "knowledge_sha256": bundle.get("knowledge_sha256"),
    }
    for field in ("source_commit", "catalog_sha256", "knowledge_sha256"):
        if observed[field] != LOCKED_RELEASE[field]:
            raise ReleasePackageError(
                f"Manifest does not match locked {field}: expected {LOCKED_RELEASE[field]}, "
                f"got {observed[field]}"
            )
    if source.get("working_tree_dirty") is not False:
        raise ReleasePackageError("A dirty source snapshot cannot satisfy this release lock")
    return authorization


def _validate_bundle(
    skill_root: Path,
    files: list[tuple[str, Path]],
    manifest: dict[str, Any],
) -> None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    catalog_path = _plain_file(skill_root, "references/catalog.json")
    catalog = _load_json(catalog_path, "catalog")
    entries = catalog.get("entries")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise ReleasePackageError("Catalog entries are missing or invalid")

    catalog_sha = _sha256(catalog_path.read_bytes())
    if catalog_sha != LOCKED_RELEASE["catalog_sha256"]:
        raise ReleasePackageError(
            f"Catalog SHA-256 mismatch: expected {LOCKED_RELEASE['catalog_sha256']}, got {catalog_sha}"
        )
    knowledge_sha = _knowledge_tree_sha(files)
    if knowledge_sha != LOCKED_RELEASE["knowledge_sha256"]:
        raise ReleasePackageError(
            f"Knowledge SHA-256 mismatch: expected {LOCKED_RELEASE['knowledge_sha256']}, "
            f"got {knowledge_sha}"
        )

    knowledge_count = sum(name.startswith("references/knowledge/") for name, _ in files)
    if knowledge_count != bundle.get("markdown_file_count"):
        raise ReleasePackageError(
            f"Knowledge Markdown count mismatch: expected {bundle.get('markdown_file_count')}, "
            f"got {knowledge_count}"
        )
    if len(entries) != bundle.get("content_page_count"):
        raise ReleasePackageError(
            f"Catalog entry count mismatch: expected {bundle.get('content_page_count')}, "
            f"got {len(entries)}"
        )

    for entry in entries:
        relative = str(entry.get("path") or "")
        page = _plain_file(skill_root, f"references/knowledge/{relative}")
        page_sha = _sha256(page.read_bytes())
        if page_sha != entry.get("sha256"):
            raise ReleasePackageError(f"Catalog page SHA-256 mismatch: {relative}")


def _validate_fixed_bundle_facts(validated: ValidatedBundle) -> None:
    """Bind the validated runtime closure to the one authorized snapshot."""

    source = validated.manifest["source"]
    expected_facts = {
        "catalog_sha256": LOCKED_RELEASE["catalog_sha256"],
        "knowledge_sha256": LOCKED_RELEASE["knowledge_sha256"],
    }
    if source.get("git_commit") != LOCKED_RELEASE["source_commit"]:
        raise ReleasePackageError(
            "Validated bundle source commit does not match the authorization lock"
        )
    for field, expected in expected_facts.items():
        if validated.facts.get(field) != expected:
            raise ReleasePackageError(
                f"Validated bundle fact {field} does not match the authorization lock: "
                f"expected {expected}, got {validated.facts.get(field)}"
            )


def _zip_info(relative: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (0o100644 & 0xFFFF) << 16
    info.internal_attr = 0
    info.extra = b""
    info.comment = b""
    return info


def build_release(skill_root: Path, output: Path) -> dict[str, Any]:
    """Validate the locked snapshot and atomically create its local ZIP."""

    skill_root = skill_root.expanduser().resolve()
    output = output.expanduser().resolve()
    if output.suffix.lower() != ".zip":
        raise ReleasePackageError("Release output must use a .zip extension")
    if output.exists():
        raise ReleasePackageError(f"Refusing to overwrite existing output: {output}")

    try:
        validated = load_validated_bundle(skill_root / "references")
    except BundleIntegrityError as exc:
        raise ReleasePackageError(f"Runtime bundle integrity validation failed: {exc}") from exc
    _validate_fixed_bundle_facts(validated)
    manifest = validated.manifest
    authorization = _validate_authorization(skill_root, manifest)
    files = collect_release_files(skill_root)
    _validate_bundle(skill_root, files, manifest)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        os.close(descriptor)
        with zipfile.ZipFile(temporary_name, mode="w", compression=zipfile.ZIP_STORED) as archive:
            archive.comment = b""
            for relative, path in files:
                archive.writestr(_zip_info(relative), path.read_bytes())
        os.replace(temporary_name, output)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)

    archive_bytes = output.read_bytes()
    skill_document = _plain_file(skill_root, "SKILL.md").read_bytes()
    return {
        "skill_name": authorization["skill_name"],
        "version": authorization["version"],
        "displayName": authorization["displayName"],
        "license": authorization["license"],
        "summary": authorization["summary"],
        "tags": authorization["tags"],
        "homepage": authorization["homepage"],
        "archive": str(output),
        "archive_sha256": _sha256(archive_bytes),
        "runtime_tree_sha256": _runtime_tree_sha(files),
        "skill_document_sha256": _sha256(skill_document),
        "skillhub_content_hash": _skillhub_content_hash(files),
        "skillhub_content_hash_algorithm": "sha256(sorted path:sha256\\n records)",
        "file_count": len(files),
        "files": [relative for relative, _ in files],
        "source_commit": LOCKED_RELEASE["source_commit"],
        "catalog_sha256": LOCKED_RELEASE["catalog_sha256"],
        "knowledge_sha256": LOCKED_RELEASE["knowledge_sha256"],
        "authority": LOCKED_RELEASE["authority"],
        "non_authoritative_disclosure": LOCKED_RELEASE["non_authoritative_disclosure"],
        "distribution_target": LOCKED_RELEASE["distribution_target"],
        "authorized_on": LOCKED_RELEASE["authorized_on"],
        "authorization_basis": LOCKED_RELEASE["authorization_basis"],
        "future_changes_automatically_authorized": False,
        "skillhub_publication_authorized": True,
        "external_publication_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skill-root",
        type=Path,
        default=SKILL_ROOT,
        help="Skill directory to validate and package (defaults to this Skill)",
    )
    parser.add_argument("--output", type=Path, required=True, help="New local .zip path")
    args = parser.parse_args()
    try:
        result = build_release(args.skill_root, args.output)
    except (OSError, ReleasePackageError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
