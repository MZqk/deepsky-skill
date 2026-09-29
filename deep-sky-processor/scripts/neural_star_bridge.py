"""External Neural Network Star Removal Bridge & Recommendation Engine.

负责深空天文全流程中与外部神经网络去星工具（StarNet2 / StarNet++ / StarXTerminator 等）的
智能环境探测、多维度天体物理推荐决策、标准 16-bit 线性载荷导出、可执行命令生成与回流真实性审查。
遵循【零额外依赖承诺】，纯标准库 + NumPy 实现。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from skimage.io import imsave


def detect_neural_starnet_environment(custom_path: Optional[str] = None) -> Dict[str, Any]:
    """
    智能探查本机 StarNet2 / StarNet++ 运行环境与硬件加速支持。
    
    返回字典结构:
      - available: bool, 是否存在可用可执行二进制
      - executable: Optional[str], 绝对路径
      - version: Optional[str], 版本号 (如 "2.5.4")
      - backend: Optional[str], 计算后端 (如 "CoreML", "ONNX", "CPU")
      - supports_unscreen: bool, 是否原生支持 --unscreen 星点层剥离
      - supports_stride: bool, 是否支持 -s/--stride 步长控制
      - recommendation_readiness: str, "ready" | "not_installed" | "executable_error"
    """
    from star_tools import find_starnet_executable

    if custom_path:
        custom_path_abs = os.path.abspath(os.path.expanduser(custom_path))
        if not os.path.isfile(custom_path_abs):
            return {
                "available": False,
                "executable": None,
                "version": None,
                "backend": None,
                "supports_unscreen": False,
                "supports_stride": False,
                "recommendation_readiness": "not_installed",
            }
        exe_path = custom_path_abs
    else:
        exe_path = find_starnet_executable()

    if not exe_path or not os.path.isfile(exe_path):
        return {
            "available": False,
            "executable": None,
            "version": None,
            "backend": None,
            "supports_unscreen": False,
            "supports_stride": False,
            "recommendation_readiness": "not_installed",
        }

    info: Dict[str, Any] = {
        "available": True,
        "executable": os.path.abspath(exe_path),
        "version": "unknown",
        "backend": "unknown",
        "supports_unscreen": False,
        "supports_stride": True,
        "recommendation_readiness": "ready",
    }

    # 尝试运行 --help 或 --version 获取元数据
    try:
        parent_dir = os.path.dirname(os.path.abspath(exe_path))
        env = os.environ.copy()
        if sys.platform == "darwin":
            dyld_path = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = parent_dir + (":" + dyld_path if dyld_path else "")
        else:
            ld_path = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = parent_dir + (":" + ld_path if ld_path else "")

        help_res = subprocess.run(
            [exe_path, "--help"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
        )
        output_text = (help_res.stdout or "") + "\n" + (help_res.stderr or "")

        # 版本解析 (如 StarNet2 v2.5.4)
        ver_match = re.search(r"StarNet2\s+v?([\d\.]+)", output_text, re.IGNORECASE)
        if ver_match:
            info["version"] = ver_match.group(1)
        elif "starnet++" in exe_path.lower():
            info["version"] = "v1/v2 legacy"

        # 后端检测 (如 CoreML backend)
        if "CoreML" in output_text:
            info["backend"] = "CoreML (Apple Silicon Hardware Accelerated)"
        elif "ONNX" in output_text or "DirectML" in output_text:
            info["backend"] = "ONNX / GPU"
        elif "CPU" in output_text:
            info["backend"] = "CPU"
        else:
            info["backend"] = "Native Binary"

        # 功能支持检测
        if "--unscreen" in output_text or "-n <string>" in output_text:
            info["supports_unscreen"] = True
        if "--stride" in output_text or "-s <int>" in output_text:
            info["supports_stride"] = True

    except Exception:
        info["recommendation_readiness"] = "executable_error"

    return info


def evaluate_neural_bridge_recommendation(
    image_shape: Tuple[int, ...],
    star_density: Optional[str] = None,
    star_area_ratio: Optional[float] = None,
    star_removal_quality: Optional[Dict[str, Any]] = None,
    target_type: Optional[str] = None,
    env_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    多维度天体物理智能评估：是否推荐使用外部神经网络去星。

    考量维度:
    1. 天体物理红线：球状星团/疏散星团以星点为主体，严禁去星 (DISALLOWED)；
    2. 内置去星质量评分：残星率高或星云受损时强烈推荐 (STRONGLY_RECOMMENDED)；
    3. 星场繁密度与覆盖率：dense/very_dense 且为发射星云/星系时推荐使用 (RECOMMENDED)；
    4. 本地环境就绪情况：若本地已部署 StarNet2，提升就绪建议。
    """
    from target_rules import STAR_DOMINANT_TYPES

    if env_info is None:
        env_info = detect_neural_starnet_environment()

    # 1. 严格遵守天体物理红线 (星团目标禁止去星)
    norm_type = str(target_type or "").lower().strip()
    if norm_type in STAR_DOMINANT_TYPES or norm_type in ("globular_cluster", "open_cluster", "star_cluster"):
        return {
            "recommended": False,
            "level": "DISALLOWED",
            "priority": "none",
            "target_type": target_type,
            "reason": f"天体类型为 {target_type}（星点即主体目标），天体物理规则严禁执行去星分离！",
            "local_environment": env_info,
            "action_advice": "保持全流程完整星点，使用星团专用柔和曲线与防饱和拉伸。",
        }

    # 2. 提取特征指标
    is_very_dense = (star_density == "very_dense")
    is_dense = (star_density in ("dense", "very_dense"))
    high_star_ratio = (star_area_ratio is not None and star_area_ratio > 0.12)

    needs_starnet_plus = False
    quality_score = 1.0
    damage_ratio = 0.0
    quality_rating = "good"
    if star_removal_quality:
        needs_starnet_plus = bool(star_removal_quality.get("needs_starnet_plus", False))
        quality_score = float(star_removal_quality.get("repair_quality_score", 1.0))
        damage_ratio = float(star_removal_quality.get("nebula_damage_ratio", 0.0))
        quality_rating = str(star_removal_quality.get("quality", "good"))

    # 3. 智能决策分级
    if needs_starnet_plus or quality_rating == "poor" or damage_ratio > 0.15:
        level = "STRONGLY_RECOMMENDED"
        recommended = True
        reason = (
            f"内置形态学去星质量受限 (评分={quality_score:.2f}, 损伤比={damage_ratio:.1%})；"
            "强烈建议桥接外部神经网络去星 (StarNet++ v2) 彻底消除残星并保护星云微弱结构！"
        )
    elif is_very_dense or (is_dense and high_star_ratio):
        level = "STRONGLY_RECOMMENDED"
        recommended = True
        reason = (
            f"当前视场星场极度繁密 (密度={star_density}, 星点覆盖率={star_area_ratio if star_area_ratio is not None else 0.0:.1%})；"
            "神经网络去星可显著降低密集微星对背景和星云的干扰。"
        )
    elif is_dense or quality_rating == "marginal" or high_star_ratio:
        level = "RECOMMENDED"
        recommended = True
        reason = (
            f"星场密集且天体为 {target_type or '深空天体'}，建议桥接 StarNet++ 外部无星层以追求更高信噪比。"
        )
    elif env_info.get("available") and norm_type in ("emission_nebula", "galaxy", "supernova_remnant"):
        level = "RECOMMENDED"
        recommended = True
        reason = (
            f"检测到本机已就绪高性能 {env_info.get('version', '')} {env_info.get('backend', '')}，"
            f"建议对 {target_type} 启用神经网络去星以获取极致平滑无星底图。"
        )
    else:
        level = "OPTIONAL"
        recommended = False
        reason = "星场密度适中且内置形态学去星质量良好，外部神经网络去星为可选增强项。"

    # 4. 生成行动建议
    if env_info.get("available"):
        action_advice = (
            f"本机已安装 StarNet2 ({env_info['executable']})，"
            "可在命令行直接追加 `--use-starnet` 一键自动调用原生神经网络去星！"
        )
    else:
        action_advice = (
            "可导出当前线性标准载荷 `starnet_payload_linear.tif`，"
            "使用外部 StarNet++ v2 CLI 执行去星后，通过 `--external-starless` 回流管线。"
        )

    return {
        "recommended": recommended,
        "level": level,
        "target_type": target_type,
        "star_density": star_density,
        "star_area_ratio": star_area_ratio,
        "repair_quality_score": quality_score,
        "reason": reason,
        "local_environment": env_info,
        "action_advice": action_advice,
    }


STARNET_PAYLOAD_META = "starnet_payload_meta.json"


def write_starnet_payload_meta(work_dir: str, *, domain: str, midtones: Optional[float],
                               shadows: float = 0.0,
                               source_median: Optional[float] = None) -> str:
    """写载荷 sidecar，记录域与拉伸参数，供回流侧自动解析。

    没有它的话，用户必须手工记住导出时用的 midtones 并在回流时重传，一旦漏传
    逆变换就会用错参数、画面悄悄错色。
    """
    import json

    os.makedirs(work_dir, exist_ok=True)
    path = os.path.join(work_dir, STARNET_PAYLOAD_META)
    payload = {
        "schema_version": "1.0",
        "domain": str(domain),
        "midtones": None if midtones is None else float(midtones),
        "shadows": float(shadows),
        "source_median": None if source_median is None else float(source_median),
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path


def read_starnet_payload_meta(*candidates: Optional[str]) -> Optional[Dict[str, Any]]:
    """按顺序在候选目录/路径中查找 sidecar，返回首个可解析的结果。"""
    import json

    for candidate in candidates:
        if not candidate:
            continue
        meta_path = (candidate if str(candidate).endswith(".json")
                     else os.path.join(str(candidate), STARNET_PAYLOAD_META))
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def export_starless_payload(image: np.ndarray, output_path: str,
                            domain: str = "linear",
                            midtones: Optional[float] = None,
                            shadows: float = 0.0) -> str:
    """
    将图像导出为 StarNet2 兼容的 16-bit TIFF 格式 (RGB, uint16)。

    domain:
      - "linear"（默认，向后兼容）：原样量化。
      - "mtf"：先做 MTF 拉伸再量化。**极暗数据必须用这个**——线性直接量化会让
        弱色通道被压成 0（实测星云区 G 通道 93% 像素为 0），StarNet2 输出随之
        丢掉 OIII，拉伸后整片星云变纯红。先拉伸可把 G 的量化级数从 70 抬到 5800。

    midtones 必须 < 0.5 才有实际拉伸效果（0.5 是恒等映射）；由调用方通过
    `derive_mtf_midtones` 按图像背景推导，并写入 sidecar 供回流逆变换使用。

    技术细节:
      - 确保数值范围精准映射至 [0, 65535]；
      - 保持三通道 RGB，剔除 Alpha 通道；
      - 写入标准无损 TIFF。
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    img_arr = np.asarray(image, dtype=np.float32)

    # 确保 [0, 1] 裁剪
    img_arr = np.clip(img_arr, 0.0, 1.0)

    # 通道对齐
    if img_arr.ndim == 2:
        rgb = np.stack([img_arr] * 3, axis=-1)
    elif img_arr.ndim == 3:
        if img_arr.shape[2] == 1:
            rgb = np.concatenate([img_arr] * 3, axis=-1)
        else:
            rgb = img_arr[..., :3]
    else:
        raise ValueError(f"不支持的图像形状导出为 StarNet 载荷: {img_arr.shape}")

    if domain == "mtf":
        from stretch import mtf_stretch

        m = 0.25 if midtones is None else float(midtones)
        rgb = mtf_stretch(rgb, midtones=m, shadows=float(shadows))
        rgb = np.clip(rgb, 0.0, 1.0)

    # 转换为 16-bit uint16
    uint16_img = (rgb * 65535.0).round().astype(np.uint16)

    # 使用 skimage / tifffile 写入无损 16-bit TIFF
    imsave(output_path, uint16_img, check_contrast=False)
    return os.path.abspath(output_path)


def build_bridge_run_instructions(
    payload_path: str,
    output_starless_path: str,
    input_fits_path: str,
    final_output_path: str,
    env_info: Dict[str, Any],
    work_dir: str,
    extra_pipeline_args: Optional[str] = None,
    domain: str = "mtf",
    stride: int = 128,
    midtones: Optional[float] = None,
) -> Dict[str, Any]:
    """生成直接可执行的外部 CLI 运行命令与后续管线一键恢复命令。

    domain: 载荷所在域。
      - "mtf"（默认）：载荷**已做 MTF 拉伸**，星点层是显示域语义 → 用 `-n/--unscreen`
        （Screen 混合）。见 references/external_tools.md 的星点层语义表。
      - "linear"：载荷为线性近黑数据 → 用 `-m/--mask`（可加性星点层）。

    为什么默认 mtf：极暗线性数据直接量化为 16-bit 会让弱色通道被量化归零
    （实测星云区 G 通道 93% 像素精确为 0），StarNet2 输出随之丢失 OIII。
    先做 MTF 拉伸可把弱通道抬进可用范围（实测 G 的量化级数 70 → 5800）。

    stride 默认 128（旧版硬编码 256）。外部桥接面向最难/最暗、值得慢跑的帧，
    细步长收益更大；内置 --use-starnet 路径的默认值另由 --starnet-stride 控制。
    """
    exe = env_info.get("executable") or "starnet2"
    supports_unscreen = bool(env_info.get("supports_unscreen", False))
    stride = int(stride)

    stars_layer_name = "stars_unscreen.tif" if domain == "mtf" else "stars_mask.tif"
    stars_layer_path = os.path.join(
        os.path.dirname(output_starless_path), stars_layer_name
    )

    # 外部去星执行命令（星点层参数按域选择，二者语义相反、不可混用）
    if domain == "linear":
        starnet_cmd = (
            f'"{exe}" -i "{payload_path}" -o "{output_starless_path}" '
            f'-m "{stars_layer_path}" -s {stride}'
        )
    elif supports_unscreen:
        starnet_cmd = (
            f'"{exe}" -i "{payload_path}" -o "{output_starless_path}" '
            f'-n "{stars_layer_path}" -s {stride}'
        )
    else:
        # 不支持 -n 的版本：只输出无星层（星点层由管线自行相减）
        starnet_cmd = (
            f'"{exe}" -i "{payload_path}" -o "{output_starless_path}" -s {stride}'
        )

    # 管线恢复回流命令：必须带上域（以及实际的 midtones），否则逆变换会用错参数
    extra = f" {extra_pipeline_args.strip()}" if extra_pipeline_args else ""
    mid_flag = (
        f' --external-starless-midtones {float(midtones):g}'
        if midtones is not None else ""
    )
    resume_cmd = (
        f'python scripts/pipeline.py "{input_fits_path}" "{final_output_path}" '
        f'--external-starless "{output_starless_path}" '
        f'--external-starless-domain {domain} '
        f'--work-dir "{work_dir}"{mid_flag}{extra}'
    )

    return {
        "external_starnet_command": starnet_cmd,
        "resume_pipeline_command": resume_cmd,
        "domain": domain,
        "stride": stride,
        "midtones": None if midtones is None else float(midtones),
    }


def _as_rgb(image: np.ndarray) -> np.ndarray:
    """统一到 3 通道 float32，便于逐通道比较。"""
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        return np.stack([arr] * 3, axis=-1)
    if arr.ndim == 3 and arr.shape[2] >= 3:
        return arr[..., :3]
    if arr.ndim == 3 and arr.shape[2] == 1:
        return np.concatenate([arr] * 3, axis=-1)
    raise ValueError(f"无法解释为 RGB: shape={arr.shape}")


def _nebula_signal_mask(original_rgb: np.ndarray) -> np.ndarray:
    """用**原图**亮度分位定义星云信号区，排除背景与亮星。

    掩膜只由原图决定，两张图共用同一掩膜，保证比较的是同一批像素。
    """
    lum = (0.2126 * original_rgb[..., 0]
           + 0.7152 * original_rgb[..., 1]
           + 0.0722 * original_rgb[..., 2])
    lo = float(np.percentile(lum, 55.0))
    hi = float(np.percentile(lum, 99.2))
    return (lum > lo) & (lum < hi)


def _channel_profile(rgb: np.ndarray, mask: np.ndarray) -> Tuple[List[float], List[float]]:
    """逐通道返回 (相对强度, 归零比例)，两者都是**尺度无关**量。

    相对强度 = 该通道信号区 p95(减去本通道 p10 背景) / 三通道中最大值。
    归零比例 = 信号区内 sig <= 0 的像素占比（sig = clip(通道 - p10, 0, None)）。

    尺度无关是硬要求：外部无星图若为 FITS 且走 force_linear 读入则**不做归一化**
    （见 fits_io.py 的 _read_fits），与原图（已归一化到 [0,1]）量级不同，
    任何绝对阈值都会失效。
    """
    amps: List[float] = []
    sigs: List[np.ndarray] = []
    for c in range(3):
        bg = float(np.percentile(rgb[..., c], 10.0))
        sig = np.clip(rgb[..., c] - bg, 0.0, None)
        sigs.append(sig)
        amps.append(float(np.percentile(sig[mask], 95.0)) if mask.any() else 0.0)
    scale = max(max(amps), 1e-9)
    rel = [a / scale for a in amps]
    zeros = [float(np.mean(sigs[c][mask] <= 0.0)) for c in range(3)]
    return rel, zeros


def validate_starless_channel_integrity(
    original_image: np.ndarray,
    starless_image: np.ndarray,
    *,
    presence_floor: float = 0.05,
    level_drop_factor: float = 2.0,
    zero_surge: float = 0.25,
    min_signal_pixels: int = 100,
) -> Dict[str, Any]:
    """双图逐通道完整性校验：抓「原图该通道存在、无星层却大范围丢失」。

    两个**互补**信号（缺一不可）：
      - level_drop：通道被**均匀压低**（像素都还在，但强度只剩 <1/2）。
        例如 G 被整体乘 0.3 —— 此时归零比例可能没变，只有本信号能抓。
      - zero_surge：通道被**大面积剪零**（归零比例比原图上升 ≥25pp）。
        实测故障即此形态：星云区 G 有 93%+ 像素精确为 0。

    **不误伤天然缺通道的窄带图**：先用 `presence_floor` 判断「原图该通道本来
    是否存在」（相对最强通道 ≥5%）。纯 Hα 无 OIII 时 rel_orig 可能只有 0.01
    → 永不判塌缩。

    与 `quality_metrics.collapsed_channels` 的区别：后者是**单图绝对**判定
    （某通道 p99 < 最强通道 p99 的 1%），没有原图参照，且 1% 阈值过宽 ——
    通道剩 7% 时 0.07 > 0.01，检不出；也看不到「大面积归零」这一独立信号。
    """
    orig = np.asarray(original_image, dtype=np.float32)
    sl = np.asarray(starless_image, dtype=np.float32)
    if orig.shape[:2] != sl.shape[:2]:
        return {
            "valid": False, "skipped": "shape_mismatch", "collapsed_channels": [],
            "signal_pixel_count": 0, "channels": {},
            "error": f"外部无星图尺寸不匹配: 原图 {orig.shape[:2]} vs 无星图 {sl.shape[:2]}",
        }

    o = _as_rgb(orig)
    s = _as_rgb(sl)
    mask = _nebula_signal_mask(o)
    n = int(np.count_nonzero(mask))
    if n < min_signal_pixels:
        return {
            "valid": True, "skipped": "insufficient_signal", "collapsed_channels": [],
            "signal_pixel_count": n, "channels": {}, "error": None,
        }

    rel_o, zero_o = _channel_profile(o, mask)
    rel_s, zero_s = _channel_profile(s, mask)

    collapsed: List[str] = []
    channels: Dict[str, Any] = {}
    for i, label in enumerate(("r", "g", "b")):
        present = rel_o[i] >= presence_floor
        dropped = bool(present and rel_s[i] < rel_o[i] / level_drop_factor)
        zeroed = bool(present and (zero_s[i] - zero_o[i]) >= zero_surge)
        # 通道被完全清零时比值无意义，用 None 而不是天文数字
        drop_ratio = round(rel_o[i] / rel_s[i], 2) if rel_s[i] > 1e-6 else None
        reasons = []
        if dropped:
            reasons.append("level_drop")
        if zeroed:
            reasons.append("zero_surge")
        channels[label] = {
            "rel_orig": round(rel_o[i], 4),
            "rel_starless": round(rel_s[i], 4),
            "level_drop": drop_ratio,
            "zero_orig": round(zero_o[i], 4),
            "zero_starless": round(zero_s[i], 4),
            "present_in_original": bool(present),
            "collapsed": bool(reasons),
            "reason": "+".join(reasons) if reasons else None,
        }
        if reasons:
            collapsed.append(label)

    error = None
    if collapsed:
        parts = []
        for label in collapsed:
            ch = channels[label]
            drop_txt = "∞" if ch["level_drop"] is None else f"{ch['level_drop']:.2f}×"
            parts.append(
                f"{label.upper()} 通道（相对强度 {ch['rel_orig']:.3f}→{ch['rel_starless']:.3f}，"
                f"下降 {drop_txt}，归零像素 {ch['zero_orig']:.1%}→{ch['zero_starless']:.1%}，"
                f"判定={ch['reason']}）"
            )
        error = "外部无星图通道完整性校验失败：星云信号区大范围丢失 " + "；".join(parts)

    return {
        "valid": not collapsed,
        "skipped": None,
        "collapsed_channels": collapsed,
        "signal_pixel_count": n,
        "channels": channels,
        "error": error,
    }


def check_starless_scale_consistency(
    original_image: np.ndarray,
    starless_image: np.ndarray,
    *,
    warn: Tuple[float, float] = (0.5, 2.0),
    fail: Tuple[float, float] = (0.1, 10.0),
) -> Dict[str, Any]:
    """无星层与原件的中位亮度比应接近 1（星点对中位贡献可忽略）。

    比值越界说明两种可能之一：外部图未归一化（FITS + force_linear 读入会保留
    物理量级），或该图被整体压死/放大。**不做静默缩放** —— 那会掩盖真实问题，
    只报告。

    返回 {ratio, status: "ok"|"warn"|"fail", message}。
    """
    o = _as_rgb(original_image)
    s = _as_rgb(starless_image)
    lum_o = 0.2126 * o[..., 0] + 0.7152 * o[..., 1] + 0.0722 * o[..., 2]
    lum_s = 0.2126 * s[..., 0] + 0.7152 * s[..., 1] + 0.0722 * s[..., 2]
    mo = float(np.median(lum_o))
    ms = float(np.median(lum_s))
    ratio = ms / mo if mo > 1e-12 else float("nan")

    if not np.isfinite(ratio) or ratio < fail[0] or ratio > fail[1]:
        return {
            "ratio": None if not np.isfinite(ratio) else round(ratio, 5),
            "status": "fail",
            "message": (
                f"外部无星层中位亮度比 {ratio:.4f} 超出合理范围 "
                f"[{fail[0]}, {fail[1]}]；疑似未归一化的 FITS（force_linear 保留物理量级）"
                "或该层被整体压死"
            ),
        }
    if ratio < warn[0] or ratio > warn[1]:
        return {
            "ratio": round(ratio, 5),
            "status": "warn",
            "message": f"外部无星层中位亮度比 {ratio:.4f} 偏离 1（合理范围 [{warn[0]}, {warn[1]}]），建议核对",
        }
    return {"ratio": round(ratio, 5), "status": "ok", "message": None}


def verify_external_starless(original_image: np.ndarray, starless_image: np.ndarray,
                             *, check_channels: bool = True) -> Dict[str, Any]:
    """
    审查外部无星图真实性：几何尺寸、通道对齐与残星剥离健康度。
    """
    orig = np.asarray(original_image, dtype=np.float32)
    sl = np.asarray(starless_image, dtype=np.float32)

    if orig.shape[:2] != sl.shape[:2]:
        return {
            "valid": False,
            "error": f"外部无星图尺寸不匹配: 原图 {orig.shape[:2]} vs 无星图 {sl.shape[:2]}",
        }

    # 计算差异星点
    diff = np.clip(orig[..., :3] - sl[..., :3], 0.0, 1.0)
    stars_energy = float(np.mean(diff))
    starless_energy = float(np.mean(sl[..., :3]))

    result: Dict[str, Any] = {
        "valid": True,
        "stars_energy_mean": round(stars_energy, 6),
        "starless_energy_mean": round(starless_energy, 6),
        "stars_extracted_successfully": bool(stars_energy > 1e-5),
    }

    if not check_channels:
        return result

    # 逐通道完整性（抓「弱色通道被大面积清零」——亮度域指标看不见这类塌缩）
    integrity = validate_starless_channel_integrity(orig, sl)
    result["channel_integrity"] = integrity
    if not integrity.get("valid", True):
        result["valid"] = False
        result["error"] = integrity.get("error")

    # 尺度一致性（抓未归一化的 FITS 或整体压死）
    scale = check_starless_scale_consistency(orig, sl)
    result["scale_consistency"] = scale
    if scale["status"] == "fail":
        result["valid"] = False
        if not result.get("error"):
            result["error"] = scale["message"]

    return result


def format_bridge_recommendation_card(rec: Dict[str, Any], instructions: Optional[Dict[str, str]] = None) -> str:
    """格式化为易读的控制台建议卡片。"""
    lines = [
        "┌─────────────────────────────────────────────────────────────┐",
        "│       🌌 外部神经网络去星桥接建议 (Neural Star Bridge)      │",
        "├─────────────────────────────────────────────────────────────┤",
        f"│ 建议评级: {rec.get('level', 'UNKNOWN'):<49} │",
        f"│ 推荐状态: {'✅ 强烈建议使用' if rec.get('recommended') else 'ℹ️ 当前可选':<48} │",
    ]
    env = rec.get("local_environment", {})
    if env.get("available"):
        lines.append(f"│ 本地环境: ✅ 已就绪 ({env.get('backend', 'StarNet2')}){'':<26} │")
        lines.append(f"│ 执行路径: {env.get('executable', ''):<49} │")
    else:
        lines.append(f"│ 本地环境: ⚠️ 未检测到系统 StarNet2 二进制{'':<26} │")

    lines.append("├─────────────────────────────────────────────────────────────┤")
    lines.append(f"│ 判定依据: {rec.get('reason', ''):<49} │")
    lines.append(f"│ 行动建议: {rec.get('action_advice', ''):<49} │")

    if instructions:
        lines.append("├─────────────────────────────────────────────────────────────┤")
        lines.append("│ 🛠️ 一键执行外部去星命令:                                   │")
        lines.append(f"│   {instructions.get('external_starnet_command', ''):<57} │")
        lines.append("│ 🚀 管线恢复回流命令:                                         │")
        lines.append(f"│   {instructions.get('resume_pipeline_command', ''):<57} │")

    lines.append("└─────────────────────────────────────────────────────────────┘")
    return "\n".join(lines)
