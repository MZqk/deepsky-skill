"""端到端：外部无星层注入时的通道完整性硬失败，以及 MTF 域回流。

复现的故障（实测）：用户对同一张极暗 Duo-Band 母版连跑 7 轮。第 3 轮起用
`--external-starless` 注入手动跑的 StarNet2 无星层 —— 那层在星云区 G 通道有
93% 像素精确为 0。旧版 `verify_external_starless` 只查尺寸与星点能量，返回
`valid: True`，管线静默放行，成片整片星云变纯红（R/G 从 1.70 涨到 9.4）。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imsave


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline  # noqa: E402
from stretch import derive_mtf_midtones, inverse_mtf_stretch, mtf_stretch  # noqa: E402


H, W = 96, 128


def _scene():
    """暗背景 + Hα 主导、OIII 真实存在的星云 + 两颗星。

    用 8-bit 可存活的值（G 幅度 0.09 → 23 级），避免测试本身被量化干扰。
    """
    rng = np.random.default_rng(19)
    starless = rng.normal(loc=0.02, scale=0.002, size=(H, W, 3)).astype(np.float32)
    starless = np.clip(starless, 0.002, 1.0)
    starless[26:70, 30:98, 0] += 0.25   # Hα
    starless[26:70, 30:98, 1] += 0.09   # OIII
    starless[26:70, 30:98, 2] += 0.07   # OIII
    stars = np.zeros((H, W, 3), np.float32)
    stars[12:15, 14:17] = [0.8, 0.9, 1.0]
    stars[40:43, 100:103] = [0.9, 0.8, 0.6]
    full = np.clip(starless + stars, 0.0, 1.0)
    return full, starless


class ExternalStarlessE2ETests(unittest.TestCase):
    def _write_inputs(self, td):
        full, starless = _scene()
        src = os.path.join(td, "input.png")
        imsave(src, (np.clip(full, 0, 1) * 255).astype(np.uint8))
        return src, starless

    def test_pipeline_rejects_channel_collapsed_starless(self):
        """G 通道被清零的无星层必须硬失败，而不是静默产出纯红成片。"""
        with tempfile.TemporaryDirectory() as td:
            src, starless = self._write_inputs(td)
            collapsed = starless.copy()
            collapsed[..., 1] = 0.0            # 复现实测故障形态
            collapsed_path = os.path.join(td, "starless_collapsed.tif")
            imsave(collapsed_path, collapsed.astype(np.float32))

            with self.assertRaises(ValueError) as ctx:
                pipeline.run_pipeline(
                    src, os.path.join(td, "out.jpg"),
                    external_starless=collapsed_path,
                    preset="light",
                    steps="star_remove,stretch",
                    keep_all=True,
                    work_dir=os.path.join(td, "work"),
                )
            message = str(ctx.exception)
            self.assertTrue("通道" in message or "G" in message,
                            f"错误信息未指出通道塌缩: {message}")

    def test_pipeline_accepts_healthy_linear_starless(self):
        """负例：健康的线性无星层照常通过（向后兼容）。"""
        with tempfile.TemporaryDirectory() as td:
            src, starless = self._write_inputs(td)
            path = os.path.join(td, "starless_ok.tif")
            imsave(path, starless.astype(np.float32))

            result = pipeline.run_pipeline(
                src, os.path.join(td, "out.jpg"),
                external_starless=path,
                preset="light",
                steps="star_remove,stretch",
                keep_all=True,
                work_dir=os.path.join(td, "work"),
            )
            self.assertTrue(os.path.exists(os.path.join(td, "out.jpg")))
            diag = result.get("external_starless_diagnostics")
            self.assertIsNotNone(diag)
            self.assertEqual(diag["domain"], "linear")

    def test_pipeline_accepts_mtf_domain_roundtrip(self):
        """负例：MTF 域无星层经逆变换后通过，且通道完整性检查放行。"""
        with tempfile.TemporaryDirectory() as td:
            src, starless = self._write_inputs(td)
            m = derive_mtf_midtones(starless)
            payload = (mtf_stretch(starless, midtones=m) * 255).astype(np.uint8)
            payload_path = os.path.join(td, "starless_mtf.png")
            imsave(payload_path, payload)

            result = pipeline.run_pipeline(
                src, os.path.join(td, "out.jpg"),
                external_starless=payload_path,
                external_starless_domain="mtf",
                external_starless_midtones=m,
                preset="light",
                steps="star_remove,stretch",
                keep_all=True,
                work_dir=os.path.join(td, "work"),
            )
            self.assertTrue(os.path.exists(os.path.join(td, "out.jpg")))
            diag = result.get("external_starless_diagnostics")
            self.assertEqual(diag["domain"], "mtf")
            # 逆变换后 G 通道应仍然存活
            self.assertEqual(
                diag["channel_integrity"]["channels"]["g"]["collapsed"], False
            )

    def test_sidecar_auto_resolves_mtf_domain(self):
        """不显式传 domain 时，work-dir 里的 sidecar 应自动识别 mtf。"""
        with tempfile.TemporaryDirectory() as td:
            src, starless = self._write_inputs(td)
            work_dir = os.path.join(td, "work")
            os.makedirs(work_dir, exist_ok=True)

            from neural_star_bridge import write_starnet_payload_meta
            m = derive_mtf_midtones(starless)
            write_starnet_payload_meta(work_dir, domain="mtf", midtones=m)

            payload = (mtf_stretch(starless, midtones=m) * 255).astype(np.uint8)
            payload_path = os.path.join(td, "starless_mtf.png")
            imsave(payload_path, payload)

            result = pipeline.run_pipeline(
                src, os.path.join(td, "out.jpg"),
                external_starless=payload_path,
                preset="light",
                steps="star_remove,stretch",
                keep_all=True,
                work_dir=work_dir,
            )
            self.assertEqual(
                result["external_starless_diagnostics"]["domain"], "mtf"
            )

    def test_inverse_mtf_recovers_original_ratios(self):
        """纯函数级验证：逆 MTF 后通道比值与原始一致（不依赖管线）。"""
        _, starless = _scene()
        m = derive_mtf_midtones(starless)
        back = inverse_mtf_stretch(mtf_stretch(starless, midtones=m), midtones=m)
        lum = back.mean(axis=2)
        mask = lum > np.percentile(lum, 60)
        r_in = np.median(starless[..., 0][mask]) / np.median(starless[..., 1][mask])
        r_out = np.median(back[..., 0][mask]) / np.median(back[..., 1][mask])
        self.assertLess(abs(r_out - r_in) / r_in, 0.02)


if __name__ == "__main__":
    unittest.main()
