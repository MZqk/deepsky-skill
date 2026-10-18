#!/usr/bin/env python3
"""保真度门禁阈值标定器（离线工具，非管线组件）。

在**降采样 1400px 口径**下测量 5 个度量的真实分布，并构造劣化对照组，据此定出
degraded / hard 阈值。

为什么必须实测而不能抄 starun 侧的值：那边的 0.06 / 1.60 / 0.015 / 1.05 / 0.95
是在它自己的数据上标的。同一门在不同数据上松紧完全不同 —— 直接搬来，A 侧会
出现 1.0~1.5× 的静默通过区（这正是 `level_drop_factor=2.0`事故的形态）。

口径纪律：`downsample_for_gates` 带 `anti_aliasing=True`，会平滑高频噪声，
1400px 上的 sigma 系统性低于全分辨率。**标定与运行时必须同口径**，故本脚本
一律在降采样后的图上测量。

用法:
  python scripts/calibrate_fidelity_gates.py --corpus NGC6888 M31 M45 \
      --calib-root /tmp/calib --output references/fidelity_gate_calibration.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fidelity_gates as fg
from fidelity_gates import (
    _background_mask,
    _chroma_map,
    _downsample,
    _hue_ratio_drift_p95,
    _mad_sigma,
    _luma,
    _rgb,
    _resolve_signal_mask,
    _chroma_visibility_metrics,
)


# 劣化对照的注入幅度。三档由轻到重，用于确定 hard 线的下界。
#
# noise 档用**相对幅度**（乘背景中位数），不是绝对值：绝对注入会同时抬高
# median，而相对噪声 = sigma/median 的分母跟着涨，结果增益远达不到预期。
# 实测：NGC6888 背景中位 0.0279，绝对注入 0.30 使 median 涨到 0.1318，
# 相对噪声只从 0.342 升到 0.530（阈值 1.60 够不到）；改用相对 0.30×
# 中位后增益才显著。
DEGRADED_CONTROLS = {
    "noise_light": ("noise", 0.30),
    "noise_heavy": ("noise", 0.60),
    "saturation_light": ("saturation", 1.15),
    "saturation_heavy": ("saturation", 1.45),
    "subject_compress_light": ("compress", 0.05),
    "subject_compress_heavy": ("compress", 0.12),
}


def measure_all(candidate, reference, subject_mask=None):
    """在给定配对上测量全部 5 个度量（不做阈值判定，只取值）。"""
    candidate = _rgb(_downsample(candidate))
    reference = _rgb(_downsample(reference))
    if candidate.shape != reference.shape:
        return None
    background = _background_mask(candidate)
    signal = _resolve_signal_mask(candidate, subject_mask, background)
    if int(np.count_nonzero(background)) < fg.MIN_BACKGROUND_PIXELS:
        return None

    candidate_luma = _luma(candidate)
    reference_luma = _luma(reference)
    candidate_bg = candidate_luma[background]
    reference_bg = reference_luma[background]
    candidate_relative = _mad_sigma(candidate_bg) / max(
        float(np.median(candidate_bg)), 1e-9)
    reference_relative = _mad_sigma(reference_bg) / max(
        float(np.median(reference_bg)), 1e-9)

    chroma = _chroma_visibility_metrics(
        candidate, reference, background_mask=background, signal_mask=signal)

    return {
        "background_sigma": _mad_sigma(candidate_bg),
        "reference_background_sigma": _mad_sigma(reference_bg),
        "background_relative_noise_gain": min(
            candidate_relative / max(reference_relative, 1e-9),
            fg.HARD_CEILING_RELATIVE_NOISE),
        "hue_ratio_drift_p95": _hue_ratio_drift_p95(
            candidate, reference, background_mask=background),
        "subject_chroma_snr_retention": chroma["subject_chroma_snr_retention"],
        "background_chroma_noise_gain": chroma["background_chroma_noise_gain"],
        "chroma_available": chroma["chroma_measurement_available"],
        "chroma_status": chroma["chroma_measurement_status"],
        "background_px": int(np.count_nonzero(background)),
        "signal_px": int(np.count_nonzero(signal)),
        "shape": list(candidate.shape),
    }


def degrade(candidate, reference, kind, amount, seed=0):
    """构造劣化对照：注入已知缺陷后返回候选图。

    ``kind == "noise"`` 时amount 是**相对背景中位数的比例**（见
    DEGRADED_CONTROLS 注释：绝对注入会被分母median 抵消）。
    """
    candidate = _rgb(_downsample(candidate))
    if kind == "noise":
        rng = np.random.default_rng(seed)
        background = _background_mask(candidate)
        values = _luma(candidate)[background]
        base = float(np.median(values)) if values.size else 0.0
        return np.clip(candidate + rng.normal(0.0, amount * base, candidate.shape),
                       0.0, 1.0).astype(np.float32)
    if kind == "saturation":
        gray = candidate.mean(axis=2, keepdims=True)
        return np.clip(gray + (candidate - gray) * amount, 0.0, 1.0
                       ).astype(np.float32)
    if kind == "compress":
        # 只压主体的对比度（保留背景），模拟「主体被压平看不见」
        background = _background_mask(candidate)
        signal = _resolve_signal_mask(candidate, None, background)
        if int(signal.sum()) == 0:
            return candidate
        out = candidate.copy()
        out[signal] = candidate[signal] * (1.0 - amount)
        return np.clip(out, 0.0, 1.0).astype(np.float32)
    raise ValueError(f"unknown degradation kind: {kind}")


def load_pair(work_dir):
    """从管线 work 目录取 (拉伸结果, 参考图)。

    正常路径是 05_display_reference.tif（由 render_protected_stretch 以固定
    pi_mtf 曲线渲染的 display 参考）。但当拉伸被拒并回退时，管线不落盘该文件
    —— 此时保真度门禁本身也被跳过（跨域不可比），标定器需要用回退产物
    05_rejected_recovery.tif 作为参考，否则该母版会被整体丢弃。
    """
    work = Path(work_dir)
    candidate_path = work / "05_stretched_starless.tif"
    reference_candidates = (
        work / "05_display_reference.tif",
        work / "05_rejected_recovery.tif",
    )
    if not candidate_path.exists():
        return None, None, None
    reference_path = next(
        (path for path in reference_candidates if path.exists()), None)
    if reference_path is None:
        return None, None, None
    from fits_io import read_image
    candidate, _ = read_image(str(candidate_path))
    reference, _ = read_image(str(reference_path))
    return candidate, reference, reference_path.name


def collect(calib_root, name):
    from fits_io import read_image
    result_path = Path(calib_root) / name / "result.json"
    if not result_path.exists():
        return None
    result = json.loads(result_path.read_text(encoding="utf-8"))
    diagnostics = result.get("stretch_diagnostics") or {}
    target_type = diagnostics.get("target_type") or (
        (result.get("recognition") or {}).get("target_type"))
    candidate, reference, reference_name = load_pair(
        Path(calib_root) / name / "work")
    if candidate is None:
        return None
    healthy = measure_all(candidate, reference)
    if healthy is None:
        return None
    healthy["target"] = name
    healthy["target_type"] = target_type
    healthy["applied_method"] = diagnostics.get("applied_method")
    healthy["pipeline_status"] = result.get("status")
    healthy["geometry"] = healthy["shape"]
    healthy["reference_artifact"] = reference_name
    # 记录管线自己是否跑了保真度门禁：跳过说明该母版走了回退路径，
    # 其绝对 σ 分布与正常路径不完全可比，需在报告里标注。
    fidelity = diagnostics.get("fidelity_gates") or {}
    healthy["pipeline_fidelity_status"] = fidelity.get("status")
    healthy["pipeline_fidelity_ran"] = bool(fidelity.get("gates"))

    controls = {}
    for label, (kind, amount) in DEGRADED_CONTROLS.items():
        degraded = degrade(candidate, reference, kind, amount, seed=hash(label) % 2**31)
        measured = measure_all(degraded, reference)
        if measured:
            controls[label] = measured
    return {"healthy": healthy, "controls": controls}


def percentile(values, q):
    clean = [v for v in values if v is not None and np.isfinite(v)]
    if not clean:
        return None
    return float(np.percentile(np.asarray(clean, dtype=np.float64), q))


def pick_thresholds(healthy_rows, control_rows):
    """定阈值：hard = max(劣化最轻档, 健康 p95×1.5)；degraded = 健康 p90。

    取二者较大值保证「劣化被抓住」且「健康不误报」。ratio 类门再与专业硬上限
    取 min，落实「配置可更严、不可放松」。
    """
    definitions = {
        "background_sigma": ("BACKGROUND_NOISE_BOUNDED", "higher", None),
        "background_relative_noise_gain": (
            "BACKGROUND_RELATIVE_NOISE_BOUNDED", "higher",
            fg.HARD_CEILING_RELATIVE_NOISE),
        "hue_ratio_drift_p95": ("HUE_PRESERVED", "higher", None),
        "background_chroma_noise_gain": (
            "BACKGROUND_CHROMA_NOISE_BOUNDED", "higher",
            fg.HARD_CEILING_CHROMA_NOISE),
        "subject_chroma_snr_retention": (
            "SUBJECT_CHROMA_VISIBILITY_PRESERVED", "lower",
            fg.HARD_FLOOR_CHROMA_SNR),
    }
    control_prefix = {
        "background_sigma": "noise_light",
        "background_relative_noise_gain": "noise_light",
        "hue_ratio_drift_p95": "saturation_light",
        "background_chroma_noise_gain": "noise_light",
        "subject_chroma_snr_retention": "subject_compress_light",
    }
    result = {}
    for metric, (code, direction, ceiling) in definitions.items():
        healthy = [row.get(metric) for row in healthy_rows]
        degraded = [
            row.get(metric) for row in control_rows
            if row.get("_label") == control_prefix[metric]
        ]
        # 劣化组可能整组为None（例如纯色主体使色度门不可测量），
        # 必须在聚合前滤掉，否则 max()/min() 会在 None 上抛 TypeError。
        degraded = [v for v in degraded if v is not None and np.isfinite(v)]
        p90 = percentile(healthy, 90.0)
        p95 = percentile(healthy, 95.0)
        worst_degraded = None
        if degraded:
            worst_degraded = (max(degraded) if direction == "higher"
                              else min(degraded))
        if p95 is None:
            result[code] = {"degraded": None, "hard": None,
                            "note": "健康组无有效样本"}
            continue
        # 单样本（或全部同值）时 p90 == p95，hard 会被劣化组单点主导。
        # 样本数不足时明确降级为「仅记录」，不产出可升级的阈值。
        sparse = len([v for v in healthy if v is not None
                      and np.isfinite(v)]) < 3
        if direction == "higher":
            hard = max((worst_degraded or 0.0), p95 * 1.5)
            if ceiling is not None:
                hard = min(hard, ceiling)
            degraded_line = p90
            if degraded_line is not None and degraded_line >= hard:
                # 健康组本身已越过 hard ⇒ 标定失败信号，交给人工而非继续调数字
                degraded_line = hard * 0.75
        else:
            hard = min((worst_degraded if worst_degraded is not None
                        else 0.0), p95 * 0.5 if p95 else 0.0)
            if ceiling is not None:
                hard = max(hard, ceiling) if worst_degraded is None else min(
                    max(hard, 0.0), ceiling)
            degraded_line = p90
            if degraded_line is not None and degraded_line <= hard:
                degraded_line = hard * 1.25
        result[code] = {
            "degraded": (round(degraded_line, 6)
                         if degraded_line is not None else None),
            "hard": round(hard, 6),
            "healthy_p90": (round(p90, 6) if p90 is not None else None),
            "healthy_p95": (round(p95, 6) if p95 is not None else None),
            "worst_degraded_control": (round(worst_degraded, 6)
                                       if worst_degraded is not None else None),
            "hard_ceiling": ceiling,
            "sparse_corpus": sparse,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description="保真度门禁阈值标定")
    parser.add_argument("--corpus", nargs="+", required=True,
                        help="母版名目录（位于 --calib-root 下）")
    parser.add_argument("--calib-root", default="/tmp/calib")
    parser.add_argument("--output", default=None)
    parser.add_argument("--json", dest="json_output", default=None)
    args = parser.parse_args()

    healthy_rows, control_rows, skipped = [], [], []
    for name in args.corpus:
        record = collect(args.calib_root, name)
        if record is None:
            skipped.append(name)
            continue
        healthy_rows.append(record["healthy"])
        for label, row in record["controls"].items():
            row = dict(row)
            row["_label"] = label
            row["_target"] = name
            control_rows.append(row)

    if not healthy_rows:
        print(json.dumps({
            "error": "no usable corpus rows",
            "skipped": skipped,
            "hint": "run the pipeline with --keep-all first",
        }, ensure_ascii=False, indent=2))
        return 1

    thresholds = pick_thresholds(healthy_rows, control_rows)
    report = {
        "corpus": [row["target"] for row in healthy_rows],
        "corpus_size": len({row.get("target_type") or row["target"]
                            for row in healthy_rows}),
        "geometry_max_side": fg.GATE_PREVIEW_MAX_SIDE,
        "healthy": healthy_rows,
        "controls": control_rows,
        "thresholds": thresholds,
        "skipped": skipped,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json_output:
        Path(args.json_output).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
