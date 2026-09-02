#!/usr/bin/env python3
"""Strict, read-only verification helpers for the bundled Siril manual.

This module deliberately has no network, subprocess, cache, or write path.  It is
shared by the runtime query command and the deterministic release packager.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence


MANIFEST_SCHEMA = "deep-sky-siril.siril-manual-manifest/v1"
FILES_SCHEMA = "deep-sky-siril.siril-manual-files/v1"
QUERY_SCHEMA = "starun-siril.manual-query.v1"
TREE_ALGORITHM = "sha256-path-nul-sha256-nul-size-lf/v1"
MANUAL_VERSION = "1.4.4"
MANUAL_COMMIT = "1550a31d325276124fe961368477c90d49df804b"
RTD_BUILD_ID = "34132359"
EXPECTED_COMMIT_TIME = "2026-08-19T08:44:55+02:00"
EXPECTED_SOURCE_URL = (
    "https://gitlab.com/free-astro/siril-doc/-/tree/"
    f"{MANUAL_COMMIT}/doc"
)
EXPECTED_SOURCE_ARCHIVE_URL = (
    "https://gitlab.com/free-astro/siril-doc/-/archive/"
    f"{MANUAL_COMMIT}/siril-doc-{MANUAL_COMMIT}.tar.gz"
)
EXPECTED_SOURCE_ARCHIVE_SHA256 = (
    "13d19abb4f1309f53200820bfa8b9507219ba836edb3d79bc7045d7eb0fc40a0"
)
EXPECTED_RTD_URL = "https://siril.readthedocs.io/en/stable/"
EXPECTED_MANIFEST_SHA256 = (
    "5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d"
)
EXPECTED_FILES_SHA256 = (
    "fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce"
)
EXPECTED_TREE_SHA256 = (
    "475f37da07acd98e9dbf406cc60ff0f0643d839d363dfa914e5b3717336be9a9"
)
EXPECTED_GPL_LICENSE_SHA256 = (
    "43e0a03410d5863a435c32f42deaa92bbf22fd5bd662e10bb0ce87727ba71e60"
)
EXPECTED_GFDL_LICENSE_SHA256 = (
    "2652c22ba086f92e55ae4a9f9c890ad4766ffd7814a73d318e22a597edf857a4"
)
COMPONENT_RELATIVE_PATH = "references/siril-manual"
TRANSACTION_RELATIVE_PATH = "references/.siril-manual.transaction.json"
MANIFEST_NAME = "manifest.json"
FILES_NAME = "files.json"
EXPECTED_INDEX_PATHS = MappingProxyType(
    {
        "catalog": "catalog.json",
        "commands": "commands.json",
        "sections": "sections.jsonl",
        "aliases": "aliases.zh-en.json",
        "image_selection": "image-selection.json",
    }
)
EXPECTED_COMPONENT_FILES = frozenset(
    {
        "LICENSE.GPL-3.0.txt",
        "LICENSE.GFDL-1.2.txt",
        "NOTICE.md",
        "MODIFICATIONS.md",
        *EXPECTED_INDEX_PATHS.values(),
    }
)
_HEX_DIGITS = frozenset("0123456789abcdef")
GFDL_CANDIDATE_SPDX = "GFDL-1.2-no-invariants-only"
GFDL_LICENSE_ID = "LicenseRef-MuniPack-GNU-FDL-version-unspecified"
GFDL_SOURCE_URL = "https://ftp.gnu.org/gnu/Licenses/fdl-1.2.txt"
GFDL_APPLIES_TO = (
    "source/doc/photometry/general.rst#munipack-derived-excerpt",
)


class BundleError(RuntimeError):
    """The bundled manual is absent, malformed, changed, or untrusted."""


class DuplicateKeyError(ValueError):
    """A JSON object contains a duplicate key."""


@dataclass(frozen=True)
class CapturedFile:
    """Immutable bytes plus the file identity observed while reading them."""

    relative_path: str
    data: bytes
    sha256: str
    size_bytes: int
    device: int
    inode: int
    mode: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class BundleSnapshot:
    """A fully verified, closed snapshot of the manual component."""

    skill_root: Path
    component_root: Path
    manifest: Mapping[str, Any]
    files_document: Mapping[str, Any]
    records: tuple[Mapping[str, Any], ...]
    captures: Mapping[str, CapturedFile]
    manifest_capture: CapturedFile
    files_capture: CapturedFile
    tree_sha256: str
    fingerprint: str
    root_identity: tuple[int, int, int, int, int]

    @property
    def manual(self) -> Mapping[str, Any]:
        return _require_mapping(self.manifest.get("manual"), "manifest.manual")

    def data(self, relative_path: str) -> bytes:
        try:
            return self.captures[relative_path].data
        except KeyError as exc:
            raise BundleError(
                f"manual component path is not in the verified closure: {relative_path}"
            ) from exc

    def sha256(self, relative_path: str) -> str:
        try:
            return self.captures[relative_path].sha256
        except KeyError as exc:
            raise BundleError(
                f"manual component path is not in the verified closure: {relative_path}"
            ) from exc

    def reverify(self, relative_paths: Iterable[str] = ()) -> None:
        """Recheck identities and hashes immediately before returning output."""

        current_root = _directory_identity(self.component_root, "manual component")
        if current_root != self.root_identity:
            raise BundleError("manual component changed during the query")

        required = {MANIFEST_NAME, FILES_NAME, *relative_paths}
        for relative_path in sorted(required):
            if relative_path == MANIFEST_NAME:
                expected = self.manifest_capture
            elif relative_path == FILES_NAME:
                expected = self.files_capture
            else:
                try:
                    expected = self.captures[relative_path]
                except KeyError as exc:
                    raise BundleError(
                        "cannot reverify a path outside the manual closure: "
                        f"{relative_path}"
                    ) from exc
            observed = _read_regular_no_follow(self.component_root, relative_path)
            if not _same_capture(expected, observed):
                raise BundleError(
                    f"manual component file changed during the query: {relative_path}"
                )


def _reject_duplicate_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def strict_json_bytes(data: bytes, *, document: str) -> Any:
    """Decode UTF-8 JSON while rejecting duplicate object keys and non-finite data."""

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{document} must be valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=lambda token: (_raise_nonfinite(token)),
        )
    except (ValueError, DuplicateKeyError) as exc:
        raise BundleError(f"{document} is not strict JSON: {exc}") from exc


def _raise_nonfinite(token: str) -> Any:
    raise ValueError(f"non-finite JSON number: {token}")


def strict_json_lines(data: bytes, *, document: str) -> tuple[Mapping[str, Any], ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError(f"{document} must be valid UTF-8") from exc
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            raise BundleError(f"{document} has a blank line at {line_number}")
        value = strict_json_bytes(
            line.encode("utf-8"), document=f"{document} line {line_number}"
        )
        rows.append(_require_mapping(value, f"{document} line {line_number}"))
    return tuple(rows)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BundleError(f"{label} must be a JSON object")
    return value


def _require_sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise BundleError(f"{label} must be a JSON array")
    return value


def _require_string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        qualifier = "a non-empty" if nonempty else "a"
        raise BundleError(f"{label} must be {qualifier} string")
    return value


def _require_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise BundleError(f"{label} must be an integer >= {minimum}")
    return value


def _require_sha256(value: Any, label: str) -> str:
    digest = _require_string(value, label)
    if len(digest) != 64 or any(char not in _HEX_DIGITS for char in digest):
        raise BundleError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _require_git_blob(value: Any, label: str) -> str:
    digest = _require_string(value, label)
    if len(digest) != 40 or any(char not in _HEX_DIGITS for char in digest):
        raise BundleError(f"{label} must be a lowercase Git SHA-1 object ID")
    return digest


def _git_blob_sha1(data: bytes) -> str:
    payload = b"blob " + str(len(data)).encode("ascii") + b"\0" + data
    try:
        return hashlib.sha1(payload, usedforsecurity=False).hexdigest()
    except TypeError:  # Python implementations without usedforsecurity.
        return hashlib.sha1(payload).hexdigest()


def safe_relative_path(value: Any, *, label: str) -> str:
    path = _require_string(value, label)
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or "\\" in path
        or path != pure.as_posix()
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise BundleError(f"{label} is not a canonical relative POSIX path: {path!r}")
    if any("\x00" in part for part in pure.parts):
        raise BundleError(f"{label} contains NUL")
    return path


def _directory_identity(path: Path, label: str) -> tuple[int, int, int, int, int]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BundleError(f"{label} is missing or unreadable: {exc}") from exc
    if _is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise BundleError(f"{label} must be a real directory, not a link")
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _identity(info: os.stat_result, *, include_size: bool) -> tuple[int, ...]:
    values = (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )
    return (*values, info.st_size) if include_size else values


def _is_link_or_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _secure_openat_available() -> bool:
    """Return whether Python exposes the primitives needed for safe traversal."""

    return (
        bool(getattr(os, "O_DIRECTORY", 0))
        and bool(getattr(os, "O_NOFOLLOW", 0))
        and os.open in getattr(os, "supports_dir_fd", ())
        and os.listdir in getattr(os, "supports_fd", ())
        and os.stat in getattr(os, "supports_dir_fd", ())
        and os.stat in getattr(os, "supports_follow_symlinks", ())
    )


def _capture_open_file(descriptor: int, relative_path: str) -> CapturedFile:
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise BundleError(f"manual component entry is not a regular file: {relative_path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise BundleError(
            f"cannot read manual component file safely: {relative_path}: {exc}"
        ) from exc
    if _identity(before, include_size=True) != _identity(after, include_size=True):
        raise BundleError(f"manual component file changed while reading: {relative_path}")
    data = b"".join(chunks)
    if len(data) != after.st_size:
        raise BundleError(f"manual component file size changed while reading: {relative_path}")
    return CapturedFile(
        relative_path=relative_path,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        device=after.st_dev,
        inode=after.st_ino,
        mode=after.st_mode,
        mtime_ns=after.st_mtime_ns,
        ctime_ns=after.st_ctime_ns,
    )


def _read_regular_openat(root: Path, relative_path: str) -> CapturedFile:
    """Read below root while every parent remains bound to an open directory fd."""

    parts = PurePosixPath(relative_path).parts
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_before_path = root.lstat()
    except OSError as exc:
        raise BundleError(f"manual component root is missing or unreadable: {exc}") from exc
    if _is_link_or_reparse(root_before_path) or not stat.S_ISDIR(root_before_path.st_mode):
        raise BundleError("manual component root must be a real directory, not a link")

    directory_descriptors: list[int] = []
    directory_identities: list[tuple[int, ...]] = []
    file_descriptor: int | None = None
    try:
        try:
            root_descriptor = os.open(root, directory_flags)
        except OSError as exc:
            raise BundleError(f"cannot securely open manual component root: {exc}") from exc
        directory_descriptors.append(root_descriptor)
        root_opened = os.fstat(root_descriptor)
        if (
            not stat.S_ISDIR(root_opened.st_mode)
            or _identity(root_before_path, include_size=False)
            != _identity(root_opened, include_size=False)
        ):
            raise BundleError("manual component root changed while opening")
        directory_identities.append(_identity(root_opened, include_size=False))

        parent_descriptor = root_descriptor
        for part in parts[:-1]:
            try:
                child_descriptor = os.open(
                    part, directory_flags, dir_fd=parent_descriptor
                )
            except OSError as exc:
                raise BundleError(
                    "cannot securely open manual component parent for "
                    f"{relative_path}: {exc}"
                ) from exc
            directory_descriptors.append(child_descriptor)
            child_info = os.fstat(child_descriptor)
            if not stat.S_ISDIR(child_info.st_mode):
                raise BundleError(
                    f"manual component path has a non-directory parent: {relative_path}"
                )
            directory_identities.append(_identity(child_info, include_size=False))
            parent_descriptor = child_descriptor

        try:
            file_descriptor = os.open(
                parts[-1], file_flags, dir_fd=parent_descriptor
            )
        except OSError as exc:
            raise BundleError(
                "cannot securely open manual component file without following links: "
                f"{relative_path}: {exc}"
            ) from exc
        capture = _capture_open_file(file_descriptor, relative_path)
        try:
            path_after = os.stat(
                parts[-1], dir_fd=parent_descriptor, follow_symlinks=False
            )
        except OSError as exc:
            raise BundleError(
                f"manual component file disappeared while reading: {relative_path}: {exc}"
            ) from exc
        if _is_link_or_reparse(path_after) or _identity(
            path_after, include_size=True
        ) != _identity(os.fstat(file_descriptor), include_size=True):
            raise BundleError(f"manual component path changed while reading: {relative_path}")
        for descriptor, expected in zip(
            directory_descriptors, directory_identities
        ):
            if _identity(os.fstat(descriptor), include_size=False) != expected:
                raise BundleError(
                    f"manual component parent changed while reading: {relative_path}"
                )
        try:
            root_after_path = root.lstat()
        except OSError as exc:
            raise BundleError(f"manual component root disappeared while reading: {exc}") from exc
        if _is_link_or_reparse(root_after_path) or _identity(
            root_after_path, include_size=False
        ) != directory_identities[0]:
            raise BundleError("manual component root changed while reading")
        return capture
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for descriptor in reversed(directory_descriptors):
            os.close(descriptor)


def _assert_safe_components_fallback(
    root: Path, relative_path: str
) -> tuple[Path, tuple[tuple[Path, tuple[int, ...]], ...]]:
    """Conservative fallback for platforms without secure dirfd traversal."""

    candidate = root
    observed: list[tuple[Path, tuple[int, ...]]] = []
    parts = PurePosixPath(relative_path).parts
    for index, part in enumerate(parts):
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise BundleError(
                f"manual component file is missing or unreadable: {relative_path}: {exc}"
            ) from exc
        if _is_link_or_reparse(info):
            raise BundleError(f"manual component contains a link: {relative_path}")
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise BundleError(
                f"manual component path has a non-directory parent: {relative_path}"
            )
        observed.append((candidate, _identity(info, include_size=True)))
    return candidate, tuple(observed)


def _read_regular_fallback(root: Path, relative_path: str) -> CapturedFile:
    root_before = _directory_identity(root, "manual component root")
    path, observed_components = _assert_safe_components_fallback(root, relative_path)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BundleError(
            f"cannot conservatively open manual component file: {relative_path}: {exc}"
        ) from exc
    try:
        capture = _capture_open_file(descriptor, relative_path)
    finally:
        os.close(descriptor)
    for component, expected in observed_components:
        try:
            current = component.lstat()
        except OSError as exc:
            raise BundleError(
                f"manual component path disappeared while reading: {relative_path}: {exc}"
            ) from exc
        if _is_link_or_reparse(current) or _identity(
            current, include_size=True
        ) != expected:
            raise BundleError(f"manual component path changed while reading: {relative_path}")
    if _directory_identity(root, "manual component root") != root_before:
        raise BundleError("manual component root changed while reading")
    return capture


def _read_regular_no_follow(root: Path, relative_path: str) -> CapturedFile:
    relative_path = safe_relative_path(relative_path, label="component file path")
    if _secure_openat_available():
        return _read_regular_openat(root, relative_path)
    return _read_regular_fallback(root, relative_path)


def _same_capture(first: CapturedFile, second: CapturedFile) -> bool:
    return (
        first.sha256 == second.sha256
        and first.size_bytes == second.size_bytes
        and first.device == second.device
        and first.inode == second.inode
        and first.mode == second.mode
        and first.mtime_ns == second.mtime_ns
        and first.ctime_ns == second.ctime_ns
    )


def read_skill_file(skill_root: Path, relative_path: str) -> CapturedFile:
    """Read one fixed Skill file with the same no-follow and drift checks."""

    root = Path(skill_root).absolute()
    _directory_identity(root, "Skill root")
    return _read_regular_no_follow(root, relative_path)


def captures_match(first: CapturedFile, second: CapturedFile) -> bool:
    """Compare content and on-disk identity from two checked reads."""

    return _same_capture(first, second)


def _record_inventory_path(
    paths: list[str], folded: dict[str, str], relative: str
) -> None:
    folded_path = relative.casefold()
    previous = folded.get(folded_path)
    if previous is not None:
        raise BundleError(
            "manual component contains case-fold-colliding paths: "
            f"{previous!r} and {relative!r}"
        )
    folded[folded_path] = relative
    paths.append(relative)


def _walk_component_files_openat(root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    folded: dict[str, str] = {}
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        root_path_info = root.lstat()
    except OSError as exc:
        raise BundleError(f"cannot inspect manual component root: {exc}") from exc
    if _is_link_or_reparse(root_path_info) or not stat.S_ISDIR(root_path_info.st_mode):
        raise BundleError("manual component root must be a real directory, not a link")
    try:
        root_descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise BundleError(f"cannot securely enumerate manual component: {exc}") from exc
    try:
        root_opened = os.fstat(root_descriptor)
        if _identity(root_path_info, include_size=False) != _identity(
            root_opened, include_size=False
        ):
            raise BundleError("manual component root changed while opening")

        def visit(
            directory_descriptor: int,
            prefix: PurePosixPath,
            expected_identity: tuple[int, ...],
        ) -> None:
            try:
                names = sorted(os.listdir(directory_descriptor))
            except OSError as exc:
                raise BundleError(f"cannot enumerate manual component: {exc}") from exc
            for name in names:
                relative = (prefix / name).as_posix()
                safe_relative_path(relative, label="manual component inventory path")
                try:
                    info = os.stat(
                        name,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise BundleError(
                        f"cannot inspect manual component entry {relative}: {exc}"
                    ) from exc
                if _is_link_or_reparse(info):
                    raise BundleError(
                        f"manual component contains a link: {relative}"
                    )
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child_descriptor = os.open(
                            name,
                            directory_flags,
                            dir_fd=directory_descriptor,
                        )
                    except OSError as exc:
                        raise BundleError(
                            f"cannot securely open manual component directory {relative}: {exc}"
                        ) from exc
                    try:
                        child_info = os.fstat(child_descriptor)
                        child_identity = _identity(child_info, include_size=False)
                        if child_identity != _identity(info, include_size=False):
                            raise BundleError(
                                f"manual component directory changed while opening: {relative}"
                            )
                        visit(
                            child_descriptor,
                            PurePosixPath(relative),
                            child_identity,
                        )
                        current = os.stat(
                            name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                        if _is_link_or_reparse(current) or _identity(
                            current, include_size=False
                        ) != child_identity:
                            raise BundleError(
                                f"manual component directory changed while enumerating: {relative}"
                            )
                    finally:
                        os.close(child_descriptor)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise BundleError(
                        f"manual component contains a non-regular entry: {relative}"
                    )
                _record_inventory_path(paths, folded, relative)
            if _identity(
                os.fstat(directory_descriptor), include_size=False
            ) != expected_identity:
                raise BundleError(
                    "manual component directory changed while enumerating: "
                    f"{prefix.as_posix() or '.'}"
                )

        visit(root_descriptor, PurePosixPath(), _identity(root_opened, include_size=False))
        root_after = root.lstat()
        if _is_link_or_reparse(root_after) or _identity(
            root_after, include_size=False
        ) != _identity(root_opened, include_size=False):
            raise BundleError("manual component root changed while enumerating")
    finally:
        os.close(root_descriptor)
    return tuple(sorted(paths))


def _walk_component_files_fallback(root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    folded: dict[str, str] = {}

    def visit(directory: Path, prefix: PurePosixPath) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise BundleError(f"cannot enumerate manual component: {exc}") from exc
        for entry in entries:
            relative = (prefix / entry.name).as_posix()
            safe_relative_path(relative, label="manual component inventory path")
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise BundleError(
                    f"cannot inspect manual component entry {relative}: {exc}"
                ) from exc
            if _is_link_or_reparse(info):
                raise BundleError(f"manual component contains a link: {relative}")
            if stat.S_ISDIR(info.st_mode):
                visit(Path(entry.path), PurePosixPath(relative))
                continue
            if not stat.S_ISREG(info.st_mode):
                raise BundleError(
                    f"manual component contains a non-regular entry: {relative}"
                )
            _record_inventory_path(paths, folded, relative)

    visit(root, PurePosixPath())
    return tuple(sorted(paths))


def _walk_component_files(root: Path) -> tuple[str, ...]:
    if _secure_openat_available():
        return _walk_component_files_openat(root)
    return _walk_component_files_fallback(root)


def tree_digest(records: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    for index, record in enumerate(records):
        path = safe_relative_path(record.get("path"), label=f"files[{index}].path")
        sha256 = _require_sha256(
            record.get("sha256"), f"files[{index}].sha256"
        )
        size_bytes = _require_int(
            record.get("size_bytes"), f"files[{index}].size_bytes"
        )
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\0")
        digest.update(str(size_bytes).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise BundleError(f"manifest.schema must be {MANIFEST_SCHEMA!r}")
    component = _require_mapping(manifest.get("component"), "manifest.component")
    if component.get("id") != "siril-manual":
        raise BundleError("manifest.component.id must be 'siril-manual'")
    if component.get("version") != MANUAL_VERSION:
        raise BundleError(f"manifest.component.version must be {MANUAL_VERSION}")

    manual = _require_mapping(manifest.get("manual"), "manifest.manual")
    if manual.get("version") != MANUAL_VERSION:
        raise BundleError(f"manifest.manual.version must be {MANUAL_VERSION}")
    if manual.get("commit") != MANUAL_COMMIT:
        raise BundleError(f"manifest.manual.commit must be {MANUAL_COMMIT}")
    if str(manual.get("rtd_build_id")) != RTD_BUILD_ID:
        raise BundleError(f"manifest.manual.rtd_build_id must be {RTD_BUILD_ID}")
    expected_manual_fields = {
        "commit_time": EXPECTED_COMMIT_TIME,
        "source_url": EXPECTED_SOURCE_URL,
        "source_archive_url": EXPECTED_SOURCE_ARCHIVE_URL,
        "source_archive_sha256": EXPECTED_SOURCE_ARCHIVE_SHA256,
        "rtd_url": EXPECTED_RTD_URL,
    }
    for key, expected in expected_manual_fields.items():
        if manual.get(key) != expected:
            raise BundleError(f"manifest.manual.{key} is not the trusted pinned value")

    license_metadata = _require_mapping(manifest.get("license"), "manifest.license")
    if set(license_metadata) != {"concluded", "legal_review", "entries"}:
        raise BundleError(
            "manifest.license must contain concluded, legal_review, and entries"
        )
    if license_metadata.get("concluded") != "NOASSERTION":
        raise BundleError("manifest.license.concluded must remain 'NOASSERTION'")
    if license_metadata.get("legal_review") != "required":
        raise BundleError("manifest.license.legal_review must remain 'required'")
    license_entries = _require_sequence(
        license_metadata.get("entries"), "manifest.license.entries"
    )
    if len(license_entries) != 2:
        raise BundleError("manifest.license.entries must contain exactly two entries")
    gpl_entry = _require_mapping(
        license_entries[0], "manifest.license.entries[0]"
    )
    if set(gpl_entry) != {"id", "path", "sha256", "scope"}:
        raise BundleError("manifest GPL license entry has unexpected fields")
    if gpl_entry.get("id") != "GPL-3.0-only":
        raise BundleError("manifest GPL license id must be 'GPL-3.0-only'")
    if gpl_entry.get("path") != "LICENSE.GPL-3.0.txt":
        raise BundleError("manifest GPL license path is invalid")
    if gpl_entry.get("sha256") != EXPECTED_GPL_LICENSE_SHA256:
        raise BundleError("manifest GPL license hash is not the trusted pinned value")
    if gpl_entry.get("scope") != (
        "siril-doc upstream material except separately attributed content"
    ):
        raise BundleError("manifest GPL license scope is invalid")

    gfdl_entry = _require_mapping(
        license_entries[1], "manifest.license.entries[1]"
    )
    if set(gfdl_entry) != {
        "id",
        "inferred_candidate_spdx",
        "path",
        "sha256",
        "source_url",
        "applies_to",
    }:
        raise BundleError("manifest GFDL candidate license entry has unexpected fields")
    if gfdl_entry.get("id") != GFDL_LICENSE_ID:
        raise BundleError("manifest GFDL candidate license id is invalid")
    if gfdl_entry.get("inferred_candidate_spdx") != GFDL_CANDIDATE_SPDX:
        raise BundleError("manifest GFDL candidate SPDX id is invalid")
    if gfdl_entry.get("path") != "LICENSE.GFDL-1.2.txt":
        raise BundleError("manifest GFDL candidate license path is invalid")
    if gfdl_entry.get("sha256") != EXPECTED_GFDL_LICENSE_SHA256:
        raise BundleError("manifest GFDL candidate hash is not the trusted pinned value")
    if gfdl_entry.get("source_url") != GFDL_SOURCE_URL:
        raise BundleError("manifest GFDL candidate source URL is invalid")
    applies_to = _require_sequence(
        gfdl_entry.get("applies_to"), "manifest GFDL candidate applies_to"
    )
    if tuple(applies_to) != GFDL_APPLIES_TO:
        raise BundleError("manifest GFDL candidate applies_to is invalid")

    files_metadata = _require_mapping(manifest.get("files"), "manifest.files")
    if files_metadata.get("path") != FILES_NAME:
        raise BundleError(f"manifest.files.path must be {FILES_NAME!r}")
    if files_metadata.get("sha256") != EXPECTED_FILES_SHA256:
        raise BundleError("manifest.files.sha256 is not the trusted pinned value")
    _require_int(files_metadata.get("size_bytes"), "manifest.files.size_bytes")

    tree = _require_mapping(manifest.get("tree"), "manifest.tree")
    if tree.get("algorithm") != TREE_ALGORITHM:
        raise BundleError(f"manifest.tree.algorithm must be {TREE_ALGORITHM!r}")
    if tree.get("sha256") != EXPECTED_TREE_SHA256:
        raise BundleError("manifest.tree.sha256 is not the trusted pinned value")
    _require_int(tree.get("file_count"), "manifest.tree.file_count")

    indexes = _require_mapping(manifest.get("indexes"), "manifest.indexes")
    if set(indexes) != set(EXPECTED_INDEX_PATHS):
        raise BundleError(
            "manifest.indexes must name the fixed catalog, command, section, "
            "alias, and image-selection files"
        )
    for name, expected_path in EXPECTED_INDEX_PATHS.items():
        metadata = _require_mapping(indexes.get(name), f"manifest.indexes.{name}")
        if metadata.get("path") != expected_path:
            raise BundleError(
                f"manifest.indexes.{name}.path must be {expected_path!r}"
            )
        _require_sha256(
            metadata.get("sha256"), f"manifest.indexes.{name}.sha256"
        )
        _require_int(
            metadata.get("size_bytes"), f"manifest.indexes.{name}.size_bytes"
        )
    coverage = _require_mapping(manifest.get("coverage"), "manifest.coverage")
    if coverage.get("rst") != "complete_at_pinned_commit":
        raise BundleError(
            "manifest.coverage.rst must be 'complete_at_pinned_commit'"
        )
    if coverage.get("image") != "selected":
        raise BundleError("manifest.coverage.image must be 'selected'")
    counts = _require_mapping(manifest.get("counts"), "manifest.counts")
    expected_count_keys = {
        "rst_files",
        "include_dependencies",
        "commands",
        "sections",
        "selected_images",
        "component_files",
    }
    if set(counts) != expected_count_keys:
        raise BundleError(
            "manifest.counts must contain exactly the fixed corpus counters"
        )
    for key in sorted(expected_count_keys):
        _require_int(counts.get(key), f"manifest.counts.{key}")
    build = _require_mapping(manifest.get("build"), "manifest.build")
    _require_string(build.get("builder_version"), "manifest.build.builder_version")


def _validate_record_path(path: str) -> None:
    if path in {MANIFEST_NAME, FILES_NAME}:
        raise BundleError(f"files.json must not include itself or manifest: {path}")
    if path in EXPECTED_COMPONENT_FILES:
        return
    if not path.startswith("source/doc/"):
        raise BundleError(f"manual component file is outside its fixed paths: {path}")


def verify_bundle(skill_root: Path | None = None) -> BundleSnapshot:
    """Hash and validate every file in the fixed manual component."""

    if skill_root is None:
        skill_root = Path(__file__).resolve().parents[1]
    skill_root = Path(skill_root).absolute()
    _directory_identity(skill_root, "Skill root")
    transaction = skill_root / TRANSACTION_RELATIVE_PATH
    try:
        transaction_info = transaction.lstat()
    except FileNotFoundError:
        transaction_info = None
    except OSError as exc:
        raise BundleError(f"cannot inspect manual transaction journal: {exc}") from exc
    if transaction_info is not None:
        raise BundleError(
            "manual transaction journal exists; refuse a possibly incomplete bundle"
        )

    references = skill_root / "references"
    _directory_identity(references, "references directory")
    component_root = skill_root / COMPONENT_RELATIVE_PATH
    root_identity = _directory_identity(component_root, "manual component")

    manifest_capture = _read_regular_no_follow(component_root, MANIFEST_NAME)
    if manifest_capture.sha256 != EXPECTED_MANIFEST_SHA256:
        raise BundleError("manifest hash is not the trusted pinned value")
    manifest = _require_mapping(
        strict_json_bytes(manifest_capture.data, document=MANIFEST_NAME), MANIFEST_NAME
    )
    _validate_manifest(manifest)

    files_capture = _read_regular_no_follow(component_root, FILES_NAME)
    if files_capture.sha256 != EXPECTED_FILES_SHA256:
        raise BundleError("files.json hash is not the trusted pinned value")
    files_metadata = _require_mapping(manifest.get("files"), "manifest.files")
    if files_capture.sha256 != files_metadata.get("sha256"):
        raise BundleError("files.json hash does not match manifest")
    if files_capture.size_bytes != files_metadata.get("size_bytes"):
        raise BundleError("files.json size does not match manifest")
    files_document = _require_mapping(
        strict_json_bytes(files_capture.data, document=FILES_NAME), FILES_NAME
    )
    if files_document.get("schema") != FILES_SCHEMA:
        raise BundleError(f"files.json schema must be {FILES_SCHEMA!r}")
    raw_records = _require_sequence(files_document.get("files"), "files.json.files")

    records: list[Mapping[str, Any]] = []
    record_paths: list[str] = []
    casefolded: dict[str, str] = {}
    for index, raw_record in enumerate(raw_records):
        record = _require_mapping(raw_record, f"files[{index}]")
        path = safe_relative_path(record.get("path"), label=f"files[{index}].path")
        _validate_record_path(path)
        if record_paths and path <= record_paths[-1]:
            raise BundleError("files.json records must be strictly POSIX-path sorted")
        folded = path.casefold()
        if folded in casefolded:
            raise BundleError(
                "files.json contains case-fold-colliding paths: "
                f"{casefolded[folded]!r} and {path!r}"
            )
        casefolded[folded] = path
        record_paths.append(path)
        _require_string(record.get("role"), f"files[{index}].role")
        _require_sha256(record.get("sha256"), f"files[{index}].sha256")
        _require_int(record.get("size_bytes"), f"files[{index}].size_bytes")
        has_upstream_path = "upstream_path" in record
        has_upstream_blob = "upstream_blob" in record
        if has_upstream_path != has_upstream_blob:
            raise BundleError(
                f"files[{index}] must bind upstream_path and upstream_blob together"
            )
        if has_upstream_path:
            safe_relative_path(
                record.get("upstream_path"), label=f"files[{index}].upstream_path"
            )
        if has_upstream_blob:
            _require_git_blob(
                record.get("upstream_blob"), f"files[{index}].upstream_blob"
            )
        records.append(record)

    expected_required = EXPECTED_COMPONENT_FILES
    missing_required = sorted(expected_required.difference(record_paths))
    if missing_required:
        raise BundleError(
            "files.json is missing fixed component files: " + ", ".join(missing_required)
        )
    actual_paths = _walk_component_files(component_root)
    expected_paths = tuple(sorted((MANIFEST_NAME, FILES_NAME, *record_paths)))
    if actual_paths != expected_paths:
        missing = sorted(set(expected_paths).difference(actual_paths))
        extra = sorted(set(actual_paths).difference(expected_paths))
        raise BundleError(
            "manual component inventory does not match files.json; "
            f"missing={missing}, extra={extra}"
        )

    captures: dict[str, CapturedFile] = {}
    for index, record in enumerate(records):
        path = record_paths[index]
        capture = _read_regular_no_follow(component_root, path)
        if capture.sha256 != record.get("sha256"):
            raise BundleError(f"manual component hash mismatch: {path}")
        if capture.size_bytes != record.get("size_bytes"):
            raise BundleError(f"manual component size mismatch: {path}")
        if "upstream_blob" in record and _git_blob_sha1(capture.data) != record.get(
            "upstream_blob"
        ):
            raise BundleError(f"manual component Git blob mismatch: {path}")
        captures[path] = capture

    computed_tree = tree_digest(records)
    if computed_tree != EXPECTED_TREE_SHA256:
        raise BundleError("manual component tree is not the trusted pinned value")
    tree = _require_mapping(manifest.get("tree"), "manifest.tree")
    if computed_tree != tree.get("sha256"):
        raise BundleError("manual component tree hash does not match manifest")
    if len(records) != tree.get("file_count"):
        raise BundleError("manual component file count does not match manifest")
    counts = _require_mapping(manifest.get("counts"), "manifest.counts")
    if len(records) != counts.get("component_files"):
        raise BundleError("manual component file count does not match manifest.counts")
    license_metadata = _require_mapping(manifest.get("license"), "manifest.license")
    license_entries = _require_sequence(
        license_metadata.get("entries"), "manifest.license.entries"
    )
    for index, raw_entry in enumerate(license_entries):
        entry = _require_mapping(raw_entry, f"manifest.license.entries[{index}]")
        path = _require_string(
            entry.get("path"), f"manifest.license.entries[{index}].path"
        )
        if captures[path].sha256 != entry.get("sha256"):
            raise BundleError(
                f"manual component license hash does not match manifest: {path}"
            )
    indexes = _require_mapping(manifest.get("indexes"), "manifest.indexes")
    for name, expected_path in EXPECTED_INDEX_PATHS.items():
        metadata = _require_mapping(indexes.get(name), f"manifest.indexes.{name}")
        capture = captures[expected_path]
        if capture.sha256 != metadata.get("sha256"):
            raise BundleError(f"manual {name} index hash does not match manifest")
        if capture.size_bytes != metadata.get("size_bytes"):
            raise BundleError(f"manual {name} index size does not match manifest")

    if _directory_identity(component_root, "manual component") != root_identity:
        raise BundleError("manual component changed during verification")
    current_manifest = _read_regular_no_follow(component_root, MANIFEST_NAME)
    current_files = _read_regular_no_follow(component_root, FILES_NAME)
    if not _same_capture(manifest_capture, current_manifest):
        raise BundleError("manifest changed during verification")
    if not _same_capture(files_capture, current_files):
        raise BundleError("files.json changed during verification")

    fingerprint_payload = (
        MANIFEST_SCHEMA.encode("ascii")
        + b"\0"
        + manifest_capture.sha256.encode("ascii")
        + b"\0"
        + files_capture.sha256.encode("ascii")
        + b"\0"
        + computed_tree.encode("ascii")
        + b"\n"
    )
    return BundleSnapshot(
        skill_root=skill_root,
        component_root=component_root,
        manifest=MappingProxyType(dict(manifest)),
        files_document=MappingProxyType(dict(files_document)),
        records=tuple(MappingProxyType(dict(item)) for item in records),
        captures=MappingProxyType(captures),
        manifest_capture=manifest_capture,
        files_capture=files_capture,
        tree_sha256=computed_tree,
        fingerprint=hashlib.sha256(fingerprint_payload).hexdigest(),
        root_identity=root_identity,
    )


def component_release_paths(snapshot: BundleSnapshot) -> tuple[str, ...]:
    """Return the verified component closure relative to the Skill root."""

    prefix = COMPONENT_RELATIVE_PATH + "/"
    return tuple(
        prefix + relative
        for relative in sorted(
            (MANIFEST_NAME, FILES_NAME, *(record["path"] for record in snapshot.records))
        )
    )


def bundle_metadata(snapshot: BundleSnapshot) -> dict[str, Any]:
    """Return the immutable manual identity shared by sessions and queries."""

    manual = snapshot.manual
    return {
        "version": manual["version"],
        "commit": manual["commit"],
        "source_url": manual["source_url"],
        "rtd_build_id": str(manual["rtd_build_id"]),
        "bundle_fingerprint": snapshot.fingerprint,
        "manifest_sha256": snapshot.manifest_capture.sha256,
        "files_sha256": snapshot.files_capture.sha256,
        "tree_sha256": snapshot.tree_sha256,
        "upstream_reverified_now": False,
    }


def bundle_verification_document(snapshot: BundleSnapshot) -> dict[str, Any]:
    """Build the deterministic query envelope used as session Bundle evidence."""

    counts = _require_mapping(snapshot.manifest.get("counts"), "manifest.counts")
    return {
        "schema": QUERY_SCHEMA,
        "status": "ok",
        "manual": bundle_metadata(snapshot),
        "mode": "verify_bundle",
        "result": {
            "status": "verified",
            "component_files": counts["component_files"],
            "rst_files": counts["rst_files"],
            "commands": counts["commands"],
            "sections": counts["sections"],
            "selected_images": counts["selected_images"],
        },
    }


__all__ = [
    "BundleError",
    "BundleSnapshot",
    "CapturedFile",
    "COMPONENT_RELATIVE_PATH",
    "EXPECTED_FILES_SHA256",
    "EXPECTED_MANIFEST_SHA256",
    "EXPECTED_SOURCE_ARCHIVE_SHA256",
    "EXPECTED_TREE_SHA256",
    "EXPECTED_INDEX_PATHS",
    "FILES_NAME",
    "FILES_SCHEMA",
    "MANUAL_COMMIT",
    "MANUAL_VERSION",
    "MANIFEST_NAME",
    "MANIFEST_SCHEMA",
    "QUERY_SCHEMA",
    "RTD_BUILD_ID",
    "TREE_ALGORITHM",
    "bundle_metadata",
    "bundle_verification_document",
    "captures_match",
    "component_release_paths",
    "read_skill_file",
    "safe_relative_path",
    "strict_json_bytes",
    "strict_json_lines",
    "tree_digest",
    "verify_bundle",
]
