"""星系主体定位回归测试。

背景：detect_target_crop 用双高通细节（fine - broad）找"紧凑结构"，对尖锐点源
的响应远强于弥散盘，密集星场里必然锁到最亮的恒星。真实 M33 母版上它返回
center=[1812.39, 340.09]（peak/天空=472x 的星点），而真实星系在帧中心附近。

该错误沿管线传导：Phase 1 的 DBE 保护区、Phase 8 的空间饱和掩膜、Phase 8b 的
局部增强全部作用在星空上，导致 texture_snr 失真、local_enhance 空转。

以下用例锁定修复后的行为。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pipeline import (_galaxy_wcs_center, detect_extended_target,  # noqa: E402
                      detect_target_crop, resolve_galaxy_center)


def synthetic_galaxy_field(height=480, width=720, seed=20261006):
    """弥散盘面星系 + 密集亮星场，且刻意让星场中**最亮的一颗星远离星系**。

    最亮星放在远离盘面的角落，使任何"锁到最亮星"的算法都会明显跑偏。
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]

    sky = 0.0020
    image = np.full((height, width), sky, dtype=np.float32)
    image += rng.normal(0, 0.00015, (height, width)).astype(np.float32)

    # 弥散星系盘：核球 + 指数盘，半径约 40px，位于画面中心。
    r = np.sqrt((xx - width / 2.0) ** 2 + (yy - height / 2.0) ** 2)
    disk = 0.9 * np.exp(-r / 18.0) + 0.35 * np.exp(-r / 55.0)
    image += (disk * 0.004).astype(np.float32)

    # 密集星场。
    for _ in range(260):
        y = rng.integers(2, height - 2)
        x = rng.integers(2, width - 2)
        amp = rng.uniform(0.05, 0.4)
        yy0, xx0 = np.ogrid[:3, :3]
        image[y - 1:y + 2, x - 1:x + 2] += (amp * np.exp(
            -(((yy0 - 1) ** 2 + (xx0 - 1) ** 2) / 1.1))).astype(np.float32)

    # 最亮的一颗星：远离星系，画幅角落附近。
    bright_y, bright_x = 40, int(width * 0.92)
    yy0, xx0 = np.ogrid[:3, :3]
    image[bright_y - 1:bright_y + 2, bright_x - 1:bright_x + 2] += (
        2.4 * np.exp(-(((yy0 - 1) ** 2 + (xx0 - 1) ** 2) / 0.9))).astype(np.float32)

    return np.clip(image, 0, None), (bright_y, bright_x)


class DiffuseTargetDetectionTests(unittest.TestCase):
    def test_star_suppressed_detection_finds_galaxy_not_brightest_star(self):
        image, (bright_y, bright_x) = synthetic_galaxy_field()
        rgb = np.repeat(image[:, :, None], 3, axis=2).astype(np.float32)

        info = detect_extended_target(rgb)

        self.assertAlmostEqual(info["center"][0], rgb.shape[1] / 2.0, delta=30)
        self.assertAlmostEqual(info["center"][1], rgb.shape[0] / 2.0, delta=30)

        # 明确不应锁到那颗最亮星。
        self.assertGreater(
            float(np.hypot(info["center"][0] - bright_x, info["center"][1] - bright_y)),
            100.0,
        )

    def test_old_compact_detection_demonstrates_the_failure(self):
        """锁定回归背景：旧算法在本数据上确实锁到最亮星。"""
        image, (bright_y, bright_x) = synthetic_galaxy_field()
        rgb = np.repeat(image[:, :, None], 3, axis=2).astype(np.float32)

        _crop, info = detect_target_crop(rgb)

        self.assertLess(
            float(np.hypot(info["center"][0] - bright_x, info["center"][1] - bright_y)),
            40.0,
        )

    def test_detected_radius_is_physically_plausible(self):
        """半径应覆盖盘面，而非只圈到核球。

        合成星系盘特征尺度 ~18-55px；结果需在该量级内，且远小于画幅短边。
        """
        image, _ = synthetic_galaxy_field()
        rgb = np.repeat(image[:, :, None], 3, axis=2).astype(np.float32)

        info = detect_extended_target(rgb)
        radius = float(info["radius"])

        self.assertGreater(radius, 8.0)
        self.assertLess(radius, rgb.shape[0] * 0.25)

    def test_star_suppress_flag_toggles(self):
        image, _ = synthetic_galaxy_field()
        self.assertTrue(detect_extended_target(image, star_suppress=True)
                        ["star_suppressed"])
        self.assertFalse(detect_extended_target(image, star_suppress=False)
                         ["star_suppressed"])

    def test_grayscale_input_is_accepted(self):
        image, _ = synthetic_galaxy_field()
        info = detect_extended_target(image)
        self.assertEqual(len(info["center"]), 2)

    def test_blank_field_raises_instead_of_guessing(self):
        flat = np.full((200, 200, 3), 0.002, dtype=np.float32)
        with self.assertRaises(ValueError):
            detect_extended_target(flat)


class GalaxyCenterResolutionTests(unittest.TestCase):
    def test_prefers_wcs_center_when_available(self):
        image, _ = synthetic_galaxy_field()
        center, info = resolve_galaxy_center(image, wcs_center=[120.0, 80.0])

        self.assertEqual(info["method"], "wcs")
        self.assertEqual(center, [120.0, 80.0])

    def test_falls_back_to_diffuse_without_wcs(self):
        image, _ = synthetic_galaxy_field()
        center, info = resolve_galaxy_center(image)

        self.assertEqual(info["method"], "diffuse")
        self.assertAlmostEqual(center[0], image.shape[1] / 2.0, delta=30)

    def test_disagreement_is_reported_for_audit(self):
        image, _ = synthetic_galaxy_field()
        center, info = resolve_galaxy_center(image, wcs_center=[50.0, 40.0])

        self.assertIsNotNone(info["disagreement_px"])
        self.assertGreater(info["disagreement_px"], 100.0)
        self.assertEqual(info["frame_center"],
                         [image.shape[1] / 2.0, image.shape[0] / 2.0])

    def test_frame_center_fallback_on_blank_field(self):
        flat = np.full((200, 300, 3), 0.002, dtype=np.float32)
        center, info = resolve_galaxy_center(flat)

        self.assertEqual(info["method"], "frame_center")
        self.assertEqual(center, [150.0, 100.0])

    def test_wcs_center_extraction_requires_exact_name_and_success(self):
        payload = {
            "status": "success",
            "objects": [
                {"name": "M31", "pixel": [10.0, 20.0]},
                {"name": "M33", "pixel": [640.0, 360.0]},
            ],
        }
        self.assertEqual(_galaxy_wcs_center(payload, "M33"), [640.0, 360.0])
        self.assertEqual(_galaxy_wcs_center(payload, "m33"), [640.0, 360.0])
        self.assertIsNone(_galaxy_wcs_center(payload, "M42"))
        self.assertIsNone(_galaxy_wcs_center(payload, None))
        self.assertIsNone(_galaxy_wcs_center({"status": "failed"}, "M33"))
        self.assertIsNone(_galaxy_wcs_center(None, "M33"))


if __name__ == "__main__":
    unittest.main()
