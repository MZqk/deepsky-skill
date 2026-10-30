#!/usr/bin/env python3
"""Quantitative diagnostic metrics probes for starun-siril.

This module never alters image pixels.  It performs read-only statistical
analysis, histogram evaluation, star profile extraction, and delta comparisons
between parent and candidate artifacts to produce structured metric reports
and evidence-grounded gate evaluations.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence

METRIC_REPORT_SCHEMA = "starun-siril.metric-report.v2"


def compare_separation_channels(parent, candidate) -> dict[str, Any]:
    """Measure aligned linear planes in identical units; never repair pixels.

    The parent-only percentile mask is a heuristic, not a verified nebula ROI.
    Ratios and loss flags require target/stellar review before a quality verdict.
    """
    import numpy as np

    if (parent.shape != candidate.shape or parent.ndim != 3
        or parent.shape[0] not in (1, 3)
        or not np.isfinite(parent).all() or not np.isfinite(candidate).all()):
        return {"status": "uncertain", "reason": "geometry_or_nonfinite_pixels"}
    lum = parent[0] if parent.shape[0] == 1 else np.tensordot(
        [0.2126, 0.7152, 0.0722], parent, axes=(0, 0))
    lo, hi = np.percentile(lum, [55, 99.2])
    mask = (lum > lo) & (lum < hi)
    count = int(mask.sum())
    if count < 100:
        return {"status": "uncertain", "reason": "insufficient_signal", "signal_pixel_count": count}
    amplitudes = [np.asarray([np.percentile(np.maximum(ch - np.percentile(ch, 10), 0)[mask], 95)
                             for ch in image]) for image in (parent, candidate)]
    before, after = amplitudes
    scale = float(before.max())
    if scale <= 0:
        return {"status": "uncertain", "reason": "insufficient_signal", "signal_pixel_count": count}
    rows = []
    for index, (a, b) in enumerate(zip(before, after)):
        present = bool(a / scale >= 0.05)
        zero_parent = float(np.mean(parent[index][mask] <= 0))
        zero_candidate = float(np.mean(candidate[index][mask] <= 0))
        retention = float(b / a) if a > 0 else None
        rows.append({"channel": index, "present_in_parent": present,
                     "signal_amplitude_parent": float(a), "signal_amplitude_candidate": float(b),
                     "signal_retention": retention, "raw_zero_parent": zero_parent,
                     "raw_zero_candidate": zero_candidate,
                     "loss_flag": bool(present and (b < a / 2 or zero_candidate - zero_parent >= 0.25))})
    return {"status": "measured", "measurement_domain": "same_scale_linear_scientific",
            "mask": "parent_luminance_p55_to_p99.2", "signal_pixel_count": count,
            "channels": rows, "requires_visual_review": True}


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 != 0 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _mean_and_std(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean_val = sum(values) / n
    var = sum((x - mean_val) ** 2 for x in values) / max(1, n - 1)
    return mean_val, math.sqrt(var)


def parse_stars_tsv(path: Path) -> dict[str, Any]:
    """Parse star detection table produced by Siril's ``findstar -out=stars.tsv``."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.stat().st_size == 0:
        return {"detected": False, "star_count": 0}

    fwhms: list[float] = []
    roundness_list: list[float] = []

    with resolved.open("r", encoding="utf-8", errors="ignore") as stream:
        sample = stream.read(2048)
        stream.seek(0)
        delimiter = "\t" if "\t" in sample else None  # None splits on any whitespace in reader

        lines = stream.readlines()
        if not lines:
            return {"detected": False, "star_count": 0}

        header_idx = -1
        col_fwhm = -1
        col_fwhmx = -1
        col_fwhmy = -1
        col_roundness = -1

        for i, line in enumerate(lines[:15]):
            clean = line.strip().lower()
            if not clean:
                continue
            if clean.startswith("#"):
                clean = clean.lstrip("#").strip()
            parts = re.split(r"[\t,]+", clean) if "\t" in clean or "," in clean else clean.split()
            found_header = False
            for col_i, part in enumerate(parts):
                if "fwhmx" in part and ("px" in part or "[" not in part):
                    col_fwhmx = col_i
                    found_header = True
                elif "fwhmy" in part and ("px" in part or "[" not in part):
                    col_fwhmy = col_i
                    found_header = True
                elif "fwhm" in part and "[" not in part:
                    col_fwhm = col_i
                    found_header = True
                elif part.startswith("round") or part == "roundness":
                    col_roundness = col_i
            if found_header:
                header_idx = i
                break

        data_lines = lines[header_idx + 1 :] if header_idx != -1 else lines
        for line in data_lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            parts = (
                re.split(r"[\t,]+", line_str)
                if "\t" in line_str or "," in line_str
                else line_str.split()
            )
            try:
                val = None
                if (
                    col_fwhmx != -1
                    and col_fwhmy != -1
                    and col_fwhmx < len(parts)
                    and col_fwhmy < len(parts)
                ):
                    vx = float(parts[col_fwhmx])
                    vy = float(parts[col_fwhmy])
                    if math.isfinite(vx) and math.isfinite(vy) and 0.1 < vx < 50.0 and 0.1 < vy < 50.0:
                        val = (vx + vy) / 2.0
                        if col_roundness == -1 and max(vx, vy) > 0:
                            roundness_list.append(min(vx, vy) / max(vx, vy))
                elif col_fwhm != -1 and col_fwhm < len(parts):
                    v = float(parts[col_fwhm])
                    if math.isfinite(v) and 0.1 < v < 50.0:
                        val = v
                if val is not None:
                    fwhms.append(val)
                if col_roundness != -1 and col_roundness < len(parts):
                    r_val = float(parts[col_roundness])
                    if math.isfinite(r_val) and 0.0 <= r_val <= 1.0:
                        roundness_list.append(r_val)
            except ValueError:
                continue

    if not fwhms:
        return {"detected": False, "star_count": 0}

    fwhm_med = _median(fwhms)
    fwhm_mean, fwhm_std = _mean_and_std(fwhms)
    round_med = _median(roundness_list) if roundness_list else None

    return {
        "detected": True,
        "star_count": len(fwhms),
        "fwhm_median": round(fwhm_med, 3),
        "fwhm_mean": round(fwhm_mean, 3),
        "fwhm_std": round(fwhm_std, 3),
        "roundness_median": round(round_med, 3) if round_med is not None else None,
    }


def _channel_statistics(values, channel, *, scientific, quantiles=False):
    import numpy as np
    values = np.asarray(values, dtype=float).ravel()
    finite = values[np.isfinite(values)]
    record = {"channel": channel, "pixel_count": int(values.size),
              "finite_count": int(finite.size), "nonfinite_count": int(values.size - finite.size)}
    if finite.size:
        median = float(np.median(finite))
        record.update(median=median, mad=float(np.median(np.abs(finite - median))),
                      std=float(np.std(finite)), mean=float(np.mean(finite)),
                      near_black_rate=float(np.count_nonzero((finite > 0) & (finite <= .0001)) / finite.size))
        if scientific:
            record.update(shadow_clip_rate=float(np.count_nonzero(finite <= 0) / finite.size),
                          highlight_sat_rate=float(np.count_nonzero(finite >= 1) / finite.size))
        if quantiles:
            record.update(zip(("p01", "p50", "p99"), map(float, np.percentile(finite, [1, 50, 99]))))
    return record


def analyze_image_histogram(image_path: Path) -> dict[str, Any]:
    """Measure every scientific FITS/XISF pixel before clipping; display statistics are diagnostic only."""
    try:
        import numpy as np
        from deep_sky_siril_artifacts import read_fits_pixels, read_xisf_pixels
        from deep_sky_siril_contract import fingerprint
        resolved = image_path.expanduser().resolve()
        scientific = resolved.suffix.lower() in {".fit", ".fits", ".fts", ".xisf"}
        selection = {}
        before = fingerprint(resolved, include_path=False) if resolved.suffix.lower() == ".xisf" else None
        if resolved.suffix.lower() == ".xisf":
            layers, scale, zero, selection = read_xisf_pixels(resolved)
        elif scientific:
            layers, scale, zero = read_fits_pixels(resolved)
        else:
            from PIL import Image
            with Image.open(resolved) as image:
                layers = np.asarray(image.convert("RGB"), dtype=float).transpose(2, 0, 1)
            scale, zero = 1 / 255, 0
        channels = []
        for index, layer in enumerate(layers):
            values = np.asarray(layer, dtype=float)
            if selection.get("bounds") is not None:
                low, high = selection["bounds"]
                # Subtract then divide so declared black/white endpoints remain exactly 0/1.
                values = (values - low) / (high - low)
            else:
                values = values * scale + zero
            record = _channel_statistics(values, index, scientific=scientific)
            for name in ("median", "mad", "std", "mean"):
                if name in record:
                    record["image_" + name] = record.pop(name)
            channels.append(record)
        if before is not None and fingerprint(resolved, include_path=False) != before:
            raise ValueError("XISF changed during measurement")
        return {**selection, "status": "measured" if all(c["finite_count"] and not c["nonfinite_count"] for c in channels) else "uncertain",
                "measurement_domain": "scientific" if scientific else "display_quantized",
                "channels": channels, "pixel_count": sum(c["pixel_count"] for c in channels)}
    except Exception as exc:
        return {"status": "uncertain", "error": f"{type(exc).__name__}: {exc}"}


def _separation_region_differences(parent, candidate, stars, rect) -> dict[str, Any]:
    """Read signed local differences; neither canceling medians nor closure prove preservation."""
    import numpy as np
    from deep_sky_siril_contract import STAR_DIAGNOSTICS_POLICY

    x0, _y0, _x1, y1 = rect
    channels = []
    for index, (p, a, s) in enumerate(zip(parent, candidate, stars)):
        difference = p - a
        closure = a + s - p
        row = _channel_statistics(difference, index, scientific=False, quantiles=True)
        finite = np.isfinite(difference)
        selected = difference[finite]
        row.update(positive_count=int(np.count_nonzero(selected > 0)),
                   negative_count=int(np.count_nonzero(selected < 0)),
                   nonpositive_count=int(np.count_nonzero(selected <= 0)),
                   positive_mass=float(np.sum(np.maximum(selected, 0))),
                   negative_mass=float(np.sum(np.maximum(-selected, 0))),
                   absolute_mass=float(np.sum(np.abs(selected))),
                   max_abs_difference=None, max_abs_difference_position=None)
        if selected.size:
            y, x = np.unravel_index(np.argmax(np.where(finite, np.abs(difference), -np.inf)), difference.shape)
            row.update(max_abs_difference=float(abs(difference[y, x])),
                       max_abs_difference_position=[int(x0 + x), int(y1 - 1 - y)])
        closure_finite = np.isfinite(closure)
        row['closure_nonfinite_count'] = int(np.count_nonzero(~closure_finite))
        row['max_abs_closure'] = float(np.max(np.abs(closure[closure_finite]))) if closure_finite.any() else None
        row['status'] = 'measured' if (finite.all() and closure_finite.all()) else 'uncertain'
        channels.append(row)
    return {'policy': STAR_DIAGNOSTICS_POLICY, 'measurement_domain': 'same_scale_linear_scientific',
            'difference_definition': 'parent - starless', 'requires_visual_review': True,
            'channels': channels}


def measure_regions(candidate_path: Path, parent_path: Path | None, regions_path: Path,
                    *, star_layer_path: Path | None = None, scientific: bool = False) -> dict[str, Any]:
    """Measure explicit rectangles; display top-left y is reversed for FITS storage."""
    import numpy as np
    from deep_sky_siril_artifacts import read_fits_pixels, read_scientific_pixels
    from deep_sky_siril_contract import ContractError, sha256_file
    from siril_manual_bundle import strict_json_bytes
    raw = regions_path.read_bytes()
    contract = strict_json_bytes(raw, document="regions contract")
    if not isinstance(contract, dict) or set(contract) != {"schema", "reference", "regions"} or contract["schema"] != "starun-siril.regions.v1":
        raise ValueError("Invalid region contract fields or schema")
    reference = contract["reference"]
    if not isinstance(reference, dict) or set(reference) != {"sha256", "width", "height"}:
        raise ValueError("Invalid region reference")
    if not isinstance(reference["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", reference["sha256"]):
        raise ValueError("Invalid region reference SHA-256")
    width, height = reference["width"], reference["height"]
    if any(type(n) is not int or n <= 0 for n in (width, height)):
        raise ValueError("Region reference dimensions must be positive integers")
    regions = contract["regions"]
    if not isinstance(regions, list) or not 1 <= len(regions) <= 64:
        raise ValueError("Declare 1..64 explicit regions")
    seen = set()
    for region in regions:
        if not isinstance(region, dict) or set(region) != {"id", "role", "rect"}:
            raise ValueError("Invalid region fields")
        name, role, rect = region["id"], region["role"], region["rect"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or name in seen:
            raise ValueError("Region IDs must be unique safe identifiers")
        seen.add(name)
        if not isinstance(role, str) or role not in {"background", "core", "faint", "stars"}:
            raise ValueError("Invalid region role")
        if not isinstance(rect, list) or len(rect) != 4 or any(type(n) is not int for n in rect):
            raise ValueError("Region rectangle requires four integers")
        x0, y0, x1, y1 = rect
        if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
            raise ValueError("Region rectangle is empty or outside the reference")
    if star_layer_path is not None and parent_path is None:
        raise ValueError("Separation region diagnostics require a parent")
    paths = {"candidate": candidate_path, **({"parent": parent_path} if parent_path else {}),
             **({"star_layer": star_layer_path} if star_layer_path is not None else {})}
    hashes = {name: sha256_file(path) for name, path in paths.items()}
    if reference["sha256"] != hashes["parent" if parent_path else "candidate"]:
        raise ValueError("Region reference hash changed; declare new regions")
    report = {"schema": "starun-siril.region-metrics.v1", "status": "measured",
              "coordinate_frame": "preview_top_left_zero_based_half_open",
              "contract_sha256": __import__("hashlib").sha256(raw).hexdigest(),
              "reference": reference, "images": {k: {"path": str(p), "sha256": hashes[k]} for k, p in paths.items()},
              "regions": [], "recommended_verdict": "uncertain"}
    formats = {".fit", ".fits", ".fts", *({".xisf"} if scientific else set())}
    if any(path.suffix.lower() not in formats for path in paths.values()):
        return {**report, "status": "unsupported", "reason": "Scientific region metrics require uncompressed FITS; no display fallback."}
    layers = {}
    try:
        for name, path in paths.items():
            if scientific:
                arr, selection = read_scientific_pixels(path)
                report.setdefault('scientific_sources', {})[name] = selection
                scale, zero = 1, 0
            else:
                arr, scale, zero = read_fits_pixels(path)
            if arr.shape[1:] != (height, width):
                return {**report, "status": "uncertain", "reason": "Geometry differs; declare new regions after cropping."}
            layers[name] = (arr, scale, zero)
        if len({arr.shape[0] for arr, _, _ in layers.values()}) != 1:
            return {**report, "status": "uncertain", "reason": "Channel count differs; region comparison is unavailable."}
        for region in regions:
            x0, y0, x1, y1 = region["rect"]
            record = dict(region)
            crops = {}
            for name, (arr, scale, zero) in layers.items():
                crops[name] = np.asarray(arr[:, height-y1:height-y0, x0:x1], dtype=float) * scale + zero
                channels = [_channel_statistics(layer, index, scientific=True, quantiles=True)
                            for index, layer in enumerate(crops[name])]
                if star_layer_path is not None:
                    for channel in channels:
                        channel['raw_nonpositive_rate'] = channel.get('shadow_clip_rate')
                status = "measured" if all(c["finite_count"] and not c["nonfinite_count"] for c in channels) else "uncertain"
                record[name] = {"status": status, "channels": channels}
                if status != "measured":
                    report["status"] = "uncertain"
            if star_layer_path is not None:
                record['separation_diagnostics'] = _separation_region_differences(
                    crops['parent'], crops['candidate'], crops['star_layer'], region['rect'])
                if any(row['status'] != 'measured' for row in record['separation_diagnostics']['channels']):
                    report['status'] = 'uncertain'
            report["regions"].append(record)
    except (OSError, ValueError, ContractError) as exc:
        report.update(status="uncertain", reason=str(exc))
    if regions_path.read_bytes() != raw or any(sha256_file(p) != hashes[k] for k, p in paths.items()):
        raise ValueError("Region contract or image changed during measurement")
    return report


def _has_neutral_evidence(report: Any) -> bool:
    """Incomplete or foreign diagnostics cannot suppress the absolute-count fallback."""
    if not isinstance(report, dict):
        return False
    binding, partition = report.get('bindings'), report.get('background_partition')
    benefit, safety = report.get('benefit'), report.get('safety')
    return (report.get('schema') == 'starun-siril.neutral-validation.v1'
            and report.get('policy') in {'heldout_benefit_safety_v1', 'scientific_native_flow_v2'} and report.get('status') == 'measured'
            and report.get('recommended_verdict') == 'uncertain'
            and report.get('method') in {'pure_additive', 'reference_white_balance'}
            and isinstance(binding, dict) and set(binding) == ({'parent', 'candidate', 'regions', 'coefficients_sha256'} |
                ({'scientific_sources'} if report.get('policy') == 'scientific_native_flow_v2' else set()))
            and (report.get('policy') != 'scientific_native_flow_v2' or
                 isinstance(binding.get('scientific_sources'), dict) and set(binding['scientific_sources']) == {'parent', 'candidate'})
            and isinstance(binding['coefficients_sha256'], str) and bool(re.fullmatch(r'[a-f0-9]{64}', binding['coefficients_sha256']))
            and isinstance(binding['regions'], dict) and set(binding['regions']) == {'sha256', 'size'}
            and isinstance(partition, dict) and set(partition) == {'fit', 'validation'}
            and all(isinstance(partition[k], list) and len(partition[k]) >= 3 for k in partition)
            and isinstance(benefit, dict) and benefit.get('status') == 'measured'
            and isinstance(benefit.get('regions'), list) and len(benefit['regions']) == len(partition['validation'])
            and isinstance(safety, dict) and isinstance(safety.get('channels'), list) and len(safety['channels']) == 3)


def evaluate_stage_gates(protocol: str, metrics: dict[str, Any], *,
                         parent_metrics: dict[str, Any] | None = None,
                         stars_metrics: dict[str, Any] | None = None,
                         background_validation: dict[str, Any] | None = None,
                         neutral_validation: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Only proven failures can reject; numerical diagnostics never accept visual gates."""
    gates = {name: {"verdict": "uncertain", "evidence": "Actual visual review is required."}
             for name in ("structure", "background", "color", "stars", "geometry")}
    verdict = "uncertain"
    heldout_background = (protocol == 'background.subtract' and isinstance(background_validation, dict)
                          and background_validation.get('policy') in {'heldout_material_gate_v2', 'heldout_material_gate_v3', 'heldout_material_gate_v4', 'heldout_review_gate_v5'})
    if heldout_background:
        status = background_validation.get('status')
        gates['background'] = {'verdict': 'fail' if status == 'failed' else 'uncertain',
                               'evidence': 'Bound held-out background evidence: ' + str(background_validation.get('decision')) + '; actual visual review is required.'}
        if status == 'failed':
            verdict = 'reject'
    neutral = protocol == 'color.calibrate' and _has_neutral_evidence(neutral_validation)
    if neutral:
        gates['background']['evidence'] = 'Bound held-out neutral benefit and paired endpoint risks; actual visual review is required.'
        gates['color']['evidence'] = 'Bound gain-adjusted subject/stellar signals; physical color and visual review remain independent.'
    if protocol != "input.inspect" and not heldout_background and not neutral and metrics.get("measurement_domain") == "scientific":
        rates = [c.get("shadow_clip_rate") for c in metrics.get("channels", [])]
        if any(isinstance(rate, (float, int)) and math.isfinite(rate) and rate >= 0.0001 for rate in rates):
            gates["background"] = {"verdict": "fail", "evidence": "Scientific shadow clipping reaches 0.01% in at least one channel."}
            verdict = "reject"
    if stars_metrics and stars_metrics.get("detected"):
        rnd = stars_metrics.get("roundness_median")
        if isinstance(rnd, (float, int)) and math.isfinite(rnd) and rnd < 0.30:
            gates["stars"] = {"verdict": "fail", "evidence": "Measured median star roundness is below 0.30."}
            verdict = "reject"
    return gates, verdict


def generate_metric_report(
    *,
    run_id: str,
    protocol: str,
    candidate_path: Path,
    parent_path: Path | None = None,
    stars_tsv_path: Path | None = None,
    output_path: Path | None = None,
    regions_path: Path | None = None,
    star_layer_path: Path | None = None,
    background_validation: dict[str, Any] | None = None,
    neutral_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile comprehensive quantitative metric report for a completed run."""
    if star_layer_path is not None and (protocol != 'stars.separate' or parent_path is None or regions_path is None):
        raise ValueError("--star-layer requires stars.separate with --parent and --regions")
    if output_path is not None:
        protected = [p.resolve() for p in (candidate_path, parent_path, stars_tsv_path, regions_path, star_layer_path) if p is not None]
        if output_path.resolve() in protected or output_path.suffix.lower() in {".fit", ".fits", ".fts", ".xisf"}:
            raise ValueError("Metric output must not overwrite scientific inputs or evidence")
    if neutral_validation is not None:
        from deep_sky_siril_contract import fingerprint
        if not _has_neutral_evidence(neutral_validation):
            raise ValueError('Neutral metrics require complete held-out benefit and paired safety evidence')
        binding = neutral_validation.get('bindings', {})
        if (protocol != 'color.calibrate' or parent_path is None
            or binding.get('parent') != fingerprint(parent_path, include_path=False)
            or binding.get('candidate') != fingerprint(candidate_path, include_path=False)):
            raise ValueError('Neutral metrics require evidence bound to this parent and candidate')
    region_metrics = measure_regions(candidate_path, parent_path, regions_path, star_layer_path=star_layer_path) if regions_path else None
    candidate_metrics = analyze_image_histogram(candidate_path)
    parent_metrics = analyze_image_histogram(parent_path) if parent_path else None
    stars_metrics = parse_stars_tsv(stars_tsv_path) if stars_tsv_path else None

    gates, recommended_verdict = evaluate_stage_gates(
        protocol,
        candidate_metrics,
        parent_metrics=parent_metrics,
        stars_metrics=stars_metrics,
        background_validation=background_validation,
        neutral_validation=neutral_validation,
    )

    report = {
        "schema": METRIC_REPORT_SCHEMA,
        "run_id": run_id,
        "protocol": protocol,
        "candidate": {
            "path": str(candidate_path),
            "metrics": candidate_metrics,
        },
        "parent": {
            "path": str(parent_path) if parent_path else None,
            "metrics": parent_metrics,
        }
        if parent_metrics
        else None,
        "stars": stars_metrics,
        "gate_evaluations": gates,
        "recommended_verdict": recommended_verdict,
    }

    if regions_path is not None:
        report["region_metrics"] = region_metrics
    if background_validation is not None:
        report['background_validation'] = background_validation
    if neutral_validation is not None:
        report['neutral_validation'] = neutral_validation

    if output_path is not None:
        resolved_out = output_path.expanduser().resolve()
        resolved_out.parent.mkdir(parents=True, exist_ok=True)
        temp_out = resolved_out.with_name(f".{resolved_out.name}.tmp")
        temp_out.write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
        )
        temp_out.replace(resolved_out)

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute quantitative physical metrics for starun-siril run artifacts."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier, e.g. 030-background")
    parser.add_argument("--session", help="Recover a supplemental report from a verified successful run")
    parser.add_argument("--protocol", help="Protocol ID")
    parser.add_argument("--candidate", help="Path to primary candidate artifact")
    parser.add_argument("--parent", required=False, default=None, help="Path to parent source artifact")
    parser.add_argument("--stars-tsv", required=False, default=None, help="Optional stars.tsv path")
    parser.add_argument("--regions", help="Explicit hash-bound scientific region contract JSON")
    parser.add_argument("--star-layer", help="Signed linear star layer for optional stars.separate ROI diagnostics")
    parser.add_argument("--output", required=False, default=None, help="Destination report JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.session is not None:
        if any(getattr(args, name) is not None for name in
               ("protocol", "candidate", "parent", "stars_tsv", "regions", "star_layer", "output")):
            parser.error("--session is mutually exclusive with manual measurement arguments")
        from deep_sky_siril_contract import ContractError
        from deep_sky_siril_core import recover_run_metrics
        try:
            result = recover_run_metrics(args.session, args.run_id)
        except ContractError as exc:
            print(json.dumps({"status": "failed", "error": {"code": exc.code, "message": str(exc)}}), file=sys.stderr)
            return 2
        except Exception as exc:
            print(json.dumps({"status": "failed", "error": {"code": "metrics_recovery_failed",
                  "message": f"{type(exc).__name__}: {exc}"}}), file=sys.stderr)
            return 3
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.protocol is None or args.candidate is None:
        parser.error("--protocol and --candidate are required without --session")
    if args.star_layer and (args.protocol != 'stars.separate' or not args.parent or not args.regions):
        parser.error("--star-layer requires stars.separate with --parent and --regions")
    candidate_path = Path(args.candidate).expanduser().resolve()
    parent_path = Path(args.parent).expanduser().resolve() if args.parent else None
    stars_tsv_path = Path(args.stars_tsv).expanduser().resolve() if args.stars_tsv else None
    output_path = Path(args.output).expanduser().resolve() if args.output else None

    report = generate_metric_report(
        run_id=args.run_id,
        protocol=args.protocol,
        candidate_path=candidate_path,
        parent_path=parent_path,
        stars_tsv_path=stars_tsv_path,
        output_path=output_path,
        regions_path=Path(args.regions).expanduser().resolve() if args.regions else None,
        star_layer_path=Path(args.star_layer).expanduser().resolve() if args.star_layer else None,
    )
    print(
        f"Metrics evaluated for {args.run_id} ({args.protocol}): recommended verdict = {report['recommended_verdict']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def measure_color_protection(source, candidate, regions_path, *, gains=None, sky_ids=None):
    """Paired subject evidence with the neutralization signal-chromaticity convention."""
    import numpy as np
    from deep_sky_siril_artifacts import read_scientific_pixels
    from deep_sky_siril_contract import fingerprint
    from deep_sky_siril_neutral import _center, endpoint_transitions
    measured = measure_regions(candidate, source, regions_path, scientific=True)
    if measured['status'] != 'measured':
        raise ValueError('Color protection requires source-bound finite regions and unchanged geometry')
    if gains is not None and (not isinstance(gains, (list, tuple)) or len(gains) != 3
                              or any(type(v) not in (int, float) or not np.isfinite(v) or v <= 0 for v in gains)):
        raise ValueError('Color protection gains must be three finite positive values')
    before, _ = read_scientific_pixels(source)
    after, _ = read_scientific_pixels(candidate)
    width, height = measured['reference']['width'], measured['reference']['height']
    subjects = [r for r in measured['regions'] if r['role'] in ('faint', 'core')]
    sky = [r for r in measured['regions'] if r['role'] == 'background'
           and (sky_ids is None or r['id'] in sky_ids)
           and all(ch['finite_count'] >= 64 for layer in ('parent', 'candidate') for ch in r[layer]['channels'])]
    def intersects(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
    sky = [r for r in sky if not any(intersects(r['rect'], s['rect']) for s in subjects)]
    report = {'policy': 'vivid_preservation_v1', 'regions_binding': fingerprint(regions_path, include_path=False),
              'reference': measured['reference'], 'gains': gains, 'regions': [],
              'interpretation': 'Compare continuity, color and weak edges per ROI; neutral sky cannot offset subject regression.'}
    for row in subjects:
        x0, y0, x1, y1 = row['rect']
        crops = {key: values[:, height-y1:height-y0, x0:x1].astype(float)
                 for key, values in (('parent', before), ('candidate', after))}
        rgb = {key: np.asarray([ch['median'] for ch in row[key]['channels']]) for key in crops}
        local = min(sky, key=lambda r: (sum((a-b)**2 for a,b in zip(_center(row,width,height), _center(r,width,height))), r['id'])) if sky else None
        signals = {key: values-np.asarray([ch['median'] for ch in local[key]['channels']])
                   for key, values in rgb.items()} if local else None
        colors = {}
        for key in crops:
            usable = bool(signals is not None and np.all(signals[key] > 1e-7) and len(signals[key]) == 3)
            colors[key] = {'status': 'measured' if usable else 'unavailable',
                           'rgb_fraction': (signals[key]/signals[key].sum()).tolist() if usable else None,
                           'reason': None if usable else ('No reliable nonoverlapping background reference' if local is None
                                                         else 'Nonpositive or near-zero background-subtracted signal')}
        report['regions'].append({'id': row['id'], 'role': row['role'], 'rect': row['rect'],
            'rgb': {key: values.tolist() for key, values in rgb.items()},
            'luminance': {key: float(np.median(np.sum(values*np.array([.2126,.7152,.0722])[:,None,None],axis=0)))
                          if len(values) == 3 else float(np.median(values)) for key, values in crops.items()},
            'local_sky_id': local['id'] if local else None,
            'signal_rgb': {key: values.tolist() for key, values in signals.items()} if signals else None,
            'signal_chromaticity': colors,
            'gain_adjusted_signal_delta': (signals['candidate']-np.asarray(gains)*signals['parent']).tolist()
                                          if gains is not None and signals and len(before) == 3 else None,
            'endpoints': [endpoint_transitions(a,b) for a,b in zip(crops['parent'],crops['candidate'])]})
    if report['regions_binding'] != fingerprint(regions_path, include_path=False):
        raise ValueError('Color protection regions changed during measurement')
    return report
