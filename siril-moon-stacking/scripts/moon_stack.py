#!/usr/bin/env python3
"""Moon / planetary lucky-imaging stacking driver.

Architecture:
  Siril 1.4.4 CLI acts as the main processing backbone:
    - RAW decoding & demosaicing (convert -debayer) / FITS sequence linking (link)
    - Multithreaded subpixel bicubic resampling & common area cropping (seqapplyreg -framing=min -interp=cu)
    - Winsorized sigma clipping stacking (stack rej w 3 3 -norm=addscale -filter-included)
    - Multiscale 'à trous' B-Spline wavelet reconstruction & unsharp mask (wavelet / wrecons / unsharp)
    - Mineral moon chromatic alignment & saturation boost (rmgreen / satu)
    - Professional 32-bit FITS / 16-bit TIFF / JPG exports (savetif / savejpg)

  Python acts as a lightweight registration plugin:
    - Robust lunar surface feature ROI localization (gradient-based, moon phase agnostic)
    - Subpixel Hann-windowed FFT phase correlation & confidence estimation
    - Seeing sharpness ranking & outlier rejection
    - Injection of R0 homography matrices into Siril's native .seq sequence file
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_SIRIL = "/Applications/Siril.app/Contents/MacOS/siril-cli"
RAW_EXTS = {".orf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raw", ".rw2", ".pef", ".srf"}
FIT_EXTS = {".fit", ".fits", ".fit.gz", ".fits.gz"}


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


def run_siril_script(siril: str, script_lines: list[str], cwd: Path, log_path: Path, timeout: int = 3600) -> dict:
    """Write an SSF script and execute Siril CLI in an isolated directory."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ssf_file = log_path.with_suffix(".ssf")
    # Always ensure 'requires 1.4.4' is at the top
    if not script_lines or not script_lines[0].startswith("requires"):
        script_lines = ["requires 1.4.4"] + script_lines
    if script_lines[-1] != "exit":
        script_lines.append("exit")

    ssf_file.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
    cmd = [siril, "-s", str(ssf_file), "-d", str(cwd)]
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
            "date_obs": h.get("DATE-OBS"),
        }


# ------------------------------------------------------------------- 1. import

def cmd_import(args) -> None:
    src = Path(args.input).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()
    if not src.is_dir():
        die(f"input directory not found: {src}")

    work.mkdir(parents=True, exist_ok=True)
    logs_dir = work / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    candidates = sorted(p for p in src.iterdir() if p.is_file())
    fits_files = [p for p in candidates if p.suffix.lower() in FIT_EXTS]
    raw_files = [p for p in candidates if p.suffix.lower() in RAW_EXTS]

    # Format selection
    fmt = getattr(args, "format", "auto")
    limit = getattr(args, "limit", 0)

    if fmt == "raw":
        fits_files = []
    elif fmt == "fits":
        raw_files = []
    else:  # auto
        if fits_files:
            raw_files = []  # prefer existing FITS if present

    # Filter out already stacked masters from FITS inputs
    usable_fits, rejected_fits = [], []
    for p in fits_files:
        if "stack" in p.name.lower():
            rejected_fits.append({"path": str(p), "reason": "name suggests stacked master"})
            continue
        try:
            h = fits_header_info(p)
            if h["bitpix"] in (-32, -64):
                rejected_fits.append({"path": str(p), "reason": f"float master (BITPIX={h['bitpix']})"})
                continue
            usable_fits.append(p)
        except Exception as exc:
            rejected_fits.append({"path": str(p), "reason": f"unreadable: {exc}"})

    if limit > 0:
        if raw_files:
            raw_files = raw_files[:limit]
        if usable_fits:
            usable_fits = usable_fits[:limit]

    log(f"inventory: found {len(usable_fits)} FITS frames, {len(raw_files)} RAW files ({len(rejected_fits)} rejected)")
    if not usable_fits and not raw_files:
        die(f"no usable frames found in {src}")

    script_lines = ["requires 1.4.4"]
    seq_name = "moon_"

    if raw_files:
        # RAW flow: use Siril's native parallel convert -debayer
        log(f"importing {len(raw_files)} RAW frames via Siril native debayering...")
        raw_staging = work / "raw_staging"
        raw_staging.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(raw_files, 1):
            target_link = raw_staging / f"raw_{i:05d}{r.suffix.lower()}"
            if target_link.is_symlink() or target_link.exists():
                target_link.unlink()
            os.symlink(r.resolve(), target_link)
        script_lines.extend([
            f"convert raw_ -debayer -out=..",
        ])
        cwd = raw_staging
        script_lines.append("exit")
        receipt = run_siril_script(args.siril, script_lines, cwd, logs_dir / "01_import.log", args.timeout)
        if receipt["exit_code"] != 0:
            die(f"RAW import failed in Siril (exit {receipt['exit_code']}); see {receipt['log']}")

        # Rename converted raw_*.fit in work dir to moon_*.fit and build sequence
        converted_fits = sorted(work.glob("raw_*.fit"))
        for i, cf in enumerate(converted_fits, 1):
            target_fit = work / f"{seq_name}{i:05d}.fit"
            if target_fit.exists():
                target_fit.unlink()
            cf.rename(target_fit)
        total_frames = len(converted_fits)
    else:
        # FITS flow: safely symlink original files using absolute paths and create .seq
        log(f"importing {len(usable_fits)} FITS frames via safe symlinks...")
        for i, f in enumerate(usable_fits, 1):
            target_link = work / f"{seq_name}{i:05d}.fit"
            if target_link.is_symlink() or target_link.exists():
                target_link.unlink()
            os.symlink(f.resolve(), target_link)
        total_frames = len(usable_fits)

    # Build standard Siril .seq file
    seq_lines = [
        "#Siril sequence file. Generated by siril-moon-stacking",
        f"S '{seq_name}' 1 {total_frames} {total_frames} 5 -1 6 0 0 0",
        "L 3",
    ]
    for i in range(1, total_frames + 1):
        seq_lines.append(f"I {i} 1")

    seq_path = work / f"{seq_name}.seq"
    seq_path.write_text("\n".join(seq_lines) + "\n", encoding="utf-8")

    import_info = {
        "input": str(src),
        "work": str(work),
        "is_raw": bool(raw_files),
        "frame_count": len(raw_files) if raw_files else len(usable_fits),
        "sequence_name": seq_name,
        "seq_file": str(work / f"{seq_name}.seq"),
        "rejected": rejected_fits,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    dump_json(work / "import_receipt.json", import_info)
    log(f"import complete: sequence '{seq_name}' ready in {work}")


# ----------------------------------------------------------------- 2. register

def _locate_high_contrast_roi(plane, roi_size: int = 1024, downsample: int = 4) -> tuple[int, int, int, int]:
    """Locate the primary lunar terrain region with maximum texture gradient."""
    import cv2
    import numpy as np

    h, w = plane.shape
    small = cv2.resize(plane, (w // downsample, h // downsample), interpolation=cv2.INTER_AREA)

    gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(gx * gx + gy * gy)

    thr = small.mean() + 0.5 * small.std()
    grad_mag[small <= thr] = 0.0

    sh, sw = small.shape
    s_roi = max(32, roi_size // downsample)
    step = max(8, s_roi // 4)

    best_score = -1.0
    best_x, best_y = sw // 2, sh // 2

    for sy in range(0, max(1, sh - s_roi), step):
        for sx in range(0, max(1, sw - s_roi), step):
            patch = grad_mag[sy : sy + s_roi, sx : sx + s_roi]
            score = float(patch.sum())
            if score > best_score:
                best_score = score
                best_x = sx + s_roi // 2
                best_y = sy + s_roi // 2

    cx = best_x * downsample
    cy = best_y * downsample

    half = roi_size // 2
    x0 = int(min(max(cx - half, 0), max(w - roi_size, 0)))
    y0 = int(min(max(cy - half, 0), max(h - roi_size, 0)))
    x1 = min(w, x0 + roi_size)
    y1 = min(h, y0 + roi_size)
    return x0, y0, x1, y1


def _locate_dual_anchor_rois(plane, roi_size: int = 512, downsample: int = 4) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]:
    """Locate two distinct, well-separated high-contrast lunar terrain ROIs for rigid rotation estimation."""
    import cv2
    import numpy as np

    h, w = plane.shape
    small = cv2.resize(plane, (w // downsample, h // downsample), interpolation=cv2.INTER_AREA)

    gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(gx * gx + gy * gy)

    thr = small.mean() + 0.5 * small.std()
    grad_mag[small <= thr] = 0.0

    sh, sw = small.shape
    s_roi = max(32, roi_size // downsample)
    step = max(8, s_roi // 2)

    candidates = []
    for sy in range(0, max(1, sh - s_roi), step):
        for sx in range(0, max(1, sw - s_roi), step):
            score = float(grad_mag[sy : sy + s_roi, sx : sx + s_roi].sum())
            candidates.append((score, sx + s_roi // 2, sy + s_roi // 2))

    if not candidates:
        r1 = _locate_high_contrast_roi(plane, roi_size=roi_size, downsample=downsample)
        return r1, None

    candidates.sort(key=lambda c: c[0], reverse=True)
    best1 = candidates[0]
    cx1 = best1[1] * downsample
    cy1 = best1[2] * downsample

    half = roi_size // 2
    r1 = (
        int(min(max(cx1 - half, 0), max(w - roi_size, 0))),
        int(min(max(cy1 - half, 0), max(h - roi_size, 0))),
        min(w, int(min(max(cx1 - half, 0), max(w - roi_size, 0))) + roi_size),
        min(h, int(min(max(cy1 - half, 0), max(h - roi_size, 0))) + roi_size),
    )

    # Secondary anchor must be sufficiently far away (at least 1/4 of frame dimension)
    min_distance = min(w, h) // 4
    best2 = None
    for cand in candidates[1:]:
        c_x = cand[1] * downsample
        c_y = cand[2] * downsample
        if np.hypot(c_x - cx1, c_y - cy1) >= min_distance:
            best2 = cand
            break

    if not best2:
        return r1, None

    cx2 = best2[1] * downsample
    cy2 = best2[2] * downsample
    r2 = (
        int(min(max(cx2 - half, 0), max(w - roi_size, 0))),
        int(min(max(cy2 - half, 0), max(h - roi_size, 0))),
        min(w, int(min(max(cx2 - half, 0), max(w - roi_size, 0))) + roi_size),
        min(h, int(min(max(cy2 - half, 0), max(h - roi_size, 0))) + roi_size),
    )
    return r1, r2


def _compute_rigid_homography(theta: float, dx: float, dy: float, cx: float, cy: float) -> str:
    """Format Siril R0 homography matrix for rigid rotation (theta in rad) + translation (dx, dy).
    Coordinates follow Siril FITS convention: Y=0 at bottom, increasing upwards.
    """
    import math

    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    h11 = cos_t
    h12 = sin_t
    h13 = cx * (1.0 - cos_t) - cy * sin_t - dx
    h21 = -sin_t
    h22 = cos_t
    h23 = cy * (1.0 - cos_t) + cx * sin_t + dy
    return f"H {h11:.6f} {h12:.6f} {h13:.4f} {h21:.6f} {h22:.6f} {h23:.4f} 0 0 1"


def _read_frame_plane(path: Path):
    """Read single green/mono 2D plane safely with memmap=False."""
    import numpy as np
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
        if data.ndim == 3:
            plane = data[1] if data.shape[0] == 3 else data[0]
        else:
            plane = data
        return np.asarray(plane, dtype=np.float32)


def _subpixel_phase_correlation(ref, img, upsample_factor: int = 20) -> tuple[float, float, float]:
    """Compute subpixel displacement (dx, dy) and confidence response (1 - error).
    Prefers skimage.registration.phase_cross_correlation (Guizar-Sicairos single-step DFT)
    for true subpixel accuracy down to 1/20th of a pixel, with OpenCV Hann fallback.
    """
    import numpy as np

    # Normalize to [0.0, 1.0] float64 to completely eliminate large-frame FFT power overflow
    a = np.asarray(ref, dtype=np.float64)
    b = np.asarray(img, dtype=np.float64)
    max_a, max_b = a.max(), b.max()
    if max_a > 0:
        a /= max_a
    if max_b > 0:
        b /= max_b

    try:
        from skimage.registration import phase_cross_correlation
        shift, error, _ = phase_cross_correlation(a, b, upsample_factor=upsample_factor, normalization=None)
        # shift is [row_shift, col_shift]
        dx = float(-shift[1])
        dy = float(-shift[0])
        # In skimage with normalization=None, error is normalized RMS error:
        # ~0.0 for matching shifted image, ~1.0 for noise / non-correlation.
        response = float(max(0.0, 1.0 - error))
        return dx, dy, response
    except ImportError:
        import cv2
        h, w = ref.shape
        win = cv2.createHanningWindow((w, h), cv2.CV_32F)
        c = np.array(a, dtype=np.float32, copy=True)
        d = np.array(b, dtype=np.float32, copy=True)
        (dx, dy), resp = cv2.phaseCorrelate(c, d, win)
        return float(dx), float(dy), float(resp)


def _measure_sharpness(patch) -> float:
    """Variance of Laplacian on lunar surface."""
    import cv2
    import numpy as np

    f = np.asarray(patch, dtype=np.float32)
    return float(cv2.Laplacian(f, cv2.CV_32F).var())


def align_rgb_channels(master_path: Path, patch_size: int = 512) -> dict:
    """Subpixel Atmospheric Dispersion Correction (ADC) for 3-channel lunar masters.
    Aligns Red and Blue channels to Green channel via windowed phase correlation.
    """
    import cv2
    import numpy as np
    from astropy.io import fits

    with fits.open(master_path, mode="update", memmap=False) as hdul:
        data = hdul[0].data
        if data.ndim != 3 or data.shape[0] < 3:
            return {"applied": False, "reason": "not 3-channel color data"}

        c, h, w = data.shape
        # Locate high-contrast region for accurate channel correlation
        g_full = data[1].astype(np.float32)
        rx0, ry0, rx1, ry1 = _locate_high_contrast_roi(g_full, roi_size=patch_size)

        g_patch = g_full[ry0:ry1, rx0:rx1]
        r_patch = data[0, ry0:ry1, rx0:rx1].astype(np.float32)
        b_patch = data[2, ry0:ry1, rx0:rx1].astype(np.float32)

        ph, pw = g_patch.shape
        win = cv2.createHanningWindow((pw, ph), cv2.CV_32F)

        (dx_r, dy_r), resp_r = cv2.phaseCorrelate(g_patch - g_patch.mean(), r_patch - r_patch.mean(), win)
        (dx_b, dy_b), resp_b = cv2.phaseCorrelate(g_patch - g_patch.mean(), b_patch - b_patch.mean(), win)

        # In phaseCorrelate(G, R), (dx, dy) is shift from G to R.
        # To bring R back into alignment with G, shift by (-dx, -dy).
        shift_r = (-float(dx_r), -float(dy_r))
        shift_b = (-float(dx_b), -float(dy_b))

        applied = False
        r_native = np.asarray(data[0], dtype=np.float32)
        b_native = np.asarray(data[2], dtype=np.float32)

        if abs(dx_r) > 0.03 or abs(dy_r) > 0.03:
            M_r = np.float32([[1, 0, shift_r[0]], [0, 1, shift_r[1]]])
            r_warped = cv2.warpAffine(r_native, M_r, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            data[0] = r_warped.astype(data.dtype)
            applied = True

        if abs(dx_b) > 0.03 or abs(dy_b) > 0.03:
            M_b = np.float32([[1, 0, shift_b[0]], [0, 1, shift_b[1]]])
            b_warped = cv2.warpAffine(b_native, M_b, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            data[2] = b_warped.astype(data.dtype)
            applied = True

        hdul.flush()
        return {
            "applied": applied,
            "shift_r": shift_r,
            "shift_b": shift_b,
            "response_r": float(resp_r),
            "response_b": float(resp_b),
            "roi": [rx0, ry0, rx1, ry1],
        }


def _select_frames_by_quality(
    valid_items: list[dict],
    total_count: int,
    select_mode: str = "otsu",
    keep_percent: float | None = None,
    quality_threshold: float = 0.75,
) -> tuple[set[int], dict]:
    """Intelligent frame selection based on reference frame quality or user criteria.

    Modes:
      - 'otsu' (default): Adaptive bimodal clustering using Otsu's threshold on the 1D
        sharpness spectrum. Automatically identifies natural seeing breakpoint between
        calm seeing and turbulent/blurred frames without arbitrary manual percentages.
      - 'relative': Retains frames with sharpness >= quality_threshold * reference_sharpness.
      - 'percent': Traditional percentage ranking (with small-sample tiering if keep_percent is None).
    """
    import numpy as np

    if not valid_items:
        return set(), {"mode": select_mode, "kept_count": 0, "kept_percent": 0.0}

    valid_items.sort(key=lambda it: it["sharpness"], reverse=True)
    ref_sharpness = float(valid_items[0]["sharpness"])
    sharpnesses = np.array([it["sharpness"] for it in valid_items], dtype=np.float64)

    # If keep_percent is explicitly specified by user (> 0), honor it as 'percent' mode
    if keep_percent is not None and keep_percent > 0:
        select_mode = "percent"

    meta: dict = {
        "mode": select_mode,
        "ref_sharpness": ref_sharpness,
    }

    if select_mode == "otsu":
        if len(valid_items) < 4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = float(valid_items[-1]["sharpness"])
            meta["threshold_rel"] = 1.0
            meta["reason"] = "sample size < 4, retained all valid frames"
        elif (sharpnesses.max() - sharpnesses.min()) < 1e-4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = float(sharpnesses.min())
            meta["threshold_rel"] = 1.0
            meta["reason"] = "uniform sharpness distribution"
        else:
            from skimage.filters import threshold_otsu

            th = float(threshold_otsu(sharpnesses))
            meta["threshold"] = th
            meta["threshold_rel"] = round(th / ref_sharpness, 4)
            otsu_kept = [it for it in valid_items if it["sharpness"] >= th]
            # Safety bounds: keep at least 3 frames (or all if < 3) and at least 10%
            min_k = min(len(valid_items), max(3, int(round(total_count * 0.10))))
            if len(otsu_kept) < min_k:
                otsu_kept = valid_items[:min_k]
                meta["safety_clamped"] = "min_k"
            kept_indices = {it["index"] for it in otsu_kept}

    elif select_mode == "relative":
        abs_threshold = ref_sharpness * float(quality_threshold)
        rel_kept = [it for it in valid_items if it["sharpness"] >= abs_threshold]
        min_k = min(len(valid_items), max(3, int(round(total_count * 0.10))))
        if len(rel_kept) < min_k:
            rel_kept = valid_items[:min_k]
            meta["safety_clamped"] = "min_k"
        kept_indices = {it["index"] for it in rel_kept}
        meta["threshold"] = abs_threshold
        meta["threshold_rel"] = float(quality_threshold)

    else:  # percent
        if keep_percent is None or keep_percent <= 0:
            if total_count <= 60:
                keep_percent = 70.0
            elif total_count <= 300:
                keep_percent = 50.0
            else:
                keep_percent = 30.0
        n_keep = max(1, int(round(total_count * float(keep_percent) / 100.0)))
        kept_indices = {it["index"] for it in valid_items[:n_keep]}
        meta["keep_percent"] = float(keep_percent)
        if n_keep <= len(valid_items):
            cutoff_s = float(valid_items[n_keep - 1]["sharpness"])
            meta["threshold"] = cutoff_s
            meta["threshold_rel"] = round(cutoff_s / ref_sharpness, 4)

    meta["kept_count"] = len(kept_indices)
    meta["kept_percent"] = round(len(kept_indices) / total_count * 100.0, 2)
    return kept_indices, meta


def cmd_register(args) -> None:
    from astropy.io import fits

    work = Path(args.work).expanduser().resolve()
    seq_path = work / f"{args.seq}.seq"
    if not seq_path.exists():
        die(f"sequence file not found: {seq_path}; run import first")

    # Parse .seq file to get image list
    seq_lines = seq_path.read_text(encoding="utf-8").splitlines()
    s_line = next((ln for ln in seq_lines if ln.startswith("S ")), None)
    if not s_line:
        die("invalid .seq file: missing S header line")

    parts = s_line.split()
    seq_name = parts[1].strip("'\"")
    start_idx = int(parts[2])
    nb_images = int(parts[3])
    fixed_len = int(parts[5])

    image_files = []
    for i in range(start_idx, start_idx + nb_images):
        fname = f"{seq_name}{i:0{fixed_len}d}.fit"
        fpath = work / fname
        if not fpath.exists():
            die(f"missing frame in sequence: {fpath}")
        image_files.append({"index": i, "file": fname, "path": fpath})

    log(f"registering {len(image_files)} frames from sequence '{seq_name}'...")

    # Choose candidate reference frame (center of sequence) to establish ROI
    mid_frame = image_files[len(image_files) // 2]
    mid_plane = _read_frame_plane(mid_frame["path"])
    roi = _locate_high_contrast_roi(mid_plane, roi_size=args.roi)
    rx0, ry0, rx1, ry1 = roi
    log(f"localized high-contrast lunar feature ROI: [{rx0}:{rx1}, {ry0}:{ry1}] ({rx1-rx0}x{ry1-ry0})")

    # Pass 1: compute phase shift relative to candidate frame
    raw_shifts = []
    for item in image_files:
        plane = _read_frame_plane(item["path"])
        dx, dy, resp = _subpixel_phase_correlation(mid_plane, plane)
        item["coarse_dx"] = dx
        item["coarse_dy"] = dy
        item["resp"] = resp

        # Measure sharpness on aligned feature ROI
        sx0 = int(round(rx0 + dx))
        sy0 = int(round(ry0 + dy))
        sx1 = sx0 + (rx1 - rx0)
        sy1 = sy0 + (ry1 - ry0)
        if sx0 >= 0 and sy0 >= 0 and sx1 <= plane.shape[1] and sy1 <= plane.shape[0]:
            patch = plane[sy0:sy1, sx0:sx1]
            sharpness = _measure_sharpness(patch)
        else:
            sharpness = 0.0
        item["sharpness"] = sharpness

    # Exclude outliers with low phase correlation response (clouds, extreme shake)
    valid_items = [it for it in image_files if it["resp"] >= args.min_confidence and it["sharpness"] > 0]
    if not valid_items:
        die("no frames met the correlation confidence threshold; check data quality or lower --min-confidence")

    # Pick the sharpest frame as true reference
    best_item = max(valid_items, key=lambda it: it["sharpness"])
    ref_idx = best_item["index"]
    ref_dx = best_item["coarse_dx"]
    ref_dy = best_item["coarse_dy"]
    log(f"selected reference frame: #{ref_idx} (sharpness={best_item['sharpness']:.1f}, resp={best_item['resp']:.2f})")

    # Pass 2: Dual-anchor rigid transformation estimation (field rotation theta + translation dx, dy)
    import math

    ref_item = next(it for it in image_files if it["index"] == ref_idx)
    ref_plane = _read_frame_plane(ref_item["path"])
    fh, fw = ref_plane.shape
    cx_img, cy_img = fw / 2.0, fh / 2.0

    dual_roi1, dual_roi2 = _locate_dual_anchor_rois(ref_plane, roi_size=min(args.roi, 512))
    has_dual_anchor = dual_roi2 is not None
    if has_dual_anchor:
        log(f"dual anchors localized for rigid rotation: Anchor 1={dual_roi1}, Anchor 2={dual_roi2}")
        p1_center = ((dual_roi1[0] + dual_roi1[2]) / 2.0, (dual_roi1[1] + dual_roi1[3]) / 2.0)
        p2_center = ((dual_roi2[0] + dual_roi2[2]) / 2.0, (dual_roi2[1] + dual_roi2[3]) / 2.0)
        ref_p1 = ref_plane[dual_roi1[1]:dual_roi1[3], dual_roi1[0]:dual_roi1[2]]
        ref_p2 = ref_plane[dual_roi2[1]:dual_roi2[3], dual_roi2[0]:dual_roi2[2]]
        vec_ref = (p2_center[0] - p1_center[0], p2_center[1] - p1_center[1])
        angle_ref = math.atan2(vec_ref[1], vec_ref[0])
    else:
        log(f"single anchor localized: {dual_roi1}")
        ref_p1 = ref_plane[dual_roi1[1]:dual_roi1[3], dual_roi1[0]:dual_roi1[2]]

    for it in image_files:
        plane = _read_frame_plane(it["path"])
        dx_c = it["coarse_dx"] - ref_dx
        dy_c = it["coarse_dy"] - ref_dy

        sx0 = int(round(dual_roi1[0] + dx_c))
        sy0 = int(round(dual_roi1[1] + dy_c))
        sx1 = sx0 + (dual_roi1[2] - dual_roi1[0])
        sy1 = sy0 + (dual_roi1[3] - dual_roi1[1])
        if sx0 >= 0 and sy0 >= 0 and sx1 <= fw and sy1 <= fh:
            target_p1 = plane[sy0:sy1, sx0:sx1]
            f_dx1, f_dy1, resp1 = _subpixel_phase_correlation(ref_p1, target_p1)
            total_dx = dx_c + f_dx1
            total_dy = dy_c + f_dy1
        else:
            total_dx, total_dy = dx_c, dy_c

        theta = 0.0
        if has_dual_anchor:
            sx2_0 = int(round(dual_roi2[0] + dx_c))
            sy2_0 = int(round(dual_roi2[1] + dy_c))
            sx2_1 = sx2_0 + (dual_roi2[2] - dual_roi2[0])
            sy2_1 = sy2_0 + (dual_roi2[3] - dual_roi2[1])
            if sx2_0 >= 0 and sy2_0 >= 0 and sx2_1 <= fw and sy2_1 <= fh:
                target_p2 = plane[sy2_0:sy2_1, sx2_0:sx2_1]
                f_dx2, f_dy2, resp2 = _subpixel_phase_correlation(ref_p2, target_p2)
                if resp2 >= 0.15:
                    p2_mov = (p2_center[0] + dx_c + f_dx2, p2_center[1] + dy_c + f_dy2)
                    p1_mov = (p1_center[0] + total_dx, p1_center[1] + total_dy)
                    vec_mov = (p2_mov[0] - p1_mov[0], p2_mov[1] - p1_mov[1])
                    angle_mov = math.atan2(vec_mov[1], vec_mov[0])
                    d_theta = -(angle_mov - angle_ref)
                    if abs(d_theta) < 0.1:  # Physical limit: within ~5.7 degrees
                        theta = d_theta

        it["dx"] = total_dx
        it["dy"] = total_dy
        it["theta"] = theta
        it["homography"] = _compute_rigid_homography(theta, total_dx, total_dy, cx_img, cy_img)

    # Adaptive frame selection using Otsu bimodal clustering (or user-selected mode)
    select_mode = getattr(args, "select_mode", "otsu")
    keep_pct_arg = getattr(args, "keep_percent", None)
    quality_thresh = getattr(args, "quality_threshold", 0.75)

    kept_indices, select_meta = _select_frames_by_quality(
        valid_items,
        total_count=len(image_files),
        select_mode=select_mode,
        keep_percent=keep_pct_arg,
        quality_threshold=quality_thresh,
    )

    for it in image_files:
        it["selected"] = it["index"] in kept_indices

    th_str = f"{select_meta['threshold']:.1f} ({select_meta.get('threshold_rel', 0)*100:.1f}% of ref)" if "threshold" in select_meta else "N/A"
    log(f"frame selection: mode='{select_meta['mode']}', cutoff={th_str}, keeping {len(kept_indices)}/{len(image_files)} best frames ({select_meta['kept_percent']}%)")

    # Inject R0 homography matrices & selection into Siril's .seq file
    new_s_line = (
        f"S '{seq_name}' {start_idx} {nb_images} {len(kept_indices)} {fixed_len} "
        f"{ref_idx - start_idx} 6 0 0 0"
    )

    new_seq_lines = [
        "#Siril sequence file. Generated by siril-moon-stacking with rigid homography registration",
        new_s_line,
        "L 3",
    ]
    for it in image_files:
        new_seq_lines.append(f"I {it['index']} {1 if it['selected'] else 0}")

    for it in image_files:
        new_seq_lines.append(f"R0 1.0 1.0 1.0 0 0.0 1 {it['homography']}")

    seq_path.write_text("\n".join(new_seq_lines) + "\n", encoding="utf-8")
    log(f"updated Siril sequence file: {seq_path}")

    ranking_data = {
        "sequence": seq_name,
        "reference_index": ref_idx,
        "reference_sharpness": float(best_item["sharpness"]),
        "dual_anchors": [dual_roi1, dual_roi2] if has_dual_anchor else [dual_roi1],
        "total_frames": len(image_files),
        "kept_frames": len(kept_indices),
        "keep_percent": select_meta["kept_percent"],
        "selection_meta": select_meta,
        "frames": [
            {
                "index": it["index"],
                "file": it["file"],
                "sharpness": it["sharpness"],
                "response": it["resp"],
                "shift": [round(it["dx"], 4), round(it["dy"], 4)],
                "rotation_deg": round(math.degrees(it["theta"]), 4),
                "selected": it["selected"],
            }
            for it in image_files
        ],
    }
    dump_json(work / "ranking.json", ranking_data)
    log(f"registration & ranking complete: data dumped to {work / 'ranking.json'}")


# -------------------------------------------------------------------- 3. stack

def cmd_stack(args) -> None:
    work = Path(args.work).expanduser().resolve()
    seq_name = args.seq
    seq_path = work / f"{seq_name}.seq"
    if not seq_path.exists():
        die(f"sequence file {seq_path} not found; run register first")

    logs_dir = work / "logs"
    master = work / args.out

    # Inspect bit depth of input frames to select stacking method:
    # - 8-bit (AVI / planetary SER): sum stack to expand dynamic range (8-bit -> 14+ bit equivalent)
    # - 16-bit+ (FITS / 16-bit SER): Winsorized rejection mean stack (rej w)
    # - Strictly excludes: median (only for dark/flat/bias), max (star trails), min
    first_frame = next(work.glob(f"{seq_name}*.fit"), None)
    is_8bit = False
    if first_frame:
        try:
            h = fits_header_info(first_frame)
            if h.get("bitpix") == 8:
                is_8bit = True
        except Exception:
            pass

    if is_8bit:
        stack_cmd = f"stack r_{seq_name} sum -filter-included -out={master.stem}"
        log("detected 8-bit source data: applying 'sum' stacking to physically expand dynamic range")
    else:
        stack_cmd = f"stack r_{seq_name} rej w {args.sigma[0]} {args.sigma[1]} -norm={args.norm} -filter-included -out={master.stem}"
        log("detected 16-bit+ source data: applying Winsorized rejection mean stacking (rej w)")

    framing = getattr(args, "framing", "min")
    maximize_flag = " -maximize" if framing == "max" else ""

    interp_raw = getattr(args, "interp", "cu")
    interp_map = {
        "linear": "li", "bilinear": "li", "li": "li",
        "cubic": "cu", "bicubic": "cu", "cu": "cu",
        "lanczos": "la", "lanczos4": "la", "la": "la",
        "none": "no", "no": "no"
    }
    interp = interp_map.get(str(interp_raw).lower(), "cu")

    lines = [
        "requires 1.4.4",
        # Multithreaded subpixel resampling with strict clamping (no -noclamp) to avoid undershoot overflows
        f"seqapplyreg {seq_name} -framing={framing} -interp={interp} -filter-incl",
        f"{stack_cmd}{maximize_flag}",
        "exit",
    ]

    log(f"executing Siril seqapplyreg (framing={framing}, interp={interp}, clamped) & stack pipeline...")
    receipt = run_siril_script(args.siril, lines, work, logs_dir / "02_align_stack.log", args.timeout)
    if receipt["exit_code"] != 0 or not master.exists():
        die(f"stacking failed (exit {receipt['exit_code']}); see {receipt['log']}")

    receipt["interp"] = interp
    receipt["framing"] = framing
    dump_json(work / "stack_receipt.json", receipt)
    log(f"master stack generated successfully: {master} (interp={interp})")


# -------------------------------------------------------------- 4. postprocess

def cmd_postprocess(args) -> None:
    from astropy.io import fits
    import numpy as np

    work = Path(args.work).expanduser().resolve()
    master = work / args.master
    if not master.exists():
        die(f"master FITS not found: {master}; run stack first")

    logs_dir = work / "logs"

    # 0. Atmospheric Dispersion Correction (ADC)
    if not getattr(args, "no_adc", False):
        adc_res = align_rgb_channels(master)
        if adc_res.get("applied"):
            log(f"ADC applied: R shift={adc_res['shift_r']}, B shift={adc_res['shift_b']} (resp R={adc_res['response_r']:.2f}, B={adc_res['response_b']:.2f})")
            dump_json(work / "adc_receipt.json", adc_res)
        else:
            log("ADC check: channels aligned or single channel data")

    # Analyze linear master data for planetary histogram stretch & white balance
    with fits.open(master, memmap=False) as hdul:
        hdr = hdul[0].header
        d = hdul[0].data

    px_size = float(getattr(args, "pixel_size", 0) or hdr.get("XPIXSZ") or 3.73)
    fl = float(getattr(args, "focal", 0) or hdr.get("FOCALLEN") or 400.0)
    dia = float(getattr(args, "aperture", 0) or 80.0)
    deconv_method = getattr(args, "deconv", "sb")

    deconv_lines = []
    if deconv_method == "sb":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks=25 -savepsf=psf_airy.fit",
            "sb -loadpsf=psf_airy.fit -iters=2 -alpha=2000",
        ]
        log(f"deconvolution: Split Bregman with physical Airy PSF (dia={dia}mm, fl={fl}mm, px={px_size}um)")
    elif deconv_method == "wiener":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks=25 -savepsf=psf_airy.fit",
            "wiener -loadpsf=psf_airy.fit -alpha=0.01",
        ]
        log(f"deconvolution: Wiener with physical Airy PSF (dia={dia}mm, fl={fl}mm, px={px_size}um)")
    elif deconv_method == "rl":
        deconv_lines = [
            "makepsf manual -gaussian -fwhm=2.0 -savepsf=psf.fit",
            "rl -loadpsf=psf.fit -iters=10 -tv -alpha=1000",
        ]
        log("deconvolution: classic Richardson-Lucy with Gaussian PSF")
    else:
        log("deconvolution: bypassed (none)")

    if d.ndim == 3 and d.shape[0] >= 3:
        # Measure camera pedestal / black point from corner patches
        bg_r = float(np.median(d[0, :100, :100]))
        bg_g = float(np.median(d[1, :100, :100]))
        bg_b = float(np.median(d[2, :100, :100]))
        # Measure lunar highlight peaks per channel (99.95th percentile with 10% dynamic headroom)
        p999_r = float(np.percentile(d[0], 99.95))
        p999_g = float(np.percentile(d[1], 99.95))
        p999_b = float(np.percentile(d[2], 99.95))

        hi_r = max(p999_r * 1.10, bg_r + 0.01)
        hi_g = max(p999_g * 1.10, bg_g + 0.01)
        hi_b = max(p999_b * 1.10, bg_b + 0.01)

        mid_val = getattr(args, "midtone", 0.13)
        log(f"calibrating planetary white balance with highlight protection (bg=[{bg_r:.5f}, {bg_g:.5f}, {bg_b:.5f}], hi=[{hi_r:.5f}, {hi_g:.5f}, {hi_b:.5f}], midtone={mid_val})")
        mtf_lines = [
            f"mtf {bg_r:.6f} {mid_val:.2f} {hi_r:.6f} R",
            f"mtf {bg_g:.6f} {mid_val:.2f} {hi_g:.6f} G",
            f"mtf {bg_b:.6f} {mid_val:.2f} {hi_b:.6f} B",
            "rmgreen 1 0.8",
        ]
    else:
        plane = d if d.ndim == 2 else d[0]
        bg_val = float(np.median(plane[:100, :100]))
        p999_val = float(np.percentile(plane, 99.95))
        hi_val = max(p999_val * 1.10, bg_val + 0.01)
        mid_val = getattr(args, "midtone", 0.13)
        mtf_lines = [
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
        ]

    # Detect interpolation method from stack receipt for interp-aware wavelet tuning
    interp_used = "cu"
    stack_receipt_file = work / "stack_receipt.json"
    if stack_receipt_file.exists():
        try:
            sr = load_json(stack_receipt_file)
            interp_used = sr.get("interp", "cu")
        except Exception:
            pass

    wavelet_l1 = getattr(args, "wavelet_l1", None)
    if wavelet_l1 is None:
        if interp_used == "li":
            wavelet_l1 = 1.10
            wrecons_cmd = "wrecons 1.10 1.22 1.25 1.15 1.00 1.00"
            log(f"interp-aware wavelet tuning: using {wrecons_cmd} (bilinear MTF compensation mode)")
        else:
            wavelet_l1 = 1.05
            wrecons_cmd = "wrecons 1.05 1.20 1.25 1.15 1.00 1.00"
            log(f"interp-aware wavelet tuning: using {wrecons_cmd} (bicubic overshoot-suppression mode)")
    else:
        wrecons_cmd = f"wrecons {wavelet_l1:.2f} 1.20 1.25 1.15 1.00 1.00"
        log(f"custom wavelet tuning: using {wrecons_cmd}")

    # Full professional lunar post-processing chain:
    # 1. Airy / Gaussian Deconvolution (Split Bregman / Wiener / RL)
    # 2. Planetary channel MTF white balance & stretch with highlight protection
    # 3. Multiscale 'à trous' B-Spline wavelet detail reconstruction (executed BEFORE CLAHE to avoid noise amplification)
    # 4. Lightweight CLAHE (post-wavelet macro contrast, default clip=1.0)
    # 5. Fine unsharp mask for micro-contrast
    # 6. Progressive mineral saturation boost (preserving neutral black space)
    clahe_clip = float(getattr(args, "clahe_clip", 1.0))
    clahe_lines = [f"clahe {clahe_clip:.1f} 32"] if clahe_clip > 0 else []

    lines = [
        "requires 1.4.4",
        f"load {master.name}",
        *deconv_lines,
        # 2. Planetary MTF white balance & background offset neutralization
        *mtf_lines,
        # 3. 5-layer B-spline wavelet transform (frequency-inverted noise attenuation)
        "wavelet 5 2",
        wrecons_cmd,
        # 4. Lightweight CLAHE local contrast (operates on clean reconstructed details)
        *clahe_lines,
        # 5. Micro-contrast unsharp mask (tight radius to prevent dark rings)
        "unsharp 1.0 0.3",
        # Export natural version
        "savetif moon_natural -astro",
        "savejpg moon_natural 95",
        # 6. Progressive mineral saturation boost
        "satu 0.7 1.2",
        "satu 0.4 1.0",
        # Export mineral version
        "savejpg moon_mineral 95",
        "exit",
    ]

    log("executing Siril multiscale wavelets & mineral color enhancement...")
    receipt = run_siril_script(args.siril, lines, work, logs_dir / "03_postprocess.log", args.timeout)
    if receipt["exit_code"] != 0:
        die(f"postprocessing failed (exit {receipt['exit_code']}); see {receipt['log']}")

    dump_json(work / "postprocess_receipt.json", receipt)
    log(f"postprocessing complete! Products in {work}:")
    log(f"  - Natural master TIFF: {work / 'moon_natural.tif'}")
    log(f"  - Natural JPG:         {work / 'moon_natural.jpg'}")
    log(f"  - Mineral Moon JPG:    {work / 'moon_mineral.jpg'}")


# ------------------------------------------------------------------- 5. verify

def cmd_verify(args) -> None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(args.work).expanduser().resolve()
    master_path = work / args.master
    nat_tif = work / "moon_natural.tif"
    rank_json = work / "ranking.json"

    if not master_path.exists():
        die(f"master file not found: {master_path}")

    # Inspect master
    with fits.open(master_path, memmap=False) as hdul:
        master_data = hdul[0].data
        m_hdr = hdul[0].header

    report = {
        "master": str(master_path),
        "shape": list(master_data.shape),
        "bitpix": m_hdr.get("BITPIX"),
        "min": float(master_data.min()),
        "max": float(master_data.max()),
        "mean": float(master_data.mean()),
    }

    # Background noise in corner
    bg_patch = master_data[:, :150, :150] if master_data.ndim == 3 else master_data[:150, :150]
    report["background_noise_std"] = float(bg_patch.std())

    # Measure surface sharpness on master vs single frame
    if rank_json.exists():
        r_info = load_json(rank_json)
        ref_idx = r_info.get("reference_index")
        report["reference_frame"] = ref_idx
        report["kept_frames"] = r_info.get("kept_frames")

    dump_json(work / "verify_report.json", report)
    log("verification summary:")
    log(f"  Master dimensions: {report['shape']} (BITPIX={report['bitpix']})")
    log(f"  Pixel range: [{report['min']:.4f}, {report['max']:.4f}] (mean={report['mean']:.4f})")
    log(f"  Background noise std: {report['background_noise_std']:.6f}")
    if (work / "moon_natural.jpg").exists():
        log(f"  [OK] moon_natural.jpg ({round((work / 'moon_natural.jpg').stat().st_size / 1024)} KB)")
    if (work / "moon_mineral.jpg").exists():
        log(f"  [OK] moon_mineral.jpg ({round((work / 'moon_mineral.jpg').stat().st_size / 1024)} KB)")


# ---------------------------------------------------------------------- 6. all

def cmd_all(args) -> None:
    cmd_import(args)
    cmd_register(args)
    cmd_stack(args)
    cmd_postprocess(args)
    cmd_verify(args)


# -------------------------------------------------------------------- 7. probe

def cmd_probe(args) -> None:
    info = {"siril": args.siril, "siril_exists": os.path.exists(args.siril)}
    if info["siril_exists"]:
        env = dict(os.environ, LANG="C")
        p = subprocess.run([args.siril, "-v"], capture_output=True, text=True, env=env, timeout=60)
        info["siril_version"] = (p.stdout or "").strip().splitlines()[-1:]
    for mod in ("numpy", "astropy", "cv2", "skimage"):
        try:
            m = __import__(mod)
            info[mod] = getattr(m, "__version__", "ok")
        except Exception:
            info[mod] = None
    print(json.dumps(info, indent=2))


# ----------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Professional Moon Stacking via Siril CLI + Subpixel Registration Plugin")
    p.add_argument("--siril", default=DEFAULT_SIRIL)
    p.add_argument("--timeout", type=int, default=3600)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("probe")
    a.set_defaults(func=cmd_probe)

    a = sub.add_parser("import")
    a.add_argument("--input", required=True, help="Input directory containing RAW or FITS frames")
    a.add_argument("--work", required=True, help="Isolated working directory")
    a.add_argument("--format", default="auto", choices=["auto", "fits", "raw"], help="Force format selection")
    a.add_argument("--limit", type=int, default=0, help="Limit number of frames to import (0=all)")
    a.set_defaults(func=cmd_import)

    a = sub.add_parser("register")
    a.add_argument("--work", required=True)
    a.add_argument("--seq", default="moon_")
    a.add_argument("--roi", type=int, default=1024)
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "relative", "percent"],
                   help="Frame selection mode: 'otsu' (adaptive bimodal seeing clustering, default), 'relative' (relative to reference frame), 'percent' (fixed/tiered percentage)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Minimum sharpness relative to reference frame (0.0 - 1.0) for 'relative' mode (default: 0.75)")
    a.add_argument("--keep-percent", type=float, default=None, help="Frame selection ratio in percent (switches to 'percent' mode if specified)")
    a.add_argument("--min-confidence", type=float, default=0.15)
    a.set_defaults(func=cmd_register)

    a = sub.add_parser("stack")
    a.add_argument("--work", required=True)
    a.add_argument("--seq", default="moon_")
    a.add_argument("--out", default="moon_master.fit")
    a.add_argument("--framing", default="min", choices=["min", "max", "cog"], help="Framing mode for resampling")
    a.add_argument("--interp", default="cu", choices=["cu", "li", "la", "none", "cubic", "linear", "lanczos", "bilinear"],
                   help="Resampling interpolation: 'li'/'linear' (bilinear, conservative, zero overshoot), 'cu'/'cubic' (bicubic, high MTF, default), 'la'/'lanczos' (lanczos4)")
    a.add_argument("--sigma", nargs=2, default=["3", "3"])
    a.add_argument("--norm", default="addscale")
    a.set_defaults(func=cmd_stack)

    a = sub.add_parser("postprocess")
    a.add_argument("--work", required=True)
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--deconv", default="sb", choices=["sb", "wiener", "rl", "none"], help="Deconvolution method (sb=Split Bregman, wiener, rl, none)")
    a.add_argument("--no-adc", action="store_true", help="Disable Atmospheric Dispersion Correction (RGB channel alignment)")
    a.add_argument("--midtone", type=float, default=0.13, help="MTF midtone stretch value with highlight protection (default: 0.13)")
    a.add_argument("--wavelet-l1", type=float, default=None, help="Layer 1 wavelet gain (default: adaptive 1.05 for bicubic, 1.10 for bilinear)")
    a.add_argument("--clahe-clip", type=float, default=1.0, help="CLAHE clip limit (default: 1.0, set <=0 to bypass CLAHE)")
    a.add_argument("--aperture", type=float, default=80.0, help="Telescope aperture in mm (for Airy PSF)")
    a.add_argument("--focal", type=float, default=400.0, help="Telescope focal length in mm (for Airy PSF)")
    a.add_argument("--pixel-size", type=float, default=3.73, help="Sensor pixel size in microns (for Airy PSF)")
    a.set_defaults(func=cmd_postprocess)

    a = sub.add_parser("verify")
    a.add_argument("--work", required=True)
    a.add_argument("--master", default="moon_master.fit")
    a.set_defaults(func=cmd_verify)

    a = sub.add_parser("all")
    a.add_argument("--input", required=True)
    a.add_argument("--work", required=True)
    a.add_argument("--format", default="auto", choices=["auto", "fits", "raw"], help="Force format selection")
    a.add_argument("--limit", type=int, default=0, help="Limit number of frames to import (0=all)")
    a.add_argument("--seq", default="moon_")
    a.add_argument("--roi", type=int, default=1024)
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "relative", "percent"],
                   help="Frame selection mode: 'otsu' (adaptive bimodal seeing clustering, default), 'relative' (relative to reference frame), 'percent' (fixed/tiered percentage)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Minimum sharpness relative to reference frame (0.0 - 1.0) for 'relative' mode (default: 0.75)")
    a.add_argument("--keep-percent", type=float, default=None, help="Frame selection ratio in percent (switches to 'percent' mode if specified)")
    a.add_argument("--min-confidence", type=float, default=0.15)
    a.add_argument("--framing", default="min", choices=["min", "max", "cog"])
    a.add_argument("--interp", default="cu", choices=["cu", "li", "la", "none", "cubic", "linear", "lanczos", "bilinear"],
                   help="Resampling interpolation: 'li'/'linear' (bilinear, zero overshoot), 'cu'/'cubic' (bicubic, default)")
    a.add_argument("--out", default="moon_master.fit")
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--sigma", nargs=2, default=["3", "3"])
    a.add_argument("--norm", default="addscale")
    a.add_argument("--deconv", default="sb", choices=["sb", "wiener", "rl", "none"])
    a.add_argument("--no-adc", action="store_true")
    a.add_argument("--midtone", type=float, default=0.13)
    a.add_argument("--wavelet-l1", type=float, default=None, help="Layer 1 wavelet gain (default: adaptive 1.05 for cu, 1.10 for li)")
    a.add_argument("--clahe-clip", type=float, default=1.0, help="CLAHE clip limit (default: 1.0, set <=0 to bypass CLAHE)")
    a.add_argument("--aperture", type=float, default=80.0)
    a.add_argument("--focal", type=float, default=400.0)
    a.add_argument("--pixel-size", type=float, default=3.73)
    a.set_defaults(func=cmd_all)

    return p


if __name__ == "__main__":
    ns = build_parser().parse_args()
    ns.func(ns)
