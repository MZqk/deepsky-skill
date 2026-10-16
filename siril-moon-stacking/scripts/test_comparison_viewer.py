#!/usr/bin/env python3
"""Tests for the interactive HTML dual-window comparison viewer.

Verifies:
  1. Reference frame isometric rendering (_render_reference_frame)
  2. HTML report generator structure and asset links (_generate_comparison_html)
  3. Metric badges and HUD payload embedding
  4. Fallback handling when reference or metrics are missing
  5. CLI --html-report / --no-html-report argument parsing
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from astropy.io import fits

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from moon_stack import (
    _apply_adaptive_sky_margins,
    _generate_comparison_html,
    _render_reference_frame,
    _video_crop,
    build_parser,
)



@pytest.fixture
def temp_work_dir():
    d = Path(tempfile.mkdtemp(prefix="test_moon_viewer_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _create_synthetic_fits(path: Path, shape=(256, 256), dtype=np.uint16, channels=1):
    """Create a synthetic lunar test FITS file."""
    if channels == 1:
        data = np.full(shape, 8000, dtype=dtype)
        # Add a circular bright disk
        yy, xx = np.ogrid[:shape[0], :shape[1]]
        mask = (xx - shape[1] // 2) ** 2 + (yy - shape[0] // 2) ** 2 < (shape[0] // 3) ** 2
        data[mask] = 35000
    else:
        data = np.full((3, shape[0], shape[1]), 8000, dtype=dtype)
        yy, xx = np.ogrid[:shape[0], :shape[1]]
        mask = (xx - shape[1] // 2) ** 2 + (yy - shape[0] // 2) ** 2 < (shape[0] // 3) ** 2
        data[0][mask] = 36000  # R
        data[1][mask] = 34000  # G
        data[2][mask] = 32000  # B

    hdu = fits.PrimaryHDU(data)
    hdu.header["XPIXSZ"] = 2.9
    hdu.header["FOCALLEN"] = 250.0
    hdu.header["TELESCOP"] = "Seestar S50"
    hdu.writeto(str(path), overwrite=True)
    return path


def test_render_reference_frame_mono(temp_work_dir):
    work = temp_work_dir
    ref_fit = work / "moon_00005.fit"
    _create_synthetic_fits(ref_fit, shape=(128, 128), channels=1)

    ranking = {"reference_index": 5, "registered_frames": 20}
    (work / "ranking.json").write_text(json.dumps(ranking))

    profile = {
        "histogram": {"bg_lum": 0.05, "hi_lum": 0.75, "midtone": 0.15},
        "white_balance": {"k_r": 1.0, "k_b": 1.0},
    }

    out_jpg = _render_reference_frame(work, active_profile=profile)
    assert out_jpg is not None
    assert out_jpg.exists()
    assert out_jpg.name == "moon_reference.jpg"

    # Verify image is valid and readable by OpenCV
    img = cv2.imread(str(out_jpg))
    assert img is not None
    assert img.shape[:2] == (128, 128)


def test_render_reference_frame_rgb(temp_work_dir):
    work = temp_work_dir
    ref_fit = work / "moon_00002.fit"
    _create_synthetic_fits(ref_fit, shape=(100, 100), channels=3)

    ranking = {"reference_index": 2, "registered_frames": 15}
    (work / "ranking.json").write_text(json.dumps(ranking))

    profile = {
        "histogram": {"bg_r": 0.02, "bg_g": 0.02, "bg_b": 0.02, "hi_lum": 0.8, "midtone": 0.12},
        "white_balance": {"k_r": 1.05, "k_b": 0.95},
    }

    out_jpg = _render_reference_frame(work, active_profile=profile)
    assert out_jpg is not None
    assert out_jpg.exists()

    img = cv2.imread(str(out_jpg))
    assert img is not None
    assert img.shape == (100, 100, 3)


def test_render_reference_frame_subpixel_alignment_to_product(temp_work_dir):
    work = temp_work_dir
    ref_fit = work / "moon_00001.fit"

    # 200x200 raw FITS with a lunar disk and crater feature
    data = np.full((3, 200, 200), 1000, dtype=np.uint16)
    yy, xx = np.ogrid[:200, :200]
    mask = (xx - 100) ** 2 + (yy - 100) ** 2 < 70 ** 2
    data[:, mask] = 30000
    # Add a recognizable crater
    c_mask = (xx - 90) ** 2 + (yy - 85) ** 2 < 15 ** 2
    data[:, c_mask] = 12000
    fits.writeto(str(ref_fit), data)

    # Product image 240x240 (simulating 20px adaptive sky margin padding on all sides)
    prod_img = np.zeros((240, 240, 3), dtype=np.uint8)
    screen = (np.clip(data.astype(np.float32) / 65535.0, 0, 1) * 255.0).astype(np.uint8)
    screen_bgr = np.moveaxis(screen[[2, 1, 0], ::-1, :], 0, -1)
    prod_img[20:220, 20:220] = screen_bgr
    cv2.imwrite(str(work / "moon_natural.jpg"), prod_img)

    profile = {
        "histogram": {"bg_r": 0.0, "bg_g": 0.0, "bg_b": 0.0, "hi_lum": 0.5, "midtone": 0.13},
        "white_balance": {"k_r": 1.0, "k_b": 1.0},
    }

    out_jpg = _render_reference_frame(work, active_profile=profile)
    assert out_jpg is not None
    assert out_jpg.exists()

    img = cv2.imread(str(out_jpg))
    assert img is not None
    # Must match product canvas dimensions exactly
    assert img.shape == (240, 240, 3)

    # Crater features in both must align to within 1 pixel
    patch = prod_img[120 - 20 : 120 + 20, 120 - 20 : 120 + 20]
    res = cv2.matchTemplate(img, patch, cv2.TM_CCOEFF_NORMED)
    min_v, max_v, min_l, max_l = cv2.minMaxLoc(res)
    assert max_v > 0.95
    dx = abs(max_l[0] - (120 - 20))
    dy = abs(max_l[1] - (120 - 20))
    assert dx <= 1
    assert dy <= 1


def test_generate_comparison_html_structure(temp_work_dir):
    work = temp_work_dir
    # Create mock product images
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(work / "moon_reference.jpg"), dummy_img)
    cv2.imwrite(str(work / "moon_natural.jpg"), dummy_img)
    cv2.imwrite(str(work / "moon_mineral.jpg"), dummy_img)

    report = {
        "master": str(work / "moon_master.fit"),
        "shape": [100, 100],
        "bitpix": 16,
        "focal_length": 250.0,
        "aperture": 50.0,
        "pixel_size": 2.9,
        "device": {"id": "seestar-s50", "confidence": "high"},
        "drizzle": {"enabled": True, "scale": 2.0, "kernel": "square"},
        "reference_frame": 5,
        "registered_frames": 30,
        "stacked_frames": 25,
        "dark_halo_ratio": 0.012,
        "dark_halo_status": "EXCELLENT (artifact-free)",
        "chalky_saturation_index": 0.007,
        "chalky_status": "EXCELLENT (highlight dynamic retained)",
        "gradient_kurtosis": 5.4,
        "brittleness_status": "ORGANIC (natural smooth texture)",
        "background_noise_std": 0.0015,
        "background_noise_status": "measured",
    }

    html_file = _generate_comparison_html(work, report=report)
    assert html_file.exists()
    assert html_file.name == "comparison.html"

    content = html_file.read_text(encoding="utf-8")
    # Verify core modes are in the HTML
    assert "side-by-side" in content
    assert "split-slider" in content or "Split Slider" in content
    assert "blink" in content or "Blink" in content

    # Verify image references
    assert "moon_reference.jpg" in content
    assert "moon_natural.jpg" in content
    assert "moon_mineral.jpg" in content

    # Verify HUD is hidden by default and toggle button is not active
    assert 'class="hud-panel hidden"' in content
    assert 'id="btn-hud-toggle" class="hud-toggle-btn"' in content
    assert 'id="btn-hud-toggle" class="hud-toggle-btn active"' not in content

    # Verify Chinese localization
    assert "🌙 月面质检与光学参数 HUD" in content
    assert "暗环比率 (DHR)" in content
    assert "高光白垩指数 (CSI)" in content
    assert "梯度峰度脆裂度 (GKM)" in content
    assert "深空残噪标准差" in content
    assert "双窗并排" in content
    assert "卷帘对比" in content
    assert "闪烁交替" in content

    # Verify HUD metrics payload are embedded
    assert "seestar-s50" in content
    assert "250.0" in content
    assert "DHR" in content or "dark_halo_ratio" in content
    assert "CSI" in content or "chalky_saturation_index" in content
    assert "GKM" in content or "gradient_kurtosis" in content

    # Verify Dual Mode Ref Source controls (Aligned vs Native RAW)
    assert 'id="ref-source-group"' in content
    assert 'id="btn-ref-aligned"' in content
    assert 'id="btn-ref-raw"' in content
    assert "单帧配准版 (Aligned Ref)" in content
    assert "相机原生 RAW (True Raw As-Shot)" in content


def test_render_reference_frame_native_raw_export(temp_work_dir):
    work = temp_work_dir
    ref_fit = work / "moon_00001.fit"
    # Create a 150x200 raw FITS
    data = np.full((3, 150, 200), 500, dtype=np.uint16)
    yy, xx = np.ogrid[:150, :200]
    mask = (xx - 80) ** 2 + (yy - 75) ** 2 < 40 ** 2
    data[:, mask] = 25000
    fits.writeto(str(ref_fit), data)

    profile = {
        "histogram": {"bg_r": 0.01, "bg_g": 0.01, "bg_b": 0.01, "hi_lum": 0.6, "midtone": 0.14},
        "white_balance": {"k_r": 1.0, "k_b": 1.0},
    }

    out_jpg = _render_reference_frame(work, active_profile=profile)
    assert out_jpg is not None
    assert (work / "moon_reference.jpg").exists()

    # Verify raw original export
    raw_jpg = work / "moon_raw_original.jpg"
    assert raw_jpg.exists()
    raw_img = cv2.imread(str(raw_jpg))
    assert raw_img is not None
    # Must preserve native raw dimensions exactly (150, 200)
    assert raw_img.shape == (150, 200, 3)



def test_generate_comparison_html_missing_reference_fallback(temp_work_dir):
    work = temp_work_dir
    dummy_img = np.zeros((64, 64, 3), dtype=np.uint8)
    cv2.imwrite(str(work / "moon_natural.jpg"), dummy_img)
    # Note: moon_reference.jpg is intentionally omitted

    html_file = _generate_comparison_html(work, report=None)
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")
    assert "moon_natural.jpg" in content


def test_cli_html_report_flags():
    parser = build_parser()
    
    # Test postprocess defaults and overrides
    args = parser.parse_args(["postprocess", "--work", "/tmp/mock"])
    assert getattr(args, "html_report", None) is True

    args_no = parser.parse_args(["postprocess", "--work", "/tmp/mock", "--no-html-report"])
    assert getattr(args_no, "html_report", None) is False

    # Test verify defaults and overrides
    args_v = parser.parse_args(["verify", "--work", "/tmp/mock"])
    assert getattr(args_v, "html_report", None) is True

    args_v_no = parser.parse_args(["verify", "--work", "/tmp/mock", "--no-html-report"])
    assert getattr(args_v_no, "html_report", None) is False

    # Test all defaults and overrides
    args_all = parser.parse_args(["all", "--input", "/tmp/in", "--work", "/tmp/mock"])
    assert getattr(args_all, "html_report", None) is True

    args_all_no = parser.parse_args(["all", "--input", "/tmp/in", "--work", "/tmp/mock", "--no-html-report"])
    assert getattr(args_all_no, "html_report", None) is False


def test_apply_adaptive_sky_margins_centering():
    # Synthetic off-center image: 200x200, moon center at (70, 80), radius 50
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    yy, xx = np.ogrid[:200, :200]
    disc = (xx - 70)**2 + (yy - 80)**2 < 50**2
    img[disc] = 200

    circle = (70.0, 80.0, 50.0, 0.99)
    padded, meta = _apply_adaptive_sky_margins(
        img,
        circle=circle,
        sky_margin_ratio=0.25,
        auto_center=True,
        bg_val=0,
    )
    assert meta["applied"] is True
    new_h, new_w = padded.shape[:2]
    # Check that new dimensions are even
    assert new_w % 2 == 0 and new_h % 2 == 0
    # Check that new center is exact center
    new_cx, new_cy = meta["new_center"]
    assert abs(new_cx - new_w / 2.0) < 1.0
    assert abs(new_cy - new_h / 2.0) < 1.0

    # Required 25% margin: 50 * 0.25 = 12.5 -> 13 px
    # Distance from center to all edges minus radius must be >= 12.5
    assert (new_cx - 50.0) >= 12.5
    assert (new_w - (new_cx + 50.0)) >= 12.5
    assert (new_cy - 50.0) >= 12.5
    assert (new_h - (new_cy + 50.0)) >= 12.5


def test_apply_adaptive_sky_margins_already_satisfied():
    # Centered image with huge margins: 400x400, circle at (200, 200), radius 50
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    circle = (200.0, 200.0, 50.0, 0.99)
    padded, meta = _apply_adaptive_sky_margins(
        img,
        circle=circle,
        sky_margin_ratio=0.25,
        auto_center=True,
    )
    assert meta["applied"] is True
    assert meta["padding"] == [0, 0, 0, 0]
    assert padded.shape == (400, 400, 3)


def test_video_crop_adaptive_sky_margin():
    profile = {
        "width": 1000,
        "height": 1000,
        "circle": [500, 500, 400, 0.99],
        "rows": [{"index": 0, "box": [100, 100, 900, 900]}],
    }
    # With default margin=64, but radius=400, sky_margin_ratio=0.25 gives 100 px > 64 px
    box, reason = _video_crop(profile, [0], margin=64, mode="auto", sky_margin_ratio=0.25)
    assert box is not None
    # Box should be expanded by effective_margin = 100
    assert box[0] == 0
    assert box[1] == 0
    assert box[2] == 1000
    assert box[3] == 1000


def test_cli_sky_margin_flags():
    parser = build_parser()

    # Postprocess
    p_args = parser.parse_args(["postprocess", "--work", "/tmp"])
    assert getattr(p_args, "sky_margin", None) == 0.25
    assert getattr(p_args, "auto_margin", None) is True

    p_custom = parser.parse_args(["postprocess", "--work", "/tmp", "--sky-margin", "0.35", "--no-auto-margin"])
    assert getattr(p_custom, "sky_margin", None) == 0.35
    assert getattr(p_custom, "auto_margin", None) is False

    # Import
    i_args = parser.parse_args(["import", "--input", "/tmp", "--work", "/tmp"])
    assert getattr(i_args, "sky_margin", None) == 0.25
    assert getattr(i_args, "auto_margin", None) is True

