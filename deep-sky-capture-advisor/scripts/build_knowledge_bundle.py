#!/usr/bin/env python3
"""Build a validated knowledge bundle from regular blobs in a clean Git commit.

This maintainer command never executes code from the source repository. Runtime queries use
``query_knowledge.py`` and do not require the source repository or PyYAML.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping
from urllib.parse import unquote

from knowledge_common import (
    CATALOG_SCHEMA_VERSION,
    FORMAL_CATEGORIES,
    MANIFEST_SCHEMA_VERSION,
    TRANSACTION_JOURNAL_NAME,
    TRANSACTION_SCHEMA_VERSION,
    BundleIntegrityError,
    compute_bundle_facts,
    expected_runtime_file_hashes,
    load_json_strict,
    load_validated_bundle,
    read_regular_file,
    sha256_bytes,
    tree_sha256,
    validate_bundle,
    validate_human_verification,
)

try:
    import yaml
except ImportError as exc:  # pragma: no cover - exercised by the command-line error path
    raise SystemExit("Building the bundle requires PyYAML: python3 -m pip install PyYAML") from exc


SKILL_ROOT = Path(__file__).resolve().parent.parent
INCLUDED_DIRS = FORMAL_CATEGORIES
EXCLUDED_PATHS = (
    "raw/",
    "archive/",
    "integrations/",
    "services/",
    "scripts/",
    ".knowledge-catalog/",
    "内容补充缺口调研-2026-08/",
    "README.md",
    "log.md",
    "llms.txt",
)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
SOURCE_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
FORMAL_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
DOMAIN_SLUGS = {
    "00-知识库规范": "governance",
    "01-新人入门": "getting-started",
    "02-器材百科": "equipment",
    "03-拍摄SOP": "capture-sop",
    "04-后期处理": "processing",
    "05-目标图鉴": "targets",
    "06-选址与环境": "observing-conditions",
    "07-软件工具": "software",
    "08-FAQ": "faq",
    "09-踩坑与复盘": "lessons-learned",
}
SOURCE_REQUIRED_FIELDS = {
    "type",
    "title",
    "description",
    "tags",
    "status",
    "generated",
    "review",
    "stale_after",
    "applies_to",
    "sources",
    "category",
}
VALID_STATUSES = {"draft", "stable", "deprecated"}
TRANSACTION_KEYS = {"schema_version", "transaction_id", "transaction_dir", "old", "new"}
DIGEST_KEYS = {"catalog_file_sha256", "knowledge_sha256", "manifest_file_sha256"}


class BundleError(RuntimeError):
    """Raised when the requested snapshot cannot be built or recovered safely."""


@dataclass(frozen=True)
class BundlePaths:
    """All mutable bundle paths, injectable so tests never touch the installed skill."""

    skill_root: Path

    @property
    def references_root(self) -> Path:
        return self.skill_root / "references"

    @property
    def knowledge_root(self) -> Path:
        return self.references_root / "knowledge"

    @property
    def catalog_path(self) -> Path:
        return self.references_root / "catalog.json"

    @property
    def manifest_path(self) -> Path:
        return self.references_root / "manifest.json"

    @property
    def journal_path(self) -> Path:
        return self.references_root / TRANSACTION_JOURNAL_NAME


DEFAULT_PATHS = BundlePaths(SKILL_ROOT)


@dataclass(frozen=True)
class SourceSnapshot:
    source: Path
    commit: str
    commit_date: str
    blobs: dict[str, bytes]
    formal_page_sha256: str
    retrieval_corpus_sha256: str


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BundleError(f"Cannot render strict JSON: {exc}") from exc
    return (rendered + "\n").encode("utf-8")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_bytes(temporary, _json_bytes(value))
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _git_command(arguments: list[str]) -> list[str]:
    return [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        *arguments,
    ]


def _run_git_bytes(arguments: list[str], source: Path) -> bytes:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    completed = subprocess.run(
        _git_command(arguments),
        cwd=source,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if not detail:
            detail = completed.stdout.decode("utf-8", errors="replace").strip()
        raise BundleError(f"Git command failed ({' '.join(arguments)}): {detail}")
    return completed.stdout


def _run_git_text(arguments: list[str], source: Path) -> str:
    try:
        return _run_git_bytes(arguments, source).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise BundleError(f"Git command returned non-UTF-8 text: {' '.join(arguments)}") from exc


def _resolve_clean_source(source: Path) -> tuple[Path, str, str]:
    expanded = source.expanduser()
    if expanded.is_symlink():
        raise BundleError(f"Source path must not be a symbolic link: {expanded}")
    try:
        resolved = expanded.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BundleError(f"Source repository does not exist: {expanded}") from exc
    metadata = resolved.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise BundleError(f"Source must be a directory: {resolved}")
    top_level = Path(_run_git_text(["rev-parse", "--show-toplevel"], resolved)).resolve()
    if top_level != resolved:
        raise BundleError(f"Source must be the Git worktree root, got {resolved}; root is {top_level}")
    status = _run_git_bytes(
        ["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"],
        resolved,
    )
    if status:
        raise BundleError("Source working tree must be completely clean; commit or remove all changes first")
    commit = _run_git_text(["rev-parse", "--verify", "HEAD^{commit}"], resolved)
    if not COMMIT_RE.fullmatch(commit):
        raise BundleError(f"Git returned an invalid commit id: {commit!r}")
    commit_date = _run_git_text(["show", "-s", "--format=%cI", commit], resolved)
    return resolved, commit, commit_date


def _parse_ls_tree(data: bytes) -> dict[str, tuple[str, str, str]]:
    entries: dict[str, tuple[str, str, str]] = {}
    for record in data.split(b"\0"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split(" ")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise BundleError("Git tree contains an unsupported or non-UTF-8 entry") from exc
        if path in entries:
            raise BundleError(f"Git tree returned duplicate path: {path}")
        entries[path] = (mode, kind, object_id)
    return entries


def _commit_formal_entries(source: Path, commit: str) -> dict[str, tuple[str, str, str]]:
    raw = _run_git_bytes(
        ["ls-tree", "-r", "-z", "--full-tree", commit, "--", *INCLUDED_DIRS],
        source,
    )
    tree = _parse_ls_tree(raw)
    formal: dict[str, tuple[str, str, str]] = {}
    for directory in INCLUDED_DIRS:
        prefix = f"{directory}/"
        category_pages: list[str] = []
        for path, entry in tree.items():
            if not path.startswith(prefix) or not path.lower().endswith(".md"):
                continue
            relative_parts = PurePosixPath(path).parts
            if len(relative_parts) != 2:
                raise BundleError(f"Nested formal Markdown is not supported: {path}")
            if relative_parts[-1] == "index.md":
                continue
            mode, kind, _ = entry
            if mode == "120000" or kind != "blob" or mode not in {"100644", "100755"}:
                raise BundleError(f"Formal page must be a regular Git blob, not a symlink: {path}")
            category_pages.append(path)
            formal[path] = entry
        if not category_pages:
            raise BundleError(f"No formal knowledge pages were found in committed category: {directory}")
    return dict(sorted(formal.items()))


def _scan_nested_markdown(directory: Path, category_root: Path) -> list[str]:
    nested: list[str] = []
    for child in os.scandir(directory):
        path = Path(child.path)
        if child.is_symlink():
            raise BundleError(f"Symbolic links are not allowed below formal categories: {path}")
        if child.is_dir(follow_symlinks=False):
            nested.extend(_scan_nested_markdown(path, category_root))
        elif child.is_file(follow_symlinks=False) and path.suffix.lower() == ".md":
            nested.append(path.relative_to(category_root.parent).as_posix())
    return nested


def _worktree_formal_paths(source: Path) -> set[str]:
    paths: set[str] = set()
    for directory in INCLUDED_DIRS:
        category = source / directory
        try:
            metadata = category.lstat()
        except FileNotFoundError as exc:
            raise BundleError(f"Formal category is missing from the worktree: {category}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise BundleError(f"Formal category must be a real directory: {category}")
        for child in os.scandir(category):
            path = Path(child.path)
            if child.name == "index.md":
                # Category indexes are intentionally outside the trusted build input.
                continue
            if child.is_symlink():
                raise BundleError(f"Symbolic links are not allowed below formal categories: {path}")
            if child.is_dir(follow_symlinks=False):
                nested = _scan_nested_markdown(path, category)
                if nested:
                    raise BundleError("Nested formal Markdown is not supported: " + ", ".join(sorted(nested)))
                continue
            if child.is_file(follow_symlinks=False) and path.suffix.lower() == ".md":
                paths.add(path.relative_to(source).as_posix())
    return paths


def _read_commit_blobs(
    source: Path,
    entries: Mapping[str, tuple[str, str, str]],
) -> dict[str, bytes]:
    blobs: dict[str, bytes] = {}
    for path, (_, _, object_id) in entries.items():
        blobs[path] = _run_git_bytes(["cat-file", "blob", object_id], source)
    return blobs


def _normalize_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def _split_frontmatter(raw: bytes, relative: str) -> tuple[dict[str, Any], str]:
    try:
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise BundleError(f"Formal page is not valid UTF-8: {relative}") from exc
    if not text.startswith("---\n"):
        raise BundleError(f"Formal page has no YAML frontmatter: {relative}")
    marker = text.find("\n---\n", 4)
    if marker < 0:
        raise BundleError(f"Formal page has unterminated YAML frontmatter: {relative}")
    try:
        metadata = yaml.load(text[4:marker], Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise BundleError(f"Formal page frontmatter is invalid: {relative}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise BundleError(f"Formal page frontmatter is not an object: {relative}")
    return metadata, text[marker + 5 :]


def _validated_page(raw: bytes, relative: str) -> tuple[dict[str, Any], str]:
    metadata, body = _split_frontmatter(raw, relative)
    missing = sorted(field for field in SOURCE_REQUIRED_FIELDS if metadata.get(field) in (None, ""))
    if missing:
        raise BundleError(f"Formal page is missing required metadata {missing}: {relative}")
    if metadata["status"] not in VALID_STATUSES:
        raise BundleError(f"Formal page has invalid status {metadata['status']!r}: {relative}")
    if str(metadata["category"]) != PurePosixPath(relative).parent.name:
        raise BundleError(f"Formal page category does not match its path: {relative}")
    try:
        dt.date.fromisoformat(str(metadata["stale_after"]))
    except ValueError as exc:
        raise BundleError(f"Formal page has invalid stale_after date: {relative}") from exc
    generated = metadata["generated"]
    if not isinstance(generated, dict) or not generated.get("by") or not generated.get("at"):
        raise BundleError(f"Formal page generated metadata must contain by/at: {relative}")
    review = metadata["review"]
    if not isinstance(review, dict) or not review.get("owner") or not review.get("state"):
        raise BundleError(f"Formal page review metadata must contain owner/state: {relative}")
    applies_to = metadata["applies_to"]
    if not isinstance(applies_to, dict):
        raise BundleError(f"Formal page applies_to must be an object: {relative}")
    for field in ("系统", "条件", "不适用"):
        values = applies_to.get(field)
        if not isinstance(values, list) or not values or not all(str(value).strip() for value in values):
            raise BundleError(f"Formal page applies_to.{field} must be a non-empty list: {relative}")
    sources = metadata["sources"]
    if not isinstance(sources, list) or not sources:
        raise BundleError(f"Formal page sources must be a non-empty list: {relative}")
    source_ids: set[str] = set()
    for source in sources:
        if not isinstance(source, dict) or not source.get("id") or not source.get("resource"):
            raise BundleError(f"Every formal-page source must contain id/resource: {relative}")
        source_id = str(source["id"])
        if source_id in source_ids:
            raise BundleError(f"Formal page contains duplicate source id {source_id!r}: {relative}")
        source_ids.add(source_id)
    tags = metadata["tags"]
    if not isinstance(tags, list) or not all(isinstance(tag, str) and tag.strip() for tag in tags):
        raise BundleError(f"Formal page tags must be a list of non-empty strings: {relative}")
    verification = _json_safe(metadata.get("verified"))
    try:
        validated = validate_human_verification(verification, location=f"{relative}.verified")
    except BundleIntegrityError as exc:
        raise BundleError(str(exc)) from exc
    if validated is not None and validated != verification:
        raise BundleError(f"Human verification fields must not have surrounding whitespace: {relative}")
    return metadata, body


def _internal_links(body: str, page_path: PurePosixPath) -> list[str]:
    links: set[str] = set()
    for raw in LINK_RE.findall(body):
        target = raw.strip().strip("<>").split("#", 1)[0]
        if not target or "://" in target or target.startswith(("mailto:", "#")):
            continue
        target = unquote(target)
        normalized = PurePosixPath(target.lstrip("/")) if target.startswith("/") else page_path.parent / target
        parts: list[str] = []
        escaped = False
        for part in normalized.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    escaped = True
                    break
                parts.pop()
                continue
            parts.append(part)
        if escaped:
            raise BundleError(f"Internal link escapes the knowledge root in {page_path}: {raw}")
        if parts:
            links.add(PurePosixPath(*parts).as_posix())
    return sorted(links)


def _catalog_entry(relative: str, raw: bytes) -> dict[str, Any]:
    metadata, body = _validated_page(raw, relative)
    verification = _json_safe(metadata.get("verified"))
    return {
        "path": relative,
        "title": str(metadata["title"]),
        "description": str(metadata["description"]),
        "type": str(metadata["type"]),
        "category": str(metadata["category"]),
        "tags": [str(tag) for tag in metadata.get("tags") or []],
        "difficulty": _json_safe(metadata.get("difficulty")),
        "audience": _json_safe(metadata.get("audience")),
        "status": str(metadata["status"]),
        "updated": _json_safe(metadata.get("updated")),
        "stale_after": _json_safe(metadata.get("stale_after")),
        "review": _json_safe(metadata.get("review")),
        "verified": verification,
        "applies_to": _json_safe(metadata.get("applies_to")),
        "sources": _json_safe(metadata.get("sources")),
        "links": _internal_links(body, PurePosixPath(relative)),
        "sha256": sha256_bytes(raw),
    }


def _unbundled_sources(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: dict[str, dict[str, set[str]]] = {}
    for entry in entries:
        referrer = str(entry["path"])
        for source in entry.get("sources") or []:
            if not isinstance(source, dict):
                continue
            resource = str(source.get("resource") or "")
            if not resource.startswith("/raw/"):
                continue
            path = resource.lstrip("/")
            record = records.setdefault(
                path,
                {"referrers": set(), "source_ids": set(), "titles": set()},
            )
            record["referrers"].add(referrer)
            if source.get("id"):
                record["source_ids"].add(str(source["id"]))
            if source.get("title"):
                record["titles"].add(str(source["title"]))
        for link in entry.get("links") or []:
            if str(link).startswith("raw/"):
                record = records.setdefault(
                    str(link),
                    {"referrers": set(), "source_ids": set(), "titles": set()},
                )
                record["referrers"].add(referrer)
    return [
        {
            "path": path,
            "reason": "Raw evidence ledger intentionally excluded from the portable knowledge snapshot.",
            "referrers": sorted(values["referrers"]),
            "source_ids": sorted(values["source_ids"]),
            "titles": sorted(values["titles"]),
        }
        for path, values in sorted(records.items())
    ]


def _page_slug(relative: str) -> str:
    path = PurePosixPath(relative)
    domain = DOMAIN_SLUGS[path.parent.name]
    stem = unicodedata.normalize("NFKC", path.stem).lower()
    stem = re.sub(r"[^a-z0-9\u3400-\u9fff]+", "-", stem).strip("-")
    if not stem:
        raise BundleError(f"Cannot derive retrieval slug for {relative}")
    return f"concept/{domain}/{stem}"


def _formal_record_links(body: str, relative: str, formal_paths: set[str]) -> list[str]:
    links: set[str] = set()
    page = PurePosixPath(relative)
    for _, raw_target in SOURCE_LINK_RE.findall(body):
        target = urllib.parse.unquote(raw_target.strip()).split("#", 1)[0].split("?", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        normalized = PurePosixPath(target.lstrip("/")) if target.startswith("/") else page.parent / target
        parts: list[str] = []
        escaped = False
        for part in normalized.parts:
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    escaped = True
                    break
                parts.pop()
                continue
            parts.append(part)
        if escaped or not parts:
            continue
        resolved = PurePosixPath(*parts).as_posix()
        if target.endswith("/"):
            resolved = f"{resolved}/index.md"
        if resolved in formal_paths:
            links.add(resolved)
    return sorted(links)


def _render_retrieval_corpus(blobs: Mapping[str, bytes]) -> bytes:
    formal_paths = set(blobs)
    records: list[dict[str, Any]] = []
    titles: set[str] = set()
    slugs: set[str] = set()
    for relative, raw in blobs.items():
        metadata, body = _validated_page(raw, relative)
        title = str(metadata["title"]).strip()
        if title in titles:
            raise BundleError(f"Duplicate formal-page title: {title}")
        titles.add(title)
        slug = _page_slug(relative)
        if slug in slugs:
            raise BundleError(f"Duplicate formal-page slug: {slug}")
        slugs.add(slug)
        review = metadata["review"]
        verified = _json_safe(metadata.get("verified"))
        sources = metadata["sources"]
        record = {
            "access": "public_candidate",
            "category": PurePosixPath(relative).parent.name,
            "content_withheld": False,
            "description": str(metadata["description"]).strip(),
            "display_name": title,
            "entry_name": relative,
            "links": _formal_record_links(body, relative, formal_paths),
            "review_state": str(review["state"]),
            "slug": slug,
            "source_ids": sorted(str(source["id"]) for source in sources),
            "stale_after": str(metadata.get("stale_after") or "") or None,
            "status": str(metadata["status"]),
            "tags": sorted(str(tag) for tag in metadata.get("tags") or []),
            "text": _normalize_text(body),
            "type": str(metadata["type"]),
            "verified": bool(verified),
            "verified_at": str(verified.get("at")) if verified else None,
            "verified_by": str(verified.get("by")) if verified else None,
            "verified_scope": str(verified.get("scope")) if verified else None,
        }
        records.append(record)
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records
    ).encode("utf-8")


def _load_source_snapshot(
    source: Path,
    *,
    expect_source_commit: str | None,
    expect_formal_sha256: str | None,
) -> SourceSnapshot:
    resolved, commit, commit_date = _resolve_clean_source(source)
    if expect_source_commit is not None and commit != expect_source_commit:
        raise BundleError(f"Source commit mismatch: expected {expect_source_commit}, got {commit}")
    entries = _commit_formal_entries(resolved, commit)
    worktree_paths = _worktree_formal_paths(resolved)
    if worktree_paths != set(entries):
        missing = sorted(set(entries) - worktree_paths)
        extra = sorted(worktree_paths - set(entries))
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("extra or ignored " + ", ".join(extra))
        raise BundleError("Worktree formal-page inventory differs from the clean commit: " + "; ".join(details))
    blobs = _read_commit_blobs(resolved, entries)
    formal_sha = tree_sha256(blobs)
    if expect_formal_sha256 is not None and formal_sha != expect_formal_sha256:
        raise BundleError(f"Formal page SHA-256 mismatch: expected {expect_formal_sha256}, got {formal_sha}")
    corpus_sha = sha256_bytes(_render_retrieval_corpus(blobs))
    if _run_git_bytes(
        ["status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"],
        resolved,
    ):
        raise BundleError("Source working tree changed while commit blobs were being read")
    return SourceSnapshot(
        source=resolved,
        commit=commit,
        commit_date=commit_date,
        blobs=blobs,
        formal_page_sha256=formal_sha,
        retrieval_corpus_sha256=corpus_sha,
    )


def _render_skill_index(
    entries: list[dict[str, Any]],
    source_commit: str,
    verified_count: int,
    needs_review_count: int,
) -> str:
    lines = [
        "# 深空摄影知识总索引",
        "",
        "本页由 Skill 构建器根据 `catalog.json` 自动生成，仅供人工浏览；普通问答请使用",
        "`scripts/query_knowledge.py` 检索并回读正式页面。请勿手工维护本页。",
        "",
        "## 快照与信任边界",
        "",
        f"- 源提交：`{source_commit}`",
        f"- 正式页面：{len(entries)}",
        f"- 有效人工签署：{verified_count}/{len(entries)}",
        f"- 等待人工审核：{needs_review_count}/{len(entries)}",
        "- 完整来源与内容哈希：[manifest.json](../manifest.json)",
        "- 结构化检索元数据：[catalog.json](../catalog.json)",
        "- 未随 Skill 分发：`raw/`、`archive/`、`log.md` 及所有运行态、密钥和第三方服务。",
        "",
    ]
    if verified_count < len(entries):
        lines.extend(
            [
                "> 非权威参考：当前快照并非所有正式页面都具备有效人工签署；使用页面前仍需检查",
                "> `status`、`stale_after`、`review`、`verified.scope`、`applies_to` 与来源卡。",
                "",
            ]
        )
    for directory in INCLUDED_DIRS:
        lines.extend([f"## {directory}", ""])
        category_entries = [
            entry for entry in entries if str(entry["path"]).startswith(f"{directory}/")
        ]
        for entry in category_entries:
            title = " ".join(str(entry["title"]).split())
            description = " ".join(str(entry["description"]).split())
            lines.append(f"- [{title}](<{entry['path']}>) — {description}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _stage_bundle(snapshot: SourceSnapshot, stage_references: Path, paths: BundlePaths) -> dict[str, Any]:
    knowledge_root = stage_references / "knowledge"
    knowledge_root.mkdir(parents=True)
    for relative, content in snapshot.blobs.items():
        _write_bytes(knowledge_root / relative, content)

    entries = [_catalog_entry(relative, raw) for relative, raw in snapshot.blobs.items()]
    entries.sort(key=lambda item: item["path"])
    unbundled_sources = _unbundled_sources(entries)
    catalog = {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "entries": entries,
        "unbundled_internal_sources": unbundled_sources,
    }
    catalog_content = _json_bytes(catalog)
    verified_count = sum(entry["verified"] is not None for entry in entries)
    needs_review_count = sum(entry["review"]["state"] == "needs-human-review" for entry in entries)
    _write_bytes(
        knowledge_root / "index.md",
        _render_skill_index(entries, snapshot.commit, verified_count, needs_review_count).encode("utf-8"),
    )
    facts = compute_bundle_facts(catalog, catalog_content, knowledge_root)
    if facts["formal_page_sha256"] != snapshot.formal_page_sha256:
        raise BundleError("Staged formal-page hash differs from the committed source blobs")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "skill_name": "deep-sky-capture-advisor",
        "packaged_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "name": "StarunWiki",
            "git_commit": snapshot.commit,
            "git_commit_date": snapshot.commit_date,
            "working_tree_dirty": False,
            "retrieval_corpus_sha256": snapshot.retrieval_corpus_sha256,
        },
        "bundle": facts,
        "included": [
            "index.md (generated)",
            *[f"{directory}/**/*.md (excluding index.md)" for directory in INCLUDED_DIRS],
        ],
        "excluded": list(EXCLUDED_PATHS),
        "runtime": {
            "self_contained": True,
            "query_network_access": False,
            "source_repository_required": False,
            "writes_to_bundle": False,
        },
        "runtime_files": expected_runtime_file_hashes(paths.skill_root),
        "distribution_notice": (
            "SkillHub public non-authoritative beta 0.1.0. Source-rights and third-party "
            "disclosures are provided in NOTICE.md when present."
        ),
        "authority_rule": (
            "A bundled claim is authoritative only when all critical pages are stable, not stale, "
            "applicable, and covered by a human: verified.scope."
        ),
    }
    manifest_content = _json_bytes(manifest)
    _write_bytes(stage_references / "catalog.json", catalog_content)
    _write_bytes(stage_references / "manifest.json", manifest_content)
    validate_bundle(
        manifest,
        catalog,
        catalog_content,
        knowledge_root,
        skill_root=paths.skill_root,
    )
    return {
        "catalog": catalog,
        "manifest": manifest,
        "catalog_bytes": catalog_content,
        "manifest_bytes": manifest_content,
    }


def _knowledge_digest(root: Path) -> str:
    if not _lexists(root):
        raise BundleError(f"Knowledge directory is missing: {root}")
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BundleError(f"Knowledge root must be a real directory: {root}")
    files: dict[str, bytes] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for child in os.scandir(directory):
            path = Path(child.path)
            relative = path.relative_to(root).as_posix()
            if child.is_symlink():
                raise BundleError(f"Symbolic link found in bundle artifact: {relative}")
            if child.is_dir(follow_symlinks=False):
                pending.append(path)
            elif child.is_file(follow_symlinks=False):
                if path.suffix.lower() != ".md":
                    raise BundleError(f"Non-Markdown file found in knowledge artifact: {relative}")
                files[relative] = read_regular_file(path, root=root)
            else:
                raise BundleError(f"Unsupported filesystem object in knowledge artifact: {relative}")
    return tree_sha256(files)


def _artifact_presence(references_root: Path) -> tuple[bool, bool, bool]:
    return tuple(
        _lexists(references_root / relative)
        for relative in ("knowledge", "catalog.json", "manifest.json")
    )  # type: ignore[return-value]


def _artifact_digest(references_root: Path, relative: str) -> str:
    target = references_root / relative
    if relative == "knowledge":
        return _knowledge_digest(target)
    return sha256_bytes(read_regular_file(target, root=references_root))


def _artifact_digests(references_root: Path) -> dict[str, str] | None:
    presence = _artifact_presence(references_root)
    if presence == (False, False, False):
        return None
    if presence != (True, True, True):
        raise BundleError(f"Bundle artifact set is incomplete below {references_root}")
    return {
        "catalog_file_sha256": _artifact_digest(references_root, "catalog.json"),
        "knowledge_sha256": _artifact_digest(references_root, "knowledge"),
        "manifest_file_sha256": _artifact_digest(references_root, "manifest.json"),
    }


def _rename(source: Path, destination: Path) -> None:
    source.rename(destination)


def _validate_digest_record(value: Any, location: str, *, allow_none: bool) -> dict[str, str] | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, dict) or set(value) != DIGEST_KEYS:
        raise BundleError(f"{location} must contain exactly {sorted(DIGEST_KEYS)}")
    for key, digest in value.items():
        if not isinstance(digest, str) or not FORMAL_SHA_RE.fullmatch(digest):
            raise BundleError(f"{location}.{key} is not a lowercase SHA-256 digest")
    return value


def _load_transaction(paths: BundlePaths) -> tuple[dict[str, Any], Path]:
    journal = load_json_strict(paths.journal_path, root=paths.references_root)
    if set(journal) != TRANSACTION_KEYS:
        raise BundleError("Transaction journal has missing or unexpected fields")
    if type(journal["schema_version"]) is not int or journal["schema_version"] != TRANSACTION_SCHEMA_VERSION:
        raise BundleError("Transaction journal schema is unsupported")
    transaction_id = journal["transaction_id"]
    if not isinstance(transaction_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transaction_id):
        raise BundleError("Transaction journal has an invalid transaction id")
    expected_name = f".bundle-txn-{transaction_id}"
    if journal["transaction_dir"] != expected_name:
        raise BundleError("Transaction journal directory does not match its transaction id")
    transaction_dir = paths.references_root / expected_name
    try:
        metadata = transaction_dir.lstat()
    except FileNotFoundError as exc:
        raise BundleError(f"Transaction directory is missing: {transaction_dir}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BundleError("Transaction directory must be a real directory below references/")
    _validate_digest_record(journal["old"], "transaction.old", allow_none=True)
    _validate_digest_record(journal["new"], "transaction.new", allow_none=False)
    return journal, transaction_dir


def _remove_journal(paths: BundlePaths) -> None:
    paths.journal_path.unlink()
    _fsync_directory(paths.references_root)


def _cleanup_transaction_dir(transaction_dir: Path, references_root: Path) -> None:
    if transaction_dir.parent != references_root or not transaction_dir.name.startswith(".bundle-txn-"):
        raise BundleError(f"Refusing to clean unscoped transaction path: {transaction_dir}")
    if not _lexists(transaction_dir):
        return
    metadata = transaction_dir.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise BundleError(f"Refusing to clean non-directory transaction path: {transaction_dir}")
    shutil.rmtree(transaction_dir)


def _restore_old_artifacts(paths: BundlePaths, transaction_dir: Path, expected: dict[str, str]) -> None:
    old_root = transaction_dir / "old"
    quarantine = transaction_dir / "quarantine"
    quarantine.mkdir(exist_ok=True)
    digest_keys = {
        "knowledge": "knowledge_sha256",
        "catalog.json": "catalog_file_sha256",
        "manifest.json": "manifest_file_sha256",
    }
    selections: dict[str, Path] = {}
    for relative, digest_key in digest_keys.items():
        required = expected[digest_key]
        current = paths.references_root / relative
        backup = old_root / relative
        current_matches = False
        backup_matches = False
        if _lexists(current):
            try:
                current_matches = _artifact_digest(paths.references_root, relative) == required
            except (BundleError, BundleIntegrityError):
                current_matches = False
        if _lexists(backup):
            try:
                backup_matches = _artifact_digest(old_root, relative) == required
            except (BundleError, BundleIntegrityError):
                backup_matches = False
        if backup_matches:
            selections[relative] = backup
        elif current_matches:
            selections[relative] = current
        else:
            raise BundleError(f"Cannot locate the recorded old artifact during recovery: {relative}")

    for relative, selected in selections.items():
        current = paths.references_root / relative
        if selected == current:
            continue
        if _lexists(current):
            quarantine_target = quarantine / f"{relative.replace('/', '_')}-{uuid.uuid4().hex}"
            _rename(current, quarantine_target)
        _rename(selected, current)
    _fsync_directory(paths.references_root)
    if _artifact_digests(paths.references_root) != expected:
        raise BundleError("Restored artifact set does not match the recorded old hashes")


def recover(paths: BundlePaths = DEFAULT_PATHS) -> dict[str, Any]:
    """Resolve one recorded interrupted transaction without touching unknown paths."""

    if not _lexists(paths.journal_path):
        return {"status": "no-transaction", "recovered": False}
    journal, transaction_dir = _load_transaction(paths)
    old = journal["old"]
    new = journal["new"]
    try:
        current = _artifact_digests(paths.references_root)
    except BundleError:
        current = None

    if current == new:
        try:
            load_validated_bundle(paths.references_root, reject_transaction=False)
        except BundleIntegrityError as exc:
            if old is None:
                raise BundleError(f"Installed new artifacts are invalid and no old bundle exists: {exc}") from exc
        else:
            _remove_journal(paths)
            _cleanup_transaction_dir(transaction_dir, paths.references_root)
            return {"status": "committed-new", "recovered": True}

    if old is not None and current == old:
        _remove_journal(paths)
        _cleanup_transaction_dir(transaction_dir, paths.references_root)
        return {"status": "kept-old", "recovered": True}

    if old is not None:
        _restore_old_artifacts(paths, transaction_dir, old)
        _remove_journal(paths)
        _cleanup_transaction_dir(transaction_dir, paths.references_root)
        return {"status": "restored-old", "recovered": True}
    raise BundleError(
        "Interrupted initial installation is ambiguous; journal retained and runtime remains blocked"
    )


def _install_transaction(paths: BundlePaths, transaction_dir: Path) -> None:
    new_root = transaction_dir / "new"
    old_root = transaction_dir / "old"
    old = _artifact_digests(paths.references_root)
    new = _artifact_digests(new_root)
    if new is None:
        raise BundleError("Staged transaction has no artifact set")
    old_root.mkdir()
    transaction_id = transaction_dir.name.removeprefix(".bundle-txn-")
    journal = {
        "schema_version": TRANSACTION_SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "transaction_dir": transaction_dir.name,
        "old": old,
        "new": new,
    }
    _atomic_write_json(paths.journal_path, journal)
    try:
        if old is not None:
            for relative in ("knowledge", "catalog.json", "manifest.json"):
                _rename(paths.references_root / relative, old_root / relative)
        for relative in ("knowledge", "catalog.json", "manifest.json"):
            _rename(new_root / relative, paths.references_root / relative)
        _fsync_directory(paths.references_root)
        load_validated_bundle(paths.references_root, reject_transaction=False)
        _remove_journal(paths)
    except Exception as exc:
        try:
            recover(paths)
        except Exception as recovery_exc:
            raise BundleError(
                f"Bundle installation failed ({exc}); automatic recovery also failed ({recovery_exc}). "
                "The transaction journal was retained."
            ) from recovery_exc
        raise BundleError(f"Bundle installation failed and was recovered safely: {exc}") from exc
    _cleanup_transaction_dir(transaction_dir, paths.references_root)


def build(
    source: Path,
    *,
    replace: bool = False,
    check: bool = False,
    expect_source_commit: str | None = None,
    expect_formal_sha256: str | None = None,
    paths: BundlePaths = DEFAULT_PATHS,
) -> dict[str, Any]:
    """Build, verify, and optionally transactionally install one clean-commit snapshot."""

    if expect_source_commit is not None and not COMMIT_RE.fullmatch(expect_source_commit):
        raise BundleError("--expect-source-commit must be a lowercase 40- or 64-character Git id")
    if expect_formal_sha256 is not None and not FORMAL_SHA_RE.fullmatch(expect_formal_sha256):
        raise BundleError("--expect-formal-sha256 must be a lowercase SHA-256 digest")
    if check and replace:
        raise BundleError("--check cannot be combined with --replace")
    if _lexists(paths.journal_path):
        raise BundleError("An unfinished bundle transaction exists; run --recover first")

    snapshot = _load_source_snapshot(
        source,
        expect_source_commit=expect_source_commit,
        expect_formal_sha256=expect_formal_sha256,
    )
    if check:
        with tempfile.TemporaryDirectory(prefix="deep-sky-bundle-check-") as temporary:
            stage_references = Path(temporary) / "references"
            stage_references.mkdir()
            staged = _stage_bundle(snapshot, stage_references, paths)
            manifest = staged["manifest"]
            return {
                "installed": False,
                "checked": True,
                "source_commit": snapshot.commit,
                "content_pages": len(snapshot.blobs),
                "retrieval_corpus_sha256": snapshot.retrieval_corpus_sha256,
                **manifest["bundle"],
            }

    paths.references_root.mkdir(parents=True, exist_ok=True)
    if paths.references_root.is_symlink():
        raise BundleError(f"References root must not be a symbolic link: {paths.references_root}")

    presence = _artifact_presence(paths.references_root)
    if presence != (False, False, False) and presence != (True, True, True):
        raise BundleError("Existing bundle is incomplete; recover or repair it before rebuilding")
    if presence == (True, True, True) and not replace:
        raise BundleError(f"Bundle already exists: {paths.knowledge_root}; pass --replace to rebuild it")

    transaction_id = uuid.uuid4().hex
    transaction_dir = Path(
        tempfile.mkdtemp(prefix=f".bundle-txn-{transaction_id}-prepare-", dir=paths.references_root)
    )
    final_transaction_dir = paths.references_root / f".bundle-txn-{transaction_id}"
    try:
        new_root = transaction_dir / "new"
        new_root.mkdir()
        staged = _stage_bundle(snapshot, new_root, paths)
        transaction_dir.rename(final_transaction_dir)
        transaction_dir = final_transaction_dir
        _install_transaction(paths, transaction_dir)
    except Exception:
        if not _lexists(paths.journal_path) and _lexists(transaction_dir):
            _cleanup_transaction_dir(transaction_dir, paths.references_root)
        raise
    manifest = staged["manifest"]
    return {
        "installed": True,
        "checked": True,
        "source_commit": snapshot.commit,
        "content_pages": len(snapshot.blobs),
        "retrieval_corpus_sha256": snapshot.retrieval_corpus_sha256,
        **manifest["bundle"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Path to the clean StarunWiki Git worktree")
    parser.add_argument("--replace", action="store_true", help="Transactionally replace an existing bundle")
    parser.add_argument("--check", action="store_true", help="Build and validate without installing artifacts")
    parser.add_argument("--expect-source-commit", help="Require an exact clean source commit")
    parser.add_argument(
        "--expect-formal-sha256",
        help="Require the exact path-and-raw-blob hash of committed formal pages",
    )
    parser.add_argument("--recover", action="store_true", help="Recover one recorded interrupted transaction")
    args = parser.parse_args()
    try:
        if args.recover:
            if any(
                value
                for value in (
                    args.source,
                    args.replace,
                    args.check,
                    args.expect_source_commit,
                    args.expect_formal_sha256,
                )
            ):
                raise BundleError("--recover cannot be combined with build options")
            result = recover()
        else:
            if args.source is None:
                raise BundleError("--source is required unless --recover is used")
            result = build(
                args.source,
                replace=args.replace,
                check=args.check,
                expect_source_commit=args.expect_source_commit,
                expect_formal_sha256=args.expect_formal_sha256,
            )
    except (BundleError, BundleIntegrityError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
