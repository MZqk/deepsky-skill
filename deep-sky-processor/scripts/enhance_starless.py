#!/usr/bin/env python3
"""Enhance stretched starless images and recompose an independent stars layer."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from fits_io import read_image, write_image
from starless_diagnostics import (
    diagnose_starless,
    ensure_rgb_float32,
    validate_stretched_input_meta,
)
from starless_masks import build_starless_masks
from starless_multiscale import enhance_starless_candidate
from starless_profiles import (
    CandidateLevel,
    build_candidate_params,
    get_target_profile,
    validate_starless_target,
)
from starless_quality import downgrade_params, evaluate_candidate
from stellar_recompose import (
    process_stars_layer,
    recompose_additive,
    validate_stars_layer,
)


def _write_review_image(image, path):
    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 2:
        low, high = np.percentile(source, (1, 99))
        source = np.clip((source - low) / max(high - low, 1e-8), 0, 1)
    write_image(source, str(path))


def run_starless_enhancement(
    starless_path,
    stars_path,
    output_dir,
    target_type,
    target_name=None,
    write_jpg=True,
    report_path=None,
):
    normalized = validate_starless_target(target_type, target_name)
    profile = get_target_profile(normalized)
    starless, starless_meta = read_image(starless_path)
    stars, stars_meta = read_image(stars_path)
    validate_stretched_input_meta(starless_meta)
    validate_stretched_input_meta(stars_meta)
    starless = ensure_rgb_float32(starless)
    stars = ensure_rgb_float32(stars)
    if starless.shape != stars.shape:
        raise ValueError("starless and stars inputs must have identical shapes")
    stars_validation = validate_stars_layer(stars)

    diagnostics, artifact_mask = diagnose_starless(starless)
    masks, mask_report = build_starless_masks(
        starless, diagnostics, artifact_mask, profile)
    output = Path(output_dir)
    review = output / "review"
    review.mkdir(parents=True, exist_ok=True)
    for name, mask in masks.items():
        write_image(mask, str(review / f"{name}_mask.tif"))

    baseline_final = recompose_additive(starless, stars)
    candidates = {}
    shared_artifacts = None
    overall = "success"
    for level in CandidateLevel:
        params = build_candidate_params(profile, level)
        retry_count = 0
        candidate, enhance_report, artifacts = enhance_starless_candidate(
            starless, masks, params)
        processed_stars, stars_report = process_stars_layer(stars, params)
        final = recompose_additive(candidate, processed_stars)
        quality = evaluate_candidate(
            starless, candidate, baseline_final, final, stars,
            masks, level, profile.continuity_required)
        if quality["status"] == "failed":
            retry_count = 1
            params = downgrade_params(params, quality["failed_codes"])
            candidate, enhance_report, artifacts = enhance_starless_candidate(
                starless, masks, params)
            processed_stars, stars_report = process_stars_layer(stars, params)
            final = recompose_additive(candidate, processed_stars)
            quality = evaluate_candidate(
                starless, candidate, baseline_final, final, stars,
                masks, level, profile.continuity_required)

        if shared_artifacts is None:
            shared_artifacts = artifacts
        status = quality["status"]
        outputs = []
        if status != "failed" or level != CandidateLevel.HIGH:
            starless_out = output / f"starless_{level.value}.tif"
            final_tif = output / f"final_{level.value}.tif"
            write_image(candidate, str(starless_out))
            write_image(final, str(final_tif))
            outputs.extend([str(starless_out), str(final_tif)])
            if write_jpg:
                final_jpg = output / f"final_{level.value}.jpg"
                write_image(final, str(final_jpg))
                outputs.append(str(final_jpg))
            _write_review_image(
                np.abs(candidate - starless),
                review / f"{level.value}_difference.tif",
            )
        if status == "failed":
            overall = "review_required"
        candidates[level.value] = {
            "status": status,
            "retry_count": retry_count,
            "params": enhance_report["params"],
            "enhancement": enhance_report,
            "stars": stars_report,
            "quality": quality,
            "outputs": outputs,
        }

    for name, image in (shared_artifacts or {}).items():
        _write_review_image(image, review / f"{name}.tif")
    report = {
        "schema_version": "1.0",
        "status": overall,
        "target_type": normalized,
        "target_name": target_name,
        "inputs": {
            "starless": str(starless_path),
            "stars": str(stars_path),
            "shape": list(starless.shape),
            "stars_validation": stars_validation,
        },
        "diagnostics": diagnostics,
        "masks": mask_report,
        "candidates": candidates,
    }
    destination = Path(report_path) if report_path else output / "report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Enhance stretched starless deep-sky images and recompose stars")
    parser.add_argument("starless")
    parser.add_argument("stars")
    parser.add_argument("output_dir")
    parser.add_argument("--target-type", required=True)
    parser.add_argument("--target-name")
    parser.add_argument("--no-jpg", action="store_true")
    parser.add_argument("--report")
    args = parser.parse_args()
    try:
        report = run_starless_enhancement(
            args.starless,
            args.stars,
            args.output_dir,
            args.target_type,
            target_name=args.target_name,
            write_jpg=not args.no_jpg,
            report_path=args.report,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({
        "status": report["status"],
        "report": args.report or str(Path(args.output_dir) / "report.json"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
