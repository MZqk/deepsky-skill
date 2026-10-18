#!/usr/bin/env python3
"""去星误伤门禁（damage_limit）的劣化对照标定器（离线工具，非管线组件）。

**这个工具回答什么问题**：``star_tools._star_removal_gate`` 里的
``damage_limit = 0.20`` 是在什么数据上定出来的？换一个目标类型还成立吗？
NGC6888 实测同一母版跨 preset 跨度 **25×**（medium 0.4502 vs adaptive
0.0180）—— 档内方差远大于档间差异，所以**分档之前必须先有标定数据**。

三条设计纪律（都是被实测否决过一次的）

1. **分组用 retention ratio ``q``，不用构造参数、也不用 damage 区间。**
   - 构造参数尺度不可比：``k=0.7``（亮度缩放）与 ``α=1.4``（过减混合）
     可给同一 damage，参数差一倍。
   - damage 有 ±10% 死区（``max(0, |1-q| - 0.10)``），健康组在死区内
     **全部读 0** ⇒ 按 damage 分组会把健康组压成一个点，算不出分位。
2. **只扰动 ``starless`` 的构造在数学上不可能触发可测性短路** ——
   可测性判据只用 ``orig_smooth``。这条既是简化也是约束：E/F 型构造
   分别负责「证明 damage 的主体局部性」与「证明可测性判据仍活着」。
3. **E 型阴性对照是强制红线**：任何准备进标定的构造，必须先通过 ——
   同一参数下**只扰动掩膜外**的版本，damage 必须仍 ``< 1e-9``。
   不满足说明 damage 升高来自全局亮度效应 ⇒ 拒绝入组。这条规则自动
   把 A′（分母扰动）挡在门外，无需人工判断。

闭式（``star_tools`` 化简后）

    q      = median(sl_smooth[M]) / median(orig_smooth[M])
    damage = max(0, |1 - q| - 0.10)          # ±10% 死区, V 形双侧

三个结构性性质（读数前必须知道）

- **死区**：``q ∈ [0.90, 1.10] ⇒ damage ≡ 0``
- **V 形双侧**：``q=0.70``（暗 30%）与 ``q=1.30``（亮 30%）读数相同 ⇒
  指标**无法区分变暗与变亮**。星移除的真实物理是变暗，亮侧无对应失效模式。
- **削顶边界**：构造的 ``k`` 必须落在不削顶区间。本数据 ``p99=0.0017``，
  ``k=2`` 已削顶，此时 ``q ≠ k×``、闭式失效（实测 k=2 读 0.8998 而
  预测 2.8995）。V1 会自动检出这件事而不是给出假的「单调」。

用法::

  # V1-V4：闭式 / 单调性 / 隔离 / 抖动
  python scripts/calibrate_star_damage_gate.py --corpus ngc6888 M17 \
      --calib-root /tmp/calib --json /tmp/dmg.json

  # 门禁口径回放：把每条样本喂 _star_removal_gate 看 gate_state
  python scripts/calibrate_star_damage_gate.py --corpus ngc6888 M17 \
      --calib-root /tmp/calib --replay
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, shift as ndi_shift

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import star_tools as st
from star_removal_profiles import DEPRECATED_FIELDS, get_profile


#: 标定口径 = **全分辨率**。
#:
#: 这里**不能**照抄 ``calibrate_fidelity_gates`` 的 1400px：那条降采样只
#: 适用于保真度/星点伪影门禁（它们的调用点显式过 ``downsample_for_gates``），
#: 而去星质量门禁的调用点（``star_tools.separate_stars``）**不过**——
#: ``estimate_star_removal_quality`` 直接吃 Phase 4 的全分辨率线性图。
#:
#: 实测代价（NGC6888，3840×2160）：1400px 口径下 ``subject_separation``
#: 读 1.2870 < 判据下限 1.30 ⇒ ``measurable=False``、damage 读 0；
#: 全分辨率读 1.3080 ⇒ 可测、damage 0.2403。**同一份数据、同一对图，
#: 换口径就得出「不可测」与「轻度误伤」两个相反结论。**
#:
#: 跨口径标定是本工具最容易犯也最致命的错误：定出的线拿到运行时
#: 会被判成另一个形态，而报告里看不出任何异常。
CALIB_MAX_SIDE = None  # None = 不降采样

#: 口径自检：报告里记录实际使用的尺寸，供验收核对。
CALIB_APERTURE_NOTE = (
    '去星质量门禁在运行时吃全分辨率线性图（调用点不过 '
    'downsample_for_gates），故标定同口径。保真度门禁的 1400px 口径'
    '不适用于本指标。'
)

#: 每档标定所需的母版数下限。对齐 calibrate_fidelity_gates 的
#: ``sparse = len(...) < 3`` 判据：少于 3 个母版的档只能标 PROVISIONAL。
CALIBRATION_CORPUS_SIZE = 3

#: V4 判定「抖动已测」所需的**可测**子窗数下限。
#: 不可测子窗恒读 0（``raw_change`` 被强制），混进来会把 spread 撑大
#: 而不反映任何噪声 —— 所以门槛只数可测的。
MIN_MEASURABLE_WINDOWS = 3

#: V4 子窗默认数。9 = 3×3，是覆盖四角/边中/中心的最小对称网格。
#: 16 = 4×4 更密但每子窗降到 ~0.55MP（接近 separation 判据的敏感区），
#: 故只作可选档。
DEFAULT_JITTER_WINDOWS = 9

#: V5 定线的**健康侧放宽系数**：``健康组最大 damage × 该系数`` = 上界。
#: 1.0 = 线不得低于任何健康样本的读数（零漏放）；1.5 = 允许健康样本
#: 最多超出线 50%（容忍健康组内部的自然波动）。
#: 依据：5 份实测健康样本 damage 为 0.0357~0.3580，跨 10 倍 ——
#: 系数取 1.0 会让上界完全由 M8 一份决定，取 1.5 才有解释余量。
LINE_RELAX_FACTOR = 1.5

#: E 型阴性对照的硬线：掩膜外损伤时 damage 相对健康态的**升幅**上限。
#:
#: 阈值必须对齐 ``nebula_damage_ratio`` 的**舍入粒度**（4 位小数），
#: 也就是 1e-4。第一版取 1e-9 —— 那比最小可分辨差还小 5 个数量级，
#: 于是**任何舍入都会判红线失败**：M8 实测掩膜外全损后 damage
#: 0.3580 → 0.3581（升幅 1e-4，纯粹是 round(0.35805) 的进位），
#: 被报成「E 型未通过，说明 damage 不是纯主体局部量」。
#:
#: 取 2e-4 = 2 个舍入单位：容得下一次进位，容不下真实渗入
#: （未腐蚀版本实测升幅 0.0132，比它大两个数量级）。
OUTSIDE_MASK_DAMAGE_EPS = 2e-4

#: 闭式残差判据（damage 保留 4 位小数，残差按 1e-4 判）。
CLOSED_FORM_TOLERANCE = 1e-4


# ── 降采样与掩膜（口径必须与 star_tools 一致）────────────────────────
def downsample(image, max_side=CALIB_MAX_SIDE):
    """按需降到 ``max_side``；``max_side=None`` 时**原样返回副本**。

    ``anti_aliasing=True`` 是必须的（它会平滑高频噪声，标定与运行时
    口径不同则阈值不可迁移），但默认不降 —— 理由见 :data:`CALIB_MAX_SIDE`。
    """
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    arr = arr[..., :3]
    if max_side is None:
        return arr.copy()
    h, w = arr.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return arr.copy()
    from skimage.transform import resize
    scale = max_side / float(longest)
    out = resize(arr, (max(int(round(h * scale)), 1),
                       max(int(round(w * scale)), 1), 3),
                 anti_aliasing=True, preserve_range=True)
    return out.astype(np.float32)


def to_gray(image):
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        return arr
    return 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]


def subject_mask_from(orig_gray):
    """主体掩膜 = ``orig_smooth >= p75``，与 ``star_tools`` 同一口径。

    必须复刻而非导入：``star_tools`` 里没有暴露这个掩膜，而口径差一位
    分位就会让掩膜偏移、构造失效。这段是**可测性判据的第二个来源**
    （第一个是 separation），所以必须与线上同源。
    """
    orig_smooth = gaussian_filter(orig_gray, sigma=8.0)
    signal_level = float(np.percentile(orig_smooth, 75))
    if signal_level <= 1e-6:
        return None, orig_smooth, 0.0
    mask = orig_smooth >= signal_level
    return mask, orig_smooth, signal_level


def retention_ratio(orig_gray, sl_gray, mask):
    """``q = median(sl_smooth[M]) / median(orig_smooth[M])``。"""
    if mask is None or not np.any(mask):
        return None
    sl_smooth = gaussian_filter(sl_gray, sigma=8.0)
    orig_ref = float(np.median(gaussian_filter(orig_gray, sigma=8.0)[mask]))
    sl_ref = float(np.median(sl_smooth[mask]))
    if orig_ref <= 1e-8:
        return None
    return sl_ref / orig_ref


# ── 构造族 ────────────────────────────────────────────────────────
#: 每个构造返回 (label, starless 数组, 该构造的语义标记)。
#: 标记 ``touches_original=True`` 的构造会改动**分母**，按 §3.2 的规则
#: 不允许进标定组（只能当反面元验证）。
def construct_brightness(sl, k):
    """A 亮度缩放。``sl * k``。

    实测性质：damage 对 k **单调降**；``residual`` 逐位不变；但
    ``high_grad`` **会变**（缩放越狠越接近 0）。

    原因（第一版方案里写错过，这里是实测结论）：``high_grad_ratio`` 的
    判据是 ``grad_sl > max(grad_orig × 2, 6σ_sl)``，其中 ``6σ_sl`` 是
    **绝对**噪声地板。``sl`` 整体缩放时 ``grad_sl`` 与 ``σ_sl`` 同比缩放，
    但 ``grad_orig`` 不变 ⇒ 判据的相对关系被打破。实测（合成星场）：
    ``k=1.0 → hgr 0.0794``、``k=0.9 → 0.0000``。

    ⇒ A 型**不是** ``pure_damage``（三段归因法判为 ``confounded``），
    只能定 ``damage_limit`` 的量级，不能声称「零混淆」。
    这是「方案假设被实测否决」的第三例（前两例：掩膜占比恒 0.25、
    damage 闭式仅在不削顶区间成立）。
    """
    return np.clip(sl * k, 0.0, 1.0).astype(np.float32)


def construct_overshoot(orig, sl, beta):
    """B★ 过减混合 ``orig + (1+β)(sl - orig)``，``β >= 0``。

    ``β = 0`` 即**健康态本身**（构造的零参照点），``β > 0`` 逐步过度去星。
    用 ``β`` 而非 ``α`` 是为了与 A 型的 ``f`` 对齐：两者的
    「0 = 不构造」语义一致，定线时才能共用同一套夹逼口径。

    实测性质：damage 对 ``β`` **严格线性**（``q' = 1 − (1+β)(1−q)`` 是线性的）；
    同时动 residual / high_grad ⇒ 与 A 共线，**不能当独立档**
    （v0.1.35 实测判为 ``residual_only``）。

    与「原式」``α*sl + (1-α)*orig`` 的区别是致命的：后者是凸组合，
    掩膜内取中位后原图分量把分子拉回 0，damage 全程恒 0，而 residual /
    high_grad 大幅移动 —— 动了两个指标却不动目标指标，零信息量。
    """
    return np.clip(orig + (1.0 + beta) * (sl - orig),
                   0.0, 1.0).astype(np.float32)


def construct_shift(sl, offset_px):
    """D 空间位移。几何错位档。

    实测性质：饱和型（死区 ≈ σ_smooth）；``high_grad`` 强增 ⇒ 混淆档，
    只能定 hard 线的**下界**。
    """
    return np.clip(ndi_shift(sl, offset_px, order=1, mode='nearest'),
                   0.0, 1.0).astype(np.float32)


#: E 型构造的**腐蚀深度**（像素），必须 ≥ 平滑核的有效支撑半径。
#:
#: **第一版的 E 型红线在数学上不可能成立**。``damage`` 定义在
#: ``gaussian_filter(gray, sigma=8)`` 上，而高斯核的支撑是**无限**的：
#: 掩膜外紧邻边界的一圈像素，其亮度会被卷进掩膜内。实测（合成星场，
#: 掩膜占比 0.25）：掩膜外全损（f=1.0）时掩膜内中位比从 0.6505 掉到
#: 0.6373，damage 从 0.2495 升到 0.2627。
#:
#: ⇒ 「掩膜外损伤 ⇒ damage ≡ 0」不是门禁的性质缺陷，是**构造的定义错误**。
#: 正确做法：把待损伤区域**腐蚀** ``3σ = 24px``，使被改动的像素到掩膜
#: 的距离超过核支撑 ⇒ 卷积后掩膜内取值逐位不变。实测腐蚀 24px 后
#: damage 0.2495 → 0.2495（Δ < 5e-5，落在 4 位小数的舍入内）。
E_EROSION_SIGMA = 3.0
E_EROSION_PX = int(round(8.0 * E_EROSION_SIGMA))


def construct_outside_mask(orig, sl, mask, fraction):
    """E 掩膜外损伤：掩膜外**腐蚀 3σ 后**乘 ``(1 − fraction)``。

    腐蚀是必需的（见 :data:`E_EROSION_PX` 的说明）：不腐蚀时高斯平滑
    会把边界外的亮度卷进掩膜内，红线恒不成立。

    实测性质（腐蚀后）：**damage 相对健康态的升幅 < 5e-5**，而
    ``residual`` / ``high_grad`` 显著变化 —— 证明 damage 确实是
    **主体局部**量。本工具据此把 E 用作所有劣化构造的**阴性对照红线**：
    任何构造若在「只扰动掩膜外远处」时仍推高 damage，说明它的
    damage 升高来自全局亮度效应 ⇒ 拒绝入组。
    """
    if mask is None:
        return sl
    outside = _eroded_outside(mask)
    out = sl.copy()
    out[outside] = sl[outside] * (1.0 - fraction)
    return np.clip(out, 0.0, 1.0).astype(np.float32)


def _eroded_outside(mask):
    """掩膜的补集，腐蚀 :data:`E_EROSION_PX` 像素。"""
    from scipy.ndimage import binary_erosion
    from skimage.morphology import disk

    outside = ~np.asarray(mask, dtype=bool)
    if E_EROSION_PX <= 0:
        return outside
    return binary_erosion(outside, structure=disk(E_EROSION_PX))


def construct_gamma(orig, gamma_value):
    """F1 原图 γ 扰动（改**分母**，幂律）。

    唯一能在**不裁掉主体**的前提下压低 ``subject_separation`` 的构造之一
    ⇒ **元验证专用，禁止定线**：它的 damage 变化是分母造成的，混进
    标定组会污染 ``damage_limit``。

    实测（NGC6888 全分辨率）：``sep=1.308``（γ=1）→ 1.118（γ=0.5）
    → 1.054（γ=0.25）→ 1.021（γ=0.1）。机制：幂律对 p25 与 p75 施加
    **同一个**单调变换，两者比值必然下降（压缩动态范围）。
    """
    return np.clip(np.power(np.clip(orig, 0.0, 1.0), gamma_value),
                   0.0, 1.0).astype(np.float32)


def construct_bright_fill(orig, amount):
    """F2 原图亮填充（加常量，改**分母**）。

    与 F1 同为元验证构造，但机制不同：常量直接抬高 p25（安静区），
    比值下降更快（NGC6888：``c=0.005`` 即 sep 1.033）。两者一起用可以
    证明可测性判据对**两种**分母扰动都敏感 —— 只测一种可能撞上该构造
    恰好不敏感的系数区间。
    """
    return np.clip(np.asarray(orig, dtype=np.float32) + amount,
                   0.0, 1.0).astype(np.float32)


def outside_mask_variant(orig, sl, mask, fraction):
    """同一参数下的**只扰动掩膜外**版本 —— E 型阴性对照的红线判据。

    语义与 :func:`construct_outside_mask` 相同，单独命名是为了让红线规则
    在代码里可搜到：``check_isolation`` 断言的就是「只扰动掩膜外时 damage
    仍为 0」这条性质。
    """
    return construct_outside_mask(orig, sl, mask, fraction)


#: 参数网格。
#:
#: 刻意不写死数值起点：起点的意义是「让 q 落到健康组边界之外」，而那
#: 取决于每份母版实测的 q。**写死的档位在不同数据上覆盖的是完全不同的
#: 区间** —— 实测 NGC6888（q=0.6597）与合成星场（q=0.9855）上，
#: 同一张 B★ 网格在前者覆盖 α=1.25~5.0、在后者整族落在死区内。
#:
#: C 型（跨母版错配）不在这张表里：它需要两份不同母版的无星图配对，
#: 且必须先中心裁到公共尺寸（M17 是 3697×2090，其余 3840×2160）。
#: 拿不到健康无星图时**宁可不跑** —— 用同母版的亮度缩放冒充「跨母版错配」
#: 会把它降级成 A 的重复，得出「单调」的假结论。
#:
#: **A/B★ 用相对倍率（v0.1.37）**：``f = 1.00`` 即**健康态本身**，
#: 是构造的零参照点。第一版用「绝对 q 阶梯」（使 q 落在
#: 0.90/0.855/…/0.45，与样本无关），而**真实健康样本的 q 本身就在
#: 0.54~0.86** —— 因为去星必然带走主体光通量。两者在同一区间交错，
#: 于是「劣化组最轻档」（恒为 0.0450）比4/5 份样本的**健康态**
#: （0.185~0.358）还低，双向夹逼会算出 0.045这种荒谬的线。
#: 详见 ``references/star_damage_gate_calibration.md`` §3.12。
GRIDS = {
    # A型：直接乘无星图，k = f。f=1.00 即健康态。
    'A': ('f', (1.00, 0.95, 0.90, 0.85, 0.80, 0.70, 0.60, 0.50)),
    # B★ 过减混合：α = 1 + β。β=0 即健康态。
    'B_star': ('beta', (0.0, 0.10, 0.25, 0.50, 1.00, 2.00, 4.00)),
    'D': ('offset_px', (0, 2, 4, 8, 16, 30, 60, 120)),
    'E': ('fraction', (0.0, 0.3, 0.6, 0.9, 1.0)),
    'F1': ('gamma', (1.0, 0.8, 0.6, 0.5, 0.4, 0.25, 0.10)),
    'F2': ('amount', (0.0, 0.001, 0.005, 0.02, 0.05, 0.10)),
}

#: 「健康态本身」在相对网格里的位置。A/B★ 的第一个倍率恒为 1.0。
HEALTHY_RATIO = 1.00

#: 「最轻劣化档」用的倍率。1.00 是健康态，0.95 是第一个真正的劣化档。
LIGHTEST_DEGRADED_RATIO = 0.95


def grid_values(grid_key, q_healthy=None):
    """该构造在某份母版上的实际参数档（**相对倍率**）。

    A/B★ 的档位是**相对倍率**而非绝对值：``f=1.00`` 对应
    ``q' = q_healthy``（健康态本身），``f<1`` 逐步把无星图调暗。
    这样「最轻劣化档」才回答「比该样本健康态差多少才算劣化」，
    而「绝对 q 阶梯」回答的是「q 落在哪一格」—— 后者与健康态重叠，
    定线会算出比健康数据还严的线（见 :data:`GRIDS` 注释）。

    相对倍率**不依赖** ``q_healthy``（倍率是纯无量纲数）⇒ 这里不因
    ``q_healthy`` 缺失而返回空列表。那种检查放在
    :func:`check_degraded_separation`：它要验证的是「读数是否与
    健康态自洽」，那才是真依赖 ``q`` 的地方。
    """
    return list(GRIDS[grid_key][1])


def grid_values_absolute(grid_key, q_healthy):
    """**绝对** q 阶梯（v0.1.36 之前的口径，保留供对照与回归测试）。

    它的读数已被 V1/V2/V3 验证过，不能直接删；但它**不能用于定线** ——
    5 份真实样本实测：它的「最轻非零档」恒为 0.0450，而健康态本身是
    0.0357~0.3580 ⇒ 4/5 份样本的「劣化」比「健康」还轻。

    两族离开死区的阈值公式**不同**，不能共用换算：
    A型 ``q' = k·q`` 需 ``k > 0.90/q``；
    B★ ``q' = 1 − α(1−q)`` 需 ``α > 0.10/(1−q)``。
    """
    if q_healthy is None or q_healthy <= 0.0:
        return []
    if grid_key == 'A':
        edge = 0.90 / q_healthy
        return [round(edge * f, 6) for f in
                (1.0, 0.95, 0.90, 0.85, 0.80, 0.70, 0.60, 0.50)]
    if grid_key == 'B_star':
        edge = 0.10 / max(1.0 - q_healthy, 1e-8)
        return [round(edge * f, 6) for f in
                (1.0, 1.1, 1.25, 1.5, 2.0, 3.0, 5.0, 8.0)]
    return list(GRIDS[grid_key][1])


def apply_grid(construct, grid_key, q_healthy=None, absolute=False):
    """按该构造在本数据上的实际档位施加构造。"""
    values = (grid_values_absolute(grid_key, q_healthy) if absolute
              else grid_values(grid_key, q_healthy))
    return [(value, construct(value)) for value in values]


# ── 单条样本的采集与测量 ──────────────────────────────────────────
def measure(original, starless, target_type=None, profile=None):
    """跑一次 ``estimate_star_removal_quality``（唯一的读数来源）。"""
    return st.estimate_star_removal_quality(original, starless,
                                            target_type, profile)


def measure_delta(row, key):
    """三段归因法用的 Δ（§3.3）。"""
    return row[key] - row['_healthy'][key]

def classify_delta(row, eps=None):
    """按「Δ_damage / Δ_resid / Δ_hgr」三段法给构造分组。

    判据阈值取**各指标的噪声地板量级**（``1e-4``），不是 0：三个指标
    都被 round 到 4 位，逐位比较会把舍入噪声当成「变了」。

    实测结论（否证了方案里「只有 A 是纯 damage 劣化」的假设）：
    **没有任何构造是 ``pure_damage``**。A 型亮度缩放虽然 ``residual``
    逐位不变，但 ``high_grad`` 会动 —— 它的判据含**绝对**噪声地板
    ``6σ_sl``，而 ``σ_sl`` 随缩放同比变化、``grad_orig`` 不变。
    所以 A 型判为 ``confounded``：可定 ``damage_limit`` 的量级，
    但不能声称「零混淆」。
    """
    eps = 1e-4 if eps is None else eps
    d_damage = row['delta_damage']
    d_resid = abs(row['delta_residual'])
    d_hgr = abs(row['delta_gradient'])
    if d_damage > eps and d_resid <= eps and d_hgr <= eps:
        return 'pure_damage'
    if d_damage > eps and d_hgr > eps:
        return 'confounded'
    if d_damage <= eps and d_resid > eps:
        return 'residual_only'
    if d_damage <= eps and d_resid <= eps and d_hgr > eps:
        return 'gradient_only'
    return 'no_effect'


def collect_sample(original, starless, target_type, label,
                   clip_percentile=99.0):
    """把一对 (原图, 无星图) 折成一条可比较的样本（降采样 + 灰度 + 掩膜）。"""
    orig_ds = downsample(original)
    sl_ds = downsample(starless)
    if orig_ds.shape != sl_ds.shape:
        return None
    orig_gray = to_gray(orig_ds)
    sl_gray = to_gray(sl_ds)
    mask, _, signal_level = subject_mask_from(orig_gray)
    q = retention_ratio(orig_gray, sl_gray, mask)
    report = measure(orig_ds, sl_ds, target_type)
    return {
        'label': label,
        'target_type': target_type,
        'shape': list(orig_ds.shape),
        'clip_pctl': float(np.percentile(orig_ds, clip_percentile)),
        'signal_level': signal_level,
        'coverage': float(np.mean(mask)) if mask is not None else 0.0,
        'q': q,
        'q_predicted': 1.0 if q is None else q,
        'residual_star_fraction': report['residual_star_fraction'],
        'nebula_damage_ratio': report['nebula_damage_ratio'],
        'high_gradient_ratio': report['high_gradient_ratio'],
        'repair_quality_score': report['repair_quality_score'],
        'nebula_damage_measurable': report['nebula_damage_measurable'],
        'subject_separation': report['subject_separation'],
        'gate_state': st._star_removal_gate(
            report, 0.45, target_type)[1],
    }


def _with_deltas(row, healthy):
    row = dict(row)
    row['_healthy'] = healthy
    row['delta_damage'] = (row['nebula_damage_ratio']
                           - healthy['nebula_damage_ratio'])
    row['delta_residual'] = (row['residual_star_fraction']
                             - healthy['residual_star_fraction'])
    row['delta_gradient'] = (row['high_gradient_ratio']
                             - healthy['high_gradient_ratio'])
    row['class'] = classify_delta(row)
    return row


def run_constructs(original, starless, healthy_row, target_type):
    """在一条健康样本上跑全部构造，返回 ``{构造名: [行, ...]}``。

    构造与读数都在**同一降采样口径**上做（``collect_sample`` 会再降一次，
    幂等）—— 跨口径构造等于换了一个判据。
    """
    orig_ds = downsample(original)
    sl_ds = downsample(starless)
    mask_ds, _, _ = subject_mask_from(to_gray(orig_ds))
    q_healthy = retention_ratio(to_gray(orig_ds), to_gray(sl_ds), mask_ds)
    out = {}
    for grid_key in ('A', 'B_star', 'D', 'E', 'F1', 'F2'):
        rows = []
        for value, _ in apply_grid(lambda v: v, grid_key, q_healthy):
            if grid_key == 'A':
                candidate = construct_brightness(sl_ds, value)
            elif grid_key == 'B_star':
                candidate = construct_overshoot(orig_ds, sl_ds, value)
            elif grid_key == 'D':
                candidate = construct_shift(sl_ds, value)
            elif grid_key == 'E':
                candidate = construct_outside_mask(orig_ds, sl_ds,
                                                   mask_ds, value)
            elif grid_key == 'F1':
                # 注意：F 族扰动的是**原图**（分母），所以要同时替换
                # 参与读数的 orig —— 只改 starless 侧不会压低 separation。
                candidate = construct_gamma(orig_ds, value)
                orig_arg = candidate
            else:  # F2
                candidate = construct_bright_fill(orig_ds, value)
                orig_arg = candidate
            row = (collect_sample(orig_arg, sl_ds, target_type,
                                  '%s=%s' % (grid_key, value))
                   if grid_key.startswith('F') else
                   collect_sample(orig_ds, candidate, target_type,
                                  '%s=%s' % (grid_key, value)))
            if row is None:
                continue
            row['param'] = value
            # ``ratio`` 是**相对倍率**本身（与 param 对应但语义不同：
            # param 是传给构造的数值，ratio 是它在网格里的位置）。
            # V5 靠 ratio 精确匹配 f=1.00 / f=0.95 两档 —— 按数值找
            # 会因浮点误差漏掉健康档本身。
            row['ratio'] = value
            rows.append(_with_deltas(row, healthy_row))
        out[grid_key] = rows
    return out


# ── V1 闭式一致性 ─────────────────────────────────────────────────
def check_closed_form(healthy_row, rows_by_construct, tolerance=None):
    """V1：``damage' = max(0, |1 - k*q| - 0.10)`` 与实测逐档比对。

    为什么必须先跑：它把闭式变成可执行断言。**不过则后面全部分析作废** ——
    构造若在实测路径上有未知副作用（削顶、掩膜偏移），「单调性」只是一条
    看起来漂亮的曲线。

    顺带检出**削顶**：若某档 ``k*q`` 远超 1 而实测 damage 远小于预测，
    就是 ``clip(sl*k, 0, 1)`` 削顶导致 ``q' ≠ k*q``（见模块 docstring）。
    """
    tolerance = CLOSED_FORM_TOLERANCE if tolerance is None else tolerance
    q = healthy_row['q']
    # **不可测样本不能验闭式**：``raw_change`` 被强制为 0.0，damage 恒 0，
    # 闭式预测再准也只会得到「残差 = 预测值」。M17 实测 sep=1.017 <
    # 1.30 ⇒ 全档读 0，V1 报 worst=0.41 —— 那是「没测」而不是「不符」。
    if not healthy_row.get('nebula_damage_measurable', True):
        return {
            'applicable': False,
            'reason': '健康样本不可测（separation=%.3f < 下限），'
                      'damage 被强制为 0，无法验证闭式'
                      % healthy_row.get('subject_separation', 0.0),
        }
    results, worst, clipped, dead = [], 0.0, [], 0
    if q is None:
        return {'applicable': False, 'reason': '健康样本 q 不可测'}
    for row in rows_by_construct.get('A', []):
        k = row['param']
        predicted = max(0.0, abs(1.0 - k * q) - 0.10)
        measured = row['nebula_damage_ratio']
        residual = abs(predicted - measured)
        # 削顶检出：k>1 时若实测远低于预测，说明量化上限起作用了。
        if k > 1.0 and measured + 0.05 < predicted:
            clipped.append(k)
        # 死区档（预测 0）单独计数：它们**必然**吻合，混进 worst 会
        # 稀释真正的残差，也让「网格全在死区内」看起来像「闭式完美」。
        if predicted == 0.0:
            dead += 1
            results.append({'k': k, 'predicted': 0.0, 'measured': measured,
                            'residual': round(residual, 4),
                            'in_dead_zone': True})
            continue
        worst = max(worst, residual)
        results.append({
            'k': k, 'predicted': round(predicted, 4),
            'measured': measured, 'residual': round(residual, 4),
            'in_dead_zone': False,
        })
    if not any(not r['in_dead_zone'] for r in results):
        return {
            'applicable': False,
            'reason': 'A 型网格全部落在死区内 —— 闭式在此恒等于 0，'
                      '无法验证（检查 grid_values 的自适应阈值）',
        }
    return {
        'applicable': True,
        'q_healthy': round(q, 6),
        'rows': results,
        'dead_zone_rows': dead,
        'worst_residual': round(worst, 6),
        'tolerance': tolerance,
        'passed': worst <= tolerance,
        'clip_detected_at_k': clipped,
        'clip_note': ('预测与实测在 k>1 处系统性偏离 —— clip(sl*k,0,1) 已削顶，'
                      '此时 q\' ≠ k*q，闭式在该区间不成立，构造的 k 必须限制在'
                      '不削顶单侧区间'
                      if clipped else ''),
    }


# ── V2 单调性与死区 ───────────────────────────────────────────────
def check_monotonicity(rows, expect='monotonic_increase'):
    """按构造的期望形态判定。返回 ``(passed, 诊断)``。

    不做统一判据：实测形态差异很大（E 恒 0、D 饱和、F 阶跃），
    用同一个「必须严格单调」会把合法构造误判为失败。

    方向标签按**实测**定，不按直觉：「亮度缩放系数越小」读数**越大**
    （主体被压暗 = 误伤加重），所以 A 型是 ``monotonic_increase``。
    """
    values = [r['param'] for r in rows]
    damages = [r['nebula_damage_ratio'] for r in rows]
    residuals = [r['residual_star_fraction'] for r in rows]
    grads = [r['high_gradient_ratio'] for r in rows]

    if expect == 'constant_zero':
        # 红线是「**相对健康态不升高**」，不是「绝对为 0」。健康样本
        # 本身的 damage 可以非零（真实 StarNet2 产物 NGC6888 读 0.2403、
        # M8 读 0.3580）—— 那是「去星确实带走了主体光通量」的真实代价。
        # 按绝对 0 判会得出「E 型未通过」，但实测 f=0.0（即不施加任何
        # 掩膜外损伤）就已经读到 0.2403/0.3580 ⇒ 判据把**健康态自己**
        # 当成了违规。这是绝对值判据的必然后果。
        deltas = [r.get('delta_damage') for r in rows
                  if r.get('delta_damage') is not None]
        damages = [r['nebula_damage_ratio'] for r in rows]
        residuals = [r['residual_star_fraction'] for r in rows]
        grads = [r['high_gradient_ratio'] for r in rows]
        moved = (max(residuals) - min(residuals) > 1e-4
                 or max(grads) - min(grads) > 1e-4)
        if not deltas:
            return False, {'reason': '缺少 delta_damage，无法判红线',
                           'damages': damages}
        worst = max(abs(d) for d in deltas)
        return (worst < OUTSIDE_MASK_DAMAGE_EPS and moved,
                {'max_abs_delta_damage': round(worst, 8),
                 'healthy_damage': round(rows[0].get('_healthy', {}).get(
                     'nebula_damage_ratio', 0.0), 6),
                 'damages': damages,
                 'other_moved': moved,
                 'criterion': '掩膜外全损时 damage 相对健康态的升幅 < %g'
                              % OUTSIDE_MASK_DAMAGE_EPS,
                 'note': 'E 型阴性对照：掩膜外损伤不得推高 damage'})

    if len(damages) < 3:
        return False, {'reason': '样本点不足 3 个，无法判单调'}

    diffs = [damages[i + 1] - damages[i] for i in range(len(damages) - 1)]
    # 全部读数相同 ⇒ 构造无效（死区/不可测），不是「单调」
    if max(damages) - min(damages) < 1e-9:
        return False, {'reason': '全部档位读数相同 —— 构造未生效'
                                  '（检查是否整族落在死区或样本不可测）',
                       'damages': damages}
    strictly_dec = all(d <= 1e-9 for d in diffs)
    strictly_inc = all(d >= -1e-9 for d in diffs)

    if expect == 'monotonic_decrease':
        return strictly_dec, {'diffs': [round(d, 6) for d in diffs],
                              'strictly_decreasing': strictly_dec}
    if expect == 'monotonic_increase':
        return strictly_inc, {'diffs': [round(d, 6) for d in diffs],
                              'strictly_increasing': strictly_inc}
    if expect == 'saturating':
        if not strictly_inc:
            return False, {'reason': '非单调升',
                           'diffs': [round(d, 6) for d in diffs]}
        first = sum(diffs[:2]) or 0.0
        last = sum(diffs[-2:]) or 0.0
        if first == 0.0:
            return False, {'reason': '前两档差为 0，无法算饱和比'}
        ratio = last / abs(first)
        return ratio <= 0.2, {'tail_ratio': round(ratio, 4),
                              'criterion': '末两档差/首两档差 <= 0.2'}
    if expect == 'step':
        flags = [bool(r['nebula_damage_measurable']) for r in rows]
        has_switch = any(flags) and not all(flags)
        zero_at_switch = any(
            not r['nebula_damage_measurable'] and r['nebula_damage_ratio'] == 0.0
            for r in rows)
        return (has_switch and zero_at_switch,
                {'measurable_flags': flags,
                 'switch_found': has_switch,
                 'damage_zero_at_switch': zero_at_switch,
                 'note': 'F 型可测性判据存活证明：切换点 damage 须恰为 0'
                         '（验证 star_tools 的 raw_change=0.0 分支）'})
    return False, {'reason': '未知期望形态 %r' % expect}


CONSTRUCT_EXPECTATIONS = {
    # A 型实测（NGC6888 全分辨率，q=0.6597）：k 1.00→0.50 时
    # damage 0.2403→0.5701 **单调升**。方向是「k 越小 = 主体越暗 =
    # 误伤越大」，与直觉里的「缩放系数越小读数越小」相反。
    # 第一版标成 ``monotonic_decrease``，V2 报 A 型失败 —— 是标签错，
    # 不是构造错。
    'A': 'monotonic_increase',
    'B_star': 'monotonic_increase',
    'D': 'saturating',
    'E': 'constant_zero',
    'F1': 'step',
    'F2': 'step',
}


def check_v2(rows_by_construct):
    """V2 汇总：逐构造跑期望形态判定。

    不做统一判据 —— 实测形态差异很大（E 恒 0、D 饱和、F 阶跃），
    用同一个「必须严格单调」会把合法构造误判为失败。
    """
    results = {}
    for key, rows in rows_by_construct.items():
        if not rows:
            results[key] = {'passed': False, 'reason': '无有效样本点'}
            continue
        passed, diag = check_monotonicity(rows, CONSTRUCT_EXPECTATIONS[key])
        results[key] = {'passed': passed, **diag}
    return {
        'per_construct': results,
        'all_passed': all(r['passed'] for r in results.values()),
    }


# ── V3 副作用隔离 ─────────────────────────────────────────────────
def check_isolation(rows_by_construct):
    """每个入组构造须通过 E 负对照 + 归类。

    「入组」的含义（实测后修正）：**没有任何构造是 ``pure_damage``** ——
    A 型亮度缩放的 ``high_grad`` 也会动（判据含绝对噪声地板 ``6σ_sl``，
    而 ``σ_sl`` 随缩放同比变化、``grad_orig`` 不变）。
    所以构造只能定 ``damage_limit`` 的**量级**，不能声称「零混淆」。
    这直接限制了本工具能交付什么：可以给「量级正确的候选线」，
    不能给「经隔离验证的精确线」。
    """
    e_rows = rows_by_construct.get('E', [])
    e_passed, e_diag = check_monotonicity(e_rows, 'constant_zero')
    verdict = {'any_pure_damage': False}
    for key, rows in rows_by_construct.items():
        classes = sorted({r['class'] for r in rows})
        if 'pure_damage' in classes:
            verdict['any_pure_damage'] = True
        # A/B 是「单调、可预测、闭式成立」的构造，可用于定线；
        # D（几何错位）与 E（阴性对照）不行；F 改分母只在元验证里用。
        verdict[key] = {
            'classes': classes,
            'usable_for_limit': key in ('A', 'B_star'),
            'note': ('F1/F2 改分母，仅元验证用'
                     if key.startswith('F') else
                     '与 A 共线，不能当独立档' if key == 'B_star' else
                     '几何错位档，confounded' if key == 'D' else
                     '阴性对照，只作红线' if key == 'E' else
                     'confounded（high_grad 会动）⇒ 可定量级非精确线'),
        }
    return {
        'e_control_passed': e_passed,
        'e_control': e_diag,
        'per_construct': verdict,
        # E 未过 ⇒ 整轮不可信：damage 可能被全局亮度效应污染
        'isolation_valid': e_passed,
        'limitation': (
            '' if verdict['any_pure_damage'] else
            '无 pure_damage 构造：定线只能给量级正确的候选值，'
            '并须在报告中标注「未经隔离验证」。'),
    }


# ── V4 数据抖动 ───────────────────────────────────────────────────
def window_positions(height, width, win_h, win_w, n_windows):
    """2D 网格布置子窗位置，覆盖四角、边中与中心。

    **为什么不用对角线**：第一版用同一个 ``frac`` 同时算 y0 与 x0，
    所有子窗沿**一条对角线**排列 —— 实测 n=5 时距画幅中心
    (-615,-346)→(+614,+345)，**四个角一个都采不到**。而角区/边区/
    中心的形态差异正是「位置」这个自变量的全部内容；对角线采样
    让位置只沿一个方向变化，连空间代表性都不具备。

    去重：``win_h == height``（只剩一行可滑）时 ``linspace`` 全给同一
    个 y，``k`` 个位置会退化成 1 个 —— 用 ``np.unique`` 折叠后再截断。
    """
    k = int(np.ceil(np.sqrt(max(1, n_windows))))
    if win_h >= height:
        ys = np.array([0.0])
    else:
        ys = np.linspace(0.0, float(height - win_h), k)
    if win_w >= width:
        xs = np.array([0.0])
    else:
        xs = np.linspace(0.0, float(width - win_w), k)
    positions, seen = [], set()
    for y in ys:
        for x in xs:
            key = (int(round(float(y))), int(round(float(x))))
            if key in seen:
                continue
            seen.add(key)
            positions.append(key)
    return positions[:n_windows]


def _stratum_stats(rows):
    """一组子窗的读数统计（不含判定）。"""
    if not rows:
        return None
    damages = [r['nebula_damage_ratio'] for r in rows]
    seps = [r['subject_separation'] for r in rows]
    return {
        'n': len(rows),
        'damages': damages,
        'p50': round(float(np.percentile(damages, 50)), 6),
        'p95': round(float(np.percentile(damages, 95)), 6),
        'spread': round(max(damages) - min(damages), 6),
        'separation_min': round(min(seps), 4),
        'separation_max': round(max(seps), 4),
    }


def data_jitter(original, starless, target_type, n_windows=9,
                window_frac=0.6, construct_key='A', construct_param=0.6):
    """把同一母版裁成 n 个 2D 网格位置的子窗，各自构造劣化，取散布。

    算法抖动地板实测为 0（代码无随机源，同输入两次运行逐位相同），
    所以这里测的是**数据侧**抖动 —— 它才回答「读数是否可信映射到劣化
    程度」。子窗散布过大 ⇒ 同档不同区域读数不可比，阈值无意义。

    **必须传真实 ``starless``**（第一版没有这个形参，把原图自己当无星图，
    配对变成 ``(orig, orig×k)`` ⇒ ``q = 1/k`` 是代数后果，与像素内容无关；
    实测 9 子窗 ``q`` 极差 0.000000 —— 结构上就测不出数据抖动）。
    详见 ``references/star_damage_gate_calibration.md`` §3.10。

    **按 ``measurable`` 分层**（本函数的核心）：``damage`` 在不可测子窗上
    被强制为 0（``raw_change = 0.0`` 分支），那是「没测」不是「读数为 0」。
    混池化算 spread 得到的是「可测子窗占比」，不是读数稳定性 ——
    实测第一版 NGC6888 读数 ``[0,0,0,0,0.3]``、spread 0.30，
    分层后两组组内 spread 均**精确为 0**。
    """
    orig_ds = downsample(original)
    sl_ds = downsample(starless)
    if orig_ds.shape != sl_ds.shape:
        return {'applicable': False,
                'reason': '原图与无星图几何不一致（%s vs %s），无法配对裁切'
                          % (orig_ds.shape[:2], sl_ds.shape[:2])}
    h, w = orig_ds.shape[:2]
    win_h, win_w = int(h * window_frac), int(w * window_frac)
    positions = window_positions(h, w, win_h, win_w, n_windows)

    rows, measurable, unmeasurable = [], [], []
    for index, (y0, x0) in enumerate(positions):
        sub_o = orig_ds[y0:y0 + win_h, x0:x0 + win_w]
        sub_s = sl_ds[y0:y0 + win_h, x0:x0 + win_w]
        # 配对必须是「健康原图 vs 缩放后的真实无星图」——
        # 缩放原图会让 q 恒等于构造系数，测不出任何数据相关的东西。
        if construct_key == 'A':
            candidate = construct_brightness(sub_s, construct_param)
        elif construct_key == 'B_star':
            candidate = construct_overshoot(sub_o, sub_s, construct_param)
        else:
            raise ValueError('data_jitter 只支持 A / B_star 构造，收到 %r'
                             % (construct_key,))
        row = collect_sample(sub_o, candidate, target_type,
                             'window%d' % index)
        if row is None:
            continue
        row['position'] = [y0, x0]
        rows.append(row)
        (measurable if row['nebula_damage_measurable']
         else unmeasurable).append(row)

    if len(rows) < 2:
        return {'applicable': False, 'reason': '有效子窗不足 2 个',
                'n_windows': len(rows)}

    meas_stats = _stratum_stats(measurable)
    unmeas_stats = _stratum_stats(unmeasurable)
    pooled = _stratum_stats(rows)
    qs = [r['q'] for r in rows if r['q'] is not None]
    total = len(rows)
    out = {
        'applicable': True,
        'n_windows': total,
        'n_windows_requested': n_windows,
        'window_frac': window_frac,
        'window_shape': [win_h, win_w],
        'layout': '2d_grid',
        'construct': '%s=%s' % (construct_key, construct_param),
        'strata': {
            'measurable': meas_stats,
            'unmeasurable': unmeas_stats,
        },
        'unmeasurable_fraction': round(len(unmeasurable) / total, 4),
        # q 极差是「配对是否真的接上了无星图」的直接证据：
        # 若 V4 又退回用原图当无星图，这里会精确为 0。
        'q_values': [round(q, 6) for q in qs],
        'q_spread': (round(max(qs) - min(qs), 6) if len(qs) >= 2 else None),
        'algorithm_jitter_floor': 0.0,
    }
    if out['unmeasurable_fraction'] > 0.5:
        out['unmeasurable_warning'] = (
            '该母版仅 %.0f%% 区域可测（separation 低于判据下限）—— '
            'damage 阈值在此数据上代表性有限'
            % (out['unmeasurable_fraction'] * 100.0))
    # 未分层的 spread 保留但**明确标为不可用**：
    # 混池化时它衡量的是「可测子窗占比」，不是读数稳定性。留着是為了
    # 让旧报告能看出问题，不是因为它有用。
    out['pooled_spread_misleading'] = pooled['spread']
    out['pooled_note'] = ('混合了可测与不可测子窗，不可用于定线；'
                          '请用 strata.measurable.spread')
    if meas_stats is None:
        out['verdict'] = 'no_measurable_window'
        out['reason'] = ('全部 %d 个子窗均不可测 —— 该母版无法用于'
                         '测抖动' % total)
    else:
        out['verdict'] = ('insufficient_windows'
                          if meas_stats['n'] < MIN_MEASURABLE_WINDOWS
                          else 'measured')
        if meas_stats['n'] < MIN_MEASURABLE_WINDOWS:
            out['reason'] = ('可测子窗仅 %d 个 < %d，抖动未测'
                             % (meas_stats['n'], MIN_MEASURABLE_WINDOWS))
    return out


# ── 门禁口径回放（§5.5）────────────────────────────────────────
def replay_gate_states(rows_by_construct, healthy_row, target_type):
    """定线不是看数值，而是把样本喂门禁看 ``gate_state``。

    硬线的可达性检查（§5.3）也在此汇报：``repair_score`` 里的
    ``min(damage * 2.0, 0.45)`` 在 damage 达 0.225 后饱和，且门禁
    **先判score** ⇒ 硬线设在饱和点之上是不可达的。
    """
    out = {'healthy': healthy_row['gate_state'], 'by_construct': {}}
    for key, rows in rows_by_construct.items():
        states = [r['gate_state'] for r in rows]
        out['by_construct'][key] = {
            'states': states,
            'worst': ('hard_failed' if 'hard_failed' in states
                      else 'advisory' if 'advisory' in states else 'ok'),
        }
    prof = get_profile(target_type)
    limit = prof.damage_limit
    score_saturation = 0.225
    out['score_saturation_damage'] = score_saturation
    if limit is None:
        # 阈值未标定 ⇒ **没有**「硬线」可言。不能让 None * multiplier
        # 崩在这里，也不能报一个假的 0.0 —— 那是把「未标定」伪装成
        # 「阈值极宽松」，方向完全相反。
        out.update({
            'damage_limit': None,
            'damage_limit_calibrated': False,
            'hard_line': None,
            'hard_reachable': None,
            'note': ('该档 damage 阈值未标定（跨样本口径跨度达 10 倍，'
                     '夹逼无解）⇒ 门禁走 uncalibrated：误伤读数记录但不判定'),
        })
        return out
    hard = limit * prof.advisory_multiplier
    out['damage_limit'] = limit
    out['damage_limit_calibrated'] = True
    out['hard_line'] = hard
    out['hard_reachable'] = hard <= score_saturation
    out['note'] = (
        '硬线 %.2f ≤ score 饱和点 %.3f ⇒ damage 分支可判定' % (hard, score_saturation)
        if hard <= score_saturation else
        '硬线 %.2f > score 饱和点 %.3f ⇒ damage > %.3f 后由 score 分支'
        '实际把关，damage 分支不可判定'
        % (hard, score_saturation, score_saturation))
    return out


# ── V5 定线：劣化组/健康组的双向夹逼 ──────────────────────────────
def check_degraded_separation(sample, jitter_spread=None,
                              ratio=LIGHTEST_DEGRADED_RATIO,
                              construct_key='A'):
    """V5：把一份样本拆成「健康态」与「最轻劣化档」两段，报出四段数据。

    这是定线的**必要输入**，四段缺一不可：

    ==============  ============================================
    ``healthy``        ``f=1.00`` 档 —— **等于**该样本真实的
                       StarNet2 读数（构造的零参照点）
    ``lightest_degraded``  ``f=0.95`` 档 —— 第一个真正的劣化档
    ``jitter_bound``    V4 的 ``measurable.spread``
    ``overlap``         两者之差，**必须 > 0**
    ==============  ============================================

    ``overlap <= 0`` ⇒ **拒绝该构造**：劣化组没有越过健康态，
    此时任何「下界」都是把健康数据判成劣化的线。这是
    ``star_damage_gate_calibration.md`` §5.6「跨不过健康组上界
    则弃用」的自动化。

    ⚠ 为什么必须用**相对倍率**网格：绝对 q 阶梯下，健康样本的
    ``q`` 本身就落在阶梯中段（真实 StarNet2 的 q 天生 < 1）⇒
    5 份实测样本的「最轻非零档」恒为 0.0450，而健康态是
    0.0357~0.3580 ⇒ 4/5 份的「劣化」比「健康」还轻。
    """
    healthy = sample.get('healthy') or {}
    rows = (sample.get('constructs') or {}).get(construct_key) or []
    if not rows:
        return {'applicable': False,
                'reason': '样本缺少 %s 型构造读数' % construct_key}
    if not healthy.get('nebula_damage_measurable', True):
        return {'applicable': False,
                'reason': '健康态不可测（separation 低于下限），不参与定线',
                'separation': healthy.get('subject_separation')}

    # f=1.00 是健康态本身；f=ratio 是最轻劣化档。ratio 用绝对匹配而非
    # 「第一个 > 健康的档」，因为浮点上二者可能差 1e-9（闭式预测与
    # 实测的舍入），按大小找会漏掉健康档本身。
    target_ratio = float(rows[0].get('ratio', 0.0)) if rows else 0.0
    by_ratio = {round(float(r.get('ratio', -1.0)), 4): r for r in rows}
    healthy_row = by_ratio.get(round(HEALTHY_RATIO, 4))
    degraded_row = by_ratio.get(round(ratio, 4))
    if healthy_row is None or degraded_row is None:
        return {'applicable': False,
                'reason': '构造读数里找不到 f=%.2f 或 f=%.2f 档'
                          % (HEALTHY_RATIO, ratio),
                'available_ratios': sorted(by_ratio)}

    healthy_damage = float(healthy_row['nebula_damage_ratio'])
    degraded_damage = float(degraded_row['nebula_damage_ratio'])
    overlap = degraded_damage - healthy_damage
    out = {
        'applicable': True,
        'construct': construct_key,
        'healthy_ratio': HEALTHY_RATIO,
        'degraded_ratio': ratio,
        'healthy_damage': round(healthy_damage, 6),
        'lightest_degraded': round(degraded_damage, 6),
        'overlap': round(overlap, 6),
        'q_healthy': healthy.get('q'),
        'healthy_matches_baseline': abs(
            healthy_damage - float(healthy.get('nebula_damage_ratio',
                                               -1.0))) < 1e-4,
    }
    if jitter_spread is not None:
        out['jitter_bound'] = round(float(jitter_spread), 6)
    if overlap <= 0:
        out['usable'] = False
        out['reject_reason'] = (
            '劣化组未越过健康态（最轻档 %.4f ≤ 健康态 %.4f）—— '
            '此构造无法区分「健康」与「劣化」，弃用'
            % (degraded_damage, healthy_damage))
    else:
        out['usable'] = True
    return out


def squeeze_line(separation_rows, healthy_damage_values, relax=1.0,
                 percentile=50, min_separation_multiple=3.0):
    """跨样本夹逼，给出 ``damage_limit`` **候选值**。

    ``damage_limit`` 的语义是「**接纳线**」：``damage <= limit`` 即接受。
    因此两条约束的方向是**相反**的：

    ==================  ==================================================
    ``healthy_ceiling``   ``max(健康读数) × relax`` —— 线下界。
    健康数据必须全部被接受 ⇒ 线不能低于它，否则误杀健康数据。
    ``degraded_floor``    ``min(最轻劣化档)`` —— 线上界。
    劣化数据必须全部被拒绝 ⇒ 线不能高于它，否则漏放真正的劣化。
    ==================  ==================================================

    ⇒ 可行区间是 ``[healthy_ceiling, degraded_floor]``，
    **空区间 ⇒ ``not_lineable``**，报越界多少，**不硬凑一个数**。

    ⚠ **方向错误会算出荒谬的线**：v0.1.36 的 §4.4 写成
    ``min(劣化下界, 健康上界 × relax)``，那个 min 恒取劣化下界
    （实测 0.0450，而健康态是 0.0357~0.3580）⇒ 线比健康数据还低
    ⇒ 严4.4 倍，会拒绝几乎所有健康数据。

    实测（5 份可测样本，relax=1.0）：
    ``healthy_ceiling = 0.3580``、``degraded_floor = 0.2733``
    ⇒ **区间为空**，越界 0.0847。

    这不是数据噪声，而是**结构性结论**：健康基线在样本间跨 10 倍
    （0.0357~0.3580），而「最轻劣化」相对各自健康态只抬高约 0.03
    ⇒ **单一绝对阈值无法区分健康与劣化**。正确处置是报不可定线，
    而不是放宽任一侧。
    """
    usable = [row for row in separation_rows
              if row.get('applicable') and row.get('usable')]
    out = {
        'relax_factor': relax,
        'percentile': percentile,
        'min_separation_multiple': min_separation_multiple,
        'n_samples': len(separation_rows),
        'n_usable': len(usable),
        # 记**样本名**而不是 reason：验收者要知道是哪几份被排除，
        # 只给一句 reason 无法定位。
        'excluded': [row.get('target', '?') for row in separation_rows
                     if not (row.get('applicable') and row.get('usable'))],
        'excluded_reasons': [row.get('reject_reason')
                             or row.get('reason') or 'unknown'
                             for row in separation_rows
                             if not (row.get('applicable')
                                     and row.get('usable'))],
    }
    if not usable:
        out.update({'status': 'not_lineable',
                    'reason': '没有任何样本的劣化组越过其健康态'})
        return out

    degraded = [row['lightest_degraded'] for row in usable]
    healthy = [float(v) for v in healthy_damage_values if v is not None]
    out.update({
        'degraded_values': [round(v, 6) for v in sorted(degraded)],
        'healthy_values': [round(v, 6) for v in sorted(healthy)],
    })
    if not healthy:
        out.update({'status': 'not_lineable',
                    'reason': '缺少健康组读数，无法给下界'})
        return out

    # 线上界取 min（最容易被漏放的那份劣化），下界取 max（最差的那份健康）。
    degraded_floor = min(degraded)
    healthy_ceiling = max(healthy) * relax
    out.update({
        'degraded_floor': round(degraded_floor, 6),
        'degraded_p50': round(float(np.percentile(degraded, 50)), 6),
        'degraded_p75': round(float(np.percentile(degraded, 75)), 6),
        'healthy_ceiling': round(healthy_ceiling, 6),
        'healthy_max': round(max(healthy), 6),
        'healthy_min': round(min(healthy), 6),
    })
    if healthy_ceiling > degraded_floor:
        out.update({
            'status': 'not_lineable',
            'crossing': round(healthy_ceiling - degraded_floor, 6),
            'reason': (
                '健康下界 %.4f 高于劣化上界 %.4f（越界 %.4f）—— '
                '健康组基线在样本间跨 %.1f 倍（%.4f~%.4f），而「最轻劣化」'
                '相对各自健康态只抬高约 %.4f；**单一绝对 damage 阈值'
                '无法区分健康与劣化**。'
                % (healthy_ceiling, degraded_floor,
                   healthy_ceiling - degraded_floor,
                   max(healthy) / max(min(healthy), 1e-8),
                   min(healthy), max(healthy),
                   float(np.median([row['overlap'] for row in usable])))),
            'structural': True,
            'remedy': ('需把判据改为**相对基线**（例如以 q_healthy 归一化的'
                       'damage 增量），或按 target_type 再细分形态；'
                       '**不得**为凑出线而放宽任一侧'),
        })
        return out

    # 区间不空：取 degraded_p50 作为候选（比 floor 宽一档，避免单点决定）
    candidate = float(np.percentile(degraded, percentile))
    candidate = min(max(candidate, healthy_ceiling), degraded_floor)
    out.update({
        'status': 'lineable',
        'damage_limit': round(candidate, 6),
        'advisory_hard_line': round(candidate * 2.0, 6),
        'feasible_interval': [round(healthy_ceiling, 6),
                              round(degraded_floor, 6)],
        'basis': ('夹逼区间 [%.4f, %.4f]，取 p%d = %.4f'
                  % (healthy_ceiling, degraded_floor, percentile, candidate)),
    })
    spreads = [row.get('jitter_bound') for row in usable
               if row.get('jitter_bound') is not None]
    if spreads:
        worst_jitter = max(spreads)
        # 间隔必须是**实际可用的最窄间隔** = degraded_floor − healthy_ceiling。
        # 用 degraded_floor − max(healthy) 会忽略 relax 系数，把间隔算大
        # （实测集中夹具：0.300−0.110=0.190 vs 正确的 0.300−0.165=0.135）
        # ⇒ 漏报抖动告警。
        gap = degraded_floor - healthy_ceiling
        out['worst_jitter'] = round(worst_jitter, 6)
        out['separation_gap'] = round(gap, 6)
        out['separation_over_jitter'] = (
            round(gap / worst_jitter, 3) if worst_jitter else None)
        if worst_jitter > 0 and gap < min_separation_multiple * worst_jitter:
            out['separation_warning'] = (
                '最窄可行间隔 %.4f（degraded_floor %.4f − healthy_ceiling '
                '%.4f）小于 %d× 最坏抖动 %.4f —— 即使区间不空，'
                '该线也不足以区分健康与劣化'
                % (gap, degraded_floor, healthy_ceiling,
                   min_separation_multiple, worst_jitter))
    return out


# ── 样本载入 ──────────────────────────────────────────────────────
def load_work_pair(calib_root, name):
    """从管线 work 目录取 (分离参考, 无星图)。

    关键：``04_starless_linear.tif`` 在**回退**时不存在（v0.1.34 起回退
    落 ``04_starless_fallback.tif``），而回退文件是**原图副本** ——
    拿它当健康态样本会让 ``q ≡ 1``、damage ≡ 0，构造全部失效。
    所以这里**拒绝**用 fallback 文件，只接受真正的无星图。
    """
    work = Path(calib_root) / name / 'work'
    ref_path = work / '04_separation_reference.tif'
    for starless_name in ('04_starless_linear.tif', '04_starless_external.tif'):
        starless_path = work / starless_name
        if ref_path.exists() and starless_path.exists():
            from fits_io import read_image
            original, _ = read_image(str(ref_path))
            starless, _ = read_image(str(starless_path))
            return original, starless, starless_name
    return None, None, None


def read_result_target_type(calib_root, name):
    """从 result.json 读该次运行的 effective target_type。"""
    path = Path(calib_root) / name / 'result.json'
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return ((data.get('effective_config') or {}).get('target_type')
            or data.get('target_type'))


def main():
    parser = argparse.ArgumentParser(
        description='去星误伤门禁的劣化对照标定（V1-V4 + 门禁回放）')
    parser.add_argument('--corpus', nargs='+', required=True,
                        help='母版名目录（位于 --calib-root 下）')
    parser.add_argument('--calib-root', default='/tmp/calib')
    parser.add_argument('--json', dest='json_output', default=None,
                        help='独立 JSON 输出路径（不写回 result.json）')
    parser.add_argument('--replay', action='store_true',
                        help='额外做门禁口径回放（§5.5）')
    parser.add_argument('--jitter-windows', type=int,
                        default=DEFAULT_JITTER_WINDOWS,
                        help='V4 数据抖动的子窗数（9=3×3 网格；'
                             '16=4×4 更密但每子窗更小）')
    args = parser.parse_args()

    report = {
        'schema': 'deep-sky-processor.star-damage-calibration/v1',
        'calib_max_side': CALIB_MAX_SIDE,
        'corpus_size_required': CALIBRATION_CORPUS_SIZE,
        'deprecated_fields': sorted(DEPRECATED_FIELDS),
        'samples': [],
        'skipped': [],
    }

    for name in args.corpus:
        original, starless, source = load_work_pair(args.calib_root, name)
        if original is None:
            report['skipped'].append({
                'target': name,
                'reason': '缺少 04_separation_reference.tif 或真实无星图产物'
                          '（回退副本不算 —— 它是原图副本，q 恒 1）',
            })
            continue
        target_type = read_result_target_type(args.calib_root, name)
        healthy = collect_sample(original, starless, target_type, 'healthy')
        if healthy is None:
            report['skipped'].append({'target': name,
                                      'reason': '几何不匹配'})
            continue
        rows_by_construct = run_constructs(original, starless, healthy,
                                           target_type)
        sample = {
            'target': name,
            'target_type': target_type,
            'starless_source': source,
            'healthy': {k: v for k, v in healthy.items()
                        if not k.startswith('_')},
            'v1_closed_form': check_closed_form(healthy, rows_by_construct),
            'v2_monotonicity': check_v2(rows_by_construct),
            'v3_isolation': check_isolation(rows_by_construct),
            'v4_data_jitter': data_jitter(
                original, starless, target_type,
                n_windows=args.jitter_windows),
            'constructs': {
                key: [{k: v for k, v in row.items() if not k.startswith('_')}
                      for row in rows]
                for key, rows in rows_by_construct.items()
            },
        }
        if args.replay:
            sample['gate_replay'] = replay_gate_states(rows_by_construct,
                                                       healthy, target_type)
        report['samples'].append(sample)

    report['verdict'] = summarize(report)
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_output:
        Path(args.json_output).write_text(payload, encoding='utf-8')
    print(payload)
    return 0


def summarize(report):
    """跨样本汇总：**不给线，只报「能不能定线」**（§5.4/§5.6）。

    三态区分（实测踩过）：``passed`` / ``not applicable`` / ``failed``。
    把「没测」当成「不符」会得出「闭式不成立」的假结论 —— M17 的
    separation=1.017 < 1.30 使 damage 被强制为 0，闭式无从验证，
    V1 报 worst=0.41 看着像失败，实际是**样本不可测**。
    """
    if not report['samples']:
        return {'status': 'no_data',
                'note': '无有效样本 —— 先跑管线产出真实无星图'}

    per_type = {}
    for sample in report['samples']:
        key = sample.get('target_type') or 'unknown'
        per_type.setdefault(key, []).append(sample['target'])

    v1_failed, v1_na, iso_failed, unmeasurable = [], [], [], []
    jitter_unmeasured, jitter_too_large = [], []
    jitter_strata = {}
    for sample in report['samples']:
        v1 = sample.get('v1_closed_form') or {}
        if not v1.get('applicable', False):
            v1_na.append(sample['target'])
        elif not v1.get('passed'):
            v1_failed.append(sample['target'])
        if not (sample.get('v3_isolation') or {}).get('isolation_valid'):
            iso_failed.append(sample['target'])
        if not (sample.get('healthy') or {}).get('nebula_damage_measurable',
                                                 True):
            unmeasurable.append(sample['target'])

        # V4 此前完全不参与判定 —— 这正是它带 bug 却没被发现的直接原因
        # （跑出什么都影响不了 verdict，「没有反馈」被当成「没问题」）。
        v4 = sample.get('v4_data_jitter') or {}
        if not v4.get('applicable'):
            jitter_unmeasured.append(sample['target'])
            # 不能 continue：抖动未测的样本**仍要参与 V5 分离** ——
            # V4 缺的是 spread（抖动可信度），V5 要的是构造读数
            # （健康态 vs 劣化档），两者输入不同。v0.1.36 第一版在这里
            # continue，导致这类样本的分离信息整份丢失。
        else:
            meas = (v4.get('strata') or {}).get('measurable')
            jitter_strata[sample['target']] = {
                'measurable_n': (meas or {}).get('n', 0),
                'measurable_spread': (meas or {}).get('spread'),
                'unmeasurable_fraction': v4.get('unmeasurable_fraction'),
                'q_spread': v4.get('q_spread'),
                'verdict': v4.get('verdict'),
            }
            if v4.get('verdict') == 'no_measurable_window':
                jitter_unmeasured.append(sample['target'])
            elif v4.get('verdict') == 'insufficient_windows':
                jitter_unmeasured.append(sample['target'])

    # 档内 damage 全距（健康组），用作「抖动是否大于信号」的参照。
    # 用 .get 而非 s['healthy']：缺键时前者返回 {}、后者抛 KeyError ——
    # 上层循环已经用 (s.get('healthy') or {}) 做了防御，这里必须一致。
    healthy_ranges = [(s.get('healthy') or {}).get('nebula_damage_ratio')
                      for s in report['samples']
                      if (s.get('healthy') or {}).get(
                          'nebula_damage_measurable', True)
                      and (s.get('healthy') or {}).get(
                          'nebula_damage_ratio') is not None]

    # ── V5 定线：逐样本的「健康态 vs 最轻劣化档」分离 ──
    # 必须放在 healthy_ranges 之后：夹逼的上界要用健康组读数。
    # （第一版把它插在前面 ⇒ UnboundLocalError。）
    separation_rows = []
    for sample in report['samples']:
        spread = ((jitter_strata.get(sample['target']) or {})
                  .get('measurable_spread'))
        row = check_degraded_separation(sample, jitter_spread=spread)
        row['target'] = sample['target']
        separation_rows.append(row)
    line = squeeze_line(separation_rows, healthy_ranges,
                        relax=LINE_RELAX_FACTOR)

    pooled_range = (max(healthy_ranges) - min(healthy_ranges)
                    if len(healthy_ranges) >= 2 else None)
    for target, entry in jitter_strata.items():
        spread = entry['measurable_spread']
        if (spread is not None and pooled_range and entry['measurable_n'] >= 2
                and spread > pooled_range):
            jitter_too_large.append(target)

    reasons = []
    if v1_failed:
        reasons.append('V1 闭式未通过（构造有未知副作用或削顶）：%s'
                       % ', '.join(v1_failed))
    if iso_failed:
        reasons.append('E 型阴性对照未通过（掩膜外损伤推高了 damage，'
                       '说明 damage 不是纯主体局部量）：%s'
                       % ', '.join(iso_failed))
    if unmeasurable:
        reasons.append('这些样本的误伤指标不可测（separation < 下限），'
                       '只能记 0、不能参与定线：%s' % ', '.join(unmeasurable))
    if jitter_unmeasured:
        reasons.append('数据抖动**未测**（可测子窗不足 %d 个）：%s —— '
                       '放宽的前置条件是「抖动已知且可接受」，'
                       '「未知」不等于「可接受」'
                       % (MIN_MEASURABLE_WINDOWS,
                          ', '.join(sorted(set(jitter_unmeasured)))))
    if jitter_too_large:
        reasons.append('组内数据抖动大于档内 damage 全距（%.4f），'
                       '单点定的线无意义：%s'
                       % (pooled_range, ', '.join(sorted(jitter_too_large))))
    if line.get('status') == 'not_lineable':
        reasons.append('V5 双向夹逼无解：%s' % line.get('reason'))
    insufficient = [k for k, v in per_type.items()
                    if len(v) < CALIBRATION_CORPUS_SIZE]
    for key in insufficient:
        reasons.append('%s 档可测样本 %d < %d ⇒ PROVISIONAL'
                       % (key,
                          len([t for t in per_type[key]
                               if t not in unmeasurable]),
                          CALIBRATION_CORPUS_SIZE))
    return {
        'status': ('blocked' if reasons else 'ready_to_line'),
        'blocking_reasons': reasons,
        'per_target_type': per_type,
        'v1_not_applicable': v1_na,
        'unmeasurable_samples': unmeasurable,
        'v1_all_applicable_passed': not v1_failed and bool(report['samples']),
        'isolation_all_passed': not iso_failed,
        'jitter_strata': jitter_strata,
        'jitter_unmeasured': sorted(set(jitter_unmeasured)),
        'jitter_exceeds_damage_range': sorted(set(jitter_too_large)),
        'pooled_damage_range': (round(pooled_range, 6)
                                if pooled_range is not None else None),
        'v5_separation': separation_rows,
        'line_candidate': line,
        'line_recommendation': (
            ('不可定线：先解除 blocking_reasons。健康组是锚 —— '
             '跨不过健康组上界的劣化组一律弃用，绝不从其他 target_type '
             '借数值。') if reasons else
            ('夹逼成功，候选 damage_limit=%.4f（见 line_candidate）。'
             '**尚未采纳** —— 落值前必须做门禁口径回放：把健康组喂'
             '_star_removal_gate，要求全部 ok。'
             % line['damage_limit'])),
    }


if __name__ == '__main__':
    raise SystemExit(main())
