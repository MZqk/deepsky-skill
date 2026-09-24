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

import numpy as np

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
    utility_alpha: float = 2.0,
    utility_beta: float = 1.0,
) -> tuple[set[int], dict]:
    """Intelligent frame selection based on reference frame quality or user criteria.

    Modes:
      - 'otsu' (default): Adaptive bimodal clustering using Otsu's threshold on the 1D
        sharpness spectrum. Automatically identifies natural seeing breakpoint between
        calm seeing and turbulent/blurred frames without arbitrary manual percentages.
      - 'utility' / 'mtf-snr': MTF-SNR joint utility optimization model. Maximizes
        U(k) = (mean_contrast_norm(k) ** alpha) * (sqrt(k / N) ** beta), finding
        the mathematically optimal trade-off between optical resolution and noise reduction.
      - 'relative': Retains frames with sharpness >= quality_threshold * reference_sharpness.
      - 'percent': Traditional percentage ranking (with small-sample tiering if keep_percent is None).
    """
    import numpy as np

    if not valid_items:
        return set(), {"mode": select_mode, "kept_count": 0, "kept_percent": 0.0}

    valid_items.sort(key=lambda it: it["sharpness"], reverse=True)
    ref_sharpness = float(valid_items[0]["sharpness"])
    sharpnesses = np.array([it["sharpness"] for it in valid_items], dtype=np.float64)

    # Normalize select_mode
    if select_mode in ("mtf-snr", "mtf_snr"):
        select_mode = "utility"

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

    elif select_mode == "utility":
        # MTF-SNR joint utility optimization
        s_min = float(sharpnesses.min())
        s_max = float(sharpnesses.max())
        n_valid = len(valid_items)

        if n_valid < 4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = float(valid_items[-1]["sharpness"])
            meta["threshold_rel"] = 1.0
            meta["reason"] = "sample size < 4, retained all valid frames"
        elif (s_max - s_min) < 1e-4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = s_min
            meta["threshold_rel"] = 1.0
            meta["reason"] = "uniform sharpness distribution"
        else:
            # Baseline-adjusted normalized sharpness in [0, 1]
            q_norm = (sharpnesses - s_min) / (s_max - s_min + 1e-6)
            min_k = min(n_valid, max(3, int(round(total_count * 0.10))))
            max_k = min(n_valid, max(min_k, int(round(total_count * 0.95))))

            utility_scores = []
            for k in range(1, n_valid + 1):
                q_mean = float(np.mean(q_norm[:k]))
                snr_factor = float(np.sqrt(k / total_count))
                u = (q_mean ** float(utility_alpha)) * (snr_factor ** float(utility_beta))
                utility_scores.append(u)

            search_scores = utility_scores[min_k - 1 : max_k]
            best_k_offset = int(np.argmax(search_scores))
            best_k = min_k + best_k_offset

            u_kept = valid_items[:best_k]
            kept_indices = {it["index"] for it in u_kept}
            cutoff_s = float(valid_items[best_k - 1]["sharpness"])
            meta["threshold"] = cutoff_s
            meta["threshold_rel"] = round(cutoff_s / ref_sharpness, 4)
            meta["utility_alpha"] = float(utility_alpha)
            meta["utility_beta"] = float(utility_beta)
            meta["max_utility"] = round(float(utility_scores[best_k - 1]), 4)

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
    u_alpha = getattr(args, "utility_alpha", 2.0)
    u_beta = getattr(args, "utility_beta", 1.0)

    kept_indices, select_meta = _select_frames_by_quality(
        valid_items,
        total_count=len(image_files),
        select_mode=select_mode,
        keep_percent=keep_pct_arg,
        quality_threshold=quality_thresh,
        utility_alpha=u_alpha,
        utility_beta=u_beta,
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

def _estimate_pedestal(plane: np.ndarray) -> float:
    """Robustly estimate sky background or sensor black pedestal.

    Avoids framing zero-padding, off-center lunar disk overlap in corners,
    and dark crater detail clipping on full-frame close-up lunar shots.
    Computes a safe noise ceiling (median + 2.0*std) on valid sky corners
    so that background noise cleanly clips to 0 and avoids colored fringing.
    """
    import numpy as np

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

        # Check if the darkest corner represents true space background:
        # In wide lunar shots, space background has very low variance and brightness is in lower 70% of frame.
        is_sky = False
        if dyn_range > 1e-4:
            if min_corner <= float(np.percentile(valid, 70)) and (min_std < 0.10 * dyn_range):
                is_sky = True
        else:
            is_sky = True

        if is_sky:
            safe_ceiling = min_corner + 2.0 * min_std
            return float(min(safe_ceiling, p_high * 0.50))

    # Close-up / full-frame moon without dark sky corners: return low percentile
    return max(0.0, p_low)


def _fit_lunar_limb_circle(lum_2d: np.ndarray) -> tuple[float, float, float, float] | None:
    """Fit a high-precision lunar physical circle using radial gradient inflection points.

    Instead of relying on thresholding (which captures diffuse atmospheric glare),
    this algorithm casts radial rays from the approximate center and finds the point
    of maximum negative radial gradient (the true physical limb edge), then applies
    RANSAC circle fitting with sub-pixel precision.
    """
    H, W = lum_2d.shape
    p999 = float(np.percentile(lum_2d, 99.95))
    if p999 <= 1e-5:
        return None

    import cv2

    core_mask = (lum_2d > 0.35 * p999).astype(np.uint8)
    M = cv2.moments(core_mask)
    if M["m00"] == 0:
        return None
    cx_init = float(M["m10"] / M["m00"])
    cy_init = float(M["m01"] / M["m00"])

    dense_angles = np.linspace(-np.radians(65), np.radians(65), 100)
    r_samples = np.arange(min(H, W) * 0.15, max(H, W) * 0.85, 1.0, dtype=np.float32)

    limb_points = []
    for theta in dense_angles:
        xs = cx_init + r_samples * np.cos(theta)
        ys = cy_init + r_samples * np.sin(theta)
        valid = (xs >= 0) & (xs < W - 1) & (ys >= 0) & (ys < H - 1)
        if np.count_nonzero(valid) < 50:
            continue
        xs_val = xs[valid].astype(np.float32).reshape(-1, 1)
        ys_val = ys[valid].astype(np.float32).reshape(-1, 1)
        r_curr = r_samples[valid]

        vals = cv2.remap(lum_2d, xs_val, ys_val, cv2.INTER_LINEAR).flatten()
        grad = np.diff(vals)
        if grad.size == 0:
            continue
        min_idx = int(np.argmin(grad))
        if vals[min_idx] > 0.08 * p999 and grad[min_idx] < -0.0001:
            edge_x = cx_init + float(r_curr[min_idx]) * np.cos(theta)
            edge_y = cy_init + float(r_curr[min_idx]) * np.sin(theta)
            limb_points.append([edge_x, edge_y])

    if len(limb_points) < 15:
        return None

    pts = np.array(limb_points, dtype=np.float64)
    N = len(pts)

    best_inliers = []
    rng = np.random.default_rng(42)
    for _ in range(100):
        sample_idx = rng.choice(N, 3, replace=False)
        p = pts[sample_idx]
        A = np.column_stack([2.0 * p[:, 0], 2.0 * p[:, 1], np.ones(3)])
        b = p[:, 0]**2 + p[:, 1]**2
        try:
            c = np.linalg.solve(A, b)
            xc_cand, yc_cand = c[0], c[1]
            R_sq = c[2] + xc_cand**2 + yc_cand**2
            if R_sq <= 0:
                continue
            R_cand = np.sqrt(R_sq)
            dists = np.abs(np.sqrt((pts[:, 0] - xc_cand)**2 + (pts[:, 1] - yc_cand)**2) - R_cand)
            inliers = np.where(dists < 2.0)[0]
            if len(inliers) > len(best_inliers):
                best_inliers = inliers
        except np.linalg.LinAlgError:
            continue

    if len(best_inliers) < 15:
        inlier_pts = pts
    else:
        inlier_pts = pts[best_inliers]

    x = inlier_pts[:, 0]
    y = inlier_pts[:, 1]
    A = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x**2 + y**2
    c, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    xc, yc = float(c[0]), float(c[1])
    R_sq = float(c[2] + xc**2 + yc**2)
    if R_sq <= 0:
        return None
    R = float(np.sqrt(R_sq))

    dists = np.sqrt((x - xc)**2 + (y - yc)**2)
    res_std = float(np.std(dists - R))

    if not (0.15 * min(H, W) < R < 2.5 * max(H, W)) or res_std > 5.0:
        return None

    return xc, yc, R, res_std


def _suppress_lunar_limb_glare(
    planes: np.ndarray,
    glare_mode: str = "auto",
) -> tuple[np.ndarray, dict]:
    """Suppress atmospheric and optical forward scattering glare outside lunar physical limb.

    Operates strictly in 32-bit linear space before non-linear MTF stretch.
    Leaves lunar surface (r <= R + 1px) 100% unaltered.
    Smoothly transitions over a narrow delta (2.5px to 8.0px) into deep space zero.
    """
    if glare_mode == "off":
        return planes, {"active": False}

    is_3d = (planes.ndim == 3)
    if is_3d:
        lum_2d = 0.299 * planes[0] + 0.587 * planes[1] + 0.114 * planes[2]
    else:
        lum_2d = planes
    H, W = lum_2d.shape

    fit_res = _fit_lunar_limb_circle(lum_2d)
    if fit_res is None:
        return planes, {"active": False, "reason": "circle_fit_failed"}

    xc, yc, R, res_std = fit_res

    if glare_mode == "aggressive":
        delta = 2.5
    elif glare_mode == "mild":
        delta = 8.0
    else:  # "auto"
        delta = 4.5

    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - xc)**2 + (yy - yc)**2)

    r0 = R + 1.0
    t = np.clip((r_grid - r0) / delta, 0.0, 1.0)
    w_smooth = 1.0 - (3.0 * t**2 - 2.0 * t**3)

    weight = np.ones((H, W), dtype=np.float32)
    apply_mask = r_grid > r0
    weight[apply_mask] = w_smooth[apply_mask]
    outer_space_mask = r_grid > (r0 + delta)
    weight[outer_space_mask] = 0.0

    if is_3d:
        out_planes = planes * weight[np.newaxis, :, :]
    else:
        out_planes = planes * weight

    meta = {
        "active": True,
        "glare_mode": glare_mode,
        "center_x": xc,
        "center_y": yc,
        "radius": R,
        "residual_std": res_std,
        "delta": delta,
    }
    return out_planes.astype(np.float32), meta


def _calculate_channel_balance(
    d: np.ndarray,
    bg_r: float,
    bg_g: float,
    bg_b: float,
    mid_val: float = 0.13,
    wb_mode: str = "gray-world",
    glare_mode: str = "auto",
) -> dict:
    """Calculate channel white point stretch parameters with neutral Gray-World balance.

    In astronomical imaging, white balance must be performed linearly before non-linear MTF stretch.
    Applying independent non-linear MTF stretches with different white/black points causes non-linear
    color divergence, destroying color fidelity in shadows and generating purple fringing along
    high-contrast boundaries (the lunar limb).

    This function:
    1. Subtracts per-channel pedestals linearly (R_net, G_net, B_net);
    2. Suppresses diffuse optical and atmospheric lunar limb glare (Limb Glare Suppression);
    3. Identifies valid lunar surface pixels and computes relative Gray-World linear gains (k_R, k_B);
    4. Normalizes color channels so that average lunar albedo renders as true neutral gray;
    5. Suppresses optical chromatic aberration / Rayleigh blue flare (Defringe) along the limb;
    6. Derives a physical 32-bit Luminance channel and unified isometric stretch parameters.
    """
    import numpy as np

    p999_r = float(np.percentile(d[0], 99.95))
    p999_g = float(np.percentile(d[1], 99.95))
    p999_b = float(np.percentile(d[2], 99.95))

    hi_r_raw = max(p999_r * 1.10, bg_r + 0.01)
    hi_g_raw = max(p999_g * 1.10, bg_g + 0.01)
    hi_b_raw = max(p999_b * 1.10, bg_b + 0.01)

    lum_legacy = (0.299 * d[0] + 0.587 * d[1] + 0.114 * d[2]).astype(np.float32)
    bg_lum_legacy = _estimate_pedestal(lum_legacy)
    p999_lum_legacy = float(np.percentile(lum_legacy, 99.95))
    hi_lum_legacy = max(p999_lum_legacy * 1.10, bg_lum_legacy + 0.01)

    res = {
        "bg_r": bg_r, "hi_r": hi_r_raw,
        "bg_g": bg_g, "hi_g": hi_g_raw,
        "bg_b": bg_b, "hi_b": hi_b_raw,
        "bg_lum": bg_lum_legacy, "hi_lum": hi_lum_legacy,
        "lum_data": lum_legacy,
        "color_data": d,
        "mode": wb_mode,
        "ratio_r": 1.0,
        "ratio_b": 1.0,
        "k_r": 1.0,
        "k_b": 1.0,
        "moon_pixels": 0,
    }

    if wb_mode != "gray-world":
        return res

    # 1. Linear pedestal subtraction
    r_net = np.maximum(0.0, d[0] - bg_r)
    g_net = np.maximum(0.0, d[1] - bg_g)
    b_net = np.maximum(0.0, d[2] - bg_b)

    # 2. Lunar Limb Glare Suppression (physics-based radial falloff outside celestial limb)
    if glare_mode != "off":
        net_planes = np.stack([r_net, g_net, b_net], axis=0)
        net_planes, glare_meta = _suppress_lunar_limb_glare(net_planes, glare_mode=glare_mode)
        r_net, g_net, b_net = net_planes[0], net_planes[1], net_planes[2]
        res["glare_meta"] = glare_meta

    lum_approx = 0.299 * r_net + 0.587 * g_net + 0.114 * b_net
    p999_lum_raw = float(np.percentile(lum_approx, 99.95))
    if p999_lum_raw <= 1e-4:
        return res

    # Mask valid lunar surface: exclude dark background/shadows and overexposed peaks
    mask = (lum_approx > (0.05 * p999_lum_raw)) & (lum_approx < (0.95 * p999_lum_raw))
    valid_count = int(np.count_nonzero(mask))
    res["moon_pixels"] = valid_count

    if valid_count < 200:
        return res

    net_r = float(np.median(r_net[mask]))
    net_g = float(np.median(g_net[mask]))
    net_b = float(np.median(b_net[mask]))

    if net_g <= 1e-5 or net_r <= 1e-5 or net_b <= 1e-5:
        return res

    ratio_r = net_r / net_g
    ratio_b = net_b / net_g
    k_r = 1.0 / ratio_r
    k_b = 1.0 / ratio_b

    # 2. Linear channel balancing
    r_lin = r_net * k_r
    g_lin = g_net
    b_lin = b_net * k_b

    # 3. Edge Defringe (Optical Chromatic Aberration & Purple Fringe Suppression)
    # The physical lunar surface consists of basalt and anorthosite: titanium maria reach at most +15% blue excess.
    # High-contrast optical dispersion causes unphysical blue/violet flare (+100%~+380%) along the limb and crater shadows.
    # On lunar body: allow up to 1.15x for legitimate titanium maria.
    # In edge transition (lum_approx < 0.10 * p999): strictly cap Blue to max(Green, Red)
    # because highlands/limbs are non-titanium and edge optical dispersion flares Blue.
    max_b_body = np.maximum(g_lin, r_lin) * 1.15
    max_b_edge = np.maximum(g_lin, r_lin) * 1.00
    edge_zone = lum_approx < (0.10 * p999_lum_raw)
    max_allowed_b = np.where(edge_zone, max_b_edge, max_b_body)
    b_clean = np.minimum(b_lin, max_allowed_b)

    # 4. Calibrated Luminance and Color planes
    lum_clean = (0.299 * r_lin + 0.587 * g_lin + 0.114 * b_clean).astype(np.float32)
    color_clean = np.stack([r_lin, g_lin, b_clean], axis=0).astype(np.float32)

    p999_clean = float(np.percentile(lum_clean, 99.95))
    hi_unified = max(p999_clean * 1.10, 0.01)

    res.update({
        "bg_r": 0.0, "hi_r": hi_unified,
        "bg_g": 0.0, "hi_g": hi_unified,
        "bg_b": 0.0, "hi_b": hi_unified,
        "bg_lum": 0.0, "hi_lum": hi_unified,
        "lum_data": lum_clean,
        "color_data": color_clean,
        "ratio_r": ratio_r,
        "ratio_b": ratio_b,
        "k_r": k_r,
        "k_b": k_b,
    })
    return res


def _render_deep_cine_mineral(
    lum_sharp: np.ndarray,
    color_balanced: np.ndarray,
    circle_meta: dict | None = None,
    fe_boost: float = 6.8,
    ti_boost: float = 10.2,
    gamma: float = 1.09,
) -> np.ndarray:
    """Render high-end deep-tone geological mineral moon (Refined Deep-Cine Aesthetic).

    Features:
      1. Filmic S-Curve Tone Sculpting: Deep velvety basalt maria (V ~ 110-130) + radiant
         silver-white highlands (V ~ 180-220), eliminating perceptual color washout while
         retaining crisp dynamic range.
      2. Bilateral / Gaussian Low-pass Chroma Filtering: Eliminates Bayer sensor noise
         and subpixel atmospheric dispersion on high-contrast crater rims.
      3. Bipolar Pure Geological Saturation:
         - Fe (Iron-rich regolith / Mare Serenitatis): Pure Terracotta / Copper Peach
           (H ~ 11-12 in OpenCV hue, zero yellow-green mud).
         - Ti (Titanium-rich basalts / Mare Tranquillitatis): Pure Azure / Denim Blue
           (H ~ 106-107 in OpenCV hue, radiant and cerulean).
      4. Terminator Phase-Reddening Defense:
         - Distance transform from unlit night side smoothly zeroes saturation within
           terminator zone, eliminating artificial neon-orange crater rim glow.
      5. Dual Luma-guided Chroma Protection:
         - Shadow Rolloff: Craters along terminator transition to 100% pure neutral stone gray/carbon black.
         - Highlight Rolloff: Copernicus/Tycho ray systems and crater peaks remain crisp silver-white.
         - Limb Edge Zeroing: Suppresses color fringing at physical celestial boundary.
    """
    import cv2

    H, W = lum_sharp.shape
    lum_sharp = np.ascontiguousarray(lum_sharp, dtype=np.float32)
    r = np.ascontiguousarray(color_balanced[0], dtype=np.float32)
    g = np.ascontiguousarray(color_balanced[1], dtype=np.float32)
    b = np.ascontiguousarray(color_balanced[2], dtype=np.float32)

    lum_lin = 0.299 * r + 0.587 * g + 0.114 * b
    p999_lin = float(np.percentile(lum_lin[lum_lin > 0.0001], 99.95))

    mask_valid = lum_lin > (0.01 * p999_lin)
    cr_r = np.ones_like(r)
    cr_g = np.ones_like(g)
    cr_b = np.ones_like(b)

    cr_r[mask_valid] = r[mask_valid] / lum_lin[mask_valid]
    cr_g[mask_valid] = g[mask_valid] / lum_lin[mask_valid]
    cr_b[mask_valid] = b[mask_valid] / lum_lin[mask_valid]

    # Geological chrominance smoothing (filters Bayer noise and subpixel rim dispersion)
    sigma_color = max(11, int(round(min(H, W) * 0.016)))
    cr_r_f = cv2.GaussianBlur(cr_r, (0, 0), sigma_color)
    cr_g_f = cv2.GaussianBlur(cr_g, (0, 0), sigma_color)
    cr_b_f = cv2.GaussianBlur(cr_b, (0, 0), sigma_color)

    dr = cr_r_f - 1.0
    dg = cr_g_f - 1.0
    db = cr_b_f - 1.0

    # Celestial circle geometry
    if circle_meta and circle_meta.get("active"):
        cx = float(circle_meta["center_x"])
        cy = float(circle_meta["center_y"])
        R = float(circle_meta["radius"])
    else:
        fit = _fit_lunar_limb_circle(lum_sharp)
        if fit:
            cx, cy, R, _ = fit
        else:
            cx, cy, R = W / 2.0, H / 2.0, min(H, W) * 0.45

    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - cx)**2 + (yy - cy)**2)
    limb_dist = R - r_grid
    limb_mask = np.clip(limb_dist / 14.0, 0.0, 1.0)

    # 1. Terminator phase-reddening defense
    lit_mask = np.zeros((H, W), dtype=np.uint8)
    lit_mask[(r_grid <= R) & (lum_sharp >= 0.05)] = 1
    if np.count_nonzero(lit_mask) > 100:
        dist_term = cv2.distanceTransform(lit_mask, cv2.DIST_L2, 5)
        term_mask = np.clip((dist_term - 25.0) / 80.0, 0.0, 1.0)**1.5
    else:
        term_mask = 1.0

    # 2. Smooth luma masks (shadow proximity and highland ray control)
    sigma_luma = max(5, int(round(min(H, W) * 0.007)))
    lum_smooth = cv2.GaussianBlur(lum_sharp, (0, 0), sigma_luma)
    shadow_mask = np.clip((lum_smooth - 0.10) / 0.16, 0.0, 1.0)**1.4
    hi_mask = 1.0 - np.clip((lum_smooth - 0.62) / 0.22, 0.0, 1.0)**1.4

    color_weight = shadow_mask * hi_mask * limb_mask * term_mask

    # 3. Pure Bipolar Mineral Color Synthesis (Azure Blue & Terracotta Peach)
    delta_rb = dr - db
    dr_b = np.zeros_like(dr)
    dg_b = np.zeros_like(dg)
    db_b = np.zeros_like(db)

    # Ti-rich Basalt: Azure / Denim Blue (OpenCV H ~ 106-107)
    ti_m = (delta_rb < -0.001) & (db > 0.001)
    mag_ti = np.maximum(db[ti_m], -delta_rb[ti_m]) * ti_boost * color_weight[ti_m]
    db_b[ti_m] = mag_ti
    dr_b[ti_m] = -0.32 * mag_ti
    dg_b[ti_m] = +0.38 * mag_ti

    # Fe-rich Regolith: Pure Terracotta / Copper Peach (OpenCV H ~ 11-12)
    fe_m = (delta_rb > 0.001) & (dr > 0.001)
    mag_fe = np.maximum(dr[fe_m], delta_rb[fe_m]) * fe_boost * color_weight[fe_m]
    dr_b[fe_m] = mag_fe
    db_b[fe_m] = -0.35 * mag_fe
    dg_b[fe_m] = +0.18 * mag_fe

    cr_r_new = np.clip(1.0 + dr_b, 0.1, 4.0)
    cr_g_new = np.clip(1.0 + dg_b, 0.1, 4.0)
    cr_b_new = np.clip(1.0 + db_b, 0.1, 4.0)

    # 4. Filmic S-curve tone sculpting: deep velvety maria + radiant silver highlands
    lum_safe = np.nan_to_num(np.clip(lum_sharp, 0.0, 1.0), nan=0.0)
    blend = np.clip((lum_safe - 0.35) / 0.45, 0.0, 1.0)
    blend = blend * blend * (3.0 - 2.0 * blend)
    g_dark = max(1.0, gamma * 1.06)
    g_bright = max(0.95, gamma * 0.88)
    lum_cine = (1.0 - blend) * np.power(lum_safe, g_dark) + blend * np.power(lum_safe, g_bright)
    lum_cine = np.nan_to_num(np.clip(lum_cine, 0.0, 1.0), nan=0.0)

    final_r = np.nan_to_num(np.clip(lum_cine * cr_r_new, 0.0, 1.0), nan=0.0)
    final_g = np.nan_to_num(np.clip(lum_cine * cr_g_new, 0.0, 1.0), nan=0.0)
    final_b = np.nan_to_num(np.clip(lum_cine * cr_b_new, 0.0, 1.0), nan=0.0)

    # Outer space clean zeroing
    final_r[r_grid > R + 4.5] = 0
    final_g[r_grid > R + 4.5] = 0
    final_b[r_grid > R + 4.5] = 0

    # Flip vertically to match image row 0 at top convention
    rgb_fits = np.stack([final_r, final_g, final_b], axis=-1)
    rgb_jpg = rgb_fits[::-1, :, :]
    bgr_jpg = cv2.cvtColor((rgb_jpg * 255.0).astype(np.uint8), cv2.COLOR_RGB2BGR)
    return bgr_jpg


def _estimate_adaptive_sharpening(
    lum_data: np.ndarray,
    has_deconv: bool = True,
    interp_used: str = "cu",
    sharp_mode: str = "auto",
    user_wavelet_l1: float | None = None,
    user_clahe_clip: float | None = None,
    user_unsharp: float | None = None,
) -> dict:
    """Calculate organic, physically-adaptive lunar wavelet and contrast parameters.

    Prevents over-sharpening (chalky crater rims, crunchy texture, noisy flat maria)
    by jointly analyzing:
      1. Lunar dynamic contrast ratio (p90 - p10) / p10
      2. Residual high-frequency noise floor (sigma_noise via Donoho MAD in flat maria)
      3. Deconvolution status (applies MTF discount factor if Airy PSF deconvolution was run)
      4. Interpolation filter properties (Bicubic vs Bilinear)
      5. Anti-redundancy defense: automatically bypasses USM unsharp mask when
         multi-scale wavelets or deconvolution are active, eliminating artificial halos.
    """
    lum_2d = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum_2d, 99.95))
    moon_mask = (lum_2d > 0.05 * p999) & (lum_2d < 0.95 * p999)
    valid_pixels = lum_2d[moon_mask]

    if valid_pixels.size < 500:
        contrast_ratio = 3.5
        sigma_noise = 0.0005
    else:
        p10 = float(np.percentile(valid_pixels, 10))
        p90 = float(np.percentile(valid_pixels, 90))
        contrast_ratio = float((p90 - p10) / max(p10, 1e-4))

        diff_h = np.abs(np.diff(lum_2d, axis=1)[:-1, :])
        diff_v = np.abs(np.diff(lum_2d, axis=0)[:, :-1])
        std_local = diff_h + diff_v
        sub_mask = moon_mask[:-1, :-1]
        if np.count_nonzero(sub_mask) > 500:
            flat_thresh = np.percentile(std_local[sub_mask], 20)
            flat_mask = sub_mask & (std_local <= flat_thresh)
            sigma_noise = float(np.median(std_local[flat_mask]) / 1.4826)
        else:
            sigma_noise = float(np.std(lum_2d[~moon_mask])) if np.count_nonzero(~moon_mask) > 100 else 0.001

    if sharp_mode == "none":
        w_coeffs = [1.00, 1.00, 1.00, 1.00, 1.00, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mild":
        w_coeffs = [1.02, 1.08, 1.10, 1.05, 1.00, 1.00]
        clahe_clip = 0.35
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "crisp":
        w_coeffs = [1.05, 1.16, 1.20, 1.12, 1.00, 1.00]
        clahe_clip = 0.60
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    else:  # "auto"
        deconv_discount = 0.55 if has_deconv else 1.0
        noise_penalty = float(np.clip(1.0 - (sigma_noise / max(p999 * 0.005, 1e-6)), 0.4, 1.0))
        l1_base = 0.04 if interp_used == "cu" else 0.08
        w1 = 1.0 + l1_base * deconv_discount * noise_penalty
        w2 = 1.0 + 0.16 * deconv_discount * noise_penalty
        w3 = 1.0 + 0.20 * deconv_discount * noise_penalty
        w4 = 1.0 + 0.12 * deconv_discount * noise_penalty
        w_coeffs = [round(w1, 2), round(w2, 2), round(w3, 2), round(w4, 2), 1.00, 1.00]
        clahe_clip = round(float(np.clip(1.5 / max(contrast_ratio, 1.0), 0.25, 0.70)), 2)
        unsharp_amt = 0.0

    # User manual overrides
    if user_wavelet_l1 is not None:
        w_coeffs[0] = round(float(user_wavelet_l1), 2)
    if user_clahe_clip is not None:
        clahe_clip = round(float(user_clahe_clip), 2)
    if user_unsharp is not None:
        unsharp_amt = round(float(user_unsharp), 2)

    wrecons_cmd = f"wrecons {w_coeffs[0]:.2f} {w_coeffs[1]:.2f} {w_coeffs[2]:.2f} {w_coeffs[3]:.2f} {w_coeffs[4]:.2f} {w_coeffs[5]:.2f}"
    clahe_lines = [f"clahe {clahe_clip:.2f} 32"] if clahe_clip > 0 else []
    unsharp_lines = [f"unsharp 1.0 {unsharp_amt:.2f}"] if unsharp_amt > 0 else []

    return {
        "sharp_mode": sharp_mode,
        "wrecons_cmd": wrecons_cmd,
        "wavelet_coeffs": w_coeffs,
        "clahe_lines": clahe_lines,
        "clahe_clip": clahe_clip,
        "unsharp_lines": unsharp_lines,
        "unsharp_amount": unsharp_amt,
        "contrast_ratio": contrast_ratio,
        "sigma_noise": sigma_noise,
        "noise_penalty": noise_penalty if sharp_mode == "auto" else 1.0,
        "deconv_discount": deconv_discount if sharp_mode == "auto" else 1.0,
    }


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

    # Detect interpolation method from stack receipt for interp-aware wavelet tuning
    interp_used = "cu"
    stack_receipt_file = work / "stack_receipt.json"
    if stack_receipt_file.exists():
        try:
            sr = load_json(stack_receipt_file)
            interp_used = sr.get("interp", "cu")
        except Exception:
            pass

    mineral_mode = getattr(args, "mineral_mode", "lrgb")
    wb_mode = getattr(args, "white_balance", "gray-world")
    is_rgb = (d.ndim == 3 and d.shape[0] >= 3)
    mid_val = getattr(args, "midtone", 0.13)

    sat_base = float(getattr(args, "sat_base", 0.3))
    sat_fe = float(getattr(args, "sat_fe", 0.8))
    sat_ti = float(getattr(args, "sat_ti", 0.8))
    sat_bg = float(getattr(args, "sat_bg_factor", 1.2))

    sat_lines = []
    if sat_base > 0:
        sat_lines.append(f"satu {sat_base:.2f} {sat_bg:.1f} 6")  # base foundation across all hues
    if sat_fe > 0:
        sat_lines.append(f"satu {sat_fe:.2f} {sat_bg:.1f} 1")    # targeted orange-yellow (Fe-rich basalt/highlands)
    if sat_ti > 0:
        sat_lines.append(f"satu {sat_ti:.2f} {sat_bg:.1f} 3")    # targeted cyan (Ti-rich boundary)
        sat_lines.append(f"satu {sat_ti:.2f} {sat_bg:.1f} 4")    # targeted cyan-magenta (Mare Tranquillitatis core Ti)
    if not sat_lines:
        sat_lines = [f"satu 0.70 {sat_bg:.1f} 6", f"satu 0.40 1.0 6"]

    glare_mode = getattr(args, "glare_suppress", "auto")

    if is_rgb:
        bg_r = _estimate_pedestal(d[0])
        bg_g = _estimate_pedestal(d[1])
        bg_b = _estimate_pedestal(d[2])
        wb = _calculate_channel_balance(d, bg_r, bg_g, bg_b, mid_val=mid_val, wb_mode=wb_mode, glare_mode=glare_mode)
        if "glare_meta" in wb and wb["glare_meta"].get("active"):
            gm = wb["glare_meta"]
            log(f"lunar limb glare suppression active: center=({gm['center_x']:.1f}, {gm['center_y']:.1f}), R={gm['radius']:.1f}px (res_std={gm['residual_std']:.2f}px, falloff={gm['delta']:.1f}px)")
        if wb["moon_pixels"] >= 200 and wb_mode == "gray-world":
            log(f"neutral Gray-World balance applied: R/G={wb['ratio_r']:.3f}, B/G={wb['ratio_b']:.3f} ({wb['moon_pixels']} lunar surface pixels sampled)")
        log(f"calibrated channels (bg=[{wb['bg_r']:.5f}, {wb['bg_g']:.5f}, {wb['bg_b']:.5f}], hi=[{wb['hi_r']:.5f}, {wb['hi_g']:.5f}, {wb['hi_b']:.5f}], midtone={mid_val})")
        lum_for_sharp = wb["lum_data"]
    else:
        plane = d if d.ndim == 2 else d[0]
        lum_for_sharp = plane

    # Dynamic Organic Sharpening Parameter Generation
    has_deconv = (deconv_method in ("sb", "wiener", "rl"))
    sharp_mode = getattr(args, "sharp_mode", "auto")
    user_wavelet_l1 = getattr(args, "wavelet_l1", None)
    user_clahe_clip = getattr(args, "clahe_clip", None)
    user_unsharp = getattr(args, "unsharp", None)

    sharp_info = _estimate_adaptive_sharpening(
        lum_for_sharp,
        has_deconv=has_deconv,
        interp_used=interp_used,
        sharp_mode=sharp_mode,
        user_wavelet_l1=user_wavelet_l1,
        user_clahe_clip=user_clahe_clip,
        user_unsharp=user_unsharp,
    )
    wrecons_cmd = sharp_info["wrecons_cmd"]
    clahe_lines = sharp_info["clahe_lines"]
    unsharp_lines = sharp_info["unsharp_lines"]

    log(f"adaptive organic sharpening: mode='{sharp_mode}', contrast_ratio={sharp_info['contrast_ratio']:.2f}, mare_noise={sharp_info['sigma_noise']:.6f}")
    log(f"  - wavelet: {wrecons_cmd} (deconv discount={sharp_info['deconv_discount']:.2f}x, noise penalty={sharp_info['noise_penalty']:.2f})")
    if clahe_lines:
        log(f"  - CLAHE: {sharp_info['clahe_clip']:.2f} clip (dynamic contrast-aware)")
    else:
        log("  - CLAHE: bypassed")
    if unsharp_lines:
        log(f"  - USM unsharp: amount={sharp_info['unsharp_amount']:.2f}")
    else:
        log("  - USM unsharp: bypassed (anti-redundancy protection)")

    if is_rgb:
        if mineral_mode == "lrgb":
            log("executing professional L/RGB separation pipeline (Luminance detail deconv/wavelets + Chrominance saturation)...")
            lum_path = work / "moon_lum.fit"
            fits.writeto(lum_path, wb["lum_data"], header=hdr, overwrite=True)
            log(f"calibrated Luminance (bg_lum={wb['bg_lum']:.5f}, hi_lum={wb['hi_lum']:.5f})")

            color_target = master.stem
            if wb_mode == "gray-world" and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                # --- Stage 1: Luminance High-Frequency Processing ---
                "load moon_lum",
                *deconv_lines,
                f"mtf {wb['bg_lum']:.6f} {mid_val:.2f} {wb['hi_lum']:.6f}",
                "wavelet 5 2",
                wrecons_cmd,
                *clahe_lines,
                *unsharp_lines,
                "save moon_lum_sharp",
                # --- Stage 2: Chrominance Balancing & Saturation ---
                f"load {color_target}",
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "rmgreen 0",
                "save moon_color_clean",
                *sat_lines,
                "save moon_color_sat",
                # --- Stage 3: LRGB Composite ---
                # 1. Natural LRGB Version (sharp lum + neutral balanced color)
                "rgbcomp -lum=moon_lum_sharp moon_color_clean -out=moon_natural_master",
                "load moon_natural_master",
                "savetif moon_natural -astro",
                "savejpg moon_natural 95",
                # 2. Mineral LRGB Version (sharp lum + boosted mineral color)
                "rgbcomp -lum=moon_lum_sharp moon_color_sat -out=moon_mineral_master",
                "load moon_mineral_master",
                "savejpg moon_mineral 95",
                "exit",
            ]
        else:
            # Legacy monolithic RGB pipeline
            log("executing legacy monolithic RGB wavelet pipeline...")
            color_target = master.stem
            if wb_mode == "gray-world" and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                f"load {color_target}",
                *deconv_lines,
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "rmgreen 0",
                "wavelet 5 2",
                wrecons_cmd,
                *clahe_lines,
                *unsharp_lines,
                "savetif moon_natural -astro",
                "savejpg moon_natural 95",
                *sat_lines,
                "savejpg moon_mineral 95",
                "exit",
            ]

    else:
        # Monochrome pipeline
        log("executing monochrome lunar detail pipeline...")
        plane = d if d.ndim == 2 else d[0]
        bg_val = _estimate_pedestal(plane)
        p999_val = float(np.percentile(plane, 99.95))
        hi_val = max(p999_val * 1.10, bg_val + 0.01)

        lines = [
            "requires 1.4.4",
            f"load {master.name}",
            *deconv_lines,
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
            "wavelet 5 2",
            wrecons_cmd,
            *clahe_lines,
            *unsharp_lines,
            "savetif moon_natural -astro",
            "savejpg moon_natural 95",
            "exit",
        ]

    receipt = run_siril_script(args.siril, lines, work, logs_dir / "03_postprocess.log", args.timeout)
    if receipt["exit_code"] != 0:
        die(f"postprocessing failed (exit {receipt['exit_code']}); see {receipt['log']}")

    mineral_style = getattr(args, "mineral_style", "deep-cine")
    if is_rgb and mineral_mode == "lrgb" and mineral_style == "deep-cine":
        lum_sharp_path = work / "moon_lum_sharp.fit"
        color_bal_path = work / "moon_color_balanced.fit"
        if lum_sharp_path.exists() and color_bal_path.exists():
            with fits.open(lum_sharp_path, memmap=False) as hdul_l:
                l_data = hdul_l[0].data
            with fits.open(color_bal_path, memmap=False) as hdul_c:
                c_data = hdul_c[0].data

            c_meta = wb.get("glare_meta")
            fe_boost = float(getattr(args, "mineral_fe_boost", 6.8))
            ti_boost = float(getattr(args, "mineral_ti_boost", 10.2))
            gamma = float(getattr(args, "mineral_gamma", 1.09))

            import cv2
            import shutil

            deep_mineral_bgr = _render_deep_cine_mineral(
                l_data,
                c_data,
                circle_meta=c_meta,
                fe_boost=fe_boost,
                ti_boost=ti_boost,
                gamma=gamma,
            )

            # Backup default Siril mineral output as moon_mineral_natural.jpg
            legacy_mineral_path = work / "moon_mineral.jpg"
            if legacy_mineral_path.exists():
                shutil.copy2(legacy_mineral_path, work / "moon_mineral_natural.jpg")

            # Write deep-cine mineral JPG
            cv2.imwrite(str(work / "moon_mineral.jpg"), deep_mineral_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            log(f"deep-cine mineral moon rendered: Fe terracotta red (x{fe_boost:.1f}) + Ti cobalt blue (x{ti_boost:.1f}), bilateral chroma smoothing, shadow/ray rolloff")

    dump_json(work / "postprocess_receipt.json", receipt)
    log(f"postprocessing complete! Products in {work}:")
    log(f"  - Natural master TIFF: {work / 'moon_natural.tif'}")
    log(f"  - Natural JPG:         {work / 'moon_natural.jpg'}")
    if (work / "moon_mineral.jpg").exists():
        log(f"  - Mineral Moon JPG:    {work / 'moon_mineral.jpg'}")
        if (work / "moon_mineral_natural.jpg").exists():
            log(f"  - Natural Mineral JPG: {work / 'moon_mineral_natural.jpg'}")


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
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "utility", "mtf-snr", "relative", "percent"],
                   help="Frame selection mode: 'otsu' (adaptive bimodal seeing clustering, default), 'utility'/'mtf-snr' (MTF-SNR joint utility optimization), 'relative' (relative to reference frame), 'percent' (fixed/tiered percentage)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Minimum sharpness relative to reference frame (0.0 - 1.0) for 'relative' mode (default: 0.75)")
    a.add_argument("--utility-alpha", type=float, default=2.0, help="MTF/contrast weight exponent for 'utility' mode (default: 2.0)")
    a.add_argument("--utility-beta", type=float, default=1.0, help="SNR weight exponent for 'utility' mode (default: 1.0)")
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
    a.add_argument("--wavelet-l1", type=float, default=None, help="Layer 1 wavelet gain (default: auto adaptive)")
    a.add_argument("--clahe-clip", type=float, default=None, help="CLAHE clip limit (default: auto dynamic, set <=0 to bypass CLAHE)")
    a.add_argument("--sharp-mode", default="auto", choices=["auto", "mild", "crisp", "none"],
                   help="Sharpening mode: 'auto' (physics-adaptive contrast/noise-aware, default), 'mild' (soft natural), 'crisp' (classic), 'none' (bypass)")
    a.add_argument("--unsharp", type=float, default=None, help="USM unsharp mask amount (default: auto bypassed when wavelets/deconv active)")
    a.add_argument("--aperture", type=float, default=80.0, help="Telescope aperture in mm (for Airy PSF)")
    a.add_argument("--focal", type=float, default=400.0, help="Telescope focal length in mm (for Airy PSF)")
    a.add_argument("--pixel-size", type=float, default=3.73, help="Sensor pixel size in microns (for Airy PSF)")
    a.add_argument("--mineral-mode", default="lrgb", choices=["lrgb", "legacy"],
                   help="Mineral moon processing pipeline: 'lrgb' (Luminance/Chrominance separation, clean details, default) or 'legacy' (monolithic RGB wavelet)")
    a.add_argument("--white-balance", default="gray-world", choices=["gray-world", "legacy"],
                   help="Color balance mode: 'gray-world' (neutral lunar albedo baseline, default) or 'legacy' (per-channel percentile stretch)")
    a.add_argument("--sat-fe", type=float, default=0.8, help="Mineral saturation boost for Fe-rich terrain (orange-yellow hue 1, default: 0.8)")
    a.add_argument("--sat-ti", type=float, default=0.8, help="Mineral saturation boost for Ti-rich basalt (cyan-blue hues 3 & 4, default: 0.8)")
    a.add_argument("--sat-base", type=float, default=0.3, help="Foundation base saturation boost across all hues (default: 0.3)")
    a.add_argument("--sat-bg-factor", type=float, default=1.2, help="Background noise saturation suppression threshold factor (default: 1.2)")
    a.add_argument("--glare-suppress", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Lunar limb forward scattering glare suppression mode: 'auto' (4.5px falloff, default), 'mild' (8.0px), 'aggressive' (2.5px), 'off' (bypass)")
    a.add_argument("--mineral-style", default="deep-cine", choices=["deep-cine", "natural"],
                   help="Mineral moon aesthetic style: 'deep-cine' (deep matte basalt tone, bilateral chroma smoothing, terracotta/cobalt pure boost, shadow/ray rolloff, default) or 'natural' (classic subtle saturation)")
    a.add_argument("--mineral-fe-boost", type=float, default=6.8, help="Deep-cine saturation boost for Fe-rich terrain (terracotta peach, default: 6.8)")
    a.add_argument("--mineral-ti-boost", type=float, default=10.2, help="Deep-cine saturation boost for Ti-rich basalt (azure denim blue, default: 10.2)")
    a.add_argument("--mineral-gamma", type=float, default=1.09, help="Deep-cine filmic luminance sculpting gamma (default: 1.09)")
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
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "utility", "mtf-snr", "relative", "percent"],
                   help="Frame selection mode: 'otsu' (adaptive bimodal seeing clustering, default), 'utility'/'mtf-snr' (MTF-SNR joint utility optimization), 'relative' (relative to reference frame), 'percent' (fixed/tiered percentage)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Minimum sharpness relative to reference frame (0.0 - 1.0) for 'relative' mode (default: 0.75)")
    a.add_argument("--utility-alpha", type=float, default=2.0, help="MTF/contrast weight exponent for 'utility' mode (default: 2.0)")
    a.add_argument("--utility-beta", type=float, default=1.0, help="SNR weight exponent for 'utility' mode (default: 1.0)")
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
    a.add_argument("--wavelet-l1", type=float, default=None, help="Layer 1 wavelet gain (default: auto adaptive)")
    a.add_argument("--clahe-clip", type=float, default=None, help="CLAHE clip limit (default: auto dynamic, set <=0 to bypass CLAHE)")
    a.add_argument("--sharp-mode", default="auto", choices=["auto", "mild", "crisp", "none"],
                   help="Sharpening mode: 'auto' (physics-adaptive contrast/noise-aware, default), 'mild' (soft natural), 'crisp' (classic), 'none' (bypass)")
    a.add_argument("--unsharp", type=float, default=None, help="USM unsharp mask amount (default: auto bypassed when wavelets/deconv active)")
    a.add_argument("--aperture", type=float, default=80.0)
    a.add_argument("--focal", type=float, default=400.0)
    a.add_argument("--pixel-size", type=float, default=3.73)
    a.add_argument("--mineral-mode", default="lrgb", choices=["lrgb", "legacy"],
                   help="Mineral moon processing pipeline: 'lrgb' (Luminance/Chrominance separation, default) or 'legacy' (monolithic RGB)")
    a.add_argument("--white-balance", default="gray-world", choices=["gray-world", "legacy"],
                   help="Color balance mode: 'gray-world' (neutral lunar albedo baseline, default) or 'legacy' (per-channel percentile stretch)")
    a.add_argument("--sat-fe", type=float, default=0.8, help="Mineral saturation boost for Fe-rich terrain (orange-yellow hue 1, default: 0.8)")
    a.add_argument("--sat-ti", type=float, default=0.8, help="Mineral saturation boost for Ti-rich basalt (cyan-blue hues 3 & 4, default: 0.8)")
    a.add_argument("--sat-base", type=float, default=0.3, help="Foundation base saturation boost across all hues (default: 0.3)")
    a.add_argument("--sat-bg-factor", type=float, default=1.2, help="Background noise saturation suppression threshold factor (default: 1.2)")
    a.add_argument("--glare-suppress", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Lunar limb forward scattering glare suppression mode: 'auto' (4.5px falloff, default), 'mild' (8.0px), 'aggressive' (2.5px), 'off' (bypass)")
    a.add_argument("--mineral-style", default="deep-cine", choices=["deep-cine", "natural"],
                   help="Mineral moon aesthetic style: 'deep-cine' (deep matte basalt tone, bilateral chroma smoothing, terracotta/cobalt pure boost, shadow/ray rolloff, default) or 'natural' (classic subtle saturation)")
    a.add_argument("--mineral-fe-boost", type=float, default=6.8, help="Deep-cine saturation boost for Fe-rich terrain (terracotta peach, default: 6.8)")
    a.add_argument("--mineral-ti-boost", type=float, default=10.2, help="Deep-cine saturation boost for Ti-rich basalt (azure denim blue, default: 10.2)")
    a.add_argument("--mineral-gamma", type=float, default=1.09, help="Deep-cine filmic luminance sculpting gamma (default: 1.09)")
    a.set_defaults(func=cmd_all)

    return p


if __name__ == "__main__":
    ns = build_parser().parse_args()
    ns.func(ns)
