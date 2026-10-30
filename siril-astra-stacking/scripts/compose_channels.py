"""Compose prepared filters, with optional explicit H-alpha mixing or LRGB fusion."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
import tempfile

from astra_stack import find_siril, siril_version, write_report
from fits_probe import FitsError
from param_derive import derive_preview_mtf
from prepare_composition import (
    EXPOSURE_KEYS, inspect_pixels, publish, run_script, sha256_file, validate_metadata,
)
from siril_script_gen import ScriptBuilder
from composition_quality import (
    background_mtf, clipping_counts, common_coverage, edge_coverage, sample_statistics,
)

MODES = {"rgb": ("R", "G", "B"), "sho": ("SII", "HA", "OIII"),
         "hoo": ("HA", "OIII", "OIII"), "lrgb": ("R", "G", "B"),
         "hargb": ("R", "G", "B"), "halrgb": ("R", "G", "B")}
HA_MODES = {"hargb", "halrgb"}
LUMINANCE_MODES = {"lrgb", "halrgb"}
ALIASES = {"H": "HA", "HA": "HA", "HALPHA": "HA", "H-ALPHA": "HA",
           "H_ALPHA": "HA", "S": "SII", "SII": "SII", "O": "OIII",
           "OIII": "OIII", "R": "R", "G": "G", "B": "B", "L": "L"}
REMOVED_KEYS = ("BAYERPAT", "EXPTIME", "LIVETIME", "STACKCNT")
LRGB_COMMENT = "NONLINEAR LRGB: MTF pre-stretch applied to L and RGB"
HA_COMMENT = "Explicit H-alpha/R mixture; no continuum subtraction"


def validate_ha_options(args):
    weight = getattr(args, "ha_weight", None)
    scale = getattr(args, "ha_scale", None)
    if args.mode not in HA_MODES:
        if weight is not None or scale is not None:
            raise ValueError("--ha-weight and --ha-scale apply only to hargb/halrgb")
        return
    if weight is None or scale is None:
        raise ValueError("hargb/halrgb require both --ha-weight and --ha-scale")
    if type(weight) not in (int, float) or not 0 <= weight <= 1 or not math.isfinite(weight):
        raise ValueError("--ha-weight must be finite and between 0 and 1")
    if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
        raise ValueError("--ha-scale must be finite and positive")


def validate_display_options(args):
    stretch = getattr(args, "stretch", "legacy")
    crop = getattr(args, "crop", "none")
    target = getattr(args, "target_bg", None)
    if stretch not in ("legacy", "background") or crop not in ("none", "common"):
        raise ValueError("invalid stretch or crop strategy")
    if target is not None and stretch != "background":
        raise ValueError("--target-bg applies only to --stretch background")
    if target is not None and (type(target) not in (int, float) or not math.isfinite(target) or not 0 < target < 1):
        raise ValueError("--target-bg must be finite and in (0,1)")


def contributing_channels(args, selected):
    channels = set(selected)
    if args.mode in HA_MODES:
        if args.ha_weight == 0:
            channels.remove("HA")
        if args.ha_weight == 1:
            channels.remove("R")
    return sorted(channels)


def collect_prepared(directory, mode):
    """Read only the selected local channels, independent of historic paths."""
    root = Path(directory).resolve()
    manifests = [path for path in (
        root / "composition_manifest.json",
        root / "diagnostics" / "composition_manifest.json",
    ) if path.is_file()]
    if len(manifests) != 1:
        raise FitsError("expected exactly one composition_manifest.json (root or diagnostics)")
    manifest_path = manifests[0]
    manifest_hash = sha256_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    external = (isinstance(manifest, dict) and manifest.get("schema_version") == "1.1"
                and manifest.get("origin") == "external_masters" and manifest.get("linear_confirmed") is True)
    if (not isinstance(manifest, dict) or not (manifest.get("schema_version") == "1.0" or external)
            or manifest.get("status") != "ok"
            or manifest.get("monochrome_confirmed") is not True
            or not isinstance(manifest.get("layers"), list)):
        raise FitsError("expected a successful monochrome preparation manifest (schema 1.0 or explicit external 1.1)")
    needed = set(MODES[mode])
    if mode in LUMINANCE_MODES:
        needed.add("L")
    if mode in HA_MODES:
        needed.add("HA")
    selected = {}
    for layer in manifest["layers"]:
        if not isinstance(layer, dict) or not isinstance(layer.get("filter"), str):
            raise FitsError("manifest layer must have a FILTER label")
        label = layer["filter"]
        canonical = ALIASES.get(label.upper())
        if canonical not in needed:
            continue
        if canonical in selected:
            raise FitsError("duplicate aliases for required channel %s" % canonical)
        if (not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", label)
                or layer.get("format_ready") is not True
                or not isinstance(layer.get("header"), dict)
                or any(key not in layer["header"] for key in (("FILTER", "OBJECT") if external else EXPOSURE_KEYS))):
            raise FitsError("%s: channel is not a prepared composition input" % label)
        path = root / (label + ".fit")
        if path.resolve().parent != root:
            raise FitsError("%s: channel must be inside the preparation directory" % label)
        digest = sha256_file(path)
        header, pixels, warnings = inspect_pixels(path)
        validate_metadata(layer, header)
        if str(header.get("FILTER") or "").strip().upper() != label.upper():
            raise FitsError("%s: FILTER disagrees with manifest label" % label)
        if not isinstance(header.get("OBJECT"), str) or not header["OBJECT"].strip():
            raise FitsError("%s: missing OBJECT" % label)
        count = header.get("STACKCNT")
        if (not external or count is not None) and (type(count) is not int or count < 1):
            raise FitsError("%s: invalid STACKCNT" % label)
        for key in ("EXPTIME", "LIVETIME"):
            value = header.get(key)
            if external and value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise FitsError("%s: invalid %s" % (label, key))
        quality_warnings = layer.get("quality_warnings", [])
        if not isinstance(quality_warnings, list) or not all(
                isinstance(warning, str) for warning in quality_warnings):
            raise FitsError("%s: invalid quality warnings" % label)
        selected[canonical] = {
            "channel": canonical, "filter": label, "path": str(path),
            "sha256": digest, "header": header, "pixels": pixels,
            "quality_warnings": list(dict.fromkeys(quality_warnings + warnings)),
        }
        metadata = layer.get("external_metadata", {}) if external else {}
        if not isinstance(metadata, dict):
            raise FitsError("invalid external metadata")
        reported_count = count if count is not None else metadata.get("reported_frames")
        if reported_count is not None and (type(reported_count) is not int or reported_count < 1):
            raise FitsError("invalid external frame count")
        selected[canonical].update(
            origin="external_masters" if external else "astra_stacks",
            exposure={"nominal_seconds": header.get("EXPTIME"), "stacked_frames": reported_count,
                      "total_seconds": header.get("LIVETIME"),
                      "frames_evidence": ["FITS STACKCNT"] if count is not None else metadata.get("frames_evidence", [])},
            external_metadata=metadata if external else None,
        )
    missing = sorted(needed - selected.keys())
    if missing:
        raise FitsError("missing required channel(s): %s" % ", ".join(missing))
    sources = list(selected.values())
    if len({source["header"]["OBJECT"].strip() for source in sources}) != 1:
        raise FitsError("channels have different OBJECT values")
    if len({(source["header"]["NAXIS1"], source["header"]["NAXIS2"])
            for source in sources}) != 1:
        raise FitsError("channels do not share a common shape")
    return manifest_path, manifest_hash, selected


def compose(args):
    validate_ha_options(args)
    validate_display_options(args)
    stretch = getattr(args, "stretch", "legacy")
    crop = getattr(args, "crop", "none")
    target_bg = getattr(args, "target_bg", None)
    target_bg = 0.15 if target_bg is None else target_bg
    lrgb = args.mode in LUMINANCE_MODES
    ha = args.mode in HA_MODES
    color_channels = ("R_HA", "G", "B") if ha else MODES[args.mode]
    out = Path(args.out).absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError("output already exists: %s" % out)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".compose-", dir=str(out.parent)))
    report = {
        "schema_version": "1.4", "status": "failed", "failed_stage": "collect",
        "mode": args.mode, "input_dir": str(Path(args.input).resolve()),
        "output_state": "nonlinear" if lrgb else "linear",
        "normalization": "explicit_ha_scale" if ha else "none",
        "pre_stretch": None, "ha_blend": None,
        "warnings": [], "artifacts": {},
        "stretch": {"strategy": stretch, "target_bg": target_bg if stretch == "background" else None},
        "quality": {"stretch_clipping": {}},
    }
    try:
        manifest_path, manifest_hash, selected = collect_prepared(args.input, args.mode)
        report["input_manifest"] = {"path": str(manifest_path), "sha256": manifest_hash}
        report["sources"] = list(selected.values())
        report["input_origin"] = next(iter(selected.values()))["origin"]
        report["mapping"] = dict(zip(("r", "g", "b"), color_channels))
        if ha:
            report["ha_blend"] = {
                "formula": "(1 - weight) * R + weight * scale * HA",
                "weight": args.ha_weight, "scale": args.ha_scale,
                "continuum_subtraction": False,
            }
        if lrgb:
            report["mapping"]["l"] = "L"
        report["warnings"] = list(dict.fromkeys(
            warning for source in selected.values() for warning in source["quality_warnings"]
        ))
        report["failed_stage"] = "environment"
        siril = find_siril(args.siril)
        if not siril:
            raise FitsError("Siril CLI was not found")
        version = siril_version(siril) or ""
        if not re.search(r"\b1\.4\.", version):
            raise FitsError("expected Siril 1.4.x, found %s" % version)
        report["siril"] = {"path": siril, "version": version}
        report["failed_stage"] = "coverage"
        contributors = contributing_channels(args, selected)
        intervals = []
        channel_coverage = {}
        for channel in contributors:
            rows, summary = edge_coverage(selected[channel]["path"])
            intervals.append(rows)
            channel_coverage[channel] = summary
        reference = next(iter(selected.values()))["header"]
        width, height = reference["NAXIS1"], reference["NAXIS2"]
        candidate = common_coverage(intervals, width)
        report["coverage"] = {
            "method": "edge_zero_runs", "heuristic": True, "coordinate_origin": "siril_top_left",
            "contributors": contributors, "channels": channel_coverage,
            "input_size": [width, height], "candidate_rect": candidate, "crop": crop,
            "applied_rect": None,
            "candidate_loss_pct": 100 * (1 - candidate["width"] * candidate["height"] / (width * height))
            if candidate else 100.0,
        }
        if any(item["edge_zero_count"] for item in channel_coverage.values()):
            report["warnings"].append("edge-zero padding candidate; coverage is a heuristic, not an exposure mask")
        if candidate is None:
            report["warnings"].append("no common coverage candidate")
            if crop == "common" or stretch == "background":
                raise FitsError("no common candidate region for crop or background stretch")
        diagnostics = work / "diagnostics"
        diagnostics.mkdir()
        for channel, source in selected.items():
            (diagnostics / (channel + ("_source.fit" if crop == "common" else ".fit"))).symlink_to(source["path"])
        cropped_pixels = {}
        if crop == "common":
            builder = ScriptBuilder()
            builder.add("setext", ["fit"])
            builder.add("set32bits")
            for channel in selected:
                builder.add("load", [channel + "_source.fit"])
                builder.add("crop", [str(candidate[key]) for key in ("x", "y", "width", "height")])
                builder.add("save", [channel])
            report["failed_stage"] = "crop"
            run_script(siril, diagnostics, "crop", builder, args.timeout)
            width, height = candidate["width"], candidate["height"]
            for channel in selected:
                cropped, cropped_pixels[channel], _ = inspect_pixels(diagnostics / (channel + ".fit"))
                validate_metadata(selected[channel], cropped)
                if (cropped["NAXIS"] != 2 or cropped.get("BAYERPAT")
                        or (cropped["NAXIS1"], cropped["NAXIS2"]) != (width, height)
                        or cropped.get("OBJECT") != reference["OBJECT"]):
                    raise FitsError("invalid cropped channel")
            report["coverage"]["applied_rect"] = candidate
        report["coverage"]["output_size"] = [width, height]
        region = (dict(x=0, y=0, width=width, height=height) if crop == "common" else candidate)

        def display_mtf(path, legacy_medians, mono=False, label="preview"):
            if stretch == "background":
                mtf, warnings = background_mtf(sample_statistics(path, region), target_bg)
                report["warnings"].extend(warnings)
            else:
                mtf = derive_preview_mtf(legacy_medians, mono=mono)
            if region:
                report["quality"]["stretch_clipping"][label] = clipping_counts(path, region, mtf["low"], mtf["high"])
            return mtf
        name = "composed_%s" % args.mode
        master = work / (name + "_32bit.fits")
        builder = ScriptBuilder()
        builder.add("setext", ["fit"])
        builder.add("set32bits")
        if ha:
            builder.add("pm", ["$R$ * %s + $HA$ * %s" % (
                1 - args.ha_weight, args.ha_weight * args.ha_scale), "-nosum"])
            builder.add("save", ["R_HA"])
            report["failed_stage"] = "ha_blend"
            run_script(siril, diagnostics, "ha_blend", builder, args.timeout)
            mixed_header, mixed_pixels, _ = inspect_pixels(diagnostics / "R_HA.fit")
            red_header = selected["R"]["header"]
            if (mixed_header["NAXIS"] != 2 or mixed_header.get("BAYERPAT")
                    or (mixed_header["NAXIS1"], mixed_header["NAXIS2"])
                    != (width, height)
                    or mixed_header.get("OBJECT") != red_header["OBJECT"]):
                raise FitsError("invalid H-alpha mixture format or metadata")
            if region:
                before = sample_statistics(diagnostics / "R.fit", region)
                after = sample_statistics(diagnostics / "R_HA.fit", region)
                original = before["channels"]["m"]["median"]
                mixed = after["channels"]["m"]["median"]
                report["ha_blend"]["diagnostics"] = {
                    "before": before, "after": after,
                    "median_ratio": mixed / original if original != 0 else None,
                    "median_change_pct": 100 * (mixed / original - 1) if original != 0 else None,
                    "mixed_range_counts": mixed_pixels["range_counts"],
                }
                if mixed < original:
                    report["warnings"].append("H-alpha mixture lowered the representative red median; coefficients unchanged")
            builder = ScriptBuilder()
            builder.add("setext", ["fit"])
            builder.add("set32bits")
        if lrgb:
            builder.add("rgbcomp", [channel + ".fit" for channel in color_channels]
                        + ["-out=rgb_linear", "-nosum"])
            report["failed_stage"] = "compose_rgb"
            run_script(siril, diagnostics, "compose_rgb", builder, args.timeout)
            report["failed_stage"] = "pre_stretch"
            rgb_header, rgb_pixels, _ = inspect_pixels(diagnostics / "rgb_linear.fit")
            if rgb_header["NAXIS"] != 3 or rgb_header.get("NAXIS3") != 3:
                raise FitsError("expected a three-channel linear RGB intermediate")
            report["pre_stretch"] = {
                "l": display_mtf(diagnostics / "L.fit", cropped_pixels["L"]["medians"]
                                 if crop == "common" else selected["L"]["pixels"]["medians"], mono=True, label="l"),
                "rgb": display_mtf(diagnostics / "rgb_linear.fit", rgb_pixels["medians"], label="rgb"),
            }
            builder = ScriptBuilder()
            builder.add("setext", ["fit"])
            builder.add("set32bits")
            for channel, source in (("l", "L.fit"), ("rgb", "rgb_linear.fit")):
                mtf = report["pre_stretch"][channel]
                builder.add("load", [source])
                builder.add("mtf", [str(mtf[key]) for key in ("low", "mid", "high")])
                builder.add("save", [channel + "_stretched"])
            run_script(siril, diagnostics, "pre_stretch", builder, args.timeout)
            builder = ScriptBuilder()
            builder.add("setext", ["fit"])
            builder.add("set32bits")
            builder.add("rgbcomp", ["-lum=l_stretched.fit", "rgb_stretched.fit",
                                    "-out=combined", "-nosum"])
        else:
            builder.add("rgbcomp", [channel + ".fit" for channel in color_channels]
                        + ["-out=combined", "-nosum"])
        builder.add("load", ["combined.fit"])
        if ha:
            builder.add("update_key", ["-comment", HA_COMMENT])
        if lrgb:
            builder.add("update_key", ["-comment", LRGB_COMMENT])
        # -nosum still inherits the first filter's exposure counters.
        for key in REMOVED_KEYS:
            builder.add("update_key", ["-delete", key])
        builder.add("save", ["../" + master.name])
        report["failed_stage"] = "compose"
        run_script(siril, diagnostics, "compose", builder, args.timeout)
        report["failed_stage"] = "verify"
        header, pixels, warnings = inspect_pixels(master)
        reference = next(iter(selected.values()))["header"]
        if (header["NAXIS"] != 3 or header.get("NAXIS3") != 3
                or (header["NAXIS1"], header["NAXIS2"])
                != (width, height)
                or header.get("OBJECT") != reference["OBJECT"]
                or any(key in header for key in REMOVED_KEYS)):
            raise FitsError("invalid RGB composition format or metadata")
        report["output_pixels"] = pixels
        report["warnings"] = list(dict.fromkeys(report["warnings"] + warnings))
        report["failed_stage"] = "preview"
        builder = ScriptBuilder()
        builder.add("load", ["../" + master.name])
        if lrgb:
            report["preview"] = {"method": "direct"}
        else:
            mtf = display_mtf(master, pixels["medians"])
            report["preview"] = mtf
            builder.add("mtf", [str(mtf[key]) for key in ("low", "mid", "high")])
        builder.add("savepng", ["../" + name])
        run_script(siril, diagnostics, "preview", builder, args.timeout)
        preview = work / (name + ".png")
        if not preview.is_file() or preview.stat().st_size == 0:
            raise FitsError("preview was not produced")
        report["failed_stage"] = "verify"
        for source in selected.values():
            if sha256_file(source["path"]) != source["sha256"]:
                raise FitsError("input channel changed during composition: %s" % source["filter"])
        if sha256_file(manifest_path) != manifest_hash:
            raise FitsError("preparation manifest changed during composition")
        for path in diagnostics.glob("*.fit"):
            path.unlink()
        report["artifacts"] = {
            "master": str(out / master.name), "preview": str(out / preview.name),
            "report": str(out / "composition_report.json"),
            "diagnostics": str(out / "diagnostics"),
        }
        report["failed_stage"] = "report"
        write_report(str(work / "composition_report.json"),
                     dict(report, status="ok", failed_stage=None))
        report["failed_stage"] = "publish"
        publish(work, out)
        report["failed_stage"] = None
        report["status"] = "ok"
        print("[compose] OK: %s" % report["artifacts"]["report"], file=sys.stderr)
        return report
    except BaseException as exc:
        report["status"] = "failed"
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["work_dir"] = str(work)
        if work.exists():
            write_report(str(work / "failure.json"), report)
        print("[compose] FAILED: %s; work=%s" % (exc, work), file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="prepared composition directory (plain or bundle)")
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--out", required=True, help="new directory for composed outputs")
    parser.add_argument("--siril", help="explicit Siril CLI executable")
    parser.add_argument("--timeout", type=int, default=3600, help="timeout per Siril invocation")
    parser.add_argument("--ha-weight", type=float, help="explicit H-alpha mixture weight (0 to 1)")
    parser.add_argument("--ha-scale", type=float, help="explicit positive H-alpha scale")
    parser.add_argument("--stretch", choices=("legacy", "background"), default="legacy")
    parser.add_argument("--target-bg", type=float, help="background target for --stretch background (default 0.15)")
    parser.add_argument("--crop", choices=("none", "common"), default="none")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        validate_ha_options(args)
        validate_display_options(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        compose(args)
    except (Exception, KeyboardInterrupt) as exc:
        print("[compose] %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
