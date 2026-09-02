#!/usr/bin/env python3
"""Build the version-locked Siril 1.4.4 offline manual component.

The maintenance builder consumes only the archive named in
``siril_manual_sources.lock.json``.  It never imports or executes upstream
Python, Sphinx configuration, Git, pip, or any subprocess.  Runtime releases
contain the generated component, not this builder or its source lock.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import Any, BinaryIO, Iterable, Mapping, Sequence
from urllib.parse import urlparse
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener


SKILL_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = Path(__file__).with_name("siril_manual_sources.lock.json")
DEFAULT_OUTPUT = SKILL_ROOT / "references" / "siril-manual"
JOURNAL_NAME = ".siril-manual.transaction.json"

MANIFEST_SCHEMA = "deep-sky-siril.siril-manual-manifest/v1"
FILES_SCHEMA = "deep-sky-siril.siril-manual-files/v1"
CATALOG_SCHEMA = "deep-sky-siril.siril-manual-catalog/v1"
COMMANDS_SCHEMA = "deep-sky-siril.siril-manual-commands/v1"
SECTION_SCHEMA = "deep-sky-siril.siril-manual-section/v1"
ALIASES_SCHEMA = "deep-sky-siril.siril-manual-aliases/v1"
IMAGE_SELECTION_SCHEMA = "deep-sky-siril.siril-manual-image-selection/v1"
TREE_ALGORITHM = "sha256-path-nul-sha256-nul-size-lf/v1"
BUILDER_VERSION = "1"

HASH_RE = re.compile(r"[a-f0-9]{64}\Z")
COMMIT_RE = re.compile(r"[a-f0-9]{40}\Z")
DIRECTIVE_RE = re.compile(
    r"^\s*\.\.\s+(?P<kind>include|literalinclude|download|image|figure)::\s*(?P<target>\S.*?)\s*$"
)
CSV_TABLE_RE = re.compile(r"^(?P<indent>\s*)\.\.\s+csv-table::(?:\s*.*)?$")
CSV_FILE_RE = re.compile(r"^\s+:file:\s*(?P<target>\S.*?)\s*$")
COMMAND_RE = re.compile(
    r"^\.\. command::\s+(?P<name>\S+)\s*\n"
    r"\s+:scriptable:\s+(?P<scriptable>[01])\s*$",
    re.MULTILINE,
)
INCLUDE_RE = re.compile(r"^\s*\.\.\s+include::\s+(?P<target>\S+)\s*$", re.MULTILINE)
CONF_RELEASE_RE = re.compile(r"^release\s*=\s*(['\"])(?P<version>[^'\"]+)\1\s*$", re.MULTILINE)
HEADING_MARKS = frozenset("=-~^\"'`:+*#_")

NOTICE_TEXT = """# Siril documentation component notice

This directory is a separately licensed, version-locked offline component.
It contains documentation and selected images from the Siril documentation
project at commit `1550a31d325276124fe961368477c90d49df804b`.

Source: https://gitlab.com/free-astro/siril-doc
Pinned documentation version: 1.4.4
Concluded component license: NOASSERTION pending human legal review

Most upstream Siril documentation is accompanied by GNU General Public License
version 3 only (`GPL-3.0-only`).  The excerpt in
`source/doc/photometry/general.rst` identifies itself as a truncated and
modified copy of David Motl's MuniPack documentation under the GNU Free
Documentation License.  Its exact version is not stated in the Siril source.
This component therefore also supplies `LICENSE.GFDL-1.2.txt` as the candidate
license text identified by the upstream MuniPack manual evidence, while keeping
the concluded license as `NOASSERTION` until a human review confirms scope and
version.

The proprietary license of the surrounding `starun-siril` Skill does not
replace or restrict either license supplied in this component.  No warranty is
provided.  See `MODIFICATIONS.md` for the exact selection, provenance caveat,
and derived-index changes made by the Skill maintainers.
"""

MODIFICATIONS_TEXT = """# Modifications and selection statement

- Every bundled `source/doc/**/*.rst` file is copied byte-for-byte from the
  pinned upstream commit.  Safe in-document `include`, `literalinclude`,
  `download`, and `csv-table :file:` dependencies that resolve within `doc/`
  are also copied byte-for-byte.
- The Siril documentation project's upstream license is copied byte-for-byte
  and renamed `LICENSE.GPL-3.0.txt`.
- `LICENSE.GFDL-1.2.txt` is copied byte-for-byte from GNU's fixed GFDL 1.2
  license URL as the inferred candidate text for the MuniPack-derived excerpt in
  `source/doc/photometry/general.rst`.  Siril's RST does not state the GFDL
  version, so metadata remains `NOASSERTION` and public release remains blocked
  pending human legal review.
- Only the PNG files listed by `image-selection.json` are bundled.  The list is
  curated for command-line deep-sky processing; unselected images, generated
  HTML, themes, videos, animations, and external resources are omitted.
- `catalog.json`, `commands.json`, `sections.jsonl`, `aliases.zh-en.json`,
  `image-selection.json`, `files.json`, `manifest.json`, this file, and
  `NOTICE.md` are generated or authored by the `starun-siril` maintainers.
- The Chinese aliases are retrieval aids, not translations of the official
  manual and not additional Siril execution authorization.

The upstream RST is authoritative for documented Siril behavior.  The Skill's
separate command policy remains authoritative for commands it may execute.
"""


class ManualBuildError(RuntimeError):
    """A stable fail-closed maintenance-build error."""


def _load_json(path: Path) -> Any:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ManualBuildError(f"cannot read {path}: {exc}") from exc
    return _loads_json(data, str(path))


def _loads_json(data: bytes, label: str) -> Any:
    def reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ManualBuildError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result

    try:
        return json.loads(data, object_pairs_hook=reject_duplicate)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ManualBuildError(f"cannot decode {label}: {exc}") from exc


def load_source_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    payload = _load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != "starun-siril.siril-manual-source-lock/v1":
        raise ManualBuildError("unsupported Siril manual source lock")
    source = payload.get("source")
    component = payload.get("component")
    limits = payload.get("limits")
    if not all(isinstance(item, dict) for item in (source, component, limits)):
        raise ManualBuildError("source lock objects are incomplete")
    if component != {"id": "siril-manual", "version": "1.4.4"}:
        raise ManualBuildError("source lock component identity changed")
    if not COMMIT_RE.fullmatch(str(source.get("commit", ""))):
        raise ManualBuildError("source lock commit is invalid")
    if not HASH_RE.fullmatch(str(source.get("archive_sha256", ""))):
        raise ManualBuildError("source lock archive hash is invalid")
    parsed = urlparse(str(source.get("archive_url", "")))
    if parsed.scheme != "https" or parsed.hostname != "gitlab.com" or parsed.username or parsed.password:
        raise ManualBuildError("source lock archive URL is not the trusted GitLab HTTPS origin")
    if source.get("archive_root") != f"siril-doc-{source['commit']}":
        raise ManualBuildError("source lock archive root does not bind the commit")
    dependency_paths = source.get("expected_dependency_paths")
    if (
        not isinstance(dependency_paths, list)
        or dependency_paths != sorted(dependency_paths)
        or len(dependency_paths) != len(set(dependency_paths))
        or any(
            not isinstance(item, str)
            or _safe_relative_path(item, context="source-lock dependency").parts[0]
            != "doc"
            for item in dependency_paths
        )
    ):
        raise ManualBuildError("source lock dependency paths are not uniquely sorted")
    third_party_license = payload.get("third_party_license")
    if not isinstance(third_party_license, dict):
        raise ManualBuildError("source lock third-party license is missing")
    if third_party_license.get("concluded") != "NOASSERTION":
        raise ManualBuildError("third-party license must remain NOASSERTION pending review")
    if (
        third_party_license.get("inferred_candidate_spdx")
        != "GFDL-1.2-no-invariants-only"
    ):
        raise ManualBuildError("third-party license candidate changed")
    if not HASH_RE.fullmatch(str(third_party_license.get("sha256", ""))):
        raise ManualBuildError("third-party license hash is invalid")
    if third_party_license.get("size_bytes") != 20394:
        raise ManualBuildError("third-party license size is invalid")
    license_source = urlparse(str(third_party_license.get("source_url", "")))
    if (
        license_source.scheme != "https"
        or license_source.hostname != "ftp.gnu.org"
        or license_source.username
        or license_source.password
    ):
        raise ManualBuildError("third-party license URL is not the fixed GNU HTTPS origin")
    return payload


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_json_line(payload: Any) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def _safe_relative_path(raw: str, *, context: str) -> PurePosixPath:
    if not raw or "\x00" in raw or "\\" in raw:
        raise ManualBuildError(f"unsafe path in {context}: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManualBuildError(f"unsafe path in {context}: {raw!r}")
    return path


def _resolve_doc_reference(source_path: str, raw_target: str) -> str | None:
    target = raw_target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target or target.startswith(("http://", "https://", "data:", "|")):
        return None
    target = target.split("#", 1)[0]
    if target.startswith("/"):
        candidate = PurePosixPath("doc") / target.lstrip("/")
    else:
        candidate = PurePosixPath(source_path).parent / target
    normalized: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not normalized:
                raise ManualBuildError(f"document reference escapes root: {source_path} -> {raw_target}")
            normalized.pop()
        else:
            normalized.append(part)
    result = PurePosixPath(*normalized)
    if not result.parts or result.parts[0] != "doc":
        raise ManualBuildError(f"document reference escapes doc/: {source_path} -> {raw_target}")
    return result.as_posix()


def _read_archive_bytes(path: Path, expected_sha256: str, max_bytes: int) -> bytes:
    """Read a pinned local input without following any path component.

    The builder accepts an archive and a license text supplied by a maintainer.
    Both are trust inputs, so opening the final component with ``O_NOFOLLOW``
    is not sufficient: an attacker could redirect one of its parent
    directories.  On POSIX, walk the absolute path one component at a time
    using directory file descriptors and ``O_NOFOLLOW``.  The descriptor and
    its directory entry are rechecked after the read to catch replacement or
    in-place mutation while hashing.

    The conservative fallback is used only where ``openat``-style directory
    descriptors are unavailable.  It lstat-checks every component before and
    after opening, then applies the same descriptor drift checks.
    """
    if max_bytes <= 0:
        raise ManualBuildError("local input size bound is invalid")

    absolute = Path(os.path.abspath(os.fspath(path)))
    if os.name == "posix" and os.open in os.supports_dir_fd:
        return _read_regular_via_dirfd(absolute, expected_sha256, max_bytes)
    return _read_regular_fallback(absolute, expected_sha256, max_bytes)


def _read_and_verify_fd(
    fd: int,
    *,
    path: Path,
    expected_sha256: str,
    max_bytes: int,
) -> tuple[bytes, os.stat_result]:
    try:
        before = os.fstat(fd)
    except OSError as exc:
        raise ManualBuildError(f"cannot stat local input safely: {path}: {exc}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ManualBuildError(f"local input is not a regular file: {path}")
    if before.st_size <= 0 or before.st_size > max_bytes:
        raise ManualBuildError("local input size is outside the locked bound")

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        with os.fdopen(fd, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(min(1024 * 1024, max_bytes - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ManualBuildError("local input exceeds the locked byte bound")
                digest.update(chunk)
                chunks.append(chunk)
    except OSError as exc:
        raise ManualBuildError(f"cannot read local input safely: {path}: {exc}") from exc

    try:
        after = os.fstat(fd)
    except OSError as exc:
        raise ManualBuildError(f"cannot restat local input safely: {path}: {exc}") from exc
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after) or total != before.st_size:
        raise ManualBuildError(f"local input changed while being read: {path}")
    if digest.hexdigest() != expected_sha256:
        raise ManualBuildError("local input SHA-256 does not match the source lock")
    return b"".join(chunks), after


def _read_regular_via_dirfd(
    path: Path, expected_sha256: str, max_bytes: int
) -> bytes:
    parts = path.parts
    if not parts or parts[0] != os.sep or len(parts) < 2:
        raise ManualBuildError(f"local input path is invalid: {path}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
        os, "O_NOFOLLOW", 0
    )
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_fd: int | None = None
    file_fd: int | None = None
    try:
        directory_fd = os.open(os.sep, directory_flags)
        for component in parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, dir_fd=directory_fd)
        data, after = _read_and_verify_fd(
            file_fd,
            path=path,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
        )
        entry = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode) or (
            entry.st_dev,
            entry.st_ino,
            entry.st_size,
            entry.st_mtime_ns,
            entry.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ManualBuildError(f"local input path changed while being read: {path}")
        return data
    except ManualBuildError:
        raise
    except OSError as exc:
        raise ManualBuildError(
            f"cannot open local input without following links: {path}: {exc}"
        ) from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _read_regular_fallback(
    path: Path, expected_sha256: str, max_bytes: int
) -> bytes:
    checked: list[tuple[Path, os.stat_result]] = []
    current = Path(path.anchor)
    try:
        for component in path.parts[1:]:
            current = current / component
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise ManualBuildError(f"local input path contains a link: {current}")
            checked.append((current, info))
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
    except ManualBuildError:
        raise
    except OSError as exc:
        raise ManualBuildError(f"cannot open local input safely: {path}: {exc}") from exc
    try:
        data, after = _read_and_verify_fd(
            fd,
            path=path,
            expected_sha256=expected_sha256,
            max_bytes=max_bytes,
        )
        for component, before in checked:
            current_info = component.lstat()
            if stat.S_ISLNK(current_info.st_mode) or (
                current_info.st_dev,
                current_info.st_ino,
                current_info.st_mode,
                current_info.st_size,
                current_info.st_mtime_ns,
                current_info.st_ctime_ns,
            ) != (
                before.st_dev,
                before.st_ino,
                before.st_mode,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ):
                raise ManualBuildError(
                    f"local input path changed while being read: {path}"
                )
        if (after.st_dev, after.st_ino) != (checked[-1][1].st_dev, checked[-1][1].st_ino):
            raise ManualBuildError(f"local input path changed while being read: {path}")
        return data
    except OSError as exc:
        raise ManualBuildError(f"cannot recheck local input safely: {path}: {exc}") from exc
    finally:
        os.close(fd)


def _download_fixed_https(
    url: str,
    expected_sha256: str,
    max_bytes: int,
    *,
    trusted_hostname: str,
    user_agent: str,
) -> bytes:
    # Empty ProxyHandler prevents urllib from consulting proxy environment
    # variables, which can contain credentials or route a fixed source through
    # an unapproved endpoint.
    opener = build_opener(ProxyHandler({}), HTTPSHandler())
    request = Request(url, headers={"User-Agent": user_agent})
    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    try:
        with opener.open(request, timeout=60) as response:
            final = urlparse(response.geturl())
            if (
                final.scheme != "https"
                or final.hostname != trusted_hostname
                or final.username
                or final.password
            ):
                raise ManualBuildError(
                    "locked resource redirected outside its trusted HTTPS origin"
                )
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ManualBuildError("source archive exceeds the locked byte bound")
                digest.update(chunk)
                chunks.append(chunk)
    except ManualBuildError:
        raise
    except Exception as exc:
        raise ManualBuildError(f"fixed HTTPS resource download failed: {exc}") from exc
    if total == 0 or digest.hexdigest() != expected_sha256:
        raise ManualBuildError("downloaded resource SHA-256 does not match the source lock")
    return b"".join(chunks)


def _download_archive(url: str, expected_sha256: str, max_bytes: int) -> bytes:
    return _download_fixed_https(
        url,
        expected_sha256,
        max_bytes,
        trusted_hostname="gitlab.com",
        user_agent="starun-siril-manual-builder/1",
    )


def read_locked_archive(lock: Mapping[str, Any], archive_path: Path | None) -> bytes:
    source = lock["source"]
    limits = lock["limits"]
    if archive_path is not None:
        return _read_archive_bytes(
            archive_path,
            str(source["archive_sha256"]),
            int(limits["archive_bytes"]),
        )
    return _download_archive(
        str(source["archive_url"]),
        str(source["archive_sha256"]),
        int(limits["archive_bytes"]),
    )


def read_locked_gfdl_license(
    lock: Mapping[str, Any], license_path: Path | None
) -> bytes:
    metadata = lock.get("third_party_license")
    if not isinstance(metadata, dict):
        raise ManualBuildError("third-party license lock is missing")
    expected_sha256 = str(metadata.get("sha256", ""))
    expected_size = metadata.get("size_bytes")
    if not HASH_RE.fullmatch(expected_sha256) or not isinstance(expected_size, int):
        raise ManualBuildError("third-party license lock is invalid")
    if license_path is not None:
        data = _read_archive_bytes(license_path, expected_sha256, expected_size)
        if len(data) != expected_size:
            raise ManualBuildError("third-party license size does not match the source lock")
        return data
    data = _download_fixed_https(
        str(metadata.get("source_url", "")),
        expected_sha256,
        expected_size,
        trusted_hostname="ftp.gnu.org",
        user_agent="starun-siril-manual-license-builder/1",
    )
    if len(data) != expected_size:
        raise ManualBuildError("third-party license size does not match the source lock")
    return data


def read_safe_tar(archive: bytes, lock: Mapping[str, Any]) -> dict[str, bytes]:
    source = lock["source"]
    limits = lock["limits"]
    root = str(source["archive_root"])
    files: dict[str, bytes] = {}
    casefolded: set[str] = set()
    expanded = 0
    members_seen = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            for member in bundle:
                members_seen += 1
                if members_seen > int(limits["member_count"]):
                    raise ManualBuildError("source archive has too many members")
                path = _safe_relative_path(member.name.rstrip("/"), context="source archive")
                if not path.parts or path.parts[0] != root:
                    raise ManualBuildError("source archive contains an unexpected root")
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise ManualBuildError(f"source archive contains a link or special file: {member.name}")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ManualBuildError(f"source archive member is not a regular file: {member.name}")
                if member.mode & 0o111:
                    raise ManualBuildError(f"source archive contains an executable file: {member.name}")
                if member.size < 0 or member.size > int(limits["member_bytes"]):
                    raise ManualBuildError(f"source archive member exceeds the size bound: {member.name}")
                expanded += member.size
                if expanded > int(limits["expanded_bytes"]):
                    raise ManualBuildError("source archive exceeds the expanded byte bound")
                relative = PurePosixPath(*path.parts[1:]).as_posix()
                folded = relative.casefold()
                if relative in files or folded in casefolded:
                    raise ManualBuildError(f"source archive has a duplicate or case-colliding path: {relative}")
                handle = bundle.extractfile(member)
                if handle is None:
                    raise ManualBuildError(f"cannot read archive member: {member.name}")
                data = handle.read(int(limits["member_bytes"]) + 1)
                if len(data) != member.size:
                    raise ManualBuildError(f"archive member length mismatch: {member.name}")
                files[relative] = data
                casefolded.add(folded)
    except ManualBuildError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise ManualBuildError(f"invalid source archive: {exc}") from exc
    return files


def _decode_rst(data: bytes, path: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManualBuildError(f"upstream RST is not UTF-8: {path}") from exc


def _headings(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    result: list[tuple[int, str]] = []
    for index in range(len(lines) - 1):
        title = lines[index].strip()
        underline = lines[index + 1].strip()
        if (
            title
            and underline
            and len(set(underline)) == 1
            and underline[0] in HEADING_MARKS
            and len(underline) >= max(3, len(title) - 2)
            and not title.startswith("..")
        ):
            result.append((index, title))
    return result


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "section"


def _write_file(root: Path, relative: str, data: bytes) -> None:
    path = _safe_relative_path(relative, context="component output")
    target = root.joinpath(*path.parts)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError as exc:
        raise ManualBuildError(f"cannot create component file {relative}: {exc}") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _record(
    path: str,
    role: str,
    data: bytes,
    *,
    upstream_path: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path,
        "role": role,
        "sha256": _sha256(data),
        "size_bytes": len(data),
    }
    if upstream_path is not None:
        result["upstream_path"] = upstream_path
        result["upstream_blob"] = _git_blob_id(data)
    return result


def _resolved_directives(
    rst: Mapping[str, bytes],
) -> tuple[
    set[str],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, Any]]],
]:
    includes: set[str] = set()
    images: dict[str, list[dict[str, Any]]] = {}
    text_dependencies: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(rst):
        text = _decode_rst(rst[path], path)
        lines = text.splitlines()
        for line_number, line in enumerate(lines, 1):
            match = DIRECTIVE_RE.match(line)
            if match:
                kind = match.group("kind")
                resolved = _resolve_doc_reference(path, match.group("target"))
                if resolved is not None:
                    reference = {"path": path, "line": line_number, "directive": kind}
                    if kind in {"include", "literalinclude", "download"}:
                        includes.add(resolved)
                    else:
                        images.setdefault(resolved, []).append(reference)

            csv_table = CSV_TABLE_RE.match(line)
            if not csv_table:
                continue
            base_indent = len(csv_table.group("indent").expandtabs(8))
            file_reference: tuple[int, str] | None = None
            for option_index in range(line_number, len(lines)):
                option_line = lines[option_index]
                if not option_line.strip():
                    continue
                indent_prefix = option_line[: len(option_line) - len(option_line.lstrip(" \t"))]
                option_indent = len(indent_prefix.expandtabs(8))
                if option_indent <= base_indent:
                    break
                option_match = CSV_FILE_RE.match(option_line)
                if option_match:
                    if file_reference is not None:
                        raise ManualBuildError(
                            f"csv-table has duplicate :file: options: {path}:{line_number}"
                        )
                    file_reference = (option_index + 1, option_match.group("target"))
            if file_reference is None:
                continue
            option_line_number, raw_target = file_reference
            resolved = _resolve_doc_reference(path, raw_target)
            if resolved is None:
                raise ManualBuildError(
                    f"csv-table has an external or empty :file: target: {path}:{option_line_number}"
                )
            includes.add(resolved)
            text_dependencies.setdefault(path, []).append(
                {
                    "path": resolved,
                    "line": option_line_number,
                    "directive": "csv-table",
                }
            )
    for references in text_dependencies.values():
        references.sort(key=lambda item: (item["line"], item["path"]))
    return includes, images, text_dependencies


def _dependency_metadata(
    references: Sequence[Mapping[str, Any]],
    dependencies: Mapping[str, bytes],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for reference in references:
        path = str(reference["path"])
        data = dependencies.get(path)
        if data is None:
            raise ManualBuildError(f"text dependency is absent from the frozen closure: {path}")
        result.append(
            {
                "path": path,
                "line": int(reference["line"]),
                "directive": str(reference["directive"]),
                "sha256": _sha256(data),
            }
        )
    return result


def _append_dependency_text(
    body: str,
    metadata: Sequence[Mapping[str, Any]],
    dependencies: Mapping[str, bytes],
) -> str:
    chunks = [body] if body else []
    for item in metadata:
        path = str(item["path"])
        try:
            text = dependencies[path].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ManualBuildError(f"text dependency is not UTF-8: {path}") from exc
        chunks.append(f".. bundled-csv-table-begin:: {path}\n{text.rstrip()}\n.. bundled-csv-table-end:: {path}")
    return "\n\n".join(chunks)


def _build_sections(
    rst: Mapping[str, bytes],
    text_dependency_references: Mapping[str, Sequence[Mapping[str, Any]]],
    dependencies: Mapping[str, bytes],
) -> tuple[list[dict[str, Any]], bytes]:
    records: list[dict[str, Any]] = []
    for path in sorted(rst):
        text = _decode_rst(rst[path], path)
        lines = text.splitlines()
        headings = _headings(text)
        if not headings:
            headings = [(0, PurePosixPath(path).stem.replace("-", " "))]
        used_ids: dict[str, int] = {}
        for position, (start, heading) in enumerate(headings):
            end = headings[position + 1][0] - 1 if position + 1 < len(headings) else len(lines) - 1
            body_start = start + 2 if start + 1 < len(lines) else start
            body = "\n".join(lines[body_start : end + 1]).strip()
            dependency_metadata = _dependency_metadata(
                [
                    reference
                    for reference in text_dependency_references.get(path, ())
                    if start + 1 <= int(reference["line"]) <= end + 1
                ],
                dependencies,
            )
            body = _append_dependency_text(body, dependency_metadata, dependencies)
            base = _slug(heading)
            ordinal = used_ids.get(base, 0) + 1
            used_ids[base] = ordinal
            suffix = f"-{ordinal}" if ordinal > 1 else ""
            relative = path.removeprefix("doc/")
            record = {
                "schema": SECTION_SCHEMA,
                "id": f"section:{relative}#{base}{suffix}",
                "path": path,
                "title": headings[0][1],
                "heading": heading,
                "body": body,
                "start_line": start + 1,
                "end_line": end + 1,
                "source_sha256": _sha256(rst[path]),
                "sha256": _sha256(body.encode("utf-8")),
                "dependencies": dependency_metadata,
            }
            records.append(record)
    data = b"".join(_canonical_json_line(record) for record in records)
    return records, data


def _build_commands(commands_data: bytes, rst: Mapping[str, bytes]) -> tuple[list[dict[str, Any]], bytes]:
    text = _decode_rst(commands_data, "doc/Commands.rst")
    matches = list(COMMAND_RE.finditer(text))
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    source_sha256 = _sha256(commands_data)
    for index, match in enumerate(matches):
        name = match.group("name")
        key = name.casefold()
        if key in seen:
            raise ManualBuildError(f"duplicate command directive: {name}")
        seen.add(key)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end]
        include_paths: list[str] = []
        for include in INCLUDE_RE.finditer(block):
            resolved = _resolve_doc_reference("doc/Commands.rst", include.group("target"))
            if resolved is None or resolved not in rst:
                raise ManualBuildError(f"command include is missing or unsafe: {name}")
            include_paths.append(resolved)
        usage_paths = [path for path in include_paths if path.endswith("_use.rst")]
        if len(usage_paths) != 1:
            raise ManualBuildError(f"command does not have exactly one usage include: {name}")
        description_paths = [path for path in include_paths if path not in usage_paths]
        if not description_paths:
            raise ManualBuildError(f"command description include is missing: {name}")
        usage = _decode_rst(rst[usage_paths[0]], usage_paths[0]).strip()
        description = "\n\n".join(
            _decode_rst(rst[path], path).strip() for path in description_paths
        ).strip()
        records.append(
            {
                "id": f"command:{key}",
                "name": name,
                "path": "doc/Commands.rst",
                "title": name,
                "scriptable": match.group("scriptable") == "1",
                "usage": usage,
                "description": description,
                "include_paths": include_paths,
                "section_id": f"command:{key}",
                "source_sha256": source_sha256,
                "sha256": _sha256(block.encode("utf-8")),
            }
        )
    payload = {"schema": COMMANDS_SCHEMA, "commands": records}
    return records, _canonical_json_bytes(payload)


def _build_aliases(lock: Mapping[str, Any], valid_ids: set[str]) -> tuple[dict[str, list[str]], bytes]:
    aliases: list[dict[str, Any]] = []
    by_target: dict[str, list[str]] = {}
    seen: set[str] = set()
    for raw in lock.get("aliases", []):
        if not isinstance(raw, dict):
            raise ManualBuildError("source-lock alias entry is invalid")
        alias = str(raw.get("alias", "")).strip()
        targets = raw.get("target_ids")
        if not alias or alias in seen or not isinstance(targets, list) or not targets:
            raise ManualBuildError(f"invalid or duplicate source-lock alias: {alias!r}")
        if any(not isinstance(target, str) or target not in valid_ids for target in targets):
            raise ManualBuildError(f"source-lock alias targets an unknown record: {alias}")
        seen.add(alias)
        record = {
            "alias": alias,
            "language": "zh-CN",
            "reviewed": True,
            "target_ids": targets,
        }
        aliases.append(record)
        for target in targets:
            by_target.setdefault(target, []).append(alias)
    aliases.sort(key=lambda item: item["alias"])
    for values in by_target.values():
        values.sort()
    return by_target, _canonical_json_bytes({"schema": ALIASES_SCHEMA, "aliases": aliases})


def _build_catalog(
    rst: Mapping[str, bytes],
    commands: Sequence[Mapping[str, Any]],
    aliases_by_target: Mapping[str, list[str]],
    text_dependency_references: Mapping[str, Sequence[Mapping[str, Any]]],
    dependencies: Mapping[str, bytes],
) -> bytes:
    records: list[dict[str, Any]] = []
    for path in sorted(rst):
        text = _decode_rst(rst[path], path)
        headings = [heading for _, heading in _headings(text)]
        title = headings[0] if headings else PurePosixPath(path).stem.replace("-", " ")
        record_id = f"page:{path}"
        relative = PurePosixPath(path).relative_to("doc")
        section = relative.parts[0] if len(relative.parts) > 1 else "root"
        dependency_metadata = _dependency_metadata(
            text_dependency_references.get(path, ()), dependencies
        )
        search_text = _append_dependency_text(text, dependency_metadata, dependencies)
        records.append(
            {
                "id": record_id,
                "kind": "page",
                "path": path,
                "title": title,
                "section": section,
                "headings": headings,
                "search_text": search_text,
                "source_sha256": _sha256(rst[path]),
                "aliases": aliases_by_target.get(record_id, []),
                "dependencies": dependency_metadata,
            }
        )
    for command in commands:
        record_id = str(command["id"])
        records.append(
            {
                "id": record_id,
                "kind": "command",
                "path": command["path"],
                "title": command["name"],
                "section": "Commands",
                "headings": [command["name"]],
                "search_text": f"{command['name']}\n{command['usage']}\n{command['description']}",
                "source_sha256": command["source_sha256"],
                "aliases": aliases_by_target.get(record_id, []),
                "dependencies": [],
            }
        )
    records.sort(key=lambda item: (item["kind"], item["id"]))
    return _canonical_json_bytes({"schema": CATALOG_SCHEMA, "records": records})


def _build_image_selection(
    lock: Mapping[str, Any],
    upstream: Mapping[str, bytes],
    image_references: Mapping[str, list[dict[str, Any]]],
) -> tuple[list[tuple[str, bytes]], bytes]:
    configured = lock.get("selected_images")
    if not isinstance(configured, list):
        raise ManualBuildError("selected_images is missing from source lock")
    selected: list[dict[str, Any]] = []
    copies: list[tuple[str, bytes]] = []
    selected_paths: set[str] = set()
    for item in configured:
        if not isinstance(item, dict) or set(item) != {"path", "reason"}:
            raise ManualBuildError("selected image source-lock entry is invalid")
        upstream_path = str(item["path"])
        if upstream_path in selected_paths:
            raise ManualBuildError(f"duplicate selected image: {upstream_path}")
        if not upstream_path.lower().endswith(".png"):
            raise ManualBuildError(f"selected image is not a PNG: {upstream_path}")
        if upstream_path not in upstream:
            raise ManualBuildError(f"selected image is absent from source archive: {upstream_path}")
        references = image_references.get(upstream_path, [])
        if not references:
            raise ManualBuildError(f"selected image is not referenced by bundled RST: {upstream_path}")
        data = upstream[upstream_path]
        output_path = f"source/{upstream_path}"
        selected.append(
            {
                "path": output_path,
                "upstream_path": upstream_path,
                "references": references,
                "reason": str(item["reason"]),
                "sha256": _sha256(data),
                "size_bytes": len(data),
            }
        )
        copies.append((upstream_path, data))
        selected_paths.add(upstream_path)
    omitted: list[dict[str, Any]] = []
    for path in sorted(image_references):
        if path in selected_paths:
            continue
        omitted.append(
            {
                "upstream_path": path,
                "references": image_references[path],
                "reason": "not selected by the locked CLI/deep-sky image policy",
            }
        )
    payload = {
        "schema": IMAGE_SELECTION_SCHEMA,
        "policy": {
            "coverage": "selected",
            "formats": ["png"],
            "include": "RST-referenced scientific diagrams, parameter views, and before/after examples needed for deep-sky CLI reasoning",
            "exclude": "decorative UI, icons, badges, animation, video, external resources, and unselected local references",
        },
        "selected": selected,
        "omitted_local_references": omitted,
    }
    return copies, _canonical_json_bytes(payload)


def _tree_hash(records: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for record in records:
        digest.update(str(record["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(record["sha256"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(str(record["size_bytes"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def build_component(
    staging: Path,
    archive: bytes,
    lock: Mapping[str, Any],
    gfdl_license_data: bytes,
) -> dict[str, Any]:
    upstream = read_safe_tar(archive, lock)
    source = lock["source"]
    version_data = upstream.get(str(source["version_assertion_path"]))
    if version_data is None:
        raise ManualBuildError("version assertion file is absent from source archive")
    version_text = _decode_rst(version_data, str(source["version_assertion_path"]))
    release_match = CONF_RELEASE_RE.search(version_text)
    if not release_match or release_match.group("version") != lock["component"]["version"]:
        raise ManualBuildError("static conf.py release assertion does not match the source lock")

    rst = {
        path: data
        for path, data in upstream.items()
        if path.startswith("doc/") and path.endswith(".rst")
    }
    if len(rst) != int(source["expected_rst_files"]):
        raise ManualBuildError(
            f"RST file count changed: expected {source['expected_rst_files']}, got {len(rst)}"
        )
    include_paths, image_references, text_dependency_references = _resolved_directives(rst)
    expected_dependencies = source.get("expected_dependency_paths")
    if not isinstance(expected_dependencies, list) or any(
        not isinstance(path, str) for path in expected_dependencies
    ):
        raise ManualBuildError("source lock dependency path list is invalid")
    if sorted(include_paths.difference(rst)) != expected_dependencies:
        raise ManualBuildError(
            "RST dependency closure changed from the source lock: "
            f"expected={expected_dependencies}, "
            f"observed={sorted(include_paths.difference(rst))}"
        )
    dependencies: dict[str, bytes] = {}
    for path in sorted(include_paths):
        if path in rst:
            continue
        data = upstream.get(path)
        if data is None:
            raise ManualBuildError(f"RST dependency is missing from source archive: {path}")
        dependencies[path] = data

    commands_source = rst.get("doc/Commands.rst")
    if commands_source is None:
        raise ManualBuildError("doc/Commands.rst is missing")
    commands, commands_bytes = _build_commands(commands_source, rst)
    if len(commands) != int(source["expected_commands"]):
        raise ManualBuildError(
            f"command count changed: expected {source['expected_commands']}, got {len(commands)}"
        )
    sections, sections_bytes = _build_sections(
        rst, text_dependency_references, dependencies
    )
    page_ids = {f"page:{path}" for path in rst}
    command_ids = {str(command["id"]) for command in commands}
    aliases_by_target, aliases_bytes = _build_aliases(lock, page_ids | command_ids)
    catalog_bytes = _build_catalog(
        rst,
        commands,
        aliases_by_target,
        text_dependency_references,
        dependencies,
    )
    image_copies, image_selection_bytes = _build_image_selection(
        lock, upstream, image_references
    )

    staging.mkdir(mode=0o700)
    records: list[dict[str, Any]] = []

    def add(
        relative: str,
        role: str,
        data: bytes,
        *,
        upstream_path: str | None = None,
    ) -> None:
        _write_file(staging, relative, data)
        records.append(_record(relative, role, data, upstream_path=upstream_path))

    license_path = str(source["license_path"])
    license_data = upstream.get(license_path)
    if license_data is None:
        raise ManualBuildError("upstream license is missing")
    add("LICENSE.GPL-3.0.txt", "license", license_data, upstream_path=license_path)
    third_party_license = lock["third_party_license"]
    if (
        _sha256(gfdl_license_data) != third_party_license["sha256"]
        or len(gfdl_license_data) != third_party_license["size_bytes"]
    ):
        raise ManualBuildError("GFDL candidate license does not match the source lock")
    add("LICENSE.GFDL-1.2.txt", "license", gfdl_license_data)
    add("NOTICE.md", "notice", NOTICE_TEXT.encode("utf-8"))
    add("MODIFICATIONS.md", "modifications", MODIFICATIONS_TEXT.encode("utf-8"))
    for path, data in sorted(rst.items()):
        add(f"source/{path}", "source-rst", data, upstream_path=path)
    for path, data in sorted(dependencies.items()):
        add(f"source/{path}", "include-dependency", data, upstream_path=path)
    for path, data in image_copies:
        add(f"source/{path}", "selected-image", data, upstream_path=path)
    add("commands.json", "index", commands_bytes)
    add("sections.jsonl", "index", sections_bytes)
    add("aliases.zh-en.json", "index", aliases_bytes)
    add("catalog.json", "index", catalog_bytes)
    add("image-selection.json", "index", image_selection_bytes)

    records.sort(key=lambda item: item["path"])
    files_payload = {"schema": FILES_SCHEMA, "files": records}
    files_bytes = _canonical_json_bytes(files_payload)
    files_meta = {
        "path": "files.json",
        "sha256": _sha256(files_bytes),
        "size_bytes": len(files_bytes),
    }
    index_meta: dict[str, dict[str, Any]] = {}
    by_path = {str(record["path"]): record for record in records}
    for key, path in {
        "catalog": "catalog.json",
        "commands": "commands.json",
        "sections": "sections.jsonl",
        "aliases": "aliases.zh-en.json",
        "image_selection": "image-selection.json",
    }.items():
        record = by_path[path]
        index_meta[key] = {
            "path": path,
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
        }
    manual = {
        "version": lock["component"]["version"],
        "commit": source["commit"],
        "commit_time": source["commit_time"],
        "source_url": f"{lock['source']['project_url']}/-/tree/{source['commit']}/doc",
        "source_archive_url": source["archive_url"],
        "source_archive_sha256": source["archive_sha256"],
        "rtd_build_id": lock["read_the_docs"]["build_id"],
        "rtd_url": lock["read_the_docs"]["stable_url"],
    }
    license_record = by_path["LICENSE.GPL-3.0.txt"]
    gfdl_record = by_path["LICENSE.GFDL-1.2.txt"]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "component": lock["component"],
        "manual": manual,
        "license": {
            "concluded": "NOASSERTION",
            "legal_review": "required",
            "entries": [
                {
                    "id": "GPL-3.0-only",
                    "path": "LICENSE.GPL-3.0.txt",
                    "sha256": license_record["sha256"],
                    "scope": "siril-doc upstream material except separately attributed content",
                },
                {
                    "id": "LicenseRef-MuniPack-GNU-FDL-version-unspecified",
                    "inferred_candidate_spdx": third_party_license[
                        "inferred_candidate_spdx"
                    ],
                    "path": "LICENSE.GFDL-1.2.txt",
                    "sha256": gfdl_record["sha256"],
                    "source_url": third_party_license["source_url"],
                    "applies_to": third_party_license["applies_to"],
                },
            ],
        },
        "files": files_meta,
        "tree": {
            "algorithm": TREE_ALGORITHM,
            "sha256": _tree_hash(records),
            "file_count": len(records),
        },
        "indexes": index_meta,
        "coverage": {
            "rst": "complete_at_pinned_commit",
            "image": "selected",
        },
        "counts": {
            "rst_files": len(rst),
            "include_dependencies": len(dependencies),
            "commands": len(commands),
            "sections": len(sections),
            "selected_images": len(image_copies),
            "component_files": len(records),
        },
        "build": {"builder_version": BUILDER_VERSION},
    }
    _write_file(staging, "files.json", files_bytes)
    _write_file(staging, "manifest.json", _canonical_json_bytes(manifest))
    validate_bundle(staging, expected_lock=lock, check_journal=False)
    return manifest


def _read_regular_no_follow(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ManualBuildError(f"cannot open component file safely: {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ManualBuildError(f"component path is not a regular file: {path}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read()
        after = os.fstat(fd)
        if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ManualBuildError(f"component file changed while being read: {path}")
        return data
    finally:
        os.close(fd)


def validate_bundle(
    root: Path,
    *,
    expected_lock: Mapping[str, Any] | None = None,
    check_journal: bool = True,
) -> dict[str, Any]:
    if check_journal and (root.parent / JOURNAL_NAME).exists():
        raise ManualBuildError("Siril manual transaction journal exists")
    if root.is_symlink() or not root.is_dir():
        raise ManualBuildError("Siril manual component is missing or not a directory")
    manifest_data = _read_regular_no_follow(root / "manifest.json")
    files_data = _read_regular_no_follow(root / "files.json")
    manifest = _loads_json(manifest_data, "manifest.json")
    files_payload = _loads_json(files_data, "files.json")
    if manifest.get("schema") != MANIFEST_SCHEMA or files_payload.get("schema") != FILES_SCHEMA:
        raise ManualBuildError("component metadata schema is unsupported")
    if set(manifest) != {
        "schema",
        "component",
        "manual",
        "license",
        "files",
        "tree",
        "indexes",
        "coverage",
        "counts",
        "build",
    }:
        raise ManualBuildError("manifest fields are not the locked schema")
    if manifest.get("component") != {"id": "siril-manual", "version": "1.4.4"}:
        raise ManualBuildError("component identity is invalid")
    records = files_payload.get("files")
    if not isinstance(records, list) or not records:
        raise ManualBuildError("component file inventory is invalid")
    paths = [record.get("path") for record in records if isinstance(record, dict)]
    if len(paths) != len(records) or paths != sorted(paths) or len(set(paths)) != len(paths):
        raise ManualBuildError("component file inventory is not uniquely sorted")
    expected_paths = {"manifest.json", "files.json"}
    for record in records:
        path = _safe_relative_path(str(record.get("path", "")), context="component inventory")
        base_fields = {"path", "role", "sha256", "size_bytes"}
        optional_fields = {"upstream_path", "upstream_blob"}
        if not base_fields.issubset(record) or set(record) - base_fields - optional_fields:
            raise ManualBuildError(f"component inventory has unknown fields: {path}")
        if ("upstream_path" in record) != ("upstream_blob" in record):
            raise ManualBuildError(f"component upstream binding is incomplete: {path}")
        if "upstream_path" in record:
            upstream_path = _safe_relative_path(
                str(record["upstream_path"]), context=f"upstream path for {path}"
            )
            if upstream_path.parts[0] not in {"doc", "LICENSE.md"}:
                raise ManualBuildError(f"component upstream path is outside its source: {path}")
        if not isinstance(record.get("role"), str):
            raise ManualBuildError(f"component role is invalid: {path}")
        if not HASH_RE.fullmatch(str(record.get("sha256", ""))):
            raise ManualBuildError(f"component SHA-256 is invalid: {path}")
        if not isinstance(record.get("size_bytes"), int) or record["size_bytes"] < 0:
            raise ManualBuildError(f"component size is invalid: {path}")
        if "upstream_blob" in record and not COMMIT_RE.fullmatch(str(record["upstream_blob"])):
            raise ManualBuildError(f"upstream Git blob ID is invalid: {path}")
        data = _read_regular_no_follow(root.joinpath(*path.parts))
        if len(data) != record["size_bytes"] or _sha256(data) != record["sha256"]:
            raise ManualBuildError(f"component file fingerprint changed: {path}")
        if "upstream_blob" in record and _git_blob_id(data) != record["upstream_blob"]:
            raise ManualBuildError(f"component upstream Git blob binding is invalid: {path}")
        expected_paths.add(path.as_posix())
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ManualBuildError(f"component contains a symlink: {path}")
        if path.is_file():
            actual_paths.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ManualBuildError(f"component contains a special path: {path}")
    if actual_paths != expected_paths:
        raise ManualBuildError("component directory does not equal its inventory closure")
    files_meta = manifest.get("files")
    if not isinstance(files_meta, dict) or files_meta != {
        "path": "files.json",
        "sha256": _sha256(files_data),
        "size_bytes": len(files_data),
    }:
        raise ManualBuildError("manifest does not bind files.json")
    tree = manifest.get("tree")
    if not isinstance(tree, dict) or tree != {
        "algorithm": TREE_ALGORITHM,
        "sha256": _tree_hash(records),
        "file_count": len(records),
    }:
        raise ManualBuildError("manifest tree hash is invalid")
    counts = manifest.get("counts")
    roles: dict[str, int] = {}
    for record in records:
        roles[str(record["role"])] = roles.get(str(record["role"]), 0) + 1
    if not isinstance(counts, dict) or set(counts) != {
        "rst_files",
        "include_dependencies",
        "commands",
        "sections",
        "selected_images",
        "component_files",
    }:
        raise ManualBuildError("manifest component count is invalid")
    if (
        counts["component_files"] != len(records)
        or counts["rst_files"] != roles.get("source-rst", 0)
        or counts["include_dependencies"] != roles.get("include-dependency", 0)
        or counts["selected_images"] != roles.get("selected-image", 0)
    ):
        raise ManualBuildError("manifest file-role counts are invalid")
    indexes = manifest.get("indexes")
    expected_indexes = {
        "catalog": "catalog.json",
        "commands": "commands.json",
        "sections": "sections.jsonl",
        "aliases": "aliases.zh-en.json",
        "image_selection": "image-selection.json",
    }
    if not isinstance(indexes, dict) or set(indexes) != set(expected_indexes):
        raise ManualBuildError("manifest index bindings are invalid")
    by_path = {str(record["path"]): record for record in records}

    def validate_dependencies(value: Any, *, label: str) -> None:
        if not isinstance(value, list):
            raise ManualBuildError(f"{label} dependencies must be an array")
        observed: set[str] = set()
        for index, dependency in enumerate(value):
            if not isinstance(dependency, dict) or set(dependency) != {
                "path",
                "line",
                "directive",
                "sha256",
            }:
                raise ManualBuildError(
                    f"{label} dependency {index} has an invalid shape"
                )
            dependency_path = _safe_relative_path(
                str(dependency["path"]), context=f"{label} dependency"
            ).as_posix()
            if dependency_path in observed or not dependency_path.startswith("doc/"):
                raise ManualBuildError(
                    f"{label} dependency path is duplicate or outside doc/: {dependency_path}"
                )
            observed.add(dependency_path)
            if dependency["directive"] != "csv-table" or not isinstance(
                dependency["line"], int
            ) or dependency["line"] < 1:
                raise ManualBuildError(f"{label} dependency directive is invalid")
            source_record = by_path.get("source/" + dependency_path)
            if (
                source_record is None
                or source_record.get("role") != "include-dependency"
                or source_record.get("sha256") != dependency["sha256"]
            ):
                raise ManualBuildError(
                    f"{label} dependency is not bound to the file closure: {dependency_path}"
                )

    for key, index_path in expected_indexes.items():
        record = by_path.get(index_path)
        expected_meta = None if record is None else {
            "path": index_path,
            "sha256": record["sha256"],
            "size_bytes": record["size_bytes"],
        }
        if indexes.get(key) != expected_meta:
            raise ManualBuildError(f"manifest index binding is invalid: {key}")
    catalog = _loads_json(_read_regular_no_follow(root / "catalog.json"), "catalog.json")
    commands = _loads_json(_read_regular_no_follow(root / "commands.json"), "commands.json")
    aliases = _loads_json(_read_regular_no_follow(root / "aliases.zh-en.json"), "aliases.zh-en.json")
    images = _loads_json(_read_regular_no_follow(root / "image-selection.json"), "image-selection.json")
    if catalog.get("schema") != CATALOG_SCHEMA or not isinstance(catalog.get("records"), list):
        raise ManualBuildError("catalog index is invalid")
    for index, record in enumerate(catalog["records"]):
        if not isinstance(record, dict):
            raise ManualBuildError(f"catalog record {index} is invalid")
        validate_dependencies(record.get("dependencies"), label=f"catalog record {index}")
    if commands.get("schema") != COMMANDS_SCHEMA or not isinstance(commands.get("commands"), list):
        raise ManualBuildError("commands index is invalid")
    if aliases.get("schema") != ALIASES_SCHEMA or not isinstance(aliases.get("aliases"), list):
        raise ManualBuildError("aliases index is invalid")
    if images.get("schema") != IMAGE_SELECTION_SCHEMA or not isinstance(images.get("selected"), list):
        raise ManualBuildError("image-selection index is invalid")
    section_count = 0
    for line_number, line in enumerate(
        _read_regular_no_follow(root / "sections.jsonl").splitlines(), 1
    ):
        section = _loads_json(line, f"sections.jsonl:{line_number}")
        if not isinstance(section, dict) or section.get("schema") != SECTION_SCHEMA:
            raise ManualBuildError(f"section index record is invalid at line {line_number}")
        validate_dependencies(
            section.get("dependencies"), label=f"section line {line_number}"
        )
        section_count += 1
    if (
        counts["commands"] != len(commands["commands"])
        or counts["sections"] != section_count
        or counts["selected_images"] != len(images["selected"])
    ):
        raise ManualBuildError("manifest derived-index counts are invalid")
    license_meta = manifest.get("license")
    license_record = by_path.get("LICENSE.GPL-3.0.txt")
    gfdl_record = by_path.get("LICENSE.GFDL-1.2.txt")
    third_party_license = None if expected_lock is None else expected_lock.get(
        "third_party_license"
    )
    if not isinstance(third_party_license, dict):
        third_party_license = {
            "inferred_candidate_spdx": "GFDL-1.2-no-invariants-only",
            "source_url": "https://ftp.gnu.org/gnu/Licenses/fdl-1.2.txt",
            "applies_to": [
                "source/doc/photometry/general.rst#munipack-derived-excerpt"
            ],
        }
    expected_license_meta = None
    if license_record is not None and gfdl_record is not None:
        expected_license_meta = {
            "concluded": "NOASSERTION",
            "legal_review": "required",
            "entries": [
                {
                    "id": "GPL-3.0-only",
                    "path": "LICENSE.GPL-3.0.txt",
                    "sha256": license_record["sha256"],
                    "scope": "siril-doc upstream material except separately attributed content",
                },
                {
                    "id": "LicenseRef-MuniPack-GNU-FDL-version-unspecified",
                    "inferred_candidate_spdx": third_party_license[
                        "inferred_candidate_spdx"
                    ],
                    "path": "LICENSE.GFDL-1.2.txt",
                    "sha256": gfdl_record["sha256"],
                    "source_url": third_party_license["source_url"],
                    "applies_to": third_party_license["applies_to"],
                },
            ],
        }
    if not isinstance(license_meta, dict) or license_meta != expected_license_meta:
        raise ManualBuildError("manifest license binding is invalid")
    if expected_lock is not None:
        source = expected_lock["source"]
        dependency_paths = sorted(
            str(record.get("upstream_path"))
            for record in records
            if record.get("role") == "include-dependency"
        )
        if dependency_paths != source["expected_dependency_paths"]:
            raise ManualBuildError(
                "component dependency closure does not match the source lock"
            )
        manual = manifest.get("manual")
        if not isinstance(manual, dict) or (
            manual.get("version") != expected_lock["component"]["version"]
            or manual.get("commit") != source["commit"]
            or manual.get("source_archive_sha256") != source["archive_sha256"]
            or manual.get("rtd_build_id") != expected_lock["read_the_docs"]["build_id"]
        ):
            raise ManualBuildError("manifest is not bound to the source lock")
    return manifest


def _write_journal(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    data = _canonical_json_bytes(payload)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ManualBuildError(f"cannot write transaction journal: {exc}") from exc


def _remove_transaction_tree(path: Path, parent: Path, allowed_prefix: str) -> None:
    if path.parent != parent or not path.name.startswith(allowed_prefix):
        raise ManualBuildError("refusing to remove a path outside the manual transaction")
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def transactional_commit(
    staging: Path,
    target: Path,
    *,
    fail_at: str | None = None,
) -> None:
    parent = target.parent
    journal = parent / JOURNAL_NAME
    backup = parent / f".siril-manual.backup-{os.getpid()}"
    if journal.exists() or backup.exists():
        raise ManualBuildError("a Siril manual transaction is already pending")
    payload: dict[str, Any] = {
        "schema": "starun-siril.siril-manual-transaction/v1",
        "target": target.name,
        "staging": staging.name,
        "backup": backup.name,
        "phase": "prepared",
    }
    _write_journal(journal, payload)
    old_existed = target.exists()
    try:
        if fail_at == "after_journal":
            raise OSError("injected failure after journal")
        if old_existed:
            if target.is_symlink() or not target.is_dir():
                raise ManualBuildError("existing Siril manual target is not a directory")
            os.replace(target, backup)
            payload["phase"] = "old_moved"
            _write_journal(journal, payload)
        if fail_at == "after_old_moved":
            raise OSError("injected failure after old target move")
        os.replace(staging, target)
        payload["phase"] = "new_installed"
        _write_journal(journal, payload)
        if fail_at == "after_new_installed":
            raise OSError("injected failure after new target install")
        # Removing the journal is the commit point.  Keep the complete backup
        # until that succeeds so every reported pre-commit failure can still
        # restore the previous component byte-for-byte.
        journal.unlink()
        if backup.exists():
            try:
                _remove_transaction_tree(backup, parent, ".siril-manual.backup-")
            except OSError:
                # The new component is committed and independently verified.
                # A hidden old backup is recoverable maintenance debris, not a
                # reason to pretend the committed component was rolled back.
                pass
    except Exception as exc:
        rollback_error: Exception | None = None
        try:
            if target.exists() and backup.exists():
                _remove_transaction_tree(target, parent, "siril-manual")
            elif target.exists() and not old_existed:
                _remove_transaction_tree(target, parent, "siril-manual")
            if backup.exists():
                os.replace(backup, target)
            if staging.exists():
                _remove_transaction_tree(staging, parent, ".siril-manual.staging-")
            journal.unlink(missing_ok=True)
        except Exception as rollback_exc:  # journal intentionally remains if rollback is incomplete
            rollback_error = rollback_exc
        if rollback_error is not None:
            raise ManualBuildError(
                f"manual commit failed and rollback is incomplete: {exc}; {rollback_error}"
            ) from exc
        if isinstance(exc, ManualBuildError):
            raise
        raise ManualBuildError(f"manual commit failed and was rolled back: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify the fixed Siril 1.4.4 offline manual component"
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="Use an already-downloaded archive; it must match the locked SHA-256",
    )
    parser.add_argument(
        "--gfdl-license",
        type=Path,
        help="Use an already-downloaded pinned SPDX GFDL text",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Verify the checked-in component only")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        lock = load_source_lock()
        output = args.output.resolve()
        if output.name != "siril-manual" or output.parent.name != "references":
            raise ManualBuildError("output must be a references/siril-manual directory")
        if args.check:
            if args.archive is not None or args.gfdl_license is not None:
                raise ManualBuildError(
                    "--archive and --gfdl-license cannot be combined with --check"
                )
            manifest = validate_bundle(output, expected_lock=lock)
        else:
            if (output.parent / JOURNAL_NAME).exists():
                raise ManualBuildError("Siril manual transaction journal exists")
            archive = read_locked_archive(lock, args.archive)
            gfdl_license_data = read_locked_gfdl_license(
                lock, args.gfdl_license
            )
            staging = output.parent / f".siril-manual.staging-{os.getpid()}"
            if staging.exists():
                raise ManualBuildError("manual staging directory already exists")
            try:
                manifest = build_component(
                    staging, archive, lock, gfdl_license_data
                )
                transactional_commit(staging, output)
            except Exception:
                if staging.exists():
                    _remove_transaction_tree(staging, output.parent, ".siril-manual.staging-")
                raise
        summary = {
            "schema": "starun-siril.siril-manual-build-result/v1",
            "status": "verified" if args.check else "built",
            "output": str(output),
            "manual": manifest["manual"],
            "tree": manifest["tree"],
            "counts": manifest["counts"],
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0
    except ManualBuildError as exc:
        print(
            json.dumps(
                {
                    "schema": "starun-siril.siril-manual-build-result/v1",
                    "status": "failed",
                    "reason": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
