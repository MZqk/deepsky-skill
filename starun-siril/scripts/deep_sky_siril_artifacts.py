#!/usr/bin/env python3
"""Pure offline parsers for starun-siril scientific and display artifacts.

This module performs no subprocess execution, network access, environment
discovery, session mutation, or workflow orchestration.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import os
from pathlib import Path
import re
import struct
from typing import Any, Sequence
import xml.etree.ElementTree as ET
import zlib

from deep_sky_siril_contract import SCHEMA_PREFIX, ContractError
from deep_sky_siril_contract import LOG_DIAGNOSTICS_POLICY

try:
    from PIL import Image
except ImportError:  # Pillow is optional to import, but display delivery fails closed without it.
    Image = None  # type: ignore[assignment]


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_STAT_LINE = re.compile(
    rf"(?:(?P<color>Red|Green|Blue|Gray|Grey|B&W|Luminance)\s+layer|"
    rf"(?P<indexed>Layer\s*#?\d+)):\s*"
    rf"Mean:\s*(?P<mean>{_NUMBER}),\s*"
    rf"Median:\s*(?P<median>{_NUMBER}),\s*"
    rf"Sigma:\s*(?P<sigma>{_NUMBER}),\s*"
    rf"Min:\s*(?P<minimum>{_NUMBER}),\s*"
    rf"Max:\s*(?P<maximum>{_NUMBER}),\s*"
    rf"bgnoise:\s*(?P<bgnoise>{_NUMBER}),\s*"
    rf"avgDev:\s*(?P<avg_dev>{_NUMBER}),\s*"
    rf"MAD:\s*(?P<mad>{_NUMBER}),\s*"
    rf"sqrt\(BWMV\):\s*(?P<sqrt_bwmv>{_NUMBER})",
    re.IGNORECASE,
)
_READ_IMAGE_LINE = re.compile(
    r"^(?:log:\s*)?Reading\s+"
    r"(?P<format>FITS|XISF|JPG|JPEG|PNG|TIF|TIFF):\s+file\s+"
    r"(?P<file>.+?),\s*(?P<layers>\d+)\s+layer(?:\(s\)|s)?\s*,\s*"
    r"\d+x\d+\s+pixels"
    r"(?:\s*,\s*(?P<bits>\d+)\s+bits)?",
    re.IGNORECASE | re.MULTILINE,
)
_PYTHON_ERROR_LINE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception):",
)
_JPEG_EXIF_ERROR = "Error: Unable to read EXIF metadata"
_BROKEN_PIPE_ERROR = "BrokenPipeError: [Errno 32] Broken pipe"
_BROKEN_PIPE_COMPANION = "Exception ignored while flushing sys.stdout:"
_SCRIPT_SUCCESS = "log: Script execution finished successfully."
_AUTOSTRETCH_MTF = re.compile(
    rf"Applying MTF with values\s+"
    rf"(?P<low>{_NUMBER}),\s*(?P<mid>{_NUMBER}),\s*(?P<high>{_NUMBER})",
    re.IGNORECASE,
)
_SIRIL_REOPEN_LINE = re.compile(
    r"Reading\s+(?P<format>FITS|XISF):\s+file\s+.+?,\s*"
    r"(?P<channels>\d+)\s+layer(?:\(s\)|s)?\s*,\s*"
    r"(?P<width>\d+)x(?P<height>\d+)\s+pixels"
    r"(?:\s*,\s*(?P<bits>\d+)\s+bits)?",
    re.IGNORECASE,
)


class _ArtifactValidationError(ContractError):
    """Internal artifact error carrying a stable per-output reason code."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__("artifact_invalid", message)
        self.reason_code = reason_code


def _looks_like_runtime_error(text: str) -> bool:
    lowered = text.lower()
    return bool(
        lowered.startswith("error:")
        or lowered.startswith("traceback")
        or lowered.startswith("exception ")
        or lowered.startswith("exception:")
        or _PYTHON_ERROR_LINE.match(text)
    )


def diagnose_siril_log(
    output: str,
    *,
    exit_code: int,
    timed_out: bool,
    execution_valid: bool,
    validated_display_names: Sequence[str] = (),
) -> dict[str, Any]:
    """Classify Siril stderr/stdout without treating exit zero as sufficient.

    The two warning cases are deliberately contextual.  Every other line that
    looks like an error, exception, or traceback remains fatal and visible in
    the immutable run receipt.
    """

    lines = output.splitlines()
    normalized = [line.strip() for line in lines]
    validated_names = {
        Path(str(name).strip().strip('"')).name for name in validated_display_names
    }
    success_lines = {
        index
        for index, line in enumerate(normalized)
        if line == _SCRIPT_SUCCESS
    }

    safe_exif_lines: set[int] = set()
    for index, line in enumerate(normalized):
        if line != _JPEG_EXIF_ERROR or not execution_valid:
            continue
        read_index: int | None = None
        read_match: re.Match[str] | None = None
        for candidate_index in range(index + 1, len(lines)):
            candidate = _READ_IMAGE_LINE.match(lines[candidate_index].strip())
            if candidate is not None:
                read_index = candidate_index
                read_match = candidate
                break
        if read_index is None or read_match is None:
            continue
        if read_match.group("format").upper() not in {"JPG", "JPEG"}:
            continue
        loaded_name = Path(read_match.group("file").strip().strip('"')).name
        if loaded_name not in validated_names:
            continue
        next_read = len(lines)
        for candidate_index in range(read_index + 1, len(lines)):
            if _READ_IMAGE_LINE.match(lines[candidate_index].strip()) is not None:
                next_read = candidate_index
                break
        statistic_count = sum(
            1
            for candidate in lines[read_index + 1 : next_read]
            if _STAT_LINE.search(candidate) is not None
        )
        if statistic_count >= int(read_match.group("layers")):
            safe_exif_lines.add(index)

    safe_broken_pipe_lines: set[int] = set()
    safe_broken_pipe_companions: set[int] = set()
    for index, line in enumerate(normalized):
        if line != _BROKEN_PIPE_ERROR:
            continue
        if (
            exit_code == 0
            and not timed_out
            and execution_valid
            and any(success_index < index for success_index in success_lines)
        ):
            safe_broken_pipe_lines.add(index)
            previous = index - 1
            while previous >= 0 and not normalized[previous]:
                previous -= 1
            if previous >= 0 and normalized[previous] == _BROKEN_PIPE_COMPANION:
                safe_broken_pipe_companions.add(previous)

    findings: list[dict[str, Any]] = []

    def finding(index: int, *, code: str, severity: str) -> None:
        findings.append(
            {
                "code": code,
                "severity": severity,
                "line_number": index + 1,
                "text": normalized[index][:500],
            }
        )

    for index, line in enumerate(normalized):
        if not line:
            continue
        if line == _JPEG_EXIF_ERROR:
            if index in safe_exif_lines:
                finding(index, code="jpeg_exif_unavailable", severity="warning")
            else:
                finding(index, code="unclassified_siril_error", severity="fatal")
            continue
        if line == _BROKEN_PIPE_ERROR:
            if index in safe_broken_pipe_lines:
                finding(index, code="post_success_pipe_flush", severity="warning")
            else:
                finding(index, code="unclassified_siril_error", severity="fatal")
            continue
        if line == _BROKEN_PIPE_COMPANION and index in safe_broken_pipe_companions:
            continue
        if _looks_like_runtime_error(line):
            finding(index, code="unclassified_siril_error", severity="fatal")

    warning_count = sum(item["severity"] == "warning" for item in findings)
    fatal_count = sum(item["severity"] == "fatal" for item in findings)
    return {
        "policy": LOG_DIAGNOSTICS_POLICY,
        "status": "failed" if fatal_count else "warning" if warning_count else "clean",
        "warning_count": warning_count,
        "fatal_count": fatal_count,
        "findings": findings,
    }


def _statistics_domain(
    source_format: str | None,
    bits: str | None,
) -> tuple[str, float]:
    normalized_format = (source_format or "unknown").upper()
    normalized_format = {"JPG": "JPEG", "TIF": "TIFF"}.get(
        normalized_format,
        normalized_format,
    )
    if normalized_format == "JPEG":
        return normalized_format, 255.0
    if bits is not None and int(bits) <= 8:
        return normalized_format, 255.0
    return normalized_format, 65535.0


def _statistics_record(
    channels: dict[str, Any],
    *,
    source_format: str,
    native_denominator: float,
) -> dict[str, Any]:
    return {
        "schema": f"{SCHEMA_PREFIX}.siril-statistics.v1",
        "source": "siril_stat_main_log",
        "source_format": source_format,
        "native_denominator": native_denominator,
        "normalization_denominator": 65535.0,
        "channels": channels,
    }


def parse_statistics_samples(output: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    channels: dict[str, Any] = {}
    read_events = list(_READ_IMAGE_LINE.finditer(output))
    read_index = -1
    sample_domain: tuple[str, float] | None = None
    names = {"red": "red", "green": "green", "blue": "blue", "gray": "mono", "grey": "mono", "b&w": "mono", "luminance": "mono"}
    fields = ("mean", "median", "sigma", "minimum", "maximum", "bgnoise", "avg_dev", "mad", "sqrt_bwmv")
    for match in _STAT_LINE.finditer(output):
        while (
            read_index + 1 < len(read_events)
            and read_events[read_index + 1].start() < match.start()
        ):
            read_index += 1
        if read_index >= 0:
            read_event = read_events[read_index]
            domain = _statistics_domain(
                read_event.group("format"),
                read_event.group("bits"),
            )
        else:
            domain = _statistics_domain(None, None)
        if channels and sample_domain != domain:
            assert sample_domain is not None
            samples.append(
                _statistics_record(
                    channels,
                    source_format=sample_domain[0],
                    native_denominator=sample_domain[1],
                )
            )
            channels = {}
        sample_domain = domain
        color = match.group("color")
        if color:
            channel = names[color.lower()]
        else:
            index = re.search(r"\d+", match.group("indexed") or "")
            channel = f"channel{index.group(0)}" if index else "mono"
        if channel in channels:
            assert sample_domain is not None
            samples.append(
                _statistics_record(
                    channels,
                    source_format=sample_domain[0],
                    native_denominator=sample_domain[1],
                )
            )
            channels = {}
        reported = {name: float(match.group(name)) for name in fields}
        native_denominator = domain[1]
        scale_to_16_bit = 65535.0 / native_denominator
        adu = {name: value * scale_to_16_bit for name, value in reported.items()}
        channels[channel] = {
            "adu_16_equivalent": adu,
            "normalized": {
                name: value / native_denominator
                for name, value in reported.items()
            },
        }
    if channels:
        assert sample_domain is not None
        samples.append(
            _statistics_record(
                channels,
                source_format=sample_domain[0],
                native_denominator=sample_domain[1],
            )
        )
    return samples


def parse_autostretch_mtf(output: str) -> list[float] | None:
    matches = list(_AUTOSTRETCH_MTF.finditer(output))
    if not matches:
        return None
    values = [float(matches[-1].group(name)) for name in ("low", "mid", "high")]
    return values if all(0.0 <= value <= 1.0 for value in values) else None


def parse_siril_reopen_metadata(
    output: str,
    *,
    expected_format: str,
) -> dict[str, Any]:
    """Extract the scientific image identity reported by Siril's ``load`` log."""

    expected = expected_format.upper()
    matches = [
        match
        for match in _SIRIL_REOPEN_LINE.finditer(output)
        if match.group("format").upper() == expected
    ]
    if not matches:
        raise _ArtifactValidationError(
            "siril_metadata_missing",
            f"Siril did not report {expected} format, dimensions, and channels",
        )
    match = matches[-1]
    width = int(match.group("width"))
    height = int(match.group("height"))
    channels = int(match.group("channels"))
    if width < 1 or height < 1 or channels not in {1, 3}:
        raise _ArtifactValidationError(
            "siril_metadata_invalid",
            "Siril reported unsupported scientific image geometry",
        )
    geometry: dict[str, int] = {
        "width": width,
        "height": height,
        "channels": channels,
    }
    bits = match.group("bits")
    if bits is not None:
        geometry["sample_bits"] = int(bits)
    return {
        "source": "siril-load-log",
        "format": expected,
        "geometry": geometry,
    }


def _parse_fits_value(raw: str) -> Any:
    value = raw.strip()
    if value.startswith("'"):
        end = value.find("'", 1)
        return value[1:end].replace("''", "'").strip() if end >= 0 else value[1:].strip()
    token = value.split("/", 1)[0].strip()
    if token in {"T", "F"}:
        return token == "T"
    try:
        return float(token.replace("D", "E")) if any(char in token for char in ".EeDd") else int(token)
    except ValueError:
        return token


def read_fits_header(path: Path) -> dict[str, Any]:
    values: dict[str, Any] = {}
    try:
        with path.open("rb") as stream:
            for _block in range(4096):
                data = stream.read(2880)
                if len(data) != 2880:
                    raise ContractError("artifact_invalid", f"Incomplete FITS header: {path}")
                for offset in range(0, 2880, 80):
                    card = data[offset : offset + 80].decode("ascii", errors="replace")
                    key = card[:8].strip().upper()
                    if key == "END":
                        return values
                    if key and card[8:10] == "= ":
                        values[key] = _parse_fits_value(card[10:])
    except OSError as exc:
        raise ContractError("artifact_invalid", f"Cannot read FITS header: {path}") from exc
    raise ContractError("artifact_invalid", f"FITS END card was not found: {path}")


def fits_geometry(path: Path) -> dict[str, int]:
    header = read_fits_header(path)
    try:
        naxis = int(header["NAXIS"])
        width = int(header["NAXIS1"])
        height = int(header["NAXIS2"])
        channels = int(header.get("NAXIS3", 1)) if naxis >= 3 else 1
        bitpix = int(header["BITPIX"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("artifact_invalid", f"Incomplete FITS geometry: {path}") from exc
    if width < 1 or height < 1 or channels not in {1, 3}:
        raise ContractError("artifact_invalid", f"Unsupported FITS geometry: {path}")
    return {"width": width, "height": height, "channels": channels, "bitpix": bitpix}


def _artifact_validation_error(reason_code: str, message: str) -> None:
    raise _ArtifactValidationError(reason_code, message)


def _required_fits_integer(
    values: dict[str, Any],
    key: str,
    *,
    default: int | None = None,
) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        _artifact_validation_error(
            "container_invalid",
            f"FITS keyword {key} is missing or is not an integer",
        )
    return value


def _fits_candidate_geometry(
    values: dict[str, Any],
    *,
    hdu_index: int,
) -> dict[str, int] | None:
    extension = str(values.get("XTENSION", "")).strip().upper()
    compressed = extension == "BINTABLE" and values.get("ZIMAGE") is True
    if compressed:
        axis_prefix = "ZNAXIS"
        bitpix_key = "ZBITPIX"
    elif hdu_index == 0 or extension == "IMAGE":
        axis_prefix = "NAXIS"
        bitpix_key = "BITPIX"
    else:
        return None

    naxis = _required_fits_integer(values, axis_prefix)
    if naxis < 2 or naxis > 999:
        return None
    axes = [
        _required_fits_integer(values, f"{axis_prefix}{index}")
        for index in range(1, naxis + 1)
    ]
    if any(axis < 1 for axis in axes):
        return None
    if naxis > 3 and any(axis != 1 for axis in axes[3:]):
        return None
    channels = axes[2] if naxis >= 3 else 1
    if channels not in {1, 3}:
        return None
    bitpix = _required_fits_integer(values, bitpix_key)
    if bitpix not in {8, 16, 32, 64, -32, -64}:
        _artifact_validation_error(
            "container_invalid",
            f"FITS image uses unsupported {bitpix_key}={bitpix}",
        )
    return {
        "width": axes[0],
        "height": axes[1],
        "channels": channels,
        "bitpix": bitpix,
    }


def inspect_fits_container(path: Path) -> dict[str, Any]:
    """Walk every FITS HDU and prove that all declared bytes are present."""

    if path.is_symlink() or not path.is_file():
        _artifact_validation_error("missing_or_empty", f"Unsafe FITS artifact: {path}")
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise _ArtifactValidationError(
            "missing_or_empty", f"Cannot inspect FITS artifact: {path}"
        ) from exc
    if file_size < 2880:
        _artifact_validation_error("payload_truncated", f"FITS file is too short: {path}")

    hdu_count = 0
    image_geometries: list[dict[str, int]] = []
    try:
        with path.open("rb") as stream:
            while stream.tell() < file_size:
                values: dict[str, Any] = {}
                seen_keywords: set[str] = set()
                first_keyword: str | None = None
                end_found = False
                for _block_index in range(4096):
                    block = stream.read(2880)
                    if len(block) != 2880:
                        _artifact_validation_error(
                            "payload_truncated",
                            f"FITS HDU {hdu_count} has a truncated header block",
                        )
                    for offset in range(0, 2880, 80):
                        raw_card = block[offset : offset + 80]
                        if any(byte < 0x20 or byte > 0x7E for byte in raw_card):
                            _artifact_validation_error(
                                "container_invalid",
                                f"FITS HDU {hdu_count} contains a non-printable header byte",
                            )
                        card = raw_card.decode("ascii")
                        keyword = card[:8].strip().upper()
                        if first_keyword is None:
                            first_keyword = keyword
                        if keyword == "END":
                            if raw_card != b"END" + b" " * 77:
                                _artifact_validation_error(
                                    "container_invalid",
                                    f"FITS HDU {hdu_count} has a malformed END card",
                                )
                            if block[offset + 80 :] != b" " * (2800 - offset):
                                _artifact_validation_error(
                                    "container_invalid",
                                    f"FITS HDU {hdu_count} header padding is not blank",
                                )
                            end_found = True
                            break
                        if keyword and card[8:10] == "= ":
                            if keyword in seen_keywords and (
                                keyword in {"SIMPLE", "XTENSION", "BITPIX", "NAXIS", "PCOUNT", "GCOUNT"}
                                or keyword.startswith("NAXIS")
                            ):
                                _artifact_validation_error(
                                    "container_invalid",
                                    f"FITS HDU {hdu_count} repeats mandatory keyword {keyword}",
                                )
                            seen_keywords.add(keyword)
                            values[keyword] = _parse_fits_value(card[10:])
                    if end_found:
                        break
                if not end_found:
                    _artifact_validation_error(
                        "container_invalid",
                        f"FITS HDU {hdu_count} has no END card",
                    )

                expected_first = "SIMPLE" if hdu_count == 0 else "XTENSION"
                if first_keyword != expected_first:
                    _artifact_validation_error(
                        "container_invalid",
                        f"FITS HDU {hdu_count} must begin with {expected_first}",
                    )
                if hdu_count == 0 and values.get("SIMPLE") is not True:
                    _artifact_validation_error(
                        "container_invalid", "FITS primary HDU is not SIMPLE=T"
                    )
                extension = str(values.get("XTENSION", "")).strip().upper()
                if hdu_count > 0 and extension not in {"IMAGE", "TABLE", "BINTABLE"}:
                    _artifact_validation_error(
                        "container_invalid",
                        f"FITS HDU {hdu_count} uses unsupported XTENSION={extension or 'missing'}",
                    )

                bitpix = _required_fits_integer(values, "BITPIX")
                if bitpix not in {8, 16, 32, 64, -32, -64}:
                    _artifact_validation_error(
                        "container_invalid",
                        f"FITS HDU {hdu_count} uses unsupported BITPIX={bitpix}",
                    )
                naxis = _required_fits_integer(values, "NAXIS")
                if naxis < 0 or naxis > 999:
                    _artifact_validation_error(
                        "container_invalid", f"FITS HDU {hdu_count} has invalid NAXIS"
                    )
                axes = [
                    _required_fits_integer(values, f"NAXIS{index}")
                    for index in range(1, naxis + 1)
                ]
                if any(axis < 0 for axis in axes):
                    _artifact_validation_error(
                        "container_invalid",
                        f"FITS HDU {hdu_count} has a negative axis",
                    )
                if values.get("GROUPS") is True:
                    _artifact_validation_error(
                        "container_invalid",
                        "FITS random-groups data is outside the supported contract",
                    )
                pcount = _required_fits_integer(values, "PCOUNT", default=0)
                gcount = _required_fits_integer(values, "GCOUNT", default=1)
                if pcount < 0 or gcount < 1:
                    _artifact_validation_error(
                        "container_invalid",
                        f"FITS HDU {hdu_count} has invalid PCOUNT/GCOUNT",
                    )
                elements = math.prod(axes) if axes else 0
                data_size = (abs(bitpix) // 8) * gcount * (pcount + elements)
                padded_size = data_size + (-data_size % 2880)
                if stream.tell() + padded_size > file_size:
                    _artifact_validation_error(
                        "payload_truncated",
                        f"FITS HDU {hdu_count} data or padding is truncated",
                    )

                geometry = _fits_candidate_geometry(values, hdu_index=hdu_count)
                if geometry is not None:
                    image_geometries.append(geometry)
                stream.seek(data_size, os.SEEK_CUR)
                padding_size = padded_size - data_size
                if padding_size:
                    padding = stream.read(padding_size)
                    padding_byte = b" " if extension == "TABLE" else b"\0"
                    if padding != padding_byte * padding_size:
                        _artifact_validation_error(
                            "container_invalid",
                            f"FITS HDU {hdu_count} data padding is invalid",
                        )
                hdu_count += 1
    except _ArtifactValidationError:
        raise
    except OSError as exc:
        raise _ArtifactValidationError(
            "container_invalid", f"Cannot read FITS artifact: {path}"
        ) from exc

    if hdu_count < 1 or not image_geometries:
        _artifact_validation_error(
            "container_invalid", f"FITS contains no supported image HDU: {path}"
        )
    return {
        "format": "FITS",
        "hdu_count": hdu_count,
        "image_count": len(image_geometries),
        "geometry": image_geometries[0],
    }


_XISF_SAMPLE_FORMATS: dict[str, tuple[int, int]] = {
    "UInt8": (8, 1),
    "UInt16": (16, 2),
    "UInt32": (32, 4),
    "UInt64": (64, 8),
    "Int8": (8, 1),
    "Int16": (16, 2),
    "Int32": (32, 4),
    "Int64": (64, 8),
    "Float32": (-32, 4),
    "Float64": (-64, 8),
}
_XISF_CHECKSUMS: dict[str, tuple[str, str]] = {
    "sha1": ("sha1", "sha1"),
    "sha-1": ("sha1", "sha1"),
    "sha256": ("sha256", "sha256"),
    "sha-256": ("sha256", "sha256"),
    "sha512": ("sha512", "sha512"),
    "sha-512": ("sha512", "sha512"),
    "sha3-256": ("sha3_256", "sha3-256"),
    "sha3-512": ("sha3_512", "sha3-512"),
}


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decode_xisf_embedded(image: ET.Element) -> bytes:
    data_nodes = [child for child in image if _xml_local_name(child.tag) == "Data"]
    if len(data_nodes) != 1:
        _artifact_validation_error(
            "container_invalid", "Embedded XISF image requires exactly one Data element"
        )
    data_node = data_nodes[0]
    encoding = str(data_node.attrib.get("encoding", "")).lower()
    encoded = "".join((data_node.text or "").split())
    try:
        if encoding == "base64":
            return base64.b64decode(encoded, validate=True)
        if encoding == "hex":
            return base64.b16decode(encoded.upper(), casefold=False)
    except (binascii.Error, ValueError) as exc:
        raise _ArtifactValidationError(
            "container_invalid", "Embedded XISF image data is malformed"
        ) from exc
    _artifact_validation_error(
        "container_invalid", f"Unsupported embedded XISF encoding: {encoding or 'missing'}"
    )


def _hash_path_range(path: Path, offset: int, size: int, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            remaining = size
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    _artifact_validation_error(
                        "payload_truncated", "XISF attachment ended during checksum validation"
                    )
                digest.update(chunk)
                remaining -= len(chunk)
    except _ArtifactValidationError:
        raise
    except OSError as exc:
        raise _ArtifactValidationError(
            "container_invalid", f"Cannot read XISF attachment: {path}"
        ) from exc
    return digest.hexdigest()


def _validate_xisf_zlib_payload(
    path: Path,
    *,
    attachment_offset: int | None,
    stored_size: int,
    block_bytes: bytes | None,
    expected_size: int,
) -> None:
    """Bound zlib expansion and prove the declared uncompressed byte count."""

    decompressor = zlib.decompressobj()
    produced = 0

    def consume(compressed: bytes) -> None:
        nonlocal produced
        pending = compressed
        while pending:
            allowance = min(1024 * 1024, expected_size - produced + 1)
            expanded = decompressor.decompress(pending, allowance)
            produced += len(expanded)
            if produced > expected_size:
                _artifact_validation_error(
                    "decompressed_size_mismatch",
                    "XISF zlib payload expands beyond its declared image size",
                )
            next_pending = decompressor.unconsumed_tail
            if next_pending and not expanded and next_pending == pending:
                _artifact_validation_error(
                    "container_invalid", "XISF zlib stream made no decoding progress"
                )
            pending = next_pending

    try:
        if block_bytes is not None:
            consume(block_bytes)
        else:
            assert attachment_offset is not None
            with path.open("rb") as stream:
                stream.seek(attachment_offset)
                remaining = stored_size
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        _artifact_validation_error(
                            "payload_truncated",
                            "XISF attachment ended during zlib validation",
                        )
                    consume(chunk)
                    remaining -= len(chunk)
    except _ArtifactValidationError:
        raise
    except (OSError, zlib.error) as exc:
        raise _ArtifactValidationError(
            "container_invalid", "XISF zlib payload is malformed"
        ) from exc

    if not decompressor.eof or decompressor.unused_data or produced != expected_size:
        _artifact_validation_error(
            "decompressed_size_mismatch",
            "XISF zlib payload does not match its declared image size",
        )


def inspect_xisf_container(path: Path) -> dict[str, Any]:
    """Validate a monolithic XISF image block without decoding its pixels."""

    if path.is_symlink() or not path.is_file():
        _artifact_validation_error("missing_or_empty", f"Unsafe XISF artifact: {path}")
    try:
        file_size = path.stat().st_size
        with path.open("rb") as stream:
            preamble = stream.read(16)
            if len(preamble) != 16:
                _artifact_validation_error(
                    "payload_truncated", f"XISF preamble is truncated: {path}"
                )
            if preamble[:8] != b"XISF0100":
                _artifact_validation_error(
                    "container_invalid", f"XISF signature is invalid: {path}"
                )
            header_length = struct.unpack("<I", preamble[8:12])[0]
            if preamble[12:16] != b"\0\0\0\0":
                _artifact_validation_error(
                    "container_invalid", "XISF reserved preamble bytes must be zero"
                )
            if (
                header_length < 1
                or header_length > 64 * 1024 * 1024
                or 16 + header_length > file_size
            ):
                _artifact_validation_error(
                    "payload_truncated", "XISF XML header length exceeds file bounds"
                )
            xml_bytes = stream.read(header_length)
    except _ArtifactValidationError:
        raise
    except OSError as exc:
        raise _ArtifactValidationError(
            "container_invalid", f"Cannot read XISF artifact: {path}"
        ) from exc

    upper_xml = xml_bytes.upper()
    if b"<!DOCTYPE" in upper_xml or b"<!ENTITY" in upper_xml:
        _artifact_validation_error(
            "external_reference_forbidden", "XISF DTD and entity declarations are forbidden"
        )
    try:
        xml_text = xml_bytes.decode("utf-8")
        root = ET.fromstring(xml_text)
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise _ArtifactValidationError(
            "container_invalid", "XISF XML header is not valid UTF-8 XML"
        ) from exc
    if _xml_local_name(root.tag) != "xisf" or root.attrib.get("version") != "1.0":
        _artifact_validation_error(
            "container_invalid", "XISF root element or version is invalid"
        )
    for element in root.iter():
        for attribute, raw_value in element.attrib.items():
            attribute_name = _xml_local_name(attribute).lower()
            value = str(raw_value).strip().lower()
            if attribute_name in {"location", "href", "src"} and value.startswith(
                ("url:", "url(", "path:", "path(", "http:", "https:", "file:")
            ):
                _artifact_validation_error(
                    "external_reference_forbidden",
                    "External XISF data references are forbidden",
                )
    images = [child for child in root if _xml_local_name(child.tag) == "Image"]
    if len(images) != 1:
        _artifact_validation_error(
            "container_invalid", "XISF outputs require exactly one Image"
        )
    image = images[0]

    try:
        geometry_values = [int(value) for value in image.attrib["geometry"].split(":")]
    except (KeyError, ValueError) as exc:
        raise _ArtifactValidationError(
            "container_invalid", "XISF Image geometry is missing or invalid"
        ) from exc
    if len(geometry_values) != 3 or any(value < 1 for value in geometry_values):
        _artifact_validation_error(
            "container_invalid", "XISF Image must be a non-empty 2D image"
        )
    width, height, channels = geometry_values
    color_space = str(image.attrib.get("colorSpace", ""))
    if (color_space, channels) not in {("Gray", 1), ("RGB", 3)}:
        _artifact_validation_error(
            "container_invalid", "XISF color space does not match image channels"
        )
    sample_format = str(image.attrib.get("sampleFormat", ""))
    sample = _XISF_SAMPLE_FORMATS.get(sample_format)
    if sample is None:
        _artifact_validation_error(
            "container_invalid", f"Unsupported XISF sample format: {sample_format or 'missing'}"
        )
    bitpix, item_size = sample
    expected_size = width * height * channels * item_size

    location_value = str(image.attrib.get("location", ""))
    location_lower = location_value.lower()
    if location_lower.startswith(("url:", "url(", "path:", "path(")):
        _artifact_validation_error(
            "external_reference_forbidden", "External XISF image blocks are forbidden"
        )
    block_bytes: bytes | None = None
    attachment_offset: int | None = None
    if location_value == "embedded":
        location = "embedded"
        block_bytes = _decode_xisf_embedded(image)
        stored_size = len(block_bytes)
    else:
        parts = location_value.split(":")
        if len(parts) != 3 or parts[0] not in {"attachment", "attached"}:
            _artifact_validation_error(
                "container_invalid", f"Unsupported monolithic XISF location: {location_value}"
            )
        try:
            attachment_offset = int(parts[1])
            stored_size = int(parts[2])
        except ValueError as exc:
            raise _ArtifactValidationError(
                "container_invalid", "XISF attachment offset or size is invalid"
            ) from exc
        header_end = 16 + header_length
        if (
            attachment_offset < header_end
            or stored_size < 1
            or attachment_offset + stored_size > file_size
        ):
            _artifact_validation_error(
                "payload_truncated", "XISF attachment lies outside the file bounds"
            )
        location = "attachment"

    compression_value = image.attrib.get("compression")
    compression: str | None = None
    if compression_value is None:
        if stored_size != expected_size:
            _artifact_validation_error(
                "payload_truncated", "XISF image byte count does not match its geometry"
            )
    else:
        compression_parts = compression_value.split(":")
        if len(compression_parts) not in {2, 3}:
            _artifact_validation_error(
                "container_invalid", "XISF compression descriptor is invalid"
            )
        compression = compression_parts[0]
        codec = compression.removesuffix("+sh")
        if codec != "zlib":
            _artifact_validation_error(
                "container_invalid",
                f"XISF compression codec cannot be predecoded safely: {compression}",
            )
        try:
            uncompressed_size = int(compression_parts[1])
        except ValueError as exc:
            raise _ArtifactValidationError(
                "container_invalid", "XISF uncompressed byte count is invalid"
            ) from exc
        if uncompressed_size != expected_size:
            _artifact_validation_error(
                "container_invalid", "XISF declared uncompressed size does not match geometry"
            )
        shuffled = compression.endswith("+sh")
        if shuffled:
            if len(compression_parts) != 3:
                _artifact_validation_error(
                    "container_invalid", "Shuffled XISF compression lacks an item size"
                )
            try:
                shuffle_item_size = int(compression_parts[2])
            except ValueError as exc:
                raise _ArtifactValidationError(
                    "container_invalid", "XISF shuffle item size is invalid"
                ) from exc
            if shuffle_item_size != item_size:
                _artifact_validation_error(
                    "container_invalid", "XISF shuffle item size differs from sample format"
                )
        elif len(compression_parts) != 2:
            _artifact_validation_error(
                "container_invalid", "Unshuffled XISF compression has an extra item size"
            )

    checksum_value = image.attrib.get("checksum")
    checksum: str | None = None
    if checksum_value is not None:
        algorithm_value, separator, expected_digest = checksum_value.partition(":")
        checksum_record = _XISF_CHECKSUMS.get(algorithm_value.lower())
        if not separator or checksum_record is None:
            _artifact_validation_error(
                "container_invalid", "XISF checksum descriptor is unsupported"
            )
        algorithm, checksum = checksum_record
        digest_size = hashlib.new(algorithm).digest_size * 2
        if (
            len(expected_digest) != digest_size
            or re.fullmatch(r"[0-9a-fA-F]+", expected_digest) is None
        ):
            _artifact_validation_error(
                "container_invalid", "XISF checksum digest is malformed"
            )
        if block_bytes is not None:
            actual_digest = hashlib.new(algorithm, block_bytes).hexdigest()
        else:
            assert attachment_offset is not None
            actual_digest = _hash_path_range(
                path, attachment_offset, stored_size, algorithm
            )
        if actual_digest.lower() != expected_digest.lower():
            _artifact_validation_error(
                "checksum_mismatch", "XISF image block checksum does not match"
            )

    if compression is not None:
        _validate_xisf_zlib_payload(
            path,
            attachment_offset=attachment_offset,
            stored_size=stored_size,
            block_bytes=block_bytes,
            expected_size=expected_size,
        )

    return {
        "format": "XISF",
        "image_count": 1,
        "geometry": {
            "width": width,
            "height": height,
            "channels": channels,
            "bitpix": bitpix,
        },
        "sample_format": sample_format,
        "color_space": color_space,
        "location": location,
        "compression": compression,
        "checksum": checksum,
    }


def validate_display(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 4:
        return {"passed": False, "reason": "missing_or_empty"}
    if Image is not None:
        try:
            with Image.open(path) as image:
                image.load()
                width, height = image.size
                return {
                    "passed": width > 0 and height > 0,
                    "width": width,
                    "height": height,
                    "mode": image.mode,
                    "format": image.format,
                    "decoder": "pillow",
                }
        except (OSError, ValueError) as exc:
            return {"passed": False, "reason": f"decode_failed:{type(exc).__name__}"}
    return {"passed": False, "reason": "pillow_required_for_format"}


__all__ = (
    "Image",
    "_ArtifactValidationError",
    "diagnose_siril_log",
    "fits_geometry",
    "inspect_fits_container",
    "inspect_xisf_container",
    "parse_autostretch_mtf",
    "parse_siril_reopen_metadata",
    "parse_statistics_samples",
    "read_fits_header",
    "validate_display",
)
