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
from typing import Any, Sequence

METRIC_REPORT_SCHEMA = "starun-siril.metric-report.v2"


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 != 0 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _median_absolute_deviation(values: Sequence[float], med: float | None = None) -> float:
    if not values:
        return 0.0
    m = med if med is not None else _median(values)
    return _median([abs(x - m) for x in values])


def _mean_and_std(values: Sequence[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    n = len(values)
    mean_val = sum(values) / n
    var = sum((x - mean_val) ** 2 for x in values) / max(1, n - 1)
    return mean_val, math.sqrt(var)


def _skewness(values: Sequence[float], mean_val: float, std_val: float) -> float:
    if len(values) < 3 or std_val <= 1e-9:
        return 0.0
    n = len(values)
    m3 = sum((x - mean_val) ** 3 for x in values) / n
    return float(m3 / (std_val**3))


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


def analyze_image_histogram(image_path: Path) -> dict[str, Any]:
    """Measure every scientific FITS pixel before clipping; display statistics are diagnostic only."""
    try:
        import numpy as np
        from deep_sky_siril_artifacts import read_fits_pixels

        resolved = image_path.expanduser().resolve()
        scientific = resolved.suffix.lower() in {".fit", ".fits", ".fts"}
        if scientific:
            raw, scale, zero = read_fits_pixels(resolved)
            layers = raw
        else:
            from PIL import Image
            with Image.open(resolved) as image:
                layers = np.asarray(image.convert("RGB"), dtype=float).transpose(2, 0, 1)
            scale, zero = 1 / 255, 0
        channels = []
        for index, layer in enumerate(layers):
            values = np.asarray(layer, dtype=float).ravel() * scale + zero
            finite = values[np.isfinite(values)]
            record = {"channel": index, "pixel_count": int(values.size),
                      "finite_count": int(finite.size), "nonfinite_count": int(values.size - finite.size)}
            if finite.size:
                med = float(np.median(finite))
                record.update(image_median=med, image_mad=float(np.median(np.abs(finite - med))),
                              image_std=float(np.std(finite)), image_mean=float(np.mean(finite)),
                              near_black_rate=float(np.count_nonzero((finite > 0) & (finite <= 0.0001)) / finite.size))
                if scientific:
                    record.update(shadow_clip_rate=float(np.count_nonzero(finite <= 0) / finite.size),
                                  highlight_sat_rate=float(np.count_nonzero(finite >= 1) / finite.size))
            channels.append(record)
        return {"status": "measured" if all(c["finite_count"] and not c["nonfinite_count"] for c in channels) else "uncertain",
                "measurement_domain": "scientific" if scientific else "display_quantized",
                "channels": channels, "pixel_count": sum(c["pixel_count"] for c in channels)}
    except Exception as exc:
        return {"status": "uncertain", "error": f"{type(exc).__name__}: {exc}"}


def evaluate_stage_gates(protocol: str, metrics: dict[str, Any], *,
                         parent_metrics: dict[str, Any] | None = None,
                         stars_metrics: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Only proven failures can reject; numerical diagnostics never accept visual gates."""
    gates = {name: {"verdict": "uncertain", "evidence": "Actual visual review is required."}
             for name in ("structure", "background", "color", "stars", "geometry")}
    verdict = "uncertain"
    if protocol != "input.inspect" and metrics.get("measurement_domain") == "scientific":
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
) -> dict[str, Any]:
    """Compile comprehensive quantitative metric report for a completed run."""
    candidate_metrics = analyze_image_histogram(candidate_path)
    parent_metrics = analyze_image_histogram(parent_path) if parent_path else None
    stars_metrics = parse_stars_tsv(stars_tsv_path) if stars_tsv_path else None

    gates, recommended_verdict = evaluate_stage_gates(
        protocol,
        candidate_metrics,
        parent_metrics=parent_metrics,
        stars_metrics=stars_metrics,
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
    parser.add_argument("--protocol", required=True, help="Protocol ID")
    parser.add_argument("--candidate", required=True, help="Path to primary candidate artifact")
    parser.add_argument("--parent", required=False, default=None, help="Path to parent source artifact")
    parser.add_argument("--stars-tsv", required=False, default=None, help="Optional stars.tsv path")
    parser.add_argument("--output", required=False, default=None, help="Destination report JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    )
    print(
        f"Metrics evaluated for {args.run_id} ({args.protocol}): recommended verdict = {report['recommended_verdict']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
