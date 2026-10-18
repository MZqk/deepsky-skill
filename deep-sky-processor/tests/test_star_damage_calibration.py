"""去星误伤门禁标定器（calibrate_star_damage_gate）的性质测试。

锁三条性质 —— 每条都对应一个**被实测否决过**的设计假设：

1. **闭式** ``damage = max(0, |1 - q| - 0.10)``（±10% 死区、V 形双侧）。
   这是「构造能被预测」的前提；不成立则劣化对照只是随机曲线。
2. **``nebula_mask_coverage``恒为 0.25**。主体掩膜由 p75 **相对**分位
   圈定，占比数学上恒等于 ``1 - 0.75``。锁死它是为了防止后人误以为
   「掩膜退化为全图」这个失败形态已受控（该判据已标 deprecated）。
3. **E 型掩膜外免疫**：只扰动掩膜外时 damage 必须仍为 0。这是所有
   劣化构造的**红线** —— 不满足说明 damage 升高来自全局亮度效应。

全部用合成数据跑，**不依赖 /tmp 下的管线产物** —— 那些产物会随代码
变化而失效，测试必须能长期当回归网。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import calibrate_star_damage_gate as cal
import star_tools as st


def synthetic_nebula(size=192, seed=0, n_stars=40, star_peak=0.05,
                     contrast=1.45, retention=0.66):
    """造一对有可测主体的 (原图, 无星图)。

    幅值压在低量级（线性域实测 p99≈0.002~0.005），主体占画面约 1/4
    （对齐真实 p75 掩膜），加平滑延展结构 + 点状星。

    参数全部**按实测反推**，不是随手写的：

    - ``star_peak``：第一版用 3×3 方块 + peak 0.05~0.4，30 颗星经
      ``gaussian_filter(sigma=8)`` 平滑后铺满主体掩膜，健康样本 ``q``
      掉到 0.087（真实 StarNet2 产物 ≈0.66）⇒ 构造一上来就落在重度
      劣化区，测的是合成器的缺陷而不是门禁的性质。
    - ``contrast``：平滑图上的 ``p75/p25`` 目标。真实发射星云实测
      ``subject_separation = 1.29~1.31``（NGC6888 全分辨率 1.308），
      **紧贴判据下限 1.30**。取值必须高于 1.30 才有可测样本，
      又不能高太多 —— 否则 F1/F2 压不到 1.30 以下，「可测性判据会不会
      被触发」这条元验证就测不到。
    - ``retention``：**q 的目标值**。真实 StarNet2 产物在主体掩膜内
      只保留约 **66%** 的平滑亮度（NGC6888 实测 q=0.6597，
      对应 damage 0.2403）。第二版把 starless 设成「恰好等于星云」，
      于是 q=0.9855、B★（过减混合）**无从放大**—— 它乘的是
      ``sl − orig``，而两者在星云区完全相同，读数钉在 0.9842 不动。
      这不是 B★ 的缺陷，是夹具缺了「去星会带走主体光通量」这���事实。
    - 星点为**单像素**：真实星点在 sigma=8 平滑图上贡献有限，
      方块星会被平滑成「主体的一部分」。

    三条踩过的坑（写进 docstring 因为它们会再次发生）：

    1. **合成数据必须先验明健康态读数落在真实区间**，否则测的是合成器
       的缺陷而不是门禁的性质。
    2. **对比度不能用「整体乘系数」调** —— 乘法不改变 p75/p25 的**比**。
       第二版写了 ``nebula *= contrast / current`` 的校正循环，它恒等
       无效（比值被原样保留）。正确做法是加 pedestal：
       ``(p75+c)/(p25+c) = target`` 有闭式解 ``c = (p75 − target·p25)/(target − 1)``。
    3. **两族构造离开死区的阈值公式不同**：A 型 ``q' = k·q`` 需
       ``k > 0.90/q``；B★ ``q' = 1 − α(1−q)`` 需 ``α > 0.10/(1−q)``。
       用错公式会让整族构造落在死区内，读数全 0 而被误判成「构造失效」。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = size / 2.0
    radius = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    nebula = np.exp(-((radius / (size * 0.22)) ** 2)) * 0.004
    nebula += np.exp(-((radius / (size * 0.05)) ** 2)) * 0.0015
    nebula += rng.normal(0.0, 2e-4, nebula.shape).astype(np.float32)

    # 对比度校正：加 pedestal（乘法不改变 p75/p25 的比，见 docstring）。
    # contrast 必须 > 1：等于 1 时下面那个除零。曾被 test 的
    # contrast=1.0 触发 —— 夹具自己没兜住，等于「能产出 NaN 的数据」，
    # 而测试夹具能产 NaN 就会让所有基于它的断言失去意义。
    if contrast <= 1.0:
        raise ValueError('contrast 必须 > 1（1.0 会让 pedestal 公式除零）')
    from scipy.ndimage import gaussian_filter
    smooth = gaussian_filter(nebula, sigma=8.0)
    p25 = float(np.percentile(smooth, 25))
    p75 = float(np.percentile(smooth, 75))
    pedestal = (p75 - contrast * p25) / (contrast - 1.0)
    nebula = np.clip(nebula + max(pedestal, 0.0), 0.0, 1.0).astype(np.float32)

    # 去星会带走一部分主体光通量：**原图**保留全部星云亮度，
    # ``starless`` 只保留 retention 比例。第二版写成
    # ``starless = nebula*retention`` 再 ``original = starless + stars``，
    # 于是 retention 在两者间**抵消** —— q 恒 ≈0.98，与真实 0.66 差很远。
    # 正确做法是让两者**共享**同一份星云底图，只在保留比例上分叉。
    base = nebula[..., None].repeat(3, axis=2)
    starless = np.ascontiguousarray(base * retention, dtype=np.float32)
    stars = np.zeros_like(base)
    for _ in range(n_stars):
        sx, sy = rng.integers(4, size - 4, 2)
        stars[sy, sx, :] = rng.uniform(0.5, 1.0) * star_peak
    original = np.clip(base + stars, 0.0, 1.0)
    return original.astype(np.float32), starless.astype(np.float32)


class ClosedFormTests(unittest.TestCase):
    """性质 1：闭式在**不削顶区间**成立。"""

    def setUp(self):
        self.original, self.starless = synthetic_nebula()

    def test_identity_copy_gives_q_exactly_one(self):
        """**恒等副本**（回退产物）给出 q ≡ 1.0。

        这解释了为什么回退副本不能当健康样本：q≡1 ⇒ damage≡0 ⇒ 任何
        劣化构造都读 0，标定静默失效。注意这与「有星的真实配对」不同
        —— 后者 q 在 0.95~0.99（星的光通量被拿走一部分）。
        """
        original, _starless = synthetic_nebula()
        orig_ds = cal.downsample(original)
        mask, _, _ = cal.subject_mask_from(cal.to_gray(orig_ds))
        q = cal.retention_ratio(cal.to_gray(orig_ds),
                                cal.to_gray(orig_ds), mask)
        self.assertAlmostEqual(q, 1.0, places=6)

    def test_realistic_pair_lands_in_expected_q_band(self):
        """合成数据必须先验明健康态读数落在真实区间。

        真实 StarNet2 产物实测 q ∈ [0.54, 0.98]（NGC6888 0.6597、
        M8 0.5420、M17 0.9797）。夹具第一版给 0.087（星点太亮，
        ``gaussian_filter(sigma=8)`` 把它们抹进主体掩膜），第二版给
        0.9855（starless 由 nebula 派生，``retention`` 抵消掉了）。
        两者都会让构造一上来就落在死区外，测的是夹具的缺陷。
        """
        original, starless = synthetic_nebula()
        healthy = cal.collect_sample(original, starless, None, 'h')
        self.assertGreater(healthy['q'], 0.50)
        self.assertLessEqual(healthy['q'], 1.0)
        self.assertTrue(healthy['nebula_damage_measurable'])
        # 健康态的 damage 应落在真实区间（真实 StarNet2 读 0.24~0.36）
        self.assertGreater(healthy['nebula_damage_ratio'], 0.10)
        self.assertLess(healthy['nebula_damage_ratio'], 0.45)

    def test_brightness_scaling_matches_closed_form(self):
        """A型构造（``sl * k``）的 damage 必须等于闭式预测，残差 ≤1e-4。

        这同时证明闭式可用于定线：给定 k 就能预测读数。
        网格用工具自己的 :func:`grid_values`（按 q 自适应），不自造档位 ——
        自造档位可能整族落在死区内，读数全 0 而误判为「闭式成立」。
        """
        healthy = cal.collect_sample(self.original, self.starless, None, 'h')
        self.assertIsNotNone(healthy)
        rows = []
        for k in cal.grid_values('A', healthy['q']):
            candidate = cal.construct_brightness(
                cal.downsample(self.starless), k)
            row = cal.collect_sample(cal.downsample(self.original), candidate,
                                     None, 'A=%s' % k)
            row['param'] = k
            rows.append(row)
        result = cal.check_closed_form(healthy, {'A': rows})
        self.assertTrue(result['applicable'], result.get('reason'))
        self.assertTrue(result['passed'],
                        '闭式残差超标：%s' % result['worst_residual'])
        self.assertEqual(result['clip_detected_at_k'], [])

    def test_adaptive_grid_leaves_the_dead_zone(self):
        """自适应网格必须保证**至少一档落在死区外**。

        死区内的档预测恒为 0、必然吻合 —— 全在死区内时闭式「完美通过」
        却什么都没验证。这是最容易骗过自己的形态。
        """
        for q in (0.66, 0.75, 0.90, 0.9855):
            with self.subTest(q=q):
                for key in ('A', 'B_star'):
                    values = cal.grid_values(key, q)
                    self.assertTrue(values)
                    outside = [v for v in values
                               if max(0.0, abs(1.0 - v * q) - 0.10) > 0.0
                               or max(0.0, abs(1.0 - (1.0 - v * (1.0 - q)))
                                      - 0.10) > 0.0]
                    self.assertTrue(outside,
                                    '%s 在 q=%.4f 时整族落在死区内：%s'
                                    % (key, q, values))

    def test_dead_zone_makes_damage_exactly_zero(self):
        """±10% 死区：``q ∈ [0.90, 1.10] ⇒ damage ≡ 0``（精确 0，不是近似）。

        死区的存在意味着「按 damage 分组」会把健康组压成一个点 ——
        这是标定必须按 q 分组的原因。
        """
        healthy = cal.collect_sample(self.original, self.starless, None, 'h')
        q = healthy['q']
        for k in (1.0, 0.95, 0.9, 1.05, 1.1):
            candidate = cal.construct_brightness(
                cal.downsample(self.starless), k)
            row = cal.collect_sample(cal.downsample(self.original), candidate,
                                     None, 'k=%s' % k)
            if abs(k * q - 1.0) <= 0.10:
                self.assertEqual(row['nebula_damage_ratio'], 0.0,
                                 'k=%s 落在死区内却读到非零 damage' % k)
    def test_clipping_breaks_the_closed_form(self):
        """削顶使 ``q' ≠ k*q`` ⇒ 闭式在该区间失效。

        这是标定的**边界条件**而非缺陷：k 必须限制在不削顶单侧区间。
        削顶阈值是数据相关的 —— 本合成夹具 ``sl_smooth.max()=0.006``，
        所以要 ``k > 166`` 才开始削顶（真实 NGC6888 是 ``k > 2``，
        因为它 p99 更高）。第一版测试写死 ``k=4.0``，在合成数据上
        根本没削顶，于是「闭式失效」这条性质测了个空。
        ⇒ 这里**按数据算** k，而不是硬编码。
        """
        from scipy.ndimage import gaussian_filter
        peak = float(gaussian_filter(
            cal.to_gray(cal.downsample(self.starless)), sigma=8.0).max())
        k = (1.0 / peak) * 4.0
        self.assertGreater(k, 1.0 / peak,
                           'k 必须超过削顶阈值 1/peak 才算测到削顶')
        healthy = cal.collect_sample(self.original, self.starless, None, 'h')
        candidate = cal.construct_brightness(cal.downsample(self.starless), k)
        row = cal.collect_sample(cal.downsample(self.original), candidate,
                                 None, 'A=%s' % k)
        row['param'] = k
        predicted = max(0.0, abs(1.0 - k * healthy['q']) - 0.10)
        self.assertLess(row['nebula_damage_ratio'], predicted - 0.05)
        result = cal.check_closed_form(healthy, {'A': [row]})
        self.assertIn(k, result['clip_detected_at_k'])
        self.assertTrue(result['clip_note'])

    def test_damage_is_v_shaped_and_blind_to_direction(self):
        """V 形双侧：q=0.70（暗 30%）与 q=1.30（亮 30%）读数相同。

        指标**无法区分变暗与变亮**。星移除的真实物理是变暗，亮侧无对应
        失效模式 —— 所以标定时不能把亮侧样本当「另一种劣化」。
        """
        healthy = cal.collect_sample(self.original, self.starless, None, 'h')
        q = healthy['q']
        dark = cal.collect_sample(
            cal.downsample(self.original),
            cal.construct_brightness(cal.downsample(self.starless), 0.7 / q),
            None, 'dark')
        bright = cal.collect_sample(
            cal.downsample(self.original),
            cal.construct_brightness(cal.downsample(self.starless), 1.3 / q),
            None, 'bright')
        self.assertAlmostEqual(dark['nebula_damage_ratio'],
                               bright['nebula_damage_ratio'], places=3)


class CoverageIsAlwaysQuarterTests(unittest.TestCase):
    """性质 2：``nebula_mask_coverage`` 恒为 0.25 —— 死判据的证明。"""

    def test_coverage_is_always_quarter(self):
        """掩膜 = ``orig_smooth >= percentile(orig_smooth, 75)``。

        相对分位的定义让占比**数学上恒等于 0.25**，与图像内容无关。
        """
        for seed in (0, 1, 2, 7):
            with self.subTest(seed=seed):
                original, starless = synthetic_nebula(seed=seed, size=128)
                row = cal.collect_sample(original, starless, None, 's%d' % seed)
                self.assertAlmostEqual(row['coverage'], 0.25, places=3)
                report = st.estimate_star_removal_quality(
                    cal.downsample(original), cal.downsample(starless))
                self.assertAlmostEqual(report['nebula_mask_coverage'], 0.25,
                                       places=3)

    def test_coverage_does_not_affect_measurability(self):
        """v0.1.34 起 coverage 退出判定链 —— 抬高它也不改变可测性。

        如果这条失败，说明 deprecated 字段仍在暗中参与判定，
        后维护者会误以为「掩膜退化」已受控。
        """
        original, starless = synthetic_nebula()
        report = st.estimate_star_removal_quality(
            cal.downsample(original), cal.downsample(starless))
        self.assertIn('nebula_mask_coverage', report)
        self.assertEqual(
            report['nebula_damage_measurable'],
            report['subject_separation'] >= 1.30)

    def test_flat_field_is_unmeasurable_via_separation_only(self):
        """纯暗场：p25=p75 ⇒ separation=1.0 ⇒ 不可测（走 unavailable）。"""
        flat = np.full((96, 96, 3), 0.02, dtype=np.float32)
        report = st.estimate_star_removal_quality(flat, flat)
        self.assertFalse(report['nebula_damage_measurable'])
        self.assertEqual(report['nebula_damage_ratio'], 0.0)
        self.assertEqual(report['quality'], 'unmeasurable')
        accepted, state = st._star_removal_gate(report, 0.45)
        self.assertTrue(accepted)
        self.assertEqual(state, 'advisory')


class OutsideMaskImmunityTests(unittest.TestCase):
    """性质 3：E 型阴性对照 —— damage 是**主体局部**量。"""

    def setUp(self):
        self.original, self.starless = synthetic_nebula()
        self.orig_ds = cal.downsample(self.original)
        self.sl_ds = cal.downsample(self.starless)
        self.mask, _, _ = cal.subject_mask_from(cal.to_gray(self.orig_ds))

    def test_damage_is_insensitive_to_damage_far_outside_the_mask(self):
        """掩膜外**远处**损伤不得改变 damage（阴性对照）。

        判据是「**相对健康态的升幅**」而非「绝对为 0」：健康样本本身
        的 damage 可以非零（真实 StarNet2 产物 NGC6888 读 0.2403、
        M8 读 0.3580 —— 那是「去星确实带走主体光通量」的真实代价）。
        按绝对 0 判会把**健康态自己**当成违规。

        另注：构造**必须腐蚀 3σ**（``E_EROSION_PX``）。高斯核支撑无限，
        不腐蚀时边界外的亮度会卷进掩膜内 —— 实测未腐蚀版本 f=1.0 时
        damage 0.2495 → 0.2627，红线恒不成立。那不是门禁的缺陷，
        是构造的定义错误。
        """
        healthy = cal.collect_sample(self.orig_ds, self.sl_ds, None, 'h')
        for fraction in cal.GRIDS['E'][1]:
            with self.subTest(fraction=fraction):
                candidate = cal.outside_mask_variant(
                    self.orig_ds, self.sl_ds, self.mask, fraction)
                row = cal.collect_sample(self.orig_ds, candidate, None,
                                         'E=%s' % fraction)
                self.assertLess(
                    abs(row['nebula_damage_ratio']
                        - healthy['nebula_damage_ratio']),
                    cal.OUTSIDE_MASK_DAMAGE_EPS)

    def test_red_line_tolerance_matches_rounding_granularity(self):
        """红线阈值必须容得下一次舍入进位。

        ``nebula_damage_ratio`` 被 round 到 4 位 ⇒ 最小可分辨差是 1e-4。
        第一版阈值取 1e-9（比它小 5 个数量级），于是**任何舍入都判失败**：
        M8 实测掩膜外全损后 0.3580 → 0.3581（升幅 1e-4，纯粹是进位）
        被报成「damage 不是纯主体局部量」。这是假阴性，且指向的是
        一个**错误的结论方向**（会让人怀疑门禁本身）。
        """
        self.assertGreaterEqual(cal.OUTSIDE_MASK_DAMAGE_EPS, 1e-4)
        # 但也不能大到容下真实渗入（未腐蚀版本实测升幅 0.0132）
        self.assertLess(cal.OUTSIDE_MASK_DAMAGE_EPS, 1e-2)

    def test_erosion_is_required_for_the_red_line(self):
        """不腐蚀掩膜外 ⇒ 红线不成立（记录这条设计约束）。

        这条测试的作用是**把踩过的坑钉住**：将来若有人简化 E 构造
        （去掉腐蚀），会看到它变「简洁」但红线失效。
        """
        uneroded = ~np.asarray(self.mask, dtype=bool)
        healthy = cal.collect_sample(self.orig_ds, self.sl_ds, None, 'h')
        # 直接对补集施加全损（不腐蚀）
        raw = self.sl_ds.copy()
        raw[uneroded] = 0.0
        row = cal.collect_sample(self.orig_ds, raw, None, 'E-raw')
        self.assertGreater(
            abs(row['nebula_damage_ratio'] - healthy['nebula_damage_ratio']),
            cal.OUTSIDE_MASK_DAMAGE_EPS,
            '未腐蚀的掩膜外损伤本应渗入掩膜内；若此断言失败说明'
            '高斯核支撑被误认为有限')

    def test_outside_mask_damage_still_moves_other_metrics(self):
        """阴性对照的另一半：构造必须**确实生效**。

        若其他指标也不动，说明构造是空操作，红线就形同虚设
        （「什么都没发生」也会让 damage 读 0）。

        注意动的是 ``high_gradient_ratio`` 而**不是** ``residual``：
        掩膜外损伤把星点周围的背景清零，制造的是**硬边**而不是残留点。
        第一版断言 ``residual`` 上升，方向就搞反了。
        """
        healthy = cal.collect_sample(self.orig_ds, self.sl_ds, None, 'h')
        candidate = cal.outside_mask_variant(self.orig_ds, self.sl_ds,
                                             self.mask, 1.0)
        row = cal.collect_sample(self.orig_ds, candidate, None, 'E=1.0')
        self.assertGreater(row['high_gradient_ratio'],
                           healthy['high_gradient_ratio'] + 1e-4)

    def test_e_construct_passes_its_own_expectation(self):
        """E 型必须通过自己的红线 —— 这是所有构造的准入门槛。"""
        healthy = cal.collect_sample(self.orig_ds, self.sl_ds, None, 'h')
        rows = []
        for fraction in cal.GRIDS['E'][1]:
            candidate = cal.construct_outside_mask(
                self.orig_ds, self.sl_ds, self.mask, fraction)
            row = cal.collect_sample(self.orig_ds, candidate, None,
                                     'E=%s' % fraction)
            row['param'] = fraction
            rows.append(cal._with_deltas(row, healthy))
        passed, diag = cal.check_monotonicity(rows, 'constant_zero')
        self.assertTrue(passed, 'E 型红线未通过：%s' % diag)


class ConstructIsolationTests(unittest.TestCase):
    """副作用隔离：只有 A 能单独定 ``damage_limit``。"""

    def setUp(self):
        self.original, self.starless = synthetic_nebula()
        self.healthy = cal.collect_sample(self.original, self.starless,
                                          None, 'h')

    def test_brightness_scaling_is_pure_damage(self):
        """A 型：damage 变、残差与硬边**逐位不变**（缩放不改它们之比）。"""
        rows = []
        for k in (0.9, 0.7, 0.5):
            candidate = cal.construct_brightness(
                cal.downsample(self.starless), k)
            row = cal.collect_sample(cal.downsample(self.original), candidate,
                                     None, 'A=%s' % k)
            row['param'] = k
            rows.append(cal._with_deltas(row, self.healthy))
        for row in rows:
            self.assertEqual(row['class'], 'pure_damage',
                             'k=%s 被误判为 %s' % (row['param'],
                                                    row['class']))

    def test_b_star_moves_damage_beyond_the_dead_zone(self):
        """B★（过减混合）离开死区后 damage 单调升。

        死区是硬约束，而**两族构造离开死区的阈值公式不同**：

        - A型 ``q' = k·q``       ⇒ 需 ``k > 0.90 / q``
        - B★ ``q' = 1 − α(1−q)`` ⇒ 需 ``α > 0.10 / (1 − q)``

        合成夹具 ``q=0.9855`` 时后者是 ``α > 6.9`` —— 网格里最大只到
        5.0，**整族都在死区内**。第一版用 ``1/q`` 当阈值（1.015），
        测出「α 未离开死区」就被当成构造失效，方向完全错了。

        真实 NGC6888 ``q=0.6597`` ⇒ ``α > 0.29``，网格 1.25 起就够 ——
        又一次说明写死档位会在不同数据上覆盖完全不同的区间。
        """
        od = cal.downsample(self.original)
        sd = cal.downsample(self.starless)
        healthy = cal.collect_sample(od, sd, None, 'h')
        q = healthy['q']
        # B★ 的 q' = 1 − α(1 − q)；damage = max(0, |1−q'| − 0.10)
        threshold = 0.10 / max(1.0 - q, 1e-8)
        damages = []
        for factor in (1.05, 1.2, 1.5, 2.0):
            alpha = threshold * factor
            row = cal.collect_sample(
                od, cal.construct_overshoot(od, sd, alpha), None, str(alpha))
            row['param'] = alpha
            damages.append(row['nebula_damage_ratio'])
            self.assertGreater(row['nebula_damage_ratio'], 0.0,
                               'α=%.3f（阈值 %.3f）未离开死区'
                               % (alpha, threshold))
        self.assertEqual(damages, sorted(damages),
                         'B★ 的 damage 应对 α 单调升：%s' % damages)

    def test_only_monotonic_constructs_can_set_damage_limit(self):
        """只有「单调 + 可预测」的构造能定线；改分母/几何/对照不行。

        A 与 B★ 可用（闭式成立、damage 单调）；D（几何错位）与
        E（阴性对照）不可用；F1/F2 改分母，只能用于元验证。
        """
        rows_by = cal.run_constructs(self.original, self.starless,
                                     self.healthy, None)
        verdict = cal.check_isolation(rows_by)
        for key in ('A', 'B_star'):
            with self.subTest(construct=key):
                self.assertTrue(verdict['per_construct'][key]['usable_for_limit'])
        for key in ('D', 'E', 'F1', 'F2'):
            with self.subTest(construct=key):
                self.assertFalse(
                    verdict['per_construct'][key]['usable_for_limit'],
                    '%s 不应能定 damage_limit' % key)

    def test_isolation_reports_limitation_when_no_pure_damage(self):
        """没有 ``pure_damage`` 构造时，报告必须自陈局限。

        真实数据实测：A 型亮度缩放的 ``high_grad`` **会动**（判据含绝对
        噪声地板 ``6σ_sl``，而 ``σ_sl`` 随缩放同比变化、``grad_orig``
        不变）⇒ 判为 ``confounded``。此时定线只能给「量级正确的候选值」，
        报告若不说明就成了「经隔离验证的精确线」。
        """
        rows_by = cal.run_constructs(self.original, self.starless,
                                     self.healthy, None)
        verdict = cal.check_isolation(rows_by)
        if not verdict['per_construct']['any_pure_damage']:
            self.assertIn('未经隔离验证', verdict['limitation'])
        else:
            self.assertEqual(verdict['limitation'], '')


class FalsificationGuardTests(unittest.TestCase):
    """F1/F2 必须是唯一能触发 ``measurable=False`` 的构造 —— 判据存活证明。"""

    def _assert_switch_with_zero_damage(self, key, construct):
        original, starless = synthetic_nebula()
        orig_ds = cal.downsample(original)
        sl_ds = cal.downsample(starless)
        found = []
        for value in cal.GRIDS[key][1]:
            row = collect = cal.collect_sample(construct(orig_ds, value),
                                               sl_ds, None, str(value))
            found.append((value, row['subject_separation'],
                          row['nebula_damage_measurable'],
                          row['nebula_damage_ratio']))
        measurable = [f for f in found if f[2]]
        unmeasurable = [f for f in found if not f[2]]
        self.assertTrue(measurable,
                        '%s 从未保持可测 —— 夹具的 separation 越界了' % key)
        self.assertTrue(unmeasurable,
                        '%s 未能触发 measurable=False —— '
                        '可测性判据可能已失效' % key)
        for value, _sep, _m, damage in unmeasurable:
            self.assertEqual(damage, 0.0,
                             '%s=%s 不可测档的 damage 须恰为 0（验证 '
                             'star_tools 的 raw_change=0.0 分支）'
                             % (key, value))

    def test_gamma_reaches_unmeasurable_with_zero_damage(self):
        """幂律扰动分母：separation 降到 1.30 以下且 damage 恰为 0。"""
        self._assert_switch_with_zero_damage(
            'F1', cal.construct_gamma)

    def test_bright_fill_reaches_unmeasurable_with_zero_damage(self):
        """加常量扰动分母：机制与幂律不同，两者都要能触发。

        只测一种可能撞上「该构造恰好不敏感的系数区间」——
        实测 NGC6888 上 γ=0.5 只把 sep 从 1.308 压到 1.118，
        而 c=0.005 就到 1.033，灵敏度差一个量级。
        """
        self._assert_switch_with_zero_damage(
            'F2', cal.construct_bright_fill)

    def test_starless_only_constructs_cannot_break_measurability(self):
        """只扰动 starless 的构造在数学上不可能触发可测性短路。

        可测性判据只用 ``orig_smooth``，与 starless 无关。这条性质是
        简化构造设计的前提；若它不成立，构造就必须同时扰动原图，
        整套「只扰动 starless」的方法论失效。
        """
        original, starless = synthetic_nebula()
        orig_ds = cal.downsample(original)
        sl_ds = cal.downsample(starless)
        mask, _, _ = cal.subject_mask_from(cal.to_gray(orig_ds))
        q = cal.retention_ratio(cal.to_gray(orig_ds), cal.to_gray(sl_ds),
                                mask)
        for key in ('A', 'B_star', 'D', 'E'):
            for value in cal.grid_values(key, q):
                if key == 'A':
                    cand = cal.construct_brightness(sl_ds, value)
                elif key == 'B_star':
                    cand = cal.construct_overshoot(orig_ds, sl_ds, value)
                elif key == 'D':
                    cand = cal.construct_shift(sl_ds, value)
                else:
                    cand = cal.construct_outside_mask(orig_ds, sl_ds, mask,
                                                      value)
                row = cal.collect_sample(orig_ds, cand, None, '%s=%s'
                                         % (key, value))
                with self.subTest(construct=key, value=value):
                    self.assertTrue(row['nebula_damage_measurable'])


class FallbackArtifactRefusalTests(unittest.TestCase):
    """标定器必须拒绝回退副本当健康态。"""

    def test_fallback_copy_is_not_accepted_as_healthy(self):
        """``04_starless_fallback.tif`` 是原图副本，q 恒 1。

        拿它当健康样本会让全部构造失效（damage ≡ 0、单调性无意义），
        所以 ``load_work_pair`` 根本不读它 —— 被拒绝的样本会进
        ``skipped`` 并带明��。
        """
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / 'ngc6888' / 'work'
            work.mkdir(parents=True)
            original, starless = synthetic_nebula(size=64)
            import sys as _sys
            _sys.path.insert(0, str(ROOT / 'scripts'))
            from fits_io import write_image
            write_image(original, str(work / '04_separation_reference.tif'))
            # 只放回退副本 —— 必须被拒绝
            write_image(original, str(work / '04_starless_fallback.tif'))
            got, got_sl, source = cal.load_work_pair(str(root), 'ngc6888')
            self.assertIsNone(got)
            self.assertIsNone(got_sl)
            self.assertIsNone(source)
            # 给上真实无星图后必须被接受
            write_image(starless, str(work / '04_starless_linear.tif'))
            got, got_sl, source = cal.load_work_pair(str(root), 'ngc6888')
            self.assertIsNotNone(got)
            self.assertEqual(source, '04_starless_linear.tif')
            del json


class VerdictTests(unittest.TestCase):
    """汇总层：**不给线，只报能不能定线**。"""

    #: 「V4 抖动已测且通过」的最小夹具。v0.1.35 起 ``summarize()`` 会读
    #: ``v4_data_jitter``（此前完全不读，是 V4 带bug 却没被发现的直接原因），
    #: 所以每个样本夹具都必须带这一段，否则测的是「缺键时会不会崩」而不是
    #: 「判定对不对」。V4 自身的测试在 test_star_jitter_stratification.py。
    @staticmethod
    def _v4(n_measurable=5, spread=0.01, unmeasurable=0):
        return {
            'applicable': True, 'verdict': 'measured',
            'strata': {'measurable': {'n': n_measurable, 'spread': spread},
                       'unmeasurable': (None if unmeasurable == 0 else
                                        {'n': unmeasurable, 'spread': 0.0})},
            'unmeasurable_fraction': unmeasurable / 9.0,
            'q_spread': 0.05,
        }

    def test_no_data_is_reported_not_guessed(self):
        verdict = cal.summarize({'samples': [], 'skipped': []})
        self.assertEqual(verdict['status'], 'no_data')

    def test_sparse_corpus_blocks_with_reason(self):
        report = {'samples': [
            {'target': 'a', 'target_type': 'emission_nebula',
             'v1_closed_form': {'passed': True},
             'v3_isolation': {'isolation_valid': True},
             'v4_data_jitter': self._v4()},
        ], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertEqual(verdict['status'], 'blocked')
        self.assertTrue(any('PROVISIONAL' in r
                            for r in verdict['blocking_reasons']))

    def test_failed_closed_form_blocks(self):
        report = {'samples': [
            {'target': name, 'target_type': 'galaxy',
             'v1_closed_form': {'applicable': True, 'passed': ok},
             'v3_isolation': {'isolation_valid': True},
             'healthy': {'nebula_damage_measurable': True,
                         'nebula_damage_ratio': 0.24},
             'v4_data_jitter': self._v4()}
            for name, ok in (('a', False), ('b', True), ('c', True))
        ], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertEqual(verdict['status'], 'blocked')
        self.assertTrue(any('V1' in r for r in verdict['blocking_reasons']))

    def test_not_applicable_is_not_treated_as_failed(self):
        """「没测」≠「不符」—— 不可测样本不该拉出「闭式不成立」的假结论。

        M17 实测 ``separation=1.017 < 1.30`` 使 damage 被强制为 0，
        闭式无从验证。第一版把这种情况报成 ``worst=0.41`` 的「失败」，
        会让人以为闭式被证伪 —— 实际是**样本不可测**。
        """
        report = {'samples': [
            {'target': name, 'target_type': 'emission_nebula',
             'v1_closed_form': {'applicable': True, 'passed': True},
             'v3_isolation': {'isolation_valid': True},
             'healthy': {'nebula_damage_measurable': True,
                         'nebula_damage_ratio': 0.24},
             'v4_data_jitter': self._v4()}
            for name in ('a', 'b')
        ] + [
            {'target': 'unmeasurable_one', 'target_type': 'emission_nebula',
             'v1_closed_form': {'applicable': False,
                                'reason': '健康样本不可测'},
             'v3_isolation': {'isolation_valid': True},
             'healthy': {'nebula_damage_measurable': False,
                         'nebula_damage_ratio': 0.0},
             'v4_data_jitter': self._v4(n_measurable=0, unmeasurable=9)}
        ], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertEqual(verdict['v1_not_applicable'], ['unmeasurable_one'])
        self.assertIn('unmeasurable_one', verdict['unmeasurable_samples'])
        self.assertFalse(any('V1 闭式未通过' in r
                             for r in verdict['blocking_reasons']))
        # 不可测样本应被单独点名，而不是混进「闭式失败」
        self.assertTrue(any('不可测' in r
                            for r in verdict['blocking_reasons']))

    def test_ready_only_when_all_gates_pass(self):
        """全部通过（含 V5 夹逼）⇒ ready_to_line。

        劣化档必须抬到 **0.40**：全局 ``LINE_RELAX_FACTOR = 1.5`` 会把
        健康 0.24 抬到 ``0.36``，若劣化档只到 0.30 则区间为空 ⇒ blocked。
        夹具的档位必须自己算一遍，不能凭感觉填。
        """
        def _v4():
            return {'applicable': True, 'verdict': 'measured',
                    'strata': {'measurable': {'n': 5, 'spread': 0.01},
                               'unmeasurable': None},
                    'unmeasurable_fraction': 0.0, 'q_spread': 0.05}

        def _constructs(healthy, degraded):
            return {'A': [{'ratio': 1.00, 'param': 1.00,
                           'nebula_damage_ratio': healthy},
                          {'ratio': 0.95, 'param': 0.95,
                           'nebula_damage_ratio': degraded}]}

        report = {'samples': [
            {'target': name, 'target_type': 'galaxy',
             'v1_closed_form': {'applicable': True, 'passed': True},
             'v3_isolation': {'isolation_valid': True},
             'healthy': {'nebula_damage_measurable': True,
                         'nebula_damage_ratio': 0.24, 'q': 0.6},
             'constructs': _constructs(0.24, 0.40),
             'v4_data_jitter': _v4()}
            for name in ('a', 'b', 'c')
        ], 'skipped': []}
        verdict = cal.summarize(report)
        self.assertEqual(verdict['line_candidate']['status'], 'lineable')
        self.assertEqual(verdict['status'], 'ready_to_line')

    def test_missing_healthy_key_does_not_crash(self):
        """缺 ``healthy`` 键时不得抛 KeyError。

        这不是假想：``summarize()`` 上层循环用 ``(s.get('healthy') or {})``
        做了防御，而本函数曾用 ``s['healthy'][...]`` 直接下标 ——
        防御不一致就会在缺键的夹具上崩掉。
        """
        report = {'samples': [
            {'target': 'a', 'target_type': 'galaxy',
             'v1_closed_form': {'applicable': True, 'passed': True},
             'v3_isolation': {'isolation_valid': True},
             'v4_data_jitter': self._v4()},
        ], 'skipped': []}
        verdict = cal.summarize(report)   # 不应抛异常
        self.assertIn('a', verdict['per_target_type']['galaxy'])


class HardLineReachabilityTests(unittest.TestCase):
    """硬线可达性：score 分支的饱和点会吃掉 damage 的高段。"""

    def test_uncalibrated_profile_reports_no_hard_line(self):
        """默认档未标定 ⇒ **没有硬线可言**（v0.1.38）。

        不能让 ``None * multiplier`` 崩，也不能报一个假的 0.0 ——
        那是把「未标定」伪装成「阈值极宽松」，方向完全相反。
        """
        replay = cal.replay_gate_states({}, {'gate_state': 'ok'},
                                       'emission_nebula')
        self.assertIsNone(replay['damage_limit'])
        self.assertFalse(replay['damage_limit_calibrated'])
        self.assertIsNone(replay['hard_line'])
        self.assertIsNone(replay['hard_reachable'])
        self.assertIn('uncalibrated', replay['note'])

    def test_calibrated_profile_hard_line_exceeds_score_saturation(self):
        """**已标定**档（damage_limit=0.20）下硬线 0.40 > score 饱和点 0.225。

        ``min(damage * 2.0, 0.45)`` 在 damage 达 0.225 后饱和，且门禁
        **先判 score** ⇒ damage > 0.225 区间由 score 分支实际把关。
        报告必须说明这一点，否则验收者会以为 damage 硬线在生效。
        """
        import dataclasses
        import star_removal_profiles as srp
        calibrated = dataclasses.replace(srp.DEFAULTS, name='calibrated',
                                         damage_limit=0.20)
        original = srp.PROFILES['emission_nebula']
        srp.PROFILES['emission_nebula'] = calibrated
        try:
            replay = cal.replay_gate_states({}, {'gate_state': 'ok'},
                                           'emission_nebula')
        finally:
            srp.PROFILES['emission_nebula'] = original
        self.assertAlmostEqual(replay['hard_line'], 0.40)
        self.assertFalse(replay['hard_reachable'])
        self.assertIn('score 分支', replay['note'])

    def test_healthy_sample_gate_state_is_reported(self):
        report = cal.replay_gate_states(
            {'A': [cal.collect_sample(*[cal.downsample(x) for x in
                                       synthetic_nebula()], None, 'A=0.5')]},
            {'gate_state': 'ok'}, 'emission_nebula')
        self.assertEqual(report['healthy'], 'ok')
        self.assertIn('A', report['by_construct'])


if __name__ == '__main__':
    unittest.main()
