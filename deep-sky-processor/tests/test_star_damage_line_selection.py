"""V5 定线：劣化组/健康组分离 + 双向夹逼。

**本文件锁的是一个已被实测否证的设计**：v0.1.36 的§4.4 公式写成
``damage_limit = min(劣化下界, 健康上界 × 放宽系数)``，方向是错的。

实测（8 份样本，5 份可测）：A 型网格当时是**绝对 q 阶梯**，
最轻非零档恒为 **0.0450**，而健康态本身是 **0.0357~0.3580**
⇒ 4/5 份样本的「劣化」比「健康」还轻 ⇒ ``min()`` 恒取 0.045
⇒ 定出的线比现行 0.20 严 4.4 倍，会拒绝几乎所有健康数据。

根因：**去星必然带走主体光通量**，真实 StarNet2 产物的 ``q``
天生就< 1（实测 0.54~0.86），正好落在绝对阶梯中段。
⇒ 网格必须改**相对倍率**（``f=1.00`` 即健康态本身），
且夹逼方向必须是**区间**而非 ``min()``。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import calibrate_star_damage_gate as cal


def make_sample(target, q, healthy_damage, lightest_degraded,
                measurable=True, jitter=None):
    """构造一份最小 sample 夹具，含 V5 需要的全部字段。"""
    return {
        'target': target,
        'target_type': 'emission_nebula',
        'healthy': {
            'q': q,
            'nebula_damage_ratio': healthy_damage,
            'nebula_damage_measurable': measurable,
            'subject_separation': 1.5 if measurable else 1.0,
        },
        'constructs': {
            'A': [
                {'ratio': 1.00, 'param': 1.00,
                 'nebula_damage_ratio': healthy_damage},
                {'ratio': 0.95, 'param': 0.95,
                 'nebula_damage_ratio': lightest_degraded},
            ],
        },
    }


def spread_for(q, ratios=(1.00, 0.95)):
    """按闭式 ``damage = max(0, |1 − f·q| − 0.10)`` 算各档读数。"""
    return [max(0.0, abs(1.0 - f * q) - 0.10) for f in ratios]


class RelativeGridTests(unittest.TestCase):
    """相对倍率网格的核心性质。"""

    def test_first_ratio_is_identity(self):
        """第一个倍率恒为 1.00 —— 构造的零参照点是健康态本身。"""
        self.assertEqual(cal.HEALTHY_RATIO, 1.00)
        self.assertEqual(cal.grid_values('A')[0], 1.00)
        self.assertEqual(cal.grid_values('B_star')[0], 0.0,
                         'B★ 的 β=0 即健康态')

    def test_relative_grid_does_not_depend_on_q(self):
        """倍率是纯无量纲数，不随样本变化。

        绝对阶梯必须按 q 换算（否则 q 值相同、健康态各异），
        相对倍率不需要 —— 这正是它能对齐「零参照点」的原因。
        """
        self.assertEqual(cal.grid_values('A', 0.54),
                         cal.grid_values('A', 0.86))

    def test_f100_reproduces_healthy_damage(self):
        """``f=1.00`` 档的读数**等于**该样本的真实 StarNet2 读数。

        实测 5 份样本全部吻合到 4 位：ngc6888 0.2403、M8 0.3580、
        NGC2237 0.2983、IC434-S50 0.0357、C50-S50 0.1851。
        """
        for q, healthy in ((0.6597, 0.2403), (0.5420, 0.3580),
                           (0.6017, 0.2983), (0.8643, 0.0357),
                           (0.7149, 0.1851)):
            with self.subTest(q=q):
                predicted = spread_for(q, (1.00,))[0]
                self.assertAlmostEqual(predicted, healthy, places=4)

    def test_degraded_ladder_exceeds_healthy(self):
        """每个 ``f<1`` 档都**严格高于**健康态。

        这是「最轻劣化档」有意义的前提：若劣化档比健康态还低，
        任何以它为下界的线都会误杀健康数据。
        """
        for q in (0.5420, 0.6017, 0.6597, 0.7149, 0.8643):
            with self.subTest(q=q):
                healthy, degraded = spread_for(q, (1.00, 0.95))
                self.assertGreater(degraded, healthy)

    def test_absolute_grid_overlaps_healthy(self):
        """**回归本缺陷**：绝对阶梯的最轻档**低于**健康态。

        这条测试的作用是钉住「旧网格错在哪」，防止有人觉得
        「绝对阶梯更直观」而改回去。
        """
        overlapped = 0
        for q, healthy in ((0.6597, 0.2403), (0.5420, 0.3580),
                           (0.6017, 0.2983), (0.7149, 0.1851)):
            with self.subTest(q=q):
                k_values = cal.grid_values_absolute('A', q)
                q_ladder = [k * q for k in k_values]
                # 绝对阶梯的 q 目标恒为 0.90/0.855/… ⇒ 第二格 damage 恒 0.045
                lightest = max(0.0, abs(1.0 - q_ladder[1]) - 0.10)
                self.assertAlmostEqual(lightest, 0.0450, places=4)
                if lightest < healthy:
                    overlapped += 1
        self.assertEqual(overlapped, 4,
                         '实测 4/5 份样本的绝对阶梯最轻档低于健康态')


class SeparationTests(unittest.TestCase):
    """``check_degraded_separation`` 的四段输出与弃用规则。"""

    def test_returns_four_sections(self):
        sample = make_sample('a', 0.6597, 0.2403, 0.2733, jitter=0.0112)
        row = cal.check_degraded_separation(sample, jitter_spread=0.0112)
        self.assertTrue(row['applicable'])
        for key in ('healthy_damage', 'lightest_degraded', 'jitter_bound',
                    'overlap'):
            self.assertIn(key, row)
        self.assertAlmostEqual(row['overlap'], 0.2733 - 0.2403, places=4)

    def test_baseline_match_is_verified(self):
        """构造读数必须与 report 的 healthy 一致，否则说明配对错了。"""
        good = make_sample('a', 0.6597, 0.2403, 0.2733)
        self.assertTrue(
            cal.check_degraded_separation(good)['healthy_matches_baseline'])
        bad = make_sample('b', 0.6597, 0.2403, 0.2733)
        bad['constructs']['A'][0]['nebula_damage_ratio'] = 0.9
        self.assertFalse(
            cal.check_degraded_separation(bad)['healthy_matches_baseline'])

    def test_non_overlapping_is_rejected(self):
        """劣化档不高于健康态 ⇒ 该构造不可用。"""
        sample = make_sample('a', 0.6597, 0.2403, 0.1000)
        row = cal.check_degraded_separation(sample)
        self.assertTrue(row['applicable'])
        self.assertFalse(row['usable'])
        self.assertIn('未越过健康态', row['reject_reason'])

    def test_unmeasurable_sample_is_not_applicable(self):
        sample = make_sample('a', 0.6597, 0.0, 0.0, measurable=False)
        row = cal.check_degraded_separation(sample)
        self.assertFalse(row['applicable'])
        self.assertIn('不可测', row['reason'])

    def test_missing_ratios_are_reported(self):
        """找不到 f=0.95 档时如实报出可用倍率，而不是静默取最轻档。"""
        sample = make_sample('a', 0.6597, 0.2403, 0.2733)
        sample['constructs']['A'] = [
            {'ratio': 1.00, 'nebula_damage_ratio': 0.2403}]
        row = cal.check_degraded_separation(sample)
        self.assertFalse(row['applicable'])
        self.assertIn('available_ratios', row)


class SqueezeTests(unittest.TestCase):
    """双向夹逼 —— 是**区间**，不是 min()。

    ``damage_limit`` 的语义是「接纳线」（``damage <= limit`` 即接受），
    所以两条约束方向相反：

    - ``healthy_ceiling = max(健康读数) × relax`` —— **线下界**
      （健康数据必须全部被接受）
    - ``degraded_floor = min(最轻劣化档)`` —— **线上界**
      （劣化数据必须全部被拒绝）

    真实 5 份样本实测**区间为空**（见
    :meth:`test_real_corpus_is_not_lineable`），所以这里的「可定线」
    夹具用**人为构造**的数据：健康组集中、劣化档统一抬高。
    真实数据不可定线是**结论**，不是夹具错误。
    """

    #: 健康组集中、劣化档统一抬高。
    #: 间隔要够宽以容纳全局 ``LINE_RELAX_FACTOR = 1.5`` ——
    #: 0.105 × 1.5 = 0.1575 会跨过degraded_floor 0.150 ⇒ 区间为空。
    #: 第一版用 0.100~0.110 / 0.150~0.160（间隔 0.045）恰好踩中这个
    #: 交叉，测试报not_lineable 却查不出原因 —— 夹具必须自己算一遍。
    LINEABLE_HEALTHY = (0.100, 0.110, 0.105)
    LINEABLE_DEGRADED = (0.300, 0.310, 0.305)

    @staticmethod
    def _rows(usable=True, spread=None,
              healthy=None, degraded=None):
        healthy = healthy or SqueezeTests.LINEABLE_HEALTHY
        degraded = degraded or SqueezeTests.LINEABLE_DEGRADED
        return [{'applicable': True, 'usable': usable,
                 'target': name, 'healthy_damage': h,
                 'lightest_degraded': d,
                 'overlap': round(d - h, 6), 'jitter_bound': spread}
                for name, h, d in zip(('a', 'b', 'c'), healthy, degraded)]

    def test_line_sits_inside_the_clamp(self):
        """候选线必须落在可行区间内。"""
        rows = self._rows()
        healthy = [r['healthy_damage'] for r in rows]
        out = cal.squeeze_line(rows, healthy, relax=1.0)
        self.assertEqual(out['status'], 'lineable')
        lo, hi = out['feasible_interval']
        self.assertGreaterEqual(out['damage_limit'], lo - 1e-9)
        self.assertLessEqual(out['damage_limit'], hi + 1e-9)

    def test_bounds_are_reported_not_just_the_result(self):
        """两端都要报出来 —— 只给一个数字无法判断它是哪一侧决定的。"""
        out = cal.squeeze_line(self._rows(), list(self.LINEABLE_HEALTHY),
                              relax=1.0)
        self.assertAlmostEqual(out['healthy_ceiling'], 0.110, places=4)
        self.assertAlmostEqual(out['degraded_floor'], 0.300, places=4)

    def test_min_formula_would_have_been_wrong(self):
        """**回归本缺陷**：旧公式 ``min(劣化, 健康×relax)`` 方向反了。

        必须用**健康组跨大范围**的夹具才能暴露它 ——
        :meth:`_rows` 的集中夹具（0.100~0.110）下旧公式恰好给出
        0.110 = 健康最大值，不越界，**证明不了任何东西**（第一版
        就犯了这个错：拿「看起来会越界」的集中夹具写断言，实测不越界）。

        真实语料（健康 0.0357~0.3580）下旧公式取 0.2733，
        低于 M8 的健康读数 0.3580 ⇒ 必然误杀健康数据。
        """
        healthy = (0.0357, 0.2403, 0.2983, 0.3580)
        degraded = (0.0789, 0.2733, 0.3284, 0.3851)
        rows = self._rows(healthy=healthy, degraded=degraded)
        old_formula = min(max(degraded), max(healthy) * 1.0)
        self.assertAlmostEqual(old_formula, 0.3580, places=4)
        self.assertLess(old_formula, max(degraded),
                        '旧公式取健康侧，压到最轻劣化档之下 ⇒ 漏放劣化')
        # 新公式识别出区间为空，而不是硬返回一个误杀线
        out = cal.squeeze_line(rows, list(healthy), relax=1.0)
        self.assertEqual(out['status'], 'not_lineable')
        self.assertNotIn('damage_limit', out)

    def test_real_corpus_is_not_lineable(self):
        """**真实 5 份样本实测：区间为空** ⇒ 报 not_lineable。

        健康基线跨 10 倍（0.0357~0.3580），而「最轻劣化」相对各自
        健康态只抬高约 0.033 ⇒ **单一绝对阈值无法区分健康与劣化**。

        这是**结构性结论**，不是数据噪声：不得为凑出线而放宽任一侧。
        """
        healthy = (0.0357, 0.2403, 0.2983, 0.3580)
        degraded = (0.0789, 0.2733, 0.3284, 0.3851)
        rows = self._rows(healthy=healthy, degraded=degraded, spread=0.0991)
        for relax in (1.0, 1.5):
            with self.subTest(relax=relax):
                out = cal.squeeze_line(rows, list(healthy), relax=relax)
                self.assertEqual(out['status'], 'not_lineable')
                self.assertTrue(out.get('structural'))
                self.assertGreater(out['crossing'], 0)
                self.assertNotIn('damage_limit', out)
                self.assertIn('remedy', out)

    def test_relax_factor_widens_ceiling(self):
        rows = self._rows()
        healthy = list(self.LINEABLE_HEALTHY)
        tight = cal.squeeze_line(rows, healthy, relax=1.0)
        loose = cal.squeeze_line(rows, healthy, relax=1.5)
        self.assertAlmostEqual(loose['healthy_ceiling'],
                               tight['healthy_ceiling'] * 1.5, places=4)

    def test_not_lineable_when_all_constructs_rejected(self):
        out = cal.squeeze_line(self._rows(usable=False),
                               list(self.LINEABLE_HEALTHY))
        self.assertEqual(out['status'], 'not_lineable')
        self.assertIn('没有任何样本', out['reason'])

    def test_not_lineable_when_healthy_missing(self):
        out = cal.squeeze_line(self._rows(), [])
        self.assertEqual(out['status'], 'not_lineable')
        self.assertIn('缺少健康组读数', out['reason'])

    def test_p50_used_not_max(self):
        """取 p50 而非 max —— 单份极端值不决定结果。"""
        rows = self._rows()
        rows.append({'applicable': True, 'usable': True, 'target': 'outlier',
                     'healthy_damage': 0.105, 'lightest_degraded': 9.9,
                     'overlap': 9.8, 'jitter_bound': None})
        out = cal.squeeze_line(rows, list(self.LINEABLE_HEALTHY),
                              relax=1.0)
        self.assertIn('degraded_p50', out)
        self.assertLess(out['degraded_p50'], 1.0,
                        'p50 不受那个 9.9 outlier 影响')

    def test_unusable_rows_excluded_from_aggregation(self):
        rows = self._rows()
        rows.append({'applicable': True, 'usable': False, 'target': 'bad',
                     'healthy_damage': 9.9, 'lightest_degraded': 0.001,
                     'overlap': -9.9, 'jitter_bound': None})
        out = cal.squeeze_line(rows, list(self.LINEABLE_HEALTHY),
                              relax=1.0)
        self.assertEqual(out['n_usable'], 3)
        self.assertIn('bad', out['excluded'])
        self.assertNotIn(9.9, out['healthy_values'],
                         '被排除的行不得进入健康组聚合')

    def test_separation_warning_when_jitter_dominates(self):
        """最窄可行间隔 < N× 最坏抖动 ⇒ 必须告警。

        间隔 = ``degraded_floor − healthy_ceiling``（要含relax 系数）。
        本夹具：0.300 − 0.165 = 0.135，而 3× 抖动 = 0.15 ⇒ 触发告警。
        """
        rows = self._rows(spread=0.05)
        out = cal.squeeze_line(rows, list(self.LINEABLE_HEALTHY),
                              relax=1.5)
        self.assertEqual(out['status'], 'lineable')
        self.assertIn('separation_warning', out)
        self.assertLess(out['separation_gap'],
                        out['min_separation_multiple'] * out['worst_jitter'])

    def test_separation_gap_accounts_for_relax(self):
        """间隔必须用 ``healthy_ceiling``（含 relax），不是 ``max(healthy)``。

        用后者会把间隔算大：集中夹具下 0.300−0.110=0.190 vs 正确的
        0.300−0.165=0.135 ⇒ **漏报**抖动告警。
        """
        rows = self._rows(spread=0.05)
        out = cal.squeeze_line(rows, list(self.LINEABLE_HEALTHY),
                              relax=1.5)
        self.assertAlmostEqual(out['separation_gap'],
                               out['degraded_floor']
                               - out['healthy_ceiling'], places=6)

    def test_no_warning_when_separation_is_large(self):
        out = cal.squeeze_line(self._rows(spread=0.001),
                              list(self.LINEABLE_HEALTHY), relax=1.0)
        self.assertNotIn('separation_warning', out)


class VerdictIntegrationTests(unittest.TestCase):
    @staticmethod
    def _sample(name, q, dmg, deg, spread):
        s = make_sample(name, q, dmg, deg, jitter=spread)
        s['v1_closed_form'] = {'applicable': True, 'passed': True}
        s['v3_isolation'] = {'isolation_valid': True}
        s['v4_data_jitter'] = {
            'applicable': True, 'verdict': 'measured',
            'strata': {'measurable': {'n': 6, 'spread': spread},
                       'unmeasurable': None},
            'unmeasurable_fraction': 0.0, 'q_spread': 0.05}
        return s

    def test_not_lineable_becomes_blocking_reason(self):
        """真实语料不可定线 ⇒ 成为阻塞理由。"""
        healthy = (0.0357, 0.2403, 0.2983, 0.3580)
        degraded = (0.0789, 0.2733, 0.3284, 0.3851)
        samples = [self._sample(name, 0.6597, h, d, 0.09)
                   for name, h, d in zip(('a', 'b', 'c', 'd'),
                                         healthy, degraded)]
        verdict = cal.summarize({'samples': samples, 'skipped': []})
        self.assertEqual(verdict['status'], 'blocked')
        self.assertTrue(any('双向夹逼无解' in r
                            for r in verdict['blocking_reasons']))
        self.assertEqual(verdict['line_candidate']['status'],
                         'not_lineable')

    def test_v5_runs_even_when_jitter_unmeasured(self):
        """抖动未测的样本**仍要参与 V5 分离**。

        V4 缺的是 spread（抖动可信度），V5 要的是构造读数
        （健康态 vs 劣化档）—— 两者输入不同。第一版在这里continue，
        导致这类样本的分离信息整份丢失。
        """
        samples = [self._sample(n, 0.6597, 0.24, 0.27, 0.01)
                   for n in ('a', 'b', 'c')]
        samples[2]['v4_data_jitter'] = {'applicable': False,
                                        'reason': '有效子窗不足 2 个'}
        verdict = cal.summarize({'samples': samples, 'skipped': []})
        self.assertIn('c', verdict['jitter_unmeasured'])
        targets = [row.get('target') for row in verdict['v5_separation']]
        self.assertIn('c', targets,
                      '抖动未测的样本被V5 整份丢弃了')

    def test_line_candidate_present_when_lineable(self):
        """健康组集中时区间不空 ⇒ 产出候选（但**未采纳**）。

        劣化档要抬到 0.30：全局 ``LINE_RELAX_FACTOR = 1.5`` 会把
        healthy 0.105 抬到 0.1575，劣化档 0.155 会被它跨过。
        """
        samples = [self._sample(n, 0.6597, h, d, 0.001)
                   for n, h, d in (('a', 0.100, 0.300),
                                   ('b', 0.110, 0.310),
                                   ('c', 0.105, 0.305))]
        verdict = cal.summarize({'samples': samples, 'skipped': []})
        candidate = verdict['line_candidate']
        self.assertEqual(candidate['status'], 'lineable')
        self.assertIn('damage_limit', candidate)
        self.assertIn('advisory_hard_line', candidate)
        # 候选产出 ≠ 采纳：recommendation 必须说明尚未落值
        self.assertIn('尚未采纳', verdict['line_recommendation'])


if __name__ == '__main__':
    unittest.main()
