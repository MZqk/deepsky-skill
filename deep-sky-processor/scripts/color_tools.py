#!/usr/bin/env python3
"""
Deep-Sky Color Tools (颜色校准与调色)

原理：
  深空图像的颜色需要经过多步校准和处理。
  首先是背景中性化（让空荡的天空区域呈现中性灰黑），
  然后是白平衡调整（基于参考恒星的颜色），
  最后是艺术性调色（控制色彩饱和度和色调方向）。

方法：
  - background_neutralize: 背景中性化
  - white_balance:        白平衡调整
  - color_saturation:     色彩饱和度增强
  - green_noise_remove:   去除绿色噪声
  - channel_alignment:    RGB通道对齐

用法:
  python color_tools.py <input> <output> [options]
"""

import argparse
import sys
from typing import Optional, Dict, Tuple, Any, Union
import numpy as np
from scipy.ndimage import gaussian_filter, median_filter
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from color_conv import safe_rgb2hsv as rgb2hsv, safe_hsv2rgb as hsv2rgb, safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb


def _background_pixels(rgb, gray, bg_percentile):
    """取亮度最低的一批像素作为背景样本，返回 (N,3) 数组。

    掩膜用 `<=`，并在命中不足时按亮度排序取前 k 个兜底。
    旧实现用严格 `<`，当大量像素恰好等于分位阈值时（DBE 之后很常见）
    掩膜会变成空集，导致整步被静默跳过。
    """
    threshold = float(np.percentile(gray, bg_percentile))
    mask = gray <= threshold
    count = int(np.count_nonzero(mask))
    if count >= 100:
        return rgb[mask]

    total = gray.size
    k = min(total, max(100, int(total * bg_percentile / 100.0)))
    idx = np.argpartition(gray.ravel(), k - 1)[:k]
    return rgb.reshape(-1, 3)[idx]


def background_neutralize(image, bg_percentile=30, sample_radius=20):
    """
    背景中性化：只扣除**通道之间**的背景差异，让背景呈中性。

    为什么不是"各通道各自减到 0"：
      DBE 阶段（`normalize_background_subtracted`）已经给背景留了 pedestal，
      若这里再把每个通道的背景中值整体减掉，等于重复扣黑 —— 实测会把 90%
      的画面压成纯 0，随后所有"背景亮度分位"类判据全部失效。
      因此这里只去掉通道差异，保留共同黑位（取各通道最小背景）。

    为什么是加性而非乘性：
      乘性增益 `bg_mean / max(bg_ch, floor)` 在近零通道上会爆炸
      （B≈0 时可算出 ~96× 增益），且该增益被无差别地乘到天体本体上，
      把暖核染成冷核。加性平移不可能放大任何通道。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return source

    rgb = source[..., :3]
    gray = rgb.mean(axis=2)
    flat = _background_pixels(rgb, gray, bg_percentile)

    black_points = np.median(flat, axis=0).astype(np.float64)
    # 只保留通道差异，共同黑位（pedestal）原样保留
    common = float(np.min(black_points))
    offsets = black_points - common

    if float(np.max(offsets)) <= 1e-9:
        print("[背景中性化-加法] 背景已中性，无需调整")
        return source

    corrected = np.clip(rgb.astype(np.float64) - offsets, 0, None)
    result = corrected.astype(np.float32)
    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])

    print(f"[背景中性化-加法] offsets={offsets.round(6).tolist()} "
          f"(保留共同黑位 {common:.6f})")
    return result


def lock_background_neutrality(
    image: np.ndarray,
    bg_percentile: float = 25.0,
    upper_percentile: float = 50.0,
    star_mask: Optional[np.ndarray] = None,
    exclusion_mask: Optional[np.ndarray] = None,
    target_bg: Optional[float] = None,
    max_iters: int = 2,
    cast_tolerance: float = 0.05,
    return_report: bool = False,
) -> Union[np.ndarray, Tuple[np.ndarray, Dict[str, Any]]]:
    """
    深空暗背景中性灰锁定 (Background Neutralization Lock)。
    精准消除通道间底电平色偏，自适应闭环对齐质量门禁，严格收敛 BACKGROUND_COLOR_CAST 指标至健康区间。

    参数:
      image: (H, W, 3) 浮点图像 [0, 1]
      bg_percentile: 背景采样下界分位数 (默认 25.0)
      upper_percentile: 平滑过渡上界分位数 (默认 50.0)
      star_mask: 可选星点掩膜，保护恒星
      exclusion_mask: 可选天体主体排除掩膜，避免星云/星系采样
      target_bg: 可选目标背景中值，默认取 R/G/B 三通道当前中位数的均值
      max_iters: 最大微调闭环迭代次数 (默认 2)
      cast_tolerance: 闭环收敛目标色偏容差 (默认 0.05)
      return_report: 是否返回量化分析字典
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        report = {"applied": False, "reason": "not_rgb"}
        return (source, report) if return_report else source

    alpha = source[..., 3:] if source.shape[2] > 3 else None
    current_rgb = source[..., :3].copy()

    total_offsets = np.zeros(3, dtype=np.float32)
    initial_medians = None
    final_medians = None
    initial_cast = None
    final_cast = None
    iterations_run = 0
    t_low_val = 0.0
    t_high_val = 0.0
    bg_samples_count = 0

    for iteration in range(max(1, max_iters)):
        alive = np.min(current_rgb, axis=2) > 1e-6
        if np.count_nonzero(alive) < 100:
            if iteration == 0:
                report = {"applied": False, "reason": "insufficient_alive_pixels"}
                return (source, report) if return_report else source
            break

        valid_mask = alive.copy()
        if exclusion_mask is not None:
            em = np.asarray(exclusion_mask, dtype=bool)
            if em.shape == valid_mask.shape:
                valid_mask &= ~em

        if star_mask is not None:
            sm = np.asarray(star_mask, dtype=np.float32)
            if sm.ndim == 3:
                sm = np.mean(sm, axis=2)
            valid_mask &= (sm < 0.2)

        if np.count_nonzero(valid_mask) < 100:
            valid_mask = alive

        gray = current_rgb.mean(axis=2)
        pool = gray[valid_mask]

        t_low = float(np.percentile(pool, bg_percentile))
        t_high = float(np.percentile(pool, max(upper_percentile, bg_percentile + 5.0)))
        if iteration == 0:
            t_low_val = t_low
            t_high_val = t_high

        bg_samples = valid_mask & (gray <= t_low)
        if np.count_nonzero(bg_samples) < 100:
            bg_samples = valid_mask & (gray <= t_high)
        bg_samples_count = int(np.count_nonzero(bg_samples))

        med_r = float(np.median(current_rgb[..., 0][bg_samples]))
        med_g = float(np.median(current_rgb[..., 1][bg_samples]))
        med_b = float(np.median(current_rgb[..., 2][bg_samples]))

        curr_r_g = med_r / max(med_g, 1e-9)
        curr_b_g = med_b / max(med_g, 1e-9)
        curr_cast = max(abs(curr_r_g - 1.0), abs(curr_b_g - 1.0))

        if iteration == 0:
            initial_medians = [med_r, med_g, med_b]
            initial_cast = curr_cast

        if target_bg is not None:
            target_val = float(target_bg)
        else:
            target_val = float((med_r + med_g + med_b) / 3.0)

        offsets = np.array([target_val - med_r, target_val - med_g, target_val - med_b], dtype=np.float32)
        total_offsets += offsets

        span = max(t_high - t_low, 1e-6)
        t = np.clip((gray - t_low) / span, 0.0, 1.0)
        smooth_w = 1.0 - (3.0 * t * t - 2.0 * t * t * t)
        smooth_w = smooth_w[..., None]

        if star_mask is not None:
            sm_clip = np.clip(np.asarray(star_mask, dtype=np.float32), 0.0, 1.0)
            if sm_clip.ndim == 2:
                sm_clip = sm_clip[..., None]
            smooth_w = smooth_w * (1.0 - sm_clip)

        current_rgb = np.clip(current_rgb + offsets * smooth_w, 0.0, 1.0)

        p1_check = float(np.percentile(current_rgb[alive], 1.0))
        if p1_check <= 1e-4:
            pedestal_boost = float(1.5e-4 - p1_check)
            current_rgb = np.clip(current_rgb + pedestal_boost * smooth_w, 0.0, 1.0)

        iterations_run += 1

        # 与 quality_metrics 严格一致的闭环背景色偏核验
        gray_eval = current_rgb.mean(axis=2)
        alive_eval = np.min(current_rgb, axis=2) > 0
        if np.count_nonzero(alive_eval) >= 100:
            pool_eval = gray_eval[alive_eval]
            p_eval = float(np.percentile(pool_eval, bg_percentile))
            bg_eval = alive_eval & (gray_eval <= p_eval)
            if np.count_nonzero(bg_eval) >= 100:
                eval_r = float(np.median(current_rgb[..., 0][bg_eval]))
                eval_g = float(np.median(current_rgb[..., 1][bg_eval]))
                eval_b = float(np.median(current_rgb[..., 2][bg_eval]))
                final_medians = [eval_r, eval_g, eval_b]
                rg = eval_r / max(eval_g, 1e-9)
                bg = eval_b / max(eval_g, 1e-9)
                final_cast = max(abs(rg - 1.0), abs(bg - 1.0))
                if final_cast <= cast_tolerance:
                    break
        else:
            final_medians = [med_r, med_g, med_b]
            final_cast = curr_cast
            break

    if alpha is not None:
        current_rgb = np.dstack([current_rgb, alpha])

    report = {
        "applied": True,
        "iterations_run": iterations_run,
        "bg_samples_count": bg_samples_count,
        "t_low": round(t_low_val, 6),
        "t_high": round(t_high_val, 6),
        "pre_medians": [round(x, 6) for x in (initial_medians or [0, 0, 0])],
        "post_medians": [round(x, 6) for x in (final_medians or [0, 0, 0])],
        "offsets": total_offsets.round(6).tolist(),
        "pre_cast_magnitude": round(initial_cast if initial_cast is not None else 0.0, 4),
        "post_cast_magnitude": round(final_cast if final_cast is not None else 0.0, 4),
    }

    return (current_rgb.astype(np.float32), report) if return_report else current_rgb.astype(np.float32)


def _reference_star_mask(rgb, sat_limit=0.995):
    """挑出可用作白平衡参考的恒星像素；样本不可用时返回 None。"""
    from skimage.morphology import disk, white_tophat

    gray = rgb.mean(axis=2)
    # 小尺度 top-hat：压制星系/星云等延展结构，只保留紧凑亮源
    response = white_tophat(gray, disk(3))
    positive = response[response > 0]
    if positive.size == 0:
        return None

    median = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median))) * 1.4826
    threshold = max(
        float(np.percentile(positive, 99.5)),
        median + 4.0 * max(mad, 1e-9),
    )
    mask = response >= threshold

    peak = rgb.max(axis=2)
    mask &= peak < sat_limit        # 排除过曝星核（已失去颜色信息）
    mask &= peak > 1e-6

    # 排除落在延展主体（星系盘/星云）上的像素
    body = gaussian_filter(gray, sigma=15.0)
    mask &= body <= float(np.percentile(body, 75))
    return mask


def white_balance_from_stars(image, method='stars', max_gain=1.25,
                             min_star_samples=50, strength=0.35):
    """
    基于参考恒星的白平衡（有界、保守、亮度中性）。

    与旧实现的关键差别：
      - 旧版名为 from_stars，实际用**全图均值**做 gray-world，并硬编码
        R×0.9 / B×1.1 的偏置。深空画面由星系积分色主导而非恒星主导，
        gray-world 假设不成立，那两个偏置还会把画面整体推冷。
      - 新版真正采样恒星像素（见 _reference_star_mask），取样本 RGB 中值，
        增益取"几何平均 / 通道值"，使参考星校正后三通道相等。

    strength 默认 0.35（保守）而不是 1.0：
      把参考星强行拉成中性只在"星应当是白的"这一前提成立时才对。低银纬视场
      （如 M31，受银河尘埃红化）或相机光谱响应偏离 CIE 时该前提不成立 ——
      实测把 1141 颗场星拉中性会把星系盘从 R/G≈1.55 压到 ≈1.01，
      抹掉天体本身的暖色。因此默认只施加 35% 的校正量，
      既能去掉明显的仪器色偏，又不会改写天体固有颜色。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return source

    rgb = source[..., :3]

    def _apply(raw_gains, label):
        gains = 1.0 + float(strength) * (raw_gains - 1.0)
        print(f"[白平衡-{label}] raw={raw_gains.round(3).tolist()} "
              f"strength={float(strength):.2f} → gains={gains.round(3).tolist()}")
        return _apply_gains(source, rgb, gains)

    if method == 'percentile':
        p = np.percentile(rgb.reshape(-1, 3), 95.0, axis=0).astype(np.float64)
        if float(np.min(p)) <= 1e-6:
            print("[白平衡-percentile] 存在近零通道，跳过")
            return source
        log_p = np.log(p)
        raw = np.exp(np.clip(log_p.mean() - log_p,
                             -np.log(max_gain), np.log(max_gain)))
        return _apply(raw, "percentile")

    if method not in ('stars', 'gray_world'):
        return source

    mask = _reference_star_mask(rgb)
    n_samples = 0 if mask is None else int(np.count_nonzero(mask))
    if n_samples < min_star_samples:
        print(f"[白平衡-星采样] 星样本不足 (n={n_samples}<{min_star_samples})，"
              f"跳过（保留加法中性化结果）")
        return source

    star_rgb = np.median(rgb[mask], axis=0).astype(np.float64)
    if float(np.min(star_rgb)) <= 1e-6:
        print("[白平衡-星采样] 参考星存在近零通道，跳过")
        return source

    # 原始增益 = 几何平均 / 通道值，使参考星三通道在校正后相等（亮度不变）
    log_star = np.log(star_rgb)
    raw_gains = np.exp(np.clip(log_star.mean() - log_star,
                               -np.log(max_gain), np.log(max_gain)))
    print(f"[白平衡-星采样] n={n_samples} star_rgb={star_rgb.round(5).tolist()}")
    return _apply(raw_gains, "星采样")


def _apply_gains(source, rgb, gains):
    """按增益缩放 RGB 并夹回 [0,1]（保持与旧版一致的输出契约）。"""
    result = np.clip(rgb.astype(np.float64) * gains, 0, 1).astype(np.float32)
    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])
    return result


def enhance_saturation(image, factor=1.5, protect_background=True,
                       bg_protection_percentile=20):
    """
    增强色彩饱和度 —— **保比例**色度缩放，不是 HSV 的 S 乘法。

    公式：以像素均值为轴缩放色度差，`new = mean + (x - mean) * k`。
    未越界裁切时逐像素均值严格不变，弱通道只按偏离均值的比例缩放，不会塌到 0。

    **为什么不用 HSV 的 S 乘法**（旧实现）：它保持 max 通道不变、把 (max−min)
    拉开，当 G≠B 时两个非最大通道被**不等比例**压向 0。实测受控输入
    `[0.25,0.10,0.06]` 经 ×1.32 后 B 直接归零（B/G 保留率 **0%**），
    `[0.50,0.15,0.09]` 同样归零 —— 对发射星云即系统性破坏 OIII。
    改用保比例公式后同样输入 B/G 保留 0.402 / 0.335。

    protect_background: 保护暗区不被着色（保持背景纯净）。用三次 Hermite
    (smoothstep) 在 V（= max 通道）的分位区间上做平滑滚降，避免暗部硬阶跃。
    """
    source = np.asarray(image, dtype=np.float32)
    source_rgb = source[..., :3]
    # V 就是 max 通道，直接取即可 —— 不再需要 RGB↔HSV 往返
    v_channel = source_rgb.max(axis=2)

    # 生成背景保护权重（与旧实现同参数、同 Hermite 曲线）
    if protect_background:
        p_low = float(np.percentile(v_channel, bg_protection_percentile))
        p_high = float(np.percentile(v_channel, min(95.0, bg_protection_percentile + 30.0)))
        span = max(p_high - p_low, 1e-5)
        t = np.clip((v_channel - p_low) / span, 0.0, 1.0)
        smooth_w = 3.0 * t * t - 2.0 * t * t * t
        local_factor = 1.0 + (float(factor) - 1.0) * smooth_w
    else:
        local_factor = np.full(v_channel.shape, float(factor), dtype=np.float32)

    neutral = source_rgb.mean(axis=2, keepdims=True)
    deviation = source_rgb - neutral

    # 每像素色度上限：保证缩放后不越出 [0,1]，从而均值**严格守恒**。
    # 这替代了旧实现末尾的"底电平守护"（那是对 HSV 塌缩的补丁，会均匀抬亮像素、
    # 破坏均值守恒）。到达色域边缘的像素本来也无法再提升色度。
    safe = np.maximum(np.abs(deviation), 1e-9)
    headroom = np.where(deviation > 0, (1.0 - neutral) / safe, neutral / safe)
    k_max = np.min(headroom, axis=2)
    k_eff = np.minimum(local_factor, np.maximum(k_max, 0.0))

    result = np.clip(neutral + deviation * k_eff[..., None], 0.0, 1.0)

    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def remove_green_noise(image, strength=0.3):
    """
    去除绿色噪声 (SCNR - Subtract Chrominance Noise Reduction)。
    原理：绿色噪声来源——拜耳阵列中有两个绿色像素（RGGB），
    导致绿色通道对噪声更敏感。去除方法是在 Lab 色彩空间中将
    a 通道（绿→品红方向）中偏绿的部分向中性色推移。

    修正按亮度加权：Lab 的 a/b 在近零亮度处极不稳定，对线性阶段很暗的
    背景施加色度修正会把像素推成负值 —— 实测在线性图上直接跑会把 45%
    的画面变成纯 0。SCNR 的意义本来也只在中高亮区。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.max() <= 0:
        return source.copy()

    lab = rgb2lab(source)
    luminance = lab[..., 0]
    reference = float(np.percentile(luminance, 99.9))
    if reference <= 1e-6:
        return source.copy()

    weight = np.clip(luminance / (0.05 * reference), 0.0, 1.0)
    a_channel = lab[..., 1]

    # 只作用于偏绿部分 (a < 0)，且随亮度渐入
    green_mask = (a_channel < 0).astype(np.float32) * weight
    lab[..., 1] = a_channel * (1.0 - green_mask * float(strength))

    result = lab2rgb(lab)
    return np.clip(result, 0, 1)


def channel_alignment(image, shift_b=0, shift_r=0):
    """
    RGB通道对齐。
    原理：大气色散或光学色差可能导致R/G/B通道间有微小偏移。
    这里做简单的亚像素平移校正。
    """
    from scipy.ndimage import shift as nd_shift
    result = image.copy()
    if shift_b != 0:
        result[..., 2] = nd_shift(image[..., 2], (shift_b, shift_b), order=1)
    if shift_r != 0:
        result[..., 0] = nd_shift(image[..., 0], (shift_r, shift_r), order=1)
    return np.clip(result, 0, 1)


def auto_color_calibrate(image, return_report=False):
    """
    自动颜色校准：加性背景中性化 + 有界星采样白平衡 + 绿色噪声去除。
    适用于无法精确测光的 JPG/PNG 深空图像。

    顺序很重要：先做加性逐通道黑点把背景压到 0 附近，
    再做全局白平衡 —— 此时增益作用在已中性的背景上，不会重新引入背景色偏。
    """
    print("[自动色彩校准] 开始（加法黑点 + 星采样白平衡）...")
    result = background_neutralize(image, bg_percentile=25)
    result = white_balance_from_stars(result, method='stars')
    result = remove_green_noise(result, strength=0.25)
    result = np.clip(result, 0, 1)
    if return_report:
        return result, {"mode": "additive_blackpoint_plus_star_white_balance"}
    return result


def emission_nebula_calibrate(image, background_percentile=1.0,
                              star_balance_strength=0.65,
                              oiii_blue_injection=0.0,
                              return_report=False):
    """
    发射星云颜色校准。

    仅减去每通道暗部基线，再用未饱和亮星的软蒙版校正星色。
    不对整幅图应用灰度世界增益，因此保留 Hα 主导的真实红色结构。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return (source, {}) if return_report else source

    flat = source[..., :3].reshape(-1, 3)
    raw_black_points = np.percentile(flat, background_percentile, axis=0)
    black_points = raw_black_points.copy()
    # 限制 B 通道背景剪除黑点，防止极暗的 B 通道被过度减除截断为 0
    black_points[2] = min(black_points[2], black_points[1])
    # 当三通道黑点量级极小（如线性 FITS 数据的真实底电平在 0.005 以下），
    # 通道间轻微噪声起伏会导致扣黑严重不平衡、底电平被人工制造出严重红偏。
    # 此时应约束各通道扣黑差值，避免弱通道（G/B）被过度剪裁。
    if float(np.max(black_points)) < 0.005:
        min_bp = float(np.min(black_points))
        black_points = np.minimum(black_points, min_bp + 0.00015)
    result = np.clip(source[..., :3] - black_points, 0, None)

    # 默认不从 G 人为构造 B。仅在用户明确知道输入是双窄带映射时，
    # 才允许通过参数做有限的 OIII 蓝通道注入。
    if oiii_blue_injection > 0:
        injection = float(np.clip(oiii_blue_injection, 0.0, 1.0))
        result[..., 2] = np.maximum(
            result[..., 2],
            result[..., 1] * injection,
        )

    luminance = (
        0.299 * result[..., 0]
        + 0.587 * result[..., 1]
        + 0.114 * result[..., 2]
    )
    low = np.percentile(luminance, 99.2)
    high = np.percentile(luminance, 99.85)
    star_samples = (luminance > low) & (luminance < high)
    gains = np.ones(3, dtype=np.float32)
    star_sample_count = int(np.count_nonzero(star_samples))
    if star_sample_count >= 100:
        star_rgb = np.median(result[star_samples], axis=0)
        target = float(np.exp(np.mean(np.log(np.maximum(star_rgb, 1e-9)))))
        gains = np.clip(target / np.maximum(star_rgb, 1e-9), 0.65, 1.55)
        star_mask = gaussian_filter(star_samples.astype(np.float32), sigma=2.0)
        star_mask = np.clip(star_mask[..., None] * star_balance_strength, 0, 1)
        result *= 1.0 + (gains - 1.0) * star_mask
        print(
            f"[发射星云校色] black={black_points.round(6).tolist()} "
            f"star_gains={gains.round(3).tolist()}"
        )
    else:
        print(f"[发射星云校色] black={black_points.round(6).tolist()} 星样本不足")

    calibrated = np.clip(result, 0, 1).astype(np.float32)
    report = {
        "black_points": black_points.astype(float).tolist(),
        "star_gains": gains.astype(float).tolist(),
        "star_sample_count": star_sample_count,
        "star_gains_scope": "star_mask_only",
        "oiii_blue_injection": float(oiii_blue_injection),
    }
    return (calibrated, report) if return_report else calibrated


def stabilize_emission_channels(image, collapse_ratio=0.02,
                                max_gain=1.35, strength=0.6,
                                target_ratios=None):
    """
    对发射星云信号区做有边界的通道恢复。

    默认只修复接近数值塌缩的通道。target_ratios 仅供显式覆盖，
    例如 {"r_over_g": 1.8, "r_over_b": 2.2}；不会自动把 Hα
    主导图像强制白平衡。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return source, {"applied": False, "reason": "not_rgb"}

    rgb = source[..., :3]
    luminance = (
        0.2126 * rgb[..., 0]
        + 0.7152 * rgb[..., 1]
        + 0.0722 * rgb[..., 2]
    )
    low = float(np.percentile(luminance, 55.0))
    high = float(np.percentile(luminance, 99.2))
    signal_mask = (luminance > low) & (luminance < high)
    if np.count_nonzero(signal_mask) < 100:
        return source, {"applied": False, "reason": "insufficient_signal"}

    background_mask = luminance <= np.percentile(luminance, 35.0)
    background_rgb = np.median(rgb[background_mask], axis=0)
    signal_rgb = np.clip(rgb[signal_mask] - background_rgb, 0, None)
    channel_signal = np.percentile(signal_rgb, 95.0, axis=0)
    strongest = max(float(np.max(channel_signal)), 1e-9)
    gains = np.ones(3, dtype=np.float32)

    for index in range(3):
        relative = float(channel_signal[index]) / strongest
        if relative < float(collapse_ratio):
            needed = (strongest * float(collapse_ratio)) / max(
                float(channel_signal[index]),
                1e-9,
            )
            gains[index] = min(float(max_gain), needed)

    if target_ratios:
        red = max(float(channel_signal[0]), 1e-9)
        for key, index in (("r_over_g", 1), ("r_over_b", 2)):
            target = target_ratios.get(key)
            if target and float(target) > 0:
                desired = red / float(target)
                needed = desired / max(float(channel_signal[index]), 1e-9)
                gains[index] = max(
                    gains[index],
                    min(float(max_gain), max(1.0, needed)),
                )

    if np.allclose(gains, 1.0):
        return source, {
            "applied": False,
            "reason": "channels_not_collapsed",
            "channel_signal": channel_signal.astype(float).tolist(),
            "background_rgb": background_rgb.astype(float).tolist(),
            "gains": gains.astype(float).tolist(),
        }

    soft_mask = gaussian_filter(signal_mask.astype(np.float32), sigma=3.0)
    soft_mask = np.clip(soft_mask * float(strength), 0, 1)[..., None]
    corrected = rgb * (1.0 + (gains - 1.0) * soft_mask)
    peak = np.max(corrected, axis=2, keepdims=True)
    corrected = corrected / np.maximum(peak, 1.0)
    if source.shape[2] > 3:
        corrected = np.dstack([corrected, source[..., 3:]])

    return np.clip(corrected, 0, 1).astype(np.float32), {
        "applied": True,
        "reason": "bounded_signal_recovery",
        "channel_signal": channel_signal.astype(float).tolist(),
        "background_rgb": background_rgb.astype(float).tolist(),
        "gains": gains.astype(float).tolist(),
        "collapse_ratio": float(collapse_ratio),
        "max_gain": float(max_gain),
        "target_ratios": target_ratios,
    }


def main():
    p = argparse.ArgumentParser(description='深空图像色彩工具')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='auto',
                   choices=['auto', 'emission', 'background', 'white_balance',
                            'saturation', 'green_noise'],
                   help='色彩处理方法 (默认: auto)')
    p.add_argument('--factor', type=float, default=1.5, help='饱和度因子 (默认: 1.5)')
    p.add_argument('--strength', type=float, default=0.3, help='绿噪去除强度 (默认: 0.3)')
    p.add_argument('--light', action='store_true', help='轻度处理')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[色彩] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    if args.light:
        args.factor = min(args.factor, 1.2)
        args.strength = min(args.strength, 0.15)

    if args.method == 'auto':
        result = auto_color_calibrate(img)
    elif args.method == 'emission':
        result = emission_nebula_calibrate(img)
    elif args.method == 'background':
        result = background_neutralize(img)
    elif args.method == 'white_balance':
        result = white_balance_from_stars(img, method='gray_world')
    elif args.method == 'saturation':
        result = enhance_saturation(img, factor=args.factor)
    elif args.method == 'green_noise':
        result = remove_green_noise(img, strength=args.strength)

    imsave(args.output, img_as_ubyte(result))
    print(f"[色彩] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
