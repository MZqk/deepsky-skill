"""标定母版筛查工具（``screen_calibration_corpus``）的测试。

两条主线：

1. **等价性自检**（``self_check``）—— 「raw sep == post sep」是筛查能
   成立的**唯一前提**。它一旦失效，筛查结果就是**系统性错误**而非随机
   误差，而且**不会报错**，只会把不可测的母版报成可测。
   ⇒ 必须有测试锁住「失效时会被发现」。

2. **``catalog_hit`` 标注** —— 靠兜底猜测的类型去标定是危险的。
   实测C50（疏散星团）被猜成 ``emission_nebula``，于是星团禁令不触发，
   会在「星点即主体」的数据上跑完整去星链。
"""

import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import screen_calibration_corpus as screen


class SeparationTests(unittest.TestCase):
    """``separation`` 必须与 ``star_tools`` 同口径。"""

    def test_matches_star_tools_criterion(self):
        """同输入下，本工具的 sep 必须等于 ``estimate_star_removal_quality``
        报出的 ``subject_separation``。

        口径漂移的后果很隐蔽：筛选说「可测」，实际门禁判「不可测」，
        于是母版白跑8 分钟管线。
        """
        import star_tools as st
        from test_star_damage_calibration import synthetic_nebula
        original, starless = synthetic_nebula()
        mine = screen.separation_of(original)
        theirs = st.estimate_star_removal_quality(original, starless)[
            'subject_separation']
        self.assertAlmostEqual(mine, theirs, places=3)

    def test_flat_field_is_unmeasurable(self):
        """纯暗场 p25≈p75 ⇒ sep≈1，判不可测。"""
        flat = np.full((192, 192, 3), 0.02, dtype=np.float32)
        self.assertLess(screen.separation_of(flat),
                        screen.DEFAULTS.min_subject_separation)

    def test_high_contrast_is_measurable(self):
        """高对比主体 ⇒ sep 明显高于下限。"""
        from test_star_damage_calibration import synthetic_nebula
        original, _ = synthetic_nebula(contrast=1.45, retention=0.7)
        self.assertGreater(screen.separation_of(original), 1.30)


class CatalogStatusTests(unittest.TestCase):
    """类型来源必须可见。"""

    def test_known_target_hits_catalog(self):
        status = screen.catalog_status('M8')
        self.assertTrue(status['catalog_hit'])
        self.assertFalse(status['type_is_guessed'])
        self.assertEqual(status['resolved_type'], 'emission_nebula')

    def test_unknown_target_is_flagged_as_guessed(self):
        """不在星表的目标必须标 ``type_is_guessed``。

        实测 C50（疏散星团）查不到 ⇒ 兜底猜成 ``emission_nebula``。
        不标出来就会拿它当发射星云标定，而管线**不会**触发星团禁令
        （``is_star_poi_target`` 对兜底路径返回 False）。
        """
        status = screen.catalog_status('C50')
        self.assertFalse(status['catalog_hit'])
        self.assertTrue(status['type_is_guessed'])

    def test_star_dominant_is_reported(self):
        status = screen.catalog_status('M45')
        self.assertTrue(status['star_dominant'])


class SelfCheckTests(unittest.TestCase):
    """等价性自检 —— 本工具的生命线。"""

    @staticmethod
    def _make_corpus(root, name, image, post_image, master_path):
        work = root / name / 'work'
        work.mkdir(parents=True, exist_ok=True)
        from fits_io import write_image
        write_image(image, str(work / '04_separation_reference.tif'))
        write_image(post_image, str(work / '04_starless_linear.tif'))
        (root / name / 'result.json').write_text(json.dumps({
            'input': str(master_path),
            'star_removal': {'star_removal_profile': {
                'target_type': 'emission_nebula'}},
        }), encoding='utf-8')

    def test_missing_calib_root_is_not_applicable(self):
        result = screen.self_check('/nonexistent/path/xyz')
        self.assertFalse(result['applicable'])
        self.assertIn('不存在', result['reason'])

    def test_empty_corpus_reports_no_samples(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = screen.self_check(tmp)
            self.assertFalse(result['applicable'])

    def test_corpus_without_starless_is_skipped(self):
        """只有回退副本的目录不能参与自检（v0.1.34 §3.1）。"""
        import tempfile
        from fits_io import write_image
        from test_star_damage_calibration import synthetic_nebula
        original, starless = synthetic_nebula(size=64)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / 'x' / 'work'
            work.mkdir(parents=True)
            write_image(original, str(work / '04_separation_reference.tif'))
            # 不写 04_starless_linear.tif ⇒ 该样本被跳过
            (root / 'x' / 'result.json').write_text(
                json.dumps({'input': 'x'}), encoding='utf-8')
            result = screen.self_check(tmp)
            self.assertFalse(result['applicable'])

    def test_equivalent_pair_passes(self):
        """raw == post ⇒ ``verdict='ok'``。"""
        import tempfile
        from test_star_damage_calibration import synthetic_nebula
        original, starless = synthetic_nebula(size=96)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / 'master.tif'
            from fits_io import write_image
            write_image(original, str(master))
            # post 写成同一张 ⇒ Δ=0
            self._make_corpus(root, 'sample', original, starless, master)
            result = screen.self_check(str(root))
            self.assertEqual(result['verdict'], 'ok')
            self.assertTrue(result['rows'][0]['equivalent'])
            self.assertEqual(result['rows'][0]['delta'], 0.0)

    def test_inequivalent_pair_is_detected(self):
        """raw ≠ post ⇒ 报 ``broken`` 并点名。

        这是本工具最重要的一条测试：等价性失效时**必须**被发现。
        失效时的后果不是「筛错几份」，而是把系统性不可测的母版
        报成可测，且全程无报错。
        """
        import tempfile
        from test_star_damage_calibration import synthetic_nebula
        # raw 用高对比、post 用低对比 ⇒ 两者 sep 必然不同
        raw_img, _ = synthetic_nebula(size=96, contrast=2.5, retention=0.7)
        post_img, _ = synthetic_nebula(size=96, contrast=1.15, retention=0.7)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            master = root / 'master.tif'
            from fits_io import write_image
            write_image(raw_img, str(master))
            self._make_corpus(root, 'sample', post_img, post_img, master)
            result = screen.self_check(str(root))
            self.assertEqual(result['verdict'], 'broken')
            self.assertIn('sample', result['broken_samples'])
            self.assertFalse(result['rows'][0]['equivalent'])


class ScreenRecordTests(unittest.TestCase):
    """单份筛查的输出结构。"""

    def test_unreadable_file_is_reported_not_fatal(self):
        """单份读失败只记 ``read_failed``，不让整轮筛查崩掉。

        实测库里有 1 份 ``SeestarS30Pro-IC434`` 是坏文件
        （``not well-formed: line 6, column 76``），必须能被跳过。
        """
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'SeestarS30Pro-IC434-60s210_IRCUT.tif'
            bad.write_text('not a real xisf\n', encoding='utf-8')
            record = screen.screen_file(bad)
            self.assertTrue(record['read_failed'])
            self.assertIn('error', record)
            self.assertEqual(record['target'], 'IC434')

    def test_target_name_parsed_from_filename(self):
        """目标名取自文件名中的目标段（带数字前缀的仪器名要跳过）。"""
        import tempfile
        from fits_io import write_image
        from test_star_damage_calibration import synthetic_nebula
        original, _ = synthetic_nebula(size=64)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SeestarS30Pro-NGC2237-60s174_IRCUT.tif'
            write_image(original, str(path))
            record = screen.screen_file(path)
            self.assertEqual(record['target'], 'NGC2237')
            self.assertFalse(record['read_failed'])

    def test_record_contains_all_screening_fields(self):
        import tempfile
        from fits_io import write_image
        from test_star_damage_calibration import synthetic_nebula
        original, _ = synthetic_nebula(size=96)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SeestarS30Pro-M8-60s169_IRCUT.tif'
            write_image(original, str(path))
            record = screen.screen_file(path)
            for key in ('separation', 'measurable_ratio', 'verdict',
                        'window_separations', 'catalog', 'megapixels',
                        'measurable_threshold'):
                self.assertIn(key, record)

    def test_window_count_matches_requested(self):
        """子窗数必须真的用上 V4 的默认网格数。"""
        import tempfile
        from fits_io import write_image
        from test_star_damage_calibration import synthetic_nebula
        original, _ = synthetic_nebula(size=192)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'SeestarS30Pro-M8-60s169_IRCUT.tif'
            write_image(original, str(path))
            record = screen.screen_file(path, n_windows=9)
            self.assertEqual(len(record['window_separations']), 9)


if __name__ == '__main__':
    unittest.main()
