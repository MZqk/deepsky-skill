"""外部无星层的**逐通道**完整性校验。

背景（实测）：用户对一张极暗 Duo-Band 母版连跑 7 轮。第 3 轮起注入手动跑的 StarNet2
无星层，整片星云被剪成纯红。实测该层在星云主体掩膜内 **G 通道 93.37% 像素精确为 0**、
B 79.56% 为 0，而 R 只有 0.73%。对照内部形态学无星层 R/G/B 全部 0.00% 归零。

旧实现 `verify_external_starless` 只检查尺寸与 `stars_energy > 1e-5`，因此对这层
返回 `valid: True`，管线静默放行 —— 这是烧掉 5 轮的直接原因。

本文件锁住新校验的两条关键性质：
  1. 能检出「弱色通道大面积归零 / 被均匀压低」；
  2. **不误伤**天然缺通道的窄带图、以及整体未归一化（FITS）的输入。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from neural_star_bridge import (  # noqa: E402
    check_starless_scale_consistency,
    validate_starless_channel_integrity,
    verify_external_starless,
)


def make_nebula_with_channels(h=200, w=260, rgb_amp=(0.030, 0.010, 0.008),
                              bg=0.002, seed=3):
    """极暗线性星云：R 强、G/B 弱但真实存在，带空间结构（避免常数图掩膜为空）。"""
    yy, xx = np.mgrid[:h, :w]
    neb = np.exp(-(((yy - h * 0.55) ** 2 + (xx - w * 0.5) ** 2)
                   / (2 * (min(h, w) * 0.22) ** 2))).astype(np.float32)
    img = np.full((h, w, 3), bg, np.float32)
    img += neb[..., None] * np.array(rgb_amp, np.float32)
    return np.clip(img, 0, 1)


def _strip_stars(image, positions=((20, 24), (60, 150), (140, 40))):
    """把星点位置压暗，模拟「只去掉了星点」的健康无星层。"""
    out = image.copy()
    for cy, cx in positions:
        out[cy - 2:cy + 3, cx - 2:cx + 3] *= 0.2
    return out


class ChannelIntegrityTests(unittest.TestCase):
    def test_detects_zeroed_green_channel(self):
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] = 0.0                      # G 清零（实测故障形态）
        result = validate_starless_channel_integrity(orig, starless)
        self.assertFalse(result["valid"])
        self.assertIn("g", result["collapsed_channels"])
        self.assertIn("G", result["error"])
        self.assertEqual(result["channels"]["g"]["zero_starless"], 1.0)

    def test_healthy_starless_passes(self):
        orig = make_nebula_with_channels()
        result = validate_starless_channel_integrity(orig, _strip_stars(orig))
        self.assertTrue(result["valid"])
        self.assertEqual(result["collapsed_channels"], [])
        self.assertIsNone(result["error"])

    def test_naturally_absent_channel_is_not_flagged(self):
        """纯 Hα 无 OIII：原图 G 本来就只有 R 的 1%，不该判塌缩。"""
        orig = make_nebula_with_channels(rgb_amp=(0.030, 0.0003, 0.0002))
        starless = orig.copy()
        starless[..., 1] = 0.0                      # 把本来就近零的 G 归零
        result = validate_starless_channel_integrity(orig, starless)
        self.assertTrue(result["valid"])
        self.assertNotIn("g", result["collapsed_channels"])
        self.assertFalse(result["channels"]["g"]["present_in_original"])

    def test_scale_invariance_for_unnormalized_fits(self):
        """整体 ×65535（模拟 FITS force_linear 不归一化）不得改变判定。"""
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] = 0.0
        a = validate_starless_channel_integrity(orig, starless)
        b = validate_starless_channel_integrity(orig * 65535.0, starless * 65535.0)
        self.assertEqual(a["valid"], b["valid"])
        self.assertEqual(a["collapsed_channels"], b["collapsed_channels"])
        for label in ("r", "g", "b"):
            self.assertAlmostEqual(a["channels"][label]["rel_orig"],
                                   b["channels"][label]["rel_orig"], places=4)

    def test_uniform_level_drop_is_detected(self):
        """G 整体乘 0.3（无归零）——只有 level_drop 信号能抓。"""
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] *= 0.3
        result = validate_starless_channel_integrity(orig, starless)
        self.assertFalse(result["valid"])
        self.assertIn("g", result["collapsed_channels"])
        self.assertIn("level_drop", result["channels"]["g"]["reason"])
        self.assertEqual(result["channels"]["g"]["zero_starless"], 0.0)

    def test_zero_surge_is_detected(self):
        """G 星云区 60% 像素置 0、其余保留 —— 只有 zero_surge 信号能抓。"""
        orig = make_nebula_with_channels()
        starless = orig.copy()
        lum = starless.mean(axis=2)
        band = lum > np.percentile(lum, 55)
        ys, xs = np.where(band)
        half = len(ys) // 2
        starless[ys[:half], xs[:half], 1] = 0.0
        result = validate_starless_channel_integrity(orig, starless)
        self.assertFalse(result["valid"])
        self.assertIn("g", result["collapsed_channels"])

    def test_insufficient_signal_is_skipped(self):
        """常数图（无星云结构）→ 跳过，不误报。"""
        flat = np.full((80, 80, 3), 0.5, np.float32)
        result = validate_starless_channel_integrity(flat, flat.copy())
        self.assertTrue(result["valid"])
        self.assertEqual(result["skipped"], "insufficient_signal")

    def test_shape_mismatch(self):
        result = validate_starless_channel_integrity(
            np.zeros((10, 10, 3), np.float32), np.zeros((5, 5, 3), np.float32))
        self.assertFalse(result["valid"])
        self.assertEqual(result["skipped"], "shape_mismatch")


class ScaleConsistencyTests(unittest.TestCase):
    def test_ok_when_medians_match(self):
        orig = make_nebula_with_channels()
        r = check_starless_scale_consistency(orig, _strip_stars(orig))
        self.assertEqual(r["status"], "ok")

    def test_warn_on_moderate_deviation(self):
        orig = make_nebula_with_channels()
        r = check_starless_scale_consistency(orig, orig * 0.33)
        self.assertEqual(r["status"], "warn")

    def test_fail_on_severe_deviation(self):
        """未归一化的 FITS 量级（×65535）应硬失败而非静默接受。"""
        orig = make_nebula_with_channels()
        r = check_starless_scale_consistency(orig, orig * 65535.0)
        self.assertEqual(r["status"], "fail")


class VerifyExternalStarlessTests(unittest.TestCase):
    def test_legacy_fields_preserved(self):
        orig = make_nebula_with_channels()
        r = verify_external_starless(orig, _strip_stars(orig))
        for key in ("valid", "stars_energy_mean", "starless_energy_mean",
                    "stars_extracted_successfully"):
            self.assertIn(key, r)
        self.assertTrue(r["valid"])
        self.assertIn("channel_integrity", r)
        self.assertIn("scale_consistency", r)

    def test_channel_collapse_makes_it_invalid(self):
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] = 0.0
        r = verify_external_starless(orig, starless)
        self.assertFalse(r["valid"])
        self.assertIn("G", r["error"])

    def test_shape_mismatch_still_short_circuits(self):
        r = verify_external_starless(
            np.zeros((10, 10, 3), np.float32), np.zeros((5, 5, 3), np.float32))
        self.assertFalse(r["valid"])
        self.assertIn("尺寸不匹配", r["error"])

    def test_constant_image_still_valid(self):
        """既有测试的形态：常数 0.5 + 一个暗块，必须仍然 valid。"""
        orig = np.ones((50, 50, 3), np.float32) * 0.5
        starless = orig.copy()
        starless[10:15, 10:15] = 0.2
        r = verify_external_starless(orig, starless)
        self.assertTrue(r["valid"])
        self.assertTrue(r["stars_extracted_successfully"])

    def test_check_channels_can_be_disabled(self):
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] = 0.0
        r = verify_external_starless(orig, starless, check_channels=False)
        self.assertTrue(r["valid"])
        self.assertNotIn("channel_integrity", r)


if __name__ == "__main__":
    unittest.main()
