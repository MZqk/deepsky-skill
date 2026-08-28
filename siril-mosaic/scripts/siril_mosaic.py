#!/usr/bin/env python3
"""Source-safe orchestration for Siril 1.4 astronomical panel mosaics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SUPPORTED_SUFFIXES = (
    ".fit",
    ".fits",
    ".fts",
    ".fit.fz",
    ".fits.fz",
    ".xisf",
    ".tif",
    ".tiff",
)
FITS_SUFFIXES = (".fit", ".fits", ".fts", ".fit.fz", ".fits.fz")
DEFAULT_MACOS_SIRIL = Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
VERSION_RE = re.compile(r"\bsiril\s+(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)
SAFE_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
STACKED_COUNT_RE = re.compile(r"\b(\d+)\s+images?\s+have been stacked\b", re.IGNORECASE)


class MosaicError(RuntimeError):
    """Expected, user-actionable failure."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def expand_path(raw: str) -> Path:
    # Prompts and Markdown frequently escape a leading tilde as ``\~``.
    if raw.startswith("\\~"):
        raw = raw[1:]
    return Path(raw).expanduser().resolve()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def recognized_suffix(path: Path) -> str | None:
    lowered = path.name.lower()
    return next((suffix for suffix in SUPPORTED_SUFFIXES if lowered.endswith(suffix)), None)


def is_fits(path: Path) -> bool:
    lowered = path.name.lower()
    return any(lowered.endswith(suffix) for suffix in FITS_SUFFIXES)


def _is_hidden_path(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts)


def _resolve_contained_regular_file(path: Path, input_root: Path) -> Path:
    """Resolve one source file without permitting links or root escapes."""
    canonical_root = input_root.resolve(strict=True)
    if path.is_symlink():
        raise MosaicError(f"Symbolic-link image inputs are not allowed: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MosaicError(f"Cannot resolve input panel {path}: {exc}") from exc
    if not resolved.is_relative_to(canonical_root):
        raise MosaicError(f"Input panel escapes the selected directory: {path}")
    if not resolved.is_file():
        raise MosaicError(f"Input panel is not a regular file: {path}")
    return resolved


def discover_panels(input_dir: Path, recursive: bool = False) -> list[Path]:
    if not input_dir.is_dir():
        raise MosaicError(f"Input directory does not exist: {input_dir}")

    canonical_root = input_dir.resolve(strict=True)
    iterator: Iterable[Path] = (
        canonical_root.rglob("*") if recursive else canonical_root.iterdir()
    )
    panels: list[Path] = []
    for path in iterator:
        if _is_hidden_path(path, canonical_root):
            continue
        if path.is_symlink():
            if recognized_suffix(path) is not None or path.is_dir():
                kind = "directory" if path.is_dir() else "image input"
                raise MosaicError(f"Symbolic-link {kind} is not allowed: {path}")
            continue
        if not path.is_file() or recognized_suffix(path) is None:
            continue
        panels.append(_resolve_contained_regular_file(path, canonical_root))
    panels.sort(key=lambda item: item.name.casefold())
    if len(panels) < 2:
        raise MosaicError(
            f"Need at least two supported image panels, found {len(panels)} in {input_dir}"
        )
    return panels


def _fits_value(field: str) -> Any:
    value = field.lstrip()
    if not value:
        return None
    if value.startswith("'"):
        chars: list[str] = []
        index = 1
        while index < len(value):
            char = value[index]
            if char == "'":
                if index + 1 < len(value) and value[index + 1] == "'":
                    chars.append("'")
                    index += 2
                    continue
                break
            chars.append(char)
            index += 1
        return "".join(chars).rstrip()

    value = value.split("/", 1)[0].strip()
    if value == "T":
        return True
    if value == "F":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value.replace("D", "E"))
    except ValueError:
        return value or None


def read_fits_header(path: Path) -> dict[str, Any]:
    """Read the primary FITS header without loading or scaling image pixels."""
    if not is_fits(path) or path.name.lower().endswith(".fz"):
        return {}

    cards: list[str] = []
    try:
        with path.open("rb") as handle:
            for _ in range(2048):
                block = handle.read(2880)
                if not block:
                    break
                if len(block) != 2880:
                    raise MosaicError(f"Truncated FITS header: {path}")
                decoded = block.decode("ascii", errors="replace")
                block_cards = [decoded[index : index + 80] for index in range(0, 2880, 80)]
                cards.extend(block_cards)
                if any(card.startswith("END") for card in block_cards):
                    break
    except OSError as exc:
        raise MosaicError(f"Cannot read FITS header {path}: {exc}") from exc

    header: dict[str, Any] = {}
    for card in cards:
        key = card[:8].strip()
        if key == "END":
            break
        if not key or card[8:10] != "= ":
            continue
        header[key] = _fits_value(card[10:])
    return header


def _parse_coordinate_angle(
    value: Any,
    *,
    coordinate: str,
    allow_sexagesimal: bool,
    sexagesimal_hours: bool,
) -> tuple[float | None, str | None]:
    """Parse a FITS pointing value and report the interpretation that was used."""
    if coordinate not in {"ra", "dec"}:
        raise ValueError(f"Unsupported coordinate: {coordinate}")

    interpretation = "degrees"
    degrees: float
    if isinstance(value, bool):
        return None, "invalid"
    if isinstance(value, (int, float)):
        degrees = float(value)
        interpretation = "degrees_numeric"
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None, "invalid"
        if ":" in stripped:
            parts = [part.strip() for part in stripped.split(":")]
            if any(not part for part in parts):
                return None, "invalid"
        else:
            parts = stripped.split()
        if not 1 <= len(parts) <= 3:
            return None, "invalid"
        try:
            numbers = [float(part) for part in parts]
        except ValueError:
            return None, "invalid"
        if not all(math.isfinite(number) for number in numbers):
            return None, "invalid"
        if len(numbers) == 1:
            degrees = numbers[0]
            interpretation = "degrees_string"
        else:
            if not allow_sexagesimal:
                return None, "invalid"
            first, minutes = numbers[0], numbers[1]
            seconds = numbers[2] if len(numbers) == 3 else 0.0
            if not 0.0 <= minutes < 60.0 or not 0.0 <= seconds < 60.0:
                return None, "invalid"
            sign = -1.0 if first < 0 or parts[0].startswith("-") else 1.0
            absolute = abs(first) + minutes / 60.0 + seconds / 3600.0
            degrees = sign * absolute
            if sexagesimal_hours:
                degrees *= 15.0
                interpretation = "hour_angle_sexagesimal"
            else:
                interpretation = "degrees_sexagesimal"
    else:
        return None, "invalid"

    if not math.isfinite(degrees):
        return None, "invalid"
    if coordinate == "ra" and not 0.0 <= degrees < 360.0:
        return None, "invalid"
    if coordinate == "dec" and not -90.0 <= degrees <= 90.0:
        return None, "invalid"
    return degrees, interpretation


def _angle_degrees(value: Any, *, right_ascension: bool) -> float | None:
    """Compatibility wrapper for callers that already select sexagesimal RA units."""
    degrees, _ = _parse_coordinate_angle(
        value,
        coordinate="ra" if right_ascension else "dec",
        allow_sexagesimal=True,
        sexagesimal_hours=right_ascension,
    )
    return degrees


def _coordinate_from_header(
    header: dict[str, Any],
    sources: tuple[str, ...],
    *,
    coordinate: str,
) -> tuple[float | None, str | None, str | None]:
    for source in sources:
        value = header.get(source)
        if value in (None, ""):
            continue
        is_catalog_ra = coordinate == "ra" and source in {"RA", "OBJCTRA"}
        is_catalog_dec = coordinate == "dec" and source in {"DEC", "OBJCTDEC"}
        degrees, interpretation = _parse_coordinate_angle(
            value,
            coordinate=coordinate,
            allow_sexagesimal=is_catalog_ra or is_catalog_dec,
            sexagesimal_hours=is_catalog_ra,
        )
        return degrees, source, interpretation
    return None, None, None


def panel_metadata(path: Path) -> dict[str, Any]:
    header = read_fits_header(path)
    ra, ra_source, ra_interpretation = _coordinate_from_header(
        header,
        ("CRVAL1", "RA", "OBJCTRA"),
        coordinate="ra",
    )
    dec, dec_source, dec_interpretation = _coordinate_from_header(
        header,
        ("CRVAL2", "DEC", "OBJCTDEC"),
        coordinate="dec",
    )
    has_wcs = bool(
        header.get("CTYPE1")
        and header.get("CTYPE2")
        and ra_source == "CRVAL1"
        and dec_source == "CRVAL2"
        and ra is not None
        and dec is not None
        and (
            header.get("CDELT1") is not None
            or header.get("CD1_1") is not None
            or header.get("PC1_1") is not None
        )
    )
    channels = header.get("NAXIS3", 1) if header else None
    return {
        "path": str(path),
        "name": path.name,
        "format": recognized_suffix(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "width": header.get("NAXIS1"),
        "height": header.get("NAXIS2"),
        "channels": channels,
        "bitpix": header.get("BITPIX"),
        "object": header.get("OBJECT"),
        "filter": header.get("FILTER"),
        "focal_length_mm": header.get("FOCALLEN"),
        "pixel_size_um": header.get("XPIXSZ", header.get("PIXSIZE")),
        "exposure_seconds": header.get("EXPTIME", header.get("EXPOSURE")),
        "stack_count": header.get("STACKCNT"),
        "bayer_pattern": header.get("BAYERPAT"),
        "ra_deg": ra,
        "dec_deg": dec,
        "ra_source": ra_source,
        "ra_interpretation": ra_interpretation,
        "dec_source": dec_source,
        "dec_interpretation": dec_interpretation,
        "has_wcs": has_wcs,
    }


def _unique_known(records: list[dict[str, Any]], key: str) -> list[Any]:
    values = {record.get(key) for record in records if record.get(key) not in (None, "")}
    return sorted(values, key=lambda value: str(value))


def _maximum_center_separation(records: list[dict[str, Any]]) -> float | None:
    centers = [
        (float(record["ra_deg"]), float(record["dec_deg"]))
        for record in records
        if record.get("ra_deg") is not None and record.get("dec_deg") is not None
    ]
    if len(centers) < 2:
        return None
    maximum = 0.0
    for index, (ra1, dec1) in enumerate(centers):
        for ra2, dec2 in centers[index + 1 :]:
            delta_ra = abs(ra1 - ra2) % 360.0
            delta_ra = min(delta_ra, 360.0 - delta_ra)
            delta_ra *= math.cos(math.radians((dec1 + dec2) / 2.0))
            maximum = max(maximum, math.hypot(delta_ra, dec1 - dec2))
    return maximum


def inspect_directory(input_dir: Path, recursive: bool = False) -> dict[str, Any]:
    panels = discover_panels(input_dir, recursive=recursive)
    records = [panel_metadata(path) for path in panels]
    errors: list[str] = []
    warnings: list[str] = []

    channel_values = _unique_known(records, "channels")
    if len(channel_values) > 1:
        errors.append(f"Mixed channel counts are not one mosaic layer: {channel_values}")

    filters = _unique_known(records, "filter")
    if len(filters) > 1:
        errors.append(
            "Mixed filters must be mosaicked separately and recomposed later: "
            + ", ".join(str(value) for value in filters)
        )

    missing_pointing = [
        record["name"]
        for record in records
        if not record["has_wcs"]
        and (record["ra_deg"] is None or record["dec_deg"] is None)
    ]
    if missing_pointing:
        warnings.append(
            "Panels missing both WCS and approximate RA/DEC require local blind Astrometry.net: "
            + ", ".join(missing_pointing)
        )

    unsolved = [record["name"] for record in records if not record["has_wcs"]]
    if unsolved:
        warnings.append(
            f"{len(unsolved)} of {len(records)} panels need plate solving before astrometric registration"
        )

    if any(record.get("bayer_pattern") and record.get("channels") == 3 for record in records):
        warnings.append(
            "BAYERPAT exists on one or more 3-channel stacked panels; it is treated as stale metadata, "
            "not as permission to debayer again"
        )

    widths = [record["width"] for record in records if isinstance(record.get("width"), int)]
    heights = [record["height"] for record in records if isinstance(record.get("height"), int)]
    auto_feather = max(32, min(512, round(min(widths + heights) * 0.04))) if widths and heights else 64

    return {
        "schema": "siril-mosaic.inspect/v1",
        "created_at": utc_now(),
        "input_dir": str(input_dir),
        "recursive": recursive,
        "panel_count": len(records),
        "panels": records,
        "summary": {
            "formats": _unique_known(records, "format"),
            "filters": filters,
            "channel_counts": channel_values,
            "wcs_count": sum(bool(record["has_wcs"]) for record in records),
            "pointing_count": sum(
                record["ra_deg"] is not None and record["dec_deg"] is not None
                for record in records
            ),
            "maximum_center_separation_deg": _maximum_center_separation(records),
            "suggested_feather_px": auto_feather,
        },
        "errors": errors,
        "warnings": warnings,
        "ready": not errors,
    }


def locate_siril(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(expand_path(explicit))
    configured = os.environ.get("SIRIL_CLI")
    if configured:
        candidates.append(expand_path(configured))
    discovered = shutil.which("siril-cli")
    if discovered:
        candidates.append(Path(discovered).resolve())
    candidates.extend([DEFAULT_MACOS_SIRIL, Path("/usr/bin/siril-cli")])
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise MosaicError("siril-cli was not found; install Siril 1.4+ or pass --siril-cli")


def probe_siril(explicit: str | None = None) -> dict[str, Any]:
    executable = locate_siril(explicit)
    completed = subprocess.run(
        [str(executable), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=30,
        check=False,
    )
    output = completed.stdout.strip()
    match = VERSION_RE.search(output)
    if completed.returncode != 0 or not match:
        raise MosaicError(f"Could not determine Siril version from {executable}: {output}")
    version = tuple(int(value or 0) for value in match.groups())
    if version < (1, 4, 0):
        raise MosaicError(f"Siril >= 1.4.0 is required, found {'.'.join(map(str, version))}")
    default_initfile = locate_siril_initfile()
    return {
        "path": str(executable),
        "version": ".".join(map(str, version)),
        "supported": True,
        "validated_range": ">=1.4.0,<1.5.0",
        "version_warning": version >= (1, 5, 0),
        "default_initfile": str(default_initfile) if default_initfile else None,
        "raw_output": output,
    }


def locate_siril_initfile(explicit: str | None = None) -> Path | None:
    if explicit:
        path = expand_path(explicit)
        if not path.is_file():
            raise MosaicError(f"Siril init file does not exist: {path}")
        return path
    candidates = [
        Path.home()
        / "Library"
        / "Application Support"
        / "org.siril.Siril"
        / "siril"
        / "config.1.4.ini",
        Path.home() / ".config" / "siril" / "config.1.4.ini",
        Path.home() / ".local" / "share" / "siril" / "config.1.4.ini",
    ]
    return next((path.resolve() for path in candidates if path.is_file()), None)


def siril_quote(value: str) -> str:
    if any(character in value for character in ('"', "\n", "\r", "\x00")):
        raise MosaicError(f"Siril script arguments cannot contain quotes or control characters: {value!r}")
    return f'"{value}"'


def build_siril_script(
    *,
    staging_dir: Path,
    process_dir: Path,
    output_base: Path,
    converter: str,
    force_platesolve: bool,
    nocache: bool,
    focal_mm: float | None,
    pixel_size_um: float | None,
    catalog: str | None,
    local_astrometry_net: bool,
    blind_position: bool,
    blind_resolution: bool,
    scale: float,
    feather: int,
    preview_background: float,
) -> str:
    lines = [
        "requires 1.4.0",
        "setext fit",
        "set32bits",
        f"cd {siril_quote(str(staging_dir))}",
        f"{converter} mosaic {siril_quote('-out=' + str(process_dir))}",
        f"cd {siril_quote(str(process_dir))}",
    ]

    solve = ["seqplatesolve", "mosaic_", "-order=3"]
    if force_platesolve:
        solve.append("-force")
    if nocache:
        solve.append("-nocache")
    if focal_mm is not None:
        solve.append(f"-focal={focal_mm:g}")
    if pixel_size_um is not None:
        solve.append(f"-pixelsize={pixel_size_um:g}")
    if catalog:
        solve.append(f"-catalog={catalog}")
    if local_astrometry_net:
        solve.append("-localasnet")
        if blind_position:
            solve.append("-blindpos")
        if blind_resolution:
            solve.append("-blindres")
    lines.append(" ".join(solve))

    registration = ["seqapplyreg", "mosaic_", "-framing=max", "-interp=la"]
    if scale != 1.0:
        registration.append(f"-scale={scale:g}")
    lines.append(" ".join(registration))

    stack = [
        "stack",
        "r_mosaic_",
        "rej",
        "none",
        "-norm=addscale",
        "-overlap_norm",
    ]
    if feather > 0:
        stack.append(f"-feather={feather}")
    stack.extend(["-maximize", "-32b", siril_quote("-out=" + str(output_base))])
    lines.append(" ".join(stack))
    lines.extend(
        [
            f"load {siril_quote(str(Path(str(output_base) + '.fit')))}",
            f"autostretch -linked -2.8 {preview_background:g}",
            f"savejpg {siril_quote(str(output_base.parent / 'mosaic_preview'))} 95",
            "close",
        ]
    )
    return "\n".join(lines) + "\n"


def _validate_run_options(args: argparse.Namespace, inspection: dict[str, Any]) -> None:
    if not inspection["ready"]:
        raise MosaicError("Input inspection failed: " + "; ".join(inspection["errors"]))
    if not 0.1 <= args.scale <= 3.0:
        raise MosaicError("--scale must be between 0.1 and 3.0")
    if not 0 <= args.feather <= 4096:
        raise MosaicError("--feather must be between 0 and 4096 pixels")
    if not 0.05 <= args.preview_background <= 0.5:
        raise MosaicError("--preview-background must be between 0.05 and 0.5")
    if args.timeout < 30:
        raise MosaicError("--timeout must be at least 30 seconds")
    if not SAFE_OUTPUT_NAME_RE.fullmatch(args.output_name):
        raise MosaicError("--output-name must be a safe filename stem without path separators")
    if args.offline and args.catalog and args.catalog != "localgaia":
        raise MosaicError("Offline mode can only use --catalog localgaia")

    missing_pointing = [
        panel["name"]
        for panel in inspection["panels"]
        if not panel["has_wcs"]
        and (panel["ra_deg"] is None or panel["dec_deg"] is None)
    ]
    if missing_pointing and not (args.local_astrometry_net and args.blind_position):
        raise MosaicError(
            "Some panels lack WCS and approximate pointing. Install local Astrometry.net and use "
            "--local-astrometry-net --blind-position, or add valid pointing metadata: "
            + ", ".join(missing_pointing)
        )
    if args.offline and any(not panel["has_wcs"] for panel in inspection["panels"]):
        if not args.local_astrometry_net and args.catalog != "localgaia":
            raise MosaicError(
                "Offline mode cannot solve panels without WCS unless local Gaia or local Astrometry.net is configured"
            )


def _new_run_directory(output_dir: Path, input_dir: Path) -> None:
    if output_dir == input_dir or input_dir in output_dir.parents:
        raise MosaicError("Output directory must be outside the source directory")
    if output_dir.exists():
        raise MosaicError(f"Output directory already exists; choose a new run directory: {output_dir}")
    output_dir.mkdir(parents=True)


def _validate_input_sources(
    records: list[dict[str, Any]], input_dir: Path
) -> list[Path]:
    validated: list[Path] = []
    for record in records:
        source = Path(record["path"])
        if recognized_suffix(source) is None:
            raise MosaicError(f"Unsupported input suffix: {source}")
        validated.append(_resolve_contained_regular_file(source, input_dir))
    return validated


def _open_input_file_descriptor(source: Path, input_root: Path, root_fd: int) -> int:
    """Open a contained regular file without following any path component links."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise MosaicError("This platform cannot safely open input panels without following links")
    try:
        relative = source.relative_to(input_root)
    except ValueError as exc:
        raise MosaicError(f"Input panel escapes the selected directory: {source}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise MosaicError(f"Unsafe input panel path: {source}")

    parent_fd = root_fd
    owned_parent_fd: int | None = None
    try:
        for component in relative.parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | directory | nofollow,
                dir_fd=parent_fd,
            )
            if owned_parent_fd is not None:
                os.close(owned_parent_fd)
            owned_parent_fd = next_fd
            parent_fd = next_fd
        source_fd = os.open(
            relative.parts[-1],
            os.O_RDONLY | nofollow,
            dir_fd=parent_fd,
        )
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise MosaicError(f"Input panel is not a regular file: {source}")
            return source_fd
        except Exception:
            os.close(source_fd)
            raise
    except OSError as exc:
        raise MosaicError(f"Cannot securely open input panel {source}: {exc}") from exc
    finally:
        if owned_parent_fd is not None:
            os.close(owned_parent_fd)


def _copy_open_file_atomic(
    source_fd: int,
    source: Path,
    destination: Path,
    expected_sha256: str,
) -> str:
    """Hash and copy one already-open source inode, exposing output only on success."""
    temporary_path: Path | None = None
    try:
        with os.fdopen(source_fd, "rb", closefd=True) as source_handle:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as destination_handle:
                temporary_path = Path(destination_handle.name)
                digest = hashlib.sha256()
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    destination_handle.write(chunk)
                    digest.update(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())
        staged_hash = digest.hexdigest()
        if staged_hash != expected_sha256:
            raise MosaicError(f"Staged copy hash mismatch: {source}")
        temporary_path.replace(destination)
        temporary_path = None
        return staged_hash
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _copy_inputs(
    records: list[dict[str, Any]], staging_dir: Path, input_dir: Path
) -> list[dict[str, Any]]:
    sources = _validate_input_sources(records, input_dir)
    canonical_root = input_dir.resolve(strict=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory:
        raise MosaicError("This platform cannot safely open input panels without following links")
    try:
        root_fd = os.open(canonical_root, os.O_RDONLY | directory | nofollow)
    except OSError as exc:
        raise MosaicError(f"Cannot securely open input directory {canonical_root}: {exc}") from exc
    staging_dir.mkdir(parents=True)
    staged_records: list[dict[str, Any]] = []
    try:
        for index, (record, source) in enumerate(zip(records, sources, strict=True), start=1):
            suffix = recognized_suffix(source)
            assert suffix is not None
            destination = staging_dir / f"panel_{index:04d}{suffix}"
            if destination.exists() or destination.is_symlink():
                raise MosaicError(f"Staged destination already exists: {destination}")
            source_fd = _open_input_file_descriptor(source, canonical_root, root_fd)
            staged_hash = _copy_open_file_atomic(
                source_fd,
                source,
                destination,
                record["sha256"],
            )
            staged = dict(record)
            staged.update({"staged_path": str(destination), "staged_sha256": staged_hash})
            staged_records.append(staged)
        return staged_records
    finally:
        os.close(root_fd)


def _matching_fits(directory: Path, prefix: str) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.name.startswith(prefix) and is_fits(path)
    )


def _jpeg_valid(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 4:
        return False
    with path.open("rb") as handle:
        return handle.read(2) == b"\xff\xd8"


def _stacked_count(log_text: str) -> int | None:
    matches = STACKED_COUNT_RE.findall(log_text)
    return int(matches[-1]) if matches else None


def _artifact_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return {"path": str(path), "size_bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _union_canvas_geometry(
    *,
    output_area: int | None,
    input_areas: list[int | None],
    scale: float,
    expansion_expected: bool,
) -> dict[str, Any]:
    """Evaluate union-canvas geometry in the source panels' pixel-area domain."""
    threshold_ratio = 1.02 if expansion_expected else 0.95
    missing_geometry = (
        isinstance(output_area, bool)
        or not isinstance(output_area, int)
        or output_area <= 0
        or not input_areas
        or any(
            isinstance(area, bool) or not isinstance(area, int) or area <= 0
            for area in input_areas
        )
        or isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(float(scale))
        or scale <= 0
    )
    if missing_geometry:
        return {
            "maximum_input_area_px": None,
            "scale_adjusted_maximum_input_area_px": None,
            "union_area_ratio": None,
            "union_threshold_ratio": threshold_ratio,
            "canvas_expanded": False,
            "gate_passed": not expansion_expected,
        }

    maximum_input_area = max(area for area in input_areas if area is not None)
    scale_adjusted_area = maximum_input_area * float(scale) ** 2
    union_area_ratio = output_area / scale_adjusted_area
    return {
        "maximum_input_area_px": maximum_input_area,
        "scale_adjusted_maximum_input_area_px": scale_adjusted_area,
        "union_area_ratio": union_area_ratio,
        "union_threshold_ratio": threshold_ratio,
        "canvas_expanded": union_area_ratio > threshold_ratio,
        "gate_passed": not expansion_expected or union_area_ratio > threshold_ratio,
    }


def execute_run(args: argparse.Namespace) -> dict[str, Any]:
    input_dir = expand_path(args.input_dir)
    output_dir = expand_path(args.output_dir)
    inspection = inspect_directory(input_dir, recursive=args.recursive)
    if args.feather is None:
        args.feather = int(inspection["summary"]["suggested_feather_px"])
    _validate_run_options(args, inspection)
    _validate_input_sources(inspection["panels"], input_dir)
    tool = probe_siril(args.siril_cli)
    _new_run_directory(output_dir, input_dir)

    staging_dir = output_dir / "staging"
    process_dir = output_dir / "process"
    outputs_dir = output_dir / "outputs"
    process_dir.mkdir()
    outputs_dir.mkdir()
    script_path = output_dir / "mosaic.ssf"
    log_path = output_dir / "siril.log"
    manifest_path = output_dir / "manifest.json"
    result_path = output_dir / "result.json"
    output_base = outputs_dir / args.output_name
    linear_path = Path(str(output_base) + ".fit")

    try:
        staged_records = _copy_inputs(inspection["panels"], staging_dir, input_dir)
        converter = "link" if all(is_fits(Path(record["path"])) for record in staged_records) else "convert"
        force_platesolve = not args.offline
        nocache = not args.offline and not args.local_astrometry_net
        source_initfile = locate_siril_initfile(args.initfile)
        isolated_initfile = output_dir / "siril-init.ini" if source_initfile else None
        initfile_snapshot = output_dir / "siril-init-source.ini" if source_initfile else None
        if source_initfile and isolated_initfile:
            shutil.copy2(source_initfile, initfile_snapshot)
            shutil.copy2(initfile_snapshot, isolated_initfile)
        script = build_siril_script(
            staging_dir=staging_dir,
            process_dir=process_dir,
            output_base=output_base,
            converter=converter,
            force_platesolve=force_platesolve,
            nocache=nocache,
            focal_mm=args.focal_mm,
            pixel_size_um=args.pixel_size_um,
            catalog=args.catalog,
            local_astrometry_net=args.local_astrometry_net,
            blind_position=args.blind_position,
            blind_resolution=args.blind_resolution,
            scale=args.scale,
            feather=args.feather,
            preview_background=args.preview_background,
        )
        script_path.write_text(script, encoding="utf-8")
        manifest = {
            "schema": "siril-mosaic.manifest/v1",
            "created_at": utc_now(),
            "status": "prepared",
            "input_dir": str(input_dir),
            "run_dir": str(output_dir),
            "tool": tool,
            "config": {
                "converter": converter,
                "recursive": args.recursive,
                "offline": args.offline,
                "focal_mm": args.focal_mm,
                "pixel_size_um": args.pixel_size_um,
                "catalog": args.catalog,
                "local_astrometry_net": args.local_astrometry_net,
                "blind_position": args.blind_position,
                "blind_resolution": args.blind_resolution,
                "scale": args.scale,
                "feather_px": args.feather,
                "preview_background": args.preview_background,
                "output_name": args.output_name,
            },
            "inspection": inspection,
            "inputs": staged_records,
            "siril_script": {"path": str(script_path), "sha256": sha256_file(script_path)},
            "siril_initfile_source": _artifact_record(initfile_snapshot) if initfile_snapshot else None,
            "siril_initfile_working_path": str(isolated_initfile) if isolated_initfile else None,
        }
        atomic_write_json(manifest_path, manifest)

        command = [tool["path"]]
        if isolated_initfile:
            command.extend(["--initfile", str(isolated_initfile)])
        if args.offline:
            command.append("--offline")
        command.extend(["--directory", str(output_dir), "--script", str(script_path)])
        try:
            completed = subprocess.run(
                command,
                cwd=output_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=args.timeout,
                check=False,
            )
            log_text = completed.stdout
            returncode = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
            log_text = (partial or "") + f"\nTimed out after {args.timeout} seconds.\n"
            returncode = None
            timed_out = True
        log_path.write_text(log_text, encoding="utf-8")

        preview_path = outputs_dir / "mosaic_preview.jpg"
        sequence_panels = _matching_fits(process_dir, "mosaic_")
        registered_panels = _matching_fits(process_dir, "r_mosaic_")
        solved_wcs_count = sum(panel_metadata(path)["has_wcs"] for path in sequence_panels)
        stacked_count = _stacked_count(log_text)
        output_meta = panel_metadata(linear_path) if linear_path.is_file() else None
        input_areas = [
            int(panel["width"]) * int(panel["height"])
            if isinstance(panel.get("width"), int)
            and not isinstance(panel.get("width"), bool)
            and panel["width"] > 0
            and isinstance(panel.get("height"), int)
            and not isinstance(panel.get("height"), bool)
            and panel["height"] > 0
            else None
            for panel in inspection["panels"]
        ]
        output_area = (
            int(output_meta["width"]) * int(output_meta["height"])
            if output_meta
            and isinstance(output_meta.get("width"), int)
            and isinstance(output_meta.get("height"), int)
            else None
        )
        center_span = inspection["summary"]["maximum_center_separation_deg"]
        expansion_expected = center_span is not None and center_span > 0.1
        canvas_geometry = _union_canvas_geometry(
            output_area=output_area,
            input_areas=input_areas,
            scale=args.scale,
            expansion_expected=expansion_expected,
        )
        gates = {
            "siril_exit_zero": returncode == 0 and not timed_out,
            "all_sequence_panels_present": len(sequence_panels) == inspection["panel_count"],
            "all_panels_plate_solved": solved_wcs_count == inspection["panel_count"],
            "all_panels_registered": len(registered_panels) == inspection["panel_count"],
            "all_panels_stacked": stacked_count == inspection["panel_count"],
            "linear_fits_present": linear_path.is_file() and linear_path.stat().st_size > 2880,
            "preview_jpeg_valid": _jpeg_valid(preview_path),
            "union_canvas_expanded": canvas_geometry["gate_passed"],
        }
        execution_ok = all(gates.values())
        result = {
            "schema": "siril-mosaic.result/v1",
            "created_at": utc_now(),
            "status": "visual_review_pending" if execution_ok else "failed",
            "execution": {
                "status": "succeeded" if execution_ok else "failed",
                "command": command,
                "returncode": returncode,
                "timed_out": timed_out,
                "panel_count": inspection["panel_count"],
                "plate_solved_count": solved_wcs_count,
                "registered_count": len(registered_panels),
                "stacked_count": stacked_count,
                "gates": gates,
            },
            "geometry": {
                "maximum_input_area_px": canvas_geometry["maximum_input_area_px"],
                "scale_adjusted_maximum_input_area_px": canvas_geometry[
                    "scale_adjusted_maximum_input_area_px"
                ],
                "union_area_ratio": canvas_geometry["union_area_ratio"],
                "union_threshold_ratio": canvas_geometry["union_threshold_ratio"],
                "output_width_px": output_meta.get("width") if output_meta else None,
                "output_height_px": output_meta.get("height") if output_meta else None,
                "output_area_px": output_area,
                "maximum_center_separation_deg": center_span,
                "scale": args.scale,
            },
            "artifacts": {
                "linear_fits": _artifact_record(linear_path),
                "preview_jpeg": _artifact_record(preview_path),
                "siril_log": _artifact_record(log_path),
                "manifest": _artifact_record(manifest_path),
                "siril_initfile_final": _artifact_record(isolated_initfile) if isolated_initfile else None,
            },
            "visual_review": {
                "required": True,
                "status": "pending",
                "checks": [
                    "target_complete",
                    "alignment_no_duplicate_stars",
                    "seams_and_background",
                    "no_internal_black_gaps",
                    "source_structure_preserved",
                ],
            },
            "warnings": inspection["warnings"]
            + (["Siril >=1.5 has not been forward-tested by this skill"] if tool["version_warning"] else [])
            + (["No Siril init file was found; global Siril preferences may be updated"] if not isolated_initfile else []),
        }
        atomic_write_json(result_path, result)

        if execution_ok and not args.keep_work:
            shutil.rmtree(staging_dir)
            shutil.rmtree(process_dir)
        return result
    except Exception as exc:
        failure = {
            "schema": "siril-mosaic.result/v1",
            "created_at": utc_now(),
            "status": "failed",
            "execution": {"status": "failed", "error": str(exc)},
        }
        atomic_write_json(result_path, failure)
        raise


def record_review(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = expand_path(args.run_dir)
    result_path = run_dir / "result.json"
    if not result_path.is_file():
        raise MosaicError(f"Missing result.json: {result_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("execution", {}).get("status") != "succeeded":
        raise MosaicError("Cannot accept a run whose execution gates did not pass")

    artifacts = result.get("artifacts", {})
    for key in ("linear_fits", "preview_jpeg"):
        record = artifacts.get(key)
        if not record:
            raise MosaicError(f"Missing artifact record: {key}")
        path = Path(record["path"])
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise MosaicError(f"Artifact missing or hash drifted: {path}")

    checks = {
        "target_complete": args.target_complete,
        "alignment_no_duplicate_stars": args.alignment,
        "seams_and_background": args.seams,
        "no_internal_black_gaps": args.black_gaps,
        "source_structure_preserved": args.source_structure,
    }
    if args.verdict == "accept" and any(value != "pass" for value in checks.values()):
        raise MosaicError("An accepted review requires every visual check to pass")

    review = {
        "schema": "siril-mosaic.review/v1",
        "created_at": utc_now(),
        "verdict": args.verdict,
        "reviewed_preview": artifacts["preview_jpeg"],
        "reviewed_linear_fits": artifacts["linear_fits"],
        "checks": checks,
        "notes": args.notes,
    }
    review_path = run_dir / "review.json"
    atomic_write_json(review_path, review)
    result["status"] = "success" if args.verdict == "accept" else "review_required"
    result["visual_review"] = {
        "required": True,
        "status": "accepted" if args.verdict == "accept" else "review_required",
        "receipt": {"path": str(review_path), "sha256": sha256_file(review_path)},
    }
    atomic_write_json(result_path, result)
    return review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect and stitch already-stacked astronomical panels with Siril 1.4+"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="Locate and validate siril-cli")
    probe.add_argument("--siril-cli")

    inspect = subparsers.add_parser("inspect", help="Read-only inspection of a panel directory")
    inspect.add_argument("input_dir")
    inspect.add_argument("--recursive", action="store_true")
    inspect.add_argument("--out", help="Optional JSON output path; stdout is always printed")

    run = subparsers.add_parser("run", help="Create an isolated Siril mosaic run")
    run.add_argument("input_dir")
    run.add_argument("--output-dir", required=True)
    run.add_argument("--siril-cli")
    run.add_argument("--initfile", help="Siril config to copy into the isolated run")
    run.add_argument("--recursive", action="store_true")
    run.add_argument("--offline", action="store_true")
    run.add_argument("--focal-mm", type=float)
    run.add_argument("--pixel-size-um", type=float)
    run.add_argument(
        "--catalog",
        choices=("tycho2", "nomad", "localgaia", "gaia", "ppmxl", "brightstars", "apass"),
    )
    run.add_argument("--local-astrometry-net", action="store_true")
    run.add_argument("--blind-position", action="store_true")
    run.add_argument("--blind-resolution", action="store_true")
    run.add_argument("--scale", type=float, default=1.0)
    run.add_argument("--feather", type=int, default=None, help="Pixels; default is 4%% of the shortest panel side")
    run.add_argument("--preview-background", type=float, default=0.20)
    run.add_argument("--output-name", default="mosaic_linear")
    run.add_argument("--timeout", type=int, default=3600)
    run.add_argument("--keep-work", action="store_true")

    review = subparsers.add_parser("review", help="Hash-bind a completed visual review")
    review.add_argument("run_dir")
    review.add_argument("--verdict", choices=("accept", "review_required"), required=True)
    for option in ("target-complete", "alignment", "seams", "black-gaps", "source-structure"):
        review.add_argument(f"--{option}", choices=("pass", "fail", "unknown"), required=True)
    review.add_argument("--notes", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            payload = probe_siril(args.siril_cli)
        elif args.command == "inspect":
            payload = inspect_directory(expand_path(args.input_dir), recursive=args.recursive)
            if args.out:
                atomic_write_json(expand_path(args.out), payload)
        elif args.command == "run":
            payload = execute_run(args)
        elif args.command == "review":
            payload = record_review(args)
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload.get("status") != "failed" else 1
    except (MosaicError, OSError, subprocess.SubprocessError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
