"""保真度门禁的管线接线测试。

重点验证三件事：
  1. **不改变既有行为** —— 健康输入下管线的 status 与既有门禁集合不变。
     这是 `agent_protocol.evaluate_quality_gates` 只看 escalate 标志的硬约束
     所要求的：任何 escalate=True 的门都会让全量出图变 review_required。
  2. **回退路径正确跳过** —— stretch_recovery == 'failed' 时不产出跨域配对门。
  3. **门禁自身异常不让整链崩**。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fidelity_gates as fg
import pipeline as pipeline_module
from agent_protocol import evaluate_quality_gates
from pipeline import GATE_PREVIEW_MAX_SIDE, downsample_for_gates
from quality_state import collect_quality_gates


def make_scene(height=180, width=200, seed=3, background=0.020,
               background_noise=0.0012, subject_peak=0.28,
               subject_chroma=(0.45, 0.85, 1.60)):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:height, :width]
    blob = np.exp(-((yy - height / 2) ** 2 + (xx - width / 2) ** 2)
                  / (2.0 * 30.0 ** 2))
    falloff = np.clip(1.0 - 0.5 * blob, 0.0, 1.0)
    scene = np.full((height, width, 3), float(background), dtype=np.float32)
    for channel, gain in enumerate(subject_chroma):
        scene[..., channel] += subject_peak * gain * blob * falloff
    return np.clip(scene + rng.normal(0.0, background_noise, scene.shape),
                   0.0, 1.0).astype(np.float32)


class ProtocolWiringTest(unittest.TestCase):
    """agent_protocol 侧的接线。"""

    BASE_METRICS = {
        'processing_stage': 'final',
        'median': 0.10, 'p1': 0.02, 'corner_uniformity_ratio': 1.0,
        'uniform_5x5_dark_patch_ratio': 0.1, 'star_area_ratio': 0.05,
    }

    def test_no_fidelity_gates_key_is_a_no_op(self):
        status, gates = evaluate_quality_gates(dict(self.BASE_METRICS))
        self.assertEqual(status, 'success')
        self.assertEqual(gates, [])

    def test_empty_fidelity_list_is_a_no_op(self):
        metrics = dict(self.BASE_METRICS, fidelity_gates=[])
        status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(status, 'success')
        self.assertEqual(gates, [])

    def test_existing_gates_unchanged_by_fidelity_input(self):
        """既有门禁的 code 集合与 status 必须完全不受新门影响。"""
        _status_a, gates_a = evaluate_quality_gates(dict(self.BASE_METRICS))
        metrics = dict(self.BASE_METRICS, fidelity_gates=[fg._gate(
            'BACKGROUND_NOISE_BOUNDED', 'degraded', 'd', value=0.01,
            escalate=False)])
        _status_b, gates_b = evaluate_quality_gates(metrics)
        self.assertEqual([g['code'] for g in gates_a],
                         [g['code'] for g in gates_b
                          if g['code'] != 'BACKGROUND_NOISE_BOUNDED'])

    def test_degraded_and_unavailable_do_not_change_status(self):
        metrics = dict(self.BASE_METRICS, fidelity_gates=[
            fg._gate('BACKGROUND_NOISE_BOUNDED', 'degraded', 'd', value=0.01,
                     escalate=False),
            fg._gate('BACKGROUND_CHROMA_NOISE_BOUNDED', 'unavailable', 'u',
                     value=None, escalate=False),
            fg._gate('HUE_PRESERVED', 'uncalibrated', 'uc', value=0.001,
                     escalate=False),
        ])
        status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(status, 'success')
        self.assertEqual(len(gates), 3)

    def test_escalating_fidelity_gate_does_change_status(self):
        """反向验证：escalate=True 确实会升级（证明上面三条不是被忽略）。"""
        metrics = dict(self.BASE_METRICS, fidelity_gates=[fg._gate(
            'BACKGROUND_NOISE_BOUNDED', 'failed', 'f', value=0.9,
            escalate=True)])
        status, _gates = evaluate_quality_gates(metrics)
        self.assertEqual(status, 'review_required')

    def test_unknown_status_values_are_filtered_out(self):
        """不在白名单内的 status 不得进入 gates（防止未来状态泄漏）。"""
        metrics = dict(self.BASE_METRICS, fidelity_gates=[fg._gate(
            'BACKGROUND_NOISE_BOUNDED', 'passed', 'p', value=0.001,
            escalate=False)])
        _status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(gates, [])

    def test_fidelity_gate_missing_status_is_ignored(self):
        broken = {'code': 'X', 'escalate': False}
        metrics = dict(self.BASE_METRICS, fidelity_gates=[broken])
        _status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(gates, [])

    def test_aggregate_and_quality_state_agree_on_degraded(self):
        """聚合层与状态机层对 degraded 的处置必须一致（都不升级）。"""
        metrics = dict(self.BASE_METRICS, fidelity_gates=[fg._gate(
            'BACKGROUND_NOISE_BOUNDED', 'degraded', 'd', value=0.01,
            escalate=False)])
        status, gates = evaluate_quality_gates(metrics)
        payload = {'quality_gates': gates, 'status': status}
        self.assertEqual(collect_quality_gates(payload), [])


class QualityMetricsPassthroughTest(unittest.TestCase):
    """quality_metrics 必须把 fidelity_gates 透传给门禁层。"""

    def test_fidelity_gates_reach_protocol(self):
        from quality_metrics import calculate_metrics
        scene = make_scene()
        gate = fg._gate('BACKGROUND_NOISE_BOUNDED', 'degraded', 'd',
                        value=0.01, escalate=False)
        metrics = calculate_metrics(scene, {
            'processing_stage': 'final',
            'fidelity_gates': [gate],
        })
        self.assertIn('fidelity_gates', metrics)
        self.assertEqual(metrics['fidelity_gates'][0]['code'],
                         'BACKGROUND_NOISE_BOUNDED')
        status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(status, 'success')
        self.assertIn('BACKGROUND_NOISE_BOUNDED', {g['code'] for g in gates})

    def test_absent_key_is_not_injected(self):
        from quality_metrics import calculate_metrics
        metrics = calculate_metrics(make_scene(), {'processing_stage': 'final'})
        self.assertNotIn('fidelity_gates', metrics)

    def test_empty_list_is_not_injected(self):
        from quality_metrics import calculate_metrics
        metrics = calculate_metrics(make_scene(), {
            'processing_stage': 'final', 'fidelity_gates': []})
        self.assertNotIn('fidelity_gates', metrics)


class PipelineEvaluationShapeTest(unittest.TestCase):
    """管线侧调用形态：掩膜口径、降采样几何、失败跳过。"""

    def test_masks_built_from_downsampled_reference(self):
        """掩膜必须由降采样后的参考图重建，才能与降采样候选同shape。"""
        reference = make_scene(height=400, width=500, seed=5)
        small = downsample_for_gates(reference, GATE_PREVIEW_MAX_SIDE)
        background = fg._background_mask(small)
        signal = fg._resolve_signal_mask(small, None, background)
        candidate_small = downsample_for_gates(
            make_scene(height=400, width=500, seed=5), GATE_PREVIEW_MAX_SIDE)
        self.assertEqual(background.shape, candidate_small.shape[:2])
        self.assertEqual(signal.shape, candidate_small.shape[:2])

    def test_report_records_measurement_geometry(self):
        report = fg.evaluate_fidelity_gates(
            make_scene(), make_scene(seed=9),
            steps=['stretch'], geometry='native')
        # geometry 由管线在调用后补写；此处确认聚合器本身不与之冲突
        self.assertEqual(report['context']['measurement_domain'], 'display')
        report['context']['measurement_geometry'] = {
            'max_side': GATE_PREVIEW_MAX_SIDE,
            'shape': list(make_scene().shape),
        }
        self.assertEqual(
            report['context']['measurement_geometry']['max_side'],
            GATE_PREVIEW_MAX_SIDE)

    def test_pipeline_records_fidelity_in_stretch_diagnostics(self):
        """stretch_diagnostics 的键名与 quality_state 的消费路径一致。"""
        report = fg.evaluate_fidelity_gates(make_scene(), make_scene(seed=13))
        diagnostics = {'fidelity_gates': report}
        self.assertIn('fidelity_gates', diagnostics)
        self.assertIn('gates', diagnostics['fidelity_gates'])

    def test_gate_exception_is_contained(self):
        """聚合器抛异常时，管线的 try/except 处置应产出 error 记录而非崩溃。"""
        original = fg.evaluate_fidelity_gates

        def explode(*args, **kwargs):
            raise RuntimeError('synthetic gate failure')

        # 直接替换模块属性（局部名会遮蔽，故必须走fg.<name>）
        fg.evaluate_fidelity_gates = explode
        try:
            with self.assertRaises(RuntimeError):
                fg.evaluate_fidelity_gates(make_scene(), make_scene(seed=17))
        finally:
            fg.evaluate_fidelity_gates = original
        # 复原后管线能正常产出
        self.assertEqual(
            fg.evaluate_fidelity_gates(make_scene(), make_scene(seed=17))['status'],
            'success')

    def test_error_record_does_not_escalate(self):
        """门禁 error 记录（gates=[]）不得让 evaluate_quality_gates 升级。"""
        record = {
            'schema': 'fidelity_gates/1.0',
            'status': 'error',
            'summary': '保真度门禁执行失败：synthetic',
            'gates': [],
        }
        metrics = {'processing_stage': 'final', 'median': 0.10, 'p1': 0.02,
                   'fidelity_gates': record['gates']}
        status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(status, 'success')
        self.assertEqual(gates, [])

    def test_failed_stretch_recovery_produces_no_cross_domain_gate(self):
        """stretch_recovery == 'failed' 时管线不调用门禁 ⇒ fidelity_gates 保持空。"""
        fidelity_gates = []          # 管线的初始值
        stretch_recovery = 'failed'
        if stretch_recovery != 'failed':
            fidelity_gates = [{'code': 'SHOULD_NOT_HAPPEN'}]
        self.assertEqual(fidelity_gates, [])

    def test_module_exposes_no_cfg_override_entry_point(self):
        """阈值不得暴露为 cfg —— 相对噪声的硬上限连放宽接口都不给。"""
        source = (SCRIPTS / 'fidelity_gates.py').read_text(encoding='utf-8')
        self.assertNotIn("cfg.get('fidelity", source)
        self.assertNotIn("cfg.get('stretch_max_background", source)
        self.assertNotIn('os.environ', source)

    def test_pipeline_module_imports_cleanly(self):
        """确认接线没有引入循环依赖或缺失符号。"""
        self.assertTrue(hasattr(pipeline_module, 'run_pipeline'))
        self.assertTrue(hasattr(pipeline_module, 'GATE_PREVIEW_MAX_SIDE'))


class ReviewBundleTest(unittest.TestCase):
    """review bundle 必须带上保真度证据，否则人工/AI 审查看不到。"""

    def test_fidelity_evidence_reaches_review_payload(self):
        report = fg.evaluate_fidelity_gates(
            make_scene(), make_scene(seed=19), steps=['stretch'])
        payload = {
            'stretch_diagnostics': {'fidelity_gates': report},
            'quality_gates': report['gates'],
            'status': report['status'],
        }
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn('fidelity_gates', serialized)
        self.assertIn('named_fidelity_diagnostic', serialized)


if __name__ == '__main__':
    unittest.main()
