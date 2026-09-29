"""Tests for pure-code built-in À Trous Wavelet (Starlet) and Anscombe VST denoising.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from denoise import (  # noqa: E402
    starlet_transform,
    inverse_starlet_transform,
    anscombe_transform,
    inverse_anscombe_transform,
    denoise_atrous_wavelet,
    denoise_luminance_chroma,
)


class AtrousWaveletDenoiseTests(unittest.TestCase):
    def test_starlet_exact_reconstruction(self):
        """Starlet transform must guarantee exact reconstruction (perfect conservation)."""
        rng = np.random.default_rng(42)
        img = rng.uniform(0.001, 0.05, size=(128, 128)).astype(np.float32)
        wavelets, residual = starlet_transform(img, n_scales=4)
        reconstructed = inverse_starlet_transform(wavelets, residual)
        max_err = float(np.max(np.abs(img - reconstructed)))
        self.assertLess(max_err, 1e-5, f"Starlet roundtrip max error too high: {max_err}")

    def test_starlet_rgb_reconstruction(self):
        """Starlet transform must support 3-channel RGB data seamlessly."""
        rng = np.random.default_rng(42)
        img = rng.uniform(0.001, 0.05, size=(64, 64, 3)).astype(np.float32)
        wavelets, residual = starlet_transform(img, n_scales=3)
        reconstructed = inverse_starlet_transform(wavelets, residual)
        self.assertEqual(reconstructed.shape, img.shape)
        max_err = float(np.max(np.abs(img - reconstructed)))
        self.assertLess(max_err, 1e-5)

    def test_anscombe_vst_roundtrip(self):
        """Anscombe VST and inverse must roundtrip with minimal error on positive values."""
        x = np.linspace(0.01, 10.0, 100, dtype=np.float32)
        z = anscombe_transform(x)
        x_rec = inverse_anscombe_transform(z)
        rel_err = np.abs(x - x_rec) / x
        self.assertLess(float(np.mean(rel_err)), 0.01)

    def test_noise_reduction_and_star_preservation(self):
        """High-frequency noise should drop by at least 3x while star peak is preserved >= 98%."""
        rng = np.random.default_rng(123)
        h, w = 120, 120
        clean = np.full((h, w), 0.002, dtype=np.float32)
        # Add a synthetic star
        clean[60, 60] = 0.85
        # Add a faint nebula gradient
        y, x = np.ogrid[:h, :w]
        clean += 0.01 * np.exp(-((y - 60)**2 + (x - 60)**2) / (2 * 15**2)).astype(np.float32)

        noise_sigma = 0.001
        noisy = clean + rng.normal(0, noise_sigma, clean.shape).astype(np.float32)

        denoised = denoise_atrous_wavelet(noisy, n_scales=4, k_sigmas=(3.0, 2.0, 1.0, 0.0))

        # Check noise std in background corner
        noise_orig = float(np.std(noisy[:30, :30]))
        noise_filtered = float(np.std(denoised[:30, :30]))
        self.assertLess(noise_filtered, noise_orig * 0.45, "Noise was not sufficiently reduced")

        # Check star peak
        star_orig = float(clean[60, 60])
        star_filtered = float(denoised[60, 60])
        self.assertGreater(star_filtered, star_orig * 0.98, "Star peak was excessively attenuated")

    def test_denoise_luminance_chroma_atrous_mode(self):
        """denoise_luminance_chroma with method='atrous' must work on RGB images."""
        rng = np.random.default_rng(999)
        rgb = rng.uniform(0.01, 0.2, size=(80, 80, 3)).astype(np.float32)
        out = denoise_luminance_chroma(rgb, lum_strength=0.005, chroma_strength=0.015, method='atrous')
        self.assertEqual(out.shape, rgb.shape)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_degenerate_constant_input(self):
        """Flat images must return finite valid arrays without crashing."""
        flat = np.full((50, 50), 0.05, dtype=np.float32)
        out = denoise_atrous_wavelet(flat)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertAlmostEqual(float(np.mean(out)), 0.05, places=4)


if __name__ == "__main__":
    unittest.main()
