"""FITS BSCALE/BZERO handling — guards against applying BZERO twice.

astropy 在 fits.open() 阶段已经按 FITS 标准应用了 BSCALE/BZERO（包括
BZERO=2**(BITPIX-1) 的伪无符号 uint16 约定）。曾经的实现又手动
`data * bscale + bzero` 一次，导致整幅图被抬升 32768（50% 量程），
有效对比度被压缩，背景归一化后落在 0.4468 而非 0.17。
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fits_io import read_image


class FitsBzeroTests(unittest.TestCase):
    def _fixture(self, td):
        """写一个真的带 BZERO=32768 的 uint16 FITS（astropy 写 uint16 时会自动加）。"""
        from astropy.io import fits

        data = (np.arange(32 * 32, dtype=np.uint16).reshape(32, 32) * 7) + 1000
        path = Path(td) / "bzero.fits"
        fits.writeto(path, data.astype(np.uint16), overwrite=True)

        header = fits.getheader(path)
        # 前提校验：这个 fixture 必须真的触发 BZERO 分支，否则测试没有意义
        self.assertEqual(float(header.get("BZERO", 0.0)), 32768.0)
        self.assertEqual(int(header["BITPIX"]), 16)
        return path

    def test_bzero_is_not_applied_twice(self):
        from astropy.io import fits

        with tempfile.TemporaryDirectory() as td:
            path = self._fixture(td)
            reference = fits.getdata(str(path))  # astropy 的正确标定结果

            image, meta = read_image(str(path), force_linear=True)

            self.assertAlmostEqual(
                float(image.min()), float(reference.min()), places=3
            )
            self.assertAlmostEqual(
                float(image.max()), float(reference.max()), places=3
            )
            # 核心回归：绝不能等于 astropy 结果再加一次 BZERO
            self.assertNotAlmostEqual(
                float(image.max()),
                float(reference.max()) + 2 * 32768,
                places=3,
            )
            self.assertEqual(meta["original_min"], float(reference.min()))
            self.assertEqual(meta["original_max"], float(reference.max()))

    def test_scale_info_records_bzero_and_true_range(self):
        with tempfile.TemporaryDirectory() as td:
            path = self._fixture(td)
            _image, meta = read_image(str(path))

            # 溯源信息保留 header 原值
            self.assertEqual(meta["bzero"], 32768.0)
            self.assertEqual(meta["bscale"], 1.0)
            # 归一化除数应是真实量程，而不是被 BZERO 抬升后的量程
            self.assertAlmostEqual(
                meta["data_scale"], float(meta["original_max"])
            )
            self.assertNotAlmostEqual(
                meta["data_scale"], float(meta["original_max"]) + 32768, places=3
            )

    def test_normalized_background_lands_near_true_ratio(self):
        """归一化背景 = 真实背景 / 真实最大值，而不是被 BZERO 抬升后的比值。"""
        from astropy.io import fits

        with tempfile.TemporaryDirectory() as td:
            background = 11000
            peak = 60000
            data = np.full((32, 32), background, dtype=np.uint16)
            data[10:20, 10:20] = peak
            path = Path(td) / "levels.fits"
            fits.writeto(path, data.astype(np.uint16), overwrite=True)

            image, _meta = read_image(str(path))

            expected = background / peak
            self.assertAlmostEqual(float(np.median(image)), expected, places=3)
            # 旧行为会把背景推到 (background + 32768) / (peak + 32768) 附近
            wrong = (background + 32768) / (peak + 32768)
            self.assertNotAlmostEqual(float(np.median(image)), wrong, places=3)

    def test_plain_float_fits_is_unchanged(self):
        from astropy.io import fits

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plain.fits"
            data = np.linspace(0.0, 1.0, 32 * 32, dtype=np.float32).reshape(32, 32)
            fits.writeto(path, data, overwrite=True)
            self.assertEqual(float(fits.getheader(path).get("BZERO", 0.0)), 0.0)

            reference = fits.getdata(str(path))
            image, _meta = read_image(str(path), force_linear=True)

            self.assertTrue(np.allclose(image, reference, atol=1e-6))


if __name__ == "__main__":
    unittest.main()
