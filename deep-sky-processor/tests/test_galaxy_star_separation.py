"""星系星点分离回归测试。

背景：星系目标此前被安全规则**完全禁止**去星，理由是核球与星系盘无法靠亮度
区分。但代价被实测证明更高：全局 max 由稀疏亮星决定，主体被压进归一化轴最
底端 —— 某母版星点独占 99.1% 的高光余量，主体 p99 仅占余量的 0.74%，于是
盘面/天空比被锁死在 ~1.28，任何拉伸参数都改不动。

改为「核球保护 + 分离」后，主体在归一化轴上从 [0.0025, 0.0074] 移到
[0.287, 0.832] —— 99.1% 的余量被释放。

以下用例锁定：星团/双星仍不分离、星系默认分离、Agent 逐阶段不静默扩步。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pipeline import GALAXY_CORE_PROTECTION_FRACTION, run_pipeline  # noqa: E402
from target_rules import is_star_poi_target, resolve_target_type  # noqa: E402
from fits_io import write_image  # noqa: E402


def galaxy_scene(size=96, seed=4242):
    """弥散盘面星系 + 密集星场，最亮星远离星系。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.full((size, size), 0.002, dtype=np.float32)
    img += rng.normal(0, 0.00012, (size, size)).astype(np.float32)
    r = np.sqrt((xx - size / 2.0) ** 2 + (yy - size / 2.0) ** 2)
    disk = 0.9 * np.exp(-r / 5.0) + 0.3 * np.exp(-r / 14.0)
    img += (disk * 0.004).astype(np.float32)
    for _ in range(70):
        y = int(rng.integers(2, size - 2))
        x = int(rng.integers(2, size - 2))
        img[y - 1:y + 2, x - 1:x + 2] += np.float32(rng.uniform(0.05, 0.5))
    # 最亮星远离盘面
    img[size - 8:size - 5, size - 8:size - 5] += 2.0
    return np.clip(img, 0, None)


class StarDominantTargetRuleTests(unittest.TestCase):
    def test_clusters_and_double_star_skip_separation(self):
        for kind in ("globular_cluster", "open_cluster", "star_cluster", "double_star"):
            self.assertTrue(is_star_poi_target(kind, None), kind)

    def test_m45_is_star_dominant(self):
        self.assertTrue(is_star_poi_target(None, "M45"))

    def test_galaxy_and_nebula_are_not_star_dominant(self):
        for kind in ("galaxy", "emission_nebula", "planetary_nebula", "wide_field"):
            self.assertFalse(is_star_poi_target(kind, None), kind)

    def test_double_star_is_a_known_type(self):
        self.assertEqual(resolve_target_type("double_star", None), "double_star")


class GalaxySeparationDefaultTests(unittest.TestCase):
    def _run(self, tmp, **kwargs):
        src = Path(tmp) / "source.tif"
        write_image(np.repeat(galaxy_scene()[:, :, None], 3, axis=2), src)
        return run_pipeline(str(src), str(Path(tmp) / "out.tif"),
                           target_type="galaxy", input_is_linear=True, **kwargs)

    def test_galaxy_attempts_separation_without_subject_mask(self):
        """完整管线（不显式指定 steps）时，星系默认**尝试**分离链路。

        这里断言「尝试过」而非「成功」：合成图上内置形态学去星可能因质量
        门禁回退，但分离阶段必须已经执行 —— 这正是本次修复要锁定的行为。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(tmp)
            # star_remove 执行过，或已回退并记录回退状态，二者必居其一
            # executed_steps 会剔除已回退的步骤，所以直接查 star_removal 段。
            removal = result.get("star_removal")
            self.assertIsNotNone(removal, "星系未记录 star_removal 段")
            self.assertFalse(removal.get("skipped"), "星系去星被静默跳过")

    def test_explicit_steps_are_never_silently_expanded(self):
        """Agent 逐阶段 run_step 会显式传 steps，此时绝不能静默补步。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run(tmp, steps=["dbe"])
            self.assertEqual(result["execution"]["executed_steps"], ["dbe"])

    def test_core_protection_fraction_is_sane(self):
        self.assertGreater(GALAXY_CORE_PROTECTION_FRACTION, 0)
        self.assertLess(GALAXY_CORE_PROTECTION_FRACTION, 0.5)

    def test_cluster_target_still_skips_separation(self):
        import tempfile
        from pipeline import run_pipeline as rp

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.tif"
            write_image(np.repeat(galaxy_scene()[:, :, None], 3, axis=2), src)
            result = rp(str(src), str(Path(tmp) / "o.tif"),
                        target_type="open_cluster", input_is_linear=True)
            executed = result["execution"]["executed_steps"]
            self.assertNotIn("star_remove", executed)


if __name__ == "__main__":
    unittest.main()