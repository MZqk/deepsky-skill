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


class DegradedChannelTests(unittest.TestCase):
    """「退化」是「塌缩」与「健康」之间的第三态。

    背景（实测 NGC6888 + StarNet2）：MTF 域通道保留率 R 0.856 / G 0.703 /
    B 0.739 —— 网络对弱色通道剥离得更狠，使无星层**从构造上就偏红**。
    G 的 `level_drop` 实测 1.51，低于按「G 被剪零 93%」标定的 2.0 阈值，
    旧判据按设计检不出 → 静默通过。

    这类偏差可以靠逐通道增益救回（G 需 ×1.5~3.0），所以处置是**补偿**而非
    拒绝；把它并入 `collapsed` 只会把管线推向 `review_required` 而不产出图。
    """

    def test_moderate_channel_loss_is_degraded_not_collapsed(self):
        """掉三成左右：判 degraded，不判 collapsed（仍 valid=True）。"""
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] *= 0.66                    # G 掉约 34%
        result = validate_starless_channel_integrity(orig, starless)
        self.assertTrue(result["valid"])
        self.assertEqual(result["collapsed_channels"], [])
        self.assertIn("g", result["degraded_channels"])
        self.assertNotIn("b", result["degraded_channels"])

    def test_healthy_channel_is_not_degraded(self):
        orig = make_nebula_with_channels()
        result = validate_starless_channel_integrity(orig, _strip_stars(orig))
        self.assertEqual(result["degraded_channels"], [])

    def test_absent_channel_is_neither_degraded_nor_collapsed(self):
        """天然缺通道的窄带图不应被标退化 —— 原图里本来就没有。"""
        orig = make_nebula_with_channels(rgb_amp=(0.030, 0.0003, 0.0002))
        starless = orig.copy()
        starless[..., 1] *= 0.66
        result = validate_starless_channel_integrity(orig, starless)
        self.assertEqual(result["degraded_channels"], [])

    def test_severe_loss_stays_collapsed_not_degraded(self):
        """真塌缩（×0.1）必须仍是 collapsed —— 增益救不回来。"""
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] *= 0.10
        result = validate_starless_channel_integrity(orig, starless)
        self.assertFalse(result["valid"])
        self.assertIn("g", result["collapsed_channels"])
        self.assertNotIn("g", result["degraded_channels"])


class ChannelRebalanceTests(unittest.TestCase):
    """逐通道保真回补：把无星层色比拉回原图。"""

    def _ratios(self, image):
        from pipeline import _nebula_signal_mask_for_rebalance
        from pipeline import _display_domain_channel_ratios
        mask = _nebula_signal_mask_for_rebalance(image)
        r = _display_domain_channel_ratios(image, mask)
        return float(r[0] / r[1]), float(r[2] / r[1])

    def test_solves_gains_that_restore_original_colour_ratio(self):
        from pipeline import _rebalance_starless_channels
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] *= 0.66                    # 模拟 G 被过度剥离
        starless[..., 2] *= 0.74
        before = self._ratios(starless)
        warnings = []
        out = _rebalance_starless_channels(
            orig, starless, {'target_bg': .045}, warnings)
        self.assertTrue(out["applied"], out.get("reason"))
        self.assertGreater(out["gains"][1], 1.2)   # G 被拉回
        self.assertAlmostEqual(out["gains"][0], 1.0, places=6)  # 归一到 R=1
        after = self._ratios(out["_corrected"])
        self.assertAlmostEqual(after[0], self._ratios(orig)[0], delta=.05)
        self.assertAlmostEqual(after[1], self._ratios(orig)[1], delta=.05)
        self.assertNotEqual(round(before[0], 3), round(after[0], 3))

    def test_records_a_warning_so_the_repair_is_auditable(self):
        from pipeline import _rebalance_starless_channels
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] *= 0.66
        warnings = []
        _rebalance_starless_channels(orig, starless, {'target_bg': .045}, warnings)
        self.assertEqual([w["code"] for w in warnings],
                         ["STARLESS_CHANNEL_REBALANCE_APPLIED"])

    def test_refuses_when_gain_would_exceed_the_limit(self):
        """增益上限是安全阀：弱通道差太多时宁可不修，也不能把噪声拉上天。"""
        from pipeline import _rebalance_starless_channels
        orig = make_nebula_with_channels()
        starless = orig.copy()
        starless[..., 1] = 1e-5
        out = _rebalance_starless_channels(
            orig, starless, {'starless_rebalance_max_gain': 1.2}, [])
        self.assertFalse(out["applied"])
        self.assertEqual(out["reason"], "gain_limit_exceeded")

    def test_healthy_input_is_not_flagged_for_repair(self):
        """健康输入不应被标退化 —— 回补只由 degraded_channels 触发。"""
        orig = make_nebula_with_channels()
        starless = _strip_stars(orig)
        integrity = validate_starless_channel_integrity(orig, starless)
        self.assertEqual(integrity["degraded_channels"], [])
        self.assertTrue(integrity["valid"])


    def test_final_residual_and_new_clipping_reject_unsafe_gains(self):
        from neural_star_bridge import solve_channel_rebalance_gains
        from pipeline import _solve_gains_with_probe
        orig = make_nebula_with_channels(rgb_amp=(.03, .03, .03), bg=0)
        starless = orig.copy(); starless[..., 1:] *= .25
        def nonlinear_probe(image):
            ratios = np.sqrt(image.mean(axis=(0, 1)))
            return ratios / ratios.max()
        result = _solve_gains_with_probe(orig, starless, nonlinear_probe,
                                         {'starless_rebalance_iterations': 1})
        self.assertFalse(result['applied'])
        self.assertEqual(result['reason'], 'not_converged')
        self.assertGreater(result['residual'], .01)
        result = solve_channel_rebalance_gains(orig, starless)
        self.assertTrue(result['applied'])
        self.assertLessEqual(result['residual'], .01)
        starless[0, 0, 1] = .8
        result = solve_channel_rebalance_gains(orig, starless)
        self.assertFalse(result['applied'])
        self.assertEqual(result['reason'], 'new_clipping')

    def test_integrity_reports_raw_zero_separately_and_rejects_nonfinite(self):
        orig = make_nebula_with_channels()
        result = validate_starless_channel_integrity(orig, orig.copy())
        self.assertEqual(result['channels']['g']['raw_zero_starless'], 0)
        self.assertIn('p10', result['zero_measurement'])
        starless = orig.copy(); starless[0, 0, 1] = np.nan
        self.assertFalse(validate_starless_channel_integrity(orig, starless)['valid'])


if __name__ == "__main__":
    unittest.main()
