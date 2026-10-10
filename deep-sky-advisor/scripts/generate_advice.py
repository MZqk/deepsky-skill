#!/usr/bin/env python3
"""Compile measured diagnostics into auditable deep-sky processing advice.

The compiler produces a *dual-track* report: one shared diagnostic layer, two
parallel primary tracks (Siril and PixInsight by default), and an optional
downstream finishing stage (Photoshop). Operations are software-independent
diagnostic decisions; each operation carries a per-software `implementations`
map and a `phase` used to decide which stage may own it.

Content ownership (do not duplicate):

- per application (tools / steps / parameter logic / mask strategy)
  -> `software_guidance.py`
- per operation, application-independent (purpose / start / adjust / acceptance /
  rollback / cautions) -> this module, inline at each `_operation()` call site
- labels and report layout -> `report_zh.py`
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from software_guidance import get_primary_tool, get_software_guidance
from report_zh import (
    DEVICE_LABEL,
    FILE_LABEL,
    FILTER_LABEL,
    FINISHING_LABEL,
    FINISHING_NONE,
    FINISHING_NOTE,
    FINISHING_OWNED_TITLE,
    FINISHING_TITLE,
    HANDOFF_NO_PREREQ,
    HANDOFF_NO_TRACK,
    HANDOFF_PARAM_TITLE,
    HANDOFF_PREREQ_TITLE,
    HANDOFF_TITLE,
    HANDOFF_UPSTREAM_FALLBACK,
    HANDOFF_UPSTREAM_HEADER,
    HANDOFF_UPSTREAM_TITLE,
    MAPPING_INTRO,
    MAPPING_STEP_COLUMN,
    MAPPING_TITLE,
    MASTER_FORMATS,
    METRIC_DISCLAIMER,
    MULTI_TRACK_NOTE,
    NOT_PROVIDED_VALUE,
    NOT_RECOMMENDED_HEADER,
    NOT_RECOMMENDED_TITLE,
    NO_SEQUENCE,
    OPERATION_FIELDS,
    PHASE_LABELS,
    PRIORITY_TITLE,
    RECOMMENDED_PREFIX,
    REPORT_TITLE,
    REQUIRED_INFO_TITLE,
    REVIEW_PREFIX,
    SEQUENCE_TITLE,
    SKIPPED_PREFIX,
    TARGET_NAME_LABEL,
    TARGET_TYPE_LABEL,
    TRACK_EMPTY,
    TRACK_LABELS,
    TRACK_LABEL,
    TRACK_ORDINALS,
    TRACK_SECTION_TITLE,
    UNKNOWN_VALUE,
    localized_evidence,
    localized_value,
)


SCHEMA_VERSION = "2.0"
VALID_SOFTWARE = {"generic", "siril", "pixinsight", "photoshop"}
STAR_SUBJECTS = {"globular_cluster", "open_cluster", "m45"}
BACKGROUND_SENSITIVE = {
    "emission_nebula", "dark_nebula", "reflection_nebula",
    "supernova_remnant", "wide_field",
}
NARROWBAND_TOKENS = ("ha", "h-alpha", "halpha", "oiii", "o3", "sii", "s2", "dual", "duo")
UPSTREAM_CAPABLE = {"siril", "pixinsight"}

OPERATION_LABELS = {
    "calibrate_integrate": "校准、选帧与叠加",
    "crop_edges": "裁切无效边缘",
    "background_review": "背景与梯度处理",
    "color_calibration": "宽带色彩校准",
    "narrowband_mapping": "窄带通道映射",
    "linear_denoise": "线性阶段降噪",
    "star_shape_review": "星点形态诊断",
    "controlled_stretch": "受控非线性拉伸",
    "highlight_protection": "亮核与高光保护",
    "star_treatment": "星点处理",
    "color_refinement": "色彩精修",
    "final_export": "母版保存与最终导出",
}

DECISION_LABELS = {
    "recommend": "建议执行",
    "review": "确认后执行",
    "skip": "当前跳过",
}

# Which stage of the pipeline each operation belongs to.
PHASE = {
    "calibrate_integrate": "linear",
    "crop_edges": "linear",
    "background_review": "linear",
    "color_calibration": "linear",
    "narrowband_mapping": "linear",
    "linear_denoise": "linear",
    "star_shape_review": "linear",
    "controlled_stretch": "nonlinear",
    "highlight_protection": "nonlinear",
    "star_treatment": "nonlinear",
    "color_refinement": "finishing",
    "final_export": "export",
}

ALL_OPERATIONS = tuple(PHASE)

# Which phases exist. `finishing` is reserved for downstream-only operations;
# no operation uses it yet, but the capability table already reserves it for Photoshop.
VALID_PHASES = {"linear", "nonlinear", "finishing", "export"}

# Which phases each software is allowed to own.
# Photoshop never owns `linear`: it cannot calibrate, register, stack, model the
# background, or perform photometric color calibration. It only finishes and exports.
TRACK_CAPABILITY = {
    "generic": {"linear", "nonlinear", "finishing", "export"},
    "siril": {"linear", "nonlinear", "finishing", "export"},
    "pixinsight": {"linear", "nonlinear", "finishing", "export"},
    "photoshop": {"nonlinear", "finishing", "export"},
}


def _get(payload, path, default=None):
    current = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _evidence(payload, path, interpretation):
    return {
        "path": path,
        "value": _get(payload, path),
        "interpretation": interpretation,
    }


def _context_evidence(path, value, interpretation):
    return {
        "path": f"user_context.{path}",
        "value": value,
        "interpretation": interpretation,
    }


def _normalize_tracks(software):
    """Accept a single software name, or a comma-separated / list form."""
    if isinstance(software, str):
        values = [item.strip() for item in software.split(",") if item.strip()]
    else:
        values = [str(item).strip() for item in software if str(item).strip()]
    if not values:
        raise ValueError("At least one software track is required")
    tracks = []
    for value in values:
        if value not in VALID_SOFTWARE:
            raise ValueError(f"Unsupported software: {value}")
        if value not in tracks:
            tracks.append(value)
    if "generic" in tracks and len(tracks) > 1:
        raise ValueError("'generic' is software-independent and cannot be combined with a concrete application")
    return tracks


def _implementations(operation_id, tracks, finishing, acceptance, rollback):
    """Build the per-software implementation map for one operation.

    The finishing stage is included alongside the primary tracks: it shares the
    same diagnostic decision but only owns the phases it is capable of.
    """
    phase = PHASE[operation_id]
    software_list = list(tracks)
    if finishing and finishing not in software_list:
        software_list.append(finishing)
    implementations = {}
    for software in software_list:
        if phase not in TRACK_CAPABILITY[software]:
            continue
        guidance = get_software_guidance(software, operation_id)
        guidance["primary_tool"] = get_primary_tool(software, operation_id)
        guidance["checkpoints"] = list(acceptance)
        guidance["failure_signs"] = list(rollback)
        implementations[software] = guidance
    return implementations


def _operation(
    operation_id,
    decision,
    confidence,
    evidence,
    purpose,
    starting_point,
    adjust,
    acceptance,
    rollback,
    tracks,
    parameter_mode="qualitative",
    parameter_rules=None,
    cautions=None,
):
    return {
        "id": operation_id,
        "decision": decision,
        "confidence": confidence,
        "evidence": evidence,
        "purpose": purpose,
        "phase": PHASE[operation_id],
        "implementations": {},
        "parameter_mode": parameter_mode,
        "parameter_rules": parameter_rules or [],
        "starting_point": starting_point,
        "how_to_adjust": adjust,
        "acceptance_checks": acceptance,
        "rollback_conditions": rollback,
        "cautions": cautions or [],
    }


def _is_narrowband(analysis, filter_override=None):
    filter_name = str(filter_override or _get(analysis, "classification.filter") or "").lower()
    if any(token in filter_name for token in NARROWBAND_TOKENS):
        return True
    device_filters = str(_get(analysis, "classification.device.priors.builtin_filters") or "").lower()
    return any(token in device_filters for token in ("dual-band", "dual narrowband", "duo-band"))


def _postprocessing_ready(analysis):
    stage = _get(analysis, "classification.processing_stage", "unknown")
    return stage == "stacked_or_integrated"


HIGHLIGHT_REVIEW_THRESHOLD = 0.002
AGGREGATE_HIGHLIGHT_PATH = "clipping.highlight_ratio_ge_0_999"


def _highlight_pressure(analysis):
    """Effective bright-end occupancy, taking per-channel saturation into account.

    `clipping.highlight_ratio_ge_0_999` is measured on luminance, and luminance is a
    weighted sum of R/G/B. A single saturated channel is therefore invisible to it:
    when red is clipped but green and blue are not, luminance never reaches the
    threshold. That is the normal case for Ha and HOO data, where the red channel
    carries most of the signal, so gating on the aggregate alone silently skips
    highlight review exactly when it is most needed.

    The effective pressure is the maximum of the aggregate and every per-channel
    ratio. Returns (ratio, evidence_path, channel_label, per_channel_driven).
    """
    aggregate = float(_get(analysis, AGGREGATE_HIGHLIGHT_PATH, 0) or 0)
    best_ratio = aggregate
    best_path = AGGREGATE_HIGHLIGHT_PATH
    best_label = ""
    per_channel = _get(analysis, "clipping.per_channel")
    if isinstance(per_channel, dict):
        for label, values in per_channel.items():
            if not isinstance(values, dict):
                continue
            ratio = values.get("highlight_ratio_ge_0_999")
            if ratio is None:
                continue
            ratio = float(ratio)
            if ratio > best_ratio:
                best_ratio = ratio
                best_path = f"clipping.per_channel.{label}.highlight_ratio_ge_0_999"
                best_label = str(label).upper()
    return best_ratio, best_path, best_label, best_path != AGGREGATE_HIGHLIGHT_PATH


COLOR_REFINEMENT_THRESHOLD = 0.10


def _color_state(analysis):
    """Measured colour-state signals that justify a finishing colour pass.

    Returns (imbalance_spread, collapsed_channels). `imbalance_spread` is the largest
    absolute deviation of any background channel ratio from unity — a residual colour
    cast that survived linear-stage calibration. Collapsed channels are reported
    separately because colour refinement cannot recover them.
    """
    ratios = _get(analysis, "color.background_ratios_to_mean")
    deviations = []
    if isinstance(ratios, dict):
        for value in ratios.values():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                deviations.append(abs(float(value) - 1.0))
    collapsed = _get(analysis, "color.collapsed_channels") or []
    if not isinstance(collapsed, list):
        collapsed = [collapsed]
    return (max(deviations) if deviations else 0.0), collapsed


def compile_advice(
    analysis,
    software="generic",
    target_type="unknown",
    target_name=None,
    filter_name=None,
    finishing="auto",
):
    tracks = _normalize_tracks(software)
    if finishing == "auto":
        finishing = "photoshop" if any(track in UPSTREAM_CAPABLE for track in tracks) else None
    if finishing and finishing not in VALID_SOFTWARE:
        raise ValueError(f"Unsupported finishing software: {finishing}")
    if finishing and finishing in tracks:
        finishing = None

    operations = []
    stage = _get(analysis, "classification.processing_stage", "unknown")
    role = _get(analysis, "classification.frame_role", "unknown")
    transfer = _get(analysis, "classification.transfer_state", "unknown")
    is_narrowband = _is_narrowband(analysis, filter_name)
    target_key = (target_name or "").strip().lower().replace(" ", "")
    target_is_star_subject = target_type in STAR_SUBJECTS or target_key == "m45"

    if role in {"dark", "flat", "bias"}:
        operations.append(_operation(
            "calibrate_integrate", "recommend", "high",
            [_evidence(analysis, "classification.frame_role", "文件被分类为校准帧")],
            "用该帧参与校准，而不是把它当作后期处理目标。",
            "核对曝光、温度、增益、合并方式和光路是否与亮场一致。",
            "只在序列一致时构建 master，并检查 master 是否受污染。",
            ["应用到代表性亮场后，master 表现符合预期。", "没有引入类似目标的结构。"],
            ["校准后梯度、辉光、灰尘阴影或固定图样噪声加重。"],
            tracks,
        ))
        return _payload(analysis, operations, tracks, finishing, target_type, target_name, filter_name)

    if role == "light" and stage != "stacked_or_integrated":
        operations.append(_operation(
            "calibrate_integrate", "recommend", "high",
            [
                _evidence(analysis, "classification.frame_role", "文件被分类为亮场单帧"),
                _evidence(analysis, "classification.processing_stage", "没有发现已叠加的证据"),
            ],
            "避免根据单张未叠加的曝光制定成片后期决策。",
            "在注册和叠加之前先完成校准并评估序列。",
            "只剔除有明确焦点、跟踪、云层或背景缺陷的帧。",
            ["叠加母版背景噪声下降且星形未恶化。", "拒绝图中是伪影而不是真实信号。"],
            ["校准或拒绝删除了真实星点/目标结构，或加重固定图样噪声。"],
            tracks,
        ))
        return _payload(analysis, operations, tracks, finishing, target_type, target_name, filter_name)

    exact_min = float(_get(analysis, "statistics.exact_min_ratio", 0) or 0)
    near_min = float(_get(analysis, "statistics.near_min_ratio", 0) or 0)
    if max(exact_min, near_min) >= 0.01:
        operations.append(_operation(
            "crop_edges", "review", "medium",
            [
                _evidence(analysis, "statistics.exact_min_ratio", "有可测比例的像素等于图像最小值"),
                _evidence(analysis, "statistics.near_min_ratio", "有可测比例的像素接近图像最小值"),
            ],
            "在统计分析和背景建模之前移除无效注册边缘。",
            "高对比检查四边，只裁掉零值楔形、空白拼接边缘和明确无效像素。",
            "采用能够清除无效数据的最小裁切，不因暗弱天空较暗而扩大裁切。",
            ["无效楔形或空白边缘已清除。", "目标构图和暗弱外围仍完整。"],
            ["裁切损失目标结构、拼接有效区域或真实暗空。"],
            tracks,
        ))

    gradient = float(_get(analysis, "background.plane.magnitude_across_frame", 0) or 0)
    gradient_r2 = float(_get(analysis, "background.plane.r_squared", 0) or 0)
    corner_range = float(_get(analysis, "background.corner_median_range", 0) or 0)
    gradient_detected = gradient >= 0.08 and gradient_r2 >= 0.55
    gradient_confidence = "medium" if gradient_detected else "low"
    background_cautions = [
        "数值趋势本身不足以证明背景可以移除。",
        "接受校正前必须检查背景模型和差分图。",
    ]
    if target_type in BACKGROUND_SENSITIVE or is_narrowband:
        background_cautions.append("该目标/滤镜可能包含与梯度相似的真实大尺度信号。")
    operations.append(_operation(
        "background_review",
        "review" if gradient_detected else "skip",
        gradient_confidence,
        [
            _evidence(analysis, "background.plane.magnitude_across_frame", "低信号背景平面跨画面变化幅度"),
            _evidence(analysis, "background.plane.r_squared", "背景平面解释的低信号方差比例"),
            _evidence(analysis, "background.corner_median_range", "独立的四角背景差异"),
        ],
        "确认画面中的低频变化是否属于可校正背景，同时避免减掉真实天体结构。",
        (
            "在确认的空背景位置建立低复杂度试验模型，但不要立即应用。"
            if gradient_detected else
            "默认不执行背景提取；先在背景预览中确认是否存在与目标无关的趋势。"
        ),
        "只有残差仍呈连贯的仪器或天空梯度，且模型始终不含目标结构时，才提高模型复杂度。",
        [
            "背景模型只包含平滑的非目标低频成分。",
            "四角差异改善且没有黑坑、断层或目标边缘损失。",
            "已知星云、尘埃、IFN、星系外晕和暗弱细丝保持原有形态。",
        ],
        [
            "背景模型出现弧线、尘埃带、星系外晕、IFN 或星云细丝。",
            "校正后出现黑坑、色彩断层或角落过减。",
        ],
        tracks,
        cautions=background_cautions,
    ))

    channel_model = str(_get(analysis, "classification.channel_model", "unknown"))
    if is_narrowband:
        operations.append(_operation(
            "narrowband_mapping", "review", "medium",
            [
                (
                    _context_evidence("filter", filter_name, "用户提供了滤镜/通道信息")
                    if filter_name else
                    _evidence(analysis, "classification.filter", "滤镜元数据提示窄带或双窄带采集")
                ),
                _evidence(analysis, "color.channel_p99_normalized", "存在 RGB 通道时的各通道信号分布"),
            ],
            "根据真实采集通道建立可追溯的窄带配色，不把伪色映射表述成自然 RGB。",
            "先确认每个源通道对应的发射线、滤镜和信噪质量，再决定颜色映射。",
            "弱通道不应仅为凑配色而被强行拉到与强通道等权。",
            ["通道映射已有明确记录。", "弱通道噪声没有被提升为伪结构，恒星层处理方式明确。"],
            ["结果暗示不存在的通道、出现荧光色块，或把噪声变成疑似发射结构。"],
            tracks,
            cautions=["不要把宽带白平衡假设套用到星云发射线。"],
        ))
    elif channel_model == "rgb":
        has_wcs = bool(_get(analysis, "file.header.WCSAXES"))
        operations.append(_operation(
            "color_calibration", "recommend" if has_wcs else "review", "medium",
            [
                _evidence(analysis, "classification.channel_model", "数据包含三个图像通道"),
                _evidence(analysis, "file.header.WCSAXES", "WCS 证据决定能否直接进行星表校准"),
                _evidence(analysis, "color.background_ratios_to_mean", "实测背景通道失衡"),
            ],
            "使用恒星和器材响应约束宽带颜色，而不是仅凭视觉把背景强行调成中性。",
            "确认或完成 WCS 求解，再使用实际或有依据的相机/滤镜响应进行星表校色。",
            "空间色度梯度应与全局色彩校准分开处理，不用白平衡掩盖残余梯度。",
            ["未饱和恒星颜色合理。", "背景色度梯度减弱，同时真实发射颜色未被中和。"],
            ["求解失败、通道被裁切、彩色恒星被洗白或目标预期颜色被破坏。"],
            tracks,
            cautions=["背景通道失衡本身不足以证明存在色偏。"],
        ))

    noise_sigma = float(_get(analysis, "noise.background_noise_sigma_normalized", 0) or 0)
    noise_blocks = int(_get(analysis, "noise.block_count", 0) or 0)
    noise_decision = "review" if noise_blocks >= 4 and noise_sigma >= 0.01 else "skip"
    operations.append(_operation(
        "linear_denoise", noise_decision, "medium" if noise_decision == "review" else "low",
        [
            _evidence(analysis, "noise.background_noise_sigma_normalized", "归一化高通 MAD 噪声估计"),
            _evidence(analysis, "noise.block_count", "支持该估计的低信号分块数量"),
            _evidence(analysis, "classification.transfer_state", "线性阶段降噪取决于传输状态"),
        ],
        "在线性阶段降低有统计依据的背景噪声，同时保护暗弱真实信号。",
        (
            "在副本上使用保护蒙版做保守降噪，并以 100% 视图和强预览拉伸进行比较。"
            if noise_decision == "review" else
            "除非视觉复核显示噪声明显，或有可对比版本证明有效，否则跳过降噪。"
        ),
        "只有背景方差下降且小星、尘埃边缘和连贯细丝仍保留时才增加强度。",
        ["背景颗粒下降但没有塑料感。", "小星、细丝和尘埃边缘仍保留，未出现分块或色斑。"],
        ["小星消失、细丝断裂、尘埃变成塑料感，或出现相关分块。"],
        tracks,
        cautions=["该指标不是物理信噪比，不能证明暗弱结构就是噪声。"],
    ))

    star_evidence = _get(analysis, "stars.evidence")
    if star_evidence == "measured":
        eccentricity = float(_get(analysis, "stars.eccentricity_p90", 0) or 0)
        operations.append(_operation(
            "star_shape_review", "review" if eccentricity >= 0.45 else "skip", "medium",
            [
                _evidence(analysis, "stars.usable_star_count", "通过校验的星点样本数量"),
                _evidence(analysis, "stars.fwhm_major_median_px", "基于矩的长轴 FWHM 中位数"),
                _evidence(analysis, "stars.eccentricity_p90", "星点偏心率上尾值"),
                _evidence(analysis, "stars.position_angle_median_deg", "被测样本的方向角中位数"),
            ],
            "在进行美容修正前，判断星点异常来自跟踪、光学边场、倾斜、色差还是注册。",
            "分别检查中心、四角、边缘和代表性单帧的星点形态与方向。",
            "区分全场同向拉长、径向/切向边场变化、单侧异常和通道相关异常。",
            ["星点异常原因得到空间分布或单帧证据支持。", "后续处理没有破坏恒星轮廓和颜色。"],
            ["美容修正产生非物理圆星、黑圈、核心裁切或双星丢失。"],
            tracks,
            parameter_mode="evidence_bound",
            parameter_rules=[
                {
                    "rule": "测得的 FWHM 只作为蒙版与检查孔径的相对尺度。",
                    "evidence_path": "stars.fwhm_major_median_px",
                }
            ],
            cautions=["基于矩的 FWHM 不是完整 PSF 拟合，也不是视宁度测量。"],
        ))

    if _postprocessing_ready(analysis):
        operations.append(_operation(
            "controlled_stretch", "recommend", "medium",
            [
                _evidence(analysis, "classification.processing_stage", "文件被视为已具备后期处理条件"),
                _evidence(analysis, "classification.transfer_state", "传输状态启发式决定是否适合拉伸"),
                _evidence(analysis, "clipping.shadow_ratio_le_0_001", "归一化暗端占比"),
                _evidence(analysis, "clipping.highlight_ratio_ge_0_999", "归一化亮端占比"),
            ],
            "在保留黑位、星色和亮核层次的前提下显现暗弱目标。",
            "先用非破坏预览确定目标效果，再分多次进行小幅永久拉伸。",
            "当新增拉伸带来的噪声增长超过真实结构增长，或亮部开始变平时停止。",
            ["背景与纯黑分离且没有硬截断。", "亮核保留内部层次，星色仍可见。"],
            ["黑位裁切增加、亮核变成死白、星点明显膨胀，或暗弱区域主要剩噪声。"],
            tracks,
            cautions=["稳健归一化裁切比例只是复核指标，不等于传感器物理饱和。"],
        ))

    highlight_ratio, highlight_path, highlight_label, per_channel_driven = _highlight_pressure(analysis)
    if highlight_ratio >= HIGHLIGHT_REVIEW_THRESHOLD:
        highlight_evidence = [
            _evidence(analysis, AGGREGATE_HIGHLIGHT_PATH, "稳健映射下的整图亮端占比（基于亮度加权）"),
        ]
        if per_channel_driven:
            highlight_evidence.append(_evidence(
                analysis, highlight_path,
                f"{highlight_label} 通道单独压到上限，整图亮度指标看不到该通道的饱和",
            ))
        highlight_evidence.append(
            _evidence(analysis, "statistics.exact_max_ratio", "恰好等于原始最大值的像素比例")
        )
        highlight_cautions = ["没有原始 ADU/位深证据时，不要把该现象判定为传感器饱和。"]
        if per_channel_driven:
            highlight_cautions.append(
                f"{highlight_label} 通道已被单独压到上限；后续提升饱和度或色彩时不要进一步推高该通道。"
            )
        operations.append(_operation(
            "highlight_protection", "review", "medium" if per_channel_driven else "low",
            highlight_evidence,
            "确认并保护亮星或目标亮核，避免拉伸后内部结构和星色丢失。",
            (
                f"整图亮度尚未压到上限，但 {highlight_label} 通道已经饱和；先按通道单独检查亮端，再决定保护方式。"
                if per_channel_driven else
                "结合高光预览和原始数值范围确认风险，再建立柔和的范围或亮核蒙版。"
            ),
            "只在已确认的亮部区域增加保护，使用能够恢复层次的最低强度；分通道饱和时优先保护该通道。",
            ["亮核结构可见，蒙版过渡不可见，未饱和星色保留。"],
            ["保护区变灰、出现硬 HDR 边界，或与周围结构脱节。"],
            tracks,
            cautions=highlight_cautions,
        ))

    if target_is_star_subject:
        operations.append(_operation(
            "star_treatment", "skip", "high",
            [
                _context_evidence("target_type", target_type, "用户指定了以星点为主体的目标类型"),
                _context_evidence("target_name", target_name, "用户提供了目标名称"),
            ],
            "星点是主体，必须保留恒星族群。",
            "不要移除或整体缩小星点。",
            "只在必要时做克制的颜色与核心保护。",
            ["星团结构、星点层级、双星和颜色保持完整。"],
            ["星点消失、变得同样细小、失去颜色，或出现暗环。"],
            tracks,
            cautions=["M45、球状星团和疏散星团必须显式保护星点。"],
        ))
    elif star_evidence == "measured":
        density = float(_get(analysis, "stars.density_per_megapixel", 0) or 0)
        operations.append(_operation(
            "star_treatment", "review" if density >= 80 else "skip", "low",
            [
                _evidence(analysis, "stars.density_per_megapixel", "通过校验的亮星样本密度"),
                _evidence(analysis, "stars.fwhm_major_median_px", "星点相对尺度"),
            ],
            "判断拉伸后星点是否在视觉上压制目标。",
            "先判断拉伸后的图像；如有需要再测试低强度星点蒙版调整。",
            "蒙版尺度参考测得 FWHM；小星消失时先降低强度，不要先扩大半径。",
            ["目标可读性提高，同时星点层级和颜色保持自然。", "没有黑圈或核心裁切。"],
            ["星点变得同样人工化、小星消失，或星云亮结被误判为星点。"],
            tracks,
            parameter_mode="evidence_bound",
            parameter_rules=[
                {
                    "rule": "蒙版尺度必须由测得 FWHM 推导，不要使用固定像素半径。",
                    "evidence_path": "stars.fwhm_major_median_px",
                }
            ],
        ))

    color_spread, collapsed_channels = _color_state(analysis)
    if is_narrowband:
        color_decision, color_confidence = "skip", "high"
        color_evidence = [
            _evidence(analysis, "classification.filter", "滤镜元数据提示窄带或双窄带采集"),
            _evidence(analysis, "color.channel_p99_normalized", "各通道信号分布"),
        ]
        color_start = "窄带数据不做宽带式色彩精修；配色由窄带通道映射决定，并已单独记录。"
        color_cautions = ["窄带配色属于映射选择，不要用宽带饱和度或白平衡假设去「修正」它。"]
    elif collapsed_channels:
        color_decision, color_confidence = "skip", "high"
        color_evidence = [
            _evidence(analysis, "color.collapsed_channels", "检测到信号塌陷的通道"),
            _evidence(analysis, "color.background_ratios_to_mean", "背景通道相对比例"),
        ]
        color_start = "存在信号塌陷的通道；色彩精修无法恢复该通道的真实信息，应先回查采集或校准。"
        color_cautions = ["不要用饱和度或色彩平衡去补造塌陷通道缺失的信号。"]
    elif color_spread >= COLOR_REFINEMENT_THRESHOLD:
        color_decision, color_confidence = "review", "medium"
        color_evidence = [
            _evidence(analysis, "color.background_ratios_to_mean", "背景通道相对比例；偏差最大者为待处理的残余色偏"),
            _evidence(analysis, "color.channel_p99_normalized", "各通道信号分布"),
        ]
        color_start = "在主体拉伸与星点处理完成后，对已测得的残余色偏做低强度、可逆的色彩精修。"
        color_cautions = ["背景通道失衡也可能来自真实发射线，不要把它一律当作色偏去除。"]
        if per_channel_driven and highlight_ratio >= HIGHLIGHT_REVIEW_THRESHOLD:
            color_cautions.append(
                f"{highlight_label} 通道已接近或压到上限；提升饱和度会进一步推高该通道，必须先确认余量。"
            )
    else:
        color_decision, color_confidence = "skip", "low"
        color_evidence = [
            _evidence(analysis, "color.background_ratios_to_mean", "背景通道相对比例"),
            _evidence(analysis, "color.channel_p99_normalized", "各通道信号分布"),
        ]
        color_start = "未测得需要处理的残余色偏；此时提升饱和度属于纯审美选择，不作为建议。"
        color_cautions = ["没有可测的色彩问题时不建议提升饱和度，以免把噪声染成彩色。"]
    operations.append(_operation(
        "color_refinement", color_decision, color_confidence,
        color_evidence,
        "在不引入伪色的前提下处理已测得的残余色偏，并保持恒星颜色合理。",
        color_start,
        "只处理已确认的残余偏差；先降低强度，再考虑分通道调整，并始终监控通道余量与背景噪声。",
        ["未饱和恒星颜色合理。", "背景没有出现彩色噪声斑块。", "没有通道被进一步推向上限。"],
        ["背景出现彩色斑块、恒星被染色、通道裁切增加，或真实发射颜色被中和。"],
        tracks,
        cautions=color_cautions,
    ))

    operations.append(_operation(
        "final_export", "recommend", "high",
        [_evidence(analysis, "file.format", "输入格式决定母版与导出的处理方式")],
        "保留高精度母版，并生成颜色可预测、没有新增裁切和光晕的展示版本。",
        "先保存全分辨率高位深母版，再复制用于色彩空间转换、缩放和输出锐化。",
        "输出锐化必须基于最终像素尺寸和观看介质，并保护平滑背景。",
        ["高位深母版完整保留。", "展示版本嵌入配置文件，且没有新增色带、裁切或锐化光晕。"],
        ["导出后颜色异常、出现色带，或暗部/高光被裁切。"],
        tracks,
    ))
    return _payload(analysis, operations, tracks, finishing, target_type, target_name, filter_name)


def _handoff_contract(operations, tracks, finishing):
    """Derive the upstream-required checklist and the technical handoff parameters."""
    finishing_software = finishing or ("photoshop" if "photoshop" in tracks else None)
    if not finishing_software:
        return None
    by_id = {operation["id"]: operation for operation in operations}
    upstream_required = [
        operation["id"] for operation in operations
        if operation["phase"] == "linear" and operation["decision"] in {"recommend", "review"}
    ]
    calibration = by_id.get("color_calibration", {}).get("decision", "skip")
    narrowband = by_id.get("narrowband_mapping", {}).get("decision", "skip")
    star_separation = by_id.get("star_treatment", {}).get("decision", "skip")
    return {
        "finishing_software": finishing_software,
        "upstream_tracks": [track for track in tracks if track in UPSTREAM_CAPABLE],
        "upstream_required": upstream_required,
        "color_calibration_done": calibration in {"recommend", "review"},
        "narrowband_mapping_used": narrowband in {"recommend", "review"},
        "star_separation_planned": star_separation in {"recommend", "review"},
    }


def _payload(analysis, operations, tracks, finishing, target_type, target_name, filter_name):
    for operation in operations:
        operation["implementations"] = _implementations(
            operation["id"],
            tracks,
            finishing,
            operation["acceptance_checks"],
            operation["rollback_conditions"],
        )
    required_info = []
    if _get(analysis, "classification.processing_stage") == "unknown":
        required_info.append("请确认文件是已校准单帧还是已经叠加的母版。")
    if _get(analysis, "classification.transfer_state") in ("unknown", None):
        required_info.append("请确认图像仍为线性数据还是已经完成非线性拉伸。")
    if target_type == "unknown":
        required_info.append("请提供目标类型，以启用对应的真实性和安全规则。")
    if not (filter_name or _get(analysis, "classification.filter")):
        required_info.append("请提供滤镜或通道采集信息。")
    return {
        "schema_version": SCHEMA_VERSION,
        "source_analysis_schema": analysis.get("schema_version"),
        "source_analysis_json": analysis.get("analysis_json"),
        "context": {
            "software": tracks[0] if len(tracks) == 1 else None,
            "tracks": list(tracks),
            "finishing": finishing,
            "target_type": target_type,
            "target_name": target_name,
            "filter": filter_name or _get(analysis, "classification.filter"),
            "device": _get(analysis, "classification.device.label"),
        },
        "operations": operations,
        "handoff_contract": _handoff_contract(operations, tracks, finishing),
        "required_information": required_info,
        "policy": {
            "exact_parameters_require_evidence": True,
            "background_correction_requires_visual_model_review": True,
            "all_recommended_or_review_operations_require_acceptance_and_rollback": True,
            "primary_tracks_are_alternatives": True,
            "photoshop_never_owns_linear_phase": True,
        },
    }


def validate_advice(advice):
    errors = []
    context = advice.get("context") or {}
    tracks = context.get("tracks") or []
    finishing = context.get("finishing")
    if not tracks:
        errors.append("context: no software tracks declared")
    software_targets = list(tracks)
    if finishing and finishing not in software_targets:
        software_targets.append(finishing)
    for index, operation in enumerate(advice.get("operations", [])):
        prefix = f"operations[{index}]({operation.get('id')})"
        operation_id = operation.get("id")
        phase = operation.get("phase")
        if phase not in VALID_PHASES:
            errors.append(f"{prefix}: missing or invalid phase")
        elif PHASE.get(operation_id) != phase:
            errors.append(f"{prefix}: phase does not match the phase table")
        active = operation.get("decision") in {"recommend", "review"}
        if active:
            evidence = operation.get("evidence") or []
            if not evidence or any(not item.get("path") for item in evidence):
                errors.append(f"{prefix}: missing evidence paths")
            elif not any(item.get("value") is not None for item in evidence):
                errors.append(f"{prefix}: all evidence values are unavailable")
            if not operation.get("acceptance_checks"):
                errors.append(f"{prefix}: missing acceptance checks")
            if not operation.get("rollback_conditions"):
                errors.append(f"{prefix}: missing rollback conditions")
            for field in ("purpose", "starting_point", "how_to_adjust"):
                if not operation.get(field):
                    errors.append(f"{prefix}: missing {field}")
        if operation.get("parameter_mode") == "evidence_bound":
            for rule in operation.get("parameter_rules", []):
                if not rule.get("evidence_path"):
                    errors.append(f"{prefix}: evidence-bound parameter rule lacks evidence_path")
        if operation.get("parameter_mode") == "exact":
            errors.append(f"{prefix}: exact parameter mode is not allowed")
        implementations = operation.get("implementations") or {}
        for track in software_targets:
            if phase not in TRACK_CAPABILITY.get(track, set()):
                if track in implementations:
                    errors.append(f"{prefix}: track {track} must not own phase {phase}")
                continue
            implementation = implementations.get(track)
            if not implementation:
                errors.append(f"{prefix}: missing implementation for track {track}")
                continue
            if not implementation.get("primary_tool"):
                errors.append(f"{prefix}: missing implementations.{track}.primary_tool")
            for field in ("tools", "steps", "parameter_logic", "mask_strategy"):
                if not implementation.get(field):
                    errors.append(f"{prefix}: missing implementations.{track}.{field}")
    return errors


def _report_filename(advice):
    source = advice.get("source_analysis_json")
    if not source:
        return NOT_PROVIDED_VALUE
    stem = Path(source).stem
    return stem.replace("_analysis", "") or stem


def _track_heading(index, track, multi):
    label = TRACK_LABELS.get(track, track)
    if multi:
        return f"## 轨 {TRACK_ORDINALS[index]} — {label} {TRACK_SECTION_TITLE}"
    return f"## {label} {TRACK_SECTION_TITLE}"


def _render_header(advice):
    context = advice["context"]
    tracks = context.get("tracks") or []
    finishing = context.get("finishing")
    labels = " ｜ ".join(TRACK_LABELS.get(track, track) for track in tracks)
    if len(tracks) > 1:
        labels = f"{labels}（{MULTI_TRACK_NOTE}）"
    lines = [
        f"# {REPORT_TITLE}",
        "",
        f"- {FILE_LABEL}：{_report_filename(advice)}",
        f"- {TRACK_LABEL}：{labels}",
    ]
    if finishing:
        lines.append(f"- {FINISHING_LABEL}：{TRACK_LABELS.get(finishing, finishing)}（{FINISHING_NOTE}）")
    lines.extend([
        f"- {TARGET_TYPE_LABEL}：{localized_value(context['target_type'])}",
        f"- {TARGET_NAME_LABEL}：{context.get('target_name') or UNKNOWN_VALUE}",
        f"- {FILTER_LABEL}：{context.get('filter') or UNKNOWN_VALUE}",
        f"- {DEVICE_LABEL}：{context.get('device') or UNKNOWN_VALUE}",
        "",
    ])
    return lines


def _render_priority(advice):
    lines = [f"## {PRIORITY_TITLE}", ""]
    recommended = [op["id"] for op in advice["operations"] if op["decision"] == "recommend"]
    review = [op["id"] for op in advice["operations"] if op["decision"] == "review"]
    skipped = [op["id"] for op in advice["operations"] if op["decision"] == "skip"]
    lines.extend([
        f"- {RECOMMENDED_PREFIX}：{' → '.join(OPERATION_LABELS[item] for item in recommended) if recommended else '无'}",
        f"- {REVIEW_PREFIX}：{'、'.join(OPERATION_LABELS[item] for item in review) if review else '无'}",
        f"- {SKIPPED_PREFIX}：{'、'.join(OPERATION_LABELS[item] for item in skipped) if skipped else '无'}",
        "",
        f"## {SEQUENCE_TITLE}",
        "",
    ])
    active = [op["id"] for op in advice["operations"] if op["decision"] in {"recommend", "review"}]
    lines.append(
        " → ".join(OPERATION_LABELS[item] for item in active)
        if active else
        NO_SEQUENCE
    )
    lines.extend(["", f"> {METRIC_DISCLAIMER}"])
    if skipped:
        lines.extend([
            "",
            f"## {NOT_RECOMMENDED_TITLE}",
            "",
            NOT_RECOMMENDED_HEADER[0],
            NOT_RECOMMENDED_HEADER[1],
        ])
        for op in advice["operations"]:
            if op["decision"] == "skip":
                reason = op["starting_point"].replace("|", "/")
                lines.append(f"| {OPERATION_LABELS[op['id']]} | {reason} |")
    return lines


def _render_operation(operation, software, heading_level=3):
    implementation = operation["implementations"][software]
    hashes = "#" * heading_level
    fields = OPERATION_FIELDS
    lines = [
        "",
        f"{hashes} {OPERATION_LABELS[operation['id']]} — {DECISION_LABELS[operation['decision']]}",
        "",
        f"- {fields['confidence']}：{localized_value(operation['confidence'])}",
        f"- {fields['phase']}：{PHASE_LABELS[operation['phase']]}",
        f"- {fields['purpose']}：{operation['purpose']}",
        f"- {fields['parameter_mode']}：{localized_value(operation['parameter_mode'])}",
        f"- {fields['start']}：{operation['starting_point']}",
        f"- {fields['adjust']}：{operation['how_to_adjust']}",
        f"- {fields['evidence']}：",
    ]
    for evidence in operation["evidence"]:
        lines.append(
            f"  - `{evidence['path']}` = `{localized_value(evidence.get('value'))}`"
            f" — {localized_evidence(evidence)}"
        )
    lines.append(f"- {fields['tools']}：")
    for item in implementation["tools"]:
        lines.append(f"  - {item}")
    lines.append(f"- {fields['steps']}：")
    for index, item in enumerate(implementation["steps"], start=1):
        lines.append(f"  {index}. {item}")
    lines.append(f"- {fields['parameter_logic']}：")
    for item in implementation["parameter_logic"]:
        lines.append(f"  - {item}")
    lines.append(f"- {fields['mask_strategy']}：")
    for item in implementation["mask_strategy"]:
        lines.append(f"  - {item}")
    lines.append(f"- {fields['checkpoints']}：")
    for item in operation["acceptance_checks"]:
        lines.append(f"  - {item}")
    lines.append(f"- {fields['failure_signs']}：")
    for item in operation["rollback_conditions"]:
        lines.append(f"  - {item}")
    if operation["cautions"]:
        lines.append(f"- {fields['cautions']}：")
        for item in operation["cautions"]:
            lines.append(f"  - {item}")
    return lines


def _render_tracks(advice, tracks):
    lines = []
    multi = len(tracks) > 1
    active = [op for op in advice["operations"] if op["decision"] in {"recommend", "review"}]
    for index, track in enumerate(tracks):
        lines.extend(["", _track_heading(index, track, multi), ""])
        rendered = 0
        for operation in active:
            if operation["phase"] not in TRACK_CAPABILITY.get(track, set()):
                continue
            if track not in operation["implementations"]:
                continue
            lines.extend(_render_operation(operation, track, heading_level=3))
            rendered += 1
        if not rendered:
            lines.append(TRACK_EMPTY)
    return lines


def _render_track_mapping(advice, tracks):
    if len(tracks) < 2:
        return []
    shared = [
        operation for operation in advice["operations"]
        if operation["decision"] in {"recommend", "review"}
        and all(track in operation["implementations"] for track in tracks)
    ]
    if not shared:
        return []
    lines = [
        "",
        f"## {MAPPING_TITLE}",
        "",
        MAPPING_INTRO,
        "",
        f"| {MAPPING_STEP_COLUMN} | " + " | ".join(TRACK_LABELS.get(track, track) for track in tracks) + " |",
        "|---|" + "---|" * len(tracks),
    ]
    for operation in shared:
        cells = []
        for track in tracks:
            implementation = operation["implementations"][track]
            tools = implementation.get("tools") or []
            cells.append(implementation.get("primary_tool") or (tools[0] if tools else "—"))
        lines.append(f"| {OPERATION_LABELS[operation['id']]} | " + " | ".join(cells) + " |")
    return lines


def _upstream_location(advice):
    tracks = [track for track in advice["context"]["tracks"] if track in UPSTREAM_CAPABLE]
    if not tracks:
        return HANDOFF_UPSTREAM_FALLBACK
    return " / ".join(TRACK_LABELS.get(track, track) for track in tracks)


def _render_handoff(advice):
    contract = advice.get("handoff_contract")
    if not contract:
        return []
    lines = ["", f"## {HANDOFF_TITLE}", ""]
    if not contract["upstream_tracks"]:
        lines.extend([HANDOFF_NO_TRACK, ""])
    lines.extend([f"### {HANDOFF_PREREQ_TITLE}", ""])
    if contract["upstream_required"]:
        for operation_id in contract["upstream_required"]:
            lines.append(f"- [ ] {OPERATION_LABELS[operation_id]}")
    else:
        lines.append(f"- {HANDOFF_NO_PREREQ}")
    lines.extend([
        "",
        f"### {HANDOFF_UPSTREAM_TITLE}",
        "",
        HANDOFF_UPSTREAM_HEADER[0],
        HANDOFF_UPSTREAM_HEADER[1],
    ])
    upstream_only = [op for op in advice["operations"] if op["phase"] == "linear"]
    if upstream_only:
        for operation in upstream_only:
            lines.append(
                f"| {OPERATION_LABELS[operation['id']]} | {DECISION_LABELS[operation['decision']]} |"
                f" {_upstream_location(advice)} |"
            )
    else:
        lines.append("| 无 | — | — |")
    master_formats = " / ".join(
        MASTER_FORMATS.get(track, track) for track in contract["upstream_tracks"]
    ) or MASTER_FORMATS["generic"]
    lines.extend([
        "",
        f"### {HANDOFF_PARAM_TITLE}",
        "",
        f"- 上游母版：{master_formats}",
        "- 交给 Photoshop：16-bit TIFF；用 Convert to Profile 转换到目标色彩空间，不要用 Assign",
        "- 色彩校准：" + (
            "主轨已完成测光校色，Photoshop 不再做测光校色"
            if contract["color_calibration_done"] else
            "主轨未建议测光校色，Photoshop 同样不做"
        ),
        "- 窄带映射：" + (
            "主轨已建立并记录通道到颜色的映射" if contract["narrowband_mapping_used"] else "未使用窄带映射"
        ),
        "- 星点分离：" + (
            "主轨计划做星点分离，可向其索取星点层"
            if contract["star_separation_planned"] else
            "未计划星点分离，Photoshop 如需缩星必须自带准确星点层"
        ),
        "- 不可逆项：禁止仿制图章、修复画笔、内容识别填充、生成式填充修改天区",
    ])
    return lines


def _render_finishing(advice, finishing):
    lines = ["", f"## {FINISHING_TITLE} — {TRACK_LABELS.get(finishing, finishing)}", ""]
    owned = [
        operation for operation in advice["operations"]
        if operation["decision"] in {"recommend", "review"}
        and finishing in operation["implementations"]
    ]
    lines.extend([f"### {FINISHING_OWNED_TITLE}", ""])
    if owned:
        for operation in owned:
            lines.extend(_render_operation(operation, finishing, heading_level=4))
    else:
        lines.append(FINISHING_NONE)
    return lines


def _render_required_info(advice):
    if not advice["required_information"]:
        return []
    lines = ["", f"## {REQUIRED_INFO_TITLE}", ""]
    lines.extend(f"- {item}" for item in advice["required_information"])
    return lines


def render_markdown(advice):
    context = advice["context"]
    tracks = context.get("tracks") or ([context["software"]] if context.get("software") else [])
    finishing = context.get("finishing")
    if finishing and finishing in tracks:
        finishing = None
    lines = _render_header(advice)
    lines.extend(_render_priority(advice))
    lines.extend(_render_tracks(advice, tracks))
    lines.extend(_render_track_mapping(advice, tracks))
    lines.extend(_render_handoff(advice))
    if finishing:
        lines.extend(_render_finishing(advice, finishing))
    lines.extend(_render_required_info(advice))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compile analysis JSON into auditable processing advice")
    parser.add_argument("analysis_json")
    parser.add_argument(
        "--software",
        default="siril,pixinsight",
        help=(
            "Primary processing track(s). Accepts one value or a comma-separated list, "
            "e.g. 'siril,pixinsight'. 'generic' cannot be combined with a concrete application."
        ),
    )
    parser.add_argument(
        "--no-finishing",
        action="store_true",
        help="Disable the downstream finishing stage (Photoshop) section.",
    )
    parser.add_argument("--target-type", default="unknown")
    parser.add_argument("--target-name")
    parser.add_argument("--filter")
    parser.add_argument("--output-json")
    parser.add_argument("--output-markdown")
    args = parser.parse_args(argv)

    try:
        analysis_path = Path(args.analysis_json).expanduser().resolve()
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        advice = compile_advice(
            analysis,
            software=args.software,
            target_type=args.target_type,
            target_name=args.target_name,
            filter_name=args.filter,
            finishing=None if args.no_finishing else "auto",
        )
        errors = validate_advice(advice)
        if errors:
            raise ValueError("; ".join(errors))
        output_json = Path(args.output_json).expanduser().resolve() if args.output_json else analysis_path.with_name(
            analysis_path.stem.replace("_analysis", "") + "_advice.json"
        )
        output_markdown = Path(args.output_markdown).expanduser().resolve() if args.output_markdown else analysis_path.with_name(
            analysis_path.stem.replace("_analysis", "") + "_processing_report.md"
        )
        advice["advice_json"] = str(output_json)
        advice["report_markdown"] = str(output_markdown)
        output_json.write_text(json.dumps(advice, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        output_markdown.write_text(render_markdown(advice), encoding="utf-8")
    except Exception as exc:
        print(f"Advice generation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Advice JSON: {output_json}")
    print(f"Markdown report: {output_markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
