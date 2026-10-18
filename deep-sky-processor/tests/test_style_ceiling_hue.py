"""高光钳位不得破坏色相（v0.1.41）。

**要锁的性质**：

  1. **色相守恒**：ceiling<1 时，逐像素等比缩放，通道比不变。
     旧实现按通道各自 min(gain, room)，实测把 M31 核球 R/G 从 1.529
     打到 0.383 —— 黄白星系核渲染成青绿色。
  2. **ceiling 仍被强制**：末端（color_separation / saturation /
     background_desat / warmth 之后）最大值 == ceiling。
  3. **ceiling=1.0 逐位不变**：默认不封顶是历史行为，不得被本改动波及。
  4. **抗饱和保证不变**：0 饱和像素占比为 0。

构造用的是**复现 M31 核球的实测数值**，不是随手编的数字 ——
旧实现在这个输入上确实会塌色相（见 test_hue_is_destroyed_by_old_rule）。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from style_tools import apply_professional_style  # noqa: E402


def _m31_core_like(size=192, peak=0.8085, seed=11):
    """复现实测 M31 核球色度：R 远高于 G/B，且整体接近上限。

    实测值：R=0.8085 G=0.5289 B=0.4454（R/G=1.529）。
    这是**红通道已接近 ceiling** 的典型形态 —— 旧实现在此必塌。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.empty((size, size, 3), dtype=np.float32)
    img[..., 0] = peak * np.exp(-((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (2 * 9.0 ** 2))
    img[..., 1] = peak * 0.654 * np.exp(-((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (2 * 9.0 ** 2))
    img[..., 2] = peak * 0.551 * np.exp(-((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (2 * 9.0 ** 2))
    img += rng.normal(0, 0.004, img.shape).astype(np.float32)
    img += 0.02
    return np.clip(img, 0, 1).astype(np.float32)


def _core_ratio(img, size=None):
    """核球区的 R/G（取最亮的 1% 像素）。"""
    g = img.mean(axis=2)
    thr = np.percentile(g, 99.0)
    m = g >= thr
    return float(img[..., 0][m].mean() / max(img[..., 1][m].mean(), 1e-9))


class CeilingHueTests(unittest.TestCase):

    def _run(self, ceiling, style="galaxy_core", target="galaxy"):
        return apply_professional_style(
            _m31_core_like(), style=style, target_type=target,
            color_mode="standard", strength=0.70,
            highlight_ceiling=ceiling)[0]

    def test_hue_survives_ceiling(self):
        """性质 1：ceiling=0.98 时核心 R/G 不得塌陷。"""
        src = _core_ratio(_m31_core_like())
        out = _core_ratio(self._run(0.98))
        self.assertGreater(out, src * 0.75,
                          "核心 R/G 相对输入塌陷：%.3f -> %.3f" % (src, out))

    def test_ceiling_is_enforced_after_all_ops(self):
        """性质 2：末端最大值 == ceiling。"""
        for ceiling in (0.95, 0.98):
            with self.subTest(ceiling=ceiling):
                out = self._run(ceiling)
                self.assertLessEqual(float(out.max()), ceiling + 1e-6)

    def test_default_ceiling_is_bit_identical_to_one(self):
        """性质 3：ceiling=1.0（默认）不受本改动影响。"""
        a = apply_professional_style(
            _m31_core_like(), style="galaxy_core", target_type="galaxy",
            color_mode="standard", strength=0.70, highlight_ceiling=1.0)[0]
        b = apply_professional_style(
            _m31_core_like(), style="galaxy_core", target_type="galaxy",
            color_mode="standard", strength=0.70)[0]
        np.testing.assert_array_equal(a, b)

    def test_no_saturated_pixels_under_ceiling(self):
        """性质 4：抗饱和保证不变。"""
        out = self._run(0.98)
        self.assertEqual(float(np.mean(out >= 0.995)), 0.0)

    def test_hue_is_destroyed_by_old_rule(self):
        """反证：旧公式在本夹具上确实会塌色相。

        这条用例保证本测试集不是「怎么改都过」——它先证明旧实现有缺陷，
        上面几条才有意义。
        """
        src = np.clip(_m31_core_like(), 0, 1)
        ceiling, gain = 0.98, 1.1184
        room = np.maximum(ceiling - src, 0.0) / np.maximum(src, 1e-6)
        old = np.clip(src * np.minimum(gain, room), 0, 1)
        before = _core_ratio(src)
        after = _core_ratio(old)
        # 旧公式的缺陷签名是**色相翻转**：R/G 跌破 1.0（R 不再是最强通道），
        # 而不只是「变淡」。夹具的绝对降幅小于真实 M31（那里 R 更高、
        # room_R 更小），所以断言翻转而非固定倍数。
        self.assertLess(after, 1.0,
                        "夹具未能复现旧缺陷（未发生色相翻转）：%.3f -> %.3f"
                        % (before, after))
        self.assertLess(after, before * 0.75,
                        "夹具未能复现旧缺陷：%.3f -> %.3f" % (before, after))


if __name__ == "__main__":
    unittest.main()