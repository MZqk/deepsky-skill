"""DeepSNR float FITS bridge, quality rejection and pipeline fallback checks."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import pipeline
from denoise import (
    find_deepsnr_executable,
    run_deepsnr,
    validate_external_denoise,
)
from fits_io import read_image, write_image
from agent_workflow import initialize_session, apply_action, load_state


def scene(size=512):
    y, x = np.mgrid[:size, :size]
    image = 0.03 + 0.06 * np.exp(-((x-size/2)**2 + (y-size/2)**2) / (size/8)**2)
    image += 0.3 * np.exp(-((x-size/3)**2 + (y-size/3)**2) / 8)
    return image.astype(np.float32)


class DeepSNRTests(unittest.TestCase):
    def fake_cli(self, command, **kwargs):
        self.commands.append(command)
        if '--machine-info' in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({
                'product': 'deepsnr', 'version': self.version, 'backend': {'id': 'coreml'},
                'features': {'linear_mtf': {'supported': self.version == '1.3.2',
                                          'restore_clipped_source_samples': False}},
                'io': {'images': {direction: [{
                    'formats': ['fits'], 'sample_types': ['float32'],
                    'color_spaces': ['rgb', 'grayscale'],
                }] for direction in ('inputs', 'outputs')}},
            }), '')
        self.assertIn('--model', command)
        self.assertEqual(command[command.index('--model') + 1], '2')
        self.assertNotIn('--linear', command)
        source = command[command.index('--input') + 1]
        output = command[command.index('--output') + 1]
        with fits.open(source) as hdul:
            self.assertEqual(hdul[0].header['BITPIX'], -32)
            self.payload = hdul[0].data.copy()
            data = self.payload
        if self.failure == 'bgr':
            data = data[::-1].copy()
        elif self.failure == 'integer':
            data = (data * 65535).astype(np.uint16)
        elif self.failure == 'nan':
            data.flat[0] = np.nan
        elif self.failure == 'overflow':
            data.flat[0] = 1.5
        elif self.failure == 'layout':
            data = data[..., :-1]
        elif self.failure == 'timeout':
            raise subprocess.TimeoutExpired(command, 1)
        elif self.failure == 'exit':
            return subprocess.CompletedProcess(command, 2, '', 'model unavailable')
        elif self.failure == 'missing':
            return subprocess.CompletedProcess(command, 0, '', '')
        fits.PrimaryHDU(data).writeto(output)
        return subprocess.CompletedProcess(command, 0, '', '')

    def bridge(self, image, failure=None, version='1.2.3'):
        self.failure = failure
        self.version = version
        self.commands = []
        with tempfile.TemporaryDirectory() as tmp, \
                patch('denoise.shutil.which', return_value='/fake/deepsnr'), \
                patch('denoise.subprocess.run', side_effect=self.fake_cli):
            return run_deepsnr(image, tmp)

    def test_float_mtf_roundtrip_rgb_and_grayscale(self):
        for image in (scene(), np.stack([scene()*0.8, scene(), scene()*0.6], axis=-1)):
            result, report = self.bridge(image)
            self.assertEqual(report['status'], 'accepted')
            self.assertEqual(result.shape, image.shape)
            self.assertEqual(result.dtype, np.float32)
            np.testing.assert_allclose(result, image, rtol=2e-5, atol=1e-7)
            self.assertEqual(report['shadows'], 0)
            self.assertGreater(float(self.payload.mean()), float(image.mean()))

    def test_rgb_transport_calibration_corrects_bgr_builds(self):
        image = np.stack([scene()*0.8, scene(), scene()*0.6], axis=-1)
        result, report = self.bridge(image, 'bgr')
        self.assertEqual(report['status'], 'accepted')
        self.assertEqual(report['fits_channel_order'], 'bgr')
        np.testing.assert_allclose(result, image, rtol=2e-5, atol=1e-7)

    def test_prediction_is_saved_separately_from_applied_result(self):
        self.failure, self.version, self.commands = None, '1.3.2', []
        with tempfile.TemporaryDirectory() as tmp, \
                patch('denoise.shutil.which', return_value='/fake/deepsnr'), \
                patch('denoise.subprocess.run', side_effect=self.fake_cli):
            result, report = run_deepsnr(scene(), tmp, save_prediction=True)
            prediction, _ = read_image(report['prediction_path'])
            self.assertEqual(prediction.dtype, np.float32)
            np.testing.assert_allclose(prediction, result, rtol=2e-5, atol=1e-7)
            self.assertTrue(report['validation']['passed'])
            self.assertTrue(report['applied_validation']['passed'])

    def test_132_skips_legacy_probe_and_preserves_zero_shadow_transport(self):
        for image in (scene(), np.stack([scene()*0.8, scene(), scene()*0.6], axis=-1)):
            image.flat[0] = 1e-6
            result, report = self.bridge(image, version='1.3.2')
            self.assertEqual(report['status'], 'accepted')
            self.assertFalse(report['native_linear_used'])
            self.assertEqual(report['shadows'], 0)
            self.assertEqual(len(self.commands), 2)  # Machine info, then one inference.
            if image.ndim == 3:
                self.assertEqual(report['fits_channel_order'], 'rgb')
                self.assertEqual(report['fits_channel_order_source'], 'cli_rgb_contract')
            np.testing.assert_allclose(result, image, rtol=2e-5, atol=1e-7)

    def test_cli_and_output_failures_return_fallback(self):
        for failure in ('integer', 'nan', 'overflow', 'layout', 'timeout', 'exit', 'missing'):
            with self.subTest(failure=failure):
                result, report = self.bridge(scene(), failure)
                self.assertIsNone(result)
                self.assertEqual(report['status'], 'fallback')
                self.assertTrue(report['review_required'])
                self.assertTrue(report['reason'])

    def test_missing_executable_and_invalid_input(self):
        # 探测现在有四级优先：显式 -> DEEPSNR_PATH -> PATH -> 默认安装路径。
        # 要模拟「彻底缺失」，直接 patch 探测函数本身；只 patch shutil.which
        # 已不够 —— 默认路径扫描会绕过它，反而在本机找到真的
        # /usr/local/bin/deepsnr（曾导致本用例误判为「Background noise
        # increased」而不是「not found」）。
        with tempfile.TemporaryDirectory() as tmp, \
                patch('denoise.find_deepsnr_executable', return_value=None):
            result, report = run_deepsnr(scene(), tmp)
            self.assertIsNone(result)
            self.assertIn('not found', report['reason'])
        invalid = scene()
        invalid.flat[0] = -0.01
        result, report = self.bridge(invalid)
        self.assertIsNone(result)
        self.assertIn('normalized', report['reason'])

    def test_explicit_missing_path_does_not_fall_back(self):
        """显式指定不存在的路径必须返回 None，不得静默回退到默认位置。

        否则用户以为自己指定了 A 工具、实际跑的是 B 工具，产物记录里的
        executable 会与预期不符 —— 这类「静默换工具」比直接报错更难查。
        """
        self.assertIsNone(
            find_deepsnr_executable('/nonexistent/deepsnr'))

    def test_probe_priority_chain(self):
        """四级优先链：显式 > DEEPSNR_PATH > PATH > 默认扫描。"""
        with tempfile.TemporaryDirectory() as tmp:
            explicit = os.path.join(tmp, 'deepsnr')
            with open(explicit, 'w') as handle:
                handle.write('#!/bin/sh\n')
            # 探测器要求 POSIX 上 os.access(X_OK) 为真，故临时文件必须可执行；
            # 不可执行的文件被跳过是**正确行为**，测试要配合而非绕过。
            os.chmod(explicit, 0o755)
            # 显式存在的路径直接采用
            self.assertEqual(find_deepsnr_executable(explicit), explicit)
            # 环境变量指向目录
            with patch.dict(os.environ, {'DEEPSNR_PATH': tmp}):
                self.assertEqual(find_deepsnr_executable(), explicit)
            # 环境变量指向文件
            with patch.dict(os.environ, {'DEEPSNR_PATH': explicit}):
                self.assertEqual(find_deepsnr_executable(), explicit)
            # 环境变量无效时回退默认扫描（本机装有 /usr/local/bin/deepsnr）
            with patch.dict(os.environ, {'DEEPSNR_PATH': '/nonexistent'}):
                self.assertIsNotNone(find_deepsnr_executable())

    def test_quality_gate_accepts_noise_removal_without_structure(self):
        rng = np.random.default_rng(4)
        noisy = (0.03 + rng.normal(0, 0.002, (128, 128))).astype(np.float32)
        clean = np.full_like(noisy, 0.03)
        report = validate_external_denoise(noisy, clean)
        self.assertTrue(report['passed'], report)
        self.assertLess(report['high_frequency_energy_ratio'], 0.5)
        self.assertIsNone(report['detail_retention'])

    def test_quality_gate_rejects_drift_noise_growth_and_smoothing(self):
        original = scene(128)
        rng = np.random.default_rng(5)
        for candidate in (original * 1.2, gaussian_filter(original, 12),
                          original + rng.normal(0, 0.01, original.shape).astype(np.float32),
                          np.full_like(original, np.nan), original[:-1]):
            self.assertFalse(validate_external_denoise(original, candidate)['passed'])

    def test_pipeline_accepts_current_stage_and_suppresses_final_denoise(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'source.tif', Path(tmp)/'output.tif'
            write_image(np.stack([scene(64)]*3, axis=-1), str(source))
            def corrected(image, **kwargs):
                return image * 0.8, {}
            def accepted(image, *args, **kwargs):
                np.testing.assert_allclose(image, scene_rgb * 0.8, rtol=1e-5)
                return image, {'status': 'accepted', 'backend': 'deepsnr', 'review_required': False}
            scene_rgb = np.stack([scene(64)]*3, axis=-1)
            with patch('pipeline.auto_color_calibrate', side_effect=corrected), \
                    patch('pipeline.run_deepsnr', side_effect=accepted), \
                    patch('pipeline.denoise_luminance_chroma', side_effect=AssertionError('double denoise')):
                result = pipeline.run_pipeline(str(source), str(output),
                    steps='color,pre_denoise,final_denoise', deepsnr='always',
                    input_is_linear=True, background_neutralize_lock=False,
                    target_type='galaxy', target_name='M81')
            self.assertEqual(result['pre_denoise']['status'], 'accepted')
            self.assertTrue(result['effective_config']['skip_final_denoise'])

    def test_pipeline_fallback_is_starlet_and_review_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp)/'source.tif', Path(tmp)/'output.tif'
            write_image(np.stack([scene(64)]*3, axis=-1), str(source))
            failure = {'status': 'fallback', 'backend': 'deepsnr',
                       'review_required': True, 'reason': 'timeout'}
            with patch('pipeline.run_deepsnr', return_value=(None, failure)), \
                    patch('pipeline.denoise_luminance_chroma', side_effect=lambda x, **k: x) as fallback:
                result = pipeline.run_pipeline(str(source), str(output), steps='pre_denoise',
                    deepsnr='always', input_is_linear=True, target_type='galaxy', target_name='M81',
                    keep_all=True, work_dir=str(Path(tmp)/'artifacts'))
            self.assertEqual(fallback.call_args.kwargs['method'], 'atrous')
            self.assertEqual(result['status'], 'review_required')
            self.assertTrue(Path(result['artifacts']['03_pre_denoise_report.json']).exists())
            self.assertFalse(result['effective_config']['skip_final_denoise'])

    def test_auto_skips_clusters_low_noise_and_nonlinear_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pipeline.run_deepsnr') as cli:
            source, output = Path(tmp)/'source.tif', Path(tmp)/'output.tif'
            write_image(np.stack([scene()]*3, axis=-1), str(source))
            for target, linear, reason in (('open_cluster', True, 'stars_are_subject'),
                    ('galaxy', True, 'low_noise'), ('galaxy', False, 'nonlinear_input')):
                result = pipeline.run_pipeline(str(source), str(output), steps='pre_denoise',
                    target_type=target, target_name='test', input_is_linear=linear)
                self.assertEqual(result['pre_denoise']['reason'], reason)
            cli.assert_not_called()

    def test_external_result_takes_precedence_and_is_validated(self):
        with tempfile.TemporaryDirectory() as tmp, patch('pipeline.run_deepsnr') as cli:
            source, external, output = (Path(tmp)/name for name in ('source.tif', 'external.tif', 'out.tif'))
            image = np.stack([scene(64)]*3, axis=-1)
            write_image(image, str(source))
            for candidate, status in ((image, 'accepted'), (image*1.2, 'fallback')):
                write_image(candidate, str(external))
                result = pipeline.run_pipeline(str(source), str(output), steps='pre_denoise',
                    external_denoised=str(external), deepsnr='always')
                self.assertEqual(result['pre_denoise']['status'], status)
            cli.assert_not_called()

    def test_linear_stage_preserves_samples_and_uses_linear_review_gates(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / 'linear.tif'
            output = Path(td) / 'output.tif'
            image = np.full((64, 64, 3), [.002, .001, .0008], dtype=np.float32)
            write_image(image, source)
            with patch('pipeline.denoise_luminance_chroma', side_effect=lambda x, **k: x), \
                 patch('pipeline.lock_background_neutrality', side_effect=AssertionError('display lock on linear data')):
                result = pipeline.run_pipeline(str(source), str(output), steps='pre_denoise',
                                      input_is_linear=True, deepsnr='never')
            actual, _ = read_image(str(output))
            np.testing.assert_array_equal(actual, image)
            self.assertEqual(result['metrics']['processing_stage'], 'linear')
            self.assertIsNone(result['background_neutralization'])
            from agent_protocol import create_review_bundle
            bundle = create_review_bundle(source, output, Path(td) / 'review',
                                          context={'input_is_linear': True, 'output_is_linear': True})
            self.assertEqual(bundle['metrics_after']['processing_stage'], 'linear')
            codes = {g['code'] for g in bundle['quality_gates']}
            self.assertNotIn('BACKGROUND_LOW', codes)
            self.assertNotIn('DENOISE_PLASTICITY', codes)

    def test_agent_preserves_linear_tiff_and_ai_denoise_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, session = Path(tmp)/'input.fits', Path(tmp)/'session'
            write_image(np.stack([scene(64)]*3, axis=-1), str(source))
            initialize_session(str(source), session, target_type='galaxy', target_name='M81')
            apply_action(session, {'operation': 'run_step', 'step': 'color'})
            accepted = {'status': 'accepted', 'backend': 'deepsnr', 'review_required': False}
            with patch('pipeline.run_deepsnr', side_effect=lambda x, *a, **k: (x, accepted)) as cli:
                event = apply_action(session, {'operation': 'run_step', 'step': 'pre_denoise',
                                              'params': {'deepsnr': 'always'}})
                cli.assert_called_once()
            self.assertTrue(event['result']['pipeline_result']['effective_config']['input_is_linear'])
            with patch('pipeline.denoise_luminance_chroma', side_effect=AssertionError('double denoise')):
                apply_action(session, {'operation': 'run_step', 'step': 'final_denoise'})
            state, _ = load_state(session)
            self.assertTrue(state['artifact_state'][state['current_artifact']]['ai_denoised'])


if __name__ == '__main__':
    unittest.main()
