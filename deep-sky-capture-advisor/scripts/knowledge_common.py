"""Shared integrity and authority rules for the bundled knowledge snapshot."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


MANIFEST_SCHEMA_VERSION = 2
# Catalog v1 bytes are a published, fixed artifact. Manifest v2 adds the stronger closure around it.
CATALOG_SCHEMA_VERSION = 1
TRANSACTION_SCHEMA_VERSION = 1
TRANSACTION_JOURNAL_NAME = ".bundle-transaction.json"

FORMAL_CATEGORIES = (
    "00-知识库规范",
    "01-新人入门",
    "02-器材百科",
    "03-拍摄SOP",
    "04-后期处理",
    "05-目标图鉴",
    "06-选址与环境",
    "07-软件工具",
    "08-FAQ",
    "09-踩坑与复盘",
)

REQUIRED_RUNTIME_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "scripts/query_knowledge.py",
    "scripts/knowledge_common.py",
)
OPTIONAL_RUNTIME_FILES = (
    "NOTICE.md",
    "release-authorization.json",
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_HUMAN_ACTOR_RE = re.compile(r"human:[A-Za-z0-9][A-Za-z0-9._@+\-]{0,127}\Z")
_VAGUE_SCOPES = {
    "*",
    "all",
    "all content",
    "everything",
    "n/a",
    "na",
    "none",
    "todo",
    "unknown",
    "x",
    "全部",
    "全部已验证",
    "全部正确",
    "全文",
    "全文已验证",
    "全文正确",
}

_CATALOG_TOP_KEYS = {"schema_version", "entries", "unbundled_internal_sources"}
_CATALOG_ENTRY_KEYS = {
    "applies_to",
    "audience",
    "category",
    "description",
    "difficulty",
    "links",
    "path",
    "review",
    "sha256",
    "sources",
    "stale_after",
    "status",
    "tags",
    "title",
    "type",
    "updated",
    "verified",
}
_UNBUNDLED_SOURCE_KEYS = {"path", "reason", "referrers", "source_ids", "titles"}
_MANIFEST_TOP_KEYS = {
    "authority_rule",
    "bundle",
    "distribution_notice",
    "excluded",
    "included",
    "packaged_at",
    "runtime",
    "runtime_files",
    "schema_version",
    "skill_name",
    "source",
}
_MANIFEST_SOURCE_KEYS = {
    "git_commit",
    "git_commit_date",
    "name",
    "retrieval_corpus_sha256",
    "working_tree_dirty",
}
_MANIFEST_BUNDLE_KEYS = {
    "catalog_sha256",
    "content_page_count",
    "formal_page_sha256",
    "human_verified_page_count",
    "knowledge_sha256",
    "markdown_file_count",
    "navigation_page_count",
    "needs_human_review_count",
    "unbundled_internal_source_count",
}
_MANIFEST_RUNTIME_KEYS = {
    "query_network_access",
    "self_contained",
    "source_repository_required",
    "writes_to_bundle",
}
_VALID_STATUSES = {"draft", "stable", "deprecated"}


class BundleIntegrityError(ValueError):
    """Raised when bundled data does not satisfy the supported integrity contract."""

    def __init__(self, errors: str | Iterable[str]) -> None:
        normalized = (errors,) if isinstance(errors, str) else tuple(str(item) for item in errors)
        if not normalized:
            normalized = ("bundle integrity validation failed",)
        self.errors = normalized
        super().__init__("; ".join(normalized))


@dataclass(frozen=True)
class ValidatedBundle:
    """A fully validated point-in-time view of the three bundled artifacts."""

    manifest: dict[str, Any]
    catalog: dict[str, Any]
    facts: dict[str, Any]
    fingerprint: str

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.catalog["entries"]


def _fail(location: str, detail: str) -> BundleIntegrityError:
    return BundleIntegrityError(f"{location}: {detail}")


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise _fail(location, "; ".join(details))


def _require_string(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _fail(location, "must be a non-empty string")
    return value


def _require_string_list(value: Any, location: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        qualifier = "a list" if allow_empty else "a non-empty list"
        raise _fail(location, f"must be {qualifier} of strings")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise _fail(location, "must contain only non-empty strings")
    return value


def _parse_iso_date(value: Any, location: str) -> dt.date:
    if not isinstance(value, str):
        raise _fail(location, "must be an ISO-8601 date string")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise _fail(location, "must be an ISO-8601 date string") from exc


def _parse_aware_datetime(value: Any, location: str) -> dt.datetime:
    if not isinstance(value, str):
        raise _fail(location, "must be an ISO-8601 datetime with a timezone")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _fail(location, "must be an ISO-8601 datetime with a timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _fail(location, "must include a timezone")
    return parsed


def validate_human_verification(value: Any, *, location: str = "verified") -> dict[str, str] | None:
    """Validate a declared human signature; ``None`` means explicitly unsigned.

    This validates the record's syntax and auditability. It cannot authenticate the named person.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise _fail(location, "must be null or an object with by/at/scope")
    _require_exact_keys(value, {"by", "at", "scope"}, location)

    actor = _require_string(value["by"], f"{location}.by")
    if not _HUMAN_ACTOR_RE.fullmatch(actor):
        raise _fail(f"{location}.by", "must match human:<id>")

    timestamp = _require_string(value["at"], f"{location}.at")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", timestamp):
        _parse_iso_date(timestamp, f"{location}.at")
    else:
        _parse_aware_datetime(timestamp, f"{location}.at")

    scope = _require_string(value["scope"], f"{location}.scope").strip()
    collapsed = " ".join(scope.split())
    if not 3 <= len(collapsed) <= 500:
        raise _fail(f"{location}.scope", "must contain 3-500 characters")
    if not any(character.isalnum() for character in collapsed):
        raise _fail(f"{location}.scope", "must identify an auditable claim range")
    if collapsed.casefold() in _VAGUE_SCOPES:
        raise _fail(f"{location}.scope", "must not be a blanket or placeholder signature")
    return {"by": actor, "at": timestamp, "scope": scope}


def is_valid_human_verification(value: Any) -> bool:
    """Return whether a human-verification declaration is structurally valid."""

    try:
        return validate_human_verification(value) is not None
    except BundleIntegrityError:
        return False


def strict_json_loads(data: bytes | str, *, location: str = "JSON") -> Any:
    """Load standards-compliant JSON while rejecting duplicate object keys."""

    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _fail(location, "is not valid UTF-8") from exc
    elif isinstance(data, str):
        text = data
    else:
        raise _fail(location, "must be bytes or text")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _fail(location, f"duplicate object key {key!r}")
            result[key] = value
        return result

    def reject_constant(constant: str) -> None:
        raise _fail(location, f"non-finite number {constant!r} is not valid JSON")

    try:
        return json.loads(text, object_pairs_hook=object_pairs, parse_constant=reject_constant)
    except BundleIntegrityError:
        raise
    except json.JSONDecodeError as exc:
        raise _fail(location, f"invalid JSON: {exc}") from exc


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _require_directory_without_symlink(path: Path, location: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise _fail(location, "directory is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise _fail(location, "symbolic links are not allowed")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _fail(location, "must be a directory")


def read_regular_file(path: Path, *, root: Path | None = None, location: str | None = None) -> bytes:
    """Read one regular file after rejecting symlinks in its in-scope path."""

    label = location or str(path)
    if root is not None:
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise _fail(label, "path is outside its declared root") from exc
        _require_directory_without_symlink(root, str(root))
        current = root
        for part in relative.parts:
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError as exc:
                raise _fail(label, "file or parent directory is missing") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise _fail(label, "symbolic links are not allowed")
            if current != path and not stat.S_ISDIR(metadata.st_mode):
                raise _fail(label, "a parent component is not a directory")

    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise _fail(label, "file is missing") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise _fail(label, "symbolic links are not allowed")
    if not stat.S_ISREG(metadata.st_mode):
        raise _fail(label, "must be a regular file")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise _fail(label, f"cannot be read: {exc}") from exc


def load_json_strict(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    value = strict_json_loads(read_regular_file(path, root=root), location=str(path))
    if not isinstance(value, dict):
        raise _fail(str(path), "top-level value must be an object")
    return value


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_sha256(files: Mapping[str, bytes] | Iterable[tuple[str, bytes]]) -> str:
    """Hash sorted POSIX paths and raw bytes using the bundle tree encoding."""

    items = files.items() if isinstance(files, Mapping) else files
    digest = hashlib.sha256()
    for relative, content in sorted(items):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest()


def _catalog_path(value: Any, location: str) -> str:
    path = _require_string(value, location)
    if "\\" in path or path.startswith("/"):
        raise _fail(location, "must be a relative POSIX path")
    pure = PurePosixPath(path)
    if len(pure.parts) != 2 or pure.parts[0] not in FORMAL_CATEGORIES:
        raise _fail(location, "must be a direct Markdown child of a supported category")
    if pure.name == "index.md" or pure.suffix.lower() != ".md" or any(part in {"", ".", ".."} for part in pure.parts):
        raise _fail(location, "must name one formal non-index Markdown page")
    return pure.as_posix()


def _validate_catalog(catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str]]:
    _require_exact_keys(catalog, _CATALOG_TOP_KEYS, "catalog")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise _fail("catalog.schema_version", f"only integer {CATALOG_SCHEMA_VERSION} is supported")
    entries = catalog["entries"]
    if not isinstance(entries, list) or not entries:
        raise _fail("catalog.entries", "must be a non-empty list")

    paths: set[str] = set()
    titles: set[str] = set()
    expected_unbundled: set[str] = set()
    for index, entry in enumerate(entries):
        location = f"catalog.entries[{index}]"
        if not isinstance(entry, dict):
            raise _fail(location, "must be an object")
        _require_exact_keys(entry, _CATALOG_ENTRY_KEYS, location)
        relative = _catalog_path(entry["path"], f"{location}.path")
        if relative in paths:
            raise _fail(f"{location}.path", "duplicates another catalog path")
        paths.add(relative)
        title = _require_string(entry["title"], f"{location}.title")
        if title in titles:
            raise _fail(f"{location}.title", "duplicates another catalog title")
        titles.add(title)
        for field in ("description", "type", "category"):
            _require_string(entry[field], f"{location}.{field}")
        if entry["category"] != PurePosixPath(relative).parent.name:
            raise _fail(f"{location}.category", "does not match the page path")
        if entry["status"] not in _VALID_STATUSES:
            raise _fail(f"{location}.status", f"must be one of {sorted(_VALID_STATUSES)}")
        _parse_iso_date(entry["stale_after"], f"{location}.stale_after")
        if entry["updated"] is not None:
            _parse_iso_date(entry["updated"], f"{location}.updated")
        _require_string_list(entry["tags"], f"{location}.tags", allow_empty=True)

        review = entry["review"]
        if not isinstance(review, dict):
            raise _fail(f"{location}.review", "must be an object")
        for field in ("owner", "state"):
            _require_string(review.get(field), f"{location}.review.{field}")

        applies_to = entry["applies_to"]
        if not isinstance(applies_to, dict):
            raise _fail(f"{location}.applies_to", "must be an object")
        for field in ("系统", "条件", "不适用"):
            _require_string_list(applies_to.get(field), f"{location}.applies_to.{field}")

        sources = entry["sources"]
        if not isinstance(sources, list) or not sources:
            raise _fail(f"{location}.sources", "must be a non-empty list")
        source_ids: set[str] = set()
        for source_index, source in enumerate(sources):
            source_location = f"{location}.sources[{source_index}]"
            if not isinstance(source, dict):
                raise _fail(source_location, "must be an object")
            source_id = _require_string(source.get("id"), f"{source_location}.id")
            resource = _require_string(source.get("resource"), f"{source_location}.resource")
            if source_id in source_ids:
                raise _fail(f"{source_location}.id", "duplicates another source id on this page")
            source_ids.add(source_id)
            if resource.startswith("/raw/"):
                expected_unbundled.add(resource.lstrip("/"))

        links = _require_string_list(entry["links"], f"{location}.links", allow_empty=True)
        if len(links) != len(set(links)):
            raise _fail(f"{location}.links", "must not contain duplicates")
        for link in links:
            if link.startswith("raw/"):
                expected_unbundled.add(link)
        verification = validate_human_verification(entry["verified"], location=f"{location}.verified")
        if verification is not None and entry["verified"] != verification:
            raise _fail(f"{location}.verified", "must not contain surrounding whitespace")
        if not isinstance(entry["sha256"], str) or not _SHA256_RE.fullmatch(entry["sha256"]):
            raise _fail(f"{location}.sha256", "must be a lowercase SHA-256 digest")

    if [entry["path"] for entry in entries] != sorted(paths):
        raise _fail("catalog.entries", "must be sorted by path")

    unbundled = catalog["unbundled_internal_sources"]
    if not isinstance(unbundled, list):
        raise _fail("catalog.unbundled_internal_sources", "must be a list")
    seen_unbundled: set[str] = set()
    for index, record in enumerate(unbundled):
        location = f"catalog.unbundled_internal_sources[{index}]"
        if not isinstance(record, dict):
            raise _fail(location, "must be an object")
        _require_exact_keys(record, _UNBUNDLED_SOURCE_KEYS, location)
        path = _require_string(record["path"], f"{location}.path")
        if not path.startswith("raw/") or "\\" in path or ".." in PurePosixPath(path).parts:
            raise _fail(f"{location}.path", "must stay below raw/")
        if path in seen_unbundled:
            raise _fail(f"{location}.path", "duplicates another unbundled source")
        seen_unbundled.add(path)
        _require_string(record["reason"], f"{location}.reason")
        referrers = _require_string_list(record["referrers"], f"{location}.referrers")
        if not set(referrers) <= paths:
            raise _fail(f"{location}.referrers", "contains a page outside the catalog")
        _require_string_list(record["source_ids"], f"{location}.source_ids", allow_empty=True)
        _require_string_list(record["titles"], f"{location}.titles", allow_empty=True)
    if seen_unbundled != expected_unbundled:
        raise _fail("catalog.unbundled_internal_sources", "does not exactly cover excluded raw references")
    return entries, paths


def _collect_knowledge_files(knowledge_root: Path) -> dict[str, bytes]:
    _require_directory_without_symlink(knowledge_root, str(knowledge_root))
    files: dict[str, bytes] = {}
    pending = [knowledge_root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise _fail(str(directory), f"cannot be scanned: {exc}") from exc
        for child in children:
            path = Path(child.path)
            relative = path.relative_to(knowledge_root).as_posix()
            if child.is_symlink():
                raise _fail(relative, "symbolic links are not allowed in bundled knowledge")
            if child.is_dir(follow_symlinks=False):
                pending.append(path)
                continue
            if not child.is_file(follow_symlinks=False):
                raise _fail(relative, "only directories and regular Markdown files are allowed")
            if path.suffix.lower() != ".md":
                raise _fail(relative, "non-Markdown files are not allowed in bundled knowledge")
            files[relative] = read_regular_file(path, root=knowledge_root, location=relative)
    return files


def expected_runtime_file_hashes(skill_root: Path) -> dict[str, str]:
    """Return the exact supported runtime-file closure and its SHA-256 map."""

    expected = list(REQUIRED_RUNTIME_FILES)
    for relative in OPTIONAL_RUNTIME_FILES:
        if _lexists(skill_root / relative):
            expected.append(relative)
    result: dict[str, str] = {}
    for relative in expected:
        content = read_regular_file(skill_root / relative, root=skill_root, location=f"runtime file {relative}")
        result[relative] = sha256_bytes(content)
    return dict(sorted(result.items()))


def compute_bundle_facts(
    catalog: dict[str, Any],
    catalog_bytes: bytes,
    knowledge_root: Path,
    *,
    integrity_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Validate catalog/page structure and derive every manifest bundle fact."""

    entries, catalog_paths = _validate_catalog(catalog)
    knowledge_files = _collect_knowledge_files(knowledge_root)
    expected_paths = catalog_paths | {"index.md"}
    if set(knowledge_files) != expected_paths:
        missing = sorted(expected_paths - set(knowledge_files))
        extra = sorted(set(knowledge_files) - expected_paths)
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise _fail("references/knowledge", "; ".join(details))

    entry_by_path = {entry["path"]: entry for entry in entries}
    for relative in sorted(catalog_paths):
        actual = sha256_bytes(knowledge_files[relative])
        if actual != entry_by_path[relative]["sha256"]:
            message = f"{relative}: page SHA-256 does not match catalog"
            if integrity_errors is None:
                raise BundleIntegrityError(message)
            integrity_errors.append(message)

    return {
        "catalog_sha256": sha256_bytes(catalog_bytes),
        "content_page_count": len(entries),
        "formal_page_sha256": tree_sha256(
            (relative, knowledge_files[relative]) for relative in catalog_paths
        ),
        "human_verified_page_count": sum(
            is_valid_human_verification(entry["verified"]) for entry in entries
        ),
        "knowledge_sha256": tree_sha256(knowledge_files),
        "markdown_file_count": len(knowledge_files),
        "navigation_page_count": 1,
        "needs_human_review_count": sum(
            entry["review"]["state"] == "needs-human-review" for entry in entries
        ),
        "unbundled_internal_source_count": len(catalog["unbundled_internal_sources"]),
    }


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    _require_exact_keys(manifest, _MANIFEST_TOP_KEYS, "manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise _fail("manifest.schema_version", f"only integer {MANIFEST_SCHEMA_VERSION} is supported")
    if manifest["skill_name"] != "deep-sky-capture-advisor":
        raise _fail("manifest.skill_name", "must identify deep-sky-capture-advisor")
    _parse_aware_datetime(manifest["packaged_at"], "manifest.packaged_at")
    _require_string(manifest["distribution_notice"], "manifest.distribution_notice")
    _require_string(manifest["authority_rule"], "manifest.authority_rule")
    _require_string_list(manifest["included"], "manifest.included")
    _require_string_list(manifest["excluded"], "manifest.excluded")

    source = manifest["source"]
    if not isinstance(source, dict):
        raise _fail("manifest.source", "must be an object")
    _require_exact_keys(source, _MANIFEST_SOURCE_KEYS, "manifest.source")
    _require_string(source["name"], "manifest.source.name")
    if not isinstance(source["git_commit"], str) or not _GIT_COMMIT_RE.fullmatch(source["git_commit"]):
        raise _fail("manifest.source.git_commit", "must be a lowercase Git commit id")
    _parse_aware_datetime(source["git_commit_date"], "manifest.source.git_commit_date")
    if source["working_tree_dirty"] is not False:
        raise _fail("manifest.source.working_tree_dirty", "must be false for a commit-blob build")
    if not isinstance(source["retrieval_corpus_sha256"], str) or not _SHA256_RE.fullmatch(
        source["retrieval_corpus_sha256"]
    ):
        raise _fail("manifest.source.retrieval_corpus_sha256", "must be a lowercase SHA-256 digest")

    bundle = manifest["bundle"]
    if not isinstance(bundle, dict):
        raise _fail("manifest.bundle", "must be an object")
    _require_exact_keys(bundle, _MANIFEST_BUNDLE_KEYS, "manifest.bundle")
    for field in _MANIFEST_BUNDLE_KEYS - {
        "catalog_sha256",
        "formal_page_sha256",
        "knowledge_sha256",
    }:
        if type(bundle[field]) is not int or bundle[field] < 0:
            raise _fail(f"manifest.bundle.{field}", "must be a non-negative integer")
    for field in ("catalog_sha256", "formal_page_sha256", "knowledge_sha256"):
        if not isinstance(bundle[field], str) or not _SHA256_RE.fullmatch(bundle[field]):
            raise _fail(f"manifest.bundle.{field}", "must be a lowercase SHA-256 digest")

    runtime = manifest["runtime"]
    if not isinstance(runtime, dict):
        raise _fail("manifest.runtime", "must be an object")
    _require_exact_keys(runtime, _MANIFEST_RUNTIME_KEYS, "manifest.runtime")
    expected_runtime = {
        "self_contained": True,
        "query_network_access": False,
        "source_repository_required": False,
        "writes_to_bundle": False,
    }
    if runtime != expected_runtime:
        raise _fail("manifest.runtime", f"must equal {expected_runtime}")

    runtime_files = manifest["runtime_files"]
    if not isinstance(runtime_files, dict):
        raise _fail("manifest.runtime_files", "must be a path-to-SHA256 object")
    for path, digest in runtime_files.items():
        if not isinstance(path, str) or not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise _fail("manifest.runtime_files", "must map relative paths to lowercase SHA-256 digests")


def validate_bundle(
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    catalog_bytes: bytes,
    knowledge_root: Path,
    *,
    skill_root: Path | None = None,
) -> dict[str, Any]:
    """Validate schema, runtime closure, catalog, pages, and all recomputed facts."""

    _validate_manifest_shape(manifest)
    errors: list[str] = []
    facts = compute_bundle_facts(
        catalog,
        catalog_bytes,
        knowledge_root,
        integrity_errors=errors,
    )
    declared = manifest["bundle"]
    for field, actual in facts.items():
        if declared[field] != actual:
            errors.append(
                f"manifest.bundle.{field}: declares {declared[field]!r}, "
                f"recomputed {actual!r}"
            )

    actual_skill_root = skill_root or knowledge_root.parent.parent
    actual_runtime_files = expected_runtime_file_hashes(actual_skill_root)
    declared_runtime_files = manifest["runtime_files"]
    if set(declared_runtime_files) != set(actual_runtime_files):
        missing = sorted(set(actual_runtime_files) - set(declared_runtime_files))
        extra = sorted(set(declared_runtime_files) - set(actual_runtime_files))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        errors.append("manifest.runtime_files: " + "; ".join(details))
    for relative in sorted(set(actual_runtime_files) & set(declared_runtime_files)):
        actual = actual_runtime_files[relative]
        if declared_runtime_files[relative] != actual:
            errors.append(f"manifest.runtime_files.{relative}: SHA-256 mismatch")
    if errors:
        raise BundleIntegrityError(errors)
    return facts


def load_validated_bundle(references_root: Path, *, reject_transaction: bool = True) -> ValidatedBundle:
    """Load the three-artifact snapshot and fail closed on any integrity drift."""

    journal = references_root / TRANSACTION_JOURNAL_NAME
    if reject_transaction and _lexists(journal):
        raise _fail(str(journal), "an unfinished bundle transaction blocks reads; run builder --recover")
    _require_directory_without_symlink(references_root, str(references_root))
    manifest_path = references_root / "manifest.json"
    catalog_path = references_root / "catalog.json"
    manifest_bytes = read_regular_file(manifest_path, root=references_root)
    catalog_bytes = read_regular_file(catalog_path, root=references_root)
    manifest = strict_json_loads(manifest_bytes, location=str(manifest_path))
    catalog = strict_json_loads(catalog_bytes, location=str(catalog_path))
    if not isinstance(manifest, dict):
        raise _fail(str(manifest_path), "top-level value must be an object")
    if not isinstance(catalog, dict):
        raise _fail(str(catalog_path), "top-level value must be an object")
    facts = validate_bundle(
        manifest,
        catalog,
        catalog_bytes,
        references_root / "knowledge",
        skill_root=references_root.parent,
    )
    if reject_transaction and _lexists(journal):
        raise _fail(str(journal), "bundle changed while it was being validated")
    if manifest_bytes != read_regular_file(manifest_path, root=references_root):
        raise _fail(str(manifest_path), "changed while the bundle was being validated")
    if catalog_bytes != read_regular_file(catalog_path, root=references_root):
        raise _fail(str(catalog_path), "changed while the bundle was being validated")
    fingerprint_payload = json.dumps(
        {
            "manifest_sha256": sha256_bytes(manifest_bytes),
            "catalog_sha256": sha256_bytes(catalog_bytes),
            "facts": facts,
            "runtime_files": manifest["runtime_files"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ValidatedBundle(
        manifest=manifest,
        catalog=catalog,
        facts=facts,
        fingerprint=sha256_bytes(fingerprint_payload),
    )


def assert_bundle_unchanged(references_root: Path, expected: ValidatedBundle) -> ValidatedBundle:
    """Revalidate immediately before output and reject a concurrent generation switch."""

    current = load_validated_bundle(references_root)
    if current.fingerprint != expected.fingerprint:
        raise _fail(str(references_root), "bundle changed during the query")
    return current
