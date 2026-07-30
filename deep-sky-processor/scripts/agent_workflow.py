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


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_state(session_dir):
    path = Path(session_dir) / "session.json"
    if not path.exists():
        raise ValueError(f"session not initialized: {path}")
    return load_json(path), path


def initialize_session(input_path, session_dir, target_type=None, target_name=None,
                       aesthetic_goal=None, plate_solve=False,
                       solve_field_path=None, catalog=None):
    session_dir = Path(session_dir)
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
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "input_artifact": str(Path(input_path).resolve()),
        "current_artifact": str(Path(input_path).resolve()),
        "accepted_artifact": None,
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
    state_path = session_dir / "session.json"
    write_json_atomic(state, state_path)
    return state


def _run_candidate(state, session_dir, action, suffix):
    step = action["step"]
    if step not in ALL_STEPS:
        raise ValueError(f"unsupported step: {step}")
    before = state["current_artifact"]
    run_dir = Path(session_dir) / "runs" / suffix
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "output.tif"
    overrides = intent_to_overrides(action.get("intent"))
    overrides.update(action.get("params") or {})
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
        preset=action.get("preset", "medium"),
        keep_all=True,
        work_dir=str(run_dir / "artifacts"),
        override_params=overrides,
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
    )
    review = create_review_bundle(
        before,
        output,
        run_dir / "review",
        context={
            "step": step,
            "steps": [step],
            "target_type": state.get("target", {}).get("type"),
            "effective_params": overrides,
            "region": region,
            "aesthetic_goal": state.get("aesthetic_goal"),
        },
    )
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
            Path(session_dir) / "runs"
            / f"{len(state['history']) + 1:03d}_masked_adjustment"
        )
        run_dir.mkdir(parents=True, exist_ok=True)
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
            },
        )
        result = {
            "artifact": str(output),
            "mask_artifact": str(mask_output),
            "mask_preview": str(mask_preview),
            "mask_report": str(mask_report_path),
            "review": review,
        }
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
        artifact = action.get("artifact") or state["current_artifact"]
        state["accepted_artifact"] = artifact
        state["current_artifact"] = artifact
        state["pending_review"] = None
        event["result"] = {"accepted_artifact": artifact}
    elif operation == "rollback":
        artifact = action.get("artifact") or state["input_artifact"]
        state["current_artifact"] = artifact
        state["pending_review"] = None
        event["result"] = {"current_artifact": artifact}
    else:
        state["pending_review"] = {
            "reason": action.get("reason", "agent requested human review"),
            "artifact": state["current_artifact"],
        }
        event["result"] = state["pending_review"]

    state["history"].append(event)
    state["updated_at"] = now_iso()
    write_json_atomic(state, state_path)
    return event


def apply_review(session_dir, review):
    review = validate_review(review)
    events = []
    if review["verdict"] == "accept" and not review.get("actions"):
        events.append(apply_action(session_dir, {"operation": "accept"}))
    else:
        for action in review.get("actions", []):
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
