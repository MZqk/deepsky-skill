"""阶段「执行了但效果为 0」必须自检，不能靠 save_artifact 冒充已执行。

`cfg.completed` 由 `save_artifact` 追加，即「save 了就算 executed」。因此一个
被蒙版塌陷毁掉的阶段照样进 `executed_steps` 拿「已执行」背书 —— 实测 NGC6888
上 `local_enhance` 的 HF 放大恒为 1.0026×、`mean|d|` 1.6e-5，数值上等同没跑，
但日志打 `✓`。

本文件锁住两件事：
  1. `measure_stage_effect` 能区分「空操作」与「无法判定」；
  2. 空操作阶段被记入 `skipped_steps`（reason=`no_op_zero_effect`），
     且从 `executed_steps` 中摘出。
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from enhance import (  # noqa: E402
    MIN_EFFECTIVE_HF_AMPLIFICATION,
    measure_stage_effect,
)
from fits_io import read_image, write_image  # noqa: E402
from pipeline import run_pipeline  # noqa: E402


def _nebula(size=256, amplitude=0.35):
    """带空间结构的合成星云：常量图没有信号区，会被正确判为「无法判定」。"""
    yy, xx = np.mgrid[0:size, 0:size]
    base = 0.02 + amplitude * np.exp(
        -(((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (2 * (size / 6.0) ** 2))
    )
    return np.stack([base * 1.2, base * 0.8, base * 0.9], axis=-1).clip(0, 1).astype(np.float32)


class MeasureStageEffectTests(unittest.TestCase):
    def test_flat_input_returns_none_not_no_op(self):
        """无法判定 ≠ 空操作。常数图没有信号区，`_nebula_hf_energy` 给 None。"""
        flat = np.full((64, 64, 3), 0.2, np.float32)
        self.assertIsNone(measure_stage_effect(flat, flat))

    def test_identical_arrays_are_no_op(self):
        rng = np.random.default_rng(5)
        scene = rng.uniform(0.05, 0.3, (128, 128, 3)).astype(np.float32)
        effect = measure_stage_effect(scene, scene.copy())
        self.assertTrue(effect['no_op'])
        self.assertEqual(effect['hf_amplification'], 1.0)

    def test_real_enhancement_is_not_flagged(self):
        nebula = _nebula()
        effect = measure_stage_effect(nebula, nebula * 1.10)
        self.assertFalse(effect['no_op'])
        self.assertGreaterEqual(effect['hf_amplification'],
                                MIN_EFFECTIVE_HF_AMPLIFICATION)

    def test_threshold_sits_below_a_real_enhancement(self):
        """阈值必须低于真实有效的增强 —— 否则会把正常增强判成空操作。"""
        self.assertLess(MIN_EFFECTIVE_HF_AMPLIFICATION, 1.05)


class NoOpSkippedReasonTests(unittest.TestCase):
    """空操作阶段被记入 skipped_steps 且从 executed_steps 摘出。"""

    def _run(self, tmp_path, steps='local_enhance', **kwargs):
        source = _nebula(size=192)
        path = tmp_path / 'source.tif'
        write_image(source, path)
        return run_pipeline(
            str(path), str(tmp_path / 'out.tif'), steps=steps, preset='light',
            input_is_linear=True, auto_crop_edges=False, recognize=False,
            background_neutralize_lock=False, **kwargs
        )

    def test_identical_stage_output_is_reported_as_no_op(self):
        """把 local_nebula_enhance 打成恒等，模拟「跑了但没效果」。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            with patch('pipeline.local_nebula_enhance',
                       side_effect=lambda x, **kwargs: x.copy()):
                result = self._run(Path(td))
        reasons = {item['step']: item['reason']
                   for item in result['execution']['skipped_steps']}
        self.assertEqual(reasons.get('local_enhance'), 'no_op_zero_effect')
        self.assertNotIn('local_enhance', result['execution']['executed_steps'])

    def test_genuine_enhancement_is_not_marked_no_op(self):
        """真实增强不应被误判为空操作（改动 5 修好蒙版后就是这种情况）。"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = self._run(Path(td), local_strength=0.9)
        reasons = {item['step']: item['reason']
                   for item in result['execution']['skipped_steps']}
        self.assertNotEqual(reasons.get('local_enhance'), 'no_op_zero_effect')


class LocalStrengthOverrideTests(unittest.TestCase):
    """`local_strength` 必须走 cfg.get —— 现状用裸形参导致 override 失效。"""

    def test_override_is_applied_and_recorded_as_consumed(self):
        import tempfile
        source = _nebula(size=192)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / 'source.tif'
            write_image(source, path)
            result = run_pipeline(
                str(path), str(root / 'out.tif'), steps='local_enhance',
                preset='light', input_is_linear=True, auto_crop_edges=False,
                recognize=False, background_neutralize_lock=False,
                local_strength=0.9,
            )
        used = result['execution']['used_params'].get('local_enhance', {})
        self.assertEqual(used.get('local_strength'), 0.9)
        self.assertNotIn('local_strength', result['execution']['unused_params'])


if __name__ == '__main__':
    unittest.main()
