#!/usr/bin/env python3
"""Stage 2 geometry correction and stacking border cropping (geometry.crop-near-black).

Detects and conservatively crops near-black dither/rotation borders resulting from
multi-frame stacking, following references/stages/stage-02-geometry.md#geometry-crop-near-black:
- Max crop per side: <= 3.5% (quantized in 0.5% steps);
- Minimum preserved area: >= 70%;
- Even output width and height;
- Pass-through by default if no stacking border is detected;
- Preserves scientific input domain (linear stays linear).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from deep_sky_siril_artifacts import read_fits_pixels
from deep_sky_siril_contract import ContractError, sha256_file, protocol_reference_bindings
import deep_sky_siril_core as core

ROOT = Path(__file__).resolve().parents[1]
MAX_SIDE_CROP_RATIO = 0.035
STEP_CROP_RATIO = 0.005
MIN_PRESERVED_AREA = 0.70


def detect_stacking_borders(
    fits_path: Path,
    max_crop_ratio: float = MAX_SIDE_CROP_RATIO,
    step_ratio: float = STEP_CROP_RATIO,
    session: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Detect near-black stacking dither/rotation border margins.

    Returns:
        Dict with crop geometry box and diagnostics if cropping is needed,
        or None if image is clean and should pass through.
    """
    raw = None
    try:
        raw, scale, zero = read_fits_pixels(fits_path)
    except Exception:
        # Non-FITS container (e.g. monolithic XISF or unsupported memory mapping):
        # Convert to an inspection FITS via siril-cli if session is provided
        if session is not None:
            inspect_fit = session / "artifacts" / "010-source.fit"
            if not inspect_fit.exists():
                try:
                    import subprocess
                    from deep_sky_siril_tooling import discover_siril, _subprocess_environment
                    siril_cli = discover_siril()
                    if siril_cli:
                        ssf = f"requires 1.4.4 1.5.0\nset32bits\nload \"{fits_path}\"\nsave \"{session}/artifacts/010-source\"\nclose\n"
                        inspect_fit.parent.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            [str(siril_cli), "-s", "-"],
                            input=ssf,
                            text=True,
                            capture_output=True,
                            env=_subprocess_environment("siril_runtime"),
                            check=False,
                        )
                except Exception:
                    pass
            if inspect_fit.exists():
                try:
                    raw, scale, zero = read_fits_pixels(inspect_fit)
                except Exception:
                    raw = None
    if raw is None:
        return None

    data = np.asarray(raw, dtype=np.float32) * scale + zero
    lum = data[0] if data.shape[0] == 1 else (0.2126 * data[0] + 0.7152 * data[1] + 0.0722 * data[2])
    h, w = lum.shape

    # 1. Measure background baseline in central 50% interior
    inner = lum[int(h * 0.25) : int(h * 0.75), int(w * 0.25) : int(w * 0.75)]
    med_bg = float(np.median(inner))
    mad_bg = float(np.median(np.abs(inner - med_bg)))
    sigma_bg = mad_bg * 1.4826

    # Effective dark pixel threshold
    dark_thresh = max(0.0, med_bg - 2.5 * sigma_bg)
    if dark_thresh < 1e-5:
        dark_thresh = 0.5 * med_bg

    max_search_x = int(w * max_crop_ratio)
    max_search_y = int(h * max_crop_ratio)
    tol_x = max(5, int(w * 0.005))
    tol_y = max(5, int(h * 0.005))
    step_x = max(1, int(round(w * step_ratio)))
    step_y = max(1, int(round(h * step_ratio)))

    # 2. Scan four margins
    # Left margin (x = 0 .. max_search_x)
    raw_left = 0
    for c in range(max_search_x):
        col = lum[:, c]
        blank_count = int(np.count_nonzero(~np.isfinite(col) | (col <= 1e-6)))
        dark_ratio = float(np.mean(col < dark_thresh))
        col_med = float(np.median(col))
        col_std = float(np.std(col))
        std_ratio = col_std / (sigma_bg + 1e-7)
        if blank_count > 0 or dark_ratio > 0.05 or (med_bg > 1e-4 and col_med < 0.90 * med_bg) or std_ratio < 0.35:
            raw_left = c + 1
        elif c > raw_left + tol_x:
            break

    # Right margin (c = w - 1 - i)
    raw_right = 0
    for i in range(max_search_x):
        c = w - 1 - i
        col = lum[:, c]
        blank_count = int(np.count_nonzero(~np.isfinite(col) | (col <= 1e-6)))
        dark_ratio = float(np.mean(col < dark_thresh))
        col_med = float(np.median(col))
        col_std = float(np.std(col))
        std_ratio = col_std / (sigma_bg + 1e-7)
        if blank_count > 0 or dark_ratio > 0.05 or (med_bg > 1e-4 and col_med < 0.90 * med_bg) or std_ratio < 0.35:
            raw_right = i + 1
        elif i > raw_right + tol_x:
            break

    # Top margin (r = 0 .. max_search_y)
    raw_top = 0
    for r in range(max_search_y):
        row = lum[r, :]
        blank_count = int(np.count_nonzero(~np.isfinite(row) | (row <= 1e-6)))
        dark_ratio = float(np.mean(row < dark_thresh))
        row_med = float(np.median(row))
        row_std = float(np.std(row))
        std_ratio = row_std / (sigma_bg + 1e-7)
        if blank_count > 0 or dark_ratio > 0.05 or (med_bg > 1e-4 and row_med < 0.90 * med_bg) or std_ratio < 0.35:
            raw_top = r + 1
        elif r > raw_top + tol_y:
            break

    # Bottom margin (r = h - 1 - i)
    raw_bottom = 0
    for i in range(max_search_y):
        r = h - 1 - i
        row = lum[r, :]
        blank_count = int(np.count_nonzero(~np.isfinite(row) | (row <= 1e-6)))
        dark_ratio = float(np.mean(row < dark_thresh))
        row_med = float(np.median(row))
        row_std = float(np.std(row))
        std_ratio = row_std / (sigma_bg + 1e-7)
        if blank_count > 0 or dark_ratio > 0.05 or (med_bg > 1e-4 and row_med < 0.90 * med_bg) or std_ratio < 0.35:
            raw_bottom = i + 1
        elif i > raw_bottom + tol_y:
            break

    # 3. Quantize detected margins into 0.5% steps
    def quantize(raw_depth: int, step: int, max_px: int) -> int:
        if raw_depth <= 0:
            return 0
        steps = math.ceil(raw_depth / step)
        return min(max_px, steps * step)

    crop_left = quantize(raw_left, step_x, max_search_x)
    crop_right = quantize(raw_right, step_x, max_search_x)
    crop_top = quantize(raw_top, step_y, max_search_y)
    crop_bottom = quantize(raw_bottom, step_y, max_search_y)

    # 4. Check if any side requires cropping
    if crop_left == 0 and crop_right == 0 and crop_top == 0 and crop_bottom == 0:
        return None

    # 5. Calculate resulting crop box & enforce even dimensions
    out_x = crop_left
    out_y = crop_top
    out_w = w - crop_left - crop_right
    out_h = h - crop_top - crop_bottom

    if out_w % 2 != 0:
        out_w -= 1
        crop_right += 1
    if out_h % 2 != 0:
        out_h -= 1
        crop_bottom += 1

    area_retention = float((out_w * out_h) / (w * h))
    if area_retention < MIN_PRESERVED_AREA:
        raise ContractError(
            "crop_area_underflow",
            f"Preserved area {area_retention:.4f} is below protocol limit {MIN_PRESERVED_AREA}",
        )

    return {
        "x": out_x,
        "y": out_y,
        "width": out_w,
        "height": out_h,
        "left_px": crop_left,
        "right_px": crop_right,
        "top_px": crop_top,
        "bottom_px": crop_bottom,
        "raw_margins": {
            "left": raw_left,
            "right": raw_right,
            "top": raw_top,
            "bottom": raw_bottom,
        },
        "original_width": w,
        "original_height": h,
        "area_retention": round(area_retention, 4),
        "med_bg": round(med_bg, 6),
        "dark_thresh": round(dark_thresh, 6),
    }


def generate_geometry_assets(
    session: Path,
    source_fits: Path,
    crop_box: Dict[str, Any],
) -> Tuple[Path, Path]:
    """Generate SSF script and provenance for Stage 2 geometry cropping."""
    scripts_dir = session / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = session / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    previews_dir = session / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    x = crop_box["x"]
    y = crop_box["y"]
    w = crop_box["width"]
    h = crop_box["height"]

    ssf_content = f"""requires 1.4.4 1.5.0
set32bits
load "{source_fits}"
crop {x} {y} {w} {h}
stat main
save "{session}/artifacts/020-crop" -chksum
autostretch -linked -2.8 0.22
savejpg "{session}/previews/020-crop" 95
close
"""
    ssf_file = scripts_dir / "020-crop.ssf"
    ssf_file.write_text(ssf_content)

    prov_file = scripts_dir / "020-crop.provenance.json"
    prov_data = {
        "schema": "starun-siril.ssf-provenance.v1",
        "contract_version": "1",
        "run_id": "020-crop",
        "protocol": "geometry.crop-near-black",
        "script": {
            "path": "scripts/020-crop.ssf",
            "sha256": hashlib.sha256(ssf_content.encode()).hexdigest(),
        },
        "references": protocol_reference_bindings("geometry.crop-near-black"),
        "command_policy": {
            "path": "references/command-policy.json",
            "sha256": sha256_file(ROOT / "references/command-policy.json"),
        },
        "manual_lookup": {
            "status": "not_needed",
            "reason": "Standard crop-near-black protocol skeleton",
            "evidence": [],
        },
        "rationale": {
            "applicability": "Stacking dither/rotation border removal",
            "parameter_choices": [
                f"crop {x} {y} {w} {h}",
                f"retained area ratio {crop_box['area_retention']}",
                f"quantized 0.5% steps: L={crop_box['left_px']}px, R={crop_box['right_px']}px, T={crop_box['top_px']}px, B={crop_box['bottom_px']}px",
            ],
        },
    }
    prov_file.write_text(json.dumps(prov_data, indent=2))
    return ssf_file, prov_file


def run_geometry_stage(
    session: Path,
    source_fits: Path,
    force: bool = False,
) -> Dict[str, Any]:
    """Execute Stage 2 geometry check and cropping in an automated pipeline.

    Returns dict with execution status and output scientific FITS path.
    """
    crop_fit = session / "artifacts" / "020-crop.fit"
    crop_jpg = session / "previews" / "020-crop.jpg"

    if crop_fit.exists() and not force:
        return {
            "status": "cached",
            "action": "cropped",
            "output_fits": crop_fit,
            "preview_jpg": crop_jpg if crop_jpg.exists() else None,
            "notes": "Reused existing 020-crop.fit artifact",
        }

    # 1. Detect borders
    crop_box = detect_stacking_borders(source_fits, session=session)

    # If clean, pass through
    if crop_box is None:
        return {
            "status": "success",
            "action": "pass-through",
            "output_fits": source_fits,
            "preview_jpg": None,
            "crop_box": None,
            "notes": "Image full-frame clean; no stacking borders detected",
        }

    # 2. Generate assets
    ssf_script, _ = generate_geometry_assets(session, source_fits, crop_box)

    # 3. Execute Siril crop
    run_res = core.run_script(
        session,
        protocol="geometry.crop-near-black",
        script_value=ssf_script,
        source_value=source_fits,
        expected_values=[crop_fit, crop_jpg],
        primary_output_value=crop_fit,
        timeout=600,
    )

    return {
        "status": run_res.get("status", "success"),
        "action": "cropped",
        "output_fits": crop_fit,
        "preview_jpg": crop_jpg,
        "crop_box": crop_box,
        "notes": f"Cropped stacking border: {crop_box['width']}x{crop_box['height']} (retained {crop_box['area_retention']*100:.1f}%)",
    }
