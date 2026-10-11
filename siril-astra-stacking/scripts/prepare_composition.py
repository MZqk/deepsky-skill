"""Prepare published monochrome filter stacks for Siril RGB composition.

The original masters and single-filter reports are never changed. Dependencies:
Python 3.9+ standard library and Siril 1.4.x.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile

from astra_stack import find_siril, siril_version, write_report
from fits_probe import (
    FitsError, data_offset, estimate_channel_medians,
    read_fits_header_with_warnings, read_seq_regdata,
)
from siril_script_gen import ScriptBuilder

IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)
EXPOSURE_KEYS = ("FILTER", "OBJECT", "STACKCNT", "EXPTIME", "LIVETIME")
FILTER_ORDER = ("L", "R", "G", "B", "H", "S")

BUNDLE_ROOT_README = """# 多滤镜合成交付包

本目录包含经过全局星点配准、单通道转换与最小公共画幅裁切的单色 FITS 叠加图，专用于 Siril「多图层图像合成（RGB Compositing）」。

## 目录结构

- `<FILTER>.fit` (如 L.fit, R.fit, G.fit, B.fit, H.fit, S.fit)：
  - 32-bit 单通道（NAXIS=2）浮点 FITS 叠加图；
  - 像素尺寸完全一致，在 Siril 图像合成窗口中直接选择，显示全绿 `OK`。
- `previews/`：
  - 各滤镜通道经 MTF 自适应中点拉伸的 16-bit PNG 预览图。
- `diagnostics/`：
  - 包含 `composition_manifest.json`（对齐单应矩阵、溯源 SHA-256 与质量警告）以及 Siril 转换和配准执行日志。
- `README.md`：
  - 本使用说明。
"""



def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_pixels(path):
    """Validate the whole Float32 primary image with bounded memory."""
    header, warnings = read_fits_header_with_warnings(str(path))
    if header.get("BITPIX") != -32 or header.get("NAXIS") not in (2, 3):
        raise FitsError("%s: expected a Float32 mono or RGB master" % path)
    planes = 1 if header["NAXIS"] == 2 else header.get("NAXIS3")
    width, height = header.get("NAXIS1"), header.get("NAXIS2")
    if planes not in (1, 3) or any(
        type(v) is not int or v < 1 for v in (width, height)
    ):
        raise FitsError("%s: invalid image dimensions" % path)
    scale, zero = header.get("BSCALE", 1), header.get("BZERO", 0)
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (scale, zero)):
        raise FitsError("%s: invalid FITS scaling" % path)
    count = width * height * planes
    remaining = count * 4
    zeros = 0
    minimum, maximum = math.inf, -math.inf
    with open(path, "rb") as handle:
        handle.seek(data_offset(str(path)))
        while remaining:
            block = handle.read(min(remaining, 1024 * 1024))
            if not block or len(block) % 4:
                raise FitsError("%s: truncated Float32 primary image" % path)
            remaining -= len(block)
            for (stored,) in struct.iter_unpack(">f", block):
                value = stored * scale + zero
                if not math.isfinite(value):
                    raise FitsError("%s: non-finite pixel" % path)
                zeros += value == 0
                minimum = min(minimum, value)
                maximum = max(maximum, value)
    return header, {
        "pixel_count": count, "all_pixels_finite": True,
        "zero_pct": 100 * zeros / count, "min": minimum, "max": maximum,
        "medians": estimate_channel_medians(str(path)),
    }, warnings


def collect_inputs(run_dir):
    layers = []
    for report_path in sorted(Path(run_dir).glob("*/*_report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if report.get("status") != "ok" or "commit" not in report.get("completed_stages", []):
            raise FitsError("%s: not a completed successful stack" % report_path)
        source = Path(report.get("artifacts", {}).get("master") or "")
        if not source.is_absolute() or not source.is_file():
            raise FitsError("%s: missing published master" % report_path)
        header, pixels, warnings = inspect_pixels(source)
        label = str(header.get("FILTER") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]*", label):
            raise FitsError("%s: missing or unsafe FILTER label" % source)
        if not str(header.get("OBJECT") or "").strip():
            raise FitsError("%s: missing OBJECT" % source)
        for key in ("FILTER", "OBJECT"):
            text = header[key]
            if not isinstance(text, str) or any(ord(ch) < 32 or ord(ch) > 126 or ch == '"' for ch in text):
                raise FitsError("%s: unsupported characters in %s" % (source, key))
        count = header.get("STACKCNT")
        if type(count) is not int or count < 1 or count != report.get("stacking", {}).get("frames_stacked"):
            raise FitsError("%s: STACKCNT disagrees with stack report" % source)
        for key in ("EXPTIME", "LIVETIME"):
            value = header.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise FitsError("%s: missing or invalid %s" % (source, key))
        quality_warnings = list(dict.fromkeys(
            warnings + report.get("warnings", [])
            + (report.get("calibration") or {}).get("warnings", [])
        ))
        if any(median <= 0 for median in pixels["medians"].values()):
            quality_warnings.append("nonpositive whole-image median; inspect calibration and clipping")
        if count < 3:
            quality_warnings.append("fewer than three stacked frames; pixel rejection is unreliable")
        preview_path = report.get("artifacts", {}).get("preview")
        preview_source = (
            str(Path(preview_path).resolve())
            if preview_path and Path(preview_path).is_file()
            else None
        )
        layers.append({
            "filter": label, "source": str(source.resolve()),
            "source_report": str(report_path.resolve()),
            "source_sha256": sha256_file(source),
            "source_report_sha256": sha256_file(report_path),
            "preview_source": preview_source,
            "header": header, "source_pixels": pixels,
            "quality_warnings": quality_warnings,
            "conversion": "equal_mean_rgb" if header["NAXIS"] == 3 else "mono_copy",
        })
    if not 2 <= len(layers) <= 8:
        raise FitsError("expected 2 to 8 successful filter reports in immediate subdirectories")
    if len({layer["filter"] for layer in layers}) != len(layers):
        raise FitsError("duplicate FILTER in stack reports")
    if len({str(layer["header"]["OBJECT"]).strip() for layer in layers}) != 1:
        raise FitsError("masters have different OBJECT values")
    return sorted(layers, key=lambda layer: (
        FILTER_ORDER.index(layer["filter"]) if layer["filter"] in FILTER_ORDER else 8,
        layer["filter"],
    ))


def run_script(siril, directory, name, builder, timeout):
    script = directory / (name + ".sir")
    builder.write(str(script))
    # Stream logs so a timeout still leaves the last completed operation.
    with (directory / (name + ".log")).open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [siril, "-d", str(directory), "-s", str(script)],
            stdout=log, stderr=subprocess.STDOUT,
            timeout=timeout, env=dict(os.environ, LC_ALL="C"),
        )
    if result.returncode:
        raise RuntimeError("%s failed (exit %d); see %s.log" % (name, result.returncode, name))


def prepare_mono(siril, work, layers, timeout):
    conversion = work / "conversion"
    mono = work / "mono"
    conversion.mkdir()
    mono.mkdir()
    builder = ScriptBuilder()
    builder.add("setext", ["fit"])
    builder.add("set32bits")
    for index, layer in enumerate(layers):
        alias = "source_%04d.fit" % index
        (conversion / alias).symlink_to(layer["source"])
        builder.add("load", [alias])
        if layer["header"]["NAXIS"] == 3:
            names = ["plane_%d_%s" % (index, channel) for channel in ("r", "g", "b")]
            builder.add("split", names)
            builder.add("pm", ["(" + " + ".join("$%s$" % name for name in names) + ") / 3", "-nosum"])
        if layer["header"].get("BAYERPAT"):
            builder.add("update_key", ["-delete", "BAYERPAT"])
        # SPLIT may replace FILTER with a plane name; restore source provenance.
        for key in EXPOSURE_KEYS:
            value = layer["header"][key]
            if isinstance(value, str):
                value = "'" + value.replace("'", "''") + "'"
            builder.add("update_key", [key, str(value)])
        builder.add("save", ["../mono/channel%04d" % index])
    run_script(siril, conversion, "prepare_mono", builder, timeout)
    for index, layer in enumerate(layers):
        header, pixels, _ = inspect_pixels(mono / ("channel%04d.fit" % index))
        validate_metadata(layer, header)
        layer["mono_pixels"] = pixels
    return mono


def validate_metadata(layer, header):
    if header.get("BITPIX") != -32 or header.get("NAXIS") != 2 or header.get("BAYERPAT"):
        raise FitsError("%s: composition image must be Float32 mono without BAYERPAT" % layer["filter"])
    for key in EXPOSURE_KEYS:
        if header.get(key) != layer["header"].get(key):
            raise FitsError("%s: %s changed during preparation" % (layer["filter"], key))


def validate_registration(regdata, count):
    frames = regdata["frames"]
    if regdata["warnings"] or len(frames) != count or sorted(f["index"] for f in frames) != list(range(count)):
        raise FitsError("registration did not return one valid transform per filter")
    if regdata["reference_image"] not in range(count):
        raise FitsError("registration has no valid reference image")
    for frame in frames:
        matrix = frame["h"]
        if not all(math.isfinite(value) for value in matrix) or matrix[8] == 0:
            raise FitsError("registration contains an invalid homography")
        if matrix[0] * matrix[4] - matrix[1] * matrix[3] <= 0:
            raise FitsError("registration contains a degenerate homography")
    return all(all(abs(a / frame["h"][8] - b) < 1e-10 for a, b in zip(frame["h"], IDENTITY)) for frame in frames)


def align_layers(siril, work, layers, mono, timeout):
    alignment = work / "alignment"
    alignment.mkdir()
    for index in range(len(layers)):
        (alignment / ("channel%04d.fit" % index)).symlink_to(mono / ("channel%04d.fit" % index))
    builder = ScriptBuilder()
    builder.add("register", ["channel", "-2pass"])
    run_script(siril, alignment, "register", builder, timeout)
    regdata = read_seq_regdata(str(alignment / "channel.seq"))
    identity = validate_registration(regdata, len(layers))
    builder = ScriptBuilder(registration_available=True)
    builder.add("setext", ["fit"])
    builder.add("set32bits")
    if identity:
        width = min(layer["header"]["NAXIS1"] for layer in layers)
        height = min(layer["header"]["NAXIS2"] for layer in layers)
        for index, layer in enumerate(layers):
            builder.add("load", ["channel%04d.fit" % index])
            if (layer["header"]["NAXIS1"], layer["header"]["NAXIS2"]) != (width, height):
                builder.add("crop", ["0", "0", str(width), str(height)])
            builder.add("save", ["../" + layer["filter"]])
    else:
        builder.add("seqapplyreg", ["channel", "-prefix=aligned_", "-framing=min"])
    run_script(siril, alignment, "apply", builder, timeout)
    if not identity:
        exported = sorted(alignment.glob("aligned_channel*.fit"))
        expected = {alignment / ("aligned_channel%04d.fit" % index) for index in range(len(layers))}
        if set(exported) != expected:
            raise FitsError("exported filter list disagrees with input filter mapping")
        for index, layer in enumerate(layers):
            shutil.move(str(alignment / ("aligned_channel%04d.fit" % index)), work / (layer["filter"] + ".fit"))
    return regdata, identity


def publish(work, out):
    if out.exists() or out.is_symlink():
        raise FileExistsError("output already exists: %s" % out)
    os.rename(work, out)


def prepare(args):
    out = Path(args.out).absolute()
    if out.exists() or out.is_symlink():
        raise FileExistsError("output already exists: %s" % out)
    if not args.monochrome:
        raise FitsError("--monochrome is required to confirm a monochrome sensor")
    siril = find_siril(args.siril)
    if not siril:
        raise FitsError("Siril CLI was not found")
    version = siril_version(siril) or ""
    if not re.search(r"\b1\.4\.", version):
        raise FitsError("expected Siril 1.4.x, found %s" % version)
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".composition-", dir=str(out.parent)))
    manifest = {
        "schema_version": "1.0", "status": "failed", "failed_stage": "collect",
        "run_dir": str(Path(args.run_dir).absolute()),
        "monochrome_confirmed": True, "siril": {"path": siril, "version": version},
    }
    try:
        layers = collect_inputs(args.run_dir)
        print("[composition] collect: %d filters" % len(layers), file=sys.stderr, flush=True)
        manifest["failed_stage"] = "mono"
        mono = prepare_mono(siril, work, layers, args.timeout)
        print("[composition] mono: complete", file=sys.stderr, flush=True)
        manifest["failed_stage"] = "registration"
        regdata, identity = align_layers(siril, work, layers, mono, args.timeout)
        manifest["reference_filter"] = layers[regdata["reference_image"]]["filter"]
        manifest["framing"] = "min"
        manifest["identity_transforms"] = identity
        manifest["failed_stage"] = "verify"
        shapes = set()
        for index, layer in enumerate(layers):
            target = work / (layer["filter"] + ".fit")
            header, pixels, warnings = inspect_pixels(target)
            validate_metadata(layer, header)
            shapes.add((header["NAXIS1"], header["NAXIS2"]))
            layer["output"] = str(out / target.name)
            layer["output_pixels"] = pixels
            layer["transform"] = regdata["frames"][index]["h"]
            layer["quality_warnings"] = list(dict.fromkeys(layer["quality_warnings"] + warnings))
            layer["quality_status"] = "warning" if layer["quality_warnings"] else "not_assessed"
            layer["format_ready"] = True
            if sha256_file(layer["source"]) != layer["source_sha256"] or sha256_file(layer["source_report"]) != layer["source_report_sha256"]:
                raise FitsError("source master or report changed during preparation")
        if len(shapes) != 1:
            raise FitsError("composition images do not share a common shape")
        manifest["output_size"] = list(shapes.pop())
        manifest["layers"] = layers
        diagnostics = work / "diagnostics"
        diagnostics.mkdir()
        for directory in (work / "conversion", work / "alignment"):
            if directory.exists():
                for path in directory.iterdir():
                    if path.suffix in (".sir", ".log", ".seq"):
                        shutil.move(str(path), diagnostics / path.name)
        for directory in (work / "conversion", work / "alignment", work / "mono"):
            if directory.exists():
                shutil.rmtree(directory)

        is_bundle = bool(getattr(args, "bundle", False))
        manifest["bundle"] = is_bundle

        prev_dir = work / "previews"
        has_previews = False
        for layer in layers:
            src_prev = layer.get("preview_source")
            if src_prev and Path(src_prev).is_file():
                if not has_previews:
                    prev_dir.mkdir(exist_ok=True)
                    has_previews = True
                dst_prev = prev_dir / ("%s_preview.png" % layer["filter"])
                shutil.copy2(str(src_prev), str(dst_prev))
                layer["preview"] = str(out / "previews" / dst_prev.name)

        if is_bundle:
            (work / "README.md").write_text(BUNDLE_ROOT_README, encoding="utf-8")
            manifest["status"] = "ok"
            manifest["failed_stage"] = None
            manifest["manifest_path"] = str(out / "diagnostics" / "composition_manifest.json")
            write_report(str(diagnostics / "composition_manifest.json"), manifest)
        else:
            manifest["status"] = "ok"
            manifest["failed_stage"] = None
            manifest["manifest_path"] = str(out / "composition_manifest.json")
            write_report(str(work / "composition_manifest.json"), manifest)

        manifest["failed_stage"] = "publish"
        publish(work, out)
        manifest["failed_stage"] = None
        report_msg = (
            str(out / "diagnostics" / "composition_manifest.json")
            if is_bundle
            else str(out / "composition_manifest.json")
        )
        print("[composition] OK: %s" % report_msg, file=sys.stderr)
        return manifest
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["error"] = "%s: %s" % (type(exc).__name__, exc)
        manifest["work_dir"] = str(work)
        if work.exists():
            write_report(str(work / "failure.json"), manifest)
        print("[composition] FAILED: %s; work=%s" % (exc, work), file=sys.stderr)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="directory containing per-filter report subdirectories")
    parser.add_argument("--out", required=True, help="new directory for composition inputs")
    parser.add_argument("--monochrome", action="store_true", help="confirm a monochrome sensor")
    parser.add_argument(
        "--bundle", action="store_true",
        help="package into a structured 3-tier delivery bundle "
             "(01_composition_inputs, 02_previews, 03_raw_masters_archive)",
    )
    parser.add_argument("--siril", help="explicit Siril CLI executable")
    parser.add_argument("--timeout", type=int, default=3600, help="timeout per Siril invocation")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        prepare(args)
    except (Exception, KeyboardInterrupt) as exc:
        print("[composition] %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
