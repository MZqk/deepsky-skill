#!/usr/bin/env python3
"""
Deep-Sky Detail Enhancement (星云细节增强)

原理：
  拉伸后的深空图像中，星云的暗弱纹理和明亮核心的动态范围仍然很大。
  增强模块通过多尺度动态范围压缩和局部对比度增强，
  让星云的细丝结构、暗纹和明亮核心的细节同时可见。

方法：
  - hdr_compress:  HDR 动态范围压缩（多尺度）
  - clahe:        自适应直方图均衡化 (CLAHE)
  - curves:       S 曲线对比度增强
  - local_contrast: 局部对比度增强

用法:
  python enhance.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from skimage.exposure import equalize_adapthist


def hdr_multiscale_compress(image, layers=3, strength=0.5):
    """
    多尺度 HDR 动态范围压缩。
    原理：
      1. 将图像分解为多个尺度（高频层 = 原图 - 模糊版）
      2. 对高频层施加压缩（减弱极高对比度）
      3. 保留低频层（整体亮度分布）
      
    效果：明亮核心（如星系核、星云中心）的亮度被压缩，
    暗弱外围（如星系悬臂、星云边缘）的细节被提升。
    """
    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c] = _hdr_compress_channel(image[..., c], layers, strength)
        return np.clip(result, 0, 1)
    else:
        return _hdr_compress_channel(image, layers, strength)


def protected_hdr_compress(image, strength=0.5, knee_percentile=85.0):
    """
    亮度域高光压缩。

    仅压缩 knee 以上的亮核/亮星，阴影和中间调保持不变；RGB 使用
    同一个逐像素亮度增益，因此不会产生逐通道 HDR 的色相漂移。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    rgb = source[..., :3] if is_color else source
    if is_color:
        luminance = (
            0.2126 * rgb[..., 0]
            + 0.7152 * rgb[..., 1]
            + 0.0722 * rgb[..., 2]
        )
    else:
        luminance = rgb

    knee = float(np.percentile(luminance, knee_percentile))
    knee = float(np.clip(knee, 0.05, 0.9))
    span = max(1.0 - knee, 1e-6)
    normalized = np.clip((luminance - knee) / span, 0, 1)
    compressed = normalized / (
        1.0 + float(strength) * normalized
    )
    target_luminance = np.where(
        luminance > knee,
        knee + compressed * span,
        luminance,
    )

    if is_color:
        gain = target_luminance / np.maximum(luminance, 1e-8)
        result = rgb * gain[..., None]
        if source.shape[2] > 3:
            result = np.dstack([result, source[..., 3:]])
    else:
        result = target_luminance

    report = {
        "strength": float(strength),
        "knee": knee,
        "p99_before": float(np.percentile(luminance, 99.0)),
        "p99_after": float(np.percentile(target_luminance, 99.0)),
        "median_before": float(np.median(luminance)),
        "median_after": float(np.median(target_luminance)),
        "affected_ratio": float(np.mean(luminance > knee)),
    }
    return np.clip(result, 0, 1).astype(np.float32), report


ENHANCE_MODES = ("both", "hdr_only", "clahe_only", "none")

# 星云区纹理 / 背景噪声地板 的最小比值。低于它说明星云区也没比噪声多出结构，
# 此时 CLAHE 只会把噪声放大成团（实测 5.4×），不会揭示任何东西。
# 实测分离度：有伪影的输入 1.24，干净的输入 2.32~5.26 → 取几何中点 1.7。
CLAHE_MIN_TEXTURE_SNR = 1.7

# CLAHE 允许的**星云区高频能量放大倍数**上限。超过即判定为"过度增强"并跳过。
#
# 为什么需要实测而不是调参：`equalize_adapthist` 的 `clip_limit` 在**窄直方图**上会
# 饱和。实测某极暗发射星云（span=0.39）上，clip_limit 从 0.0003 到 0.003 给出
# **逐位相同**的输出（HF 放大恒为 1.85×、p75 恒为 0.440），只有到 0.006 才变化。
# 也就是说**无法靠调小 clip_limit 来减弱效果**，只能二选一 —— 因此需要一条实测判据。
#
# 该例实测放大 1.85×，而跳过 CLAHE 后成片 p75 由 0.381 降到 0.173（−55%）、
# HF 比由 6.74 降到 3.62（−46%），大幅贴近参考成片。阈值取 1.5 略低于该例，
# 保守起见只拦"明显过度"的情形。**该阈值目前只在单一案例上标定过，需随样本积累复核。**
CLAHE_MAX_HF_AMPLIFICATION = 1.5


def _nebula_hf_energy(image, sigma=2.0, nebula_percentile=55.0):
    """星云主体区（亮度分位以上）的**绝对**高频能量。"""
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[..., :3]
        lum = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1]
               + 0.0722 * arr[..., 2])
    else:
        lum = arr
    mask = lum > float(np.percentile(lum, nebula_percentile))
    if not mask.any():
        return None
    hf = np.abs(lum - gaussian_filter(lum, sigma=float(sigma)))
    return float(hf[mask].mean())


def preview_clahe_hf_amplification(image, clip_limit, kernel_size,
                                  max_side=640, **clahe_kwargs):
    """在下采样预览上实测 CLAHE 会把星云区高频能量放大多少倍。

    返回 None 表示无法判定（无信号区或能量为零）。预览用 `max_side` 限幅以控制开销。
    """
    from skimage.transform import resize

    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[..., :3]
    h, w = arr.shape[:2]
    if max(h, w) > int(max_side):
        scale = float(max_side) / max(h, w)
        target = (max(1, int(round(h * scale))), max(1, int(round(w * scale))))
        arr = resize(arr, target + (arr.shape[2],) if arr.ndim == 3 else target,
                     preserve_range=True, anti_aliasing=True).astype(np.float32)

    before = _nebula_hf_energy(arr)
    if before is None or before <= 1e-9:
        return None
    after_img = apply_clahe(arr, clip_limit=clip_limit,
                            kernel_size=kernel_size, **clahe_kwargs)
    after = _nebula_hf_energy(after_img)
    if after is None:
        return None
    return float(after / before)


def measure_texture_snr(gray, sigma=2.0, nebula_pctl=55.0, bg_pctl=20.0):
    """星云区高通能量 / 背景区高通能量 —— 用来判断 CLAHE 是"揭示结构"还是"放大噪声"。

    背景区（低分位）只有噪声、没有结构，所以它就是**噪声地板**。若星云区的
    高通能量与背景地板相当（比值 ≈1），说明星云区也没有可揭示的纹理 ——
    CLAHE 的局部直方图均衡只会把这个噪声放大成可见的蜂窝状斑块。

    实测（真实母版 Phase 6 输入）：伪影案例比值 1.24，干净案例 2.32~5.26。

    返回 None 表示无法判定（区域为空或噪声地板为 0），调用方应视为"不触发守卫"。
    """
    g = np.asarray(gray, dtype=np.float32)
    if g.size == 0:
        return None
    hf = np.abs(g - gaussian_filter(g, sigma=float(sigma)))
    nebula = g > np.percentile(g, float(nebula_pctl))
    background = g <= np.percentile(g, float(bg_pctl))
    if not nebula.any() or not background.any():
        return None
    floor = float(hf[background].mean())
    if floor <= 1e-12:
        return None
    return float(hf[nebula].mean()) / floor


def decide_enhance_mode(signals, target_type=None):
    """决定 Phase 6 跑哪些非线性细节增强。

    外部权威经验：「HDRMT 与 LHE 同时使用太过头，二选一」。本地 Phase 6 此前是
    无条件串联 HDR + CLAHE。本函数把它变成条件触发，但**保守默认仍是 both**，
    即大多数情况下行为与历史一致，只在测到明确信号时才跳过一个：

      - 有高光过曝风险（highlight_clip 或 p99 很高）→ HDR 必要；
        若背景本身已很亮（p50 高），再叠 CLAHE 会放大噪声 → 只跑 HDR。
      - 完全没有高光风险（p99 < 0.80）→ HDR 近似空操作 → 只跑 CLAHE。
      - 全局层次不足（span = p99 − p50 偏小）→ 优先局部对比 → 只跑 CLAHE。
      - 其余 → both（默认兜底 = 历史行为）。

    **噪声守卫**（signals 含 `texture_snr` 时启用）：若星云区的高通能量并不比
    背景噪声地板高出多少（`texture_snr < CLAHE_MIN_TEXTURE_SNR`），说明这张图
    没有可揭示的纹理，CLAHE 只会把噪声放大成蜂窝状斑块 —— 此时跳过 CLAHE。

    实测：某极暗母版的 Phase 6 输入 `texture_snr = 1.24`，跑 CLAHE 后星云区高通
    能量被放大 **5.4×**，成片出现明显灰色蜂窝斑块，必须靠 `enhance_mode=none`
    规避。注意**光看 span 分不出来**：同一批数据里 `span = 0.2187` 的几张是有
    结构的、跑 CLAHE 无害，而 `span = 0.0814` 的才是噪声主导。判据必须是 SNR。

    signals: {"highlight_clip_ratio", "p50", "p99", "span", 可选 "texture_snr"}
    """
    # 关键信号缺失时回落 both（= 历史行为），而不是静默跳过一个阶段。
    if "p50" not in signals or "p99" not in signals:
        return "both"

    hl_clip = float(signals.get("highlight_clip_ratio", 0.0))
    p50 = float(signals.get("p50", 0.0))
    p99 = float(signals.get("p99", 0.0))
    span = float(signals.get("span", max(p99 - p50, 0.0)))

    if hl_clip >= 0.002 or p99 >= 0.95:
        mode = "hdr_only" if p50 >= 0.12 else "both"
    elif p99 < 0.80:
        mode = "clahe_only"
    elif span < 0.22:
        mode = "clahe_only"
    else:
        mode = "both"

    # 噪声守卫：只在明确噪声主导时削掉 CLAHE，HDR 该跑还跑。
    # 注意 mode 是**模式名**，不能拿 "clahe" in mode 做子串判断（"clahe" 不是
    # "both" 的子串），必须按语义列集合。
    texture_snr = signals.get("texture_snr")
    if (texture_snr is not None and np.isfinite(float(texture_snr))
            and float(texture_snr) < CLAHE_MIN_TEXTURE_SNR
            and mode in ("both", "clahe_only")):
        mode = "hdr_only" if mode == "both" else "none"
    return mode


def _hdr_compress_channel(channel, layers, strength):
    """
    多尺度 HDR 动态范围压缩（修正版拉普拉斯金字塔）。

    原理：
      1. 构建高斯金字塔：逐层模糊
      2. 构建拉普拉斯金字塔：每层 = 当前层 - 下一层模糊版（细节层）
      3. 对过亮的细节层做温和压缩（soft compression）
      4. 从最低频开始，逐层加回压缩后的细节，重建图像

    关键修正（相比旧版）：
      - 重建阶段不再对结果做高斯模糊（旧版 sigma=32 的模糊是严重 bug）
      - 使用 soft compression 代替 aggressive division，保留暗部细节
    """
    # 构建高斯金字塔
    gaussian_layers = [channel.copy()]
    current = channel.copy()
    for i in range(layers):
        sigma = 2.0 ** (i + 2)
        current = gaussian_filter(current, sigma=sigma)
        gaussian_layers.append(current)

    # 构建拉普拉斯金字塔（高频细节层）
    laplacian_layers = []
    for i in range(layers):
        detail = gaussian_layers[i] - gaussian_layers[i + 1]
        # Soft compression: 只对极端对比度做温和压缩
        # 小 detail 几乎不变，大 detail 被压缩
        # 公式: detail / (1 + strength * |detail|)
        compressed = detail / (1.0 + strength * np.abs(detail))
        laplacian_layers.append(compressed)

    # 重建：从最低频开始，逐层加回压缩后的细节
    result = gaussian_layers[-1]
    for detail in reversed(laplacian_layers):
        result = result + detail

    return np.clip(result, 0, 1)


def _clahe_rolloff(L, highlight_rolloff=True, shadow_rolloff=True,
                   mask_gamma=None, mask_low_pctl=90.0, mask_high_pctl=99.9):
    """CLAHE 的作用权重掩版（0 = 完全不动，1 = 满强度）。

    两部分**相乘**：

    1. 固定的分段线性斜坡（历史行为）：高光 >0.65 渐弱、>0.95 归零；
       暗部 <0.16 渐弱、<0.06 归零。
    2. 可选的**自适应高光渐弱掩版**（mask_gamma 非 None 时启用）：
       用图像自身的分位定义渐弱窗口 [p_low, p_high]，
       ``taper = clip((hi - L)/(hi - lo))``，再取 ``mask_gamma`` 次幂。
       **在 p_low 以下 taper 恒为 1**，所以绝大多数像素（背景与星云主体）完全不受
       影响；只有最亮的 p_low~p_high 区间被逐步保护。

    为什么必须是「下降型 taper」而不是「上升型幂版」：上升型 ``((L-lo)/(hi-lo))**g``
    会在整个中低亮度区间给出接近 0 的权重，等于把 CLAHE 全图关掉——实测某星云
    占满画幅的图 median 掉了 25%，远超保守范围。下降型只保护亮端，实测 median
    几乎不动。

    乘法的意义：taper ∈ [0,1]，故新权重**恒 ≤ 旧权重** —— 相对历史行为只会更保守。
    mask_gamma=None（默认）时与历史实现逐位一致。
    """
    rolloff = 1.0
    if highlight_rolloff:
        rolloff = rolloff * np.clip((0.95 - L) / (0.95 - 0.65), 0.0, 1.0) ** 2
    if shadow_rolloff:
        rolloff = rolloff * np.clip((L - 0.06) / (0.16 - 0.06), 0.0, 1.0) ** 2
    if mask_gamma is not None and float(mask_gamma) > 0.0:
        lo = float(np.percentile(L, float(mask_low_pctl)))
        hi = float(np.percentile(L, float(mask_high_pctl)))
        taper = np.clip((hi - L) / max(hi - lo, 1e-6), 0.0, 1.0)
        # exp(gamma*ln(max(taper, eps))) 与 taper**gamma 数值等价，
        # 写成 exp/ln 是为了与外部经验的公式形式对齐、便于对照。
        rolloff = rolloff * np.exp(
            float(mask_gamma) * np.log(np.maximum(taper, 1e-5))
        )
    return rolloff


def apply_clahe(image, clip_limit=0.02, kernel_size=64, highlight_rolloff=True, shadow_rolloff=True,
                mask_gamma=None, mask_low_pctl=90.0, mask_high_pctl=99.9):
    """
    CLAHE (Contrast Limited Adaptive Histogram Equalization)。
    原理：在每个小窗口内做受限的直方图均衡化，
    增强局部对比度而不过分放大噪声。
    适合增强星云内部的细丝和涟漪纹理。

    clip_limit: 对比度限制（防止噪声放大）
    kernel_size: 局部窗口大小
    highlight_rolloff: 是否对高光区域（>0.65）应用软滚降衰减，防止亮核被顶爆
    shadow_rolloff: 是否对暗部/背景区域（<0.16）应用软滚降衰减，防止空背景/暗角被局部均衡化放大噪点
    mask_gamma: 自适应高光渐弱掩版指数（None=关闭，保持历史行为；建议 2.0）
    mask_low_pctl / mask_high_pctl: 渐弱窗口的分位端点（默认 p90 / p99.9）。
        p_low 以下权重恒为 1（不受影响），p_high 以上被完全保护。
    """
    image = np.asarray(image, dtype=np.float32)
    if image.ndim == 3:
        L = (
            0.2126 * image[..., 0]
            + 0.7152 * image[..., 1]
            + 0.0722 * image[..., 2]
        )
        L_enhanced = equalize_adapthist(L, kernel_size=kernel_size, clip_limit=clip_limit)
        rolloff = _clahe_rolloff(
            L, highlight_rolloff, shadow_rolloff,
            mask_gamma, mask_low_pctl, mask_high_pctl,
        )
        L_target = L + (L_enhanced - L) * rolloff
        gain = L_target / np.maximum(L, 1e-8)
        result = image * gain[..., None]
    else:
        L_enhanced = equalize_adapthist(image, kernel_size=kernel_size, clip_limit=clip_limit)
        rolloff = _clahe_rolloff(
            image, highlight_rolloff, shadow_rolloff,
            mask_gamma, mask_low_pctl, mask_high_pctl,
        )
        result = image + (L_enhanced - image) * rolloff
    return np.clip(result, 0, 1).astype(np.float32)


def apply_curves(image, shadows=1.0, midtones=1.3, highlights=1.0):
    """
    曲线调整（保比例感官亮度增益乘法模式）。
    原理：分别控制阴影、中间调、高光的亮度，并将增益直接作用于 RGB 通道，
    严格保持每个像素原有的通道比率与色相，避免压暗背景时色偏被相对放大。
    - shadows:  阴影亮度因子 (>1 提亮暗部, <1 压暗背景)
    - midtones: 中间调亮度因子 (>1 提亮星云主体)
    - highlights: 高光亮度因子 (=1 保护亮星不过曝)
    """
    image = np.asarray(image, dtype=np.float32)
    # 使用样条插值构造 S 曲线
    x = np.array([0, 0.25, 0.5, 0.75, 1.0])
    y = np.array([0, 0.25 * shadows, 0.5 * midtones,
                  0.75 * highlights, 1.0])
    y = np.clip(y, 0, 1)

    def interpolate(v):
        return np.interp(v, x, y)

    if image.ndim == 3:
        L = (
            0.2126 * image[..., 0]
            + 0.7152 * image[..., 1]
            + 0.0722 * image[..., 2]
        )
        L_adj = interpolate(L)
        gain = L_adj / np.maximum(L, 1e-8)
        result = image * gain[..., None]
    else:
        result = interpolate(image)

    return np.clip(result, 0, 1).astype(np.float32)


def local_contrast_enhance(image, radius=20, strength=0.3):
    """
    局部对比度增强。
    原理：原图 - 局部模糊版 = 局部细节，
    将局部细节乘以 strength 叠加回去。
    比全局锐化更适合增强星云纹理。
    """
    blurred = gaussian_filter(image, sigma=radius)
    detail = np.subtract(image, blurred)
    result = image + strength * detail
    return np.clip(result, 0, 1)


def local_nebula_enhance(image, center_y, center_x, radius=500, strength=0.25,
                         star_mask=None):
    """
    对指定区域（如目标星云中央）做局部对比度和纹理增强。

    原理：
      1. 创建以 (center_x, center_y) 为中心的径向软蒙版
      2. 在蒙版区域内做双尺度结构增强
      3. 用软蒙版将增强结果与原图混合，避免硬边缘
      4. 可选星点反向蒙版，避免带星图上同步强化星核

    参数:
      center_y, center_x: 星云中心位置（像素坐标）
      radius: 增强区域半径（像素）
      strength: 增强强度 (0-1, 推荐 0.2-0.35)
    """
    from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb

    h, w = image.shape[:2]
    Y, X = np.ogrid[:h, :w]
    dist = np.sqrt((Y - center_y) ** 2 + (X - center_x) ** 2)
    mask = np.clip(1.0 - dist / radius, 0, 1)
    mask = gaussian_filter(mask, sigma=radius / 3)

    if image.ndim == 3:
        lab = rgb2lab(np.clip(image, 0, 1))
        luminance = lab[..., 0] / 100.0
    else:
        luminance = image

    fine_sigma = max(2.0, radius / 95.0)
    medium_sigma = max(5.0, radius / 32.0)
    fine_smooth = gaussian_filter(luminance, sigma=fine_sigma)
    medium_smooth = gaussian_filter(luminance, sigma=medium_sigma)
    fine_detail = luminance - fine_smooth
    medium_detail = fine_smooth - medium_smooth
    detail = fine_detail * 0.45 + medium_detail * 0.85
    signal_low = np.percentile(medium_smooth, 35)
    signal_high = np.percentile(medium_smooth, 98)
    signal_mask = np.clip(
        (medium_smooth - signal_low) / max(signal_high - signal_low, 1e-6),
        0,
        1,
    )
    mask *= gaussian_filter(signal_mask, sigma=max(3.0, radius / 35.0))
    if star_mask is not None:
        stars = np.asarray(star_mask, dtype=np.float32)
        if stars.ndim == 3:
            stars = np.max(stars, axis=2)
        if stars.shape != luminance.shape:
            raise ValueError(
                f"star_mask shape {stars.shape} != image shape {luminance.shape}"
            )
        stars = gaussian_filter(np.clip(stars, 0, 1), sigma=1.5)
        mask *= np.clip(1.0 - stars * 0.95, 0.05, 1.0)
    enhanced_luminance = np.clip(luminance + strength * detail * mask, 0, 1)

    if image.ndim == 3:
        lab[..., 0] = enhanced_luminance * 100.0
        result = lab2rgb(lab)
    else:
        result = enhanced_luminance

    return np.clip(result, 0, 1)


def positive_starless_detail_enhance(image, original_linear, starless_linear,
                                    strength=0.75, full_frame=False):
    """
    用外部无星线性图引导正向结构增强。

    只提取无星层中高于局部背景的多尺度正细节，并按当前 RGB 比例增益。
    负残差和绝对亮度均不参与融合，因此不会把 StarNet 暗环、黑洞或
    无星层背景色带入成片。
    """
    current = np.asarray(image, dtype=np.float32)
    original = np.asarray(original_linear, dtype=np.float32)
    starless = np.asarray(starless_linear, dtype=np.float32)
    if current.shape != original.shape or current.shape != starless.shape:
        raise ValueError(
            "image, original_linear and starless_linear must have identical shapes"
        )

    difference = np.clip(np.subtract(original, starless), 0, None)
    difference_gray = (
        np.max(difference[..., :3], axis=2)
        if difference.ndim == 3 else difference
    )
    artifact_threshold = np.percentile(difference_gray, 97.0)
    artifact_mask = binary_dilation(
        difference_gray > artifact_threshold,
        iterations=4,
    )
    if starless.ndim == 3:
        local_background = gaussian_filter(starless, sigma=(2, 2, 0))
        cleaned = np.where(artifact_mask[..., None], local_background, starless)
        luminance = (
            0.299 * cleaned[..., 0]
            + 0.587 * cleaned[..., 1]
            + 0.114 * cleaned[..., 2]
        )
    else:
        local_background = gaussian_filter(starless, sigma=2)
        cleaned = np.where(artifact_mask, local_background, starless)
        luminance = cleaned

    broad_detail = np.clip(np.subtract(luminance, gaussian_filter(luminance, 28)), 0, None)
    fine_detail = np.clip(np.subtract(luminance, gaussian_filter(luminance, 5)), 0, None)
    structure = broad_detail + 0.45 * fine_detail
    normalization = float(np.percentile(structure, 99.7))
    if normalization <= 1e-9:
        return current.copy()
    detail = gaussian_filter(np.clip(structure / normalization, 0, 1), sigma=1)

    signal_threshold = np.percentile(luminance, 63)
    signal_mask = gaussian_filter(
        (luminance > signal_threshold).astype(np.float32),
        sigma=12,
    )
    if full_frame:
        radial = 1.0
    else:
        h, w = luminance.shape
        yy, xx = np.mgrid[:h, :w]
        radial = np.exp(
            -(((xx - w / 2) / max(w * 0.43, 1)) ** 4
              + ((yy - h / 2) / max(h * 0.43, 1)) ** 4)
        )
    guide = np.clip(detail * signal_mask * radial, 0, 1)

    if current.ndim == 3:
        current_luminance = (
            0.299 * current[..., 0]
            + 0.587 * current[..., 1]
            + 0.114 * current[..., 2]
        )
        gain = 1.0 + strength * guide * (1.0 - current_luminance)
        result = current * gain[..., None]
    else:
        result = current + strength * guide * (1.0 - current)
    return np.clip(result, 0, 1).astype(np.float32)


def apply_pi_detail_layer(image, strength_fine=0.35, strength_med=0.18, 
                          sigma_fine=3.2, sigma_med=14.0, bg_thresh=0.065):
    """
    PixInsight 风格双尺度微对比度与高通细节层 (LHE + Detail Layer) 注入。

    公式参考 PixInsight-pipeline:
      $T + detailStr * (Ha - GaussianBlur(Ha, 15))
    结合自适应明度保护掩膜，提取毛细细丝与中尺度立体结构，按比率守恒映射回 RGB。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    rgb = source[..., :3] if is_color else source

    if is_color:
        lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    else:
        lum = rgb

    # 自适应掩膜：保护暗部噪点，高光核心平滑过渡
    mask = np.clip((lum - float(bg_thresh)) / 0.18, 0.0, 1.0)
    mask = mask * mask * (3.0 - 2.0 * mask)  # smoothstep
    mask = gaussian_filter(mask, sigma=4.0)

    # 细微毛细纤维层
    blur_fine = gaussian_filter(lum, sigma=float(sigma_fine))
    detail_fine = lum - blur_fine

    # 中尺度立体结构层
    blur_med = gaussian_filter(lum, sigma=float(sigma_med))
    detail_med = lum - blur_med

    # 细节注入
    lum_enhanced = lum + mask * (float(strength_fine) * detail_fine + float(strength_med) * detail_med)
    lum_enhanced = np.clip(lum_enhanced, 0.0, 1.0)

    if is_color:
        gain = (lum_enhanced + 1e-6) / (lum + 1e-6)
        result = rgb * gain[..., None]
        if source.shape[2] > 3:
            result = np.dstack([result, source[..., 3:]])
    else:
        result = lum_enhanced

    return np.clip(result, 0, 1).astype(np.float32)


def main():
    p = argparse.ArgumentParser(description='深空星云细节增强')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='hdr',
                   choices=['hdr', 'clahe', 'curves', 'local_contrast', 'detail_layer'],
                   help='增强方法 (默认: hdr)')
    p.add_argument('--strength', type=float, default=0.5,
                   help='增强强度 (默认: 0.5)')
    p.add_argument('--midtones', type=float, default=1.3,
                   help='曲线中间调 (默认: 1.3)')
    p.add_argument('--clip-limit', type=float, default=0.02,
                   help='CLAHE 对比度限制 (默认: 0.02)')
    p.add_argument('--light', action='store_true', help='轻度增强')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[增强] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    if args.light:
        args.strength /= 2

    if args.method == 'hdr':
        result = hdr_multiscale_compress(img, strength=args.strength)
    elif args.method == 'clahe':
        result = apply_clahe(img, clip_limit=args.clip_limit)
    elif args.method == 'curves':
        result = apply_curves(img, midtones=args.midtones, shadows=args.strength)
    elif args.method == 'local_contrast':
        result = local_contrast_enhance(img, strength=args.strength)
    elif args.method == 'detail_layer':
        result = apply_pi_detail_layer(img, strength_fine=args.strength * 0.7, strength_med=args.strength * 0.35)

    imsave(args.output, img_as_ubyte(result))
    print(f"[增强] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
