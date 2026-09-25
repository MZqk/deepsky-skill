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
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

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

    ssf_file.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
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
            "date_obs": h.get("DATE-OBS"),
        }



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
) -> dict:
    """Extract frames from SER file into individual 16-bit FITS files with metadata."""
    from astropy.io import fits
    import cv2

    hdr = parse_ser_header(ser_path)
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

    dt_endian = "<" if hdr["little_endian"] else ">"
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

        hdu.writeto(target_fit, overwrite=True)

    return {
        "extracted_frames": extract_count,
        "total_in_file": total_frames,
        "channels": out_channels,
        "width": w,
        "height": h,
        "metadata": hdr,
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


def probe_video_seeing_profile(
    video_path: Path,
    stride: int = 2,
    roi_size: int = 400,
) -> list[tuple[int, float]]:
    """Pass 1: Lightweight stream probe without disk I/O.

    Computes high-frequency seeing sharpness on central lunar surface ROI
    across the entire video container to generate an objective seeing timeline.
    """
    import cv2
    import numpy as np

    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        cap.release()
        raise ValueError(format_video_decode_error(video_path, "probe_open"))

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    # 1. Localize lunar center on first valid frame
    ret, frame = cap.read()
    if not ret or frame is None:
        cap.release()
        raise ValueError(format_video_decode_error(video_path, "probe_initial_frame", cap=cap, total_reported=total_frames))

    gray_init = frame[..., 0] if frame.ndim == 3 else frame
    mask = gray_init > 20
    ys, xs = np.nonzero(mask)
    if len(xs) > 100:
        cx, cy = int(round(xs.mean())), int(round(ys.mean()))
    else:
        cx, cy = w // 2, h // 2

    half = roi_size // 2
    x0, x1 = max(0, cx - half), min(w, cx + half)
    y0, y1 = max(0, cy - half), min(h, cy + half)

    stride = max(1, int(stride))
    log(f"pass 1 seeing probe: scanning video container [{video_path.name}] (total={total_frames if total_frames > 0 else 'stream'} frames, stride={stride}, ROI=[{x0}:{x1}, {y0}:{y1}])...")

    results: list[tuple[int, float]] = []
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret or frame is None:
            break

        if frame_idx % stride == 0:
            gray_roi = frame[y0:y1, x0:x1, 0] if frame.ndim == 3 else frame[y0:y1, x0:x1]
            lap_val = float(cv2.Laplacian(gray_roi, cv2.CV_32F).var())
            results.append((frame_idx, lap_val))

        frame_idx += 1

    cap.release()
    log(f"pass 1 seeing probe complete: sampled {len(results)} frames across {frame_idx} total frames")
    return results


def unpack_video_to_fits(
    video_path: Path,
    out_dir: Path,
    seq_name: str = "moon_",
    limit: int = 0,
    force_mono: bool = False,
    debayer: str = "auto",
    start_frame: int = 0,
    target_indices: list[int] | None = None,
) -> dict:
    """Extract frames from an AVI/MP4/MOV video container directly into 16-bit FITS files.

    Direct-pass architecture:
      - Reads frames sequentially or via targeted direct-seek (FFmpeg/OpenCV).
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

    if target_indices is not None and len(target_indices) > 0:
        mode_seek = True
        frame_queue = sorted(list(target_indices))
        if limit > 0:
            frame_queue = frame_queue[:limit]
        log(f"unpacking video in targeted direct-seek mode: extracting {len(frame_queue)} optimal frames...")
    else:
        mode_seek = False
        frame_queue = None
        if start_frame > 0:
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

    out_channels = 1 if force_mono else 3
    detected_mode = None  # "mono", "rgb", or debayer cv2 code

    seek_ptr = 0
    while True:
        if mode_seek:
            if seek_ptr >= len(frame_queue):
                break
            target_fno = frame_queue[seek_ptr]
            seek_ptr += 1
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_fno)
            current_src_frame = target_fno
            ret, frame = cap.read()
        else:
            if limit > 0 and extract_count >= limit:
                break
            current_src_frame = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
            ret, frame = cap.read()

        if not ret or frame is None:
            if mode_seek:
                continue
            else:
                break

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

        extract_count += 1
        target_fit = out_dir / f"{seq_name}{extract_count:05d}.fit"

        hdu = fits.PrimaryHDU(fits_data)
        hdu.header["BITPIX"] = 16
        hdu.header["ORIG_BIT"] = 8
        hdu.header["SRC_FMT"] = video_path.suffix.upper().lstrip(".")
        if fps > 0:
            hdu.header["FPS"] = fps
        if fourcc_str:
            hdu.header["FOURCC"] = fourcc_str
        if current_src_frame is not None:
            hdu.header["SRC_FRM"] = int(current_src_frame)

        if "SENSOR" in sidecar_meta:
            hdu.header["INSTRUME"] = sidecar_meta["SENSOR"]
            sens = sidecar_meta["SENSOR"].lower()
            if "imx585" in sens or "imx462" in sens or "imx662" in sens:
                hdu.header["XPIXSZ"] = 2.9
                hdu.header["YPIXSZ"] = 2.9
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

        hdu.writeto(target_fit, overwrite=True)

    cap.release()

    if extract_count <= 0:
        raise ValueError(format_video_decode_error(video_path, "unpack_zero_frames", total_reported=total_in_file))

    return {
        "extracted_frames": extract_count,
        "total_in_file": total_frames_val if (total_frames_val := total_in_file) > 0 else extract_count,
        "channels": out_channels,
        "width": w,
        "height": h,
        "fps": fps,
        "fourcc": fourcc_str,
        "orig_bit_depth": 8,
    }


# ------------------------------------------------------------------- 1. import

def cmd_import(args) -> None:
    src = Path(args.input).expanduser().resolve()
    work = Path(args.work).expanduser().resolve()

    if not src.exists():
        die(f"input path not found: {src}")

    work.mkdir(parents=True, exist_ok=True)
    logs_dir = work / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    fmt = getattr(args, "format", "auto")
    limit = getattr(args, "limit", 0)
    ser_debayer = getattr(args, "ser_debayer", True)
    force_mono = getattr(args, "force_mono", False)

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
        sample_mode = getattr(args, "sample_mode", "smart-top") or "smart-top"
        probe_stride = max(1, int(getattr(args, "probe_stride", 2) or 2))
        target_indices = None
        probe_stats = None

        if sample_mode in ("smart-top", "smart-cluster") and limit > 0:
            import cv2
            cap_probe = cv2.VideoCapture(str(video_path))
            if not cap_probe.isOpened():
                cap_probe.release()
                die(format_video_decode_error(video_path, "probe_open"))
            v_total = int(cap_probe.get(cv2.CAP_PROP_FRAME_COUNT))
            cap_probe.release()

            if v_total > 0 and v_total <= limit:
                log(f"video has {v_total} frames <= limit {limit}: importing all frames without subsampling")
            else:
                effective_stride = probe_stride
                if v_total > 0 and v_total // effective_stride < limit:
                    effective_stride = max(1, v_total // limit)

                try:
                    scored = probe_video_seeing_profile(video_path, stride=effective_stride)
                except ValueError as exc:
                    die(str(exc))
                if scored:
                    all_vals = [s[1] for s in scored]
                    k_limit = min(limit, len(scored))

                    if sample_mode == "smart-top":
                        scored_sorted = sorted(scored, key=lambda x: x[1], reverse=True)
                        chosen = scored_sorted[:k_limit]
                        target_indices = sorted([x[0] for x in chosen])
                        chosen_scores = [x[1] for x in chosen]
                        log(f"smart-top selection complete: picked {len(target_indices)} frames (avg sharpness={np.mean(chosen_scores):.1f}, range=[{min(chosen_scores):.1f}, {max(chosen_scores):.1f}], frame span=[{target_indices[0]} - {target_indices[-1]}])")
                    elif sample_mode == "smart-cluster":
                        n_bins = 10
                        bin_size = max(1, len(scored) // n_bins)
                        per_bin_k = max(1, k_limit // n_bins)
                        chosen = []
                        for b in range(n_bins):
                            sub = scored[b * bin_size : (b + 1) * bin_size]
                            sub_sorted = sorted(sub, key=lambda x: x[1], reverse=True)
                            chosen.extend(sub_sorted[:per_bin_k])
                        if len(chosen) < k_limit:
                            remaining = [x for x in scored if x not in chosen]
                            remaining.sort(key=lambda x: x[1], reverse=True)
                            chosen.extend(remaining[: (k_limit - len(chosen))])
                        target_indices = sorted([x[0] for x in chosen[:k_limit]])
                        chosen_scores = [x[1] for x in chosen[:k_limit]]
                        log(f"smart-cluster selection complete: picked {len(target_indices)} frames across {n_bins} time windows (avg sharpness={np.mean(chosen_scores):.1f})")

                    probe_stats = {
                        "total_probed": len(scored),
                        "sample_mode": sample_mode,
                        "min_sharpness": float(np.min(all_vals)),
                        "p50_sharpness": float(np.percentile(all_vals, 50)),
                        "p90_sharpness": float(np.percentile(all_vals, 90)),
                        "max_sharpness": float(np.max(all_vals)),
                        "chosen_avg_sharpness": float(np.mean(chosen_scores)),
                        "chosen_frame_count": len(target_indices),
                    }

        start_frame = int(getattr(args, "start_frame", 0) or 0)
        try:
            video_info = unpack_video_to_fits(
                video_path=video_path,
                out_dir=work,
                seq_name=seq_name,
                limit=limit,
                force_mono=force_mono,
                debayer=video_debayer,
                start_frame=start_frame,
                target_indices=target_indices,
            )
        except ValueError as exc:
            die(str(exc))
        if probe_stats:
            video_info["seeing_probe"] = probe_stats
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
        )
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
    for i in range(1, total_frames + 1):
        seq_lines.append(f"I {i} 1")

    seq_path = work / f"{seq_name}.seq"
    seq_path.write_text("\n".join(seq_lines) + "\n", encoding="utf-8")

    orig_bit_depth = 16
    if is_video:
        orig_bit_depth = 8
    elif is_ser and ser_info:
        orig_bit_depth = ser_info.get("metadata", {}).get("pixel_depth", 16)

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

    # Choose candidate reference frame (center of sequence) to establish ROI
    mid_frame = image_files[len(image_files) // 2]
    mid_plane = _read_frame_plane(mid_frame["path"])
    roi = _locate_high_contrast_roi(mid_plane, roi_size=args.roi)
    rx0, ry0, rx1, ry1 = roi
    log(f"localized high-contrast lunar feature ROI: [{rx0}:{rx1}, {ry0}:{ry1}] ({rx1-rx0}x{ry1-ry0})")

    # Pass 1: compute phase shift relative to candidate frame
    raw_shifts = []
    for item in image_files:
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
        item["sharpness"] = sharpness

    # Exclude outliers with low phase correlation response (clouds, extreme shake)
    valid_items = [it for it in image_files if it["resp"] >= args.min_confidence and it["sharpness"] > 0]
    if not valid_items:
        die("no frames met the correlation confidence threshold; check data quality or lower --min-confidence")

    # Pick the sharpest frame as true reference
    best_item = max(valid_items, key=lambda it: it["sharpness"])
    ref_idx = best_item["index"]
    ref_dx = best_item["coarse_dx"]
    ref_dy = best_item["coarse_dy"]
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

    kept_indices, select_meta = _select_frames_by_quality(
        valid_items,
        total_count=len(image_files),
        select_mode=select_mode,
        keep_percent=keep_pct_arg,
        quality_threshold=quality_thresh,
        utility_alpha=u_alpha,
        utility_beta=u_beta,
    )

    for it in image_files:
        it["selected"] = it["index"] in kept_indices

    th_str = f"{select_meta['threshold']:.1f} ({select_meta.get('threshold_rel', 0)*100:.1f}% of ref)" if "threshold" in select_meta else "N/A"
    log(f"frame selection: mode='{select_meta['mode']}', cutoff={th_str}, keeping {len(kept_indices)}/{len(image_files)} best frames ({select_meta['kept_percent']}%)")

    # Inject R0 homography matrices & selection into Siril's .seq file
    new_s_line = (
        f"S '{seq_name}' {start_idx} {nb_images} {len(kept_indices)} {fixed_len} "
        f"{ref_idx - start_idx} 6 0 0 0"
    )

    new_seq_lines = [
        "#Siril sequence file. Generated by siril-moon-stacking with rigid homography registration",
        new_s_line,
        "L 3",
    ]
    for it in image_files:
        new_seq_lines.append(f"I {it['index']} {1 if it['selected'] else 0}")

    for it in image_files:
        new_seq_lines.append(f"R0 1.0 1.0 1.0 0 0.0 1 {it['homography']}")

    seq_path.write_text("\n".join(new_seq_lines) + "\n", encoding="utf-8")
    log(f"updated Siril sequence file: {seq_path}")

    ranking_data = {
        "sequence": seq_name,
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
    log(f"registration & ranking complete: data dumped to {work / 'ranking.json'}")


# -------------------------------------------------------------------- 3. stack

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

    if stack_method == "sum" or (stack_method == "auto" and is_8bit):
        stack_cmd = f"stack r_{seq_name} sum -filter-included -out={master.stem}"
        log(f"stacking policy: applying 'sum' stacking ({'forced by user' if stack_method == 'sum' else 'detected 8-bit source data'}) to physically expand dynamic range")
    else:
        stack_cmd = f"stack r_{seq_name} rej w {args.sigma[0]} {args.sigma[1]} -norm={args.norm} -filter-included -out={master.stem}"
        log(f"stacking policy: applying Winsorized rejection mean stacking (rej w) ({'forced by user' if stack_method == 'rej' else 'detected 16-bit+ source data'})")

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

    lines = [
        "requires 1.4.4",
        # Multithreaded subpixel resampling with strict clamping (no -noclamp) to avoid undershoot overflows
        f"seqapplyreg {seq_name} -framing={framing} -interp={interp} -filter-incl",
        f"{stack_cmd}{maximize_flag}",
        "exit",
    ]

    log(f"executing Siril seqapplyreg (framing={framing}, interp={interp}, mosaic_mode={mosaic_mode}, clamped) & stack pipeline...")
    receipt = run_siril_script(args.siril, lines, work, logs_dir / "02_align_stack.log", args.timeout)
    if receipt["exit_code"] != 0 or not master.exists():
        die(f"stacking failed (exit {receipt['exit_code']}); see {receipt['log']}")

    receipt["interp"] = interp
    receipt["framing"] = framing
    receipt["mosaic_mode"] = mosaic_mode
    dump_json(work / "stack_receipt.json", receipt)
    log(f"master stack generated successfully: {master} (interp={interp}, framing={framing})")

    # Clean up intermediate resampled frames (r_*.fit) to conserve disk space
    resampled_fits = list(work.glob(f"r_{seq_name}*.fit"))
    if resampled_fits:
        log(f"cleaning up {len(resampled_fits)} intermediate resampled frames (r_{seq_name}*.fit) to reclaim disk space...")
        for rf in resampled_fits:
            try:
                rf.unlink()
            except Exception:
                pass


# -------------------------------------------------------------- 4. postprocess

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
            return float(min(safe_ceiling, p_high * 0.50))

    # Close-up / full-frame moon without dark sky corners: return low percentile
    return max(0.0, p_low)


def _fit_lunar_limb_circle(lum_2d: np.ndarray) -> tuple[float, float, float, float] | None:
    """Fit a high-precision lunar physical circle using radial gradient inflection points.

    Instead of relying on thresholding (which captures diffuse atmospheric glare),
    this algorithm casts radial rays across full 360 degrees from the center and finds
    the point of maximum negative radial gradient (the true physical limb edge), then applies
    RANSAC circle fitting with sub-pixel precision.
    """
    lum_2d = np.ascontiguousarray(lum_2d, dtype=np.float32)
    H, W = lum_2d.shape
    p999 = float(np.percentile(lum_2d, 99.95))
    if p999 <= 1e-5:
        return None

    import cv2

    core_mask = (lum_2d > 0.25 * p999).astype(np.uint8)
    M = cv2.moments(core_mask)
    if M["m00"] == 0:
        return None
    cx_init = float(M["m10"] / M["m00"])
    cy_init = float(M["m01"] / M["m00"])

    dense_angles = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    r_samples = np.arange(min(H, W) * 0.15, max(H, W) * 0.85, 1.0, dtype=np.float32)

    limb_points = []
    for theta in dense_angles:
        xs = cx_init + r_samples * np.cos(theta)
        ys = cy_init + r_samples * np.sin(theta)
        valid = (xs >= 0) & (xs < W - 1) & (ys >= 0) & (ys < H - 1)
        if np.count_nonzero(valid) < 50:
            continue
        xs_val = xs[valid].astype(np.float32).reshape(-1, 1)
        ys_val = ys[valid].astype(np.float32).reshape(-1, 1)
        r_curr = r_samples[valid]

        vals = cv2.remap(lum_2d, xs_val, ys_val, cv2.INTER_LINEAR).flatten()
        grad = np.diff(vals)
        if grad.size == 0:
            continue
        min_idx = int(np.argmin(grad))
        if vals[min_idx] > 0.05 * p999 and grad[min_idx] < -0.005:
            edge_x = cx_init + float(r_curr[min_idx]) * np.cos(theta)
            edge_y = cy_init + float(r_curr[min_idx]) * np.sin(theta)
            limb_points.append([edge_x, edge_y])

    if len(limb_points) < 15:
        return None

    pts = np.array(limb_points, dtype=np.float64)
    N = len(pts)

    best_inliers = []
    rng = np.random.default_rng(42)
    for _ in range(200):
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
            inliers = np.where(dists < 2.0)[0]
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

    if not (0.15 * min(H, W) < R < 2.5 * max(H, W)) or res_std > 5.0:
        return None

    return xc, yc, R, res_std


def _infer_optical_parameters(
    hdr: fits.Header,
    data: np.ndarray,
    args: argparse.Namespace,
    mosaic_mode: str = "disc",
) -> dict:
    """Intelligently infer telescope optical parameters (pixel size, focal length, aperture).

    Combines FITS header metadata mining with subpixel lunar limb RANSAC circle
    fitting and astronomical angular diameter geometry to accurately estimate
    effective focal length (e.g. ~864mm from ~2095px lunar disc on 3.73um sensor).
    """
    # 1. Pixel size inference
    user_px = getattr(args, "pixel_size", None)
    if user_px is not None and user_px > 0:
        px_size = float(user_px)
        px_source = "user"
    else:
        hdr_px = None
        for k in ["XPIXSZ", "YPIXSZ", "PIXSIZE", "PIXSIZE1", "PIXEL_SZ", "PIXELSIZE"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_px = fval
                        px_source = f"FITS Header ({k})"
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_px is not None:
            px_size = hdr_px
        else:
            px_size = 3.73
            px_source = "default (3.73um)"

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
                        dia_source = f"FITS Header ({k})"
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_dia is not None:
            dia = hdr_dia
        else:
            dia = 80.0
            dia_source = "default (80.0mm)"

    # 3. Focal length inference
    user_fl = getattr(args, "focal", None)
    fl = None
    fl_source = None
    disc_info = None
    f_range = None

    if user_fl is not None and user_fl > 0:
        fl = float(user_fl)
        fl_source = "user"
    elif mosaic_mode != "tile":
        # Attempt geometric inversion from subpixel lunar disc fit
        is_3d = (data.ndim == 3)
        if is_3d:
            lum_2d = 0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]
        else:
            lum_2d = data

        fit_res = _fit_lunar_limb_circle(lum_2d)
        if fit_res is not None:
            xc, yc, R, res_std = fit_res
            D_px = 2.0 * R
            sensor_dia_mm = D_px * px_size * 1e-3  # mm on sensor plane

            # Astronomical lunar angular diameter constants:
            # Mean angular diameter: 31.07 arcmin = 0.517833 deg = 0.0090379 rad
            # Perigee (max): 33.50 arcmin = 0.558333 deg = 0.0097448 rad
            # Apogee (min): 29.40 arcmin = 0.490000 deg = 0.0085521 rad
            theta_mean = 0.009037905
            theta_max = 0.009744843
            theta_min = 0.008552113

            f_est = sensor_dia_mm / (2.0 * np.tan(theta_mean / 2.0))
            f_min = sensor_dia_mm / (2.0 * np.tan(theta_max / 2.0))
            f_max = sensor_dia_mm / (2.0 * np.tan(theta_min / 2.0))

            if 100.0 <= f_est <= 15000.0:
                fl = f_est
                f_range = [f_min, f_max]
                fl_source = f"geometric inversion (disc D={D_px:.1f}px, θ=31.1')"
                disc_info = {
                    "center": [round(xc, 1), round(yc, 1)],
                    "radius_px": round(R, 1),
                    "diameter_px": round(D_px, 1),
                    "res_std": round(res_std, 2),
                    "sensor_dia_mm": round(sensor_dia_mm, 3),
                    "angular_diam_arcmin": 31.07,
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
                        fl_source = f"FITS Header ({k})"
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_fl is not None:
            fl = hdr_fl
        else:
            fl = 400.0
            fl_source = "default (400.0mm)"

    f_ratio = fl / dia if dia > 0 else 0.0
    # Theoretical Airy radius in pixels for green light (550nm = 0.55um):
    # r_airy = 1.22 * lambda * f / (D * px_size)
    r_airy_px = (1.22 * 0.55 * fl) / (dia * px_size) if (dia > 0 and px_size > 0) else 0.0

    return {
        "pixel_size": round(px_size, 3),
        "pixel_size_source": px_source,
        "focal_length": round(fl, 1),
        "focal_source": fl_source,
        "focal_range": [round(f_range[0], 1), round(f_range[1], 1)] if f_range else None,
        "aperture": round(dia, 1),
        "aperture_source": dia_source,
        "f_ratio": round(f_ratio, 2),
        "airy_radius_px": round(r_airy_px, 2),
        "lunar_disc": disc_info,
    }


def _suppress_lunar_limb_glare(
    planes: np.ndarray,
    glare_mode: str = "auto",
) -> tuple[np.ndarray, dict]:
    """Suppress atmospheric and optical forward scattering glare outside lunar physical limb.

    Operates strictly in 32-bit linear space before non-linear MTF stretch.
    Leaves lunar surface (r <= R + 1px) 100% unaltered.
    Smoothly transitions over a narrow delta (2.5px to 8.0px) into deep space zero.
    """
    if glare_mode == "off":
        return planes, {"active": False}

    is_3d = (planes.ndim == 3)
    if is_3d:
        lum_2d = 0.299 * planes[0] + 0.587 * planes[1] + 0.114 * planes[2]
    else:
        lum_2d = planes
    H, W = lum_2d.shape

    fit_res = _fit_lunar_limb_circle(lum_2d)
    if fit_res is None:
        return planes, {"active": False, "reason": "circle_fit_failed"}

    xc, yc, R, res_std = fit_res

    if glare_mode == "aggressive":
        delta = 2.5
    elif glare_mode == "mild":
        delta = 8.0
    else:  # "auto"
        delta = 4.5

    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - xc)**2 + (yy - yc)**2)

    r0 = R + 1.0
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
    mid_val: float = 0.13,
    wb_mode: str = "gray-world",
    glare_mode: str = "auto",
    extinction_comp: str = "auto",
    locked_profile: dict | None = None,
    lock_wb: tuple[float, float] | None = None,
    lock_stretch: tuple[float, float] | None = None,
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
        net_planes, glare_meta = _suppress_lunar_limb_glare(net_planes, glare_mode=glare_mode)
        r_net, g_net, b_net = net_planes[0], net_planes[1], net_planes[2]
        res["glare_meta"] = glare_meta

    lum_approx = 0.299 * r_net + 0.587 * g_net + 0.114 * b_net
    p999_lum_raw = float(np.percentile(lum_approx, 99.95))
    if p999_lum_raw <= 1e-4:
        return res

    # Mask valid lunar surface: exclude dark background/shadows and overexposed peaks
    mask = (lum_approx > (0.05 * p999_lum_raw)) & (lum_approx < (0.95 * p999_lum_raw))
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
        # Provide +25% headroom to absorb subsequent deconvolution & wavelet peak energy without clipping
        hi_unified = max(p999_clean * 1.25, 0.01)

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
    })
    return res


def _render_deep_cine_mineral(
    lum_sharp: np.ndarray,
    color_balanced: np.ndarray,
    circle_meta: dict | None = None,
    fe_boost: float = 4.5,
    ti_boost: float = 5.5,
    gamma: float = 1.00,
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
            fit_res = _fit_lunar_limb_circle(lum_sharp)
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
        fade_val = np.clip((limb_dist - 4.0) / 14.0, 0.0, 1.0)
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
        space_cut = np.clip((limb_dist + 1.0) / 2.5, 0.0, 1.0)
        final_r *= space_cut
        final_g *= space_cut
        final_b *= space_cut

    # Flip vertically to match image row 0 at top convention
    rgb_fits = np.stack([final_r, final_g, final_b], axis=-1)
    rgb_jpg = rgb_fits[::-1, :, :]
    bgr_jpg = cv2.cvtColor((rgb_jpg * 255.0).astype(np.uint8), cv2.COLOR_RGB2BGR)
    return bgr_jpg
    return bgr_jpg


def _compute_edge_ringing_damping_mask(
    lum_base: np.ndarray,
    contrast_threshold: float = 1.0,
    return_positive: bool = False,
) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
    """Compute subpixel edge ringing damping masks for lunar step edges.

    Identifies steep luminance cliffs (crater rims, terminator, limb) and localizes:
      1. Negative undershoot valleys (shadow side) causing artificial dark halos.
      2. Positive overshoot peaks (bright side / limb inner band) causing unnatural bright glare rims.
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
    smooth = cv2.GaussianBlur(norm, (7, 7), 1.5)

    # 3. Step edge relative contrast (prevents activation on noisy flat maria)
    rel_contrast = grad / (smooth + 0.02)
    is_step_edge = rel_contrast > contrast_threshold

    # 4. Shadow side identification (negative undershoot: L < smooth)
    is_shadow_side = norm < (smooth - 0.005)
    hazard_neg = grad * (is_step_edge & is_shadow_side).astype(np.float32)

    # 5. Morphological dilation for shadow side (2-3 px outwards into shadow)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    dilated_neg = cv2.dilate(hazard_neg, kernel, iterations=2)
    damp_mask_neg = cv2.GaussianBlur(dilated_neg, (5, 5), 1.2)

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
    dilated_pos = cv2.dilate(hazard_pos, kernel, iterations=2)
    damp_mask_pos = cv2.GaussianBlur(dilated_pos, (5, 5), 1.2)

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

    # Erode valid_mask by 5px to eliminate the celestial limb edge step
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    interior_mask = cv2.erode(valid_mask.astype(np.uint8), kernel).astype(bool)
    if np.count_nonzero(interior_mask) < 100:
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
      3. Deconvolution status (applies MTF discount factor if Airy PSF deconvolution was run)
      4. Interpolation filter properties (Bicubic vs Bilinear)
      5. Anti-redundancy defense: automatically bypasses USM unsharp mask when
         multi-scale wavelets or deconvolution are active, eliminating artificial halos.
      6. Edge undershoot anti-ringing damping factor calculation.
    """
    lum_2d = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum_2d, 99.95))
    moon_mask = (lum_2d > 0.05 * p999) & (lum_2d < 0.95 * p999)
    valid_pixels = lum_2d[moon_mask]

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
        w_coeffs = [1.00, 1.00, 1.00, 1.00, 1.00, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mellow":
        w_coeffs = [1.01, 1.02, 1.03, 1.01, 1.00, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mild":
        w_coeffs = [1.01, 1.03, 1.04, 1.02, 1.00, 1.00]
        clahe_clip = 0.08
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "crisp":
        w_coeffs = [1.04, 1.10, 1.12, 1.08, 1.00, 1.00]
        clahe_clip = 0.30
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    else:  # "auto" -> Scheme B: Balanced Natural Baseline
        deconv_discount = 0.55 if has_deconv else 1.0
        noise_penalty = float(np.clip(1.0 - (sigma_noise / max(p999 * 0.005, 1e-6)), 0.4, 1.0))
        # Keep Layer 1 gain at 1.00 when deconvolution is active or noise floor is detectable
        # to strictly avoid crunch / salt-and-pepper shot noise amplification
        l1_base = 0.00 if (has_deconv or sigma_noise > 0.0006) else (0.02 if interp_used == "cu" else 0.04)
        w1 = 1.0 + l1_base * deconv_discount * noise_penalty
        w2 = 1.0 + 0.05 * deconv_discount * noise_penalty
        w3 = 1.0 + 0.07 * deconv_discount * noise_penalty
        w4 = 1.0 + 0.03 * deconv_discount * noise_penalty
        w_coeffs = [round(w1, 2), round(w2, 2), round(w3, 2), round(w4, 2), 1.00, 1.00]
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

    wrecons_cmd = f"wrecons {w_coeffs[0]:.2f} {w_coeffs[1]:.2f} {w_coeffs[2]:.2f} {w_coeffs[3]:.2f} {w_coeffs[4]:.2f} {w_coeffs[5]:.2f}"
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
        "noise_penalty": noise_penalty if sharp_mode == "auto" else 1.0,
        "deconv_discount": deconv_discount if sharp_mode == "auto" else 1.0,
        "anti_ringing": anti_ringing,
        "damping_strength": rec_damping,
    }


def cmd_postprocess(args) -> None:
    from astropy.io import fits
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

    mosaic_mode = getattr(args, "mosaic_mode", "disc")
    optics = _infer_optical_parameters(hdr, d, args, mosaic_mode=mosaic_mode)
    px_size = optics["pixel_size"]
    fl = optics["focal_length"]
    dia = optics["aperture"]
    deconv_method = getattr(args, "deconv", "sb")

    if optics.get("lunar_disc"):
        disc = optics["lunar_disc"]
        log(f"optical inference: fitted lunar disc D={disc['diameter_px']:.1f}px (R={disc['radius_px']:.1f}px, res={disc['res_std']:.2f}px)")
        log(f"optical inference: sensor diameter={disc['sensor_dia_mm']:.2f}mm -> inferred focal={fl:.0f}mm (range {disc['focal_range'][0]:.0f}~{disc['focal_range'][1]:.0f}mm)")
    log(f"optical setup: fl={fl:.1f}mm ({optics['focal_source']}), dia={dia:.1f}mm ({optics['aperture_source']}), px={px_size:.2f}um ({optics['pixel_size_source']})")
    log(f"optical diffraction: F/{optics['f_ratio']:.1f}, Airy radius = {optics['airy_radius_px']:.2f}px")

    deconv_lines = []
    if deconv_method == "sb":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks=25 -savepsf=psf_airy.fit",
            "sb -loadpsf=psf_airy.fit -iters=2 -alpha=2000",
        ]
        log(f"deconvolution: Split Bregman with physical Airy PSF (dia={dia:.1f}mm, fl={fl:.1f}mm, px={px_size:.2f}um)")
    elif deconv_method == "wiener":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks=25 -savepsf=psf_airy.fit",
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
    mid_val = getattr(args, "midtone", 0.42)

    sat_base = float(getattr(args, "sat_base", 0.3))
    sat_fe = float(getattr(args, "sat_fe", 0.8))
    sat_ti = float(getattr(args, "sat_ti", 0.8))
    sat_bg = float(getattr(args, "sat_bg_factor", 1.2))

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
            mid_val=mid_val,
            wb_mode=wb_mode,
            glare_mode=glare_mode,
            extinction_comp=extinction_comp,
            locked_profile=locked_profile,
            lock_wb=lock_wb,
            lock_stretch=lock_stretch,
        )
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
            log(f"calibrated channels (LOCKED hi_unified={wb['hi_lum']:.5f}, midtone={mid_val})")
        else:
            log(f"calibrated channels (bg=[{wb['bg_r']:.5f}, {wb['bg_g']:.5f}, {wb['bg_b']:.5f}], hi=[{wb['hi_r']:.5f}, {wb['hi_g']:.5f}, {wb['hi_b']:.5f}], midtone={mid_val})")
        lum_for_sharp = wb["lum_data"]
    else:
        plane = d if d.ndim == 2 else d[0]
        bg_val = _estimate_pedestal(plane)
        p999_val = float(np.percentile(plane, 99.95))
        hi_val = max(p999_val * 1.25, bg_val + 0.01)
        if lock_stretch:
            bg_val, hi_val = lock_stretch[0], lock_stretch[1]
            log(f"monochrome stretch ceiling LOCKED: bg={bg_val:.5f}, hi={hi_val:.5f}")
        elif locked_profile and "histogram" in locked_profile:
            hist_prof = locked_profile["histogram"]
            hi_val = float(hist_prof.get("hi_lum", hist_prof.get("hi_unified", hi_val)))
            bg_val = float(hist_prof.get("bg_lum", bg_val))
            log(f"monochrome stretch ceiling LOCKED from profile: bg={bg_val:.5f}, hi={hi_val:.5f}")
        lum_for_sharp = plane

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

    log(f"adaptive organic sharpening: mode='{sharp_mode}', contrast_ratio={sharp_info['contrast_ratio']:.2f}, mare_noise={sharp_info['sigma_noise']:.6f}")
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
            if wb_mode == "gray-world" and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                # --- Stage 1: Luminance High-Frequency Processing ---
                "load moon_lum",
                *deconv_lines,
                f"mtf {wb['bg_lum']:.6f} {mid_val:.2f} {wb['hi_lum']:.6f}",
                "save moon_lum_base",
                "wavelet 5 2",
                wrecons_cmd,
                *clahe_lines,
                *unsharp_lines,
                "save moon_lum_sharp",
                # --- Stage 2: Chrominance Balancing & Saturation ---
                f"load {color_target}",
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_color_clean",
                *sat_lines,
                "save moon_color_sat",
                # --- Stage 3: LRGB Composite ---
                # 1. Natural LRGB Version (sharp lum + neutral balanced color)
                "rgbcomp -lum=moon_lum_sharp moon_color_clean -out=moon_natural_master",
                "load moon_natural_master",
                "savetif moon_natural -astro",
                "savejpg moon_natural 95",
                # 2. Mineral LRGB Version (sharp lum + boosted mineral color)
                "rgbcomp -lum=moon_lum_sharp moon_color_sat -out=moon_mineral_master",
                "load moon_mineral_master",
                "savejpg moon_mineral 95",
                "exit",
            ]
        else:
            # Legacy monolithic RGB pipeline
            log("executing legacy monolithic RGB wavelet pipeline...")
            color_target = master.stem
            if wb_mode == "gray-world" and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                f"load {color_target}",
                *deconv_lines,
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_base",
                "wavelet 5 2",
                wrecons_cmd,
                *clahe_lines,
                *unsharp_lines,
                "savetif moon_natural -astro",
                "savejpg moon_natural 95",
                *sat_lines,
                "savejpg moon_mineral 95",
                "exit",
            ]

    else:
        # Monochrome pipeline
        log("executing monochrome lunar detail pipeline...")
        plane = d if d.ndim == 2 else d[0]
        bg_val = _estimate_pedestal(plane)
        p999_val = float(np.percentile(plane, 99.95))
        hi_val = max(p999_val * 1.10, bg_val + 0.01)

        lines = [
            "requires 1.4.4",
            f"load {master.name}",
            *deconv_lines,
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
            "save moon_base",
            "wavelet 5 2",
            wrecons_cmd,
            *clahe_lines,
            *unsharp_lines,
            "savetif moon_natural -astro",
            "savejpg moon_natural 95",
            "exit",
        ]

    receipt = run_siril_script(args.siril, lines, work, logs_dir / "03_postprocess.log", args.timeout)
    if receipt["exit_code"] != 0:
        die(f"postprocessing failed (exit {receipt['exit_code']}); see {receipt['log']}")

    # Apply subpixel anti-ringing damping to eliminate edge undershoot dark halos
    strength = sharp_info["damping_strength"]
    if anti_ringing != "off" and strength > 0.0:
        import cv2

        if is_rgb and mineral_mode == "lrgb":
            lum_base_path = work / "moon_lum_base.fit"
            lum_sharp_path = work / "moon_lum_sharp.fit"
            if lum_base_path.exists() and lum_sharp_path.exists():
                with fits.open(lum_base_path, memmap=False) as hd_base:
                    lum_base_data = hd_base[0].data
                with fits.open(lum_sharp_path, memmap=False) as hd_sharp:
                    lum_sharp_data = hd_sharp[0].data
                    sharp_hdr = hd_sharp[0].header

                damp_mask_neg, damp_mask_pos = _compute_edge_ringing_damping_mask(lum_base_data, return_positive=True)
                dhr_raw = _measure_dark_halo_ratio(lum_base_data, lum_sharp_data, damp_mask_neg)
                lum_damped = _apply_anti_ringing_damping(
                    lum_base_data,
                    lum_sharp_data,
                    damp_mask_neg,
                    strength=strength,
                    pos_mask=damp_mask_pos,
                    pos_strength=0.75,
                    soft_knee=True,
                )
                dhr_damped = _measure_dark_halo_ratio(lum_base_data, lum_damped, damp_mask_neg)

                fits.writeto(lum_sharp_path, lum_damped, header=sharp_hdr, overwrite=True)
                reduction = (1.0 - dhr_damped / max(dhr_raw, 1e-6)) * 100.0 if dhr_raw > 1e-5 else 0.0
                log(f"anti-ringing damping applied (LRGB): mode='{anti_ringing}', strength={strength:.2f}, DHR={dhr_raw:.4f} -> {dhr_damped:.4f} (reduced {reduction:.1f}%)")
                receipt["anti_ringing"] = {
                    "mode": anti_ringing,
                    "strength": strength,
                    "dhr_raw": dhr_raw,
                    "dhr_damped": dhr_damped,
                    "reduction_percent": reduction,
                }

                # Refresh moon_natural.tif and moon_natural.jpg with damped lum and clean limb
                clean_color_path = work / "moon_color_clean.fit"
                if clean_color_path.exists() and (work / "moon_natural.tif").exists():
                    with fits.open(clean_color_path, memmap=False) as hd_clean:
                        c_clean = hd_clean[0].data
                    lum_clean = 0.299 * c_clean[0] + 0.587 * c_clean[1] + 0.114 * c_clean[2]
                    scale = lum_damped / np.maximum(lum_clean, 1e-6)
                    rgb_damped = np.clip(c_clean * scale, 0.0, 1.0)

                    # Soft-knee highlight protection (consistent with deep-cine mineral tone)
                    knee = 0.75
                    above_knee = rgb_damped > knee
                    if np.any(above_knee):
                        rgb_damped[above_knee] = knee + (1.0 - knee) * np.tanh((rgb_damped[above_knee] - knee) / (1.0 - knee))

                    # Smooth limb desaturation to eliminate residual chromatic fringe
                    if wb.get("glare_meta") and wb["glare_meta"].get("active"):
                        gm = wb["glare_meta"]
                        R_d = float(gm["radius"])
                        cx_d, cy_d = float(gm["center_x"]), float(gm["center_y"])
                        yy_d, xx_d = np.mgrid[0:c_clean.shape[1], 0:c_clean.shape[2]]
                        r_d = np.sqrt((xx_d - cx_d)**2 + (yy_d - cy_d)**2)
                        limb_dist_d = R_d - r_d
                        fade_val_d = np.clip((limb_dist_d - 4.0) / 14.0, 0.0, 1.0)
                        limb_fade_d = fade_val_d * fade_val_d * (3.0 - 2.0 * fade_val_d)
                        for ch in range(3):
                            rgb_damped[ch] = lum_damped + (rgb_damped[ch] - lum_damped) * limb_fade_d
                        space_gate = np.clip((limb_dist_d + 1.0) / 2.5, 0.0, 1.0)
                        rgb_damped *= space_gate

                    rgb_screen = rgb_damped[:, ::-1, :]
                    bgr_16 = np.transpose((rgb_screen[[2, 1, 0]] * 65535.0).astype(np.uint16), (1, 2, 0))
                    bgr_8 = np.transpose((rgb_screen[[2, 1, 0]] * 255.0).astype(np.uint8), (1, 2, 0))
                    cv2.imwrite(str(work / "moon_natural.tif"), bgr_16)
                    cv2.imwrite(str(work / "moon_natural.jpg"), bgr_8, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        elif not is_rgb:
            base_path = work / "moon_base.fit"
            sharp_path = work / "moon_sharp.fit"
            if base_path.exists() and sharp_path.exists():
                with fits.open(base_path, memmap=False) as hd_base:
                    b_data = hd_base[0].data
                with fits.open(sharp_path, memmap=False) as hd_sharp:
                    s_data = hd_sharp[0].data
                    s_hdr = hd_sharp[0].header

                damp_mask_neg, damp_mask_pos = _compute_edge_ringing_damping_mask(b_data, return_positive=True)
                dhr_raw = _measure_dark_halo_ratio(b_data, s_data, damp_mask_neg)
                damped_mono = _apply_anti_ringing_damping(
                    b_data,
                    s_data,
                    damp_mask_neg,
                    strength=strength,
                    pos_mask=damp_mask_pos,
                    pos_strength=0.75,
                    soft_knee=True,
                )
                dhr_damped = _measure_dark_halo_ratio(b_data, damped_mono, damp_mask_neg)

                fits.writeto(sharp_path, damped_mono, header=s_hdr, overwrite=True)
                reduction = (1.0 - dhr_damped / max(dhr_raw, 1e-6)) * 100.0 if dhr_raw > 1e-5 else 0.0
                log(f"anti-ringing damping applied (mono): mode='{anti_ringing}', strength={strength:.2f}, DHR={dhr_raw:.4f} -> {dhr_damped:.4f} (reduced {reduction:.1f}%)")
                receipt["anti_ringing"] = {
                    "mode": anti_ringing,
                    "strength": strength,
                    "dhr_raw": dhr_raw,
                    "dhr_damped": dhr_damped,
                    "reduction_percent": reduction,
                }
                mono_screen = damped_mono[::-1, :]
                m16 = (np.clip(mono_screen, 0.0, 1.0) * 65535.0).astype(np.uint16)
                m8 = (np.clip(mono_screen, 0.0, 1.0) * 255.0).astype(np.uint8)
                cv2.imwrite(str(work / "moon_natural.tif"), m16)
                cv2.imwrite(str(work / "moon_natural.jpg"), m8, [int(cv2.IMWRITE_JPEG_QUALITY), 95])

    mineral_style = getattr(args, "mineral_style", "deep-cine")
    fe_boost = float(getattr(args, "mineral_fe_boost", 4.5))
    ti_boost = float(getattr(args, "mineral_ti_boost", 5.5))
    gamma = float(getattr(args, "mineral_gamma", 1.00))
    if is_rgb and mineral_mode == "lrgb" and mineral_style == "deep-cine":
        lum_sharp_path = work / "moon_lum_sharp.fit"
        color_bal_path = work / "moon_color_balanced.fit"
        if lum_sharp_path.exists() and color_bal_path.exists():
            with fits.open(lum_sharp_path, memmap=False) as hdul_l:
                l_data = hdul_l[0].data
            with fits.open(color_bal_path, memmap=False) as hdul_c:
                c_data = hdul_c[0].data

            c_meta = wb.get("glare_meta")
            if mosaic_mode == "tile":
                c_meta = dict(c_meta) if c_meta else {}
                c_meta["is_tile"] = True
                c_meta["active"] = False

            import cv2
            import shutil

            deep_mineral_bgr = _render_deep_cine_mineral(
                l_data,
                c_data,
                circle_meta=c_meta,
                fe_boost=fe_boost,
                ti_boost=ti_boost,
                gamma=gamma,
            )

            # Backup default Siril mineral output as moon_mineral_natural.jpg
            legacy_mineral_path = work / "moon_mineral.jpg"
            if legacy_mineral_path.exists():
                shutil.copy2(legacy_mineral_path, work / "moon_mineral_natural.jpg")

            # Write deep-cine mineral JPG
            cv2.imwrite(str(work / "moon_mineral.jpg"), deep_mineral_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            log(f"deep-cine mineral moon rendered: Fe terracotta red (x{fe_boost:.1f}) + Ti cobalt blue (x{ti_boost:.1f}), bilateral chroma smoothing, shadow/ray rolloff")

    receipt["mosaic_mode"] = mosaic_mode
    dump_json(work / "postprocess_receipt.json", receipt)

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
        "profile_locked": active_profile["locked"],
        "calibration_profile": active_profile,
        "products": {
            "natural_tif": str(work / "moon_natural.tif"),
            "natural_jpg": str(work / "moon_natural.jpg"),
            "mineral_jpg": str(work / "moon_mineral.jpg") if (work / "moon_mineral.jpg").exists() else None,
        },
    }
    # Visual finishing: Orientation rotation & 1:1 Square close-up export
    rotate_deg = int(getattr(args, "rotate", 0) or 0)
    square_crop = getattr(args, "square_crop", True)

    product_files = [
        work / "moon_natural.tif",
        work / "moon_natural.jpg",
        work / "moon_mineral.jpg",
        work / "moon_mineral_natural.jpg",
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

    # 2. Export 1:1 Square Close-Up Master
    if square_crop and (work / "moon_natural.jpg").exists():
        nat_sample = cv2.imread(str(work / "moon_natural.jpg"), cv2.IMREAD_UNCHANGED)
        if nat_sample is not None:
            sh, sw = nat_sample.shape[:2]
            gray_s = cv2.cvtColor(nat_sample, cv2.COLOR_BGR2GRAY) if nat_sample.ndim == 3 else nat_sample
            lunar_mask = gray_s > 15
            ys, xs = np.nonzero(lunar_mask)
            if len(xs) > 100:
                c_x, c_y = int(round(xs.mean())), int(round(ys.mean()))
                r_est = max(c_x - xs.min(), xs.max() - c_x, c_y - ys.min(), ys.max() - c_y)
                # Give ~35% deep space margin around lunar disk
                half_box = int(round(r_est * 1.35))
                half_box = min(half_box, c_x, sw - c_x, c_y, sh - c_y)
                x0, x1 = c_x - half_box, c_x + half_box
                y0, y1 = c_y - half_box, c_y + half_box

                for pf in product_files:
                    if pf.exists():
                        sq_name = pf.stem + "_square" + pf.suffix
                        p_img = cv2.imread(str(pf), cv2.IMREAD_UNCHANGED)
                        if p_img is not None:
                            sq_crop = p_img[y0:y1, x0:x1]
                            cv2.imwrite(str(work / sq_name), sq_crop)
                log(f"square close-up master exported: 1:1 crop box [{x0}:{x1}, {y0}:{y1}] ({half_box*2}x{half_box*2}px, center=({c_x}, {c_y}))")

    dump_json(work / "mosaic_tile_info.json", tile_info)
    log(f"mosaic tile metadata exported to {work / 'mosaic_tile_info.json'}")
    log(f"postprocessing complete! Products in {work}:")
    log(f"  - Natural master TIFF: {work / 'moon_natural.tif'}")
    log(f"  - Natural JPG:         {work / 'moon_natural.jpg'}")
    if (work / "moon_mineral.jpg").exists():
        log(f"  - Mineral Moon JPG:    {work / 'moon_mineral.jpg'}")
        if (work / "moon_mineral_natural.jpg").exists():
            log(f"  - Natural Mineral JPG: {work / 'moon_mineral_natural.jpg'}")
    if (work / "moon_natural_square.jpg").exists():
        log(f"  - Square Close-up JPG: {work / 'moon_natural_square.jpg'}")


# ------------------------------------------------------------------- 5. verify

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

    report = {
        "master": str(master_path),
        "shape": list(master_data.shape),
        "bitpix": m_hdr.get("BITPIX"),
        "min": float(master_data.min()),
        "max": float(master_data.max()),
        "mean": float(master_data.mean()),
    }

    mosaic_mode = getattr(args, "mosaic_mode", None)
    tile_json = work / "mosaic_tile_info.json"
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

    # Background noise in corner
    bg_patch = master_data[:, :150, :150] if master_data.ndim == 3 else master_data[:150, :150]
    report["background_noise_std"] = float(bg_patch.std())

    # Measure surface sharpness on master vs single frame
    if rank_json.exists():
        r_info = load_json(rank_json)
        ref_idx = r_info.get("reference_index")
        report["reference_frame"] = ref_idx
        report["kept_frames"] = r_info.get("kept_frames")

    # Measure Edge Undershoot Dark Halo Ratio (DHR)
    lum_base_path = work / "moon_lum_base.fit"
    lum_sharp_path = work / "moon_lum_sharp.fit"
    if not lum_base_path.exists():
        lum_base_path = work / "moon_base.fit"
        lum_sharp_path = work / "moon_sharp.fit"

    if lum_base_path.exists() and lum_sharp_path.exists():
        try:
            with fits.open(lum_base_path, memmap=False) as h_b:
                b_dat = h_b[0].data
            with fits.open(lum_sharp_path, memmap=False) as h_s:
                s_dat = h_s[0].data
            d_mask = _compute_edge_ringing_damping_mask(b_dat)
            dhr_val = _measure_dark_halo_ratio(b_dat, s_dat, d_mask)
            report["dark_halo_ratio"] = dhr_val
            if dhr_val < 0.015:
                status = "EXCELLENT (artifact-free)"
            elif dhr_val < 0.035:
                status = "GOOD (controlled)"
            else:
                status = "WARNING (edge ringing detected)"
            report["dark_halo_status"] = status

            # Measure Chalky Saturation Index (CSI)
            csi_val = _measure_chalky_saturation_index(s_dat)
            report["chalky_saturation_index"] = csi_val
            if csi_val < 0.010:
                csi_status = "EXCELLENT (highlight dynamic retained)"
            elif csi_val < 0.025:
                csi_status = "GOOD (controlled highlights)"
            else:
                csi_status = "WARNING (highlight chalkiness / over-saturation)"
            report["chalky_status"] = csi_status

            # Measure Gradient Kurtosis Metric (GKM)
            gkm_val = _measure_gradient_kurtosis(s_dat)
            report["gradient_kurtosis"] = gkm_val
            if gkm_val < 6.0:
                gkm_status = "ORGANIC (natural smooth texture)"
            elif gkm_val < 14.0:
                gkm_status = "CRISP (high detail)"
            else:
                gkm_status = "WARNING (brittle / crunchy texture detected)"
            report["brittleness_status"] = gkm_status
        except Exception as exc:
            report["quality_metric_error"] = str(exc)

    dump_json(work / "verify_report.json", report)
    log("verification summary:")
    log(f"  Mosaic mode: {report['mosaic_mode']}")
    if report.get("profile_locked"):
        log(f"  Calibration Profile: LOCKED (source={report.get('lock_source')}, hi_lum={report.get('profile_hi_lum')})")
    elif "profile_locked" in report:
        log(f"  Calibration Profile: ANCHOR MASTER (unlocked baseline, hi_lum={report.get('profile_hi_lum')})")
    if report.get("focal_length"):
        fl_v = float(report["focal_length"])
        dia_v = float(report.get("aperture") or 80.0)
        px_v = float(report.get("pixel_size") or 3.73)
        f_rat = fl_v / dia_v if dia_v else 0.0
        airy_r = (1.22 * 0.55 * fl_v) / (dia_v * px_v) if (dia_v and px_v) else 0.0
        opt_src = report.get("optical_inference", {}).get("focal_source", "manual") if report.get("optical_inference") else "manual"
        log(f"  Optical Setup: fl={fl_v:.1f}mm, dia={dia_v:.1f}mm (F/{f_rat:.1f}, Airy r={airy_r:.2f}px, source='{opt_src}')")
    log(f"  Master dimensions: {report['shape']} (BITPIX={report['bitpix']})")
    log(f"  Pixel range: [{report['min']:.4f}, {report['max']:.4f}] (mean={report['mean']:.4f})")
    log(f"  Background noise std: {report['background_noise_std']:.6f}")
    if "extinction_compensation" in report:
        ec = report["extinction_compensation"]
        log(f"  Atmospheric Extinction: COMPENSATED (zenith={ec.get('zenith_angle_deg')}°, B-grad={ec.get('amp_b', 0)*100:.1f}%, conf={ec.get('confidence')})")
    if "dark_halo_ratio" in report:
        log(f"  Dark Halo Ratio (DHR): {report['dark_halo_ratio']:.4f} -> [{report.get('dark_halo_status', 'N/A')}]")
    if "chalky_saturation_index" in report:
        log(f"  Chalky Saturation (CSI): {report['chalky_saturation_index']:.4f} -> [{report.get('chalky_status', 'N/A')}]")
    if "gradient_kurtosis" in report:
        log(f"  Gradient Kurtosis (GKM): {report['gradient_kurtosis']:.2f} -> [{report.get('brittleness_status', 'N/A')}]")
    if (work / "moon_natural.jpg").exists():
        log(f"  [OK] moon_natural.jpg ({round((work / 'moon_natural.jpg').stat().st_size / 1024)} KB)")
    if (work / "moon_mineral.jpg").exists():
        log(f"  [OK] moon_mineral.jpg ({round((work / 'moon_mineral.jpg').stat().st_size / 1024)} KB)")


# ---------------------------------------------------------------------- 6. all

def cmd_all(args) -> None:
    cmd_import(args)
    cmd_register(args)
    cmd_stack(args)
    cmd_postprocess(args)
    cmd_verify(args)


# -------------------------------------------------------------------- 7. probe

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
    a.add_argument("--limit", type=int, default=0, help="Limit number of frames to import (0=all)")
    a.add_argument("--sample-mode", default="smart-top", choices=["smart-top", "smart-cluster", "window", "head"],
                   help="Frame sampling strategy for video containers: 'smart-top' (global sharpness scan & top-K extraction, default), 'smart-cluster' (temporal windowed top-K), 'window' (consecutive window starting at --start-frame), 'head' (first N frames)")
    a.add_argument("--probe-stride", type=int, default=2,
                   help="Frame subsampling stride for fast seeing profile scan (default: 2)")
    a.add_argument("--start-frame", type=int, default=0, help="Starting frame index in video container (default: 0)")
    a.add_argument("--ser-debayer", action=argparse.BooleanOptionalAction, default=True, help="Debayer Bayer SER frames to RGB FITS (default: True)")
    a.add_argument("--force-mono", action="store_true", help="Force extracting video as 1-channel monochrome FITS")
    a.add_argument("--video-debayer", default="auto", choices=["auto", "bggr", "rggb", "grbg", "gbrg", "none"],
                   help="Demosaicing for RAW Bayer video containers (e.g. Seestar RAW.avi): 'auto' (detects Bayer grid, defaults to BGGR), 'bggr', 'rggb', or 'none'")
    a.set_defaults(func=cmd_import)

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
                   help="Stacking method: 'auto' (sum for 8-bit video/SER, rej w for 16-bit+), 'sum', or 'rej'")
    a.set_defaults(func=cmd_stack)

    a = sub.add_parser("postprocess")
    a.add_argument("--work", required=True)
    a.add_argument("--master", default="moon_master.fit")
    a.add_argument("--deconv", default="sb", choices=["sb", "wiener", "rl", "none"], help="Deconvolution method (sb=Split Bregman, wiener, rl, none)")
    a.add_argument("--no-adc", action="store_true", help="Disable Atmospheric Dispersion Correction (RGB channel alignment)")
    a.add_argument("--midtone", type=float, default=0.42, help="MTF midtone stretch value for natural lunar albedo dynamics (default: 0.42)")
    a.add_argument("--wavelet-l1", type=float, default=None, help="Layer 1 wavelet gain (default: auto adaptive)")
    a.add_argument("--clahe-clip", type=float, default=None, help="CLAHE clip limit (default: auto dynamic, set <=0 to bypass CLAHE)")
    a.add_argument("--sharp-mode", default="auto", choices=["auto", "mellow", "mild", "crisp", "none"],
                   help="Sharpening mode: 'auto' (balanced natural organic baseline, default), 'mellow' (ultra-soft optical view, no CLAHE), 'mild' (subtle natural), 'crisp' (classic), 'none' (bypass)")
    a.add_argument("--unsharp", type=float, default=None, help="USM unsharp mask amount (default: auto bypassed when wavelets/deconv active)")
    a.add_argument("--anti-ringing", default="auto", choices=["auto", "off", "mild", "aggressive"],
                   help="Edge undershoot dark ringing suppression mode: 'auto' (adaptive damping, default), 'mild' (subtle), 'aggressive' (strong), 'off' (bypass)")
    a.add_argument("--damping-factor", type=float, default=None,
                   help="Manual anti-ringing damping factor (0.0 to 1.0, overrides preset if specified)")
    a.add_argument("--aperture", type=float, default=None, help="Telescope aperture in mm (for Airy PSF, default: auto inferred/80.0)")
    a.add_argument("--focal", type=float, default=None, help="Telescope focal length in mm (for Airy PSF, default: auto inferred from lunar disc/header)")
    a.add_argument("--pixel-size", type=float, default=None, help="Sensor pixel size in microns (for Airy PSF, default: auto from FITS header/3.73)")
    a.add_argument("--mineral-mode", default="lrgb", choices=["lrgb", "legacy"],
                   help="Mineral moon processing pipeline: 'lrgb' (Luminance/Chrominance separation, clean details, default) or 'legacy' (monolithic RGB wavelet)")
    a.add_argument("--white-balance", default="gray-world", choices=["gray-world", "legacy"],
                   help="Color balance mode: 'gray-world' (neutral lunar albedo baseline, default) or 'legacy' (per-channel percentile stretch)")
    a.add_argument("--sat-fe", type=float, default=0.8, help="Mineral saturation boost for Fe-rich terrain (orange-yellow hue 1, default: 0.8)")
    a.add_argument("--sat-ti", type=float, default=0.8, help="Mineral saturation boost for Ti-rich basalt (cyan-blue hues 3 & 4, default: 0.8)")
    a.add_argument("--sat-base", type=float, default=0.3, help="Foundation base saturation boost across all hues (default: 0.3)")
    a.add_argument("--sat-bg-factor", type=float, default=1.2, help="Background noise saturation suppression threshold factor (default: 1.2)")
    a.add_argument("--glare-suppress", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Lunar limb forward scattering glare suppression mode: 'auto' (4.5px falloff, default), 'mild' (8.0px), 'aggressive' (2.5px), 'off' (bypass)")
    a.add_argument("--extinction-comp", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Atmospheric extinction gradient compensation: 'auto' (adaptive threshold, default), 'mild' (50% strength), 'aggressive', 'off' (bypass)")
    a.add_argument("--mineral-style", default="deep-cine", choices=["deep-cine", "natural"],
                   help="Mineral moon aesthetic style: 'deep-cine' (deep matte basalt tone, bilateral chroma smoothing, terracotta/cobalt pure boost, shadow/ray rolloff, default) or 'natural' (classic subtle saturation)")
    a.add_argument("--mineral-fe-boost", type=float, default=4.5, help="Deep-cine saturation boost for Fe-rich terrain (terracotta peach, default: 4.5)")
    a.add_argument("--mineral-ti-boost", type=float, default=5.5, help="Deep-cine saturation boost for Ti-rich basalt (azure denim blue, default: 5.5)")
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
    a.add_argument("--limit", type=int, default=0, help="Limit number of frames to import (0=all)")
    a.add_argument("--sample-mode", default="smart-top", choices=["smart-top", "smart-cluster", "window", "head"],
                   help="Frame sampling strategy for video containers: 'smart-top' (global sharpness scan & top-K extraction, default), 'smart-cluster' (temporal windowed top-K), 'window' (consecutive window starting at --start-frame), 'head' (first N frames)")
    a.add_argument("--probe-stride", type=int, default=2,
                   help="Frame subsampling stride for fast seeing profile scan (default: 2)")
    a.add_argument("--start-frame", type=int, default=0, help="Starting frame index in video container (default: 0)")
    a.add_argument("--ser-debayer", action=argparse.BooleanOptionalAction, default=True, help="Debayer Bayer SER frames to RGB FITS (default: True)")
    a.add_argument("--force-mono", action="store_true", help="Force extracting video as 1-channel monochrome FITS")
    a.add_argument("--video-debayer", default="auto", choices=["auto", "bggr", "rggb", "grbg", "gbrg", "none"],
                   help="Demosaicing for RAW Bayer video containers (e.g. Seestar RAW.avi): 'auto' (detects Bayer grid, defaults to BGGR), 'bggr', 'rggb', or 'none'")
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
                   help="Stacking method: 'auto' (sum for 8-bit video/SER, rej w for 16-bit+), 'sum', or 'rej'")
    a.add_argument("--deconv", default="sb", choices=["sb", "wiener", "rl", "none"])
    a.add_argument("--no-adc", action="store_true")
    a.add_argument("--midtone", type=float, default=0.42)
    a.add_argument("--wavelet-l1", type=float, default=None, help="Layer 1 wavelet gain (default: auto adaptive)")
    a.add_argument("--clahe-clip", type=float, default=None, help="CLAHE clip limit (default: auto dynamic, set <=0 to bypass CLAHE)")
    a.add_argument("--sharp-mode", default="auto", choices=["auto", "mellow", "mild", "crisp", "none"],
                   help="Sharpening mode: 'auto' (balanced natural organic baseline, default), 'mellow' (ultra-soft optical view, no CLAHE), 'mild' (subtle natural), 'crisp' (classic), 'none' (bypass)")
    a.add_argument("--unsharp", type=float, default=None, help="USM unsharp mask amount (default: auto bypassed when wavelets/deconv active)")
    a.add_argument("--anti-ringing", default="auto", choices=["auto", "off", "mild", "aggressive"],
                   help="Edge undershoot dark ringing suppression mode: 'auto' (adaptive damping, default), 'mild' (subtle), 'aggressive' (strong), 'off' (bypass)")
    a.add_argument("--damping-factor", type=float, default=None,
                   help="Manual anti-ringing damping factor (0.0 to 1.0, overrides preset if specified)")
    a.add_argument("--aperture", type=float, default=None, help="Telescope aperture in mm (default: auto/80.0)")
    a.add_argument("--focal", type=float, default=None, help="Telescope focal length in mm (default: auto inferred from limb/header)")
    a.add_argument("--pixel-size", type=float, default=None, help="Sensor pixel size in microns (default: auto from FITS header/3.73)")
    a.add_argument("--mineral-mode", default="lrgb", choices=["lrgb", "legacy"],
                   help="Mineral moon processing pipeline: 'lrgb' (Luminance/Chrominance separation, default) or 'legacy' (monolithic RGB)")
    a.add_argument("--white-balance", default="gray-world", choices=["gray-world", "legacy"],
                   help="Color balance mode: 'gray-world' (neutral lunar albedo baseline, default) or 'legacy' (per-channel percentile stretch)")
    a.add_argument("--sat-fe", type=float, default=0.8, help="Mineral saturation boost for Fe-rich terrain (orange-yellow hue 1, default: 0.8)")
    a.add_argument("--sat-ti", type=float, default=0.8, help="Mineral saturation boost for Ti-rich basalt (cyan-blue hues 3 & 4, default: 0.8)")
    a.add_argument("--sat-base", type=float, default=0.3, help="Foundation base saturation boost across all hues (default: 0.3)")
    a.add_argument("--sat-bg-factor", type=float, default=1.2, help="Background noise saturation suppression threshold factor (default: 1.2)")
    a.add_argument("--glare-suppress", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Lunar limb forward scattering glare suppression mode: 'auto' (4.5px falloff, default), 'mild' (8.0px), 'aggressive' (2.5px), 'off' (bypass)")
    a.add_argument("--extinction-comp", default="auto", choices=["auto", "mild", "aggressive", "off"],
                   help="Atmospheric extinction gradient compensation: 'auto' (adaptive threshold, default), 'mild' (50% strength), 'aggressive', 'off' (bypass)")
    a.add_argument("--mineral-style", default="deep-cine", choices=["deep-cine", "natural"],
                   help="Mineral moon aesthetic style: 'deep-cine' (deep matte basalt tone, bilateral chroma smoothing, terracotta/cobalt pure boost, shadow/ray rolloff, default) or 'natural' (classic subtle saturation)")
    a.add_argument("--mineral-fe-boost", type=float, default=4.5, help="Deep-cine saturation boost for Fe-rich terrain (terracotta peach, default: 4.5)")
    a.add_argument("--mineral-ti-boost", type=float, default=5.5, help="Deep-cine saturation boost for Ti-rich basalt (azure denim blue, default: 5.5)")
    a.add_argument("--mineral-gamma", type=float, default=1.00, help="Deep-cine filmic luminance sculpting gamma (default: 1.00)")
    a.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270], help="Rotate output images clockwise (default: 0, 180 for standard north-up lunar orientation)")
    a.add_argument("--square-crop", action=argparse.BooleanOptionalAction, default=True, help="Automatically export 1:1 square close-up master (default: True)")
    a.set_defaults(func=cmd_all)

    return p


if __name__ == "__main__":
    ns = build_parser().parse_args()
    ns.func(ns)
