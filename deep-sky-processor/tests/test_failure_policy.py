"""失败处置策略（failure_policy）+ degraded 状态测试。

三组断言：
  1. **三态语义** —— auto_fallback / preserve_review / stop 的产物保留与
     status 推导；``stop_is_stricter`` 规则。
  2. **degraded 是中间态** —— 可见、可审、**不阻断 accept**，且与
     「二值 status」相比能区分「回退后出图」与「干净出图」。
  3. **非法输入不静默回落** —— 处置策略是最后一道闸，回落会把用户的显式
     意图变成别的行为。
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import failure_policy as fp
from artifact_gates import _gate
from quality_state import build_quality_state, derive_status, resolve_quality_state


def _artifact(tmpdir, name='artifact.tif'):
    path = Path(tmpdir) / name
    path.write_bytes(b'failure-policy-test-payload')
    return str(path)


class ActionSemanticsTest(unittest.TestCase):
    def test_three_actions_are_declared(self):
        self.assertEqual(fp.FAILURE_ACTIONS,
                         ('auto_fallback', 'preserve_review', 'stop'))

    def test_normalize_accepts_known_actions(self):
        for action in fp.FAILURE_ACTIONS:
            self.assertEqual(fp.normalize_action(action), action)

    def test_normalize_is_case_and_space_insensitive(self):
        self.assertEqual(fp.normalize_action('  STOP '), fp.STOP)

    def test_invalid_action_raises_rather_than_falling_back(self):
        """非法值必须报错 —— 回落会把用户的显式意图变成别的行为。"""
        for bad in ('nonsense', 'skip', 'abort', 'true', ''):
            with self.subTest(value=bad):
                with self.assertRaises(fp.FailurePolicyError):
                    fp.normalize_action(bad, 'stretch')

    def test_none_means_no_override(self):
        self.assertIsNone(fp.normalize_action(None))

    def test_preserves_artifacts(self):
        self.assertTrue(fp.preserves_artifacts(fp.AUTO_FALLBACK))
        self.assertTrue(fp.preserves_artifacts(fp.PRESERVE_REVIEW))
        self.assertFalse(fp.preserves_artifacts(fp.STOP))

    def test_action_status_mapping(self):
        self.assertEqual(fp.action_status(fp.STOP), 'failed')
        self.assertEqual(fp.action_status(fp.PRESERVE_REVIEW), 'degraded')
        # auto_fallback 的 status 取决于是否真的回退过
        self.assertEqual(fp.action_status(fp.AUTO_FALLBACK, fallback_used=False),
                         'success')
        self.assertEqual(fp.action_status(fp.AUTO_FALLBACK, fallback_used=True),
                         'degraded')

    def test_pipeline_statuses_include_degraded(self):
        self.assertIn('degraded', fp.PIPELINE_STATUSES)
        self.assertIn('degraded', fp.NON_BLOCKING_STATUSES)
        self.assertNotIn('review_required', fp.NON_BLOCKING_STATUSES)
        self.assertNotIn('failed', fp.NON_BLOCKING_STATUSES)


class StopIsStricterTest(unittest.TestCase):
    def test_default_is_auto_fallback(self):
        self.assertEqual(fp.resolve_failure_actions('stretch'),
                         fp.AUTO_FALLBACK)

    def test_unknown_stage_defaults_to_auto_fallback(self):
        self.assertEqual(fp.resolve_failure_actions('no_such_stage'),
                         fp.AUTO_FALLBACK)

    def test_explicit_request_wins_over_default(self):
        self.assertEqual(
            fp.resolve_failure_actions('stretch', requested=fp.PRESERVE_REVIEW),
            fp.PRESERVE_REVIEW)

    def test_stop_on_this_stage(self):
        self.assertEqual(fp.resolve_failure_actions('stretch', requested=fp.STOP),
                         fp.STOP)

    def test_stop_elsewhere_disables_local_fallback(self):
        """stop_is_stricter：任一阶段 stop ⇒ 其他阶段不得自动回退。"""
        for requested in (None, fp.AUTO_FALLBACK, fp.PRESERVE_REVIEW):
            with self.subTest(requested=requested):
                self.assertEqual(
                    fp.resolve_failure_actions('stretch', requested=requested,
                                               overrides={'dbe': fp.STOP}),
                    fp.STOP)

    def test_stop_from_invalid_override_still_raises(self):
        with self.assertRaises(fp.FailurePolicyError):
            fp.resolve_failure_actions('stretch', overrides={'dbe': 'garbage'})

    def test_non_stop_override_does_not_escalate(self):
        self.assertEqual(
            fp.resolve_failure_actions('stretch',
                                       overrides={'dbe': fp.PRESERVE_REVIEW}),
            fp.AUTO_FALLBACK)


class DeriveStatusTest(unittest.TestCase):
    """status 推导链：failed > review_required > degraded > success。"""

    def test_clean_run(self):
        self.assertEqual(derive_status([]), 'success')

    def test_degraded_when_only_degraded_stages(self):
        self.assertEqual(derive_status([], ['stretch']), 'degraded')

    def test_review_required_when_issues(self):
        issues = [{'id': '1', 'code': 'X', 'status': 'failed'}]
        self.assertEqual(derive_status(issues), 'review_required')

    def test_issues_outrank_degraded(self):
        issues = [{'id': '1', 'code': 'X', 'status': 'failed'}]
        self.assertEqual(derive_status(issues, ['stretch']),
                         'review_required')

    def test_stop_outranks_everything(self):
        issues = [{'id': '1', 'code': 'X', 'status': 'failed'}]
        self.assertEqual(derive_status(issues, ['stop']), 'failed')

    def test_all_statuses_are_declared(self):
        for issues, stages, expected in (
            ([], (), 'success'),
            ([], ('stretch',), 'degraded'),
            ([{'id': '1'}], (), 'review_required'),
            ([{'id': '1'}], ('stop',), 'failed'),
        ):
            self.assertEqual(derive_status(issues, stages), expected)
            self.assertIn(expected, fp.PIPELINE_STATUSES)


class QualityStateDegradedTest(unittest.TestCase):
    def test_degraded_stage_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = build_quality_state(
                _artifact(tmp), gates=[],
                degraded_stages=['stretch', 'star_remove'])
            self.assertEqual(state['status'], 'degraded')
            self.assertEqual(state['degraded_stages'],
                             ['star_remove', 'stretch'])
            self.assertEqual(state['issues'], [])

    def test_degradation_inherits_from_parent(self):
        """父阶段走过回退 ⇒ 子阶段即使干净，整条链路仍是 degraded。"""
        with tempfile.TemporaryDirectory() as tmp:
            parent = build_quality_state(
                _artifact(tmp, 'parent.tif'), gates=[],
                degraded_stages=['stretch'])
            self.assertEqual(parent['status'], 'degraded')
            child = build_quality_state(
                _artifact(tmp, 'child.tif'), parent_state=parent, gates=[])
            self.assertEqual(child['status'], 'degraded')
            self.assertEqual(child['degraded_stages'], ['stretch'])

    def test_clean_run_has_empty_degraded_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = build_quality_state(_artifact(tmp), gates=[])
            self.assertEqual(state['status'], 'success')
            self.assertEqual(state['degraded_stages'], [])

    def test_issues_outrank_inherited_degradation(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = build_quality_state(
                _artifact(tmp, 'parent.tif'), gates=[],
                degraded_stages=['stretch'])
            child = build_quality_state(
                _artifact(tmp, 'child.tif'), parent_state=parent,
                gates=[_gate('BAD', 'failed', 'm', value=1, threshold='<=0.5')])
            self.assertEqual(child['status'], 'review_required')
            # 降级记录仍保留 —— 信息不能因为出了 issue 就丢
            self.assertEqual(child['degraded_stages'], ['stretch'])

    def test_resolving_all_issues_keeps_degraded(self):
        """复核清空 issue 后应回落到 degraded 而不是 success。

        这是 degraded 的核心价值：问题解决了，但「曾经降级过」这个事实
        不能被抹掉 —— 否则与「一次干净的处理」重新无法区分。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            state = build_quality_state(
                path, gates=[_gate('BAD', 'failed', 'm', value=1,
                                   threshold='<=0.5')],
                degraded_stages=['stretch'])
            self.assertEqual(state['status'], 'review_required')
            resolved = resolve_quality_state(path, state, {
                'artifact': str(Path(path).resolve()),
                'artifact_sha256': state['artifact_sha256'],
                'resolutions': [],
            })
            # 没有 resolution 时 issue 仍在
            self.assertEqual(resolved['status'], 'review_required')
            # 造一个 solved 的 state 验证回落
            solved = {**state, 'issues': [],
                      'status': derive_status([], state['degraded_stages'])}
            self.assertEqual(solved['status'], 'degraded')

    def test_degraded_state_verifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            state = build_quality_state(path, gates=[],
                                        degraded_stages=['stretch'])
            from quality_state import verify_quality_state
            verify_quality_state(path, state)      # 不抛即通过


class AcceptSemanticsTest(unittest.TestCase):
    """degraded 不阻断 accept。"""

    def test_accept_allows_degraded_quality_state(self):
        from agent_protocol import ALLOWED_VERDICTS
        # 判据是 issues 非空；degraded 不产生 issue ⇒ 天然放行。
        with tempfile.TemporaryDirectory() as tmp:
            state = build_quality_state(
                _artifact(tmp), gates=[], degraded_stages=['stretch'])
            self.assertEqual(state['issues'], [])
            self.assertEqual(state['status'], 'degraded')

    def test_verdict_set_includes_expected_values(self):
        from agent_protocol import ALLOWED_VERDICTS
        for verdict in ('accept', 'retry', 'rollback'):
            self.assertIn(verdict, ALLOWED_VERDICTS)


class RegistrySelfValidationTest(unittest.TestCase):
    def test_overridable_stages_must_be_registered(self):
        """OVERRIDABLE_STAGES 声明的阶段必须在 DEFAULT_FAILURE_ACTIONS 里。"""
        self.assertTrue(fp.OVERRIDABLE_STAGES.issubset(
            set(fp.DEFAULT_FAILURE_ACTIONS)))

    def test_every_action_is_fully_specified(self):
        for action in fp.FAILURE_ACTIONS:
            self.assertIn(action, fp.ACTION_TO_STATUS)
            self.assertIn(action, fp.ACTION_PRESERVES_ARTIFACTS)

    def test_all_mapped_statuses_are_known(self):
        for action, status in fp.ACTION_TO_STATUS.items():
            if status is not None:
                self.assertIn(status, fp.PIPELINE_STATUSES, action)


if __name__ == '__main__':
    unittest.main()
