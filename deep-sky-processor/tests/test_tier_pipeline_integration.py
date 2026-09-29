#!/usr/bin/env python3
import sys
import os
import tempfile
import numpy as np
import pytest
from skimage.io import imsave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from pipeline import run_pipeline


def test_pipeline_tier_options_integration():
    with tempfile.TemporaryDirectory() as tmpdir:
        input_fits = os.path.join(tmpdir, "test_input.fits")
        output_jpg = os.path.join(tmpdir, "test_output.jpg")
        
        # 构造一个 128x128x3 线性浮点图像并保存为 FITS
        h, w = 128, 128
        img = np.full((h, w, 3), 0.02, dtype=np.float32)
        # 添加中心亮星和星云结构
        cy, cx = 64, 64
        y, x = np.ogrid[:h, :w]
        r = np.sqrt((y - cy) ** 2 + (x - cx) ** 2)
        nebula = 0.15 * np.exp(-r / 25.0)
        img[..., 0] += nebula * 1.5  # Hα
        img[..., 1] += nebula * 0.8  # OIII
        img[..., 2] += nebula * 0.8  # OIII
        # 亮星
        img[cy, cx, :] = 0.95
        # 边缘叠边暗带
        img[:2, :, :] = 0.001

        from astropy.io import fits
        hdu = fits.PrimaryHDU(np.transpose(img, (2, 0, 1)))
        hdu.header['OBJECT'] = 'IC5070'
        hdu.writeto(input_fits)

        # 运行管线：包含反卷积、叠边裁切、星晕守卫和哈勃假彩色调色板
        result = run_pipeline(
            input_fits,
            output_jpg,
            steps='dbe,color,pre_denoise,star_remove,stretch,final_color',
            preset='emission',
            palette='sho',
            linear_deconv=True,
            deconv_iterations=3,
            stacking_crop='auto',
            halo_guard=True,
            work_dir=os.path.join(tmpdir, "work"),
            save_intermediates=True,
        )

        assert os.path.exists(output_jpg)
        assert result.get('status') == 'success'
        # 验证调色板与反卷积产物已正常生成
        work = os.path.join(tmpdir, "work")
        assert os.path.exists(os.path.join(work, "02c_linear_deconv.tif"))
        assert os.path.exists(os.path.join(work, "08_palette_sho.tif"))
