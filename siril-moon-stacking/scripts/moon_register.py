#!/usr/bin/env python3
"""Lunar surface ROI localization, FFT phase correlation, and registration matrix injection."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from moon_common import (
    _frame_identity,
    die,
    dump_json,
    load_json,
    log,
)


def _locate_high_contrast_roi(plane, roi_size: int = 1024, downsample: int = 4) -> tuple[int, int, int, int]:
    """Locate the primary lunar terrain region with maximum texture gradient."""
    import cv2

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


def _locate_dual_anchor_rois(
    plane, roi_size: int = 512, downsample: int = 4
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]:
    """Locate two distinct, well-separated high-contrast lunar terrain ROIs for rigid rotation estimation."""
    import cv2

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
    """Format Siril R0 homography matrix for rigid rotation (theta in rad) + translation (dx, dy)."""
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
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
        if data.ndim == 3:
            plane = data[1] if data.shape[0] == 3 else data[0]
        else:
            plane = data
        return np.asarray(plane, dtype=np.float32)


def _subpixel_phase_correlation(ref, img, upsample_factor: int = 20) -> tuple[float, float, float]:
    """Compute subpixel displacement (dx, dy) and confidence response (1 - error)."""
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
        dx = float(-shift[1])
        dy = float(-shift[0])
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

    f = np.asarray(patch, dtype=np.float32)
    return float(cv2.Laplacian(f, cv2.CV_32F).var())


def align_rgb_channels(master_path: Path, patch_size: int = 512) -> dict:
    """Subpixel Atmospheric Dispersion Correction (ADC) for 3-channel lunar masters."""
    import cv2
    from astropy.io import fits

    with fits.open(master_path, mode="update", memmap=False) as hdul:
        data = hdul[0].data
        if data.ndim != 3 or data.shape[0] < 3:
            return {"applied": False, "reason": "not 3-channel color data"}

        c, h, w = data.shape
        g_full = np.ascontiguousarray(data[1], dtype=np.float32)
        r_native = np.ascontiguousarray(data[0], dtype=np.float32)
        b_native = np.ascontiguousarray(data[2], dtype=np.float32)

        grad_g = cv2.magnitude(cv2.Sobel(g_full, cv2.CV_32F, 1, 0), cv2.Sobel(g_full, cv2.CV_32F, 0, 1))
        grad_r = cv2.magnitude(cv2.Sobel(r_native, cv2.CV_32F, 1, 0), cv2.Sobel(r_native, cv2.CV_32F, 0, 1))
        grad_b = cv2.magnitude(cv2.Sobel(b_native, cv2.CV_32F, 1, 0), cv2.Sobel(b_native, cv2.CV_32F, 0, 1))

        rx0, ry0, rx1, ry1 = _locate_high_contrast_roi(grad_g, roi_size=patch_size)

        g_patch = grad_g[ry0:ry1, rx0:rx1]
        r_patch = grad_r[ry0:ry1, rx0:rx1]
        b_patch = grad_b[ry0:ry1, rx0:rx1]

        ph, pw = g_patch.shape
        win = cv2.createHanningWindow((pw, ph), cv2.CV_32F)

        (dx_r, dy_r), resp_r = cv2.phaseCorrelate(g_patch - g_patch.mean(), r_patch - r_patch.mean(), win)
        (dx_b, dy_b), resp_b = cv2.phaseCorrelate(g_patch - g_patch.mean(), b_patch - b_patch.mean(), win)

        shift_r = (-float(dx_r), -float(dy_r))
        shift_b = (-float(dx_b), -float(dy_b))

        applied = False

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
    """Intelligent frame selection based on reference frame quality or user criteria."""
    if not valid_items:
        return set(), {"mode": select_mode, "kept_count": 0, "kept_percent": 0.0}

    valid_items.sort(key=lambda it: it["sharpness"], reverse=True)
    ref_sharpness = float(valid_items[0]["sharpness"])
    sharpnesses = np.array([it["sharpness"] for it in valid_items], dtype=np.float64)

    if select_mode in ("mtf-snr", "mtf_snr"):
        select_mode = "utility"

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
            spread_rel = float((sharpnesses.max() - sharpnesses.min()) / max(ref_sharpness, 1e-6))
            if len(valid_items) >= 5 and spread_rel < 0.06:
                target_k = min(len(valid_items), max(3, int(round(total_count * 0.75))))
                otsu_kept = valid_items[:target_k]
                meta["threshold"] = float(valid_items[target_k - 1]["sharpness"])
                meta["threshold_rel"] = round(meta["threshold"] / ref_sharpness, 4)
                meta["reason"] = f"ultra-calm seeing window detected (spread={spread_rel:.3f} < 0.06), expanded to 75% frames for optimal SNR"
                kept_indices = {it["index"] for it in otsu_kept}
            else:
                from skimage.filters import threshold_otsu

                th = float(threshold_otsu(sharpnesses))
                meta["threshold"] = th
                meta["threshold_rel"] = round(th / ref_sharpness, 4)
                otsu_kept = [it for it in valid_items if it["sharpness"] >= th]
                min_k = min(len(valid_items), max(3, int(round(total_count * 0.10))))
                if len(otsu_kept) < min_k:
                    otsu_kept = valid_items[:min_k]
                    meta["safety_clamped"] = "min_k"
                kept_indices = {it["index"] for it in otsu_kept}

    elif select_mode == "utility":
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


def _siril_homographies(matrices: list[str]) -> tuple[list[str], int]:
    """Translate the common output origin to avoid Siril 1.4.4 zero-sum rejection."""
    values = [list(map(float, matrix.split()[1:])) for matrix in matrices]
    sums = [sum(value) for value in values]
    if all(abs(total) > 1e-8 for total in sums):
        return matrices, 0
    offset = 1
    while any(abs(total + offset) <= 1e-8 for total in sums):
        offset += 1
    for value in values:
        value[2] += offset
    return ["H " + " ".join(f"{v:.8f}" for v in value) for value in values], offset


def cmd_register(args) -> None:
    work = Path(args.work).expanduser().resolve()
    seq_path = work / f"{args.seq}.seq"
    if not seq_path.exists():
        die(f"sequence file not found: {seq_path}; run import first")

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

    imported = load_json(work / "import_receipt.json") if (work / "import_receipt.json").exists() else {}
    video = imported.get("video_metadata", {})
    signature = {
        "processing": video.get("processing_signature"),
        "scores": video.get("score_identity"),
        "roi": args.roi,
        "min_confidence": args.min_confidence,
    }
    cache_path = work / "registration_cache.json"
    previous_path = cache_path if cache_path.exists() else work / "ranking.json"
    previous = load_json(previous_path) if previous_path.exists() else {}
    cached = {}
    if video.get("processing_signature") and previous.get("registration_signature") == signature:
        cached = {r["index"]: r for r in previous.get("frames", [])}
        ref = int(previous.get("reference_index") or 0)
        ref_file = work / f"{seq_name}{ref:05d}.fit"
        if not ref_file.exists() or cached.get(ref, {}).get("identity") != _frame_identity(ref_file):
            cached = {}
    included = {int(line.split()[1]) for line in seq_lines if line.startswith("I ") and line.split()[2] == "1"}
    primary = bool(video.get("primary_selected")) and getattr(args, "register_selection", "auto") == "auto"
    source_mapping = video.get("source_mapping", {})
    quality = {}
    score_path = work / "video_scores.json"
    if video and score_path.exists():
        profile = load_json(score_path)
        if profile.get("identity") == video.get("score_identity"):
            quality = {r["index"]: r["sharpness"] for r in profile["rows"]}
    for item in image_files:
        item["identity"] = _frame_identity(item["path"])
        item["source_index"] = source_mapping.get(str(item["index"]))
        item["cached"] = cached.get(item["index"], {})
        if item["cached"].get("identity") != item["identity"]:
            item["cached"] = {}

    previous_reference = previous.get("reference_index") if cached else None
    mid_frame = next((it for it in image_files if it["index"] == previous_reference), image_files[len(image_files) // 2])
    mid_plane = _read_frame_plane(mid_frame["path"])
    roi = _locate_high_contrast_roi(mid_plane, roi_size=args.roi)
    rx0, ry0, rx1, ry1 = roi
    log(f"localized high-contrast lunar feature ROI: [{rx0}:{rx1}, {ry0}:{ry1}] ({rx1-rx0}x{ry1-ry0})")

    for item in image_files:
        saved = item["cached"]
        if saved:
            item.update(coarse_dx=saved["shift"][0], coarse_dy=saved["shift"][1], resp=saved["response"], sharpness=saved["sharpness"])
            continue
        plane = _read_frame_plane(item["path"])
        dx, dy, resp = _subpixel_phase_correlation(mid_plane, plane)
        item["coarse_dx"] = dx
        item["coarse_dy"] = dy
        item["resp"] = resp

        rw = rx1 - rx0
        rh = ry1 - ry0
        sx0 = int(round(rx0 + dx))
        sy0 = int(round(ry0 + dy))
        sx0 = max(0, min(sx0, plane.shape[1] - rw))
        sy0 = max(0, min(sy0, plane.shape[0] - rh))
        sx1 = sx0 + rw
        sy1 = sy0 + rh
        patch = plane[sy0:sy1, sx0:sx1]
        sharpness = _measure_sharpness(patch)
        item["sharpness"] = quality.get(item["source_index"], sharpness)

    valid_items = [it for it in image_files if it["index"] in included and it["resp"] >= args.min_confidence and it["sharpness"] > 0]
    if not valid_items:
        die("no frames met the correlation confidence threshold; check data quality or lower --min-confidence")

    best_item = next((it for it in image_files if it["index"] == previous_reference), max(valid_items, key=lambda it: it["sharpness"]))
    ref_idx = best_item["index"]
    ref_dx = 0.0 if cached else best_item["coarse_dx"]
    ref_dy = 0.0 if cached else best_item["coarse_dy"]
    log(f"selected reference frame: #{ref_idx} (sharpness={best_item['sharpness']:.1f}, resp={best_item['resp']:.2f})")

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
        saved = it["cached"]
        if saved:
            it.update(dx=saved["shift"][0], dy=saved["shift"][1], theta=math.radians(saved["rotation_deg"]), homography=saved["homography"])
            continue
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
                    if abs(d_theta) < 0.1:
                        theta = d_theta

        it["dx"] = total_dx
        it["dy"] = total_dy
        it["theta"] = theta
        it["homography"] = _compute_rigid_homography(theta, total_dx, total_dy, cx_img, cy_img)

    select_mode = getattr(args, "select_mode", "otsu")
    keep_pct_arg = getattr(args, "keep_percent", None)
    quality_thresh = getattr(args, "quality_threshold", 0.75)
    u_alpha = getattr(args, "utility_alpha", 2.0)
    u_beta = getattr(args, "utility_beta", 1.0)

    if primary:
        kept_indices = {it["index"] for it in valid_items}
        select_meta = {
            "mode": "import_selection",
            "kept_count": len(kept_indices),
            "kept_percent": round(100 * len(kept_indices) / len(included), 2),
        }
    else:
        kept_indices, select_meta = _select_frames_by_quality(
            valid_items, len(included), select_mode, keep_pct_arg, quality_thresh, u_alpha, u_beta
        )

    for it in image_files:
        it["selected"] = it["index"] in kept_indices

    th_str = f"{select_meta['threshold']:.1f} ({select_meta.get('threshold_rel', 0)*100:.1f}% of ref)" if "threshold" in select_meta else "N/A"
    log(f"frame selection: mode='{select_meta['mode']}', cutoff={th_str}, keeping {len(kept_indices)}/{len(image_files)} best frames ({select_meta['kept_percent']}%)")

    new_s_line = (
        f"S '{seq_name}' {start_idx} {nb_images} {len(kept_indices)} {fixed_len} "
        f"{ref_idx - start_idx} 6 0 0 0"
    )

    layer_count = 1
    l_line = next((ln for ln in seq_lines if ln.startswith("L ")), None)
    if l_line:
        try:
            layer_count = max(1, int(l_line.split()[1]))
        except (IndexError, ValueError):
            layer_count = 1

    new_seq_lines = [
        "#Siril sequence file. Generated by siril-moon-stacking with rigid homography registration",
        new_s_line,
        f"L {layer_count}",
    ]
    for it in image_files:
        new_seq_lines.append(f"I {it['index']} {1 if it['selected'] else 0}")

    matrices, origin_offset = _siril_homographies([it["homography"] for it in image_files])
    for matrix in matrices:
        new_seq_lines.append(f"R0 1.0 1.0 1.0 0 0.0 1 {matrix}")

    seq_path.write_text("\n".join(new_seq_lines) + "\n", encoding="utf-8")
    log(f"updated Siril sequence file: {seq_path}")

    ranking_data = {
        "sequence": seq_name,
        "registration_signature": signature,
        "origin_offset_x": origin_offset,
        "candidate_frames": len(included),
        "registered_frames": len(valid_items),
        "reused_frames": sum(bool(it["cached"]) for it in image_files),
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
                "source_index": it["source_index"],
                "identity": it["identity"],
                "homography": it["homography"],
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
    if video:
        dump_json(work / "registration_cache.json", ranking_data)
    log(f"registration & ranking complete: data dumped to {work / 'ranking.json'}")
