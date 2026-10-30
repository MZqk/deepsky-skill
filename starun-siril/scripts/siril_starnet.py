#!/usr/bin/env python3
# Copyright (C) 2012-2026 team free-astro (see more in upstream AUTHORS)
# Copyright (C) 2026 StarNetAstro contributors
# Copyright (C) 2026 MZqk (CLI integration modifications)
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned, offline, single-image StarNet bridge. See starnet-source.json.

Upstream pixel conversion, TIFF handoff and MTF functions below are unchanged.
The bridge never installs packages, discovers settings, updates, or emits masks.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from deep_sky_siril_contract import (
    atomic_write_json, fingerprint, fingerprint_matches, load_json,
    resource_fingerprint_matches, sha256_file,
)

AS_DEFAULT_SHADOWS_CLIPPING = -2.80
AS_DEFAULT_TARGET_BACKGROUND = 0.25
MAD_NORM = 1.4826
CONTRACT_SCHEMA = "starun-siril.starnet-contract.v1"
RECEIPT_SCHEMA = "starun-siril.starnet-receipt.v1"
DEPENDENCIES = ("sirilpy", "numpy", "tifffile", "imagecodecs", "requests", "packaging")


def probe_runtime():
    global np, tifffile
    records = {}
    for name in DEPENDENCIES:
        module = importlib.import_module(name)
        records[name] = {"version": importlib.metadata.version(name),
                         "module": fingerprint(Path(module.__file__).resolve())}
    if records["sirilpy"]["version"] != "1.0.25":
        raise ValueError("StarNet bridge requires sirilpy ==1.0.25")
    np = importlib.import_module("numpy")
    tifffile = importlib.import_module("tifffile")
    return {"status": "verified", "python_version": sys.version.split()[0],
            "interpreter": fingerprint(Path(sys.executable).resolve()), "dependencies": records}


def validate_contract(document, source, runtime, session, run_id, *, replaying=False):
    if not isinstance(document, dict) or set(document) != {"schema", "source", "runtime", "stride", "work_dir"}:
        raise ValueError("Invalid StarNet contract fields")
    if document["schema"] != CONTRACT_SCHEMA or document["source"] != {"path": str(source), "sha256": sha256_file(source)}:
        raise ValueError("StarNet contract source changed")
    if document["runtime"] != runtime or runtime.get("compatible") is not True:
        raise ValueError("StarNet contract differs from the frozen runtime")
    if type(document["stride"]) is not int or document["stride"] not in (128, 256, 512):
        raise ValueError("StarNet stride must be 128, 256 or 512")
    work = Path(document["work_dir"])
    expected = session / "runtime" / "starnet" / run_id
    if not work.is_absolute() or work != expected or work.resolve() != expected or (work.exists() and not replaying) or work.is_symlink():
        raise ValueError("StarNet work directory is unsafe or already exists")
    if not fingerprint_matches(runtime.get("executable")) or not resource_fingerprint_matches(runtime.get("model")):
        raise ValueError("StarNet executable/model drift")
    if not fingerprint_matches(runtime.get("adapter")) or not fingerprint_matches(runtime.get("source_lock")):
        raise ValueError("StarNet adapter/source lock drift")
    frozen = runtime.get("python", {})
    if frozen.get("status") != "verified" or not fingerprint_matches(frozen.get("interpreter")):
        raise ValueError("StarNet Python runtime is unavailable or changed")
    dependencies = frozen.get("dependencies", {})
    if set(dependencies) != set(DEPENDENCIES) or not all(fingerprint_matches(r.get("module")) for r in dependencies.values()):
        raise ValueError("StarNet Python dependency drift")
    return work


def siril_to_working(arr):
    """Convert a Siril pixel array to (H, W, C) float32 in [0, 1].

    Returns (working, meta) where meta records how to convert back.
    Float images whose peak exceeds 1.0 are rescaled to avoid clipping the
    highlights when written to an integer TIFF (mirrors the C StarNet tool).
    """
    is_uint16 = (arr.dtype == np.uint16)
    if arr.ndim == 2:
        mono = True
        hwc = arr[:, :, np.newaxis].astype(np.float32)
    elif arr.ndim == 3:
        mono = (arr.shape[0] == 1)
        hwc = np.transpose(arr, (1, 2, 0)).astype(np.float32)
    else:
        raise ValueError(f"Unsupported pixel data shape: {arr.shape}")

    rescaled = False
    if is_uint16:
        hwc /= 65535.0
    else:
        scale = 1.0
        peak = float(hwc.max()) if hwc.size else 1.0
        if peak > 1.0:
            scale = peak
            hwc /= scale
            rescaled = True
    hwc = np.clip(hwc, 0.0, 1.0)
    meta = {
        "mono": mono,
        "is_uint16": is_uint16,
        "rescaled": rescaled,
        "scale": 1.0 if is_uint16 else scale,
    }
    return hwc, meta

def working_to_siril(hwc, meta, force_uint16=False):
    """Convert an (H, W, C) float32 [0, 1] array back to Siril's layout/dtype.

    The output dtype matches the original image (meta['is_uint16']) unless
    force_uint16 is set (Siril's core.force_16bit preference), in which case it
    is always saved as 16-bit."""
    hwc = np.clip(hwc, 0.0, 1.0)
    if meta["mono"]:
        out = hwc[:, :, 0]
    else:
        out = np.transpose(hwc, (2, 0, 1))
    if meta["is_uint16"] or force_uint16:
        out = (out * 65535.0 + 0.5).astype(np.uint16)
    else:
        out = out * float(meta.get("scale", 1.0))
        out = out.astype(np.float32)
    return np.ascontiguousarray(out)

def tiff_handoff_is_mono(hwc):
    """Match CLI grayscale detection after the actual 16-bit TIFF conversion."""
    if hwc.shape[2] == 1:
        return True
    if hwc.shape[2] != 3:
        return False

    def plane(channel):
        return (np.clip(hwc[:, :, channel], 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)

    first = plane(0)
    return all(np.array_equal(first, plane(channel)) for channel in (1, 2))

def write_starnet_tiff(path, hwc):
    """Write an (H, W, C) float [0, 1] array as a 16-bit integer TIFF for StarNet."""
    a = hwc[:, :, 0] if hwc.shape[2] == 1 else hwc
    a = (np.clip(a, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    tifffile.imwrite(path, a, compression="lzw")

def read_image_tiff(path):
    """Read a TIFF written by StarNet, returning (H, W, C) float [0, 1]."""
    a = tifffile.imread(path)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    elif a.dtype == np.uint16:
        a = a.astype(np.float32) / 65535.0
    else:
        a = a.astype(np.float32)
    if a.ndim == 2:
        a = a[:, :, np.newaxis]
    return np.clip(a, 0.0, 1.0)

def _mtf(x, m, lo, hi):
    """Midtones transfer function (scalar or ndarray x)."""
    rng = hi - lo
    xp = np.clip((x - lo) / rng, 0.0, 1.0) if rng > 0 else np.zeros_like(x)
    # Positive-term form avoids cancellation; explicit endpoints also cover
    # degenerate m=0/1 without evaluating 0/0.
    num = (1.0 - m) * xp
    denom = num + m * (1.0 - xp)
    result = np.divide(num, denom, out=np.zeros_like(xp), where=denom != 0)
    return np.where(xp >= 1.0, 1.0, result)

def _mtf_scalar(x, m, lo, hi):
    if x <= lo:
        return 0.0
    if x >= hi:
        return 1.0
    xp = (x - lo) / (hi - lo)
    return (1.0 - m) * xp / ((1.0 - m) * xp + m * (1.0 - xp))

def compute_unlinked_mtf_params(hwc):
    """Per-channel (shadows, midtones, highlights), matching Siril's default
    unlinked autostretch (find_unlinked_midtones_balance_default)."""
    nb = hwc.shape[2]
    medians = [float(np.median(hwc[:, :, c])) for c in range(nb)]
    inverted = sum(1 for med in medians if med > 0.5)
    use_inverted = (inverted >= nb)

    params = []
    for c in range(nb):
        chan = hwc[:, :, c]
        if np.min(chan) == np.max(chan):
            # A constant channel has no background distribution to estimate.
            # Identity is finite, invertible and preserves black/white exactly.
            params.append((0.0, 0.5, 1.0))
            continue
        median = medians[c]
        mad = float(np.median(np.abs(chan - median))) * MAD_NORM
        if mad == 0.0:
            mad = 0.001
        if not use_inverted:
            c0 = median + AS_DEFAULT_SHADOWS_CLIPPING * mad
            if c0 < 0.0:
                c0 = 0.0
            m2 = median - c0
            midtones = _mtf_scalar(m2, AS_DEFAULT_TARGET_BACKGROUND, 0.0, 1.0)
            params.append((c0, midtones, 1.0))
        else:
            c1 = median - AS_DEFAULT_SHADOWS_CLIPPING * mad
            if c1 > 1.0:
                c1 = 1.0
            m2 = c1 - median
            midtones = 1.0 - _mtf_scalar(m2, AS_DEFAULT_TARGET_BACKGROUND, 0.0, 1.0)
            params.append((0.0, midtones, c1))
    return params

def apply_unlinked_mtf(hwc, params):
    out = np.empty_like(hwc)
    for c, (lo, m, hi) in enumerate(params):
        out[:, :, c] = _mtf(hwc[:, :, c], m, lo, hi)
    return out

def apply_unlinked_pseudoinverse_mtf(hwc, params):
    """Approximate inverse of apply_unlinked_mtf, used to reverse the
    pre-stretch on the starless image (matches Siril's MTF_pseudoinverse)."""
    out = np.empty_like(hwc)
    for c, (shadows, midtones, highlights) in enumerate(params):
        y = np.clip(hwc[:, :, c].astype(np.float64), 0.0, 1.0)
        num = midtones * y
        den = num + (1.0 - midtones) * (1.0 - y)
        x = np.divide(num, den, out=np.zeros_like(y), where=den != 0)
        x = np.where(y >= 1.0, 1.0, x)
        out[:, :, c] = shadows + (highlights - shadows) * x
    return np.clip(out, 0.0, 1.0)

def build_starnet_argv(exe, in_tiff, out_tiff, *, stride=None, upsample=False,
                       weights=None, mask_tiff=None, unscreen_tiff=None,
                       protect_highlights=True):
    argv = [exe, "-i", in_tiff, "-o", out_tiff]
    if stride is not None:
        argv += ["-s", str(stride)]
    if upsample:
        argv.append("-u")
    if weights:
        argv += ["-w", weights]
    if mask_tiff:
        argv += ["-m", mask_tiff]
    if unscreen_tiff:
        argv += ["-n", unscreen_tiff]
    if not protect_highlights:
        argv.append("-d")
    return argv

def process_starless(arr, runtime, stride, work, log):
    """Upstream single-image math with fixed options and session-local I/O."""
    if arr.ndim not in (2, 3) or (arr.ndim == 3 and arr.shape[0] not in (1, 3)) or not np.isfinite(arr).all():
        raise ValueError("StarNet requires finite mono or RGB pixels")
    working, meta = siril_to_working(arr)
    params = compute_unlinked_mtf_params(working)
    stretched = apply_unlinked_mtf(working, params)
    handoff_mono = tiff_handoff_is_mono(stretched)
    work.mkdir(parents=True, exist_ok=False)
    temporary = Path(tempfile.mkdtemp(prefix="handoff-", dir=work))
    input_tiff, output_tiff = temporary / "input.tif", temporary / "starless.tif"
    write_starnet_tiff(input_tiff, stretched)
    argv = build_starnet_argv(runtime["executable"]["path"], str(input_tiff), str(output_tiff),
                             stride=stride, weights=runtime["model"]["path"])
    completed = subprocess.run(argv, cwd=temporary, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, errors="replace", check=False)
    log(completed.stdout)
    if completed.returncode or not output_tiff.is_file() or output_tiff.is_symlink():
        raise ValueError(f"StarNet failed with exit code {completed.returncode}")
    if {p.name for p in temporary.iterdir()} != {"input.tif", "starless.tif"}:
        raise ValueError("StarNet wrote undeclared temporary outputs")
    starless = read_image_tiff(output_tiff)
    if starless.shape[:2] != stretched.shape[:2] or starless.shape[2] not in (1, 3) or not np.isfinite(starless).all():
        raise ValueError("StarNet output geometry or finite-value check failed")
    if meta["mono"] and starless.shape[2] == 3:
        starless = starless[:, :, :1]
    if not meta["mono"] and starless.shape[2] == 1:
        if not handoff_mono:
            raise ValueError("StarNet returned mono for a colour RGB handoff")
        starless = np.repeat(starless, 3, axis=2)
    output = working_to_siril(apply_unlinked_pseudoinverse_mtf(starless, params), meta)
    if not np.isfinite(output).all():
        raise ValueError("StarNet inverse MTF produced nonfinite pixels")
    evidence = {"mtf_parameters": params, "normalization": meta,
                "input_negative_count": int(np.count_nonzero(arr < 0)),
                "handoff_bits": 16, "shape": list(output.shape), "argv": argv}
    return output, evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--contract")
    parser.add_argument("--contract-sha256")
    parser.add_argument("--expected-source")
    parser.add_argument("--receipt", required=True)
    args = parser.parse_args(argv)
    receipt = Path(args.receipt).resolve()
    if receipt.exists():
        raise ValueError("StarNet receipt already exists")
    actual_runtime = probe_runtime()
    if args.probe:
        if args.contract or args.contract_sha256 or args.expected_source:
            raise ValueError("Runtime probe accepts no processing contract")
        atomic_write_json(receipt, actual_runtime)
        return 0
    if not all((args.contract, args.contract_sha256, args.expected_source)):
        raise ValueError("StarNet processing requires a hash-bound contract")
    contract_path = Path(args.contract).resolve()
    if sha256_file(contract_path) != args.contract_sha256:
        raise ValueError("StarNet contract hash changed")
    document = load_json(contract_path)
    source = Path(args.expected_source).resolve()
    if receipt.parent != contract_path.parent:
        raise ValueError("StarNet receipt must stay beside its contract")
    session = contract_path.parents[2]
    runtime = document["runtime"]
    work = validate_contract(document, source, runtime, session, contract_path.parent.name)
    if actual_runtime != runtime["python"]:
        raise ValueError("StarNet Python dependency version drift")
    import sirilpy as s
    siril = s.SirilInterface()
    siril.connect()
    try:
        if Path(siril.get_image_filename()).resolve() != source:
            raise ValueError("Siril loaded image differs from the declared source")
        arr = siril.get_image_pixeldata()
        output, evidence = process_starless(arr, runtime, document["stride"], work, siril.log)
        if probe_runtime() != actual_runtime or sha256_file(source) != document["source"]["sha256"] or not fingerprint_matches(runtime["executable"]) or not resource_fingerprint_matches(runtime["model"]):
            raise ValueError("StarNet runtime or source changed during processing")
        with siril.image_lock():
            siril.set_image_pixeldata(output)
        atomic_write_json(receipt, {"schema": RECEIPT_SCHEMA, "status": "verified",
                          "contract_sha256": args.contract_sha256, "source": document["source"],
                          "runtime": runtime, "stride": document["stride"], **evidence})
    finally:
        siril.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
