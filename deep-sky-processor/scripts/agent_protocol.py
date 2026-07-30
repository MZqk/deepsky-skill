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


def validate_action(action):
    if not isinstance(action, dict):
        raise ValueError("agent action must be a JSON object")
    operation = action.get("operation")
    if operation not in ALLOWED_OPERATIONS:
        raise ValueError(
            f"unsupported operation: {operation}; "
            f"allowed={sorted(ALLOWED_OPERATIONS)}"
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
    elif visibility == "increase":
        overrides["stretch_factor"] = 65.0

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

    def add(code, status, value, threshold, message):
        gates.append(
            {
                "code": code,
                "status": status,
                "value": value,
                "threshold": threshold,
                "message": message,
            }
        )

    stage = metrics.get("processing_stage", "final")
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

    corner_ratio = float(metrics.get("corner_uniformity_ratio", 1.0))
    if corner_ratio > 3.0:
        status = "failed" if "dbe" in steps else "warning"
        add("CORNER_NONUNIFORM", status, corner_ratio, "<=3.0",
            "四角亮度不均匀，可能存在梯度、黑边或 DBE 过减")

    uniform_ratio = float(metrics.get("uniform_5x5_dark_patch_ratio", 0.0))
    if uniform_ratio > 0.35:
        add("DENOISE_PLASTICITY", "warning", uniform_ratio, "<=0.35",
            "暗部均匀斑块比例偏高，需要检查降噪塑料感")

    star_ratio = float(metrics.get("star_area_ratio", 0.0))
    if target_type not in ("globular_cluster", "open_cluster") and star_ratio > 0.18:
        add("STAR_DOMINANCE", "warning", star_ratio, "<=0.18",
            "星点占比偏高，可能压制主体")

    status = "review_required" if gates else "success"
    if any(gate["status"] == "failed" for gate in gates):
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
    before, _ = read_image(str(before_path))
    after, _ = read_image(str(after_path))
    before = _rgb(before)
    after = _rgb(after)
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

    metrics_before = calculate_metrics(before)
    metrics_after = calculate_metrics(after)
    status, gates = evaluate_quality_gates(
        metrics_after,
        target_type=(context or {}).get("target_type"),
        steps=(context or {}).get("steps"),
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "created_at": now_iso(),
        "source": {"before": str(before_path), "after": str(after_path)},
        "previews": paths,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
        "metric_delta": {
            key: round(float(metrics_after[key]) - float(metrics_before[key]), 6)
            for key in metrics_after
            if key in metrics_before
            and isinstance(metrics_after[key], (int, float))
            and isinstance(metrics_before[key], (int, float))
        },
        "quality_gates": gates,
        "critic_checklist": build_critic_checklist(
            (context or {}).get("target_type")
        ),
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
