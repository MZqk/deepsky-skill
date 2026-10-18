#!/usr/bin/env python3
"""Machine protocol helpers for agent-in-the-loop deep-sky processing."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from skimage.transform import resize

from fits_io import read_image, write_image
from quality_metrics import calculate_metrics
from artifact_gates import evaluate_star_artifact_gates
from gate_authority import seal_gate


SCHEMA_VERSION = "1.0"
ALLOWED_OPERATIONS = {
    "run_step",
    "create_variants",
    "masked_adjustment",
    "accept",
    "rollback",
    "request_human_review",
}
ALLOWED_VERDICTS = {"accept", "retry", "rollback", "review_required"}


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json_atomic(payload, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(output.parent), delete=False
    ) as tmp:
        json.dump(payload, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, output)


# action 里允许出现的**流程控制**字段 —— 它们由 agent_workflow 自己消费，
# 不转发给 run_pipeline。其余未知字段一律报错（见 validate_action）。
# 清单来自实际消费点：`accept` 用 artifact / resolutions / verdict，
# `create_variants` 会把 variant 的 `id` 等字段 merge 进 action 再校验。
_ACTION_CONTROL_FIELDS = frozenset({
    "operation", "step", "intent", "params", "region", "object_name",
    "object_radius", "variants", "mask", "adjustment", "verdict",
    "issues", "actions", "notes", "comment",
    # accept / create_variants / resolve_review 消费的字段
    "artifact", "artifact_sha256", "id", "review", "resolutions",
    "issue_id", "outcome", "reason", "evidence",
})

# action 里允许出现、且**确实会被转发**给 run_pipeline 的字段。
# 显式列举而非「不报错就放行」，是为了让「拼错字段名」和「字段名对但没接线」
# 两种错误都在提交前暴露，而不是静默走默认值。
_ACTION_PIPELINE_FIELDS = frozenset({
    # 目标与强度策略
    "preset", "color_mode", "style", "style_strength",
    "stretch_method", "input_is_linear",
    # 星点链路
    "subject_mask", "external_starless", "external_starless_domain",
    "external_starless_midtones", "external_denoised", "external_detail_strength",
    "use_starnet", "starnet_path", "starnet_stride", "starnet_domain",
    "starnet_timeout", "starnet_recommendation",
    "export_starnet_payload", "export_starnet_payload_domain",
    "star_blend_mode", "halo_guard",
    # 局部与掩膜
    "local_center", "local_radius", "local_strength",
    # 裁切与内存
    "auto_low_memory", "low_memory", "low_memory_threshold_mpix",
    "auto_crop_edges", "auto_crop_target", "auto_crop_padding",
    "crop", "stacking_crop", "tile_size", "cleanup", "save_intermediates",
    "keep_all", "quality_policy", "background_neutralize_lock",
    "override_params",
    # 识别与天区解析
    "recognize", "recognize_output", "recognize_input", "recognition_workflow_dir",
    "plate_solve", "solve_field_path", "plate_solve_timeout", "catalog",
    # 参考图匹配
    "reference_image", "reference_auto_search", "reference_color_match",
    "reference_color_only", "reference_strength", "reference_match_orientation",
    # 线性修复与去卷积
    "stellar_repair", "stellar_repair_mode", "stellar_repair_strength",
    "linear_deconv", "deconv_iterations", "palette",
    # 降噪
    "deepsnr", "deepsnr_path", "deepsnr_timeout", "deepsnr_strength",
    # 输出
    "result_json",
})


def validate_action(action):
    if not isinstance(action, dict):
        raise ValueError("agent action must be a JSON object")
    operation = action.get("operation")
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(
            f"unsupported operation: {operation}; "
            f"allowed={sorted(ALLOWED_OPERATIONS)}"
        )
    # 未知字段必须在提交前报错。此前 `external_starless` 放进 `params` 会被静默
    # 忽略（它走 run_pipeline 的独立形参，不读 cfg），表现为 star_remove 悄悄
    # 走内存跳过分支、artifacts 只有 manifest、**无任何报错** —— 排查成本极高。
    unknown = (set(action) - _ACTION_CONTROL_FIELDS - _ACTION_PIPELINE_FIELDS)
    if unknown:
        raise ValueError(
            "action 含未声明字段（会被静默忽略）: "
            f"{sorted(unknown)}；允许的流程字段={sorted(_ACTION_CONTROL_FIELDS)}，"
            f"可转发的管线字段见 agent_protocol._ACTION_PIPELINE_FIELDS"
        )
    if operation == "run_step" and not action.get("step"):
        raise ValueError("run_step requires step")
    if operation == "masked_adjustment":
        if not isinstance(action.get("mask"), dict):
            raise ValueError("masked_adjustment requires mask")
        if not isinstance(action.get("adjustment"), dict):
            raise ValueError("masked_adjustment requires adjustment")
    if operation == "create_variants":
        variants = action.get("variants")
        if not isinstance(variants, list) or not (2 <= len(variants) <= 5):
            raise ValueError("create_variants requires 2-5 variants")
        for variant in variants:
            if not isinstance(variant, dict) or not variant.get("id"):
                raise ValueError("each variant requires an id")
    return action


def validate_review(review):
    if not isinstance(review, dict):
        raise ValueError("review must be a JSON object")
    verdict = review.get("verdict")
    if verdict not in ALLOWED_VERDICTS:
        raise ValueError(
            f"unsupported verdict: {verdict}; allowed={sorted(ALLOWED_VERDICTS)}"
        )
    actions = review.get("actions", [])
    if not isinstance(actions, list):
        raise ValueError("review.actions must be an array")
    for action in actions:
        validate_action(action)
    return review


def intent_to_overrides(intent):
    """Translate semantic visual intent into bounded deterministic parameters."""
    if not intent:
        return {}
    if not isinstance(intent, dict):
        raise ValueError("intent must be a JSON object")

    overrides = {}
    background = intent.get("background")
    if background == "darker":
        overrides["target_bg"] = 0.055
    elif background == "slightly_darker":
        overrides["target_bg"] = 0.07
    elif background == "brighter":
        overrides["target_bg"] = 0.12

    visibility = intent.get("subject_visibility")
    if visibility == "increase_slightly":
        overrides["stretch_factor"] = 52.0
        overrides["stretch_subject_midtones"] = 0.27
    elif visibility == "increase":
        overrides["stretch_factor"] = 65.0
        overrides["stretch_subject_midtones"] = 0.30

    core = intent.get("core_protection")
    if core == "strong":
        overrides["ghs_protect_strength"] = 0.75
        overrides["hdr_strength"] = 0.55
    elif core == "moderate":
        overrides["ghs_protect_strength"] = 0.6

    stars = intent.get("star_dominance")
    if stars == "reduce_slightly":
        overrides["star_reduction"] = 0.18
    elif stars == "reduce":
        overrides["star_reduction"] = 0.3

    noise = intent.get("noise_tolerance")
    if noise == "preserve_detail":
        overrides["final_denoise_lum"] = 0.006
        overrides["final_denoise_chroma"] = 0.018
    elif noise == "clean_background":
        overrides["final_denoise_lum"] = 0.018
        overrides["final_denoise_chroma"] = 0.055

    saturation = intent.get("saturation")
    if saturation == "lower":
        overrides["saturation"] = 1.1
    elif saturation == "moderate":
        overrides["saturation"] = 1.3
    elif saturation == "increase_slightly":
        overrides["saturation"] = 1.5

    return overrides


def evaluate_quality_gates(metrics, target_type=None, steps=None):
    """Convert quantitative anchors into explicit review gates."""
    steps = set(steps or [])
    gates = []
    gates.extend(gate for gate in metrics.get('channel_clipping_gates', [])
                 if gate.get('status') in ('warning', 'failed'))

    def add(code, status, value, threshold, message, escalate=True):
        """Record a gate.

        escalate=False 表示"只记录不升级"：该门会出现在 gates / warnings 里供审查，
        但不参与 status 判定。用于诊断性指标，避免历史正常文件突然全部变成
        review_required。

        门禁在此**创建并封印**（gate_authority.seal_gate）：授权字段
        （code/status/escalate/value/threshold）自此不可改写，后续任何改动
        都会被 quality_state 复验检出。诊断字段（message/evidence）可自由
        改写，不影响授权 —— 见 gate_authority 模块文档。
        """
        gates.append(
            seal_gate(
                {
                    "code": code,
                    "status": status,
                    "value": value,
                    "threshold": threshold,
                    "message": message,
                    "escalate": escalate,
                }
            )
        )

    # 保真度门禁（噪声增益 / 色度保真）：与上面的标量门并存。
    # 由 pipeline 评估后通过 manifest_data['fidelity_gates'] 传入。
    #
    # 纪律：**只在触发时 append**（fidelity_gates 内部已过滤健康项），且必须
    # 带status 过滤 —— 保真度体系有 degraded / unavailable / uncalibrated /
    # skipped 四个既有门禁体系没有的状态，它们只应记录、不参与 status 判定。
    # 任何 escalate=True 的门会让本函数末尾的 escalating 判定把 status 打成
    # review_required，所以 escalate 必须原样透传、不得改写。
    for _gate in metrics.get('fidelity_gates', []) or []:
        if _gate.get('status') not in ('warning', 'failed', 'degraded',
                                       'unavailable', 'uncalibrated'):
            continue
        gates.append({
            "code": _gate.get("code", "FIDELITY_GATE"),
            "status": _gate["status"],
            "value": _gate.get("value"),
            "threshold": _gate.get("threshold"),
            "message": _gate.get("message", ""),
            "escalate": bool(_gate.get("escalate", False)),
        })

    stage = metrics.get("processing_stage", "final")
    if stage == 'stellar_layer':
        return ('review_required' if any(g.get('escalate', True) for g in gates) else 'success'), gates
    median = float(metrics.get("median", 0.0))
    p1 = float(metrics.get("p1", median))
    negative_ratio = float(metrics.get("negative_pixel_ratio", 0.0))
    nonpositive_ratio = float(metrics.get("nonpositive_pixel_ratio", 0.0))

    if stage == "linear":
        if negative_ratio > 0.01:
            add("LINEAR_BACKGROUND_UNDERSHOOT", "warning", negative_ratio, "<=0.01",
                "线性背景负值比例偏高，应检查背景基线扣除或 DBE 过减")
    else:
        if nonpositive_ratio > 0.01 or p1 <= 1e-4:
            add(
                "BACKGROUND_CRUSHED",
                "warning",
                {
                    "p1": p1,
                    "nonpositive_pixel_ratio": nonpositive_ratio,
                },
                "p1>0.0001 and nonpositive_pixel_ratio<=0.01",
                "最终图暗部存在明显贴黑或裁切，需要恢复黑位过渡",
            )
        low_limit = 0.04 if target_type == "emission_nebula" else 0.025
        if median < low_limit:
            add("BACKGROUND_LOW", "warning", median, f">={low_limit}",
                "最终背景整体偏低，需要确认微弱云气与暗尘过渡是否仍然可信")

    if median > 0.30:
        add("BACKGROUND_TOO_BRIGHT", "warning", median, "<=0.30",
            "整体亮度偏高，需要检查背景与核心")

    corner_analysis = metrics.get("corner_analysis")
    corner_ratio = float(metrics.get("corner_uniformity_ratio", 1.0))
    raw_corner_ratio = float(metrics.get("raw_corner_uniformity_ratio", corner_ratio))

    if corner_analysis and corner_analysis.get("exemption_applied"):
        # 触发天体感知豁免：天体物理结构（如发射星云外延/旋臂）显著覆盖角区，且纯背景角平整度达标
        add(
            "CORNER_NONUNIFORM",
            "passed",
            {
                "effective_ratio": corner_ratio,
                "raw_ratio": raw_corner_ratio,
                "celestial_corners": corner_analysis.get("celestial_dominated_corners"),
            },
            "<=3.0",
            corner_analysis.get("exemption_reason")
            or "四角纯背景均匀度健康，角区偏高为真实天体物理结构延伸（已自动感知豁免）",
            escalate=False,
        )
    elif corner_ratio > 3.0:
        status = "failed" if "dbe" in steps else "warning"
        add("CORNER_NONUNIFORM", status, corner_ratio, "<=3.0",
            "四角亮度不均匀，可能存在梯度、黑边或 DBE 过减")

    # 风格色调曲线的局部黑位裁切：报的是**成因**（黑位越过局部背景），发生在
    # style 步骤本身，因此早于并独立于末端才可见的 CORNER_NONUNIFORM。
    #
    # 注意：只在触发时 append。本函数末尾用 `escalating = [g for g in gates if
    # g["escalate"]]` 判定 status，**只看 escalate 标志、不看 status** —— 追加一个
    # escalate=True 的 passed 门会让所有出图都变成 review_required。
    # 同理 pipeline 会把所有门禁都收进 warnings，追加 passed 门只会产生噪音。
    style_diag = metrics.get("style_diagnostics")
    if style_diag:
        frac_before = float(style_diag.get("clipped_block_frac_before", 0.0))
        after = int(style_diag.get("blocks_clipped_after_repair", 0))
        zeroed = int(style_diag.get("blocks_zeroed_final", 0))
        shift = float(style_diag.get("repair_shift", 0.0))
        value = {
            "clipped_block_frac_before": frac_before,
            "blocks_clipped_before_repair": style_diag.get("blocks_clipped_before_repair"),
            "blocks_clipped_after_repair": after,
            "blocks_zeroed_final": zeroed,
            "repair_shift": shift,
            "min_block_median_final": style_diag.get("min_block_median_final"),
            "n_blocks": style_diag.get("n_blocks"),
            "block_size": style_diag.get("block_size"),
            "floor_global": style_diag.get("floor_global"),
            "floor_min": style_diag.get("floor_min"),
            "floor_max": style_diag.get("floor_max"),
        }
        threshold = (
            "clipped_block_frac_before<=0.05 且 repair_shift<=0.01 "
            "且 blocks_zeroed_final==0"
        )
        if zeroed > 0 or after > 0:
            add(
                "STYLE_LOCAL_BLACK_CLIP",
                "failed",
                value,
                threshold,
                "风格色调曲线把局部背景裁成纯黑，且自动下移黑位的自愈未完全生效；"
                "需降低该风格 black_floor 或 style_strength",
            )
        elif frac_before > 0.05 or shift > 0.01:
            add(
                "STYLE_LOCAL_BLACK_CLIP",
                "warning",
                value,
                threshold,
                f"风格色调曲线在局部暗背景上过度抬升黑位：自愈前 "
                f"{style_diag.get('blocks_clipped_before_repair')}/"
                f"{style_diag.get('n_blocks')} 个区块会被裁到 0，"
                f"已下移黑位 {shift:.4f} 修复；建议降低该风格 black_floor 或 "
                f"style_strength",
                escalate=False,
            )

    # 去星修补足迹：报的也是**成因**（inpaint 补丁被拉伸放大），在拉伸后即可见，
    # 早于并独立于末端的 CORNER_NONUNIFORM / DENOISE_PLASTICITY。
    # 与 STYLE_LOCAL_BLACK_CLIP 同样的 append 纪律：只在触发时 append；
    # passed / skipped / error 一律不进 gates（否则会让所有出图变 review_required，
    # 并在 warnings 里制造噪音）。
    footprint = metrics.get("inpaint_footprint")
    if footprint and footprint.get("status") in ("warning", "failed"):
        add(
            "INPAINT_FOOTPRINT",
            footprint["status"],
            footprint.get("value"),
            footprint.get("threshold"),
            footprint.get("message"),
            escalate=(footprint["status"] == "failed"),
        )

    uniform_ratio = float(metrics.get("uniform_5x5_dark_patch_ratio", 0.0))
    if stage != "linear" and uniform_ratio > 0.35:
        add("DENOISE_PLASTICITY", "warning", uniform_ratio, "<=0.35",
            "暗部均匀斑块比例偏高，需要检查降噪塑料感")

    star_ratio = float(metrics.get("star_area_ratio", 0.0))
    if target_type not in ("globular_cluster", "open_cluster") and star_ratio > 0.18:
        # 只记录，不升级 status。`star_area_ratio` 由 detect_stars 的**膨胀后**
        # 蒙版面积算出（star_tools 的 `binary_dilation(kept, disk(dilation_radius))`），
        # 实测 NGC6888 上：膨胀前 0.1475、膨胀后 0.2550 —— 膨胀只贡献 1.73×，
        # 即使完全不膨胀也仍是阈值 0.18 的 82%。即该指标在密集星场**天然偏高**
        # （召回 86~100% 的门等于没有门），作 escalate 门会稳定误报。
        # 用 `n_components_kept/total` 替代也无效：实测该比值 0.9998，零判别力。
        # 门禁条目与数值仍完整进 gates/warnings，供人工审查时参考。
        # **该阈值仅在 NGC6888 一例上标定过，需随样本积累复核。**
        add("STAR_DOMINANCE", "warning", star_ratio, "<=0.18",
            "星点占比偏高，可能压制主体（膨胀后蒙版面积；密集星场天然偏高，仅供参考）",
            escalate=False)

    # 背景色偏：只记录，不升级 status（escalate=False）。
    # 这是诊断性指标 —— 现有 7 道门里没有任何一道看每通道背景色偏，
    # 导致严重偏色的输出（例如背景 R/G≈3.3）能一路通过。
    cast = metrics.get("background_color_cast_magnitude")
    if cast is not None and stage != "linear" and float(cast) > 0.08:
        add(
            "BACKGROUND_COLOR_CAST",
            "warning",
            {
                "magnitude": float(cast),
                **(metrics.get("background_color_cast") or {}),
            },
            "max(|R/G-1|,|B/G-1|)<=0.08",
            "背景通道色偏偏高，可能存在色彩校准失效或通道塌缩；仅记录，不触发人工复核",
            escalate=False,
        )

    escalating = [gate for gate in gates if gate.get("escalate", True)]
    status = "review_required" if escalating else "success"
    if any(gate["status"] == "failed" and gate.get("escalate", True)
           for gate in gates):
        status = "review_required"
    return status, gates


def _rgb(image):
    image = np.asarray(image, dtype=np.float32)
    if image.ndim == 2:
        image = np.stack([image] * 3, axis=-1)
    return np.clip(image[..., :3], 0, 1)


def _preview(image, max_side=1400):
    image = _rgb(image)
    height, width = image.shape[:2]
    scale = min(1.0, max_side / max(height, width))
    if scale < 1.0:
        image = resize(
            image,
            (max(1, int(height * scale)), max(1, int(width * scale)), 3),
            preserve_range=True,
            anti_aliasing=True,
        ).astype(np.float32)
    return image


def create_review_bundle(before_path, after_path, output_dir, context=None):
    """Create compact visual evidence for an LLM or human critic."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    before, before_meta = read_image(str(before_path))
    after, after_meta = read_image(str(after_path))
    before = _rgb(before)
    after = _rgb(after)
    context = context or {}
    reference_comparable = (
        before.shape == after.shape
        and context.get("input_is_linear", before_meta["is_linear"])
        == context.get("output_is_linear", after_meta["is_linear"])
    )
    if before.shape != after.shape:
        before = resize(
            before, after.shape, preserve_range=True, anti_aliasing=True
        ).astype(np.float32)

    delta = after - before
    abs_delta = np.abs(delta)
    scale = max(float(np.percentile(abs_delta, 99.5)), 1e-6)
    diff_preview = np.clip(abs_delta / scale, 0, 1)
    luminance_delta = np.mean(delta, axis=2)
    signed = np.zeros((*luminance_delta.shape, 3), dtype=np.float32)
    signed[..., 0] = np.clip(luminance_delta / scale, 0, 1)
    signed[..., 2] = np.clip(-luminance_delta / scale, 0, 1)

    paths = {
        "before_preview": str(output_dir / "before.jpg"),
        "after_preview": str(output_dir / "after.jpg"),
        "absolute_difference": str(output_dir / "difference.jpg"),
        "signed_luminance_difference": str(output_dir / "signed_difference.jpg"),
    }
    write_image(_preview(before), paths["before_preview"])
    write_image(_preview(after), paths["after_preview"])
    write_image(_preview(diff_preview), paths["absolute_difference"])
    write_image(_preview(signed), paths["signed_luminance_difference"])

    metrics_before = calculate_metrics(before, {
        "processing_stage": "linear" if context.get("input_is_linear", before_meta["is_linear"]) else "final",
    })
    metrics_after = calculate_metrics(after, {
        "processing_stage": ('stellar_layer' if context.get('image_role') == 'stars'
                             else "linear" if context.get("output_is_linear", after_meta["is_linear"]) else "final"),
    })
    if not context.get('output_is_linear', after_meta['is_linear']):
        from artifact_gates import check_channel_clipping
        metrics_after['channel_clipping_gates'] = [check_channel_clipping(
            after, before if reference_comparable else None)]
    status, gates = evaluate_quality_gates(
        metrics_after,
        target_type=(context or {}).get("target_type"),
        steps=(context or {}).get("steps"),
    )

    # 具名星点伪影门禁（STAR_RINGING / STAR_BLOAT / STAR_LAYER_LOSS /
    # STAR_HOLES / CORE_BURNING）：与标量门禁并存，输出可审计的定位证据。
    # 数值门禁是审查触发器而非视觉质量的证明；门禁自身异常时降级为
    # error 记录并要求复核，继续生成 review bundle。
    try:
        if context.get('image_role') == 'stars':
            star_artifact_gates = {'schema': 'artifact_gates/1.0', 'status': 'skipped', 'gates': [],
                                   'summary': 'Isolated stellar layer: inspect layer previews; field gates run after composition'}
        else:
            gate_before = before
            comparable = reference_comparable
            diagnostics = context.get('stretch_diagnostics') or {}
            display = diagnostics.get('display_reference') or {}
            if display.get('path') and 'stretch' in context.get('steps', []):
                import hashlib
                digest = hashlib.sha256(np.ascontiguousarray(before).tobytes()).hexdigest()
                if digest != display.get('source_sha256'):
                    raise ValueError('Display reference source does not match the review input')
                gate_before, _ = read_image(display['path'])
                if gate_before.shape != after.shape:
                    raise ValueError('Display reference geometry does not match')
                comparable = True
                paths['display_reference'] = str(output_dir / 'display_reference.jpg')
                paths['display_difference'] = str(output_dir / 'display_difference.jpg')
                write_image(_preview(gate_before), paths['display_reference'])
                difference = np.abs(after - gate_before)
                write_image(_preview(difference / max(float(np.percentile(difference, 99.5)), 1e-6)), paths['display_difference'])
            star_artifact_gates = evaluate_star_artifact_gates(
                gate_before, after, steps=context.get("steps"),
                reference_comparable=comparable, display_reference=bool(display.get('path')),
            )
            star_artifact_gates['context'].update(
                measurement_domain='display' if display.get('path') else 'same_domain' if comparable else 'cross_domain',
                reference_type='fixed_mtf' if display.get('path') else 'stage_input',
                input_reference_comparable=reference_comparable)
            if diagnostics.get('core_plateau', {}).get('escalate'):
                status = 'review_required'
    except Exception as exc:
        status = "review_required"
        star_artifact_gates = {
            "schema": "artifact_gates/1.0",
            "status": "error",
            "summary": f"星点门禁执行失败：{exc}",
            "gates": [],
        }
    if star_artifact_gates.get("status") == "review_required":
        status = "review_required"

    linear_measurement = None
    if context.get('input_is_linear', before_meta['is_linear']) and 'stretch' in context.get('steps', []):
        from star_tools import estimate_fwhm
        linear_source = context.get('linear_star_source') or str(before_path)
        linear_image = read_image(linear_source)[0] if context.get('linear_star_source') else before
        width, deviation, n_used, n_candidates = estimate_fwhm(np.mean(linear_image[..., :3], axis=2) if linear_image.ndim == 3 else linear_image)
        linear_measurement = {'domain': 'linear', 'role': 'physical_width', 'method': 'gaussian_psf_estimate',
                              'source': str(linear_source),
                              'estimated_fwhm_px': float(width) if n_used >= 3 else None,
                              'std_px': float(deviation) if n_used >= 3 else None,
                              'n_stars': int(n_used), 'n_candidates': int(n_candidates),
                              'status': 'measured' if n_used >= 3 else 'insufficient_samples'}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "created_at": now_iso(),
        "source": {"before": str(before_path), "after": str(after_path)},
        "previews": paths,
        "metrics_before": metrics_before,
        "linear_star_measurement": linear_measurement,
        "metrics_after": metrics_after,
        "metric_delta": {
            key: round(float(metrics_after[key]) - float(metrics_before[key]), 6)
            for key in metrics_after
            if key in metrics_before
            and isinstance(metrics_after[key], (int, float))
            and isinstance(metrics_before[key], (int, float))
        },
        "quality_gates": gates,
        "star_artifact_gates": star_artifact_gates,
        "critic_checklist": build_critic_checklist(
            (context or {}).get("target_type")
        ),
        "runtime": context.get("runtime"),
        "execution": context.get("execution"),
        "stretch_diagnostics": context.get("stretch_diagnostics"),
        "context": context or {},
    }
    report_path = output_dir / "review.json"
    write_json_atomic(payload, report_path)
    payload["report_path"] = str(report_path)
    return payload


def build_critic_checklist(target_type):
    common = [
        "确认处理后没有出现原图不存在的结构",
        "检查背景是否存在黑坑、拼接缝或过度平滑",
        "检查星点是否有黑边、紫边、硬边或异常膨胀",
        "检查高光核心是否失去层次",
    ]
    specialized = {
        "emission_nebula": [
            "检查 Hα 是否由深红偏成品红",
            "检查 OIII 是否变成不自然的电蓝",
            "检查星云细丝边缘是否出现锐化光环",
        ],
        "galaxy": [
            "检查星系核心是否过曝",
            "检查尘埃带是否连续且未被降噪抹除",
            "检查旋臂是否出现过度锐化",
        ],
        "reflection_nebula": [
            "检查蓝色反射区域是否过饱和",
            "检查暗弱尘埃是否被背景压暗吞没",
        ],
        "globular_cluster": [
            "检查是否错误缩星或破坏密集恒星主体",
            "检查恒星颜色是否保持自然",
        ],
        "open_cluster": [
            "检查是否错误缩星或破坏恒星主体",
            "检查亮星核心和星色是否保留",
        ],
    }
    return common + specialized.get(target_type, [])
