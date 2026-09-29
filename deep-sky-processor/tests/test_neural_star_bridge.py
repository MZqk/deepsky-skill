"""Tests for external neural star removal bridge & recommendation engine."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imread

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from neural_star_bridge import (
    detect_neural_starnet_environment,
    evaluate_neural_bridge_recommendation,
    export_starless_payload,
    build_bridge_run_instructions,
    verify_external_starless,
    format_bridge_recommendation_card,
)


class TestNeuralStarBridge(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_starnet_bridge_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_detect_environment(self):
        # 1. 默认检测本机环境
        env = detect_neural_starnet_environment()
        self.assertIsInstance(env, dict)
        self.assertIn("available", env)
        self.assertIn("recommendation_readiness", env)

        # 2. 模拟不存在的路径
        fake_env = detect_neural_starnet_environment(custom_path="/path/to/nonexistent/starnet_fake")
        self.assertFalse(fake_env["available"])
        self.assertEqual(fake_env["recommendation_readiness"], "not_installed")

    def test_cluster_disallowed_redline(self):
        # 球状星团、疏散星团必须严格返回 DISALLOWED
        for ctype in ["globular_cluster", "open_cluster", "star_cluster"]:
            rec = evaluate_neural_bridge_recommendation(
                image_shape=(100, 100, 3),
                star_density="very_dense",
                star_area_ratio=0.25,
                target_type=ctype,
            )
            self.assertFalse(rec["recommended"])
            self.assertEqual(rec["level"], "DISALLOWED")
            self.assertIn("天体物理规则严禁", rec["reason"])

    def test_recommendation_levels(self):
        # 1. 内置去星质量评分较低或星云受损 -> STRONGLY_RECOMMENDED
        rec_poor = evaluate_neural_bridge_recommendation(
            image_shape=(100, 100, 3),
            star_removal_quality={"repair_quality_score": 0.45, "quality": "poor", "nebula_damage_ratio": 0.20},
            target_type="emission_nebula",
        )
        self.assertTrue(rec_poor["recommended"])
        self.assertEqual(rec_poor["level"], "STRONGLY_RECOMMENDED")

        # 2. 极度繁密星场 -> STRONGLY_RECOMMENDED
        rec_dense = evaluate_neural_bridge_recommendation(
            image_shape=(100, 100, 3),
            star_density="very_dense",
            star_area_ratio=0.15,
            target_type="emission_nebula",
        )
        self.assertTrue(rec_dense["recommended"])
        self.assertEqual(rec_dense["level"], "STRONGLY_RECOMMENDED")

        # 3. 稀疏星场且质量良好 -> OPTIONAL
        rec_opt = evaluate_neural_bridge_recommendation(
            image_shape=(100, 100, 3),
            star_density="sparse",
            star_area_ratio=0.02,
            target_type="emission_nebula",
            env_info={"available": False},
        )
        self.assertFalse(rec_opt["recommended"])
        self.assertEqual(rec_opt["level"], "OPTIONAL")

    def test_export_starless_payload(self):
        # 导出 16-bit uint16 RGB TIFF
        img = np.random.uniform(0.0, 1.0, size=(64, 64, 3)).astype(np.float32)
        out_path = os.path.join(self.test_dir, "payload.tif")
        exported = export_starless_payload(img, out_path)

        self.assertTrue(os.path.exists(exported))
        read_back = imread(exported)
        self.assertEqual(read_back.dtype, np.uint16)
        self.assertEqual(read_back.shape, (64, 64, 3))
        self.assertGreater(read_back.max(), 1000)

    def test_export_starless_payload_grayscale_to_rgb(self):
        # 单通道灰度输入也应转为 3 通道 16-bit uint16
        img_gray = np.linspace(0.0, 1.0, 100, dtype=np.float32).reshape(10, 10)
        out_path = os.path.join(self.test_dir, "payload_gray.tif")
        exported = export_starless_payload(img_gray, out_path)

        self.assertTrue(os.path.exists(exported))
        read_back = imread(exported)
        self.assertEqual(read_back.dtype, np.uint16)
        self.assertEqual(read_back.shape, (10, 10, 3))

    def test_build_bridge_run_instructions(self):
        env_info = {
            "available": True,
            "executable": "/usr/local/bin/starnet2",
            "supports_unscreen": True,
        }
        inst = build_bridge_run_instructions(
            payload_path="/tmp/payload.tif",
            output_starless_path="/tmp/starless.tif",
            input_fits_path="/tmp/input.fits",
            final_output_path="/tmp/final.jpg",
            env_info=env_info,
            work_dir="/tmp/work",
        )
        self.assertIn("external_starnet_command", inst)
        self.assertIn("resume_pipeline_command", inst)
        self.assertIn("/usr/local/bin/starnet2", inst["external_starnet_command"])
        self.assertIn("-n", inst["external_starnet_command"])
        self.assertIn("--external-starless", inst["resume_pipeline_command"])

    def test_verify_external_starless(self):
        orig = np.ones((50, 50, 3), dtype=np.float32) * 0.5
        # 1. 形状不一致
        mismatch = np.ones((40, 40, 3), dtype=np.float32)
        res_mismatch = verify_external_starless(orig, mismatch)
        self.assertFalse(res_mismatch["valid"])

        # 2. 正常剥离出星点
        starless = orig.copy()
        starless[10:15, 10:15] = 0.2
        res_ok = verify_external_starless(orig, starless)
        self.assertTrue(res_ok["valid"])
        self.assertTrue(res_ok["stars_extracted_successfully"])
        self.assertGreater(res_ok["stars_energy_mean"], 0.0)

    def test_format_bridge_recommendation_card(self):
        rec = {
            "level": "STRONGLY_RECOMMENDED",
            "recommended": True,
            "reason": "密集星场",
            "action_advice": "使用 StarNet2",
            "local_environment": {"available": True, "backend": "CoreML", "executable": "/usr/local/bin/starnet2"},
        }
    def test_pipeline_integration_export_payload(self):
        from astropy.io import fits
        from pipeline import run_pipeline

        # 创建极简测试 FITS
        fits_path = os.path.join(self.test_dir, "test_input.fits")
        out_jpg = os.path.join(self.test_dir, "test_output.jpg")
        work_dir = os.path.join(self.test_dir, "work_test")
        fake_data = (np.random.uniform(0.01, 0.05, size=(3, 32, 32)) * 65535.0).astype(np.uint16)
        # 写入几颗假亮星
        fake_data[:, 10, 10] = 60000
        fake_data[:, 20, 20] = 55000
        hdu = fits.PrimaryHDU(fake_data)
        hdu.writeto(fits_path, overwrite=True)

        res = run_pipeline(
            input_path=fits_path,
            output_path=out_jpg,
            steps=['dbe', 'pre_denoise', 'star_remove', 'stretch'],
            work_dir=work_dir,
            keep_all=True,
            export_starnet_payload=True,
            starnet_recommendation='auto',
        )

        # 验证 manifest 中记录了桥接结构
        manifest_path = os.path.join(work_dir, "manifest.json")
        self.assertTrue(os.path.exists(manifest_path))
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        self.assertIn("neural_star_removal_bridge", manifest_data)
        bridge_info = manifest_data["neural_star_removal_bridge"]
        self.assertTrue(bridge_info["payload_exported"])
        self.assertIsNotNone(bridge_info["payload_path"])
        self.assertTrue(os.path.exists(bridge_info["payload_path"]))

        # 验证导出的载荷为标准 uint16 TIFF
        payload_read = imread(bridge_info["payload_path"])
        self.assertEqual(payload_read.dtype, np.uint16)
        self.assertEqual(payload_read.shape, (32, 32, 3))


if __name__ == "__main__":
    unittest.main()
