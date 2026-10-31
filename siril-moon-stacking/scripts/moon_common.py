#!/usr/bin/env python3
"""Shared core utilities, IO, device priors, and image-scale constants for moon_stack."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from device_specs import (
        DEVICE_SPECS,
        EXCLUDED_DEVICES,
        SENSOR_STATUS,
        SOURCE_GRADES,
        get_device,
        identify_device,
        list_devices,
    )
except ImportError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from device_specs import (  # type: ignore[no-redef]
        DEVICE_SPECS,
        EXCLUDED_DEVICES,
        SENSOR_STATUS,
        SOURCE_GRADES,
        get_device,
        identify_device,
        list_devices,
    )

try:
    import numpy as np
except Exception as exc:  # pragma: no cover
    np = None
    _numpy_import_error = f"{type(exc).__name__}: {exc}"
else:
    _numpy_import_error = None

DEFAULT_SIRIL = "/Applications/Siril.app/Contents/MacOS/siril-cli"
RAW_EXTS = {".orf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raw", ".rw2", ".pef", ".srf"}
FIT_EXTS = {".fit", ".fits", ".fit.gz", ".fits.gz"}
SER_EXTS = {".ser"}
VIDEO_EXTS = {".avi", ".mp4", ".mov", ".mkv", ".m4v"}

SER_COLOR_MONO = 0
SER_COLOR_BAYER_RGGB = 8
SER_COLOR_BAYER_GRBG = 9
SER_COLOR_BAYER_GBRG = 10
SER_COLOR_BAYER_BGGR = 11
SER_COLOR_RGB = 100
SER_COLOR_BGR = 101

SER_BAYER_NAMES = {
    8: "RGGB",
    9: "GRBG",
    10: "GBRG",
    11: "BGGR",
}

OPTICAL_PRIOR_KEYS = ("FOCALLEN", "APERTURE", "XPIXSZ", "YPIXSZ")


# --------------------------------------------------------------------------- io

def log(msg: str) -> None:
    print(f"[moon-stack] {msg}", flush=True)


def die(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"[moon-stack] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, obj: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def resolve_siril(siril: str) -> str | None:
    path = shutil.which(siril)
    return str(Path(path).resolve()) if path else None


def run_siril_script(siril: str, script_lines: list[str], cwd: Path, log_path: Path, timeout: int = 3600) -> dict:
    """Write an SSF script and execute Siril CLI in an isolated directory."""
    executable = resolve_siril(siril)
    if not executable:
        die(f"Siril CLI not found or not executable: {siril}; pass --siril before the subcommand")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ssf_file = log_path.with_suffix(".ssf")
    # Always ensure 'requires 1.4.4' is at the top
    if not script_lines or not script_lines[0].startswith("requires"):
        script_lines = ["requires 1.4.4"] + script_lines
    if script_lines[-1] != "exit":
        script_lines.append("exit")

    bounded = []
    for line in script_lines:
        if line.startswith("mtf "):
            fields = line.split()
            low, middle, high = map(float, fields[1:4])
            if not all(np.isfinite([low, middle, high])) or not 0 < middle < 1:
                die(f"invalid MTF parameters: {line}")
            low = min(max(low, 0.0), 0.999998)
            high = min(max(high, low + 0.000001), 1.0)
            fields[1:4] = [f"{low:.6f}", f"{middle:.6f}", f"{high:.6f}"]
            line = " ".join(fields)
        bounded.append(line)
    ssf_file.write_text("\n".join(bounded) + "\n", encoding="utf-8")
    cmd = [executable, "-s", str(ssf_file), "-d", str(cwd)]
    env = dict(os.environ, LANG="C")
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    elapsed = round(time.time() - started, 2)
    output_log = (proc.stdout or "") + (proc.stderr or "")
    log_path.write_text(output_log, encoding="utf-8")

    return {
        "script": str(ssf_file),
        "log": str(log_path),
        "exit_code": proc.returncode,
        "seconds": elapsed,
        "output": output_log,
    }


def fits_header_info(path: Path) -> dict:
    """Inspect FITS header without memory mapping issues."""
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        h = hdul[0].header
        return {
            "naxis": h.get("NAXIS"),
            "width": h.get("NAXIS1"),
            "height": h.get("NAXIS2"),
            "channels": h.get("NAXIS3", 1),
            "bitpix": h.get("BITPIX"),
            "bayer": h.get("BAYERPAT"),
            "exptime": h.get("EXPTIME"),
            "instrument": h.get("INSTRUME"),
            "telescope": h.get("TELESCOP"),
            "date_obs": h.get("DATE-OBS"),
        }


# --- capture device priors ---------------------------------------------------

def _apply_device_priors(header, ident, *, overwrite: bool = False) -> dict:
    """Write device-table priors plus their provenance into a FITS header."""
    if not ident:
        return {"injected": False, "target": "none"}

    if ident.get("id"):
        header["DEVICE"] = str(ident["id"])
        header["DEVSRC"] = str(ident.get("match_source") or "unknown")
        header["DEVCONF"] = str(ident.get("confidence") or "unknown")

    applied = {}
    for key, value in (ident.get("priors") or {}).items():
        if value is None or (key in header and not overwrite):
            continue
        header[key] = float(value)
        applied[key] = float(value)

    if not applied:
        return {"injected": False, "target": "none",
                "reason": "all optical keywords already present"}

    header["OPTPRIOR"] = ",".join(sorted(applied))
    header["OPTISRC"] = "device_table" if ident.get("id") else "sensor_pixel_only"
    return {"injected": True, "target": "fits_header", "applied": applied}


def _device_ident_summary(ident) -> dict:
    """Compact, JSON-safe description of an identification result for receipts."""
    if not ident:
        return {"id": None, "label": None, "match_source": None, "confidence": None,
                "ambiguous": False, "brand_only": False, "source_grade": None,
                "sensor": None, "sensor_status": None, "priors": {}, "notes": ""}
    return {
        "id": ident.get("id"),
        "label": ident.get("label"),
        "match_source": ident.get("match_source"),
        "confidence": ident.get("confidence"),
        "ambiguous": bool(ident.get("ambiguous")),
        "brand_only": bool(ident.get("brand_only")),
        "source_grade": ident.get("source_grade"),
        "sensor": ident.get("sensor"),
        "sensor_status": ident.get("sensor_status"),
        "priors": dict(ident.get("priors") or {}),
        "notes": ident.get("notes", ""),
    }


def _hdr_source_label(hdr, key: str, base: str) -> str:
    """Label where an optical value came from, flagging device-table priors."""
    priors = str((hdr or {}).get("OPTPRIOR") or "")
    if key not in priors:
        return base
    device = (hdr or {}).get("DEVICE")
    src = (hdr or {}).get("OPTISRC") or "device_table"
    tag = f"{src}: {device}" if device and device != "unknown" else src
    return f"{base} [{tag}]"


def _hdr_positive_float(hdr, keys) -> float | None:
    """First strictly-positive float among `keys` in a FITS header, else None."""
    if hdr is None:
        return None
    for k in keys:
        v = hdr.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _resolve_device_arg(args) -> str:
    """Validate --device, exiting with an actionable message on an unknown id."""
    requested = str(getattr(args, "device", "auto") or "auto").strip()
    if requested in ("auto", "none", ""):
        return requested
    if requested not in DEVICE_SPECS:
        die(f"unknown --device '{requested}'; run `moon_stack.py devices` to list "
            f"the {len(DEVICE_SPECS)} supported ids, or use 'auto'/'none'")
    return requested


# --- scale, geometry and statistics helpers -----------------------------------

def _px(value: float, px_scale: float = 1.0) -> float:
    """Scale a native-pixel constant onto the working pixel grid."""
    return float(value) * float(px_scale)


def _odd(value: float) -> int:
    """Nearest odd integer >= 1 (OpenCV convolution kernels must be odd-sized)."""
    n = int(round(float(value)))
    if n < 1:
        n = 1
    if n % 2 == 0:
        n += 1
    return n


def _resolve_px_scale(args, work: Path, master: Path) -> float:
    """Recover the drizzle pixel-scale factor for the postprocess stage."""
    from astropy.io import fits

    receipt = work / "stack_receipt.json"
    if receipt.exists():
        try:
            scale = float(load_json(receipt).get("px_scale", 1.0) or 1.0)
            if scale > 0:
                return scale
        except Exception:
            pass
    try:
        with fits.open(master, memmap=False) as hdul:
            scale = float(hdul[0].header.get("DRZSCALE", 1.0) or 1.0)
            if scale > 0:
                return scale
    except Exception:
        pass
    return 1.0


def _pixel_full_scale(data):
    if np.issubdtype(data.dtype, np.integer):
        return float(np.iinfo(data.dtype).max)
    return 1.0 if np.nanpercentile(data, 99.99) <= 2.0 else 65535.0


def _estimate_pedestal(plane: np.ndarray) -> float:
    """Robustly estimate sky background or sensor black pedestal."""
    valid = plane[np.isfinite(plane) & (plane > 0)]
    if valid.size == 0:
        return 0.0

    h, w = plane.shape
    cs = max(16, min(100, h // 8, w // 8))
    margin = max(4, cs // 5)
    corners = [
        plane[margin:margin + cs, margin:margin + cs],
        plane[margin:margin + cs, max(0, w - margin - cs):w - margin],
        plane[max(0, h - margin - cs):h - margin, margin:margin + cs],
        plane[max(0, h - margin - cs):h - margin, max(0, w - margin - cs):w - margin],
    ]
    corner_medians = []
    corner_stds = []
    for c in corners:
        c_valid = c[np.isfinite(c) & (c > 0)]
        if c_valid.size > 0:
            corner_medians.append(float(np.median(c_valid)))
            corner_stds.append(float(np.std(c_valid)))

    p_low = float(np.percentile(valid, 0.1))
    p_high = float(np.percentile(valid, 99.95))
    dyn_range = p_high - p_low

    if corner_medians:
        min_idx = int(np.argmin(corner_medians))
        min_corner = corner_medians[min_idx]
        min_std = corner_stds[min_idx]

        is_sky = False
        if dyn_range > 1e-4:
            if min_corner <= float(np.percentile(valid, 70)) and (min_std < 0.10 * dyn_range):
                is_sky = True
        else:
            is_sky = True

        if is_sky:
            safe_ceiling = min_corner + 2.0 * min_std
            return float(min(safe_ceiling, p_high * 0.95) if safe_ceiling < p_high else min_corner)

    return max(0.0, p_low)


def _wavelet_plane_count(shape, px_scale=1.0):
    if not np.isfinite(px_scale) or px_scale < 1 or min(shape[-2:]) < 1:
        raise ValueError("wavelet adaptation requires a finite scale >= 1 and nonempty image")
    return max(0, min(5 + int(np.ceil(np.log2(px_scale))), int(np.floor(np.log2(min(shape[-2:])))) - 2))


# --- disk space & budgeting ---------------------------------------------------

def _disk_available(work, args, volume=None):
    free = shutil.disk_usage(volume or work).free
    reserve = max(10 * 1024**3, int(free * 0.1))
    available = max(0, free - reserve)
    cap = getattr(args, "disk_budget_gb", None)
    if cap is not None:
        if not np.isfinite(cap) or cap <= 0:
            die("--disk-budget-gb must be positive and finite")
        used = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
        available = min(available, max(0, int(cap * 1024**3) - used))
    return free, reserve, available


def _disk_frame_budget(work, args, width, height, channels, requested, existing=0, scale=1.0, importing=True):
    free, reserve, available = _disk_available(work, args)
    pixels = width * height * channels
    raw = ((pixels * 2 + 2879) // 2880 + 1) * 2880
    aligned = ((int(pixels * 4 * scale**2) + 2879) // 2880 + 1) * 2880
    drizzle_scratch = aligned if scale > 1 else 0
    planes = _wavelet_plane_count((max(1, int(height * scale)), max(1, int(width * scale))), scale)
    wavelet_scratch = (2 * planes + 1) * aligned if getattr(args, "wavelet_adapt", "auto") == "auto" else 0

    def peak(n):
        return int(1.25 * ((max(0, n - existing) * raw if importing else 0) + n * (aligned + drizzle_scratch) + 6 * aligned + wavelet_scratch))

    low, high = 0, requested
    while low < high:
        middle = (low + high + 1) // 2
        if peak(middle) <= available:
            low = middle
        else:
            high = middle - 1
    if low < min(3, requested):
        die(f"insufficient disk budget: need {peak(min(3, requested))} bytes, available {available}, shortfall {max(0, peak(min(3, requested)) - available)} bytes; use a new work directory, smaller ROI or more disk space")
    return low, {
        "free_bytes": free,
        "reserve_bytes": reserve,
        "available_bytes": available,
        "requested_frames": requested,
        "allowed_frames": low,
        "estimated_peak_additional_bytes": peak(low),
        "estimated_requested_bytes": peak(requested),
        "scale": scale,
        "reduced": low < requested,
    }


def _seq_frame_paths(work: Path, seq_name: str) -> list[Path]:
    return sorted(work.glob(f"{seq_name}[0-9][0-9][0-9][0-9][0-9].fit"))


def _frame_identity(path: Path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]

