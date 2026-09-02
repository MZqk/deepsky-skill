from __future__ import annotations

import base64
import gzip
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
from typing import Callable
import zlib

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import deep_sky_siril_artifacts as artifacts  # noqa: E402
import deep_sky_siril_core as core  # noqa: E402
import deep_sky_siril_tooling as tooling  # noqa: E402


def _fits_card(keyword: str, value: object | None = None) -> bytes:
    if value is None:
        text = keyword
    else:
        if isinstance(value, bool):
            encoded = "T" if value else "F"
        elif isinstance(value, str):
            encoded = f"'{value}'"
        else:
            encoded = str(value)
        text = f"{keyword:<8}= {encoded:>20}"
    return text.ljust(80).encode("ascii")


def _fits_hdu(cards: list[tuple[str, object]], data: bytes = b"") -> bytes:
    header = b"".join(_fits_card(key, value) for key, value in cards)
    header += _fits_card("END")
    header += b" " * (-len(header) % 2880)
    return header + data + b"\0" * (-len(data) % 2880)


def _simple_fits() -> bytes:
    width, height = 4, 3
    pixels = b"".join(struct.pack(">H", value) for value in range(width * height))
    return _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", 16),
            ("NAXIS", 2),
            ("NAXIS1", width),
            ("NAXIS2", height),
            ("EXTEND", True),
        ],
        pixels,
    )


def _multi_hdu_fits() -> bytes:
    primary = _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", 8),
            ("NAXIS", 0),
            ("EXTEND", True),
        ]
    )
    width, height, channels = 2, 2, 3
    pixels = b"".join(
        struct.pack(">f", value / 10) for value in range(width * height * channels)
    )
    image = _fits_hdu(
        [
            ("XTENSION", "IMAGE   "),
            ("BITPIX", -32),
            ("NAXIS", 3),
            ("NAXIS1", width),
            ("NAXIS2", height),
            ("NAXIS3", channels),
            ("PCOUNT", 0),
            ("GCOUNT", 1),
        ],
        pixels,
    )
    return primary + image


def _compressed_image_fits() -> bytes:
    primary = _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", 8),
            ("NAXIS", 0),
            ("EXTEND", True),
        ]
    )
    tiles = [
        b"".join(struct.pack(">h", row * 4 + column) for column in range(4))
        for row in range(3)
    ]
    compressed_tiles = [gzip.compress(tile, compresslevel=9, mtime=0) for tile in tiles]
    offsets: list[int] = []
    offset = 0
    for tile in compressed_tiles:
        offsets.append(offset)
        offset += len(tile)
    rows = b"".join(
        struct.pack(">II", len(tile), tile_offset)
        for tile, tile_offset in zip(compressed_tiles, offsets, strict=True)
    )
    heap = b"".join(compressed_tiles)
    table = _fits_hdu(
        [
            ("XTENSION", "BINTABLE"),
            ("BITPIX", 8),
            ("NAXIS", 2),
            ("NAXIS1", 8),
            ("NAXIS2", 3),
            ("PCOUNT", len(heap)),
            ("GCOUNT", 1),
            ("TFIELDS", 1),
            ("ZIMAGE", True),
            ("ZTENSION", "IMAGE   "),
            ("ZBITPIX", 16),
            ("ZNAXIS", 2),
            ("ZNAXIS1", 4),
            ("ZNAXIS2", 3),
            ("ZPCOUNT", 0),
            ("ZGCOUNT", 1),
            ("ZTILE1", 4),
            ("ZTILE2", 1),
            ("ZCMPTYPE", "GZIP_1"),
            ("TTYPE1", "COMPRESSED_DATA"),
            ("TFORM1", f"1PB({max(map(len, compressed_tiles))})"),
        ],
        rows + heap,
    )
    return primary + table


def _write(path: Path, payload: bytes) -> Path:
    path.write_bytes(payload)
    return path


def _assert_artifact_invalid(function: Callable[[Path], object], path: Path) -> None:
    with pytest.raises(core.ContractError) as caught:
        function(path)
    assert caught.value.code == "artifact_invalid"


def test_inspect_fits_container_reads_complete_primary_image(tmp_path: Path) -> None:
    path = _write(tmp_path / "simple.fit", _simple_fits())

    summary = artifacts.inspect_fits_container(path)

    assert summary["format"] == "FITS"
    assert summary["hdu_count"] == 1
    assert summary["image_count"] == 1
    assert summary["geometry"] == {
        "width": 4,
        "height": 3,
        "channels": 1,
        "bitpix": 16,
    }


def test_inspect_fits_container_walks_all_hdus(tmp_path: Path) -> None:
    path = _write(tmp_path / "multi.fits", _multi_hdu_fits())

    summary = artifacts.inspect_fits_container(path)

    assert summary["hdu_count"] == 2
    assert summary["image_count"] == 1
    assert summary["geometry"] == {
        "width": 2,
        "height": 2,
        "channels": 3,
        "bitpix": -32,
    }


def test_inspect_fits_container_supports_compressed_image_table(tmp_path: Path) -> None:
    path = _write(tmp_path / "compressed.fits", _compressed_image_fits())

    summary = artifacts.inspect_fits_container(path)

    assert summary["hdu_count"] == 2
    assert summary["image_count"] == 1
    assert summary["geometry"] == {
        "width": 4,
        "height": 3,
        "channels": 1,
        "bitpix": 16,
    }


@pytest.mark.parametrize(
    "payload",
    [
        _simple_fits()[:2880],
        _multi_hdu_fits()[:-1],
        _simple_fits() + b"unexpected trailing byte",
    ],
    ids=["missing-primary-data", "truncated-later-hdu", "trailing-garbage"],
)
def test_inspect_fits_container_rejects_incomplete_or_inexact_eof(
    tmp_path: Path,
    payload: bytes,
) -> None:
    path = _write(tmp_path / "broken.fit", payload)
    _assert_artifact_invalid(artifacts.inspect_fits_container, path)


def test_inspect_fits_container_rejects_random_groups(tmp_path: Path) -> None:
    payload = _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", 16),
            ("NAXIS", 3),
            ("NAXIS1", 0),
            ("NAXIS2", 4),
            ("NAXIS3", 3),
            ("GROUPS", True),
            ("PCOUNT", 5),
            ("GCOUNT", 2),
        ]
    )
    path = _write(tmp_path / "groups.fit", payload)
    _assert_artifact_invalid(artifacts.inspect_fits_container, path)


@pytest.mark.parametrize("corruption", ["header-padding", "data-padding"])
def test_inspect_fits_container_rejects_invalid_padding(
    tmp_path: Path,
    corruption: str,
) -> None:
    payload = bytearray(_simple_fits())
    if corruption == "header-padding":
        end_offset = 6 * 80
        payload[end_offset + 80] = ord("X")
    else:
        payload[2880 + 24] = 0x7F
    path = _write(tmp_path / f"{corruption}.fit", bytes(payload))
    _assert_artifact_invalid(artifacts.inspect_fits_container, path)


def test_inspect_fits_container_rejects_duplicate_mandatory_keyword(
    tmp_path: Path,
) -> None:
    payload = _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", 16),
            ("BITPIX", 16),
            ("NAXIS", 2),
            ("NAXIS1", 1),
            ("NAXIS2", 1),
        ],
        b"\0\0",
    )
    path = _write(tmp_path / "duplicate-bitpix.fit", payload)
    _assert_artifact_invalid(artifacts.inspect_fits_container, path)


def _byte_shuffle(payload: bytes, item_size: int) -> bytes:
    assert item_size > 0 and len(payload) % item_size == 0
    return b"".join(payload[offset::item_size] for offset in range(item_size))


def _xisf_xml(
    *,
    geometry: str,
    sample_format: str,
    color_space: str,
    location: str,
    compression: str | None = None,
    checksum: str | None = None,
    embedded: bytes | None = None,
    extra_image: bool = False,
    declaration: str = '<?xml version="1.0" encoding="UTF-8"?>',
) -> bytes:
    attributes = [
        f'geometry="{geometry}"',
        f'sampleFormat="{sample_format}"',
        f'colorSpace="{color_space}"',
        f'location="{location}"',
    ]
    if compression is not None:
        attributes.append(f'compression="{compression}"')
    if checksum is not None:
        attributes.append(f'checksum="{checksum}"')
    fits_keyword = (
        '<FITSKeyword name="COMMENT" value="" '
        'comment="starun-siril deterministic fixture"/>'
    )
    if embedded is None:
        image = f"<Image {' '.join(attributes)}>{fits_keyword}</Image>"
    else:
        encoded = base64.b64encode(embedded).decode("ascii")
        image = (
            f"<Image {' '.join(attributes)}>{fits_keyword}"
            f'<Data encoding="base64">{encoded}</Data></Image>'
        )
    if extra_image:
        image += (
            '<Image geometry="1:1:1" sampleFormat="UInt8" colorSpace="Gray" '
            'location="embedded"><Data encoding="base64">AA==</Data></Image>'
        )
    xml = (
        f"{declaration}<xisf version=\"1.0\" "
        'xmlns="http://www.pixinsight.com/xisf">'
        f"{image}<Metadata/></xisf>"
    )
    return xml.encode("utf-8")


def _xisf_file(
    raw_pixels: bytes,
    *,
    geometry: str = "4:3:1",
    sample_format: str = "UInt16",
    color_space: str = "Gray",
    block_kind: str = "attachment",
    compression: bool = False,
    checksum: bool = False,
    declared_size: int | None = None,
    reserved: bytes = b"\0\0\0\0",
    extra_image: bool = False,
) -> bytes:
    assert len(reserved) == 4
    stored = raw_pixels
    compression_value = None
    if compression:
        stored = zlib.compress(_byte_shuffle(raw_pixels, 2), level=9)
        uncompressed_size = len(raw_pixels) if declared_size is None else declared_size
        compression_value = f"zlib+sh:{uncompressed_size}:2"
    checksum_value = f"sha1:{hashlib.sha1(stored).hexdigest()}" if checksum else None

    if block_kind == "embedded":
        xml = _xisf_xml(
            geometry=geometry,
            sample_format=sample_format,
            color_space=color_space,
            location="embedded",
            compression=compression_value,
            checksum=checksum_value,
            embedded=stored,
            extra_image=extra_image,
        )
        return b"XISF0100" + struct.pack("<I", len(xml)) + reserved + xml

    location = block_kind
    if block_kind == "attachment":
        offset = 0
        while True:
            location = f"attachment:{offset}:{len(stored)}"
            xml = _xisf_xml(
                geometry=geometry,
                sample_format=sample_format,
                color_space=color_space,
                location=location,
                compression=compression_value,
                checksum=checksum_value,
                extra_image=extra_image,
            )
            next_offset = ((16 + len(xml) + 4095) // 4096) * 4096
            if next_offset == offset:
                break
            offset = next_offset
        padding = b"\0" * (offset - 16 - len(xml))
        return (
            b"XISF0100"
            + struct.pack("<I", len(xml))
            + reserved
            + xml
            + padding
            + stored
        )

    xml = _xisf_xml(
        geometry=geometry,
        sample_format=sample_format,
        color_space=color_space,
        location=location,
        compression=compression_value,
        checksum=checksum_value,
        extra_image=extra_image,
    )
    return b"XISF0100" + struct.pack("<I", len(xml)) + reserved + xml


def test_inspect_xisf_container_decodes_attached_gray_image(tmp_path: Path) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(12))
    path = _write(tmp_path / "gray.xisf", _xisf_file(pixels))

    summary = artifacts.inspect_xisf_container(path)

    assert summary["format"] == "XISF"
    assert summary["image_count"] == 1
    assert summary["geometry"] == {
        "width": 4,
        "height": 3,
        "channels": 1,
        "bitpix": 16,
    }
    assert summary["sample_format"] == "UInt16"
    assert summary["color_space"] == "Gray"
    assert summary["location"] == "attachment"
    assert summary["compression"] is None
    assert summary["checksum"] is None


def test_inspect_xisf_container_decodes_embedded_rgb_image(tmp_path: Path) -> None:
    pixels = bytes(range(12))
    payload = _xisf_file(
        pixels,
        geometry="2:2:3",
        sample_format="UInt8",
        color_space="RGB",
        block_kind="embedded",
    )
    summary = artifacts.inspect_xisf_container(_write(tmp_path / "rgb.xisf", payload))

    assert summary["geometry"] == {
        "width": 2,
        "height": 2,
        "channels": 3,
        "bitpix": 8,
    }
    assert summary["location"] == "embedded"


def test_inspect_xisf_container_decodes_zlib_shuffle_and_checksum(tmp_path: Path) -> None:
    pixels = b"".join(struct.pack("<H", value * 257) for value in range(12))
    payload = _xisf_file(pixels, compression=True, checksum=True)

    summary = artifacts.inspect_xisf_container(_write(tmp_path / "compressed.xisf", payload))

    assert summary["compression"] == "zlib+sh"
    assert summary["checksum"] == "sha1"
    assert summary["geometry"]["width"] == 4


@pytest.mark.parametrize(
    "payload_factory",
    [
        lambda pixels: b"NOTXISF!" + _xisf_file(pixels)[8:],
        lambda pixels: _xisf_file(pixels, reserved=b"\0\0\0\1"),
        lambda pixels: _xisf_file(pixels)[:-1],
        lambda pixels: _xisf_file(pixels, compression=True, declared_size=len(pixels) + 2),
        lambda pixels: _xisf_file(pixels, extra_image=True),
        lambda pixels: _xisf_file(pixels, block_kind="url:https://example.invalid/image.bin"),
        lambda pixels: _xisf_file(pixels, block_kind="path:../image.bin"),
    ],
    ids=[
        "signature",
        "reserved-field",
        "attachment-bounds",
        "declared-decompressed-size",
        "multiple-images",
        "external-url",
        "external-path",
    ],
)
def test_inspect_xisf_container_rejects_invalid_monolithic_units(
    tmp_path: Path,
    payload_factory: Callable[[bytes], bytes],
) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(12))
    payload = payload_factory(pixels)
    path = _write(tmp_path / "invalid.xisf", payload)
    _assert_artifact_invalid(artifacts.inspect_xisf_container, path)


def test_inspect_xisf_container_rejects_checksum_mismatch(tmp_path: Path) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(12))
    payload = bytearray(_xisf_file(pixels, checksum=True))
    payload[-1] ^= 0xFF
    path = _write(tmp_path / "checksum.xisf", bytes(payload))
    _assert_artifact_invalid(artifacts.inspect_xisf_container, path)


def test_inspect_xisf_container_rejects_corrupt_zlib_without_checksum(
    tmp_path: Path,
) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(256))
    payload = bytearray(
        _xisf_file(
            pixels,
            geometry="16:16:1",
            compression=True,
            checksum=False,
        )
    )
    payload[-1] ^= 0xFF
    path = _write(tmp_path / "corrupt-zlib.xisf", bytes(payload))
    _assert_artifact_invalid(artifacts.inspect_xisf_container, path)


def test_inspect_xisf_container_rejects_nested_external_reference(
    tmp_path: Path,
) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(12))
    payload = _xisf_file(pixels, block_kind="embedded")
    header_length = struct.unpack("<I", payload[8:12])[0]
    xml = payload[16 : 16 + header_length]
    poisoned = xml.replace(
        b"</Image>",
        b'<Property id="unsafe" type="String" '
        b'location="url:https://example.invalid/secret"/></Image>',
    )
    rebuilt = (
        b"XISF0100"
        + struct.pack("<I", len(poisoned))
        + b"\0" * 4
        + poisoned
    )
    path = _write(tmp_path / "nested-external.xisf", rebuilt)
    _assert_artifact_invalid(artifacts.inspect_xisf_container, path)


@pytest.mark.parametrize("directive", ["DOCTYPE", "ENTITY"])
def test_inspect_xisf_container_rejects_dtd_and_entities(
    tmp_path: Path,
    directive: str,
) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(12))
    payload = _xisf_file(pixels, block_kind="embedded")
    header_length = struct.unpack("<I", payload[8:12])[0]
    xml = payload[16 : 16 + header_length]
    declaration, root = xml.split(b"?>", 1)
    if directive == "DOCTYPE":
        injection = b"<!DOCTYPE xisf>"
    else:
        injection = b'<!DOCTYPE xisf [<!ENTITY xxe "forbidden">]>'
    poisoned = declaration + b"?>" + injection + root
    rebuilt = (
        b"XISF0100"
        + struct.pack("<I", len(poisoned))
        + b"\0" * 4
        + poisoned
    )
    path = _write(tmp_path / f"{directive.lower()}.xisf", rebuilt)
    _assert_artifact_invalid(artifacts.inspect_xisf_container, path)


def test_inspect_xisf_container_rejects_invalid_utf8_and_xml_bounds(tmp_path: Path) -> None:
    pixels = b"".join(struct.pack("<H", value) for value in range(12))
    payload = bytearray(_xisf_file(pixels))
    header_length = struct.unpack("<I", payload[8:12])[0]
    payload[16] = 0xFF
    _assert_artifact_invalid(
        artifacts.inspect_xisf_container,
        _write(tmp_path / "utf8.xisf", bytes(payload)),
    )

    payload = bytearray(_xisf_file(pixels))
    payload[8:12] = struct.pack("<I", header_length + len(payload))
    _assert_artifact_invalid(
        artifacts.inspect_xisf_container,
        _write(tmp_path / "xml-bounds.xisf", bytes(payload)),
    )


def _statistics_output(channels: tuple[str, ...]) -> str:
    return "".join(
        f"{channel} layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, "
        "bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        for channel in channels
    )


def _siril_output(
    *,
    format_name: str,
    width: int,
    height: int,
    channels: tuple[str, ...],
    bits: int = 16,
) -> str:
    return (
        f"Reading {format_name}: file science, {len(channels)} layer(s), "
        f"{width}x{height} pixels, {bits} bits\n"
        + _statistics_output(channels)
    )


def _decoder_fixture(
    tmp_path: Path,
    *,
    payload: bytes,
    name: str = "science.fit",
) -> tuple[Path, Path]:
    session = tmp_path / "session"
    for relative in ("runtime/decode-validation", "runtime/siril-configs", "logs"):
        (session / relative).mkdir(parents=True, exist_ok=True)
    path = _write(session / name, payload)
    return session, path


@pytest.mark.parametrize(
    ("payload", "channels", "expected_count"),
    [
        (_simple_fits(), ("Gray",), 1),
        (_multi_hdu_fits(), ("Red", "Green", "Blue"), 3),
    ],
    ids=["gray", "rgb"],
)
def test_scientific_decoder_reopens_offline_with_sanitized_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    channels: tuple[str, ...],
    expected_count: int,
) -> None:
    session, path = _decoder_fixture(tmp_path, payload=payload)
    captured: dict[str, object] = {}
    monkeypatch.setenv("OPENAI_API_KEY", "sentinel-token")
    monkeypatch.setenv("HTTPS_PROXY", "https://secret-proxy.invalid")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/tmp/secret-agent.sock")
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/private/tmp/secret.dylib")

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            _siril_output(
                format_name="FITS",
                width=4 if expected_count == 1 else 2,
                height=3 if expected_count == 1 else 2,
                channels=channels,
                bits=16 if expected_count == 1 else 32,
            ),
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = core._decode_scientific_output(
        path,
        session=session,
        run_id="science-pass",
        output_index=1,
        siril={"path": "/frozen/siril-cli", "version": "1.4.4"},
        timeout=999,
        expected_format="FITS",
        validation_mode="siril",
    )

    assert result["passed"] is True, result
    assert result["container_validation"] == "siril"
    assert result["format"] == "FITS"
    assert result["geometry"]["channels"] == expected_count
    assert "strict_container" not in result
    assert result["decoder"]["statistics_channel_count"] == expected_count
    assert result["decoder"]["timeout_seconds"] == 300
    assert "--offline" in captured["command"]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["LC_ALL"] == "C"
    assert environment["PIP_NO_INDEX"] == "1"
    for secret in (
        "OPENAI_API_KEY",
        "HTTPS_PROXY",
        "SSH_AUTH_SOCK",
        "DYLD_INSERT_LIBRARIES",
    ):
        assert secret not in environment
    assert (session / "runtime/decode-validation/science-pass-01.ssf").is_file()
    assert (session / "runtime/siril-configs/science-pass-decode-01.ini").is_file()
    assert (session / "logs/science-pass-decode-01.log").is_file()


def test_scientific_decoder_rejects_unknown_error_despite_valid_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, path = _decoder_fixture(tmp_path, payload=_simple_fits())

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            _siril_output(
                format_name="FITS",
                width=4,
                height=3,
                channels=("Gray",),
            )
            + "error: decoder emitted an unclassified warning\n",
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = core._decode_scientific_output(
        path,
        session=session,
        run_id="science-log-error",
        output_index=1,
        siril={"path": "/frozen/siril-cli", "version": "1.4.4"},
        timeout=30,
        expected_format="FITS",
        validation_mode="siril",
    )

    assert result["passed"] is False
    assert result["reason_code"] == "decode_log_rejected"
    diagnostics = result["decoder"]["log_diagnostics"]
    assert diagnostics["status"] == "failed"
    assert diagnostics["findings"][0]["code"] == "unclassified_siril_error"


def test_siril_mode_does_not_require_the_strict_container_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Siril is the authority in the default mode. The deliberately truncated
    # payload therefore succeeds when a fresh Siril process reports a complete,
    # finite image identity for it.
    session, path = _decoder_fixture(tmp_path, payload=_simple_fits()[:2880])

    def forbidden_inspector(_candidate: Path) -> dict[str, object]:
        raise AssertionError("siril mode must not invoke the strict parser")

    monkeypatch.setattr(artifacts, "inspect_fits_container", forbidden_inspector)
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            _siril_output(
                format_name="FITS",
                width=4,
                height=3,
                channels=("Gray",),
            ),
        ),
    )
    result = core._validate_output(
        path,
        session=session,
        run_id="siril-only",
        output_index=1,
        siril={"path": "/frozen/siril-cli", "version": "1.4.4"},
        timeout=20,
        container_validation="siril",
    )

    assert result["passed"] is True, result
    assert result["container_validation"] == "siril"


def test_strict_mode_rejects_truncated_container_before_siril_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, path = _decoder_fixture(tmp_path, payload=_simple_fits()[:2880])
    child_called = False

    def forbidden_child(*_args: object, **_kwargs: object) -> None:
        nonlocal child_called
        child_called = True
        raise AssertionError("invalid strict container must not reach Siril")

    monkeypatch.setattr(core.subprocess, "run", forbidden_child)
    result = core._validate_output(
        path,
        session=session,
        run_id="strict-truncated",
        output_index=1,
        siril={"path": "/frozen/siril-cli", "version": "1.4.4"},
        timeout=20,
        container_validation="strict",
    )

    assert result["passed"] is False
    assert result["reason_code"] == "payload_truncated"
    assert child_called is False


def test_strict_mode_binds_container_scan_siril_metadata_and_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, path = _decoder_fixture(tmp_path, payload=_simple_fits())
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            _siril_output(
                format_name="FITS",
                width=4,
                height=3,
                channels=("Gray",),
            ),
        ),
    )

    result = core._validate_output(
        path,
        session=session,
        run_id="strict-pass",
        output_index=1,
        siril={"path": "/frozen/siril-cli", "version": "1.4.4"},
        timeout=20,
        container_validation="strict",
    )

    assert result["passed"] is True, result
    assert result["container_validation"] == "strict"
    assert result["strict_container"]["format"] == "FITS"
    assert result["strict_container"]["geometry"]["width"] == 4
    assert result["decoder"]["artifact_fingerprint"]["sha256"] == hashlib.sha256(
        path.read_bytes()
    ).hexdigest()


@pytest.mark.parametrize("mode", ["before", "during"])
def test_scientific_decoder_rejects_changed_frozen_siril(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    session, path = _decoder_fixture(tmp_path, payload=_simple_fits())
    executable = tmp_path / "siril-cli"
    executable.write_text("frozen-runtime\n", encoding="utf-8")
    executable.chmod(0o755)
    runtime_fingerprint = core.fingerprint(executable, role="siril_cli")
    child_called = False

    if mode == "before":
        executable.write_text("replacement-runtime\n", encoding="utf-8")

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal child_called
        child_called = True
        if mode == "during":
            executable.write_text("replacement-runtime\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            _siril_output(
                format_name="FITS",
                width=4,
                height=3,
                channels=("Gray",),
            ),
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = core._decode_scientific_output(
        path,
        session=session,
        run_id=f"runtime-{mode}",
        output_index=1,
        siril={
            "path": str(executable),
            "version": "1.4.4",
            "fingerprint": runtime_fingerprint,
        },
        timeout=20,
        expected_format="FITS",
        validation_mode="siril",
    )

    assert result["passed"] is False
    assert result["reason_code"] == "decoder_runtime_changed"
    assert child_called is (mode == "during")
    assert result["decoder"]["runtime_binding_unchanged"] is False


@pytest.mark.parametrize(
    ("mode", "expected_reason"),
    [
        ("nonzero", "decode_failed"),
        ("timeout", "decode_timeout"),
        ("no-load-metadata", "siril_metadata_missing"),
        ("no-statistics", "statistics_missing"),
        ("channel-mismatch", "statistics_missing"),
        ("drift", "artifact_changed_during_validation"),
    ],
)
def test_scientific_decoder_fails_closed_with_stable_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_reason: str,
) -> None:
    payload = _multi_hdu_fits() if mode == "channel-mismatch" else _simple_fits()
    session, path = _decoder_fixture(tmp_path, payload=payload)

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 20, output=b"partial")
        if mode == "nonzero":
            return subprocess.CompletedProcess(command, 23, "decoder failed")
        if mode == "drift":
            path.write_bytes(path.read_bytes() + b"changed")
        if mode == "no-load-metadata":
            output = _statistics_output(("Gray",))
        elif mode == "no-statistics":
            output = "Reading FITS: file science, 1 layer(s), 4x3 pixels, 16 bits\n"
        elif mode == "channel-mismatch":
            output = _siril_output(
                format_name="FITS",
                width=2,
                height=2,
                channels=("Gray",),
                bits=32,
            ).replace("1 layer(s)", "3 layer(s)")
        else:
            output = _siril_output(
                format_name="FITS",
                width=4,
                height=3,
                channels=("Gray",),
            )
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    result = core._decode_scientific_output(
        path,
        session=session,
        run_id=f"science-{mode}",
        output_index=1,
        siril={"path": "/frozen/siril-cli", "version": "1.4.4"},
        timeout=20,
        expected_format="FITS",
        validation_mode="siril",
    )

    assert result["passed"] is False
    assert result["reason_code"] == expected_reason
    assert result["decoder"]["log"]["sha256"]


@pytest.mark.skipif(
    not os.environ.get("DEEP_SKY_SIRIL_REAL_SIRIL_BIN"),
    reason="set DEEP_SKY_SIRIL_REAL_SIRIL_BIN for the real Siril decoder test",
)
@pytest.mark.parametrize("compressed", [False, True], ids=["plain", "zlib-sh"])
def test_real_siril_reopens_compressed_xisf_fixture(
    tmp_path: Path,
    compressed: bool,
) -> None:
    executable = Path(os.environ["DEEP_SKY_SIRIL_REAL_SIRIL_BIN"]).resolve()
    version, _excerpt = tooling._tool_version(executable)
    if version != "1.4.4":
        pytest.skip(f"real decoder fixture is frozen to Siril 1.4.4, found {version}")

    pixels = b"".join(struct.pack("<H", value * 257) for value in range(12))
    session, path = _decoder_fixture(
        tmp_path,
        payload=_xisf_file(pixels, compression=compressed, checksum=compressed),
        name="compressed.xisf",
    )
    result = core._decode_scientific_output(
        path,
        session=session,
        run_id="real-compressed-xisf",
        output_index=1,
        siril={"path": str(executable), "version": version},
        timeout=300,
        expected_format="XISF",
        validation_mode="strict",
    )

    assert result["passed"] is True, result
    assert result["strict_container"]["compression"] == (
        "zlib+sh" if compressed else None
    )
    assert result["strict_container"]["checksum"] == (
        "sha1" if compressed else None
    )
    assert result["decoder"]["statistics_channel_count"] == 1
    assert result["decoder"]["version"] == "1.4.4"
