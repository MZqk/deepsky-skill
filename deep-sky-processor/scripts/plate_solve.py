#!/usr/bin/env python3
"""Optional local Astrometry.net solve-field adapter and WCS catalog projector."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from fits_io import read_image


SCHEMA_VERSION = "1.0"


def find_solve_field(user_path=None):
    if user_path:
        path = Path(user_path).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which("solve-field")


def _header_float(header, key):
    value = header.get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_wcs(wcs_path, image_shape):
    from astropy.io import fits
    from astropy.wcs import WCS
    from astropy.wcs.utils import proj_plane_pixel_scales

    header = fits.getheader(wcs_path)
    wcs = WCS(header)
    height, width = image_shape[:2]
    center_ra, center_dec = wcs.pixel_to_world_values(
        (width - 1) / 2.0, (height - 1) / 2.0
    )
    scales_deg = np.abs(proj_plane_pixel_scales(wcs.celestial))
    scale_arcsec = float(np.mean(scales_deg) * 3600.0)
    return {
        "center_ra_deg": round(float(center_ra), 8),
        "center_dec_deg": round(float(center_dec), 8),
        "pixel_scale_arcsec": round(scale_arcsec, 4),
        "field_width_deg": round(float(scales_deg[0] * width), 5),
        "field_height_deg": round(float(scales_deg[1] * height), 5),
        "orientation_deg": _header_float(header, "CROTA2"),
        "wcs_path": str(wcs_path),
    }


def project_catalog(wcs_path, image_shape, catalog):
    """Project catalog RA/DEC positions into normalized image coordinates."""
    from astropy.io import fits
    from astropy.wcs import WCS

    wcs = WCS(fits.getheader(wcs_path))
    height, width = image_shape[:2]
    projected = []
    for item in catalog or []:
        try:
            x, y = wcs.world_to_pixel_values(
                float(item["ra_deg"]), float(item["dec_deg"])
            )
        except (KeyError, TypeError, ValueError):
            continue
        inside = 0 <= x < width and 0 <= y < height
        if not inside:
            continue
        projected.append({
            **item,
            "pixel": [round(float(x), 2), round(float(y), 2)],
            "normalized_center": [
                round(float(x) / max(width - 1, 1), 6),
                round(float(y) / max(height - 1, 1), 6),
            ],
            "inside_frame": True,
        })
    return projected


def solve_image(image_path, output_dir, solve_field_path=None, timeout=180,
                scale_low=None, scale_high=None, ra=None, dec=None,
                radius=None, catalog=None):
    executable = find_solve_field(solve_field_path)
    if not executable:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "unavailable",
            "backend": "astrometry.net-solve-field",
            "error": {
                "code": "SOLVE_FIELD_NOT_FOUND",
                "message": "Install Astrometry.net or pass --solve-field-path.",
            },
        }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        str(image_path),
        "--dir", str(output_dir),
        "--overwrite",
        "--no-plots",
    ]
    if scale_low is not None and scale_high is not None:
        command.extend([
            "--scale-units", "arcsecperpix",
            "--scale-low", str(scale_low),
            "--scale-high", str(scale_high),
        ])
    if ra is not None and dec is not None:
        command.extend(["--ra", str(ra), "--dec", str(dec)])
        if radius is not None:
            command.extend(["--radius", str(radius)])

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "backend": "astrometry.net-solve-field",
            "error": {"code": "PLATE_SOLVE_TIMEOUT", "message": f"timeout={timeout}s"},
        }

    stem = Path(image_path).stem
    candidates = [
        output_dir / f"{stem}.wcs",
        *output_dir.glob("*.wcs"),
    ]
    wcs_path = next((path for path in candidates if path.exists()), None)
    if result.returncode != 0 or wcs_path is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "backend": "astrometry.net-solve-field",
            "command": command,
            "error": {
                "code": "PLATE_SOLVE_FAILED",
                "message": result.stderr.strip() or result.stdout.strip(),
            },
        }

    image, _ = read_image(str(image_path))
    solution = summarize_wcs(wcs_path, image.shape)
    objects = project_catalog(wcs_path, image.shape, catalog)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "success",
        "backend": "astrometry.net-solve-field",
        "solution": solution,
        "objects": objects,
        "catalog_note": (
            "Plate solving establishes WCS. Object identification is limited "
            "to the supplied catalog and is not implied by solve-field alone."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="Local Astrometry.net plate solver")
    parser.add_argument("input")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--solve-field-path")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--scale-low", type=float)
    parser.add_argument("--scale-high", type=float)
    parser.add_argument("--ra", type=float)
    parser.add_argument("--dec", type=float)
    parser.add_argument("--radius", type=float)
    parser.add_argument("--catalog-json")
    args = parser.parse_args()
    catalog = None
    if args.catalog_json:
        catalog = json.loads(Path(args.catalog_json).read_text(encoding="utf-8"))
    payload = solve_image(
        args.input,
        args.output_dir,
        solve_field_path=args.solve_field_path,
        timeout=args.timeout,
        scale_low=args.scale_low,
        scale_high=args.scale_high,
        ra=args.ra,
        dec=args.dec,
        radius=args.radius,
        catalog=catalog,
    )
    Path(args.output_json).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["status"] in ("success", "unavailable") else 1


if __name__ == "__main__":
    raise SystemExit(main())
