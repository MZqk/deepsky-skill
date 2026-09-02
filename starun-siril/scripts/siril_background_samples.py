#!/usr/bin/env python3
"""Inject one hash-bound background-sample contract into a running Siril.

This script is intentionally a tiny bridge.  Pixel processing remains in
Siril: the parent ``.ssf`` loads the image, invokes this bridge, runs
``subsky 1 -existing``, and saves the candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from pathlib import Path
from typing import Any, Sequence


CONTRACT_SCHEMA = "starun-siril.background-sample-contract.v1"
RECEIPT_SCHEMA = "starun-siril.background-sample-injection.v1"
SIRILPY_VERSION_REQUIREMENT = "==1.0.25"
SIRIL_BACKGROUND_SAMPLE_ABI_SIZE = 80
SIRILPY_BACKGROUND_SAMPLE_FORMAT = "3dd2dQ2dI"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_contract(path: Path, expected_sha256: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if (
        re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
        or sha256_file(resolved) != expected_sha256
    ):
        raise ValueError("background sample contract fingerprint changed")
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    required = {"schema", "source", "fit_samples"}
    if (
        not isinstance(payload, dict)
        or set(payload) != required
        or payload.get("schema") != CONTRACT_SCHEMA
    ):
        raise ValueError("unsupported background sample contract")
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) != {"path", "sha256", "width", "height"}:
        raise ValueError("background source binding is invalid")
    source_path = source.get("path")
    source_sha256 = source.get("sha256")
    width = source.get("width")
    height = source.get("height")
    if (
        not isinstance(source_path, str)
        or not 1 <= len(source_path) <= 500
        or not source_path.strip()
        or not isinstance(source_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", source_sha256) is None
        or isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
    ):
        raise ValueError("background source binding is invalid")
    samples = payload.get("fit_samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 256:
        raise ValueError("background sample contract requires 1..256 fit samples")
    sample_ids: set[str] = set()
    positions: set[tuple[float, float]] = set()
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"id", "x", "y"}:
            raise ValueError("background sample entry is invalid")
        identifier = sample.get("id")
        x = sample.get("x")
        y = sample.get("y")
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier) is None
            or identifier in sample_ids
            or isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            raise ValueError("background sample coordinates are invalid")
        position = (float(x), float(y))
        if (
            position in positions
            or not 0 <= position[0] < width
            or not 0 <= position[1] < height
        ):
            raise ValueError("background sample coordinates are unsafe")
        sample_ids.add(identifier)
        positions.add(position)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", required=True)
    parser.add_argument("--contract-sha256", required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--receipt", required=True)
    return parser


def require_supported_sirilpy(module: Any) -> str:
    version = str(getattr(module, "__version__", "unknown"))
    checker = getattr(module, "check_module_version", None)
    if not callable(checker):
        raise RuntimeError("sirilpy does not expose check_module_version()")
    try:
        supported = bool(checker(SIRILPY_VERSION_REQUIREMENT))
    except (TypeError, ValueError) as error:
        raise RuntimeError("sirilpy version could not be validated") from error
    if not supported:
        raise RuntimeError(
            "Unsupported sirilpy version "
            f"{version}; required {SIRILPY_VERSION_REQUIREMENT}"
        )
    return version


def installed_sample_positions(installed: Sequence[Any] | None) -> list[tuple[float, float]]:
    if installed is None:
        return []
    positions: list[tuple[float, float]] = []
    for sample in installed:
        position = getattr(sample, "position", None)
        if not isinstance(position, (tuple, list)) or len(position) != 2:
            raise RuntimeError("Siril returned a background sample without coordinates")
        if getattr(sample, "valid", True) is False:
            raise RuntimeError("Siril marked a background sample invalid")
        positions.append((float(position[0]), float(position[1])))
    return positions


def sample_positions_match(
    expected: Sequence[tuple[float, float]],
    actual: Sequence[tuple[float, float]],
    *,
    tolerance: float = 1e-6,
) -> bool:
    return len(expected) == len(actual) and all(
        abs(expected_x - actual_x) <= tolerance
        and abs(expected_y - actual_y) <= tolerance
        for (expected_x, expected_y), (actual_x, actual_y) in zip(expected, actual)
    )


def has_sirilpy_native_tail_padding_signature(sample_count: int) -> bool:
    if sample_count < 1:
        return False
    packed_size = struct.calcsize(SIRILPY_BACKGROUND_SAMPLE_FORMAT * sample_count)
    return packed_size == SIRIL_BACKGROUND_SAMPLE_ABI_SIZE * sample_count - 4


def install_declared_samples(
    siril: Any, points: Sequence[tuple[float, float]]
) -> dict[str, Any]:
    requested = [(float(x), float(y)) for x, y in points]
    siril.clear_image_bgsamples()
    if not siril.set_image_bgsamples(requested, show_samples=False):
        raise RuntimeError("Siril rejected the background samples")
    installed = siril.get_image_bgsamples()
    actual = installed_sample_positions(installed)
    strategy = "sirilpy_public_setter"

    if (
        len(actual) == len(requested) - 1
        and has_sirilpy_native_tail_padding_signature(len(requested))
    ):
        # SirilPy 1.0.25 omits the final four bytes of native tail padding.
        # Adding one ignored trailing sentinel makes the C side receive exactly
        # the requested N complete 80-byte background_sample structures.
        siril.clear_image_bgsamples()
        padded = [*requested, requested[-1]]
        if not siril.set_image_bgsamples(padded, show_samples=False):
            raise RuntimeError("Siril rejected the ABI-compatible sample payload")
        installed = siril.get_image_bgsamples()
        actual = installed_sample_positions(installed)
        strategy = "sirilpy_native_tail_padding_sentinel_v1"

    if not sample_positions_match(requested, actual):
        raise RuntimeError(
            "Siril did not retain every declared background sample coordinate"
        )
    return {
        "installed_count": len(actual),
        "installed_positions": [[x, y] for x, y in actual],
        "strategy": strategy,
    }


def write_receipt(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temporary.replace(resolved)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    import sirilpy as s  # Supplied by Siril's managed Python environment.

    sirilpy_version = require_supported_sirilpy(s)
    siril = s.SirilInterface()
    siril.connect()
    contract_path = Path(args.contract).expanduser().resolve()
    contract = load_contract(
        contract_path, str(args.contract_sha256).lower()
    )
    expected_source = Path(args.expected_source).expanduser().resolve()
    contract_source = Path(str(contract["source"]["path"])).expanduser().resolve()
    if contract_source != expected_source:
        raise ValueError("background sample contract is bound to another source")
    if sha256_file(expected_source) != str(contract["source"]["sha256"]):
        raise ValueError("background sample source fingerprint changed")
    receipt = Path(args.receipt).expanduser().resolve()
    if receipt.parent != contract_path.parent:
        raise ValueError("background sample receipt must stay beside the contract")

    loaded = Path(str(siril.get_image_filename())).expanduser().resolve()
    if loaded != expected_source:
        raise ValueError("Siril loaded image does not match the declared sample source")
    fit_samples = contract["fit_samples"]
    points = [
        (float(sample["x"]), float(sample["y"]))
        for sample in fit_samples
    ]
    result = install_declared_samples(siril, points)
    write_receipt(
        receipt,
        {
            "schema": RECEIPT_SCHEMA,
            "status": "verified",
            "contract": {
                "path": str(contract_path),
                "sha256": str(args.contract_sha256).lower(),
            },
            "source": str(expected_source),
            "sirilpy_version": sirilpy_version,
            "sirilpy_version_requirement": SIRILPY_VERSION_REQUIREMENT,
            "requested_count": len(points),
            "requested_positions": [[x, y] for x, y in points],
            **result,
        },
    )
    siril.log(f"Injected {len(points)} hash-bound background samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
