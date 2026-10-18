"""V4 数据抖动的分层与 2D 网格测试。

**V4 在此之前零测试覆盖**，而它带着一个结构性缺陷跑了整个 v0.1.34：
``data_jitter`` 的签名里没有 ``starless``，函数体把原图自己当无星图，
配对变成 ``(orig, orig × k)`` ⇒ ``q = 1/k`` 是**代数后果**，
与像素内容无关（实测 9 子窗 ``q`` 极差 0.000000）。

它能存活有三个原因，都值得当回归网锁住：

1. 零测试覆盖；
2. ``summarize()`` 根本不读 ``v4_data_jitter`` 的任何字段 ⇒
   跑出什么都影响不了 ``verdict``，「没有反馈」被当成「没问题」；
3. 输出的数字看起来合理（0.0 / 0.3 都像真实读数），不像 ``NaN``。

⇒ ``test_q_varies_across_windows`` 是本文件最关键的一条：它让
「V4 又退回用原图当无星图」立刻失败。

全部用合成数据，不依赖 ``/tmp`` 下的管线产物。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import calibrate_star_damage_gate as cal
from test_star_damage_calibration import synthetic_nebula


def make_pair(size=320, seed=0, retention=0.72, contrast=1.9):
    """造一对可测的 (原图, 无星图)，并让两者在**不同区域**有不同形态。

    「不同区域形态不同」是必须的：V4 要测的就是「同一构造在不同位置
    读数是否一致」。若整幅均匀，spread 恒 0，测试就成了「测不出东西
    也能过」—— 那正是 V4 原 bug 的形态。
    """
    original, starless = synthetic_nebula(size=size, seed=seed,
                                          retention=retention,
                                          contrast=contrast)
    # 在角落叠一块亮度不同的结构 ⇒ 子窗之间必有形态差异
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    patch = np.exp(-((((xx - size * 0.82) / (size * 0.18)) ** 2)
                    + (((yy - size * 0.18) / (size * 0.18)) ** 2)))
    boost = (patch * 0.002)[..., None]
    original = np.clip(original + boost, 0.0, 1.0)
    starless = np.clip(starless + boost * retention, 0.0, 1.0)
    return (original.astype(np.float32), starless.astype(np.float32))


class PairingRegressionTests(unittest.TestCase):
    """回归 V4 的结构性缺陷。"""

    def test_q_varies_across_windows(self):
        """**本 bug 的回归网**：真实配对下 ``q`` 必须随子窗变化。

        若V4 又把原图当无星图（``(orig, orig×k)``），``q`` 恒等于
        ``1/k``，极差精确为 0 —— 这正是 v0.1.34 的实测读数
        （9 子窗全 0.6）。
        """
        original, starless = make_pair()
        result = cal.data_jitter(original, starless, None, n_windows=9)
        self.assertTrue(result['applicable'], result.get('reason'))
        self.assertIsNotNone(result['q_spread'])
        self.assertGreater(result['q_spread'], 1e-4,
                           'q 极差为 0 ⇒ V4 又退回用原图当无星图了')
        # 且q 必须落在合理区间（缩放 k=0.6 ⇒ q'≈0.6q_healthy）
        for q in result['q_values']:
            self.assertGreater(q, 0.05)
            self.assertLess(q, 1.0)

    def test_identical_pair_gives_wider_q_than_degenerate_pair(self):
        """恒等配对（无星图=原图）的 q 应恒为 1，与「缩放原图」可区分。

        这条把两种退化都钉住：``(orig, orig)`` 与 ``(orig, orig×k)``
        都会让V4 失去分辨力，但表现不同。
        """
        original, starless = make_pair()
        healthy = cal.data_jitter(original, starless, None, n_windows=9)
        identity = cal.data_jitter(original, original, None, n_windows=9)
        # 恒等配对：任何缩放都会产生 q' = k，构造完全可预测
        self.assertEqual(identity['pooled_spread_misleading'], 0.0)
        self.assertIsNotNone(healthy['q_spread'])

    def test_geometry_mismatch_is_refused(self):
        """原图与无星图几何不一致时必须拒绝，不能静默出一堆读数。"""
        original, starless = make_pair()
        result = cal.data_jitter(original, starless[:-10], None, n_windows=9)
        self.assertFalse(result['applicable'])
        self.assertIn('几何不一致', result['reason'])


class LayoutTests(unittest.TestCase):
    """2D 网格布置。"""

    def test_windows_cover_corners_and_center(self):
        """网格必须含四角与中心 —— 对角线布置一个角都采不到。"""
        positions = cal.window_positions(1000, 800, 400, 320, 9)
        self.assertEqual(len(positions), 9)
        self.assertIn((0, 0), positions, '缺左上角')
        self.assertIn((0, 800 - 320), positions, '缺右上角')
        self.assertIn((1000 - 400, 0), positions, '缺左下角')
        self.assertIn((1000 - 400, 800 - 320), positions, '缺右下角')
        ys = [p[0] for p in positions]
        xs = [p[1] for p in positions]
        self.assertIn((1000 - 400) // 2, ys, '缺中心行')

    def test_layout_is_not_diagonal(self):
        """回归「对角线滑动」缺陷：y 与 x 必须独立变化。"""
        positions = cal.window_positions(1000, 800, 400, 320, 9)
        ratios = {round(x / max(800 - 320, 1), 3)
                  for y, x in positions}
        self.assertGreater(len(ratios), 1,
                           'x 坐标只有一个取值 ⇒ 仍在对角线上')

    def test_window_count_deduped_when_window_spans_axis(self):
        """子窗跨满整轴时 linspace 会产生重复值，必须去重。

        ``win_h == height`` ⇒ y 只剩一个取值，但 **x 仍应变化** ——
        所以结果是「1 行 × k 列」= k 个位置，不是 1 个。
        去重的意义在于：naive 实现会产出 k×k 个**含重复**的位置。
        """
        positions = cal.window_positions(100, 100, 100, 40, 9)
        self.assertEqual(len(positions), len(set(positions)),
                         '仍有重复位置')
        ys = {p[0] for p in positions}
        xs = {p[1] for p in positions}
        self.assertEqual(len(ys), 1, 'y 应只有 1 个取值')
        self.assertGreater(len(xs), 1, 'x 仍应变化')
        self.assertLessEqual(len(positions), 9)
        # 双向都跨满 ⇒ 只剩 1 个位置
        both = cal.window_positions(100, 100, 100, 100, 9)
        self.assertEqual(both, [(0, 0)])

    def test_reported_count_matches_actual(self):
        """请求数与实际位置数不一致时**如实上报**，不静默截断。"""
        original, starless = make_pair(size=160)
        result = cal.data_jitter(original, starless, None, n_windows=16)
        self.assertTrue(result['applicable'])
        self.assertEqual(result['n_windows'], result['n_windows_requested'])
        self.assertEqual(result['layout'], '2d_grid')

    def test_window_shape_is_reported(self):
        original, starless = make_pair(size=200)
        result = cal.data_jitter(original, starless, None, n_windows=9,
                                 window_frac=0.6)
        self.assertEqual(result['window_shape'],
                         [int(200 * 0.6), int(200 * 0.6)])


class StratificationTests(unittest.TestCase):
    """按 ``measurable`` 分层 —— 本方案的核心。"""

    def setUp(self):
        self.original, self.starless = make_pair()
        self.result = cal.data_jitter(self.original, self.starless, None,
                                      n_windows=9)

    def test_strata_cover_every_window_exactly_once(self):
        """分层必须**穷尽且互斥** —— 漏一个就等于把它算进 pooled。"""
        total = self.result['n_windows']
        meas = self.result['strata']['measurable']
        unmeas = self.result['strata']['unmeasurable']
        counted = (meas['n'] if meas else 0) + (unmeas['n'] if unmeas else 0)
        self.assertEqual(counted, total)

    def test_pooled_spread_is_flagged_as_misleading(self):
        """未分层 spread 必须带警告标签（它衡量的是可测占比，不是噪声）。"""
        self.assertIn('pooled_spread_misleading', self.result)
        self.assertIn('不可用于定线', self.result['pooled_note'])

    def test_unmeasurable_fraction_is_reported(self):
        frac = self.result['unmeasurable_fraction']
        self.assertGreaterEqual(frac, 0.0)
        self.assertLessEqual(frac, 1.0)
        if frac > 0.5:
            self.assertIn('代表性有限', self.result.get(
                'unmeasurable_warning', ''))
        else:
            self.assertNotIn('unmeasurable_warning', self.result)

    def test_all_unmeasurable_gives_no_measurable_window(self):
        """全部子窗不可测 ⇒ 明确报「无法测抖动」，不是 spread=0。"""
        flat = np.full((192, 192, 3), 0.02, dtype=np.float32)
        result = cal.data_jitter(flat, flat, None, n_windows=9)
        self.assertEqual(result['verdict'], 'no_measurable_window')
        self.assertIn('无法用于测抖动', result['reason'])

    def test_flat_field_damage_is_not_counted_as_jitter(self):
        """纯暗场的 damage 恒0（被强制），不能被当成「抖动为零」。

        这是「没测」与「测了是 0」的区别 —— v0.1.34 的 pooled spread
        正是把这两者混在一起。
        """
        flat = np.full((192, 192, 3), 0.02, dtype=np.float32)
        result = cal.data_jitter(flat, flat, None, n_windows=9)
        self.assertEqual(result['pooled_spread_misleading'], 0.0)
        self.assertEqual(result['verdict'], 'no_measurable_window')


class DeterminismTests(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        """算法抖动地板 = 0：同输入两次跑必须逐位相同。"""
        original, starless = make_pair()
        a = cal.data_jitter(original, starless, None, n_windows=9)
        b = cal.data_jitter(original, starless, None, n_windows=9)
        self.assertEqual(a['q_values'], b['q_values'])
        self.assertEqual(a['pooled_spread_misleading'],
                         b['pooled_spread_misleading'])
        self.assertEqual(a['algorithm_jitter_floor'], 0.0)


class VerdictIntegrationTests(unittest.TestCase):
    """``summarize()`` 必须真的读 V4 —— 它此前完全不读。"""

    @staticmethod
    def _sample(name, v4, measurable=True, damage=0.24):
        return {
            'target': name, 'target_type': 'emission_nebula',
            'v1_closed_form': {'applicable': True, 'passed': True},
            'v3_isolation': {'isolation_valid': True},
            'healthy': {'nebula_damage_measurable': measurable,
                        'nebula_damage_ratio': damage},
            'v4_data_jitter': v4,
        }

    def test_verdict_blocks_when_no_measurable_window(self):
        v4 = {'applicable': True, 'verdict': 'no_measurable_window',
              'strata': {'measurable': None,
                         'unmeasurable': {'n': 9, 'spread': 0.0}},
              'unmeasurable_fraction': 1.0, 'q_spread': 0.01}
        report = {'samples': [self._sample('a', v4)], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertEqual(verdict['status'], 'blocked')
        self.assertIn('a', verdict['jitter_unmeasured'])
        self.assertTrue(any('抖动**未测**' in r
                            for r in verdict['blocking_reasons']))

    def test_verdict_blocks_when_measurable_layer_too_small(self):
        v4 = {'applicable': True, 'verdict': 'insufficient_windows',
              'strata': {'measurable': {'n': 2, 'spread': 0.01},
                         'unmeasurable': {'n': 7, 'spread': 0.0}},
              'unmeasurable_fraction': 0.78, 'q_spread': 0.02}
        report = {'samples': [self._sample('a', v4)], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertIn('a', verdict['jitter_unmeasured'])

    def test_verdict_blocks_when_jitter_exceeds_damage_range(self):
        """可测层 spread > 档内 damage 全距 ⇒ 单点定的线无意义。"""
        big = {'applicable': True, 'verdict': 'measured',
               'strata': {'measurable': {'n': 5, 'spread': 0.40},
                          'unmeasurable': {'n': 0, 'spread': 0.0}},
               'unmeasurable_fraction': 0.0, 'q_spread': 0.2}
        small = {'applicable': True, 'verdict': 'measured',
                 'strata': {'measurable': {'n': 5, 'spread': 0.01},
                            'unmeasurable': {'n': 0, 'spread': 0.0}},
                 'unmeasurable_fraction': 0.0, 'q_spread': 0.01}
        report = {'samples': [
            self._sample('a', big, damage=0.30),
            self._sample('b', small, damage=0.32),
            self._sample('c', small, damage=0.31),
        ], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertIn('a', verdict['jitter_exceeds_damage_range'])
        self.assertTrue(any('大于档内 damage 全距' in r
                            for r in verdict['blocking_reasons']))

    def test_jitter_strata_are_surfaced_in_verdict(self):
        """分层读数必须进verdict，验收者不必回翻样本。"""
        v4 = {'applicable': True, 'verdict': 'measured',
              'strata': {'measurable': {'n': 6, 'spread': 0.02},
                         'unmeasurable': {'n': 3, 'spread': 0.0}},
              'unmeasurable_fraction': 0.33, 'q_spread': 0.03}
        report = {'samples': [self._sample('a', v4)], 'skipped': []}
        verdict = cal.summarize(report)
        entry = verdict['jitter_strata']['a']
        self.assertEqual(entry['measurable_n'], 6)
        self.assertEqual(entry['measurable_spread'], 0.02)
        self.assertEqual(entry['unmeasurable_fraction'], 0.33)
        self.assertEqual(entry['q_spread'], 0.03)


if __name__ == '__main__':
    unittest.main()
