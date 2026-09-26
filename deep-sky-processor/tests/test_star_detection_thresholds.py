"""星点检测阈值必须锚定背景噪声，不能锚定画面里最亮的星。

旧实现有两处「相对最亮星」的阈值：
  - per-scale: `max(median + 4·MAD of positive, min(p97 of positive, 0.85·factor·max))`
  - 第二道:   `star_threshold * combined_response.max() * 0.12`
对 M31 这种带极亮核的目标，第二道阈值达到 0.0985 —— 把 3.12% 的候选集压到
0.044%，检出 115 个星点，而局部极大法在同一张图上有约 1985 个。
只要阈值锚定"画面里有什么"，检测结果就取决于最亮的那个天体。
"""

import unittest
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from star_tools import detect_stars_multiscale


def _field(peaks, size=160, fwhm=3.3, background=0.01, noise=0.0, seed=0):
    """在平坦背景上放若干高斯星点；noise>0 时叠加读噪声。"""
    rng = np.random.default_rng(seed)
    image = np.full((size, size), background, dtype=np.float32)
    impulses = np.zeros_like(image)
    for y, x, peak in peaks:
        impulses[y, x] = peak
    image += gaussian_filter(impulses, sigma=fwhm / 2.355).astype(np.float32)
    if noise > 0:
        image += rng.normal(0, noise, image.shape).astype(np.float32)
    return np.clip(image, 0, 1)


class BrightObjectIndependenceTests(unittest.TestCase):
    def test_a_very_bright_star_does_not_hide_the_others(self):
        """核心回归：把最亮的星从 1.0 提到 1.0（已饱和）不该改变其余星的检出。"""
        peaks = [(30, 30, 0.30), (60, 110, 0.24), (110, 50, 0.20), (130, 130, 0.16)]

        modest = _field(peaks + [(20, 130, 0.55)])
        extreme = _field(peaks + [(20, 130, 1.00)])

        _m1, _c1, d1 = detect_stars_multiscale(modest, fwhm=3.3, return_details=True)
        _m2, _c2, d2 = detect_stars_multiscale(extreme, fwhm=3.3, return_details=True)

        self.assertGreaterEqual(d1["n_components_kept"], 4)
        self.assertGreaterEqual(d2["n_components_kept"], 4)
        self.assertLessEqual(
            abs(d1["n_components_kept"] - d2["n_components_kept"]), 1
        )

    def test_scaling_the_whole_field_does_not_change_the_count(self):
        """整体乘一个常数不应改变检出数量 —— 阈值必须是尺度不变的。"""
        peaks = [(30, 30, 0.95), (60, 110, 0.80), (110, 50, 0.70), (130, 130, 0.62)]
        base = _field(peaks, background=0.01, noise=0.001, seed=3)
        scaled = np.clip(base * 2.0, 0, 1)

        _m1, _c1, d1 = detect_stars_multiscale(base, fwhm=3.3, return_details=True)
        _m2, _c2, d2 = detect_stars_multiscale(scaled, fwhm=3.3, return_details=True)

        self.assertGreaterEqual(d1["n_components_kept"], 3)
        self.assertLessEqual(
            abs(d1["n_components_kept"] - d2["n_components_kept"]), 1
        )

    def test_faint_stars_are_detected_on_a_flat_background(self):
        """无噪声的合成图上，背景 tophat ≈ 0，全部星点都应通过。"""
        peaks = [(30, 30, 0.20), (60, 110, 0.15), (110, 50, 0.12), (130, 130, 0.10)]
        image = _field(peaks)

        mask, _c, details = detect_stars_multiscale(image, fwhm=3.3, return_details=True)

        self.assertGreaterEqual(details["n_components_kept"], 4)
        for y, x, _p in peaks:
            self.assertGreater(float(mask[y, x]), 0.5)

    def test_noise_floor_suppresses_pure_noise(self):
        """纯噪声图上不应检出成规模的"星点"。"""
        rng = np.random.default_rng(7)
        image = (0.02 + rng.normal(0, 0.002, (256, 256))).astype(np.float32)

        _m, _c, details = detect_stars_multiscale(image, fwhm=3.0, return_details=True)

        self.assertLess(details["n_components_kept"], 60)

    def test_elongated_structure_is_still_rejected(self):
        """锚定噪声后，细长结构仍必须被拒绝。"""
        image = np.full((128, 128), 0.01, dtype=np.float32)
        image[62:66, 20:108] += 0.25
        impulses = np.zeros_like(image)
        impulses[30, 30] = 0.5
        impulses[90, 95] = 0.4
        image += gaussian_filter(impulses, sigma=1.3)

        mask, _c, _d = detect_stars_multiscale(image, fwhm=3.0, return_details=True)

        self.assertLess(float(np.mean(mask[62:66, 35:90])), 0.1)


if __name__ == "__main__":
    unittest.main()
