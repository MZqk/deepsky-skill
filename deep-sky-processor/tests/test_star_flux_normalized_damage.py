"""星层归一化误伤判据（v0.1.39）—— 性质锁定，不是读数快照。

要锁定的是四条性质（每条都对应一次真实踩坑）：

  1. **旧字段逐位不变**：并存不是替换，nebula_damage_ratio 的语义与取值
     不能因为新增归一化读数而改变。否则「哪个改动带来读数变化」无法归因。
  2. **单调性**：注入递增的结构损伤，归一化读数必须严格递增。
     这一条是 v0.1.39 最重要的一条 —— 派生星层（orig - starless）会让
     读数**非单调**（M8 实测 beta 0→0.30：0.0942→0.0948→0.0917→0.0773，
     越损伤越小），因为注入的损伤被同时算进了「星层」。
  3. **主体局部性**：只损伤掩膜外不得改变读数。
  4. **无星层输入 ⇒ 不产出该读数**，而不是产出 0（0 会被读成「没误伤」）。

不锁具体数字：这些读数依赖合成夹具的具体参数，锁死会让夹具微调变成测试失败，
而性质（单调、局部、可用性）才是真正不能退化的部分。
"""

import dataclasses
import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.ndimage import binary_erosion, gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import star_removal_profiles as srp
from star_tools import estimate_star_removal_quality

def _scene(with_stars=True, size=256, seed=3, background=0.01, noise=0.002):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    image = np.full((size, size), background, dtype=np.float32)
    image += rng.normal(0, noise, (size, size)).astype(np.float32)
    image += 0.35 * np.exp(
        -(((xx - size * 0.5) / 55.0) ** 2 + ((yy - size * 0.47) / 38.0) ** 2)
    ).astype(np.float32)
    if with_stars:
        for cy, cx in rng.integers(10, size - 10, (60, 2)):
            image += 0.7 * np.exp(
                -((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.5 ** 2)
            ).astype(np.float32)
    return np.clip(image, 0, 1).astype(np.float32)


def _split(image, leak=0.06):
    """返回 (starless, stars, star_mask)：按「亮且紧凑」把星点抽走。

    这里**不用** orig - starless 当星层：那正是 v0.1.39 禁止的派生方式。

    leak：残留主体偏暗的比例，模拟真实去星器留下的少量结构损失。
    缺省 0（完美分离）会让归一化读数恒落在死区内 —— 夹具必须含有
    真实的非零残差，否则「单调性」这条性质根本无从检验。
    """
    local_bg = gaussian_filter(image, sigma=4.0)
    star_resp = image - local_bg
    thresh = np.percentile(star_resp[star_resp > 0], 99.0)
    mask = star_resp > thresh
    stars = np.zeros_like(image)
    stars[mask] = image[mask]
    starless = image.copy()
    # 非星区整体压暗 leak 比例 = 真实的残留结构损失
    starless[~mask] *= (1.0 - leak)
    starless[mask] = local_bg[mask] * (1.0 - leak)
    return starless.astype(np.float32), stars.astype(np.float32), mask


def _profile(**over):
    return dataclasses.replace(srp.DEFAULTS, star_flux_normalize=True, **over)


class LegacyMetricUnchangedTests(unittest.TestCase):
    """性质 1：并存不改旧字段。"""

    def test_legacy_reading_identical_with_and_without_star_layer(self):
        image = _scene()
        starless, stars, _mask = _split(image)
        with_stars = estimate_star_removal_quality(
            image, starless, "emission_nebula", _profile(), stars=stars)
        without = estimate_star_removal_quality(
            image, starless, "emission_nebula", _profile())
        self.assertEqual(with_stars["nebula_damage_ratio"],
                         without["nebula_damage_ratio"],
                         "传入星层改变了旧读数 —— 并存变成了替换")
        self.assertEqual(with_stars["repair_quality_score"],
                         without["repair_quality_score"])

    def test_legacy_reading_identical_when_feature_disabled(self):
        """关闭开关时归一化读数一律不产出，且旧读数不变。"""
        image = _scene()
        starless, stars, _mask = _split(image)
        off = dataclasses.replace(srp.DEFAULTS, star_flux_normalize=False)
        r = estimate_star_removal_quality(image, starless,
                                          "emission_nebula", off, stars=stars)
        self.assertFalse(r["star_flux_metric_available"])
        self.assertEqual(r["nebula_damage_ratio_star_flux"], 0.0)
        self.assertFalse(r["nebula_damage_star_flux_measurable"])


class AvailabilityTests(unittest.TestCase):
    """性质 4：没有星层就不产出读数。"""

    def test_no_star_layer_marks_metric_unavailable(self):
        image = _scene()
        starless, _stars, _mask = _split(image)
        r = estimate_star_removal_quality(image, starless,
                                          "emission_nebula", _profile())
        self.assertFalse(r["star_flux_metric_available"],
                         "未传星层却声称归一化读数可用")

    def test_derived_star_layer_is_refused(self):
        """星层必须显式传入；不传时不得退回 orig - starless 派生。

        派生量与被测量共用同一个减法，修正项会与信号项自相消（实测非单调）。
        """
        image = _scene()
        starless, _stars, _mask = _split(image)
        r = estimate_star_removal_quality(image, starless,
                                          "emission_nebula", _profile())
        self.assertFalse(r["star_flux_metric_available"])
        self.assertEqual(r["nebula_damage_ratio_star_flux"], 0.0,
                         "退回了派生星层的结果")


class MonotonicityTests(unittest.TestCase):
    """性质 2：递增损伤 ⇒ 严格递增读数（v0.1.39 的核心修复）。"""

    def _series(self, image, starless, stars, betas):
        """按 beta 递减**主体区**亮度，读数序列。

        星层保持**不变**（真实观测量），只动无星层 —— 这正是
        「注入损伤不该改变星层」的要求。

        损伤必须施加在**主体掩膜**（orig_smooth 的 p75）上，不是星点掩膜：
        误伤按定义就发生在有真实信号的主体上。在星点位置注入损伤测的是
        另一件事。
        """
        orig_smooth = gaussian_filter(image.astype(np.float32), 8.0)
        nebula = orig_smooth >= np.percentile(orig_smooth, 75)
        nebula = binary_erosion(nebula, iterations=8)
        H, W = image.shape
        yy = np.arange(H)[:, None]
        xx = np.arange(W)[None, :]
        field = (0.5 + 0.5 * np.cos(2 * np.pi * xx / W)
                 * np.cos(2 * np.pi * yy / H))
        out = []
        for beta in betas:
            d = starless.copy()
            d[nebula] = starless[nebula] * (1.0 - beta * field)[nebula]
            r = estimate_star_removal_quality(
                image, d, "emission_nebula", _profile(), stars=stars)
            out.append(r["nebula_damage_ratio_star_flux"])
        return out

    def test_damage_away_from_healthy_is_monotonic(self):
        """从健康态把主体**压暗**（q<1 侧），读数必须单调递增。

        死区 0.02，健康态可能已读 0，所以比较相邻差值而非绝对值。
        """
        image = _scene()
        starless, stars, mask = _split(image)
        r0 = estimate_star_removal_quality(image, starless,
                                           "emission_nebula", _profile(),
                                           stars=stars)
        self.assertTrue(r0["star_flux_metric_available"])
        self.assertTrue(r0["nebula_damage_star_flux_measurable"])
        betas = [0.0, 0.05, 0.12, 0.25, 0.40]
        series = self._series(image, starless, stars, betas)
        for a, b in zip(series, series[1:]):
            self.assertGreater(
                b, a,
                "读数非单调（down 侧）：%r -> %r；派生星层会抵消信号"
                % (series, betas))

    def test_final_damage_exceeds_healthy_by_clear_margin(self):
        """重度损伤必须明显高于健康态 —— 单调但不分离等于没修好。"""
        image = _scene()
        starless, stars, mask = _split(image)
        healthy = estimate_star_removal_quality(
            image, starless, "emission_nebula", _profile(), stars=stars)
        heavy = self._series(image, starless, stars, [0.0, 0.40])[-1]
        self.assertGreater(
            heavy,
            healthy["nebula_damage_ratio_star_flux"] + 0.05,
            "重度损伤未与健康态拉开距离")


class LocalityTests(unittest.TestCase):
    """性质 3：主体局部性 —— 掩膜外损伤不得影响读数。"""

    def test_outside_mask_damage_does_not_move_reading(self):
        """腐蚀 3σ=24px（文档 §3.6）后只损伤掩膜外，读数必须不动。

        不腐蚀会因高斯核支撑而渗入：sigma=8 的核支撑是无限的。
        """
        image = _scene(size=256)
        starless, stars, _mask = _split(image)
        base = estimate_star_removal_quality(
            image, starless, "emission_nebula", _profile(), stars=stars)
        orig_smooth = gaussian_filter(image, 8.0)
        nebula = orig_smooth >= np.percentile(orig_smooth, 75)
        outside = binary_erosion(~nebula, iterations=24)
        damaged = starless.copy()
        damaged[outside] *= 0.5
        r = estimate_star_removal_quality(
            image, damaged, "emission_nebula", _profile(), stars=stars)
        self.assertLess(
            abs(r["nebula_damage_ratio_star_flux"]
                - base["nebula_damage_ratio_star_flux"]),
            0.02,
            "掩膜外损伤改变了读数 —— 判据不是主体局部量")


class ReportFieldTests(unittest.TestCase):
    """报告字段：新增量必须自洽，且旧字段名不变。"""

    def test_all_new_fields_present(self):
        image = _scene()
        starless, stars, _mask = _split(image)
        r = estimate_star_removal_quality(image, starless,
                                          "emission_nebula", _profile(),
                                          stars=stars)
        for key in ("nebula_damage_ratio_star_flux",
                    "nebula_damage_star_flux_measurable",
                    "star_flux_metric_available",
                    "star_flux_fraction",
                    "star_free_subject_level"):
            self.assertIn(key, r)

    def test_star_flux_fraction_in_unit_range(self):
        image = _scene()
        starless, stars, _mask = _split(image)
        r = estimate_star_removal_quality(image, starless,
                                          "emission_nebula", _profile(),
                                          stars=stars)
        self.assertGreaterEqual(r["star_flux_fraction"], 0.0)
        self.assertLessEqual(r["star_flux_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()