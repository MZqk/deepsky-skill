#!/usr/bin/env python3
"""Moon / planetary lucky-imaging stacking driver (Unified Facade & CLI Entry).

Architecture:
  Siril 1.4.4 CLI acts as the main processing backbone:
    - RAW decoding & demosaicing (convert -debayer) / FITS sequence linking (link)
    - Multithreaded subpixel bicubic resampling & common area cropping (seqapplyreg -framing=min -interp=cu)
    - Winsorized sigma clipping stacking (stack rej w 3 3 -norm=addscale -filter-included)
    - Multiscale 'à trous' B-Spline wavelet reconstruction & unsharp mask (wavelet / wrecons / unsharp)
    - Mineral moon chromatic alignment & saturation boost (rmgreen / satu)
    - Professional 32-bit FITS / 16-bit TIFF / JPG exports (savetif / savejpg)

  Modularized Subsystems:
    - moon_common: Logging, I/O, Siril runner, pixel scaling, device priors, disk budgeting.
    - moon_optics: Lunar limb physical circle fitting, angular diameter, optical parameter inference.
    - moon_register: Subpixel FFT phase correlation, high-contrast ROI localization, homography injection.
    - moon_import: RAW/FITS/SER/AVI video decoding, seeing-profile quality scanning, auto-cropping.
    - moon_stacking: Frame probing, drizzle supersampling heuristics, Winsorized stacking & retry ladder.
    - moon_wavelets: Multiscale B-spline decomposition/reconstruction, anti-ringing damping masks.
    - moon_sharp_guard: Artifact detection (DHR, CSI, GKM), MTF midtone solver, adaptive sharpening.
    - moon_viewer: Isometric reference rendering and interactive HTML dual-window comparison viewer.
    - moon_verify: Delivered product sharp guard verification, background QC, candidate feedback loop.
    - moon_postprocess: Pedestal calibration, Gray-World WB, atmospheric extinction compensation, LRGB/Mineral.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

# Ensure script directory is on sys.path for robust submodule resolution
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# ---------------------------------------------------------------------------
# Fallback Defaults and Pure-Python Primitives (Safe in python -S)
# ---------------------------------------------------------------------------
DEFAULT_SIRIL = "/Applications/Siril.app/Contents/MacOS/siril-cli"
RAW_EXTS = {".orf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raw", ".rw2", ".pef", ".srf"}
FIT_EXTS = {".fit", ".fits", ".fit.gz", ".fits.gz"}
SER_EXTS = {".ser"}
VIDEO_EXTS = {".avi", ".mp4", ".mov", ".mkv", ".m4v"}
DRIZZLE_KERNELS = ("point", "turbo", "square", "gaussian", "lanczos2", "lanczos3")
DRIZZLE_AIRY_THRESHOLD_PX = 1.5
DRIZZLE_PSF_THRESHOLD_PX = 2.0


def log(msg: str) -> None:
    print(f"[moon-stack] {msg}")


def die(msg: str):
    print(f"[moon-stack] error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def resolve_siril(siril: str) -> str | None:
    if shutil.which(siril):
        return siril
    if os.path.isabs(siril) and os.access(siril, os.X_OK):
        return siril
    candidates = [
        "/Applications/Siril.app/Contents/MacOS/siril-cli",
        os.path.expanduser("~/Applications/Siril.app/Contents/MacOS/siril-cli"),
        "/usr/local/bin/siril-cli",
        "/usr/bin/siril-cli",
        "/opt/homebrew/bin/siril-cli",
    ]
    for c in candidates:
        if os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None


try:
    from device_specs import (
        DEVICE_SPECS,
        EXCLUDED_DEVICES,
        SENSOR_STATUS,
        SOURCE_GRADES,
        get_device,
        identify_device,
        list_devices,
    )
except Exception:
    DEVICE_SPECS = {}
    EXCLUDED_DEVICES = []
    SENSOR_STATUS = []
    SOURCE_GRADES = []
    get_device = lambda *_: None
    identify_device = lambda *_: None
    list_devices = lambda: []

try:
    import numpy as np
except Exception as exc:
    np = None
    _numpy_import_error = f"{type(exc).__name__}: {exc}"
else:
    _numpy_import_error = None


# ---------------------------------------------------------------------------
# Submodule Imports & Symbol Re-exports (Dynamic & Tolerant)
# ---------------------------------------------------------------------------
_SUBMODULES: list[Any] = []

try:
    import device_specs
    _SUBMODULES.append(device_specs)
    import moon_common
    _SUBMODULES.append(moon_common)
    import moon_optics
    _SUBMODULES.append(moon_optics)
    import moon_register
    _SUBMODULES.append(moon_register)
    import moon_import
    _SUBMODULES.append(moon_import)
    import moon_stacking
    _SUBMODULES.append(moon_stacking)
    import moon_wavelets
    _SUBMODULES.append(moon_wavelets)
    import moon_sharp_guard
    _SUBMODULES.append(moon_sharp_guard)
    import moon_viewer
    _SUBMODULES.append(moon_viewer)
    import moon_verify
    _SUBMODULES.append(moon_verify)
    import moon_postprocess
    _SUBMODULES.append(moon_postprocess)

    for _mod in _SUBMODULES:
        for _k, _v in _mod.__dict__.items():
            if not _k.startswith("__"):
                globals()[_k] = _v
except Exception:
    pass


# ---------------------------------------------------------------------- Top CLI Commands

def cmd_all(args) -> None:
    if getattr(args, "candidate_mode", "single") == "feedback":
        if getattr(args, "sample_mode", "adaptive") in ("head", "window"):
            die("feedback is only available for quality-selected video modes")
        _candidate_feedback(args)
    else:
        cmd_import(args)
        cmd_register(args)
        cmd_stack(args)
    cmd_postprocess(args)
    cmd_verify(args)


def cmd_devices(args) -> None:
    """Print the built-in smart-telescope specification table as JSON."""
    wanted = getattr(args, "id", None)
    if wanted:
        spec = get_device(wanted)
        if spec is None:
            die(f"unknown device id '{wanted}'; run `moon_stack.py devices` to list all ids")
        print(json.dumps(spec, indent=2, ensure_ascii=False))
        return

    payload = {
        "count": len(DEVICE_SPECS),
        "source_grades": list(SOURCE_GRADES),
        "sensor_status": list(SENSOR_STATUS),
        "devices": list_devices(),
        "excluded": EXCLUDED_DEVICES,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_probe(args) -> None:
    errors, warnings = [], []
    executable = resolve_siril(args.siril)
    info = {
        "siril": args.siril,
        "siril_exists": bool(executable) or os.path.exists(args.siril),
        "siril_resolved": executable,
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
    }
    Version, requirements = None, None
    try:
        from packaging.requirements import Requirement
        from packaging.version import InvalidVersion, Version
    except Exception as exc:
        errors.append(f"Dependency versions unverified: {type(exc).__name__}: {exc}")
    else:
        try:
            requirements = {}
            for line in (Path(__file__).resolve().parent.parent / "requirements.txt").read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    requirement = Requirement(line)
                    name = requirement.name.lower().replace("_", "-")
                    if name in requirements:
                        raise ValueError(f"duplicate dependency requirement: {name}")
                    requirements[name] = requirement
        except Exception as exc:
            requirements = None
            errors.append(f"Dependency versions unverified: {type(exc).__name__}: {exc}")

    version_text = None
    if info["siril_exists"]:
        info["siril_version"] = []
    if not executable:
        errors.append(f"Siril CLI not found or not executable: {args.siril}")
    else:
        try:
            p = subprocess.run([executable, "-v"], capture_output=True, text=True,
                               env=dict(os.environ, LANG="C"), timeout=60)
            output = (p.stdout or "") + "\n" + (p.stderr or "")
            info["siril_version"] = (p.stdout or p.stderr or "").strip().splitlines()[-1:]
            if p.returncode:
                errors.append(f"Siril version command failed with exit code {p.returncode}")
            else:
                match = re.search(r"\bsiril(?:-cli)?\s+(?:version\s+)?v?([0-9][0-9A-Za-z.!+_-]*)", output, re.I)
                version_text = match.group(1) if match else None
                if not version_text:
                    errors.append("Siril version could not be verified")
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            errors.append(f"Siril version check failed: {type(exc).__name__}: {exc}")

    if version_text:
        if Version is None:
            errors.append("Siril version unverified: version comparison is unavailable")
        else:
            try:
                if Version(version_text) < Version("1.4.4"):
                    errors.append(f"Siril {version_text} is unsupported; version 1.4.4 or newer is required")
            except InvalidVersion:
                errors.append(f"Siril version could not be verified: {version_text}")

    modules = {"numpy": "numpy", "astropy": "astropy", "cv2": "opencv-python-headless", "skimage": "scikit-image"}
    if requirements is not None:
        unknown = sorted(set(requirements) - set(modules.values()))
        if unknown:
            errors.append(f"Dependency requirements unverified: {', '.join(unknown)}")
    for mod, package in modules.items():
        try:
            if mod == "numpy" and np is None:
                raise ImportError(_numpy_import_error)
            m = __import__(mod)
            info[mod] = str(getattr(m, "__version__", "ok"))
        except Exception as exc:
            info[mod] = None
            errors.append(f"{mod} import failed: {type(exc).__name__}: {exc}")
            continue

        if mod == "cv2":
            from importlib import metadata
            try:
                metadata.version("opencv-python-headless")
            except metadata.PackageNotFoundError:
                try:
                    metadata.version("opencv-python")
                    warnings.append("opencv-python is installed instead of opencv-python-headless")
                except metadata.PackageNotFoundError:
                    errors.append("OpenCV distribution unverified: install opencv-python or opencv-python-headless")
                except Exception as exc:
                    errors.append(f"OpenCV distribution unverified: {type(exc).__name__}: {exc}")
            except Exception as exc:
                errors.append(f"OpenCV distribution unverified: {type(exc).__name__}: {exc}")

        if requirements is not None:
            requirement = requirements.get(package)
            if not requirement:
                errors.append(f"Dependency requirement unverified: {package} is missing from requirements.txt")
                continue
            try:
                version = Version(str(info[mod]))
                if not requirement.specifier.contains(version):
                    errors.append(f"{mod} {version} does not satisfy {requirement.specifier}")
            except InvalidVersion:
                errors.append(f"{mod} version could not be verified: {info[mod]}")

    info.update(ready=not errors, errors=errors, warnings=warnings)
    print(json.dumps(info, indent=2))
    if errors:
        raise SystemExit(1)


# ----------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Professional Moon Stacking via Siril CLI + Subpixel Registration Plugin")
    p.add_argument("--siril", default=DEFAULT_SIRIL)
    p.add_argument("--timeout", type=int, default=3600)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("probe")
    a.set_defaults(func=cmd_probe)

    a = sub.add_parser("import")
    a.add_argument("--input", required=True, help="Input directory containing RAW, FITS, SER, or AVI/video frames, or a single SER/AVI/video file")
    a.add_argument("--work", required=True, help="Isolated working directory")
    a.add_argument("--format", default="auto", choices=["auto", "fits", "raw", "ser", "video", "avi", "mp4"], help="Force format selection")
    a.add_argument("--limit", type=int, default=0, help="Import frame cap (0=uncapped; adaptive video mode still filters by quality)")
    a.add_argument("--sample-mode", default="adaptive", choices=["adaptive", "smart-top", "smart-cluster", "window", "head"],
                   help="Video sampling: 'adaptive' (quality-based count, default), 'smart-top' (global top-K), 'smart-cluster' (temporal top-K), 'window'/'head' (consecutive frames)")
    a.add_argument("--probe-stride", type=int, default=1,
                   help="Quality scan stride (default: 1, scores every decoded frame)")
    a.add_argument("--start-frame", type=int, default=0, help="Starting source frame for head/window modes (default: 0)")
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "utility", "mtf-snr", "relative", "percent"],
                   help="Adaptive video import quality filter (default: otsu)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Relative sharpness threshold for adaptive import")
    a.add_argument("--utility-alpha", type=float, default=2.0, help="Sharpness exponent for utility selection")
    a.add_argument("--utility-beta", type=float, default=1.0, help="SNR exponent for utility selection")
    a.add_argument("--keep-percent", type=float, default=None, help="Adaptive import keep percentage; 100 retains all positive-quality frames")
    a.add_argument("--ser-debayer", action=argparse.BooleanOptionalAction, default=True, help="Debayer Bayer SER frames to RGB FITS (default: True)")
    a.add_argument("--force-mono", action="store_true", help="Force extracting video as 1-channel monochrome FITS")
    a.add_argument("--video-debayer", default="auto", choices=["auto", "bggr", "rggb", "grbg", "gbrg", "none"],
                   help="Demosaicing for RAW Bayer video containers (e.g. Seestar RAW.avi): 'auto' (detects Bayer grid, defaults to BGGR), 'bggr', 'rggb', or 'none'")
    a.add_argument("--device", default="auto",
                   help="Capture device for optics priors, e.g. 'seestar-s50', 'dwarf-3'. "
                        "'auto' (default) identifies from FITS header, capture sidecar, filename "
                        "and sensor model; 'none' disables. Run 'devices' to list supported ids")
    a.set_defaults(func=lambda args: _ensure_deps_and_call("cmd_import", args))

    a = sub.add_parser("devices",
                       help="Print the built-in smart-telescope specification table (for agent lookup)")
    a.add_argument("--id", default=None, help="Print a single device id instead of the whole table")
    a.set_defaults(func=cmd_devices)

    a = sub.add_parser("register")
    a.add_argument("--work", required=True)
    a.add_argument("--seq", default="moon_")
    a.add_argument("--roi", type=int, default=1024)
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "utility", "mtf-snr", "relative", "percent"],
                   help="Frame selection mode: 'otsu' (adaptive bimodal seeing clustering, default), 'utility'/'mtf-snr' (MTF-SNR joint utility optimization), 'relative' (relative to reference frame), 'percent' (fixed/tiered percentage)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Minimum sharpness relative to reference frame (0.0 - 1.0) for 'relative' mode (default: 0.75)")
    a.add_argument("--utility-alpha", type=float, default=2.0, help="MTF/contrast weight exponent for 'utility' mode (default: 2.0)")
    a.add_argument("--utility-beta", type=float, default=1.0, help="SNR weight exponent for 'utility' mode (default: 1.0)")
    a.add_argument("--keep-percent", type=float, default=None, help="Frame selection ratio in percent (switches to 'percent' mode if specified)")
    a.add_argument("--min-confidence", type=float, default=0.15)
    a.set_defaults(func=lambda args: _ensure_deps_and_call("cmd_register", args))

    a = sub.add_parser("stack")
    a.add_argument("--work", required=True)
    a.add_argument("--seq", default="moon_")
    a.add_argument("--out", default="moon_master.fit")
    a.add_argument("--framing", default=None, choices=["min", "max", "cog"], help="Framing mode for resampling (default: 'min' for disc, 'max' for tile)")
    a.add_argument("--mosaic-mode", default="disc", choices=["disc", "tile"],
                   help="Mosaic mode: 'disc' (full lunar disc, default) or 'tile' (mosaic panel, auto-enables framing=max)")
    a.add_argument("--interp", default="cu", choices=["cu", "li", "la", "none", "cubic", "linear", "lanczos", "bilinear"],
                   help="Resampling interpolation: 'li'/'linear' (bilinear, conservative, zero overshoot), 'cu'/'cubic' (bicubic, high MTF, default), 'la'/'lanczos' (lanczos4)")
    a.add_argument("--sigma", nargs=2, default=["3", "3"])
    a.add_argument("--norm", default="addscale")
    a.add_argument("--stack-method", default="auto", choices=["auto", "sum", "rej"],
                   help="Stacking method: 'auto' (rejection mean for every source depth, default), "
                        "'rej' (force rejection mean), 'sum' (legacy additive stack - no pixel "
                        "rejection, no weighting, normalised by the image maximum; kept only as an "
                        "explicit fallback)")
    a.add_argument("--weight", default="auto", choices=["auto", "none", "noise"],
                   help="Frame weighting for rejection stacking: 'auto' (noise weighting, default), "
                        "'noise' (weight frames by lower background noise), 'none'. Ignored by 'sum'")
    a.add_argument("--drizzle", default="auto", choices=["auto", "off", "2", "3"],
                   help="HST drizzle supersampling: 'auto' (2x when the theoretical Airy radius is "
                        f"below {DRIZZLE_AIRY_THRESHOLD_PX}px, i.e. an undersampled imaging train), "
                        "'off', or a forced scale. Trades noise and ~4x file size at scale 2 for "
                        "real resolution on undersampled setups")
    a.add_argument("--drizzle-pixfrac", type=float, default=None,
                   help="Drizzle droplet pixel fraction (default: 1/scale, the manual's rule of thumb)")
    a.add_argument("--drizzle-kernel", default="square", choices=list(DRIZZLE_KERNELS),
                   help="Drizzle droplet kernel (default: 'square', flux preserving and robust at any "
                        "scale). 'turbo' is faster but leaves null pixels; lanczos2/3 are only valid "
                        "at scale == pixfrac == 1.0")
    a.add_argument("--drizzle-airy-threshold", type=float, default=None,
                   help=f"Airy-radius threshold in px for the --drizzle auto diffraction criterion "
                        f"(default {DRIZZLE_AIRY_THRESHOLD_PX}); needs focal length, aperture and pixel size")
    a.add_argument("--drizzle-psf-threshold", type=float, default=None,
                   help=f"Measured PSF FWHM threshold in px for the --drizzle auto fallback criterion "
                        f"(default {DRIZZLE_PSF_THRESHOLD_PX}); measured from the lunar limb, needs no metadata")
    a.add_argument("--focal", type=float, default=None,
                   help="Override focal length (mm) for the --drizzle auto decision")
    a.add_argument("--pixel-size", type=float, default=None,
                   help="Override sensor pixel size (um) for the --drizzle auto decision")
    a.add_argument("--aperture", type=float, default=None,
                   help="Override aperture (mm) for the --drizzle auto decision")
    a.set_defaults(func=lambda args: _ensure_deps_and_call("cmd_stack", args))

    a = sub.add_parser("postprocess")
    a.add_argument("--work", required=True)
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--deconv", default="auto", choices=["auto", "sb", "wiener", "rl", "none"], help="Deconvolution method (sb=Split Bregman, wiener, rl, none)")
    a.add_argument("--no-adc", action="store_true", help="Disable Atmospheric Dispersion Correction (RGB channel alignment)")
    a.add_argument("--midtone", type=float, default=None, help="MTF midtone stretch value for natural lunar albedo dynamics (default: None for auto albedo-adaptive estimation)")
    a.add_argument("--wavelet-l1", type=float, default=None, help="Finest native-scale gain; working-grid layer 1 with --wavelet-adapt off")
    a.add_argument("--clahe-clip", type=float, default=None, help="CLAHE clip limit (default: auto dynamic, set <=0 to bypass CLAHE)")
    a.add_argument("--sharp-mode", default="auto", choices=["auto", "mellow", "mild", "crisp", "none"],
                   help="Sharpening mode: 'auto' (balanced natural organic baseline, default), 'mellow' (ultra-soft optical view, no CLAHE), 'mild' (subtle natural), 'crisp' (classic), 'none' (bypass)")
    a.add_argument("--unsharp", type=float, default=None, help="USM unsharp mask amount (default: auto bypassed when wavelets/deconv active)")
    a.add_argument("--sharp-guard", default="auto", choices=["auto", "off"], help="Validate the full sharpening chain and reduce unsafe enhancement (default: auto)")
    a.add_argument("--anti-ringing", default="auto", choices=["auto", "off", "mild", "aggressive"],
                   help="Edge undershoot dark ringing suppression mode: 'auto' (adaptive damping, default), 'mild' (subtle), 'aggressive' (strong), 'off' (bypass)")
    a.add_argument("--damping-factor", type=float, default=None,
                   help="Manual anti-ringing damping factor (0.0 to 1.0, overrides preset if specified)")
    a.add_argument("--aperture", type=float, default=None, help="Telescope aperture in mm (for Airy PSF, default: trusted metadata only)")
    a.add_argument("--focal", type=float, default=None, help="Telescope focal length in mm (for Airy PSF, default: auto inferred from lunar disc/header)")
    a.add_argument("--pixel-size", type=float, default=None, help="Sensor pixel size in microns (for Airy PSF, default: trusted metadata only)")
    a.add_argument("--mineral-mode", default="lrgb", choices=["lrgb", "legacy"],
                   help="Mineral moon processing pipeline: 'lrgb' (Luminance/Chrominance separation, clean details, default) or 'legacy' (monolithic RGB wavelet)")
    a.add_argument("--white-balance", default="gray-world", choices=["gray-world", "legacy"],
                   help="Color balance mode: 'gray-world' (neutral lunar albedo baseline, default) or 'legacy' (per-channel percentile stretch)")
    a.add_argument("--sat-fe", type=float, default=0.8, help="Mineral saturation boost for Fe-rich terrain (orange-yellow hue 1, default: 0.8)")
    a.add_argument("--sat-ti", type=float, default=0.8, help="Mineral saturation boost for Ti-rich basalt (cyan-blue hues 3 & 4, default: 0.8)")
    a.add_argument("--sat-base", type=float, default=0.35, help="Foundation base saturation boost across all hues (default: 0.35)")
    a.add_argument("--sat-bg-factor", type=float, default=1.2, help="Background noise saturation suppression threshold factor (default: 1.2)")
    a.add_argument("--glare-suppress", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Lunar limb forward scattering glare suppression mode: 'auto' (4.5px falloff, default), 'mild' (8.0px), 'aggressive' (2.5px), 'off' (bypass)")
    a.add_argument("--extinction-comp", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Atmospheric extinction gradient compensation: 'auto' (adaptive threshold, default), 'mild' (50% strength), 'aggressive', 'off' (bypass)")
    a.add_argument("--mineral-style", default="deep-cine", choices=["deep-cine", "natural"],
                   help="Mineral moon aesthetic style: 'deep-cine' (deep matte basalt tone, bilateral chroma smoothing, terracotta/cobalt pure boost, shadow/ray rolloff, default) or 'natural' (classic subtle saturation)")
    a.add_argument("--mineral-fe-boost", type=float, default=5.2, help="Deep-cine saturation boost for Fe-rich terrain (terracotta peach, default: 5.2)")
    a.add_argument("--mineral-ti-boost", type=float, default=6.5, help="Deep-cine saturation boost for Ti-rich basalt (azure denim blue, default: 6.5)")
    a.add_argument("--mineral-gamma", type=float, default=1.00, help="Deep-cine filmic luminance sculpting gamma (default: 1.00)")
    a.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="Rotate output images clockwise (default: 0, 180 for standard north-up lunar orientation)")
    a.add_argument("--square-crop", action=argparse.BooleanOptionalAction, default=True, help="Automatically export 1:1 square close-up master (default: True)")
    a.add_argument("--mosaic-mode", default="disc", choices=["disc", "tile"],
                   help="Mosaic mode: 'disc' (full celestial disk with limb handling, default) or 'tile' (lunar mosaic panel, bypasses limb glare suppression, exports tile metadata)")
    a.add_argument("--lock-profile", default=None, help="Path to lunar_profile.json to lock global histogram and color balance")
    a.add_argument("--lock-from", default=None, help="Directory of reference anchor panel to auto-load lunar_profile.json or mosaic_tile_info.json")
    a.add_argument("--export-profile", default=None, help="Explicit destination file path to export calibration profile (default: work/lunar_profile.json)")
    a.add_argument("--lock-wb", nargs=2, type=float, default=None, help="Explicitly lock Gray-World gains (k_r k_b)")
    a.add_argument("--lock-stretch", nargs=2, type=float, default=None, help="Explicitly lock histogram stretch range (bg_lum hi_lum)")
    a.set_defaults(func=lambda args: _ensure_deps_and_call("cmd_postprocess", args))

    a = sub.add_parser("verify")
    a.add_argument("--work", required=True)
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--mosaic-mode", default=None, choices=["disc", "tile"],
                   help="Mosaic mode override ('disc' or 'tile', auto-detected from mosaic_tile_info.json if omitted)")
    a.set_defaults(func=lambda args: _ensure_deps_and_call("cmd_verify", args))

    a = sub.add_parser("all")
    a.add_argument("--input", required=True, help="Input directory or single SER/AVI/video file")
    a.add_argument("--work", required=True)
    a.add_argument("--format", default="auto", choices=["auto", "fits", "raw", "ser", "video", "avi", "mp4"], help="Force format selection")
    a.add_argument("--limit", type=int, default=0, help="Import frame cap (0=uncapped; adaptive video mode still filters by quality)")
    a.add_argument("--sample-mode", default="adaptive", choices=["adaptive", "smart-top", "smart-cluster", "window", "head"],
                   help="Video sampling: 'adaptive' (quality-based count, default), 'smart-top' (global top-K), 'smart-cluster' (temporal top-K), 'window'/'head' (consecutive frames)")
    a.add_argument("--probe-stride", type=int, default=1,
                   help="Quality scan stride (default: 1, scores every decoded frame)")
    a.add_argument("--start-frame", type=int, default=0, help="Starting source frame for head/window modes (default: 0)")
    a.add_argument("--ser-debayer", action=argparse.BooleanOptionalAction, default=True, help="Debayer Bayer SER frames to RGB FITS (default: True)")
    a.add_argument("--force-mono", action="store_true", help="Force extracting video as 1-channel monochrome FITS")
    a.add_argument("--video-debayer", default="auto", choices=["auto", "bggr", "rggb", "grbg", "gbrg", "none"],
                   help="Demosaicing for RAW Bayer video containers (e.g. Seestar RAW.avi): 'auto' (detects Bayer grid, defaults to BGGR), 'bggr', 'rggb', or 'none'")
    a.add_argument("--device", default="auto",
                   help="Capture device for optics priors, e.g. 'seestar-s50', 'dwarf-3'. "
                        "'auto' (default) identifies from FITS header, capture sidecar, filename "
                        "and sensor model; 'none' disables. Run 'devices' to list supported ids")
    a.add_argument("--seq", default="moon_")
    a.add_argument("--roi", type=int, default=1024)
    a.add_argument("--select-mode", default="otsu", choices=["otsu", "utility", "mtf-snr", "relative", "percent"],
                   help="Frame selection mode: 'otsu' (adaptive bimodal seeing clustering, default), 'utility'/'mtf-snr' (MTF-SNR joint utility optimization), 'relative' (relative to reference frame), 'percent' (fixed/tiered percentage)")
    a.add_argument("--quality-threshold", type=float, default=0.75, help="Minimum sharpness relative to reference frame (0.0 - 1.0) for 'relative' mode (default: 0.75)")
    a.add_argument("--utility-alpha", type=float, default=2.0, help="MTF/contrast weight exponent for 'utility' mode (default: 2.0)")
    a.add_argument("--utility-beta", type=float, default=1.0, help="SNR weight exponent for 'utility' mode (default: 1.0)")
    a.add_argument("--keep-percent", type=float, default=None, help="Frame selection ratio in percent (switches to 'percent' mode if specified)")
    a.add_argument("--min-confidence", type=float, default=0.15)
    a.add_argument("--framing", default=None, choices=["min", "max", "cog"], help="Framing mode for resampling (default: 'min' for disc, 'max' for tile)")
    a.add_argument("--mosaic-mode", default="disc", choices=["disc", "tile"],
                   help="Mosaic mode: 'disc' (full celestial disk, default) or 'tile' (lunar mosaic panel, framing=max, bypasses limb glare suppression, exports tile metadata)")
    a.add_argument("--lock-profile", default=None, help="Path to lunar_profile.json to lock global histogram and color balance")
    a.add_argument("--lock-from", default=None, help="Directory of reference anchor panel to auto-load lunar_profile.json or mosaic_tile_info.json")
    a.add_argument("--export-profile", default=None, help="Explicit destination file path to export calibration profile (default: work/lunar_profile.json)")
    a.add_argument("--lock-wb", nargs=2, type=float, default=None, help="Explicitly lock Gray-World gains (k_r k_b)")
    a.add_argument("--lock-stretch", nargs=2, type=float, default=None, help="Explicitly lock histogram stretch range (bg_lum hi_lum)")
    a.add_argument("--interp", default="cu", choices=["cu", "li", "la", "none", "cubic", "linear", "lanczos", "bilinear"],
                   help="Resampling interpolation: 'li'/'linear' (bilinear, zero overshoot), 'cu'/'cubic' (bicubic, default)")
    a.add_argument("--out", default="moon_master.fit")
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--sigma", nargs=2, default=["3", "3"])
    a.add_argument("--norm", default="addscale")
    a.add_argument("--stack-method", default="auto", choices=["auto", "sum", "rej"],
                   help="Stacking method: 'auto' (rejection mean for every source depth, default), "
                        "'rej' (force rejection mean), 'sum' (legacy additive stack, explicit fallback)")
    a.add_argument("--weight", default="auto", choices=["auto", "none", "noise"],
                   help="Frame weighting for rejection stacking: 'auto' (noise weighting, default), "
                        "'noise', 'none'. Ignored by 'sum'")
    a.add_argument("--drizzle", default="auto", choices=["auto", "off", "2", "3"],
                   help="HST drizzle supersampling: 'auto' (2x when the theoretical Airy radius is "
                        f"below {DRIZZLE_AIRY_THRESHOLD_PX}px), 'off', or a forced scale")
    a.add_argument("--drizzle-pixfrac", type=float, default=None,
                   help="Drizzle droplet pixel fraction (default: 1/scale)")
    a.add_argument("--drizzle-kernel", default="square", choices=list(DRIZZLE_KERNELS),
                   help="Drizzle droplet kernel (default: 'square'); lanczos2/3 only at scale == pixfrac == 1.0")
    a.add_argument("--drizzle-airy-threshold", type=float, default=None,
                   help=f"Airy-radius threshold in px for the --drizzle auto diffraction criterion (default {DRIZZLE_AIRY_THRESHOLD_PX})")
    a.add_argument("--drizzle-psf-threshold", type=float, default=None,
                   help=f"Measured PSF FWHM threshold in px for the --drizzle auto fallback criterion (default {DRIZZLE_PSF_THRESHOLD_PX})")
    a.add_argument("--deconv", default="auto", choices=["auto", "sb", "wiener", "rl", "none"])
    a.add_argument("--no-adc", action="store_true")
    a.add_argument("--midtone", type=float, default=None, help="MTF midtone stretch value (default: None for auto albedo-adaptive estimation)")
    a.add_argument("--wavelet-l1", type=float, default=None, help="Finest native-scale gain; working-grid layer 1 with --wavelet-adapt off")
    a.add_argument("--clahe-clip", type=float, default=None, help="CLAHE clip limit (default: auto dynamic, set <=0 to bypass CLAHE)")
    a.add_argument("--sharp-mode", default="auto", choices=["auto", "mellow", "mild", "crisp", "none"],
                   help="Sharpening mode: 'auto' (balanced natural organic baseline, default), 'mellow' (ultra-soft optical view, no CLAHE), 'mild' (subtle natural), 'crisp' (classic), 'none' (bypass)")
    a.add_argument("--unsharp", type=float, default=None, help="USM unsharp mask amount (default: auto bypassed when wavelets/deconv active)")
    a.add_argument("--sharp-guard", default="auto", choices=["auto", "off"], help="Validate the full sharpening chain and reduce unsafe enhancement (default: auto)")
    a.add_argument("--anti-ringing", default="auto", choices=["auto", "off", "mild", "aggressive"],
                   help="Edge undershoot dark ringing suppression mode: 'auto' (adaptive damping, default), 'mild' (subtle), 'aggressive' (strong), 'off' (bypass)")
    a.add_argument("--damping-factor", type=float, default=None,
                   help="Manual anti-ringing damping factor (0.0 to 1.0, overrides preset if specified)")
    a.add_argument("--aperture", type=float, default=None, help="Telescope aperture in mm (default: trusted metadata only)")
    a.add_argument("--focal", type=float, default=None, help="Telescope focal length in mm (default: auto inferred from limb/header)")
    a.add_argument("--pixel-size", type=float, default=None, help="Sensor pixel size in microns (default: trusted metadata only)")
    a.add_argument("--mineral-mode", default="lrgb", choices=["lrgb", "legacy"],
                   help="Mineral moon processing pipeline: 'lrgb' (Luminance/Chrominance separation, default) or 'legacy' (monolithic RGB)")
    a.add_argument("--white-balance", default="gray-world", choices=["gray-world", "legacy"],
                   help="Color balance mode: 'gray-world' (neutral lunar albedo baseline, default) or 'legacy' (per-channel percentile stretch)")
    a.add_argument("--sat-fe", type=float, default=0.8, help="Mineral saturation boost for Fe-rich terrain (orange-yellow hue 1, default: 0.8)")
    a.add_argument("--sat-ti", type=float, default=0.8, help="Mineral saturation boost for Ti-rich basalt (cyan-blue hues 3 & 4, default: 0.8)")
    a.add_argument("--sat-base", type=float, default=0.35, help="Foundation base saturation boost across all hues (default: 0.35)")
    a.add_argument("--sat-bg-factor", type=float, default=1.2, help="Background noise saturation suppression threshold factor (default: 1.2)")
    a.add_argument("--glare-suppress", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Lunar limb forward scattering glare suppression mode: 'auto' (4.5px falloff, default), 'mild' (8.0px), 'aggressive' (2.5px), 'off' (bypass)")
    a.add_argument("--extinction-comp", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Atmospheric extinction gradient compensation: 'auto' (adaptive threshold, default), 'mild' (50% strength), 'aggressive', 'off' (bypass)")
    a.add_argument("--mineral-style", default="deep-cine", choices=["deep-cine", "natural"],
                   help="Mineral moon aesthetic style: 'deep-cine' (deep matte basalt tone, bilateral chroma smoothing, terracotta/cobalt pure boost, shadow/ray rolloff, default) or 'natural' (classic subtle saturation)")
    a.add_argument("--mineral-fe-boost", type=float, default=5.2, help="Deep-cine saturation boost for Fe-rich terrain (terracotta peach, default: 5.2)")
    a.add_argument("--mineral-ti-boost", type=float, default=6.5, help="Deep-cine saturation boost for Ti-rich basalt (azure denim blue, default: 6.5)")
    a.add_argument("--mineral-gamma", type=float, default=1.00, help="Deep-cine filmic luminance sculpting gamma (default: 1.00)")
    a.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="Rotate output images clockwise (default: 0, 180 for standard north-up lunar orientation)")
    a.add_argument("--square-crop", action=argparse.BooleanOptionalAction, default=True, help="Automatically export 1:1 square close-up master (default: True)")
    a.set_defaults(func=lambda args: _ensure_deps_and_call("cmd_all", args))

    for name in ("import", "all"):
        command = sub.choices[name]
        command.add_argument("--video-roi", default="auto", choices=["auto", "off"], help="Fixed tracked lunar crop; off preserves full source dimensions")
        command.add_argument("--roi-margin", type=int, default=64, help="Lunar crop margin in native pixels")
        command.add_argument("--min-signal-ratio", type=float, default=0.2, help="Minimum lunar signal versus the clip's 90th percentile (default: 0.2); rejects photon-starved compression texture")
    for name in ("import", "stack", "postprocess", "all"):
        sub.choices[name].add_argument("--disk-budget-gb", type=float, default=None, help="Maximum work allocation in GiB; filesystem safety reserve still applies")
    for name in ("register", "all"):
        sub.choices[name].add_argument("--register-selection", default="auto", choices=["auto", "reselect"], help="Reuse primary video selection or explicitly filter again")
    sub.choices["all"].add_argument("--candidate-mode", default="single", choices=["single", "feedback"], help="Single selection or measured doubling of video candidates")
    for name in ("postprocess", "all"):
        sub.choices[name].add_argument("--wavelet-adapt", default="auto", choices=["auto", "off"], help="Limit wavelet gains to supported native scales and regions (default: auto)")
        sub.choices[name].add_argument("--luminance-channel", default="weighted", choices=["weighted", "green"], help="Weighted RGB luminance or green detail plane")
    for name in ("postprocess", "verify", "all"):
        sub.choices[name].add_argument(
            "--html-report",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Generate interactive HTML dual-window comparison viewer (default: True)",
        )
    for name in ("import", "postprocess", "verify", "all"):
        sub.choices[name].add_argument(
            "--sky-margin",
            type=float,
            default=0.25,
            help="Adaptive sky margin ratio relative to lunar radius (default: 0.25 for 25%% margin)",
        )
        sub.choices[name].add_argument(
            "--auto-margin",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Automatically center lunar disc and ensure balanced sky margins (default: True)",
        )
    return p


def _ensure_deps_and_call(fn_name: str, args: Any) -> None:
    if np is None:
        die(f"numpy import failed: {_numpy_import_error}; install dependencies with {sys.executable} -m pip install -r {Path(__file__).resolve().parent.parent / 'requirements.txt'}")
    fn = globals().get(fn_name)
    if fn is None:
        for mod in _SUBMODULES:
            if hasattr(mod, fn_name):
                fn = getattr(mod, fn_name)
                break
    if fn is None:
        die(f"command handler {fn_name} is unavailable")
    fn(args)


# ---------------------------------------------------------------------------
# Module Facade Proxy: Transparent Monkeypatch Synchronization & Dynamic Re-export
# ---------------------------------------------------------------------------

class _MoonStackModule(types.ModuleType):
    """Dynamic facade module that propagates monkeypatched attributes down to submodules."""

    def __getattr__(self, name: str) -> Any:
        for mod in _SUBMODULES:
            if hasattr(mod, name):
                return getattr(mod, name)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        for mod in _SUBMODULES:
            if hasattr(mod, name):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _MoonStackModule


if __name__ == "__main__":
    ns = build_parser().parse_args()
    ns.func(ns)
