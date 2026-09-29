#!/usr/bin/env python3
"""
Deep-Sky Histogram Stretching (直方图拉伸)

原理：
  深空线性图像中绝大部分像素集中在暗部，肉眼看去几乎全黑。
  拉伸通过非线性映射将暗部数据重新分配到整个亮度范围，
  使星云的暗弱结构变得可见。

支持方法：
  - arcsinh: 反双曲正弦拉伸（类似对数，但保留亮部细节）
  - mtf:    Midtone Transfer Function（S曲线保留阴影与高光）
  - masked: 蒙版拉伸（保护亮星不让其过曝）
  - gamma:  伽马校正（简单非线性拉伸）

用法:
  python stretch.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from scipy.ndimage import gaussian_filter, median_filter


def arcsinh_stretch(image, factor=30.0, black_point=0.0):
    """
    反双曲正弦拉伸。
    原理: stretched = arcsinh(x * factor) / arcsinh(factor)
    类似对数拉伸，但在亮部和暗部都保持细节。
    factor 越大，拉伸越激进（暗部提亮越多）。
    """
    norm = np.arcsinh(factor)
    stretched = np.arcsinh((image - black_point) * factor) / norm
    return np.clip(stretched, 0, 1)


def mtf_stretch(image, midtones=0.5, shadows=0.0):
    """
    Midtone Transfer Function (MTF) 拉伸。
    原理: MTF = (m-1)*x / ((2*m-1)*x - m)
    其中 m 控制中间调位置。
    本质是一个 S 曲线：保护阴影和高光，只拉伸中间调。
    midtones: 0-1，控制中间调位置 (<0.5 偏暗, >0.5 偏亮)
    shadows: 阴影保护强度

    注意：midtones=0.5 时本函数是**恒等映射**（`((0.5-1)x)/((1-1)x-0.5) = x`），
    完全不做拉伸。要真正抬升极暗数据必须取 m < 0.5。
    """
    m = max(midtones, 0.001)
    x = np.clip(image - shadows, 0, 1)
    stretched = ((m - 1) * x) / ((2 * m - 1) * x - m)
    return np.clip(stretched, 0, 1)


def inverse_mtf_stretch(image, midtones=0.25, shadows=0.0):
    """MTF 的解析逆变换：从已拉伸域回到线性域。

    由 `y = (m-1)x / ((2m-1)x - m)` 反解：
        y·((2m-1)x - m) = (m-1)x
        x·[(2m-1)y - (m-1)] = m·y
        x = m·y / ((2m-1)·y - (m-1))

    数值边界：分母 `D(y) = (2m-1)y - (m-1)` 在 `m∈(0,1)`、`y∈[0,1]` 上恒
    `≥ min(m, 1-m) > 0`，**区间内无极点**（极点 `y=(1-m)/(1-2m)` 落在 [0,1] 之外）。
    故只需 clip 输入即可，无需复杂保护。

    shadows：正向变换里的 `clip(image - shadows, 0, 1)` 会让所有 ≤ shadows 的
    像素永久归零，**不可逆**。因此去星载荷配方固定 shadows=0.0；若传非零值，
    低于 shadows 的信息已经丢失，这里只能加回常量。
    """
    m = float(np.clip(midtones, 1e-4, 1.0 - 1e-4))
    y = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    denom = (2.0 * m - 1.0) * y - (m - 1.0)
    x = np.clip((m * y) / np.where(np.abs(denom) < 1e-9, 1e-9, denom), 0.0, 1.0)
    if shadows:
        x = np.clip(x + float(shadows), 0.0, 1.0)
    return x.astype(np.float32)


def derive_mtf_midtones(image, target_bg=0.15, min_midtones=0.02,
                        max_midtones=0.45, fallback=0.15):
    """按背景中值反解 midtones，使背景被映射到 target_bg。

    令 `MTF(m, b) = t`（b = 图像背景中值，t = target_bg），解出
        m = b(t-1) / (2bt - t - b)

    底部增益为 `(1-m)/m`：m=0.15 → 5.7×，m=0.02 → 49×。

    **必须 < 0.5**（m=0.5 是恒等映射，等于没拉伸）。clamp 到
    `[min_midtones, max_midtones]` 防止病态放大噪声。

    注意：极暗数据（b ~ 2.7e-4）算出的 m 会低于下限而被 clamp 到 0.02，
    此时背景实际只能抬到 ~0.013 而非 target_bg —— target_bg 是方向而非保证。
    """
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        lum = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1]
               + 0.0722 * arr[..., 2])
    else:
        lum = arr
    b = float(np.median(lum))
    if not np.isfinite(b) or b <= 0.0 or b >= float(target_bg):
        return float(np.clip(fallback, min_midtones, max_midtones))
    t = float(target_bg)
    m = b * (t - 1.0) / (2.0 * t * b - t - b)
    if not np.isfinite(m) or m <= 0.0:
        return float(np.clip(fallback, min_midtones, max_midtones))
    return float(np.clip(m, min_midtones, max_midtones))


def masked_stretch(image, target_bg=0.1, factor=100.0):
    """
    蒙版拉伸（类似 PixInsight MaskedStretch）。
    原理：先做星点蒙版保护亮星（用阈值+模糊），
    然后对背景和星云区域做激进拉伸，亮星区域保持不变。
    这样亮星不会在拉伸过程中过曝。

    对极暗图像自动降级为全局 arcsinh + 亮度归一化。
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)

    # 检测图像是否极暗（中位数极低或亮部不足），若是则使用积极的全局拉伸
    p50 = np.percentile(img_gray, 50)
    p99 = np.percentile(img_gray, 99)
    if p50 <= 0.02 or p99 <= 0.3:
        # 极暗图像：arcsinh + 百分位归一化 + 背景映射
        stretched = arcsinh_stretch(image, factor=factor)
        # 将 0.1%-99.9% 范围映射到 [0, 1]，充分利用动态范围
        p_low = np.percentile(stretched, 0.1)
        p_high = np.percentile(stretched, 99.9)
        if p_high > p_low:
            normalized = np.clip((stretched - p_low) / (p_high - p_low), 0, 1)
        else:
            normalized = stretched
        # 将暗部映射到目标背景亮度
        bg_current = np.percentile(normalized, 10)
        scale = target_bg / max(bg_current, 1e-6)
        result = np.clip(normalized * scale, 0, 1)
        print(f"[masked_stretch] 极暗数据回退: p50={p50:.6f} p99={p99:.6f} "
              f"归一化后 scale={scale:.2f}x")
        return result

    # 正常亮度图像：使用蒙版拉伸
    # 生成星点蒙版：亮于阈值 + 模糊边缘
    threshold = np.percentile(img_gray, 99)
    star_mask = (img_gray > threshold).astype(np.float32)
    star_mask = gaussian_filter(star_mask, sigma=3)
    star_mask = np.clip(star_mask, 0, 1)

    # 对非星区域做拉伸
    bg_mask = 1.0 - star_mask
    stretched = arcsinh_stretch(image, factor=factor)
    if image.ndim == 3:
        star_mask_3d = np.expand_dims(star_mask, axis=-1)
        bg_mask_3d = np.expand_dims(bg_mask, axis=-1)
        result = image * star_mask_3d + stretched * bg_mask_3d
    else:
        result = image * star_mask + stretched * bg_mask

    # 目标背景亮度
    bg_select = stretched[img_gray <= np.percentile(img_gray, 50)]
    bg_level = np.median(bg_select) if len(bg_select) > 0 else target_bg
    result = result * (target_bg / max(bg_level, 0.001))
    return np.clip(result, 0, 1)


def auto_stretch(image, clip_shadows=0.01, clip_highlights=0.999):
    """
    自动拉伸：基于百分位数裁剪后做线性映射。
    原理：找到暗部和亮部的百分位阈值，将中间部分线性拉伸到[0,1]。
    适合快速预览。
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)
    shadow = np.percentile(img_gray, clip_shadows * 100)
    highlight = np.percentile(img_gray, clip_highlights * 100)
    stretched = (image - shadow) / max(highlight - shadow, 0.0001)
    return np.clip(stretched, 0, 1)


def deep_stretch(image, shadow_pctl=0.5, highlight_pctl=99.9, gamma=0.4):
    """
    针对极暗深空数据的激进拉伸（两阶段：百分位裁剪 + 伽马增强）。

    原理：
      1. 对每个通道分别计算低/高百分位阈值，线性拉伸到 [0,1]
      2. 用 gamma<1 做伽马校正，大幅提亮暗部、增强暗部对比度
      3. 保留各通道独立的比例关系，不破坏原始颜色差异

    参数:
      shadow_pctl:  暗部裁剪百分位 (默认 0.5，即背景中位数附近)
      highlight_pctl: 亮部裁剪百分位 (默认 99.9，保留最亮星点)
      gamma:        伽马值 (<1 增强暗部，默认 0.4)
    """
    result = image.copy()
    is_color = image.ndim == 3 and image.shape[2] >= 3

    if is_color:
        for c in range(image.shape[2]):
            ch = image[..., c]
            shadow = np.percentile(ch, shadow_pctl)
            highlight = np.percentile(ch, highlight_pctl)
            span = highlight - shadow
            if span > 1e-9:
                diff = ch - shadow
                epsilon = 0.05 * max(shadow, 1e-6)
                val = diff / epsilon
                corrected_ch = np.where(
                    val > 50.0,
                    diff,
                    epsilon * np.log(1.0 + np.exp(np.clip(val, -50.0, 50.0)))
                )
                result[..., c] = np.clip(corrected_ch / span, 0, 1)
    else:
        shadow = np.percentile(image, shadow_pctl)
        highlight = np.percentile(image, highlight_pctl)
        span = highlight - shadow
        if span > 1e-9:
            diff = image - shadow
            epsilon = 0.05 * max(shadow, 1e-6)
            val = diff / epsilon
            corrected_ch = np.where(
                val > 50.0,
                diff,
                epsilon * np.log(1.0 + np.exp(np.clip(val, -50.0, 50.0)))
            )
            result = np.clip(corrected_ch / span, 0, 1)

    # 伽马校正：gamma < 1 时暗部被大幅提亮，亮部变化较小
    result = np.power(result, gamma)
    return np.clip(result, 0, 1)


def _smoothstep(values):
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


def _lift_underfilled_highlights(luminance, target_bg, target_p99=0.5):
    """Lift signal above the background when the stretched range is underfilled."""
    current_p99 = float(np.percentile(luminance, 99.0))
    if current_p99 >= target_p99 or current_p99 <= target_bg + 1e-6:
        return luminance

    signal_position = (
        (luminance - float(target_bg))
        / max(current_p99 - float(target_bg), 1e-6)
    )
    signal_weight = _smoothstep(signal_position)
    lift = min(float(target_p99) - current_p99, 0.35)
    return np.clip(luminance + signal_weight * lift, 0, 1)


def pi_mtf_stretch(image, target_bg=0.075, headroom=0.12, shadow_ratio=0.75):
    """
    PixInsight 标准 Linked MTF (Midtone Transfer Function) 拉伸。

    采用 PixInsight 官方 STF 算法：
    1. 测量全图或通道背景中位数与极大值；
    2. shadows 锚定在背景下方 (保留噪声基底，杜绝黑点削波)；
    3. highlights 锚定在极大值上方 (极大值 * (1.0 + headroom))，杜绝高光截断；
    4. 反解 midtones 参数 m = b*(t-1) / (2*b*t - t - b)；
    5. 应用 MTF 传递函数: MTF(m, x) = (m - 1)*x / ((2m - 1)*x - m)；
    6. 通道关联保色，完美还原深空发射星云/星系真实色彩与微观纤维。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    rgb = source[..., :3] if is_color else source

    bg = float(np.median(rgb))
    max_val = float(np.max(rgb))
    if max_val <= 1e-9:
        return np.zeros_like(source, dtype=np.float32)

    shadows = bg * float(shadow_ratio)
    highlights = max_val * (1.0 + float(headroom))

    x = np.clip((rgb - shadows) / max(highlights - shadows, 1e-9), 0.0, 1.0)
    bg_x = float(np.median(x))

    t = float(target_bg)
    b = bg_x
    denom = 2.0 * b * t - t - b
    if abs(denom) < 1e-9:
        m = 0.5
    else:
        m = b * (t - 1.0) / denom
    m = float(np.clip(m, 0.001, 0.999))

    stretched = ((m - 1.0) * x) / ((2.0 * m - 1.0) * x - m)
    result = np.clip(stretched, 0.0, 1.0)

    if is_color and source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])
    return result.astype(np.float32)


def very_dark_stretch(image, factor=25.0, gamma=0.45,
                      shadow_pctl=0.1, highlight_pctl=100.0,
                      target_bg=0.12, min_p99=0.5, headroom=0.12):
    """
    极暗数据专用保色拉伸（带高光 Headroom 保护）。

    使用每通道保守黑点消除基线，但非线性曲线只从亮度生成，并将相同
    的逐像素增益应用回 RGB，避免独立通道归一化放大噪声或改写色相。
    通过 headroom 机制保留高光动态空间，杜绝无星图上星云核心削顶。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    rgb = source[..., :3] if is_color else source

    if is_color:
        flat = rgb.reshape(-1, 3)
        medians = np.percentile(flat, 50, axis=0)
        black_points = np.percentile(flat, shadow_pctl, axis=0)
        black_points = np.minimum(black_points, medians * 0.25)
        corrected = np.clip(rgb - black_points, 0, None)
        luminance = (
            0.2126 * corrected[..., 0]
            + 0.7152 * corrected[..., 1]
            + 0.0722 * corrected[..., 2]
        )
    else:
        median = float(np.percentile(rgb, 50))
        black_point = min(
            float(np.percentile(rgb, shadow_pctl)),
            median * 0.25,
        )
        corrected = np.clip(rgb - black_point, 0, None)
        luminance = corrected

    max_lum = float(np.max(luminance))
    if float(highlight_pctl) >= 100.0:
        scale_ref = max_lum
    else:
        p_val = float(np.percentile(luminance, highlight_pctl))
        scale_ref = max_lum if max_lum < p_val * 1.5 else p_val
    if scale_ref <= 1e-9:
        scale_ref = max_lum
    if scale_ref <= 1e-9:
        return np.zeros_like(source, dtype=np.float32)

    normalized_luminance = np.clip(luminance / scale_ref, 0, None)
    stretched_luminance = (
        np.arcsinh(normalized_luminance * float(factor))
        / np.arcsinh(float(factor))
    )
    stretched_luminance = np.power(
        np.clip(stretched_luminance, 0, 1),
        float(gamma),
    )

    background_mask = luminance <= np.percentile(luminance, 50)
    positive_background = stretched_luminance[
        background_mask & (luminance > 0)
    ]
    if positive_background.size:
        background_level = float(np.median(positive_background))
        stretched_luminance *= (
            float(target_bg) / max(background_level, 1e-6)
        )

    stretched_luminance = _lift_underfilled_highlights(
        stretched_luminance,
        target_bg=float(target_bg),
        target_p99=float(min_p99),
    )

    # 预留高光 headroom 软肩压缩，避免 R 通道硬性撞墙
    if headroom > 0:
        target_max = 1.0 - float(headroom)
        cur_max = float(np.max(stretched_luminance))
        if cur_max > target_max:
            ro_start = float(target_bg) + 0.25
            if cur_max > ro_start:
                above = stretched_luminance > ro_start
                scale_h = (target_max - ro_start) / (cur_max - ro_start)
                stretched_luminance = np.where(above, ro_start + (stretched_luminance - ro_start) * scale_h, stretched_luminance)

    if is_color:
        gain = stretched_luminance / np.maximum(luminance, 1e-9)
        result = corrected * gain[..., None]
        peak = np.max(result, axis=2, keepdims=True)
        # 平滑 rolloff 替代生硬截断
        target_ceil = 1.0 - (float(headroom) if headroom > 0 else 0.0)
        over = peak > target_ceil
        result = np.where(over, result * (target_ceil / np.maximum(peak, 1e-6)), result)
        if source.shape[2] > 3:
            result = np.dstack([result, source[..., 3:]])
    else:
        result = stretched_luminance

    return np.clip(result, 0, 1).astype(np.float32)


def emission_stretch(image, shadow_pctl=0.5, highlight_pctl=99.94,
                     gamma=0.33, target_bg=0.08,
                     min_p99=0.5, headroom=0.0):
    """
    发射星云自适应保色拉伸 (优化版，支持高光 Headroom 保护)。

    每通道只校正暗部黑点，再从亮度通道生成一条共享拉伸曲线，并将
    同一亮度增益应用回 RGB。这样不会用独立通道归一化篡改 Hα/OIII
    的真实颜色比例，也避免共享 RGB 标尺把弱 G/B 通道数值压死。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    if not is_color:
        return deep_stretch(
            source,
            shadow_pctl=shadow_pctl,
            highlight_pctl=highlight_pctl,
            gamma=gamma,
        )

    flat = source[..., :3].reshape(-1, 3)
    medians = np.percentile(flat, 50, axis=0)
    black_points = np.percentile(flat, shadow_pctl, axis=0)
    black_points = np.minimum(black_points, medians * 0.3)
    
    diff = source[..., :3] - black_points
    corrected = np.clip(diff, 0, None)
    
    luminance = (
        0.2126 * corrected[..., 0]
        + 0.7152 * corrected[..., 1]
        + 0.0722 * corrected[..., 2]
    )

    max_lum = float(np.max(luminance))
    if float(highlight_pctl) >= 100.0:
        scale_ref = max_lum
    else:
        p_val = float(np.percentile(luminance, highlight_pctl))
        scale_ref = max_lum if max_lum < p_val * 1.5 else p_val
    if scale_ref <= 1e-9:
        scale_ref = max_lum
    if scale_ref <= 1e-9:
        return np.zeros_like(source, dtype=np.float32)

    normalized_lum = np.clip(luminance / scale_ref, 0, None)
    stretched_luminance = np.where(
        normalized_lum <= 1.0,
        np.power(normalized_lum, gamma),
        1.0 + np.arcsinh(normalized_lum - 1.0) * float(gamma),
    )

    background_mask = luminance <= np.percentile(luminance, 50)
    positive_background = stretched_luminance[
        background_mask & (luminance > 0)
    ]
    if positive_background.size:
        background_level = float(np.median(positive_background))
        stretched_luminance *= float(target_bg) / max(background_level, 1e-6)

    stretched_luminance = _lift_underfilled_highlights(
        stretched_luminance,
        target_bg=float(target_bg),
        target_p99=float(min_p99),
    )

    # 预留高光 headroom
    if headroom > 0:
        target_max = 1.0 - float(headroom)
        cur_max = float(np.max(stretched_luminance))
        if cur_max > target_max:
            ro_start = float(target_bg) + 0.25
            if cur_max > ro_start:
                above = stretched_luminance > ro_start
                scale_h = (target_max - ro_start) / (cur_max - ro_start)
                stretched_luminance = np.where(above, ro_start + (stretched_luminance - ro_start) * scale_h, stretched_luminance)

    gain = stretched_luminance / np.maximum(luminance, 1e-9)
    result = corrected * gain[..., None]

    peak = np.max(result, axis=2, keepdims=True)
    target_ceil = 1.0 - (float(headroom) if headroom > 0 else 0.0)
    over = peak > target_ceil
    result = np.where(over, result * (target_ceil / np.maximum(peak, 1e-6)), result)

    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])
    return np.clip(result, 0, 1).astype(np.float32)


def _ghs_base(x, sp, b):
    """GHS 基准 S 曲线，两端锚定在 0 与 1。"""
    denom = np.sinh(b * (1.0 - sp)) - np.sinh(-b * sp)
    if abs(denom) < 1e-9:
        denom = 1e-9
    return (np.sinh(b * (x - sp)) - np.sinh(-b * sp)) / denom


def _ghs_base_slope(x, sp, b):
    """基准曲线的一阶导数，用于在 LP/HP 边界构造切线。"""
    denom = np.sinh(b * (1.0 - sp)) - np.sinh(-b * sp)
    if abs(denom) < 1e-9:
        denom = 1e-9
    return b * np.cosh(b * (x - sp)) / denom


def ghs_stretch(image, sp=0.01, b=8.0, c=0.0, lp=0.0, hp=1.0):
    """
    Generalized Hyperbolic Stretch (GHS) 广义双曲拉伸，带 LP/HP 两端保护。

    窗口内基准曲线数学模型:
      f(x) = [sinh(b * (x - sp)) - sinh(-b * sp)] / [sinh(b * (1.0 - sp)) - sinh(-b * sp)]

    sp: 对称点（通常在背景中值附近，如 0.002 ~ 0.05）
    b: 拉伸强度因子（通常为 2 ~ 15，数值越大拉伸越强烈）
    c: 高光平滑 rolloff 因子，可选。
    lp: 阴影锚点。曲线在 lp 以下改走 lp 处的切线，只做等比缩放、不引入非线性
        扭曲。极暗数据把它设为背景水平，可避免暗部被压成一条窄带（拉伸坍缩）。
    hp: 高光锚点。曲线在 hp 以上同样改走切线，为亮核留出高光 headroom，并让
        离群亮像素不再主导整条曲线。

    窗口 [lp, hp] 之外做切线延伸后整体仿射归一化，使 f(0)=0、f(1)=1。
    默认 lp=0.0 / hp=1.0 时窗口覆盖全域，行为与不带 LP/HP 的旧实现逐位一致。
    """
    x = np.clip(image, 0, 1)
    b = max(float(b), 1e-5)
    sp = float(sp)

    lp = float(np.clip(lp, 0.0, 1.0))
    hp = float(np.clip(hp, 0.0, 1.0))
    if hp <= lp + 1e-6:
        # 退化窗口无法定义曲线，回退到全域（等价于旧行为）
        lp, hp = 0.0, 1.0

    t_lp = float(_ghs_base(lp, sp, b))
    t_hp = float(_ghs_base(hp, sp, b))
    s_lp = float(_ghs_base_slope(lp, sp, b))
    s_hp = float(_ghs_base_slope(hp, sp, b))

    stretched = _ghs_base(x, sp, b)

    # 窗口外以边界切线线性延伸：只做等比缩放，不产生非线性扭曲。
    if lp > 0.0:
        below = x < lp
        if below.any():
            stretched[below] = t_lp + s_lp * (x[below] - lp)
    if hp < 1.0:
        above = x >= hp
        if above.any():
            stretched[above] = t_hp + s_hp * (x[above] - hp)

    # 仿射归一化，把两条切线在 0 / 1 处的截距钉回 [0, 1]
    r0 = t_lp - s_lp * lp
    r1 = t_hp + s_hp * (1.0 - hp)
    stretched = (stretched - r0) / max(r1 - r0, 1e-12)

    # 压制高光核心 Rolloff 保护
    if c > 0:
        c = float(c)
        stretched = np.power(stretched, 1.0 + c * (1.0 - stretched))

    return np.clip(stretched, 0, 1)


def masked_ghs_stretch(image, sp=0.01, b=8.0, protect_strength=0.5,
                       smooth_sigma=5.0, target_bg=0.08,
                       shadow_pctl=0.0, highlight_pctl=99.9, gamma=0.45,
                       lp=None, hp=None,
                       protect_lp=None, protect_hp=None, c=0.0):
    """
    基于亮度掩膜自适应保护的分区 GHS 拉伸。

    高光保护掩膜(Luminance Mask)使得高亮度核心和亮星主要应用温和的拉伸，而暗星云/背景主要
    应用激进的 GHS 拉伸，最后合并并自适应平移背景。

    lp / hp: 可选的显式归一化锚点（绝对输入单位）。给出时直接作为低/高锚点，
        跳过 shadow_pctl / highlight_pctl 推导与 max_val 兜底，用于精确控制暗部
        拉伸起点与亮部 headroom。两者为 None（默认）时行为与旧实现逐位一致。
    protect_lp / protect_hp: 内层 GHS 的两端切线保护锚点，**归一化 [0,1] 空间**。
        取值 None（默认，禁用）/ "auto"（按归一化背景与 p99.5 自动推导）/ float。
        默认 None 时内层调用等价于 lp=0、hp=1，输出与旧实现逐位一致。
    c: GHS 输出后的高光 rolloff（域无关，作用在 [0,1] 的曲线输出上）。默认 0.0。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    source_gray = source if not is_color else np.mean(source, axis=2)

    # 极暗线性数据先映射到可用动态范围。shadow_pctl=0 时不减黑位，
    # 所有原本大于零的微弱信号都会保留。
    if lp is not None:
        low = float(lp)
    else:
        low = (
            0.0
            if float(shadow_pctl) <= 0
            else float(np.percentile(source_gray, shadow_pctl))
        )
    if hp is not None:
        # 显式高光锚点，跳过百分位推导与 max_val 兜底
        high = float(hp)
    elif float(highlight_pctl) >= 100.0:
        high = float(np.max(source_gray))
    else:
        high = float(np.percentile(source_gray, highlight_pctl))
        max_val = float(np.max(source_gray))
        if max_val > high * 1.5:
            high = max_val
    if high <= low + 1e-12:
        high = float(np.max(source_gray))
    if high <= low + 1e-12:
        return np.clip(source, 0, 1)

    normalized = np.clip((source - low) / (high - low), 0, 1)
    normalized = np.power(
        normalized,
        float(np.clip(gamma, 0.2, 1.0)),
    )
    img_gray = normalized if not is_color else np.mean(normalized, axis=2)

    # 1. 产生亮度保护掩膜
    mask = gaussian_filter(img_gray, sigma=smooth_sigma)
    mask_max = float(mask.max())
    if mask_max > 1e-8:
        mask = mask / mask_max
    # 用 sqrt (power 0.5) 展宽中高光保护区域
    mask = np.power(mask, 0.5)
    mask = np.clip(mask * float(protect_strength), 0, 1)

    if is_color:
        mask_3d = np.expand_dims(mask, axis=-1)
    else:
        mask_3d = mask

    # 2. 激进 GHS 拉伸轨道
    if sp is None or sp < 0:
        # 自动取灰度中值作为 sp
        sp = float(np.median(img_gray))

    # 内层 GHS 的两端切线保护：**归一化 [0,1] 空间**的锚点。
    #
    # 与外层 lp/hp 语义完全不同——外层 lp/hp 是「绝对输入单位的重锚点」
    # （source=lp→0、source=hp→1），作用在归一化之前；内层 ghs_stretch 看到
    # 的已经是 normalized，若把外层 lp/hp 原样透传则量纲不符，按
    # ((v-low)/(high-low)) 换算又会退化为 (0,1)（等于无保护）。故必须用独立键。
    #
    # c 与之不同：它作用在曲线输出（已是 [0,1]）上，域无关，可以直接透传。
    bg_norm = float(np.median(img_gray))          # 恒等于上面的自动 sp
    auto_lp = float(np.clip(bg_norm, 0.005, 0.10))                       # ≈ 归一化背景水平
    auto_hp = float(np.clip(np.percentile(img_gray, 99.5), 0.70, 0.95))

    def _resolve_anchor(value, auto_value):
        if value is None:
            return None
        if isinstance(value, str):
            if value.strip().lower() != 'auto':
                raise ValueError(f"内层保护锚点只接受 None / 'auto' / float，收到 {value!r}")
            return auto_value
        return float(np.clip(float(value), 0.0, 1.0))

    protect_lp_val = _resolve_anchor(protect_lp, auto_lp)
    protect_hp_val = _resolve_anchor(protect_hp, auto_hp)
    if (protect_lp_val is not None and protect_hp_val is not None
            and protect_hp_val <= protect_lp_val + 1e-6):
        print(
            f"  [WARN] 内层保护窗口退化 (hp {protect_hp_val:.4f} <= "
            f"lp {protect_lp_val:.4f})，已禁用内层 LP/HP"
        )
        protect_lp_val = protect_hp_val = None

    stretched_strong = ghs_stretch(
        normalized,
        sp=sp,
        b=b,
        c=c,
        lp=0.0 if protect_lp_val is None else protect_lp_val,
        hp=1.0 if protect_hp_val is None else protect_hp_val,
    )

    # 3. 温和拉伸保护轨道（主要针对亮部核心和星点）
    stretched_weak = arcsinh_stretch(normalized, factor=5.0)

    # 4. 根据亮度掩膜插值混合
    blended = stretched_weak * mask_3d + stretched_strong * (1.0 - mask_3d)

    # 5. 目标背景自动亮度归一化
    bg_mask = source_gray <= np.percentile(source_gray, 50)
    positive_bg = blended[bg_mask & (source_gray > 0)]
    bg_level = (
        np.median(positive_bg)
        if positive_bg.size > 0
        else np.median(blended[bg_mask])
    )
    if bg_level > 1e-4:
        result = blended * (float(target_bg) / bg_level)
    else:
        result = blended

    return np.clip(result, 0, 1)


def luminance_range_health(image):
    """Summarise the tonal distribution used to judge whether a stretch worked."""
    gray = np.asarray(image, dtype=np.float32)
    if gray.ndim == 3:
        gray = np.mean(gray, axis=2)
    p50 = float(np.median(gray))
    p99 = float(np.percentile(gray, 99.0))
    p999 = float(np.percentile(gray, 99.9))
    return {
        'p50': p50,
        'p99': p99,
        'p999': p999,
        'span': p99 - p50,
        'core_ratio': p999 / max(p50, 1e-9),
    }


def is_stretch_collapsed(before, after, min_span=0.02):
    """Detect a stretch that compressed the tonal range instead of expanding it.

    判据只有一条：拉伸后 `p99 - p50` 既不能低于绝对下限 `min_span`，也不能低于
    拉伸前的 25%。病态输出（例如 masked_ghs 在黑点估计失准时）会把几乎所有像素
    挤进一条很窄的亮度带里，用这个**绝对跨度**判断最直接。

    历史说明：这里曾有一条 `p999/p50`（core_ratio）判据，要求拉伸后该比值不低于
    拉伸前的一半（`min_core_frac=0.5`）。该判据在数学上不成立 —— p999/p50 是比值，
    **任何抬升背景的拉伸都会让它下降**（实测 216 个正常拉伸样本的归一化比值分布
    在 0.31~0.97，中位数 0.47），因此无法区分"正常的背景抬升"与"病态的对比度压塌"。
    以 0.5 为阈值时实测误报率 40.3%；且它在暗数据上（拉伸前 p50 <= 0.01）本就处于
    禁用状态，对人工构造的 4 个洗白病态检出 0/4，低于 span 判据的 3/4。已移除。

    已知局限：把整幅图整体映射到高亮度区间（如 [0.50, 0.90]）的"洗白"病态**不会**
    被本判据捕获。实测该情形的 span 比为 0.80，而健康的极暗拉伸为 0.95 —— 用 span
    （无论绝对下限还是相对比例）都无法区分，把绝对下限从 0.02 提到 0.04 只会把两者
    一起误判。真正的判别量是输出背景 p50（0.50 对 0.006，差两个数量级），但那需要
    一个独立的背景电平门禁并经过真实数据校准，不在本判据的职责范围内。
    """
    hb = luminance_range_health(before)
    ha = luminance_range_health(after)
    collapsed = ha['span'] < max(min_span, hb['span'] * 0.25)
    return bool(collapsed), hb, ha


def apply_luminance_stretch(image, method='arcsinh', **kwargs):
    """
    亮度通道拉伸，保留原始色彩比例。
    原理：将图像转换到 Lab 色彩空间，只对 L 通道拉伸，
    保持 a/b 色彩通道不变，避免拉伸时颜色偏移。

    对极暗数据（median < 0.001）不使用 Lab 转换（极低值下不稳定），
    直接对 RGB 做整体拉伸，保持颜色比例。
    """
    is_color = image.ndim == 3 and image.shape[2] >= 3
    if not is_color:
        return globals()[f'{method}_stretch'](image, **kwargs)

    # 这两种方法自己从 RGB 亮度构造共享增益。先转 Lab 会丢失通道
    # 信息，使专用的保色路径失效。
    if method == 'emission':
        return emission_stretch(image, **kwargs)
    if method == 'very_dark':
        return very_dark_stretch(image, **kwargs)

    gray = np.mean(image, axis=2)
    # 极暗数据：跳过 Lab 转换，直接在 RGB 上拉伸
    if np.median(gray) < 0.001:
        stretch_func = {
            'arcsinh': arcsinh_stretch,
            'luminance_arcsinh': arcsinh_stretch,
            'mtf': mtf_stretch,
            'masked': masked_stretch,
            'auto': auto_stretch,
            'deep': deep_stretch,
            'very_dark': very_dark_stretch,
            'emission': emission_stretch,
            'ghs': ghs_stretch,
            'masked_ghs': masked_ghs_stretch,
        }.get(method, arcsinh_stretch)
        return stretch_func(image, **kwargs)

    from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb
    lab = rgb2lab(image)
    L = lab[..., 0] / 100.0

    stretch_func = {
        'arcsinh': arcsinh_stretch,
        'luminance_arcsinh': arcsinh_stretch,
        'mtf': mtf_stretch,
        'masked': masked_stretch,
        'auto': auto_stretch,
        'deep': deep_stretch,
        'very_dark': very_dark_stretch,
        'emission': emission_stretch,
        'ghs': ghs_stretch,
        'masked_ghs': masked_ghs_stretch,
    }.get(method, arcsinh_stretch)

    L_stretched = stretch_func(L, **kwargs)

    # 用亮度增益作用于 RGB，逐像素精确保留通道比例。
    #
    # 不能只替换 Lab 的 L 通道：Lab 的 a/b 是**绝对**色度，亮度抬高后色度不变
    # 等于稀释饱和度。实测核心像素 (0.295,0.260,0.242) 的 L 从 28.9 拉伸到 86.5、
    # a/b 原样保留后，R/G 从 1.137 掉到 1.055；整幅 M31 的核心 R/G 由 1.49
    # 塌到 1.02，画面变成灰调。按增益缩放 RGB 则与参考实现一致，色彩比例不变。
    gain = L_stretched / np.maximum(L, 1e-6)
    result = image * gain[..., None]
    return np.clip(result, 0, 1).astype(np.float32)


def main():
    p = argparse.ArgumentParser(description='深空图像直方图拉伸')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='masked',
                   choices=['arcsinh', 'mtf', 'masked', 'auto', 'deep',
                            'very_dark', 'emission', 'ghs', 'masked_ghs', 'pi_mtf'],
                   help='拉伸方法 (默认: masked)')
    p.add_argument('--factor', type=float, default=30.0, help='arcsinh factor (默认: 30)')
    p.add_argument('--midtones', type=float, default=0.3, help='MTF midtones (默认: 0.3)')
    p.add_argument('--target-bg', type=float, default=0.08, help='目标背景亮度 (默认: 0.08)')
    p.add_argument('--headroom', type=float, default=0.12, help='高光 Headroom 预留比例 (默认: 0.12)')
    p.add_argument('--sp', type=float, default=0.01, help='GHS 对称点 (默认: 0.01)')
    p.add_argument('--b', type=float, default=8.0, help='GHS 强度因子 (默认: 8.0)')
    p.add_argument('--protect-strength', type=float, default=0.5, help='Masked GHS 核心保护强度 (默认: 0.5)')
    p.add_argument('--lp', type=float, default=None,
                   help='GHS 阴影锚点，lp 以下只做等比缩放 (默认: 0.0 即不启用)')
    p.add_argument('--hp', type=float, default=None,
                   help='GHS 高光锚点，hp 以上只做等比缩放并为亮核留 headroom (默认: 1.0 即不启用)')
    p.add_argument('--luminance-only', action='store_true',
                   help='仅在亮度通道上拉伸')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[拉伸] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    kwargs = {'factor': args.factor}
    if args.method == 'mtf':
        kwargs = {'midtones': args.midtones}
    elif args.method == 'pi_mtf':
        kwargs = {'target_bg': args.target_bg, 'headroom': args.headroom}
    elif args.method == 'masked':
        kwargs = {'target_bg': args.target_bg, 'factor': args.factor}
    elif args.method == 'deep':
        kwargs = {'shadow_pctl': 0.5, 'highlight_pctl': 99.9, 'gamma': 0.4}
    elif args.method == 'very_dark':
        kwargs = {
            'factor': args.factor,
            'gamma': 0.45,
            'target_bg': args.target_bg,
            'headroom': args.headroom,
        }
    elif args.method == 'emission':
        kwargs = {
            'shadow_pctl': 1.0,
            'highlight_pctl': 100.0,
            'gamma': 0.43,
            'target_bg': args.target_bg,
            'headroom': args.headroom,
        }
    elif args.method == 'ghs':
        kwargs = {
            'sp': args.sp,
            'b': args.b,
            'lp': 0.0 if args.lp is None else args.lp,
            'hp': 1.0 if args.hp is None else args.hp,
        }
    elif args.method == 'masked_ghs':
        kwargs = {'sp': args.sp, 'b': args.b, 'protect_strength': args.protect_strength,
                  'target_bg': args.target_bg, 'lp': args.lp, 'hp': args.hp}

    if args.luminance_only:
        result = apply_luminance_stretch(img, method=args.method, **kwargs)
    else:
        stretch_func = {
            'arcsinh': arcsinh_stretch,
            'mtf': mtf_stretch,
            'pi_mtf': pi_mtf_stretch,
            'masked': masked_stretch,
            'auto': auto_stretch,
            'deep': deep_stretch,
            'very_dark': very_dark_stretch,
            'emission': emission_stretch,
            'ghs': ghs_stretch,
            'masked_ghs': masked_ghs_stretch,
        }[args.method]
        result = stretch_func(img, **kwargs)

    imsave(args.output, img_as_ubyte(result))
    print(f"[拉伸] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
