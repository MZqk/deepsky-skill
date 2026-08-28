#!/usr/bin/env python3
"""Build the exact, auditable Siril Mosaic SkillHub release archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Sequence


SKILL_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = SKILL_ROOT / "release-files.txt"
EXPECTED_RELEASE_FILES = (
    "LICENSE.md",
    "SKILL.md",
    "agents/openai.yaml",
    "references/quality.md",
    "references/workflow.md",
    "scripts/siril_mosaic.py",
)
EXPECTED_SLUG = "siril-mosaic"
EXPECTED_VERSION = "0.1.0"
FIXED_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_MODE = stat.S_IFREG | 0o644
RECEIPT_SCHEMA = "siril-mosaic.release-receipt/v1"


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


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_regular_file_no_follow(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseError(f"cannot open regular file without following links: {path}: {exc}") from exc
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
            raise ReleaseError(f"missing or unreadable release entry: {relative_path}: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise ReleaseError(f"release entry contains a symbolic link: {relative_path}")
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
        pure_path = PurePosixPath(entry)
        if (
            pure_path.is_absolute()
            or "\\" in entry
            or entry != pure_path.as_posix()
            or any(part in {"", ".", ".."} for part in pure_path.parts)
        ):
            raise ReleaseError(f"invalid allowlist path on line {line_number}: {entry!r}")
        if entry in seen:
            raise ReleaseError(f"duplicate allowlist path on line {line_number}: {entry}")
        seen.add(entry)
        entries.append(entry)

    if tuple(entries) != EXPECTED_RELEASE_FILES:
        expected = ", ".join(EXPECTED_RELEASE_FILES)
        raise ReleaseError(f"release-files.txt must contain exactly, in order: {expected}")
    return tuple(entries)


def validate_release_source(
    skill_root: Path = SKILL_ROOT,
    allowlist_path: Path | None = None,
) -> list[ReleaseFile]:
    """Validate and capture the six exact public files without following links."""

    try:
        root = skill_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReleaseError(f"skill root is missing or unreadable: {skill_root}: {exc}") from exc
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
        source_path = _assert_no_symlink_components(root, relative_path)
        release_files.append(ReleaseFile(relative_path, _read_regular_file_no_follow(source_path)))
    return release_files


def skillhub_content_hash(release_files: Sequence[ReleaseFile]) -> str:
    """Match SkillHub's sorted ``path:file_sha256`` content fingerprint."""

    digest = hashlib.sha256()
    for release_file in sorted(release_files, key=lambda item: item.relative_path):
        digest.update(
            f"{release_file.relative_path}:{release_file.sha256}\n".encode("utf-8")
        )
    return digest.hexdigest()


def _write_deterministic_zip(path: Path, release_files: Sequence[ReleaseFile]) -> None:
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for release_file in sorted(release_files, key=lambda item: item.relative_path):
            info = zipfile.ZipInfo(release_file.relative_path, date_time=FIXED_ZIP_TIMESTAMP)
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = FIXED_FILE_MODE << 16
            info.internal_attr = 0
            info.extra = b""
            info.comment = b""
            archive.writestr(info, release_file.data)


def verify_release_archive(path: Path, release_files: Sequence[ReleaseFile]) -> None:
    expected = [item.relative_path for item in sorted(release_files, key=lambda item: item.relative_path)]
    try:
        with zipfile.ZipFile(path, mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names != expected or len(names) != len(set(names)):
                raise ReleaseError(f"archive inventory mismatch: expected {expected}, got {names}")
            for info, release_file in zip(infos, sorted(release_files, key=lambda item: item.relative_path)):
                if info.is_dir() or info.compress_type != zipfile.ZIP_STORED:
                    raise ReleaseError(f"archive entry is not a stored regular file: {info.filename}")
                if info.date_time != FIXED_ZIP_TIMESTAMP:
                    raise ReleaseError(f"archive entry timestamp is not deterministic: {info.filename}")
                archived_mode = (info.external_attr >> 16) & 0o177777
                if archived_mode != FIXED_FILE_MODE:
                    raise ReleaseError(f"archive entry mode is not deterministic: {info.filename}")
                data = archive.read(info)
                if hashlib.sha256(data).hexdigest() != release_file.sha256:
                    raise ReleaseError(f"archive entry hash mismatch: {info.filename}")
    except zipfile.BadZipFile as exc:
        raise ReleaseError(f"invalid release archive: {path}: {exc}") from exc


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
        raise ReleaseError(f"SkillHub dry-run failed: {detail or 'no output'}")
    try:
        payload = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReleaseError("SkillHub dry-run did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise ReleaseError("SkillHub dry-run JSON must be an object")
    if (
        payload.get("dryRun") is not True
        or payload.get("slug") != EXPECTED_SLUG
        or payload.get("version") != EXPECTED_VERSION
    ):
        raise ReleaseError(f"unexpected SkillHub dry-run result: {payload}")
    return payload


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
    """Reject every existing symlink component while allowing a missing directory tail."""

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
            raise ReleaseError(f"cannot inspect output path component {current}: {exc}") from exc
        if stat.S_ISLNK(mode):
            raise ReleaseError(f"refusing symbolic-link output path component: {current}")
        if not stat.S_ISDIR(mode):
            raise ReleaseError(f"output parent component is not a directory: {current}")


def _prepare_output_path(output: Path, skill_root: Path, force: bool) -> tuple[Path, Path]:
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
            raise ReleaseError(f"output already exists; use --force to replace it: {path}")
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
    """Commit the ZIP first and its success receipt last.

    The receipt is the commit marker. During a forced replacement, invalidate
    the old marker before the ZIP changes. A failed second rename can therefore
    never leave a new ZIP authenticated by an old success receipt.
    """

    try:
        if force and receipt_path.exists():
            receipt_path.unlink()
        os.replace(temporary_archive, output_path)
        os.replace(temporary_receipt, receipt_path)
    except OSError as exc:
        raise ReleaseError(f"failed to commit release; no success receipt was committed: {exc}") from exc


def build_release(
    output: Path,
    *,
    skill_root: Path = SKILL_ROOT,
    allowlist_path: Path | None = None,
    force: bool = False,
    skillhub_preflight: bool = False,
    skillhub_command: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> dict[str, object]:
    """Build, verify, optionally preflight, then commit a ZIP and receipt marker."""

    root = skill_root.expanduser().resolve(strict=True)
    release_files = validate_release_source(root, allowlist_path)
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
            raise ReleaseError("skillhub executable not found; preflight is required")

        dry_run: dict[str, object] | None = None
        if skillhub_preflight:
            assert resolved_command is not None
            dry_run = run_skillhub_preflight(temporary_archive, resolved_command, runner)

        archive_sha256 = _sha256_file(temporary_archive)
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
            "archive": {
                "name": output_path.name,
                "sha256": archive_sha256,
                "size_bytes": temporary_archive.stat().st_size,
                "compression": "ZIP_STORED",
                "inventory": [item.relative_path for item in release_files],
            },
            "skillhub": {
                "content_hash": skillhub_content_hash(release_files),
                "cli_version": cli_version,
                "cli_version_error": version_error,
                "preflight_requested": skillhub_preflight,
                "dry_run": dry_run,
            },
            "result": {"status": "success", "commit_marker": True},
        }
        receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        temporary_receipt = _write_atomic_bytes(receipt_path, receipt_bytes)

        if not force:
            for path in (output_path, receipt_path):
                if path.exists() or path.is_symlink():
                    raise ReleaseError(f"output appeared during build; refusing to replace it: {path}")
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
    release_files = validate_release_source(skill_root, allowlist_path)
    return {
        "status": "ready",
        "file_count": len(release_files),
        "files": [item.relative_path for item in release_files],
        "content_hash": skillhub_content_hash(release_files),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or build the exact Siril Mosaic SkillHub release archive."
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="validate the exact release whitelist")
    action.add_argument("--output", type=Path, help="write a deterministic .zip outside the skill root")
    parser.add_argument("--force", action="store_true", help="replace an existing ZIP and receipt")
    parser.add_argument(
        "--skillhub-preflight",
        action="store_true",
        help="require skillhub publish ZIP --dry-run --json to pass",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.check and (args.force or args.skillhub_preflight):
        parser.error("--force and --skillhub-preflight require --output")
    try:
        if args.check:
            payload = check_release()
        else:
            payload = build_release(
                args.output,
                force=args.force,
                skillhub_preflight=args.skillhub_preflight,
            )
    except (OSError, ReleaseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
