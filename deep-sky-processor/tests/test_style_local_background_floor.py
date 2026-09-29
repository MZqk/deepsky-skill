"""风格色调曲线的黑位必须局部自适应，不得把局部暗背景裁成纯黑。

旧实现用一个**全局标量**黑位：

    floor = min(p5(L) + black_floor, p25(L) * 0.5, 0.25)

当背景本身空间非均匀时（实测某图四角 0.0855 / 0.0564 / 0.0628 / **0.0413**），
全局 floor 0.043 会超过最暗角落的局部背景 0.0413，把整片裁成纯黑；下游
`gain = blended_luminance / luminance` 在 `strength=1.0` 时对 `toned==0` 的
像素给出 `gain = 0`，于是该角被乘成 0。实测四角比值从 2.07 一步跳到 **5203**，
触发 CORNER_NONUNIFORM，整条管线变成 review_required。

修法：逐像素局部黑位（分块低分位 + 平滑上采样）+ 确定性后置自愈。
本文件同时锁住两条性质：
  1. 空间非均匀背景下，输出不得有任何 block×block 区块中位数为 0；
  2. **均匀背景下与旧公式逐像素完全一致**（这是关键的不回归保证）。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from style_tools import (  # noqa: E402
    STYLE_PROFILES,
    _block_medians,
    _tone_curve,
    apply_professional_style,
)
from agent_protocol import evaluate_quality_gates  # noqa: E402


STYLE = "dramatic_nebula"
STYLE_KW = dict(
    style=STYLE,
    target_type="emission_nebula",
    color_mode="emission",
)


def _nonuniform_scene(height=320, width=320, seed=7):
    """背景空间非均匀：TL≈0.09 → BR≈0.005，中央有明亮星云主体。

    这组参数实测能在**旧**实现上复现 3/16 个区块被裁到纯黑、8.16% 像素归零。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    background = 0.09 - 0.085 * (yy / height) * (xx / width)
    glow = np.exp(-(((xx - width * 0.5) / (width * 0.22)) ** 2
                    + ((yy - height * 0.5) / (height * 0.22)) ** 2))
    base = background + 0.5 * glow
    image = np.stack([base, base * 0.82, base * 0.68], axis=-1)
    image = image + rng.normal(0, 2e-3, image.shape)
    return np.clip(image, 0, 1).astype(np.float32)


def _uniform_scene(height=192, width=192, background=0.05, peak=0.6):
    """均匀背景 + 中央亮方块（小图会退化为单块 → 必须与旧公式完全一致）。"""
    lum = np.full((height, width), background, dtype=np.float32)
    lum[height // 3: height * 2 // 3, width // 3: width * 2 // 3] = peak
    return lum


def _old_tone_curve(luminance, profile):
    """修复前的实现，仅用于不回归对照。"""
    low = float(np.percentile(luminance, 5))
    background = float(np.percentile(luminance, 25))
    floor = min(max(low, 0.0) + profile["black_floor"], background * 0.5, 0.25)
    toned = np.clip((luminance - floor) / max(1.0 - floor, 1e-6), 0, 1)
    toned = np.power(toned, profile["gamma"])
    contrast = profile["contrast"]
    toned = np.clip(
        toned + contrast * (toned - 0.5) * 4.0 * toned * (1.0 - toned), 0, 1
    )
    rolloff = profile["highlight_rolloff"]
    if rolloff > 0:
        compressed = toned / (1.0 + rolloff * toned)
        compressed /= max(float(compressed.max()), 1e-6)
        toned = np.clip(compressed, 0, 1)
    return toned.astype(np.float32)


class LocalBackgroundFloorTests(unittest.TestCase):
    def test_nonuniform_background_leaves_no_black_block(self):
        """核心：空间非均匀背景下不得有任何区块中位数为 0。"""
        image = _nonuniform_scene()
        graded, _, _, diag = apply_professional_style(
            image, strength=1.0, return_diagnostics=True, **STYLE_KW
        )

        # 场景确实复现了风险（否则这个用例什么都没测到）
        self.assertGreater(diag["blocks_clipped_before_repair"], 0)
        # 但自愈之后必须干净
        self.assertEqual(diag["blocks_clipped_after_repair"], 0)
        self.assertEqual(diag["blocks_zeroed_final"], 0)

        medians = _block_medians(graded.mean(axis=2), diag["block_size"])
        self.assertGreater(float(medians.min()), 0.0)
        self.assertGreater(diag["min_block_median_final"], 0.0)

        # 最暗角不得被压成纯黑
        h, w = graded.shape[:2]
        cs = min(h, w) // 8
        bottom_right = float(np.median(graded[-cs:, -cs:].mean(axis=2)))
        self.assertGreater(bottom_right, 1e-3)
        self.assertEqual(float((graded.mean(axis=2) <= 0.0).mean()), 0.0)

    def test_uniform_background_matches_old_formula(self):
        """均匀背景（含单块退化）必须与旧公式逐像素一致。"""
        profile = dict(STYLE_PROFILES[STYLE])
        luminance = _uniform_scene()

        toned, diag = _tone_curve(luminance, profile, return_diagnostics=True)
        reference = _old_tone_curve(luminance, profile)

        self.assertAlmostEqual(diag["floor_min"], diag["floor_global"], places=6)
        self.assertAlmostEqual(diag["floor_max"], diag["floor_global"], places=6)
        self.assertTrue(np.array_equal(toned, reference))

        # 小图退化为单块：局部 == 全局
        small = _uniform_scene(height=40, width=40)
        _, small_diag = _tone_curve(small, profile, return_diagnostics=True)
        self.assertEqual(small_diag["n_blocks"], 1)
        self.assertEqual(small_diag["floor_min"], small_diag["floor_global"])
        self.assertEqual(small_diag["floor_max"], small_diag["floor_global"])

    def test_new_floor_is_never_higher_and_recovers_clipped_pixels(self):
        """黑位不升高、归零像素严格减少、净效果变亮。

        注意：**不能**断言「最终 toned 逐像素只会更亮」。末端的 highlight_rolloff
        做了 `compressed /= max(compressed)` 的全局重标定，分母会随整体变亮而变大，
        因此少数中间调像素可能轻微下降（实测最大 -1.9e-4）。可保证的是下面四条。
        """
        profile = dict(STYLE_PROFILES[STYLE])
        image = _nonuniform_scene(height=192, width=192)
        from color_conv import safe_rgb2lab as rgb2lab

        luminance = np.clip(rgb2lab(image)[..., 0] / 100.0, 0, 1)
        toned, diag = _tone_curve(luminance, profile, return_diagnostics=True)
        reference = _old_tone_curve(luminance, profile)

        # 1) 黑位不升高（这是严格成立的不变量）
        self.assertLessEqual(diag["floor_max"], diag["floor_global"] + 1e-9)
        # 2) 归零像素严格减少
        self.assertLess(
            int(np.count_nonzero(toned <= 0)),
            int(np.count_nonzero(reference <= 0)),
        )
        # 3) 少数中间调的下行幅度可忽略
        self.assertGreaterEqual(float((toned - reference).min()), -5e-3)
        # 4) 净效果为变亮
        self.assertGreater(float(np.median(toned - reference)), 0.0)

    def test_strength_below_one_never_blackens(self):
        image = _nonuniform_scene(height=192, width=192)
        magnitudes = {}
        for strength in (0.3, 0.6, 1.0):
            graded, _, _, diag = apply_professional_style(
                image, strength=strength, return_diagnostics=True, **STYLE_KW
            )
            self.assertEqual(diag["blocks_zeroed_final"], 0)
            self.assertGreater(float(_block_medians(graded.mean(axis=2)).min()), 0.0)
            magnitudes[strength] = float(np.mean(np.abs(graded - image)))

        self.assertLess(magnitudes[0.6], magnitudes[1.0])

    def test_diagnostics_fields_and_invariants(self):
        image = _nonuniform_scene(height=192, width=192)
        result = apply_professional_style(
            image, strength=1.0, return_diagnostics=True, **STYLE_KW
        )
        self.assertEqual(len(result), 4)
        graded, selected, reasoning, diag = result
        self.assertEqual(selected, STYLE)

        required = {
            "floor_global", "floor_min", "floor_max", "floor_mean",
            "local_bg_min", "local_bg_max", "block_size", "block_grid",
            "n_blocks", "blocks_clipped_before_repair",
            "blocks_clipped_after_repair", "repair_shift", "repair_applied",
            "clipped_block_frac_before", "min_block_median_final",
            "blocks_zeroed_final",
        }
        self.assertTrue(required.issubset(diag.keys()), required - set(diag))

        self.assertLessEqual(diag["floor_min"], diag["floor_max"])
        self.assertEqual(diag["n_blocks"], diag["block_grid"][0] * diag["block_grid"][1])
        self.assertGreaterEqual(
            diag["blocks_clipped_before_repair"],
            diag["blocks_clipped_after_repair"],
        )
        self.assertEqual(diag["blocks_clipped_after_repair"], 0)
        self.assertLessEqual(diag["floor_max"], diag["floor_global"] + 1e-9)
        self.assertLessEqual(diag["floor_min"], diag["local_bg_min"] + 1e-9)
        self.assertEqual(graded.shape, image.shape)

        # 向后兼容：默认调用仍返回三元组
        legacy = apply_professional_style(image, strength=1.0, **STYLE_KW)
        self.assertEqual(len(legacy), 3)

    def _gate_metrics(self, style_diagnostics):
        return {
            "processing_stage": "final",
            "median": 0.08,
            "p1": 0.01,
            "nonpositive_pixel_ratio": 0.0,
            "corner_uniformity_ratio": 1.5,
            "uniform_5x5_dark_patch_ratio": 0.05,
            "star_area_ratio": 0.02,
            "style_diagnostics": style_diagnostics,
        }

    def test_gate_warns_on_local_clip_risk_without_escalating(self):
        """自愈已保证产物安全 → 只记录，不升级 status。"""
        status, gates = evaluate_quality_gates(
            self._gate_metrics({
                "clipped_block_frac_before": 0.40,
                "blocks_clipped_before_repair": 6,
                "blocks_clipped_after_repair": 0,
                "blocks_zeroed_final": 0,
                "repair_shift": 0.02,
                "n_blocks": 16,
                "block_size": 96,
            }),
            target_type="emission_nebula",
        )
        gate = next(g for g in gates if g["code"] == "STYLE_LOCAL_BLACK_CLIP")
        self.assertEqual(gate["status"], "warning")
        self.assertFalse(gate["escalate"])
        self.assertEqual(status, "success")

    def test_gate_fails_when_output_block_is_black(self):
        status, gates = evaluate_quality_gates(
            self._gate_metrics({
                "clipped_block_frac_before": 0.40,
                "blocks_clipped_before_repair": 6,
                "blocks_clipped_after_repair": 0,
                "blocks_zeroed_final": 3,
                "repair_shift": 0.02,
                "n_blocks": 16,
                "block_size": 96,
            }),
            target_type="emission_nebula",
        )
        gate = next(g for g in gates if g["code"] == "STYLE_LOCAL_BLACK_CLIP")
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["escalate"])
        self.assertEqual(status, "review_required")

    def test_gate_absent_without_style_diagnostics(self):
        """无 style_diagnostics 时不 append —— 保护 gates[0] 的既有断言。"""
        status, gates = evaluate_quality_gates(
            {
                "median": 0.08,
                "corner_uniformity_ratio": 4.2,
                "uniform_5x5_dark_patch_ratio": 0.1,
                "star_area_ratio": 0.02,
            },
            target_type="emission_nebula",
            steps=["dbe"],
        )
        codes = [g["code"] for g in gates]
        self.assertNotIn("STYLE_LOCAL_BLACK_CLIP", codes)
        self.assertEqual(gates[0]["code"], "CORNER_NONUNIFORM")

    def test_gate_absent_when_risk_is_below_threshold(self):
        status, gates = evaluate_quality_gates(
            self._gate_metrics({
                "clipped_block_frac_before": 0.0,
                "blocks_clipped_before_repair": 0,
                "blocks_clipped_after_repair": 0,
                "blocks_zeroed_final": 0,
                "repair_shift": 0.0,
                "n_blocks": 16,
                "block_size": 96,
            }),
            target_type="emission_nebula",
        )
        codes = [g["code"] for g in gates]
        self.assertNotIn("STYLE_LOCAL_BLACK_CLIP", codes)
        self.assertEqual(status, "success")


if __name__ == "__main__":
    unittest.main()
