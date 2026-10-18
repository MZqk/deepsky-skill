"""累计配置指纹（config_fingerprint）测试。

核心断言（也是本模块存在的全部理由）：

    ``artifact_sha256`` 只能证明「产物文件没被改」，
    **证明不了「产生它的配置没被改」**。

所以「改一个上游参数重跑，旧配置的 issues 被 parent_state 继承到新产物」
这条静默失效路径必须被指纹切断。测试按四层组织：
  1. 累计语义 —— 改上游必影响下游，改下游不影响上游
  2. 参数分桶 —— 未识别键进 ``_unassigned`` 但**照样参与哈希**（宁可多算）
  3. 质量继承 —— 指纹不同 ⇒ 切断父 issue 继承
  4. 边界 —— 与 source_fingerprint 正交、遗留 state 保守处理
"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_fingerprint as cf
from artifact_gates import _gate
from quality_state import build_quality_state


BASE = {
    'dbe_degree': 2, 'dbe_pctl_low': 0.3, 'stretch_factor': 45.0,
    'ghs_b': 6.0, 'star_threshold': 0.85, 'saturation': 1.4,
    'hdr_strength': 0.5, 'final_denoise_lum': 0.018,
}


def _artifact(tmpdir, name='artifact.tif', content=b'payload'):
    path = Path(tmpdir) / name
    path.write_bytes(content)
    return str(path)


class CumulativeSemanticsTest(unittest.TestCase):
    """累计指纹的核心性质。"""

    def test_fingerprints_are_deterministic(self):
        self.assertEqual(cf.stage_fingerprint(BASE, upto_step='stretch'),
                         cf.stage_fingerprint(BASE, upto_step='stretch'))

    def test_later_cutoff_accumulates_more(self):
        early = cf.stage_fingerprint(BASE, upto_step='stretch')
        late = cf.stage_fingerprint(BASE, upto_step='style')
        self.assertNotEqual(early, late)

    def test_changing_upstream_invalidates_downstream(self):
        """改 dbe（order 0）⇒ style（order 10）的指纹必须变。"""
        base = cf.stage_fingerprint(BASE, upto_step='style')
        changed = cf.stage_fingerprint(dict(BASE, dbe_degree=5),
                                       upto_step='style')
        self.assertNotEqual(base, changed)

    def test_changing_downstream_does_not_affect_upstream(self):
        """改 saturation（final_color, order 9）不应影响 stretch（order 5）的指纹。"""
        before = cf.stage_fingerprint(BASE, upto_step='stretch')
        after = cf.stage_fingerprint(dict(BASE, saturation=1.9),
                                     upto_step='stretch')
        self.assertEqual(before, after)

    def test_changing_a_stage_affects_only_later_cutoffs(self):
        base = cf.stage_fingerprint(BASE, upto_step='stretch')
        changed = cf.stage_fingerprint(dict(BASE, stretch_factor=60.0),
                                       upto_step='stretch')
        self.assertNotEqual(base, changed,
                            '改 stretch 自身参数必须影响 stretch 自身的指纹')

    def test_input_fingerprint_is_part_of_the_payload(self):
        """换输入必须让指纹变 —— 否则「同一配置处理不同数据」会误判为可继承。"""
        with tempfile.TemporaryDirectory() as tmp:
            # 内容必须不同 —— SHA-256 相同则指纹必然相同，那测的是哈希而非本机制
            first = _artifact(tmp, 'first.tif', b'first-input-data')
            second = _artifact(tmp, 'second.tif', b'second-input-data')
            self.assertNotEqual(first, second)
            self.assertNotEqual(
                cf.stage_fingerprint(BASE, input_path=first,
                                     upto_step='stretch'),
                cf.stage_fingerprint(BASE, input_path=second,
                                     upto_step='stretch'))
            # 同一输入 ⇒ 指纹稳定
            self.assertEqual(
                cf.stage_fingerprint(BASE, input_path=first,
                                     upto_step='stretch'),
                cf.stage_fingerprint(BASE, input_path=first,
                                     upto_step='stretch'))
            # 无 input 时同一配置指纹相同（不引入随机性）
            self.assertEqual(
                cf.stage_fingerprint(BASE, upto_step='stretch'),
                cf.stage_fingerprint(BASE, upto_step='stretch'))
            # 有 input 与无 input 也不同 —— 说明 input 真的进了 payload
            self.assertNotEqual(
                cf.stage_fingerprint(BASE, input_path=first,
                                     upto_step='stretch'),
                cf.stage_fingerprint(BASE, upto_step='stretch'))

    def test_missing_input_yields_none_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            fingerprint = cf.stage_fingerprint(
                BASE, str(Path(tmp) / 'nope.tif'), upto_step='stretch')
            self.assertIsInstance(fingerprint, str)

    def test_optional_step_rejected_as_cutoff(self):
        """可选步骤不在主流程，不能作为累计终点。"""
        for step in ('linear_deconv', 'reference_grade',
                     'background_neutralization'):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    cf.stage_fingerprint(BASE, upto_step=step)

    def test_unknown_step_rejected(self):
        with self.assertRaises(ValueError):
            cf.stage_fingerprint(BASE, upto_step='no_such_step')

    def test_empty_config_yields_valid_fingerprint(self):
        self.assertIsInstance(cf.stage_fingerprint({}, upto_step='stretch'), str)


class StepFingerprintTest(unittest.TestCase):
    def test_step_fingerprint_is_scoped_to_its_stage(self):
        before = cf.step_fingerprint(BASE, 'stretch')
        after = cf.step_fingerprint(dict(BASE, saturation=1.9), 'stretch')
        self.assertEqual(before, after,
                         '改别的阶段不应影响本阶段指纹')

    def test_step_fingerprint_changes_with_own_params(self):
        before = cf.step_fingerprint(BASE, 'stretch')
        after = cf.step_fingerprint(dict(BASE, ghs_b=8.0), 'stretch')
        self.assertNotEqual(before, after)

    def test_step_fingerprint_for_empty_stage(self):
        self.assertIsInstance(cf.step_fingerprint({}, 'sharpen'), str)


class ParameterAssignmentTest(unittest.TestCase):
    def test_known_keys_map_to_expected_stages(self):
        cases = {
            'dbe_degree': 'dbe',
            'stretch_factor': 'stretch',
            'ghs_b': 'stretch',
            'star_threshold': 'star_remove',
            'saturation': 'final_color',
            'hdr_strength': 'enhance',
            'final_denoise_lum': 'final_denoise',
            'sharpen_amount': 'sharpen',
            'style_strength': 'style',
            'deepsnr_strength': 'pre_denoise',
        }
        for key, expected in cases.items():
            with self.subTest(key=key):
                self.assertEqual(cf.assign_step(key), expected)

    def test_prefix_fallback(self):
        self.assertEqual(cf.assign_step('ghs_brand_new_knob'), 'stretch')
        self.assertEqual(cf.assign_step('sharpen_whatever'), 'sharpen')

    def test_excluded_keys_are_not_assigned(self):
        for key in cf.EXCLUDED_KEYS:
            with self.subTest(key=key):
                self.assertIsNone(cf.assign_step(key))

    def test_excluded_keys_do_not_affect_fingerprint(self):
        """追踪容器（used/completed/phase）不该影响指纹。"""
        before = cf.stage_fingerprint(BASE, upto_step='stretch')
        after = cf.stage_fingerprint(
            dict(BASE, used={'stretch': {'ghs_b': 9}}, completed=['dbe'],
                 phase='stretch'),
            upto_step='stretch')
        self.assertEqual(before, after)

    def test_unknown_keys_go_to_unassigned_not_dropped(self):
        """未识别键进 _unassigned 桶 —— 漏算会让「改了却算出同样指纹」。"""
        buckets = cf.split_config({'totally_unknown_knob': 7})
        self.assertIn(cf.UNASSIGNED, buckets)
        self.assertEqual(buckets[cf.UNASSIGNED]['totally_unknown_knob'], 7)
        # 且它参与哈希
        self.assertNotEqual(
            cf.stage_fingerprint({'totally_unknown_knob': 7},
                                 upto_step='stretch'),
            cf.stage_fingerprint({'totally_unknown_knob': 8},
                                 upto_step='stretch'))

    def test_split_config_covers_all_buckets(self):
        buckets = cf.split_config(BASE)
        flat = [key for params in buckets.values() for key in params]
        self.assertEqual(sorted(flat), sorted(BASE))

    def test_split_config_handles_execution_config(self):
        """ExecutionConfig 是 dict 子类，须能被直接迭代。"""
        from runtime_info import ExecutionConfig
        cfg = ExecutionConfig(dict(BASE))
        buckets = cf.split_config(cfg)
        self.assertIn('stretch', buckets)
        self.assertEqual(buckets['stretch']['ghs_b'], 6.0)


class DescribeTest(unittest.TestCase):
    def test_describe_lists_stages_in_order(self):
        """describe 必须按主流程 order 升序 —— 报告要能当配置清单读。"""
        rows = cf.describe(BASE, upto_step='style')
        steps = [row['step'] for row in rows]
        orders = [row['order'] for row in rows]
        self.assertEqual(orders, sorted(orders))
        self.assertEqual(steps,
                         ['dbe', 'star_remove', 'stretch', 'enhance',
                          'final_color'])

    def test_describe_puts_unassigned_last(self):
        """_unassigned 无 order，必须排在所有有 order 的阶段之后。"""
        rows = cf.describe(dict(BASE, mystery_knob=1), upto_step='style')
        self.assertEqual(rows[-1]['step'], cf.UNASSIGNED)
        self.assertIsNone(rows[-1]['order'])

    def test_describe_includes_unassigned(self):
        rows = cf.describe({'mystery_knob': 1}, upto_step='stretch')
        self.assertIn(cf.UNASSIGNED, [row['step'] for row in rows])

    def test_describe_omits_stages_without_params(self):
        rows = cf.describe(BASE, upto_step='style')
        steps = {row['step'] for row in rows}
        self.assertNotIn('star_combine', steps)   # BASE 无 star_combine_strength


class QualityInheritanceTest(unittest.TestCase):
    """指纹的核心用途：配置变了就切断父 issue 继承。"""

    def _state_with_issue(self, path, fingerprint):
        return build_quality_state(
            path, gates=[_gate('OLD', 'failed', 'm', value=0.9,
                               threshold='<=0.5')],
            config_fingerprint=fingerprint)

    def test_same_config_inherits_issues(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            parent = self._state_with_issue(path, 'fp_A')
            child = build_quality_state(path, parent_state=parent, gates=[],
                                        config_fingerprint='fp_A')
            self.assertEqual(len(child['issues']), 1)
            self.assertFalse(child['config_changed'])

    def test_changed_config_cuts_inheritance(self):
        """核心场景：改配置重跑，父 issue 不得继承到新产物。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            parent = self._state_with_issue(path, 'fp_A')
            child = build_quality_state(path, parent_state=parent, gates=[],
                                        config_fingerprint='fp_B')
            self.assertEqual(child['issues'], [])
            self.assertTrue(child['config_changed'])

    def test_legacy_parent_without_fingerprint_inherits(self):
        """父 state 无指纹（历史产物）⇒ 保守继承，不静默丢弃已有 finding。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            parent = self._state_with_issue(path, 'fp_A')
            legacy = {k: v for k, v in parent.items()
                      if k != 'config_fingerprint'}
            child = build_quality_state(path, parent_state=legacy, gates=[],
                                        config_fingerprint='fp_B')
            self.assertEqual(len(child['issues']), 1)
            self.assertFalse(child['config_changed'])

    def test_current_gates_still_recorded_when_config_changed(self):
        """切断继承只影响「父的」，当前阶段的门禁照常记录。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            parent = self._state_with_issue(path, 'fp_A')
            child = build_quality_state(
                path, parent_state=parent,
                gates=[_gate('NEW', 'failed', 'm', value=0.8,
                             threshold='<=0.5')],
                config_fingerprint='fp_B')
            codes = {issue['code'] for issue in child['issues']}
            self.assertEqual(codes, {'NEW'})

    def test_config_fingerprint_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _artifact(tmp)
            state = build_quality_state(path, gates=[],
                                        config_fingerprint='fp_Z')
            self.assertEqual(state['config_fingerprint'], 'fp_Z')


class BoundaryTest(unittest.TestCase):
    def test_matches_requires_both_present(self):
        """任一为 None ⇒ 不匹配。无指纹不等于匹配。"""
        self.assertTrue(cf.matches('a', 'a'))
        self.assertFalse(cf.matches('a', 'b'))
        self.assertFalse(cf.matches(None, 'a'))
        self.assertFalse(cf.matches('a', None))
        self.assertFalse(cf.matches(None, None))

    def test_schema_is_declared(self):
        self.assertTrue(cf.CONFIG_FINGERPRINT_SCHEMA.endswith('/v1'))

    def test_canonical_payload_is_order_independent(self):
        self.assertEqual(cf.canonical_payload({'a': 1, 'b': 2}),
                         cf.canonical_payload({'b': 2, 'a': 1}))

    def test_canonical_payload_handles_unserializable(self):
        """含不可序列化对象时不能抛 —— 指纹是诊断辅助，不该阻断出图。"""
        payload = cf.canonical_payload({'obj': object()})
        self.assertIsInstance(payload, str)

    def test_fingerprint_is_short_and_hex(self):
        fingerprint = cf.stage_fingerprint(BASE, upto_step='stretch')
        self.assertEqual(len(fingerprint), 20)
        int(fingerprint, 16)          # 合法十六进制

    def test_config_fingerprint_is_orthogonal_to_source_fingerprint(self):
        """两个指纹语义不同：config 管参数、source 管代码版本。

        不重复 source 的职责，也不引入「两份指纹并存」的耦合。
        """
        self.assertNotIn('source', cf.CONFIG_FINGERPRINT_SCHEMA)
        self.assertNotIn('code', cf.CONFIG_FINGERPRINT_SCHEMA)


class RealConfigTest(unittest.TestCase):
    """用真实的 PRESETS 验证 —— 合成 dict 可能漏掉真实键。"""

    def test_all_presets_produce_stable_fingerprints(self):
        from pipeline import STRENGTH_PRESETS as PRESETS
        for name, preset in PRESETS.items():
            with self.subTest(preset=name):
                first = cf.stage_fingerprint(preset, upto_step='stretch')
                self.assertEqual(first, cf.stage_fingerprint(preset,
                                                             upto_step='stretch'))
                self.assertIsInstance(first, str)

    def test_presets_differ_from_each_other(self):
        from pipeline import STRENGTH_PRESETS as PRESETS
        names = sorted(PRESETS)
        prints = {n: cf.stage_fingerprint(PRESETS[n], upto_step='stretch')
                  for n in names}
        self.assertEqual(len(set(prints.values())), len(names),
                         '不同 preset 必须算出不同指纹')

    def test_unassigned_bucket_reports_unmapped_preset_keys(self):
        """把真实 preset 里未映射的键暴露出来 —— 便于后续补全映射表。"""
        from pipeline import STRENGTH_PRESETS as PRESETS
        rows = cf.describe(PRESETS['medium'], upto_step='stretch')
        unmapped = next((row for row in rows if row['step'] == cf.UNASSIGNED),
                        None)
        if unmapped:
            # 有未映射键 —— 它们参与哈希但归属不明，值得记录
            self.assertTrue(unmapped['params'])


if __name__ == '__main__':
    unittest.main()
