#!/usr/bin/env python3
"""FITS Header Preservation, Inspection, and WCS Synchronization Tool.

Enables lossless preservation and synchronization of astrometric solutions (WCS)
and observational priors from capture subframes/reference frames to stacked masters.
Guarantees bit-for-bit invariance of scientific pixel data arrays.
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


def inspect_fits(path: Path, ref_path: Path | None = None) -> dict[str, Any]:
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
        stage4_note = "WCS is complete. Stage 4 will preserve existing solution with 0 dependencies."
    elif all(k in header for k in ("RA", "DEC", "FOCALLEN", "XPIXSZ")):
        stage4_readiness = "ready_for_targeted_solve"
        stage4_note = "Priors (RA/Dec/Focal/Pixel) are present. Siril/ASTAP can solve targeted without blind search."
    else:
        stage4_readiness = "missing_astrometric_evidence"
        stage4_note = "Lacks both complete WCS and required coordinates/sampling priors."

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
    }

    if ref_path:
        ref_reader = FitsHeaderReader(ref_path)
        ref_h = ref_reader.header_dict
        ref_geom = ref_reader.get_geometry()
        ref_has_wcs, ref_wcs_msg = check_complete_wcs(ref_h)
        diff_keys_in_ref = [k for k in ref_h if k not in header and not k in STRUCTURAL_KEYS]
        result["reference_comparison"] = {
            "reference_file": str(ref_reader.path),
            "reference_complete_wcs": ref_has_wcs,
            "geometry_match": geom["width"] == ref_geom["width"] and geom["height"] == ref_geom["height"],
            "dimensions": {
                "target": f"{geom['width']}x{geom['height']}",
                "reference": f"{ref_geom['width']}x{ref_geom['height']}",
            },
            "keys_in_ref_missing_in_target": sorted(diff_keys_in_ref),
        }

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
            "Use --center-align, --offset-crpix dx,dy, or --allow-geometry-mismatch to proceed."
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
        res = inspect_fits(Path(args.file), ref_path=ref)
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
            print(f"Stage 4 Readiness: {res['stage4_readiness']} - {res['stage4_note']}")
            if "reference_comparison" in res:
                comp = res["reference_comparison"]
                print(f"\n--- Comparison with Reference ---")
                print(f"Reference: {comp['reference_file']}")
                print(f"Geometry Match: {comp['geometry_match']} ({comp['dimensions']['target']} vs {comp['dimensions']['reference']})")
                print(f"Missing in target: {len(comp['keys_in_ref_missing_in_target'])} cards")
        return 0

    if args.command == "verify":
        reader = FitsHeaderReader(Path(args.file))
        complete, msg = check_complete_wcs(reader.header_dict)
        res = {
            "file": str(reader.path),
            "complete_wcs": complete,
            "message": msg,
            "stage4_status": "preserved_existing_solution" if complete else "requires_platesolve",
        }
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"File: {res['file']}")
            print(f"Complete WCS: {'YES' if complete else 'NO'} ({msg})")
            print(f"Stage 4 Status: {res['stage4_status']}")
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
