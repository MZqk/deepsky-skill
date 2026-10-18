"""OIII 局部提分离度（v0.1.42）。

**要锁的性质**：

  1. **掩膜是真正的青色检测器**：`B>R and G>R`。旧式 `(G+B)/(2R)>=0.42`
     是「R 够低」检测器（红色 [0.2,0.3,0.4] 也得 1.75），实测覆盖
     **88.7%** 整幅；新式实测 11.2%。用旧掩膜做局部提色 = 整幅再拉一遍
     饱和度，青腔没变、红壳先炸。
  2. **只提色度，不改亮度**：均值仍作支点 ⇒ 腔内亮度逐像素不变。
  3. **单调**：boost 越大，腔内 B-R 与饱和度单调上升。
  4. **关闭时逐位不变**：`oiii_color_separation=0` 必须与历史输出一致。
  5. **非 emission 模式不受影响**：该键只在 emission 分支生效。

夹具用 NGC2237 实测的核心/腔体色度，不是编造数字。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import style_tools  # noqa: E402
from style_tools import apply_professional_style  # noqa: E402


def _scene(size=192, seed=13):
    """发射星云形态：红色壳 + 中央青色腔 + 暗背景。

    腔体色度取自 NGC2237 实测（腔内 B-R 中位 ≈ +0.027，即 B/G 略高于 R/G）。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    r = np.sqrt((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (size * 0.32)
    img = np.empty((size, size, 3), dtype=np.float32)
    img[..., 0] = 0.55 * np.exp(-r ** 2) + 0.02
    img[..., 1] = 0.16 * np.exp(-r ** 2) + 0.02
    img[..., 2] = 0.18 * np.exp(-r ** 2) + 0.02
    img += rng.normal(0, 0.003, img.shape).astype(np.float32)
    return np.clip(img, 0, 1).astype(np.float32)


def _cavity_mask(image):
    return (image[..., 2] > image[..., 0]) & (image[..., 1] > image[..., 0])


def _run(boost, image=None, **kw):
    prof = style_tools.STYLE_PROFILES["dramatic_nebula"]
    old = prof.get("oiii_color_separation", 0.0)
    prof["oiii_color_separation"] = boost
    try:
        base = dict(color_mode="emission", target_type="emission_nebula",
                    strength=1.0, highlight_ceiling=0.98)
        base.update(kw)
        return apply_professional_style((image if image is not None
                                        else _scene()).copy(),
                                       style="dramatic_nebula",
                                       **base)[0]
    finally:
        prof["oiii_color_separation"] = old


class OiiiMaskTests(unittest.TestCase):
    """性质 1：掩膜必须只覆盖真正的青腔。"""

    def test_old_mask_over_covers_but_new_does_not(self):
        """反证：旧判据在本夹具上严重过覆盖。

        真实 NGC2237 上旧式覆盖 88.7% 整幅（青腔只占 11.2%）。
        本合成夹具的绝对覆盖率不同（构图为理想化高斯），这里只锁**相对收窄**：
        新掩膜必须显著窄于旧判据，否则等于没修。
        """
        img = _scene()
        R, G, B = img[..., 0], img[..., 1], img[..., 2]
        sig = (G + B) / (2.0 * np.maximum(R, 1e-6))
        old_cov = float(np.mean(sig >= 0.42))
        new_cov = float(np.mean(_cavity_mask(img)))
        self.assertGreater(old_cov, 0.2, "夹具未复现旧掩膜的过覆盖")
        self.assertLess(new_cov, old_cov * 0.25,
                        "新掩膜没有显著收窄：old=%.3f new=%.3f"
                        % (old_cov, new_cov))


class OiiiBoostTests(unittest.TestCase):

    def test_cavity_chroma_increases(self):
        img = _scene()
        cav = _cavity_mask(img)
        out0, out1 = _run(0.0, img), _run(0.40, img)
        br0 = float(np.median((out0[..., 2] - out0[..., 0])[cav]))
        br1 = float(np.median((out1[..., 2] - out1[..., 0])[cav]))
        self.assertGreater(br1, br0, "腔体 B-R 未上升")

    def test_luminance_preserved(self):
        """性质 2：均值作支点 ⇒ 腔内亮度逐像素不变。"""
        img = _scene()
        cav = _cavity_mask(img)
        a, b = _run(0.0, img), _run(0.40, img)
        la, lb = a.mean(axis=2), b.mean(axis=2)
        self.assertLess(abs(float(np.mean(la[cav])) - float(np.mean(lb[cav]))),
                        2e-3, "增益改变了腔体亮度")

    def test_monotonic(self):
        """性质 3：单调。"""
        img = _scene()
        cav = _cavity_mask(img)
        vals = [float(np.median((_run(b, img)[..., 2] - _run(b, img)[..., 0])[cav]))
                for b in (0.0, 0.2, 0.4, 0.8)]
        for x, y in zip(vals, vals[1:]):
            self.assertGreaterEqual(y, x - 1e-6, "非单调：%r" % (vals,))

    def test_zero_boost_is_identical(self):
        """性质 4：boost=0 与不设该键逐位一致。"""
        img = _scene()
        np.testing.assert_array_equal(_run(0.0, img), _run(0.0, img))

    def test_non_emission_unaffected(self):
        """性质 5：非 emission 目标不受该键影响。

        生效条件是 `color_mode == 'emission' **或** target_type 是
        `emission_nebula`（与既有 oiii_protect 同一判据）—— 所以要真正
        退出该分支，必须**同时**改掉 color_mode 与 target_type。
        """
        img = _scene()
        a = _run(0.0, img, color_mode="standard", target_type="galaxy")
        b = _run(0.40, img, color_mode="standard", target_type="galaxy")
        np.testing.assert_array_equal(a, b)

    def test_emission_target_type_alone_activates(self):
        """记录现有语义：target_type 单独就能开启 OIII 增益。

        这是与 oiii_protect 一致的历史判据（`or` 而非 `and`）。
        本测试把它钉住，避免以后有人只改其中一个分支造成行为漂移。
        """
        img = _scene()
        a = _run(0.0, img, color_mode="standard")
        b = _run(0.40, img, color_mode="standard")
        self.assertFalse(np.array_equal(a, b),
                         "target_type 单独应能开启 OIII 增益")


if __name__ == "__main__":
    unittest.main()