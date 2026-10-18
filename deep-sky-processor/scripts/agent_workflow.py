#!/usr/bin/env python3
"""Stateful CLI bridge that lets an agent direct processing one decision at a time."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_protocol import (
    SCHEMA_VERSION,
    create_review_bundle,
    intent_to_overrides,
    now_iso,
    validate_action,
    validate_review,
    write_json_atomic,
)
from analyze import analyze_image
from fits_io import read_image
from pipeline import ALL_STEPS, run_pipeline
from mask_tools import execute_masked_adjustment
from fits_io import write_image
from runtime_info import runtime_info
from quality_state import (read_quality_state, build_quality_state, collect_quality_gates,
                           persist_quality_state, verify_quality_state, resolve_quality_state)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_state(session_dir):
    path = Path(session_dir) / "session.json"
    if not path.exists():
        raise ValueError(f"session not initialized: {path}")
    state = load_json(path)
    state['artifact_state'] = {str(Path(key).resolve()): value
                               for key, value in state.get('artifact_state', {}).items()}
    for key in ('input_artifact', 'current_artifact', 'accepted_artifact'):
        if state.get(key):
            state[key] = str(Path(state[key]).resolve())
    records = state['artifact_state']
    if state.get('input_artifact'):
        records.setdefault(state['input_artifact'], {})
    for event in state.get('history', []):
        result = event.get('result') or {}
        for candidate in result.get('candidates', [result]):
            if not candidate.get('artifact'):
                continue
            artifact = str(Path(candidate['artifact']).resolve())
            config = (candidate.get('pipeline_result') or {}).get('effective_config') or {}
            record = records.setdefault(artifact, {})
            if isinstance(config.get('output_is_linear'), bool):
                record.setdefault('is_linear', config['output_is_linear'])
                record.setdefault('stage_source', 'pipeline')
    return state, path


def artifact_quality(state, artifact, visiting=None):
    """Recover old branch evidence without assigning it to unrelated variants."""
    artifact = str(Path(artifact).resolve())
    record = state.get('artifact_state', {}).get(artifact)
    if record is None or not Path(artifact).is_file():
        raise ValueError('Artifact must exist and belong to the current session')
    quality = record.get('quality_state')
    saved_quality = read_quality_state(artifact)
    if quality and saved_quality:
        issues = {issue['id']: issue for issue in quality['issues']}
        issues.update({issue['id']: issue for issue in saved_quality['issues']})
        quality = {**quality, 'issues': list(issues.values()),
                   'status': 'review_required' if issues else 'success'}
    quality = quality or saved_quality
    if quality:
        verify_quality_state(artifact, quality)
        record['quality_state'] = quality
        return quality
    visiting = set(visiting or ())
    if artifact in visiting:
        raise ValueError('Cyclic artifact history')
    visiting.add(artifact)
    for event in reversed(state.get('history', [])):
        result = event.get('result') or {}
        candidates = result.get('candidates', [result])
        for candidate in candidates:
            if not candidate.get('artifact') or str(Path(candidate['artifact']).resolve()) != artifact:
                continue
            pipeline = candidate.get('pipeline_result') or {}
            review = candidate.get('review') or {}
            if review.get('report_path') and Path(review['report_path']).is_file():
                review = load_json(review['report_path'])
            parent = pipeline.get('input') or review.get('source', {}).get('before')
            parent = str(Path(parent).resolve()) if parent else None
            parent_quality = artifact_quality(state, parent, visiting) if parent in state.get('artifact_state', {}) else None
            historical_quality = pipeline.get('quality_state') or review.get('quality_state')
            if historical_quality:
                verify_quality_state(artifact, historical_quality)
            gates = collect_quality_gates(pipeline) + collect_quality_gates(review)
            if not historical_quality:
                gates.append({'code': 'LEGACY_ARTIFACT_IDENTITY_UNKNOWN', 'status': 'warning'})
            if not any(key in payload for payload in (pipeline, review)
                       for key in ('quality_gates', 'star_artifact_gates')):
                gates.append({'code': 'LEGACY_QUALITY_UNKNOWN', 'status': 'warning'})
            # 从历史 pipeline_result 回读配置指纹：管线侧已写入（v0.1.32），
            # 缺失（历史产物）时传 None，build_quality_state 按「父无指纹 ⇒
            # 保守继承」处理，不会误丢已有 finding。
            quality = build_quality_state(
                artifact, parent, historical_quality or parent_quality,
                event.get('action', {}).get('step', event['operation']), gates,
                config_fingerprint=pipeline.get('config_fingerprint'))
            record['quality_state'] = quality
            return quality
    gates = [] if artifact == state['input_artifact'] else [{'code': 'LEGACY_QUALITY_UNKNOWN', 'status': 'warning'}]
    quality = build_quality_state(artifact, gates=gates)
    record['quality_state'] = quality
    return quality


def initialize_session(input_path, session_dir, target_type=None, target_name=None,
                       aesthetic_goal=None, plate_solve=False,
                       solve_field_path=None, catalog=None):
    session_dir = Path(session_dir).resolve()
    session_dir.mkdir(parents=True, exist_ok=True)
    analysis = analyze_image(input_path)
    analysis_path = session_dir / "analysis.json"
    write_json_atomic(analysis, analysis_path)
    inferred = analysis.get("target_type_hint", {})
    astrometry = None
    if plate_solve:
        from plate_solve import solve_image
        astrometry = solve_image(
            input_path,
            session_dir / "astrometry",
            solve_field_path=solve_field_path,
            catalog=catalog,
        )
    state = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_dir.name,
        "stretch_policy_version": 3,
        "runtime": runtime_info(__file__),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "input_artifact": str(Path(input_path).resolve()),
        "current_artifact": str(Path(input_path).resolve()),
        "accepted_artifact": None,
        "artifact_state": {str(Path(input_path).resolve()): {
            "is_linear": bool(analysis.get("is_linear", False)), "ai_denoised": False,
            "stage_source": analysis.get("stage_source", "format"),
        }},
        "target": {
            "type": target_type or inferred.get("target_type"),
            "name": target_name,
        },
        "aesthetic_goal": aesthetic_goal or {
            "style": "natural_enhanced",
            "background": "deep_but_not_crushed",
            "saturation": "moderate",
            "stars": "subordinate",
            "detail": "structural_not_crispy",
        },
        "constraints": {
            "no_generated_detail": True,
            "preserve_star_color": True,
            "max_style_strength": 1.2,
        },
        "analysis_report": str(analysis_path),
        "capture_metadata": analysis.get("capture_metadata", {}),
        "physical_priors": analysis.get("physical_priors", {}),
        "astrometry": astrometry,
        "history": [],
        "pending_review": None,
    }
    state['artifact_state'][state['input_artifact']]['quality_state'] = (read_quality_state(input_path)
        or build_quality_state(input_path))
    state_path = session_dir / "session.json"
    write_json_atomic(state, state_path)
    return state


# 这些 run_pipeline 形参在函数自身已有非 None 的默认值，必须「键存在才转发」。
# 键缺失时若传 None 会覆盖默认值并触发校验失败（`deepsnr=None` →
# "DeepSNR mode must be auto, always or never"）。逐个对照 run_pipeline 签名核实。
_OPTIONAL_PIPELINE_FIELDS = (
    "external_starless_domain", "external_starless_midtones",
    "external_detail_strength", "starnet_stride", "starnet_domain",
    "starnet_timeout", "starnet_recommendation", "export_starnet_payload",
    "export_starnet_payload_domain", "star_blend_mode", "halo_guard",
    "auto_crop_target", "auto_crop_padding", "crop", "stacking_crop",
    "tile_size", "low_memory", "low_memory_threshold_mpix", "quality_policy",
    "plate_solve", "solve_field_path", "plate_solve_timeout", "catalog",
    "recognition_workflow_dir", "reference_image", "reference_auto_search",
    "reference_color_match", "reference_color_only", "reference_strength",
    "reference_match_orientation", "stellar_repair", "stellar_repair_mode",
    "stellar_repair_strength", "palette", "linear_deconv", "deconv_iterations",
    "deepsnr", "deepsnr_path", "deepsnr_timeout", "deepsnr_strength",
    "result_json", "input_is_linear", "override_params",
)


def _optional_pipeline_kwargs(action):
    """只在 action **显式给出**时才转发的 run_pipeline 形参。

    这些形参在 `run_pipeline` 里有非 None 的默认值（`deepsnr='auto'`、
    `starnet_domain='mtf'`、`quality_policy='advisory'`、`stacking_crop='none'` 等）。
    若无脑写 `action.get(name)`，键缺失时会传 None 覆盖掉那些默认值并触发
    校验失败（例如 `deepsnr=None` → "DeepSNR mode must be auto, always or never"）。

    判据是「键存在**且值非 None**」—— action 里显式写 `"deepsnr": null` 同样
    会覆盖默认值并失败，与键缺失等价。

    **键缺失 = 用 run_pipeline 自己的默认值**，这样「补转发」本身永不改变行为。
    """
    return {name: action[name] for name in _OPTIONAL_PIPELINE_FIELDS
            if action.get(name) is not None}


def _run_candidate(state, session_dir, action, suffix):
    step = action["step"]
    if step not in ALL_STEPS:
        raise ValueError(f"unsupported step: {step}")
    before = state["current_artifact"]
    input_quality = artifact_quality(state, before)
    run_dir = Path(session_dir).resolve() / "runs" / suffix
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "output.tif"
    overrides = intent_to_overrides(action.get("intent"))
    if state.get("stretch_policy_version", 1) < 3:
        overrides.pop("stretch_subject_midtones", None)
    overrides.update(action.get("params") or {})
    overrides.setdefault("stretch_policy_version", state.get("stretch_policy_version", 1))
    input_state = state.get("artifact_state", {}).get(before, {})
    known_stage = (before != state['input_artifact']
                   or input_state.get('stage_source') in ('sidecar', 'pipeline')
                   or Path(before).suffix.lower() in ('.fit', '.fits', '.fts', '.xisf'))
    if (known_stage and 'input_is_linear' in overrides and 'is_linear' in input_state
            and overrides['input_is_linear'] != input_state['is_linear']):
        raise ValueError('Input stage conflicts with recorded Agent artifact state')
    analysis = analyze_image(before, input_is_linear=overrides.get('input_is_linear', input_state.get('is_linear')),
                             capture_metadata=state.get('capture_metadata'))
    write_json_atomic(analysis, run_dir / 'analysis.json')
    if "is_linear" in input_state:
        overrides.setdefault("input_is_linear", input_state["is_linear"])
    overrides.setdefault("skip_final_denoise", input_state.get("ai_denoised", False))
    local_center = None
    local_radius = None
    region = action.get("region")
    object_name = action.get("object_name")
    if object_name and not region:
        objects = (state.get("astrometry") or {}).get("objects", [])
        matched = next(
            (
                item for item in objects
                if str(item.get("name", "")).strip().lower()
                == str(object_name).strip().lower()
            ),
            None,
        )
        if not matched:
            raise ValueError(f"catalog object not found in solved field: {object_name}")
        region = {
            "center": matched["normalized_center"],
            "radius": float(action.get("object_radius", 0.08)),
            "source": "astrometry_catalog",
            "object_name": object_name,
        }
    if region:
        image, _ = read_image(before)
        height, width = image.shape[:2]
        if "bbox" in region:
            x, y, box_width, box_height = [float(value) for value in region["bbox"]]
            center_x = x + box_width / 2.0
            center_y = y + box_height / 2.0
            radius_fraction = max(box_width, box_height) / 2.0
        else:
            center_x, center_y = [
                float(value) for value in region.get("center", [0.5, 0.5])
            ]
            radius_fraction = float(region.get("radius", 0.2))
        if not (
            0 <= center_x <= 1
            and 0 <= center_y <= 1
            and 0 < radius_fraction <= 1
        ):
            raise ValueError("region coordinates must be normalized to 0..1")
        local_center = f"{round(center_x * (width - 1))},{round(center_y * (height - 1))}"
        local_radius = max(2, round(radius_fraction * min(height, width)))
    pipeline_result = run_pipeline(
        before,
        str(output),
        steps=step,
        preset=action.get("preset", "adaptive"),
        keep_all=True,
        work_dir=str(run_dir / "artifacts"),
        override_params=overrides,
        input_quality_state=input_quality,
        target_type=state.get("target", {}).get("type"),
        target_name=state.get("target", {}).get("name"),
        color_mode=action.get("color_mode", "auto"),
        style=action.get("style", "auto"),
        style_strength=min(
            float(action.get("style_strength", 1.0)),
            float(state.get("constraints", {}).get("max_style_strength", 1.2)),
        ),
        local_center=local_center,
        local_radius=local_radius,
        local_strength=float(action.get("local_strength", 0.30)),
        stretch_method=action.get("stretch_method", "auto"),
        analysis_report=analysis,
        capture_metadata=state.get('capture_metadata'),
        star_layers=input_state.get('star_layers'),
        subject_mask=action.get('subject_mask'),
        external_starless=action.get('external_starless'),
        # 域 / midtones / starnet / 参考图 / 降噪 等 40+ 个原本未被转发的形参
        # 统一走 _optional_pipeline_kwargs：键存在且值非 None 才传，缺失时
        # 保留 run_pipeline 自己的默认值。external_starless_domain 漏转发的
        # 后果尤其隐蔽：默认 'linear' 会把 MTF 域无星层**按线性读入** ——
        # 域错、色比错，且不产生任何报错。
        **_optional_pipeline_kwargs(action),
        auto_low_memory=action.get('auto_low_memory', True),
        auto_crop_edges=action.get('auto_crop_edges', True),
        background_neutralize_lock=action.get('background_neutralize_lock', True),
    )
    config = pipeline_result["effective_config"]
    state.setdefault("artifact_state", {})[str(output)] = {
        "is_linear": config["output_is_linear"],
        "stage_source": "pipeline",
        "ai_denoised": config["skip_final_denoise"],
        "star_layers": pipeline_result.get('star_layers'),
        "quality_state": pipeline_result["quality_state"],
    }
    review_before, review_after = before, output
    review_input_linear, review_output_linear = config['input_is_linear'], config['output_is_linear']
    if step == 'star_process' and pipeline_result.get('star_layers'):
        layers = pipeline_result['star_layers']
        review_before, review_after = layers['stars'], layers['processed_stars']
        review_input_linear, review_output_linear = True, False
    review = create_review_bundle(
        review_before,
        review_after,
        run_dir / "review",
        context={
            "step": step,
            "image_role": 'stars' if step == 'star_process' and pipeline_result.get('star_layers') else 'image',
            "steps": [step],
            "target_type": state.get("target", {}).get("type"),
            "effective_params": config['params'],
            "runtime": pipeline_result["runtime"],
            "execution": pipeline_result["execution"],
            "input_is_linear": review_input_linear,
            "output_is_linear": review_output_linear,
            "pre_denoise": pipeline_result.get("pre_denoise"),
            "stretch_diagnostics": pipeline_result.get("stretch_diagnostics"),
            "linear_star_source": (input_state.get('star_layers') or {}).get('source') if step == 'stretch' else None,
            "region": region,
            "aesthetic_goal": state.get("aesthetic_goal"),
        },
    )
    if pipeline_result['status'] != 'success':
        review["status"] = pipeline_result['status'] if review['status'] == 'success' else review['status']
        review['pipeline_status'] = pipeline_result['status']
        review['pipeline_quality_gates'] = pipeline_result['quality_gates']
        write_json_atomic({k: v for k, v in review.items() if k != "report_path"}, review["report_path"])
    quality = build_quality_state(output, before, pipeline_result['quality_state'], step,
                                  collect_quality_gates(review),
                                  config_fingerprint=pipeline_result.get('config_fingerprint'))
    state['artifact_state'][str(output)]['quality_state'] = quality
    review['quality_state'] = quality
    pipeline_result['quality_state'] = quality
    if quality['issues']:
        review['status'] = pipeline_result['status'] = 'review_required'
    manifest_path = pipeline_result['outputs'].get('manifest')
    if manifest_path:
        manifest = load_json(manifest_path)
        manifest.update(quality_state=quality, status=pipeline_result['status'])
        write_json_atomic(manifest, manifest_path)
    persist_quality_state(output, quality)
    write_json_atomic({k: v for k, v in review.items() if k != 'report_path'}, review['report_path'])
    return {
        "id": action.get("id", suffix),
        "artifact": str(output),
        "pipeline_result": pipeline_result,
        "review": review,
    }


def apply_action(session_dir, action):
    action = validate_action(action)
    state, state_path = load_state(session_dir)
    operation = action["operation"]
    event = {
        "timestamp": now_iso(),
        "operation": operation,
        "action": action,
    }

    if operation == "run_step":
        result = _run_candidate(
            state, session_dir, action, f"{len(state['history']) + 1:03d}_{action['step']}"
        )
        state["current_artifact"] = result["artifact"]
        state["pending_review"] = result["review"]["report_path"]
        event["result"] = result
    elif operation == "masked_adjustment":
        before = state["current_artifact"]
        run_dir = (
            Path(session_dir).resolve() / "runs"
            / f"{len(state['history']) + 1:03d}_masked_adjustment"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
        parent_quality = artifact_quality(state, before)
        image, _ = read_image(before)
        result_image, mask, mask_report = execute_masked_adjustment(
            image,
            action["mask"],
            action["adjustment"],
        )
        output = run_dir / "output.tif"
        mask_output = run_dir / "mask.tif"
        mask_preview = run_dir / "mask.jpg"
        mask_report_path = run_dir / "mask_report.json"
        write_image(result_image, output)
        source_state = state.get('artifact_state', {}).get(before, {})
        write_json_atomic({'capture_metadata': state.get('capture_metadata', {}),
                           'is_linear': source_state.get('is_linear', False)}, str(output) + '.meta.json')
        write_image(mask, mask_output)
        write_image(mask, mask_preview)
        write_json_atomic(mask_report, mask_report_path)
        review = create_review_bundle(
            before,
            output,
            run_dir / "review",
            context={
                "operation": operation,
                "target_type": state.get("target", {}).get("type"),
                "mask_spec": action["mask"],
                "mask_report": mask_report,
                "adjustment": action["adjustment"],
                "input_is_linear": source_state.get('is_linear', False),
                "output_is_linear": source_state.get('is_linear', False),
            },
        )
        result = {
            "artifact": str(output),
            "mask_artifact": str(mask_output),
            "mask_preview": str(mask_preview),
            "mask_report": str(mask_report_path),
            "review": review,
        }
        state.setdefault("artifact_state", {})[str(output)] = dict(
            state.get("artifact_state", {}).get(before, {})
        )
        quality = build_quality_state(output, before, parent_quality, operation,
                                      collect_quality_gates(review),
                                      # 掩码调整不经管线、**不改配置** ⇒ 沿用父指纹。
                                      # 此路径无 pipeline_result可读，故取自父状态。
                                      config_fingerprint=parent_quality.get('config_fingerprint'))
        state['artifact_state'][str(output)]['quality_state'] = quality
        review['quality_state'] = quality
        if quality['issues']:
            review['status'] = 'review_required'
        persist_quality_state(output, quality)
        write_json_atomic({k: v for k, v in review.items() if k != 'report_path'}, review['report_path'])
        layers = state['artifact_state'][str(output)].get('star_layers')
        if layers:
            state['artifact_state'][str(output)]['star_layers'] = {**layers, 'starless': str(output)}
        state["current_artifact"] = str(output)
        state["pending_review"] = review["report_path"]
        event["result"] = result
    elif operation == "create_variants":
        candidates = []
        common = {
            key: value for key, value in action.items()
            if key not in ("operation", "variants")
        }
        for variant in action["variants"]:
            merged = dict(common)
            merged.update(variant)
            merged["step"] = merged.get("step") or action.get("step")
            validate_action({"operation": "run_step", **merged})
            candidates.append(
                _run_candidate(
                    state,
                    session_dir,
                    merged,
                    f"{len(state['history']) + 1:03d}_variant_{variant['id']}",
                )
            )
        event["result"] = {"candidates": candidates}
        state["pending_review"] = [
            candidate["review"]["report_path"] for candidate in candidates
        ]
    elif operation == "accept":
        artifact = str(Path(action.get("artifact") or state["current_artifact"]).resolve())
        quality = artifact_quality(state, artifact)
        if action.get('review'):
            quality = resolve_quality_state(artifact, quality, action['review'])
        if quality['issues']:
            raise ValueError('Cannot accept artifact with unresolved quality issues')
        # degraded **不阻断 accept** —— 它是「可见、可审、可接受」的中间态
        # （见 failure_policy 模块文档）。判据是 issues 非空，degraded 不
        # 产生 issue，因此这里天然放行；显式记录以便调用方看到。
        if quality.get('status') == 'degraded':
            event.setdefault('notes', []).append(
                'accepted a degraded artifact: %s'
                % ', '.join(quality.get('degraded_stages') or ['unknown']))
        state['artifact_state'][artifact]['quality_state'] = quality
        if artifact != state['input_artifact']:
            persist_quality_state(artifact, quality)
        state["accepted_artifact"] = artifact
        state["current_artifact"] = artifact
        state["pending_review"] = None
        event["result"] = {"accepted_artifact": artifact}
    elif operation == "rollback":
        artifact = str(Path(action.get("artifact") or state["input_artifact"]).resolve())
        quality = artifact_quality(state, artifact)
        state["current_artifact"] = artifact
        state["pending_review"] = quality if quality['issues'] else None
        event["result"] = {"current_artifact": artifact}
    else:
        artifact = str(Path(action.get('artifact') or state['current_artifact']).resolve())
        previous = artifact_quality(state, artifact)
        reason = action.get('reason', 'agent requested human review')
        quality = build_quality_state(
            artifact, previous.get('parent_artifact'), previous, 'agent_review',
            [{'code': 'AGENT_REVIEW_PENDING', 'status': 'warning',
              'evidence': {'reason': reason}}],
            # 转人工复核不改配置 ⇒ 沿用上一轮的指纹，避免被误判为「配置已变」
            # 而把既有 findings 清空。
            config_fingerprint=previous.get('config_fingerprint'))
        state['artifact_state'][artifact]['quality_state'] = quality
        if artifact != state['input_artifact']:
            persist_quality_state(artifact, quality)
        state["pending_review"] = {"reason": reason, "artifact": artifact,
                                   'quality_state': quality}
        event["result"] = state["pending_review"]

    state["history"].append(event)
    state["updated_at"] = now_iso()
    write_json_atomic(state, state_path)
    return event


def apply_review(session_dir, review):
    review = validate_review(review)
    if review.get('artifact_sha256'):
        state, _ = load_state(session_dir)
        artifact = review.get('artifact') or state['current_artifact']
        if artifact_quality(state, artifact)['artifact_sha256'] != review['artifact_sha256']:
            raise ValueError('Review SHA-256 does not match the selected artifact')
    if review['verdict'] != 'accept' and any(action['operation'] == 'accept' for action in review.get('actions', [])):
        raise ValueError('Only an accept verdict may authorize acceptance')
    events = []
    if review["verdict"] == "accept" and not review.get("actions"):
        events.append(apply_action(session_dir, {"operation": "accept", 'artifact': review.get('artifact'),
                                                'review': review if any(key in review for key in ('artifact_sha256', 'resolutions')) else None}))
    elif review['verdict'] == 'review_required' and not review.get('actions'):
        events.append(apply_action(session_dir, {'operation': 'request_human_review',
                          'reason': review.get('reason', 'Structured review requires further evidence'),
                          'artifact': review.get('artifact'), 'review': review}))
    elif review['verdict'] == 'rollback' and not review.get('actions'):
        events.append(apply_action(session_dir, {'operation': 'rollback', 'artifact': review.get('artifact')}))
    else:
        for action in review.get("actions", []):
            if action['operation'] == 'accept' and review.get('resolutions'):
                action = {**action, 'review': review}
            events.append(apply_action(session_dir, action))
    return {"schema_version": SCHEMA_VERSION, "events": events}


def main():
    parser = argparse.ArgumentParser(description="Agent-in-the-loop deep-sky workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("input")
    init.add_argument("session_dir")
    init.add_argument("--target-type")
    init.add_argument("--target-name")
    init.add_argument("--aesthetic-goal", help="JSON object")
    init.add_argument("--plate-solve", action="store_true")
    init.add_argument("--solve-field-path")
    init.add_argument("--catalog-json")

    apply = sub.add_parser("apply")
    apply.add_argument("session_dir")
    apply.add_argument("action_json")

    review = sub.add_parser("review")
    review.add_argument("session_dir")
    review.add_argument("review_json")

    show = sub.add_parser("show")
    show.add_argument("session_dir")

    args = parser.parse_args()
    try:
        if args.command == "init":
            goal = json.loads(args.aesthetic_goal) if args.aesthetic_goal else None
            catalog = load_json(args.catalog_json) if args.catalog_json else None
            payload = initialize_session(
                args.input,
                args.session_dir,
                target_type=args.target_type,
                target_name=args.target_name,
                aesthetic_goal=goal,
                plate_solve=args.plate_solve,
                solve_field_path=args.solve_field_path,
                catalog=catalog,
            )
        elif args.command == "apply":
            payload = apply_action(args.session_dir, load_json(args.action_json))
        elif args.command == "review":
            payload = apply_review(args.session_dir, load_json(args.review_json))
        else:
            payload, _ = load_state(args.session_dir)
    except Exception as exc:
        print(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
