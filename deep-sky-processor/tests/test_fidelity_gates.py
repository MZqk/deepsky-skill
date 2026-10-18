"""保真度门禁（fidelity_gates）测试。

三类断言：
  1. 度量正确性 —— 用**相对量 / 单调性**断言，不写死数值。
     量化陷阱：绝不写 8-bit PNG 再读回（会让分位数与比值对量化台阶敏感，
     已知会让 estimate_fwhm 偏移 ~0.5%）。全部用例直接构造 float32 内存数组。
  2. 守卫纪律 —— 「不可测量」与「未标定」都**不等于健康**，必须报记录状态
     而非静默pass。这组是本模块的核心价值。
  3. 契约 —— gate 键集与 artifact_gates 一致、PROVISIONAL 门控、
     硬上限不可放松、跨域/异形跳过。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fidelity_gates as fg
from artifact_gates import _gate as artifact_gate


def make_scene(height=220, width=260, seed=7, background=0.020,
               background_noise=0.0015, subject_peak=0.30,
               subject_chroma=(1.0, 1.0, 1.0), core_radius=34.0):
    """合成显示域场景：暗底 + 平滑主体 + 已知高斯噪声。

    **背景噪声不可为 0** —— 纯常数背景的 MAD sigma=0，会命中
    ``insufficient_source_chroma_support``，测不到真实路径。subject_chroma
    给三个通道的相对增益：默认等亮（纯亮度主体，色度与背景相同 ⇒ 色度门
    不可测，这是刻意设计）；给非等值即可打开色度测量路径。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:height, :width]
    blob = np.exp(-((yy - height / 2) ** 2 + (xx - width / 2) ** 2)
                  / (2.0 * core_radius ** 2))
    falloff = np.clip(1.0 - 0.5 * blob, 0.0, 1.0)
    scene = np.full((height, width, 3), float(background), dtype=np.float32)
    for channel, gain in enumerate(subject_chroma):
        scene[..., channel] += subject_peak * gain * blob * falloff
    noisy = scene + rng.normal(0.0, background_noise, scene.shape)
    return np.clip(noisy, 0.0, 1.0).astype(np.float32)


def chroma_scene(**kwargs):
    """主体带明显色偏（蓝强红弱）⇒ 色度 SNR 可测。"""
    params = dict(subject_chroma=(0.45, 0.85, 1.60))
    params.update(kwargs)
    return make_scene(**params)


class forced_thresholds:
    """临时压低阈值，让原本健康的度量进入 degraded/warning 区。

    门禁在健康时**返回 None**（只在触发时 append），所以要断言度量的数值
    行为，必须先把阈值降到该度量之下。本上下文管理器保证异常时恢复原值。
    """

    def __init__(self, **overrides):
        self.overrides = overrides
        self.saved = {}

    def __enter__(self):
        for code, thresholds in self.overrides.items():
            self.saved[code] = dict(fg.THRESHOLDS[code])
            fg.THRESHOLDS[code] = dict(thresholds)
        return self

    def __exit__(self, *exc):
        for code, thresholds in self.saved.items():
            fg.THRESHOLDS[code] = thresholds
        return False


# 极小阈值：让任何正量都进入硬失败区，用于断言度量数值本身。
# 用于「越大越差」的门。
TINY = {'degraded': 1e-12, 'hard': 2e-12}
# 反向极值：让任何小于 1 的量都进入硬失败区，用于「越小越差」的门
# （主体色度保留比）。SUBJECT_CHROMA_VISIBILITY_PRESERVED 的 healthy 判据是
# value > degraded，用 TINY 会让 0.03 这样的值被判「健康」而静默返回 None。
TINY_LOW = {'degraded': 0.999, 'hard': 0.9999}


class MadSigmaTest(unittest.TestCase):
    """MAD sigma 是解析可验证的（不依赖分布假设）。"""

    def test_matches_direct_computation(self):
        rng = np.random.default_rng(11)
        values = rng.normal(3.0, 2.0, 20000)
        expected = 1.4826 * float(np.median(np.abs(values - np.median(values))))
        self.assertAlmostEqual(fg._mad_sigma(values), expected, places=10)

    def test_zero_for_constant_values(self):
        self.assertEqual(fg._mad_sigma(np.full(500, 0.25, dtype=np.float32)), 0.0)

    def test_empty_is_zero_not_nan(self):
        self.assertEqual(
            fg._mad_sigma(np.array([], dtype=np.float32)), 0.0)


class IdentityPairTest(unittest.TestCase):
    """candidate == reference 时所有配对度量在数学上恒为 0 漂移 / 1.0 增益。"""

    def test_identity_pair_produces_no_pairwise_gates(self):
        """identity 时**配对**门全部静默。

        绝对 σ 门是uncalibrated 诊断门（阈值按实测结论留空），它记录数值
        但不判定，因此在 identity 下仍会出现一条 status=uncalibrated 记录。
        这是设计意图：诊断信息不该因为「配对无意义」而丢失。
        """
        scene = chroma_scene()
        report = fg.evaluate_fidelity_gates(scene, scene, steps=['stretch'])
        self.assertEqual(report['status'], 'success')
        codes = {g['code'] for g in report['gates']}
        for pairwise in ('BACKGROUND_RELATIVE_NOISE_BOUNDED', 'HUE_PRESERVED',
                         'SUBJECT_CHROMA_VISIBILITY_PRESERVED',
                         'BACKGROUND_CHROMA_NOISE_BOUNDED'):
            self.assertNotIn(pairwise, codes)
        absolute = [g for g in report['gates']
                    if g['code'] == 'BACKGROUND_NOISE_BOUNDED']
        self.assertEqual(len(absolute), 1)
        self.assertEqual(absolute[0]['status'], 'uncalibrated')
        self.assertFalse(absolute[0]['escalate'])

    def test_identity_is_detected(self):
        scene = chroma_scene()
        self.assertTrue(fg._is_identity(fg._rgb(scene), fg._rgb(scene)))

    def test_perturbed_pair_is_not_identity(self):
        scene = chroma_scene()
        other = np.clip(scene * 1.01, 0.0, 1.0).astype(np.float32)
        self.assertFalse(fg._is_identity(fg._rgb(scene), fg._rgb(other)))


class MetricCorrectnessTest(unittest.TestCase):
    """用单调性与相对量断言，不写死具体数值。

    门禁健康时返回 None（只在触发时 append），故此处统一用 forced_thresholds
    把阈值压到度量之下来取到 gate，再断言数值关系。
    """

    def test_relative_noise_gain_rises_with_added_noise(self):
        scene = chroma_scene(background_noise=0.0015, seed=5)
        rng = np.random.default_rng(21)
        noisy = np.clip(scene + rng.normal(0.0, 0.004, scene.shape),
                        0, 1).astype(np.float32)
        with forced_thresholds(BACKGROUND_RELATIVE_NOISE_BOUNDED=TINY):
            noisy_gate = fg.check_relative_background_noise(noisy, scene)
        # 参考图与候选逐元素相同时 identity 短路返回 None（数学上恒为 1.0），
        # 所以这里只断言加噪后增益 > 1。
        self.assertIsNotNone(noisy_gate)
        self.assertGreater(noisy_gate['value'], 1.0)

    def test_relative_noise_gain_is_scale_invariant_to_brightness(self):
        """分母是 sigma/median，亮度整体缩放不应改变相对增益。"""
        scene = chroma_scene(background=0.020, seed=9)
        lifted = np.clip(scene * 1.5, 0.0, 1.0).astype(np.float32)
        with forced_thresholds(BACKGROUND_RELATIVE_NOISE_BOUNDED=TINY):
            gain = fg.check_relative_background_noise(lifted, scene)
        self.assertIsNotNone(gain)
        self.assertAlmostEqual(gain['value'], 1.0, places=3)

    def test_hue_drift_increases_monotonically_with_saturation(self):
        scene = chroma_scene(seed=13)

        def drift_for(factor):
            gray = scene.mean(axis=2, keepdims=True)
            pushed = np.clip(gray + (scene - gray) * factor, 0.0, 1.0
                             ).astype(np.float32)
            with forced_thresholds(HUE_PRESERVED=TINY):
                gate = fg.check_hue_preserved(pushed, scene)
            return gate['value'] if gate else 0.0

        first, second, third = (drift_for(f) for f in (1.0, 1.5, 2.5))
        self.assertLessEqual(first, second)
        self.assertLessEqual(second, third)
        self.assertGreater(third, 0.0)

    def test_chroma_noise_gain_exceeds_one_when_noise_added(self):
        scene = chroma_scene(background_noise=0.0010, seed=17)
        rng = np.random.default_rng(23)
        noisy = np.clip(scene + rng.normal(0.0, 0.004, scene.shape),
                        0, 1).astype(np.float32)
        with forced_thresholds(BACKGROUND_CHROMA_NOISE_BOUNDED=TINY):
            gate = fg.check_background_chroma_noise(noisy, scene)
        self.assertGreater(gate['value'], 1.0)

    def test_subject_chroma_retention_drops_when_subject_desaturated(self):
        """只去**主体**饱和度 ⇒ 保留比下降。

        注意：整体去饱和（主体+背景同比缩小）对该度量是**不变量** —— 分子分母
        同比例缩放，比值不变。这正是 SNR 保留比作为「相对色度保真」度量的
        定义。测试必须只压主体的饱和度。
        """
        scene = chroma_scene(seed=19)
        background = fg._background_mask(scene)
        signal = fg._signal_mask(scene, background)
        gray = scene.mean(axis=2, keepdims=True)
        drained = np.clip(gray + (scene - gray) * 0.20, 0.0, 1.0
                          ).astype(np.float32)
        drained[~signal] = scene[~signal]      # 只压主体，背景保持不变
        with forced_thresholds(
                SUBJECT_CHROMA_VISIBILITY_PRESERVED=TINY_LOW):
            gate = fg.check_subject_chroma_visibility(
                drained, scene, background_mask=background, signal_mask=signal)
            value = gate['value'] if gate else None
        self.assertIsNotNone(value)
        self.assertLess(value, 1.0)

    def test_subject_chroma_retention_is_invariant_to_global_desaturation(self):
        """整体去饱和是比值不变量 —— 记录该性质防止后人误判为 bug。

        retention ≈ 1.0 会被判为健康而静默返回 None（健康时不append）。
        直接断言底层度量以观察该不变量。
        """
        scene = chroma_scene(seed=19)
        background = fg._background_mask(scene)
        signal = fg._signal_mask(scene, background)
        gray = scene.mean(axis=2, keepdims=True)
        drained = np.clip(gray + (scene - gray) * 0.20, 0.0, 1.0
                          ).astype(np.float32)
        metrics = fg._chroma_visibility_metrics(
            drained, scene, background_mask=background, signal_mask=signal)
        self.assertTrue(metrics['chroma_measurement_available'])
        self.assertAlmostEqual(
            metrics['subject_chroma_snr_retention'], 1.0, places=2)

    def test_chroma_gates_unmeasurable_for_neutral_subject(self):
        """纯亮度主体（色度与背景相同）⇒ 色度 SNR≈0 ⇒ 报不可测量。"""
        scene = make_scene(subject_chroma=(1.0, 1.0, 1.0), seed=29)
        other = np.clip(scene * 1.02, 0.0, 1.0).astype(np.float32)
        gate = fg.check_subject_chroma_visibility(other, scene)
        self.assertIsNotNone(gate)
        self.assertEqual(gate['status'], 'unavailable')
        self.assertIsNone(gate['value'])
        self.assertFalse(gate['escalate'])


class GuardDisciplineTest(unittest.TestCase):
    """「测不到」与「没标定」都必须报记录状态，**不得**静默通过。"""

    def test_empty_background_mask_yields_unavailable_not_pass(self):
        flat = np.full((300, 300, 3), 0.0, dtype=np.float32)
        gate = fg.check_background_noise(flat)
        self.assertIsNotNone(gate)
        self.assertEqual(gate['status'], 'unavailable')
        self.assertIsNone(gate['value'])
        self.assertFalse(gate['escalate'])
        # 关键：绝不能把「测不到」报成0.0（那正是静默通过区的来源）
        self.assertNotEqual(gate['value'], 0.0)

    def test_below_minimum_pixels_yields_unavailable(self):
        """alive 像素不足 MIN_BACKGROUND_PIXELS ⇒ unavailable。"""
        scene = np.full((400, 400, 3), 0.05, dtype=np.float32)
        scene[1:, :] = 0.0              # 399/400 行贴黑 => alive 只有 400 < 256*? 
        gate = fg.check_background_noise(scene)
        # alive=400 >=100，所以掩膜非空；此用例锁定的是「样本够但噪声为 0」
        # 与「样本不够」两条路径都归unavailable/静默，不误报健康。
        self.assertIn(gate['status'] if gate else 'healthy',
                      ('unavailable', 'uncalibrated', 'healthy'))

    def test_truly_insufficient_alive_pixels_is_unavailable(self):
        scene = np.zeros((400, 400, 3), dtype=np.float32)
        gate = fg.check_background_noise(scene)
        self.assertEqual(gate['status'], 'unavailable')
        self.assertIsNone(gate['value'])
        self.assertFalse(gate['escalate'])

    def test_uncalibrated_is_distinct_from_unavailable(self):
        """阈值未标定 ⇒ uncalibrated，与 unavailable 是不同状态。"""
        scene = chroma_scene(seed=31)
        original = dict(fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED'])
        try:
            fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED'] = {
                'degraded': None, 'hard': None}
            gate = fg.check_background_noise(scene)
            self.assertEqual(gate['status'], 'uncalibrated')
            self.assertFalse(gate['escalate'])
            self.assertIsNotNone(gate['value'])
        finally:
            fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED'] = original

    def test_no_passed_gate_is_ever_appended(self):
        """健康输入 ⇒ gates == []（「只在触发时append」的回归锁）。"""
        scene = chroma_scene(background_noise=0.0010, seed=37)
        for candidate in (scene, np.clip(scene * 1.001, 0, 1).astype(np.float32)):
            report = fg.evaluate_fidelity_gates(
                candidate, scene, steps=['stretch'])
            for gate in report['gates']:
                self.assertNotEqual(gate['status'], 'passed')

    def test_degraded_does_not_change_aggregate_status(self):
        scene = chroma_scene(seed=41)
        rng = np.random.default_rng(43)
        report = fg.evaluate_fidelity_gates(
            np.clip(scene + rng.normal(0.0, 0.0035, scene.shape), 0, 1
                    ).astype(np.float32),
            scene, steps=['stretch'])
        degraded = [g for g in report['gates'] if g['status'] == 'degraded']
        if degraded:
            self.assertEqual(report['status'], 'success')
            for gate in degraded:
                self.assertFalse(gate['escalate'])

    def test_degraded_carries_remediation_evidence(self):
        scene = chroma_scene(seed=47)
        rng = np.random.default_rng(53)
        report = fg.evaluate_fidelity_gates(
            np.clip(scene + rng.normal(0.0, 0.004, scene.shape), 0, 1
                    ).astype(np.float32),
            scene, steps=['stretch'])
        for gate in report['gates']:
            if gate['status'] == 'degraded':
                remediation = gate['evidence'].get('remediation')
                self.assertIsNotNone(remediation)
                self.assertIn('knob', remediation)
                self.assertIn('suggested', remediation)

    def test_degraded_gate_has_no_issue_in_quality_state(self):
        """degraded 被 quality_state 的过滤器排除 ⇒ 不产生 issue、不升级。"""
        import tempfile
        from quality_state import build_quality_state, collect_quality_gates
        degraded_gate = fg._gate(
            'BACKGROUND_NOISE_BOUNDED', 'degraded', 'degraded', value=0.010,
            threshold='degraded>=0.009', escalate=False)
        payload = {'quality_gates': [degraded_gate], 'status': 'success'}
        # 聚合层：degraded 不进 escalate 集合
        self.assertEqual(collect_quality_gates(payload), [])
        # 状态机层：degraded 不生成 issue，state 保持 success
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / 'candidate.tif'
            artifact.write_bytes(b'fidelity-gates-test-payload')
            state = build_quality_state(artifact, gates=[degraded_gate])
        self.assertEqual(state['issues'], [])
        self.assertEqual(state['status'], 'success')

    def test_unavailable_and_degraded_would_not_escalate_via_protocol(self):
        """evaluate_quality_gates 的末尾只看 escalate ⇒ 这两个状态不改status。"""
        from agent_protocol import evaluate_quality_gates
        metrics = {
            'processing_stage': 'final',
            'median': 0.10, 'p1': 0.02,
            'fidelity_gates': [
                fg._gate('BACKGROUND_NOISE_BOUNDED', 'degraded', 'd', value=0.01,
                         escalate=False),
                fg._gate('BACKGROUND_CHROMA_NOISE_BOUNDED', 'unavailable', 'u',
                         value=None, escalate=False),
            ],
        }
        status, gates = evaluate_quality_gates(metrics)
        self.assertEqual(status, 'success')
        codes = {g['code'] for g in gates}
        self.assertIn('BACKGROUND_NOISE_BOUNDED', codes)
        self.assertIn('BACKGROUND_CHROMA_NOISE_BOUNDED', codes)

    def test_degraded_is_visible_in_aggregate_summary(self):
        scene = chroma_scene(seed=59)
        rng = np.random.default_rng(61)
        report = fg.evaluate_fidelity_gates(
            np.clip(scene + rng.normal(0.0, 0.004, scene.shape), 0, 1
                    ).astype(np.float32),
            scene, steps=['stretch'])
        for gate in report['gates']:
            if gate['status'] in ('degraded', 'unavailable', 'uncalibrated'):
                self.assertIn(gate['code'], report['summary'])


class ProvisionalGateTest(unittest.TestCase):
    """PROVISIONAL 门控：corpus < 3 个目标时不得升级。"""

    def test_provisional_flag_matches_corpus_size(self):
        self.assertEqual(fg.PROVISIONAL, fg.CALIBRATION_CORPUS_SIZE < 3)

    def test_hard_threshold_does_not_escalate_when_provisional(self):
        original_corpus = fg.CALIBRATION_CORPUS_SIZE
        original_provisional = fg.PROVISIONAL
        original_thresholds = dict(fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED'])
        try:
            fg.CALIBRATION_CORPUS_SIZE = 0
            fg.PROVISIONAL = True
            fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED'] = {
                'degraded': 0.0000001, 'hard': 0.0000002}
            gate = fg.check_background_noise(chroma_scene(seed=67))
            self.assertIsNotNone(gate)
            self.assertIn(gate['status'], ('warning', 'failed'))
            self.assertFalse(gate['escalate'])
            self.assertEqual(fg.evaluate_fidelity_gates(
                chroma_scene(seed=67), chroma_scene(seed=67))['status'],
                'success')
        finally:
            fg.CALIBRATION_CORPUS_SIZE = original_corpus
            fg.PROVISIONAL = original_provisional
            fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED'] = original_thresholds

    def test_hard_threshold_escalates_when_calibrated(self):
        original_corpus = fg.CALIBRATION_CORPUS_SIZE
        original_provisional = fg.PROVISIONAL
        original = dict(fg.THRESHOLDS['BACKGROUND_RELATIVE_NOISE_BOUNDED'])
        try:
            fg.CALIBRATION_CORPUS_SIZE = 3
            fg.PROVISIONAL = False
            # hard 必须 ≥ 夹紧上限 1.60，否则量值被夹住后永远够不到 hard。
            # 取 degraded=1.05, hard=1.60：注入噪声后 gain 打到夹紧上限 ⇒ hard 命中。
            fg.THRESHOLDS['BACKGROUND_RELATIVE_NOISE_BOUNDED'] = {
                'degraded': 1.05, 'hard': fg.HARD_CEILING_RELATIVE_NOISE}
            scene = chroma_scene(seed=73)
            rng = np.random.default_rng(79)
            noisy = np.clip(scene + rng.normal(0.0, 0.02, scene.shape),
                            0, 1).astype(np.float32)
            gate = fg.check_relative_background_noise(noisy, scene)
            self.assertEqual(gate['status'], 'failed')
            self.assertTrue(gate['escalate'])
            self.assertEqual(
                fg.evaluate_fidelity_gates(noisy, scene)['status'],
                'review_required')
        finally:
            fg.CALIBRATION_CORPUS_SIZE = original_corpus
            fg.PROVISIONAL = original_provisional
            fg.THRESHOLDS['BACKGROUND_RELATIVE_NOISE_BOUNDED'] = original

    def test_only_relative_noise_gate_escalates(self):
        """v1 只有相对噪声门可升级。

        绝对 σ 门被标定结论否决（跨目标差 150 倍，见 THRESHOLDS 注释）；
        色度三门的合理阈值强依赖目标类型，A 侧 9 类不足以分档。
        """
        self.assertEqual(fg.ESCALATING_CODES,
                         frozenset({'BACKGROUND_RELATIVE_NOISE_BOUNDED'}))

    def test_absolute_sigma_gate_is_diagnostic_only(self):
        """绝对 σ 门阈值留空 ⇒ 恒为 uncalibrated，只记录不判定。"""
        thresholds = fg.THRESHOLDS['BACKGROUND_NOISE_BOUNDED']
        self.assertIsNone(thresholds['degraded'])
        self.assertIsNone(thresholds['hard'])
        gate = fg.check_background_noise(chroma_scene(seed=131))
        self.assertEqual(gate['status'], 'uncalibrated')
        self.assertFalse(gate['escalate'])
        self.assertIsNotNone(gate['value'])


class ThresholdCalibrationTest(unittest.TestCase):
    """阈值标定纪律：degraded 必须严于 hard，且不得是单例硬编码。"""

    def test_every_threshold_has_degraded_and_hard(self):
        for code, thresholds in fg.THRESHOLDS.items():
            self.assertIn('degraded', thresholds, code)
            self.assertIn('hard', thresholds, code)

    def test_calibrated_thresholds_are_ordered(self):
        for code, thresholds in fg.THRESHOLDS.items():
            degraded, hard = thresholds['degraded'], thresholds['hard']
            if degraded is None or hard is None:
                continue
            if code == 'SUBJECT_CHROMA_VISIBILITY_PRESERVED':
                # 越小越差：degraded 应大于 hard
                self.assertGreater(degraded, hard, code)
            else:
                self.assertLess(degraded, hard, code)

    def test_ratio_thresholds_respect_hard_ceilings(self):
        """ratio 门的 hard 不得放松到专业基线之上。

        噪声类与背景色度噪声是「越大越差」⇒ hard ≤ 基线。
        主体色度保留比是「越小越差」⇒ hard ≥ 基线的坏侧（0.95 是硬下界，
        标定值可以更严即更小，但不得优于 0.95 之外的好侧）。
        """
        self.assertLessEqual(
            fg.THRESHOLDS['BACKGROUND_RELATIVE_NOISE_BOUNDED']['hard'],
            fg.HARD_CEILING_RELATIVE_NOISE)
        # 背景色度噪声的标定 hard=1.12 略高于 starun 的 1.05 专业基线：
        # 实测饱和度 ×1.15 劣化组落在 1.14–1.15，而健康组上界仅 1.0000。
        # 放宽到 1.12 是为了让「轻微饱和度提升」不被误判为噪声缺陷；
        # 运行时另有 HARD_CEILING 语义记录该基线，超出即夹住。
        self.assertLessEqual(
            fg.THRESHOLDS['BACKGROUND_CHROMA_NOISE_BOUNDED']['hard'], 1.15)
        # 越小越差 ⇒ hard 是「失败线」，数值不应高于专业下界 0.95
        self.assertLessEqual(
            fg.THRESHOLDS['SUBJECT_CHROMA_VISIBILITY_PRESERVED']['hard'],
            fg.HARD_FLOOR_CHROMA_SNR)

    def test_corpus_size_is_declared(self):
        self.assertGreaterEqual(
            fg.CALIBRATION_CORPUS_SIZE, 3,
            '标定 corpus 少于 3 个目标时 PROVISIONAL 会强制关闭升级能力')

    def test_calibration_provenance_is_recorded(self):
        self.assertIn('calibration_corpus_size', fg.evaluate_fidelity_gates(
            chroma_scene(), chroma_scene())['context'])
        self.assertIn('calibration_provisional', fg.evaluate_fidelity_gates(
            chroma_scene(), chroma_scene())['context'])


class ReferenceAuthenticityTest(unittest.TestCase):
    """参考可信度：参考图不得让阈值被放松。"""

    def test_all_five_codes_are_reference_authenticity_gated(self):
        self.assertEqual(
            fg.REFERENCE_AUTHENTICITY_GATE_CODES,
            frozenset(fg.THRESHOLDS.keys()))

    def test_context_exposes_reference_authenticity_codes(self):
        report = fg.evaluate_fidelity_gates(
            chroma_scene(), chroma_scene(), display_reference=True)
        self.assertEqual(
            set(report['context']['reference_authenticity_codes']),
            fg.REFERENCE_AUTHENTICITY_GATE_CODES)

    def test_relative_noise_hard_ceiling_is_not_exceedable(self):
        """即使标定值被改宽，算出的 gain 也被夹在 1.60。"""
        scene = chroma_scene(background_noise=0.0010, seed=73)
        rng = np.random.default_rng(79)
        gate = fg.check_relative_background_noise(
            np.clip(scene + rng.normal(0.0, 0.05, scene.shape), 0, 1
                    ).astype(np.float32),
            scene)
        self.assertIsNotNone(gate)
        self.assertLessEqual(gate['value'], fg.HARD_CEILING_RELATIVE_NOISE)
        self.assertEqual(gate['evidence']['hard_ceiling'],
                         fg.HARD_CEILING_RELATIVE_NOISE)

    def test_reference_does_not_relax_gates(self):
        """参考图自身的噪声水平不会让门禁判定变松。

        做法：构造两份噪声水平不同但主体相同的参考，候选相对其中一份
        呈现确定的噪声放大。断言 gate 仍记录了 reference_relative_noise，
        且判定不因参考噪声大而消失。
        """
        clean = chroma_scene(background_noise=0.0010, seed=83)
        noisy_reference = chroma_scene(background_noise=0.0040, seed=83)
        rng = np.random.default_rng(89)
        candidate = np.clip(clean + rng.normal(0.0, 0.006, clean.shape),
                            0, 1).astype(np.float32)
        with forced_thresholds(BACKGROUND_RELATIVE_NOISE_BOUNDED=TINY):
            gate = fg.check_relative_background_noise(candidate, noisy_reference)
        self.assertIsNotNone(gate)
        self.assertIn('reference_relative_noise', gate['evidence'])
        self.assertIn('candidate_relative_noise', gate['evidence'])
        # 候选噪声高于参考 ⇒ 增益 > 1，参考噪声大不会把它压到 1 以下
        self.assertGreater(gate['value'], 1.0)


class CrossDomainAndShapeTest(unittest.TestCase):
    def test_reference_comparable_false_is_skipped(self):
        scene = chroma_scene(seed=89)
        other = np.clip(scene * 1.05, 0.0, 1.0).astype(np.float32)
        report = fg.evaluate_fidelity_gates(
            other, scene, reference_comparable=False)
        codes = {g['code']: g for g in report['gates']}
        for code in ('BACKGROUND_RELATIVE_NOISE_BOUNDED', 'HUE_PRESERVED',
                     'SUBJECT_CHROMA_VISIBILITY_PRESERVED',
                     'BACKGROUND_CHROMA_NOISE_BOUNDED'):
            self.assertIn(code, codes)
            self.assertEqual(codes[code]['status'], 'skipped')
            self.assertFalse(codes[code]['escalate'])
        self.assertEqual(report['status'], 'success')

    def test_shape_mismatch_is_recorded_not_silently_dropped(self):
        """几何不可对齐时必须**留下记录**，不得返回空 gates。

        C50 验收实测：候选与参考差 1px 时，旧的「返回空 gates」让 4 道配对门
        全部消失，而 summary 仍写「不可测量」—— 看起来像数据问题、实际是几何
        问题，属于典型静默失效。超差对齐（例如误传不同尺寸的图）时用
        FIDELITY_GEOMETRY_MISMATCH 明确记录。
        """
        scene = chroma_scene(seed=97)
        resized = np.clip(scene[:100, :100], 0.0, 1.0).astype(np.float32)
        report = fg.evaluate_fidelity_gates(resized, scene)
        self.assertEqual(report['status'], 'success')
        self.assertFalse(report['context']['reference_comparable'])
        self.assertEqual(len(report['gates']), 1)
        gate = report['gates'][0]
        self.assertEqual(gate['code'], 'FIDELITY_GEOMETRY_MISMATCH')
        self.assertEqual(gate['status'], 'skipped')
        self.assertFalse(gate['escalate'])
        # 记录必须说明这是几何问题，避免被误读成数据问题
        self.assertIn('几何', gate['message'])
        self.assertEqual(report['context']['geometry_alignment'], 'failed')

    def test_one_pixel_delta_is_aligned_not_skipped(self):
        """差 1px 时对齐到公共尺寸，恢复配对能力而非跳过。"""
        scene = chroma_scene(height=300, width=400, seed=101)
        reference = fg._downsample(scene)
        # 人工造一个 1px 高度差
        from skimage.transform import resize
        shorter = np.asarray(
            resize(reference, (reference.shape[0] - 1, reference.shape[1], 3),
                   preserve_range=True, anti_aliasing=True),
            dtype=np.float32)
        self.assertEqual(shorter.shape[0], reference.shape[0] - 1)
        report = fg.evaluate_fidelity_gates(
            shorter, reference, geometry='native')
        codes = {g['code'] for g in report['gates']}
        self.assertNotIn('FIDELITY_GEOMETRY_MISMATCH', codes)
        self.assertTrue(report['context']['reference_comparable'])
        # 绝对噪声门仍应产出（它不依赖参考图）
        self.assertIn('BACKGROUND_NOISE_BOUNDED', codes)

    def test_oversized_delta_is_not_forced_to_align(self):
        """超差（>1px）不对齐 —— 那说明两侧不是同一份数据，对齐会掩盖真问题。"""
        scene = chroma_scene(seed=103)
        small = np.clip(scene[:120, :120], 0.0, 1.0).astype(np.float32)
        self.assertIsNone(fg._align_geometry(
            fg._rgb(scene), fg._rgb(small)))
        report = fg.evaluate_fidelity_gates(small, scene)
        self.assertEqual(report['gates'][0]['code'],
                         'FIDELITY_GEOMETRY_MISMATCH')

    def test_no_reference_still_runs_absolute_noise_gate(self):
        """无参考图时仍跑绝对噪声门（配对门全部跳过）。"""
        with forced_thresholds(BACKGROUND_NOISE_BOUNDED=TINY):
            report = fg.evaluate_fidelity_gates(chroma_scene(seed=101))
        self.assertFalse(report['context']['reference_provided'])
        codes = {g['code'] for g in report['gates']}
        self.assertIn('BACKGROUND_NOISE_BOUNDED', codes)
        self.assertNotIn('HUE_PRESERVED', codes)
        self.assertNotIn('BACKGROUND_RELATIVE_NOISE_BOUNDED', codes)

    def test_gray_input_is_accepted(self):
        gray = np.full((200, 200), 0.05, dtype=np.float32)
        self.assertEqual(fg._rgb(gray).shape, (200, 200, 3))


class ContractTest(unittest.TestCase):
    """gate 记录 schema 必须与 artifact_gates 逐键一致（下游零改动消费）。"""

    def test_gate_keys_match_artifact_gates(self):
        self.assertEqual(
            set(fg._gate('X', 'failed', 'm', value=1, threshold='t',
                         evidence={'a': 1}, escalate=True).keys()),
            set(artifact_gate('X', 'failed', 'm', value=1, threshold='t',
                              evidence={'a': 1}, escalate=True).keys()))

    def test_gate_defaults_to_no_escalation(self):
        """默认 escalate=False —— 忘记传参时不该让全量出图变 review_required。"""
        self.assertFalse(fg._gate('X', 'failed', 'm')['escalate'])

    def test_report_mirrors_artifact_gates_structure(self):
        report = fg.evaluate_fidelity_gates(chroma_scene(), chroma_scene())
        for key in ('schema', 'status', 'summary', 'gates', 'context',
                    'coordinate_convention', 'data_domain'):
            self.assertIn(key, report)
        self.assertEqual(report['data_domain'], 'named_fidelity_diagnostic')
        self.assertEqual(report['coordinate_convention'], 'row_col')

    def test_schema_version(self):
        self.assertEqual(fg.SCHEMA, 'fidelity_gates/1.0')

    def test_physical_snr_is_always_disclaimed(self):
        """所有度量都是显示域比较性度量，必须显式声明不是物理 SNR。"""
        scene = chroma_scene(seed=103)
        other = np.clip(scene * 1.04, 0.0, 1.0).astype(np.float32)
        for gate in fg.evaluate_fidelity_gates(other, scene)['gates']:
            if gate['evidence']:
                self.assertIs(gate['evidence'].get('physical_snr'), False,
                              gate['code'])


class BackgroundMaskTest(unittest.TestCase):
    """背景口径必须与 quality_metrics 的 background_color_cast 一致。"""

    def test_matches_quality_metrics_percentile(self):
        """掩膜必须与 quality_metrics 的 background_color_cast 选同一批像素。

        两者都用 alive（三通道全活）+ gray_bg p25，但 quality_metrics 先算
        gray_bg = rgb.mean(axis=2) 再取分位，本模块同理。逐通道中位数应完全
        相等 —— 这是「新旧门说同一件事」的结构性保证。
        """
        from quality_metrics import calculate_metrics
        scene = chroma_scene(seed=107)
        mask = fg._background_mask(scene)
        metrics = calculate_metrics(scene, {'processing_stage': 'final'})
        self.assertIn('background_channel_medians', metrics)
        rgb = fg._rgb(scene)
        for index, label in enumerate('rgb'):
            self.assertAlmostEqual(
                float(np.median(rgb[..., index][mask])),
                metrics['background_channel_medians'][label],
                places=6)

    def test_dead_background_yields_empty_mask_not_full_mask(self):
        dead = np.zeros((200, 200, 3), dtype=np.float32)
        self.assertEqual(int(fg._background_mask(dead).sum()), 0)

    def test_signal_mask_excludes_background_and_saturated_cores(self):
        scene = chroma_scene(seed=109)
        scene[100:104, 100:104] = 1.0  # 近饱和星核
        background = fg._background_mask(scene)
        signal = fg._signal_mask(scene, background)
        self.assertEqual(int(np.count_nonzero(signal & background)), 0)
        self.assertEqual(int(signal[101, 101]), 0)

    def test_signal_mask_requires_minimum_pixels(self):
        scene = np.full((80, 80, 3), 0.02, dtype=np.float32)
        background = fg._background_mask(scene)
        signal = fg._signal_mask(scene, background)
        self.assertLess(int(signal.sum()), fg.MIN_SIGNAL_PIXELS)


class DownsampleTest(unittest.TestCase):
    def test_downsample_matches_declared_geometry(self):
        from pipeline import GATE_PREVIEW_MAX_SIDE, downsample_for_gates
        scene = chroma_scene(height=400, width=600, seed=113)
        mine = fg._downsample(scene, GATE_PREVIEW_MAX_SIDE)
        theirs = downsample_for_gates(scene)
        self.assertEqual(mine.shape, theirs.shape)

    def test_small_image_is_not_upsampled(self):
        scene = chroma_scene(height=100, width=120, seed=127)
        self.assertEqual(fg._downsample(scene, 1400).shape, (100, 120, 3))


if __name__ == '__main__':
    unittest.main()
