import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from palette_tools import (
    derive_classic_dualband_channels,
    apply_dualband_palette,
    get_rec709_luminance,
    PALETTE_MAPPINGS,
)


class TestTier1Palette:
    def test_derive_classic_dualband_channels(self):
        """测试 H, O, S 物理通道分解与合成硫二"""
        h, w = 20, 20
        img = np.zeros((h, w, 3), dtype=np.float32)
        # Red = 0.8 (Ha), Green = 0.4 (OIII), Blue = 0.4 (OIII)
        img[..., 0] = 0.8
        img[..., 1] = 0.4
        img[..., 2] = 0.4

        channels = derive_classic_dualband_channels(img)
        assert "H" in channels and "O" in channels and "S" in channels
        assert np.isclose(channels["H"][10, 10], 0.8)
        assert np.isclose(channels["O"][10, 10], 0.4)
        # Synthetic SII = (0.8 + 0.4) / 2 = 0.6
        assert np.isclose(channels["S"][10, 10], 0.6)

    def test_luminance_preservation_in_sho(self):
        """测试 SHO 映射严格保全 Rec.709 亮度，杜绝噪声放大或亮暗突变"""
        rng = np.random.default_rng(42)
        h, w = 50, 50
        img = rng.uniform(0.05, 0.75, size=(h, w, 3)).astype(np.float32)

        base_luma = get_rec709_luminance(img)
        sho_mapped = apply_dualband_palette(img, palette="SHO")
        mapped_luma = get_rec709_luminance(sho_mapped)

        # 亮度的平均绝对漂移应极小 (< 0.005)
        diff = np.abs(mapped_luma - base_luma)
        assert np.mean(diff) < 0.005
        assert np.max(diff) < 0.02

    def test_all_palette_mappings_valid(self):
        """测试全部预设色板均能正常生成合法 RGB 输出"""
        h, w = 30, 30
        img = np.full((h, w, 3), 0.5, dtype=np.float32)
        img[..., 0] = 0.7  # 红色主导
        img[..., 1] = 0.3
        img[..., 2] = 0.3

        for name in PALETTE_MAPPINGS:
            res = apply_dualband_palette(img, palette=name)
            assert res.shape == (h, w, 3)
            assert res.min() >= 0.0
            assert res.max() <= 1.0

    def test_invalid_palette_raises(self):
        """测试未知色板名抛出清晰异常"""
        img = np.zeros((10, 10, 3), dtype=np.float32)
        with pytest.raises(ValueError, match="不支持的双窄带色板"):
            apply_dualband_palette(img, palette="INVALID_PALETTE")
