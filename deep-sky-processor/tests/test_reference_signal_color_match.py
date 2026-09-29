"""参考图的**信号区色比匹配**（`reference_grade.match_signal_color`）。

背景：P0-3「白平衡/色比旋钮」。实测过两条现有路径都不解决它：

| 路径 | 结果 |
|---|---|
| `_background_channel_gains` | 只看背景（luma ≤ p60），**不触及星云 Hα/OIII 比** |
| `optimize_reference_grade` | 搜索空间是 stretch/gamma/bg/sat/hdr，**没有色比控制** —— 实测把源 R/G 1.77 冲到 **0.79**（目标 1.37），B/G 完全不动（0.73） |

而**闭式解通道增益**能精确命中：

```
gR = (R/G)_ref / (R/G)_src ,  gG = 1 ,  gB = (B/G)_ref / (B/G)_src
```

真实数据（源 R/G=1.769 / B/G=0.727，参考 R/G=1.375 / B/G=0.993）
→ 解出 [0.777, 1.0, 1.365]，命中后 R/G 误差 **0.0%**、B/G 误差 **1.1%**（限幅所致）。

本文件锁住：闭式解正确性、strength 混合、限幅与报告、信号不足的降级。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from reference_grade import match_signal_color, _signal_ratios  # noqa: E402


def _scene(r, g, b, h=96, w=96, bg=0.01, patch=36):
    """深色背景 + 中央均匀色块（模拟星云主体）。"""
    image = np.full((h, w, 3), bg, dtype=np.float32)
    y0, x0 = (h - patch) // 2, (w - patch) // 2
    image[y0:y0 + patch, x0:x0 + patch] = (r, g, b)
    return image


# 与真实数据同量级的色比：源 R/G=1.77 B/G=0.73，参考 R/G=1.375 B/G=0.993
SOURCE = _scene(0.3540, 0.2000, 0.1460)
REFERENCE = _scene(0.2750, 0.2000, 0.1986)


class SignalColorMatchTests(unittest.TestCase):
    def test_closed_form_hits_reference_ratios(self):
        """① 核心：命中后色比必须等于参考图。"""
        out, report = match_signal_color(SOURCE, REFERENCE, strength=1.0)
        self.assertTrue(report["applied"])
        got = _signal_ratios(out)
        want = _signal_ratios(REFERENCE)
        self.assertAlmostEqual(got["r_over_g"], want["r_over_g"], delta=0.01)
        self.assertAlmostEqual(got["b_over_g"], want["b_over_g"], delta=0.01)

    def test_gains_are_closed_form(self):
        """② 增益必须等于解析解（gG 恒为 1，只动 R/B）。"""
        _, report = match_signal_color(SOURCE, REFERENCE, strength=1.0, max_gain=10.0)
        src = _signal_ratios(SOURCE)
        ref = _signal_ratios(REFERENCE)
        self.assertAlmostEqual(report["gains"][0], ref["r_over_g"] / src["r_over_g"],
                               places=5)
        self.assertEqual(report["gains"][1], 1.0)
        self.assertAlmostEqual(report["gains"][2], ref["b_over_g"] / src["b_over_g"],
                               places=5)

    def test_strength_zero_is_identity(self):
        """③ strength=0 → 逐位恒等（可安全用于"只看不应用"）。"""
        out, report = match_signal_color(SOURCE, REFERENCE, strength=0.0)
        self.assertTrue(np.allclose(out, SOURCE, atol=1e-6))
        self.assertTrue(np.allclose(report["effective_gains"], 1.0, atol=1e-6))

    def test_strength_blends_monotonically(self):
        """④ strength 越大越接近参考色比。"""
        errs = []
        want = _signal_ratios(REFERENCE)["r_over_g"]
        for s in (0.0, 0.35, 0.7, 1.0):
            out, _ = match_signal_color(SOURCE, REFERENCE, strength=s)
            errs.append(abs(_signal_ratios(out)["r_over_g"] - want))
        self.assertTrue(all(b <= a + 1e-9 for a, b in zip(errs, errs[1:])), errs)

    def test_max_gain_clips_and_reports(self):
        """⑤ 极端参考 → 增益被限幅，且报告里标记 clipped。"""
        extreme = _scene(0.90, 0.10, 0.10)      # R/G=9
        out, report = match_signal_color(SOURCE, extreme, strength=1.0, max_gain=1.45)
        self.assertTrue(report["clipped"])
        self.assertLessEqual(max(report["gains"]), 1.45 + 1e-6)
        self.assertGreaterEqual(min(report["gains"]), 1.0 / 1.45 - 1e-6)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_unclipped_case_is_not_flagged(self):
        _, report = match_signal_color(SOURCE, REFERENCE, strength=1.0, max_gain=10.0)
        self.assertFalse(report["clipped"])

    def test_insufficient_signal_degrades_gracefully(self):
        """⑥ 无信号结构（均匀图）→ applied=False，原样返回。"""
        flat = np.full((48, 48, 3), 0.05, dtype=np.float32)
        out, report = match_signal_color(flat, REFERENCE)
        self.assertFalse(report["applied"])
        self.assertEqual(report["reason"], "insufficient_signal")
        self.assertTrue(np.allclose(out, flat, atol=1e-6))

    def test_output_stays_in_range_and_finite(self):
        for s in (0.0, 0.5, 1.0):
            out, _ = match_signal_color(SOURCE, REFERENCE, strength=s)
            self.assertTrue(np.all(np.isfinite(out)))
            self.assertGreaterEqual(float(out.min()), 0.0)
            self.assertLessEqual(float(out.max()), 1.0)

    def test_report_carries_audit_fields(self):
        """⑦ 报告必须可直接写进 manifest 供审计（源/参考/结果三方色比 + 增益）。"""
        _, report = match_signal_color(SOURCE, REFERENCE, strength=0.8)
        for key in ("method", "applied", "source_ratios", "reference_ratios",
                    "gains", "raw_gains", "effective_gains", "strength",
                    "max_gain", "clipped", "result_ratios"):
            self.assertIn(key, report)
        self.assertEqual(report["method"], "signal_color_match")
        for side in ("source_ratios", "reference_ratios", "result_ratios"):
            self.assertIn("r_over_g", report[side])
            self.assertIn("b_over_g", report[side])

    def test_does_not_touch_green_channel_scale(self):
        """⑧ gG 恒为 1 → 绿色通道整体量级不被改动（只改色比，不改曝光）。"""
        out, report = match_signal_color(SOURCE, REFERENCE, strength=1.0)
        self.assertEqual(report["gains"][1], 1.0)
        self.assertAlmostEqual(
            float(np.median(out[..., 1])), float(np.median(SOURCE[..., 1])),
            delta=1e-6,
        )


if __name__ == "__main__":
    unittest.main()
