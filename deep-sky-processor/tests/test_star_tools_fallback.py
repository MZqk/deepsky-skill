import unittest
from unittest.mock import patch

import dataclasses
import numpy as np

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import star_removal_profiles
import star_tools


def detection(confidence):
    mask = np.zeros((32, 32), dtype=np.float32)
    mask[14:18, 14:18] = 1.0
    details = {
        "n_components_kept": 1,
        "n_components_total": 1,
        "confidence_info": {"low_confidence_reason": "test"},
    }
    return mask, confidence, details


def quality(score, needs_model=False):
    return {
        "repair_quality_score": score,
        "needs_starnet_plus": needs_model,
        "quality": "good" if score > 0.7 else "poor",
    }


class StarRemovalFallbackTests(unittest.TestCase):
    def setUp(self):
        self.image = np.full((32, 32), 0.1, dtype=np.float32)
        self.image[14:18, 14:18] = 1.0

    def test_low_confidence_skips_repair_and_returns_original(self):
        with patch("star_tools.detect_stars_multiscale", return_value=detection(0.2)), \
             patch("star_tools._repair_star_mask") as repair:
            starless, stars, mask, report = star_tools.separate_stars(
                self.image, min_confidence=0.3, return_report=True
            )

        repair.assert_not_called()
        np.testing.assert_array_equal(starless, self.image)
        self.assertFalse(np.any(stars))
        self.assertFalse(np.any(mask))
        self.assertTrue(report["fallback_applied"])
        self.assertEqual(report["fallback_reason"], "low_detection_confidence")

    def test_no_response_details_also_fall_back_safely(self):
        no_response = (
            np.zeros((32, 32), dtype=np.float32),
            0.0,
            {
                "confidence": 0.0,
                "reason": "no_response",
                "confidence_info": {},
            },
        )
        with patch("star_tools.detect_stars_multiscale", return_value=no_response):
            starless, stars, mask, report = star_tools.separate_stars(
                self.image, return_report=True
            )

        np.testing.assert_array_equal(starless, self.image)
        self.assertFalse(np.any(stars))
        self.assertFalse(np.any(mask))
        self.assertEqual(report["fallback_reason"], "low_detection_confidence")

    def test_low_quality_retries_and_accepts_better_result(self):
        first = self.image.copy()
        first[14:18, 14:18] = 0.4
        retry = self.image.copy()
        retry[14:18, 14:18] = 0.1

        with patch("star_tools.detect_stars_multiscale", return_value=detection(0.8)), \
             patch("star_tools._repair_star_mask",
                   side_effect=[(first, "ns"), (retry, "telea")]) as repair, \
             patch("star_tools.estimate_star_removal_quality",
                   side_effect=[quality(0.3, True), quality(0.85, False)]):
            starless, _stars, _mask, report = star_tools.separate_stars(
                self.image, return_report=True
            )

        self.assertEqual(repair.call_count, 2)
        np.testing.assert_array_equal(starless, retry)
        self.assertTrue(report["accepted"])
        self.assertTrue(report["retry_attempted"])
        self.assertFalse(report["fallback_applied"])

    def test_failed_retry_falls_back_to_original(self):
        repaired = self.image.copy()
        repaired[14:18, 14:18] = 0.4

        with patch("star_tools.detect_stars_multiscale", return_value=detection(0.8)), \
             patch("star_tools._repair_star_mask",
                   side_effect=[(repaired, "ns"), (repaired, "telea")]), \
             patch("star_tools.estimate_star_removal_quality",
                   side_effect=[quality(0.3, True), quality(0.4, True)]):
            starless, stars, mask, report = star_tools.separate_stars(
                self.image, return_report=True
            )

        np.testing.assert_array_equal(starless, self.image)
        self.assertFalse(np.any(stars))
        self.assertFalse(np.any(mask))
        self.assertTrue(report["fallback_applied"])
        self.assertEqual(
            report["fallback_reason"],
            "star_removal_quality_below_threshold",
        )


class StarRemovalGateTest(unittest.TestCase):
    #: **已标定**的档（``damage_limit=0.20``）。默认档自 v0.1.38 起
    #: ``damage_limit=None``（未标定 ⇒ 走 uncalibrated），所以要验
    #: ``ok`` / ``advisory`` / ``hard_failed`` 三态的用例必须显式
    #: 传一个已标定档 —— 否则它们全都只会拿到 uncalibrated，
    #: 「两级门禁」这个逻辑就失去覆盖了。
    CALIBRATED = dataclasses.replace(
        star_removal_profiles.DEFAULTS, name='calibrated_test',
        damage_limit=0.20)

    """两级门禁 + 误伤可测性判据（借鉴 aiseestart 的 advisory/hard 分级）。"""

    def test_ok_when_quality_and_damage_within_limit(self):
        gate = star_tools._star_removal_gate(
            {"repair_quality_score": 0.8, "nebula_damage_ratio": 0.1,
             "nebula_damage_measurable": True}, 0.45,
            profile=self.CALIBRATED)
        self.assertEqual(gate, (True, "ok"))

    def test_advisory_when_damage_above_limit_below_hard_line(self):
        """误伤落在 (limit, limit×2] ⇒ 带警告接受，不全盘回退。"""
        accepted, state = star_tools._star_removal_gate(
            {"repair_quality_score": 0.8, "nebula_damage_ratio": 0.30,
             "nebula_damage_measurable": True}, 0.45,
            profile=self.CALIBRATED)
        self.assertTrue(accepted)
        self.assertEqual(state, "advisory")

    def test_hard_failed_when_damage_above_hard_line(self):
        accepted, state = star_tools._star_removal_gate(
            {"repair_quality_score": 0.8, "nebula_damage_ratio": 0.45,
             "nebula_damage_measurable": True}, 0.45,
            profile=self.CALIBRATED)
        self.assertFalse(accepted)
        self.assertEqual(state, "hard_failed")

    def test_hard_failed_when_score_below_minimum(self):
        """分数不达标时 damage 再低也不放行。"""
        accepted, state = star_tools._star_removal_gate(
            {"repair_quality_score": 0.30, "nebula_damage_ratio": 0.0,
             "nebula_damage_measurable": True}, 0.45,
            profile=self.CALIBRATED)
        self.assertFalse(accepted)
        self.assertEqual(state, "hard_failed")

    def test_unmeasurable_damage_does_not_reject(self):
        """误伤不可测时**不据此拒绝** —— 判定依据不存在，不能否决已完成的推理。"""
        accepted, state = star_tools._star_removal_gate(
            {"repair_quality_score": 0.8, "nebula_damage_ratio": 0.9,
             "nebula_damage_measurable": False}, 0.45)
        self.assertTrue(accepted)
        self.assertEqual(state, "advisory")

    def test_unmeasurable_damage_still_rejects_low_score(self):
        """不可测 + 分数不达标 ⇒ 仍拒绝（可测性只豁免误伤项）。"""
        accepted, _state = star_tools._star_removal_gate(
            {"repair_quality_score": 0.20, "nebula_damage_ratio": 0.9,
             "nebula_damage_measurable": False}, 0.45)
        self.assertFalse(accepted)

    def test_report_exposes_measurability_fields(self):
        """quality 报告必须带可测性判据字段，否则门禁无从区分「不可测」与「健康」。

        ``gate_state`` 由 :func:`_record_gate_state` 在**门禁处**写入，
        不由 quality 函数产出 —— 这是职责划分：quality 只测量，gate 只判定。
        """
        image = np.full((64, 64), 0.02, dtype=np.float32)
        image[30:34, 30:34] = 1.0
        report = star_tools.estimate_star_removal_quality(image, image)
        for key in ("nebula_damage_measurable", "nebula_mask_coverage",
                    "subject_separation"):
            self.assertIn(key, report)
        # gate_state 由门禁侧补写。用**已标定**档 —— 阈值未标定时
        # ``advisory`` 分支不写 note（因为没有阈值可报），这本身
        # 由UncalibratedDamageLimitTests 单独覆盖。
        star_tools._record_gate_state(report, "advisory", 0.45,
                                      self.CALIBRATED)
        self.assertEqual(report["gate_state"], "advisory")
        self.assertIn("gate_note", report)

    def test_flat_field_reports_damage_unmeasurable(self):
        """恒定画面（无星云主体）⇒ 误伤不可测，damage 报0 且不参与判定。"""
        flat = np.full((64, 64), 0.03, dtype=np.float32)
        report = star_tools.estimate_star_removal_quality(flat, flat)
        self.assertFalse(report["nebula_damage_measurable"])
        self.assertEqual(report["nebula_damage_ratio"], 0.0)
        self.assertEqual(report["quality"], "unmeasurable")
        accepted, _state = star_tools._star_removal_gate(report, 0.45)
        self.assertTrue(accepted)

    def test_constant_tolerance_is_zero_tolerance(self):
        """非回归零容忍：分数可以不变或变好，绝不允许变差。"""
        self.assertEqual(star_tools._STAR_REMOVAL_MAX_SCORE_GROWTH, 0.0)

    def test_damage_limit_and_multiplier_are_explicit(self):
        """``damage_limit`` 未标定（None），但**倍率仍是 2.0**。

        v0.1.38 起默认 ``damage_limit=None``（未标定）。倍率不随之失效 ——
        它是「接纳线以上到哪算 advisory」的系数，标定时会用上；
        未标定时门禁走 ``uncalibrated`` 分支，根本不看它。
        """
        self.assertIsNone(star_tools._STAR_REMOVAL_DAMAGE_LIMIT)
        self.assertAlmostEqual(star_tools._GATE_ADVISORY_MULTIPLIER, 2.0)

    def test_module_constants_derive_from_profile_defaults(self):
        """模块级常量必须从分档默认值派生，不能独立写死。

        独立写死就是 v0.1.33 的原状：改分档时只改一处、漏一处，
        棘轮于是放松了某一族而没人发现。
        """
        defaults = star_removal_profiles.DEFAULTS
        self.assertEqual(star_tools._STAR_REMOVAL_DAMAGE_LIMIT,
                         defaults.damage_limit)
        self.assertEqual(star_tools._GATE_ADVISORY_MULTIPLIER,
                         defaults.advisory_multiplier)
        self.assertEqual(star_tools._STAR_REMOVAL_BASELINE_CHANGE,
                         defaults.baseline_change)
        self.assertEqual(star_tools._STAR_REMOVAL_MAX_SCORE_GROWTH,
                         defaults.max_score_growth)
        self.assertEqual(star_tools._NEBULA_MIN_SUBJECT_SEPARATION,
                         defaults.min_subject_separation)

    def test_gate_note_uses_profile_thresholds_not_module_constants(self):
        """分档后 gate_note 必须报 profile 的值。

        验收者靠这些数字判断门禁口径；硬编码模块常量会在换档后打印
        错误的阈值，而报告本身完全看不出异常。
        """
        report = {"nebula_damage_ratio": 0.5,
                  "repair_quality_score": 0.1}
        relaxed = dataclasses.replace(star_removal_profiles.DEFAULTS,
                                      damage_limit=0.60)
        star_tools._record_gate_state(report, "hard_failed", 0.45, relaxed)
        self.assertIn("接纳线 0.60", report["gate_note"])
        self.assertIn("硬线 1.20", report["gate_note"])
        # 默认档（未标定）下同一读数**不报阈值** —— 证明确实读了 profile
        report2 = {"nebula_damage_ratio": 0.5, "repair_quality_score": 0.1}
        star_tools._record_gate_state(report2, "hard_failed", 0.45)
        self.assertNotIn("接纳线", report2["gate_note"])
        self.assertIn("不参与判定", report2["gate_note"])

    def test_gate_state_changes_with_profile_threshold(self):
        """同一读数在不同档下必须给出不同门禁结论。"""
        report = {"repair_quality_score": 0.9,
                  "nebula_damage_ratio": 0.30,
                  "nebula_damage_measurable": True}
        strict = dataclasses.replace(star_removal_profiles.DEFAULTS,
                                     damage_limit=0.20)
        relaxed = dataclasses.replace(star_removal_profiles.DEFAULTS,
                                      damage_limit=0.60)
        self.assertEqual(
            star_tools._star_removal_gate(report, 0.45, profile=strict)[1],
            "advisory")
        self.assertEqual(
            star_tools._star_removal_gate(report, 0.45, profile=relaxed)[1],
            "ok")

    def test_separate_stars_reports_profile_and_gate_evaluated(self):
        """任何返回路径都要带 star_removal_profile 与 gate_evaluated。"""
        image = np.full((32, 32), 0.1, dtype=np.float32)
        image[14:18, 14:18] = 1.0
        with patch("star_tools.detect_stars_multiscale",
                   return_value=detection(0.2)):
            _sl, _st, _m, report = star_tools.separate_stars(
                image, min_confidence=0.3, return_report=True,
                target_type="emission_nebula")
        self.assertIn("star_removal_profile", report)
        self.assertTrue(report["gate_evaluated"])
        self.assertEqual(report["star_removal_profile"]["profile_name"],
                         "emission_nebula")
        self.assertEqual(
            report["star_removal_profile"]["deprecated"],
            ["mask_max_coverage"])

    def test_external_starless_path_is_marked_gate_not_evaluated(self):
        """外部无星层不进本模块门禁 —— 必须显式标注。

        否则「无 gate_state」会被读成「默认档通过」，那是两种完全不同的
        结论：前者是「没判」，后者是「判过且通过」。
        """
        image = np.full((32, 32, 3), 0.1, dtype=np.float32)
        image[14:18, 14:18, :] = 1.0
        starless = np.full((32, 32, 3), 0.08, dtype=np.float32)
        _sl, _st, _m, report = star_tools.separate_stars(
            image, external_starless=starless, return_report=True,
            target_type="galaxy")
        self.assertFalse(report["gate_evaluated"])
        self.assertIsNone(report["gate_state"])
        self.assertEqual(report["source"], "external_starless")
        self.assertIn("star_removal_profile", report)

    def test_star_cluster_type_reports_unreachable_profile(self):
        image = np.full((32, 32), 0.1, dtype=np.float32)
        image[14:18, 14:18] = 1.0
        with patch("star_tools.detect_stars_multiscale",
                   return_value=detection(0.2)):
            _sl, _st, _m, report = star_tools.separate_stars(
                image, min_confidence=0.3, return_report=True,
                target_type="globular_cluster")
        self.assertFalse(report["star_removal_profile"]["reachable"])


if __name__ == "__main__":
    unittest.main()


class UncalibratedDamageLimitTests(unittest.TestCase):
    """``damage_limit=None`` ⇒门禁走 ``uncalibrated``（v0.1.38）。

    这是本轮最关键的回归网：``float <= None`` 在Python 是
    **TypeError** 而非静默降级 —— 漏改任何一个消费点就是全线崩，
    而「崩」会表现为整份标定/管线跑不出来，不会被误读成「阈值没定」。
    """

    @staticmethod
    def _report(damage=0.2983, measurable=True, score=0.542):
        return {'nebula_damage_ratio': damage,
                'nebula_damage_measurable': measurable,
                'repair_quality_score': score}

    def test_none_damage_limit_does_not_crash(self):
        """5 个消费点逐个跑，任何一个未处理都会抛 TypeError。"""
        report = self._report()
        accepted, state = star_tools._star_removal_gate(report, 0.45,
                                                        'emission_nebula')
        self.assertTrue(accepted)
        self.assertEqual(state, 'uncalibrated')
        # _record_gate_state 的 None 分支
        star_tools._record_gate_state(report, state, 0.45)
        self.assertIn('gate_note', report)
        # 不可测 + 未标定的组合
        rep2 = self._report(damage=0.0, measurable=False)
        acc2, st2 = star_tools._star_removal_gate(rep2, 0.45,
                                                  'emission_nebula')
        self.assertTrue(acc2)
        self.assertEqual(st2, 'advisory')
        star_tools._record_gate_state(rep2, st2, 0.45)

    def test_gate_state_uncalibrated_not_advisory(self):
        """``uncalibrated`` 与 ``advisory`` **必须可区分**。

        ``advisory`` 已被「这张图测不了」（``measurable=False``）占用。
        混用会让报告里「阈值没定」与「读数不可用」无法区分 ——
        而两者的排查方向完全不同（前者补标定，后者换数据）。
        """
        _acc_a, uncal = star_tools._star_removal_gate(
            self._report(), 0.45, 'emission_nebula')
        _acc_b, unavail = star_tools._star_removal_gate(
            self._report(measurable=False), 0.45, 'emission_nebula')
        self.assertEqual(uncal, 'uncalibrated')
        self.assertEqual(unavail, 'advisory')
        self.assertNotEqual(uncal, unavail)

    def test_uncalibrated_still_judges_by_score(self):
        """未标定**不等于**放行：质量分不达标仍须拒。

        ``repair_quality_score`` 有实测依据（残留 2.5% / 误伤 22% /
        硬边 6.7% 三档扣满），应继续拦住「去星完全没生效」这类硬失败。
        """
        report = self._report(score=0.10)
        accepted, state = star_tools._star_removal_gate(report, 0.45,
                                                        'emission_nebula')
        self.assertFalse(accepted, '质量分 0.10 不该被接受')
        self.assertEqual(state, 'hard_failed')
        star_tools._record_gate_state(report, state, 0.45)
        self.assertIn('质量分', report['gate_note'])

    def test_gate_note_explains_uncalibrated(self):
        report = self._report()
        _acc, state = star_tools._star_removal_gate(report, 0.45,
                                                     'emission_nebula')
        star_tools._record_gate_state(report, state, 0.45)
        note = report['gate_note']
        self.assertIn('不参与判定', note)
        self.assertIn('0.2983', note)
        # 绝不能出现「接纳线 None」或「接纳线 nan」这类会被误读的措辞
        self.assertNotIn('None', note)
        self.assertNotIn('nan', note)

    def test_damage_reading_still_recorded(self):
        """``uncalibrated`` 是**不判定**，不是**不测量**。

        误伤读数是唯一的「伤了多少」量化，删了就没有任何观测。
        """
        report = self._report(damage=0.2983)
        _acc, state = star_tools._star_removal_gate(report, 0.45,
                                                     'emission_nebula')
        self.assertEqual(state, 'uncalibrated')
        self.assertEqual(report['nebula_damage_ratio'], 0.2983)

    def test_starnet_reject_note_has_no_none(self):
        """``starnet_reject_note`` 不得出现「接纳线 None」。"""
        prof = star_removal_profiles.get_profile('emission_nebula')
        self.assertIsNone(prof.damage_limit)
        limit_text = ('接纳线未标定' if prof.damage_limit is None
                      else '接纳线 %.2f' % prof.damage_limit)
        self.assertNotIn('None', limit_text)
        self.assertIn('未标定', limit_text)


class RatchetUncalibratedTests(unittest.TestCase):
    """未标定字段**不可被具体值覆盖**。"""

    def test_ratchet_cannot_fill_none(self):
        profile, bound = star_removal_profiles.apply_profile(
            star_removal_profiles.DEFAULTS, 0.25, 'damage_limit')
        self.assertIsNone(profile.damage_limit,
                          'None 被具体值覆盖 ⇒「未标定」消失')
        self.assertTrue(bound, '试图定值这件事必须记入 ratchet_bound')

    def test_uncalibrated_fields_reported(self):
        report = star_removal_profiles.profile_report(
            star_removal_profiles.DEFAULTS, 'emission_nebula')
        self.assertFalse(report['damage_limit_calibrated'])
        self.assertEqual(report['uncalibrated'], ['damage_limit'])

    def test_all_types_are_uncalibrated(self):
        """全部档都未标定（v0.1.38 决策：6 个零样本档同样未标定）。"""
        for target in ('emission_nebula', 'galaxy', 'planetary_nebula',
                       'reflection_nebula', 'dark_nebula',
                       'supernova_remnant', 'wide_field', None):
            with self.subTest(target=target):
                profile = star_removal_profiles.get_profile(target)
                self.assertIsNone(profile.damage_limit)
                self.assertFalse(profile.damage_limit_calibrated)

    def test_other_fields_still_calibrated(self):
        """只有 ``damage_limit`` 变None，其余阈值不动。

        ``min_subject_separation`` / E 型红线 / 闭式都已被独立验证，
        一起改会让那些验证全部失效。
        """
        defaults = star_removal_profiles.DEFAULTS
        self.assertEqual(defaults.min_subject_separation, 1.30)
        self.assertEqual(defaults.baseline_change, 0.10)
        self.assertEqual(defaults.min_quality_score, 0.45)
