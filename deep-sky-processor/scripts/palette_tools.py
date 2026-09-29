#!/usr/bin/env python3
"""
Dual-Band False-Color Palette Synthesis Engine (双窄带假彩色色板合成引擎)

基于 Carlo Mollicone (AstroBOH) 的经典双窄带转哈勃色板算法 (GPL-3.0-or-later 兼容实现)。
从双窄带 (Hα + OIII) OSC 彩色数据中，通过物理通道重解离与合成硫二 (Synthetic SII) 分布构建，
合成包括哈勃经典 SHO、HSO、HOO 等多种专业天文假彩色色板。
采用感官亮度绝对保全色域重映射 (Luminance-Preserving Gamut Mapping)，
确保改变色板时 100% 保持底图的亮度结构、对比度与噪声分布，仅置换色彩。
"""

from typing import Dict, Tuple, Optional
import numpy as np
from scipy.ndimage import gaussian_filter


PALETTE_MAPPINGS: Dict[str, Tuple[str, str, str]] = {
    "SHO": ("S", "H", "O"),  # 经典哈勃色板：金黄云气、橄榄过渡与深蓝电离空腔
    "HSO": ("H", "S", "O"),  # 偏暖的深空色板
    "HOO": ("H", "O", "O"),  # 自然双窄带映射 (真彩色基线)
    "OSH": ("O", "S", "H"),  # 适合丝状超新星遗迹
    "OHS": ("O", "H", "S"),  # 适合致密行星状星云
    "HOS": ("H", "O", "S"),  # 适合亮核复合星云
}


def derive_classic_dualband_channels(rgb: np.ndarray) -> Dict[str, np.ndarray]:
    """
    从归一化 RGB 数据中分解 Hα、OIII 并构建合成硫二 (Synthetic SII)。
    
    参数:
      rgb: (H, W, 3) 形状的浮点图像，范围 [0, 1]
    返回:
      字典包含 'H', 'O', 'S' 各单通道二维矩阵
    """
    image = np.asarray(rgb, dtype=np.float32)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"双窄带色板分解需要 (H, W, 3) 形状的彩色图像，当前形状: {image.shape}")

    red = np.clip(image[..., 0], 0.0, 1.0)
    green = np.clip(image[..., 1], 0.0, 1.0)
    blue = np.clip(image[..., 2], 0.0, 1.0)

    # Hα 主要位于红通道
    hydrogen = red
    # OIII 辐射落在 496nm 与 500nm，分布在绿蓝通道
    oxygen = np.clip((green + blue) * 0.50, 0.0, 1.0)
    # 合成硫二：基于 Hα 与 OIII 的能量梯度模拟硫二在发射星云外缘与交界面的辐射分布
    synthetic_sii = np.clip((hydrogen + oxygen) * 0.50, 0.0, 1.0)

    return {"H": hydrogen, "O": oxygen, "S": synthetic_sii}


def get_rec709_luminance(rgb: np.ndarray) -> np.ndarray:
    """计算 Rec.709 感官亮度标量场。"""
    return (
        0.2126 * rgb[..., 0]
        + 0.7152 * rgb[..., 1]
        + 0.0722 * rgb[..., 2]
    ).astype(np.float32)


def luminance_preserving_gamut_map(mapped: np.ndarray, base: np.ndarray) -> np.ndarray:
    """
    保亮度色域重映射。
    在保持 base 亮度完全不变的前提下，将 mapped 的通道色彩比例精准投影到 [0, 1] 色域内。
    """
    target_luma = get_rec709_luminance(base)
    mapped_luma = get_rec709_luminance(mapped)
    
    valid = mapped_luma > 1e-6
    scaled = base.copy()
    
    # 按照亮度比例缩放映射后的颜色
    ratio = np.zeros_like(target_luma)
    ratio[valid] = target_luma[valid] / mapped_luma[valid]
    scaled[valid] = mapped[valid] * ratio[valid, None]
    
    # 色度向量 = scaled - target_luma
    chroma = scaled - target_luma[..., None]
    
    # 计算色域缩放比例，防止任何通道溢出 [0, 1]
    gamut_scale = np.ones_like(target_luma, dtype=np.float32)
    for c in range(3):
        comp = chroma[..., c]
        pos = comp > 1e-7
        neg = comp < -1e-7
        
        channel_scale = np.ones_like(target_luma, dtype=np.float32)
        channel_scale[pos] = (1.0 - target_luma[pos]) / comp[pos]
        channel_scale[neg] = target_luma[neg] / -comp[neg]
        gamut_scale = np.minimum(gamut_scale, channel_scale)
        
    gamut_scale = np.clip(gamut_scale * 0.995, 0.0, 1.0)
    result = target_luma[..., None] + chroma * gamut_scale[..., None]
    result[~valid] = base[~valid]
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def neutralize_palette_green(rgb: np.ndarray, strength: float = 0.90) -> np.ndarray:
    """
    经典哈勃色板去绿 (SCNR) 转换：
    将强盛的 Hα 绿光转化为标志性的哈勃金黄 (Golden Yellow)，
    同时让 OIII 显现为深邃纯净的皇家蓝 (Deep Blue)。
    """
    image = np.asarray(rgb, dtype=np.float32).copy()
    r = image[..., 0]
    g = image[..., 1]
    b = image[..., 2]

    # 哈勃金黄经典配比：绿色受红色上限约束，转化为暖金与琥珀
    target_g = 0.80 * r + 0.20 * b
    excess = np.maximum(g - target_g, 0.0)

    image[..., 1] = g - excess * float(np.clip(strength, 0.0, 1.0))
    return np.clip(image, 0.0, 1.0).astype(np.float32)


def neutralize_dark_background(rgb: np.ndarray, bg_threshold: Optional[float] = None) -> np.ndarray:
    """
    深空暗背景中性灰锁定 (Background Neutralization Anchor)。
    在假彩色映射后，将低信号深空暗背景的 R/G/B 通道中位数精准拉齐至中性灰，
    彻底杜绝底电平色偏，消除 BACKGROUND_COLOR_CAST 警告并凸显星云轮廓。
    """
    from color_tools import lock_background_neutrality
    return lock_background_neutrality(rgb, bg_percentile=25.0, upper_percentile=50.0)


def apply_dualband_palette(
    image: np.ndarray,
    palette: str = "SHO",
    chroma_boost: float = 1.15,
    blend_strength: float = 1.0,
    auto_gold_balance: bool = True,
    star_mask: Optional[np.ndarray] = None,
    neutralize_bg: bool = True,
) -> np.ndarray:
    """
    应用双窄带哈勃假彩色色板合成（支持自然星色保护与背景中性灰锁定）。
    
    参数:
      image: (H, W, 3) 浮点图像，范围 [0, 1]
      palette: 色板名称，支持 'SHO', 'HSO', 'HOO', 'OSH', 'OHS', 'HOS'
      chroma_boost: 色彩饱和度微调因子 (默认 1.15)
      blend_strength: 假彩色混合强度 [0.0, 1.0]，1.0 为完全置换
      auto_gold_balance: 是否自动进行哈勃金黄/深蓝平衡 (SCNR green to gold)
      star_mask: 可选星点保护掩膜 (H, W) 或 (H, W, 1)，用于在单图流程中保护恒星天然色彩
      neutralize_bg: 是否自动对低信号深空背景执行中性灰锁定
    返回:
      合成后的 (H, W, 3) 浮点图像
    """
    palette_key = str(palette or "SHO").strip().upper()
    if palette_key not in PALETTE_MAPPINGS:
        raise ValueError(f"不支持的双窄带色板: {palette}，支持选项: {list(PALETTE_MAPPINGS.keys())}")

    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] != 3:
        return source

    channels = derive_classic_dualband_channels(source)
    ch_r, ch_g, ch_b = PALETTE_MAPPINGS[palette_key]
    
    raw_mapped = np.stack([channels[ch_r], channels[ch_g], channels[ch_b]], axis=-1)

    # 针对 SHO / HSO 进行标志性的哈勃金黄转化 (压绿出金，突显深蓝)
    if auto_gold_balance and palette_key in ("SHO", "HSO"):
        raw_mapped = neutralize_palette_green(raw_mapped, strength=0.90)
    
    # 保亮度色域映射 (保持原始图像 Rec.709 感官亮度与明暗对比绝对不变)
    gamut_mapped = luminance_preserving_gamut_map(raw_mapped, source)
    
    # 可选色彩饱和度微调
    if abs(chroma_boost - 1.0) > 1e-4:
        target_luma = get_rec709_luminance(gamut_mapped)
        chroma = gamut_mapped - target_luma[..., None]
        gamut_mapped = np.clip(target_luma[..., None] + chroma * float(chroma_boost), 0.0, 1.0)

    # 深空暗背景中性灰锁定
    if neutralize_bg:
        gamut_mapped = neutralize_dark_background(gamut_mapped)

    if blend_strength < 1.0:
        strength = float(np.clip(blend_strength, 0.0, 1.0))
        result = source * (1.0 - strength) + gamut_mapped * strength
    else:
        result = gamut_mapped

    # 如果传入了星点掩膜，在星点区域软衰减调色板置换，锁定天然真彩星色
    if star_mask is not None:
        sm = np.asarray(star_mask, dtype=np.float32)
        if sm.ndim == 3:
            sm = np.mean(sm, axis=2)
        feathered_star = gaussian_filter(np.clip(sm, 0.0, 1.0), sigma=1.5)[..., None]
        result = result * (1.0 - feathered_star) + source * feathered_star

    return np.clip(result, 0.0, 1.0).astype(np.float32)

