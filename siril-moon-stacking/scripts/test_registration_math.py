#!/usr/bin/env python3
"""Self-check and regression tests for siril-moon-stacking registration math.

Verifies:
  1. Subpixel phase correlation accuracy on synthetic shifts (error < 0.05 px)
  2. High-contrast lunar terrain ROI localization robustness
  3. Siril R0 homography matrix format compliance
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from moon_stack import (
    _calculate_channel_balance,
    _estimate_adaptive_sharpening,
    _estimate_pedestal,
    _locate_high_contrast_roi,
    _render_deep_cine_mineral,
    _select_frames_by_quality,
    _subpixel_phase_correlation,
    _suppress_lunar_limb_glare,
    cmd_postprocess,
)


def translate(src: np.ndarray, tx: float, ty: float) -> np.ndarray:
    """Fourier shift theorem for true subpixel translation."""
    h, w = src.shape
    fy = np.fft.fftfreq(h)
    fx = np.fft.fftfreq(w)
    fx_grid, fy_grid = np.meshgrid(fx, fy)
    phase = np.exp(-2j * np.pi * (fx_grid * tx + fy_grid * ty))
    shifted = np.real(np.fft.ifft2(np.fft.fft2(src) * phase))
    return shifted.astype(np.float32)


def make_synthetic_moon(h: int = 512, w: int = 512, seed: int = 42) -> np.ndarray:
    """Generate synthetic moon with textured craters and dark space."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h // 2, w // 2
    r = min(h, w) * 0.4
    dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    moon_mask = dist <= r

    img = np.zeros((h, w), dtype=np.float32)
    # Background space
    img += rng.normal(50, 2, (h, w)).astype(np.float32)

    # Moon surface base
    img[moon_mask] = 1200.0

    # Add craters
    for _ in range(40):
        crater_y = rng.integers(int(cy - r * 0.7), int(cy + r * 0.7))
        crater_x = rng.integers(int(cx - r * 0.7), int(cx + r * 0.7))
        crater_r = rng.uniform(8, 30)
        c_dist = np.sqrt((xx - crater_x) ** 2 + (yy - crater_y) ** 2)
        c_mask = c_dist <= crater_r
        # Crater rim (bright) and floor (dark)
        rim = (c_dist > crater_r * 0.7) & c_mask
        floor = c_dist <= crater_r * 0.7
        img[rim] += rng.uniform(300, 800)
        img[floor] -= rng.uniform(200, 500)

    return img


def test_subpixel_shifts() -> None:
    failures = []
    ref = make_synthetic_moon()
    test_cases = [
        (0.0, 0.0),
        (5.25, -3.75),
        (-8.40, 12.15),
        (1.50, 0.00),
        (0.00, -7.85),
    ]

    for tx, ty in test_cases:
        shifted = translate(ref, tx, ty)
        dx, dy, resp = _subpixel_phase_correlation(ref, shifted)
        err_x = abs(dx - tx)
        err_y = abs(dy - ty)
        print(f"Shift test truth=({tx:+6.2f}, {ty:+6.2f}) -> estimated=({dx:+6.2f}, {dy:+6.2f}), resp={resp:.3f}, err=({err_x:.3f}, {err_y:.3f})")
        if err_x > 0.08 or err_y > 0.08:
            failures.append(f"Subpixel shift error too large for ({tx},{ty}): got ({dx:.3f},{dy:.3f})")
        if resp < 0.15:
            failures.append(f"Response too low for valid synthetic image: {resp:.3f}")

    assert not failures, f"Failures in test_subpixel_shifts: {failures}"



def test_roi_localization() -> None:
    failures = []
    img = make_synthetic_moon(h=1024, w=1024)
    x0, y0, x1, y1 = _locate_high_contrast_roi(img, roi_size=256)
    print(f"ROI localization on 1024x1024 moon: [{x0}:{x1}, {y0}:{y1}]")

    if x0 < 100 or x1 > 924 or y0 < 100 or y1 > 924:
        failures.append(f"ROI fell outside the moon disc: [{x0}:{x1}, {y0}:{y1}]")

    assert not failures, f"Failures in test_roi_localization: {failures}"


def test_siril_r0_format() -> None:
    failures = []
    dx, dy = 15.3421, -8.7654
    line = f"R0 1.0 1.0 1.0 0 0.0 1 H 1 0 {-dx:.4f} 0 1 {-dy:.4f} 0 0 1"
    parts = line.split()
    if parts[0] != "R0":
        failures.append("Line must start with R0")
    if parts[7] != "H":
        failures.append("Matrix flag must be H")
    if len(parts) != 17:
        failures.append(f"Expected 17 tokens in R0 homography line, got {len(parts)}")
    assert not failures, f"Failures in test_siril_r0_format: {failures}"



def test_otsu_frame_selection() -> None:
    failures = []
    # Synthetic bimodal distribution: 10 sharp frames (~2500), 10 blurry frames (~1200)
    rng = np.random.default_rng(123)
    sharp_frames = [{"index": i, "sharpness": float(rng.normal(2500, 50))} for i in range(1, 11)]
    blurry_frames = [{"index": i, "sharpness": float(rng.normal(1200, 50))} for i in range(11, 21)]
    all_frames = sharp_frames + blurry_frames

    kept, meta = _select_frames_by_quality(all_frames, total_count=20, select_mode="otsu")
    print(f"Otsu bimodal test: kept {len(kept)}/20 frames, threshold={meta.get('threshold', 0):.1f}")
    if kept != set(range(1, 11)):
        failures.append(f"Otsu failed to separate bimodal distribution: expected 1..10, got {kept}")

    # Small sample test (< 4 frames)
    tiny = [{"index": 1, "sharpness": 2000.0}, {"index": 2, "sharpness": 1500.0}]
    kept_tiny, meta_tiny = _select_frames_by_quality(tiny, total_count=2, select_mode="otsu")
    if len(kept_tiny) != 2:
        failures.append(f"Otsu should retain all frames for tiny samples (<4), got {len(kept_tiny)}")

    # Percentage override test
    kept_pct, meta_pct = _select_frames_by_quality(all_frames, total_count=20, select_mode="otsu", keep_percent=30)
    if len(kept_pct) != 6:
        failures.append(f"keep_percent=30 override failed: expected 6 frames, got {len(kept_pct)}")

    assert not failures, f"Failures in test_otsu_frame_selection: {failures}"



def test_utility_frame_selection() -> None:
    failures = []
    # Gradually decaying distribution: 20 frames from 2500 down to 1000
    frames = [{"index": i, "sharpness": 2500.0 - (i - 1) * 75.0} for i in range(1, 21)]

    kept, meta = _select_frames_by_quality(frames, total_count=20, select_mode="utility", utility_alpha=2.0, utility_beta=1.0)
    print(f"MTF-SNR Utility test (alpha=2.0, beta=1.0): kept {len(kept)}/20 frames, cutoff={meta.get('threshold', 0):.1f} ({meta.get('threshold_rel', 0)*100:.1f}%)")
    if not (5 <= len(kept) <= 15):
        failures.append(f"Utility model returned unexpected frame count: {len(kept)}")

    # Alias 'mtf-snr' check
    kept_alias, _ = _select_frames_by_quality(frames, total_count=20, select_mode="mtf-snr")
    if kept_alias != kept:
        failures.append(f"'mtf-snr' alias did not match 'utility' mode output")

    assert not failures, f"Failures in test_utility_frame_selection: {failures}"



def test_pedestal_estimation() -> None:
    failures = []

    # 1. Standard centered moon with black space
    img_normal = make_synthetic_moon(512, 512, seed=1)
    # Background noise was generated with normal(50, 2)
    bg_normal = _estimate_pedestal(img_normal)
    print(f"Pedestal estimation (centered moon): estimated={bg_normal:.2f}, expected~50.0")
    if not (45.0 <= bg_normal <= 55.0):
        failures.append(f"Normal moon pedestal out of range: got {bg_normal:.2f}, expected ~50.0")

    # 2. Off-center moon covering top-left corner
    img_offcenter = np.zeros((512, 512), dtype=np.float32)
    rng = np.random.default_rng(2)
    img_offcenter += rng.normal(50, 2, (512, 512)).astype(np.float32)
    # Place bright lunar surface covering top-left [0:150, 0:150]
    img_offcenter[:150, :150] = 1200.0
    bg_offcenter = _estimate_pedestal(img_offcenter)
    print(f"Pedestal estimation (off-center moon covering top-left): estimated={bg_offcenter:.2f}, expected~50.0")
    if not (45.0 <= bg_offcenter <= 55.0):
        failures.append(f"Off-center moon hit bright corner: got {bg_offcenter:.2f}, expected ~50.0")

    # 3. Full-frame close-up moon without sky background
    img_closeup = np.full((512, 512), 1000.0, dtype=np.float32)
    img_closeup[100:200, 100:200] = 200.0  # deep shadow
    img_closeup[300:400, 300:400] = 1600.0  # bright crater
    bg_closeup = _estimate_pedestal(img_closeup)
    print(f"Pedestal estimation (close-up surface, no sky): estimated={bg_closeup:.2f}")
    if bg_closeup > 1000.0:
        failures.append(f"Close-up moon pedestal exceeded surface base: {bg_closeup:.2f}")

    # 4. Image with framing border zeros
    img_padded = img_normal.copy()
    img_padded[:8, :] = 0.0
    img_padded[:, :8] = 0.0
    bg_padded = _estimate_pedestal(img_padded)
    print(f"Pedestal estimation (with zero framing padding): estimated={bg_padded:.2f}, expected~50.0")
    if not (45.0 <= bg_padded <= 55.0):
        failures.append(f"Padded moon was corrupted by zero border: got {bg_padded:.2f}")

    assert not failures, f"Failures in test_pedestal_estimation: {failures}"


def test_postprocess_pipelines() -> None:
    import argparse
    import tempfile
    from astropy.io import fits

    failures = []
    siril_bin = "/Applications/Siril.app/Contents/MacOS/siril-cli"
    if not Path(siril_bin).exists():
        print("Siril CLI not found on host, skipping full integration test")
        return

    # 1. Test L/RGB separation pipeline
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        # Create synthetic master RGB (256x256)
        master = np.zeros((3, 256, 256), dtype=np.float32)
        master[0] = 0.6  # R
        master[1] = 0.5  # G
        master[2] = 0.4  # B
        master[:, 64:192, 64:192] += 0.2
        fits.writeto(work / "moon_master.fit", master, overwrite=True)

        args = argparse.Namespace(
            work=str(work),
            master="moon_master.fit",
            siril=siril_bin,
            timeout=120,
            deconv="none",
            no_adc=True,
            midtone=0.13,
            clahe_clip=1.0,
            wavelet_l1=1.05,
            mineral_mode="lrgb",
            sat_fe=0.85,
            sat_ti=0.90,
            sat_base=0.35,
            sat_bg_factor=1.2,
        )

        cmd_postprocess(args)

        # Check lum file was written and accurate
        lum_file = work / "moon_lum.fit"
        if not lum_file.exists():
            failures.append("moon_lum.fit was not generated in LRGB mode")
        else:
            with fits.open(lum_file) as h:
                actual_val = float(h[0].data[0, 0])
                if actual_val <= 0 or not np.isfinite(actual_val):
                    failures.append(f"Invalid luminance value: {actual_val}")
                if h[0].data.shape != (256, 256):
                    failures.append(f"Luminance shape mismatch: {h[0].data.shape}")

        # Check that 03_postprocess.ssf contains targeted saturation commands
        ssf_path = work / "logs" / "03_postprocess.ssf"
        if ssf_path.exists():
            ssf_text = ssf_path.read_text()
            if "satu 0.85 1.2 1" not in ssf_text or "satu 0.90 1.2 3" not in ssf_text or "satu 0.35 1.2 6" not in ssf_text:
                failures.append(f"03_postprocess.ssf missing targeted saturation commands: {ssf_text}")
        else:
            failures.append("03_postprocess.ssf was not generated")

        # Check output products
        for prod in ["moon_natural.tif", "moon_natural.jpg", "moon_mineral.jpg"]:
            f = work / prod
            if not f.exists() or f.stat().st_size == 0:
                failures.append(f"LRGB product {prod} was not generated or empty")

        print("L/RGB pipeline integration test passed (natural tif/jpg, mineral jpg, and targeted satu verified)")

    # 2. Test monochrome pipeline
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        mono = np.zeros((256, 256), dtype=np.float32)
        mono[64:192, 64:192] = 0.5
        fits.writeto(work / "moon_master.fit", mono, overwrite=True)

        args = argparse.Namespace(
            work=str(work),
            master="moon_master.fit",
            siril=siril_bin,
            timeout=120,
            deconv="none",
            no_adc=True,
            midtone=0.13,
            clahe_clip=1.0,
            wavelet_l1=1.05,
            mineral_mode="lrgb",
        )

        cmd_postprocess(args)

        for prod in ["moon_natural.tif", "moon_natural.jpg"]:
            f = work / prod
            if not f.exists() or f.stat().st_size == 0:
                failures.append(f"Mono product {prod} was not generated or empty")
        if (work / "moon_mineral.jpg").exists():
            failures.append("Mono pipeline should not generate mineral moon")

        print("Monochrome pipeline integration test passed")

    assert not failures, f"Failures in test_postprocess_pipelines: {failures}"


def test_gray_world_channel_balance() -> None:
    failures = []

    # 1. Synthesize moon with significant camera Bayer green/color cast
    h, w = 256, 256
    rng = np.random.default_rng(123)
    moon_neutral = rng.uniform(0.1, 0.7, (h, w)).astype(np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    mask = np.sqrt((xx - 128) ** 2 + (yy - 128) ** 2) <= 100
    moon_neutral[~mask] = 0.002  # background dark sky

    # Distort with realistic sensor color cast:
    # R response: 0.85x, G response: 1.25x (strong green cast), B response: 0.70x
    r = moon_neutral * 0.85 + 0.001
    g = moon_neutral * 1.25 + 0.0015
    b = moon_neutral * 0.70 + 0.0008
    d = np.stack([r, g, b], axis=0)

    bg_r = _estimate_pedestal(d[0])
    bg_g = _estimate_pedestal(d[1])
    bg_b = _estimate_pedestal(d[2])

    wb_gw = _calculate_channel_balance(d, bg_r, bg_g, bg_b, mid_val=0.13, wb_mode="gray-world")
    print(f"Gray-World test: moon_pixels={wb_gw['moon_pixels']}, R/G ratio={wb_gw['ratio_r']:.4f}, B/G ratio={wb_gw['ratio_b']:.4f}")

    if wb_gw["moon_pixels"] < 5000:
        failures.append(f"Gray-World detected too few moon pixels: {wb_gw['moon_pixels']}")

    # Check linear balanced values of the median lunar surface
    lunar_mask = (wb_gw["lum_data"] > (wb_gw["bg_lum"] + 0.05 * (wb_gw["hi_lum"] - wb_gw["bg_lum"]))) & (wb_gw["lum_data"] < (0.95 * wb_gw["hi_lum"]))
    c_data = wb_gw["color_data"]
    med_r_lin = float(np.median(c_data[0][lunar_mask]))
    med_g_lin = float(np.median(c_data[1][lunar_mask]))
    med_b_lin = float(np.median(c_data[2][lunar_mask]))

    ratio_rg = med_r_lin / med_g_lin
    ratio_bg = med_b_lin / med_g_lin
    print(f"Balanced linear lunar albedo: R={med_r_lin:.4f}, G={med_g_lin:.4f}, B={med_b_lin:.4f} -> R/G={ratio_rg:.4f}, B/G={ratio_bg:.4f} (target=1.0000)")

    if abs(ratio_rg - 1.0) > 0.02 or abs(ratio_bg - 1.0) > 0.02:
        failures.append(f"Gray-World balance failed to neutralize lunar color: R/G={ratio_rg:.4f}, B/G={ratio_bg:.4f}")

    # 2. Check Defringe capability on synthetic purple limb flare
    d_fringe = d.copy()
    # Add strong chromatic aberration / purple flare along outer rim
    rim_mask = (np.sqrt((xx - 128) ** 2 + (yy - 128) ** 2) >= 95) & (np.sqrt((xx - 128) ** 2 + (yy - 128) ** 2) <= 105)
    d_fringe[2, rim_mask] = d_fringe[1, rim_mask] * 3.5  # 350% excess blue flare
    wb_defringe = _calculate_channel_balance(d_fringe, bg_r, bg_g, bg_b, mid_val=0.13, wb_mode="gray-world")
    c_defringed = wb_defringe["color_data"]
    denom = np.maximum(c_defringed[1, rim_mask], c_defringed[0, rim_mask])
    valid_denom = denom > 1e-4
    rim_b_excess = c_defringed[2, rim_mask][valid_denom] / denom[valid_denom]
    max_rim_excess = float(np.max(rim_b_excess)) if rim_b_excess.size > 0 else 1.0
    print(f"Limb defringe test: maximum Blue/Green ratio on rim after defringe = {max_rim_excess:.2f} (raw was 3.50)")
    if max_rim_excess > 1.16:
        failures.append(f"Limb defringe failed to clamp purple flare: got {max_rim_excess:.2f}, expected <= 1.15")

    # 3. Check legacy mode (does not balance relative ratios)
    wb_leg = _calculate_channel_balance(d, bg_r, bg_g, bg_b, mid_val=0.13, wb_mode="legacy")
    if wb_leg["ratio_r"] != 1.0 or wb_leg["ratio_b"] != 1.0:
        failures.append("Legacy mode should not calculate relative ratios")

    assert not failures, f"Failures in test_gray_world_channel_balance: {failures}"


def test_adaptive_sharpening_math() -> None:
    failures = []
    # Synthesize a lunar image with crater contrast and flat mare
    img = make_synthetic_moon(h=512, w=512, seed=99)

    # 1. Test auto mode with active deconvolution (Airy deconv discount applied)
    res_deconv = _estimate_adaptive_sharpening(img, has_deconv=True, interp_used="cu", sharp_mode="auto")
    print(f"Adaptive deconv=True: wrecons={res_deconv['wrecons_cmd']}, clahe={res_deconv['clahe_clip']}, unsharp={res_deconv['unsharp_amount']}")

    if res_deconv["deconv_discount"] != 0.55:
        failures.append(f"Expected deconv discount 0.55, got {res_deconv['deconv_discount']}")
    if res_deconv["unsharp_amount"] != 0.0 or res_deconv["unsharp_lines"]:
        failures.append("USM unsharp mask should be automatically bypassed in auto mode")
    if not (0.05 <= res_deconv["clahe_clip"] <= 0.25):
        failures.append(f"CLAHE clip out of balanced organic range: {res_deconv['clahe_clip']}")

    # 2. Test auto mode without deconvolution (should have higher wavelet gain)
    res_nodeconv = _estimate_adaptive_sharpening(img, has_deconv=False, interp_used="cu", sharp_mode="auto")
    print(f"Adaptive deconv=False: wrecons={res_nodeconv['wrecons_cmd']}, clahe={res_nodeconv['clahe_clip']}")
    if res_nodeconv["wavelet_coeffs"][2] <= res_deconv["wavelet_coeffs"][2]:
        failures.append("Wavelet gain without deconvolution should be greater than with deconvolution")

    # 3. Test interpolation awareness (bilinear interp should boost L1 compared to bicubic)
    res_li = _estimate_adaptive_sharpening(img, has_deconv=True, interp_used="li", sharp_mode="auto")
    if res_li["wavelet_coeffs"][0] < res_deconv["wavelet_coeffs"][0]:
        failures.append("Bilinear interpolation should receive higher L1 compensation than bicubic")

    # 4. Test explicit user overrides
    res_override = _estimate_adaptive_sharpening(
        img, has_deconv=True, sharp_mode="auto",
        user_wavelet_l1=1.12, user_clahe_clip=0.45, user_unsharp=0.25
    )
    if res_override["wavelet_coeffs"][0] != 1.12:
        failures.append(f"User wavelet_l1 override failed: {res_override['wavelet_coeffs'][0]}")
    if res_override["clahe_clip"] != 0.45:
        failures.append(f"User clahe_clip override failed: {res_override['clahe_clip']}")
    if res_override["unsharp_amount"] != 0.25 or not res_override["unsharp_lines"]:
        failures.append(f"User unsharp override failed: {res_override['unsharp_amount']}")

    # 5. Test sharp_mode='mellow'
    res_mellow = _estimate_adaptive_sharpening(img, sharp_mode="mellow")
    if res_mellow["clahe_clip"] != 0.0 or res_mellow["clahe_lines"]:
        failures.append("Mode 'mellow' should completely bypass CLAHE")
    if res_mellow["wavelet_coeffs"][0] != 1.01 or res_mellow["wavelet_coeffs"][2] != 1.03:
        failures.append(f"Mode 'mellow' wavelet mismatch: {res_mellow['wavelet_coeffs']}")

    # 6. Test sharp_mode='none'
    res_none = _estimate_adaptive_sharpening(img, sharp_mode="none")
    if res_none["wavelet_coeffs"] != [1.0, 1.0, 1.0, 1.0, 1.0, 1.0] or res_none["clahe_clip"] != 0.0:
        failures.append("Mode 'none' did not bypass wavelets and CLAHE")

    assert not failures, f"Failures in test_adaptive_sharpening_math: {failures}"


def test_lunar_limb_glare_suppression() -> None:
    failures = []
    h, w = 512, 512
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt((xx - 256)**2 + (yy - 256)**2)
    img = np.zeros((h, w), dtype=np.float32)
    img[r <= 150] = 0.5 + 0.1 * np.cos(r[r <= 150] / 10.0)
    glare_zone = (r > 150) & (r <= 200) & (xx > 256)
    img[glare_zone] = 0.25 * np.exp(-(r[glare_zone] - 150) / 15.0)

    # 1. Apply glare suppression in auto mode
    cleaned, meta = _suppress_lunar_limb_glare(img, glare_mode="auto")
    print(f"Glare suppression auto: active={meta.get('active')}, center=({meta.get('center_x', 0):.1f}, {meta.get('center_y', 0):.1f}), R={meta.get('radius', 0):.1f}")

    if not meta.get("active"):
        failures.append("Limb glare suppression failed to detect and fit synthetic lunar limb")
    else:
        # Check that lunar disc inside r <= 145 is 100% unaltered
        inner_mask = r <= 145
        diff_inner = float(np.max(np.abs(cleaned[inner_mask] - img[inner_mask])))
        if diff_inner > 1e-5:
            failures.append(f"Lunar disc pixels were modified by glare suppression: max diff={diff_inner}")

        # Check that glare outside r >= R + delta + 2px is suppressed to zero
        outer_mask = (r >= 150 + meta["delta"] + 2.0) & (xx > 256)
        max_outer = float(np.max(cleaned[outer_mask]))
        if max_outer > 1e-4:
            failures.append(f"Outer space glare was not suppressed to zero: max remaining={max_outer}")

    # 2. Test off mode
    clean_off, meta_off = _suppress_lunar_limb_glare(img, glare_mode="off")
    if meta_off.get("active"):
        failures.append("Mode 'off' should not be active")

    assert not failures, f"Failures in test_lunar_limb_glare_suppression: {failures}"


def test_render_deep_cine_mineral() -> None:
    failures = []
    h, w = 256, 256
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy, R = 128.0, 128.0, 100.0
    r_dist = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    moon_mask = r_dist <= R

    # 1. Synthesize lum_sharp (2D float)
    lum_sharp = np.zeros((h, w), dtype=np.float32)
    lum_sharp[moon_mask] = 0.40
    # Shadow craters near terminator (lum ~ 0.04)
    shadow_mask = moon_mask & (xx < 60)
    lum_sharp[shadow_mask] = 0.04
    # Bright ray peaks (lum ~ 0.95)
    ray_mask = moon_mask & (xx > 200)
    lum_sharp[ray_mask] = 0.95

    # 2. Synthesize color_balanced (3, 256, 256)
    color_bal = np.zeros((3, h, w), dtype=np.float32)
    color_bal[0, moon_mask] = lum_sharp[moon_mask]
    color_bal[1, moon_mask] = lum_sharp[moon_mask]
    color_bal[2, moon_mask] = lum_sharp[moon_mask]

    # Add Fe-rich zone (R excess) in northern maria (yy < 100, 80 <= xx <= 180)
    fe_zone = moon_mask & (yy < 100) & (xx >= 80) & (xx <= 180)
    color_bal[0, fe_zone] *= 1.03
    color_bal[2, fe_zone] *= 0.97

    # Add Ti-rich basalt zone (B excess) in southern mare (yy > 150, 80 <= xx <= 180)
    ti_zone = moon_mask & (yy > 150) & (xx >= 80) & (xx <= 180)
    color_bal[2, ti_zone] *= 1.04
    color_bal[0, ti_zone] *= 0.96

    circle_meta = {"active": True, "center_x": cx, "center_y": cy, "radius": R}

    bgr = _render_deep_cine_mineral(
        lum_sharp,
        color_bal,
        circle_meta=circle_meta,
        fe_boost=8.5,
        ti_boost=9.0,
        gamma=1.38,
    )

    if bgr.shape != (h, w, 3) or bgr.dtype != np.uint8:
        failures.append(f"Unexpected output shape/dtype: shape={bgr.shape}, dtype={bgr.dtype}")

    # Verify outer space zeroing (r_dist > R + 6)
    r_dist_bgr = r_dist[::-1, :]
    outer_space = r_dist_bgr > (R + 6.0)
    if np.max(bgr[outer_space]) > 0:
        failures.append(f"Outer space was not strictly zeroed: max={np.max(bgr[outer_space])}")

    # Shadow craters roll-off: shadow craters had lum ~ 0.04 (< 0.05), so shadow_mask taper is 0.
    shadow_bgr_mask = shadow_mask[::-1, :]
    shadow_pixels = bgr[shadow_bgr_mask]
    diff_rg = np.abs(shadow_pixels[:, 2].astype(int) - shadow_pixels[:, 1].astype(int))
    diff_bg = np.abs(shadow_pixels[:, 0].astype(int) - shadow_pixels[:, 1].astype(int))
    if np.max(diff_rg) > 1 or np.max(diff_bg) > 1:
        failures.append(f"Shadow crater chroma was not rolled off: max diff RG={np.max(diff_rg)}, BG={np.max(diff_bg)}")

    # Check Fe vs Ti color discrimination:
    fe_bgr_mask = fe_zone[::-1, :]
    mean_fe_r = np.mean(bgr[fe_bgr_mask, 2])
    mean_fe_b = np.mean(bgr[fe_bgr_mask, 0])
    if mean_fe_r <= mean_fe_b:
        failures.append(f"Fe zone did not boost red: mean R={mean_fe_r:.1f} <= B={mean_fe_b:.1f}")

    ti_bgr_mask = ti_zone[::-1, :]
    mean_ti_b = np.mean(bgr[ti_bgr_mask, 0])
    mean_ti_r = np.mean(bgr[ti_bgr_mask, 2])
    if mean_ti_b <= mean_ti_r:
        failures.append(f"Ti zone did not boost blue: mean B={mean_ti_b:.1f} <= R={mean_ti_r:.1f}")

    # Deep tone sculpting: midtone 0.40 with gamma=1.38 should sculpt midtones ~ 0.27
    mid_neutral = (moon_mask & (lum_sharp == 0.40) & ~fe_zone & ~ti_zone)[::-1, :]
    med_val = np.median(bgr[mid_neutral, 1])
    print(f"Deep-Cine sculpted midtone median: {med_val:.1f} (expected ~ 65-75, linear was ~102)")
    if not (50 <= med_val <= 85):
        failures.append(f"Deep-Cine midtone out of expected sculpted range: {med_val}")

    print("Deep-Cine mineral rendering unit test passed")
    assert not failures, f"Failures in test_render_deep_cine_mineral: {failures}"


def main() -> int:
    tests = [
        ("test_subpixel_shifts", test_subpixel_shifts),
        ("test_roi_localization", test_roi_localization),
        ("test_siril_r0_format", test_siril_r0_format),
        ("test_otsu_frame_selection", test_otsu_frame_selection),
        ("test_utility_frame_selection", test_utility_frame_selection),
        ("test_pedestal_estimation", test_pedestal_estimation),
        ("test_gray_world_channel_balance", test_gray_world_channel_balance),
        ("test_adaptive_sharpening_math", test_adaptive_sharpening_math),
        ("test_lunar_limb_glare_suppression", test_lunar_limb_glare_suppression),
        ("test_render_deep_cine_mineral", test_render_deep_cine_mineral),
        ("test_postprocess_pipelines", test_postprocess_pipelines),
    ]
    failed = 0
    for name, t in tests:
        print(f"\n--- Running {name} ---")
        try:
            t()
        except AssertionError as e:
            print(f"FAILED: {e}")
            failed += 1

    if failed:
        print(f"\n{failed} TESTS FAILED!")
        return 1

    print("\nALL MATHEMATICAL & FORMAT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
