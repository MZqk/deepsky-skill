#!/usr/bin/env python3
"""Shared contract, deterministic I/O, fingerprint, and path primitives.

This module is the dependency root for the flat starun-siril runtime modules.
It must stay free of tool discovery, subprocess execution, image decoding, and
workflow orchestration.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any, Sequence


SCRIPTS_ROOT = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPTS_ROOT.parent

CONTRACT_VERSION = "1"
SCHEMA_PREFIX = "starun-siril"
LOG_DIAGNOSTICS_POLICY = "fail_closed_v1"
NETWORK_POLICY = "offline_default_explicit_gaia_v1"
EXECUTION_POLICY = {
    "log_diagnostics": LOG_DIAGNOSTICS_POLICY,
    "network": NETWORK_POLICY,
}
SIRIL_MINIMUM = (1, 4, 4)
SIRIL_OBSOLETE = (1, 5, 0)
DEFAULT_TIMEOUT = 1800
INPUT_EXTENSIONS = frozenset({".fit", ".fits", ".fts", ".xisf", ".tif", ".tiff"})
SESSION_DIRECTORIES = (
    "scripts",
    "runs",
    "reviews",
    "artifacts",
    "previews",
    "reports",
    "reports/manual-evidence",
    "logs",
    "runtime/decode-validation",
    "runtime/siril-configs",
    "outputs",
)
RUN_ID_PATTERN = re.compile(r"[0-9]{3}-[a-z0-9][a-z0-9-]{0,63}\Z")
HASH_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
LIMITATION_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,119}\Z")
IMAGE_SUFFIXES = frozenset(
    {".fit", ".fits", ".fts", ".xisf", ".tif", ".tiff", ".jpg", ".jpeg", ".png", ".svg"}
)
DISPLAY_IMAGE_SUFFIXES = frozenset({".tif", ".tiff", ".jpg", ".jpeg", ".png"})


class ContractError(RuntimeError):
    """Stable public error with a machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        missing_dependencies: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.missing_dependencies = list(missing_dependencies)


def classify_siril_network(
    script_text: str,
    *,
    protocol: str,
    session_offline: bool,
) -> dict[str, Any]:
    """Return the frozen effective network mode for one validated SSF."""

    catalogues: set[str] = set()
    for raw_line in script_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            raise ContractError(
                "unsafe_siril_script",
                f"Cannot parse Siril command: {line[:120]}",
            ) from exc
        if not tokens or tokens[0].lower() not in {"pcc", "spcc"}:
            continue
        values = [
            token.split("=", 1)[1].lower()
            for token in tokens[1:]
            if token.startswith("-catalog=")
        ]
        if len(values) != 1 or values[0] not in {"gaia", "localgaia"}:
            raise ContractError(
                "unsafe_siril_script",
                "pcc/spcc requires exactly one -catalog=gaia|localgaia option",
            )
        catalogues.add(values[0])

    if catalogues and protocol != "color.calibrate":
        raise ContractError(
            "unsafe_siril_script",
            "Remote or local catalogue access is restricted to color.calibrate",
        )
    if len(catalogues) > 1:
        raise ContractError(
            "unsafe_siril_script",
            "One color calibration script cannot mix local and remote Gaia catalogues",
        )
    remote_gaia = "gaia" in catalogues
    if session_offline and remote_gaia:
        raise ContractError(
            "network_forbidden",
            "Offline session forbids the explicitly requested remote Gaia catalogue",
        )
    if session_offline:
        reason = "session_offline"
    elif remote_gaia:
        reason = "remote_gaia_explicit"
    elif "localgaia" in catalogues:
        reason = "local_gaia"
    else:
        reason = "protocol_offline"
    return {
        "policy": NETWORK_POLICY,
        "session_offline": bool(session_offline),
        "effective_offline": not remote_gaia,
        "reason": reason,
        "catalogues": sorted(catalogues),
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ContractError("invalid_json_value", "Value is not canonical JSON") from exc


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ContractError("artifact_missing", f"Cannot hash file: {path}") from exc
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ContractError("unsafe_path", f"Refusing to replace a symlink: {path}")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except FileExistsError as exc:
        raise ContractError("write_conflict", f"Temporary write conflict: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_bytes(path, canonical_json(value) + b"\n")


def _fsync_directory(path: Path) -> None:
    """Persist a completed rename where the host filesystem supports it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ContractError("artifact_missing", f"JSON file is missing or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("artifact_invalid", f"JSON file is invalid: {path}") from exc
    if not isinstance(value, dict):
        raise ContractError("artifact_invalid", f"JSON root must be an object: {path}")
    return value


def fingerprint(path: Path, *, role: str | None = None, include_path: bool = True) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ContractError("artifact_missing", f"File is missing or unsafe: {path}")
    resolved = path.resolve()
    record: dict[str, Any] = {
        "sha256": sha256_file(resolved),
        "size": resolved.stat().st_size,
    }
    if include_path:
        record["path"] = str(resolved)
    if role:
        record["role"] = role
    return record


def fingerprint_matches(record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return False
    path = Path(record["path"])
    return bool(
        path.is_file()
        and not path.is_symlink()
        and path.stat().st_size == record.get("size")
        and sha256_file(path) == record.get("sha256")
    )


def fingerprint_resource(path: Path, *, kind: str, include_path: bool = True) -> dict[str, Any]:
    """Fingerprint one installed executable/file or a symlink-free directory tree."""
    resolved = path.resolve(strict=False)
    if kind in {"executable", "file"}:
        record = fingerprint(resolved, include_path=include_path)
        if kind == "executable" and not os.access(resolved, os.X_OK):
            raise ContractError("artifact_invalid", f"Installed executable is not runnable: {resolved}")
        return {**record, "kind": kind}
    if kind != "directory" or path.is_symlink() or not resolved.is_dir():
        raise ContractError("artifact_invalid", f"Installed directory is missing or unsafe: {resolved}")
    digest = hashlib.sha256()
    total_size = 0
    file_count = 0
    for candidate in sorted(resolved.rglob("*"), key=lambda item: item.as_posix()):
        if candidate.is_symlink():
            raise ContractError("artifact_invalid", f"Installed directory contains a symlink: {candidate}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ContractError("artifact_invalid", f"Installed directory contains an unsafe entry: {candidate}")
        relative = candidate.relative_to(resolved).as_posix()
        size = candidate.stat().st_size
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(sha256_file(candidate)))
        total_size += size
        file_count += 1
    if file_count == 0:
        raise ContractError("artifact_invalid", f"Installed directory is empty: {resolved}")
    record: dict[str, Any] = {
        "kind": "directory",
        "sha256": digest.hexdigest(),
        "size": total_size,
        "file_count": file_count,
    }
    if include_path:
        record["path"] = str(resolved)
    return record


def resource_fingerprint_matches(record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return False
    try:
        current = fingerprint_resource(
            Path(record["path"]),
            kind=str(record.get("kind", "file")),
        )
    except ContractError:
        return False
    return all(current.get(key) == record.get(key) for key in ("kind", "sha256", "size"))


def _absolute(path_value: str | Path) -> Path:
    return Path(path_value).expanduser().absolute()


def safe_session_root(
    path_value: str | Path,
    *,
    create: bool = False,
    require_empty: bool = False,
) -> Path:
    raw = _absolute(path_value)
    if raw.is_symlink():
        raise ContractError("unsafe_session", f"Session root is a symlink: {raw}")
    if create:
        raw.mkdir(parents=True, exist_ok=True)
    if not raw.is_dir():
        raise ContractError("unsafe_session", f"Session root is missing: {raw}")
    root = raw.resolve()
    if require_empty and any(root.iterdir()):
        raise ContractError("session_not_empty", f"Session root must be empty: {root}")
    return root


def session_path(
    session: Path,
    path_value: str | Path,
    *,
    must_exist: bool = False,
    allowed_roots: Sequence[str] = (),
) -> Path:
    root = session.resolve()
    candidate = Path(path_value).expanduser()
    raw = (candidate if candidate.is_absolute() else root / candidate).absolute()
    try:
        raw.relative_to(root)
    except ValueError as exc:
        raise ContractError("unsafe_path", f"Path escaped the session: {raw}") from exc
    cursor = raw
    while cursor != root:
        if cursor.is_symlink():
            raise ContractError("unsafe_path", f"Session path contains a symlink: {raw}")
        cursor = cursor.parent
    resolved = raw.resolve(strict=must_exist)
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ContractError("unsafe_path", f"Resolved path escaped the session: {raw}") from exc
    if allowed_roots and (not relative.parts or relative.parts[0] not in set(allowed_roots)):
        raise ContractError("unsafe_path", f"Path is outside allowed session directories: {relative}")
    if must_exist and (not resolved.is_file() or resolved.is_symlink()):
        raise ContractError("artifact_missing", f"Session file is missing or unsafe: {relative}")
    return resolved


def relative_session_path(session: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(session.resolve()).as_posix()
    except ValueError as exc:
        raise ContractError("unsafe_path", f"Path escaped the session: {path}") from exc


__all__ = (
    "CONTRACT_VERSION",
    "DEFAULT_TIMEOUT",
    "DISPLAY_IMAGE_SUFFIXES",
    "EXECUTION_POLICY",
    "HASH_PATTERN",
    "IMAGE_SUFFIXES",
    "INPUT_EXTENSIONS",
    "LIMITATION_CODE_PATTERN",
    "LOG_DIAGNOSTICS_POLICY",
    "NETWORK_POLICY",
    "RUN_ID_PATTERN",
    "SCHEMA_PREFIX",
    "SESSION_DIRECTORIES",
    "SIRIL_MINIMUM",
    "SIRIL_OBSOLETE",
    "SKILL_ROOT",
    "SCRIPTS_ROOT",
    "ContractError",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "canonical_json",
    "classify_siril_network",
    "fingerprint",
    "fingerprint_matches",
    "fingerprint_resource",
    "load_json",
    "relative_session_path",
    "resource_fingerprint_matches",
    "safe_session_root",
    "session_path",
    "sha256_file",
    "stable_hash",
    "utc_now",
)
