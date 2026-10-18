"""门禁授权契约（gate_authority）测试。

核心断言：**授权字段被改写时门禁必须失去授权，诊断字段被改写时授权不变。**

背景：`collect_quality_gates` / `build_quality_state` 此前直接读
``gate.get('status') in ('warning','failed')`` 与 ``gate.get('escalate')``，
而这两个字段由门禁模块自己填 —— 任何一环改错都会让质量继承链**静默失效**。
本模块用「创建时封印 + 聚合时复验」把这条循环依赖打断，思路借鉴
starun 侧 `stage8_handoff.py:20` 的 `_UNBOUND_DIAGNOSTIC_FIELDS`。
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import gate_authority as ga
import quality_state
from artifact_gates import _gate as artifact_gate
from fidelity_gates import _gate as fidelity_gate
from quality_state import (
    build_quality_state,
    collect_quality_gates,
    verify_quality_state,
)


def _artifact(tmpdir, name='artifact.tif'):
    path = Path(tmpdir) / name
    path.write_bytes(b'gate-authority-test-payload')
    return str(path)


class FieldClassificationTest(unittest.TestCase):
    def test_authorization_fields_cover_the_decision_inputs(self):
        self.assertEqual(
            set(ga.AUTHORIZATION_FIELDS),
            {'code', 'status', 'escalate', 'value', 'threshold'})

    def test_diagnostic_fields_exclude_authorization_inputs(self):
        """诊断字段与授权字段**不得有交集** —— 否则分类失效。"""
        self.assertEqual(set(ga.AUTHORIZATION_FIELDS)
                         & set(ga.DIAGNOSTIC_FIELDS), set())

    def test_evidence_is_diagnostic_not_authorization(self):
        """evidence 是给人核对的，判定依据是 value/threshold。

        因此改动 evidence 里的数值**不应**影响授权 —— 这是
        「可变的诊断内容不得参与授权」的直接体现。
        """
        self.assertIn('evidence', ga.DIAGNOSTIC_FIELDS)
        self.assertNotIn('evidence', ga.AUTHORIZATION_FIELDS)

    def test_status_classification_is_disjoint(self):
        self.assertEqual(ga.AUTHORIZING_STATUSES & ga.DIAGNOSTIC_STATUSES,
                         frozenset())

    def test_degraded_is_diagnostic_by_definition(self):
        """degraded 必须可见但**不授权** —— 否则「阈值偏紧」会变成「门禁失效」。"""
        self.assertIn('degraded', ga.DIAGNOSTIC_STATUSES)
        self.assertNotIn('degraded', ga.AUTHORIZING_STATUSES)

    def test_unavailable_does_not_authorize(self):
        """「测不到」不等于「有问题」，但也**不等于通过** —— 不授权、只记录。"""
        self.assertIn('unavailable', ga.DIAGNOSTIC_STATUSES)


class SealTest(unittest.TestCase):
    def test_seal_adds_digest(self):
        gate = artifact_gate('X', 'warning', 'm', value=1, threshold='<=2')
        self.assertIn('authorization_digest', gate)

    def test_seal_is_idempotent_for_untouched_gate(self):
        gate = artifact_gate('X', 'warning', 'm', value=1, threshold='<=2')
        self.assertEqual(ga.seal_gate(gate), gate)

    def test_reseal_after_diagnostic_edit_keeps_original_digest(self):
        """改诊断字段后重封印，digest 必须与原来相同。"""
        gate = artifact_gate('X', 'warning', 'm', value=1, threshold='<=2')
        original = gate['authorization_digest']
        reworded = dict(gate, message='新措辞', evidence={'note': '补充'})
        self.assertEqual(ga.seal_gate(reworded)['authorization_digest'],
                         original)

    def test_reseal_after_authorization_edit_marks_tampered(self):
        gate = artifact_gate('X', 'failed', 'm', value=1, threshold='<=2')
        tampered = dict(gate, status='passed')
        resealed = ga.seal_gate(tampered)
        self.assertTrue(resealed.get('authorization_tampered'))
        self.assertNotEqual(resealed['authorization_digest'],
                            gate['authorization_digest'])

    def test_both_constructors_seal_and_share_key_set(self):
        a = artifact_gate('X', 'failed', 'm', value=1, threshold='<=2')
        b = fidelity_gate('X', 'failed', 'm', value=1, threshold='<=2')
        self.assertEqual(set(a), set(b))
        self.assertIn('authorization_digest', a)
        self.assertIn('authorization_digest', b)


class AuthorizeTest(unittest.TestCase):
    def test_failed_escalate_true_authorizes(self):
        gate = artifact_gate('X', 'failed', 'm', value=1, threshold='<=0.5')
        self.assertTrue(ga.gate_authorizes(gate)[0])

    def test_warning_escalate_true_authorizes(self):
        gate = artifact_gate('X', 'warning', 'm', value=1, threshold='<=0.5')
        self.assertTrue(ga.gate_authorizes(gate)[0])

    def test_escalate_false_never_authorizes(self):
        gate = artifact_gate('X', 'failed', 'm', value=1, threshold='<=0.5',
                             escalate=False)
        ok, reason = ga.gate_authorizes(gate)
        self.assertFalse(ok)
        self.assertIn('escalate=False', reason)

    def test_diagnostic_status_never_authorizes(self):
        for status in sorted(ga.DIAGNOSTIC_STATUSES):
            with self.subTest(status=status):
                gate = artifact_gate('X', status, 'm', value=1)
                self.assertFalse(ga.gate_authorizes(gate)[0])

    def test_unknown_status_does_not_authorize(self):
        """新状态默认**不授权** —— 宁可漏报也不误放行。"""
        gate = artifact_gate('X', 'brand_new_status', 'm', value=1)
        ok, reason = ga.gate_authorizes(gate)
        self.assertFalse(ok)
        self.assertIn('unknown gate status', reason)

    def test_unsealed_gate_from_third_party_still_evaluable(self):
        """未封印的门禁（第三方/遗留）走 sealed_here 通道，仍按字段判定。"""
        gate = {'code': 'X', 'status': 'failed', 'escalate': True,
                'value': 1, 'threshold': '<=0.5'}
        self.assertTrue(ga.gate_authorizes(gate, sealed_here=True)[0])

    def test_tampered_gate_does_not_authorize(self):
        gate = artifact_gate('X', 'failed', 'm', value=1, threshold='<=0.5')
        tampered = ga.seal_gate(dict(gate, status='passed'))
        ok, reason = ga.gate_authorizes(tampered)
        self.assertFalse(ok)
        self.assertIn('modified after sealing', reason)


class AnnotateTest(unittest.TestCase):
    def test_annotation_reports_reason(self):
        gate = fidelity_gate('X', 'degraded', 'm', value=0.1)
        annotated = ga.annotate_authorization([gate])[0]
        self.assertFalse(annotated['authorization']['authorizes'])
        self.assertIn('diagnostic status', annotated['authorization']['reason'])
        self.assertTrue(annotated['authorization']['sealed'])
        self.assertTrue(annotated['authorization']['was_sealed_upstream'])

    def test_annotation_seals_unsealed_gate(self):
        annotated = ga.annotate_authorization(
            [{'code': 'X', 'status': 'failed', 'escalate': True}])[0]
        self.assertIn('authorization_digest', annotated)
        self.assertFalse(annotated['authorization']['was_sealed_upstream'])


class QualityStateIntegrationTest(unittest.TestCase):
    """`quality_state` 的授权路径必须走 `gate_authorizes`。"""

    def test_failed_gate_produces_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = build_quality_state(
                _artifact(tmp),
                gates=[artifact_gate('BAD', 'failed', 'm', value=0.9,
                                     threshold='<=0.5')])
            self.assertEqual(state['status'], 'review_required')
            self.assertEqual(len(state['issues']), 1)

    def test_degraded_gate_produces_no_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = build_quality_state(
                _artifact(tmp),
                gates=[fidelity_gate('D', 'degraded', 'm', value=0.1,
                                     escalate=False)])
            self.assertEqual(state['status'], 'success')
            self.assertEqual(state['issues'], [])

    def test_tampering_status_is_refused(self):
        """核心场景：把 failed 改成 passed，必须不再产生 issue。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            gate = artifact_gate('BAD', 'failed', 'm', value=0.9,
                                 threshold='<=0.5')
            self.assertEqual(
                len(build_quality_state(path, gates=[gate])['issues']), 1)
            tampered = dict(gate, status='passed')
            self.assertEqual(
                len(build_quality_state(path, gates=[tampered])['issues']), 0)

    def test_tampering_escalate_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            gate = artifact_gate('BAD', 'failed', 'm', value=0.9,
                                 threshold='<=0.5')
            tampered = dict(gate, escalate=False)
            self.assertEqual(
                len(build_quality_state(path, gates=[tampered])['issues']), 0)

    def test_tampering_value_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            gate = artifact_gate('BAD', 'failed', 'm', value=0.9,
                                 threshold='<=0.5')
            tampered = dict(gate, value=0.1)
            self.assertEqual(
                len(build_quality_state(path, gates=[tampered])['issues']), 0)

    def test_tampering_threshold_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            gate = artifact_gate('BAD', 'failed', 'm', value=0.9,
                                 threshold='<=0.5')
            tampered = dict(gate, threshold='<=99')
            self.assertEqual(
                len(build_quality_state(path, gates=[tampered])['issues']), 0)

    def test_diagnostic_edits_keep_authorization(self):
        """改message / evidence **不应**影响授权 —— 这是机制的核心区分点。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            gate = artifact_gate('OK', 'warning', '原消息', value=0.1,
                                 threshold='<=0.5')
            reworded = dict(gate, message='改写后的措辞',
                            evidence={'note': '补充说明'})
            self.assertEqual(
                len(build_quality_state(path, gates=[reworded])['issues']), 1)

    def test_persisted_issue_tampering_is_detected(self):
        """落盘后编辑 issue 的门禁证据 ⇒ `verify_quality_state` 硬失败。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            state = build_quality_state(
                path, gates=[artifact_gate('BAD', 'failed', 'm', value=0.9,
                                           threshold='<=0.5')])
            state['issues'][0]['evidence']['status'] = 'passed'
            with self.assertRaises(ValueError) as ctx:
                verify_quality_state(path, state)
            self.assertIn('modified after sealing', str(ctx.exception))

    def test_untouched_persisted_state_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            state = build_quality_state(
                path, gates=[artifact_gate('BAD', 'failed', 'm', value=0.9,
                                           threshold='<=0.5')])
            verify_quality_state(path, state)      # 不抛即通过

    def test_collect_quality_gates_filters_diagnostic_statuses(self):
        payload = {'quality_gates': [
            artifact_gate('A', 'failed', 'm', value=1, threshold='<=0.5'),
            fidelity_gate('B', 'degraded', 'm', value=0.1),
            fidelity_gate('C', 'unavailable', 'm', value=None),
            fidelity_gate('D', 'uncalibrated', 'm', value=0.2),
        ]}
        codes = {g['code'] for g in collect_quality_gates(payload)}
        self.assertEqual(codes, {'A'})

    def test_collect_quality_gates_ignores_tampered_gate(self):
        gate = artifact_gate('A', 'failed', 'm', value=1, threshold='<=0.5')
        payload = {'quality_gates': [dict(gate, status='passed')]}
        self.assertEqual(collect_quality_gates(payload), [])

    def test_visual_reject_still_authorizes(self):
        """人工/多模态否决的兜底路径不受封印机制影响。"""
        payload = {'quality_gates': [], 'status': 'success',
                   'visual_review': {'verdict': 'reject'}}
        codes = {g['code'] for g in collect_quality_gates(payload)}
        self.assertIn('VISUAL_REVIEW_REJECTED', codes)


class ExistingBehaviorTest(unittest.TestCase):
    """回归锁：机制不得改变既有正常路径的结果。"""

    def test_evaluate_quality_gates_seals_its_gates(self):
        from agent_protocol import evaluate_quality_gates
        _status, gates = evaluate_quality_gates({
            'processing_stage': 'final', 'median': 0.10, 'p1': 0.00001,
            'nonpositive_pixel_ratio': 0.05,
        })
        self.assertTrue(gates)
        for gate in gates:
            self.assertIn('authorization_digest', gate)

    def test_background_crushed_still_blocks(self):
        from agent_protocol import evaluate_quality_gates
        status, gates = evaluate_quality_gates({
            'processing_stage': 'final', 'median': 0.10, 'p1': 0.00001,
            'nonpositive_pixel_ratio': 0.05,
        })
        self.assertEqual(status, 'review_required')
        self.assertIn('BACKGROUND_CRUSHED', {g['code'] for g in gates})

    def test_healthy_input_still_succeeds(self):
        from agent_protocol import evaluate_quality_gates
        status, _gates = evaluate_quality_gates({
            'processing_stage': 'final', 'median': 0.10, 'p1': 0.02,
            'nonpositive_pixel_ratio': 0.0, 'star_area_ratio': 0.05,
        })
        self.assertEqual(status, 'success')

    def test_evaluate_quality_gates_keeps_diagnostic_gates_in_output(self):
        """escalate=False 的门仍要出现在 gates/warnings 里供审查。"""
        from agent_protocol import evaluate_quality_gates
        _status, gates = evaluate_quality_gates({
            'processing_stage': 'final', 'median': 0.10, 'p1': 0.02,
            'star_area_ratio': 0.30,        # STAR_DOMINANCE，escalate=False
        })
        codes = {g['code'] for g in gates}
        self.assertIn('STAR_DOMINANCE', codes)


if __name__ == '__main__':
    unittest.main()
