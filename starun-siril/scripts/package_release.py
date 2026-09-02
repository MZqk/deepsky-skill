#!/usr/bin/env python3
"""Build the exact, auditable Starun-siril SkillHub release archive."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence


SKILL_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = SKILL_ROOT / "release-files.txt"
STATIC_RELEASE_FILES = (
    "LICENSE.md",
    "SKILL.md",
    "agents/openai.yaml",
    "references/background-sample-contract.schema.json",
    "references/cli-contract.md",
    "references/command-policy.json",
    "references/delivery.md",
    "references/final-selection.schema.json",
    "references/protocol-index.md",
    "references/stage-sequence.md",
    "references/protocols/background-subtract.md",
    "references/protocols/color-calibrate.md",
    "references/protocols/color-finish.md",
    "references/protocols/color-map.md",
    "references/protocols/delivery-render.md",
    "references/protocols/geometry-crop-near-black.md",
    "references/protocols/input-inspect.md",
    "references/protocols/restoration-deconvolve.md",
    "references/protocols/restoration-denoise.md",
    "references/protocols/stars-recompose.md",
    "references/protocols/stars-separate.md",
    "references/protocols/stretch.md",
    "references/quality.md",
    "references/review.schema.json",
    "references/session-contract.md",
    "references/siril-safety.md",
    "references/ssf-provenance.schema.json",
    "scripts/deep_sky_siril.py",
    "scripts/deep_sky_siril_artifacts.py",
    "scripts/deep_sky_siril_contract.py",
    "scripts/deep_sky_siril_core.py",
    "scripts/deep_sky_siril_session.py",
    "scripts/deep_sky_siril_tooling.py",
    "scripts/deep_sky_siril_validation.py",
    "scripts/siril_background_samples.py",
    "THIRD_PARTY_NOTICES.md",
    "references/manual-query.md",
    "scripts/query_siril_manual.py",
    "scripts/siril_manual_bundle.py",
)
COMPONENT_MANIFEST_PATH = "references/siril-manual/manifest.json"
COMPONENT_PREFIX = "references/siril-manual/"
COMPONENT_DIRECTIVE = f"@component {COMPONENT_MANIFEST_PATH}"
EXPECTED_ALLOWLIST_ENTRIES = (*STATIC_RELEASE_FILES, COMPONENT_DIRECTIVE)
COMPONENT_JOURNAL_PATH = "references/.siril-manual.transaction.json"
EXPECTED_COMPONENT_ID = "siril-manual"
EXPECTED_MANUAL_VERSION = "1.4.4"
EXPECTED_MANUAL_COMMIT = "1550a31d325276124fe961368477c90d49df804b"
EXPECTED_COMPONENT_LICENSE = "NOASSERTION"
EXPECTED_COMPONENT_LICENSE_FILES = (
    ("GPL-3.0-only", "LICENSE.GPL-3.0.txt"),
    (
        "LicenseRef-MuniPack-GNU-FDL-version-unspecified",
        "LICENSE.GFDL-1.2.txt",
    ),
)
EXPECTED_COMPONENT_NOTICE_PATH = "NOTICE.md"
EXPECTED_COMPONENT_MODIFICATIONS_PATH = "MODIFICATIONS.md"
EXPECTED_NAME = "starun-siril"
EXPECTED_SLUG = "starun-siril"
EXPECTED_LICENSE = "Proprietary"
EXPECTED_VERSION = "0.1.0"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_MODE = stat.S_IFREG | 0o644
RECEIPT_SCHEMA = "starun-siril.release-receipt/v2"
AUTHORIZATION_SCHEMA = "starun-siril.release-authorization/v1"
# This immutable release-line gate cannot be opened by an unsigned JSON file.
# A future version may change it only after independent confirmation that the
# target channel supports aggregate, path-scoped licensing and after a trusted
# authorization mechanism is selected.
PLATFORM_MIXED_LICENSE_SUPPORT_CONFIRMED = False
FRONTMATTER_PATTERN = re.compile(
    r"\A---\r?\n(?P<yaml>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)
YAML_KEY_PATTERN = re.compile(
    r"^(?P<indent> *)(?P<key>[A-Za-z0-9_-]+):(?P<value>.*)$"
)
YAML_BLOCK_SCALAR_PATTERN = re.compile(r"^[>|](?:[+-]?[1-9]|[1-9][+-]?)?$")


class ReleaseError(RuntimeError):
    """A fail-closed release validation error."""


class ReleaseFile:
    """An allowlisted file captured as immutable release input."""

    __slots__ = ("relative_path", "data", "sha256", "size_bytes")

    def __init__(self, relative_path: str, data: bytes) -> None:
        self.relative_path = relative_path
        self.data = data
        self.sha256 = hashlib.sha256(data).hexdigest()
        self.size_bytes = len(data)


class ReleaseInputs:
    """Captured release files and verified third-party component metadata."""

    __slots__ = ("files", "components")

    def __init__(
        self,
        files: list[ReleaseFile],
        components: list[dict[str, object]],
    ) -> None:
        self.files = files
        self.components = components


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_regular_file_no_follow(path: Path) -> bytes:
    path = _absolute_without_resolving_symlinks(path)
    if os.name != "nt" and os.open in os.supports_dir_fd:
        parts = path.parts
        if not parts or not path.anchor:
            raise ReleaseError(f"release read path must be absolute: {path}")
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(path.anchor, directory_flags)
        except OSError as exc:
            raise ReleaseError(f"cannot open release path anchor: {path.anchor}: {exc}") from exc
        try:
            for part in parts[1:-1]:
                try:
                    next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                except OSError as exc:
                    raise ReleaseError(
                        f"cannot open release parent without following links: {path}: {exc}"
                    ) from exc
                os.close(directory_fd)
                directory_fd = next_fd
            try:
                descriptor = os.open(parts[-1], file_flags, dir_fd=directory_fd)
            except OSError as exc:
                raise ReleaseError(
                    f"cannot open regular file without following links: {path}: {exc}"
                ) from exc
        finally:
            os.close(directory_fd)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise ReleaseError(f"release entry is not a regular file: {path}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if identity_before != identity_after:
            raise ReleaseError(f"release entry changed while reading: {path}")
        data = b"".join(chunks)
        if len(data) != after.st_size:
            raise ReleaseError(f"release entry size changed while reading: {path}")
        return data

    # Conservative compatibility fallback for platforms without openat/dir_fd.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseError(
            f"cannot open regular file without following links: {path}: {exc}"
        ) from exc
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ReleaseError(f"release entry is not a regular file: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def _assert_no_symlink_components(root: Path, relative_path: str) -> Path:
    candidate = root
    for part in PurePosixPath(relative_path).parts:
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise ReleaseError(
                f"missing or unreadable release entry: {relative_path}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ReleaseError(
                f"release entry contains a symbolic link: {relative_path}"
            )
    resolved = candidate.resolve(strict=True)
    if not _is_within(resolved, root):
        raise ReleaseError(f"release entry escapes the skill root: {relative_path}")
    return candidate


def _parse_allowlist(data: bytes) -> tuple[str, ...]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("release-files.txt must be valid UTF-8") from exc

    entries: list[str] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        entry = raw_line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("@"):
            if entry != COMPONENT_DIRECTIVE:
                raise ReleaseError(
                    f"invalid component directive on line {line_number}: {entry!r}"
                )
            if entry in seen:
                raise ReleaseError(
                    f"duplicate allowlist entry on line {line_number}: {entry}"
                )
            seen.add(entry)
            entries.append(entry)
            continue
        pure_path = PurePosixPath(entry)
        if (
            pure_path.is_absolute()
            or "\\" in entry
            or entry != pure_path.as_posix()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
        ):
            raise ReleaseError(
                f"invalid allowlist path on line {line_number}: {entry!r}"
            )
        if entry in seen:
            raise ReleaseError(
                f"duplicate allowlist path on line {line_number}: {entry}"
            )
        seen.add(entry)
        entries.append(entry)

    if tuple(entries) != EXPECTED_ALLOWLIST_ENTRIES:
        expected = ", ".join(EXPECTED_ALLOWLIST_ENTRIES)
        raise ReleaseError(
            f"release-files.txt must contain exactly, in order: {expected}"
        )
    return tuple(entries)


def _strict_yaml_scalars(
    text: str,
    *,
    document: str,
) -> dict[tuple[str, ...], str]:
    """Parse the strict YAML mapping subset used by release metadata.

    The release identity fields are deliberately constrained to plain YAML mapping
    keys and scalar values. The scanner tracks indentation-defined mapping parents
    and rejects duplicate keys before reading any identity value. Unsupported YAML
    constructs, including quoted/explicit/flow mapping keys, fail closed instead of
    being ignored by this deliberately dependency-free parser.
    """

    values: dict[tuple[str, ...], str] = {}
    seen: dict[tuple[str, ...], set[str]] = {}
    parents: list[tuple[int, tuple[str, ...]]] = []
    block_scalar_parent_indent: int | None = None
    for line_number, line in enumerate(text.splitlines(), start=1):
        leading = line[: len(line) - len(line.lstrip(" "))]
        if "\t" in leading:
            raise ReleaseError(
                f"{document} uses a tab for indentation on line {line_number}"
            )
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        if not stripped or stripped.startswith("#"):
            continue
        if block_scalar_parent_indent is not None:
            if indent > block_scalar_parent_indent:
                continue
            block_scalar_parent_indent = None
        match = YAML_KEY_PATTERN.match(line)
        if not match:
            raise ReleaseError(
                f"{document} uses unsupported YAML key or collection "
                f"syntax on line {line_number}"
            )
        indent = len(match.group("indent"))
        while parents and indent <= parents[-1][0]:
            parents.pop()
        if indent and not parents:
            raise ReleaseError(
                f"{document} has orphaned indentation on line {line_number}"
            )
        parent = parents[-1][1] if parents else ()
        key = match.group("key")
        siblings = seen.setdefault(parent, set())
        if key in siblings:
            qualified = ".".join((*parent, key))
            raise ReleaseError(
                f"{document} has duplicate-key entry {qualified!r}"
            )
        siblings.add(key)
        path = (*parent, key)
        raw_value = match.group("value").strip()
        if not raw_value:
            parents.append((indent, path))
            continue
        if YAML_BLOCK_SCALAR_PATTERN.fullmatch(raw_value):
            block_scalar_parent_indent = indent
            continue
        if (
            len(raw_value) >= 2
            and raw_value[0] == raw_value[-1]
            and raw_value[0] in {'"', "'"}
        ):
            raw_value = raw_value[1:-1]
        values[path] = raw_value
    return values


def _validate_skill_frontmatter(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("SKILL.md must be valid UTF-8") from exc
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        raise ReleaseError("SKILL.md has missing or unterminated YAML frontmatter")
    scalars = _strict_yaml_scalars(
        match.group("yaml"), document="SKILL.md frontmatter"
    )
    required_values = {
        "name": (scalars.get(("name",)), EXPECTED_NAME),
        "license": (scalars.get(("license",)), EXPECTED_LICENSE),
        "metadata.slug": (scalars.get(("metadata", "slug")), EXPECTED_SLUG),
        "metadata.version": (
            scalars.get(("metadata", "version")),
            EXPECTED_VERSION,
        ),
    }
    for label, (actual, expected) in required_values.items():
        if actual != expected:
            raise ReleaseError(
                f"SKILL.md {label} must be {expected!r}, got {actual!r}"
            )


def _validate_agent_yaml(data: bytes) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("agents/openai.yaml must be valid UTF-8") from exc
    scalars = _strict_yaml_scalars(text, document="agents/openai.yaml")
    for key in ("display_name", "short_description", "default_prompt"):
        value = scalars.get(("interface", key))
        if not isinstance(value, str) or not value.strip():
            raise ReleaseError(
                f"agents/openai.yaml interface.{key} must be a non-empty scalar"
            )


def _strict_json_object(data: bytes, *, document: str) -> dict[str, object]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError(f"{document} must be valid UTF-8") from exc

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseError(f"{document} has duplicate JSON key {key!r}")
            result[key] = value
        return result

    def reject_nonfinite(token: str) -> object:
        raise ReleaseError(f"{document} contains non-finite JSON number {token!r}")

    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_nonfinite,
        )
    except ReleaseError:
        raise
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"{document} must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseError(f"{document} must contain a JSON object")
    return payload


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _capture_manual_component(root: Path) -> tuple[list[ReleaseFile], dict[str, object]]:
    module_name = "_deep_sky_siril_manual_bundle_release"
    module_path = Path(__file__).with_name("siril_manual_bundle.py")
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ReleaseError("cannot load Siril manual bundle verifier")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        snapshot = module.verify_bundle(root)
    except Exception as exc:
        if exc.__class__.__name__ == "BundleError":
            raise ReleaseError(f"Siril manual component is invalid: {exc}") from exc
        raise ReleaseError(f"cannot run Siril manual bundle verifier: {exc}") from exc
    finally:
        sys.modules.pop(module_name, None)

    manifest_data = snapshot.manifest_capture.data
    files_data = snapshot.files_capture.data
    captures = {
        "manifest.json": snapshot.manifest_capture,
        "files.json": snapshot.files_capture,
        **dict(snapshot.captures),
    }
    release_files: list[ReleaseFile] = []
    for release_path in module.component_release_paths(snapshot):
        component_path = release_path.removeprefix(COMPONENT_PREFIX)
        capture = captures[component_path]
        release_files.append(ReleaseFile(release_path, capture.data))

    by_component_path = {
        item.relative_path.removeprefix(COMPONENT_PREFIX): item
        for item in release_files
    }
    summary: dict[str, object] = {
        "id": EXPECTED_COMPONENT_ID,
        "version": EXPECTED_MANUAL_VERSION,
        "source_commit": EXPECTED_MANUAL_COMMIT,
        "license": EXPECTED_COMPONENT_LICENSE,
        "legal_review": "required",
        "manifest_sha256": _sha256_bytes(manifest_data),
        "files_sha256": _sha256_bytes(files_data),
        "tree_sha256": snapshot.tree_sha256,
        "license_files": [
            {
                "id": license_id,
                "path": path,
                "sha256": by_component_path[path].sha256,
            }
            for license_id, path in EXPECTED_COMPONENT_LICENSE_FILES
        ],
        "notice_sha256": by_component_path[EXPECTED_COMPONENT_NOTICE_PATH].sha256,
        "modifications_sha256": by_component_path[
            EXPECTED_COMPONENT_MODIFICATIONS_PATH
        ].sha256,
    }
    return release_files, summary


def _validate_release_inputs(
    skill_root: Path = SKILL_ROOT,
    allowlist_path: Path | None = None,
) -> ReleaseInputs:
    """Validate and capture the exact static and manifest-derived release files."""

    try:
        root = skill_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(
            f"skill root is missing or unreadable: {skill_root}: {exc}"
        ) from exc
    if not root.is_dir():
        raise ReleaseError(f"skill root is not a directory: {root}")

    manifest = allowlist_path or (root / "release-files.txt")
    if not manifest.is_absolute():
        manifest = root / manifest
    try:
        manifest_relative = manifest.relative_to(root).as_posix()
    except ValueError as exc:
        raise ReleaseError("release-files.txt must be inside the skill root") from exc
    manifest_path = _assert_no_symlink_components(root, manifest_relative)
    entries = _parse_allowlist(_read_regular_file_no_follow(manifest_path))

    release_files: list[ReleaseFile] = []
    for relative_path in entries:
        if relative_path == COMPONENT_DIRECTIVE:
            component_files, component_summary = _capture_manual_component(root)
            release_files.extend(component_files)
            continue
        source_path = _assert_no_symlink_components(root, relative_path)
        release_files.append(
            ReleaseFile(relative_path, _read_regular_file_no_follow(source_path))
        )
    skill_md = next(item for item in release_files if item.relative_path == "SKILL.md")
    _validate_skill_frontmatter(skill_md.data)
    agent_yaml = next(
        item for item in release_files if item.relative_path == "agents/openai.yaml"
    )
    _validate_agent_yaml(agent_yaml.data)
    if len({item.relative_path for item in release_files}) != len(release_files):
        raise ReleaseError("expanded release inventory contains duplicate paths")
    for item in release_files:
        current = _read_regular_file_no_follow(
            _assert_no_symlink_components(root, item.relative_path)
        )
        if current != item.data:
            raise ReleaseError(f"release input changed while validating: {item.relative_path}")
    top_notice = next(
        item for item in release_files if item.relative_path == "THIRD_PARTY_NOTICES.md"
    )
    component_summary["third_party_notices_sha256"] = top_notice.sha256
    return ReleaseInputs(release_files, [component_summary])


def validate_release_source(
    skill_root: Path = SKILL_ROOT,
    allowlist_path: Path | None = None,
) -> list[ReleaseFile]:
    """Compatibility wrapper returning the captured expanded file inventory."""

    return _validate_release_inputs(skill_root, allowlist_path).files


def skillhub_content_hash(release_files: Sequence[ReleaseFile]) -> str:
    """Match SkillHub's sorted ``path:file_sha256`` content fingerprint."""

    digest = hashlib.sha256()
    for release_file in sorted(
        release_files, key=lambda item: item.relative_path
    ):
        digest.update(
            f"{release_file.relative_path}:{release_file.sha256}\n".encode("utf-8")
        )
    return digest.hexdigest()


def _write_deterministic_zip(
    path: Path, release_files: Sequence[ReleaseFile]
) -> None:
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for release_file in sorted(
            release_files, key=lambda item: item.relative_path
        ):
            info = zipfile.ZipInfo(
                release_file.relative_path, date_time=FIXED_ZIP_TIMESTAMP
            )
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = FIXED_FILE_MODE << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, release_file.data)


def verify_release_archive(
    path: Path, release_files: Sequence[ReleaseFile]
) -> None:
    ordered = sorted(release_files, key=lambda item: item.relative_path)
    expected = [item.relative_path for item in ordered]
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names != expected or len(names) != len(set(names)):
                raise ReleaseError(
                    f"archive inventory mismatch: expected {expected}, got {names}"
                )
            for info, release_file in zip(infos, ordered):
                if info.is_dir() or info.compress_type != zipfile.ZIP_STORED:
                    raise ReleaseError(
                        f"archive entry is not a stored regular file: {info.filename}"
                    )
                if info.date_time != FIXED_ZIP_TIMESTAMP:
                    raise ReleaseError(
                        f"archive entry timestamp is not deterministic: {info.filename}"
                    )
                archived_mode = (info.external_attr >> 16) & 0o177777
                if archived_mode != FIXED_FILE_MODE:
                    raise ReleaseError(
                        f"archive entry mode is not deterministic: {info.filename}"
                    )
                data = archive.read(info)
                if hashlib.sha256(data).hexdigest() != release_file.sha256:
                    raise ReleaseError(
                        f"archive entry hash mismatch: {info.filename}"
                    )
    except zipfile.BadZipFile as exc:
        raise ReleaseError(f"invalid release archive: {path}: {exc}") from exc


def sanitized_child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the minimal non-secret environment permitted for local release checks."""

    inherited = os.environ if source is None else source
    exact_keys = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "TMPDIR",
        "TEMP",
        "TMP",
        "TZ",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
    }
    environment = {
        key: value
        for key, value in inherited.items()
        if key in exact_keys or key.startswith("XDG_")
    }
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "LC_MESSAGES": "C",
            "PIP_NO_INDEX": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONHOME": "",
            "__PYVENV_LAUNCHER__": "",
        }
    )
    return environment


def _run_command(
    runner: CommandRunner,
    command: list[str],
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            env=sanitized_child_env(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseError(f"failed to run {command[0]}: {exc}") from exc


def _resolve_skillhub_command(command: str | None) -> str | None:
    if command is not None:
        return command or None
    return shutil.which("skillhub")


def _skillhub_version(
    command: str,
    runner: CommandRunner = subprocess.run,
) -> str:
    completed = _run_command(runner, [command, "--version"])
    version = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not version:
        raise ReleaseError(f"skillhub --version failed: {version or 'no output'}")
    return version


def run_skillhub_preflight(
    archive_path: Path,
    command: str,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Run only SkillHub's local dry-run and verify its machine-readable contract."""

    completed = _run_command(
        runner,
        [command, "publish", str(archive_path), "--dry-run", "--json"],
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ReleaseError(
            f"SkillHub dry-run failed: {detail or 'no output'}"
        )
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            "SkillHub dry-run did not return valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise ReleaseError("SkillHub dry-run JSON must be an object")
    if (
        payload.get("dryRun") is not True
        or payload.get("slug") != EXPECTED_SLUG
        or payload.get("version") != EXPECTED_VERSION
    ):
        raise ReleaseError(f"unexpected SkillHub dry-run result: {payload}")
    return payload


def _authorization_component_binding(
    component: Mapping[str, object],
) -> dict[str, object]:
    keys = (
        "id",
        "version",
        "source_commit",
        "license",
        "legal_review",
        "manifest_sha256",
        "files_sha256",
        "tree_sha256",
        "license_files",
        "notice_sha256",
        "modifications_sha256",
        "third_party_notices_sha256",
    )
    return {key: component[key] for key in keys}


def validate_release_authorization(
    path: Path,
    *,
    skill_root: Path,
    content_hash: str,
    components: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Verify an external, candidate-bound legal-review authorization."""

    authorization_path = _absolute_without_resolving_symlinks(path)
    _assert_no_existing_parent_symlinks(authorization_path.parent)
    if authorization_path.is_symlink():
        raise ReleaseError("release authorization must not be a symbolic link")
    try:
        canonical = authorization_path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(f"release authorization is missing or unreadable: {exc}") from exc
    if _is_within(canonical, skill_root):
        raise ReleaseError("release authorization must be outside the skill root")
    data = _read_regular_file_no_follow(authorization_path)
    payload = _strict_json_object(data, document="release authorization")
    if payload.get("schema") != AUTHORIZATION_SCHEMA:
        raise ReleaseError("unsupported release authorization schema")
    if payload.get("skill") != {
        "slug": EXPECTED_SLUG,
        "version": EXPECTED_VERSION,
    }:
        raise ReleaseError("release authorization skill identity mismatch")
    if payload.get("channel") != "skillhub":
        raise ReleaseError("release authorization channel must be 'skillhub'")
    if payload.get("content_hash") != content_hash:
        raise ReleaseError("release authorization content hash mismatch")
    expected_components = [
        _authorization_component_binding(component) for component in components
    ]
    if payload.get("components") != expected_components:
        raise ReleaseError("release authorization component/license/notice binding mismatch")
    legal_review = payload.get("legal_review")
    if not isinstance(legal_review, dict) or legal_review.get("status") != "approved":
        raise ReleaseError("release authorization legal_review is not approved")
    reviewer = legal_review.get("reviewer")
    reviewed_at = legal_review.get("reviewed_at")
    platform_support = legal_review.get("platform_license_support")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ReleaseError("release authorization reviewer is required")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        raise ReleaseError("release authorization reviewed_at is required")
    if platform_support != "confirmed":
        raise ReleaseError(
            "release authorization must confirm SkillHub mixed/path-scoped license support"
        )
    if _read_regular_file_no_follow(authorization_path) != data:
        raise ReleaseError("release authorization changed while validating")
    return {
        "required": True,
        "status": "candidate_bound_authorization",
        "authorization_sha256": _sha256_bytes(data),
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "platform_license_support": platform_support,
        "source_gate": "closed",
        "channel": "skillhub",
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt_path(output: Path) -> Path:
    return output.with_suffix(output.suffix + ".release.json")


def _absolute_without_resolving_symlinks(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _assert_no_existing_parent_symlinks(parent: Path) -> None:
    """Reject each existing symlink component while allowing a missing tail."""

    if not parent.is_absolute():
        raise ReleaseError(f"output parent must be absolute: {parent}")
    current = Path(parent.anchor)
    for part in parent.parts[1:]:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ReleaseError(
                f"cannot inspect output path component {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ReleaseError(
                f"refusing symbolic-link output path component: {current}"
            )
        if not stat.S_ISDIR(mode):
            raise ReleaseError(
                f"output parent component is not a directory: {current}"
            )


def _prepare_output_path(
    output: Path, skill_root: Path, force: bool
) -> tuple[Path, Path]:
    requested = _absolute_without_resolving_symlinks(output)
    if requested.suffix.lower() != ".zip":
        raise ReleaseError("release output must have a .zip suffix")

    _assert_no_existing_parent_symlinks(requested.parent)
    canonical_parent = requested.parent.resolve(strict=False)
    canonical_output = canonical_parent / requested.name
    if _is_within(canonical_output, skill_root):
        raise ReleaseError("release output must be outside the skill root")
    receipt = _receipt_path(requested)

    for path in (requested, receipt):
        if path.is_symlink():
            raise ReleaseError(f"refusing symbolic-link output: {path}")
        if path.exists() and (not path.is_file() or not force):
            if not path.is_file():
                raise ReleaseError(f"output target is not a regular file: {path}")
            raise ReleaseError(
                f"output already exists; use --force to replace it: {path}"
            )
    return requested, receipt


def _write_atomic_bytes(path: Path, data: bytes) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _commit_release(
    temporary_archive: Path,
    temporary_receipt: Path,
    output_path: Path,
    receipt_path: Path,
    *,
    force: bool,
) -> None:
    """Commit the ZIP first and its success receipt last."""

    try:
        if force and receipt_path.exists():
            receipt_path.unlink()
        os.replace(temporary_archive, output_path)
        os.replace(temporary_receipt, receipt_path)
    except OSError as exc:
        raise ReleaseError(
            f"failed to commit release; no success receipt was committed: {exc}"
        ) from exc


def build_release(
    output: Path,
    *,
    skill_root: Path = SKILL_ROOT,
    allowlist_path: Path | None = None,
    force: bool = False,
    skillhub_preflight: bool = False,
    authorization: Path | None = None,
    skillhub_command: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Build, verify, optionally preflight, then commit a ZIP and receipt."""

    root = skill_root.expanduser().resolve(strict=True)
    inputs = _validate_release_inputs(root, allowlist_path)
    release_files = inputs.files
    content_hash = skillhub_content_hash(release_files)
    if authorization is not None and not skillhub_preflight:
        raise ReleaseError("--authorization is accepted only with --skillhub-preflight")
    if skillhub_preflight and authorization is None:
        raise ReleaseError("--skillhub-preflight requires external --authorization")
    legal_review: dict[str, object] = {
        "required": True,
        "status": "missing",
        "authorization_sha256": None,
        "reviewer": None,
        "reviewed_at": None,
        "platform_license_support": "unconfirmed",
        "source_gate": "closed",
        "channel": "skillhub",
    }
    if authorization is not None:
        legal_review = validate_release_authorization(
            authorization,
            skill_root=root,
            content_hash=content_hash,
            components=inputs.components,
        )
    if skillhub_preflight and not PLATFORM_MIXED_LICENSE_SUPPORT_CONFIRMED:
        raise ReleaseError(
            "SkillHub mixed/path-scoped license support is not independently "
            "confirmed; the 0.1.0 source gate forbids aggregate preflight"
        )
    output_path, receipt_path = _prepare_output_path(output, root, force)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_existing_parent_symlinks(output_path.parent)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", suffix=".zip", dir=output_path.parent
    )
    os.close(descriptor)
    temporary_archive = Path(temporary_name)
    temporary_receipt: Path | None = None
    try:
        _write_deterministic_zip(temporary_archive, release_files)
        verify_release_archive(temporary_archive, release_files)

        resolved_command = _resolve_skillhub_command(skillhub_command)
        cli_version: str | None = None
        version_error: str | None = None
        if resolved_command:
            try:
                cli_version = _skillhub_version(resolved_command, runner)
            except ReleaseError as exc:
                if skillhub_preflight:
                    raise
                version_error = str(exc)
        elif skillhub_preflight:
            raise ReleaseError(
                "skillhub executable not found; preflight is required"
            )

        dry_run: dict[str, object] | None = None
        if skillhub_preflight:
            assert resolved_command is not None
            dry_run = run_skillhub_preflight(
                temporary_archive, resolved_command, runner
            )

        archive_sha256 = _sha256_file(temporary_archive)
        publishable = bool(
            PLATFORM_MIXED_LICENSE_SUPPORT_CONFIRMED
            and skillhub_preflight
            and legal_review.get("status") == "candidate_bound_authorization"
            and isinstance(dry_run, dict)
            and dry_run.get("dryRun") is True
        )
        receipt: dict[str, object] = {
            "schema": RECEIPT_SCHEMA,
            "skill": {"slug": EXPECTED_SLUG, "version": EXPECTED_VERSION},
            "files": [
                {
                    "path": item.relative_path,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                }
                for item in release_files
            ],
            "components": inputs.components,
            "archive": {
                "name": output_path.name,
                "sha256": archive_sha256,
                "size_bytes": temporary_archive.stat().st_size,
                "compression": "ZIP_STORED",
                "inventory": [item.relative_path for item in release_files],
            },
            "skillhub": {
                "content_hash": content_hash,
                "cli_version": cli_version,
                "cli_version_error": version_error,
                "preflight_requested": skillhub_preflight,
                "dry_run": dry_run,
            },
            "legal_review": legal_review,
            "publishable": publishable,
            "result": {"status": "success", "commit_marker": True},
        }
        receipt_bytes = (
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        temporary_receipt = _write_atomic_bytes(receipt_path, receipt_bytes)

        if not force:
            for path in (output_path, receipt_path):
                if path.exists() or path.is_symlink():
                    raise ReleaseError(
                        f"output appeared during build; refusing to replace it: {path}"
                    )
        _commit_release(
            temporary_archive,
            temporary_receipt,
            output_path,
            receipt_path,
            force=force,
        )
        temporary_receipt = None
        return receipt
    finally:
        temporary_archive.unlink(missing_ok=True)
        if temporary_receipt is not None:
            temporary_receipt.unlink(missing_ok=True)


def check_release(
    skill_root: Path = SKILL_ROOT,
    allowlist_path: Path | None = None,
) -> dict[str, object]:
    inputs = _validate_release_inputs(skill_root, allowlist_path)
    release_files = inputs.files
    return {
        "status": "valid_candidate",
        "publishable": False,
        "legal_review": {
            "required": True,
            "status": "missing",
            "source_gate": "closed",
        },
        "file_count": len(release_files),
        "files": [item.relative_path for item in release_files],
        "components": inputs.components,
        "content_hash": skillhub_content_hash(release_files),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate or build the exact Starun-siril SkillHub release archive."
        )
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--check", action="store_true", help="validate the exact release whitelist"
    )
    action.add_argument(
        "--output", type=Path, help="write a deterministic .zip outside the skill root"
    )
    parser.add_argument(
        "--force", action="store_true", help="replace an existing ZIP and receipt"
    )
    parser.add_argument(
        "--skillhub-preflight",
        action="store_true",
        help="require skillhub publish ZIP --dry-run --json to pass",
    )
    parser.add_argument(
        "--authorization",
        type=Path,
        help=(
            "external release-authorization JSON required for SkillHub dry-run"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check and (args.force or args.skillhub_preflight or args.authorization):
        parser.error("--force, --skillhub-preflight and --authorization require --output")
    if args.skillhub_preflight and args.authorization is None:
        parser.error("--skillhub-preflight requires --authorization")
    if args.authorization is not None and not args.skillhub_preflight:
        parser.error("--authorization requires --skillhub-preflight")
    try:
        if args.check:
            payload = check_release()
        else:
            payload = build_release(
                args.output,
                force=args.force,
                skillhub_preflight=args.skillhub_preflight,
                authorization=args.authorization,
            )
    except (OSError, ReleaseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
