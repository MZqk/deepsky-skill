#!/usr/bin/env python3
"""FITS Header Preservation, Inspection, and WCS Synchronization Tool.

Inspect Header evidence and synchronize sourced WCS or observational priors.
Synchronization copies the data portion unchanged and, by default, checks its SHA256.
Header completeness and coordinate closure do not validate sky correspondence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping, Sequence


BLOCK_SIZE = 2880
CARD_SIZE = 80

STRUCTURAL_KEYS = frozenset({
    "SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3",
    "EXTEND", "BZERO", "BSCALE", "BLANK", "DATASUM", "CHECKSUM",
})

WCS_PREFIXES = ("A_", "B_", "AP_", "BP_")
WCS_EXACT_KEYS = frozenset({
    "CRVAL1", "CRVAL2", "CRVAL3",
    "CRPIX1", "CRPIX2", "CRPIX3",
    "CTYPE1", "CTYPE2", "CTYPE3",
    "CUNIT1", "CUNIT2", "CUNIT3",
    "CD1_1", "CD1_2", "CD2_1", "CD2_2",
    "PC1_1", "PC1_2", "PC2_1", "PC2_2",
    "CDELT1", "CDELT2", "CDELT3",
    "CROTA1", "CROTA2",
    "RADESYS", "EQUINOX", "EPOCH",
    "LONPOLE", "LATPOLE", "WCSAXES",
    "A_ORDER", "B_ORDER", "AP_ORDER", "BP_ORDER",
})

PRIOR_KEYS = frozenset({
    "OBJECT", "TARGNAME",
    "RA", "DEC", "OBJCTRA", "OBJCTDEC", "RA_RAD", "DEC_RAD",
    "FOCALLEN", "FOCAL", "APERTURE", "APTDIA",
    "XPIXSZ", "YPIXSZ", "PIXSIZE", "PIXELSIZE",
    "XBINNING", "YBINNING", "CCDXBIN", "CCDYBIN",
    "TELESCOP", "TELESCOPE", "INSTRUME", "CAMERA",
    "FILTER", "FILTERID", "FILTNAME",
    "DATE-OBS", "DATE_OBS", "DATE-EXP", "DATE",
    "EXPTIME", "EXPOSURE", "TOTALEXP", "LIVETIME", "STACKCNT",
    "GAIN", "EGAIN", "CAMGAIN", "CCD-TEMP", "CCD_TEMP", "SENSOR_TEMP", "SET-TEMP",
    "SITELAT", "SITELONG", "SITEELEV",
    "FOCPOS", "ROTATOR", "EQMODE", "WIDECAM",
    "PROGRAM", "CREATOR", "PRODUCER",
})


def parse_fits_card_value(raw_val: str) -> Any:
    """Parse card value token into int, float, bool, or string."""
    token = raw_val.split("/", 1)[0].strip()
    if not token:
        return ""
    if token == "T":
        return True
    if token == "F":
        return False
    if token.startswith("'"):
        end = token.rfind("'")
        return token[1:end].rstrip() if end > 0 else token[1:].rstrip()
    try:
        if any(c in token for c in ".EeDd"):
            return float(token.replace("D", "E"))
        return int(token)
    except ValueError:
        return token


def format_fits_card(key: str, value: Any, comment: str | None = None) -> str:
    """Format an 80-character standard FITS card."""
    key = key.strip().upper()[:8].ljust(8)
    if key in ("COMMENT ", "HISTORY ") or key.strip() == "":
        text = str(value)
        card = f"{key}{text}"
        return card[:80].ljust(80)

    if isinstance(value, bool):
        val_str = "T" if value else "F"
        val_part = f"= {val_str:>20}"
    elif isinstance(value, int):
        val_part = f"= {value:>20d}"
    elif isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"Cannot format non-finite float {value} for card {key}")
        # Format floating numbers cleanly
        val_str = f"{value:.10f}".rstrip("0")
        if val_str.endswith("."):
            val_str += "0"
        if len(val_str) > 20:
            val_str = f"{value:.10e}"
        val_part = f"= {val_str:>20}"
    elif isinstance(value, str):
        val_part = f"= '{value:<8}'"
    else:
        val_part = f"= '{str(value):<8}'"

    if comment:
        card = f"{key}{val_part} / {comment}"
    else:
        card = f"{key}{val_part}"
    return card[:80].ljust(80)


class FitsHeaderReader:
    """Lightweight pure-python FITS header reader."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.cards: list[tuple[str, str]] = []  # (key, 80-char-card)
        self.data_offset: int = 0
        self.file_size: int = 0
        self._read()

    def _read(self) -> None:
        if not self.path.is_file():
            raise FileNotFoundError(f"FITS file not found: {self.path}")
        self.file_size = self.path.stat().st_size
        with self.path.open("rb") as stream:
            for _ in range(4096):
                block = stream.read(BLOCK_SIZE)
                if len(block) != BLOCK_SIZE:
                    raise ValueError(f"Incomplete FITS header in {self.path}")
                for offset in range(0, BLOCK_SIZE, CARD_SIZE):
                    card = block[offset : offset + CARD_SIZE].decode("ascii", errors="replace")
                    key = card[:8].strip().upper()
                    self.cards.append((key, card))
                    if key == "END":
                        self.data_offset = stream.tell()
                        return
        raise ValueError(f"Missing FITS END card in {self.path}")

    @property
    def header_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, card in self.cards:
            if key == "END":
                break
            if key and card[8:10] == "= ":
                result[key] = parse_fits_card_value(card[10:])
        return result

    def get_data_sha256(self) -> str:
        """Compute SHA256 of the data portion following the FITS header."""
        hasher = hashlib.sha256()
        with self.path.open("rb") as stream:
            stream.seek(self.data_offset)
            while chunk := stream.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def get_geometry(self) -> dict[str, int]:
        h = self.header_dict
        width = int(h.get("NAXIS1", 0))
        height = int(h.get("NAXIS2", 0))
        naxis = int(h.get("NAXIS", 0))
        channels = int(h.get("NAXIS3", 1)) if naxis >= 3 else 1
        bitpix = int(h.get("BITPIX", 0))
        return {
            "width": width,
            "height": height,
            "channels": channels,
            "bitpix": bitpix,
            "naxis": naxis,
        }


def is_wcs_key(key: str) -> bool:
    if key in WCS_EXACT_KEYS:
        return True
    return any(key.startswith(p) for p in WCS_PREFIXES)


def is_prior_key(key: str) -> bool:
    return key in PRIOR_KEYS


def check_complete_wcs(header: Mapping[str, Any]) -> tuple[bool, str]:
    """Verify if the header satisfies complete WCS according to starun-siril contract."""
    try:
        for k in ("CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2"):
            if k not in header:
                return False, f"Missing essential WCS coordinate key: {k}"
            val = float(header[k])
            if not math.isfinite(val):
                return False, f"Non-finite value for WCS key: {k}"

        crval1 = float(header["CRVAL1"])
        crval2 = float(header["CRVAL2"])
        if not (0 <= crval1 < 360 and -90 <= crval2 <= 90):
            return False, f"CRVAL coordinates out of range: RA={crval1}, Dec={crval2}"

        ctype1 = str(header.get("CTYPE1", ""))
        ctype2 = str(header.get("CTYPE2", ""))
        if not (ctype1.startswith("RA---") and ctype2.startswith("DEC--")):
            return False, f"CTYPE projection invalid or missing: CTYPE1='{ctype1}', CTYPE2='{ctype2}'"

        # Check CD matrix or CDELT/PC matrix
        if all(k in header for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2")):
            cd11 = float(header["CD1_1"])
            cd12 = float(header["CD1_2"])
            cd21 = float(header["CD2_1"])
            cd22 = float(header["CD2_2"])
            det = cd11 * cd22 - cd12 * cd21
        elif all(k in header for k in ("CDELT1", "CDELT2")):
            sx = float(header["CDELT1"])
            sy = float(header["CDELT2"])
            pc11 = float(header.get("PC1_1", 1.0))
            pc12 = float(header.get("PC1_2", 0.0))
            pc21 = float(header.get("PC2_1", 0.0))
            pc22 = float(header.get("PC2_2", 1.0))
            cd11, cd12 = sx * pc11, sx * pc12
            cd21, cd22 = sy * pc21, sy * pc22
            det = cd11 * cd22 - cd12 * cd21
        else:
            return False, "Missing CD matrix and CDELT/PC scale keywords"

        if not math.isfinite(det) or abs(det) < 1e-18:
            return False, f"Singular or near-zero CD transformation determinant: {det}"

        # If SIP distortion is declared, check distortion orders and coefficients
        if ctype1.endswith("-SIP"):
            for order_key in ("A_ORDER", "B_ORDER"):
                if order_key not in header:
                    return False, f"Missing SIP distortion order key: {order_key}"
                order = float(header[order_key])
                if order < 1 or order != int(order):
                    return False, f"Invalid SIP distortion order: {order_key}={order}"
            for k, v in header.items():
                if any(k.startswith(p) for p in WCS_PREFIXES):
                    try:
                        if not math.isfinite(float(v)):
                            return False, f"Non-finite SIP coefficient: {k}={v}"
                    except (ValueError, TypeError):
                        return False, f"Invalid SIP coefficient value: {k}={v}"

        return True, "Complete finite WCS verified"
    except Exception as exc:
        return False, f"Error validating WCS: {exc}"


def get_wcs_transformation(header: Mapping[str, Any]) -> tuple[float, float, float, float, float]:
    """Return cd11, cd12, cd21, cd22, det from header."""
    if all(k in header for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2")):
        cd11 = float(header["CD1_1"])
        cd12 = float(header["CD1_2"])
        cd21 = float(header["CD2_1"])
        cd22 = float(header["CD2_2"])
    elif all(k in header for k in ("CDELT1", "CDELT2")):
        sx = float(header["CDELT1"])
        sy = float(header["CDELT2"])
        pc11 = float(header.get("PC1_1", 1.0))
        pc12 = float(header.get("PC1_2", 0.0))
        pc21 = float(header.get("PC2_1", 0.0))
        pc22 = float(header.get("PC2_2", 1.0))
        cd11, cd12 = sx * pc11, sx * pc12
        cd21, cd22 = sy * pc21, sy * pc22
    else:
        raise ValueError("Missing CD matrix or CDELT/PC keywords")
    det = cd11 * cd22 - cd12 * cd21
    if not math.isfinite(det) or abs(det) < 1e-18:
        raise ValueError(f"Singular CD transformation matrix: det={det}")
    return cd11, cd12, cd21, cd22, det


def get_sip_polynomials(header: Mapping[str, Any]) -> tuple[
    dict[tuple[int, int], float],
    dict[tuple[int, int], float],
    dict[tuple[int, int], float],
    dict[tuple[int, int], float],
]:
    """Extract forward (A, B) and inverse (AP, BP) SIP coefficients."""
    a_coeffs: dict[tuple[int, int], float] = {}
    b_coeffs: dict[tuple[int, int], float] = {}
    ap_coeffs: dict[tuple[int, int], float] = {}
    bp_coeffs: dict[tuple[int, int], float] = {}

    for k, v in header.items():
        try:
            val = float(v)
            if not math.isfinite(val):
                continue
            if k.startswith("A_") and not k.endswith("_ORDER"):
                parts = k.split("_")[1:]
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    a_coeffs[(int(parts[0]), int(parts[1]))] = val
            elif k.startswith("B_") and not k.endswith("_ORDER"):
                parts = k.split("_")[1:]
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    b_coeffs[(int(parts[0]), int(parts[1]))] = val
            elif k.startswith("AP_") and not k.endswith("_ORDER"):
                parts = k.split("_")[1:]
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    ap_coeffs[(int(parts[0]), int(parts[1]))] = val
            elif k.startswith("BP_") and not k.endswith("_ORDER"):
                parts = k.split("_")[1:]
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    bp_coeffs[(int(parts[0]), int(parts[1]))] = val
        except (ValueError, TypeError):
            continue
    return a_coeffs, b_coeffs, ap_coeffs, bp_coeffs


def wcs_pix2world(header: Mapping[str, Any], x: float, y: float) -> tuple[float, float]:
    """Transform 1-based pixel coordinates (x, y) to celestial (RA, Dec) in degrees.
    Supports TAN and TAN-SIP projections.
    """
    crpix1 = float(header["CRPIX1"])
    crpix2 = float(header["CRPIX2"])
    crval1 = float(header["CRVAL1"])
    crval2 = float(header["CRVAL2"])
    cd11, cd12, cd21, cd22, _ = get_wcs_transformation(header)

    u = x - crpix1
    v = y - crpix2

    ctype1 = str(header.get("CTYPE1", ""))
    if ctype1.endswith("-SIP"):
        a_coeffs, b_coeffs, _, _ = get_sip_polynomials(header)
        f_u = sum(a * (u**p) * (v**q) for (p, q), a in a_coeffs.items())
        g_v = sum(b * (u**p) * (v**q) for (p, q), b in b_coeffs.items())
        u = u + f_u
        v = v + g_v

    xi = cd11 * u + cd12 * v
    eta = cd21 * u + cd22 * v

    xt = math.radians(xi)
    yt = math.radians(eta)
    ra0 = math.radians(crval1)
    dec0 = math.radians(crval2)

    denom = math.cos(dec0) - yt * math.sin(dec0)
    ra = math.degrees(ra0 + math.atan2(xt, denom)) % 360.0
    dec = math.degrees(math.atan2(math.sin(dec0) + yt * math.cos(dec0), math.hypot(xt, denom)))
    return ra, dec


def wcs_world2pix(header: Mapping[str, Any], ra: float, dec: float) -> tuple[float | None, float | None]:
    """Transform celestial (RA, Dec) in degrees to 1-based pixel coordinates (x, y).
    Returns (None, None) if point is behind the projection plane.
    """
    crpix1 = float(header["CRPIX1"])
    crpix2 = float(header["CRPIX2"])
    crval1 = float(header["CRVAL1"])
    crval2 = float(header["CRVAL2"])
    cd11, cd12, cd21, cd22, det = get_wcs_transformation(header)

    ra0 = math.radians(crval1)
    dec0 = math.radians(crval2)
    r = math.radians(ra)
    d = math.radians(dec)
    dra = r - ra0

    denom = math.sin(d) * math.sin(dec0) + math.cos(d) * math.cos(dec0) * math.cos(dra)
    if denom <= 0:
        return None, None

    xt = math.cos(d) * math.sin(dra) / denom
    yt = (math.sin(d) * math.cos(dec0) - math.cos(d) * math.sin(dec0) * math.cos(dra)) / denom

    xi = math.degrees(xt)
    eta = math.degrees(yt)

    u_prime = (cd22 * xi - cd12 * eta) / det
    v_prime = (-cd21 * xi + cd11 * eta) / det

    ctype1 = str(header.get("CTYPE1", ""))
    if ctype1.endswith("-SIP"):
        a_coeffs, b_coeffs, ap_coeffs, bp_coeffs = get_sip_polynomials(header)
        u, v = u_prime, v_prime
        if ap_coeffs or bp_coeffs:
            f_inv = sum(ap * (u_prime**p) * (v_prime**q) for (p, q), ap in ap_coeffs.items())
            g_inv = sum(bp * (u_prime**p) * (v_prime**q) for (p, q), bp in bp_coeffs.items())
            u = u_prime + f_inv
            v = v_prime + g_inv
        if a_coeffs or b_coeffs:
            # Inverse SIP coefficients are an initial approximation; refine against the forward map.
            uk, vk = u, v
            for _ in range(25):
                f_fwd = sum(a * (uk**p) * (vk**q) for (p, q), a in a_coeffs.items())
                g_fwd = sum(b * (uk**p) * (vk**q) for (p, q), b in b_coeffs.items())
                uk_next = u_prime - f_fwd
                vk_next = v_prime - g_fwd
                if math.hypot(uk_next - uk, vk_next - vk) < 1e-12:
                    break
                uk, vk = uk_next, vk_next
            u, v = uk, vk
    else:
        u, v = u_prime, v_prime

    return u + crpix1, v + crpix2


def wcs_round_trip_check(
    header: Mapping[str, Any],
    width: int,
    height: int,
    tolerance_px: float = 1e-4,
) -> dict[str, Any]:
    """Verify round-trip closure consistency (pixel -> world -> pixel) for center and corners."""
    has_wcs, _ = check_complete_wcs(header)
    if not has_wcs or width <= 0 or height <= 0:
        return {
            "verified": False,
            "max_closure_error_px": None,
            "tolerance_px": tolerance_px,
            "sample_points_count": 0,
            "status": "skipped_no_complete_wcs",
        }

    sample_points = [
        (width / 2.0, height / 2.0, "center"),
        (1.0, 1.0, "corner_bottom_left"),
        (float(width), 1.0, "corner_bottom_right"),
        (float(width), float(height), "corner_top_right"),
        (1.0, float(height), "corner_top_left"),
    ]

    max_err = 0.0
    point_results = []
    all_ok = True

    for x, y, label in sample_points:
        try:
            ra, dec = wcs_pix2world(header, x, y)
            x_back, y_back = wcs_world2pix(header, ra, dec)
            if x_back is None or y_back is None:
                all_ok = False
                err = float("inf")
            else:
                err = math.hypot(x - x_back, y - y_back)
                max_err = max(max_err, err)
                if err > tolerance_px:
                    all_ok = False
            point_results.append({
                "label": label,
                "pixel": [round(x, 2), round(y, 2)],
                "world_ra_dec": [round(ra, 6), round(dec, 6)],
                "error_px": round(err, 8) if math.isfinite(err) else None,
            })
        except Exception as exc:
            all_ok = False
            point_results.append({
                "label": label,
                "pixel": [x, y],
                "error": str(exc),
            })

    return {
        "verified": all_ok,
        "max_closure_error_px": round(max_err, 8) if math.isfinite(max_err) else None,
        "tolerance_px": tolerance_px,
        "sample_points_count": len(sample_points),
        "status": "passed" if all_ok else "failed_tolerance_exceeded",
        "points": point_results,
    }


def wcs_calc_footprint(header: Mapping[str, Any], width: int, height: int) -> dict[str, Any] | None:
    """Calculate celestial coordinates for corners, sky bounding box, and position angle."""
    has_wcs, _ = check_complete_wcs(header)
    if not has_wcs or width <= 0 or height <= 0:
        return None

    try:
        corners_pixels = [
            (1.0, 1.0, "bottom_left"),
            (float(width), 1.0, "bottom_right"),
            (float(width), float(height), "top_right"),
            (1.0, float(height), "top_left"),
        ]
        corners_world = []
        for x, y, name in corners_pixels:
            ra, dec = wcs_pix2world(header, x, y)
            corners_world.append({
                "corner": name,
                "pixel": [round(x, 1), round(y, 1)],
                "ra_deg": round(ra, 6),
                "dec_deg": round(dec, 6),
            })

        ra_list = [c["ra_deg"] for c in corners_world]
        dec_list = [c["dec_deg"] for c in corners_world]

        if max(ra_list) - min(ra_list) > 180.0:
            norm_ra = [(r - 360.0 if r > 180.0 else r) for r in ra_list]
            ra_min = min(norm_ra) % 360.0
            ra_max = max(norm_ra) % 360.0
        else:
            ra_min = min(ra_list)
            ra_max = max(ra_list)

        dec_min = min(dec_list)
        dec_max = max(dec_list)

        cx, cy = width / 2.0, height / 2.0
        cra, cdec = wcs_pix2world(header, cx, cy)
        nx, ny = wcs_world2pix(header, cra, cdec + 0.005)
        if nx is not None and ny is not None:
            dx = nx - cx
            dy = ny - cy
            pa_deg = math.degrees(math.atan2(dx, dy)) % 360.0
        else:
            pa_deg = None

        return {
            "corners": corners_world,
            "bounding_box": {
                "ra_min_deg": round(ra_min, 6),
                "ra_max_deg": round(ra_max, 6),
                "dec_min_deg": round(dec_min, 6),
                "dec_max_deg": round(dec_max, 6),
            },
            "position_angle_deg": round(pa_deg, 2) if pa_deg is not None else None,
        }
    except Exception:
        return None


def evaluate_framing(
    target_size_arcmin: float | None,
    fov_w_arcmin: float | None,
    fov_h_arcmin: float | None,
) -> dict[str, Any] | None:
    """Evaluate target framing and scale ratio within field of view."""
    if not fov_w_arcmin or not fov_h_arcmin or not target_size_arcmin or target_size_arcmin <= 0:
        return None

    min_fov = min(fov_w_arcmin, fov_h_arcmin)
    ratio = target_size_arcmin / min_fov

    if ratio > 0.85:
        category = "fills_frame"
        description = "Target occupies majority of field. Protect emission nebula from gray-world neutralisation."
    elif ratio >= 0.35:
        category = "balanced"
        description = "Balanced composition with dominant target and sufficient background."
    elif ratio >= 0.10:
        category = "wide_field"
        description = "Wide-field context framing with abundant stellar background."
    else:
        category = "tiny"
        description = "Small deep-sky target or compact planetary nebula/distant galaxy."

    return {
        "target_size_arcmin": round(target_size_arcmin, 2),
        "min_fov_arcmin": round(min_fov, 2),
        "framing_ratio": round(ratio, 4),
        "framing_category": category,
        "description": description,
    }


def generate_troubleshooting_hints(
    has_wcs: bool,
    stage4_readiness: str,
    ref_comp: Mapping[str, Any] | None = None,
) -> list[str]:
    """Generate concise actionable guidance for Stage 4 astrometry."""
    hints: list[str] = []
    if has_wcs:
        hints.append("Header WCS completeness checks passed. Sky correspondence is not verified; evaluate preservation with sourced geometry evidence, native Siril validation and protocol review.")
        return hints

    if ref_comp:
        ref_has_wcs = ref_comp.get("reference_complete_wcs")
        geom_match = ref_comp.get("geometry_match")
        if ref_has_wcs and geom_match:
            hints.append(
                f"Reference WCS fields are complete and dimensions match; pixel-coordinate correspondence is not verified. Only after confirming the reference-to-master mapping, sync to a new file before init: sync_fits_header.py sync --source <reference> --target <target> --output <new-master>"
            )
            return hints
        elif ref_has_wcs and not geom_match:
            hints.append(
                "Reference WCS fields are complete but dimensions differ. Use --center-align only for confirmed center cropping, or --offset-crpix for a known translation. --allow-geometry-mismatch bypasses the size check without validating the mapping."
            )

    if stage4_readiness == "ready_for_targeted_solve":
        hints.append(
            "Coordinate/sampling prior fields are present; validate their values and sources before evaluating native Siril Gaia solving."
        )
        hints.append(
            "Online Gaia is the default; each solving or color script still declares -catalog=gaia|localgaia explicitly. "
            "An explicit --offline session requires a configured local Gaia catalogue, frozen by probe."
        )
    else:
        hints.append(
            "Complete WCS or required prior fields are missing. Provide sourced coordinates/sampling evidence; synchronize WCS only from a reference with a confirmed pixel-coordinate mapping."
        )
        hints.append(
            "If solving is unavailable or rejected, evaluate background neutralization only when its channel and sky-region evidence is supported; otherwise skip it."
        )

    return hints


def inspect_fits(
    path: Path,
    ref_path: Path | None = None,
    target_size_arcmin: float | None = None,
) -> dict[str, Any]:
    """Inspect and report astrometric and observational metadata."""
    reader = FitsHeaderReader(path)
    header = reader.header_dict
    geom = reader.get_geometry()
    has_wcs, wcs_msg = check_complete_wcs(header)

    # Compute pixel scale and FOV if WCS or priors exist
    pixel_scale_arcsec = None
    fov_w_arcmin = None
    fov_h_arcmin = None
    if all(k in header for k in ("CD1_1", "CD1_2", "CD2_1", "CD2_2")):
        cd11, cd12 = float(header["CD1_1"]), float(header["CD1_2"])
        cd21, cd22 = float(header["CD2_1"]), float(header["CD2_2"])
        scale_x = math.hypot(cd11, cd21) * 3600.0
        scale_y = math.hypot(cd12, cd22) * 3600.0
        pixel_scale_arcsec = (scale_x + scale_y) / 2.0
    elif all(k in header for k in ("FOCALLEN", "XPIXSZ")):
        fl = float(header["FOCALLEN"])
        px = float(header["XPIXSZ"])
        if fl > 0 and px > 0:
            pixel_scale_arcsec = (px / fl) * 206.265

    if pixel_scale_arcsec and geom["width"] > 0 and geom["height"] > 0:
        fov_w_arcmin = (geom["width"] * pixel_scale_arcsec) / 60.0
        fov_h_arcmin = (geom["height"] * pixel_scale_arcsec) / 60.0

    priors_present = {k: header[k] for k in PRIOR_KEYS if k in header}
    wcs_present = {k: header[k] for k in header if is_wcs_key(k)}

    # Stage 4 Readiness Classification
    if has_wcs:
        stage4_readiness = "preserved_existing_solution"
        stage4_note = "Header WCS completeness checks passed; sky correspondence is not verified. Native Siril output validation and protocol review are still required before adoption."
    elif all(k in header for k in ("RA", "DEC", "FOCALLEN", "XPIXSZ")):
        stage4_readiness = "ready_for_targeted_solve"
        stage4_note = "Coordinate/sampling prior fields are present; validate values and sources before evaluating native Siril Gaia solving under the session network policy."
    else:
        stage4_readiness = "missing_astrometric_evidence"
        stage4_note = "Lacks both complete WCS and required coordinates/sampling priors."

    # Advanced WCS Diagnostics
    round_trip = wcs_round_trip_check(header, geom["width"], geom["height"])
    footprint = wcs_calc_footprint(header, geom["width"], geom["height"])
    framing = evaluate_framing(target_size_arcmin, fov_w_arcmin, fov_h_arcmin)

    ref_comp_dict = None
    if ref_path:
        ref_reader = FitsHeaderReader(ref_path)
        ref_h = ref_reader.header_dict
        ref_geom = ref_reader.get_geometry()
        ref_has_wcs, ref_wcs_msg = check_complete_wcs(ref_h)
        diff_keys_in_ref = [k for k in ref_h if k not in header and not k in STRUCTURAL_KEYS]
        ref_comp_dict = {
            "reference_file": str(ref_reader.path),
            "reference_complete_wcs": ref_has_wcs,
            "geometry_match": geom["width"] == ref_geom["width"] and geom["height"] == ref_geom["height"],
            "dimensions": {
                "target": f"{geom['width']}x{geom['height']}",
                "reference": f"{ref_geom['width']}x{ref_geom['height']}",
            },
            "keys_in_ref_missing_in_target": sorted(diff_keys_in_ref),
        }

    troubleshooting_hints = generate_troubleshooting_hints(has_wcs, stage4_readiness, ref_comp_dict)

    result: dict[str, Any] = {
        "file": str(reader.path),
        "geometry": geom,
        "complete_wcs": has_wcs,
        "wcs_status_message": wcs_msg,
        "stage4_readiness": stage4_readiness,
        "stage4_note": stage4_note,
        "pixel_scale_arcsec_per_px": round(pixel_scale_arcsec, 4) if pixel_scale_arcsec else None,
        "field_of_view_arcmin": {
            "width": round(fov_w_arcmin, 2) if fov_w_arcmin else None,
            "height": round(fov_h_arcmin, 2) if fov_h_arcmin else None,
        } if fov_w_arcmin else None,
        "target_object": header.get("OBJECT") or header.get("TARGNAME"),
        "center_coordinates": {
            "ra": header.get("CRVAL1") or header.get("RA") or header.get("OBJCTRA"),
            "dec": header.get("CRVAL2") or header.get("DEC") or header.get("OBJCTDEC"),
        },
        "optical_parameters": {
            "focal_length_mm": header.get("FOCALLEN"),
            "pixel_size_um": header.get("XPIXSZ"),
            "telescope": header.get("TELESCOP"),
            "camera": header.get("INSTRUME"),
            "filter": header.get("FILTER"),
        },
        "wcs_card_count": len(wcs_present),
        "prior_card_count": len(priors_present),
        "wcs_round_trip": round_trip,
        "sky_footprint": footprint,
        "framing": framing,
        "troubleshooting_hints": troubleshooting_hints,
    }

    if ref_comp_dict:
        result["reference_comparison"] = ref_comp_dict

    return result


def sync_fits_header(
    source_path: Path,
    target_path: Path,
    output_path: Path | None = None,
    mode: str = "wcs-and-priors",
    offset_crpix: tuple[float, float] | None = None,
    center_align: bool = False,
    allow_geometry_mismatch: bool = False,
    overwrite_existing_priors: bool = False,
    verify_pixels: bool = True,
    backup: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Synchronize WCS and observational priors from source FITS to target FITS."""
    source_path = Path(source_path).resolve()
    target_path = Path(target_path).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"Source FITS not found: {source_path}")
    if not target_path.is_file():
        raise FileNotFoundError(f"Target FITS not found: {target_path}")

    src_reader = FitsHeaderReader(source_path)
    tgt_reader = FitsHeaderReader(target_path)
    src_geom = src_reader.get_geometry()
    tgt_geom = tgt_reader.get_geometry()

    # Geometry validation
    geom_match = (
        src_geom["width"] == tgt_geom["width"]
        and src_geom["height"] == tgt_geom["height"]
    )
    if not geom_match and not allow_geometry_mismatch and not center_align and not offset_crpix:
        raise ValueError(
            f"Geometry mismatch: Source is {src_geom['width']}x{src_geom['height']}, "
            f"Target is {tgt_geom['width']}x{tgt_geom['height']}. "
            "Direct WCS copying without offset may cause astrometric distortion. "
            "Use --center-align only for confirmed center cropping, or --offset-crpix dx,dy for a known translation. --allow-geometry-mismatch only bypasses this size check; it does not validate the mapping."
        )

    # Compute CRPIX offset if needed
    dx: float = 0.0
    dy: float = 0.0
    if offset_crpix:
        dx, dy = offset_crpix
    elif center_align and not geom_match:
        dx = (tgt_geom["width"] - src_geom["width"]) / 2.0
        dy = (tgt_geom["height"] - src_geom["height"]) / 2.0

    # Determine which cards to extract from source
    cards_to_inject: dict[str, str] = {}
    for key, card in src_reader.cards:
        if key in STRUCTURAL_KEYS or key == "END":
            continue
        should_include = False
        if mode == "wcs-only":
            should_include = is_wcs_key(key)
        elif mode == "priors-only":
            should_include = is_prior_key(key)
        elif mode == "wcs-and-priors":
            should_include = is_wcs_key(key) or is_prior_key(key)
        elif mode == "full-header":
            should_include = True
        else:
            raise ValueError(f"Unknown mode: {mode}")

        if should_include:
            # If adjusting CRPIX
            if (dx != 0.0 or dy != 0.0) and key in ("CRPIX1", "CRPIX2"):
                val = parse_fits_card_value(card[10:])
                try:
                    num_val = float(val)
                    new_val = num_val + (dx if key == "CRPIX1" else dy)
                    comment = card[card.find("/") + 1:].strip() if "/" in card else "Offset-adjusted reference pixel"
                    card = format_fits_card(key, new_val, comment)
                except (ValueError, TypeError):
                    pass
            cards_to_inject[key] = card

    if not cards_to_inject:
        return {
            "status": "noop",
            "message": "No matching cards found in source to inject into target.",
            "injected_card_count": 0,
        }

    # Record target pixel data SHA256 before modification
    orig_data_sha256 = tgt_reader.get_data_sha256() if verify_pixels else None

    # Merge into target cards
    merged_keys: set[str] = set()
    new_card_list: list[str] = []

    for key, card in tgt_reader.cards:
        if key == "END":
            break
        if key in cards_to_inject:
            # WCS cards are always updated from source; priors only if requested
            if is_wcs_key(key) or overwrite_existing_priors:
                new_card_list.append(cards_to_inject[key])
            else:
                new_card_list.append(card)
            merged_keys.add(key)
        else:
            new_card_list.append(card)

    # Append remaining cards that were not present in target
    for key, card in cards_to_inject.items():
        if key not in merged_keys:
            new_card_list.append(card)

    # Add END card
    new_card_list.append(f"END{' ':<77}")

    # Pack into 2880-byte header blocks
    header_str = "".join(new_card_list)
    remainder = len(header_str) % BLOCK_SIZE
    if remainder != 0:
        header_str += " " * (BLOCK_SIZE - remainder)
    header_bytes = header_str.encode("ascii")

    if dry_run:
        return {
            "status": "dry_run",
            "target": str(target_path),
            "source": str(source_path),
            "mode": mode,
            "cards_to_inject": list(cards_to_inject.keys()),
            "total_cards_after_merge": len(new_card_list),
            "header_blocks": len(header_bytes) // BLOCK_SIZE,
            "crpix_adjustment": {"dx": dx, "dy": dy} if (dx != 0.0 or dy != 0.0) else None,
            "original_data_sha256": orig_data_sha256,
        }

    # Determine destination
    in_place = output_path is None or Path(output_path).resolve() == target_path
    final_output = target_path if in_place else Path(output_path).resolve()

    if in_place and backup:
        bak_path = target_path.with_name(f"{target_path.name}.bak")
        shutil.copy2(target_path, bak_path)

    # Atomic write to temporary file first
    temp_output = final_output.with_name(f".tmp_{final_output.name}_{os.getpid()}")
    try:
        with temp_output.open("wb") as out_f, target_path.open("rb") as in_f:
            out_f.write(header_bytes)
            in_f.seek(tgt_reader.data_offset)
            while chunk := in_f.read(65536):
                out_f.write(chunk)

        # Pixel verification
        if verify_pixels:
            temp_reader = FitsHeaderReader(temp_output)
            new_data_sha256 = temp_reader.get_data_sha256()
            if orig_data_sha256 != new_data_sha256:
                raise RuntimeError(
                    f"Pixel data SHA256 mismatch! Original: {orig_data_sha256}, New: {new_data_sha256}. "
                    "Aborting to protect scientific pixel integrity."
                )

        # Rename temp to final
        temp_output.replace(final_output)
    finally:
        if temp_output.exists():
            try:
                temp_output.unlink()
            except OSError:
                pass

    # Verify newly written FITS
    final_reader = FitsHeaderReader(final_output)
    final_complete_wcs, final_wcs_msg = check_complete_wcs(final_reader.header_dict)

    return {
        "status": "success",
        "output_file": str(final_output),
        "source_file": str(source_path),
        "mode": mode,
        "injected_card_count": len(cards_to_inject),
        "injected_cards": sorted(cards_to_inject.keys()),
        "pixel_integrity_verified": verify_pixels,
        "pixel_data_sha256": orig_data_sha256,
        "complete_wcs": final_complete_wcs,
        "wcs_status_message": final_wcs_msg,
        "stage4_readiness": "preserved_existing_solution" if final_complete_wcs else "priors_updated",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sync_fits_header.py",
        description="FITS Header and WCS preservation and synchronization tool for astrometry automation.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # inspect
    inspect_parser = subparsers.add_parser("inspect", help="Inspect astrometric WCS and observational headers.")
    inspect_parser.add_argument("file", help="Path to FITS file to inspect")
    inspect_parser.add_argument("--reference", help="Optional reference FITS to compare headers against")
    inspect_parser.add_argument(
        "--target-size-arcmin",
        type=float,
        help="Optional celestial target angular size in arcminutes to evaluate framing",
    )
    inspect_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # sync
    sync_parser = subparsers.add_parser("sync", help="Synchronize WCS / headers from source to target FITS.")
    sync_parser.add_argument("--source", required=True, help="Source FITS file with WCS/metadata")
    sync_parser.add_argument("--target", required=True, help="Target stacked Master FITS file")
    sync_parser.add_argument("--output", help="Optional output path (defaults to in-place target update)")
    sync_parser.add_argument(
        "--mode",
        choices=("wcs-and-priors", "wcs-only", "priors-only", "full-header"),
        default="wcs-and-priors",
        help="Synchronization mode (default: wcs-and-priors)",
    )
    sync_parser.add_argument(
        "--offset-crpix",
        help="Manual pixel offset dx,dy to add to CRPIX1/CRPIX2 (e.g. '10.5,-5.0')",
    )
    sync_parser.add_argument(
        "--center-align",
        action="store_true",
        help="Automatically adjust CRPIX1/CRPIX2 based on center difference between target and source",
    )
    sync_parser.add_argument(
        "--allow-geometry-mismatch",
        action="store_true",
        help="Proceed even if source and target image dimensions differ",
    )
    sync_parser.add_argument(
        "--no-verify-pixels",
        action="store_true",
        help="Skip strict SHA256 pixel data invariance verification",
    )
    sync_parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create .bak backup when updating target in place",
    )
    sync_parser.add_argument(
        "--overwrite-existing-priors",
        action="store_true",
        help="Overwrite existing prior headers in target with source values (default: preserve target priors)",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate header synchronization without modifying files",
    )
    sync_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # verify
    verify_parser = subparsers.add_parser("verify", help="Check whether a FITS file satisfies Stage 4 complete WCS.")
    verify_parser.add_argument("file", help="Path to FITS file to check")
    verify_parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "inspect":
        ref = Path(args.reference) if args.reference else None
        res = inspect_fits(
            Path(args.file),
            ref_path=ref,
            target_size_arcmin=args.target_size_arcmin,
        )
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"=== FITS Header & Astrometry Inspection ===")
            print(f"File: {res['file']}")
            geom = res['geometry']
            print(f"Geometry: {geom['width']}x{geom['height']} ({geom['channels']}ch, {geom['bitpix']}-bit)")
            print(f"Target Object: {res.get('target_object') or 'N/A'}")
            print(f"Center Coords: RA={res['center_coordinates']['ra']}, Dec={res['center_coordinates']['dec']}")
            print(f"Pixel Scale: {res.get('pixel_scale_arcsec_per_px')} arcsec/px")
            if res.get('field_of_view_arcmin'):
                fov = res['field_of_view_arcmin']
                print(f"Field of View: {fov['width']}' x {fov['height']}'")
            print(f"Complete WCS: {'YES' if res['complete_wcs'] else 'NO'} ({res['wcs_status_message']})")
            if res.get('wcs_round_trip') and res['wcs_round_trip'].get('max_closure_error_px') is not None:
                rt = res['wcs_round_trip']
                print(f"WCS Closure: {'OK' if rt['verified'] else 'FAIL'} (max residual: {rt['max_closure_error_px']} px)")
            if res.get('sky_footprint'):
                fp = res['sky_footprint']
                bbox = fp['bounding_box']
                print(f"Sky Footprint: RA [{bbox['ra_min_deg']}° .. {bbox['ra_max_deg']}°], Dec [{bbox['dec_min_deg']}° .. {bbox['dec_max_deg']}°]")
                if fp.get('position_angle_deg') is not None:
                    print(f"Position Angle: {fp['position_angle_deg']}° (North orientation)")
            if res.get('framing'):
                fr = res['framing']
                print(f"Framing: {fr['framing_category']} (target {fr['target_size_arcmin']}' / min FOV {fr['min_fov_arcmin']}', ratio: {round(fr['framing_ratio']*100, 1)}%)")
                print(f"  Note: {fr['description']}")
            print(f"Stage 4 Readiness: {res['stage4_readiness']} - {res['stage4_note']}")
            if res.get('troubleshooting_hints'):
                print("\n--- Astrometry Guidance & Troubleshooting ---")
                for i, hint in enumerate(res['troubleshooting_hints'], 1):
                    print(f"  {i}. {hint}")
            if "reference_comparison" in res:
                comp = res["reference_comparison"]
                print(f"\n--- Comparison with Reference ---")
                print(f"Reference: {comp['reference_file']}")
                print(f"Dimensions Match: {comp['geometry_match']} ({comp['dimensions']['target']} vs {comp['dimensions']['reference']}); pixel-coordinate correspondence is not verified")
                print(f"Missing in target: {len(comp['keys_in_ref_missing_in_target'])} cards")
        return 0

    if args.command == "verify":
        reader = FitsHeaderReader(Path(args.file))
        geom = reader.get_geometry()
        complete, msg = check_complete_wcs(reader.header_dict)
        rt = wcs_round_trip_check(reader.header_dict, geom["width"], geom["height"]) if complete else None
        res = {
            "file": str(reader.path),
            "complete_wcs": complete,
            "message": msg,
            "stage4_status": "preserved_existing_solution" if complete else "requires_platesolve",
            "wcs_round_trip": rt,
        }
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"File: {res['file']}")
            print(f"Complete WCS: {'YES' if complete else 'NO'} ({msg})")
            if rt and rt.get("verified"):
                print(f"WCS Closure: OK (max residual {rt['max_closure_error_px']} px)")
            print(f"Stage 4 Status: {res['stage4_status']} (header-based readiness only; sky correspondence is not verified)")
        return 0 if complete else 1

    if args.command == "sync":
        offset = None
        if args.offset_crpix:
            parts = [float(x.strip()) for x in args.offset_crpix.split(",")]
            if len(parts) != 2:
                parser.error("--offset-crpix requires dx,dy (e.g. '10,-5')")
            offset = (parts[0], parts[1])

        res = sync_fits_header(
            source_path=Path(args.source),
            target_path=Path(args.target),
            output_path=Path(args.output) if args.output else None,
            mode=args.mode,
            offset_crpix=offset,
            center_align=args.center_align,
            allow_geometry_mismatch=args.allow_geometry_mismatch,
            overwrite_existing_priors=args.overwrite_existing_priors,
            verify_pixels=not args.no_verify_pixels,
            backup=not args.no_backup,
            dry_run=args.dry_run,
        )
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            status = res.get("status")
            if status == "dry_run":
                print("=== Dry-Run Simulation ===")
                print(f"Source: {res['source']}")
                print(f"Target: {res['target']}")
                print(f"Cards to inject ({len(res['cards_to_inject'])}): {', '.join(res['cards_to_inject'])}")
            elif status == "success":
                print("=== Header Synchronization Successful ===")
                print(f"Output: {res['output_file']}")
                print(f"Injected cards: {res['injected_card_count']} cards")
                print(f"Pixel integrity verified: {res['pixel_integrity_verified']} (SHA256: {res['pixel_data_sha256'][:16]}...)")
                print(f"Complete WCS: {'YES' if res['complete_wcs'] else 'NO'} ({res['wcs_status_message']})")
                print(f"Stage 4 Readiness: {res['stage4_readiness']}")
            else:
                print(f"Result: {res}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
