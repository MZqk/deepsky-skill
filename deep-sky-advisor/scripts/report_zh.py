"""Labels and layout for generated Markdown processing reports.

This module is a LABEL LAYER only. It maps stable identifiers (evidence JSON paths,
enum values, software names, phases) to Chinese display text, and holds the report
section headings.

It must NOT carry operational content — no tools, steps, parameter logic, or mask
strategy. That content lives in `software_guidance.py` (per application) and
`generate_advice.py` (per operation, application-independent). `references/*.md` is
extended reading and is not authoritative for the generated report.
"""


EVIDENCE_LABELS = {
    "classification.frame_role": "文件角色分类",
    "classification.processing_stage": "处理阶段分类",
    "classification.transfer_state": "线性/非线性状态判断",
    "classification.channel_model": "通道模型",
    "classification.filter": "FITS/XISF 滤镜元数据",
    "classification.object": "FITS/XISF 目标元数据",
    "file.header.WCSAXES": "WCS 信息",
    "file.format": "输入文件格式",
    "statistics.exact_min_ratio": "等于原始最小值的像素比例",
    "statistics.near_min_ratio": "接近原始最小值的像素比例",
    "statistics.exact_max_ratio": "等于原始最大值的像素比例",
    "background.plane.magnitude_across_frame": "低信号背景平面跨画面变化幅度",
    "background.plane.r_squared": "背景平面拟合解释度 R²",
    "background.corner_median_range": "四角背景中位数差异",
    "color.channel_p99_normalized": "各通道 P99 信号",
    "color.background_ratios_to_mean": "背景 RGB 相对比例",
    "color.channel_correlation": "通道间相关度",
    "color.collapsed_channels": "信号塌陷的通道",
    "noise.background_noise_sigma_normalized": "归一化高通 MAD 噪声估计",
    "noise.block_count": "参与噪声评估的背景分块数量",
    "stars.usable_star_count": "有效星点样本数量",
    "stars.fwhm_major_median_px": "星点长轴 FWHM 中位数",
    "stars.eccentricity_p90": "星点偏心率 P90",
    "stars.position_angle_median_deg": "星点方向角中位数",
    "stars.density_per_megapixel": "每百万像素有效星点密度",
    "clipping.shadow_ratio_le_0_001": "稳健映射暗端占比",
    "clipping.highlight_ratio_ge_0_999": "稳健映射亮端占比",
    "clipping.per_channel.r.highlight_ratio_ge_0_999": "R 通道稳健映射亮端占比",
    "clipping.per_channel.g.highlight_ratio_ge_0_999": "G 通道稳健映射亮端占比",
    "clipping.per_channel.b.highlight_ratio_ge_0_999": "B 通道稳健映射亮端占比",
    "clipping.per_channel.mono.highlight_ratio_ge_0_999": "单通道稳健映射亮端占比",
    "user_context.target_type": "用户提供的目标类型",
    "user_context.target_name": "用户提供的目标名称",
    "user_context.filter": "用户提供的滤镜/通道信息",
}


VALUE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
    "qualitative": "定性参数",
    "evidence_bound": "证据约束参数",
    "measured": "已测量",
    "unavailable": "不可用",
    "likely_linear": "很可能为线性数据",
    "stacked_or_integrated": "已叠加/积分",
    "light": "亮场单帧",
    "dark": "暗场",
    "flat": "平场",
    "bias": "偏置场",
    "rgb": "RGB 三通道",
    "mono_or_cfa": "单通道或 CFA",
    "galaxy": "星系",
    "emission_nebula": "发射星云",
    "reflection_nebula": "反射星云",
    "dark_nebula": "暗星云",
    "planetary_nebula": "行星状星云",
    "supernova_remnant": "超新星遗迹",
    "globular_cluster": "球状星团",
    "open_cluster": "疏散星团",
    "wide_field": "宽场星野",
    "generic": "通用流程",
    "siril": "Siril",
    "pixinsight": "PixInsight",
    "photoshop": "Photoshop",
}


TRACK_LABELS = {
    "generic": "通用流程",
    "siril": "Siril",
    "pixinsight": "PixInsight",
    "photoshop": "Photoshop",
}

TRACK_ORDINALS = ["A", "B", "C", "D"]

PHASE_LABELS = {
    "linear": "线性阶段",
    "nonlinear": "非线性阶段",
    "finishing": "精修阶段",
    "export": "导出阶段",
}

MASTER_FORMATS = {
    "generic": "高位深母版",
    "siril": "32-bit FITS 母版",
    "pixinsight": "32-bit XISF 母版",
    "photoshop": "分层 16-bit PSD/TIFF 母版",
}


REPORT_TITLE = "深空天文后期处理建议"
FILE_LABEL = "文件"
TRACK_LABEL = "主轨"
FINISHING_LABEL = "二次加工"
TARGET_TYPE_LABEL = "目标类型"
TARGET_NAME_LABEL = "目标名称"
FILTER_LABEL = "滤镜/通道"
DEVICE_LABEL = "拍摄设备"
UNKNOWN_VALUE = "未知"
NOT_PROVIDED_VALUE = "未提供"

TRACK_SECTION_TITLE = "完整流程"
MULTI_TRACK_NOTE = "并列备选，任选其一执行"
FINISHING_NOTE = "在主轨之后执行"

PRIORITY_TITLE = "处理优先级"
RECOMMENDED_PREFIX = "建议执行"
REVIEW_PREFIX = "需要确认"
SKIPPED_PREFIX = "当前跳过"
SEQUENCE_TITLE = "推荐顺序"
NO_SEQUENCE = "当前证据不足，无法建立可靠的后期处理顺序。"
NOT_RECOMMENDED_TITLE = "当前不建议的操作"
NOT_RECOMMENDED_HEADER = ("| 操作 | 原因 |", "|---|---|")

MAPPING_TITLE = "双轨对应关系"
MAPPING_INTRO = "同一诊断结论在两套软件中的等价步骤；两轨任选其一执行，不要叠加。"
MAPPING_STEP_COLUMN = "步骤"

HANDOFF_TITLE = "交接给 Photoshop 的契约"
HANDOFF_PREREQ_TITLE = "前置：以下项必须在主轨完成"
HANDOFF_UPSTREAM_TITLE = "不在 Photoshop 处理的操作"
HANDOFF_UPSTREAM_HEADER = ("| 操作 | 当前判定 | 处理位置 |", "|---|---|---|")
HANDOFF_PARAM_TITLE = "技术参数"
HANDOFF_NO_PREREQ = "当前诊断未建议线性阶段操作，主轨完成后即可交接。"
HANDOFF_NO_TRACK = "未指定主轨；以下内容假设你已在 Siril 或 PixInsight 完成前置步骤。"
HANDOFF_UPSTREAM_FALLBACK = "Siril / PixInsight"

FINISHING_TITLE = "二次加工"
FINISHING_OWNED_TITLE = "可执行的收尾操作"
FINISHING_NONE = "当前诊断没有需要在该软件中执行的收尾操作。"

TRACK_EMPTY = "当前诊断没有需要在该软件中执行的步骤。"

OPERATION_FIELDS = {
    "confidence": "置信度",
    "phase": "处理阶段",
    "purpose": "目的",
    "parameter_mode": "参数模式",
    "start": "起始策略",
    "adjust": "调整原则",
    "evidence": "诊断证据",
    "tools": "关键工具/入口",
    "steps": "操作步骤",
    "parameter_logic": "参数选择依据",
    "mask_strategy": "蒙版与保护策略",
    "checkpoints": "阶段验收",
    "failure_signs": "失败征象与回退条件",
    "cautions": "风险提示",
}

REQUIRED_INFO_TITLE = "仍需补充的信息"

METRIC_DISCLAIMER = (
    "以下数值指标只用于定位风险，不能脱离原图、预览和目标类型直接解释为物理结论。"
)


def localized_value(value):
    if isinstance(value, str):
        return VALUE_LABELS.get(value, value)
    return value


def localized_evidence(evidence):
    path = evidence["path"]
    return EVIDENCE_LABELS.get(path, path)
