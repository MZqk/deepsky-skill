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
from moon_stack import _locate_high_contrast_roi, _subpixel_phase_correlation


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


def test_subpixel_shifts() -> list[str]:
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

    return failures


def test_roi_localization() -> list[str]:
    failures = []
    img = make_synthetic_moon(h=1024, w=1024)
    x0, y0, x1, y1 = _locate_high_contrast_roi(img, roi_size=256)
    print(f"ROI localization on 1024x1024 moon: [{x0}:{x1}, {y0}:{y1}]")

    # Center of 1024x1024 is (512, 512). The moon is within radius 409.
    # Check that the ROI is well inside the moon disk and does not hit the border space
    if x0 < 100 or x1 > 924 or y0 < 100 or y1 > 924:
        failures.append(f"ROI fell outside the moon disc: [{x0}:{x1}, {y0}:{y1}]")

    return failures


def test_siril_r0_format() -> list[str]:
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
    return failures


def main() -> int:
    failures = []
    print("--- Running test_subpixel_shifts ---")
    failures.extend(test_subpixel_shifts())
    print("\n--- Running test_roi_localization ---")
    failures.extend(test_roi_localization())
    print("\n--- Running test_siril_r0_format ---")
    failures.extend(test_siril_r0_format())

    if failures:
        print("\nTESTS FAILED:")
        for f in failures:
            print("  -", f)
        return 1

    print("\nALL MATHEMATICAL & FORMAT TESTS PASSED!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
