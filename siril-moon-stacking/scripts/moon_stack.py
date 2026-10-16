#!/usr/bin/env python3
"""Moon / planetary lucky-imaging stacking driver.

Architecture:
  Siril 1.4.4 CLI acts as the main processing backbone:
    - RAW decoding & demosaicing (convert -debayer) / FITS sequence linking (link)
    - Multithreaded subpixel bicubic resampling & common area cropping (seqapplyreg -framing=min -interp=cu)
    - Winsorized sigma clipping stacking (stack rej w 3 3 -norm=addscale -filter-included)
    - Multiscale 'à trous' B-Spline wavelet reconstruction & unsharp mask (wavelet / wrecons / unsharp)
    - Mineral moon chromatic alignment & saturation boost (rmgreen / satu)
    - Professional 32-bit FITS / 16-bit TIFF / JPG exports (savetif / savejpg)

  Python acts as a lightweight registration plugin:
    - Robust lunar surface feature ROI localization (gradient-based, moon phase agnostic)
    - Subpixel Hann-windowed FFT phase correlation & confidence estimation
    - Seeing sharpness ranking & outlier rejection
    - Injection of R0 homography matrices into Siril's native .seq sequence file
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

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
except ImportError:  # pragma: no cover - script dir not yet on sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from device_specs import (  # type: ignore[no-redef]
        DEVICE_SPECS,
        EXCLUDED_DEVICES,
        SENSOR_STATUS,
        SOURCE_GRADES,
        get_device,
        identify_device,
        list_devices,
    )

import numpy as np

DEFAULT_SIRIL = "/Applications/Siril.app/Contents/MacOS/siril-cli"
RAW_EXTS = {".orf", ".cr2", ".cr3", ".nef", ".arw", ".dng", ".raw", ".rw2", ".pef", ".srf"}
FIT_EXTS = {".fit", ".fits", ".fit.gz", ".fits.gz"}
SER_EXTS = {".ser"}
VIDEO_EXTS = {".avi", ".mp4", ".mov", ".mkv", ".m4v"}

SER_COLOR_MONO = 0
SER_COLOR_BAYER_RGGB = 8
SER_COLOR_BAYER_GRBG = 9
SER_COLOR_BAYER_GBRG = 10
SER_COLOR_BAYER_BGGR = 11
SER_COLOR_RGB = 100
SER_COLOR_BGR = 101

SER_BAYER_NAMES = {
    8: "RGGB",
    9: "GRBG",
    10: "GBRG",
    11: "BGGR",
}


# --------------------------------------------------------------------------- io

def log(msg: str) -> None:
    print(f"[moon-stack] {msg}", flush=True)


def die(msg: str) -> "NoReturn":  # type: ignore[valid-type]
    print(f"[moon-stack] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, obj: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def run_siril_script(siril: str, script_lines: list[str], cwd: Path, log_path: Path, timeout: int = 3600) -> dict:
    """Write an SSF script and execute Siril CLI in an isolated directory."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ssf_file = log_path.with_suffix(".ssf")
    # Always ensure 'requires 1.4.4' is at the top
    if not script_lines or not script_lines[0].startswith("requires"):
        script_lines = ["requires 1.4.4"] + script_lines
    if script_lines[-1] != "exit":
        script_lines.append("exit")

    bounded = []
    for line in script_lines:
        if line.startswith("mtf "):
            fields = line.split()
            low, middle, high = map(float,fields[1:4])
            if not all(np.isfinite([low,middle,high])) or not 0 < middle < 1:
                die(f"invalid MTF parameters: {line}")
            low = min(max(low,0.0),0.999998)
            high = min(max(high,low+0.000001),1.0)
            fields[1:4] = [f"{low:.6f}",f"{middle:.6f}",f"{high:.6f}"]
            line = " ".join(fields)
        bounded.append(line)
    ssf_file.write_text("\n".join(bounded) + "\n", encoding="utf-8")
    cmd = [siril, "-s", str(ssf_file), "-d", str(cwd)]
    env = dict(os.environ, LANG="C")
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    elapsed = round(time.time() - started, 2)
    output_log = (proc.stdout or "") + (proc.stderr or "")
    log_path.write_text(output_log, encoding="utf-8")

    return {
        "script": str(ssf_file),
        "log": str(log_path),
        "exit_code": proc.returncode,
        "seconds": elapsed,
        "output": output_log,
    }


def fits_header_info(path: Path) -> dict:
    """Inspect FITS header without memory mapping issues."""
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        h = hdul[0].header
        return {
            "naxis": h.get("NAXIS"),
            "width": h.get("NAXIS1"),
            "height": h.get("NAXIS2"),
            "channels": h.get("NAXIS3", 1),
            "bitpix": h.get("BITPIX"),
            "bayer": h.get("BAYERPAT"),
            "exptime": h.get("EXPTIME"),
            "instrument": h.get("INSTRUME"),
            "telescope": h.get("TELESCOP"),
            "date_obs": h.get("DATE-OBS"),
        }


# --- capture device priors ---------------------------------------------------
# Smart-telescope specifications live in scripts/device_specs.py.  Any value that
# reaches a FITS header from that table is tagged, so downstream code and reports
# can always tell a lookup apart from a measurement:
#   DEVICE    device id the header was matched to
#   DEVSRC    signal used (cli / header / sidecar / filename / sensor)
#   DEVCONF   user / high / medium / low
#   OPTPRIOR  comma-separated optical keys actually filled from the table
#   OPTISRC   device_table | sensor_pixel_only
OPTICAL_PRIOR_KEYS = ("FOCALLEN", "APERTURE", "XPIXSZ", "YPIXSZ")


def _apply_device_priors(header, ident, *, overwrite: bool = False) -> dict:
    """Write device-table priors plus their provenance into a FITS header.

    Only keywords that are still missing get filled unless ``overwrite`` is set,
    so an explicit sidecar value or a real measurement always beats a lookup.
    """
    if not ident:
        return {"injected": False, "target": "none"}

    if ident.get("id"):
        header["DEVICE"] = str(ident["id"])
        header["DEVSRC"] = str(ident.get("match_source") or "unknown")
        header["DEVCONF"] = str(ident.get("confidence") or "unknown")

    applied = {}
    for key, value in (ident.get("priors") or {}).items():
        if value is None or (key in header and not overwrite):
            continue
        header[key] = float(value)
        applied[key] = float(value)

    if not applied:
        return {"injected": False, "target": "none",
                "reason": "all optical keywords already present"}

    header["OPTPRIOR"] = ",".join(sorted(applied))
    header["OPTISRC"] = "device_table" if ident.get("id") else "sensor_pixel_only"
    return {"injected": True, "target": "fits_header", "applied": applied}


def _device_ident_summary(ident) -> dict:
    """Compact, JSON-safe description of an identification result for receipts."""
    if not ident:
        return {"id": None, "label": None, "match_source": None, "confidence": None,
                "ambiguous": False, "brand_only": False, "source_grade": None,
                "sensor": None, "sensor_status": None, "priors": {}, "notes": ""}
    return {
        "id": ident.get("id"),
        "label": ident.get("label"),
        "match_source": ident.get("match_source"),
        "confidence": ident.get("confidence"),
        "ambiguous": bool(ident.get("ambiguous")),
        "brand_only": bool(ident.get("brand_only")),
        "source_grade": ident.get("source_grade"),
        "sensor": ident.get("sensor"),
        "sensor_status": ident.get("sensor_status"),
        "priors": dict(ident.get("priors") or {}),
        "notes": ident.get("notes", ""),
    }


def _hdr_source_label(hdr, key: str, base: str) -> str:
    """Label where an optical value came from, flagging device-table priors.

    ``OPTPRIOR`` lists exactly which optical keywords the built-in device table
    filled, so a lookup is never reported as if it were a measurement (and a real
    header value is never blamed on the table).
    """
    priors = str((hdr or {}).get("OPTPRIOR") or "")
    if key not in priors:
        return base
    device = (hdr or {}).get("DEVICE")
    src = (hdr or {}).get("OPTISRC") or "device_table"
    tag = f"{src}: {device}" if device and device != "unknown" else src
    return f"{base} [{tag}]"


def _resolve_device_arg(args) -> str:
    """Validate --device, exiting with an actionable message on an unknown id."""
    requested = str(getattr(args, "device", "auto") or "auto").strip()
    if requested in ("auto", "none", ""):
        return requested
    if requested not in DEVICE_SPECS:
        die(f"unknown --device '{requested}'; run `moon_stack.py devices` to list "
            f"the {len(DEVICE_SPECS)} supported ids, or use 'auto'/'none'")
    return requested


def parse_ser_header(path: Path) -> dict:
    """Parse 178-byte header of a standard SER video container.

    Reference: Lucam Recorder SER format specification.
    """
    import datetime
    import struct

    if not path.is_file():
        raise FileNotFoundError(f"SER file not found: {path}")

    header_bytes = path.read_bytes()[:178] if path.stat().st_size >= 178 else b""
    if len(header_bytes) < 178:
        raise ValueError(f"File {path.name} is too small to be a valid SER file (size={len(header_bytes)} < 178 bytes)")

    fmt = "<14s7i40s40s40s2q"
    (
        file_id,
        lu_id,
        color_id,
        little_endian,
        width,
        height,
        pixel_depth,
        frame_count,
        raw_observer,
        raw_instrument,
        raw_telescope,
        date_time,
        date_time_utc,
    ) = struct.unpack(fmt, header_bytes)

    if file_id != b"LUCAM-RECORDER":
        raise ValueError(f"Invalid SER header: FileID='{file_id.decode('ascii', errors='replace')}', expected 'LUCAM-RECORDER'")

    observer = raw_observer.decode("utf-8", errors="ignore").rstrip("\x00").strip()
    instrument = raw_instrument.decode("utf-8", errors="ignore").rstrip("\x00").strip()
    telescope = raw_telescope.decode("utf-8", errors="ignore").rstrip("\x00").strip()

    # Convert DateTime_UTC (100ns intervals since 0001-01-01 00:00:00 UTC)
    date_obs = None
    epoch_100ns = 621355968000000000  # 0001-01-01 to 1970-01-01 in 100ns
    ts_val = date_time_utc if date_time_utc > epoch_100ns else (date_time if date_time > epoch_100ns else 0)
    if ts_val > epoch_100ns:
        try:
            unix_sec = (ts_val - epoch_100ns) / 10000000.0
            dt = datetime.datetime.fromtimestamp(unix_sec, datetime.timezone.utc)
            date_obs = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        except Exception:
            date_obs = None

    is_bayer = color_id in (SER_COLOR_BAYER_RGGB, SER_COLOR_BAYER_GRBG, SER_COLOR_BAYER_GBRG, SER_COLOR_BAYER_BGGR)
    is_rgb = color_id in (SER_COLOR_RGB, SER_COLOR_BGR)
    is_mono = (color_id == SER_COLOR_MONO)

    return {
        "path": str(path),
        "file_id": file_id.decode("ascii"),
        "lu_id": lu_id,
        "color_id": color_id,
        "little_endian": bool(little_endian),
        "width": width,
        "height": height,
        "pixel_depth": pixel_depth,
        "frame_count": frame_count,
        "observer": observer,
        "instrument": instrument,
        "telescope": telescope,
        "date_obs": date_obs,
        "is_mono": is_mono,
        "is_bayer": is_bayer,
        "is_rgb": is_rgb,
        "bayer_pattern": SER_BAYER_NAMES.get(color_id),
    }


def unpack_ser_to_fits(
    ser_path: Path,
    out_dir: Path,
    seq_name: str = "moon_",
    limit: int = 0,
    debayer: bool = True,
    device_override: str | None = None,
) -> dict:
    """Extract frames from SER file into individual 16-bit FITS files with metadata."""
    from astropy.io import fits
    import cv2

    hdr = parse_ser_header(ser_path)

    ident = identify_device(
        cli_device=device_override,
        header={"TELESCOP": hdr.get("telescope"), "INSTRUME": hdr.get("instrument")},
        filename=ser_path.name,
        sensor=hdr.get("instrument"),
    )
    if ident:
        log(f"device: {ident.get('label') or ident.get('id')} "
            f"(source={ident.get('match_source')}, confidence={ident.get('confidence')}"
            + (f", priors={ident.get('priors')}" if ident.get("priors") else "") + ")")
    w, h = hdr["width"], hdr["height"]
    depth = hdr["pixel_depth"]
    color_id = hdr["color_id"]
    bytes_per_sample = 1 if depth <= 8 else 2

    if hdr["is_rgb"]:
        plane_count = 3
    else:
        plane_count = 1

    frame_bytes = w * h * bytes_per_sample * plane_count
    file_size = ser_path.stat().st_size
    data_size = max(0, file_size - 178)
    max_avail_frames = data_size // frame_bytes if frame_bytes > 0 else 0
    total_frames = min(hdr["frame_count"], max_avail_frames)

    if total_frames <= 0:
        raise ValueError(f"SER file contains 0 valid frames (file size {file_size} bytes, frame size {frame_bytes})")

    extract_count = min(total_frames, limit) if limit > 0 else total_frames
    out_dir.mkdir(parents=True, exist_ok=True)

    # De-facto SER convention (SER Player / PIPP / Siril / GoQat): the
    # LittleEndian header flag means the OPPOSITE of its name -- 0 is
    # little-endian, 1 is big-endian.  The specification's literal wording is
    # the other way round, but no mainstream writer follows it, so honouring
    # the flag literally byte-swaps every real-world capture.
    # https://free-astro.org/index.php?title=SER/en#Specification_issue_with_endianness
    dt_endian = ">" if hdr["little_endian"] else "<"
    dt_type = f"{dt_endian}u2" if bytes_per_sample == 2 else "u1"

    # Memory map the video payload
    mmap_data = np.memmap(
        ser_path,
        dtype=np.dtype(dt_type),
        mode="r",
        offset=178,
        shape=(extract_count, h, w, plane_count) if plane_count > 1 else (extract_count, h, w),
    )

    out_channels = 3 if (hdr["is_rgb"] or (hdr["is_bayer"] and debayer)) else 1

    cv2_code = None
    if hdr["is_bayer"] and debayer:
        cv2_map = {
            SER_COLOR_BAYER_RGGB: cv2.COLOR_BayerRG2RGB,
            SER_COLOR_BAYER_GRBG: cv2.COLOR_BayerGR2RGB,
            SER_COLOR_BAYER_GBRG: cv2.COLOR_BayerGB2RGB,
            SER_COLOR_BAYER_BGGR: cv2.COLOR_BayerBG2RGB,
        }
        cv2_code = cv2_map.get(color_id, cv2.COLOR_BayerRG2RGB)

    log(f"unpacking SER [{ser_path.name}]: {extract_count} frames, {w}x{h}, {depth}-bit, color_id={color_id} -> {out_channels}-channel FITS")

    for i in range(extract_count):
        raw_frame = np.array(mmap_data[i], copy=True)
        # Normalize to uint16
        if bytes_per_sample == 1:
            raw_frame = (raw_frame.astype(np.uint16) << 8) | raw_frame.astype(np.uint16)
        else:
            raw_frame = raw_frame.astype(np.uint16)

        if hdr["is_bayer"] and debayer and cv2_code is not None:
            rgb = cv2.demosaicing(raw_frame, cv2_code)
            # FITS format: (channels, H, W)
            fits_data = np.transpose(rgb, (2, 0, 1))
        elif hdr["is_rgb"]:
            if color_id == SER_COLOR_BGR:
                raw_frame = raw_frame[..., ::-1]
            fits_data = np.transpose(raw_frame, (2, 0, 1))
        else:
            # Monochrome or raw CFA
            fits_data = raw_frame

        target_fit = out_dir / f"{seq_name}{i+1:05d}.fit"
        hdu = fits.PrimaryHDU(fits_data)
        if hdr["date_obs"]:
            hdu.header["DATE-OBS"] = hdr["date_obs"]
        if hdr["instrument"]:
            hdu.header["INSTRUME"] = hdr["instrument"]
        if hdr["telescope"]:
            hdu.header["TELESCOP"] = hdr["telescope"]
        if hdr["observer"]:
            hdu.header["OBSERVER"] = hdr["observer"]
        if hdr["bayer_pattern"] and not debayer:
            hdu.header["BAYERPAT"] = hdr["bayer_pattern"]

        _apply_device_priors(hdu.header, ident)

        hdu.writeto(target_fit, overwrite=True)

    return {
        "extracted_frames": extract_count,
        "total_in_file": total_frames,
        "channels": out_channels,
        "width": w,
        "height": h,
        "metadata": hdr,
        "device": _device_ident_summary(ident),
    }


def format_video_decode_error(
    video_path: Path,
    failure_stage: str,
    cap: Any = None,
    total_reported: int = 0,
) -> str:
    """Analyze a failed video decode attempt and construct a detailed diagnostic report.

    Inspects magic bytes, container atoms, FourCC codec flags, file truncation,
    and OpenCV capture state to provide precise root causes and copy-pasteable ffmpeg
    remediation commands.
    """
    import cv2

    lines = []
    lines.append("=" * 80)
    lines.append("[moon-stack] 视频解码诊断报告 / Video Decoding Diagnostic Report")
    lines.append("-" * 80)
    lines.append(f"目标文件 / Target File:   {video_path}")

    if not video_path.exists():
        lines.append("文件状态 / File Status:   不存在 (File Not Found)")
        lines.append("=" * 80)
        return "\n".join(lines)

    try:
        size_bytes = video_path.stat().st_size
    except Exception:
        size_bytes = 0

    if size_bytes < 1024:
        size_str = f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        size_str = f"{size_bytes / 1024:.1f} KB ({size_bytes:,} bytes)"
    else:
        size_str = f"{size_bytes / (1024 * 1024):.2f} MB ({size_bytes:,} bytes)"

    lines.append(f"文件大小 / File Size:     {size_str}")

    # Read header and footer bytes
    header_bytes = b""
    footer_bytes = b""
    try:
        with open(video_path, "rb") as f:
            header_bytes = f.read(65536)
            if size_bytes > 65536:
                f.seek(max(0, size_bytes - 65536))
                footer_bytes = f.read(65536)
    except Exception as exc:
        lines.append(f"读取异常 / Read Error:    {exc}")

    # Container & Magic analysis
    container_guess = "Unknown"
    detected_fourcc = ""
    has_moov = False
    is_ser = False
    is_fits = False
    is_image = False
    image_type = ""

    if header_bytes.startswith(b"RIFF") and len(header_bytes) >= 12 and header_bytes[8:12] == b"AVI ":
        container_guess = "AVI (Resource Interchange File Format)"
        idx_vids = header_bytes.find(b"vids")
        if idx_vids != -1 and idx_vids + 8 <= len(header_bytes):
            raw_fc = header_bytes[idx_vids + 4 : idx_vids + 8]
            detected_fourcc = "".join(chr(b) for b in raw_fc if 32 <= b <= 126).strip()
    elif b"ftyp" in header_bytes[:64] or b"moov" in header_bytes[:1024] or b"mdat" in header_bytes[:1024]:
        container_guess = "MP4/MOV (ISO Base Media File Format)"
        has_moov = (b"moov" in header_bytes) or (b"moov" in footer_bytes)
    elif header_bytes.startswith(b"LUCAM-RECORDER"):
        container_guess = "SER (Planetary Astronomy Video Stream)"
        is_ser = True
    elif header_bytes.startswith(b"SIMPLE  ="):
        container_guess = "FITS (Flexible Image Transport System)"
        is_fits = True
    elif header_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        container_guess = "PNG (Static Image)"
        is_image = True
        image_type = "PNG"
    elif header_bytes.startswith(b"\xff\xd8\xff"):
        container_guess = "JPEG (Static Image)"
        is_image = True
        image_type = "JPEG"
    elif header_bytes.startswith(b"II*\x00") or header_bytes.startswith(b"MM\x00*"):
        container_guess = "TIFF (Static Image)"
        is_image = True
        image_type = "TIFF"
    elif header_bytes.startswith(b"<!DOCTYPE") or header_bytes.startswith(b"<html") or header_bytes.startswith(b"{\n"):
        container_guess = "Text/HTML Document"

    lines.append(f"容器分析 / Container:     {container_guess}")

    # Inspect OpenCV Capture status
    cap_opened = False
    cap_backend = ""
    w, h, fps = 0, 0, 0.0
    cap_fourcc = ""
    cap_frames = total_reported

    probe_cap = None
    target_cap = cap
    if target_cap is None:
        try:
            probe_cap = cv2.VideoCapture(str(video_path))
            target_cap = probe_cap
        except Exception:
            pass

    if target_cap is not None:
        try:
            cap_opened = bool(target_cap.isOpened())
            if cap_opened:
                w = int(target_cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                h = int(target_cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                fps = float(target_cap.get(cv2.CAP_PROP_FPS) or 0.0)
                cap_frames = int(target_cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                fourcc_val = int(target_cap.get(cv2.CAP_PROP_FOURCC) or 0)
                raw_chars = [chr((fourcc_val >> 8 * i) & 0xFF) for i in range(4)]
                cap_fourcc = "".join([c for c in raw_chars if 32 <= ord(c) <= 126]).strip()
                if hasattr(target_cap, "getBackendName"):
                    cap_backend = str(target_cap.getBackendName())
        except Exception:
            pass

    if probe_cap is not None:
        try:
            probe_cap.release()
        except Exception:
            pass

    fourcc_display = detected_fourcc or cap_fourcc or "None/Unknown"
    lines.append(f"编码标识 / FourCC Code:   {fourcc_display}")
    lines.append(
        f"OpenCV状态 / Capture:     isOpened={cap_opened}, Backend={cap_backend or 'default'}, "
        f"Res={w}x{h}, Frames={cap_frames if cap_frames > 0 else 'stream'}"
    )

    # Root cause diagnosis & remediation
    lines.append("-" * 80)
    lines.append("【问题定位 / Root Cause】")

    root_cause = ""
    remedy_steps = []

    fourcc_upper = fourcc_display.upper()
    is_hevc = fourcc_upper in ("H265", "HEVC", "HVC1", "HEV1") or (b"hvc1" in header_bytes) or (b"hev1" in header_bytes)
    is_av1 = fourcc_upper in ("AV01", "AV1") or (b"av01" in header_bytes)
    is_prores = fourcc_upper in ("APCN", "APCH", "APCO", "AP4H", "APRN") or (b"apcn" in header_bytes) or (b"apch" in header_bytes)
    is_planetary_raw = fourcc_upper in ("Y800", "GREY", "DIB ", "ZWO ", "QHY ", "RAW ")

    fixed_avi = video_path.with_name(f"{video_path.stem}_converted.avi")
    fixed_mp4 = video_path.with_name(f"{video_path.stem}_fixed.mp4")

    if size_bytes == 0:
        root_cause = (
            "视频文件大小为 0 字节（空文件）。\n"
            "这通常是由于录像未正常开始、拍摄程序异常中断、或是从手机/智能望远镜传输过程中中断造成的文件损坏。"
        )
        remedy_steps.append("请检查拍摄设备源文件是否完好，并重新导出或拷贝完整视频。")

    elif is_ser:
        root_cause = (
            f"文件扩展名为 '{video_path.suffix}'，但底层实际为天文专用 SER 视频流（包含 'LUCAM-RECORDER' 魔数头）。\n"
            "OpenCV 仅支持通用音视频容器（AVI/MP4/MOV），无法直接按通用视频解码 SER 流。"
        )
        remedy_steps.append(
            f"将文件重命名为 .ser 扩展名，或在导入时显式声明 --format ser:\n"
            f"  python scripts/moon_stack.py import --input \"{video_path}\" --work /path/to/work --format ser"
        )

    elif is_fits:
        root_cause = (
            f"文件实际为标准 FITS 天文图像文件（Header 包含 'SIMPLE = T'），而非视频流文件。"
        )
        remedy_steps.append(
            f"请将文件重命名为 .fit/.fits 扩展名，或在导入时显式声明 --format fits:\n"
            f"  python scripts/moon_stack.py import --input \"{video_path}\" --work /path/to/work --format fits"
        )

    elif is_image:
        root_cause = (
            f"文件实际为单张静态图片（{image_type} 格式），并非用于幸运成像堆叠的多帧视频容器。"
        )
        remedy_steps.append("月面堆叠需要多帧连拍或视频流；单张成片可直接使用后期工具处理，无需执行多帧堆叠。")

    elif container_guess.startswith("MP4/MOV") and not has_moov and size_bytes > 4096:
        root_cause = (
            "检测到 MP4/MOV 容器缺少关键的 'moov' 元数据索引原子（moov atom missing）。\n"
            "这是 MP4 录制中极其常见的高频故障：当智能望远镜（如 Seestar）、相机在拍摄过程中突然断电、App 闪退、"
            "或存储卡被提前拔出时，未完成正常封装闭合。OpenCV 依赖 moov 索引定位关键帧与样本描述，因此彻底无法打开。"
        )
        remedy_steps.append(
            "尝试使用 ffmpeg 的忽略错误模式无损重建索引（非常快速，不重新编解码）：\n"
            f"  ffmpeg -err_detect ignore_err -i \"{video_path}\" -c copy \"{fixed_mp4}\""
        )
        remedy_steps.append(
            "若 ffmpeg 无法识别，可使用开源修复工具 untrunc，配合同一设备拍摄的正常参考视频修复该文件。"
        )

    elif is_hevc:
        root_cause = (
            f"视频编码格式为 H.265 / HEVC（FourCC: {fourcc_display}）。\n"
            "当前 Python 运行时的 OpenCV 库缺少 HEVC/H.265 硬件或软件解码器支持，导致 VideoCapture 无法解码帧。"
        )
        remedy_steps.append(
            "使用 ffmpeg 将其无损快速转码为标准高质量 AVI 容器（推荐，保留 100% 画质与帧率）：\n"
            f"  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )
        remedy_steps.append(
            "或者转换为高画质的 MJPEG 编码 AVI：\n"
            f"  ffmpeg -i \"{video_path}\" -c:v mjpeg -q:v 1 \"{fixed_avi}\""
        )

    elif is_av1:
        root_cause = (
            f"视频编码格式为 AV1（FourCC: {fourcc_display}）。\n"
            "当前环境的 OpenCV 缺少 AV1 解码器支持。"
        )
        remedy_steps.append(
            "建议使用 ffmpeg 转码为标准无损 AVI:\n"
            f"  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )

    elif is_prores:
        root_cause = (
            f"视频编码格式为 Apple ProRes（FourCC: {fourcc_display}）。\n"
            "OpenCV 默认后端无法解码 ProRes 视频流。"
        )
        remedy_steps.append(
            "建议使用 ffmpeg 转换为无损 AVI 序列:\n"
            f"  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )

    elif is_planetary_raw:
        root_cause = (
            f"视频使用了天文相机特有的未压缩原始色彩格式（FourCC: {fourcc_display}）。\n"
            "通用 OpenCV 库缺乏对该特殊调色板或 Bayer 格式的解码器映射。"
        )
        remedy_steps.append(
            "建议使用天文专用工具 PIPP (Planetary Imaging PreProcessor) 或 AutoStakkert 转换为标准 .SER 或 FITS 序列后导入。"
        )
        remedy_steps.append(
            "亦可尝试使用 ffmpeg 转封装为标准 rawvideo AVI:\n"
            f"  ffmpeg -i \"{video_path}\" -c:v rawvideo \"{fixed_avi}\""
        )

    elif cap_opened and (w <= 0 or h <= 0):
        root_cause = (
            f"OpenCV 成功打开了文件容器，但解析到的画面尺寸异常 ({w}x{h})。\n"
            "视频流的元数据头可能损坏或未声明有效的图像画幅。"
        )
        remedy_steps.append(
            "尝试使用 ffmpeg 重新打包容器:\n"
            f"  ffmpeg -i \"{video_path}\" -c copy \"{fixed_mp4}\""
        )

    elif failure_stage in ("probe_initial_frame", "unpack_zero_frames") or (cap_opened and total_reported == 0):
        root_cause = (
            f"OpenCV 成功连接到视频容器（FourCC: {fourcc_display}, 报告总帧数: {cap_frames}），\n"
            "但在尝试读取视频帧时失败（read() 返回 False/None，有效抽取帧数为 0）。\n"
            "可能原因为：关键帧索引损坏、视频流过早截断、或使用了非标准编解码器。"
        )
        remedy_steps.append(
            "使用 ffmpeg 检查视频流完整性:\n"
            f"  ffmpeg -v error -i \"{video_path}\" -f null -"
        )
        remedy_steps.append(
            "使用 ffmpeg 转码为标准高质量无损 AVI（确保 100% 兼容性）:\n"
            f"  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )

    else:
        root_cause = (
            f"OpenCV 无法打开或解析该视频文件（失败阶段: {failure_stage}）。\n"
            "常见原因：不支持的文件格式、缺失解码器后端、容器受损或访问权限受限。"
        )
        remedy_steps.append(
            "1. 确认该视频能否在常规播放器（如 VLC、IINA）中正常播放；"
        )
        remedy_steps.append(
            "2. 使用 ffmpeg 转码为本技能完美支持的标准高质量 AVI:\n"
            f"   ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )
        remedy_steps.append(
            "3. 如需更全的编解码器支持，可尝试安装带完整 FFmpeg 绑定的 OpenCV:\n"
            "   pip install --upgrade opencv-python"
        )

    lines.append(root_cause)
    lines.append("-" * 80)
    lines.append("【修复与自愈建议 / Suggested Actions】")
    for i, step in enumerate(remedy_steps, 1):
        if len(remedy_steps) == 1:
            lines.append(step)
        else:
            lines.append(f"{i}. {step}")
    lines.append("=" * 80)

    return "\n".join(lines)


VIDEO_SCORE_VERSION = 5


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _structure_quality(patch, background: float, noise: float, full_scale: float) -> float:
    """Exposure-normalized structure with a low-SNR gate and clipped-pixel mask."""
    import cv2
    f = np.asarray(patch, dtype=np.float32)
    hh = (f[0:f.shape[0]//2*2:2,0:f.shape[1]//2*2:2]-f[0:f.shape[0]//2*2:2,1:f.shape[1]//2*2:2]
          -f[1:f.shape[0]//2*2:2,0:f.shape[1]//2*2:2]+f[1:f.shape[0]//2*2:2,1:f.shape[1]//2*2:2])/2
    local = hh[f[0:f.shape[0]//2*2:2,0:f.shape[1]//2*2:2] > background+5*noise]
    if local.size >= 32:
        noise = max(noise,1.4826*float(np.median(np.abs(local-np.median(local)))))
    mask = ((f > background + 5 * noise) & (f < full_scale - full_scale / 510)).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8)).astype(bool)
    if np.count_nonzero(mask) < max(32, int(f.size*0.35)):
        return 0.0
    signal = float(np.median(f[mask])) - background
    if signal <= 5 * noise:
        return 0.0
    smooth = cv2.GaussianBlur(f, (3, 3), 0.6)
    energy = float(np.mean(cv2.Laplacian(smooth, cv2.CV_32F)[mask] ** 2))
    # ponytail: approximate residual noise after smoothing; upgrade only against measured scoring failures.
    return max(1e-12, energy - noise ** 2) / max(signal ** 2, 1e-12) * signal ** 2 / (signal ** 2 + 25 * noise ** 2)


def _video_component(gray, previous=None):
    import cv2
    step = 4 if min(gray.shape) >= 128 else 1
    small = np.ascontiguousarray(gray[::step, ::step])
    samples = small.ravel()[::8]
    bg = float(np.percentile(samples,25))
    sky = samples[samples <= bg+2].astype(np.float32)
    noise = max(0.25, 1.4826 * float(np.median(np.abs(sky - np.median(sky))))) if sky.size else 0.25
    mask = (small > bg + max(5, 5 * noise)).astype(np.uint8)
    count, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    if count <= 1:
        return None, bg, noise
    areas = stats[1:, cv2.CC_STAT_AREA]
    index = int(np.argmax(areas)) + 1
    if stats[index, cv2.CC_STAT_AREA] < 8:
        return None, bg, noise
    if previous and stats[index, cv2.CC_STAT_AREA] * step * step < previous[2] * 0.15:
        return None, bg, noise
    x, y, w, h, area = stats[index]
    center = [float(centers[index][0] * step), float(centers[index][1] * step), int(area * step * step)]
    box = [int(x * step), int(y * step), min(gray.shape[1], int((x + w) * step)), min(gray.shape[0], int((y + h) * step))]
    return (center, box, labels == index, step), bg, noise


def _scan_video_profile(video_path: Path, stride=1, roi_size=400, min_signal_ratio=0.2) -> dict:
    import cv2
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(format_video_decode_error(video_path, "probe_open"))
    rows, anchors = [], []
    initial_center = circle = last_center = None
    fitted_box = None
    tracking_reference = tracking_box = None
    track_dx = track_dy = 0.0
    total = 0
    started = time.monotonic()
    profile = {"width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))}
    log(f"pass 1: scanning every {stride} frame(s), tracked lunar features, {video_path.name}")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            index = total
            total += 1
            if index % stride:
                continue
            gray = frame[..., 1] if frame.ndim == 3 else frame
            component = None
            if fitted_box is not None and last_center and initial_center:
                sx,sy = last_center[0]-initial_center[0],last_center[1]-initial_center[1]
                tx,ty = max(0,int(fitted_box[0]+sx)-96),max(0,int(fitted_box[1]+sy)-96)
                ux,uy = min(gray.shape[1],int(fitted_box[2]+sx)+96),min(gray.shape[0],int(fitted_box[3]+sy)+96)
                component,bg,noise = _video_component(gray[ty:uy,tx:ux],initial_center)
                if component:
                    center,box,target,step = component
                    if box[0] <= 0 or box[1] <= 0 or box[2] >= ux-tx or box[3] >= uy-ty:
                        component = None
                    else:
                        center = [center[0]+tx,center[1]+ty,center[2]]
                        box = [box[0]+tx,box[1]+ty,box[2]+tx,box[3]+ty]
                        component = (center,box,target,step)
            if component is None:
                tx=ty=0;ux,uy=gray.shape[1],gray.shape[0]
                component,bg,noise = _video_component(gray,initial_center)
            center = component[0] if component else None
            if not anchors or (component and (initial_center is None or (fitted_box is None and center[2] > initial_center[2]*4))):
                fitted_box = None
                tracking_reference = None
                initial_center = center
                if component:
                    x0, y0, x1, y1 = component[1]
                    pad = max(x1-x0, y1-y0)
                    ax, ay = max(0, x0-pad), max(0, y0-pad)
                    bx, by = min(gray.shape[1], x1+pad), min(gray.shape[0], y1+pad)
                    local = gray[ay:by, ax:bx].astype(np.float32)
                    fit = _fit_lunar_limb_circle(local)
                    if fit is not None:
                        cx, cy, radius, residual = fit
                        circle = [cx+ax, cy+ay, radius, residual]
                        fitted_box = [cx+ax-radius, cy+ay-radius, cx+ax+radius, cy+ay+radius]
                        if min(fitted_box) < 0 or fitted_box[2] > gray.shape[1] or fitted_box[3] > gray.shape[0]:
                            fitted_box = None
                        psf = _measure_limb_psf_fwhm(local, circle=fit, full_scale=255.0)
                        profile["psf_fwhm"] = psf.get("fwhm_px") if psf else None
                bounds = fitted_box if fitted_box is not None else component[1] if component else [0,0,gray.shape[1],gray.shape[0]]
                ax,ay = max(0,int(bounds[0])),max(0,int(bounds[1]))
                bx,by = min(gray.shape[1],int(np.ceil(bounds[2]))),min(gray.shape[0],int(np.ceil(bounds[3])))
                lunar = gray[ay:by,ax:bx]
                size = min(128,roi_size,max(32,min(lunar.shape)//3),*lunar.shape)
                downsample = max(1,min(4,size//32))
                primary = _locate_high_contrast_roi(lunar,roi_size=size,downsample=downsample)
                first,second = _locate_dual_anchor_rois(lunar,roi_size=size,downsample=downsample)
                local_anchors = [first]
                if second is not None and (second[2] <= first[0] or second[0] >= first[2] or second[3] <= first[1] or second[1] >= first[3]):
                    local_anchors.append(second)
                if all(primary[2] <= r[0] or primary[0] >= r[2] or primary[3] <= r[1] or primary[1] >= r[3] for r in local_anchors):
                    local_anchors.append(primary)
                anchors = [(r[0]+ax,r[1]+ay,r[2]+ax,r[3]+ay) for r in local_anchors]
            if center:
                last_center = center
            dx = center[0]-initial_center[0] if center and initial_center else 0
            dy = center[1]-initial_center[1] if center and initial_center else 0
            tracking_response = None
            if fitted_box is not None:
                if tracking_reference is None:
                    lx,ly = max(0,int(fitted_box[0])-96),max(0,int(fitted_box[1])-96)
                    rx,ry = min(gray.shape[1],int(fitted_box[2])+96),min(gray.shape[0],int(fitted_box[3])+96)
                    tracking_box = [lx,ly,rx-lx,ry-ly]
                    tracking_reference = np.ascontiguousarray(gray[ly:ry:4,lx:rx:4],dtype=np.float32)
                lx,ly,tw,th = tracking_box
                px = max(0,min(int(round(lx+track_dx)),gray.shape[1]-tw))
                py = max(0,min(int(round(ly+track_dy)),gray.shape[0]-th))
                sample = np.ascontiguousarray(gray[py:py+th:4,px:px+tw:4],dtype=np.float32)
                fx,fy,tracking_response = _subpixel_phase_correlation(tracking_reference,sample,upsample_factor=4)
                if tracking_response >= 0.25:
                    dx,dy = px-lx+fx*4,py-ly+fy*4
                    track_dx,track_dy = dx,dy
            scores = []
            for x0, y0, x1, y1 in anchors:
                width, height = x1-x0, y1-y0
                sx = max(0, min(int(round(x0+dx)), gray.shape[1]-width))
                sy = max(0, min(int(round(y0+dy)), gray.shape[0]-height))
                scores.append(_structure_quality(gray[sy:sy+height, sx:sx+width], bg, noise, 255.0))
            box = None
            saturation = [0.0] * 3
            brightness = None
            if component:
                center, visible, target, step = component
                sample = frame[ty:uy:step,tx:ux:step]
                pixels = sample[target]
                if pixels.size:
                    brightness = float(np.median(pixels[..., 1] if pixels.ndim == 2 else pixels))
                    saturation = (np.mean(pixels >= 255, axis=0)[::-1].tolist() if pixels.ndim == 2 else [float(np.mean(pixels >= 255))]*3)
                if fitted_box is not None:
                    box = [min(fitted_box[0]+dx, visible[0]), min(fitted_box[1]+dy, visible[1]), max(fitted_box[2]+dx, visible[2]), max(fitted_box[3]+dy, visible[3])]
            quality = float(np.median(scores)) if component or initial_center is None else 0.0
            if index and index % 1000 == 0:
                log(f"pass 1: decoded {total} frames")
            rows.append({"index": index, "sharpness": quality, "roi_scores": scores, "center": center,
                         "box": box, "background": bg, "noise": noise, "brightness": brightness, "tracking_response":tracking_response, "tracking_shift":[dx,dy],
                         "saturation_rgb": saturation, "pts": float(cap.get(cv2.CAP_PROP_POS_MSEC))/1000})
    finally:
        cap.release()
    if not rows:
        raise ValueError(format_video_decode_error(video_path, "probe_initial_frame"))
    signals = [max(0,r["brightness"]-r["background"]) for r in rows if r["brightness"] is not None]
    reference_signal = float(np.percentile(signals,90)) if signals else 0.0
    for row in rows:
        signal = max(0,(row["brightness"] or 0)-row["background"])
        row["signal_ratio"] = signal/reference_signal if reference_signal else 0.0
        row["quality_flags"] = []
        if row["signal_ratio"] < min_signal_ratio:
            row["quality_flags"].append("low_lunar_signal")
            row["sharpness"] = 0.0
        if row["center"] is None:
            row["quality_flags"].append("target_lost")
        if max(row["saturation_rgb"]) > 0.05:
            row["quality_flags"].append("channel_clipping")
    profile.update(rows=rows, decoded_frames=total, anchors=anchors, circle=circle, reference_signal=reference_signal, scan_seconds=time.monotonic()-started)
    profile["timestamp_source"] = "opencv" if all(b["pts"] > a["pts"] for a,b in zip(rows,rows[1:])) else "unavailable"
    if shutil.which("ffprobe"):
        result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets", "-show_entries", "packet=pts_time", "-of", "json", str(video_path)], capture_output=True, text=True)
        if result.returncode == 0:
            try:
                timestamps = sorted(float(packet["pts_time"]) for packet in json.loads(result.stdout)["packets"])
                if len(timestamps) == total and all(b>a for a,b in zip(timestamps,timestamps[1:])):
                    for row in rows:
                        row["pts"] = timestamps[row["index"]]
                    profile["timestamp_source"] = "ffprobe packet PTS (presentation order)"
            except (KeyError, ValueError, TypeError):
                pass
    if profile["timestamp_source"] == "unavailable":
        for row in rows:
            row["pts"] = None
    log(f"pass 1 complete: scored {len(rows)}/{total} frames in {profile['scan_seconds']:.2f}s")
    return profile


def probe_video_seeing_profile(video_path: Path, stride=1, roi_size=400) -> list[tuple[int, float]]:
    return [(r["index"], r["sharpness"]) for r in _scan_video_profile(video_path, max(1, stride), roi_size)["rows"]]


def _cached_video_profile(video: Path, work: Path, stride: int, min_signal_ratio=0.2) -> dict:
    if not np.isfinite(min_signal_ratio) or not 0 <= min_signal_ratio <= 1:
        die("--min-signal-ratio must be between 0 and 1")
    identity = {"sha256": _source_sha256(video), "version": VIDEO_SCORE_VERSION, "stride": stride, "roi_size": 400,"min_signal_ratio":min_signal_ratio}
    path = work / "video_scores.json"
    if path.exists():
        try:
            cached = load_json(path)
            if cached.get("identity") == identity:
                log("reusing full-video scores (source hash and scoring parameters verified)")
                return {**cached, "cache_hit": True}
        except (OSError, ValueError):
            pass
    profile = _scan_video_profile(video,stride,min_signal_ratio=min_signal_ratio)
    profile.update(identity=identity, cache_hit=False)
    dump_json(path, profile)
    return profile


def _video_crop(profile, indices, margin: int, mode: str, sky_margin_ratio: float = 0.25):
    if mode == "off":
        return None, "disabled"
    boxes = [r["box"] for r in profile["rows"] if r["index"] in indices]
    if not boxes or any(b is None for b in boxes):
        return None, "unreliable lunar localization; using full frame"
    effective_margin = margin
    if profile.get("circle") and margin == 64:
        radius = float(profile["circle"][2])
        effective_margin = max(margin, int(np.ceil(radius * sky_margin_ratio)))
    box = [max(0, int(np.floor(min(b[0] for b in boxes)))-effective_margin), max(0, int(np.floor(min(b[1] for b in boxes)))-effective_margin),
           min(profile["width"], int(np.ceil(max(b[2] for b in boxes)))+effective_margin), min(profile["height"], int(np.ceil(max(b[3] for b in boxes)))+effective_margin)]
    # Even origins preserve Bayer parity before demosaicing; no pixels are rescaled.
    box[0] -= box[0] % 2
    box[1] -= box[1] % 2
    return box, "tracked lunar disc and visible limb union"



def _disk_available(work, args):
    free = shutil.disk_usage(work).free
    reserve = max(10*1024**3, int(free*0.1))
    available = max(0, free-reserve)
    cap = getattr(args, "disk_budget_gb", None)
    if cap is not None:
        if not np.isfinite(cap) or cap <= 0:
            die("--disk-budget-gb must be positive and finite")
        used = sum(p.stat().st_size for p in work.rglob("*") if p.is_file())
        available = min(available, max(0, int(cap*1024**3)-used))
    return free, reserve, available


def _disk_frame_budget(work, args, width, height, channels, requested, existing=0, scale=1.0, importing=True):
    free, reserve, available = _disk_available(work, args)
    pixels = width*height*channels
    raw = ((pixels*2+2879)//2880+1)*2880
    aligned = ((int(pixels*4*scale**2)+2879)//2880+1)*2880
    drizzle_scratch = aligned if scale > 1 else 0
    planes = _wavelet_plane_count((max(1, int(height*scale)), max(1, int(width*scale))), scale)
    wavelet_scratch = (2*planes+1)*aligned if getattr(args, "wavelet_adapt", "auto") == "auto" else 0
    def peak(n):
        return int(1.25*((max(0,n-existing)*raw if importing else 0) + n*(aligned+drizzle_scratch)+6*aligned+wavelet_scratch))
    low, high = 0, requested
    while low < high:
        middle = (low+high+1)//2
        if peak(middle) <= available:
            low = middle
        else:
            high = middle-1
    if low < min(3, requested):
        die(f"insufficient disk budget: need {peak(min(3,requested))} bytes, available {available}, shortfall {max(0,peak(min(3,requested))-available)} bytes; use a new work directory, smaller ROI or more disk space")
    return low, {"free_bytes": free, "reserve_bytes": reserve, "available_bytes": available,
                 "requested_frames": requested, "allowed_frames": low, "estimated_peak_additional_bytes": peak(low),
                 "estimated_requested_bytes": peak(requested), "scale": scale, "reduced": low < requested}


def _import_scored_video(video, work, args, seq_name):
    from astropy.io import fits
    stride = max(1, int(getattr(args, "probe_stride", 1)))
    profile = _cached_video_profile(video,work,stride,getattr(args,"min_signal_ratio",0.2))
    mode = getattr(args, "sample_mode", "adaptive")
    limit = int(getattr(args, "limit", 0))
    if limit < 0 or int(getattr(args, "roi_margin", 64)) < 0:
        die("--limit and --roi-margin must be non-negative")
    valid = [{"index": r["index"], "sharpness": r["sharpness"]} for r in profile["rows"] if r["sharpness"] > 0 and np.isfinite(r["sharpness"])]
    valid.sort(key=lambda r: r["sharpness"], reverse=True)
    selection = None
    primary = mode == "adaptive" or (mode in ("smart-top", "smart-cluster") and limit > 0)
    if mode == "adaptive":
        kept, selection = _select_frames_by_quality(valid, len(profile["rows"]), getattr(args,"select_mode","otsu"), getattr(args,"keep_percent",None), getattr(args,"quality_threshold",0.75), getattr(args,"utility_alpha",2.0), getattr(args,"utility_beta",1.0))
        pool = [r["index"] for r in valid if r["index"] in kept]
    elif mode in ("smart-top", "smart-cluster") and limit > 0:
        pool = [r["index"] for r in valid]
    else:
        start = max(0, int(getattr(args,"start_frame",0)))
        pool = list(range(start, profile["decoded_frames"]))
    if not pool:
        die("no video frames passed the import quality filter")
    crop, crop_reason = _video_crop(profile, set(pool), int(getattr(args,"roi_margin",64)), getattr(args,"video_roi","auto"), float(getattr(args, "sky_margin", 0.25) or 0.25))
    width, height = (crop[2]-crop[0], crop[3]-crop[1]) if crop else (profile["width"], profile["height"])
    signature = {"source": profile["identity"]["sha256"], "crop": crop, "force_mono": bool(getattr(args,"force_mono",False)), "debayer": getattr(args,"video_debayer","auto"), "device": _resolve_device_arg(args)}
    previous = load_json(work/"import_receipt.json") if (work/"import_receipt.json").exists() else {}
    old_video = previous.get("video_metadata",{})
    frames = _seq_frame_paths(work, seq_name)
    if frames and old_video.get("processing_signature") != signature:
        die("existing FITS use another source, ROI or channel configuration; use a new --work directory")
    mapping = dict(old_video.get("source_mapping",{}))
    if len(mapping) != len(frames) or any(not (work/f"{seq_name}{int(k):05d}.fit").exists() for k in mapping):
        die("incomplete source-frame mapping; use a new --work directory")
    for number,index in mapping.items():
        path = work/f"{seq_name}{int(number):05d}.fit"
        saved = old_video.get("source_files",{}).get(number)
        if saved and saved != _frame_identity(path):
            die("a generated source FITS was modified; use a new --work directory")
        if not saved:
            header = fits.getheader(path)
            if header.get("SRC_FRM") != index or header.get("CROP_X",0) != (crop[0] if crop else 0) or header.get("CROP_Y",0) != (crop[1] if crop else 0):
                die("source-frame metadata does not match the import receipt; use a new --work directory")
    channels = int(previous.get("channels", 1 if getattr(args,"force_mono",False) else 3))
    drizzle = str(getattr(args,"drizzle","off"))
    scale = float(drizzle) if drizzle in ("2","3") else (2.0 if drizzle == "auto" and (profile.get("psf_fwhm") is None or profile["psf_fwhm"] < 2) else 1.0)
    requested = min(limit,len(pool)) if limit > 0 else len(pool)
    count, budget = _disk_frame_budget(work,args,width,height,channels,requested,len(mapping),scale)
    if count < requested and not primary:
        scores = {r["index"]:r["sharpness"] for r in profile["rows"]}
        pool = sorted(pool[:requested],key=lambda index:scores.get(index,0),reverse=True)
    chosen = pool[:count]
    if mode == "smart-cluster" and primary:
        by_index = {r["index"]: r for r in profile["rows"]}
        times = [by_index[i]["pts"] for i in pool]
        if all(t is not None for t in times):
            lo, hi = min(times), max(times)
            bins = [[] for _ in range(10)]
            for i in pool:
                bins[min(9,int((by_index[i]["pts"]-lo)/max(hi-lo,1e-9)*10))].append(i)
            chosen = []
            while len(chosen) < count and any(bins):
                for group in bins:
                    if group and len(chosen) < count:
                        chosen.append(group.pop(0))
        else:
            die("smart-cluster requires reliable presentation timestamps; use smart-top")
    missing = sorted(set(chosen)-set(mapping.values()))
    if missing:
        info = unpack_video_to_fits(video,work,seq_name,force_mono=getattr(args,"force_mono",False), debayer=getattr(args,"video_debayer","auto"), target_indices=missing, device_override=_resolve_device_arg(args), crop_box=crop, output_start=len(mapping)+1)
        for number, index in enumerate(missing,len(mapping)+1):
            mapping[str(number)] = index
    else:
        info = dict(old_video)
    scores = {r["index"]:r for r in profile["rows"]}
    selected_rows = [scores[i] for i in chosen if i in scores]
    info.update(extracted_frames=len(mapping), width=width,height=height, source_width=profile["width"],source_height=profile["height"],
                processing_signature=signature, source_mapping=mapping,
                source_files={key:_frame_identity(work/f"{seq_name}{int(key):05d}.fit") for key in mapping},
                active_sources=chosen, primary_selected=primary,
                crop_box=crop,crop_reason=crop_reason, budget=budget, candidate_pool_count=len(pool), newly_extracted=len(missing),
                score_identity=profile["identity"], score_cache_hit=profile["cache_hit"], timestamp_source=profile["timestamp_source"],
                source_saturation_rgb=np.median([r["saturation_rgb"] for r in selected_rows],axis=0).tolist() if selected_rows else None)
    values = [r["sharpness"] for r in profile["rows"]]
    info["seeing_probe"] = {"total_probed":len(values), "sample_mode":mode,"probe_stride":stride,"selection_meta":selection,
                            "chosen_frame_count":len(chosen),"min_sharpness":min(values),"max_sharpness":max(values),
                            "p50_sharpness":float(np.median(values)),"p90_sharpness":float(np.percentile(values,90))}
    log(f"video ROI: {crop or 'full frame'} ({crop_reason}); selected {len(chosen)}/{len(pool)}, new FITS {len(missing)}")
    return info


def unpack_video_to_fits(
    video_path: Path,
    out_dir: Path,
    seq_name: str = "moon_",
    limit: int = 0,
    force_mono: bool = False,
    debayer: str = "auto",
    start_frame: int = 0,
    target_indices: list[int] | None = None,
    device_override: str | None = None,
    crop_box: list[int] | None = None,
    output_start: int = 1,
) -> dict:
    """Extract frames from an AVI/MP4/MOV video container directly into 16-bit FITS files.

    Direct-pass architecture:
      - Reads frames sequentially, writing only selected source indices when provided.
      - Autodetects RAW Bayer CFA videos (e.g. Seestar/planetary camera RAW.avi) and applies hardware demosaicing.
      - Converts BGR to RGB and normalizes 8-bit [0, 255] to 16-bit [0, 65535] ((val << 8) | val).
      - Injects standard astronomical FITS metadata (BITPIX=16, ORIG_BIT=8, FPS, etc.).
      - Skips damaged/truncated frames gracefully and returns valid extracted frame count.
    """
    import cv2
    from astropy.io import fits

    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(format_video_decode_error(video_path, "unpack_open"))

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_in_file = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fourcc_val = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    raw_chars = [chr((fourcc_val >> 8 * i) & 0xFF) for i in range(4)]
    fourcc_str = "".join([c for c in raw_chars if 32 <= ord(c) <= 126]).strip()

    if w <= 0 or h <= 0:
        cap.release()
        raise ValueError(format_video_decode_error(video_path, "invalid_resolution", cap=cap, total_reported=total_in_file))

    frame_queue = None
    if target_indices is not None:
        frame_queue = sorted(set(target_indices))
        if not frame_queue or frame_queue[0] < 0:
            cap.release()
            raise ValueError("target_indices must contain non-negative source frame indices")
        if limit > 0:
            frame_queue = frame_queue[:limit]
        log(f"pass 2 sequential extraction: writing {len(frame_queue)} selected frames...")
    elif start_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        log(f"seeking video stream to start_frame={start_frame} (of {total_in_file} total frames)...")

    out_dir.mkdir(parents=True, exist_ok=True)
    extract_count = 0

    # Parse potential sidecar text metadata (e.g. Seestar AVI sidecar)
    sidecar_meta = {}
    sidecar_txt = None
    for cand in [
        video_path.with_name(video_path.name + ".txt"),
        video_path.with_suffix(".avi.txt"),
        video_path.with_suffix(".txt"),
    ]:
        if cand.is_file():
            sidecar_txt = cand
            break

    if sidecar_txt:
        try:
            for ln in sidecar_txt.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln.startswith("[") and ln.endswith("]"):
                    sidecar_meta["SENSOR"] = ln[1:-1].strip()
                elif "=" in ln:
                    k, v = ln.split("=", 1)
                    sidecar_meta[k.strip().upper()] = v.strip()
            log(f"found sidecar metadata file: {sidecar_txt.name} ({sidecar_meta})")
        except Exception as exc:
            log(f"warning: failed to parse sidecar file {sidecar_txt}: {exc}")

    log(f"unpacking video [{video_path.name}]: {w}x{h} @ {fps:.1f} fps (reported frames: {total_in_file if total_in_file > 0 else 'stream'}, fourcc={fourcc_str})")

    # Identify the capture device once; priors are applied per frame further down
    # (after the sidecar passthrough, so explicit sidecar values still win).
    ident = identify_device(
        cli_device=device_override,
        sidecar=sidecar_meta,
        filename=video_path.name,
        sensor=sidecar_meta.get("SENSOR"),
    )
    if ident:
        log(f"device: {ident.get('label') or ident.get('id')} "
            f"(source={ident.get('match_source')}, confidence={ident.get('confidence')}"
            + (f", priors={ident.get('priors')}" if ident.get("priors") else "") + ")")

    out_channels = 1 if force_mono else 3
    detected_mode = None  # "mono", "rgb", or debayer cv2 code

    selected_ptr = 0
    next_src_frame = 0 if frame_queue is not None else max(0, start_frame)
    while True:
        if frame_queue is not None and selected_ptr >= len(frame_queue):
            break
        if limit > 0 and extract_count >= limit:
            break
        ret, frame = cap.read()
        if not ret or frame is None:
            break
        current_src_frame = next_src_frame
        next_src_frame += 1
        if frame_queue is not None:
            if current_src_frame < frame_queue[selected_ptr]:
                continue
            selected_ptr += 1

        # Check mode on first valid frame
        if detected_mode is None:
            if force_mono:
                detected_mode = "mono"
                out_channels = 1
            else:
                cv2_debayer_map = {
                    "bggr": cv2.COLOR_BayerBG2RGB,
                    "rggb": cv2.COLOR_BayerRG2RGB,
                    "grbg": cv2.COLOR_BayerGR2RGB,
                    "gbrg": cv2.COLOR_BayerGB2RGB,
                }
                debayer_req = debayer.lower()
                if debayer_req == "auto" and sidecar_meta.get("BAYER"):
                    sbayer = sidecar_meta["BAYER"].upper()
                    if sbayer in ("GR", "GRBG"):
                        debayer_req = "grbg"
                    elif sbayer in ("RG", "RGGB"):
                        debayer_req = "rggb"
                    elif sbayer in ("GB", "GBRG"):
                        debayer_req = "gbrg"
                    elif sbayer in ("BG", "BGGR"):
                        debayer_req = "bggr"

                if debayer_req in cv2_debayer_map:
                    detected_mode = cv2_debayer_map[debayer_req]
                    out_channels = 3
                    log(f"applying {'sidecar-guided' if debayer.lower() == 'auto' else 'user-specified'} Bayer demosaicing [{debayer_req.upper()}] to video frames")
                elif debayer_req == "none":
                    detected_mode = "mono" if frame.ndim == 2 or np.array_equal(frame[..., 0], frame[..., 1]) else "rgb"
                    out_channels = 1 if detected_mode == "mono" else 3
                else:  # auto
                    is_mono_carrier = (frame.ndim == 2 or (frame.ndim == 3 and np.array_equal(frame[..., 0], frame[..., 1]) and np.array_equal(frame[..., 1], frame[..., 2])))
                    if is_mono_carrier:
                        gray_sample = frame[..., 0] if frame.ndim == 3 else frame
                        # Check 2x2 subgrid variance across center lunar surface
                        s00 = float(gray_sample[0::2, 0::2].mean())
                        s01 = float(gray_sample[0::2, 1::2].mean())
                        s10 = float(gray_sample[1::2, 0::2].mean())
                        s11 = float(gray_sample[1::2, 1::2].mean())
                        grid_contrast = abs(s01 - s00) + abs(s10 - s11)
                        is_raw_hint = any(k in video_path.name.upper() for k in ("RAW", "CFA", "BAYER"))
                        if is_raw_hint or grid_contrast > 2.0:
                            # Automatically probe all 4 Bayer patterns on a lunar surface patch
                            # to mathematically select the one that minimizes high-frequency grid artifacts.
                            codes = {
                                "GBRG": cv2.COLOR_BayerGB2RGB,
                                "GRBG": cv2.COLOR_BayerGR2RGB,
                                "BGGR": cv2.COLOR_BayerBG2RGB,
                                "RGGB": cv2.COLOR_BayerRG2RGB,
                            }
                            # Probe the brightest lunar surface patch (128x128) instead of the
                            # frame center, which may be empty sky and carry no CFA information.
                            _gsm = gray_sample[::8, ::8].astype(np.float32)
                            _py, _px = np.unravel_index(
                                int(np.argmax(cv2.GaussianBlur(_gsm, (7, 7), 0))), _gsm.shape
                            )
                            _cy0 = int(np.clip(_py * 8 - 64, 0, max(h - 128, 0)))
                            _cx0 = int(np.clip(_px * 8 - 64, 0, max(w - 128, 0)))
                            test_crop = gray_sample[_cy0 : _cy0 + 128, _cx0 : _cx0 + 128]

                            best_pattern = "GBRG"
                            min_grid_metric = float("inf")
                            for pname, pcode in codes.items():
                                dem = cv2.demosaicing(test_crop, pcode)
                                dh = np.abs(dem[:, 1:].astype(float) - dem[:, :-1].astype(float)).mean()
                                dv = np.abs(dem[1:, :].astype(float) - dem[:-1, :].astype(float)).mean()
                                metric = dh + dv
                                if metric < min_grid_metric:
                                    min_grid_metric = metric
                                    best_pattern = pname

                            # R/B disambiguation: the grid-artifact metric is identical for a
                            # Bayer pattern and its R<->B mirror (e.g. GRBG vs GBRG), so it can
                            # silently select an R/B-swapped pattern. The Moon is physically warm
                            # (integrated R/G > B/G), so if the demosaiced patch is blue-dominant,
                            # switch to the warm-lunar mirror pattern.
                            mirror_patterns = {
                                "GBRG": "GRBG", "GRBG": "GBRG",
                                "RGGB": "BGGR", "BGGR": "RGGB",
                            }
                            try:
                                _dem = cv2.demosaicing(test_crop, codes[best_pattern]).astype(np.float32)
                                _msk = _dem[..., 1] > 20
                                if np.count_nonzero(_msk) > 100:
                                    _rg = _dem[..., 2][_msk].mean() / max(_dem[..., 1][_msk].mean(), 1e-6)
                                    _bg = _dem[..., 0][_msk].mean() / max(_dem[..., 1][_msk].mean(), 1e-6)
                                    if _bg > 1.05 * _rg:
                                        log(
                                            f"Bayer R/B disambiguation: {best_pattern} is blue-dominant "
                                            f"(R/G={_rg:.2f} < B/G={_bg:.2f}) -> switching to warm-lunar "
                                            f"mirror {mirror_patterns[best_pattern]}"
                                        )
                                        best_pattern = mirror_patterns[best_pattern]
                            except Exception:
                                pass

                            detected_mode = codes[best_pattern]
                            out_channels = 3
                            log(f"detected Bayer CFA pattern in video [{video_path.name}] (best={best_pattern}, residual grid metric={min_grid_metric:.2f}) -> applying OpenCV {best_pattern} demosaicing")
                        else:
                            detected_mode = "mono"
                            out_channels = 1
                    else:
                        detected_mode = "rgb"
                        out_channels = 3

        crop_applied = False
        if crop_box is not None and detected_mode in ("mono","rgb"):
            x0,y0,x1,y1 = crop_box
            if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
                cap.release()
                raise ValueError("crop_box is outside source dimensions")
            frame = frame[y0:y1,x0:x1]
            crop_applied = True
        if detected_mode == "mono":
            if frame.ndim == 3:
                mono_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                mono_frame = frame
            # 8-bit to 16-bit dynamic expansion
            fits_data = (mono_frame.astype(np.uint16) << 8) | mono_frame.astype(np.uint16)
        elif detected_mode == "rgb":
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            u16_rgb = (rgb.astype(np.uint16) << 8) | rgb.astype(np.uint16)
            fits_data = np.transpose(u16_rgb, (2, 0, 1))
        else:
            # OpenCV Bayer demosaicing code.
            # NOTE: cv2 demosaicing returns BGR-ordered planes (OpenCV convention),
            # so convert to RGB to match the rgb-mode branch and FITS/Siril RGB order.
            raw_plane = frame[..., 0] if frame.ndim == 3 else frame
            rgb = cv2.demosaicing(raw_plane, detected_mode)
            rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)
            u16_rgb = (rgb.astype(np.uint16) << 8) | rgb.astype(np.uint16)
            fits_data = np.transpose(u16_rgb, (2, 0, 1))

        if crop_box is not None and not crop_applied:
            x0, y0, x1, y1 = crop_box
            if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
                cap.release()
                raise ValueError("crop_box is outside source dimensions")
            fits_data = fits_data[..., y0:y1, x0:x1]
        extract_count += 1
        target_fit = out_dir / f"{seq_name}{output_start+extract_count-1:05d}.fit"
        if target_fit.exists():
            cap.release()
            raise ValueError(f"refusing to overwrite existing video frame: {target_fit}")

        hdu = fits.PrimaryHDU(fits_data)
        hdu.header["BITPIX"] = 16
        hdu.header["ORIG_BIT"] = 8
        hdu.header["SRC_W"] = w
        hdu.header["SRC_H"] = h
        hdu.header["CROP_X"] = crop_box[0] if crop_box else 0
        hdu.header["CROP_Y"] = crop_box[1] if crop_box else 0
        hdu.header["SRC_FMT"] = video_path.suffix.upper().lstrip(".")
        if fps > 0:
            hdu.header["FPS"] = fps
        if fourcc_str:
            hdu.header["FOURCC"] = fourcc_str
        if current_src_frame is not None:
            hdu.header["SRC_FRM"] = int(current_src_frame)

        if "SENSOR" in sidecar_meta:
            hdu.header["INSTRUME"] = sidecar_meta["SENSOR"]
        if "DATE-OBS" in sidecar_meta:
            hdu.header["DATE-OBS"] = sidecar_meta["DATE-OBS"]
        if "SITELONG" in sidecar_meta:
            try:
                hdu.header["SITELONG"] = float(sidecar_meta["SITELONG"])
            except ValueError:
                pass
        if "SITELAT" in sidecar_meta:
            try:
                hdu.header["SITELAT"] = float(sidecar_meta["SITELAT"])
            except ValueError:
                pass
        if "OBJECT" in sidecar_meta:
            hdu.header["OBJECT"] = sidecar_meta["OBJECT"]

        # Forward optical metadata from the capture sidecar when present, so the
        # drizzle auto-decision and the postprocess optical inference work from
        # real numbers instead of falling back to defaults.  Values already set
        # by the device lookup are never overwritten.
        for sc_key, fits_key in (("FOCALLEN", "FOCALLEN"), ("FOCAL", "FOCALLEN"),
                                 ("APERTURE", "APERTURE"), ("APTURE", "APERTURE"),
                                 ("XPIXSZ", "XPIXSZ"), ("PIXSIZE", "XPIXSZ")):
            if sc_key not in sidecar_meta or fits_key in hdu.header:
                continue
            try:
                val = float(sidecar_meta[sc_key])
            except (TypeError, ValueError):
                continue
            if val <= 0:
                continue
            hdu.header[fits_key] = val
            if fits_key == "XPIXSZ":
                hdu.header["YPIXSZ"] = val

        # Device-table priors fill whatever is still missing after the sidecar,
        # and tag themselves so they are never mistaken for measurements.
        _apply_device_priors(hdu.header, ident)

        hdu.writeto(target_fit, overwrite=True)

    cap.release()

    if extract_count <= 0:
        raise ValueError(format_video_decode_error(video_path, "unpack_zero_frames", total_reported=total_in_file))
    if frame_queue is not None and extract_count != len(frame_queue):
        raise ValueError(f"video ended before all selected frames were read: {extract_count}/{len(frame_queue)} extracted")

    return {
        "extracted_frames": extract_count,
        "total_in_file": total_frames_val if (total_frames_val := total_in_file) > 0 else extract_count,
        "channels": out_channels,
        "width": w,
        "height": h,
        "fps": fps,
        "fourcc": fourcc_str,
        "orig_bit_depth": 8,
        "device": _device_ident_summary(ident),
    }


# ------------------------------------------------------------------- 1. import

def cmd_import(args) -> None:
    src = Path(args.input).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()

    if not src.exists():
        die(f"input path not found: {src}")

    if work == src or work == src.parent or (src.is_dir() and work.is_relative_to(src)):
        die("--work must be isolated from the original input directory")
    work.mkdir(parents=True, exist_ok=True)
    logs_dir = work / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    fmt = getattr(args, "format", "auto")
    limit = getattr(args, "limit", 0)
    ser_debayer = getattr(args, "ser_debayer", True)
    force_mono = getattr(args, "force_mono", False)
    device_arg = _resolve_device_arg(args)
    device_override = device_arg   # 'auto' and 'none' are handled by identify_device
    device_ident = None

    fits_files, raw_files, ser_files, video_files = [], [], [], []

    if src.is_file():
        ext = src.suffix.lower()
        if ext in SER_EXTS:
            ser_files = [src]
        elif ext in VIDEO_EXTS:
            video_files = [src]
        elif ext in FIT_EXTS:
            fits_files = [src]
        elif ext in RAW_EXTS:
            raw_files = [src]
        else:
            other_video_exts = {".webm", ".ts", ".flv", ".wmv", ".m2ts"}
            if ext in other_video_exts:
                die(
                    f"检测到未直接支持的视频容器格式: {src.name} ({ext})\n"
                    f"当前直解仅支持标准格式: {', '.join(sorted(VIDEO_EXTS))}\n"
                    f"建议使用 ffmpeg 转换为高质量标准 AVI 容器后重试:\n"
                    f"  ffmpeg -i \"{src}\" -c:v rawvideo -pix_fmt bgr24 \"{src.with_suffix('.avi')}\""
                )
            die(f"unsupported single file format: {src}")
    elif src.is_dir():
        candidates = sorted(p for p in src.iterdir() if p.is_file())
        fits_files = [p for p in candidates if p.suffix.lower() in FIT_EXTS]
        raw_files = [p for p in candidates if p.suffix.lower() in RAW_EXTS]
        ser_files = [p for p in candidates if p.suffix.lower() in SER_EXTS]
        video_files = [p for p in candidates if p.suffix.lower() in VIDEO_EXTS]
    else:
        die(f"invalid input path: {src}")

    # Format filtering / prioritization
    if fmt == "ser":
        fits_files, raw_files, video_files = [], [], []
    elif fmt in ("video", "avi", "mp4"):
        fits_files, raw_files, ser_files = [], [], []
    elif fmt == "raw":
        fits_files, ser_files, video_files = [], [], []
    elif fmt == "fits":
        raw_files, ser_files, video_files = [], [], []
    else:  # auto
        if ser_files:
            fits_files, raw_files, video_files = [], [], []
        elif video_files:
            fits_files, raw_files, ser_files = [], [], []
        elif fits_files:
            raw_files = []

    seq_name = "moon_"
    rejected_fits = []
    total_frames = 0
    channels = 3
    is_video = bool(video_files)
    is_ser = bool(ser_files)
    is_raw = bool(raw_files)
    video_info = None
    ser_info = None

    video_debayer = getattr(args, "video_debayer", "auto")

    if is_video:
        video_path = video_files[0]
        if len(video_files) > 1:
            log(f"inventory: found {len(video_files)} video files in {src}; selecting primary video: {video_path.name}")
        try:
            video_info = _import_scored_video(video_path, work, args, seq_name)
        except ValueError as exc:
            die(str(exc))
        device_ident = video_info.get("device")
        total_frames = video_info["extracted_frames"]
        channels = video_info["channels"]
        log(f"video import complete: extracted {total_frames} frames ({channels} channel{'s' if channels > 1 else ''})")

    elif is_ser:
        ser_path = ser_files[0]
        if len(ser_files) > 1:
            log(f"inventory: found {len(ser_files)} SER files in {src}; selecting primary video: {ser_path.name}")
        ser_info = unpack_ser_to_fits(
            ser_path=ser_path,
            out_dir=work,
            seq_name=seq_name,
            limit=limit,
            debayer=ser_debayer,
            device_override=device_override,
        )
        device_ident = ser_info.get("device")
        total_frames = ser_info["extracted_frames"]
        channels = ser_info["channels"]
        log(f"SER import complete: extracted {total_frames} frames ({channels} channel{'s' if channels > 1 else ''})")

    elif is_raw:
        if limit > 0:
            raw_files = raw_files[:limit]
        log(f"importing {len(raw_files)} RAW frames via Siril native debayering...")
        raw_staging = work / "raw_staging"
        raw_staging.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(raw_files, 1):
            target_link = raw_staging / f"raw_{i:05d}{r.suffix.lower()}"
            if target_link.is_symlink() or target_link.exists():
                target_link.unlink()
            os.symlink(r.resolve(), target_link)
        script_lines = [
            "requires 1.4.4",
            "convert raw_ -debayer -out=..",
            "exit",
        ]
        cwd = raw_staging
        receipt = run_siril_script(args.siril, script_lines, cwd, logs_dir / "01_import.log", args.timeout)
        if receipt["exit_code"] != 0:
            die(f"RAW import failed in Siril (exit {receipt['exit_code']}); see {receipt['log']}")

        converted_fits = sorted(work.glob("raw_*.fit"))
        for i, cf in enumerate(converted_fits, 1):
            target_fit = work / f"{seq_name}{i:05d}.fit"
            if target_fit.exists():
                target_fit.unlink()
            cf.rename(target_fit)
        total_frames = len(converted_fits)
        channels = 3

    else:
        # FITS flow
        usable_fits = []
        for p in fits_files:
            if "stack" in p.name.lower():
                rejected_fits.append({"path": str(p), "reason": "name suggests stacked master"})
                continue
            try:
                h = fits_header_info(p)
                if h["bitpix"] in (-32, -64):
                    rejected_fits.append({"path": str(p), "reason": f"float master (BITPIX={h['bitpix']})"})
                    continue
                usable_fits.append(p)
            except Exception as exc:
                rejected_fits.append({"path": str(p), "reason": f"unreadable: {exc}"})

        if limit > 0:
            usable_fits = usable_fits[:limit]

        log(f"inventory: found {len(usable_fits)} FITS frames ({len(rejected_fits)} rejected)")
        if not usable_fits:
            die(f"no usable frames found in {src}")

        # Detect channels from the first FITS frame
        first_h = fits_header_info(usable_fits[0])
        channels = first_h.get("channels") or 1
        if first_h.get("naxis") == 2:
            channels = 1

        # Identify the capture device from the frames themselves.  These files are
        # the user's originals behind symlinks, so nothing is written back: the
        # identification is recorded in the receipt for the caller to act on.
        device_ident = _device_ident_summary(identify_device(
            cli_device=device_override,
            header={"TELESCOP": first_h.get("telescope"), "INSTRUME": first_h.get("instrument")},
            filename=usable_fits[0].name,
            sensor=first_h.get("instrument"),
        ))
        if device_ident.get("id") or device_ident.get("label"):
            log(f"device: {device_ident.get('label') or device_ident.get('id')} "
                f"(source={device_ident.get('match_source')}, "
                f"confidence={device_ident.get('confidence')}) - read-only identification, "
                f"source FITS headers are left untouched")

        log(f"importing {len(usable_fits)} FITS frames ({channels} channel{'s' if channels > 1 else ''}) via safe symlinks...")
        for i, f in enumerate(usable_fits, 1):
            target_link = work / f"{seq_name}{i:05d}.fit"
            if target_link.is_symlink() or target_link.exists():
                target_link.unlink()
            os.symlink(f.resolve(), target_link)
        total_frames = len(usable_fits)

    if total_frames <= 0:
        die(f"no usable frames imported from {src}")

    # Build standard Siril .seq file with dynamic channel (L 1 or L 3)
    seq_lines = [
        "#Siril sequence file. Generated by siril-moon-stacking",
        f"S '{seq_name}' 1 {total_frames} {total_frames} 5 -1 6 0 0 0",
        f"L {channels}",
    ]
    active_sources = set(video_info.get("active_sources", [])) if video_info else None
    selected_count = 0
    for i in range(1, total_frames + 1):
        included = active_sources is None or video_info["source_mapping"][str(i)] in active_sources
        selected_count += int(included)
        seq_lines.append(f"I {i} {int(included)}")
    seq_lines[1] = f"S '{seq_name}' 1 {total_frames} {selected_count} 5 -1 6 0 0 0"

    seq_path = work / f"{seq_name}.seq"
    seq_path.write_text("\n".join(seq_lines) + "\n", encoding="utf-8")

    orig_bit_depth = 16
    if is_video:
        orig_bit_depth = 8
    elif is_ser and ser_info:
        orig_bit_depth = ser_info.get("metadata", {}).get("pixel_depth", 16)

    device_record = dict(device_ident or {})
    if device_record:
        injected = bool(is_video or is_ser) and bool(device_record.get("priors"))
        device_record["injected"] = injected
        device_record["target"] = "fits_header" if injected else "none"

    import_info = {
        "input": str(src),
        "work": str(work),
        "is_video": is_video,
        "is_ser": is_ser,
        "is_raw": is_raw,
        "orig_bit_depth": orig_bit_depth,
        "channels": channels,
        "frame_count": total_frames,
        "sequence_name": seq_name,
        "seq_file": str(seq_path),
        "rejected": rejected_fits,
        "device": device_record or None,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if is_video and video_info:
        import_info["video_metadata"] = video_info
    if is_ser and ser_info:
        import_info["ser_metadata"] = ser_info.get("metadata", {})

    dump_json(work / "import_receipt.json", import_info)
    log(f"import complete: sequence '{seq_name}' ready in {work} ({total_frames} frames, L {channels})")


# ----------------------------------------------------------------- 2. register

def _locate_high_contrast_roi(plane, roi_size: int = 1024, downsample: int = 4) -> tuple[int, int, int, int]:
    """Locate the primary lunar terrain region with maximum texture gradient."""
    import cv2
    import numpy as np

    h, w = plane.shape
    small = cv2.resize(plane, (w // downsample, h // downsample), interpolation=cv2.INTER_AREA)

    gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(gx * gx + gy * gy)

    thr = small.mean() + 0.5 * small.std()
    grad_mag[small <= thr] = 0.0

    sh, sw = small.shape
    s_roi = max(32, roi_size // downsample)
    step = max(8, s_roi // 4)

    best_score = -1.0
    best_x, best_y = sw // 2, sh // 2

    for sy in range(0, max(1, sh - s_roi), step):
        for sx in range(0, max(1, sw - s_roi), step):
            patch = grad_mag[sy : sy + s_roi, sx : sx + s_roi]
            score = float(patch.sum())
            if score > best_score:
                best_score = score
                best_x = sx + s_roi // 2
                best_y = sy + s_roi // 2

    cx = best_x * downsample
    cy = best_y * downsample

    half = roi_size // 2
    x0 = int(min(max(cx - half, 0), max(w - roi_size, 0)))
    y0 = int(min(max(cy - half, 0), max(h - roi_size, 0)))
    x1 = min(w, x0 + roi_size)
    y1 = min(h, y0 + roi_size)
    return x0, y0, x1, y1


def _locate_dual_anchor_rois(plane, roi_size: int = 512, downsample: int = 4) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int] | None]:
    """Locate two distinct, well-separated high-contrast lunar terrain ROIs for rigid rotation estimation."""
    import cv2
    import numpy as np

    h, w = plane.shape
    small = cv2.resize(plane, (w // downsample, h // downsample), interpolation=cv2.INTER_AREA)

    gx = cv2.Sobel(small, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(small, cv2.CV_32F, 0, 1, ksize=3)
    grad_mag = np.sqrt(gx * gx + gy * gy)

    thr = small.mean() + 0.5 * small.std()
    grad_mag[small <= thr] = 0.0

    sh, sw = small.shape
    s_roi = max(32, roi_size // downsample)
    step = max(8, s_roi // 2)

    candidates = []
    for sy in range(0, max(1, sh - s_roi), step):
        for sx in range(0, max(1, sw - s_roi), step):
            score = float(grad_mag[sy : sy + s_roi, sx : sx + s_roi].sum())
            candidates.append((score, sx + s_roi // 2, sy + s_roi // 2))

    if not candidates:
        r1 = _locate_high_contrast_roi(plane, roi_size=roi_size, downsample=downsample)
        return r1, None

    candidates.sort(key=lambda c: c[0], reverse=True)
    best1 = candidates[0]
    cx1 = best1[1] * downsample
    cy1 = best1[2] * downsample

    half = roi_size // 2
    r1 = (
        int(min(max(cx1 - half, 0), max(w - roi_size, 0))),
        int(min(max(cy1 - half, 0), max(h - roi_size, 0))),
        min(w, int(min(max(cx1 - half, 0), max(w - roi_size, 0))) + roi_size),
        min(h, int(min(max(cy1 - half, 0), max(h - roi_size, 0))) + roi_size),
    )

    # Secondary anchor must be sufficiently far away (at least 1/4 of frame dimension)
    min_distance = min(w, h) // 4
    best2 = None
    for cand in candidates[1:]:
        c_x = cand[1] * downsample
        c_y = cand[2] * downsample
        if np.hypot(c_x - cx1, c_y - cy1) >= min_distance:
            best2 = cand
            break

    if not best2:
        return r1, None

    cx2 = best2[1] * downsample
    cy2 = best2[2] * downsample
    r2 = (
        int(min(max(cx2 - half, 0), max(w - roi_size, 0))),
        int(min(max(cy2 - half, 0), max(h - roi_size, 0))),
        min(w, int(min(max(cx2 - half, 0), max(w - roi_size, 0))) + roi_size),
        min(h, int(min(max(cy2 - half, 0), max(h - roi_size, 0))) + roi_size),
    )
    return r1, r2


def _compute_rigid_homography(theta: float, dx: float, dy: float, cx: float, cy: float) -> str:
    """Format Siril R0 homography matrix for rigid rotation (theta in rad) + translation (dx, dy).
    Coordinates follow Siril FITS convention: Y=0 at bottom, increasing upwards.
    """
    import math

    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    h11 = cos_t
    h12 = sin_t
    h13 = cx * (1.0 - cos_t) - cy * sin_t - dx
    h21 = -sin_t
    h22 = cos_t
    h23 = cy * (1.0 - cos_t) + cx * sin_t + dy
    return f"H {h11:.6f} {h12:.6f} {h13:.4f} {h21:.6f} {h22:.6f} {h23:.4f} 0 0 1"


def _read_frame_plane(path: Path):
    """Read single green/mono 2D plane safely with memmap=False."""
    import numpy as np
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
        if data.ndim == 3:
            plane = data[1] if data.shape[0] == 3 else data[0]
        else:
            plane = data
        return np.asarray(plane, dtype=np.float32)


def _subpixel_phase_correlation(ref, img, upsample_factor: int = 20) -> tuple[float, float, float]:
    """Compute subpixel displacement (dx, dy) and confidence response (1 - error).
    Prefers skimage.registration.phase_cross_correlation (Guizar-Sicairos single-step DFT)
    for true subpixel accuracy down to 1/20th of a pixel, with OpenCV Hann fallback.
    """
    import numpy as np

    # Normalize to [0.0, 1.0] float64 to completely eliminate large-frame FFT power overflow
    a = np.asarray(ref, dtype=np.float64)
    b = np.asarray(img, dtype=np.float64)
    max_a, max_b = a.max(), b.max()
    if max_a > 0:
        a /= max_a
    if max_b > 0:
        b /= max_b

    try:
        from skimage.registration import phase_cross_correlation
        shift, error, _ = phase_cross_correlation(a, b, upsample_factor=upsample_factor, normalization=None)
        # shift is [row_shift, col_shift]
        dx = float(-shift[1])
        dy = float(-shift[0])
        # In skimage with normalization=None, error is normalized RMS error:
        # ~0.0 for matching shifted image, ~1.0 for noise / non-correlation.
        response = float(max(0.0, 1.0 - error))
        return dx, dy, response
    except ImportError:
        import cv2
        h, w = ref.shape
        win = cv2.createHanningWindow((w, h), cv2.CV_32F)
        c = np.array(a, dtype=np.float32, copy=True)
        d = np.array(b, dtype=np.float32, copy=True)
        (dx, dy), resp = cv2.phaseCorrelate(c, d, win)
        return float(dx), float(dy), float(resp)


def _measure_sharpness(patch) -> float:
    """Variance of Laplacian on lunar surface."""
    import cv2
    import numpy as np

    f = np.asarray(patch, dtype=np.float32)
    return float(cv2.Laplacian(f, cv2.CV_32F).var())


def align_rgb_channels(master_path: Path, patch_size: int = 512) -> dict:
    """Subpixel Atmospheric Dispersion Correction (ADC) for 3-channel lunar masters.
    Aligns Red and Blue channels to Green channel via Sobel physical edge gradient phase correlation,
    providing albedo-invariant, highly accurate chromatic alignment across lunar limb and craters.
    """
    import cv2
    import numpy as np
    from astropy.io import fits

    with fits.open(master_path, mode="update", memmap=False) as hdul:
        data = hdul[0].data
        if data.ndim != 3 or data.shape[0] < 3:
            return {"applied": False, "reason": "not 3-channel color data"}

        c, h, w = data.shape
        g_full = np.ascontiguousarray(data[1], dtype=np.float32)
        r_native = np.ascontiguousarray(data[0], dtype=np.float32)
        b_native = np.ascontiguousarray(data[2], dtype=np.float32)

        # Sobel edge gradient magnitude isolates geometric boundaries (lunar limb, crater rims)
        # completely decoupling chromatic albedo differences from physical spatial alignment
        grad_g = cv2.magnitude(cv2.Sobel(g_full, cv2.CV_32F, 1, 0), cv2.Sobel(g_full, cv2.CV_32F, 0, 1))
        grad_r = cv2.magnitude(cv2.Sobel(r_native, cv2.CV_32F, 1, 0), cv2.Sobel(r_native, cv2.CV_32F, 0, 1))
        grad_b = cv2.magnitude(cv2.Sobel(b_native, cv2.CV_32F, 1, 0), cv2.Sobel(b_native, cv2.CV_32F, 0, 1))

        rx0, ry0, rx1, ry1 = _locate_high_contrast_roi(grad_g, roi_size=patch_size)

        g_patch = grad_g[ry0:ry1, rx0:rx1]
        r_patch = grad_r[ry0:ry1, rx0:rx1]
        b_patch = grad_b[ry0:ry1, rx0:rx1]

        ph, pw = g_patch.shape
        win = cv2.createHanningWindow((pw, ph), cv2.CV_32F)

        (dx_r, dy_r), resp_r = cv2.phaseCorrelate(g_patch - g_patch.mean(), r_patch - r_patch.mean(), win)
        (dx_b, dy_b), resp_b = cv2.phaseCorrelate(g_patch - g_patch.mean(), b_patch - b_patch.mean(), win)

        # In phaseCorrelate(G, R), (dx, dy) is shift from G to R.
        # To bring R back into alignment with G, shift by (-dx, -dy).
        shift_r = (-float(dx_r), -float(dy_r))
        shift_b = (-float(dx_b), -float(dy_b))

        applied = False

        if abs(dx_r) > 0.03 or abs(dy_r) > 0.03:
            M_r = np.float32([[1, 0, shift_r[0]], [0, 1, shift_r[1]]])
            r_warped = cv2.warpAffine(r_native, M_r, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            data[0] = r_warped.astype(data.dtype)
            applied = True

        if abs(dx_b) > 0.03 or abs(dy_b) > 0.03:
            M_b = np.float32([[1, 0, shift_b[0]], [0, 1, shift_b[1]]])
            b_warped = cv2.warpAffine(b_native, M_b, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
            data[2] = b_warped.astype(data.dtype)
            applied = True

        hdul.flush()
        return {
            "applied": applied,
            "shift_r": shift_r,
            "shift_b": shift_b,
            "response_r": float(resp_r),
            "response_b": float(resp_b),
            "roi": [rx0, ry0, rx1, ry1],
        }


def _select_frames_by_quality(
    valid_items: list[dict],
    total_count: int,
    select_mode: str = "otsu",
    keep_percent: float | None = None,
    quality_threshold: float = 0.75,
    utility_alpha: float = 2.0,
    utility_beta: float = 1.0,
) -> tuple[set[int], dict]:
    """Intelligent frame selection based on reference frame quality or user criteria.

    Modes:
      - 'otsu' (default): Adaptive bimodal clustering using Otsu's threshold on the 1D
        sharpness spectrum. Automatically identifies natural seeing breakpoint between
        calm seeing and turbulent/blurred frames without arbitrary manual percentages.
      - 'utility' / 'mtf-snr': MTF-SNR joint utility optimization model. Maximizes
        U(k) = (mean_contrast_norm(k) ** alpha) * (sqrt(k / N) ** beta), finding
        the mathematically optimal trade-off between optical resolution and noise reduction.
      - 'relative': Retains frames with sharpness >= quality_threshold * reference_sharpness.
      - 'percent': Traditional percentage ranking (with small-sample tiering if keep_percent is None).
    """
    import numpy as np

    if not valid_items:
        return set(), {"mode": select_mode, "kept_count": 0, "kept_percent": 0.0}

    valid_items.sort(key=lambda it: it["sharpness"], reverse=True)
    ref_sharpness = float(valid_items[0]["sharpness"])
    sharpnesses = np.array([it["sharpness"] for it in valid_items], dtype=np.float64)

    # Normalize select_mode
    if select_mode in ("mtf-snr", "mtf_snr"):
        select_mode = "utility"

    # If keep_percent is explicitly specified by user (> 0), honor it as 'percent' mode
    if keep_percent is not None and keep_percent > 0:
        select_mode = "percent"

    meta: dict = {
        "mode": select_mode,
        "ref_sharpness": ref_sharpness,
    }

    if select_mode == "otsu":
        if len(valid_items) < 4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = float(valid_items[-1]["sharpness"])
            meta["threshold_rel"] = 1.0
            meta["reason"] = "sample size < 4, retained all valid frames"
        elif (sharpnesses.max() - sharpnesses.min()) < 1e-4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = float(sharpnesses.min())
            meta["threshold_rel"] = 1.0
            meta["reason"] = "uniform sharpness distribution"
        else:
            spread_rel = float((sharpnesses.max() - sharpnesses.min()) / max(ref_sharpness, 1e-6))
            if len(valid_items) >= 5 and spread_rel < 0.06:
                # Ultra-calm seeing window: entire sequence is uniformly high quality (spread < 6%).
                # Avoid aggressive bimodal cutting; expand selection to 75% for optimal SNR.
                target_k = min(len(valid_items), max(3, int(round(total_count * 0.75))))
                otsu_kept = valid_items[:target_k]
                meta["threshold"] = float(valid_items[target_k - 1]["sharpness"])
                meta["threshold_rel"] = round(meta["threshold"] / ref_sharpness, 4)
                meta["reason"] = f"ultra-calm seeing window detected (spread={spread_rel:.3f} < 0.06), expanded to 75% frames for optimal SNR"
                kept_indices = {it["index"] for it in otsu_kept}
            else:
                from skimage.filters import threshold_otsu

                th = float(threshold_otsu(sharpnesses))
                meta["threshold"] = th
                meta["threshold_rel"] = round(th / ref_sharpness, 4)
                otsu_kept = [it for it in valid_items if it["sharpness"] >= th]
                # Safety bounds: keep at least 3 frames (or all if < 3) and at least 10%
                min_k = min(len(valid_items), max(3, int(round(total_count * 0.10))))
                if len(otsu_kept) < min_k:
                    otsu_kept = valid_items[:min_k]
                    meta["safety_clamped"] = "min_k"
                kept_indices = {it["index"] for it in otsu_kept}

    elif select_mode == "utility":
        # MTF-SNR joint utility optimization
        s_min = float(sharpnesses.min())
        s_max = float(sharpnesses.max())
        n_valid = len(valid_items)

        if n_valid < 4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = float(valid_items[-1]["sharpness"])
            meta["threshold_rel"] = 1.0
            meta["reason"] = "sample size < 4, retained all valid frames"
        elif (s_max - s_min) < 1e-4:
            kept_indices = {it["index"] for it in valid_items}
            meta["threshold"] = s_min
            meta["threshold_rel"] = 1.0
            meta["reason"] = "uniform sharpness distribution"
        else:
            # Baseline-adjusted normalized sharpness in [0, 1]
            q_norm = (sharpnesses - s_min) / (s_max - s_min + 1e-6)
            min_k = min(n_valid, max(3, int(round(total_count * 0.10))))
            max_k = min(n_valid, max(min_k, int(round(total_count * 0.95))))

            utility_scores = []
            for k in range(1, n_valid + 1):
                q_mean = float(np.mean(q_norm[:k]))
                snr_factor = float(np.sqrt(k / total_count))
                u = (q_mean ** float(utility_alpha)) * (snr_factor ** float(utility_beta))
                utility_scores.append(u)

            search_scores = utility_scores[min_k - 1 : max_k]
            best_k_offset = int(np.argmax(search_scores))
            best_k = min_k + best_k_offset

            u_kept = valid_items[:best_k]
            kept_indices = {it["index"] for it in u_kept}
            cutoff_s = float(valid_items[best_k - 1]["sharpness"])
            meta["threshold"] = cutoff_s
            meta["threshold_rel"] = round(cutoff_s / ref_sharpness, 4)
            meta["utility_alpha"] = float(utility_alpha)
            meta["utility_beta"] = float(utility_beta)
            meta["max_utility"] = round(float(utility_scores[best_k - 1]), 4)

    elif select_mode == "relative":
        abs_threshold = ref_sharpness * float(quality_threshold)
        rel_kept = [it for it in valid_items if it["sharpness"] >= abs_threshold]
        min_k = min(len(valid_items), max(3, int(round(total_count * 0.10))))
        if len(rel_kept) < min_k:
            rel_kept = valid_items[:min_k]
            meta["safety_clamped"] = "min_k"
        kept_indices = {it["index"] for it in rel_kept}
        meta["threshold"] = abs_threshold
        meta["threshold_rel"] = float(quality_threshold)

    else:  # percent
        if keep_percent is None or keep_percent <= 0:
            if total_count <= 60:
                keep_percent = 70.0
            elif total_count <= 300:
                keep_percent = 50.0
            else:
                keep_percent = 30.0
        n_keep = max(1, int(round(total_count * float(keep_percent) / 100.0)))
        kept_indices = {it["index"] for it in valid_items[:n_keep]}
        meta["keep_percent"] = float(keep_percent)
        if n_keep <= len(valid_items):
            cutoff_s = float(valid_items[n_keep - 1]["sharpness"])
            meta["threshold"] = cutoff_s
            meta["threshold_rel"] = round(cutoff_s / ref_sharpness, 4)

    meta["kept_count"] = len(kept_indices)
    meta["kept_percent"] = round(len(kept_indices) / total_count * 100.0, 2)
    return kept_indices, meta


def _siril_homographies(matrices: list[str]) -> tuple[list[str], int]:
    """Translate the common output origin to avoid Siril 1.4.4 zero-sum rejection."""
    values = [list(map(float, matrix.split()[1:])) for matrix in matrices]
    sums = [sum(value) for value in values]
    if all(abs(total) > 1e-8 for total in sums):
        return matrices, 0
    offset = 1
    while any(abs(total+offset) <= 1e-8 for total in sums):
        offset += 1
    for value in values:
        value[2] += offset
    return ["H " + " ".join(f"{v:.8f}" for v in value) for value in values], offset


def _frame_identity(path):
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def cmd_register(args) -> None:
    from astropy.io import fits

    work = Path(args.work).expanduser().resolve()
    seq_path = work / f"{args.seq}.seq"
    if not seq_path.exists():
        die(f"sequence file not found: {seq_path}; run import first")

    # Parse .seq file to get image list
    seq_lines = seq_path.read_text(encoding="utf-8").splitlines()
    s_line = next((ln for ln in seq_lines if ln.startswith("S ")), None)
    if not s_line:
        die("invalid .seq file: missing S header line")

    parts = s_line.split()
    seq_name = parts[1].strip("'\"")
    start_idx = int(parts[2])
    nb_images = int(parts[3])
    fixed_len = int(parts[5])

    image_files = []
    for i in range(start_idx, start_idx + nb_images):
        fname = f"{seq_name}{i:0{fixed_len}d}.fit"
        fpath = work / fname
        if not fpath.exists():
            die(f"missing frame in sequence: {fpath}")
        image_files.append({"index": i, "file": fname, "path": fpath})

    log(f"registering {len(image_files)} frames from sequence '{seq_name}'...")

    imported = load_json(work/"import_receipt.json") if (work/"import_receipt.json").exists() else {}
    video = imported.get("video_metadata", {})
    signature = {"processing": video.get("processing_signature"), "scores": video.get("score_identity"),
                 "roi": args.roi, "min_confidence": args.min_confidence}
    cache_path = work/"registration_cache.json"
    previous_path = cache_path if cache_path.exists() else work/"ranking.json"
    previous = load_json(previous_path) if previous_path.exists() else {}
    cached = {}
    if video.get("processing_signature") and previous.get("registration_signature") == signature:
        cached = {r["index"]:r for r in previous.get("frames", [])}
        ref = int(previous.get("reference_index") or 0)
        ref_file = work/f"{seq_name}{ref:05d}.fit"
        if not ref_file.exists() or cached.get(ref,{}).get("identity") != _frame_identity(ref_file):
            cached = {}
    included = {int(line.split()[1]) for line in seq_lines if line.startswith("I ") and line.split()[2] == "1"}
    primary = bool(video.get("primary_selected")) and getattr(args,"register_selection","auto") == "auto"
    source_mapping = video.get("source_mapping", {})
    quality = {}
    score_path = work/"video_scores.json"
    if video and score_path.exists():
        profile = load_json(score_path)
        if profile.get("identity") == video.get("score_identity"):
            quality = {r["index"]:r["sharpness"] for r in profile["rows"]}
    for item in image_files:
        item["identity"] = _frame_identity(item["path"])
        item["source_index"] = source_mapping.get(str(item["index"]))
        item["cached"] = cached.get(item["index"], {})
        if item["cached"].get("identity") != item["identity"]:
            item["cached"] = {}

    # Choose candidate reference frame (center of sequence) to establish ROI
    previous_reference = previous.get("reference_index") if cached else None
    mid_frame = next((it for it in image_files if it["index"] == previous_reference), image_files[len(image_files)//2])
    mid_plane = _read_frame_plane(mid_frame["path"])
    roi = _locate_high_contrast_roi(mid_plane, roi_size=args.roi)
    rx0, ry0, rx1, ry1 = roi
    log(f"localized high-contrast lunar feature ROI: [{rx0}:{rx1}, {ry0}:{ry1}] ({rx1-rx0}x{ry1-ry0})")

    # Pass 1: compute phase shift relative to candidate frame
    raw_shifts = []
    for item in image_files:
        saved = item["cached"]
        if saved:
            item.update(coarse_dx=saved["shift"][0], coarse_dy=saved["shift"][1], resp=saved["response"], sharpness=saved["sharpness"])
            continue
        plane = _read_frame_plane(item["path"])
        dx, dy, resp = _subpixel_phase_correlation(mid_plane, plane)
        item["coarse_dx"] = dx
        item["coarse_dy"] = dy
        item["resp"] = resp

        # Measure sharpness on aligned feature ROI.
        # Clamp the shifted ROI to valid bounds: when the feature ROI spans (or is
        # close to) the full frame, even a sub-pixel shift pushes it out of bounds and
        # the original code forced sharpness=0, discarding the frame. Clamping keeps a
        # valid in-bounds patch so every frame gets a real sharpness score.
        rw = rx1 - rx0
        rh = ry1 - ry0
        sx0 = int(round(rx0 + dx))
        sy0 = int(round(ry0 + dy))
        sx0 = max(0, min(sx0, plane.shape[1] - rw))
        sy0 = max(0, min(sy0, plane.shape[0] - rh))
        sx1 = sx0 + rw
        sy1 = sy0 + rh
        patch = plane[sy0:sy1, sx0:sx1]
        sharpness = _measure_sharpness(patch)
        item["sharpness"] = quality.get(item["source_index"], sharpness)

    # Exclude outliers with low phase correlation response (clouds, extreme shake)
    valid_items = [it for it in image_files if it["index"] in included and it["resp"] >= args.min_confidence and it["sharpness"] > 0]
    if not valid_items:
        die("no frames met the correlation confidence threshold; check data quality or lower --min-confidence")

    # Pick the sharpest frame as true reference
    best_item = next((it for it in image_files if it["index"] == previous_reference), max(valid_items, key=lambda it: it["sharpness"]))
    ref_idx = best_item["index"]
    ref_dx = 0.0 if cached else best_item["coarse_dx"]
    ref_dy = 0.0 if cached else best_item["coarse_dy"]
    log(f"selected reference frame: #{ref_idx} (sharpness={best_item['sharpness']:.1f}, resp={best_item['resp']:.2f})")

    # Pass 2: Dual-anchor rigid transformation estimation (field rotation theta + translation dx, dy)
    import math

    ref_item = next(it for it in image_files if it["index"] == ref_idx)
    ref_plane = _read_frame_plane(ref_item["path"])
    fh, fw = ref_plane.shape
    cx_img, cy_img = fw / 2.0, fh / 2.0

    dual_roi1, dual_roi2 = _locate_dual_anchor_rois(ref_plane, roi_size=min(args.roi, 512))
    has_dual_anchor = dual_roi2 is not None
    if has_dual_anchor:
        log(f"dual anchors localized for rigid rotation: Anchor 1={dual_roi1}, Anchor 2={dual_roi2}")
        p1_center = ((dual_roi1[0] + dual_roi1[2]) / 2.0, (dual_roi1[1] + dual_roi1[3]) / 2.0)
        p2_center = ((dual_roi2[0] + dual_roi2[2]) / 2.0, (dual_roi2[1] + dual_roi2[3]) / 2.0)
        ref_p1 = ref_plane[dual_roi1[1]:dual_roi1[3], dual_roi1[0]:dual_roi1[2]]
        ref_p2 = ref_plane[dual_roi2[1]:dual_roi2[3], dual_roi2[0]:dual_roi2[2]]
        vec_ref = (p2_center[0] - p1_center[0], p2_center[1] - p1_center[1])
        angle_ref = math.atan2(vec_ref[1], vec_ref[0])
    else:
        log(f"single anchor localized: {dual_roi1}")
        ref_p1 = ref_plane[dual_roi1[1]:dual_roi1[3], dual_roi1[0]:dual_roi1[2]]

    for it in image_files:
        saved = it["cached"]
        if saved:
            it.update(dx=saved["shift"][0], dy=saved["shift"][1], theta=math.radians(saved["rotation_deg"]), homography=saved["homography"])
            continue
        plane = _read_frame_plane(it["path"])
        dx_c = it["coarse_dx"] - ref_dx
        dy_c = it["coarse_dy"] - ref_dy

        sx0 = int(round(dual_roi1[0] + dx_c))
        sy0 = int(round(dual_roi1[1] + dy_c))
        sx1 = sx0 + (dual_roi1[2] - dual_roi1[0])
        sy1 = sy0 + (dual_roi1[3] - dual_roi1[1])
        if sx0 >= 0 and sy0 >= 0 and sx1 <= fw and sy1 <= fh:
            target_p1 = plane[sy0:sy1, sx0:sx1]
            f_dx1, f_dy1, resp1 = _subpixel_phase_correlation(ref_p1, target_p1)
            total_dx = dx_c + f_dx1
            total_dy = dy_c + f_dy1
        else:
            total_dx, total_dy = dx_c, dy_c

        theta = 0.0
        if has_dual_anchor:
            sx2_0 = int(round(dual_roi2[0] + dx_c))
            sy2_0 = int(round(dual_roi2[1] + dy_c))
            sx2_1 = sx2_0 + (dual_roi2[2] - dual_roi2[0])
            sy2_1 = sy2_0 + (dual_roi2[3] - dual_roi2[1])
            if sx2_0 >= 0 and sy2_0 >= 0 and sx2_1 <= fw and sy2_1 <= fh:
                target_p2 = plane[sy2_0:sy2_1, sx2_0:sx2_1]
                f_dx2, f_dy2, resp2 = _subpixel_phase_correlation(ref_p2, target_p2)
                if resp2 >= 0.15:
                    p2_mov = (p2_center[0] + dx_c + f_dx2, p2_center[1] + dy_c + f_dy2)
                    p1_mov = (p1_center[0] + total_dx, p1_center[1] + total_dy)
                    vec_mov = (p2_mov[0] - p1_mov[0], p2_mov[1] - p1_mov[1])
                    angle_mov = math.atan2(vec_mov[1], vec_mov[0])
                    d_theta = -(angle_mov - angle_ref)
                    if abs(d_theta) < 0.1:  # Physical limit: within ~5.7 degrees
                        theta = d_theta

        it["dx"] = total_dx
        it["dy"] = total_dy
        it["theta"] = theta
        it["homography"] = _compute_rigid_homography(theta, total_dx, total_dy, cx_img, cy_img)

    # Adaptive frame selection using Otsu bimodal clustering (or user-selected mode)
    select_mode = getattr(args, "select_mode", "otsu")
    keep_pct_arg = getattr(args, "keep_percent", None)
    quality_thresh = getattr(args, "quality_threshold", 0.75)
    u_alpha = getattr(args, "utility_alpha", 2.0)
    u_beta = getattr(args, "utility_beta", 1.0)

    if primary:
        kept_indices = {it["index"] for it in valid_items}
        select_meta = {"mode":"import_selection", "kept_count":len(kept_indices), "kept_percent":round(100*len(kept_indices)/len(included),2)}
    else:
        kept_indices, select_meta = _select_frames_by_quality(
            valid_items, len(included), select_mode, keep_pct_arg, quality_thresh, u_alpha, u_beta)

    for it in image_files:
        it["selected"] = it["index"] in kept_indices

    th_str = f"{select_meta['threshold']:.1f} ({select_meta.get('threshold_rel', 0)*100:.1f}% of ref)" if "threshold" in select_meta else "N/A"
    log(f"frame selection: mode='{select_meta['mode']}', cutoff={th_str}, keeping {len(kept_indices)}/{len(image_files)} best frames ({select_meta['kept_percent']}%)")

    # Inject R0 homography matrices & selection into Siril's .seq file
    new_s_line = (
        f"S '{seq_name}' {start_idx} {nb_images} {len(kept_indices)} {fixed_len} "
        f"{ref_idx - start_idx} 6 0 0 0"
    )

    # Preserve the sequence's real channel count.  `import` writes `L 1` for
    # monochrome sources (--force-mono video, mono SER/FITS); hardcoding `L 3`
    # here corrupts them, after which Siril reports "No registration data exists
    # for this sequence" and seqapplyreg aborts.
    layer_count = 1
    l_line = next((ln for ln in seq_lines if ln.startswith("L ")), None)
    if l_line:
        try:
            layer_count = max(1, int(l_line.split()[1]))
        except (IndexError, ValueError):
            layer_count = 1

    new_seq_lines = [
        "#Siril sequence file. Generated by siril-moon-stacking with rigid homography registration",
        new_s_line,
        f"L {layer_count}",
    ]
    for it in image_files:
        new_seq_lines.append(f"I {it['index']} {1 if it['selected'] else 0}")

    matrices, origin_offset = _siril_homographies([it["homography"] for it in image_files])
    for matrix in matrices:
        new_seq_lines.append(f"R0 1.0 1.0 1.0 0 0.0 1 {matrix}")

    seq_path.write_text("\n".join(new_seq_lines) + "\n", encoding="utf-8")
    log(f"updated Siril sequence file: {seq_path}")

    ranking_data = {
        "sequence": seq_name,
        "registration_signature": signature,
        "origin_offset_x": origin_offset,
        "candidate_frames": len(included),
        "registered_frames": len(valid_items),
        "reused_frames": sum(bool(it["cached"]) for it in image_files),
        "reference_index": ref_idx,
        "reference_sharpness": float(best_item["sharpness"]),
        "dual_anchors": [dual_roi1, dual_roi2] if has_dual_anchor else [dual_roi1],
        "total_frames": len(image_files),
        "kept_frames": len(kept_indices),
        "keep_percent": select_meta["kept_percent"],
        "selection_meta": select_meta,
        "frames": [
            {
                "index": it["index"],
                "source_index": it["source_index"],
                "identity": it["identity"],
                "homography": it["homography"],
                "file": it["file"],
                "sharpness": it["sharpness"],
                "response": it["resp"],
                "shift": [round(it["dx"], 4), round(it["dy"], 4)],
                "rotation_deg": round(math.degrees(it["theta"]), 4),
                "selected": it["selected"],
            }
            for it in image_files
        ],
    }
    dump_json(work / "ranking.json", ranking_data)
    if video:
        dump_json(work/"registration_cache.json",ranking_data)
    log(f"registration & ranking complete: data dumped to {work / 'ranking.json'}")


# -------------------------------------------------------------------- 3. stack

DRIZZLE_KERNELS = ("point", "turbo", "square", "gaussian", "lanczos2", "lanczos3")
DRIZZLE_AIRY_THRESHOLD_PX = 1.5
# Empirical fallback criterion: the effective PSF (optics + seeing + sampling) is
# measured from the lunar limb edge spread function.  A FWHM below ~2 px means the
# single frame is undersampled, so a finer drizzle grid can actually recover detail.
DRIZZLE_PSF_THRESHOLD_PX = 2.0
PIXEL_SIZE_KEYS = ("XPIXSZ", "YPIXSZ", "PIXSIZE", "PIXSIZE1", "PIXEL_SZ", "PIXELSIZE")
APERTURE_KEYS = ("APERTUR", "DIAMETER", "APERTURE")
FOCAL_KEYS = ("FOCALLEN", "FOCAL_LENGTH", "FOCAL")
# Siril log fragments that mean -weight=noise could not compute per-frame
# background statistics; the stack is retried without weighting.
WEIGHT_FAILURE_MARKERS = (
    "MAD is null",
    "Statistics cannot be computed",
    "Normalization failed",
    "cannot be normalized",
)


def _seq_frame_paths(work: Path, seq_name: str) -> list[Path]:
    """Numbered FITS frames of a sequence, excluding masters and other derivatives."""
    import re

    pat = re.compile(rf"^{re.escape(seq_name)}\d+\.fit$", re.IGNORECASE)
    try:
        entries = list(work.iterdir())
    except OSError:
        return []
    return sorted(p for p in entries if p.is_file() and pat.match(p.name))


def _read_first_frame_header(work: Path, seq_name: str):
    """FITS header of the first frame of a sequence (memmap=False per repo convention)."""
    from astropy.io import fits

    frames = _seq_frame_paths(work, seq_name)
    if not frames:
        return None
    try:
        with fits.open(frames[0], memmap=False) as hdul:
            return hdul[0].header.copy()
    except Exception:
        return None


def _hdr_positive_float(hdr, keys) -> float | None:
    """First strictly-positive float among `keys` in a FITS header, else None."""
    if hdr is None:
        return None
    for k in keys:
        v = hdr.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _probe_frame_for_drizzle(work: Path, seq_name: str):
    """Pick the single frame used to measure optics and sampling adequacy.

    Prefers the frame `register` picked as the sharpest reference (ranking.json),
    because limb fitting and edge-spread measurement are far cleaner on a sharp
    frame.  Falls back to the first frame of the sequence.
    """
    from astropy.io import fits

    frames = _seq_frame_paths(work, seq_name)
    if not frames:
        return None

    chosen = frames[0]
    origin = "first frame"
    ranking = work / "ranking.json"
    if ranking.exists():
        try:
            ref_idx = int(load_json(ranking).get("reference_index") or 0)
            if ref_idx > 0:
                cand = work / f"{seq_name}{ref_idx:05d}.fit"
                if cand.exists():
                    chosen = cand
                    origin = f"register reference frame #{ref_idx}"
        except Exception:
            pass

    try:
        with fits.open(chosen, memmap=False) as hdul:
            hdr = hdul[0].header.copy()
            data = hdul[0].data
    except Exception:
        return None
    if data is None:
        return None

    if data.ndim == 3:
        lum = (0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]).astype(np.float32)
    else:
        lum = np.asarray(data, dtype=np.float32)
    return hdr, lum, origin


def _pixel_full_scale(data):
    if np.issubdtype(data.dtype, np.integer):
        return float(np.iinfo(data.dtype).max)
    # ponytail: distinguish normalized FITS (including interpolation overshoot) from ADU-valued floats; ambiguous units need explicit full_scale.
    return 1.0 if np.nanpercentile(data,99.99) <= 2.0 else 65535.0


def _measure_limb_psf_fwhm(
    lum_2d: np.ndarray, circle=None, px_scale: float = 1.0, full_scale: float | None = None
) -> dict | None:
    """Measure the effective PSF FWHM, in pixels, from the lunar limb edge.

    The lunar limb against black sky is a near-perfect step edge, so the radial
    intensity profile across it *is* the edge spread function (ESF) of the whole
    optics + seeing + sampling chain.  For a Gaussian PSF the 10-90% ESF width is
    2.563 sigma, hence FWHM = 2.355 sigma = 0.919 * w10_90.

    Validated against analytic Gaussian-blurred discs: error < 1.5% across
    FWHM 0.9-7.1 px.  Requires the full limb in frame (fails on mosaic tiles and
    partial discs) -- the same precondition as the focal-length inversion.

    Note this measures the *seeing-limited* PSF, which is the quantity that
    actually decides whether a finer grid can recover detail.  It is therefore a
    sounder criterion than the diffraction limit when the two disagree.
    """
    if circle is not None:
        xc, yc, R = float(circle[0]), float(circle[1]), float(circle[2])
        res_std = None
    else:
        fit = _fit_lunar_limb_circle(lum_2d, px_scale=px_scale)
        if fit is None:
            return None
        xc, yc, R, res_std = fit

    band = _px(12.0, px_scale)
    H, W = lum_2d.shape
    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - xc) ** 2 + (yy - yc) ** 2)
    sel = np.abs(r_grid - R) <= band
    if np.count_nonzero(sel) < 500:
        return None
    rs = r_grid[sel]
    vs = lum_2d[sel].astype(np.float64)

    step = max(0.05, _px(0.1, px_scale))
    bins = np.arange(R - band, R + band + step, step)
    if bins.size < 21:
        return None
    idx = np.digitize(rs, bins)
    prof = np.full(bins.size - 1, np.nan, dtype=np.float64)
    for i in range(1, bins.size):
        m = idx == i
        if np.any(m):
            prof[i - 1] = vs[m].mean()
    rc = 0.5 * (bins[:-1] + bins[1:])
    ok = np.isfinite(prof)
    rc, prof = rc[ok], prof[ok]
    if rc.size < 20:
        return None

    lo = float(np.percentile(prof, 2))
    hi = float(np.percentile(prof, 98))
    if hi - lo <= 1e-9:
        return None
    norm = (prof - lo) / (hi - lo)

    def crossing(level: float) -> float | None:
        sign = np.sign(norm - level)
        k = np.where(np.diff(sign) != 0)[0]
        if k.size == 0:
            return None
        i = int(k[0])
        denom = norm[i + 1] - norm[i]
        if abs(denom) < 1e-12:
            return float(rc[i])
        t = (level - norm[i]) / denom
        return float(rc[i] + t * (rc[i + 1] - rc[i]))

    r90 = crossing(0.9)
    r10 = crossing(0.1)
    if r90 is None or r10 is None or r10 <= r90:
        return None
    w1090 = r10 - r90

    # A saturated inner plateau flattens the top of the ESF, which pulls the 90%
    # crossing inwards and therefore *underestimates* the FWHM.  That biases the
    # decision towards enabling drizzle, which is the safe direction, but it must
    # be visible rather than silent.
    peak = float(np.percentile(lum_2d, 99.99))
    if full_scale is None:
        full_scale = _pixel_full_scale(lum_2d)
    saturated = bool(peak >= 0.95 * full_scale)
    if saturated:
        log("drizzle auto: the lunar limb region reaches ~full scale; the measured PSF "
            "width is a lower bound and may understate the true FWHM")

    return {
        "fwhm_px": round(0.919 * w1090, 3),
        "esf_width_10_90_px": round(w1090, 3),
        "limb_radius_px": round(R, 2),
        "limb_fit_residual_std": None if res_std is None else round(float(res_std), 3),
        "possible_saturation": saturated,
    }


def _decide_drizzle(args, work: Path, seq_name: str) -> dict:
    """Decide whether to supersample the stack with Siril's HST drizzle.

    Modes (`--drizzle`):
      * 'auto' (default) - two independent criteria, tried in order:
          1. **Diffraction limit.**  Needs focal length, aperture and pixel size.
             Enables 2x when the theoretical Airy radius
             (1.22 * lambda * f/D, in pixels) is below DRIZZLE_AIRY_THRESHOLD_PX.
          2. **Measured PSF width.**  Used whenever any of the three is unknown.
             Focal length is first recovered from the frame itself by geometric
             inversion (lunar limb radius + pixel size + JPL ephemeris), and the
             effective PSF FWHM is measured from the limb edge spread function.
             Enables 2x when the FWHM is below DRIZZLE_PSF_THRESHOLD_PX.
        Aperture is geometrically unrecoverable from the image, so criterion 1
        can only fire when the aperture is known from metadata or --aperture.
      * 'off'   - never drizzle.
      * '2'/'3' - force that drizzle scale regardless of metadata.

    Every branch records the criterion, the values used and their provenance in
    the returned dict, so the decision stays auditable rather than a black box.

    Verified against Siril 1.4.4: `seqapplyreg <seq> -scale=N -drizzle` drizzles
    onto an N-times finer output grid (1024x512 -> 2046x1024 at scale 2) and
    Siril rewrites XPIXSZ accordingly.  The bundled `seqapplydrizzle` command is
    documented upstream but does not exist in this build.
    """
    mode = str(getattr(args, "drizzle", "auto") or "auto").strip().lower()
    kernel = str(getattr(args, "drizzle_kernel", "square") or "square").strip().lower()
    if kernel not in DRIZZLE_KERNELS:
        log(f"drizzle: unknown kernel '{kernel}' -> falling back to 'square'")
        kernel = "square"
    user_pixfrac = getattr(args, "drizzle_pixfrac", None)

    def pack(enabled: bool, scale: float, reason: str, **extra) -> dict:
        scale = float(scale)
        if user_pixfrac is not None:
            try:
                pf = float(user_pixfrac)
            except (TypeError, ValueError):
                pf = 1.0 / scale
        else:
            pf = 1.0 / scale if scale > 0 else 1.0
        pf = min(max(pf, 0.0), 1.0)
        k = kernel
        # Lanczos droplets are designed for resampling at the same scale; the
        # manual restricts them to scale == pixfrac == 1.0.  Siril 1.4.4 accepts
        # the combination silently, so we must guard it ourselves.
        if k.startswith("lanczos") and not (abs(scale - 1.0) < 1e-9 and abs(pf - 1.0) < 1e-9):
            log(f"drizzle: kernel '{k}' requires scale == pixfrac == 1.0 "
                f"(got scale={scale:g}, pixfrac={pf:g}); falling back to 'square'")
            k = "square"
        info = {
            "requested": mode,
            "enabled": bool(enabled),
            "scale": scale,
            "pixfrac": round(pf, 4),
            "kernel": k,
            "reason": reason,
        }
        info.update(extra)
        return info

    if mode == "off":
        return pack(False, 1.0, "user_off")

    forced = None
    if mode in ("2", "3"):
        forced = float(mode)

    if getattr(args, "mosaic_mode", "disc") == "tile" and forced is None:
        # Mosaic panels must all share one sampling scale; siril-mosaic applies
        # its own registration scale, so auto-drizzle stays out of the way.
        return pack(False, 1.0, "tile_mode_bypass",
                    hint="pass --drizzle 2 to force it (keep every tile at the same scale)")

    hdr = _read_first_frame_header(work, seq_name)
    focal = getattr(args, "focal", None) or _hdr_positive_float(hdr, FOCAL_KEYS)
    pixsz = getattr(args, "pixel_size", None) or _hdr_positive_float(hdr, PIXEL_SIZE_KEYS)
    aperture = getattr(args, "aperture", None) or _hdr_positive_float(hdr, APERTURE_KEYS)
    # Provenance of each value, so the receipt shows whether a number was supplied
    # by the user, read from the header, or filled from the device table.
    focal_source = (("user" if getattr(args, "focal", None)
                     else _hdr_source_label(hdr, "FOCALLEN", "FITS header")) if focal else None)
    pixel_source = (("user" if getattr(args, "pixel_size", None)
                     else _hdr_source_label(hdr, "XPIXSZ", "FITS header")) if pixsz else None)
    aperture_source = (("user" if getattr(args, "aperture", None)
                        else _hdr_source_label(hdr, "APERTURE", "FITS header")) if aperture else None)

    airy_threshold = float(getattr(args, "drizzle_airy_threshold", None) or DRIZZLE_AIRY_THRESHOLD_PX)
    psf_threshold = float(getattr(args, "drizzle_psf_threshold", None) or DRIZZLE_PSF_THRESHOLD_PX)

    if forced is not None:
        return pack(True, forced, "user_forced",
                    criterion="forced",
                    focal_len_mm=focal, focal_source=focal_source,
                    pixel_size_um=pixsz, aperture_mm=aperture,
                    aperture_source=aperture_source)

    # ---- measure what the header cannot tell us --------------------------
    # The lunar limb is a known step edge against black sky, so the frame itself
    # yields both the focal length (geometric inversion) and the effective PSF
    # width.  Neither needs the capture device to be identified first.
    probe = None
    probe_origin = None
    optics_probe = None
    psf_probe = None
    if not (focal and pixsz and aperture):
        probe = _probe_frame_for_drizzle(work, seq_name)
        if probe is None:
            log("drizzle auto: no FITS frame available to measure optics from")
        else:
            p_hdr, p_lum, probe_origin = probe
            try:
                optics_probe = _infer_optical_parameters(
                    p_hdr, p_lum, args, mosaic_mode="disc", px_scale=1.0)
            except Exception as exc:
                log(f"drizzle auto: optical inference on {probe_origin} failed: {exc}")
                optics_probe = None

            if optics_probe:
                px_used = str(optics_probe.get("pixel_size_source") or "")
                if not focal:
                    src = str(optics_probe.get("focal_source") or "")
                    if src.startswith("geometric inversion") and px_used.startswith("default"):
                        # The inversion scales linearly with the pixel size, so a
                        # default 3.73um guess would silently mis-scale the result.
                        log("drizzle auto: geometric inversion would use a default 3.73um pixel "
                            "size, which mis-scales the focal length; ignoring it")
                    elif not src.startswith("default") and optics_probe.get("focal_length"):
                        focal = float(optics_probe["focal_length"])
                        focal_source = src
                if not pixsz and not px_used.startswith("default") and optics_probe.get("pixel_size"):
                    pixsz = float(optics_probe["pixel_size"])
                    pixel_source = px_used
                if not aperture:
                    src = str(optics_probe.get("aperture_source") or "")
                    if not src.startswith("default") and optics_probe.get("aperture"):
                        aperture = float(optics_probe["aperture"])
                        aperture_source = src

    # ---- criterion 1: theoretical diffraction limit ----------------------
    if focal and pixsz and aperture:
        r_airy = (1.22 * 0.55 * float(focal)) / (float(aperture) * float(pixsz))
        enabled = r_airy < airy_threshold
        return pack(enabled, 2.0 if enabled else 1.0,
                    "airy_below_threshold" if enabled else "airy_at_or_above_threshold",
                    criterion="airy",
                    r_airy_px=round(r_airy, 3),
                    threshold_px=airy_threshold,
                    focal_len_mm=float(focal), focal_source=focal_source,
                    pixel_size_um=float(pixsz), pixel_size_source=pixel_source,
                    aperture_mm=float(aperture), aperture_source=aperture_source,
                    f_ratio=round(float(focal) / float(aperture), 2),
                    probe_frame=probe_origin)

    # ---- criterion 2: measured PSF width from the lunar limb -------------
    # Reached whenever the aperture is unknown: it is geometrically unrecoverable
    # from the image, so the diffraction limit simply cannot be computed.
    unavailable = [n for n, v in (("focal length", focal), ("pixel size", pixsz),
                                  ("aperture", aperture)) if not v]
    if probe is not None:
        circle = None
        disc = (optics_probe or {}).get("lunar_disc")
        if disc:
            circle = (disc["center"][0], disc["center"][1], disc["radius_px"])
        try:
            psf_probe = _measure_limb_psf_fwhm(probe[1], circle=circle, px_scale=1.0)
        except Exception as exc:
            log(f"drizzle auto: limb PSF measurement failed: {exc}")
            psf_probe = None

    if psf_probe:
        fwhm = float(psf_probe["fwhm_px"])
        enabled = fwhm < psf_threshold
        log(f"drizzle auto: {'/'.join(unavailable)} unavailable - falling back to the measured "
            f"PSF width (FWHM {fwhm:.2f}px vs threshold {psf_threshold:.2f}px, "
            f"measured on {probe_origin})")
        return pack(enabled, 2.0 if enabled else 1.0,
                    "psf_below_threshold" if enabled else "psf_at_or_above_threshold",
                    criterion="psf_fwhm",
                    psf_fwhm_px=fwhm,
                    psf_esf_width_px=psf_probe.get("esf_width_10_90_px"),
                    psf_possible_saturation=psf_probe.get("possible_saturation"),
                    limb_radius_px=psf_probe.get("limb_radius_px"),
                    limb_fit_residual_std=psf_probe.get("limb_fit_residual_std"),
                    threshold_px=psf_threshold,
                    focal_len_mm=focal, focal_source=focal_source,
                    pixel_size_um=pixsz, pixel_size_source=pixel_source,
                    aperture_mm=aperture, aperture_source=aperture_source,
                    probe_frame=probe_origin)

    log(f"drizzle auto: cannot establish sampling adequacy ({'/'.join(unavailable)} unavailable "
        f"and the lunar limb could not be measured) - drizzle disabled. Supply "
        f"--focal/--pixel-size/--aperture, or pass --drizzle 2 to force it")
    return pack(False, 1.0, "insufficient_evidence",
                criterion="none", missing=unavailable, probe_frame=probe_origin)


def _verify_master_pixel_scale(
    master: Path, drizzle: dict, work: Path, seq_name: str
) -> dict:
    """Verify (and if needed repair) the master's pixel scale after a drizzle stack.

    Siril 1.4.4 already rewrites XPIXSZ/YPIXSZ when drizzling (measured: 3.76um ->
    1.88um at scale=2, FOCALLEN and APERTURE untouched).  This acts as a safety
    net for builds that do not, and for sequences whose only pixel-size keyword
    is an alias Siril leaves alone.  Diagnostic keywords are always written so
    that downstream steps can recover the drizzle factor even without the
    stack receipt.
    """
    from astropy.io import fits

    scale = float(drizzle.get("scale", 1.0) or 1.0)
    result = {"verified": False, "rewritten": {}, "scale": scale}

    src_hdr = _read_first_frame_header(work, seq_name)
    native_px = _hdr_positive_float(src_hdr, PIXEL_SIZE_KEYS)
    expected = (native_px / scale) if native_px else None

    try:
        with fits.open(master, memmap=False, mode="update") as hdul:
            h = hdul[0].header
            if expected:
                for k in PIXEL_SIZE_KEYS:
                    v = h.get(k)
                    if v is None:
                        continue
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if abs(fv - expected) > 0.02 * expected:
                        h[k] = round(expected, 6)
                        result["rewritten"][k] = [fv, round(expected, 6)]
                result["verified"] = not result["rewritten"]
            else:
                result["verified"] = None
                log("drizzle: no native pixel-size keyword found in the input frames; "
                    "cannot verify the master's pixel scale (postprocess will fall back "
                    "to --pixel-size divided by the drizzle scale)")

            h["DRIZZLE"] = (True, "HST drizzle supersampling applied")
            h["DRZSCALE"] = (scale, "drizzle output grid scale factor")
            h["DRZPIXFR"] = (float(drizzle.get("pixfrac", 1.0)), "drizzle droplet pixel fraction")
            h["DRZKRENL"] = (str(drizzle.get("kernel", "square")), "drizzle droplet kernel")
            if native_px:
                h["ORIGXPIX"] = (round(float(native_px), 6), "native pixel size before drizzle")
            h.add_history(
                f"moon_stack: drizzle scale={scale:g} pixfrac={drizzle.get('pixfrac')} "
                f"kernel={drizzle.get('kernel')}; XPIXSZ now {expected if expected else 'unset'}"
            )
            result["native_px"] = native_px
            result["effective_px"] = expected
    except Exception as exc:
        log(f"warning: could not verify master pixel scale after drizzle: {exc}")
        result["error"] = str(exc)

    if result["rewritten"]:
        log(f"drizzle: repaired master pixel-size keywords {result['rewritten']} "
            f"(Siril left them at the native scale)")
    return result


def _stack_counts(work, seq_name, output, requested):
    exported = re.findall(r"Total:\s*(\d+) failed,\s*(\d+) exported", output)
    stacked = re.findall(r"(\d+) images have been stacked", output)
    if not exported or not stacked:
        die("cannot confirm actual Siril frame counts; inspect the align/stack log before using this master")
    failures, count = map(int, exported[-1])
    actual = int(stacked[-1])
    if actual != count or actual <= 0 or actual > requested:
        die(f"Siril frame-count mismatch: requested={requested}, exported={count}, stacked={actual}")
    resampled = work/f"r_{seq_name}.seq"
    if resampled.exists():
        included = sum(line.startswith("I ") and line.split()[2] == "1" for line in resampled.read_text().splitlines())
        if included != actual:
            die(f"Siril output sequence includes {included} frames, but stack log reports {actual}")
    return {"selected_frames":requested, "exported_frames":count, "stacked_frames":actual,
            "resampling_failed_frames":failures, "dropped_frames":requested-actual}


def cmd_stack(args) -> None:
    work = Path(args.work).expanduser().resolve()
    seq_name = args.seq
    seq_path = work / f"{seq_name}.seq"
    if not seq_path.exists():
        die(f"sequence file {seq_path} not found; run register first")

    logs_dir = work / "logs"
    master = work / args.out

    # Inspect bit depth of input frames to select stacking method:
    # - 8-bit (AVI / planetary SER): sum stack to expand dynamic range (8-bit -> 14+ bit equivalent)
    # - 16-bit+ (FITS / 16-bit SER): Winsorized rejection mean stack (rej w)
    # - Strictly excludes: median (only for dark/flat/bias), max (star trails), min
    stack_method = getattr(args, "stack_method", "auto")
    is_8bit = False

    receipt_path = work / "import_receipt.json"
    if receipt_path.exists():
        try:
            info = load_json(receipt_path)
            if info.get("orig_bit_depth") == 8 or info.get("is_video"):
                is_8bit = True
        except Exception:
            pass

    if not is_8bit:
        first_frame = next(work.glob(f"{seq_name}*.fit"), None)
        if first_frame:
            try:
                h = fits_header_info(first_frame)
                if h.get("bitpix") == 8:
                    is_8bit = True
                else:
                    from astropy.io import fits
                    with fits.open(first_frame, memmap=False) as hdul:
                        if hdul[0].header.get("ORIG_BIT") == 8:
                            is_8bit = True
            except Exception:
                pass

    # ---- stacking policy ------------------------------------------------
    # 8-bit sources used to be stacked with `sum`.  Measured behaviour of Siril
    # 1.4.4 shows `sum` rescales the result by the image maximum and performs no
    # pixel rejection whatsoever: a single hot pixel / cosmic ray / compression
    # artefact collapses the entire frame (measured 0.4926 -> 0.004984, ~100x).
    # Winsorized rejection mean keeps the same relative contrast (2.0 vs 1.97)
    # while adding rejection, inter-frame normalisation and -weight= support, so
    # it is now the default at every source depth.  `sum` stays reachable as an
    # explicit opt-in via --stack-method sum.
    use_sum = (stack_method == "sum")
    if stack_method == "auto" and is_8bit:
        log("stacking policy: 8-bit source detected; using rejection mean stacking "
            "(rej w) instead of the legacy 'sum' path - sum normalises by the image "
            "maximum and performs no pixel rejection (pass --stack-method sum to force it)")

    weight_mode = str(getattr(args, "weight", "auto") or "auto").strip().lower()
    if weight_mode not in ("auto", "none", "noise"):
        log(f"stacking policy: unknown --weight '{weight_mode}' -> treating as 'auto'")
        weight_mode = "auto"
    want_weight = (not use_sum) and weight_mode in ("auto", "noise")
    if use_sum and weight_mode == "noise":
        log("stacking policy: 'sum' does not support -weight=; noise weighting ignored")

    def build_stack_cmd(with_weight: bool) -> str:
        if use_sum:
            return f"stack r_{seq_name} sum -filter-included -out={master.stem}"
        wflag = " -weight=noise" if with_weight else ""
        return (f"stack r_{seq_name} rej w {args.sigma[0]} {args.sigma[1]} "
                f"-norm={args.norm} -filter-included{wflag} -out={master.stem}")

    if use_sum:
        log("stacking policy: applying 'sum' stacking (forced by user) - "
            "no pixel rejection, no weighting")
    else:
        log(f"stacking policy: applying Winsorized rejection mean stacking "
            f"(rej w {args.sigma[0]} {args.sigma[1]} -norm={args.norm})"
            f"{' with noise weighting' if want_weight else ''} "
            f"({'forced by user' if stack_method == 'rej' else 'default for all source depths'})")

    mosaic_mode = getattr(args, "mosaic_mode", "disc")
    framing = getattr(args, "framing", None)
    if not framing:
        framing = "max" if mosaic_mode == "tile" else "min"
    maximize_flag = " -maximize" if framing == "max" else ""

    interp_raw = getattr(args, "interp", "cu")
    interp_map = {
        "linear": "li", "bilinear": "li", "li": "li",
        "cubic": "cu", "bicubic": "cu", "cu": "cu",
        "lanczos": "la", "lanczos4": "la", "la": "la",
        "none": "no", "no": "no"
    }
    interp = interp_map.get(str(interp_raw).lower(), "cu")

    # ---- drizzle supersampling ------------------------------------------
    drizzle = _decide_drizzle(args, work, seq_name)
    if drizzle["enabled"]:
        airy_note = (f", theoretical Airy radius {drizzle['r_airy_px']}px"
                     if drizzle.get("r_airy_px") else "")
        log(f"drizzle: ENABLED scale={drizzle['scale']:g} pixfrac={drizzle['pixfrac']:g} "
            f"kernel={drizzle['kernel']} (reason={drizzle['reason']}{airy_note}) - the output "
            f"grid is {drizzle['scale']:g}x finer, expect roughly "
            f"{drizzle['scale'] ** 2:.0f}x the pixels, file size and runtime")
    else:
        log(f"drizzle: disabled (reason={drizzle['reason']})")

    seq_text = seq_path.read_text()
    selected = [int(line.split()[1]) for line in seq_text.splitlines() if line.startswith("I ") and line.split()[2] == "1"]
    source_frames = _seq_frame_paths(work, seq_name)
    if source_frames:
        header = fits_header_info(source_frames[0])
        allowed, disk_budget = _disk_frame_budget(work,args,header["width"],header["height"],header["channels"],len(selected),scale=drizzle["scale"] if drizzle["enabled"] else 1,importing=False)
        if allowed < len(selected):
            ranking = load_json(work/"ranking.json") if (work/"ranking.json").exists() else {}
            ordered = sorted([r for r in ranking.get("frames",[]) if r["index"] in selected],key=lambda r:r["sharpness"],reverse=True)
            selected = [r["index"] for r in ordered[:allowed]] if ordered else selected[:allowed]
            keep = set(selected)
            lines = seq_text.splitlines()
            for index,line in enumerate(lines):
                if line.startswith("I "):
                    lines[index] = f"I {line.split()[1]} {int(int(line.split()[1]) in keep)}"
                elif line.startswith("S "):
                    fields = line.split();fields[4] = str(allowed);lines[index] = " ".join(fields)
            seq_path.write_text("\n".join(lines)+"\n")
            log(f"disk budget reduced resampling selection to {allowed} frames")
    else:
        disk_budget = None

    # Drizzle replaces the interpolation method, so the two are mutually
    # exclusive.  Clamping (never -noclamp) is kept on the interpolation path.
    reg_drizzle = (f"seqapplyreg {seq_name} -framing={framing} -scale={drizzle['scale']:g} "
                   f"-drizzle -pixfrac={drizzle['pixfrac']:g} -kernel={drizzle['kernel']} -filter-incl")
    reg_plain = f"seqapplyreg {seq_name} -framing={framing} -interp={interp} -filter-incl"

    # Degradation ladder: prefer the richest configuration, but shed optional
    # refinements instead of failing the whole run.  `--drizzle off --weight none`
    # collapses this to a single deterministic attempt.
    ladder: list[dict] = [{"drizzle": drizzle["enabled"], "weight": want_weight}]
    if want_weight:
        ladder.append({"drizzle": drizzle["enabled"], "weight": False})
    if drizzle["enabled"] and not drizzle.get("forced"):
        ladder.append({"drizzle": False, "weight": want_weight})
        if want_weight:
            ladder.append({"drizzle": False, "weight": False})
    seen_keys = set()
    remaining: list[dict] = []
    for step in ladder:
        key = (step["drizzle"], step["weight"])
        if key not in seen_keys:
            seen_keys.add(key)
            remaining.append(step)

    receipt = None
    chosen = None
    attempt = 0
    while remaining:
        step = remaining.pop(0)
        reg_line = reg_drizzle if step["drizzle"] else reg_plain
        stk_line = build_stack_cmd(step["weight"])
        log_tag = ("drizzle" if step["drizzle"] else "interp") + \
                  ("+noise-weight" if step["weight"] else "")
        round_tag = f"_round{args.feedback_round}" if hasattr(args,"feedback_round") else ""
        log_path = logs_dir / (f"02_align_stack{round_tag}.log" if attempt == 0
                               else f"02_align_stack{round_tag}_retry{attempt}.log")
        lines = ["requires 1.4.4", reg_line, f"{stk_line}{maximize_flag}", "exit"]
        if attempt == 0:
            log(f"executing Siril registration & stack pipeline "
                f"(framing={framing}, mosaic_mode={mosaic_mode}, resampling={log_tag})...")
        else:
            log(f"retrying the stack with a reduced configuration ({log_tag})...")
        receipt = run_siril_script(args.siril, lines, work, log_path, args.timeout)
        attempt += 1
        if receipt["exit_code"] == 0 and master.exists():
            chosen = step
            break
        if remaining:
            log(f"stack attempt '{log_tag}' failed (exit {receipt['exit_code']}); "
                f"degrading and retrying - see {receipt['log']}")
            out = str(receipt.get("output", ""))
            if step["weight"] and any(m in out for m in WEIGHT_FAILURE_MARKERS):
                log("noise weighting is the likely culprit (per-frame background statistics "
                    "unavailable); skipping any further weighted attempts")
                remaining = [s for s in remaining if not s["weight"]]

    if chosen is None:
        die(f"stacking failed after {attempt} attempt(s); see "
            f"{receipt['log'] if receipt else logs_dir}")

    receipt.update(_stack_counts(work,seq_name,receipt["output"],len(selected)))
    receipt["disk_budget"] = disk_budget
    if receipt["dropped_frames"]:
        log(f"warning: Siril dropped {receipt['dropped_frames']} selected frames; actual stack={receipt['stacked_frames']}")
    receipt["interp"] = interp
    receipt["framing"] = framing
    receipt["mosaic_mode"] = mosaic_mode
    receipt["px_scale"] = float(drizzle["scale"]) if chosen["drizzle"] else 1.0
    receipt["weight"] = "noise" if chosen["weight"] else "none"

    if chosen["drizzle"] != drizzle["enabled"]:
        drizzle = {**drizzle, "fallback": True, "reason": f"{drizzle['reason']}+runtime_fallback"}
        log("drizzle: fell back to plain interpolation after a runtime failure")
    if want_weight and not chosen["weight"]:
        receipt["weight_fallback"] = True
        log("warning: noise weighting was dropped after a failure - the stack completed without it")
    receipt["drizzle"] = drizzle

    if chosen["drizzle"]:
        receipt["drizzle"]["header"] = _verify_master_pixel_scale(master, drizzle, work, seq_name)

    dump_json(work / "stack_receipt.json", receipt)
    log(f"master stack generated successfully: {master} "
        f"(resampling={'drizzle x' + format(drizzle['scale'], 'g') if chosen['drizzle'] else 'interp ' + interp}, "
        f"framing={framing}, weight={'noise' if chosen['weight'] else 'none'})")

    # Preserve resampled reference frame for pixel-perfect comparison viewer
    resampled_fits = list(work.glob(f"r_{seq_name}*.fit"))
    if resampled_fits:
        ref_idx = None
        rank_json = work / "ranking.json"
        if rank_json.exists():
            try:
                ref_idx = load_json(rank_json).get("reference_index")
            except Exception:
                pass
        ref_cand = None
        if ref_idx is not None:
            for p in (work / f"r_{seq_name}{ref_idx:05d}.fit", work / f"r_{seq_name}{ref_idx:04d}.fit", work / f"r_{seq_name}{ref_idx}.fit"):
                if p.exists():
                    ref_cand = p
                    break
        if ref_cand is None and resampled_fits:
            ref_cand = sorted(resampled_fits)[0]

        if ref_cand and ref_cand.exists():
            ref_dest = work / f"{seq_name}reference.fit"
            try:
                import shutil
                shutil.copy2(ref_cand, ref_dest)
            except Exception:
                pass

        log(f"cleaning up {len(resampled_fits)} intermediate resampled frames (r_{seq_name}*.fit) to reclaim disk space...")
        for rf in resampled_fits:
            try:
                rf.unlink()
            except Exception:
                pass
    drizzle_tmp = work / "drizztmp"
    if drizzle_tmp.is_dir():
        import shutil

        try:
            shutil.rmtree(drizzle_tmp)
            log("cleaned up Siril's drizzle scratch directory (drizztmp/)")
        except Exception as exc:
            log(f"warning: could not remove {drizzle_tmp}: {exc}")


# -------------------------------------------------------------- 4. postprocess

def _px(value: float, px_scale: float = 1.0) -> float:
    """Scale a native-pixel constant onto the working pixel grid.

    `px_scale` is the drizzle supersampling factor (1.0 for plain interpolation).
    Every pixel-domain tolerance, kernel size and transition width in this stage
    is authored in *native* pixels, so after a 2x drizzle they must be doubled to
    keep the same physical behaviour.  Chrominance smoothing sigma is deliberately
    excluded: it is already expressed relative to the image size.
    """
    return float(value) * float(px_scale)


def _odd(value: float) -> int:
    """Nearest odd integer >= 1 (OpenCV convolution kernels must be odd-sized)."""
    n = int(round(float(value)))
    if n < 1:
        n = 1
    if n % 2 == 0:
        n += 1
    return n


def _resolve_px_scale(args, work: Path, master: Path) -> float:
    """Recover the drizzle pixel-scale factor for the postprocess stage.

    Priority: stack_receipt.json (written by cmd_stack) -> the master's DRZSCALE
    keyword -> 1.0 (no drizzle).
    """
    from astropy.io import fits

    receipt = work / "stack_receipt.json"
    if receipt.exists():
        try:
            scale = float(load_json(receipt).get("px_scale", 1.0) or 1.0)
            if scale > 0:
                return scale
        except Exception:
            pass
    try:
        with fits.open(master, memmap=False) as hdul:
            scale = float(hdul[0].header.get("DRZSCALE", 1.0) or 1.0)
            if scale > 0:
                return scale
    except Exception:
        pass
    return 1.0


def _estimate_pedestal(plane: np.ndarray) -> float:
    """Robustly estimate sky background or sensor black pedestal.

    Avoids framing zero-padding, off-center lunar disk overlap in corners,
    and dark crater detail clipping on full-frame close-up lunar shots.
    Computes a safe noise ceiling (median + 2.0*std) on valid sky corners
    so that background noise cleanly clips to 0 and avoids colored fringing.
    """
    import numpy as np

    valid = plane[np.isfinite(plane) & (plane > 0)]
    if valid.size == 0:
        return 0.0

    h, w = plane.shape
    cs = max(16, min(100, h // 8, w // 8))
    margin = max(4, cs // 5)
    corners = [
        plane[margin:margin + cs, margin:margin + cs],
        plane[margin:margin + cs, max(0, w - margin - cs):w - margin],
        plane[max(0, h - margin - cs):h - margin, margin:margin + cs],
        plane[max(0, h - margin - cs):h - margin, max(0, w - margin - cs):w - margin],
    ]
    corner_medians = []
    corner_stds = []
    for c in corners:
        c_valid = c[np.isfinite(c) & (c > 0)]
        if c_valid.size > 0:
            corner_medians.append(float(np.median(c_valid)))
            corner_stds.append(float(np.std(c_valid)))

    p_low = float(np.percentile(valid, 0.1))
    p_high = float(np.percentile(valid, 99.95))
    dyn_range = p_high - p_low

    if corner_medians:
        min_idx = int(np.argmin(corner_medians))
        min_corner = corner_medians[min_idx]
        min_std = corner_stds[min_idx]

        # Check if the darkest corner represents true space background:
        # In wide lunar shots, space background has very low variance and brightness is in lower 70% of frame.
        is_sky = False
        if dyn_range > 1e-4:
            if min_corner <= float(np.percentile(valid, 70)) and (min_std < 0.10 * dyn_range):
                is_sky = True
        else:
            is_sky = True

        if is_sky:
            safe_ceiling = min_corner + 2.0 * min_std
            return float(min(safe_ceiling, p_high * 0.95) if safe_ceiling < p_high else min_corner)

    # Close-up / full-frame moon without dark sky corners: return low percentile
    return max(0.0, p_low)


def _fit_lunar_limb_circle(
    lum_2d: np.ndarray, px_scale: float = 1.0
) -> tuple[float, float, float, float] | None:
    """Fit a high-precision lunar physical circle using radial gradient inflection points.

    Instead of relying on thresholding (which captures diffuse atmospheric glare),
    this algorithm casts radial rays across full 360 degrees from the center and finds
    the point of maximum negative radial gradient (the true physical limb edge), then applies
    RANSAC circle fitting with sub-pixel precision.

    `px_scale` scales the native-pixel tolerances (ray sampling step, RANSAC inlier
    band, residual acceptance).  After a 2x drizzle the same physical residual spans
    twice as many pixels, so leaving them fixed would silently tighten the inlier
    test to ~1.5 native px and reject otherwise valid limb fits.
    """
    lum_2d = np.ascontiguousarray(lum_2d, dtype=np.float32)
    H, W = lum_2d.shape

    ped = _estimate_pedestal(lum_2d)
    lum_net = np.maximum(0.0, lum_2d - ped)
    p999 = float(np.percentile(lum_net, 99.95))
    if p999 <= 1e-5:
        return None

    import cv2

    core_mask = (lum_net > 0.20 * p999).astype(np.uint8)
    M = cv2.moments(core_mask)
    if M["m00"] == 0:
        return None
    cx_init = float(M["m10"] / M["m00"])
    cy_init = float(M["m01"] / M["m00"])

    dense_angles = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    r_samples = np.arange(min(H, W) * 0.15, max(H, W) * 0.85, _px(1.0, px_scale), dtype=np.float32)

    limb_points = []
    thresh_grad = -0.015 * p999
    for theta in dense_angles:
        xs = cx_init + r_samples * np.cos(theta)
        ys = cy_init + r_samples * np.sin(theta)
        valid = (xs >= 0) & (xs < W - 1) & (ys >= 0) & (ys < H - 1)
        if np.count_nonzero(valid) < 50:
            continue
        xs_val = xs[valid].astype(np.float32).reshape(-1, 1)
        ys_val = ys[valid].astype(np.float32).reshape(-1, 1)
        r_curr = r_samples[valid]

        vals = cv2.remap(lum_net, xs_val, ys_val, cv2.INTER_LINEAR).flatten()
        grad = np.diff(vals)
        if grad.size == 0:
            continue
        min_idx = int(np.argmin(grad))
        if vals[min_idx] > 0.03 * p999 and grad[min_idx] < thresh_grad:
            edge_x = cx_init + float(r_curr[min_idx]) * np.cos(theta)
            edge_y = cy_init + float(r_curr[min_idx]) * np.sin(theta)
            limb_points.append([edge_x, edge_y])

    if len(limb_points) < 15:
        return None

    pts = np.array(limb_points, dtype=np.float64)
    N = len(pts)

    best_inliers = []
    rng = np.random.default_rng(42)
    for _ in range(300):
        sample_idx = rng.choice(N, 3, replace=False)
        p = pts[sample_idx]
        A = np.column_stack([2.0 * p[:, 0], 2.0 * p[:, 1], np.ones(3)])
        b = p[:, 0]**2 + p[:, 1]**2
        try:
            c = np.linalg.solve(A, b)
            xc_cand, yc_cand = c[0], c[1]
            R_sq = c[2] + xc_cand**2 + yc_cand**2
            if R_sq <= 0:
                continue
            R_cand = np.sqrt(R_sq)
            dists = np.abs(np.sqrt((pts[:, 0] - xc_cand)**2 + (pts[:, 1] - yc_cand)**2) - R_cand)
            inliers = np.where(dists < _px(3.0, px_scale))[0]
            if len(inliers) > len(best_inliers):
                best_inliers = inliers
        except np.linalg.LinAlgError:
            continue

    if len(best_inliers) < 15:
        inlier_pts = pts
    else:
        inlier_pts = pts[best_inliers]

    x = inlier_pts[:, 0]
    y = inlier_pts[:, 1]
    A = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x**2 + y**2
    c, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    xc, yc = float(c[0]), float(c[1])
    R_sq = float(c[2] + xc**2 + yc**2)
    if R_sq <= 0:
        return None
    R = float(np.sqrt(R_sq))

    dists = np.sqrt((x - xc)**2 + (y - yc)**2)
    res_std = float(np.std(dists - R))

    if not (0.15 * min(H, W) < R < 3.0 * max(H, W)) or res_std > _px(5.0, px_scale):
        return None

    return xc, yc, R, res_std


def _get_lunar_angular_diameter_rad(date_obs: str | None) -> tuple[float, str]:
    """Return precise lunar angular diameter in radians from ephemeris or mean constant.

    When a valid DATE-OBS timestamp is available, queries the JPL DE ephemeris via
    Astropy to compute the geocentric Earth-Moon distance at the exact observation
    epoch, then derives the angular diameter from the Moon's physical mean radius
    (1737.4 km). This reduces the focal length geometric inversion uncertainty
    from ±7% (using the 31.07' mean) to <0.5%.

    Falls back to the IAU mean angular diameter (31.07 arcmin) when DATE-OBS is
    missing, unparseable, or when the ephemeris query fails.
    """
    THETA_MEAN_RAD = 0.009037905  # 31.07 arcmin
    MOON_RADIUS_KM = 1737.4      # IAU mean lunar radius

    if not date_obs:
        return THETA_MEAN_RAD, "mean (31.07')"

    try:
        from astropy.time import Time
        from astropy.coordinates import get_body
        import astropy.units as u

        t = Time(date_obs, scale='utc')
        moon = get_body('moon', t)
        dist_km = float(moon.distance.to(u.km).value)
        if dist_km < 300000.0 or dist_km > 420000.0:
            # Sanity check: Moon geocentric distance is ~356,500–406,700 km
            return THETA_MEAN_RAD, f"mean (31.07', ephemeris distance {dist_km:.0f}km out of range)"
        theta_rad = 2.0 * np.arctan(MOON_RADIUS_KM / dist_km)
        theta_arcmin = round(np.degrees(theta_rad) * 60.0, 2)
        return float(theta_rad), f"ephemeris ({theta_arcmin}' at {date_obs})"
    except Exception as exc:
        log(f"ephemeris lookup failed for DATE-OBS='{date_obs}': {exc}; using mean 31.07'")
        return THETA_MEAN_RAD, "mean (31.07', ephemeris unavailable)"


def _infer_optical_parameters(
    hdr: fits.Header,
    data: np.ndarray,
    args: argparse.Namespace,
    mosaic_mode: str = "disc",
    px_scale: float = 1.0,
) -> dict:
    """Intelligently infer telescope optical parameters (pixel size, focal length, aperture).

    Combines FITS header metadata mining with subpixel lunar limb RANSAC circle
    fitting and astronomical angular diameter geometry to accurately estimate
    effective focal length (e.g. ~864mm from ~2095px lunar disc on 3.73um sensor).
    """
    # 1. Pixel size inference
    # After a drizzle stack the master's header already carries the corrected
    # (smaller) sampling pitch, so the header branch is used as-is.  The user and
    # default branches describe the *sensor* pitch and must be divided by the
    # drizzle factor to describe the working grid.
    user_px = getattr(args, "pixel_size", None)
    if user_px is not None and user_px > 0:
        px_size = float(user_px) / float(px_scale)
        px_source = "user" if px_scale == 1.0 else f"user / drizzle {px_scale:g}x"
    else:
        hdr_px = None
        for k in ["XPIXSZ", "YPIXSZ", "PIXSIZE", "PIXSIZE1", "PIXEL_SZ", "PIXELSIZE"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_px = fval
                        px_source = _hdr_source_label(hdr, k, f"FITS Header ({k})")
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_px is not None:
            px_size = hdr_px
        else:
            px_size = 0.0
            px_source = "unknown"

    # 2. Aperture inference
    user_dia = getattr(args, "aperture", None)
    if user_dia is not None and user_dia > 0:
        dia = float(user_dia)
        dia_source = "user"
    else:
        hdr_dia = None
        for k in ["APERTUR", "DIAMETER", "APERTURE"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_dia = fval
                        dia_source = _hdr_source_label(hdr, k, f"FITS Header ({k})")
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_dia is not None:
            dia = hdr_dia
        else:
            dia = 0.0
            dia_source = "unknown"

    # 3. Focal length inference
    user_fl = getattr(args, "focal", None)
    fl = None
    fl_source = None
    disc_info = None
    f_range = None

    if user_fl is not None and user_fl > 0:
        fl = float(user_fl)
        fl_source = "user"
    elif mosaic_mode != "tile" and px_size > 0:
        # Attempt geometric inversion from subpixel lunar disc fit
        is_3d = (data.ndim == 3)
        if is_3d:
            lum_2d = 0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]
        else:
            lum_2d = data

        fit_res = _fit_lunar_limb_circle(lum_2d, px_scale=px_scale)
        if fit_res is not None:
            xc, yc, R, res_std = fit_res
            D_px = 2.0 * R
            sensor_dia_mm = D_px * px_size * 1e-3  # mm on sensor plane

            # Precise lunar angular diameter from ephemeris (or mean 31.07' fallback)
            date_obs = hdr.get("DATE-OBS")
            theta_actual, theta_source = _get_lunar_angular_diameter_rad(date_obs)
            # Physical orbital extremes for f_range uncertainty interval
            theta_max = 0.009744843  # Perigee: 33.50 arcmin
            theta_min = 0.008552113  # Apogee:  29.40 arcmin

            f_est = sensor_dia_mm / (2.0 * np.tan(theta_actual / 2.0))
            f_min = sensor_dia_mm / (2.0 * np.tan(theta_max / 2.0))
            f_max = sensor_dia_mm / (2.0 * np.tan(theta_min / 2.0))

            if 100.0 <= f_est <= 15000.0:
                fl = f_est
                f_range = [f_min, f_max]
                theta_arcmin = round(np.degrees(theta_actual) * 60.0, 2)
                fl_source = f"geometric inversion (disc D={D_px:.1f}px, θ={theta_arcmin}', {theta_source})"
                disc_info = {
                    "center": [round(xc, 1), round(yc, 1)],
                    "radius_px": round(R, 1),
                    "diameter_px": round(D_px, 1),
                    "res_std": round(res_std, 2),
                    "sensor_dia_mm": round(sensor_dia_mm, 3),
                    "angular_diam_arcmin": theta_arcmin,
                    "angular_diam_source": theta_source,
                    "focal_range": [round(f_min, 1), round(f_max, 1)],
                }

    # If geometric inversion was not applicable or failed, fallback to FITS header or default
    if fl is None:
        hdr_fl = None
        for k in ["FOCALLEN", "FOCAL_LENGTH", "FOCAL"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_fl = fval
                        fl_source = _hdr_source_label(hdr, k, f"FITS Header ({k})")
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_fl is not None:
            fl = hdr_fl
        else:
            fl = 0.0
            fl_source = "unknown"

    f_ratio = fl / dia if dia > 0 else 0.0
    # Theoretical Airy radius in pixels for green light (550nm = 0.55um):
    # r_airy = 1.22 * lambda * f / (D * px_size)
    r_airy_px = (1.22 * 0.55 * fl) / (dia * px_size) if (dia > 0 and px_size > 0) else 0.0

    # Independent cross-check. The device table is a prior derived from published
    # specifications, while the geometric inversion is measured from this frame, so
    # a large disagreement is worth surfacing: it can mean the device was
    # misidentified, or that a wrong pixel pitch is scaling the inversion. Advisory
    # only -- the inversion is never overwritten, because a partial lunar disc can
    # make it unreliable.
    focal_crosscheck = None
    table_focal = _hdr_positive_float(hdr, FOCAL_KEYS) if "FOCALLEN" in str(hdr.get("OPTPRIOR") or "") else None
    if table_focal and fl_source and str(fl_source).startswith("geometric inversion"):
        delta = abs(float(fl) - float(table_focal)) / float(table_focal)
        focal_crosscheck = {
            "device_table_focal_mm": round(float(table_focal), 1),
            "inversion_focal_mm": round(float(fl), 1),
            "delta_pct": round(delta * 100.0, 1),
            "suspicious": bool(delta > 0.15),
        }
        if focal_crosscheck["suspicious"]:
            log(f"optical cross-check: device-table focal {table_focal:.0f}mm vs measured "
                f"inversion {fl:.0f}mm differ by {delta * 100:.0f}% - verify the identified "
                f"device and the pixel pitch (the inversion scales linearly with it)")

    return {
        "pixel_size": round(px_size, 3) if px_size else None,
        "pixel_size_source": px_source,
        "focal_length": round(fl, 1) if fl else None,
        "focal_source": fl_source,
        "focal_crosscheck": focal_crosscheck,
        "focal_range": [round(f_range[0], 1), round(f_range[1], 1)] if f_range else None,
        "aperture": round(dia, 1) if dia else None,
        "aperture_source": dia_source,
        "f_ratio": round(f_ratio, 2) if fl and dia else None,
        "airy_radius_px": round(r_airy_px, 2) if fl and dia and px_size else None,
        "trusted": bool(fl and dia and px_size and np.all(np.isfinite([fl,dia,px_size]))),
        "lunar_disc": disc_info,
    }


def _suppress_lunar_limb_glare(
    planes: np.ndarray,
    glare_mode: str = "auto",
    px_scale: float = 1.0,
) -> tuple[np.ndarray, dict]:
    """Suppress atmospheric and optical forward scattering glare outside lunar physical limb.

    Operates strictly in 32-bit linear space before non-linear MTF stretch.
    Leaves lunar surface (r <= R + 1px) 100% unaltered.
    Smoothly transitions over a narrow delta (2.5px to 8.0px) into deep space zero.

    `px_scale` is the drizzle supersampling factor; the transition width and the
    limb guard band are native-pixel quantities and scale with it.
    """
    if glare_mode == "off":
        return planes, {"active": False}

    is_3d = (planes.ndim == 3)
    if is_3d:
        lum_2d = 0.299 * planes[0] + 0.587 * planes[1] + 0.114 * planes[2]
    else:
        lum_2d = planes
    H, W = lum_2d.shape

    fit_res = _fit_lunar_limb_circle(lum_2d, px_scale=px_scale)
    if fit_res is None:
        return planes, {"active": False, "reason": "circle_fit_failed"}

    xc, yc, R, res_std = fit_res

    if glare_mode == "aggressive":
        delta = _px(2.5, px_scale)
    elif glare_mode == "mild":
        delta = _px(8.0, px_scale)
    else:  # "auto"
        delta = _px(4.5, px_scale)

    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - xc)**2 + (yy - yc)**2)

    r0 = R + _px(1.0, px_scale)
    t = np.clip((r_grid - r0) / delta, 0.0, 1.0)
    w_smooth = 1.0 - (3.0 * t**2 - 2.0 * t**3)

    weight = np.ones((H, W), dtype=np.float32)
    apply_mask = r_grid > r0
    weight[apply_mask] = w_smooth[apply_mask]
    outer_space_mask = r_grid > (r0 + delta)
    weight[outer_space_mask] = 0.0

    if is_3d:
        out_planes = planes * weight[np.newaxis, :, :]
    else:
        out_planes = planes * weight

    meta = {
        "active": True,
        "glare_mode": glare_mode,
        "center_x": xc,
        "center_y": yc,
        "radius": R,
        "residual_std": res_std,
        "delta": delta,
    }
    return out_planes.astype(np.float32), meta


def _load_locked_profile(
    lock_profile_path: str | None = None,
    lock_from_dir: str | None = None,
) -> tuple[dict | None, str | None]:
    """Load calibration profile from explicit file or anchor work directory."""
    if lock_profile_path:
        p = Path(lock_profile_path).expanduser().resolve()
        if not p.exists():
            die(f"locked profile file not found: {p}")
        return load_json(p), str(p)

    if lock_from_dir:
        d = Path(lock_from_dir).expanduser().resolve()
        if not d.is_dir():
            die(f"lock-from directory not found: {d}")
        # Search priority: lunar_profile.json -> mosaic_tile_info.json
        p_json = d / "lunar_profile.json"
        if p_json.exists():
            return load_json(p_json), str(p_json)
        t_json = d / "mosaic_tile_info.json"
        if t_json.exists():
            info = load_json(t_json)
            if "calibration_profile" in info:
                return info["calibration_profile"], str(t_json)
            rec = {
                "version": "1.0",
                "source_master": info.get("master"),
                "histogram": {
                    "hi_unified": info.get("hi_unified", info.get("hi_lum", 0.03)),
                    "hi_lum": info.get("hi_unified", info.get("hi_lum", 0.03)),
                    "midtone": info.get("midtone", 0.13),
                },
            }
            return rec, str(t_json)
        die(f"no lunar_profile.json or mosaic_tile_info.json found in anchor directory: {d}")

    return None, None


def _estimate_atmospheric_extinction_gradient(
    net_planes: np.ndarray,
    mask: np.ndarray,
    mode: str = "auto",
) -> dict:
    """Robustly estimate first-order atmospheric extinction spatial gradient.

    Physical basis:
      In low-altitude lunar imaging (altitude < 30°), the airmass gradient across
      the 0.5° lunar disc induces an asymmetric Rayleigh/aerosol extinction slope:
      shorter blue wavelengths attenuate significantly faster towards the horizon than red.
      This produces a macroscopic 'warm/dark horizon, cool/bright zenith' color/luminance tilt.

    Mathematical strategy:
      1. Coarse grid median subsampling: partitions the moon into 16x16 blocks to decouple
         high-frequency crater details and sharp terminator topography.
      2. Log-color-ratio mapping:
         s_B = ln(B / G), s_R = ln(R / G)
         Completely cancels out surface albedo variations (mare vs highlands).
      3. Robust Huber / IRLS 2D planar regression:
         Fits s_c(x, y) = c + alpha_x * nx + alpha_y * ny
         Suppresses local geological anomalies (e.g. localized titanium-rich Mare Tranquillitatis).
      4. Rayleigh physical consistency check:
         Verifies that blue and red slope vectors are negatively collinear (opposing directions).
      5. Significance thresholding:
         If total color-ratio slope < 1.5% across the disc, automatically bypasses (zero disturbance at high altitude).
    """
    import math
    import numpy as np

    if mode == "off" or net_planes.shape[0] != 3:
        return {"active": False, "applied": False, "mode": mode, "reason": "mode off or non-RGB"}

    h, w = net_planes[0].shape
    xc, yc = w / 2.0, h / 2.0
    r_norm = 0.5 * math.hypot(w, h)

    # 1. Coarse grid median subsampling
    n_grid = 16
    gh, gw = max(4, h // n_grid), max(4, w // n_grid)
    grid_pts = []

    for gy in range(n_grid):
        y0 = gy * gh
        y1 = (gy + 1) * gh if gy < n_grid - 1 else h
        for gx in range(n_grid):
            x0 = gx * gw
            x1 = (gx + 1) * gw if gx < n_grid - 1 else w
            sub_m = mask[y0:y1, x0:x1]
            if np.count_nonzero(sub_m) < 30:
                continue
            sub_r = net_planes[0, y0:y1, x0:x1][sub_m]
            sub_g = net_planes[1, y0:y1, x0:x1][sub_m]
            sub_b = net_planes[2, y0:y1, x0:x1][sub_m]

            med_g = float(np.median(sub_g))
            med_r = float(np.median(sub_r))
            med_b = float(np.median(sub_b))
            if med_g <= 1e-4 or med_r <= 1e-4 or med_b <= 1e-4:
                continue

            cell_xc = (x0 + x1) / 2.0
            cell_yc = (y0 + y1) / 2.0
            nx = (cell_xc - xc) / r_norm
            ny = (cell_yc - yc) / r_norm

            l_bg = math.log(med_b / med_g)
            l_rg = math.log(med_r / med_g)
            grid_pts.append((nx, ny, l_bg, l_rg))

    if len(grid_pts) < 12:
        return {"active": False, "applied": False, "mode": mode, "reason": "insufficient valid grid cells (<12)"}

    pts = np.array(grid_pts, dtype=np.float64)
    xs = pts[:, 0]
    ys = pts[:, 1]
    y_bg = pts[:, 2]
    y_rg = pts[:, 3]

    # 2. Robust IRLS Plane Fitting
    def _fit_plane_irls(x_arr, y_arr, target):
        A = np.column_stack([np.ones_like(x_arr), x_arr, y_arr])
        # Initial least squares
        beta = np.linalg.lstsq(A, target, rcond=None)[0]
        for _ in range(5):
            res = target - A @ beta
            mad = np.median(np.abs(res - np.median(res))) + 1e-6
            delta = 1.345 * mad * 1.4826
            weights = np.ones_like(res)
            outliers = np.abs(res) > delta
            weights[outliers] = delta / np.abs(res[outliers])
            W = np.diag(weights)
            try:
                beta = np.linalg.solve(A.T @ W @ A, A.T @ W @ target)
            except np.linalg.LinAlgError:
                break
        return beta  # [intercept, slope_x, slope_y]

    beta_bg = _fit_plane_irls(xs, ys, y_bg)
    beta_rg = _fit_plane_irls(xs, ys, y_rg)

    gx_b, gy_b = float(beta_bg[1]), float(beta_bg[2])
    gx_r, gy_r = float(beta_rg[1]), float(beta_rg[2])

    amp_b = 2.0 * math.hypot(gx_b, gy_b)
    amp_r = 2.0 * math.hypot(gx_r, gy_r)

    # 3. Physical consistency check (Rayleigh negative collinearity)
    denom = (math.hypot(gx_b, gy_b) * math.hypot(gx_r, gy_r)) + 1e-8
    cos_br = (gx_b * gx_r + gy_b * gy_r) / denom

    zenith_angle_deg = math.degrees(math.atan2(gy_b, gx_b)) % 360.0
    confidence = float(np.clip(-cos_br, 0.0, 1.0))

    threshold = 0.015 if mode == "auto" else 0.005
    is_significant = (amp_b >= threshold)

    applied = False
    if mode == "auto":
        # Atmospheric extinction requires high confidence and strict opposing collinearity
        # to avoid misinterpreting intrinsic lunar geological albedo as atmospheric tilt.
        if is_significant and cos_br <= -0.70 and confidence >= 0.70:
            applied = True
    elif mode in ("mild", "aggressive"):
        if is_significant and cos_br < 0.0:
            applied = True

    strength = 0.85
    if mode == "mild":
        strength = 0.50
    elif mode == "aggressive":
        strength = 1.00

    return {
        "active": True,
        "applied": applied,
        "mode": mode,
        "zenith_angle_deg": round(zenith_angle_deg, 2),
        "amp_b": round(amp_b, 4),
        "amp_r": round(amp_r, 4),
        "slope_b": (gx_b, gy_b),
        "slope_r": (gx_r, gy_r),
        "cos_br": round(cos_br, 3),
        "confidence": round(confidence, 3),
        "strength": strength,
        "r_norm": r_norm,
        "center": (xc, yc),
    }


def _apply_extinction_compensation(
    r_net: np.ndarray,
    g_net: np.ndarray,
    b_net: np.ndarray,
    ext_info: dict,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply first-order planar transmission division with flux conservation."""
    import numpy as np

    if not ext_info.get("applied"):
        return r_net, g_net, b_net

    h, w = r_net.shape
    xc, yc = ext_info["center"]
    r_norm = ext_info["r_norm"]
    strength = ext_info.get("strength", 0.85)

    gx_b, gy_b = ext_info["slope_b"]
    gx_r, gy_r = ext_info["slope_r"]

    # Scale slopes by dampening factor
    gx_b_s, gy_b_s = gx_b * strength, gy_b * strength
    gx_r_s, gy_r_s = gx_r * strength, gy_r * strength
    # Luminance flux compensation along zenith axis (approx 40% of blue extinction slope)
    gx_l_s, gy_l_s = gx_b_s * 0.40, gy_b_s * 0.40

    yy, xx = np.mgrid[0:h, 0:w]
    nx = ((xx - xc) / r_norm).astype(np.float32)
    ny = ((yy - yc) / r_norm).astype(np.float32)

    T_b = np.exp(gx_b_s * nx + gy_b_s * ny).astype(np.float32)
    T_r = np.exp(gx_r_s * nx + gy_r_s * ny).astype(np.float32)
    T_l = np.exp(gx_l_s * nx + gy_l_s * ny).astype(np.float32)

    # Flux conservation normalization across lunar disc
    if np.any(mask):
        T_b /= float(np.mean(T_b[mask]))
        T_r /= float(np.mean(T_r[mask]))
        T_l /= float(np.mean(T_l[mask]))

    b_corr = (b_net / (T_b * T_l)).astype(np.float32)
    r_corr = (r_net / (T_r * T_l)).astype(np.float32)
    g_corr = (g_net / T_l).astype(np.float32)

    return r_corr, g_corr, b_corr


def _calculate_channel_balance(
    d: np.ndarray,
    bg_r: float,
    bg_g: float,
    bg_b: float,
    mid_val: float | None = None,
    wb_mode: str = "gray-world",
    glare_mode: str = "auto",
    extinction_comp: str = "auto",
    locked_profile: dict | None = None,
    lock_wb: tuple[float, float] | None = None,
    lock_stretch: tuple[float, float] | None = None,
    px_scale: float = 1.0,
) -> dict:
    """Calculate channel white point stretch parameters with neutral Gray-World balance.

    In astronomical imaging, white balance must be performed linearly before non-linear MTF stretch.
    Applying independent non-linear MTF stretches with different white/black points causes non-linear
    color divergence, destroying color fidelity in shadows and generating purple fringing along
    high-contrast boundaries (the lunar limb).

    Supports Global Profile Locking (P2) for mosaic multi-panel stitching:
    Locks histogram stretch ceiling (hi_lum) and linear white balance gains (k_R, k_B)
    to eliminate seam brightness stepping and patch chrominance drift across panels.

    Supports Atmospheric Extinction Gradient Compensation (First-order planar field compensation).
    """
    import numpy as np

    p999_r = float(np.percentile(d[0], 99.95))
    p999_g = float(np.percentile(d[1], 99.95))
    p999_b = float(np.percentile(d[2], 99.95))

    hi_r_raw = max(p999_r * 1.10, bg_r + 0.01)
    hi_g_raw = max(p999_g * 1.10, bg_g + 0.01)
    hi_b_raw = max(p999_b * 1.10, bg_b + 0.01)

    lum_legacy = (0.299 * d[0] + 0.587 * d[1] + 0.114 * d[2]).astype(np.float32)
    bg_lum_legacy = _estimate_pedestal(lum_legacy)
    p999_lum_legacy = float(np.percentile(lum_legacy, 99.95))
    hi_lum_legacy = max(p999_lum_legacy * 1.10, bg_lum_legacy + 0.01)

    initial_mid = float(mid_val) if mid_val is not None else 0.42
    res = {
        "bg_r": bg_r, "hi_r": hi_r_raw,
        "bg_g": bg_g, "hi_g": hi_g_raw,
        "bg_b": bg_b, "hi_b": hi_b_raw,
        "bg_lum": bg_lum_legacy, "hi_lum": hi_lum_legacy,
        "lum_data": lum_legacy,
        "color_data": d,
        "mode": wb_mode,
        "ratio_r": 1.0,
        "ratio_b": 1.0,
        "k_r": 1.0,
        "k_b": 1.0,
        "moon_pixels": 0,
        "wb_locked": False,
        "stretch_locked": False,
        "mid_val": initial_mid,
        "mid_info": {"midtone": initial_mid, "median_raw": initial_mid, "target": 0.50},
    }

    if wb_mode != "gray-world":
        return res

    # 1. Linear pedestal subtraction
    r_net = np.maximum(0.0, d[0] - bg_r)
    g_net = np.maximum(0.0, d[1] - bg_g)
    b_net = np.maximum(0.0, d[2] - bg_b)

    # 2. Lunar Limb Glare Suppression (physics-based radial falloff outside celestial limb)
    if glare_mode != "off":
        net_planes = np.stack([r_net, g_net, b_net], axis=0)
        net_planes, glare_meta = _suppress_lunar_limb_glare(
            net_planes, glare_mode=glare_mode, px_scale=px_scale)
        r_net, g_net, b_net = net_planes[0], net_planes[1], net_planes[2]
        res["glare_meta"] = glare_meta

    lum_approx = 0.299 * r_net + 0.587 * g_net + 0.114 * b_net
    p999_lum_raw = float(np.percentile(lum_approx, 99.95))
    if p999_lum_raw <= 1e-4:
        return res

    # Mask valid lunar surface: exclude dark background/shadows and overexposed peaks
    full_scale = _pixel_full_scale(d)
    mask = (lum_approx > (0.05 * p999_lum_raw)) & (lum_approx < (0.95 * p999_lum_raw)) & np.all(d < 0.995*full_scale,axis=0)
    valid_count = int(np.count_nonzero(mask))
    res["moon_pixels"] = valid_count

    # 2.5 Atmospheric Extinction Gradient Compensation (First-order planar field compensation)
    ext_info = _estimate_atmospheric_extinction_gradient(
        np.stack([r_net, g_net, b_net], axis=0),
        mask,
        mode=extinction_comp,
    )
    res["extinction_meta"] = ext_info
    if ext_info.get("applied"):
        r_net, g_net, b_net = _apply_extinction_compensation(r_net, g_net, b_net, ext_info, mask)
        lum_approx = 0.299 * r_net + 0.587 * g_net + 0.114 * b_net

    # Determine Gray-World gains (check locked profile first)
    is_wb_locked = False
    if lock_wb is not None:
        k_r = float(lock_wb[0])
        k_b = float(lock_wb[1])
        ratio_r = 1.0 / max(k_r, 1e-6)
        ratio_b = 1.0 / max(k_b, 1e-6)
        is_wb_locked = True
    elif locked_profile and "white_balance" in locked_profile:
        wb_prof = locked_profile["white_balance"]
        if "k_r" in wb_prof and "k_b" in wb_prof:
            k_r = float(wb_prof["k_r"])
            k_b = float(wb_prof["k_b"])
            ratio_r = float(wb_prof.get("ratio_r", 1.0 / max(k_r, 1e-6)))
            ratio_b = float(wb_prof.get("ratio_b", 1.0 / max(k_b, 1e-6)))
            is_wb_locked = True

    if not is_wb_locked:
        if valid_count < 200:
            return res
        # Sample mid-reflectance terrain (35th to 85th percentile of lunar terrain)
        # to prevent dark basalt maria albedo from skewing neutral White Balance
        valid_luma = lum_approx[mask]
        p35_luma = float(np.percentile(valid_luma, 35))
        p85_luma = float(np.percentile(valid_luma, 85))
        mask_wb = mask & (lum_approx >= p35_luma) & (lum_approx <= p85_luma)
        if np.count_nonzero(mask_wb) < 100:
            mask_wb = mask

        net_r = float(np.median(r_net[mask_wb]))
        net_g = float(np.median(g_net[mask_wb]))
        net_b = float(np.median(b_net[mask_wb]))
        if net_g <= 1e-5 or net_r <= 1e-5 or net_b <= 1e-5:
            return res
        ratio_r = net_r / net_g
        ratio_b = net_b / net_g
        k_r = 1.0 / ratio_r
        k_b = 1.0 / ratio_b

    # 3. Linear channel balancing
    r_lin = r_net * k_r
    g_lin = g_net
    b_lin = b_net * k_b

    # 4. Edge Defringe (Optical Chromatic Aberration & Purple Fringe Suppression)
    max_b_body = np.maximum(g_lin, r_lin) * 1.15
    max_b_edge = np.maximum(g_lin, r_lin) * 1.00
    edge_zone = lum_approx < (0.10 * p999_lum_raw)
    max_allowed_b = np.where(edge_zone, max_b_edge, max_b_body)
    b_clean = np.minimum(b_lin, max_allowed_b)

    # 5. Calibrated Luminance and Color planes
    lum_clean = (0.299 * r_lin + 0.587 * g_lin + 0.114 * b_clean).astype(np.float32)
    color_clean = np.stack([r_lin, g_lin, b_clean], axis=0).astype(np.float32)

    # Determine unified stretch ceiling (check locked profile first)
    is_stretch_locked = False
    if lock_stretch is not None:
        hi_unified = float(lock_stretch[1])
        is_stretch_locked = True
    elif locked_profile and "histogram" in locked_profile:
        hist_prof = locked_profile["histogram"]
        if "hi_unified" in hist_prof:
            hi_unified = float(hist_prof["hi_unified"])
            is_stretch_locked = True
        elif "hi_lum" in hist_prof:
            hi_unified = float(hist_prof["hi_lum"])
            is_stretch_locked = True

    if not is_stretch_locked:
        p999_clean = float(np.percentile(lum_clean, 99.95))
        # Provide +8% headroom to absorb subsequent deconvolution & wavelet peak energy without clipping
        hi_unified = max(p999_clean * 1.08, 1e-4)

    # Determine midtone stretch (locked profile > user override > adaptive)
    if locked_profile and "histogram" in locked_profile and "midtone" in locked_profile["histogram"] and mid_val is None:
        effective_mid = float(locked_profile["histogram"]["midtone"])
        mid_info = {"midtone": effective_mid, "median_raw": effective_mid, "target": 0.50, "locked": True}
    elif mid_val is not None:
        effective_mid = float(mid_val)
        mid_info = {"midtone": effective_mid, "median_raw": effective_mid, "target": 0.50, "locked": False}
    else:
        mid_info = _estimate_adaptive_midtone(lum_clean, hi_val=hi_unified)
        effective_mid = mid_info["midtone"]

    res.update({
        "bg_r": 0.0, "hi_r": hi_unified,
        "bg_g": 0.0, "hi_g": hi_unified,
        "bg_b": 0.0, "hi_b": hi_unified,
        "bg_lum": 0.0, "hi_lum": hi_unified,
        "lum_data": lum_clean,
        "color_data": color_clean,
        "ratio_r": ratio_r,
        "ratio_b": ratio_b,
        "k_r": k_r,
        "k_b": k_b,
        "wb_locked": is_wb_locked,
        "stretch_locked": is_stretch_locked,
        "mid_val": effective_mid,
        "mid_info": mid_info,
    })
    return res


def _render_deep_cine_mineral(
    lum_sharp: np.ndarray,
    color_balanced: np.ndarray,
    circle_meta: dict | None = None,
    fe_boost: float = 4.5,
    ti_boost: float = 5.5,
    gamma: float = 1.00,
    px_scale: float = 1.0,
    return_float: bool = False,
) -> np.ndarray:
    """Render authentic geological mineral moon via continuous chrominance space amplification.

    Features:
      1. Continuous Geological Chrominance: Eliminates artificial binary classification and
         isolated false color blotches. Smoothly amplifies titanium basalt blues (Mare Tranquillitatis)
         and iron-rich terracotta/peach hues (Mare Serenitatis, Imbrium) continuously across maria
         and highland systems.
      2. Bilateral Chroma Filtering: Strips Bayer sensor chroma noise and subpixel rim dispersion
         while rigorously preserving large-scale geological boundaries.
      3. Physical Limb Defringe & Desaturation Zone: Smoothly fades chrominance to neutral gray
         within 18px of the celestial limb (with strict 4px neutral deadzone), completely eliminating
         optical dispersion, secondary spectrum chromatic fringing, and edge halos.
      4. Natural Wide Dynamic Range: Protects deep basalt maria micro-contrast without harsh S-curves
         or shadow crushing.
      5. Soft-Knee Highlight Protection: Precludes clipped highlights on high-albedo crater peaks.
    """
    import cv2
    import numpy as np

    H, W = lum_sharp.shape
    lum_sharp = np.ascontiguousarray(lum_sharp, dtype=np.float32)
    r = np.ascontiguousarray(color_balanced[0], dtype=np.float32)
    g = np.ascontiguousarray(color_balanced[1], dtype=np.float32)
    b = np.ascontiguousarray(color_balanced[2], dtype=np.float32)

    lum_lin = 0.299 * r + 0.587 * g + 0.114 * b
    p999_lin = float(np.percentile(lum_lin[lum_lin > 0.0001], 99.95))
    safe_lum = np.maximum(lum_lin, 0.005 * p999_lin)

    # 1. Base relative chrominance ratios
    cr_r = r / safe_lum
    cr_g = g / safe_lum
    cr_b = b / safe_lum

    # 2. High-performance Bilateral chrominance low-pass
    # Decouples Bayer CFA high-frequency noise from macro geological mineral boundaries
    sigma_space = max(7, int(round(min(H, W) * 0.018)))
    cr_r_f = cv2.bilateralFilter(cr_r.astype(np.float32), d=7, sigmaColor=0.08, sigmaSpace=sigma_space)
    cr_g_f = cv2.bilateralFilter(cr_g.astype(np.float32), d=7, sigmaColor=0.08, sigmaSpace=sigma_space)
    cr_b_f = cv2.bilateralFilter(cr_b.astype(np.float32), d=7, sigmaColor=0.08, sigmaSpace=sigma_space)

    dr = cr_r_f - 1.0
    dg = cr_g_f - 1.0
    db = cr_b_f - 1.0

    # 3. Geometry & Limb Distance Field
    is_tile = bool(circle_meta and circle_meta.get("is_tile"))
    fit = None
    if not is_tile:
        if circle_meta and circle_meta.get("active"):
            cx = float(circle_meta["center_x"])
            cy = float(circle_meta["center_y"])
            R = float(circle_meta["radius"])
            fit = (cx, cy, R)
        else:
            fit_res = _fit_lunar_limb_circle(lum_sharp, px_scale=px_scale)
            if fit_res:
                cx, cy, R, _ = fit_res
                fit = (cx, cy, R)

    if fit is not None:
        cx, cy, R = fit
        yy, xx = np.mgrid[0:H, 0:W]
        r_grid = np.sqrt((xx - cx)**2 + (yy - cy)**2)
        limb_dist = R - r_grid
    elif not is_tile:
        luma_mask = (lum_sharp > 0.02 * p999_lin).astype(np.uint8)
        limb_dist = cv2.distanceTransform(luma_mask, cv2.DIST_L2, 5)
    else:
        limb_dist = np.full((H, W), 999.0, dtype=np.float32)

    # Physical Limb Defringe & Neutralization Zone:
    # Outer 4px is strict neutral deadzone (limb_dist <= 4.0 -> chroma_fade = 0.0)
    # Between 4px and 18px from the limb, saturation smoothly transitions to full mineral saturation
    # using a smooth cubic Hermite polynomial.
    if not is_tile:
        fade_val = np.clip((limb_dist - _px(4.0, px_scale)) / _px(14.0, px_scale), 0.0, 1.0)
        limb_chroma_fade = fade_val * fade_val * (3.0 - 2.0 * fade_val)
    else:
        limb_chroma_fade = 1.0

    # 4. Continuous Hue-Selective Amplification
    # Ti-rich (b > r, db > 0) receives ti_boost; Fe-rich (r > b, dr > 0) receives fe_boost
    delta_rb = dr - db
    blend_fe = 1.0 / (1.0 + np.exp(-np.clip(delta_rb / 0.03, -10.0, 10.0)))
    continuous_gain = (1.0 - blend_fe) * ti_boost + blend_fe * fe_boost

    # Highlight and shadow saturation rolloff
    luma_fade = np.clip((lum_sharp - 0.03) / 0.07, 0.0, 1.0) * (1.0 - np.clip((lum_sharp - 0.88) / 0.10, 0.0, 1.0))
    total_chroma_weight = limb_chroma_fade * luma_fade

    dr_boost = dr * continuous_gain * total_chroma_weight
    dg_boost = dg * continuous_gain * total_chroma_weight
    db_boost = db * continuous_gain * total_chroma_weight

    cr_r_new = np.clip(1.0 + dr_boost, 0.1, 4.0)
    cr_g_new = np.clip(1.0 + dg_boost, 0.1, 4.0)
    cr_b_new = np.clip(1.0 + db_boost, 0.1, 4.0)

    # 5. Natural Wide Dynamic Range Tone Sculpting
    lum_safe = np.nan_to_num(np.clip(lum_sharp, 0.0, 1.0), nan=0.0)
    if abs(gamma - 1.0) > 0.01:
        lum_cine = np.power(lum_safe, gamma)
    else:
        lum_cine = lum_safe

    # 6. Soft-Knee Highlight Protection (Guarantees zero harsh clipping)
    knee = 0.75
    above_knee = lum_cine > knee
    if np.any(above_knee):
        lum_cine[above_knee] = knee + (1.0 - knee) * np.tanh((lum_cine[above_knee] - knee) / (1.0 - knee))

    final_r = np.nan_to_num(np.clip(lum_cine * cr_r_new, 0.0, 1.0), nan=0.0)
    final_g = np.nan_to_num(np.clip(lum_cine * cr_g_new, 0.0, 1.0), nan=0.0)
    final_b = np.nan_to_num(np.clip(lum_cine * cr_b_new, 0.0, 1.0), nan=0.0)

    # Outer space clean zeroing
    if not is_tile and fit is not None:
        space_cut = np.clip((limb_dist + _px(1.0, px_scale)) / _px(2.5, px_scale), 0.0, 1.0)
        final_r *= space_cut
        final_g *= space_cut
        final_b *= space_cut

    # Flip vertically to match image row 0 at top convention
    rgb_fits = np.stack([final_r, final_g, final_b], axis=-1)
    if return_float:
        return np.moveaxis(rgb_fits, -1, 0).astype(np.float32)
    rgb_jpg = rgb_fits[::-1, :, :]
    bgr_jpg = cv2.cvtColor((rgb_jpg * 255.0).astype(np.uint8), cv2.COLOR_RGB2BGR)
    return bgr_jpg


def _compute_edge_ringing_damping_mask(
    lum_base: np.ndarray,
    contrast_threshold: float = 1.0,
    return_positive: bool = False,
    px_scale: float = 1.0,
) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
    """Compute subpixel edge ringing damping masks for lunar step edges.

    Identifies steep luminance cliffs (crater rims, terminator, limb) and localizes:
      1. Negative undershoot valleys (shadow side) causing artificial dark halos.
      2. Positive overshoot peaks (bright side / limb inner band) causing unnatural bright glare rims.

    `px_scale` is the drizzle supersampling factor.  The ringing structure lives at
    the pixel scale, so the local baseline blur, the dilation reach and the mask
    softening kernel all scale with it.
    """
    import cv2

    base = lum_base.astype(np.float32)
    p_min = float(np.min(base))
    p_max = float(np.max(base))
    if p_max <= p_min + 1e-7:
        zero_m = np.zeros_like(base, dtype=np.float32)
        return (zero_m, zero_m) if return_positive else zero_m

    norm = (base - p_min) / (p_max - p_min)

    # 1. Gradient magnitude via Sobel
    gx = cv2.Sobel(norm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(norm, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)

    # 2. Local dynamic baseline (soft low-pass)
    _k7 = _odd(_px(7.0, px_scale))
    _k5 = _odd(_px(5.0, px_scale))
    _k3 = _odd(_px(3.0, px_scale))
    _dilate_iters = max(1, int(round(_px(2.0, px_scale))))
    _sig_big = _px(1.5, px_scale)
    _sig_small = _px(1.2, px_scale)
    smooth = cv2.GaussianBlur(norm, (_k7, _k7), _sig_big)

    # 3. Step edge relative contrast (prevents activation on noisy flat maria)
    rel_contrast = grad / (smooth + 0.02)
    is_step_edge = rel_contrast > contrast_threshold

    # 4. Shadow side identification (negative undershoot: L < smooth)
    is_shadow_side = norm < (smooth - 0.005)
    hazard_neg = grad * (is_step_edge & is_shadow_side).astype(np.float32)

    # 5. Morphological dilation for shadow side (2-3 px outwards into shadow)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_k3, _k3))
    dilated_neg = cv2.dilate(hazard_neg, kernel, iterations=_dilate_iters)
    damp_mask_neg = cv2.GaussianBlur(dilated_neg, (_k5, _k5), _sig_small)

    active_pixels_neg = damp_mask_neg[damp_mask_neg > 0.001]
    if active_pixels_neg.size > 10:
        norm_val = float(np.percentile(active_pixels_neg, 95))
        damp_mask_neg = np.clip(damp_mask_neg / max(norm_val, 1e-5), 0.0, 1.0)
    else:
        damp_mask_neg = np.zeros_like(base, dtype=np.float32)

    if not return_positive:
        return damp_mask_neg.astype(np.float32)

    # 6. Bright side identification (positive overshoot / bright limb glare: L > smooth)
    is_bright_side = norm > (smooth + 0.005)
    hazard_pos = grad * (is_step_edge & is_bright_side).astype(np.float32)
    dilated_pos = cv2.dilate(hazard_pos, kernel, iterations=_dilate_iters)
    damp_mask_pos = cv2.GaussianBlur(dilated_pos, (_k5, _k5), _sig_small)

    active_pixels_pos = damp_mask_pos[damp_mask_pos > 0.001]
    if active_pixels_pos.size > 10:
        norm_val_pos = float(np.percentile(active_pixels_pos, 95))
        damp_mask_pos = np.clip(damp_mask_pos / max(norm_val_pos, 1e-5), 0.0, 1.0)
    else:
        damp_mask_pos = np.zeros_like(base, dtype=np.float32)

    return damp_mask_neg.astype(np.float32), damp_mask_pos.astype(np.float32)


def _apply_anti_ringing_damping(
    lum_base: np.ndarray,
    lum_sharp: np.ndarray,
    damping_mask: np.ndarray,
    strength: float = 0.60,
    pos_mask: np.ndarray | None = None,
    pos_strength: float = 0.70,
    soft_knee: bool = False,
) -> np.ndarray:
    """Apply elastic bilateral damping to edge ringing dips and peaks.

    - Absorbs negative undershoot dark halos on the shadow side.
    - Suppresses artificial positive overshoot bright rims on the step edge / lunar limb when pos_mask is provided.
    - Optionally applies soft-knee highlight compression (when soft_knee=True) to eliminate clipped highlight blowouts.
    """
    if strength <= 0.0 and (pos_mask is None or pos_strength <= 0.0) and not soft_knee:
        return lum_sharp

    base = lum_base.astype(np.float32)
    sharp = lum_sharp.astype(np.float32)
    delta = sharp - base
    delta_damped = delta.copy()

    # 1. Negative undershoot damping (shadow side dark halos)
    if strength > 0.0 and damping_mask is not None:
        neg_mask = (delta < 0.0) & (damping_mask > 0.0)
        damping_factor = np.clip(1.0 - strength * damping_mask, 0.0, 1.0)
        delta_damped[neg_mask] = delta[neg_mask] * damping_factor[neg_mask]

    # 2. Positive overshoot damping (bright edge glare & limb bright rim)
    if pos_mask is not None and pos_strength > 0.0:
        pos_active = (delta > 0.0) & (pos_mask > 0.0)
        pos_damp = np.clip(1.0 - pos_strength * pos_mask, 0.0, 1.0)
        delta_damped[pos_active] = delta[pos_active] * pos_damp[pos_active]

    res = base + delta_damped

    # 3. Soft-Knee Highlight Protection (Guarantees zero clipped highlights when requested)
    if soft_knee:
        knee = 0.85
        above_knee = res > knee
        if np.any(above_knee):
            res[above_knee] = knee + (1.0 - knee) * np.tanh((res[above_knee] - knee) / (1.0 - knee))

    p_min = float(np.min(base))
    p_max = float(np.max(base))
    if p_max > p_min:
        res = np.clip(res, p_min, max(p_max, float(np.max(sharp))))

    return res.astype(lum_sharp.dtype)


def _measure_dark_halo_ratio(
    lum_base: np.ndarray,
    lum_sharp: np.ndarray,
    damping_mask: np.ndarray,
) -> float:
    """Measure the Dark Halo Ratio (DHR) within step-edge shadow regions.

    Calculates the relative integrated energy of negative undershoot dips
    relative to the local unsharpened base signal.
    """
    base = lum_base.astype(np.float32)
    sharp = lum_sharp.astype(np.float32)
    active = damping_mask > 0.3

    if np.count_nonzero(active) < 10:
        return 0.0

    undershoot = np.maximum(0.0, base[active] - sharp[active])
    base_energy = np.sum(base[active]) + 1e-6
    return float(np.sum(undershoot) / base_energy)


def _measure_chalky_saturation_index(lum_data: np.ndarray) -> float:
    """Measure the Chalky Saturation Index (CSI) for lunar highlights.

    Detects over-stretched, washed-out highlights (crater peaks, ray systems)
    where pixels reach near-peak brightness but have lost micro-gradient texture,
    creating an unnatural 'plaster / chalky' flat clumping appearance.
    Returns the ratio of chalky saturated pixels to valid lunar terrain.
    """
    import cv2
    import numpy as np

    lum = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum, 99.95))
    if p999 <= 1e-5:
        return 0.0

    norm = np.clip(lum / p999, 0.0, 1.0)
    valid_lunar = norm > 0.05
    valid_count = int(np.count_nonzero(valid_lunar))
    if valid_count < 100:
        return 0.0

    # Gradient magnitude via Sobel
    gx = cv2.Sobel(norm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(norm, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)

    # Highlight region: brightness near peak (norm >= 0.88)
    highlight_mask = valid_lunar & (norm >= 0.88)
    highlight_count = int(np.count_nonzero(highlight_mask))
    if highlight_count < 20:
        return 0.0

    # Chalky pixels: highlights where local gradient is near zero (grad < 0.008)
    chalky_mask = highlight_mask & (grad < 0.008)
    chalky_count = int(np.count_nonzero(chalky_mask))

    return float(chalky_count / float(valid_count))


def _guard_luminance(data: np.ndarray) -> np.ndarray:
    if data.ndim == 2:
        return np.asarray(data, dtype=np.float32)
    if data.ndim == 3 and data.shape[0] == 3:
        return (0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]).astype(np.float32)
    raise ValueError("sharp guard requires a monochrome plane or channel-first RGB")


def _wavelet_plane_count(shape, px_scale=1.0):
    if not np.isfinite(px_scale) or px_scale < 1 or min(shape[-2:]) < 1:
        raise ValueError("wavelet adaptation requires a finite scale >= 1 and nonempty image")
    return max(0, min(5 + int(np.ceil(np.log2(px_scale))), int(np.floor(np.log2(min(shape[-2:])))) - 2))


def _wavelet_gain_schedule(coefficients, planes, px_scale=1.0):
    coefficients = np.asarray(coefficients, dtype=np.float64)
    if not np.isfinite(px_scale) or px_scale < 1:
        raise ValueError("wavelet scale must be finite and >= 1")
    if coefficients.shape != (5,) or not np.all(np.isfinite(coefficients)) or np.any(coefficients < 0) or coefficients[-1] != 1:
        raise ValueError("wavelet gains require four finite nonnegative detail gains and a unit residual")
    result = []
    for index in range(max(0, planes - 1)):
        native_index = float(index - np.log2(px_scale))
        increment = float(np.interp(native_index, np.arange(4), coefficients[:4] - 1)) if 0 <= native_index <= 3 else 0.0
        result.append({"index": index, "native_index": native_index, "requested_gain": 1 + increment,
                       "reason": "subnative_scale" if native_index < 0 else "unsupported_coarse_scale" if native_index > 3 else "unity_gain" if increment == 0 else None})
    return result


def _smoothstep(value):
    value = np.clip(value, 0.0, 1.0)
    return value * value * (3 - 2 * value)


def _wavelet_confidence(reference_detail, ctx, index):
    """Measure local support using only the unsharpened reference, never the candidate."""
    import cv2

    shape = ctx["base"].shape
    empty = np.zeros(shape, np.float32)
    flat = ctx.get("flat", np.zeros(shape, bool))
    if ctx["reason"] or np.count_nonzero(flat) < 128:
        return empty, {"reason": ctx["reason"] or "insufficient_noise_samples"}
    detail = _guard_luminance(reference_detail) / ctx["scale"]
    values = detail[flat]
    noise = max(1.4826 * float(np.median(np.abs(values - np.median(values)))), 1e-6)
    height, width = shape
    cell = max(1, int(round(32 * ctx["px_scale"])))
    rows, cols = int(np.ceil(height / cell)), int(np.ceil(width / cell))
    grid = np.full((rows, cols), noise, np.float32)
    for row in range(rows):
        for col in range(cols):
            region = np.s_[row*cell:min((row+1)*cell, height), col*cell:min((col+1)*cell, width)]
            sample = detail[region][flat[region]]
            if sample.size >= 64:
                grid[row, col] = max(noise, 1.4826 * float(np.median(np.abs(sample - np.median(sample)))))
    noise_map = cv2.resize(grid, (width, height), interpolation=cv2.INTER_LINEAR)
    valid = ctx["valid"].astype(np.float32)
    sigma = max(2 * ctx["px_scale"], 1.5 * 2**index)
    mass = cv2.GaussianBlur(valid, (0, 0), sigma)
    energy = cv2.GaussianBlur(detail * detail * valid, (0, 0), sigma) / np.maximum(mass, 1e-6)
    snr = np.sqrt(np.maximum(energy - noise_map**2, 0)) / noise_map
    confidence = _smoothstep((snr - 2) / 2)
    norm = ctx["base"] / ctx["scale"]
    confidence *= _smoothstep((norm - 0.05) / 0.10) * (1 - _smoothstep((norm - 0.85) / 0.13))
    confidence *= 1 - 0.75 * np.maximum(ctx["neg"], ctx["pos"])
    # Pad the mask so full-frame mosaic tiles also have a finite image-boundary distance.
    distance = cv2.distanceTransform(np.pad(ctx["valid"].astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    confidence *= _smoothstep((distance - 2 * ctx["px_scale"]) / (6 * ctx["px_scale"]))
    yy, xx = np.ogrid[:height, :width]
    frame_distance = np.minimum(np.minimum(xx + 0.5, width - xx - 0.5), np.minimum(yy + 0.5, height - yy - 0.5))
    radius = 2 * (2**(index + 1) - 1)
    confidence *= _smoothstep((frame_distance - radius) / radius)
    confidence *= valid
    # shortcut: single-image SNR is statistical evidence; calibrate thresholds on a real lunar corpus.
    return confidence.astype(np.float32), {"reason": None, "noise_sigma": noise,
        "local_noise_range": [float(noise_map.min()), float(noise_map.max())]}


def _reconstruct_trusted_wavelets(baseline, post_deconv, reference_dir, candidate_dir, planes, coefficients, px_scale=1.0):
    from astropy.io import fits

    if baseline.shape != post_deconv.shape or not np.all(np.isfinite(post_deconv)):
        raise ValueError("invalid post-deconvolution wavelet source")
    ctx = _sharp_guard_context(baseline, px_scale)
    schedule = _wavelet_gain_schedule(coefficients, planes, px_scale)
    candidate = post_deconv.copy()
    reference_sum, candidate_sum = np.zeros_like(baseline), np.zeros_like(post_deconv)
    support = np.zeros(ctx["base"].shape, bool)
    for index in range(planes):
        reference = fits.getdata(reference_dir / f"layer{index:02d}.fit", memmap=False).astype(np.float32)
        detail = reference if reference_dir == candidate_dir else fits.getdata(candidate_dir / f"layer{index:02d}.fit", memmap=False).astype(np.float32)
        if reference.shape != baseline.shape or detail.shape != post_deconv.shape or not np.all(np.isfinite(reference)) or not np.all(np.isfinite(detail)):
            raise ValueError(f"invalid wavelet plane {index}: shape or non-finite pixels")
        reference_sum += reference
        candidate_sum += detail
        if index == planes - 1:
            continue
        layer = schedule[index]
        increment = layer["requested_gain"] - 1
        if increment:
            weight, statistics = _wavelet_confidence(reference, ctx, index)
            candidate += increment * weight * detail
            support |= weight >= 0.5
            layer.update(statistics)
        else:
            weight = np.zeros(support.shape, np.float32)
        layer["effective_gain_range"] = sorted([float(1 + increment * weight.min()), float(1 + increment * weight.max())])
        layer["support_pixels"] = int(np.count_nonzero(weight >= 0.5))
    errors = [float(np.max(np.abs(reference_sum - baseline))), float(np.max(np.abs(candidate_sum - post_deconv)))]
    if max(errors) > 1e-6:
        raise ValueError(f"Siril wavelet reconstruction mismatch: {errors}")
    support &= ctx.get("structure", np.zeros(support.shape, bool))
    pixels = int(np.count_nonzero(support))
    receipt = {"mode": "auto", "status": "applied" if pixels >= 32 else "unavailable", "px_scale": px_scale,
        "reference": "moon_unsharpened.fit", "requested_coefficients": list(coefficients), "planes": planes,
        "layers": schedule, "residual_gain": 1.0, "reconstruction_error": errors,
        "support_pixels": pixels, "support_coverage": pixels / max(int(np.count_nonzero(ctx["valid"])), 1),
        "reason": None if pixels >= 32 else "insufficient_trusted_detail"}
    return candidate.astype(np.float32), support, receipt


def _run_wavelet_adaptation(args, work, baseline, post_deconv, detail_stem, coefficients, px_scale, has_deconv):
    from astropy.io import fits

    planes = _wavelet_plane_count(baseline.shape, px_scale)
    schedule = _wavelet_gain_schedule(coefficients, planes, px_scale)
    empty = np.zeros(baseline.shape[-2:], bool)
    identity = all(value == 1 for value in coefficients)
    free, reserve, available = _disk_available(work, args)
    plane_bytes = ((baseline.size * 4 + 2879) // 2880 + 1) * 2880
    scratch_planes = planes * (2 if has_deconv else 1) if not identity and planes >= 2 else 0
    needed = int(np.ceil(1.25 * ((scratch_planes + 1) * plane_bytes + empty.size + 5760)))
    if needed > available:
        die(f"insufficient wavelet disk budget: need {needed} bytes, available {available}; use more disk space or explicitly --wavelet-adapt off")
    budget = {"free_bytes": free, "reserve_bytes": reserve, "available_bytes": available,
              "estimated_peak_additional_bytes": needed}
    if identity or planes < 2:
        reason = "identity_gains" if identity else "insufficient_image_size"
        return post_deconv.copy(), empty, {"mode": "auto", "status": "bypassed" if identity else "unavailable",
            "reason": reason, "planes": planes, "px_scale": px_scale, "layers": schedule,
            "requested_coefficients": list(coefficients), "residual_gain": 1.0, "disk_budget": budget,
            "support_pixels": 0, "support_coverage": 0.0}
    with tempfile.TemporaryDirectory(prefix=".moon-wavelets-", dir=work) as scratch:
        reference_dir = Path(scratch) / "reference"
        candidate_dir = Path(scratch) / "candidate" if has_deconv else reference_dir
        reference_dir.mkdir()
        lines = [f'load "{work / "moon_unsharpened.fit"}"', f'cd "{reference_dir}"', f"extract {planes}"]
        if has_deconv:
            candidate_dir.mkdir()
            lines += [f'load "{work / (detail_stem + "_base.fit")}"', f'cd "{candidate_dir}"', f"extract {planes}"]
        lines.append("exit")
        extracted = run_siril_script(args.siril, lines, Path(scratch), work / "logs/03_wavelet_extract.log", args.timeout)
        if extracted["exit_code"] != 0:
            die(f"wavelet extraction failed; see {extracted['log']}")
        candidate, support, receipt = _reconstruct_trusted_wavelets(
            baseline, post_deconv, reference_dir, candidate_dir, planes, coefficients, px_scale)
    receipt["disk_budget"] = budget
    return candidate, support, receipt


def _load_wavelet_support(work, guard):
    from astropy.io import fits

    name = guard.get("detail_support_file")
    if name is None:
        return None
    if name != "moon_wavelet_support.fit":
        raise ValueError("invalid wavelet support filename")
    support = fits.getdata(work / name, memmap=False)
    if not np.all(np.isfinite(support)) or not np.all((support == 0) | (support == 1)):
        raise ValueError("invalid wavelet support pixels")
    digest = hashlib.sha256(support.astype(np.uint8).tobytes()).hexdigest()
    if digest != guard.get("detail_support_sha256"):
        raise ValueError("wavelet support hash mismatch")
    return support.astype(bool)


def _sharp_guard_context(baseline: np.ndarray, px_scale: float = 1.0, detail_support=None) -> dict:
    """Freeze all measurement regions and intensity units before inspecting candidates."""
    import cv2

    if not np.all(np.isfinite(baseline)):
        raise ValueError("sharp guard baseline contains non-finite pixels")
    base = _guard_luminance(baseline)
    scale = max(float(np.percentile(base, 99.95)), 1e-6)
    norm = base / scale
    valid = norm > 0.05
    size = _odd(max(3, int(round(_px(5.0, px_scale)))))
    interior = cv2.erode(
        (valid & (norm < 0.95)).astype(np.uint8), np.ones((size, size), np.uint8),
        borderType=cv2.BORDER_CONSTANT, borderValue=0,
    ).astype(bool)
    grad = cv2.magnitude(cv2.Sobel(norm, cv2.CV_32F, 1, 0), cv2.Sobel(norm, cv2.CV_32F, 0, 1))
    neg, pos = _compute_edge_ringing_damping_mask(base, return_positive=True, px_scale=px_scale)
    ctx = {"base": base, "source": baseline, "scale": scale, "valid": valid, "interior": interior,
           "neg": neg, "pos": pos, "sigma": _px(1.0, px_scale), "px_scale": px_scale,
           "detail_support": detail_support, "reason": None}
    if detail_support is not None and (detail_support.shape != base.shape or not np.all(np.isfinite(detail_support))):
        raise ValueError("invalid frozen wavelet support dimensions or pixels")
    if np.count_nonzero(valid) < 500 or np.count_nonzero(interior) < 100:
        ctx["reason"] = "insufficient_terrain"
        return ctx
    flat_limit, structure_limit, trim_limit = np.percentile(grad[interior], [20, 50, 99.8])
    ctx["flat"] = interior & (grad <= flat_limit) & (neg <= 0.3) & (pos <= 0.3)
    ctx["structure"] = interior & (grad > max(float(structure_limit), 1e-6))
    if detail_support is not None:
        ctx["structure"] = detail_support.astype(bool)
    ctx["interior"] = interior & (grad <= trim_limit)
    if np.count_nonzero(ctx["flat"]) < 32 or np.count_nonzero(ctx["structure"]) < 32:
        ctx["reason"] = "unmeasurable_detail_or_noise"
        return ctx
    ctx["baseline"] = _sharp_guard_metrics(ctx, baseline)
    return ctx


def _sharp_guard_metrics(ctx: dict, candidate: np.ndarray) -> dict:
    """Measure candidates in the baseline's fixed regions without clipping their peaks."""
    import cv2

    if not np.all(np.isfinite(candidate)):
        return {"finite": False}
    lum = _guard_luminance(candidate)
    base = ctx["base"]
    if lum.shape != base.shape:
        raise ValueError("sharp guard candidate and baseline dimensions differ")
    norm = lum / ctx["scale"]
    grad = cv2.magnitude(cv2.Sobel(norm, cv2.CV_32F, 1, 0), cv2.Sobel(norm, cv2.CV_32F, 0, 1))
    metrics = {"finite": True, "dhr": None, "positive_overshoot_ratio": None,
               "csi": None, "gkm": None, "noise_mad": None, "detail_gradient": None}
    for name, mask, delta in (("dhr", ctx["neg"], base - lum),
                              ("positive_overshoot_ratio", ctx["pos"], lum - base)):
        active = mask > 0.3
        if np.count_nonzero(active) >= 10:
            metrics[name] = float(np.maximum(delta[active], 0).sum() / (base[active].sum() + 1e-6))
    valid = ctx["valid"]
    if np.any(valid):
        metrics["csi"] = float(np.count_nonzero(valid & (norm >= 0.88) & (grad < 0.008)) / np.count_nonzero(valid))
    values = grad[ctx["interior"]]
    if values.size >= 50:
        std = float(np.std(values))
        metrics["gkm"] = float(np.mean(((values - np.mean(values)) / std) ** 4)) if std > 1e-7 else 0.0
    if "flat" in ctx and np.count_nonzero(ctx["flat"]) >= 32:
        residual = norm - cv2.GaussianBlur(norm, (0, 0), ctx["sigma"])
        values = residual[ctx["flat"]]
        metrics["noise_mad"] = float(np.median(np.abs(values - np.median(values))))
    if "structure" in ctx and np.count_nonzero(ctx["structure"]) >= 32:
        metrics["detail_gradient"] = float(np.median(grad[ctx["structure"]]))
    # Newly clipped pixels must not be hidden by normalizing to each candidate's maximum.
    source = ctx["source"]
    clipped = ((candidate >= 1) & (source < 1)) | ((candidate <= 0) & (source > 0)) | ((candidate < 0) & (source >= 0))
    metrics["new_clipped_pixels"] = int(np.count_nonzero(clipped))
    return metrics


def _sharp_guard_reasons(ctx: dict, metrics: dict, require_gain: bool = True) -> list[str]:
    if not metrics["finite"]:
        return ["non_finite_candidate"]
    if ctx["reason"]:
        return [ctx["reason"]]
    baseline = ctx["baseline"]
    # shortcut: thresholds have synthetic coverage; calibrate on real lunar data before relaxing them.
    limits = {"dhr": 0.035, "positive_overshoot_ratio": 0.035,
              "csi": max(0.025, baseline["csi"] + 0.005),
              "gkm": max(14.0, baseline["gkm"] * 1.10),
              "noise_mad": max(baseline["noise_mad"] * 1.10, 1e-6)}
    reasons = []
    for name, limit in limits.items():
        value = metrics[name]
        if value is None and name in ("dhr", "positive_overshoot_ratio"):
            continue
        if value is None or not np.isfinite(value):
            reasons.append(f"invalid_{name}")
        elif value > limit:
            reasons.append(name)
    if metrics["new_clipped_pixels"]:
        reasons.append("new_clipping")
    detail = metrics["detail_gradient"]
    if detail is None or not np.isfinite(detail):
        reasons.append("invalid_detail_gain")
    elif require_gain and detail < baseline["detail_gradient"] * 1.005:
        reasons.append("insufficient_detail_gain")
    return reasons


def _select_guarded_sharpening(baseline: np.ndarray, candidate: np.ndarray, ctx: dict, mode: str = "auto", render=None) -> tuple[np.ndarray, dict]:
    if candidate.shape != baseline.shape:
        raise ValueError("sharp guard candidate and baseline shapes differ")
    result = {"mode": mode, "status": "off" if mode == "off" else "fallback", "alpha": 1.0 if mode == "off" else 0.0,
              "baseline": _sharp_guard_metrics(ctx, baseline), "attempts": []}
    delivery_baseline = render(baseline) if render is not None else None
    delivery_ctx = _sharp_guard_context(delivery_baseline, ctx["px_scale"], ctx["detail_support"]) if render is not None else None
    if delivery_ctx is not None:
        result["delivery_baseline"] = _sharp_guard_metrics(delivery_ctx, delivery_baseline)
    if mode == "off":
        if not np.all(np.isfinite(candidate)):
            raise ValueError("sharpened image contains non-finite pixels")
        result["selected"] = _sharp_guard_metrics(ctx, candidate)
        if delivery_ctx is not None:
            result["delivery_selected"] = _sharp_guard_metrics(delivery_ctx, render(candidate))
        return candidate, result
    for alpha in (1.0, 0.75, 0.5, 0.25):
        trial = baseline + alpha * (candidate - baseline)
        metrics = _sharp_guard_metrics(ctx, trial)
        reasons = _sharp_guard_reasons(ctx, metrics)
        attempt = {"alpha": alpha, "metrics": metrics, "reasons": reasons}
        if delivery_ctx is not None and not reasons:
            delivery = _sharp_guard_metrics(delivery_ctx, render(trial))
            attempt["delivery"] = delivery
            reasons.extend(f"delivery_{reason}" for reason in _sharp_guard_reasons(delivery_ctx, delivery))
        result["attempts"].append(attempt)
        if not reasons:
            result.update(status="accepted" if alpha == 1.0 else "reduced", alpha=alpha, selected=metrics)
            if delivery_ctx is not None:
                result["delivery_selected"] = attempt["delivery"]
            return trial.astype(np.float32), result
    result["selected"] = result["baseline"]
    if delivery_ctx is not None:
        result["delivery_selected"] = result["delivery_baseline"]
    return baseline.copy(), result


def _compose_natural_lrgb(lum, color, circle_meta=None, px_scale=1.0):
    """Keep the existing natural-color tone and limb treatment in float FITS coordinates."""
    color_lum = _guard_luminance(color)
    rgb = np.clip(color * (lum / np.maximum(color_lum, 1e-6)), 0.0, 1.0)
    knee = 0.75
    high = rgb > knee
    rgb[high] = knee + (1.0 - knee) * np.tanh((rgb[high] - knee) / (1.0 - knee))
    if circle_meta and circle_meta.get("active"):
        yy, xx = np.mgrid[:lum.shape[0], :lum.shape[1]]
        distance = float(circle_meta["radius"]) - np.hypot(xx - float(circle_meta["center_x"]), yy - float(circle_meta["center_y"]))
        fade = np.clip((distance - _px(4.0, px_scale)) / _px(14.0, px_scale), 0.0, 1.0)
        fade = fade * fade * (3.0 - 2.0 * fade)
        rgb = lum + (rgb - lum) * fade
        rgb *= np.clip((distance + _px(1.0, px_scale)) / _px(2.5, px_scale), 0.0, 1.0)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


def _write_moon_product(work: Path, name: str, data: np.ndarray, header) -> None:
    """Write the float master and both display formats from the same FITS-oriented pixels."""
    import cv2
    from astropy.io import fits

    if not np.all(np.isfinite(data)):
        raise ValueError(f"{name} contains non-finite pixels")
    data = np.clip(data, 0.0, 1.0).astype(np.float32)
    fits.writeto(work / f"{name}_master.fit", data, header=header, overwrite=True)
    screen = np.moveaxis(data[[2, 1, 0], ::-1, :], 0, -1) if data.ndim == 3 else data[::-1, :]
    for suffix, scale in (("tif", 65535), ("jpg", 255)):
        if suffix == "tif" and name == "moon_mineral":
            continue
        pixels = np.rint(screen * scale).astype(np.uint16 if suffix == "tif" else np.uint8)
        options = [int(cv2.IMWRITE_JPEG_QUALITY), 95] if suffix == "jpg" else []
        if not cv2.imwrite(str(work / f"{name}.{suffix}"), pixels, options):
            raise OSError(f"failed to write {name}.{suffix}")


def _estimate_adaptive_midtone(
    lum_net: np.ndarray,
    moon_mask: np.ndarray | None = None,
    hi_val: float | None = None,
    target_mare_lum: float = 0.54,
) -> dict:
    """Analytically solve the optimal Siril MTF midtone stretch parameter.

    In astrophotography, the optimal visual median of the lunar surface
    is approximately 0.50 to 0.55 (natural luminous albedo perception).
    By setting target_mare_lum = 0.54, the median of the lunar surface
    maps precisely to 0.54 (or >=0.50 for low-contrast full moon), organically
    handling crescent, gibbous, and full moon as well as under- and over-exposed sequences.

    Returns dict containing:
      - midtone: optimal m parameter in [0.10, 0.65]
      - median_raw: normalized median of lunar surface
      - contrast_ratio: dynamic contrast of terrain
      - target: target mapping value
    """
    lum_2d = lum_net.astype(np.float32)
    p999 = float(np.percentile(lum_2d, 99.95))
    if hi_val is None or hi_val <= 1e-6:
        hi_val = max(p999 * 1.08, 1e-4)

    if moon_mask is None:
        moon_mask = (lum_2d > 0.05 * p999) & (lum_2d < 0.95 * p999)

    valid_vals = lum_2d[moon_mask]
    if valid_vals.size < 200:
        return {"midtone": 0.42, "median_raw": 0.42, "contrast_ratio": 2.5, "target": target_mare_lum}

    x_med = float(np.median(valid_vals))
    x_norm = float(np.clip(x_med / hi_val, 0.01, 0.95))

    p10 = float(np.percentile(valid_vals, 10))
    p90 = float(np.percentile(valid_vals, 90))
    contrast_ratio = float((p90 - p10) / max(p10, 1e-4))

    # Dynamically tune target_mare_lum slightly based on contrast
    y_target = float(np.clip(target_mare_lum, 0.40, 0.60))
    if contrast_ratio < 2.0:
        y_target = max(0.50, y_target - 0.015)

    denom = x_norm * (1.0 - 2.0 * y_target) + y_target
    if abs(denom) < 1e-6:
        m_opt = x_norm
    else:
        m_opt = (x_norm * (1.0 - y_target)) / denom

    m_opt = float(np.clip(round(m_opt, 3), 0.10, 0.65))
    return {
        "midtone": m_opt,
        "median_raw": round(x_norm, 3),
        "contrast_ratio": round(contrast_ratio, 2),
        "target": round(y_target, 3),
    }


def _estimate_seeing_cutoff(lum_2d: np.ndarray, patch_size: int = 512) -> float:
    """Estimate atmospheric seeing spatial cutoff frequency using 2D-FFT Radial PSD.

    Returns the normalized spatial cutoff frequency k_cutoff in [0.0, 1.0] (relative to Nyquist),
    where lunar surface signal drops to the high-frequency sensor noise floor.
    """
    lum = lum_2d.astype(np.float32)
    H, W = lum.shape
    if H < 128 or W < 128:
        return 0.50

    actual_patch = min(patch_size, H, W)
    actual_patch = (actual_patch // 2) * 2
    if actual_patch < 64:
        return 0.50

    x0, y0, x1, y1 = _locate_high_contrast_roi(lum, roi_size=actual_patch)
    patch = lum[y0:y1, x0:x1]
    if patch.shape[0] < 32 or patch.shape[1] < 32:
        return 0.50

    hann_2d = np.outer(np.hanning(patch.shape[0]), np.hanning(patch.shape[1]))
    patch_w = (patch - np.mean(patch)) * hann_2d

    F = np.fft.fftshift(np.fft.fft2(patch_w))
    psd = np.abs(F) ** 2

    pH, pW = psd.shape
    cy, cx = pH // 2, pW // 2
    y, x = np.ogrid[:pH, :pW]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(int)

    r_max = min(cx, cy)
    if r_max < 16:
        return 0.50

    radial_prof = np.zeros(r_max, dtype=np.float64)
    for radius in range(r_max):
        mask = (r == radius)
        if np.any(mask):
            radial_prof[radius] = np.mean(psd[mask])

    noise_floor = float(np.median(radial_prof[int(r_max * 0.85):]))
    snr_prof = radial_prof / max(noise_floor, 1e-12)

    cutoff_idx = np.where(snr_prof < 2.0)[0]
    if len(cutoff_idx) > 0 and cutoff_idx[0] > 5:
        k_cutoff = float(cutoff_idx[0]) / float(r_max)
    else:
        k_cutoff = 1.0

    return float(np.clip(round(k_cutoff, 3), 0.15, 1.00))


def _measure_gradient_kurtosis(lum_data: np.ndarray) -> float:
    """Measure the Gradient Kurtosis Metric (GKM) to quantify brittle / crunchy texture.

    Over-sharpened images with excessive multi-scale wavelets or CLAHE amplification
    exhibit heavy-tailed, unnaturally spiky gradient distributions with high kurtosis.
    Organic, well-balanced lunar textures have kurtosis in [2.5, 12.0].
    Values > 18.0 indicate brittle, over-sharpened textures.
    """
    import cv2
    import numpy as np

    lum = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum, 99.95))
    if p999 <= 1e-5:
        return 0.0

    norm = np.clip(lum / p999, 0.0, 1.0)
    valid_mask = (norm > 0.08) & (norm < 0.95)
    if np.count_nonzero(valid_mask) < 200:
        return 0.0

    H, W = lum.shape
    # Erode valid_mask by 25-50px to strictly eliminate the celestial limb edge step
    er_radius = max(5, min(51, int(min(H, W) * 0.025) | 1))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (er_radius, er_radius))
    interior_mask = cv2.erode(valid_mask.astype(np.uint8), kernel).astype(bool)
    if np.count_nonzero(interior_mask) < 100:
        kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        interior_mask = cv2.erode(valid_mask.astype(np.uint8), kernel_small).astype(bool)
        if np.count_nonzero(interior_mask) < 50:
            interior_mask = valid_mask

    gx = cv2.Sobel(norm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(norm, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    g_samples = grad[interior_mask]

    # Trim top 0.2% extreme outliers to prevent hot-pixel / cosmic ray skew
    p998 = float(np.percentile(g_samples, 99.8))
    trimmed = g_samples[g_samples <= p998]

    mean_g = float(np.mean(trimmed))
    std_g = float(np.std(trimmed))
    if std_g < 1e-7:
        return 0.0

    z = (trimmed - mean_g) / std_g
    kurt = float(np.mean(z ** 4))
    return kurt


def _estimate_adaptive_sharpening(
    lum_data: np.ndarray,
    has_deconv: bool = True,
    interp_used: str = "cu",
    sharp_mode: str = "auto",
    user_wavelet_l1: float | None = None,
    user_clahe_clip: float | None = None,
    user_unsharp: float | None = None,
    anti_ringing: str = "auto",
    damping_factor: float | None = None,
) -> dict:
    """Calculate organic, physically-adaptive lunar wavelet and contrast parameters.

    Prevents over-sharpening (chalky crater rims, crunchy texture, noisy flat maria)
    by jointly analyzing:
      1. Lunar dynamic contrast ratio (p90 - p10) / p10
      2. Residual high-frequency noise floor (sigma_noise via Donoho MAD in flat maria)
      3. Seeing cutoff frequency via 2D-FFT radial PSD
      4. Deconvolution status (applies MTF discount factor if Airy PSF deconvolution was run)
      5. Interpolation filter properties (Bicubic vs Bilinear)
      6. Anti-redundancy defense: automatically bypasses USM unsharp mask when
         multi-scale wavelets or deconvolution are active, eliminating artificial halos.
      7. Edge undershoot anti-ringing damping factor calculation.
    """
    lum_2d = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum_2d, 99.95))
    moon_mask = (lum_2d > 0.05 * p999) & (lum_2d < 0.95 * p999)
    valid_pixels = lum_2d[moon_mask]

    seeing_cutoff = _estimate_seeing_cutoff(lum_2d)

    if valid_pixels.size < 500:
        contrast_ratio = 3.5
        sigma_noise = 0.0005
    else:
        p10 = float(np.percentile(valid_pixels, 10))
        p90 = float(np.percentile(valid_pixels, 90))
        contrast_ratio = float((p90 - p10) / max(p10, 1e-4))

        diff_h = np.abs(np.diff(lum_2d, axis=1)[:-1, :])
        diff_v = np.abs(np.diff(lum_2d, axis=0)[:, :-1])
        std_local = diff_h + diff_v
        sub_mask = moon_mask[:-1, :-1]
        if np.count_nonzero(sub_mask) > 500:
            flat_thresh = np.percentile(std_local[sub_mask], 20)
            flat_mask = sub_mask & (std_local <= flat_thresh)
            sigma_noise = float(np.median(std_local[flat_mask]) / 1.4826)
        else:
            sigma_noise = float(np.std(lum_2d[~moon_mask])) if np.count_nonzero(~moon_mask) > 100 else 0.001

    if sharp_mode == "none":
        w_coeffs = [1.00, 1.00, 1.00, 1.00, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mellow":
        w_coeffs = [1.01, 1.02, 1.03, 1.01, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mild":
        w_coeffs = [1.01, 1.04, 1.06, 1.03, 1.00]
        clahe_clip = 0.08
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "crisp":
        w_coeffs = [1.04, 1.10, 1.12, 1.08, 1.00]
        clahe_clip = 0.30
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    else:  # "auto" -> Scheme B: Balanced Natural Baseline (Soft Gentle & High Detail)
        deconv_discount = 0.80 if has_deconv else 1.0
        noise_penalty = float(np.clip(1.0 - (sigma_noise / max(p999 * 0.005, 1e-6)), 0.45, 1.0))
        # Dynamic seeing-aware Layer 1 gain:
        # If seeing is poor or noise floor is high, suppress L1 strictly to 1.00.
        # If seeing is clean and sensor noise is low, allow subtle micro-contrast even with deconv.
        if sigma_noise > 0.0006 or seeing_cutoff < 0.42:
            l1_base = 0.00
        elif has_deconv:
            l1_base = 0.025 if interp_used == "cu" else 0.035
        else:
            l1_base = 0.035 if interp_used == "cu" else 0.05

        effective_factor = deconv_discount * noise_penalty
        w1 = 1.0 + l1_base * effective_factor
        w2 = 1.0 + 0.08 * effective_factor
        w3 = 1.0 + 0.11 * effective_factor
        w4 = 1.0 + 0.05 * effective_factor

        # Gentle floor clamping for calm seeing & clean background:
        # Benchmarking Moon3_gentle_work aesthetic ([1.02, 1.04, 1.06, 1.03])
        # Ensures that clean, high-resolution masters don't get overly suppressed into a flat image
        if seeing_cutoff >= 0.60 and sigma_noise < 0.0001:
            w1 = max(w1, 1.02)
            w2 = max(w2, 1.04)
            w3 = max(w3, 1.06)
            w4 = max(w4, 1.03)

        w_coeffs = [round(w1, 2), round(w2, 2), round(w3, 2), round(w4, 2), 1.00]
        if has_deconv or sigma_noise > 0.0006:
            # When deconvolution is active, physical MTF restoration renders CLAHE redundant;
            # bypassing CLAHE completely prevents 13.5x shot noise amplification in flat maria
            # and eliminates clipped white chalky crater rims.
            clahe_clip = 0.0
        else:
            clahe_clip = round(float(np.clip(0.40 / max(contrast_ratio, 1.0), 0.05, 0.15)), 2)
        unsharp_amt = 0.0

    # User manual overrides
    if user_wavelet_l1 is not None:
        w_coeffs[0] = round(float(user_wavelet_l1), 2)
    if user_clahe_clip is not None:
        clahe_clip = round(float(user_clahe_clip), 2)
    if user_unsharp is not None:
        unsharp_amt = round(float(user_unsharp), 2)

    # Determine recommended anti-ringing damping strength
    if damping_factor is not None:
        rec_damping = float(np.clip(damping_factor, 0.0, 1.0))
    elif anti_ringing == "off":
        rec_damping = 0.0
    elif anti_ringing == "mild":
        rec_damping = 0.35
    elif anti_ringing == "aggressive":
        rec_damping = 0.85
    else:  # "auto"
        if has_deconv and interp_used == "cu":
            rec_damping = 0.65
        elif interp_used == "li":
            rec_damping = 0.45
        else:
            rec_damping = 0.55

    _wavelet_gain_schedule(w_coeffs, 5)
    wrecons_cmd = "wrecons " + " ".join(f"{value:.2f}" for value in w_coeffs)
    clahe_lines = [f"clahe {clahe_clip:.2f} 32"] if clahe_clip > 0 else []
    unsharp_lines = [f"unsharp 1.0 {unsharp_amt:.2f}"] if unsharp_amt > 0 else []

    return {
        "sharp_mode": sharp_mode,
        "wrecons_cmd": wrecons_cmd,
        "wavelet_coeffs": w_coeffs,
        "clahe_lines": clahe_lines,
        "clahe_clip": clahe_clip,
        "unsharp_lines": unsharp_lines,
        "unsharp_amount": unsharp_amt,
        "contrast_ratio": contrast_ratio,
        "sigma_noise": sigma_noise,
        "seeing_cutoff": seeing_cutoff,
        "noise_penalty": noise_penalty if sharp_mode == "auto" else 1.0,
        "deconv_discount": deconv_discount if sharp_mode == "auto" else 1.0,
        "anti_ringing": anti_ringing,
        "damping_strength": rec_damping,
    }


def cmd_postprocess(args) -> None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(args.work).expanduser().resolve()
    master = work / args.master
    if not master.exists():
        die(f"master FITS not found: {master}; run stack first")

    logs_dir = work / "logs"

    # 0. Atmospheric Dispersion Correction (ADC)
    if not getattr(args, "no_adc", False):
        adc_res = align_rgb_channels(master)
        if adc_res.get("applied"):
            log(f"ADC applied: R shift={adc_res['shift_r']}, B shift={adc_res['shift_b']} (resp R={adc_res['response_r']:.2f}, B={adc_res['response_b']:.2f})")
            dump_json(work / "adc_receipt.json", adc_res)
        else:
            log("ADC check: channels aligned or single channel data")

    # Analyze linear master data for planetary histogram stretch & white balance
    with fits.open(master, memmap=False) as hdul:
        hdr = hdul[0].header
        d = hdul[0].data
    if not np.all(np.isfinite(d)):
        die("master FITS contains non-finite pixels; repair the source before postprocessing")

    mosaic_mode = getattr(args, "mosaic_mode", "disc")
    px_scale = _resolve_px_scale(args, work, master)
    if px_scale != 1.0:
        log(f"drizzle supersampling detected: the working grid is {px_scale:g}x finer than "
            f"native, so pixel-domain tolerances and kernels are scaled by {px_scale:g}")
    optics = _infer_optical_parameters(hdr, d, args, mosaic_mode=mosaic_mode, px_scale=px_scale)
    px_size = optics["pixel_size"]
    fl = optics["focal_length"]
    dia = optics["aperture"]
    requested_deconv = getattr(args, "deconv", "auto")
    deconv_method = ("sb" if optics["trusted"] else "none") if requested_deconv == "auto" else requested_deconv
    if deconv_method in ("sb", "wiener") and not optics["trusted"]:
        die("physical deconvolution needs trusted focal length, aperture and pixel size; provide --focal/--aperture/--pixel-size or use --deconv auto/none")

    if optics.get("lunar_disc"):
        disc = optics["lunar_disc"]
        log(f"optical inference: fitted lunar disc D={disc['diameter_px']:.1f}px (R={disc['radius_px']:.1f}px, res={disc['res_std']:.2f}px)")
        log(f"optical inference: sensor diameter={disc['sensor_dia_mm']:.2f}mm -> inferred focal={fl:.0f}mm (range {disc['focal_range'][0]:.0f}~{disc['focal_range'][1]:.0f}mm)")
    log(f"optical setup: focal={fl}, aperture={dia}, pixel_size={px_size}; trusted={optics['trusted']}")
    if requested_deconv == "auto" and not optics["trusted"]:
        log("deconvolution auto: missing trusted optics, continuing without an assumed Airy PSF")

    # Adaptive PSF kernel size: covers ~3 full Airy diffraction rings
    # Short focal (small Airy disc) → smaller kernel avoids high-frequency ringing
    # Long focal (large Airy disc) → larger kernel captures complete diffraction pattern
    r_airy = optics["airy_radius_px"] or 0.0
    ks = int(max(15, min(65, round(r_airy * 6.0))))
    if ks % 2 == 0:
        ks += 1  # PSF kernel must be odd
    log(f"adaptive PSF kernel: ks={ks} (from Airy radius {r_airy:.2f}px × 6.0)")

    deconv_lines = []
    if deconv_method == "sb":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks={ks} -savepsf=psf_airy.fit",
            "sb -loadpsf=psf_airy.fit -iters=2 -alpha=2000",
        ]
        log(f"deconvolution: Split Bregman with physical Airy PSF (dia={dia:.1f}mm, fl={fl:.1f}mm, px={px_size:.2f}um)")
    elif deconv_method == "wiener":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks={ks} -savepsf=psf_airy.fit",
            "wiener -loadpsf=psf_airy.fit -alpha=0.01",
        ]
        log(f"deconvolution: Wiener with physical Airy PSF (dia={dia:.1f}mm, fl={fl:.1f}mm, px={px_size:.2f}um)")
    elif deconv_method == "rl":
        deconv_lines = [
            "makepsf manual -gaussian -fwhm=2.0 -savepsf=psf.fit",
            "rl -loadpsf=psf.fit -iters=10 -tv -alpha=1000",
        ]
        log("deconvolution: classic Richardson-Lucy with Gaussian PSF")
    else:
        log("deconvolution: bypassed (none)")

    # Detect interpolation method from stack receipt for interp-aware wavelet tuning
    interp_used = "cu"
    stack_receipt_file = work / "stack_receipt.json"
    if stack_receipt_file.exists():
        try:
            sr = load_json(stack_receipt_file)
            interp_used = sr.get("interp", "cu")
        except Exception:
            pass

    mineral_mode = getattr(args, "mineral_mode", "lrgb")
    wb_mode = getattr(args, "white_balance", "gray-world")
    is_rgb = (d.ndim == 3 and d.shape[0] >= 3)
    user_mid_arg = getattr(args, "midtone", None)
    user_mid = float(user_mid_arg) if user_mid_arg is not None else None

    sat_base = float(getattr(args, "sat_base", 0.3))
    sat_fe = float(getattr(args, "sat_fe", 0.8))
    sat_ti = float(getattr(args, "sat_ti", 0.8))
    sat_bg = float(getattr(args, "sat_bg_factor", 1.2))

    imported = load_json(work/"import_receipt.json") if (work/"import_receipt.json").exists() else {}
    saturation = imported.get("video_metadata",{}).get("source_saturation_rgb")
    severe_clipping = bool(saturation and max(saturation) > 0.05)
    if severe_clipping:
        sat_base, sat_fe, sat_ti = min(sat_base,0.1), min(sat_fe,0.2), min(sat_ti,0.2)
        log("source channel clipping detected: restrained mineral saturation; lost colors cannot be recovered")
    sat_lines = []
    if sat_base > 0:
        sat_lines.append(f"satu {sat_base:.2f} {sat_bg:.1f} 6")  # base foundation across all hues
    if sat_fe > 0:
        sat_lines.append(f"satu {sat_fe:.2f} {sat_bg:.1f} 1")    # targeted orange-yellow (Fe-rich basalt/highlands)
    if sat_ti > 0:
        sat_lines.append(f"satu {sat_ti:.2f} {sat_bg:.1f} 3")    # targeted cyan (Ti-rich boundary)
        sat_lines.append(f"satu {sat_ti:.2f} {sat_bg:.1f} 4")    # targeted cyan-magenta (Mare Tranquillitatis core Ti)
    if not sat_lines:
        sat_lines = [f"satu 0.70 {sat_bg:.1f} 6", f"satu 0.40 1.0 6"]

    mosaic_mode = getattr(args, "mosaic_mode", "disc")
    glare_mode = getattr(args, "glare_suppress", "auto")
    if mosaic_mode == "tile":
        glare_mode = "off"
        log("mosaic tile mode active: bypassing lunar limb detection and glare suppression")

    # Load locked calibration profile (P2: Global color and histogram locking)
    lock_profile_path = getattr(args, "lock_profile", None)
    lock_from_dir = getattr(args, "lock_from", None)
    locked_profile, lock_source = _load_locked_profile(lock_profile_path, lock_from_dir)

    lock_wb = None
    if getattr(args, "lock_wb", None):
        lock_wb = (float(args.lock_wb[0]), float(args.lock_wb[1]))
    lock_stretch = None
    if getattr(args, "lock_stretch", None):
        lock_stretch = (float(args.lock_stretch[0]), float(args.lock_stretch[1]))

    if locked_profile:
        log(f"loaded calibration profile from {lock_source}")
        # Inherit mineral aesthetic params if user did not specify override
        if "mineral" in locked_profile:
            m_prof = locked_profile["mineral"]
            if getattr(args, "mineral_fe_boost", None) is None and "fe_boost" in m_prof:
                args.mineral_fe_boost = float(m_prof["fe_boost"])
            if getattr(args, "mineral_ti_boost", None) is None and "ti_boost" in m_prof:
                args.mineral_ti_boost = float(m_prof["ti_boost"])
            if getattr(args, "mineral_gamma", None) is None and "gamma" in m_prof:
                args.mineral_gamma = float(m_prof["gamma"])

    if is_rgb:
        bg_r = _estimate_pedestal(d[0])
        bg_g = _estimate_pedestal(d[1])
        bg_b = _estimate_pedestal(d[2])
        extinction_comp = getattr(args, "extinction_comp", "auto")
        wb = _calculate_channel_balance(
            d, bg_r, bg_g, bg_b,
            mid_val=user_mid,
            wb_mode=wb_mode,
            glare_mode=glare_mode,
            extinction_comp=extinction_comp,
            locked_profile=locked_profile,
            lock_wb=lock_wb,
            lock_stretch=lock_stretch,
            px_scale=px_scale,
        )
        mid_val = wb.get("mid_val", 0.42)
        mid_info = wb.get("mid_info", {})
        if "glare_meta" in wb and wb["glare_meta"].get("active"):
            gm = wb["glare_meta"]
            log(f"lunar limb glare suppression active: center=({gm['center_x']:.1f}, {gm['center_y']:.1f}), R={gm['radius']:.1f}px (res_std={gm['residual_std']:.2f}px, falloff={gm['delta']:.1f}px)")
        if "extinction_meta" in wb and wb["extinction_meta"].get("applied"):
            em = wb["extinction_meta"]
            log(f"atmospheric extinction gradient compensated: zenith_angle={em['zenith_angle_deg']:.1f}°, B-grad={em['amp_b']*100:.1f}%, R-grad={em['amp_r']*100:.1f}%, confidence={em['confidence']:.2f}")
            dump_json(work / "extinction_receipt.json", em)
        if wb.get("wb_locked"):
            log(f"neutral Gray-World balance LOCKED: R/G={wb['ratio_r']:.3f}, B/G={wb['ratio_b']:.3f} (k_r={wb['k_r']:.3f}, k_b={wb['k_b']:.3f})")
        elif wb["moon_pixels"] >= 200 and wb_mode == "gray-world":
            log(f"neutral Gray-World balance applied: R/G={wb['ratio_r']:.3f}, B/G={wb['ratio_b']:.3f} ({wb['moon_pixels']} lunar surface pixels sampled)")
        if wb.get("stretch_locked"):
            log(f"calibrated channels (LOCKED hi_unified={wb['hi_lum']:.5f}, midtone={mid_val:.3f})")
        elif user_mid is None:
            log(f"calibrated channels with adaptive midtone: m={mid_val:.3f} (median={mid_info.get('median_raw', 0.5):.3f}, target={mid_info.get('target', 0.5):.2f})")
        else:
            log(f"calibrated channels (bg=[{wb['bg_r']:.5f}, {wb['bg_g']:.5f}, {wb['bg_b']:.5f}], hi=[{wb['hi_r']:.5f}, {wb['hi_g']:.5f}, {wb['hi_b']:.5f}], midtone={mid_val:.3f})")
        if getattr(args,"luminance_channel","weighted") == "green":
            wb["lum_data"] = np.maximum(0.0, d[1]-bg_g).astype(np.float32)
            wb["bg_lum"] = 0.0
            wb["hi_lum"] = max(float(np.percentile(wb["lum_data"],99.95))*1.08,1e-4)
        stretch_scale = max(1.0,*(wb["hi_"+channel] for channel in ("r","g","b","lum")))
        if stretch_scale > 1:
            wb["color_data"] = wb["color_data"]/stretch_scale
            wb["lum_data"] = wb["lum_data"]/stretch_scale
            for channel in ("r","g","b","lum"):
                wb["bg_"+channel] /= stretch_scale
                wb["hi_"+channel] /= stretch_scale
        wb["stretch_scale"] = stretch_scale
        lum_for_sharp = wb["lum_data"]
    else:
        plane = d if d.ndim == 2 else d[0]
        bg_val = _estimate_pedestal(plane)
        p999_val = float(np.percentile(plane, 99.95))
        hi_val = max(p999_val * 1.10, bg_val + 0.01)
        if lock_stretch:
            bg_val, hi_val = lock_stretch[0], lock_stretch[1]
            log(f"monochrome stretch ceiling LOCKED: bg={bg_val:.5f}, hi={hi_val:.5f}")
        elif locked_profile and "histogram" in locked_profile:
            hist_prof = locked_profile["histogram"]
            hi_val = float(hist_prof.get("hi_lum", hist_prof.get("hi_unified", hi_val)))
            bg_val = float(hist_prof.get("bg_lum", bg_val))
            log(f"monochrome stretch ceiling LOCKED from profile: bg={bg_val:.5f}, hi={hi_val:.5f}")
        mono_target = master.name
        mono_scale = max(1.0,hi_val)
        if mono_scale > 1:
            plane = plane/mono_scale
            bg_val /= mono_scale
            hi_val /= mono_scale
            mono_target = "moon_mono_linear.fit"
            fits.writeto(work/mono_target,plane.astype(np.float32),header=hdr,overwrite=True)
        lum_for_sharp = plane

        if locked_profile and "histogram" in locked_profile and "midtone" in locked_profile["histogram"] and user_mid is None:
            mid_val = float(locked_profile["histogram"]["midtone"])
            log(f"monochrome midtone LOCKED from profile: m={mid_val:.3f}")
        elif user_mid is not None:
            mid_val = user_mid
            log(f"monochrome midtone specified: m={mid_val:.3f}")
        else:
            mid_res = _estimate_adaptive_midtone(np.maximum(0.0, plane - bg_val), hi_val=hi_val - bg_val)
            mid_val = mid_res["midtone"]
            log(f"monochrome adaptive midtone solved: m={mid_val:.3f} (median={mid_res['median_raw']:.3f})")

    # Dynamic Organic Sharpening Parameter Generation
    has_deconv = (deconv_method in ("sb", "wiener", "rl"))
    sharp_mode = getattr(args, "sharp_mode", "auto")
    user_wavelet_l1 = getattr(args, "wavelet_l1", None)
    user_clahe_clip = getattr(args, "clahe_clip", None)
    user_unsharp = getattr(args, "unsharp", None)
    anti_ringing = getattr(args, "anti_ringing", "auto")
    damping_factor = getattr(args, "damping_factor", None)

    sharp_info = _estimate_adaptive_sharpening(
        lum_for_sharp,
        has_deconv=has_deconv,
        interp_used=interp_used,
        sharp_mode=sharp_mode,
        user_wavelet_l1=user_wavelet_l1,
        user_clahe_clip=user_clahe_clip,
        user_unsharp=user_unsharp,
        anti_ringing=anti_ringing,
        damping_factor=damping_factor,
    )
    wrecons_cmd = sharp_info["wrecons_cmd"]
    clahe_lines = sharp_info["clahe_lines"]
    unsharp_lines = sharp_info["unsharp_lines"]
    wavelet_mode = getattr(args, "wavelet_adapt", "auto")
    detail_lines = (["wavelet 5 2", wrecons_cmd, *clahe_lines, *unsharp_lines]
                    if wavelet_mode == "off" else [])

    log(f"adaptive organic sharpening: mode='{sharp_mode}', contrast_ratio={sharp_info['contrast_ratio']:.2f}, seeing_cutoff={sharp_info.get('seeing_cutoff', 1.0):.2f}, mare_noise={sharp_info['sigma_noise']:.6f}")
    log(f"  - wavelet: {wrecons_cmd} (deconv discount={sharp_info['deconv_discount']:.2f}x, noise penalty={sharp_info['noise_penalty']:.2f})")
    if clahe_lines:
        log(f"  - CLAHE: {sharp_info['clahe_clip']:.2f} clip (dynamic contrast-aware)")
    else:
        log("  - CLAHE: bypassed")
    if unsharp_lines:
        log(f"  - USM unsharp: amount={sharp_info['unsharp_amount']:.2f}")
    else:
        log("  - USM unsharp: bypassed (anti-redundancy protection)")
    if anti_ringing != "off" and sharp_info["damping_strength"] > 0:
        log(f"  - anti-ringing: mode='{anti_ringing}', strength={sharp_info['damping_strength']:.2f} (shadow-side undershoot protection)")
    else:
        log("  - anti-ringing: bypassed")

    if is_rgb:
        if mineral_mode == "lrgb":
            log("executing professional L/RGB separation pipeline (Luminance detail deconv/wavelets + Chrominance saturation)...")
            lum_path = work / "moon_lum.fit"
            fits.writeto(lum_path, wb["lum_data"], header=hdr, overwrite=True)
            log(f"calibrated Luminance (bg_lum={wb['bg_lum']:.5f}, hi_lum={wb['hi_lum']:.5f})")

            color_target = master.stem
            if (wb_mode == "gray-world" or wb.get("stretch_scale",1)>1) and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                # --- Stage 1: Luminance High-Frequency Processing ---
                "load moon_lum",
                f"mtf {wb['bg_lum']:.6f} {mid_val:.2f} {wb['hi_lum']:.6f}",
                "save moon_unsharpened",
                "load moon_lum",
                *deconv_lines,
                f"mtf {wb['bg_lum']:.6f} {mid_val:.2f} {wb['hi_lum']:.6f}",
                "save moon_lum_base",
                *detail_lines,
                "save moon_lum_sharp",
                # --- Stage 2: Chrominance Balancing & Saturation ---
                f"load {color_target}",
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_color_clean",
                *sat_lines,
                "save moon_color_sat",
                "exit",
            ]
        else:
            # Legacy monolithic RGB pipeline
            log("executing legacy monolithic RGB wavelet pipeline...")
            color_target = master.stem
            if (wb_mode == "gray-world" or wb.get("stretch_scale",1)>1) and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                f"load {color_target}",
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_unsharpened",
                f"load {color_target}",
                *deconv_lines,
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_base",
                *detail_lines,
                "save moon_sharp",
                "exit",
            ]

    else:
        # Monochrome pipeline
        log("executing monochrome lunar detail pipeline...")

        lines = [
            "requires 1.4.4",
            f"load {mono_target}",
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
            "save moon_unsharpened",
            f"load {mono_target}",
            *deconv_lines,
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
            "save moon_base",
            *detail_lines,
            "save moon_sharp",
            "exit",
        ]

    receipt = run_siril_script(args.siril, lines, work, logs_dir / "03_postprocess.log", args.timeout)
    if receipt["exit_code"] != 0:
        die(f"postprocessing failed (exit {receipt['exit_code']}); see {receipt['log']}")

    guard_mode = getattr(args, "sharp_guard", "auto")
    detail_stem = "moon_lum" if is_rgb and mineral_mode == "lrgb" else "moon"
    baseline = fits.getdata(work / "moon_unsharpened.fit", memmap=False).astype(np.float32)
    post_deconv = fits.getdata(work / f"{detail_stem}_base.fit", memmap=False).astype(np.float32)
    detail_support = None
    wavelet_only = None
    if wavelet_mode == "auto":
        wavelet_only, support, wavelet_receipt = _run_wavelet_adaptation(
            args, work, baseline, post_deconv, detail_stem, sharp_info["wavelet_coeffs"], px_scale, has_deconv)
        fits.writeto(work / "moon_wavelet_support.fit", support.astype(np.uint8), overwrite=True)
        if wavelet_receipt["status"] != "bypassed":
            detail_support = support
        wavelet_receipt["support_file"] = "moon_wavelet_support.fit"
        header = fits.getheader(work / f"{detail_stem}_base.fit")
        fits.writeto(work / "moon_wavelet.fit", wavelet_only, header=header, overwrite=True)
        if clahe_lines or unsharp_lines:
            finish_lines = ["load moon_wavelet", *clahe_lines, *unsharp_lines, f"save {detail_stem}_sharp", "exit"]
            finish = run_siril_script(args.siril, finish_lines, work, logs_dir / "03_wavelet_finish.log", args.timeout)
            if finish["exit_code"] != 0:
                die(f"wavelet contrast finishing failed; see {finish['log']}")
        else:
            fits.writeto(work / f"{detail_stem}_sharp.fit", wavelet_only, header=header, overwrite=True)
    else:
        (work / "moon_wavelet_support.fit").unlink(missing_ok=True)
        (work / "moon_wavelet.fit").unlink(missing_ok=True)
        wavelet_receipt = {"mode": "off", "status": "off", "support_coverage": None,
                           "requested_coefficients": sharp_info["wavelet_coeffs"]}
    receipt["wavelet_adaptation"] = wavelet_receipt
    log(f"wavelet adaptation: {wavelet_receipt['status']}, trusted pixels={wavelet_receipt.get('support_pixels', 'N/A')}")
    raw_candidate = fits.getdata(work / f"{detail_stem}_sharp.fit", memmap=False).astype(np.float32)
    ctx = _sharp_guard_context(baseline, px_scale, detail_support)
    candidate = raw_candidate
    strength = sharp_info["damping_strength"]
    damping_enabled = anti_ringing != "off" and strength > 0
    if guard_mode == "off" and is_rgb and mineral_mode == "legacy":
        damping_enabled = False
    if damping_enabled and np.all(np.isfinite(raw_candidate)):
        damping_base = baseline if guard_mode == "auto" else post_deconv
        base_lum = _guard_luminance(damping_base)
        raw_lum = _guard_luminance(raw_candidate)
        neg, pos = (ctx["neg"], ctx["pos"]) if guard_mode == "auto" else _compute_edge_ringing_damping_mask(
            base_lum, return_positive=True, px_scale=px_scale)
        damped_lum = _apply_anti_ringing_damping(
            base_lum, raw_lum, neg, strength=strength, pos_mask=pos,
            pos_strength=0.75, soft_knee=guard_mode == "off")
        if raw_candidate.ndim == 3:
            delta = raw_lum - base_lum
            factor = np.divide(damped_lum - base_lum, delta, out=np.ones_like(delta), where=np.abs(delta) > 1e-8)
            candidate = damping_base + factor * (raw_candidate - damping_base)
        else:
            candidate = damped_lum
        before = _sharp_guard_metrics(ctx, raw_candidate)["dhr"]
        after = _sharp_guard_metrics(ctx, candidate)["dhr"]
        receipt["anti_ringing"] = {"mode": anti_ringing, "strength": strength,
                                  "dhr_raw": before, "dhr_damped": after}

    if is_rgb and mineral_mode == "lrgb":
        clean_color = fits.getdata(work / "moon_color_clean.fit", memmap=False)
        def render_natural(lum):
            return _compose_natural_lrgb(lum, clean_color, wb.get("glare_meta"), px_scale)
    else:
        def render_natural(data):
            return np.clip(data, 0.0, 1.0).astype(np.float32)

    def render_for_guard(data):
        return np.rint(render_natural(data) * 65535).astype(np.float32) / 65535

    selected, guard = _select_guarded_sharpening(baseline, candidate, ctx, guard_mode, render_for_guard)
    guard["stages"] = {
        "deconvolution": _sharp_guard_metrics(ctx, post_deconv),
        "wavelet": (_sharp_guard_metrics(_sharp_guard_context(post_deconv, px_scale), wavelet_only if wavelet_only is not None else raw_candidate)
                    if np.all(np.isfinite(post_deconv)) else {"finite": False}),
        "combined": _sharp_guard_metrics(ctx, raw_candidate),
    }
    guard["deconvolution"] = {"executed": deconv_method, "retained_alpha": guard["alpha"]}
    guard["baseline_file"] = "moon_unsharpened.fit"
    guard["detail_file"] = f"{detail_stem}_sharp.fit"
    if detail_support is not None:
        guard["detail_support_file"] = "moon_wavelet_support.fit"
        guard["detail_support_sha256"] = hashlib.sha256(detail_support.astype(np.uint8).tobytes()).hexdigest()
    receipt["sharp_guard"] = guard
    log(f"sharp guard: {guard['status']}, retained enhancement={guard['alpha']:.0%}")
    if guard["status"] == "fallback":
        log(f"sharp guard fallback: {guard['attempts'][0]['reasons']}")
    sharp_header = fits.getheader(work / f"{detail_stem}_sharp.fit")
    sharp_header["SHRPALPH"] = guard["alpha"]
    sharp_header["SHRPGARD"] = guard["status"]
    fits.writeto(work / f"{detail_stem}_sharp.fit", selected, header=sharp_header, overwrite=True)
    fits.writeto(work / "moon_natural_baseline.fit", render_for_guard(baseline), header=hdr, overwrite=True)
    _write_moon_product(work, "moon_natural", render_natural(selected), sharp_header)

    mineral_style = "natural" if severe_clipping else getattr(args, "mineral_style", "deep-cine")
    fe_boost = float(getattr(args, "mineral_fe_boost", 4.5))
    ti_boost = float(getattr(args, "mineral_ti_boost", 5.5))
    gamma = float(getattr(args, "mineral_gamma", 1.00))
    if is_rgb and mineral_mode == "lrgb" and mineral_style == "deep-cine":
        c_meta = wb.get("glare_meta")
        if mosaic_mode == "tile":
            c_meta = dict(c_meta or {}, is_tile=True, active=False)
        mineral = _render_deep_cine_mineral(
            selected, wb.get("color_data", d), circle_meta=c_meta, fe_boost=fe_boost,
            ti_boost=ti_boost, gamma=gamma, px_scale=px_scale, return_float=True)
        _write_moon_product(work, "moon_mineral", mineral, sharp_header)
    elif is_rgb:
        if mineral_mode == "lrgb":
            finish_lines = ["requires 1.4.4", "rgbcomp -lum=moon_lum_sharp moon_color_sat -out=moon_mineral_master", "exit"]
        else:
            finish_lines = ["requires 1.4.4", "load moon_sharp", *sat_lines, "save moon_mineral_master", "exit"]
        finish = run_siril_script(args.siril, finish_lines, work, logs_dir / "03_color_finish.log", args.timeout)
        if finish["exit_code"] != 0:
            die(f"color finishing failed; see {finish['log']}")
        _write_moon_product(work, "moon_mineral", fits.getdata(work / "moon_mineral_master.fit", memmap=False), sharp_header)

    receipt["mosaic_mode"] = mosaic_mode
    receipt["deconvolution"] = {"requested":requested_deconv,"applied":deconv_method,"reason":"trusted_optics" if optics["trusted"] else "missing_optical_metadata"}
    receipt["source_saturation_rgb"] = saturation
    receipt["conservative_color"] = severe_clipping
    receipt["luminance_channel"] = getattr(args,"luminance_channel","weighted")

    # Global calibration profile (P2: Multi-panel stitching consistency)
    active_profile = {
        "version": "1.0",
        "source_master": str(master),
        "locked": bool(locked_profile or lock_wb or lock_stretch),
        "lock_source": lock_source,
        "histogram": {
            "bg_r": float(wb["bg_r"]) if is_rgb else float(bg_val),
            "bg_g": float(wb["bg_g"]) if is_rgb else float(bg_val),
            "bg_b": float(wb["bg_b"]) if is_rgb else float(bg_val),
            "bg_lum": float(wb["bg_lum"]) if is_rgb else float(bg_val),
            "hi_unified": float(wb["hi_lum"]) if is_rgb else float(hi_val),
            "hi_lum": float(wb["hi_lum"]) if is_rgb else float(hi_val),
            "midtone": float(mid_val),
        },
        "white_balance": {
            "mode": wb_mode if is_rgb else "mono",
            "k_r": float(wb["k_r"]) if is_rgb else 1.0,
            "k_b": float(wb["k_b"]) if is_rgb else 1.0,
            "ratio_r": float(wb.get("ratio_r", 1.0)) if is_rgb else 1.0,
            "ratio_b": float(wb.get("ratio_b", 1.0)) if is_rgb else 1.0,
        },
        "mineral": {
            "mineral_style": mineral_style,
            "fe_boost": fe_boost,
            "ti_boost": ti_boost,
            "gamma": gamma,
        },
        "optical_parameters": optics,
    }

    # Save lunar_profile.json
    profile_path = work / "lunar_profile.json"
    dump_json(profile_path, active_profile)

    export_path_str = getattr(args, "export_profile", None)
    if export_path_str:
        export_p = Path(export_path_str).expanduser().resolve()
        export_p.parent.mkdir(parents=True, exist_ok=True)
        dump_json(export_p, active_profile)
        log(f"calibration profile exported to {export_p}")
    else:
        log(f"calibration profile generated & saved to {profile_path}")

    # Export mosaic tile info for downstream siril-mosaic skill
    tile_info = {
        "mosaic_mode": mosaic_mode,
        "master": str(master),
        "shape": list(d.shape),
        "bitpix": int(hdr.get("BITPIX", 16)),
        "pixel_size": px_size,
        "focal_length": fl,
        "aperture": dia,
        "optical_inference": optics,
        "px_scale": px_scale,
        "drizzle": {
            "enabled": bool(px_scale != 1.0),
            "scale": px_scale,
            "pixfrac": hdr.get("DRZPIXFR"),
            "kernel": hdr.get("DRZKRENL"),
            "native_pixel_size": hdr.get("ORIGXPIX"),
        },
        "profile_locked": active_profile["locked"],
        "calibration_profile": active_profile,
        "products": {
            "natural_tif": str(work / "moon_natural.tif"),
            "natural_jpg": str(work / "moon_natural.jpg"),
            "mineral_jpg": str(work / "moon_mineral.jpg") if is_rgb and (work / "moon_mineral.jpg").exists() else None,
        },
    }
    # Visual finishing: Orientation rotation & 1:1 Square close-up export
    rotate_deg = int(getattr(args, "rotate", 0) or 0)
    tile_info["rotation_deg"] = rotate_deg
    square_crop = getattr(args, "square_crop", True)
    for name in ("moon_natural_square.tif", "moon_natural_square.jpg", "moon_mineral_square.jpg"):
        (work / name).unlink(missing_ok=True)
    if not is_rgb:
        for name in ("moon_mineral.jpg", "moon_mineral_master.fit"):
            (work / name).unlink(missing_ok=True)

    product_files = [
        work / "moon_natural.tif",
        work / "moon_natural.jpg",
        work / "moon_mineral.jpg",
    ]

    # 1. Apply orientation rotation if requested
    if rotate_deg in (90, 180, 270):
        rot_flag = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[rotate_deg]
        for pf in product_files:
            if pf.exists():
                img = cv2.imread(str(pf), cv2.IMREAD_UNCHANGED)
                if img is not None:
                    img_rot = cv2.rotate(img, rot_flag)
                    cv2.imwrite(str(pf), img_rot)
        log(f"orientation adjustment: rotated product images by {rotate_deg}° clockwise")

    # 2. Adaptive Lunar Sky Margins & Centering (Full-disc mode)
    auto_margin = getattr(args, "auto_margin", True)
    sky_margin_ratio = float(getattr(args, "sky_margin", 0.25) or 0.25)
    if auto_margin and mosaic_mode == "disc" and (work / "moon_natural.jpg").exists():
        nat_sample = cv2.imread(str(work / "moon_natural.jpg"), cv2.IMREAD_UNCHANGED)
        if nat_sample is not None:
            gray_s = cv2.cvtColor(nat_sample, cv2.COLOR_BGR2GRAY) if nat_sample.ndim == 3 else nat_sample
            lunar_fit = _fit_lunar_limb_circle(gray_s.astype(np.float32))
            if lunar_fit:
                _, margin_meta = _apply_adaptive_sky_margins(
                    nat_sample,
                    circle=lunar_fit,
                    sky_margin_ratio=sky_margin_ratio,
                    auto_center=True,
                    bg_val=0,
                )
                if margin_meta.get("applied") and any(p > 0 for p in margin_meta.get("padding", [])):
                    padding = margin_meta["padding"]
                    for pf in product_files:
                        if pf.exists():
                            img = cv2.imread(str(pf), cv2.IMREAD_UNCHANGED)
                            if img is not None:
                                img_padded = cv2.copyMakeBorder(img, *padding, cv2.BORDER_CONSTANT, value=0)
                                cv2.imwrite(str(pf), img_padded)
                    tile_info["adaptive_sky_margin"] = margin_meta
                    log(f"adaptive sky margin: expanded & centered to {margin_meta['new_shape'][1]}x{margin_meta['new_shape'][0]} "
                        f"(sky margin={sky_margin_ratio*100:.0f}%, padding={padding})")

    # 3. Export 1:1 Square Close-Up Master
    if square_crop and (work / "moon_natural.jpg").exists():
        nat_sample = cv2.imread(str(work / "moon_natural.jpg"), cv2.IMREAD_UNCHANGED)
        if nat_sample is not None:
            sh, sw = nat_sample.shape[:2]
            gray_s = cv2.cvtColor(nat_sample, cv2.COLOR_BGR2GRAY) if nat_sample.ndim == 3 else nat_sample
            count,labels,stats,centers = cv2.connectedComponentsWithStats((gray_s > 15).astype(np.uint8))
            component = int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))+1 if count>1 else 0
            if component and stats[component,cv2.CC_STAT_AREA] > 100:
                x0,y0,width,height,_ = map(int,stats[component])
                x1,y1 = x0+width,y0+height
                margin = max(width,height)
                ax,ay = max(0,x0-margin),max(0,y0-margin)
                bx,by = min(sw,x1+margin),min(sh,y1+margin)
                lunar_fit = _fit_lunar_limb_circle(gray_s[ay:by,ax:bx].astype(np.float32))
                if lunar_fit:
                    cx_fit,cy_fit,radius,_ = lunar_fit
                    cx_fit,cy_fit = cx_fit+ax,cy_fit+ay
                    x0,x1 = min(x0,int(np.floor(cx_fit-radius))),max(x1,int(np.ceil(cx_fit+radius)))
                    y0,y1 = min(y0,int(np.floor(cy_fit-radius))),max(y1,int(np.ceil(cy_fit+radius)))
                    side = int(np.ceil(2.0 * radius * (1.0 + 2.0 * sky_margin_ratio)))
                    cx, cy = int(np.round(cx_fit)), int(np.round(cy_fit))
                else:
                    side = int(np.ceil(max(x1-x0,y1-y0) * (1.0 + 2.0 * sky_margin_ratio)))
                    cx, cy = (x0+x1)//2, (y0+y1)//2
                side += side % 2
                left, top = cx-side//2, cy-side//2
                right, bottom = left+side, top+side
                padding = [max(0,-top),max(0,bottom-sh),max(0,-left),max(0,right-sw)]
                for pf in product_files:
                    if pf.exists():
                        image = cv2.imread(str(pf),cv2.IMREAD_UNCHANGED)
                        if image is not None:
                            padded = cv2.copyMakeBorder(image,*padding,cv2.BORDER_CONSTANT,value=0)
                            crop = padded[top+padding[0]:bottom+padding[0],left+padding[2]:right+padding[2]]
                            cv2.imwrite(str(work/(pf.stem+"_square"+pf.suffix)),crop)
                tile_info["square_crop"] = {"box":[left,top,right,bottom],"padding":padding, "side": side}
                log(f"square export: full visible lunar bounds preserved, side={side}, sky_margin={sky_margin_ratio*100:.0f}%, padding={padding}")


    dump_json(work / "mosaic_tile_info.json", tile_info)
    receipt["sharp_guard"]["delivery"] = _verify_sharp_guard(work, guard, tile_info, px_scale)
    dump_json(work / "postprocess_receipt.json", receipt)
    if receipt["sharp_guard"]["delivery"]["status"] == "failed":
        die(f"delivered TIFF failed sharp guard verification: {receipt['sharp_guard']['delivery']}")
    log(f"mosaic tile metadata exported to {work / 'mosaic_tile_info.json'}")
    log(f"postprocessing complete! Products in {work}:")
    log(f"  - Natural master TIFF: {work / 'moon_natural.tif'}")
    log(f"  - Natural JPG:         {work / 'moon_natural.jpg'}")
    if (work / "moon_mineral.jpg").exists():
        log(f"  - Mineral Moon JPG:    {work / 'moon_mineral.jpg'}")
    if (work / "moon_natural_square.jpg").exists():
        log(f"  - Natural Square JPG:  {work / 'moon_natural_square.jpg'}")
    if (work / "moon_mineral_square.jpg").exists():
        log(f"  - Mineral Square JPG:  {work / 'moon_mineral_square.jpg'}")

    # Generate interactive HTML dual-window comparison viewer
    if getattr(args, "html_report", True):
        _render_reference_frame(
            work=work,
            active_profile=active_profile,
            seq_name=getattr(args, "seq", "moon_"),
            rotate_deg=rotate_deg,
            square_crop=square_crop,
            tile_info=tile_info,
        )
        html_file = _generate_comparison_html(
            work=work,
            report=None,
            tile_info=tile_info,
            active_profile=active_profile,
        )
        log(f"  - Comparison viewer:   {html_file}")



# ------------------------------------------------------------------- 5. verify

def _verify_sharp_guard(work: Path, guard: dict, tile_info: dict, px_scale: float) -> dict:
    """Check the delivered TIFF, reversing display geometry before fixed-region QC."""
    import cv2
    from astropy.io import fits

    baseline_path = work / "moon_natural_baseline.fit"
    master_path = work / "moon_natural_master.fit"
    if not baseline_path.exists() or not master_path.exists() or not guard:
        return {"status": "unknown", "reason": "missing_full_baseline_or_receipt"}
    try:
        image = cv2.imread(str(work / "moon_natural.tif"), cv2.IMREAD_UNCHANGED)
        if image is None or image.dtype != np.uint16:
            return {"status": "failed", "reason": "missing_or_invalid_16bit_tiff"}
        padding = tile_info.get("adaptive_sky_margin", {}).get("padding", [0, 0, 0, 0])
        top, bottom, left, right = padding
        image = image[top:image.shape[0] - bottom, left:image.shape[1] - right]
        image = np.rot90(image, int(tile_info.get("rotation_deg", 0)) // 90)
        if image.ndim == 3:
            image = np.moveaxis(image[:, :, [2, 1, 0]], -1, 0)[:, ::-1, :]
        else:
            image = image[::-1, :]
        delivered = image.astype(np.float32) / 65535
        master = fits.getdata(master_path, memmap=False)
        if delivered.shape != master.shape or not np.all(np.isfinite(master)):
            return {"status": "failed", "reason": "invalid_float_master_or_geometry"}
        error = float(np.max(np.abs(delivered - master)))
        if error > 1.0 / 65535 + np.finfo(np.float32).eps:
            return {"status": "failed", "reason": "tiff_master_mismatch", "max_error": error}
        baseline = fits.getdata(baseline_path, memmap=False)
        ctx = _sharp_guard_context(baseline, px_scale, _load_wavelet_support(work, guard))
        metrics = _sharp_guard_metrics(ctx, delivered)
        reasons = _sharp_guard_reasons(ctx, metrics, require_gain=guard.get("alpha", 0) > 0)
        status = "off" if guard.get("mode") == "off" else "passed"
        if ctx["reason"]:
            status = "unavailable" if status != "off" else status
        elif reasons and status != "off":
            status = "failed"
        return {"status": status, "max_error": error, "metrics": metrics, "reasons": reasons,
                "source": "moon_natural.tif"}
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "failed", "reason": str(exc)}


def _background_metrics(data, sky_mask=None):
    """Channel-wise sky statistics; no claim when a sky region cannot be identified."""
    import cv2
    planes = data if data.ndim == 3 else data[None,...]
    green = planes[1] if len(planes) >= 3 else planes[0]
    height, width = green.shape
    side = min(64,max(1,min(height,width)//4))
    corners = [green[:side,:side],green[:side,-side:],green[-side:,:side],green[-side:,-side:]]
    if sky_mask is None:
        seed = min(corners,key=lambda patch:float(np.nanmedian(patch)))
    else:
        mask = np.asarray(sky_mask,dtype=bool) & np.isfinite(green)
        if np.count_nonzero(mask) < 64:
            return {"status":"unavailable: no reliable sky region","std_green":None,"mad_green":None,"relative_noise":None}
        seed = green[mask]
    level = float(np.nanmedian(seed))
    sigma = 1.4826*float(np.nanmedian(np.abs(seed-level)))
    peak = float(np.nanpercentile(green,99.5))
    if peak-level <= max(5*sigma,1e-6):
        return {"status":"unavailable: no reliable sky region","std_green":None,"mad_green":None,"relative_noise":None}
    if sky_mask is None:
        mask = np.isfinite(green) & (green <= level+max(4*sigma,1e-7))
        bright = (green > level+max(5*sigma,0.1*(peak-level))).astype(np.uint8)
        mask &= cv2.dilate(bright,np.ones((9,9),np.uint8)) == 0
        fit = _fit_lunar_limb_circle(green)
        if fit is None:
            count,labels,stats,centers = cv2.connectedComponentsWithStats(bright)
            if count>1:
                index = int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))+1
                x,y,bw,bh,area = stats[index]
                pad = max(bw,bh)
                ax,ay = max(0,x-pad),max(0,y-pad)
                bx,by = min(width,x+bw+pad),min(height,y+bh+pad)
                local_fit = _fit_lunar_limb_circle(green[ay:by,ax:bx])
                if local_fit:
                    fit = (local_fit[0]+ax,local_fit[1]+ay,*local_fit[2:])
        if fit:
            yy,xx = np.ogrid[:height,:width]
            mask &= (xx-fit[0])**2+(yy-fit[1])**2 > (fit[2]+8)**2
    if np.count_nonzero(mask) < 64:
        return {"status":"unavailable: no reliable sky region","std_green":None,"mad_green":None,"relative_noise":None}
    std, mad = [], []
    for plane in planes:
        values = plane[mask]
        std.append(float(np.std(values)))
        mad.append(float(1.4826*np.median(np.abs(values-np.median(values)))))
    gi = 1 if len(planes) >= 3 else 0
    moon = green[(green > level+max(5*sigma,0.2*(peak-level))) & np.isfinite(green)]
    signal = float(np.median(moon))-level if moon.size else 0
    return {"status":"measured","pixels":int(np.count_nonzero(mask)),"std_channels":std,"mad_channels":mad,
            "std_green":std[gi],"mad_green":mad[gi],"background_green":level,
            "relative_noise":mad[gi]/signal if signal>0 else None}


def _feedback_frame(path, reference, region):
    from astropy.io import fits
    import cv2
    data = fits.getdata(path).astype(np.float32)
    plane = data[1] if data.ndim == 3 else data
    h,w = reference.shape
    canvas = np.zeros((max(h,plane.shape[0]),max(w,plane.shape[1])),np.float32)
    template = np.zeros_like(canvas)
    canvas[:plane.shape[0],:plane.shape[1]] = plane
    template[:h,:w] = reference
    dx,dy,response = _subpixel_phase_correlation(template,canvas)
    if response < 0.15:
        return None
    transform = np.float32([[1,0,-round(dx)],[0,1,-round(dy)]])
    aligned = cv2.warpAffine(plane,transform,(w,h),flags=cv2.INTER_NEAREST,borderValue=float("nan"))
    x0,y0,x1,y1 = region
    common = aligned[y0:y1,x0:x1]
    if not common.size or not np.all(np.isfinite(common)):
        return None
    return common


def _feedback_accept(best, candidate):
    return (best["relative_noise"] > 0 and candidate["relative_noise"] <= best["relative_noise"]*0.95 and candidate["detail"] >= best["detail"]*0.97)


def _candidate_feedback(args):
    from astropy.io import fits
    import cv2
    work = Path(args.work).expanduser().resolve()
    original_limit = args.limit
    requested = min(256,original_limit) if original_limit>0 else 256
    history, best, stalled = [], None, 0
    reference = region = anchors = sky_mask = sky_circle = None
    snapshots = {}
    stopping = "candidate_limit"
    try:
        while True:
            args.limit = requested
            cmd_import(args)
            imported = load_json(work/"import_receipt.json")
            if not imported["is_video"] or not imported["video_metadata"]["primary_selected"]:
                die("feedback requires an adaptive, smart-top or smart-cluster video input")
            cmd_register(args)
            args.feedback_round = len(history)+1
            cmd_stack(args)
            stack = load_json(work/"stack_receipt.json")
            rank = load_json(work/"ranking.json")
            if reference is None:
                reference = _read_frame_plane(work/f"{args.seq}{rank['reference_index']:05d}.fit")
                reference /= max(float(np.percentile(reference,99.95)),1e-9)
                scale = float(stack["px_scale"])
                if scale != 1:
                    reference = cv2.resize(reference,None,fx=scale,fy=scale,interpolation=cv2.INTER_LINEAR)
                profile = load_json(work/"video_scores.json")
                centers = [r["tracking_shift"] for r in profile["rows"] if r["sharpness"] > 0]
                mx = int(np.ceil((max(c[0] for c in centers)-min(c[0] for c in centers))*scale))+4 if centers else 64
                my = int(np.ceil((max(c[1] for c in centers)-min(c[1] for c in centers))*scale))+4 if centers else 64
                region = [mx,my,reference.shape[1]-mx,reference.shape[0]-my]
                ax,ay,bx,by = region
                source_index = imported["video_metadata"]["source_mapping"][str(rank["reference_index"])]
                row = next(r for r in profile["rows"] if r["index"] == source_index)
                dx,dy = row["tracking_shift"]
                crop = imported["video_metadata"]["crop_box"] or [0,0]
                anchors = [[int(round((r[0]+dx-crop[0])*scale))-ax,int(round((r[1]+dy-crop[1])*scale))-ay,
                            int(round((r[2]+dx-crop[0])*scale))-ax,int(round((r[3]+dy-crop[1])*scale))-ay] for r in profile["anchors"]]
                anchors = [r for r in anchors if 0 <= r[0] < r[2] <= bx-ax and 0 <= r[1] < r[3] <= by-ay]
                if profile.get("circle"):
                    cx,cy,radius,_ = profile["circle"]
                    cx,cy = (cx+dx-crop[0])*scale-ax,(cy+dy-crop[1])*scale-ay
                    radius = radius*scale+8
                    yy,xx = np.ogrid[:by-ay,:bx-ax]
                    sky_mask = (xx-cx)**2+(yy-cy)**2 > radius**2
                    sky_circle = [cx,cy,radius]
            common = _feedback_frame(work/args.out,reference,region)
            metrics = _background_metrics(common,sky_mask) if common is not None else {"relative_noise":None}
            if common is not None and anchors and metrics.get("relative_noise") is not None:
                bg,noise = metrics["background_green"],max(metrics["mad_green"],1e-7)
                detail = np.median([_structure_quality(common[y0:y1,x0:x1],bg,noise,1.0) for x0,y0,x1,y1 in anchors])
                metrics["detail"] = float(detail)
            selected = imported["video_metadata"]["seeing_probe"]["chosen_frame_count"]
            record = {"round":len(history)+1,"requested":requested,"candidates":selected,"actually_stacked":stack["stacked_frames"], "registered_frames":rank["registered_frames"],
                      "metrics":metrics,"disk_budget":stack["disk_budget"],"accepted":False,"log":stack["log"]}
            valid_metrics = metrics.get("relative_noise") is not None and np.isfinite(metrics["relative_noise"]) and metrics.get("detail",0)>0
            detail_safe = valid_metrics and (not history or metrics["detail"] >= history[0]["metrics"]["detail"]*0.97)
            accepted = best is None or (detail_safe and _feedback_accept(best["metrics"],metrics))
            record["accepted"] = accepted
            stalled = 0 if accepted else stalled+1
            if best is None or (detail_safe and metrics["relative_noise"] < best["metrics"]["relative_noise"]):
                best = record
                shutil.copy2(work/args.out,work/"feedback_best.fit")
                snapshots = {name:(work/name).read_bytes() for name in (f"{args.seq}.seq","ranking.json","stack_receipt.json","import_receipt.json")}
            history.append(record)
            ceiling = min(original_limit,imported["video_metadata"]["candidate_pool_count"]) if original_limit>0 else imported["video_metadata"]["candidate_pool_count"]
            if not valid_metrics:
                stopping = "unavailable_quality_metrics";break
            if stalled >= 2:
                stopping = "two_rounds_without_improvement";break
            if selected < min(requested,ceiling):
                stopping = "disk_budget";break
            if requested >= ceiling:
                stopping = "candidate_limit";break
            requested = min(requested*2,ceiling)
        latest_import = load_json(work/"import_receipt.json")
        for name,contents in snapshots.items():
            (work/name).write_bytes(contents)
        restored = load_json(work/"import_receipt.json")
        restored["frame_count"] = latest_import["frame_count"]
        for key in ("source_mapping","source_files","extracted_frames"):
            restored["video_metadata"][key] = latest_import["video_metadata"][key]
        dump_json(work/"import_receipt.json",restored)
        shutil.copy2(work/"feedback_best.fit",work/args.out)
        dump_json(work/"candidate_feedback.json",{"rounds":history,"winner":best["round"],"stop_reason":stopping,"common_region":region,"sky_circle":sky_circle,"sky_pixels":int(np.count_nonzero(sky_mask)) if sky_mask is not None else None})
        log(f"candidate feedback: selected round {best['round']}, {best['actually_stacked']} actual frames; stopped={stopping}")
    finally:
        args.limit = original_limit
        if hasattr(args,"feedback_round"):
            del args.feedback_round




def _apply_adaptive_sky_margins(
    image: np.ndarray,
    circle: tuple | list | None = None,
    sky_margin_ratio: float = 0.25,
    auto_center: bool = True,
    bg_val: float | int = 0,
) -> tuple[np.ndarray, dict]:
    """Expand sky boundaries and center the lunar disc with guaranteed sky margin ratio.

    Adheres strictly to the authentic zero-distortion rule:
    - Never rescales or interpolates lunar pixels (100% native resolution preserved).
    - Expands bounding canvas using clean background padding.
    - Symmetrically centers the lunar disc so that margins on all 4 sides are >= radius * sky_margin_ratio.
    """
    import cv2
    import numpy as np

    h, w = image.shape[:2]
    meta = {
        "applied": False,
        "sky_margin_ratio": float(sky_margin_ratio),
        "original_shape": [int(h), int(w)],
        "new_shape": [int(h), int(w)],
        "padding": [0, 0, 0, 0],
    }

    if circle is None:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        gray_f = gray.astype(np.float32) if (gray.dtype != np.float32 and gray.dtype != np.float64) else gray.copy()
        circle = _fit_lunar_limb_circle(gray_f)

    if not circle:
        meta["reason"] = "no reliable lunar limb circle fitted"
        return image, meta

    cx, cy, radius = float(circle[0]), float(circle[1]), float(circle[2])
    if radius <= 10.0 or not np.isfinite(radius):
        meta["reason"] = "invalid radius"
        return image, meta

    req_margin = int(np.ceil(radius * sky_margin_ratio))

    if auto_center:
        half_w = max(cx, w - cx, radius + req_margin)
        half_h = max(cy, h - cy, radius + req_margin)

        new_w = int(np.ceil(2.0 * half_w))
        new_h = int(np.ceil(2.0 * half_h))
        new_w += new_w % 2
        new_h += new_h % 2

        pad_left = int(np.round(new_w / 2.0 - cx))
        pad_right = new_w - (pad_left + w)
        pad_top = int(np.round(new_h / 2.0 - cy))
        pad_bottom = new_h - (pad_top + h)
    else:
        pad_left = max(0, int(np.ceil(req_margin - (cx - radius))))
        pad_right = max(0, int(np.ceil(req_margin - (w - (cx + radius)))))
        pad_top = max(0, int(np.ceil(req_margin - (cy - radius))))
        pad_bottom = max(0, int(np.ceil(req_margin - (h - (cy + radius)))))
        new_w = w + pad_left + pad_right
        new_h = h + pad_top + pad_bottom

    pad_left = max(0, pad_left)
    pad_right = max(0, pad_right)
    pad_top = max(0, pad_top)
    pad_bottom = max(0, pad_bottom)

    if pad_left == 0 and pad_right == 0 and pad_top == 0 and pad_bottom == 0:
        meta["applied"] = True
        meta["reason"] = "margins already satisfied"
        meta["circle"] = [cx, cy, radius]
        meta["new_center"] = [cx, cy]
        return image, meta

    padding = [pad_top, pad_bottom, pad_left, pad_right]
    padded = cv2.copyMakeBorder(image, *padding, cv2.BORDER_CONSTANT, value=bg_val)

    meta["applied"] = True
    meta["circle"] = [cx, cy, radius]
    meta["new_shape"] = [padded.shape[0], padded.shape[1]]
    meta["new_center"] = [cx + pad_left, cy + pad_top]
    meta["padding"] = padding
    meta["req_margin"] = req_margin
    return padded, meta


def _render_reference_frame(
    work: Path,
    active_profile: dict | None = None,
    seq_name: str = "moon_",
    rotate_deg: int = 0,
    square_crop: bool = False,
    tile_info: dict | None = None,
) -> Path | None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(work).expanduser().resolve()
    out_ref = work / "moon_reference.jpg"
    out_ref_square = work / "moon_reference_square.jpg"
    out_raw_orig = work / "moon_raw_original.jpg"

    # Locate ranking.json
    rank_json = work / "ranking.json"
    ref_idx = None
    if rank_json.exists():
        try:
            r_info = load_json(rank_json)
            ref_idx = r_info.get("reference_index")
        except Exception:
            pass

    # Prioritize true unresampled, uncropped native sequence frames
    # (Avoid r_*.fit or cropped reference.fit produced by seqapplyreg -framing=min)
    candidates = []
    if ref_idx is not None:
        candidates.append(work / f"{seq_name}{ref_idx:05d}.fit")
        candidates.append(work / f"{seq_name}{ref_idx:04d}.fit")
        candidates.append(work / f"{seq_name}{ref_idx}.fit")

    # Look for any uncropped sequence FITS frame in work directory
    for f in sorted(work.glob(f"{seq_name}*.fit")):
        name_lower = f.name.lower()
        if not any(k in name_lower for k in ("master", "lum", "base", "sharp", "color", "clean", "sat", "feedback", "reference", "r_")):
            candidates.append(f)

    # Secondary fallback to intermediate or resampled reference frames
    candidates.extend([
        work / f"{seq_name}reference.fit",
        work / "moon_reference.fit",
    ])
    if ref_idx is not None:
        candidates.append(work / f"r_{seq_name}{ref_idx:05d}.fit")
        candidates.append(work / f"r_{seq_name}{ref_idx:04d}.fit")

    target_fit = None
    for cand in candidates:
        if cand.exists():
            target_fit = cand
            break

    if target_fit is None or not target_fit.exists():
        return out_ref if out_ref.exists() else None

    try:
        with fits.open(target_fit, memmap=False) as hdul:
            data = hdul[0].data.astype(np.float32)
    except Exception as exc:
        log(f"reference frame render: failed to open {target_fit.name}: {exc}")
        return out_ref if out_ref.exists() else None

    # Handle NaNs or Infs
    data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=0.0)

    # Normalize to [0.0, 1.0]
    d_max = float(np.max(data))
    if d_max > 1.5:
        norm_factor = 65535.0 if d_max > 255.0 else 255.0
        data = data / norm_factor
    data = np.clip(data, 0.0, 1.0)

    profile = active_profile or {}
    if not profile and (work / "lunar_profile.json").exists():
        try:
            profile = load_json(work / "lunar_profile.json")
        except Exception:
            profile = {}

    hist = profile.get("histogram", {})
    wb = profile.get("white_balance", {})
    mid_val = float(hist.get("midtone", 0.13))
    mid_val = max(0.01, min(0.99, mid_val))

    def _mtf_stretch(x, m):
        denom = (2.0 * m - 1.0) * x - m
        out = np.zeros_like(x)
        mask = np.abs(denom) > 1e-7
        out[mask] = (m - 1.0) * x[mask] / denom[mask]
        return np.clip(out, 0.0, 1.0)

    is_rgb = (data.ndim == 3 and data.shape[0] >= 3)
    if is_rgb:
        bg_r_prof = float(hist.get("bg_r", 0.0))
        bg_g_prof = float(hist.get("bg_g", 0.0))
        bg_b_prof = float(hist.get("bg_b", 0.0))
        bg_r = bg_r_prof if bg_r_prof > 0.0 else _estimate_pedestal(data[0])
        bg_g = bg_g_prof if bg_g_prof > 0.0 else _estimate_pedestal(data[1])
        bg_b = bg_b_prof if bg_b_prof > 0.0 else _estimate_pedestal(data[2])

        r_net = np.maximum(0.0, data[0] - bg_r)
        g_net = np.maximum(0.0, data[1] - bg_g)
        b_net = np.maximum(0.0, data[2] - bg_b)

        # Suppress limb glare outside disk to match product sky baseline
        try:
            net_planes = np.stack([r_net, g_net, b_net], axis=0)
            net_planes, _ = _suppress_lunar_limb_glare(net_planes, glare_mode="soft")
            r_net, g_net, b_net = net_planes[0], net_planes[1], net_planes[2]
        except Exception:
            pass

        hi_lum = float(hist.get("hi_lum", 1.0))
        k_r = float(wb.get("k_r", 1.0))
        k_b = float(wb.get("k_b", 1.0))

        span_r = max(hi_lum - bg_r_prof, 1e-6)
        span_g = max(hi_lum - bg_g_prof, 1e-6)
        span_b = max(hi_lum - bg_b_prof, 1e-6)

        r_lin = np.clip(r_net / span_r * k_r, 0.0, 1.0)
        g_lin = np.clip(g_net / span_g, 0.0, 1.0)
        b_lin = np.clip(b_net / span_b * k_b, 0.0, 1.0)

        r_str = _mtf_stretch(r_lin, mid_val)
        g_str = _mtf_stretch(g_lin, mid_val)
        b_str = _mtf_stretch(b_lin, mid_val)

        # FITS coordinates vertical flip to match display
        r_str = r_str[::-1, :]
        g_str = g_str[::-1, :]
        b_str = b_str[::-1, :]

        bgr = (np.stack([b_str, g_str, r_str], axis=-1) * 255.0).astype(np.uint8)
    else:
        plane = data if data.ndim == 2 else data[0]
        bg_lum_prof = float(hist.get("bg_lum", hist.get("bg_g", 0.0)))
        bg_lum = bg_lum_prof if bg_lum_prof > 0.0 else _estimate_pedestal(plane)
        hi_lum = float(hist.get("hi_lum", 1.0))
        span = max(hi_lum - bg_lum_prof, 1e-6)
        p_net = np.maximum(0.0, plane - bg_lum)
        gray_lin = np.clip(p_net / span, 0.0, 1.0)
        gray_str = _mtf_stretch(gray_lin, mid_val)
        gray_str = gray_str[::-1, :]
        bgr = (gray_str * 255.0).astype(np.uint8)

    # Render True Camera RAW view (native sensor FOV, unaligned, unpadded, no limb glare suppression, no pedestal subtraction)
    try:
        hi_lum_raw = float(hist.get("hi_lum", 1.0))
        span_raw = max(hi_lum_raw, 1e-6)
        if is_rgb:
            k_r_val = float(wb.get("k_r", 1.0))
            k_b_val = float(wb.get("k_b", 1.0))
            r_raw_lin = np.clip(data[0] / span_raw * k_r_val, 0.0, 1.0)
            g_raw_lin = np.clip(data[1] / span_raw, 0.0, 1.0)
            b_raw_lin = np.clip(data[2] / span_raw * k_b_val, 0.0, 1.0)
            r_raw_str = _mtf_stretch(r_raw_lin, mid_val)[::-1, :]
            g_raw_str = _mtf_stretch(g_raw_lin, mid_val)[::-1, :]
            b_raw_str = _mtf_stretch(b_raw_lin, mid_val)[::-1, :]
            raw_orig_bgr = (np.stack([b_raw_str, g_raw_str, r_raw_str], axis=-1) * 255.0).astype(np.uint8)
        else:
            plane_raw = data if data.ndim == 2 else data[0]
            gray_raw_lin = np.clip(plane_raw / span_raw, 0.0, 1.0)
            gray_raw_str = _mtf_stretch(gray_raw_lin, mid_val)[::-1, :]
            raw_orig_bgr = (gray_raw_str * 255.0).astype(np.uint8)

        if rotate_deg in (90, 180, 270):
            rot_flag_raw = {
                90: cv2.ROTATE_90_CLOCKWISE,
                180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE,
            }[rotate_deg]
            raw_orig_bgr = cv2.rotate(raw_orig_bgr, rot_flag_raw)

        cv2.imwrite(str(out_raw_orig), raw_orig_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        log(f"original raw frame rendered: {out_raw_orig.name} (native {raw_orig_bgr.shape[1]}x{raw_orig_bgr.shape[0]}, unpadded, unaligned)")
    except Exception as exc:
        log(f"original raw frame render warning: {exc}")

    # Orientation rotation if requested
    if rotate_deg in (90, 180, 270):
        rot_flag = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[rotate_deg]
        bgr = cv2.rotate(bgr, rot_flag)

    # Rigid 1:1 sub-pixel coordinate alignment to finished product (moon_natural.jpg)
    target_img_path = work / "moon_natural.jpg"
    aligned_to_product = False
    if target_img_path.exists():
        try:
            target_img = cv2.imread(str(target_img_path), cv2.IMREAD_UNCHANGED)
            if target_img is not None:
                tgt_h, tgt_w = target_img.shape[:2]
                gray_tgt = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY) if target_img.ndim == 3 else target_img
                gray_ref = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr

                c_tgt = _fit_lunar_limb_circle(gray_tgt.astype(np.float32))
                c_ref = _fit_lunar_limb_circle(gray_ref.astype(np.float32))

                cur_h, cur_w = bgr.shape[:2]
                if c_tgt and c_ref:
                    dx0 = float(c_tgt[0] - c_ref[0])
                    dy0 = float(c_tgt[1] - c_ref[1])
                else:
                    dx0 = float((tgt_w - cur_w) / 2.0)
                    dy0 = float((tgt_h - cur_h) / 2.0)

                M0 = np.array([[1.0, 0.0, dx0], [0.0, 1.0, dy0]], dtype=np.float32)
                ref_coarse = cv2.warpAffine(bgr, M0, (tgt_w, tgt_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

                gray_coarse = cv2.cvtColor(ref_coarse, cv2.COLOR_BGR2GRAY) if ref_coarse.ndim == 3 else ref_coarse
                if c_tgt:
                    cxi, cyi = int(round(c_tgt[0])), int(round(c_tgt[1]))
                    box_r = int(min(c_tgt[2] * 0.5, cxi, cyi, tgt_w - 1 - cxi, tgt_h - 1 - cyi))
                else:
                    cxi, cyi = tgt_w // 2, tgt_h // 2
                    box_r = min(tgt_w, tgt_h) // 4
                box_r = max(32, box_r)

                tgt_roi = gray_tgt[cyi - box_r : cyi + box_r, cxi - box_r : cxi + box_r].astype(np.float32)
                ref_roi = gray_coarse[cyi - box_r : cyi + box_r, cxi - box_r : cxi + box_r].astype(np.float32)

                dx_fine, dy_fine = 0.0, 0.0
                if tgt_roi.shape == ref_roi.shape and tgt_roi.shape[0] >= 32 and tgt_roi.shape[1] >= 32:
                    hann = cv2.createHanningWindow((tgt_roi.shape[1], tgt_roi.shape[0]), cv2.CV_32F)
                    (f_dx, f_dy), resp = cv2.phaseCorrelate(tgt_roi, ref_roi, hann)
                    if resp > 0.08 and abs(f_dx) < 64 and abs(f_dy) < 64:
                        dx_fine, dy_fine = f_dx, f_dy

                total_dx = dx0 - dx_fine
                total_dy = dy0 - dy_fine
                M_final = np.array([[1.0, 0.0, total_dx], [0.0, 1.0, total_dy]], dtype=np.float32)
                bgr = cv2.warpAffine(bgr, M_final, (tgt_w, tgt_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                aligned_to_product = True
                log(f"reference frame aligned to product: offset=({total_dx:+.1f}, {total_dy:+.1f}) px, canvas={tgt_w}x{tgt_h}")
        except Exception as exc:
            log(f"reference frame product alignment note: {exc}")

    if not aligned_to_product:
        # Harmonize unpadded dimensions with moon_master.fit if master exists
        master_fit = work / "moon_master.fit"
        if master_fit.exists():
            try:
                with fits.open(master_fit, memmap=False) as mhdul:
                    m_h = int(mhdul[0].header.get("NAXIS2", 0))
                    m_w = int(mhdul[0].header.get("NAXIS1", 0))
                if rotate_deg in (90, 270):
                    m_h, m_w = m_w, m_h
                if m_h > 0 and m_w > 0:
                    cur_h, cur_w = bgr.shape[:2]
                    if cur_h != m_h or cur_w != m_w:
                        # Align dimensions to match master grid exactly
                        if cur_h > m_h or cur_w > m_w:
                            top = max(0, (cur_h - m_h) // 2)
                            left = max(0, (cur_w - m_w) // 2)
                            bgr = bgr[top:top + m_h, left:left + m_w]
                        cur_h, cur_w = bgr.shape[:2]
                        if cur_h < m_h or cur_w < m_w:
                            pad_top = max(0, (m_h - cur_h) // 2)
                            pad_bot = max(0, m_h - cur_h - pad_top)
                            pad_left = max(0, (m_w - cur_w) // 2)
                            pad_right = max(0, m_w - cur_w - pad_left)
                            bgr = cv2.copyMakeBorder(bgr, pad_top, pad_bot, pad_left, pad_right, cv2.BORDER_CONSTANT, value=0)
            except Exception as exc:
                log(f"reference frame dimension alignment note: {exc}")

        # Apply adaptive sky padding if recorded in tile_info
        adaptive_margin = (tile_info or {}).get("adaptive_sky_margin")
        if not adaptive_margin and (work / "mosaic_tile_info.json").exists():
            try:
                adaptive_margin = load_json(work / "mosaic_tile_info.json").get("adaptive_sky_margin")
            except Exception:
                adaptive_margin = None
        if adaptive_margin and adaptive_margin.get("applied"):
            pad = adaptive_margin.get("padding")
            if pad and any(p > 0 for p in pad):
                bgr = cv2.copyMakeBorder(bgr, *pad, cv2.BORDER_CONSTANT, value=0)

    cv2.imwrite(str(out_ref), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    log(f"reference frame rendered: {out_ref.name} (frame #{ref_idx or 'base'}, isometric MTF applied)")


    # Also render square version if tile_info has square_crop info
    if square_crop:
        sq_info = (tile_info or {}).get("square_crop")
        if not sq_info and (work / "mosaic_tile_info.json").exists():
            try:
                sq_info = load_json(work / "mosaic_tile_info.json").get("square_crop")
            except Exception:
                sq_info = None
        if sq_info and "padding" in sq_info and "box" in sq_info:
            try:
                padding = sq_info["padding"]
                left, top, right, bottom = sq_info["box"]
                padded = cv2.copyMakeBorder(bgr, *padding, cv2.BORDER_CONSTANT, value=0)
                crop = padded[top + padding[0]:bottom + padding[0], left + padding[2]:right + padding[2]]
                cv2.imwrite(str(out_ref_square), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                log(f"reference square frame rendered: {out_ref_square.name}")
            except Exception as exc:
                log(f"reference square crop warning: {exc}")

    return out_ref


def _generate_comparison_html(
    work: Path,
    report: dict | None = None,
    tile_info: dict | None = None,
    active_profile: dict | None = None,
) -> Path:
    work = Path(work).expanduser().resolve()
    out_html = work / "comparison.html"

    # 1. Gather product images
    manifest = {
        "reference": "moon_reference.jpg" if (work / "moon_reference.jpg").exists() else None,
        "referenceSquare": "moon_reference_square.jpg" if (work / "moon_reference_square.jpg").exists() else None,
        "rawOriginal": "moon_raw_original.jpg" if (work / "moon_raw_original.jpg").exists() else None,
        "targets": [],
    }

    candidates = [
        ("natural", "自然写实母版 (Natural Master)", "moon_natural.jpg", False),
        ("mineral", "电影级地质彩月 (Deep-Cine Mineral)", "moon_mineral.jpg", False),
        ("natural_square", "1:1 自然方形特写 (Natural Square)", "moon_natural_square.jpg", True),
        ("mineral_square", "1:1 彩月方形特写 (Mineral Square)", "moon_mineral_square.jpg", True),
    ]

    for tid, tname, tfile, is_sq in candidates:
        if (work / tfile).exists():
            manifest["targets"].append({
                "id": tid,
                "name": tname,
                "file": tfile,
                "square": is_sq,
            })

    if not manifest["targets"]:
        # Fallback to any existing jpg
        for jpg in sorted(work.glob("*.jpg")):
            if jpg.name not in ("moon_reference.jpg", "moon_reference_square.jpg", "moon_raw_original.jpg"):
                manifest["targets"].append({
                    "id": jpg.stem,
                    "name": jpg.name,
                    "file": jpg.name,
                    "square": "square" in jpg.name,
                })

    # 2. Gather HUD metrics
    hud_data = {}
    rep = report or {}
    if not rep and (work / "verify_report.json").exists():
        try:
            rep = load_json(work / "verify_report.json")
        except Exception:
            rep = {}

    ti = tile_info or {}
    if not ti and (work / "mosaic_tile_info.json").exists():
        try:
            ti = load_json(work / "mosaic_tile_info.json")
        except Exception:
            ti = {}

    prof = active_profile or {}
    if not prof and (work / "lunar_profile.json").exists():
        try:
            prof = load_json(work / "lunar_profile.json")
        except Exception:
            prof = {}

    post_rec = load_json(work / "postprocess_receipt.json") if (work / "postprocess_receipt.json").exists() else {}
    guard = (report or {}).get("sharp_guard") or post_rec.get("sharp_guard") or {"status": "unknown", "alpha": None}
    adaptation = (report or {}).get("wavelet_adaptation") or post_rec.get("wavelet_adaptation") or {"status": "unknown"}
    guard_metrics = guard.get("verified_selected") or guard.get("selected") or {}
    stack_rec = {}
    if (work / "stack_receipt.json").exists():
        try:
            stack_rec = load_json(work / "stack_receipt.json")
        except Exception:
            stack_rec = {}

    rank_rec = {}
    if (work / "ranking.json").exists():
        try:
            rank_rec = load_json(work / "ranking.json")
        except Exception:
            rank_rec = {}

    dev = rep.get("device") or {}
    fl_val = rep.get("focal_length") or ti.get("focal_length")
    ap_val = rep.get("aperture") or ti.get("aperture")
    px_val = rep.get("pixel_size") or ti.get("pixel_size")
    f_ratio = round(float(fl_val) / float(ap_val), 1) if fl_val and ap_val else None
    airy_r = round((1.22 * 0.55 * float(fl_val)) / (float(ap_val) * float(px_val)), 2) if fl_val and ap_val and px_val else None

    drz = rep.get("drizzle") or ti.get("drizzle") or stack_rec.get("drizzle") or {}
    stk_frames = rep.get("stacked_frames") or stack_rec.get("stacked_frames")
    reg_frames = rep.get("registered_frames") or rank_rec.get("registered_frames")
    tot_frames = rep.get("candidate_frames") or rank_rec.get("candidate_frames") or rank_rec.get("total_frames")

    hud_data = {
        "device": {
            "name": dev.get("id") or "Auto-detected",
            "source": dev.get("match_source") or "FITS Header",
            "confidence": dev.get("confidence") or "N/A",
        },
        "optics": {
            "focal_length": float(fl_val) if fl_val else None,
            "aperture": float(ap_val) if ap_val else None,
            "pixel_size": float(px_val) if px_val else None,
            "f_ratio": f_ratio,
            "airy_r": airy_r,
        },
        "stacking": {
            "stacked_frames": stk_frames,
            "registered_frames": reg_frames,
            "total_frames": tot_frames,
            "ratio": f"{(stk_frames / tot_frames * 100):.1f}%" if stk_frames and tot_frames else None,
            "method": stack_rec.get("stack_method", "Winsorized Rej Mean"),
            "weight": stack_rec.get("weight", "noise"),
        },
        "drizzle": {
            "enabled": bool(drz.get("enabled")),
            "scale": drz.get("scale", 1.0),
            "kernel": drz.get("kernel", "square"),
            "pixfrac": drz.get("pixfrac", 1.0),
        },
        "quality": {
            "sharp_guard": {"status": guard.get("status"), "alpha": guard.get("alpha")},
            "wavelet_adaptation": {"status": adaptation.get("status"), "support_coverage": adaptation.get("support_coverage")},
            "dhr": guard_metrics.get("dhr", rep.get("dark_halo_ratio")),
            "dhr_status": rep.get("dark_halo_status"),
            "csi": guard_metrics.get("csi", rep.get("chalky_saturation_index")),
            "csi_status": rep.get("chalky_status"),
            "gkm": guard_metrics.get("gkm", rep.get("gradient_kurtosis")),
            "gkm_status": rep.get("brittleness_status"),
            "bg_noise_std": rep.get("background_noise_std"),
            "bg_noise_status": rep.get("background_noise_status"),
        },
        "color": {
            "wb_kr": prof.get("white_balance", {}).get("k_r"),
            "wb_kb": prof.get("white_balance", {}).get("k_b"),
            "midtone": prof.get("histogram", {}).get("midtone"),
            "mineral_style": prof.get("mineral", {}).get("mineral_style"),
            "fe_boost": prof.get("mineral", {}).get("fe_boost"),
            "ti_boost": prof.get("mineral", {}).get("ti_boost"),
        },
    }

    import json
    manifest_json = json.dumps(manifest)
    hud_json = json.dumps(hud_data)

    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Siril Moon Stacking · Dual-Window Linked Inspector</title>
  <style>
    :root {
      --bg-dark: #07090e;
      --bg-surface: #0f1523;
      --bg-panel: #141c2e;
      --border-color: #1e293b;
      --border-highlight: #334155;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --accent-cyan: #38bdf8;
      --accent-blue: #2563eb;
      --accent-amber: #f59e0b;
      --accent-emerald: #10b981;
      --accent-rose: #f43f5e;
      --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-dark);
      color: var(--text-main);
      font-family: var(--font-family);
      overflow: hidden;
      height: 100vh;
      display: flex;
      flex-direction: column;
      user-select: none;
    }

    /* Top Navigation Toolbar */
    .toolbar {
      height: 52px;
      background: rgba(15, 23, 42, 0.9);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 16px;
      z-index: 100;
    }
    .brand-section {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-logo { font-size: 1.3rem; }
    .brand-title {
      font-size: 0.95rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: var(--text-main);
    }
    .badge-sub {
      background: rgba(56, 189, 248, 0.12);
      color: var(--accent-cyan);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 2px 7px;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .controls-center {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .btn-group {
      display: flex;
      background: var(--bg-panel);
      padding: 3px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
    }
    .btn-group button {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 5px 11px;
      font-size: 0.8rem;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.15s ease;
    }
    .btn-group button:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.05); }
    .btn-group button.active {
      color: #fff;
      background: var(--accent-blue);
      box-shadow: 0 1px 4px rgba(37, 99, 235, 0.35);
    }

    .tool-btn {
      background: var(--bg-panel);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 4px;
      transition: all 0.15s ease;
    }
    .tool-btn:hover { background: var(--border-highlight); border-color: var(--accent-cyan); }
    .zoom-text {
      font-size: 0.8rem;
      color: var(--accent-cyan);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      min-width: 44px;
      text-align: center;
    }

    .controls-right {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .target-select {
      background: var(--bg-panel);
      color: var(--text-main);
      border: 1px solid var(--border-color);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 500;
      outline: none;
      cursor: pointer;
    }
    .target-select:focus { border-color: var(--accent-cyan); }
    .hud-toggle-btn {
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: var(--accent-emerald);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.15s ease;
    }
    .hud-toggle-btn.active {
      background: var(--accent-emerald);
      color: #07090e;
    }

    /* Main Viewport Workspace */
    .workspace {
      flex: 1;
      position: relative;
      background: #000;
      overflow: hidden;
      display: flex;
    }

    /* Mode: Side by Side */
    .side-by-side-layout {
      display: flex;
      width: 100%;
      height: 100%;
    }
    .side-panel {
      flex: 1;
      height: 100%;
      position: relative;
      overflow: hidden;
      border-right: 1px solid #1e293b;
    }
    .side-panel:last-child { border-right: none; }
    .viewport {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      cursor: grab;
      overflow: hidden;
    }
    .viewport:active { cursor: grabbing; }

    .canvas-layer {
      position: absolute;
      top: 0;
      left: 0;
      transform-origin: 0 0;
      will-change: transform;
    }
    .canvas-layer img {
      display: block;
      pointer-events: none;
      max-width: none;
      max-height: none;
    }

    /* Floating Panel Badges */
    .panel-pill {
      position: absolute;
      top: 14px;
      left: 14px;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      padding: 6px 12px;
      border-radius: 8px;
      pointer-events: none;
      z-index: 10;
      display: flex;
      flex-direction: column;
      gap: 2px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    }
    .pill-title {
      font-size: 0.82rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .ref-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-amber); display: inline-block; }
    .ref-dot.raw-dot { background: #f97316; box-shadow: 0 0 6px rgba(249, 115, 22, 0.8); }
    .master-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-cyan); display: inline-block; }
    .pill-meta { font-size: 0.7rem; color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, monospace; }

    /* Crosshairs */
    .crosshair-h, .crosshair-v {
      position: absolute;
      pointer-events: none;
      z-index: 20;
      display: none;
    }
    .crosshair-h {
      left: 0;
      width: 100%;
      height: 1px;
      background: rgba(56, 189, 248, 0.5);
      box-shadow: 0 0 4px rgba(56, 189, 248, 0.8);
    }
    .crosshair-v {
      top: 0;
      height: 100%;
      width: 1px;
      background: rgba(56, 189, 248, 0.5);
      box-shadow: 0 0 4px rgba(56, 189, 248, 0.8);
    }

    /* Mode: Split Slider Layout */
    .split-layout {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      display: none;
    }
    .split-container {
      width: 100%;
      height: 100%;
      position: relative;
      overflow: hidden;
      cursor: grab;
    }
    .split-container:active { cursor: grabbing; }
    .split-clip-layer {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      pointer-events: none;
    }
    .split-divider-line {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 2px;
      background: #38bdf8;
      box-shadow: 0 0 10px rgba(56, 189, 248, 0.8);
      z-index: 30;
      cursor: ew-resize;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .split-divider-handle {
      width: 38px;
      height: 38px;
      border-radius: 50%;
      background: #0f172a;
      border: 2px solid #38bdf8;
      color: #38bdf8;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.75rem;
      font-weight: 700;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.6);
      user-select: none;
      letter-spacing: -2px;
    }

    /* Mode: Blink Toggle Layout */
    .blink-layout {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      display: none;
    }
    .blink-indicator {
      position: absolute;
      bottom: 45px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(15, 23, 42, 0.88);
      backdrop-filter: blur(10px);
      border: 1px solid var(--border-color);
      padding: 8px 18px;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
      z-index: 40;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
    }

    /* HUD Metrics Drawer */
    .hud-panel {
      position: absolute;
      bottom: 38px;
      right: 18px;
      width: 360px;
      max-height: calc(100vh - 120px);
      overflow-y: auto;
      background: rgba(15, 23, 42, 0.92);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border-highlight);
      border-radius: 12px;
      padding: 16px;
      z-index: 50;
      display: flex;
      flex-direction: column;
      gap: 14px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.7);
      transition: opacity 0.2s ease, transform 0.2s ease;
    }
    .hud-panel.hidden {
      opacity: 0;
      transform: translateY(12px);
      pointer-events: none;
    }
    .hud-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 8px;
    }
    .hud-title { font-size: 0.88rem; font-weight: 700; color: var(--text-main); }
    .hud-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 10px 12px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .card-title {
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--accent-cyan);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .metric-row {
      display: flex;
      justify-content: space-between;
      font-size: 0.78rem;
    }
    .metric-label { color: var(--text-muted); }
    .metric-val {
      color: var(--text-main);
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-weight: 600;
    }
    .status-badge {
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 0.7rem;
      font-weight: 700;
    }
    .status-excellent { background: rgba(16, 185, 129, 0.15); color: #34d399; }
    .status-good { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
    .status-warning { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

    /* Bottom Status Bar */
    .statusbar {
      height: 28px;
      background: var(--bg-surface);
      border-top: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 14px;
      font-size: 0.75rem;
      color: var(--text-muted);
      z-index: 100;
    }
    .shortcut-hints { display: flex; gap: 14px; }
    .kbd {
      background: var(--bg-panel);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 1px 5px;
      border-radius: 4px;
      font-family: monospace;
      font-size: 0.72rem;
    }
  </style>
</head>
<body>

  <!-- Top Toolbar -->
  <header class="toolbar">
    <div class="brand-section">
      <span class="brand-logo">🌙</span>
      <span class="brand-title">Siril Moon Stacking</span>
      <span class="badge-sub">同步审验器</span>
    </div>

    <div class="controls-center">
      <div class="btn-group">
        <button id="btn-mode-side" class="active" title="双窗并排联动视图 (Tab)">🔲 双窗并排 (Side-by-Side)</button>
        <button id="btn-mode-split" title="卷帘滑动对比视图 (Tab)">↔️ 卷帘对比 (Split Slider)</button>
        <button id="btn-mode-blink" title="极速闪烁交替视图 (Space)">👁️ 闪烁交替 (Blink)</button>
      </div>

      <div style="width: 1px; height: 20px; background: var(--border-color); margin: 0 4px;"></div>

      <div class="btn-group" id="ref-source-group" title="切换左侧参考图源 (快捷键 R)">
        <button id="btn-ref-aligned" class="active" title="亚像素坐标配准版，与成片严格重合，专用于卷帘与闪烁微观细节对比 (R)">🎯 对齐对比帧</button>
        <button id="btn-ref-raw" title="相机原生未裁剪画幅，无仿射平移、无扩边留白、无消光，真实还原单帧拍摄底片 (R)">📷 原生真实 RAW</button>
      </div>

      <div style="width: 1px; height: 20px; background: var(--border-color); margin: 0 4px;"></div>

      <button id="btn-zoom-fit" class="tool-btn" title="适屏全貌 (0 / F)">适屏</button>
      <button id="btn-zoom-100" class="tool-btn" title="100% 原始物理像素 (1)">100%</button>
      <button id="btn-zoom-200" class="tool-btn" title="200% 高频细节超采样 (2)">200%</button>
      <button id="btn-center" class="tool-btn" title="月盘一键居中 (C)">🎯 居中</button>
      <span id="zoom-display" class="zoom-text">100%</span>
    </div>

    <div class="controls-right">
      <select id="target-dropdown" class="target-select"></select>
      <button id="btn-hud-toggle" class="hud-toggle-btn" title="显示/隐藏质检 HUD (H)">📊 质检 HUD</button>
    </div>
  </header>

  <!-- Workspace Area -->
  <main class="workspace" id="workspace">

    <!-- Mode 1: Side by Side -->
    <div id="side-layout" class="side-by-side-layout">
      <!-- Left Panel: Reference -->
      <div class="side-panel" id="panel-left">
        <div class="panel-pill">
          <div class="pill-title" id="ref-title-text"><span class="ref-dot"></span>单帧配准版 (Aligned Ref)</div>
          <div class="pill-meta" id="ref-meta-text">1:1 亚像素对齐 · 等度 MTF · 专用于卷帘/闪烁微观比对</div>
        </div>
        <div class="viewport" id="viewport-left">
          <div class="canvas-layer" id="layer-left">
            <img id="img-left" src="" alt="参考单帧">
          </div>
          <div class="crosshair-h" id="ch-left-h"></div>
          <div class="crosshair-v" id="ch-left-v"></div>
        </div>
      </div>

      <!-- Right Panel: Master -->
      <div class="side-panel" id="panel-right">
        <div class="panel-pill">
          <div class="pill-title"><span class="master-dot"></span>堆叠增强成片 (Master)</div>
          <div class="pill-meta" id="master-meta-text">Siril 剔除均值高动态堆叠</div>
        </div>
        <div class="viewport" id="viewport-right">
          <div class="canvas-layer" id="layer-right">
            <img id="img-right" src="" alt="堆叠成片">
          </div>
          <div class="crosshair-h" id="ch-right-h"></div>
          <div class="crosshair-v" id="ch-right-v"></div>
        </div>
      </div>
    </div>

    <!-- Mode 2: Split Slider -->
    <div id="split-layout" class="split-layout">
      <div class="split-container" id="viewport-split">
        <div class="canvas-layer" id="layer-split-ref">
          <img id="img-split-ref" src="" alt="参考单帧">
        </div>
        <div class="split-clip-layer" id="split-clip-container">
          <div class="canvas-layer" id="layer-split-master">
            <img id="img-split-master" src="" alt="堆叠成片">
          </div>
        </div>
        <div class="split-divider-line" id="split-divider">
          <div class="split-divider-handle">◀ ｜ ▶</div>
        </div>
      </div>
    </div>

    <!-- Mode 3: Blink Toggle -->
    <div id="blink-layout" class="blink-layout">
      <div class="viewport" id="viewport-blink">
        <div class="canvas-layer" id="layer-blink">
          <img id="img-blink" src="" alt="闪烁视图">
        </div>
        <div class="blink-indicator" id="blink-indicator">
          <span>当前显示:</span>
          <strong id="blink-status-text" style="color: var(--accent-cyan);">堆叠增强成片</strong>
          <span style="font-size: 0.72rem; color: var(--text-muted);">(按空格键快速交替)</span>
        </div>
      </div>
    </div>

    <!-- HUD Drawer Panel (Default Hidden) -->
    <aside class="hud-panel hidden" id="hud-panel">
      <div class="hud-header">
        <div class="hud-title">🌙 月面质检与光学参数 HUD</div>
        <span class="badge-sub" id="hud-badge-device">光学母版</span>
      </div>

      <!-- Card 1: Optics -->
      <div class="hud-card">
        <div class="card-title">🔭 光学系统与设备</div>
        <div class="metric-row"><span class="metric-label">拍摄设备</span><span class="metric-val" id="hud-device">-</span></div>
        <div class="metric-row"><span class="metric-label">焦距 / 口径</span><span class="metric-val" id="hud-optics-fl">-</span></div>
        <div class="metric-row"><span class="metric-label">焦比 / 艾里斑半径</span><span class="metric-val" id="hud-optics-airy">-</span></div>
      </div>

      <!-- Card 2: Stacking & Drizzle -->
      <div class="hud-card">
        <div class="card-title">🔄 选帧堆叠与 Drizzle</div>
        <div class="metric-row"><span class="metric-label">叠加帧数</span><span class="metric-val" id="hud-stack-frames">-</span></div>
        <div class="metric-row"><span class="metric-label">选帧保留率</span><span class="metric-val" id="hud-stack-ratio">-</span></div>
        <div class="metric-row"><span class="metric-label">Drizzle 超采样</span><span class="metric-val" id="hud-drizzle">-</span></div>
      </div>

      <!-- Card 3: Quality Metrics -->
      <div class="hud-card">
        <div class="card-title">💎 伪影与质感量化指标</div>
        <div class="metric-row"><span class="metric-label">防过锐 / 增强保留率</span><span class="metric-val" id="hud-sharp-guard">-</span></div>
        <div class="metric-row"><span class="metric-label">自适应小波 / 可信覆盖率</span><span class="metric-val" id="hud-wavelet-adapt">-</span></div>
        <div class="metric-row"><span class="metric-label">暗环比率 (DHR)</span><span class="metric-val" id="hud-dhr">-</span></div>
        <div class="metric-row"><span class="metric-label">高光白垩指数 (CSI)</span><span class="metric-val" id="hud-csi">-</span></div>
        <div class="metric-row"><span class="metric-label">梯度峰度脆裂度 (GKM)</span><span class="metric-val" id="hud-gkm">-</span></div>
        <div class="metric-row"><span class="metric-label">深空残噪标准差</span><span class="metric-val" id="hud-noise">-</span></div>
      </div>

      <!-- Card 4: Color Profile -->
      <div class="hud-card">
        <div class="card-title">🎨 色彩校准与地质矿物</div>
        <div class="metric-row"><span class="metric-label">灰世界白平衡 (kr, kb)</span><span class="metric-val" id="hud-wb">-</span></div>
        <div class="metric-row"><span class="metric-label">MTF 中间调拉伸</span><span class="metric-val" id="hud-mtf">-</span></div>
        <div class="metric-row"><span class="metric-label">钛(Ti) / 铁(Fe) 饱和度增益</span><span class="metric-val" id="hud-mineral-boost">-</span></div>
      </div>
    </aside>

  </main>

  <!-- Bottom Status Bar -->
  <footer class="statusbar">
    <div class="shortcut-hints">
      <span><span class="kbd">Space</span> 闪烁</span>
      <span><span class="kbd">R</span> 切换对齐/原生RAW</span>
      <span><span class="kbd">1</span> 100%</span>
      <span><span class="kbd">2</span> 200%</span>
      <span><span class="kbd">0</span> 适屏</span>
      <span><span class="kbd">C</span> 居中</span>
      <span><span class="kbd">Tab</span> 切换视图</span>
      <span><span class="kbd">M</span> 切换成片</span>
      <span><span class="kbd">H</span> 质检面板</span>
    </div>
    <div id="footer-dimensions">就绪</div>
  </footer>

  <script>
    const IMAGES = """ + manifest_json + """;
    const METRICS = """ + hud_json + """;

    // Application state
    const state = {
      mode: 'side-by-side', // 'side-by-side' | 'split' | 'blink'
      refSource: 'aligned', // 'aligned' | 'raw'
      zoom: 1.0,
      panX: 0,
      panY: 0,
      isDragging: false,
      dragStartX: 0,
      dragStartY: 0,
      activeTarget: null,
      splitPercent: 50.0,
      isSplitting: false,
      blinkIsRef: false,
      imgWidth: 1000,
      imgHeight: 1000,
    };

    // DOM Elements
    const dom = {
      sideLayout: document.getElementById('side-layout'),
      splitLayout: document.getElementById('split-layout'),
      blinkLayout: document.getElementById('blink-layout'),
      hudPanel: document.getElementById('hud-panel'),
      zoomDisplay: document.getElementById('zoom-display'),
      targetDropdown: document.getElementById('target-dropdown'),
      splitDivider: document.getElementById('split-divider'),
      splitClipContainer: document.getElementById('split-clip-container'),
      blinkStatusText: document.getElementById('blink-status-text'),
      footerDim: document.getElementById('footer-dimensions'),

      // Reference source toggles
      btnRefAligned: document.getElementById('btn-ref-aligned'),
      btnRefRaw: document.getElementById('btn-ref-raw'),
      refTitleText: document.getElementById('ref-title-text'),
      refMetaText: document.getElementById('ref-meta-text'),

      // Images
      imgLeft: document.getElementById('img-left'),
      imgRight: document.getElementById('img-right'),
      imgSplitRef: document.getElementById('img-split-ref'),
      imgSplitMaster: document.getElementById('img-split-master'),
      imgBlink: document.getElementById('img-blink'),

      // Layers
      layerLeft: document.getElementById('layer-left'),
      layerRight: document.getElementById('layer-right'),
      layerSplitRef: document.getElementById('layer-split-ref'),
      layerSplitMaster: document.getElementById('layer-split-master'),
      layerBlink: document.getElementById('layer-blink'),

      // Crosshairs
      chLeftH: document.getElementById('ch-left-h'),
      chLeftV: document.getElementById('ch-left-v'),
      chRightH: document.getElementById('ch-right-h'),
      chRightV: document.getElementById('ch-right-v'),
    };

    // Initialize Dropdown & Images
    function initTargets() {
      dom.targetDropdown.innerHTML = '';
      if (!IMAGES.targets || IMAGES.targets.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No product images found';
        dom.targetDropdown.appendChild(opt);
        return;
      }

      IMAGES.targets.forEach((t, idx) => {
        const opt = document.createElement('option');
        opt.value = t.file;
        opt.textContent = t.name;
        if (idx === 0) opt.selected = true;
        dom.targetDropdown.appendChild(opt);
      });

      state.activeTarget = IMAGES.targets[0];
      updateImageSources();
    }

    function updateImageSources() {
      if (!state.activeTarget) return;

      const isSquare = state.activeTarget.square;
      let refSrc = '';
      let titleHtml = '';
      let metaText = '';

      if (state.refSource === 'raw' && IMAGES.rawOriginal) {
        refSrc = IMAGES.rawOriginal;
        titleHtml = '<span class="ref-dot raw-dot"></span>相机原生 RAW (True Raw As-Shot)';
        metaText = '原生未加工底片 · 原生画幅 · 无裁切扩边仿射';
      } else if (isSquare && IMAGES.referenceSquare) {
        refSrc = IMAGES.referenceSquare;
        titleHtml = '<span class="ref-dot"></span>单帧配准版 (Aligned Square)';
        metaText = '1:1 方形特写配准 · 视宁度第 1 名';
      } else {
        refSrc = IMAGES.reference || (state.activeTarget ? state.activeTarget.file : '');
        titleHtml = '<span class="ref-dot"></span>单帧配准版 (Aligned Ref)';
        metaText = '1:1 亚像素对齐 · 等度 MTF · 专用于卷帘/闪烁微观比对';
      }

      if (dom.refTitleText) dom.refTitleText.innerHTML = titleHtml;
      if (dom.refMetaText) dom.refMetaText.textContent = metaText;

      const masterSrc = state.activeTarget.file;

      dom.imgLeft.src = refSrc;
      dom.imgRight.src = masterSrc;
      dom.imgSplitRef.src = refSrc;
      dom.imgSplitMaster.src = masterSrc;
      dom.imgBlink.src = state.blinkIsRef ? refSrc : masterSrc;

      // Track loaded dimensions from master
      const testImg = new Image();
      testImg.onload = () => {
        state.imgWidth = testImg.naturalWidth;
        state.imgHeight = testImg.naturalHeight;
        dom.footerDim.textContent = `${state.imgWidth} × ${state.imgHeight} px · ${state.activeTarget.name}`;
        zoomFit();
      };
      testImg.src = masterSrc;
    }

    // Transform application
    function applyTransform() {
      const transform = `translate3d(${state.panX}px, ${state.panY}px, 0) scale(${state.zoom})`;

      dom.layerLeft.style.transform = transform;
      dom.layerRight.style.transform = transform;
      dom.layerSplitRef.style.transform = transform;
      dom.layerSplitMaster.style.transform = transform;
      dom.layerBlink.style.transform = transform;

      dom.zoomDisplay.textContent = `${Math.round(state.zoom * 100)}%`;
      updateSplitClip();
    }

    function updateSplitClip() {
      if (state.mode !== 'split') return;
      const pct = state.splitPercent;
      dom.splitDivider.style.left = `${pct}%`;
      dom.splitClipContainer.style.clipPath = `polygon(${pct}% 0, 100% 0, 100% 100%, ${pct}% 100%)`;
    }

    // Zoom Helpers
    function setZoom(newZoom, centerX, centerY) {
      newZoom = Math.max(0.05, Math.min(newZoom, 12.0));
      if (!centerX || !centerY) {
        const vp = getCurrentViewport();
        const rect = vp.getBoundingClientRect();
        centerX = rect.width / 2;
        centerY = rect.height / 2;
      }
      state.panX = centerX - (centerX - state.panX) * (newZoom / state.zoom);
      state.panY = centerY - (centerY - state.panY) * (newZoom / state.zoom);
      state.zoom = newZoom;
      applyTransform();
    }

    function zoomFit() {
      const vp = getCurrentViewport();
      const rect = vp.getBoundingClientRect();
      if (!rect.width || !rect.height || !state.imgWidth) return;

      const scaleX = rect.width / state.imgWidth;
      const scaleY = rect.height / state.imgHeight;
      const fitZoom = Math.min(scaleX, scaleY) * 0.94;

      state.zoom = fitZoom;
      state.panX = (rect.width - state.imgWidth * fitZoom) / 2;
      state.panY = (rect.height - state.imgHeight * fitZoom) / 2;
      applyTransform();
    }

    function centerMoon() {
      const vp = getCurrentViewport();
      const rect = vp.getBoundingClientRect();
      state.panX = (rect.width - state.imgWidth * state.zoom) / 2;
      state.panY = (rect.height - state.imgHeight * state.zoom) / 2;
      applyTransform();
    }

    function getCurrentViewport() {
      if (state.mode === 'side-by-side') return document.getElementById('viewport-left');
      if (state.mode === 'split') return document.getElementById('viewport-split');
      return document.getElementById('viewport-blink');
    }

    // View Mode Switching
    function setMode(newMode) {
      state.mode = newMode;

      dom.sideLayout.style.display = newMode === 'side-by-side' ? 'flex' : 'none';
      dom.splitLayout.style.display = newMode === 'split' ? 'block' : 'none';
      dom.blinkLayout.style.display = newMode === 'blink' ? 'block' : 'none';

      document.getElementById('btn-mode-side').classList.toggle('active', newMode === 'side-by-side');
      document.getElementById('btn-mode-split').classList.toggle('active', newMode === 'split');
      document.getElementById('btn-mode-blink').classList.toggle('active', newMode === 'blink');

      zoomFit();
    }

    // Blink Toggle Logic
    function toggleBlink() {
      if (state.mode !== 'blink') {
        setMode('blink');
        return;
      }
      state.blinkIsRef = !state.blinkIsRef;
      const isSquare = state.activeTarget && state.activeTarget.square;
      let refSrc = '';
      if (state.refSource === 'raw' && IMAGES.rawOriginal) {
        refSrc = IMAGES.rawOriginal;
      } else if (isSquare && IMAGES.referenceSquare) {
        refSrc = IMAGES.referenceSquare;
      } else {
        refSrc = IMAGES.reference || (state.activeTarget ? state.activeTarget.file : '');
      }
      const masterSrc = state.activeTarget ? state.activeTarget.file : '';

      dom.imgBlink.src = state.blinkIsRef ? refSrc : masterSrc;
      dom.blinkStatusText.textContent = state.blinkIsRef
        ? (state.refSource === 'raw' ? '相机原生 RAW (未加工底片)' : '单帧配准版 (1:1对齐)')
        : '堆叠增强成片 (Master)';
      dom.blinkStatusText.style.color = state.blinkIsRef ? 'var(--accent-amber)' : 'var(--accent-cyan)';
    }

    // Event Listeners: Pan & Zoom
    function bindViewportEvents(vp) {
      vp.addEventListener('mousedown', (e) => {
        if (state.isSplitting) return;
        state.isDragging = true;
        state.dragStartX = e.clientX - state.panX;
        state.dragStartY = e.clientY - state.panY;
      });

      vp.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = vp.getBoundingClientRect();
        const cursorX = e.clientX - rect.left;
        const cursorY = e.clientY - rect.top;
        const factor = e.deltaY < 0 ? 1.15 : (1 / 1.15);
        setZoom(state.zoom * factor, cursorX, cursorY);
      }, { passive: false });

      vp.addEventListener('dblclick', () => {
        if (Math.abs(state.zoom - 1.0) < 0.1) zoomFit();
        else setZoom(1.0);
      });
    }

    window.addEventListener('mousemove', (e) => {
      if (state.isDragging) {
        state.panX = e.clientX - state.dragStartX;
        state.panY = e.clientY - state.dragStartY;
        applyTransform();
      }

      if (state.isSplitting) {
        const vp = dom.splitLayout.getBoundingClientRect();
        const offsetX = e.clientX - vp.left;
        state.splitPercent = Math.max(0, Math.min(100, (offsetX / vp.width) * 100));
        updateSplitClip();
      }

      // Update Synchronized Crosshair in Side-by-Side
      if (state.mode === 'side-by-side') {
        const vpL = document.getElementById('viewport-left').getBoundingClientRect();
        const vpR = document.getElementById('viewport-right').getBoundingClientRect();

        let relX = -1, relY = -1;
        if (e.clientX >= vpL.left && e.clientX <= vpL.right && e.clientY >= vpL.top && e.clientY <= vpL.bottom) {
          relX = e.clientX - vpL.left;
          relY = e.clientY - vpL.top;
        } else if (e.clientX >= vpR.left && e.clientX <= vpR.right && e.clientY >= vpR.top && e.clientY <= vpR.bottom) {
          relX = e.clientX - vpR.left;
          relY = e.clientY - vpR.top;
        }

        if (relX >= 0) {
          dom.chLeftH.style.display = 'block';
          dom.chLeftV.style.display = 'block';
          dom.chRightH.style.display = 'block';
          dom.chRightV.style.display = 'block';

          dom.chLeftH.style.top = `${relY}px`;
          dom.chLeftV.style.left = `${relX}px`;
          dom.chRightH.style.top = `${relY}px`;
          dom.chRightV.style.left = `${relX}px`;
        } else {
          dom.chLeftH.style.display = 'none';
          dom.chLeftV.style.display = 'none';
          dom.chRightH.style.display = 'none';
          dom.chRightV.style.display = 'none';
        }
      }
    });

    window.addEventListener('mouseup', () => {
      state.isDragging = false;
      state.isSplitting = false;
    });

    // Split Divider Dragging
    dom.splitDivider.addEventListener('mousedown', (e) => {
      e.stopPropagation();
      state.isSplitting = true;
    });

    // UI Buttons Binding
    document.getElementById('btn-mode-side').addEventListener('click', () => setMode('side-by-side'));
    document.getElementById('btn-mode-split').addEventListener('click', () => setMode('split'));
    document.getElementById('btn-mode-blink').addEventListener('click', () => setMode('blink'));

    if (dom.btnRefAligned) {
      dom.btnRefAligned.addEventListener('click', () => {
        state.refSource = 'aligned';
        dom.btnRefAligned.classList.add('active');
        if (dom.btnRefRaw) dom.btnRefRaw.classList.remove('active');
        updateImageSources();
      });
    }
    if (dom.btnRefRaw) {
      dom.btnRefRaw.addEventListener('click', () => {
        state.refSource = 'raw';
        dom.btnRefRaw.classList.add('active');
        if (dom.btnRefAligned) dom.btnRefAligned.classList.remove('active');
        updateImageSources();
      });
    }

    document.getElementById('btn-zoom-fit').addEventListener('click', zoomFit);
    document.getElementById('btn-zoom-100').addEventListener('click', () => setZoom(1.0));
    document.getElementById('btn-zoom-200').addEventListener('click', () => setZoom(2.0));
    document.getElementById('btn-center').addEventListener('click', centerMoon);

    dom.targetDropdown.addEventListener('change', (e) => {
      const match = IMAGES.targets.find(t => t.file === e.target.value);
      if (match) {
        state.activeTarget = match;
        updateImageSources();
      }
    });

    document.getElementById('btn-hud-toggle').addEventListener('click', () => {
      const btn = document.getElementById('btn-hud-toggle');
      dom.hudPanel.classList.toggle('hidden');
      btn.classList.toggle('active', !dom.hudPanel.classList.contains('hidden'));
    });

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

      if (e.code === 'Space') {
        e.preventDefault();
        toggleBlink();
      } else if (e.key.toLowerCase() === 'r') {
        state.refSource = (state.refSource === 'aligned') ? 'raw' : 'aligned';
        if (dom.btnRefAligned) dom.btnRefAligned.classList.toggle('active', state.refSource === 'aligned');
        if (dom.btnRefRaw) dom.btnRefRaw.classList.toggle('active', state.refSource === 'raw');
        updateImageSources();
      } else if (e.key === '1') {
        setZoom(1.0);
      } else if (e.key === '2') {
        setZoom(2.0);
      } else if (e.key === '0' || e.key.toLowerCase() === 'f') {
        zoomFit();
      } else if (e.key.toLowerCase() === 'c') {
        centerMoon();
      } else if (e.key.toLowerCase() === 'h') {
        document.getElementById('btn-hud-toggle').click();
      } else if (e.key === 'Tab') {
        e.preventDefault();
        if (state.mode === 'side-by-side') setMode('split');
        else if (state.mode === 'split') setMode('blink');
        else setMode('side-by-side');
      } else if (e.key.toLowerCase() === 'm') {
        // Cycle Target master
        const idx = IMAGES.targets.findIndex(t => t.file === state.activeTarget.file);
        const nextIdx = (idx + 1) % IMAGES.targets.length;
        state.activeTarget = IMAGES.targets[nextIdx];
        dom.targetDropdown.value = state.activeTarget.file;
        updateImageSources();
      }
    });

    // Populate HUD Metrics
    function populateHUD() {
      if (!METRICS) return;

      const dev = METRICS.device || {};
      document.getElementById('hud-device').textContent = dev.name || '未知设备';
      if (dev.name) document.getElementById('hud-badge-device').textContent = dev.name;

      const opt = METRICS.optics || {};
      const fl = opt.focal_length ? `${opt.focal_length.toFixed(1)}mm` : '未提供';
      const dia = opt.aperture ? `D=${opt.aperture.toFixed(0)}mm` : '';
      document.getElementById('hud-optics-fl').textContent = `${fl} ${dia}`.trim() || '几何反推';

      const fr = opt.f_ratio ? `F/${opt.f_ratio}` : '';
      const airy = opt.airy_r ? `艾里斑 r=${opt.airy_r}px` : '';
      document.getElementById('hud-optics-airy').textContent = `${fr} ${airy}`.trim() || '衍射极限';

      const stk = METRICS.stacking || {};
      const methodZh = (stk.method === 'sum') ? '直接累加' : '剔除均值 (Rej Mean)';
      document.getElementById('hud-stack-frames').textContent = stk.stacked_frames ? `${stk.stacked_frames} 帧 (${methodZh})` : '自动堆叠';
      document.getElementById('hud-stack-ratio').textContent = stk.ratio ? `${stk.ratio} (共 ${stk.total_frames || stk.registered_frames} 帧)` : 'Otsu 聚类';

      const drz = METRICS.drizzle || {};
      document.getElementById('hud-drizzle').textContent = drz.enabled ? `${drz.scale}× (${drz.kernel || 'square'} 核)` : '原生采样 (关闭)';

      const q = METRICS.quality || {};
      const guard = q.sharp_guard || {};
      const guardLabels = {accepted: '通过', reduced: '已降档', fallback: '已回退基准', off: '已关闭', unknown: '未知'};
      document.getElementById('hud-sharp-guard').textContent = `${guardLabels[guard.status] || '未知'}${guard.alpha == null ? '' : ' / ' + Math.round(guard.alpha * 100) + '%'}`;
      const adaptation = q.wavelet_adaptation || {};
      const adaptationLabels = {applied: '已分配', unavailable: '证据不足', bypassed: '已旁路', off: '已关闭', unknown: '未知'};
      document.getElementById('hud-wavelet-adapt').textContent = `${adaptationLabels[adaptation.status] || '未知'}${adaptation.support_coverage == null ? '' : ' / ' + (adaptation.support_coverage * 100).toFixed(1) + '%'}`;
      if (q.dhr !== undefined && q.dhr !== null) {
        let dhrZh = '受控';
        let dhrClass = 'status-good';
        if (q.dhr < 0.02) { dhrZh = '极佳 (无暗环)'; dhrClass = 'status-excellent'; }
        else if (q.dhr >= 0.035) { dhrZh = '警告 (边缘暗环)'; dhrClass = 'status-warning'; }
        document.getElementById('hud-dhr').innerHTML = `${Number(q.dhr).toFixed(4)} <span class="status-badge ${dhrClass}">${dhrZh}</span>`;
      }
      if (q.csi !== undefined && q.csi !== null) {
        let csiZh = '极佳 (高光动态保留)';
        let csiClass = 'status-excellent';
        if (q.csi >= 0.025) { csiZh = '警告 (高光白垩化)'; csiClass = 'status-warning'; }
        else if (q.csi >= 0.015) { csiZh = '良好'; csiClass = 'status-good'; }
        document.getElementById('hud-csi').innerHTML = `${Number(q.csi).toFixed(4)} <span class="status-badge ${csiClass}">${csiZh}</span>`;
      }
      if (q.gkm !== undefined && q.gkm !== null) {
        let gkmZh = '温润纯镜感 (Organic)';
        let gkmClass = 'status-good';
        if (q.gkm >= 14.0) { gkmZh = '警告 (毛刺脆裂)'; gkmClass = 'status-warning'; }
        else if (q.gkm >= 6.0) { gkmZh = '清晰微反差 (Crisp)'; gkmClass = 'status-good'; }
        document.getElementById('hud-gkm').innerHTML = `${Number(q.gkm).toFixed(2)} <span class="status-badge ${gkmClass}">${gkmZh}</span>`;
      }
      if (q.bg_noise_std !== undefined && q.bg_noise_std !== null) {
        const noiseStatusZh = (q.bg_noise_status === 'measured') ? '实测' : (q.bg_noise_status || '实测');
        document.getElementById('hud-noise').textContent = `${Number(q.bg_noise_std).toFixed(5)} (${noiseStatusZh})`;
      }

      const col = METRICS.color || {};
      const kr = col.wb_kr ? Number(col.wb_kr).toFixed(3) : '1.00';
      const kb = col.wb_kb ? Number(col.wb_kb).toFixed(3) : '1.00';
      document.getElementById('hud-wb').textContent = `kr=${kr}, kb=${kb}`;
      document.getElementById('hud-mtf').textContent = col.midtone ? Number(col.midtone).toFixed(2) : '0.13';

      const ti = col.ti_boost ? `钛(Ti) ×${Number(col.ti_boost).toFixed(1)}` : '';
      const fe = col.fe_boost ? `铁(Fe) ×${Number(col.fe_boost).toFixed(1)}` : '';
      document.getElementById('hud-mineral-boost').textContent = `${ti} ${fe}`.trim() || '标准 LRGB';
    }

    // Init
    window.addEventListener('load', () => {
      bindViewportEvents(document.getElementById('viewport-left'));
      bindViewportEvents(document.getElementById('viewport-right'));
      bindViewportEvents(document.getElementById('viewport-split'));
      bindViewportEvents(document.getElementById('viewport-blink'));

      initTargets();
      populateHUD();
    });

    window.addEventListener('resize', zoomFit);
  </script>
</body>
</html>
"""

    out_html.write_text(html_template, encoding="utf-8")
    log(f"comparison viewer generated: {out_html.name}")
    return out_html


def cmd_verify(args) -> None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(args.work).expanduser().resolve()
    master_path = work / args.master
    nat_tif = work / "moon_natural.tif"
    rank_json = work / "ranking.json"

    if not master_path.exists():
        die(f"master file not found: {master_path}")

    # Inspect master
    with fits.open(master_path, memmap=False) as hdul:
        master_data = hdul[0].data
        m_hdr = hdul[0].header

    px_scale = _resolve_px_scale(args, work, master_path)

    report = {
        "master": str(master_path),
        "shape": list(master_data.shape),
        "bitpix": m_hdr.get("BITPIX"),
        "min": float(master_data.min()),
        "max": float(master_data.max()),
        "mean": float(master_data.mean()),
    }

    # Drizzle supersampling state (from the master header and/or the stack receipt)
    drz_scale = m_hdr.get("DRZSCALE")
    report["drizzle"] = {
        "px_scale": px_scale,
        "enabled": bool(drz_scale and float(drz_scale) != 1.0),
        "scale": float(drz_scale) if drz_scale else px_scale,
        "pixfrac": m_hdr.get("DRZPIXFR"),
        "kernel": m_hdr.get("DRZKRENL"),
        "native_pixel_size": m_hdr.get("ORIGXPIX"),
        "effective_pixel_size": m_hdr.get("XPIXSZ"),
    }

    # Capture device identification and whether any optics came from the table
    report["device"] = {
        "id": m_hdr.get("DEVICE"),
        "match_source": m_hdr.get("DEVSRC"),
        "confidence": m_hdr.get("DEVCONF"),
        "optical_source": m_hdr.get("OPTISRC"),
        "table_priors": m_hdr.get("OPTPRIOR"),
    }

    mosaic_mode = getattr(args, "mosaic_mode", None)
    tile_json = work / "mosaic_tile_info.json"
    t_data = {}
    if tile_json.exists():
        try:
            t_data = load_json(tile_json)
            if not mosaic_mode:
                mosaic_mode = t_data.get("mosaic_mode", "disc")
            report["focal_length"] = t_data.get("focal_length")
            report["aperture"] = t_data.get("aperture")
            report["pixel_size"] = t_data.get("pixel_size")
            report["optical_inference"] = t_data.get("optical_inference")
        except Exception:
            pass
    if not mosaic_mode:
        mosaic_mode = "disc"
    report["mosaic_mode"] = mosaic_mode

    prof_json = work / "lunar_profile.json"
    if prof_json.exists():
        try:
            p_data = load_json(prof_json)
            report["profile_locked"] = bool(p_data.get("locked"))
            report["lock_source"] = p_data.get("lock_source")
            report["profile_hi_lum"] = p_data.get("histogram", {}).get("hi_lum")
            report["profile_kr"] = p_data.get("white_balance", {}).get("k_r")
            report["profile_kb"] = p_data.get("white_balance", {}).get("k_b")
        except Exception:
            pass

    ext_json = work / "extinction_receipt.json"
    if ext_json.exists():
        try:
            report["extinction_compensation"] = load_json(ext_json)
        except Exception:
            pass

    # Measure each channel on identified empty sky.
    background = _background_metrics(master_data)
    report["background_noise"] = background
    report["background_noise_std"] = background.get("std_green")
    report["background_noise_status"] = background["status"]

    # Measure surface sharpness on master vs single frame
    if rank_json.exists():
        r_info = load_json(rank_json)
        ref_idx = r_info.get("reference_index")
        report["reference_frame"] = ref_idx
        report["candidate_frames"] = r_info.get("candidate_frames", r_info.get("total_frames"))
        report["registered_frames"] = r_info.get("registered_frames", r_info.get("kept_frames"))
    if (work/"stack_receipt.json").exists():
        stack_info = load_json(work/"stack_receipt.json")
        report["stacked_frames"] = stack_info.get("stacked_frames")
        report["kept_frames"] = report["stacked_frames"]
        report["dropped_frames"] = stack_info.get("dropped_frames")

    post_receipt_path = work / "postprocess_receipt.json"
    post_receipt = load_json(post_receipt_path) if post_receipt_path.exists() else {}
    guard = post_receipt.get("sharp_guard", {})
    report["wavelet_adaptation"] = post_receipt.get("wavelet_adaptation", {"status": "unknown"})
    delivery = _verify_sharp_guard(work, guard, t_data if tile_json.exists() else {}, px_scale)
    report["sharp_guard"] = dict(guard, delivery=delivery)
    if delivery["status"] == "unknown":
        report["sharp_guard"].update(status="unknown", alpha=None)

    full_baseline = work / "moon_unsharpened.fit"
    if full_baseline.exists() and guard.get("detail_file") in ("moon_lum_sharp.fit", "moon_sharp.fit"):
        base_path = full_baseline
        sharp_path = work / guard["detail_file"]
    else:
        base_path = work / "moon_lum_base.fit"
        sharp_path = work / "moon_lum_sharp.fit"
        if not base_path.exists():
            base_path, sharp_path = work / "moon_base.fit", work / "moon_sharp.fit"
    if base_path.exists() and sharp_path.exists():
        try:
            b_dat = fits.getdata(base_path, memmap=False)
            s_dat = fits.getdata(sharp_path, memmap=False)
            if base_path == full_baseline:
                metrics = _sharp_guard_metrics(_sharp_guard_context(b_dat, px_scale, _load_wavelet_support(work, guard)), s_dat)
                dhr_val, csi_val, gkm_val = (metrics.get(key) for key in ("dhr", "csi", "gkm"))
                report["positive_overshoot_ratio"] = metrics.get("positive_overshoot_ratio")
                report["sharp_guard"]["verified_selected"] = metrics
            else:
                b_dat, s_dat = _guard_luminance(b_dat), _guard_luminance(s_dat)
                mask = _compute_edge_ringing_damping_mask(b_dat, px_scale=px_scale)
                dhr_val = _measure_dark_halo_ratio(b_dat, s_dat, mask)
                csi_val = _measure_chalky_saturation_index(s_dat)
                gkm_val = _measure_gradient_kurtosis(s_dat)
            report["dark_halo_ratio"] = dhr_val
            report["dark_halo_status"] = ("NOT_APPLICABLE (no measurable step edges)" if dhr_val is None else
                "EXCELLENT (artifact-free)" if dhr_val < 0.015 else
                "GOOD (controlled)" if dhr_val < 0.035 else "WARNING (edge ringing detected)")
            report["chalky_saturation_index"] = csi_val
            report["chalky_status"] = ("UNAVAILABLE" if csi_val is None else
                "EXCELLENT (highlight dynamic retained)" if csi_val < 0.010 else
                "GOOD (controlled highlights)" if csi_val < 0.025 else "WARNING (highlight chalkiness / over-saturation)")
            report["gradient_kurtosis"] = gkm_val
            report["brittleness_status"] = ("UNAVAILABLE" if gkm_val is None else
                "ORGANIC (natural smooth texture)" if gkm_val < 6.0 else
                "CRISP (high detail)" if gkm_val < 14.0 else "WARNING (brittle / crunchy texture detected)")
        except (OSError, ValueError, TypeError) as exc:
            report["quality_metric_error"] = str(exc)

    dump_json(work / "verify_report.json", report)
    log("verification summary:")
    log(f"  Mosaic mode: {report['mosaic_mode']}")
    dev = report.get("device") or {}
    if dev.get("id") or dev.get("match_source"):
        prior_note = (f", table priors: {dev.get('table_priors')}"
                      if dev.get("table_priors") else "")
        log(f"  Capture device: {dev.get('id') or 'unknown'} "
            f"(matched from {dev.get('match_source')}, confidence={dev.get('confidence')}"
            f"{prior_note})")
    drz = report.get("drizzle") or {}
    if drz.get("enabled"):
        log(f"  Drizzle: x{float(drz.get('scale', 1.0)):g} supersampled "
            f"(pixfrac={drz.get('pixfrac')}, kernel={drz.get('kernel')}, "
            f"native pixel={drz.get('native_pixel_size')}um -> effective {drz.get('effective_pixel_size')}um)")
    else:
        log("  Drizzle: off (native sampling)")
    if report.get("profile_locked"):
        log(f"  Calibration Profile: LOCKED (source={report.get('lock_source')}, hi_lum={report.get('profile_hi_lum')})")
    elif "profile_locked" in report:
        log(f"  Calibration Profile: ANCHOR MASTER (unlocked baseline, hi_lum={report.get('profile_hi_lum')})")
    if all(report.get(k) for k in ("focal_length","aperture","pixel_size")):
        fl_v = float(report["focal_length"])
        dia_v = float(report.get("aperture") or 80.0)
        px_v = float(report.get("pixel_size") or 3.73)
        f_rat = fl_v / dia_v if dia_v else 0.0
        airy_r = (1.22 * 0.55 * fl_v) / (dia_v * px_v) if (dia_v and px_v) else 0.0
        opt_src = report.get("optical_inference", {}).get("focal_source", "manual") if report.get("optical_inference") else "manual"
        log(f"  Optical Setup: fl={fl_v:.1f}mm, dia={dia_v:.1f}mm (F/{f_rat:.1f}, Airy r={airy_r:.2f}px, source='{opt_src}')")
    log(f"  Master dimensions: {report['shape']} (BITPIX={report['bitpix']})")
    log(f"  Pixel range: [{report['min']:.4f}, {report['max']:.4f}] (mean={report['mean']:.4f})")
    log(f"  Background noise: {report['background_noise_std']} ({report['background_noise_status']})")
    if "extinction_compensation" in report:
        ec = report["extinction_compensation"]
        log(f"  Atmospheric Extinction: COMPENSATED (zenith={ec.get('zenith_angle_deg')}°, B-grad={ec.get('amp_b', 0)*100:.1f}%, conf={ec.get('confidence')})")
    if report.get("dark_halo_ratio") is not None:
        log(f"  Dark Halo Ratio (DHR): {report['dark_halo_ratio']:.4f} -> [{report.get('dark_halo_status', 'N/A')}]")
    if report.get("chalky_saturation_index") is not None:
        log(f"  Chalky Saturation (CSI): {report['chalky_saturation_index']:.4f} -> [{report.get('chalky_status', 'N/A')}]")
    if report.get("gradient_kurtosis") is not None:
        log(f"  Gradient Kurtosis (GKM): {report['gradient_kurtosis']:.2f} -> [{report.get('brittleness_status', 'N/A')}]")
    if (work / "moon_natural.jpg").exists():
        log(f"  [OK] moon_natural.jpg ({round((work / 'moon_natural.jpg').stat().st_size / 1024)} KB)")
    if (work / "moon_mineral.jpg").exists():
        log(f"  [OK] moon_mineral.jpg ({round((work / 'moon_mineral.jpg').stat().st_size / 1024)} KB)")

    # Update interactive HTML dual-window comparison viewer with full verification metrics
    if getattr(args, "html_report", True):
        if not (work / "moon_reference.jpg").exists():
            _render_reference_frame(
                work=work,
                seq_name=getattr(args, "seq", "moon_"),
            )
        html_file = _generate_comparison_html(
            work=work,
            report=report,
        )
        log(f"  - Comparison viewer:   {html_file}")

    if delivery["status"] == "failed":
        die(f"delivered TIFF failed sharp guard verification; see {work / 'verify_report.json'}")




# ---------------------------------------------------------------------- 6. all

def cmd_all(args) -> None:
    if getattr(args,"candidate_mode","single") == "feedback":
        if getattr(args,"sample_mode","adaptive") in ("head","window"):
            die("feedback is only available for quality-selected video modes")
        _candidate_feedback(args)
    else:
        cmd_import(args)
        cmd_register(args)
        cmd_stack(args)
    cmd_postprocess(args)
    cmd_verify(args)


# -------------------------------------------------------------------- 7. probe

# ------------------------------------------------------------------ 6. devices

def cmd_devices(args) -> None:
    """Print the built-in smart-telescope specification table as JSON.

    Emitted machine-readably so an agent can look a device up directly.  Every
    entry carries its source grade and caveats, because these are published
    specifications used as priors -- never measurements.
    """
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
    info = {"siril": args.siril, "siril_exists": os.path.exists(args.siril)}
    if info["siril_exists"]:
        env = dict(os.environ, LANG="C")
        p = subprocess.run([args.siril, "-v"], capture_output=True, text=True, env=env, timeout=60)
        info["siril_version"] = (p.stdout or "").strip().splitlines()[-1:]
    for mod in ("numpy", "astropy", "cv2", "skimage"):
        try:
            m = __import__(mod)
            info[mod] = getattr(m, "__version__", "ok")
        except Exception:
            info[mod] = None
    print(json.dumps(info, indent=2))


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
    a.set_defaults(func=cmd_import)

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
    a.set_defaults(func=cmd_register)

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
    a.set_defaults(func=cmd_stack)

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
    a.set_defaults(func=cmd_postprocess)

    a = sub.add_parser("verify")
    a.add_argument("--work", required=True)
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--mosaic-mode", default=None, choices=["disc", "tile"],
                   help="Mosaic mode override ('disc' or 'tile', auto-detected from mosaic_tile_info.json if omitted)")
    a.set_defaults(func=cmd_verify)

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
    a.set_defaults(func=cmd_all)

    for name in ("import", "all"):
        command = sub.choices[name]
        command.add_argument("--video-roi", default="auto", choices=["auto","off"], help="Fixed tracked lunar crop; off preserves full source dimensions")
        command.add_argument("--roi-margin", type=int, default=64, help="Lunar crop margin in native pixels")
        command.add_argument("--min-signal-ratio",type=float,default=0.2,help="Minimum lunar signal versus the clip's 90th percentile (default: 0.2); rejects photon-starved compression texture")
    for name in ("import","stack","postprocess","all"):
        sub.choices[name].add_argument("--disk-budget-gb", type=float, default=None, help="Maximum work allocation in GiB; filesystem safety reserve still applies")
    for name in ("register","all"):
        sub.choices[name].add_argument("--register-selection", default="auto", choices=["auto","reselect"], help="Reuse primary video selection or explicitly filter again")
    sub.choices["all"].add_argument("--candidate-mode", default="single", choices=["single","feedback"], help="Single selection or measured doubling of video candidates")
    for name in ("postprocess","all"):
        sub.choices[name].add_argument("--wavelet-adapt", default="auto", choices=["auto", "off"], help="Limit wavelet gains to supported native scales and regions (default: auto)")
        sub.choices[name].add_argument("--luminance-channel", default="weighted", choices=["weighted","green"], help="Weighted RGB luminance or green detail plane")
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




if __name__ == "__main__":
    ns = build_parser().parse_args()
    ns.func(ns)
