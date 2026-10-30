#!/usr/bin/env python3
"""Install hash-bound S3 samples after verifying FITS/XISF native float32 identity."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from siril_background_samples import (
    build_parser, load_contract, sha256_file, require_supported_sirilpy,
    install_declared_samples, write_receipt, RECEIPT_SCHEMA, SIRILPY_VERSION_REQUIREMENT,
)

def verify_image_precision(image, source: Path) -> dict[str, Any]:
    """Verify the native identity conversion without writing any pixels."""
    import numpy as np
    from deep_sky_siril_artifacts import read_scientific_pixels
    data = np.asarray(image.data)
    if data.ndim == 2:
        data = data[None]
    if data.dtype != np.dtype('float32') or image.bitpix != -32:
        raise ValueError('Background fitting requires native float32 before sample injection')
    raw, scientific_source = read_scientific_pixels(source)
    if data.shape != raw.shape:
        raise ValueError('Native identity conversion changed geometry or channels')
    if getattr(image, 'top_down', False):
        data = data[:, ::-1]
    maximum = 0.0
    for y in range(0, data.shape[1], 256):
        reference = raw[:, y:y+256]
        actual = data[:, y:y+256]
        if not np.isfinite(reference).all() or not np.isfinite(actual).all():
            raise ValueError('Native identity conversion requires finite scientific pixels')
        spacing = np.abs(np.spacing(reference.astype(np.float32))).astype(float)
        error = np.abs(actual.astype(float)-reference)
        if np.any(error > 2*spacing):
            raise ValueError('Native identity conversion exceeds two float32 ULP')
        for endpoint in (0, 1):
            if np.any(actual[reference == endpoint] != endpoint):
                raise ValueError('Native identity conversion changed a 0/1 endpoint')
        maximum = max(maximum, float(error.max()))
    return {'schema': 'starun-siril.native-float32-identity.v1', 'status': 'verified',
            'source_sha256': scientific_source['sha256'], 'dtype': 'float32', 'bitpix': -32,
            'shape': list(raw.shape), 'max_abs_normalized_error': maximum,
            'tolerance': 'two_float32_ULP', 'endpoints_preserved': True,
            'scientific_source': scientific_source}


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
    precision = verify_image_precision(siril.get_image(), expected_source)
    if precision['source_sha256'] != contract['source']['sha256']:
        raise ValueError('Background scientific source changed before sample injection')
    fit_samples = contract["fit_samples"]
    points = [
        (float(sample["x"]), float(sample["y"]))
        for sample in fit_samples
    ]
    result = install_declared_samples(siril, points)
    if sha256_file(expected_source) != contract['source']['sha256']:
        raise ValueError('Background scientific source changed during sample injection')
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
            "image_precision": precision,
            **result,
        },
    )
    siril.log(f"Injected {len(points)} hash-bound background samples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
