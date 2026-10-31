#!/usr/bin/env python3
"""Video and SER input decoding, seeing quality profiling, and smart frame ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np

from device_specs import identify_device
from moon_common import (
    FIT_EXTS,
    RAW_EXTS,
    SER_BAYER_NAMES,
    SER_COLOR_BAYER_BGGR,
    SER_COLOR_BAYER_GBRG,
    SER_COLOR_BAYER_GRBG,
    SER_COLOR_BAYER_RGGB,
    SER_COLOR_BGR,
    SER_COLOR_MONO,
    SER_COLOR_RGB,
    SER_EXTS,
    VIDEO_EXTS,
    _apply_device_priors,
    _device_ident_summary,
    _disk_available,
    _disk_frame_budget,
    _frame_identity,
    _resolve_device_arg,
    _seq_frame_paths,
    die,
    dump_json,
    fits_header_info,
    load_json,
    log,
    run_siril_script,
)
from moon_optics import _fit_lunar_limb_circle, _measure_limb_psf_fwhm
from moon_register import (
    _locate_dual_anchor_rois,
    _locate_high_contrast_roi,
    _select_frames_by_quality,
    _subpixel_phase_correlation,
)

VIDEO_SCORE_VERSION = 5


def parse_ser_header(path: Path) -> dict:
    """Parse 178-byte header of a standard SER video container."""
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

    date_obs = None
    epoch_100ns = 621355968000000000
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

    plane_count = 3 if hdr["is_rgb"] else 1
    frame_bytes = w * h * bytes_per_sample * plane_count
    file_size = ser_path.stat().st_size
    data_size = max(0, file_size - 178)
    max_avail_frames = data_size // frame_bytes if frame_bytes > 0 else 0
    total_frames = min(hdr["frame_count"], max_avail_frames)

    if total_frames <= 0:
        raise ValueError(f"SER file contains 0 valid frames (file size {file_size} bytes, frame size {frame_bytes})")

    extract_count = min(total_frames, limit) if limit > 0 else total_frames
    out_dir.mkdir(parents=True, exist_ok=True)

    dt_endian = ">" if hdr["little_endian"] else "<"
    dt_type = f"{dt_endian}u2" if bytes_per_sample == 2 else "u1"

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
        if bytes_per_sample == 1:
            raw_frame = (raw_frame.astype(np.uint16) << 8) | raw_frame.astype(np.uint16)
        else:
            raw_frame = raw_frame.astype(np.uint16)

        if hdr["is_bayer"] and debayer and cv2_code is not None:
            rgb = cv2.demosaicing(raw_frame, cv2_code)
            fits_data = np.transpose(rgb, (2, 0, 1))
        elif hdr["is_rgb"]:
            if color_id == SER_COLOR_BGR:
                raw_frame = raw_frame[..., ::-1]
            fits_data = np.transpose(raw_frame, (2, 0, 1))
        else:
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
    """Analyze a failed video decode attempt and construct a detailed diagnostic report."""
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
        root_cause = "文件实际为标准 FITS 天文图像文件（Header 包含 'SIMPLE = T'），而非视频流文件。"
        remedy_steps.append(
            f"请将文件重命名为 .fit/.fits 扩展名，或在导入时显式声明 --format fits:\n"
            f"  python scripts/moon_stack.py import --input \"{video_path}\" --work /path/to/work --format fits"
        )
    elif is_image:
        root_cause = f"文件实际为单张静态图片（{image_type} 格式），并非用于幸运成像堆叠的多帧视频容器。"
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
        remedy_steps.append("若 ffmpeg 无法识别，可使用开源修复工具 untrunc，配合同一设备拍摄的正常参考视频修复该文件。")
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
        root_cause = f"视频编码格式为 AV1（FourCC: {fourcc_display}）。当前环境的 OpenCV 缺少 AV1 解码器支持。"
        remedy_steps.append(
            f"建议使用 ffmpeg 转码为标准无损 AVI:\n  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )
    elif is_prores:
        root_cause = f"视频编码格式为 Apple ProRes（FourCC: {fourcc_display}）。OpenCV 默认后端无法解码 ProRes 视频流。"
        remedy_steps.append(
            f"建议使用 ffmpeg 转换为无损 AVI 序列:\n  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )
    elif is_planetary_raw:
        root_cause = (
            f"视频使用了天文相机特有的未压缩原始色彩格式（FourCC: {fourcc_display}）。\n"
            "通用 OpenCV 库缺乏对该特殊调色板或 Bayer 格式的解码器映射。"
        )
        remedy_steps.append("建议使用天文专用工具 PIPP (Planetary Imaging PreProcessor) 或 AutoStakkert 转换为标准 .SER 或 FITS 序列后导入。")
        remedy_steps.append(f"亦可尝试使用 ffmpeg 转封装为标准 rawvideo AVI:\n  ffmpeg -i \"{video_path}\" -c:v rawvideo \"{fixed_avi}\"")
    elif cap_opened and (w <= 0 or h <= 0):
        root_cause = f"OpenCV 成功打开了文件容器，但解析到的画面尺寸异常 ({w}x{h})。\n视频流的元数据头可能损坏或未声明有效的图像画幅。"
        remedy_steps.append(f"尝试使用 ffmpeg 重新打包容器:\n  ffmpeg -i \"{video_path}\" -c copy \"{fixed_mp4}\"")
    elif failure_stage in ("probe_initial_frame", "unpack_zero_frames") or (cap_opened and total_reported == 0):
        root_cause = (
            f"OpenCV 成功连接到视频容器（FourCC: {fourcc_display}, 报告总帧数: {cap_frames}），\n"
            "但在尝试读取视频帧时失败（read() 返回 False/None，有效抽取帧数为 0）。\n"
            "可能原因为：关键帧索引损坏、视频流过早截断、或使用了非标准编解码器。"
        )
        remedy_steps.append(f"使用 ffmpeg 检查视频流完整性:\n  ffmpeg -v error -i \"{video_path}\" -f null -")
        remedy_steps.append(
            f"使用 ffmpeg 转码为标准高质量无损 AVI（确保 100% 兼容性）:\n"
            f"  ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )
    else:
        root_cause = (
            f"OpenCV 无法打开或解析该视频文件（失败阶段: {failure_stage}）。\n"
            "常见原因：不支持的文件格式、缺失解码器后端、容器受损或访问权限受限。"
        )
        remedy_steps.append("1. 确认该视频能否在常规播放器（如 VLC、IINA）中正常播放；")
        remedy_steps.append(
            f"2. 使用 ffmpeg 转码为本技能完美支持的标准高质量 AVI:\n"
            f"   ffmpeg -i \"{video_path}\" -c:v rawvideo -pix_fmt bgr24 \"{fixed_avi}\""
        )
        remedy_steps.append("3. 如需更全的编解码器支持，可尝试安装带完整 FFmpeg 绑定的 OpenCV:\n   pip install --upgrade opencv-python")

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
    hh = (
        f[0 : f.shape[0] // 2 * 2 : 2, 0 : f.shape[1] // 2 * 2 : 2]
        - f[0 : f.shape[0] // 2 * 2 : 2, 1 : f.shape[1] // 2 * 2 : 2]
        - f[1 : f.shape[0] // 2 * 2 : 2, 0 : f.shape[1] // 2 * 2 : 2]
        + f[1 : f.shape[0] // 2 * 2 : 2, 1 : f.shape[1] // 2 * 2 : 2]
    ) / 2
    local = hh[f[0 : f.shape[0] // 2 * 2 : 2, 0 : f.shape[1] // 2 * 2 : 2] > background + 5 * noise]
    if local.size >= 32:
        noise = max(noise, 1.4826 * float(np.median(np.abs(local - np.median(local)))))
    mask = ((f > background + 5 * noise) & (f < full_scale - full_scale / 510)).astype(np.uint8)
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8)).astype(bool)
    if np.count_nonzero(mask) < max(32, int(f.size * 0.35)):
        return 0.0
    signal = float(np.median(f[mask])) - background
    if signal <= 5 * noise:
        return 0.0
    smooth = cv2.GaussianBlur(f, (3, 3), 0.6)
    energy = float(np.mean(cv2.Laplacian(smooth, cv2.CV_32F)[mask] ** 2))
    return max(1e-12, energy - noise**2) / max(signal**2, 1e-12) * signal**2 / (signal**2 + 25 * noise**2)


def _video_component(gray, previous=None):
    import cv2

    step = 4 if min(gray.shape) >= 128 else 1
    small = np.ascontiguousarray(gray[::step, ::step])
    samples = small.ravel()[::8]
    bg = float(np.percentile(samples, 25))
    sky = samples[samples <= bg + 2].astype(np.float32)
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
                sx, sy = last_center[0] - initial_center[0], last_center[1] - initial_center[1]
                tx, ty = max(0, int(fitted_box[0] + sx) - 96), max(0, int(fitted_box[1] + sy) - 96)
                ux, uy = min(gray.shape[1], int(fitted_box[2] + sx) + 96), min(gray.shape[0], int(fitted_box[3] + sy) + 96)
                component, bg, noise = _video_component(gray[ty:uy, tx:ux], initial_center)
                if component:
                    center, box, target, step = component
                    if box[0] <= 0 or box[1] <= 0 or box[2] >= ux - tx or box[3] >= uy - ty:
                        component = None
                    else:
                        center = [center[0] + tx, center[1] + ty, center[2]]
                        box = [box[0] + tx, box[1] + ty, box[2] + tx, box[3] + ty]
                        component = (center, box, target, step)
            if component is None:
                tx = ty = 0
                ux, uy = gray.shape[1], gray.shape[0]
                component, bg, noise = _video_component(gray, initial_center)
            center = component[0] if component else None
            if not anchors or (component and (initial_center is None or (fitted_box is None and center[2] > initial_center[2] * 4))):
                fitted_box = None
                tracking_reference = None
                initial_center = center
                if component:
                    x0, y0, x1, y1 = component[1]
                    pad = max(x1 - x0, y1 - y0)
                    ax, ay = max(0, x0 - pad), max(0, y0 - pad)
                    bx, by = min(gray.shape[1], x1 + pad), min(gray.shape[0], y1 + pad)
                    local = gray[ay:by, ax:bx].astype(np.float32)
                    fit = _fit_lunar_limb_circle(local)
                    if fit is not None:
                        cx, cy, radius, residual = fit
                        circle = [cx + ax, cy + ay, radius, residual]
                        fitted_box = [cx + ax - radius, cy + ay - radius, cx + ax + radius, cy + ay + radius]
                        if min(fitted_box) < 0 or fitted_box[2] > gray.shape[1] or fitted_box[3] > gray.shape[0]:
                            fitted_box = None
                        psf = _measure_limb_psf_fwhm(local, circle=fit, full_scale=255.0)
                        profile["psf_fwhm"] = psf.get("fwhm_px") if psf else None
                bounds = fitted_box if fitted_box is not None else component[1] if component else [0, 0, gray.shape[1], gray.shape[0]]
                ax, ay = max(0, int(bounds[0])), max(0, int(bounds[1]))
                bx, by = min(gray.shape[1], int(np.ceil(bounds[2]))), min(gray.shape[0], int(np.ceil(bounds[3])))
                lunar = gray[ay:by, ax:bx]
                size = min(128, roi_size, max(32, min(lunar.shape) // 3), *lunar.shape)
                downsample = max(1, min(4, size // 32))
                primary = _locate_high_contrast_roi(lunar, roi_size=size, downsample=downsample)
                first, second = _locate_dual_anchor_rois(lunar, roi_size=size, downsample=downsample)
                local_anchors = [first]
                if second is not None and (second[2] <= first[0] or second[0] >= first[2] or second[3] <= first[1] or second[1] >= first[3]):
                    local_anchors.append(second)
                if all(primary[2] <= r[0] or primary[0] >= r[2] or primary[3] <= r[1] or primary[1] >= r[3] for r in local_anchors):
                    local_anchors.append(primary)
                anchors = [(r[0] + ax, r[1] + ay, r[2] + ax, r[3] + ay) for r in local_anchors]
            if center:
                last_center = center
            dx = center[0] - initial_center[0] if center and initial_center else 0
            dy = center[1] - initial_center[1] if center and initial_center else 0
            tracking_response = None
            if fitted_box is not None:
                if tracking_reference is None:
                    lx, ly = max(0, int(fitted_box[0]) - 96), max(0, int(fitted_box[1]) - 96)
                    rx, ry = min(gray.shape[1], int(fitted_box[2]) + 96), min(gray.shape[0], int(fitted_box[3]) + 96)
                    tracking_box = [lx, ly, rx - lx, ry - ly]
                    tracking_reference = np.ascontiguousarray(gray[ly:ry:4, lx:rx:4], dtype=np.float32)
                lx, ly, tw, th = tracking_box
                px = max(0, min(int(round(lx + track_dx)), gray.shape[1] - tw))
                py = max(0, min(int(round(ly + track_dy)), gray.shape[0] - th))
                sample = np.ascontiguousarray(gray[py : py + th : 4, px : px + tw : 4], dtype=np.float32)
                fx, fy, tracking_response = _subpixel_phase_correlation(tracking_reference, sample, upsample_factor=4)
                if tracking_response >= 0.25:
                    dx, dy = px - lx + fx * 4, py - ly + fy * 4
                    track_dx, track_dy = dx, dy
            scores = []
            for x0, y0, x1, y1 in anchors:
                width, height = x1 - x0, y1 - y0
                sx = max(0, min(int(round(x0 + dx)), gray.shape[1] - width))
                sy = max(0, min(int(round(y0 + dy)), gray.shape[0] - height))
                scores.append(_structure_quality(gray[sy : sy + height, sx : sx + width], bg, noise, 255.0))
            box = None
            saturation = [0.0] * 3
            brightness = None
            if component:
                center, visible, target, step = component
                sample = frame[ty:uy:step, tx:ux:step]
                pixels = sample[target]
                if pixels.size:
                    brightness = float(np.median(pixels[..., 1] if pixels.ndim == 2 else pixels))
                    saturation = (
                        np.mean(pixels >= 255, axis=0)[::-1].tolist()
                        if pixels.ndim == 2
                        else [float(np.mean(pixels >= 255))] * 3
                    )
                if fitted_box is not None:
                    box = [
                        min(fitted_box[0] + dx, visible[0]),
                        min(fitted_box[1] + dy, visible[1]),
                        max(fitted_box[2] + dx, visible[2]),
                        max(fitted_box[3] + dy, visible[3]),
                    ]
            quality = float(np.median(scores)) if component or initial_center is None else 0.0
            if index and index % 1000 == 0:
                log(f"pass 1: decoded {total} frames")
            rows.append({
                "index": index,
                "sharpness": quality,
                "roi_scores": scores,
                "center": center,
                "box": box,
                "background": bg,
                "noise": noise,
                "brightness": brightness,
                "tracking_response": tracking_response,
                "tracking_shift": [dx, dy],
                "saturation_rgb": saturation,
                "pts": float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000,
            })
    finally:
        cap.release()
    if not rows:
        raise ValueError(format_video_decode_error(video_path, "probe_initial_frame"))
    signals = [max(0, r["brightness"] - r["background"]) for r in rows if r["brightness"] is not None]
    reference_signal = float(np.percentile(signals, 90)) if signals else 0.0
    for row in rows:
        signal = max(0, (row["brightness"] or 0) - row["background"])
        row["signal_ratio"] = signal / reference_signal if reference_signal else 0.0
        row["quality_flags"] = []
        if row["signal_ratio"] < min_signal_ratio:
            row["quality_flags"].append("low_lunar_signal")
            row["sharpness"] = 0.0
        if row["center"] is None:
            row["quality_flags"].append("target_lost")
        if max(row["saturation_rgb"]) > 0.05:
            row["quality_flags"].append("channel_clipping")
    profile.update(
        rows=rows,
        decoded_frames=total,
        anchors=anchors,
        circle=circle,
        reference_signal=reference_signal,
        scan_seconds=time.monotonic() - started,
    )
    profile["timestamp_source"] = "opencv" if all(b["pts"] > a["pts"] for a, b in zip(rows, rows[1:])) else "unavailable"
    if shutil.which("ffprobe"):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_packets", "-show_entries", "packet=pts_time", "-of", "json", str(video_path)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            try:
                timestamps = sorted(float(packet["pts_time"]) for packet in json.loads(result.stdout)["packets"])
                if len(timestamps) == total and all(b > a for a, b in zip(timestamps, timestamps[1:])):
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
    identity = {
        "sha256": _source_sha256(video),
        "version": VIDEO_SCORE_VERSION,
        "stride": stride,
        "roi_size": 400,
        "min_signal_ratio": min_signal_ratio,
    }
    path = work / "video_scores.json"
    if path.exists():
        try:
            cached = load_json(path)
            if cached.get("identity") == identity:
                log("reusing full-video scores (source hash and scoring parameters verified)")
                return {**cached, "cache_hit": True}
        except (OSError, ValueError):
            pass
    profile = _scan_video_profile(video, stride, min_signal_ratio=min_signal_ratio)
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
    box = [
        max(0, int(np.floor(min(b[0] for b in boxes))) - effective_margin),
        max(0, int(np.floor(min(b[1] for b in boxes))) - effective_margin),
        min(profile["width"], int(np.ceil(max(b[2] for b in boxes))) + effective_margin),
        min(profile["height"], int(np.ceil(max(b[3] for b in boxes))) + effective_margin),
    ]
    box[0] -= box[0] % 2
    box[1] -= box[1] % 2
    return box, "tracked lunar disc and visible limb union"


def _import_scored_video(video, work, args, seq_name):
    from astropy.io import fits

    stride = max(1, int(getattr(args, "probe_stride", 1)))
    profile = _cached_video_profile(video, work, stride, getattr(args, "min_signal_ratio", 0.2))
    mode = getattr(args, "sample_mode", "adaptive")
    limit = int(getattr(args, "limit", 0))
    if limit < 0 or int(getattr(args, "roi_margin", 64)) < 0:
        die("--limit and --roi-margin must be non-negative")
    valid = [{"index": r["index"], "sharpness": r["sharpness"]} for r in profile["rows"] if r["sharpness"] > 0 and np.isfinite(r["sharpness"])]
    valid.sort(key=lambda r: r["sharpness"], reverse=True)
    selection = None
    primary = mode == "adaptive" or (mode in ("smart-top", "smart-cluster") and limit > 0)
    if mode == "adaptive":
        kept, selection = _select_frames_by_quality(
            valid,
            len(profile["rows"]),
            getattr(args, "select_mode", "otsu"),
            getattr(args, "keep_percent", None),
            getattr(args, "quality_threshold", 0.75),
            getattr(args, "utility_alpha", 2.0),
            getattr(args, "utility_beta", 1.0),
        )
        pool = [r["index"] for r in valid if r["index"] in kept]
    elif mode in ("smart-top", "smart-cluster") and limit > 0:
        pool = [r["index"] for r in valid]
    else:
        start = max(0, int(getattr(args, "start_frame", 0)))
        pool = list(range(start, profile["decoded_frames"]))
    if not pool:
        die("no video frames passed the import quality filter")
    crop, crop_reason = _video_crop(
        profile,
        set(pool),
        int(getattr(args, "roi_margin", 64)),
        getattr(args, "video_roi", "auto"),
        float(getattr(args, "sky_margin", 0.25) or 0.25),
    )
    width, height = (crop[2] - crop[0], crop[3] - crop[1]) if crop else (profile["width"], profile["height"])
    signature = {
        "source": profile["identity"]["sha256"],
        "crop": crop,
        "force_mono": bool(getattr(args, "force_mono", False)),
        "debayer": getattr(args, "video_debayer", "auto"),
        "device": _resolve_device_arg(args),
    }
    previous = load_json(work / "import_receipt.json") if (work / "import_receipt.json").exists() else {}
    old_video = previous.get("video_metadata", {})
    frames = _seq_frame_paths(work, seq_name)
    if frames and old_video.get("processing_signature") != signature:
        die("existing FITS use another source, ROI or channel configuration; use a new --work directory")
    mapping = dict(old_video.get("source_mapping", {}))
    if len(mapping) != len(frames) or any(not (work / f"{seq_name}{int(k):05d}.fit").exists() for k in mapping):
        die("incomplete source-frame mapping; use a new --work directory")
    for number, index in mapping.items():
        path = work / f"{seq_name}{int(number):05d}.fit"
        saved = old_video.get("source_files", {}).get(number)
        if saved and saved != _frame_identity(path):
            die("a generated source FITS was modified; use a new --work directory")
        if not saved:
            header = fits.getheader(path)
            if header.get("SRC_FRM") != index or header.get("CROP_X", 0) != (crop[0] if crop else 0) or header.get("CROP_Y", 0) != (crop[1] if crop else 0):
                die("source-frame metadata does not match the import receipt; use a new --work directory")
    channels = int(previous.get("channels", 1 if getattr(args, "force_mono", False) else 3))
    drizzle = str(getattr(args, "drizzle", "off"))
    scale = float(drizzle) if drizzle in ("2", "3") else (2.0 if drizzle == "auto" and (profile.get("psf_fwhm") is None or profile["psf_fwhm"] < 2) else 1.0)
    requested = min(limit, len(pool)) if limit > 0 else len(pool)
    count, budget = _disk_frame_budget(work, args, width, height, channels, requested, len(mapping), scale)
    if count < requested and not primary:
        scores = {r["index"]: r["sharpness"] for r in profile["rows"]}
        pool = sorted(pool[:requested], key=lambda index: scores.get(index, 0), reverse=True)
    chosen = pool[:count]
    if mode == "smart-cluster" and primary:
        by_index = {r["index"]: r for r in profile["rows"]}
        times = [by_index[i]["pts"] for i in pool]
        if all(t is not None for t in times):
            lo, hi = min(times), max(times)
            bins = [[] for _ in range(10)]
            for i in pool:
                bins[min(9, int((by_index[i]["pts"] - lo) / max(hi - lo, 1e-9) * 10))].append(i)
            chosen = []
            while len(chosen) < count and any(bins):
                for group in bins:
                    if group and len(chosen) < count:
                        chosen.append(group.pop(0))
        else:
            die("smart-cluster requires reliable presentation timestamps; use smart-top")
    missing = sorted(set(chosen) - set(mapping.values()))
    if missing:
        info = unpack_video_to_fits(
            video,
            work,
            seq_name,
            force_mono=getattr(args, "force_mono", False),
            debayer=getattr(args, "video_debayer", "auto"),
            target_indices=missing,
            device_override=_resolve_device_arg(args),
            crop_box=crop,
            output_start=len(mapping) + 1,
        )
        for number, index in enumerate(missing, len(mapping) + 1):
            mapping[str(number)] = index
    else:
        info = dict(old_video)
    scores = {r["index"]: r for r in profile["rows"]}
    selected_rows = [scores[i] for i in chosen if i in scores]
    info.update(
        extracted_frames=len(mapping),
        width=width,
        height=height,
        source_width=profile["width"],
        source_height=profile["height"],
        processing_signature=signature,
        source_mapping=mapping,
        source_files={key: _frame_identity(work / f"{seq_name}{int(key):05d}.fit") for key in mapping},
        active_sources=chosen,
        primary_selected=primary,
        crop_box=crop,
        crop_reason=crop_reason,
        budget=budget,
        candidate_pool_count=len(pool),
        newly_extracted=len(missing),
        score_identity=profile["identity"],
        score_cache_hit=profile["cache_hit"],
        timestamp_source=profile["timestamp_source"],
        source_saturation_rgb=np.median([r["saturation_rgb"] for r in selected_rows], axis=0).tolist() if selected_rows else None,
    )
    values = [r["sharpness"] for r in profile["rows"]]
    info["seeing_probe"] = {
        "total_probed": len(values),
        "sample_mode": mode,
        "probe_stride": stride,
        "selection_meta": selection,
        "chosen_frame_count": len(chosen),
        "min_sharpness": min(values),
        "max_sharpness": max(values),
        "p50_sharpness": float(np.median(values)),
        "p90_sharpness": float(np.percentile(values, 90)),
    }
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
    """Extract frames from an AVI/MP4/MOV video container directly into 16-bit FITS files."""
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
    detected_mode = None

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
                else:
                    is_mono_carrier = (
                        frame.ndim == 2
                        or (
                            frame.ndim == 3
                            and np.array_equal(frame[..., 0], frame[..., 1])
                            and np.array_equal(frame[..., 1], frame[..., 2])
                        )
                    )
                    if is_mono_carrier:
                        gray_sample = frame[..., 0] if frame.ndim == 3 else frame
                        s00 = float(gray_sample[0::2, 0::2].mean())
                        s01 = float(gray_sample[0::2, 1::2].mean())
                        s10 = float(gray_sample[1::2, 0::2].mean())
                        s11 = float(gray_sample[1::2, 1::2].mean())
                        grid_contrast = abs(s01 - s00) + abs(s10 - s11)
                        is_raw_hint = any(k in video_path.name.upper() for k in ("RAW", "CFA", "BAYER"))
                        if is_raw_hint or grid_contrast > 2.0:
                            codes = {
                                "GBRG": cv2.COLOR_BayerGB2RGB,
                                "GRBG": cv2.COLOR_BayerGR2RGB,
                                "BGGR": cv2.COLOR_BayerBG2RGB,
                                "RGGB": cv2.COLOR_BayerRG2RGB,
                            }
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
        if crop_box is not None and detected_mode in ("mono", "rgb"):
            x0, y0, x1, y1 = crop_box
            if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
                cap.release()
                raise ValueError("crop_box is outside source dimensions")
            frame = frame[y0:y1, x0:x1]
            crop_applied = True
        if detected_mode == "mono":
            if frame.ndim == 3:
                mono_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                mono_frame = frame
            fits_data = (mono_frame.astype(np.uint16) << 8) | mono_frame.astype(np.uint16)
        elif detected_mode == "rgb":
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            u16_rgb = (rgb.astype(np.uint16) << 8) | rgb.astype(np.uint16)
            fits_data = np.transpose(u16_rgb, (2, 0, 1))
        else:
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

        for sc_key, fits_key in (
            ("FOCALLEN", "FOCALLEN"),
            ("FOCAL", "FOCALLEN"),
            ("APERTURE", "APERTURE"),
            ("APTURE", "APERTURE"),
            ("XPIXSZ", "XPIXSZ"),
            ("PIXSIZE", "XPIXSZ"),
        ):
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

        _apply_device_priors(hdu.header, ident)
        hdu.writeto(target_fit, overwrite=True)

    cap.release()

    if extract_count <= 0:
        raise ValueError(format_video_decode_error(video_path, "unpack_zero_frames", total_reported=total_in_file))
    if frame_queue is not None and extract_count != len(frame_queue):
        raise ValueError(f"video ended before all selected frames were read: {extract_count}/{len(frame_queue)} extracted")

    return {
        "extracted_frames": extract_count,
        "total_in_file": total_in_file if total_in_file > 0 else extract_count,
        "channels": out_channels,
        "width": w,
        "height": h,
        "fps": fps,
        "fourcc": fourcc_str,
        "orig_bit_depth": 8,
        "device": _device_ident_summary(ident),
    }


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
    device_override = device_arg
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

    if fmt == "ser":
        fits_files, raw_files, video_files = [], [], []
    elif fmt in ("video", "avi", "mp4"):
        fits_files, raw_files, ser_files = [], [], []
    elif fmt == "raw":
        fits_files, ser_files, video_files = [], [], []
    elif fmt == "fits":
        raw_files, ser_files, video_files = [], [], []
    else:
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

        first_h = fits_header_info(usable_fits[0])
        channels = first_h.get("channels") or 1
        if first_h.get("naxis") == 2:
            channels = 1

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
