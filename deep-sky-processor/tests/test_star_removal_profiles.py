"""去星门禁分档（star_removal_profiles）的机制测试。

锁三件事：

1. **棘轮方向逐字段正确** —— 特别是 ``repair_cap_*`` 与 ``repair_weight_*``
   **反向**。全局 ``max`` 会把 cap 放松，而放松是静默的（报告显示
   「已应用」，实际阈值比默认更松）。
2. **星团 3 类不可达** —— 它们结构上到不了门禁（pipeline 会删掉 4 个
   星点步骤），建档位等于给不可达路径写规则。
3. **弃用字段不进棘轮** —— ``mask_max_coverage`` 保留仅为 schema 兼容，
   参与分档会让维护者误以为「掩膜退化」场景已受控。
"""

import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import star_removal_profiles as srp


class RatchetDirectionTests(unittest.TestCase):
    #: 未标定字段：``None`` 不参与棘轮算术（会 TypeError），
    #: 语义也完全不同（不可被具体值覆盖），由
    #: :class:`RatchetUncalibratedTests` 单独覆盖。
    #: 注：``damage_delta_limit`` 自 v0.1.44 起**已标定**（0.005），
    #:   故不在此列，走普通棘轮。
    UNCALIBRATED = ('damage_limit',)

    def _looser(self, field):
        """按声明方向推出一个「更松」的值。

        不能硬编码：``tighter_is_higher=True`` 的字段里「更小」才是更松
        （strictest 取 max），与「阈值大 = 严」的直觉相反 —— 硬编码会把
        测试数据本身搞错，然后测试「正确地」失败。

        bool 字段（``star_flux_normalize``）不能走乘法：``False * 0.1``
        得到 ``0.0`` 而不是布尔值，棘轮比较会拿到一个「方向未知」的类型。
        对 bool，「更松」就是从 True 退到 False，反之亦然。
        """
        base = getattr(srp.DEFAULTS, field)
        if isinstance(base, bool):
            return (not base) if srp.TIGHTER_IS_HIGHER[field] else bool(base)
        return base * 0.1 if srp.TIGHTER_IS_HIGHER[field] else base * 10.0

    def _tighter(self, field):
        base = getattr(srp.DEFAULTS, field)
        if isinstance(base, bool):
            return bool(base) if srp.TIGHTER_IS_HIGHER[field] else (not base)
        return base * 2.0 if srp.TIGHTER_IS_HIGHER[field] else base * 0.5

    def test_relax_is_blocked_for_every_tightening_field(self):
        """放松（把阈值往更松的方向改）必须全部被棘轮挡住。

        包含 ``max_score_growth``：它的 base 是 0.0，``_looser`` 只能给出
        0.0（0.0×10 仍是 0.0），所以该字段单独在
        :meth:`test_saturated_score_growth_blocks_relaxation` 里测。
        """
        for field in srp.TIGHTER_IS_HIGHER:
            # bool 字段的默认值就是**最松**态（未启用新判据），「更松」
            # 无从谈起 ⇒ 该字段在 SATURATED_BASE 之外无法参与本测试。
            if field in self.UNCALIBRATED or field == 'max_score_growth':
                continue
            if isinstance(getattr(srp.DEFAULTS, field), bool):
                continue
            value = self._looser(field)
            with self.subTest(field=field, value=value):
                new, bound = srp.apply_profile(srp.DEFAULTS, value, field)
                self.assertTrue(bound, '%s 的放松未被棘轮挡住' % field)
                self.assertEqual(getattr(new, field),
                                 getattr(srp.DEFAULTS, field),
                                 '%s 被放松了' % field)

    def test_tightening_is_applied_for_every_field(self):
        """调严必须生效 —— 否则棘轮变成「全部拒绝」。"""
        for field in srp.TIGHTER_IS_HIGHER:
            if field in self.UNCALIBRATED or field == 'max_score_growth':
                continue  # base 已是 0.0，无可收紧空间；UNCALIBRATED 字段另测
            value = self._tighter(field)
            with self.subTest(field=field, value=value):
                new, bound = srp.apply_profile(srp.DEFAULTS, value, field)
                self.assertFalse(bound, '%s 的调严被误判为被挡住' % field)
                self.assertEqual(getattr(new, field), value)

    def test_saturated_score_growth_blocks_relaxation(self):
        """``max_score_growth`` 棘轮**饱和**（base = 0.0，零容忍）。

        非回归零容忍不可放松：任何正值都意味着「允许重试让质量分变差」。
        正确行为是挡住并记 ``ratchet_bound`` —— 这让「有人试图放松非回归」
        在报告里可审计，而不是静默生效。
        """
        for value in (0.0, 0.1, 1.0):
            with self.subTest(value=value):
                new, _bound = srp.apply_profile(srp.DEFAULTS, value,
                                                'max_score_growth')
                self.assertEqual(new.max_score_growth, 0.0)
        # 正值必须被明确记为「被挡住」
        _new, bound = srp.apply_profile(srp.DEFAULTS, 0.1, 'max_score_growth')
        self.assertTrue(bound)

    def test_repair_cap_and_weight_have_opposite_directions(self):
        """权重越大越严、上限越小越严 —— 这对反向是全局 max 的经典陷阱。"""
        self.assertTrue(srp.TIGHTER_IS_HIGHER['repair_weight_damage'])
        self.assertFalse(srp.TIGHTER_IS_HIGHER['repair_cap_damage'])
        # 若实现退化成全局 max，cap 的放松会被放过
        relaxed_cap, bound = srp.apply_profile(srp.DEFAULTS, 0.9,
                                              'repair_cap_damage')
        self.assertTrue(bound)
        self.assertEqual(relaxed_cap.repair_cap_damage,
                         srp.DEFAULTS.repair_cap_damage)
        # 反向：cap 调小（更严）应被接受
        tighter_cap, bound = srp.apply_profile(srp.DEFAULTS, 0.1,
                                               'repair_cap_damage')
        self.assertFalse(bound)
        self.assertEqual(tighter_cap.repair_cap_damage, 0.1)

    def test_baseline_change_tightens_by_shrinking(self):
        """baseline_change 是从 raw_change 里**减掉**的基线。

        调小才是更严（少减一点 = 判出的误伤更大）。这一条方向反了就会
        把「更宽松」误当成「更严格」，且不会有任何报错。
        """
        self.assertFalse(srp.TIGHTER_IS_HIGHER['baseline_change'])
        smaller, bound = srp.apply_profile(srp.DEFAULTS, 0.02,
                                          'baseline_change')
        self.assertFalse(bound)
        self.assertEqual(smaller.baseline_change, 0.02)
        _larger, bound = srp.apply_profile(srp.DEFAULTS, 0.5, 'baseline_change')
        self.assertTrue(bound)

    def test_measurability_direction_is_inverted(self):
        """可测性阈值**调大**才是更严。

        反直觉但必须如此：``min_subject_separation`` 抬高才让更多数据
        落入「可测」区间，从而让更多数据进入 damage 判定。
        """
        self.assertTrue(srp.TIGHTER_IS_HIGHER['min_subject_separation'])
        higher, bound = srp.apply_profile(srp.DEFAULTS, 2.5,
                                          'min_subject_separation')
        self.assertFalse(bound)
        self.assertEqual(higher.min_subject_separation, 2.5)
        _lower, bound = srp.apply_profile(srp.DEFAULTS, 1.0,
                                          'min_subject_separation')
        self.assertTrue(bound)

    def test_star_flux_normalize_ratchet_direction(self):
        """bool 字段的棘轮方向：True 视为更严，且默认档是**最松**态。

        默认 False（未启用归一化判据）⇒ 「放松」= True→False，
        放松必须被挡；反向的「调严」= False→True，必须放行。
        bool 无法用 ``base * 0.1`` 构造更松值（False*0.1=0.0 而非布尔），
        所以这三条不能混进上面的数值循环。
        """
        self.assertTrue(srp.TIGHTER_IS_HIGHER['star_flux_normalize'])
        self.assertFalse(srp.DEFAULTS.star_flux_normalize)
        # 调严：False → True 放行
        tighter, bound = srp.apply_profile(srp.DEFAULTS, True,
                                            'star_flux_normalize')
        self.assertTrue(tighter.star_flux_normalize)
        self.assertFalse(bound)
        # 放松：True → False 必须被挡（且记成 bound）
        base_true = dataclasses.replace(srp.DEFAULTS,
                                        star_flux_normalize=True)
        relaxed, bound = srp.apply_profile(base_true, False,
                                            'star_flux_normalize')
        self.assertTrue(relaxed.star_flux_normalize)
        self.assertTrue(bound, 'bool 字段的放松未被棘轮挡住')

    def test_tighter_is_higher_is_never_supplied_by_call_site(self):
        """``strictest`` 的方向参数必须来自字段常量，不允许调用点临时判断。

        方向的错误是静默的：放松了却显示「已应用」。此处对每个字段验证
        「按声明方向构造的更松值」必定被 strictest 拒绝。
        """
        for field, tighter in srp.TIGHTER_IS_HIGHER.items():
            if field in self.UNCALIBRATED:
                continue
            if isinstance(getattr(srp.DEFAULTS, field), bool):
                # bool 字段的棘轮方向单独在
                # :meth:`test_star_flux_normalize_ratchet_direction` 验证。
                continue
            with self.subTest(field=field):
                base = getattr(srp.DEFAULTS, field)
                looser = self._looser(field)
                self.assertEqual(srp.strictest(base, looser, tighter), base)


class DeprecatedFieldTests(unittest.TestCase):
    def test_mask_max_coverage_is_deprecated(self):
        self.assertIn('mask_max_coverage', srp.DEPRECATED_FIELDS)

    def test_deprecated_field_never_enters_ratchet(self):
        """弃用字段的任何改动都必须被忽略（既不放松也不调严）。"""
        for value in (0.90, 0.10, 0.0, 1.0):
            with self.subTest(value=value):
                new, bound = srp.apply_profile(srp.DEFAULTS, value,
                                               'mask_max_coverage')
                self.assertEqual(new.mask_max_coverage,
                                 srp.DEFAULTS.mask_max_coverage)
                self.assertFalse(bound)

    def test_deprecated_field_excluded_from_applied_report(self):
        applied = srp.DEFAULTS.as_applied()
        self.assertNotIn('mask_max_coverage', applied)
        self.assertNotIn('name', applied)
        self.assertNotIn('reachable', applied)

    def test_deprecated_listed_in_report(self):
        report = srp.profile_report(srp.DEFAULTS, 'emission_nebula')
        self.assertEqual(report['deprecated'], ['mask_max_coverage'])

    def test_coverage_still_reported_for_observation(self):
        """字段保留在 dataclass 里（schema 兼容 + 人工观测）。"""
        self.assertTrue(hasattr(srp.DEFAULTS, 'mask_max_coverage'))
        self.assertIn('mask_max_coverage',
                      {f.name for f in dataclasses.fields(srp.RemovalProfile)})


class TargetTypeTableTests(unittest.TestCase):
    def test_star_cluster_types_are_unreachable(self):
        """星团 3 类结构上到不了门禁（pipeline 删掉 4 个星点步骤）。"""
        for target_type in ('globular_cluster', 'open_cluster',
                            'star_cluster'):
            with self.subTest(target_type=target_type):
                profile = srp.get_profile(target_type)
                self.assertFalse(profile.reachable)
                self.assertEqual(profile.name, target_type)

    def test_reachable_profiles_have_their_own_name(self):
        for target_type in ('emission_nebula', 'galaxy', 'planetary_nebula',
                            'reflection_nebula', 'dark_nebula'):
            with self.subTest(target_type=target_type):
                self.assertEqual(srp.get_profile(target_type).name,
                                 target_type)
                self.assertTrue(srp.get_profile(target_type).reachable)

    def test_supernova_and_wide_field_are_provisional(self):
        """无现成母版的两类：建空档但标 PROVISIONAL。"""
        for target_type in ('supernova_remnant', 'wide_field'):
            with self.subTest(target_type=target_type):
                profile = srp.get_profile(target_type)
                self.assertEqual(profile.name, 'provisional')
                self.assertTrue(profile.provisional)

    def test_unknown_type_falls_back_to_default(self):
        for target_type in (None, '', 'not_a_type'):
            with self.subTest(target_type=target_type):
                self.assertEqual(srp.get_profile(target_type).name, 'default')

    def test_table_only_references_known_target_types(self):
        """表里的键必须都是 target_rules 认识的类型。

        拼错一个键 = 该类型永远落默认档，且**不报错** ——
        静默失效比报错更难查。
        """
        from target_rules import _KNOWN_TYPES
        unknown = set(srp.PROFILE_TARGET_TYPES) - set(_KNOWN_TYPES)
        self.assertEqual(unknown, set())

    def test_direction_table_covers_all_non_deprecated_fields(self):
        """方向表漏一个字段 = 该字段的棘轮方向未声明，实现会 KeyError
        或（更糟）悄悄走默认分支。"""
        field_names = {f.name for f in dataclasses.fields(srp.RemovalProfile)}
        meta = {'name', 'reachable', 'provisional'}
        self.assertEqual(
            set(srp.TIGHTER_IS_HIGHER) | srp.DEPRECATED_FIELDS,
            field_names - meta)

    def test_max_score_growth_is_zero_tolerance(self):
        """非回归零容忍：分数可以不变或变好，绝不允许变差。"""
        self.assertEqual(srp.DEFAULTS.max_score_growth, 0.0)


class DefaultsBackwardsCompatibilityTests(unittest.TestCase):
    """分档上线当天行为必须与 v0.1.33 逐位相同 —— **除 ``damage_limit``**。

    这组测试是「分档不是偷偷放宽」的可执行断言：任何改动 DEFAULTS
    数值的行为都会在这里被抓住，除非同时更新此处并说明理由。

    ``damage_limit`` 自 **v0.1.38** 起改为 ``None``（未标定）：
    v0.1.37 的标定实测**证否了它可标定** —— 5 份可测样本的健康
    damage跨 0.0357~0.3580（10 倍），而「最轻劣化」相对各自健康态
    只抬高 0.033 ⇒ 双向夹逼区间为空。见
    ``references/star_damage_gate_calibration.md`` §3.12。
    """

    def test_damage_limit_is_uncalibrated(self):
        """绝对判据阈值：``damage_limit = None``（未标定）。"""
        self.assertIsNone(srp.DEFAULTS.damage_limit)
        self.assertFalse(srp.DEFAULTS.damage_limit_calibrated)

    def test_damage_delta_limit_is_calibrated(self):
        """相对判据阈值已标定（v0.1.44）。

        标定依据：6 份真实 StarNet2 产物，健康态 delta 按构造为 0，
        合成劣化 beta=0.02 时最严样本的 delta 下界为 0.0071 ⇒ 分离区间
        (0, 0.0071)。取值 0.005 = 上界的 0.70x，留 30% 余量。
        """
        self.assertIsNotNone(srp.DEFAULTS.damage_delta_limit)
        self.assertAlmostEqual(srp.DEFAULTS.damage_delta_limit, 0.005)
        # 不得贴着分离上界（0.0071）—— 那是「刚好可分」，见 §6.7 警告
        self.assertLess(srp.DEFAULTS.damage_delta_limit, 0.0071)

    def test_damage_delta_limit_ratchets_tighter_only(self):
        """已标定字段走正常棘轮：放松被挡、收紧放行。

        ``apply_profile`` 的第三参是**标量**（与 :func:`strictest` 同签名），
        不是整个 profile 对象。
        """
        new, bound = srp.apply_profile(srp.DEFAULTS, 0.20, "damage_delta_limit")
        self.assertAlmostEqual(new.damage_delta_limit, 0.005)
        self.assertTrue(bound, "放松必须被棘轮挡住")

        new2, bound2 = srp.apply_profile(srp.DEFAULTS, 0.001,
                                         "damage_delta_limit")
        self.assertAlmostEqual(new2.damage_delta_limit, 0.001)
        self.assertFalse(bound2, "收紧应被放行")

    def test_damage_delta_limit_is_not_target_specific(self):
        """相对判据阈值**不分档** —— 全部目标类型共用一个常数。

        这是相对判据相对绝对判据的关键优势：绝对读数跨样本跨 10 倍，
        必须按 target_type 分档；相对读数只度量「比上次多坏多少」，
        量纲与目标类型无关。
        """
        names = set(srp.PROFILE_TARGET_TYPES)
        seen = set()
        for t in names:
            seen.add(srp.get_profile(t).damage_delta_limit)
        self.assertEqual(len(seen), 1,
                         "相对判据阈值不应随 target_type 变化：%r" % (seen,))

    def test_other_defaults_match_v013_constants(self):
        self.assertEqual(srp.DEFAULTS.advisory_multiplier, 2.0)
        self.assertEqual(srp.DEFAULTS.baseline_change, 0.10)
        self.assertEqual(srp.DEFAULTS.needs_starnet_residual, 0.02)
        self.assertEqual(srp.DEFAULTS.needs_starnet_damage, 0.15)
        self.assertEqual(srp.DEFAULTS.min_quality_score, 0.45)
        self.assertEqual(srp.DEFAULTS.repair_weight_residual, 20.0)
        self.assertEqual(srp.DEFAULTS.repair_weight_damage, 2.0)
        self.assertEqual(srp.DEFAULTS.repair_weight_gradient, 3.0)
        self.assertEqual(srp.DEFAULTS.repair_cap_residual, 0.5)
        self.assertEqual(srp.DEFAULTS.repair_cap_damage, 0.45)
        self.assertEqual(srp.DEFAULTS.repair_cap_gradient, 0.2)
        self.assertEqual(srp.DEFAULTS.quality_band_good, 0.7)
        self.assertEqual(srp.DEFAULTS.quality_band_marginal, 0.4)
        self.assertEqual(srp.DEFAULTS.min_subject_separation, 1.30)

    def test_every_profile_shares_default_values(self):
        """所有档位当前都等于默认值 —— 放宽必须是一次显式的、带理由的改动。"""
        for name, profile in srp.PROFILES.items():
            with self.subTest(profile=name):
                self.assertEqual(profile.as_applied(),
                                 srp.DEFAULTS.as_applied())


if __name__ == '__main__':
    unittest.main()
