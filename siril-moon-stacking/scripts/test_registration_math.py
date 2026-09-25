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
    _apply_anti_ringing_damping,
    _calculate_channel_balance,
    _compute_edge_ringing_damping_mask,
    _estimate_adaptive_sharpening,
    _estimate_pedestal,
    _load_locked_profile,
    _locate_high_contrast_roi,
    _measure_chalky_saturation_index,
    _measure_dark_halo_ratio,
    _measure_gradient_kurtosis,
    _render_deep_cine_mineral,
    _select_frames_by_quality,
    _subpixel_phase_correlation,
    _suppress_lunar_limb_glare,
    _infer_optical_parameters,
    DEFAULT_SIRIL,
    _apply_extinction_compensation,
    _estimate_atmospheric_extinction_gradient,
    cmd_import,
    cmd_postprocess,
    cmd_stack,
    parse_ser_header,
    unpack_ser_to_fits,
    unpack_video_to_fits,
    format_video_decode_error,
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
                actual_val = float(h[0].data[128, 128])
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
    if res_deconv["clahe_clip"] != 0.0 or res_deconv["clahe_lines"]:
        failures.append("CLAHE should be automatically bypassed when deconvolution is active to avoid pepper-salt shot noise amplification")

    # 2. Test auto mode without deconvolution (should have higher wavelet gain and organic CLAHE)
    res_nodeconv = _estimate_adaptive_sharpening(img, has_deconv=False, interp_used="cu", sharp_mode="auto")
    print(f"Adaptive deconv=False: wrecons={res_nodeconv['wrecons_cmd']}, clahe={res_nodeconv['clahe_clip']}")
    if res_nodeconv["wavelet_coeffs"][2] <= res_deconv["wavelet_coeffs"][2]:
        failures.append("Wavelet gain without deconvolution should be greater than with deconvolution")
    if not (0.05 <= res_nodeconv["clahe_clip"] <= 0.25):
        failures.append(f"CLAHE clip out of balanced organic range when deconv=False: {res_nodeconv['clahe_clip']}")

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


def test_anti_ringing_damping_math() -> None:
    failures = []
    # 1. Synthesize step edge (sunlit crater rim vs deep shadow)
    H, W = 256, 256
    base = np.full((H, W), 0.05, dtype=np.float32)
    base[:, :128] = 0.85

    # Simulate overshoot on bright side and negative undershoot dip on shadow side
    sharp = base.copy()
    sharp[:, 125:128] += 0.08   # overshoot (ridge peak)
    sharp[:, 128:133] -= 0.045  # undershoot (dark ringing dip -> 0.005)

    damp_mask = _compute_edge_ringing_damping_mask(base)
    # Check mask localizes shadow side and not bright side
    shadow_mask_mean = float(np.mean(damp_mask[:, 128:133]))
    bright_mask_mean = float(np.mean(damp_mask[:, 115:125]))
    print(f"Anti-ringing mask: shadow-side={shadow_mask_mean:.3f}, bright-side={bright_mask_mean:.3f}")

    if shadow_mask_mean < 0.40:
        failures.append(f"Damping mask failed to target shadow side: {shadow_mask_mean}")
    if bright_mask_mean > 0.15:
        failures.append(f"Damping mask leaked excessively into bright side: {bright_mask_mean}")

    # 2. Test damping application
    damped = _apply_anti_ringing_damping(base, sharp, damp_mask, strength=0.60)

    # Assert bright side overshoot (peak detail) is 100% retained
    if not np.allclose(damped[:, 125:128], sharp[:, 125:128]):
        failures.append("Anti-ringing damping corrupted positive highlight overshoot")

    # Assert shadow-side dip is lifted
    dip_orig = float(np.mean(sharp[:, 128:133]))
    dip_damped = float(np.mean(damped[:, 128:133]))
    print(f"Shadow dip depth: original={dip_orig:.4f} (undershoot), damped={dip_damped:.4f} (base={np.mean(base[:, 128:133]):.4f})")
    if dip_damped <= dip_orig + 0.015:
        failures.append(f"Damping did not sufficiently lift dark undershoot dip: {dip_damped}")

    # 3. Test parameter variations
    res_auto = _estimate_adaptive_sharpening(base, has_deconv=True, interp_used="cu", anti_ringing="auto")
    if res_auto["damping_strength"] != 0.65:
        failures.append(f"Expected auto damping 0.65 for deconv+cu, got {res_auto['damping_strength']}")

    res_off = _estimate_adaptive_sharpening(base, anti_ringing="off")
    if res_off["damping_strength"] != 0.0:
        failures.append(f"Expected damping 0.0 for anti_ringing='off', got {res_off['damping_strength']}")

    res_manual = _estimate_adaptive_sharpening(base, anti_ringing="auto", damping_factor=0.75)
    if res_manual["damping_strength"] != 0.75:
        failures.append(f"Manual damping_factor override failed: {res_manual['damping_strength']}")

    print("Anti-ringing mathematical damping unit test passed")
    assert not failures, f"Failures in test_anti_ringing_damping_math: {failures}"


def test_dark_halo_ratio_metric() -> None:
    failures = []
    H, W = 128, 128
    base = np.full((H, W), 0.10, dtype=np.float32)
    base[:, :64] = 0.90

    mask = _compute_edge_ringing_damping_mask(base)

    # Case A: Clean image with no undershoot
    clean_sharp = base.copy()
    clean_sharp[:, 60:64] += 0.05
    dhr_clean = _measure_dark_halo_ratio(base, clean_sharp, mask)
    print(f"DHR clean image: {dhr_clean:.5f} (target 0.0)")
    if dhr_clean != 0.0:
        failures.append(f"Clean image should have 0.0 DHR, got {dhr_clean}")

    # Case B: Artificial undershoot injection
    ringing_sharp = clean_sharp.copy()
    ringing_sharp[:, 64:68] -= 0.08
    dhr_ringing = _measure_dark_halo_ratio(base, ringing_sharp, mask)
    print(f"DHR ringing image: {dhr_ringing:.5f}")
    if dhr_ringing <= 0.02:
        failures.append(f"DHR failed to detect severe dark ringing: {dhr_ringing}")

    # Case C: After damping
    damped = _apply_anti_ringing_damping(base, ringing_sharp, mask, strength=0.75)
    dhr_damped = _measure_dark_halo_ratio(base, damped, mask)
    print(f"DHR damped image: {dhr_damped:.5f}")
    if dhr_damped >= dhr_ringing * 0.40:
        failures.append(f"DHR after damping did not drop significantly: {dhr_damped} vs {dhr_ringing}")

    print("Dark Halo Ratio metric unit test passed")
    assert not failures, f"Failures in test_dark_halo_ratio_metric: {failures}"


def test_anti_brittle_metrics_math() -> None:
    failures = []
    # 1. Clean synthetic moon
    clean_moon = make_synthetic_moon(h=256, w=256)
    clean_norm = (clean_moon - np.min(clean_moon)) / (np.max(clean_moon) - np.min(clean_moon))

    csi_clean = _measure_chalky_saturation_index(clean_norm)
    gkm_clean = _measure_gradient_kurtosis(clean_norm)
    print(f"Clean moon metrics: CSI={csi_clean:.4f}, GKM={gkm_clean:.2f}")
    if csi_clean >= 0.010:
        failures.append(f"Clean textured moon flagged as chalky: CSI={csi_clean}")
    if not (2.0 <= gkm_clean <= 12.0):
        failures.append(f"Clean textured moon GKM out of organic range [2.0, 12.0]: GKM={gkm_clean}")

    # 2. Chalky saturated moon: artificially blow out and flatten crater peaks into a plateau
    chalky_moon = clean_norm.copy()
    chalky_moon[100:150, 100:150] = 1.0  # zero micro-gradients in high-luminance plateau
    csi_chalky = _measure_chalky_saturation_index(chalky_moon)
    print(f"Chalky moon CSI: {csi_chalky:.4f} (clean was {csi_clean:.4f})")
    if csi_chalky <= 0.020:
        failures.append(f"CSI failed to detect flat bleached highlights: got {csi_chalky}")

    # 3. Brittle / crunchy texture: simulate extreme high-pass unsharp mask over-sharpening
    import cv2
    blurred = cv2.GaussianBlur(clean_norm, (3, 3), 0.8)
    high_freq = clean_norm - blurred
    brittle_moon = np.clip(clean_norm + 6.0 * high_freq, 0.0, 1.0)
    gkm_brittle = _measure_gradient_kurtosis(brittle_moon)
    print(f"Brittle moon GKM: {gkm_brittle:.2f} (clean was {gkm_clean:.2f})")
    if gkm_brittle <= 14.0:
        failures.append(f"GKM failed to detect brittle spiky gradient distribution: got {gkm_brittle}")

    print("Anti-brittle metrics mathematical unit test passed")
    assert not failures, f"Failures in test_anti_brittle_metrics_math: {failures}"


def test_mosaic_tile_mode_pipeline() -> None:
    failures = []
    from astropy.io import fits
    from moon_stack import build_parser, dump_json
    import tempfile
    import shutil

    # 1. Test CLI parser default framing inference
    parser = build_parser()

    # Mode disc: default framing should resolve to 'min'
    ns_disc = parser.parse_args(["stack", "--work", "/tmp", "--mosaic-mode", "disc"])
    resolved_framing_disc = ns_disc.framing if ns_disc.framing else ("max" if ns_disc.mosaic_mode == "tile" else "min")
    if resolved_framing_disc != "min":
        failures.append(f"Expected default framing 'min' for disc mode, got {resolved_framing_disc}")

    # Mode tile: default framing should resolve to 'max'
    ns_tile = parser.parse_args(["stack", "--work", "/tmp", "--mosaic-mode", "tile"])
    resolved_framing_tile = ns_tile.framing if ns_tile.framing else ("max" if ns_tile.mosaic_mode == "tile" else "min")
    if resolved_framing_tile != "max":
        failures.append(f"Expected default framing 'max' for tile mode, got {resolved_framing_tile}")

    # Explicit override: user asks for min in tile mode
    ns_override = parser.parse_args(["stack", "--work", "/tmp", "--mosaic-mode", "tile", "--framing", "min"])
    resolved_override = ns_override.framing if ns_override.framing else ("max" if ns_override.mosaic_mode == "tile" else "min")
    if resolved_override != "min":
        failures.append(f"Expected explicit framing 'min' to be respected in tile mode, got {resolved_override}")

    # 2. Test Deep-Cine Mineral zeroing bypass in tile mode
    H, W = 256, 256
    lum_sharp = np.full((H, W), 0.35, dtype=np.float32)
    # Put bright texture across entire frame including corners
    color_cube = np.stack([lum_sharp * 1.05, lum_sharp * 0.98, lum_sharp * 0.95], axis=0)

    # Full disc with circle metadata: corners should be strictly zeroed
    meta_disc = {"active": True, "is_tile": False, "center_x": 128, "center_y": 128, "radius": 80, "delta": 4.5}
    bgr_disc = _render_deep_cine_mineral(lum_sharp, color_cube, circle_meta=meta_disc)
    corner_val_disc = np.max(bgr_disc[0:15, 0:15])
    print(f"Disc mode corner max: {corner_val_disc} (target 0)")
    if corner_val_disc > 0:
        failures.append(f"Disc mode failed to zero outer space corner pixels: {corner_val_disc}")

    # Mosaic tile mode: corners must NOT be zeroed
    meta_tile = {"active": False, "is_tile": True, "center_x": 128, "center_y": 128, "radius": 80, "delta": 4.5}
    bgr_tile = _render_deep_cine_mineral(lum_sharp, color_cube, circle_meta=meta_tile)
    corner_val_tile = np.max(bgr_tile[0:15, 0:15])
    print(f"Tile mode corner max: {corner_val_tile} (target > 0)")
    if corner_val_tile == 0:
        failures.append("Tile mode inadvertently zeroed corner pixels (destroyed overlap data)!")

    # 3. Test mosaic_tile_info.json schema compliance
    tmp_dir = Path(tempfile.mkdtemp(prefix="moon_tile_test_"))
    try:
        master_fit = tmp_dir / "moon_master.fit"
        data = np.ones((3, 64, 64), dtype=np.float32) * 500.0
        fits.writeto(master_fit, data, overwrite=True)

        tile_info = {
            "mosaic_mode": "tile",
            "master": str(master_fit),
            "shape": list(data.shape),
            "bitpix": -32,
            "pixel_size": 3.76,
            "focal_length": 500.0,
            "aperture": 100.0,
            "products": {
                "natural_tif": str(tmp_dir / "moon_natural.tif"),
                "natural_jpg": str(tmp_dir / "moon_natural.jpg"),
                "mineral_jpg": None,
            },
        }
        dump_json(tmp_dir / "mosaic_tile_info.json", tile_info)

        tile_json = tmp_dir / "mosaic_tile_info.json"
        if not tile_json.exists():
            failures.append("mosaic_tile_info.json was not created")
        else:
            import json
            with open(tile_json, "r") as f:
                loaded = json.load(f)
            if loaded.get("mosaic_mode") != "tile":
                failures.append(f"Wrong mosaic_mode in exported info: {loaded.get('mosaic_mode')}")
            if loaded.get("shape") != [3, 64, 64]:
                failures.append(f"Wrong shape in exported info: {loaded.get('shape')}")
            if loaded.get("pixel_size") != 3.76:
                failures.append(f"Wrong pixel_size in exported info: {loaded.get('pixel_size')}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("Mosaic tile mode pipeline unit test passed")
    assert not failures, f"Failures in test_mosaic_tile_mode_pipeline: {failures}"


def test_histogram_color_lock_pipeline() -> None:
    failures = []
    import tempfile
    import shutil
    from moon_stack import dump_json

    H, W = 128, 128
    # Shared overlap terrain patch
    overlap_r = np.full((32, 32), 0.14, dtype=np.float32)
    overlap_g = np.full((32, 32), 0.20, dtype=np.float32)
    overlap_b = np.full((32, 32), 0.10, dtype=np.float32)

    # Panel A (Bright Highland Master): contains brilliant crater peaks (0.75+) and highland anorthosite
    tile_a_r = np.full((H, W), 0.35, dtype=np.float32)
    tile_a_g = np.full((H, W), 0.40, dtype=np.float32)
    tile_a_b = np.full((H, W), 0.25, dtype=np.float32)
    tile_a_r[40:60, 40:60] = 0.85
    tile_a_g[40:60, 40:60] = 0.88
    tile_a_b[40:60, 40:60] = 0.70
    # Inject overlap patch
    tile_a_r[:32, :32] = overlap_r
    tile_a_g[:32, :32] = overlap_g
    tile_a_b[:32, :32] = overlap_b
    cube_a = np.stack([tile_a_r, tile_a_g, tile_a_b], axis=0)

    # Panel B (Dark Basalt Mare Slave): dim maria (0.15) with rich titanium blue (high blue relative to red)
    tile_b_r = np.full((H, W), 0.10, dtype=np.float32)
    tile_b_g = np.full((H, W), 0.16, dtype=np.float32)
    tile_b_b = np.full((H, W), 0.18, dtype=np.float32)
    # Inject same overlap patch
    tile_b_r[:32, :32] = overlap_r
    tile_b_g[:32, :32] = overlap_g
    tile_b_b[:32, :32] = overlap_b
    cube_b = np.stack([tile_b_r, tile_b_g, tile_b_b], axis=0)

    # 1. Process Anchor Master Tile A
    wb_a = _calculate_channel_balance(cube_a, bg_r=0.0, bg_g=0.0, bg_b=0.0, mid_val=0.13)
    hi_a = wb_a["hi_lum"]
    kr_a, kb_a = wb_a["k_r"], wb_a["k_b"]
    print(f"Panel A (Highland Master): hi_lum={hi_a:.4f}, k_r={kr_a:.3f}, k_b={kb_a:.3f}")

    # 2. Case 1: Unlocked independent processing on Tile B
    wb_b_unlocked = _calculate_channel_balance(cube_b, bg_r=0.0, bg_g=0.0, bg_b=0.0, mid_val=0.13)
    hi_b_unlocked = wb_b_unlocked["hi_lum"]
    kr_b_unlocked = wb_b_unlocked["k_r"]
    kb_b_unlocked = wb_b_unlocked["k_b"]
    print(f"Panel B (Unlocked): hi_lum={hi_b_unlocked:.4f} (stepping vs {hi_a:.4f}), k_r={kr_b_unlocked:.3f}, k_b={kb_b_unlocked:.3f}")

    # In overlap region, compare normalized luminance and color balance
    lum_overlap_a = np.mean(wb_a["lum_data"][:32, :32]) / hi_a
    lum_overlap_b_unlocked = np.mean(wb_b_unlocked["lum_data"][:32, :32]) / hi_b_unlocked
    rel_diff_unlocked = abs(lum_overlap_a - lum_overlap_b_unlocked) / lum_overlap_a
    print(f"Overlap stepping unlocked: Panel A={lum_overlap_a:.4f}, Panel B={lum_overlap_b_unlocked:.4f} (relative jump={rel_diff_unlocked*100:.1f}%)")
    if rel_diff_unlocked < 0.20:
        failures.append(f"Unlocked tiles should exhibit substantial luminance stepping jump, got {rel_diff_unlocked}")

    # 3. Case 2: Locked processing on Tile B using Profile from Tile A
    prof_a = {
        "histogram": {"hi_unified": hi_a, "hi_lum": hi_a, "midtone": 0.13},
        "white_balance": {"k_r": kr_a, "k_b": kb_a, "ratio_r": wb_a["ratio_r"], "ratio_b": wb_a["ratio_b"]},
    }
    wb_b_locked = _calculate_channel_balance(cube_b, bg_r=0.0, bg_g=0.0, bg_b=0.0, mid_val=0.13, locked_profile=prof_a)
    hi_b_locked = wb_b_locked["hi_lum"]
    kr_b_locked = wb_b_locked["k_r"]
    kb_b_locked = wb_b_locked["k_b"]

    print(f"Panel B (Locked): hi_lum={hi_b_locked:.4f}, k_r={kr_b_locked:.3f}, k_b={kb_b_locked:.3f}")
    if abs(hi_b_locked - hi_a) > 1e-6:
        failures.append(f"Locked hi_lum did not match anchor: {hi_b_locked} vs {hi_a}")
    if abs(kr_b_locked - kr_a) > 1e-6 or abs(kb_b_locked - kb_a) > 1e-6:
        failures.append(f"Locked gains did not match anchor: ({kr_b_locked},{kb_b_locked}) vs ({kr_a},{kb_a})")

    lum_overlap_b_locked = np.mean(wb_b_locked["lum_data"][:32, :32]) / hi_b_locked
    rel_diff_locked = abs(lum_overlap_a - lum_overlap_b_locked) / lum_overlap_a
    print(f"Overlap stepping locked: Panel A={lum_overlap_a:.4f}, Panel B={lum_overlap_b_locked:.4f} (relative jump={rel_diff_locked*100:.4f}%)")
    if rel_diff_locked > 1e-4:
        failures.append(f"Locked tiles still exhibit luminance stepping discontinuity: {rel_diff_locked}")

    # 4. Test profile file load & recovery
    tmp_dir = Path(tempfile.mkdtemp(prefix="moon_prof_test_"))
    try:
        prof_file = tmp_dir / "lunar_profile.json"
        dump_json(prof_file, prof_a)
        loaded_prof, src_path = _load_locked_profile(lock_profile_path=str(prof_file))
        if loaded_prof is None or loaded_prof["histogram"]["hi_lum"] != hi_a:
            failures.append("Failed to load profile from explicit path")

        # Test loading from directory containing mosaic_tile_info.json
        tile_info = {
            "mosaic_mode": "tile",
            "calibration_profile": prof_a,
        }
        dump_json(tmp_dir / "mosaic_tile_info.json", tile_info)
        prof_file.unlink()  # remove lunar_profile.json to test fallback
        loaded_dir_prof, src_dir = _load_locked_profile(lock_from_dir=str(tmp_dir))
        if loaded_dir_prof is None or loaded_dir_prof["histogram"]["hi_lum"] != hi_a:
            failures.append("Failed to load profile from anchor directory mosaic_tile_info.json fallback")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print("Histogram and Color Lock pipeline unit test passed")
    assert not failures, f"Failures in test_histogram_color_lock_pipeline: {failures}"


def test_infer_optical_parameters_math() -> None:
    failures = []
    import argparse
    from astropy.io import fits

    # 1. Test geometric inversion from synthetic lunar disc
    # Let R = 1045.8 px => D = 2091.6 px. With pixel = 3.73um:
    # sensor_dia = 2091.6 * 0.00373 = 7.801668 mm
    # theta_mean = 0.009037905 rad
    # f_est = 7.801668 / (2 * tan(theta_mean / 2)) = 863.2 mm ~ 864 mm
    h, w = 2400, 2400
    yy, xx = np.mgrid[0:h, 0:w]
    xc_syn, yc_syn = 1200.0, 1200.0
    R_syn = 1045.8
    dist = np.sqrt((xx - xc_syn)**2 + (yy - yc_syn)**2)

    syn_img = np.zeros((h, w), dtype=np.float32)
    syn_img[dist <= R_syn] = 0.5 + 0.1 * np.cos(dist[dist <= R_syn] / 15.0)
    falloff = (dist > R_syn) & (dist <= R_syn + 5.0)
    syn_img[falloff] = 0.5 * (1.0 - (dist[falloff] - R_syn) / 5.0)

    hdr = fits.Header()
    hdr["XPIXSZ"] = 3.73000

    args_default = argparse.Namespace(focal=None, pixel_size=None, aperture=None)
    opt = _infer_optical_parameters(hdr, syn_img, args_default, mosaic_mode="disc")

    print(f"Inferred optics: fl={opt['focal_length']}mm ({opt['focal_source']}), px={opt['pixel_size']}um ({opt['pixel_size_source']}), F/{opt['f_ratio']}, Airy={opt['airy_radius_px']}px")

    if not (850.0 <= opt["focal_length"] <= 875.0):
        failures.append(f"Geometric inversion focal length unexpected: {opt['focal_length']}mm (expected ~864mm)")
    if "geometric inversion" not in opt["focal_source"]:
        failures.append(f"Expected geometric inversion source, got {opt['focal_source']}")
    if opt["pixel_size"] != 3.73:
        failures.append(f"Expected pixel_size 3.73 from Header, got {opt['pixel_size']}")
    if not (1.8 <= opt["airy_radius_px"] <= 2.1):
        failures.append(f"Airy radius unexpected: {opt['airy_radius_px']}px (expected ~1.94px)")

    # 2. Test user override
    args_override = argparse.Namespace(focal=600.0, pixel_size=2.9, aperture=100.0)
    opt_ovr = _infer_optical_parameters(hdr, syn_img, args_override, mosaic_mode="disc")
    if opt_ovr["focal_length"] != 600.0 or opt_ovr["focal_source"] != "user" or opt_ovr["aperture"] != 100.0 or opt_ovr["pixel_size"] != 2.9:
        failures.append(f"User override failed: {opt_ovr}")

    # 3. Test mosaic tile mode bypass
    hdr_with_focal = fits.Header()
    hdr_with_focal["FOCALLEN"] = 750.0
    hdr_with_focal["XPIXSZ"] = 3.73
    opt_tile = _infer_optical_parameters(hdr_with_focal, syn_img, args_default, mosaic_mode="tile")
    if opt_tile["focal_length"] != 750.0 or "FITS Header" not in opt_tile["focal_source"]:
        failures.append(f"Tile mode should bypass geometric inversion and use Header FOCALLEN: {opt_tile}")

    # 4. Test pure default fallback (empty header, tile mode)
    hdr_empty = fits.Header()
    opt_fallback = _infer_optical_parameters(hdr_empty, np.zeros((100, 100)), args_default, mosaic_mode="tile")
    if opt_fallback["focal_length"] != 400.0 or opt_fallback["pixel_size"] != 3.73:
        failures.append(f"Default fallback failed: {opt_fallback}")

    print("Optical parameter inference unit test passed")
    assert not failures, f"Failures in test_infer_optical_parameters_math: {failures}"


def _make_synthetic_ser(
    path: Path,
    width: int = 64,
    height: int = 64,
    num_frames: int = 4,
    color_id: int = 0,
    pixel_depth: int = 16,
    little_endian: int = 1,
    observer: str = "TestObserver",
    instrument: str = "ZWO ASI585MC",
    telescope: str = "Seestar S50",
    date_time_utc: int = 638628000000000000,
) -> Path:
    """Generate a valid binary SER video container for testing."""
    import struct
    fmt = "<14s7i40s40s40s2q"
    hdr = struct.pack(
        fmt,
        b"LUCAM-RECORDER",
        0,  # LuID
        color_id,
        little_endian,
        width,
        height,
        pixel_depth,
        num_frames,
        observer.encode("utf-8").ljust(40, b"\x00"),
        instrument.encode("utf-8").ljust(40, b"\x00"),
        telescope.encode("utf-8").ljust(40, b"\x00"),
        0,
        date_time_utc,
    )
    bytes_per_sample = 1 if pixel_depth <= 8 else 2
    plane_count = 3 if color_id in (100, 101) else 1

    frames = []
    rng = np.random.default_rng(12345)
    for i in range(num_frames):
        if pixel_depth <= 8:
            arr = rng.integers(10, 240, size=(height, width, plane_count) if plane_count > 1 else (height, width), dtype=np.uint8)
        else:
            arr = rng.integers(1000, 60000, size=(height, width, plane_count) if plane_count > 1 else (height, width), dtype=np.uint16)
        frames.append(arr.tobytes())

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hdr + b"".join(frames))
    return path


def test_ser_header_parser():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.ser"
        _make_synthetic_ser(p, width=128, height=96, num_frames=5, color_id=8, pixel_depth=16, instrument="ASI678MC")
        info = parse_ser_header(p)
        assert info["file_id"] == "LUCAM-RECORDER"
        assert info["width"] == 128
        assert info["height"] == 96
        assert info["frame_count"] == 5
        assert info["color_id"] == 8
        assert info["is_bayer"] is True
        assert info["bayer_pattern"] == "RGGB"
        assert info["instrument"] == "ASI678MC"
        assert info["date_obs"] is not None
        assert "2024" in info["date_obs"]

        # Check invalid header
        bad_p = Path(td) / "bad.ser"
        bad_p.write_bytes(b"INVALID_HEADER_DATA" * 10)
        try:
            parse_ser_header(bad_p)
            assert False, "Should raise ValueError on invalid FileID"
        except ValueError:
            pass
    print("SER header parser unit test passed")


def test_ser_unpack_mono():
    from astropy.io import fits
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ser_file = Path(td) / "test_mono.ser"
        _make_synthetic_ser(ser_file, width=64, height=48, num_frames=3, color_id=0, pixel_depth=16, instrument="ASI174MM")
        out_dir = Path(td) / "unpacked"
        res = unpack_ser_to_fits(ser_file, out_dir, seq_name="test_moon_", limit=2)

        assert res["extracted_frames"] == 2
        assert res["channels"] == 1
        f1 = out_dir / "test_moon_00001.fit"
        f2 = out_dir / "test_moon_00002.fit"
        assert f1.exists() and f2.exists()
        assert not (out_dir / "test_moon_00003.fit").exists()

        with fits.open(f1) as hdul:
            hdr = hdul[0].header
            data = hdul[0].data
            assert data.shape == (48, 64)
            assert hdr["NAXIS"] == 2
            assert hdr["INSTRUME"] == "ASI174MM"
    print("SER monochrome unpack unit test passed")


def test_ser_unpack_bayer():
    from astropy.io import fits
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        ser_file = Path(td) / "test_bayer.ser"
        _make_synthetic_ser(ser_file, width=64, height=48, num_frames=3, color_id=8, pixel_depth=16)
        out_dir = Path(td) / "unpacked_bayer"
        res = unpack_ser_to_fits(ser_file, out_dir, seq_name="test_bayer_", debayer=True)

        assert res["extracted_frames"] == 3
        assert res["channels"] == 3
        f1 = out_dir / "test_bayer_00001.fit"
        assert f1.exists()

        with fits.open(f1) as hdul:
            hdr = hdul[0].header
            data = hdul[0].data
            assert data.shape == (3, 48, 64)
            assert hdr["NAXIS"] == 3
    print("SER Bayer demosaicing unpack unit test passed")


def test_ser_cmd_import_single_file_and_dir():
    import argparse
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td)
        # Test 1: single file input (Mono)
        ser1 = p / "captures" / "lunar_mono.ser"
        _make_synthetic_ser(ser1, width=64, height=48, num_frames=4, color_id=0, pixel_depth=16)
        work1 = p / "work_mono"

        args1 = argparse.Namespace(
            input=str(ser1),
            work=str(work1),
            format="auto",
            limit=0,
            ser_debayer=True,
            siril=DEFAULT_SIRIL,
            timeout=30,
        )
        cmd_import(args1)

        seq_file1 = work1 / "moon_.seq"
        receipt1 = work1 / "import_receipt.json"
        assert seq_file1.exists()
        assert receipt1.exists()
        seq_text = seq_file1.read_text()
        assert "L 1" in seq_text
        r_data1 = json.loads(receipt1.read_text())
        assert r_data1["is_ser"] is True
        assert r_data1["channels"] == 1
        assert r_data1["frame_count"] == 4

        # Test 2: directory input (Bayer RGGB)
        cap_dir = p / "dir_bayer"
        ser2 = cap_dir / "lunar_color.ser"
        _make_synthetic_ser(ser2, width=64, height=48, num_frames=3, color_id=8, pixel_depth=16)
        work2 = p / "work_color"

        args2 = argparse.Namespace(
            input=str(cap_dir),
            work=str(work2),
            format="ser",
            limit=2,
            ser_debayer=True,
            siril=DEFAULT_SIRIL,
            timeout=30,
        )
        cmd_import(args2)

        seq_file2 = work2 / "moon_.seq"
        receipt2 = work2 / "import_receipt.json"
        assert seq_file2.exists()
        assert "L 3" in seq_file2.read_text()
        r_data2 = json.loads(receipt2.read_text())
        assert r_data2["is_ser"] is True
        assert r_data2["channels"] == 3
        assert r_data2["frame_count"] == 2
    print("SER cmd_import single file and directory integration unit test passed")


def test_extinction_gradient_synthetic():
    """Verify robust recovery of zenith extinction angle and planar slope, and compensation flattening."""
    import math
    moon = make_synthetic_moon(h=256, w=256, seed=123)
    p999 = float(np.percentile(moon, 99.95))
    mask = (moon > 0.05 * p999) & (moon < 0.95 * p999)

    h, w = moon.shape
    xc, yc = w / 2.0, h / 2.0
    r_norm = 0.5 * math.hypot(w, h)
    yy, xx = np.mgrid[0:h, 0:w]
    nx = (xx - xc) / r_norm
    ny = (yy - yc) / r_norm

    # True extinction tilt: Zenith at 45 degrees (upper-right)
    true_gx_b, true_gy_b = 0.05, 0.05
    true_gx_r, true_gy_r = -0.02, -0.02

    t_b = np.exp(true_gx_b * nx + true_gy_b * ny)
    t_r = np.exp(true_gx_r * nx + true_gy_r * ny)

    g_net = moon.copy()
    b_net = (moon * t_b).astype(np.float32)
    r_net = (moon * t_r).astype(np.float32)

    net_planes = np.stack([r_net, g_net, b_net], axis=0)
    info = _estimate_atmospheric_extinction_gradient(net_planes, mask, mode="auto")

    assert info["active"] is True
    assert info["applied"] is True
    # Verify recovered zenith angle is approx 45 degrees
    assert abs(info["zenith_angle_deg"] - 45.0) < 5.0, f"Recovered angle {info['zenith_angle_deg']} vs 45.0"
    # Verify blue amplitude: 2 * sqrt(0.05^2 + 0.05^2) = 2 * 0.0707 = 0.1414
    assert abs(info["amp_b"] - 0.1414) < 0.02, f"Recovered amp_b {info['amp_b']} vs 0.1414"
    assert info["confidence"] >= 0.8

    # Apply planar compensation
    r_corr, g_corr, b_corr = _apply_extinction_compensation(r_net, g_net, b_net, info, mask)

    # In raw tilted image, log(B/G) has significant spatial slope:
    raw_l_bg = np.log(b_net[mask] / g_net[mask])
    corr_l_bg = np.log(b_corr[mask] / g_corr[mask])
    assert corr_l_bg.std() < 0.35 * raw_l_bg.std(), f"STD after compensation {corr_l_bg.std()} vs raw {raw_l_bg.std()}"
    print("Synthetic atmospheric extinction gradient recovery & compensation unit test passed")


def test_extinction_gradient_flat_bypass():
    """Verify that flat/high-altitude moon automatically bypasses extinction compensation."""
    moon = make_synthetic_moon(h=256, w=256, seed=456)
    p999 = float(np.percentile(moon, 99.95))
    mask = (moon > 0.05 * p999) & (moon < 0.95 * p999)

    # Natural balanced channels (no extinction slope)
    net_planes = np.stack([moon.copy(), moon.copy(), moon.copy()], axis=0)
    info = _estimate_atmospheric_extinction_gradient(net_planes, mask, mode="auto")

    assert info["active"] is True
    assert info["applied"] is False, f"Expected bypass on flat moon, but applied={info['applied']} (amp_b={info.get('amp_b')})"
    print("Extinction gradient flat bypass unit test passed")


def test_extinction_geological_immunity():
    """Verify that localized mare/highland color patches do not create false macroscopic extinction slopes."""
    moon = make_synthetic_moon(h=256, w=256, seed=789)
    p999 = float(np.percentile(moon, 99.95))
    mask = (moon > 0.05 * p999) & (moon < 0.95 * p999)

    r_net = moon.copy()
    g_net = moon.copy()
    b_net = moon.copy()

    # Add localized titanium mare patch on the left side (high Blue)
    b_net[100:150, 60:110] *= 1.15
    # Add localized iron highland patch on the right side (high Red)
    r_net[100:150, 150:200] *= 1.15

    net_planes = np.stack([r_net, g_net, b_net], axis=0)
    info = _estimate_atmospheric_extinction_gradient(net_planes, mask, mode="auto")

    # The robust IRLS fit should suppress these local patches and not declare a large extinction gradient
    assert info["amp_b"] < 0.035, f"Huber should suppress local patches, but got amp_b={info['amp_b']}"
    print("Extinction geological immunity unit test passed")


def make_synthetic_avi(path: Path, num_frames: int = 5, h: int = 120, w: int = 160, is_color: bool = True) -> Path:
    """Generate a valid AVI video using OpenCV VideoWriter for testing."""
    import cv2
    path.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    out = cv2.VideoWriter(str(path), fourcc, 15.0, (w, h), isColor=is_color)
    for i in range(num_frames):
        img = np.zeros((h, w, 3) if is_color else (h, w), dtype=np.uint8)
        # Draw simulated moon disc with craters
        cv2.circle(img, (w // 2, h // 2), min(h, w) // 3 + i, (180, 180, 180) if is_color else 180, -1)
        if is_color:
            # Blue-tinted mare patch and reddish highland
            cv2.circle(img, (w // 2 - 15, h // 2 - 10), 12, (220, 160, 120), -1)
            cv2.circle(img, (w // 2 + 15, h // 2 + 10), 10, (120, 150, 210), -1)
        out.write(img)
    out.release()
    return path


def test_video_unpack_synthetic_avi():
    """Verify direct extraction of AVI frames to 16-bit FITS with metadata."""
    import tempfile
    from astropy.io import fits

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        avi_file = tmp_dir / "test_moon.avi"
        make_synthetic_avi(avi_file, num_frames=6, h=64, w=80, is_color=True)

        out_fits = tmp_dir / "extracted_fits"
        info = unpack_video_to_fits(avi_file, out_fits, seq_name="test_v_", limit=4)

        assert info["extracted_frames"] == 4, f"Expected 4 extracted frames (limit=4), got {info['extracted_frames']}"
        assert info["channels"] == 3, f"Expected 3 channels, got {info['channels']}"
        assert info["orig_bit_depth"] == 8, f"Expected orig_bit_depth=8, got {info['orig_bit_depth']}"

        # Inspect first generated FITS frame
        fit_path = out_fits / "test_v_00001.fit"
        assert fit_path.exists(), "FITS frame test_v_00001.fit does not exist"

        with fits.open(fit_path, memmap=False) as hdul:
            data = hdul[0].data
            hdr = hdul[0].header
            assert data.shape == (3, 64, 80), f"Expected FITS shape (3, 64, 80), got {data.shape}"
            assert hdr.get("BITPIX") == 16, f"Expected BITPIX=16, got {hdr.get('BITPIX')}"
            assert hdr.get("ORIG_BIT") == 8, f"Expected ORIG_BIT=8, got {hdr.get('ORIG_BIT')}"
            assert hdr.get("SRC_FMT") == "AVI", f"Expected SRC_FMT=AVI, got {hdr.get('SRC_FMT')}"
            # Check 16-bit dynamic expansion: maximum value should be normalized to uint16 range (> 255)
            assert data.max() > 255, f"Expected dynamic expansion > 255, got {data.max()}"

    print("Synthetic AVI unpack to 16-bit FITS unit test passed")


def test_video_cmd_import_single_file_and_dir():
    """Verify cmd_import handles both a single video file and a directory containing video files."""
    import argparse
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        v_dir = tmp_dir / "video_source"
        v_dir.mkdir(parents=True, exist_ok=True)
        v_path1 = v_dir / "clip1.avi"
        make_synthetic_avi(v_path1, num_frames=5, h=64, w=80, is_color=True)

        work1 = tmp_dir / "work_single"
        # Test A: single video file as --input
        args1 = argparse.Namespace(
            input=str(v_path1),
            work=str(work1),
            format="auto",
            limit=3,
            ser_debayer=True,
            force_mono=False,
            siril=DEFAULT_SIRIL,
            timeout=60,
        )
        cmd_import(args1)

        seq_file1 = work1 / "moon_.seq"
        assert seq_file1.exists(), f"Sequence file {seq_file1} was not generated"
        seq_text1 = seq_file1.read_text(encoding="utf-8")
        assert "L 3" in seq_text1, f"Expected 'L 3' in seq text, got:\n{seq_text1}"
        assert (work1 / "moon_00003.fit").exists(), "Frame 3 was not created"
        assert not (work1 / "moon_00004.fit").exists(), "Frame 4 should not exist due to limit=3"

        rcpt1 = json.loads((work1 / "import_receipt.json").read_text(encoding="utf-8"))
        assert rcpt1["is_video"] is True, f"Expected is_video=True, got {rcpt1.get('is_video')}"
        assert rcpt1["orig_bit_depth"] == 8, f"Expected orig_bit_depth=8, got {rcpt1.get('orig_bit_depth')}"
        assert rcpt1["frame_count"] == 3

        # Test B: directory containing video as --input
        work2 = tmp_dir / "work_dir"
        args2 = argparse.Namespace(
            input=str(v_dir),
            work=str(work2),
            format="video",
            limit=0,
            ser_debayer=True,
            force_mono=True,  # force mono
            siril=DEFAULT_SIRIL,
            timeout=60,
        )
        cmd_import(args2)

        seq_file2 = work2 / "moon_.seq"
        seq_text2 = seq_file2.read_text(encoding="utf-8")
        assert "L 1" in seq_text2, f"Expected 'L 1' for forced mono, got:\n{seq_text2}"
        rcpt2 = json.loads((work2 / "import_receipt.json").read_text(encoding="utf-8"))
        assert rcpt2["channels"] == 1
        assert rcpt2["frame_count"] == 5

    print("Video cmd_import single file and directory integration unit test passed")


def test_video_two_pass_selection():
    """Verify smart-top and smart-cluster video selection pick the highest seeing frames across time."""
    import argparse
    import cv2
    import json
    import tempfile
    from astropy.io import fits

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        v_path = tmp_dir / "seeing_trend.avi"

        # Generate a 12-frame video where frames 8..11 have sharp high-contrast grid texture,
        # and frames 0..3 are blurry/smooth
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        out = cv2.VideoWriter(str(v_path), fourcc, 15.0, (120, 120), isColor=True)
        for i in range(12):
            img = np.full((120, 120, 3), 100, dtype=np.uint8)
            # Center moon disc
            cv2.circle(img, (60, 60), 40, (180, 180, 180), -1)
            # Contrast/texture increases with frame index: late frames have high frequency checkered texture
            if i >= 8:
                for y in range(40, 80, 4):
                    for x in range(40, 80, 4):
                        if (x + y) % 8 == 0:
                            img[y : y + 2, x : x + 2] = 250
            elif i >= 4:
                for y in range(45, 75, 8):
                    for x in range(45, 75, 8):
                        img[y : y + 4, x : x + 4] = 220
            out.write(img)
        out.release()

        # 1. Test smart-top with limit=4
        work_top = tmp_dir / "work_top"
        args_top = argparse.Namespace(
            input=str(v_path),
            work=str(work_top),
            format="video",
            limit=4,
            sample_mode="smart-top",
            probe_stride=1,
            start_frame=0,
            ser_debayer=True,
            force_mono=False,
            video_debayer="auto",
            siril=DEFAULT_SIRIL,
            timeout=60,
        )
        cmd_import(args_top)

        rcpt_top = json.loads((work_top / "import_receipt.json").read_text(encoding="utf-8"))
        assert rcpt_top["frame_count"] == 4
        assert "seeing_probe" in rcpt_top["video_metadata"]
        probe_meta = rcpt_top["video_metadata"]["seeing_probe"]
        assert probe_meta["sample_mode"] == "smart-top"
        assert probe_meta["chosen_frame_count"] == 4

        # Read SRC_FRM from extracted FITS headers: smart-top should pick from frames 8..11!
        extracted_frms = []
        for i in range(1, 5):
            fit_f = work_top / f"moon_{i:05d}.fit"
            assert fit_f.exists()
            with fits.open(fit_f) as hdul:
                extracted_frms.append(hdul[0].header["SRC_FRM"])

        print(f"Smart-top picked source frames: {extracted_frms}")
        assert set(extracted_frms) == {8, 9, 10, 11}, f"Expected top frames {8,9,10,11}, got {extracted_frms}"

        # 2. Test smart-cluster with limit=4
        work_cluster = tmp_dir / "work_cluster"
        args_cluster = argparse.Namespace(
            input=str(v_path),
            work=str(work_cluster),
            format="video",
            limit=4,
            sample_mode="smart-cluster",
            probe_stride=1,
            start_frame=0,
            ser_debayer=True,
            force_mono=False,
            video_debayer="auto",
            siril=DEFAULT_SIRIL,
            timeout=60,
        )
        cmd_import(args_cluster)

        rcpt_cluster = json.loads((work_cluster / "import_receipt.json").read_text(encoding="utf-8"))
        assert rcpt_cluster["frame_count"] == 4
        cluster_frms = []
        for i in range(1, 5):
            fit_f = work_cluster / f"moon_{i:05d}.fit"
            with fits.open(fit_f) as hdul:
                cluster_frms.append(hdul[0].header["SRC_FRM"])

        print(f"Smart-cluster picked source frames: {cluster_frms}")
        # Cluster should span multiple time windows, not just 8..11
        assert min(cluster_frms) < 8, f"Cluster should span earlier windows too: {cluster_frms}"

    print("Video two-pass seeing probe and smart selection unit test passed")



def test_video_8bit_stack_policy():
    """Verify that 8-bit video automatically triggers 'stack sum' stacking policy."""
    import argparse
    import json
    import tempfile
    from unittest.mock import patch

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        work = tmp_dir / "work_stack"
        work.mkdir(parents=True, exist_ok=True)
        (work / "logs").mkdir(parents=True, exist_ok=True)

        # Create dummy sequence and import_receipt with orig_bit_depth=8
        seq_file = work / "moon_.seq"
        seq_file.write_text("S 'moon_' 1 2 2 5 -1 6 0 0 0\nL 3\nI 1 1\nI 2 1\n", encoding="utf-8")
        rcpt = {"is_video": True, "orig_bit_depth": 8, "frame_count": 2}
        (work / "import_receipt.json").write_text(json.dumps(rcpt), encoding="utf-8")

        captured_lines = []

        def mock_run_siril(siril, script_lines, cwd, log_path, timeout):
            captured_lines.extend(script_lines)
            (work / "moon_master.fit").touch()
            return {"exit_code": 0, "log": str(log_path), "seconds": 0.1, "output": "ok"}

        args = argparse.Namespace(
            work=str(work),
            seq="moon_",
            out="moon_master.fit",
            framing=None,
            mosaic_mode="disc",
            interp="cu",
            sigma=["3", "3"],
            norm="addscale",
            stack_method="auto",
            siril=DEFAULT_SIRIL,
            timeout=60,
        )

        with patch("moon_stack.run_siril_script", side_effect=mock_run_siril):
            cmd_stack(args)

        script_str = "\n".join(captured_lines)
        assert "stack r_moon_ sum -filter-included" in script_str, (
            f"Expected 'stack r_moon_ sum' for 8-bit video source, got:\n{script_str}"
        )

    print("8-bit video automatic 'stack sum' policy unit test passed")


def test_nonstandard_video_diagnostics():
    """Verify detailed diagnostic reports and actionable hints for non-standard / corrupted videos."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)

        # 1. Test empty video file (0 bytes)
        empty_mp4 = tmp_dir / "zero_bytes.mp4"
        empty_mp4.touch()
        err_msg_empty = format_video_decode_error(empty_mp4, failure_stage="open_container")
        assert "0 B" in err_msg_empty, "Expected '0 B' in file size report"
        assert "空文件" in err_msg_empty or "0 字节" in err_msg_empty, "Expected empty file root cause"
        assert "重新导出" in err_msg_empty or "拷贝" in err_msg_empty, "Expected actionable export advice"

        # 2. Test MP4 missing moov atom
        bad_mp4 = tmp_dir / "missing_moov.mp4"
        bad_mp4.write_bytes(b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41\x00\x00\x10\x00mdat" + b"\x00" * 5000)
        err_msg_moov = format_video_decode_error(bad_mp4, failure_stage="open_container")
        assert "moov" in err_msg_moov.lower(), "Expected 'moov' atom diagnosis"
        assert "ffmpeg -err_detect ignore_err" in err_msg_moov, "Expected ffmpeg repair command in advice"

        # 3. Test HEVC / H.265 FourCC detection
        hevc_avi = tmp_dir / "sample_hevc.avi"
        hevc_header = (
            b"RIFF\x00\x10\x00\x00AVI LIST\x00\x08\x00\x00hdrlavih"
            b"\x00\x00\x00\x00LIST\x00\x04\x00\x00strlvidsH265"
            + b"\x00" * 2000
        )
        hevc_avi.write_bytes(hevc_header)
        err_msg_hevc = format_video_decode_error(hevc_avi, failure_stage="open_container")
        assert "H.265" in err_msg_hevc or "HEVC" in err_msg_hevc, "Expected HEVC diagnosis"
        assert "ffmpeg -i" in err_msg_hevc, "Expected ffmpeg transcoding command"

        # 4. Test SER file disguised as .mp4
        fake_ser = tmp_dir / "disguised_ser.mp4"
        fake_ser.write_bytes(b"LUCAM-RECORDER\x00\x00" + b"\x00" * 200)
        err_msg_ser = format_video_decode_error(fake_ser, failure_stage="open_container")
        assert "SER" in err_msg_ser, "Expected SER format identification"
        assert "--format ser" in err_msg_ser, "Expected suggestion to use --format ser"

        # 5. Test FITS file disguised as .avi
        fake_fits = tmp_dir / "disguised_fits.avi"
        fake_fits.write_bytes(b"SIMPLE  =                    T / Standard FITS format\n" + b" " * 2800)
        err_msg_fits = format_video_decode_error(fake_fits, failure_stage="open_container")
        assert "FITS" in err_msg_fits, "Expected FITS format identification"
        assert "--format fits" in err_msg_fits, "Expected suggestion to use --format fits"

        # 6. Test unpack_video_to_fits raises ValueError with full diagnostic on 0-byte file
        try:
            unpack_video_to_fits(empty_mp4, tmp_dir / "fits_out")
            assert False, "Expected unpack_video_to_fits to raise ValueError"
        except ValueError as exc:
            assert "视频解码诊断报告" in str(exc)
            assert "0 B" in str(exc)

    print("Non-standard video decoding diagnostics & recovery guidance unit test passed")


def test_adaptive_midtone_analytical_solution():
    """Verify that _estimate_adaptive_midtone analytically solves MTF mapping accurately."""
    from moon_stack import _estimate_adaptive_midtone

    # Synthetic lunar surface with known distribution
    rng = np.random.default_rng(101)
    vals = rng.normal(0.35, 0.08, size=(100, 100)).astype(np.float32)
    vals = np.clip(vals, 0.05, 0.95)

    res = _estimate_adaptive_midtone(vals, hi_val=1.0, target_mare_lum=0.50)
    m = res["midtone"]
    x_med = res["median_raw"]
    y_mapped = ((m - 1.0) * x_med) / ((2.0 * m - 1.0) * x_med - m)
    assert abs(y_mapped - res["target"]) < 0.02, f"MTF mapping error: y_mapped={y_mapped}, expected target={res['target']}"

    # Dark / crescent moon test: x_med = 0.15
    dark_vals = np.clip(rng.normal(0.15, 0.04, size=(100, 100)), 0.02, 0.90).astype(np.float32)
    res_dark = _estimate_adaptive_midtone(dark_vals, hi_val=1.0, target_mare_lum=0.50)
    m_dark = res_dark["midtone"]
    y_dark = ((m_dark - 1.0) * res_dark["median_raw"]) / ((2.0 * m_dark - 1.0) * res_dark["median_raw"] - m_dark)
    assert abs(y_dark - res_dark["target"]) < 0.02, f"Dark MTF mapping error: y_dark={y_dark}, expected target={res_dark['target']}"
    print("Adaptive midtone analytical solution unit test passed")


def test_lunar_limb_circle_fits_linear_raw():
    """Verify that _fit_lunar_limb_circle works with un-stretched linear FITS with pedestal."""
    from moon_stack import _fit_lunar_limb_circle

    H, W = 512, 512
    pedestal = 0.0040
    data = np.full((H, W), pedestal, dtype=np.float32)
    yy, xx = np.mgrid[:H, :W]
    r = np.sqrt((xx - 256.0)**2 + (yy - 256.0)**2)

    moon_signal = 0.0080
    edge_t = np.clip((r - 160.0) / 4.0, 0.0, 1.0)
    data += moon_signal * (1.0 - edge_t)

    rng = np.random.default_rng(42)
    data += rng.normal(0, 0.0001, size=(H, W)).astype(np.float32)

    fit_res = _fit_lunar_limb_circle(data)
    assert fit_res is not None, "Limb circle fitting failed on linear raw data!"
    xc, yc, R, res_std = fit_res
    assert abs(xc - 256.0) < 3.0, f"xc error: {xc} vs 256.0"
    assert abs(yc - 256.0) < 3.0, f"yc error: {yc} vs 256.0"
    assert abs(R - 162.0) < 4.0, f"R error: {R} vs 162.0"
    print(f"Limb circle fit on linear raw: center=({xc:.1f}, {yc:.1f}), R={R:.1f} (std={res_std:.2f}) unit test passed")


def test_radial_psd_seeing_cutoff():
    """Verify that _estimate_seeing_cutoff discriminates between sharp and blurred textures."""
    import cv2
    from moon_stack import _estimate_seeing_cutoff

    rng = np.random.default_rng(42)
    base = rng.normal(0.5, 0.1, size=(256, 256)).astype(np.float32)
    sharp_cutoff = _estimate_seeing_cutoff(base, patch_size=256)

    blurred = cv2.GaussianBlur(base, (15, 15), 5.0)
    blurred_cutoff = _estimate_seeing_cutoff(blurred, patch_size=256)

    assert sharp_cutoff > blurred_cutoff, f"Expected sharp ({sharp_cutoff}) > blurred ({blurred_cutoff})"
    print(f"Radial PSD seeing cutoff: sharp={sharp_cutoff:.2f}, blurred={blurred_cutoff:.2f} unit test passed")


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
        ("test_adaptive_midtone_analytical_solution", test_adaptive_midtone_analytical_solution),
        ("test_lunar_limb_circle_fits_linear_raw", test_lunar_limb_circle_fits_linear_raw),
        ("test_radial_psd_seeing_cutoff", test_radial_psd_seeing_cutoff),
        ("test_anti_ringing_damping_math", test_anti_ringing_damping_math),
        ("test_dark_halo_ratio_metric", test_dark_halo_ratio_metric),
        ("test_anti_brittle_metrics_math", test_anti_brittle_metrics_math),
        ("test_mosaic_tile_mode_pipeline", test_mosaic_tile_mode_pipeline),
        ("test_histogram_color_lock_pipeline", test_histogram_color_lock_pipeline),
        ("test_infer_optical_parameters_math", test_infer_optical_parameters_math),
        ("test_ser_header_parser", test_ser_header_parser),
        ("test_ser_unpack_mono", test_ser_unpack_mono),
        ("test_ser_unpack_bayer", test_ser_unpack_bayer),
        ("test_ser_cmd_import_single_file_and_dir", test_ser_cmd_import_single_file_and_dir),
        ("test_video_unpack_synthetic_avi", test_video_unpack_synthetic_avi),
        ("test_video_cmd_import_single_file_and_dir", test_video_cmd_import_single_file_and_dir),
        ("test_video_two_pass_selection", test_video_two_pass_selection),
        ("test_video_8bit_stack_policy", test_video_8bit_stack_policy),
        ("test_nonstandard_video_diagnostics", test_nonstandard_video_diagnostics),
        ("test_extinction_gradient_synthetic", test_extinction_gradient_synthetic),
        ("test_extinction_gradient_flat_bypass", test_extinction_gradient_flat_bypass),
        ("test_extinction_geological_immunity", test_extinction_geological_immunity),
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
