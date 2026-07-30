#!/usr/bin/env python3
"""
Deep-Sky Image I/O — FITS/PNG/JPG/TIFF/XISF 统一读写

支持格式:
  - FITS (.fit/.fits/.fts): 天文标准格式，线性原始数据
  - XISF (.xisf): PixInsight 格式，支持压缩数据
  - PNG/JPG/TIFF: 通用图像格式 (skimage)

FITS 处理:
  - 自动检测 HDU 类型并提取图像数据
  - 16-bit int → 归一化到 [0,1] float32
  - 32-bit float → 保留浮动范围后归一化
  - 单通道(灰度) → 返回2D; 3通道(RGB) → 返回3D
  - 自动处理 BSCALE/BZERO 标定
  - 保留 FITS header 供可选的 WCS 色校准

XISF 处理:
  - 使用 xisf 包读取 (pip install xisf)
  - 自动检测 RGB/RGBA/单通道
  - 数据保持 float32，范围 [0,1] (线性数据通常极暗)
  - 标记 is_linear=True (XISF 通常为线性数据)

用法:
  from fits_io import read_image, write_image
  img = read_image('/path/to/image.xisf')  # 自动识别格式
  write_image(img, '/path/to/output.jpg') # 自动识别格式
"""

import os
import sys
import numpy as np


HEADER_ALIASES = {
    "exposure_seconds": ("EXPTIME", "EXPOSURE", "EXPOSURETIME"),
    "gain": ("GAIN", "EGAIN", "CAMGAIN"),
    "sensor_temperature_c": ("CCD-TEMP", "CCD_TEMP", "SENSOR_TEMP", "SET-TEMP"),
    "filter": ("FILTER", "FILTERID", "FILTNAME"),
    "telescope": ("TELESCOP", "TELESCOPE"),
    "camera": ("INSTRUME", "CAMERA"),
    "object": ("OBJECT", "TARGNAME"),
    "ra": ("OBJCTRA", "RA", "CRVAL1"),
    "dec": ("OBJCTDEC", "DEC", "CRVAL2"),
    "date_obs": ("DATE-OBS", "DATE_OBS"),
    "bin_x": ("XBINNING", "BINX"),
    "bin_y": ("YBINNING", "BINY"),
    "pixel_size_um": ("XPIXSZ", "PIXSIZE", "PIXELSIZE"),
    "focal_length_mm": ("FOCALLEN", "FOCAL"),
    "aperture_mm": ("APTDIA", "APERTURE"),
}


def _header_value(header, aliases):
    if not header:
        return None
    for key in aliases:
        value = header.get(key)
        if value not in (None, ""):
            if isinstance(value, (list, tuple)) and value:
                value = value[0]
            if isinstance(value, dict):
                value = value.get("value")
            return value
    return None


def _as_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_filter(filter_name):
    """Normalize common broad/narrow/dual-band filter names."""
    text = str(filter_name or "").strip().lower().replace("_", "-")
    if not text:
        return {"class": "unknown", "normalized": None, "lines": []}
    dual_tokens = (
        "dual", "duo", "l-extreme", "l-enhance", "l-ultimate",
        "nbz", "alp-t", "ha+oiii", "ha/oiii", "hα+oiii",
    )
    if any(token in text for token in dual_tokens):
        return {
            "class": "dual_band",
            "normalized": str(filter_name),
            "lines": ["H-alpha", "OIII"],
        }
    if any(token in text for token in ("h-alpha", "halpha", "ha ", "ha-")) or text == "ha":
        return {"class": "narrowband", "normalized": "H-alpha", "lines": ["H-alpha"]}
    if "oiii" in text or text in ("o3", "oxygen"):
        return {"class": "narrowband", "normalized": "OIII", "lines": ["OIII"]}
    if "sii" in text or text in ("s2", "sulfur"):
        return {"class": "narrowband", "normalized": "SII", "lines": ["SII"]}
    if text in ("l", "lum", "luminance"):
        return {"class": "luminance", "normalized": "L", "lines": []}
    if text in ("r", "g", "b", "red", "green", "blue"):
        return {"class": "broadband_channel", "normalized": text.upper(), "lines": []}
    if any(token in text for token in ("uv/ir", "uv-ir", "cls", "broadband", "clear")):
        return {"class": "broadband", "normalized": str(filter_name), "lines": []}
    return {"class": "unknown", "normalized": str(filter_name), "lines": []}


def extract_capture_metadata(header):
    """Return a stable, JSON-safe physical capture metadata structure."""
    raw = {
        field: _header_value(header, aliases)
        for field, aliases in HEADER_ALIASES.items()
    }
    numeric_fields = {
        "exposure_seconds", "gain", "sensor_temperature_c", "bin_x", "bin_y",
        "pixel_size_um", "focal_length_mm", "aperture_mm",
    }
    normalized = {
        key: (_as_float(value) if key in numeric_fields else value)
        for key, value in raw.items()
    }
    normalized["filter_profile"] = classify_filter(normalized.get("filter"))
    present = sum(value is not None for key, value in normalized.items()
                  if key != "filter_profile")
    normalized["metadata_completeness"] = round(
        present / max(len(raw), 1), 3
    )
    normalized["source"] = "header" if header else "unavailable"
    return normalized


def build_physical_priors(capture_metadata):
    """Map capture conditions to bounded processing hints with evidence."""
    metadata = capture_metadata or {}
    overrides = {}
    recommendations = []
    warnings = []
    evidence = []

    exposure = metadata.get("exposure_seconds")
    gain = metadata.get("gain")
    temperature = metadata.get("sensor_temperature_c")
    filter_profile = metadata.get("filter_profile") or {}

    if temperature is not None:
        evidence.append(f"sensor_temperature_c={temperature:g}")
        if temperature >= 10:
            overrides["pre_denoise_lum"] = 0.035
            overrides["pre_denoise_chroma"] = 0.11
            overrides["final_denoise_lum"] = 0.018
            overrides["final_denoise_chroma"] = 0.055
            recommendations.append("warm_sensor_stronger_noise_control")
        elif temperature <= -10:
            recommendations.append("cooled_sensor_preserve_faint_detail")

    if gain is not None:
        evidence.append(f"gain={gain:g}")
        if gain >= 300:
            warnings.append(
                "High numeric gain is camera-dependent; assume reduced highlight "
                "headroom unless a sensor profile proves otherwise."
            )
            overrides["ghs_protect_strength"] = max(
                overrides.get("ghs_protect_strength", 0.0), 0.65
            )
            overrides["hdr_strength"] = max(
                overrides.get("hdr_strength", 0.0), 0.45
            )
            recommendations.append("high_gain_protect_highlights")

    if exposure is not None:
        evidence.append(f"exposure_seconds={exposure:g}")
        if exposure < 30:
            recommendations.append("short_subexposure_expect_read_noise")
        elif exposure >= 300:
            recommendations.append("long_subexposure_protect_star_cores")
            overrides["ghs_protect_strength"] = max(
                overrides.get("ghs_protect_strength", 0.0), 0.65
            )

    filter_class = filter_profile.get("class")
    if filter_class == "dual_band":
        evidence.append(f"filter={metadata.get('filter')}")
        overrides["star_scnr_strength"] = max(
            overrides.get("star_scnr_strength", 0.0), 0.25
        )
        overrides["saturation"] = min(
            overrides.get("saturation", 1.45), 1.45
        )
        recommendations.extend([
            "use_emission_color_mode",
            "apply_moderate_scnr",
            "preserve_ha_oiii_ratio",
        ])
        warnings.append(
            "Dual-band OSC data may be separated only with a documented "
            "channel model; do not claim true HOO/SHO synthesis from metadata alone."
        )
    elif filter_class == "narrowband":
        recommendations.append("preserve_emission_line_physics")

    if metadata.get("metadata_completeness", 0) < 0.2:
        warnings.append("Capture metadata is sparse; keep physical priors low-confidence.")

    confidence = "high" if len(evidence) >= 3 else ("medium" if evidence else "low")
    return {
        "schema_version": "1.0",
        "confidence": confidence,
        "evidence": evidence,
        "recommendations": recommendations,
        "parameter_overrides": overrides,
        "warnings": warnings,
    }


def read_capture_metadata(filepath):
    """Read only header-level capture metadata when the format supports it."""
    ext = os.path.splitext(filepath)[1].lower()
    header = None
    if ext in (".fit", ".fits", ".fts"):
        from astropy.io import fits
        with fits.open(filepath, memmap=False) as hdul:
            for hdu in hdul:
                if hdu.data is not None and hdu.data.ndim >= 2:
                    header = hdu.header.copy()
                    break
    elif ext == ".xisf":
        from xisf import XISF
        x = XISF(filepath)
        image_metadata = x.get_images_metadata()
        image_meta = image_metadata[0] if image_metadata else {}
        raw_fits = image_meta.get("FITSKeywords", {})
        header = {}
        for key, entries in raw_fits.items():
            if isinstance(entries, list) and entries:
                header[key] = entries[0].get("value")
            elif isinstance(entries, dict):
                header[key] = entries.get("value")
            else:
                header[key] = entries
    return extract_capture_metadata(header), header


def read_image(filepath, force_linear=False):
    """
    统一图像读取。自动识别 FITS / XISF / PNG / JPG / TIFF 格式。

    参数:
      filepath: 图像文件路径
      force_linear: FITS 数据是否保持原始线性范围（默认False→归一化到[0,1]）
                   对 XISF 无效（XISF 数据已归一化，但标记 is_linear=True）

    返回:
      img:  float32 数组，范围 [0,1] (或线性数据范围)
           2D=灰度, 3D=RGB, 4D=RGBA
      meta: dict 包含:
           'format': 'fits'|'xisf'|'png'|'jpg'|'tiff'
           'is_linear': bool (FITS/XISF=True, 其他=False)
           'header': FITS header (仅FITS)
           'shape_original': 原始形状
    """
    ext = os.path.splitext(filepath)[1].lower()
    meta = {'filepath': filepath, 'format': 'unknown', 'is_linear': False,
            'header': None, 'shape_original': None}

    if ext in ('.fit', '.fits', '.fts'):
        img, header, scale_info = _read_fits(filepath, force_linear)
        meta['format'] = 'fits'
        meta['is_linear'] = True
        meta['header'] = header
        meta['shape_original'] = img.shape
        meta['data_scale'] = scale_info['norm_scale']
        meta['data_offset'] = scale_info['norm_offset']
        meta['original_min'] = scale_info['data_min']
        meta['original_max'] = scale_info['data_max']
        meta['bscale'] = scale_info['bscale']
        meta['bzero'] = scale_info['bzero']
        meta['capture_metadata'] = extract_capture_metadata(header)
        return img.astype(np.float32), meta
    elif ext == '.xisf':
        img, xisf_meta = _read_xisf(filepath)
        meta['format'] = 'xisf'
        meta['is_linear'] = True  # XISF 通常为线性数据
        meta['header'] = xisf_meta.get('fits_keywords', {})
        meta['xisf_metadata'] = xisf_meta
        meta['shape_original'] = img.shape
        meta['capture_metadata'] = extract_capture_metadata(meta['header'])
        return img.astype(np.float32), meta
    else:
        from skimage.io import imread
        img = imread(filepath)
        meta['format'] = ext[1:]  # 'png', 'jpg', 'tiff'
        meta['shape_original'] = img.shape
        meta['capture_metadata'] = extract_capture_metadata(None)

        # 归一化到 [0,1]
        if img.dtype == np.uint8:
            img = img.astype(np.float32) / 255.0
        elif img.dtype == np.uint16:
            img = img.astype(np.float32) / 65535.0
        else:
            img = img.astype(np.float32)
            if img.max() > 1.0:
                img = img / img.max()

        return img, meta


def write_image(img, filepath, as_fits=False, fits_header=None, auto_stretch=False,
                data_scale=None, data_offset=None):
    """
    统一图像写入。自动识别格式。

    参数:
      img: float32 数组，范围 [0,1]（处理后的显示范围数据）
      filepath: 输出路径
      as_fits: 强制输出为 FITS 格式
      fits_header: FITS header (仅FITS输出时使用)
      auto_stretch: 对 JPG/PNG 输出自动进行显示拉伸（基于 0.1%-99.9% 百分位归一化），
                    使图像在屏幕上可见。FITS/TIF 中间文件不受影响。
      data_scale: 输入 FITS 时的归一化缩放因子（恢复原始范围用）
      data_offset: 输入 FITS 时的归一化偏移量
    """
    img = np.clip(img.astype(np.float32), 0, 1)
    ext = os.path.splitext(filepath)[1].lower()

    if as_fits or ext in ('.fit', '.fits', '.fts'):
        _write_fits(img, filepath, fits_header,
                    data_scale=data_scale, data_offset=data_offset)
        return

    # 对显示格式（JPG/PNG等）进行可选的自动显示拉伸
    if auto_stretch and img.max() > 0:
        p0 = np.percentile(img, 0.1)
        p99 = np.percentile(img, 99.9)
        span = p99 - p0
        if span > 1e-6:
            img = np.clip((img - p0) / span, 0, 1)
            print(f"[显示拉伸] 百分位归一化: p0.1={p0:.4f} → p99.9={p99:.4f}, 缩放因子≈{1/span:.1f}x")

    # TIF 保存为 float32，保留完整精度；其他格式保存为 uint8
    if ext in ('.tif', '.tiff'):
        try:
            import tifffile
            tifffile.imwrite(filepath, img.astype(np.float32))
        except ImportError:
            from skimage.io import imsave
            imsave(filepath, img)  # skimage 对 float 会尝试保存
    else:
        from skimage.io import imsave
        imsave(filepath, (img * 255).astype(np.uint8))


def _read_fits(filepath, force_linear=False):
    """
    读取 FITS 文件，返回 float32 数组、header 和缩放信息。

    归一化策略（force_linear=False 时）：
      1. 应用 BSCALE/BZERO 标定
      2. 取数据绝对值最大值作为 scale
      3. data = data / scale（保留负值，不做截断）

    与旧版不同：
      - 不再使用 p0.1-p99.9 百分位裁剪，避免丢失原始线性范围和测光信息
      - 返回 scale_info，供输出时恢复原始范围
    """
    from astropy.io import fits

    hdul = fits.open(filepath, memmap=False)

    # 查找包含图像数据的 HDU
    data = None
    header = None
    target_hdu = 0

    for i, hdu in enumerate(hdul):
        if hdu.data is not None and hdu.data.ndim >= 2:
            data = hdu.data.copy()
            header = hdu.header.copy()
            target_hdu = i
            break

    if data is None:
        hdul.close()
        raise ValueError(f"FITS 文件中没有找到图像数据: {filepath}")

    print(f"[FITS] HDU#{target_hdu}  shape={data.shape}  dtype={data.dtype}")

    # BSCALE/BZERO 标定 (现代文件通常 BSCALE=1, BZERO=0)
    bscale = float(header.get('BSCALE', 1.0))
    bzero = float(header.get('BZERO', 0.0))
    if bscale != 1.0 or bzero != 0.0:
        print(f"[FITS] Applying BSCALE={bscale} BZERO={bzero}")
        data = data.astype(np.float64) * bscale + bzero

    # 记录原始统计（在 NaN 处理前）
    data_min = float(np.min(data))
    data_max = float(np.max(data))
    data_absmax = max(abs(data_min), abs(data_max), 1e-12)

    # 转换为 float32
    data = data.astype(np.float32)

    # 处理多维度: [N,H,W] → 取第一帧, [H,W,C] → 保持
    if data.ndim == 3 and data.shape[0] < 10:
        # 可能是 [C,H,W] 格式，需要检查 NAXIS3 关键字
        naxis3 = header.get('NAXIS3', 0)
        if naxis3 == 3:
            # RGB 图像，[3,H,W] → [H,W,3]
            print(f"[FITS] 检测到 RGB 3通道，转换 [3,H,W] → [H,W,3]")
            data = np.transpose(data, (1, 2, 0))
        elif data.shape[0] < data.shape[1] and data.shape[0] < data.shape[2]:
            # 可能是 [C,H,W] 格式
            if data.shape[0] <= 4:
                print(f"[FITS] 推测 {data.shape[0]} 通道，转换 [C,H,W] → [H,W,C]")
                data = np.transpose(data, (1, 2, 0))

    # 处理 NaN/Inf
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)

    # 归一化
    norm_scale = 1.0
    norm_offset = 0.0
    if force_linear:
        # 保持线性范围，不做归一化
        print(f"[FITS] 保持原始线性范围: [{data_min:.3e}, {data_max:.3e}]")
    else:
        # 使用绝对值最大值归一化（保留完整动态范围，不截断 tails）
        norm_scale = data_absmax
        if norm_scale > 1e-12:
            data = data / norm_scale
            print(f"[FITS] 归一化: scale={norm_scale:.3e}  "
                  f"原始范围=[{data_min:.3e}, {data_max:.3e}] → "
                  f"归一化后=[{float(np.min(data)):.4f}, {float(np.max(data)):.4f}]")
        else:
            data = np.zeros_like(data)
            print("[FITS] 警告: 数据全为零或极小")

    hdul.close()

    scale_info = {
        'data_min': data_min,
        'data_max': data_max,
        'norm_scale': norm_scale,
        'norm_offset': norm_offset,
        'bscale': bscale,
        'bzero': bzero,
    }
    return data, header, scale_info


def _read_xisf(filepath):
    """读取 XISF 文件，返回 float32 数组和 metadata dict"""
    try:
        from xisf import XISF
    except ImportError:
        raise ImportError("请先安装 xisf 包: pip install xisf")

    x = XISF(filepath)
    img = x.read_image(0)  # (H, W, C) format, float32
    file_meta = x.get_file_metadata()
    img_meta = x.get_images_metadata()

    print(f"[XISF] shape={img.shape}  dtype={img.dtype}")
    image_meta = img_meta[0] if img_meta else {}
    print(
        f"[XISF] id={image_meta.get('id', 'unknown')} "
        f"colorSpace={image_meta.get('colorSpace', 'unknown')} "
        f"sampleFormat={image_meta.get('sampleFormat', 'unknown')}"
    )

    # 处理 NaN/Inf
    img = np.nan_to_num(img, nan=0.0, posinf=0.0, neginf=0.0)

    # 归一化（保留完整动态范围，不使用百分位裁剪）
    if img.max() > 1.0 or img.min() < 0.0:
        img_absmax = max(abs(float(img.max())), abs(float(img.min())), 1e-12)
        img = img / img_absmax
        print(f"[XISF] 归一化: scale={img_absmax:.3e}  "
              f"范围=[{float(img.min()):.4f}, {float(img.max()):.4f}]")

    # 组合 metadata
    raw_fits = image_meta.get('FITSKeywords', {})
    fits_keywords = {}
    for key, entries in raw_fits.items():
        if isinstance(entries, list) and entries:
            fits_keywords[key] = entries[0].get('value')
        elif isinstance(entries, dict):
            fits_keywords[key] = entries.get('value')
        else:
            fits_keywords[key] = entries

    meta = {
        'file_metadata': file_meta,
        'image_metadata': image_meta,
        'fits_keywords': fits_keywords,
    }

    return img, meta


def _write_fits(img, filepath, header=None, data_scale=None, data_offset=None):
    """
    写入 FITS 文件，输出 float32 以保留完整精度。

    语义：
      - 输入 img 是处理后的 [0,1] 范围数据（可能已非线性拉伸）
      - 如果提供了 data_scale，乘以 scale 恢复物理量级（可选）
      - 输出 BITPIX=-32（IEEE float32），BSCALE=1.0, BZERO=0.0
      - 避免 uint16 截断导致的精度丢失
    """
    from astropy.io import fits

    # 可选：恢复原始数据量级（仅当数据未经过拉伸且用户需要原始范围时）
    if data_scale is not None and data_scale != 1.0:
        img_out = img * float(data_scale)
        if data_offset is not None and data_offset != 0.0:
            img_out = img_out + float(data_offset)
        print(f"[FITS 输出] 恢复原始范围: ×{data_scale:.3e}")
    else:
        img_out = img.copy()

    # 确保 float32（保留完整浮点精度，不做 uint16 截断）
    img_out = img_out.astype(np.float32)

    # 通道维度处理: [H,W,3] → [3,H,W]（FITS 惯例）
    if img_out.ndim == 3 and img_out.shape[2] == 3:
        img_out = np.transpose(img_out, (2, 0, 1))

    # 构建/更新 header
    if header is not None:
        out_header = header.copy()
    else:
        out_header = fits.Header()

    # 强制更新数据描述关键字，确保语义一致
    out_header['BITPIX'] = (-32, 'IEEE 32-bit floating point')
    out_header['BSCALE'] = (1.0, 'Linear scaling factor')
    out_header['BZERO'] = (0.0, 'Zero offset')
    # 清除可能存在的旧 NAXIS3 不匹配（单通道输出时）
    if img_out.ndim == 2:
        for key in ['NAXIS3', 'NAXIS4']:
            if key in out_header:
                del out_header[key]

    # 添加处理历史
    out_header.add_history('Processed by Deep-Sky Processor')
    out_header.add_history('Linear range preserved: float32 output')
    if data_scale is not None and data_scale != 1.0:
        out_header.add_history(f'Restored original scale: {data_scale:.6e}')

    hdu = fits.PrimaryHDU(img_out, header=out_header)
    hdu.writeto(filepath, overwrite=True)

    # 统计信息
    dmin, dmax = float(img_out.min()), float(img_out.max())
    print(f"[FITS] 输出: {filepath}  BITPIX=-32  范围=[{dmin:.4f}, {dmax:.4f}]  "
          f"形状={img_out.shape}")


def normalize_target_name(name):
    """规范化天体名称，以便进行匹配（如 M 42 -> M42, ngc-7000 -> NGC7000）"""
    import re
    if not name:
        return ""
    name = str(name).strip()

    # 常见俗名到标准星表编号的映射
    synonyms = {
        "ORION NEBULA": "M42",
        "ORION_NEBULA": "M42",
        "ANDROMEDA GALAXY": "M31",
        "ANDROMEDA_GALAXY": "M31",
        "TRIANGULUM GALAXY": "M33",
        "TRIANGULUM_GALAXY": "M33",
        "WHIRLPOOL GALAXY": "M51",
        "WHIRLPOOL_GALAXY": "M51",
        "EAGLE NEBULA": "M16",
        "EAGLE_NEBULA": "M16",
        "CRAB NEBULA": "M1",
        "CRAB_NEBULA": "M1",
        "LAGOON NEBULA": "M8",
        "LAGOON_NEBULA": "M8",
        "DUMBBELL NEBULA": "M27",
        "DUMBBELL_NEBULA": "M27",
        "RING NEBULA": "M57",
        "RING_NEBULA": "M57",
        "PLEIADES": "M45",
        "NORTH AMERICA NEBULA": "NGC7000",
        "NORTH_AMERICA_NEBULA": "NGC7000",
        "IRIS NEBULA": "NGC7023",
        "IRIS_NEBULA": "NGC7023",
        "CRESCENT NEBULA": "NGC6888",
        "CRESCENT_NEBULA": "NGC6888",
        "HORSEHEAD NEBULA": "IC434",
        "HORSEHEAD_NEBULA": "IC434",
    }

    upper_name = name.upper()
    if upper_name in synonyms:
        return synonyms[upper_name]

    # 去除非标准分隔符并提取 M/NGC/IC/B/LDN/SH2 编号
    pattern = re.compile(r'^(M|NGC|IC|B|LDN|SH2|CED|SH-2|VDB)\s*[-_]?\s*(\d+)$', re.IGNORECASE)
    match = pattern.match(name)
    if match:
        prefix = match.group(1).upper()
        if prefix == "SH-2":
            prefix = "SH2"
        number = match.group(2)
        return f"{prefix}{number}"

    return re.sub(r'\s+', '', upper_name)


# 常见深空天体本地离线数据库 (天体名 -> (标准目标类型, 标准中文/英文名))
LOCAL_CELESTIAL_DB = {
    # Messier Objects
    "M1": ("planetary_nebula", "Crab Nebula"),
    "M8": ("emission_nebula", "Lagoon Nebula"),
    "M13": ("globular_cluster", "Hercules Globular Cluster"),
    "M16": ("emission_nebula", "Eagle Nebula"),
    "M17": ("emission_nebula", "Omega Nebula"),
    "M20": ("emission_nebula", "Trifid Nebula"),
    "M27": ("planetary_nebula", "Dumbbell Nebula"),
    "M31": ("galaxy", "Andromeda Galaxy"),
    "M32": ("galaxy", "Messier 32"),
    "M33": ("galaxy", "Triangulum Galaxy"),
    "M42": ("emission_nebula", "Orion Nebula"),
    "M43": ("emission_nebula", "De Mairan's Gulf"),
    "M45": ("open_cluster", "Pleiades"),
    "M51": ("galaxy", "Whirlpool Galaxy"),
    "M57": ("planetary_nebula", "Ring Nebula"),
    "M81": ("galaxy", "Bode's Galaxy"),
    "M82": ("galaxy", "Cigar Galaxy"),
    "M101": ("galaxy", "Pinwheel Galaxy"),
    "M104": ("galaxy", "Sombrero Galaxy"),
    "M106": ("galaxy", "Messier 106"),
    "M2": ("globular_cluster", "Messier 2"),
    "M3": ("globular_cluster", "Messier 3"),
    "M4": ("globular_cluster", "Messier 4"),
    "M5": ("globular_cluster", "Messier 5"),
    "M9": ("globular_cluster", "Messier 9"),
    "M10": ("globular_cluster", "Messier 10"),
    "M11": ("open_cluster", "Wild Duck Cluster"),
    "M12": ("globular_cluster", "Messier 12"),
    "M15": ("globular_cluster", "Messier 15"),
    "M22": ("globular_cluster", "Messier 22"),
    "M29": ("open_cluster", "Messier 29"),
    "M34": ("open_cluster", "Messier 34"),
    "M35": ("open_cluster", "Messier 35"),
    "M39": ("open_cluster", "Messier 39"),
    "M41": ("open_cluster", "Messier 41"),
    "M44": ("open_cluster", "Beehive Cluster"),
    "M53": ("globular_cluster", "Messier 53"),
    "M63": ("galaxy", "Sunflower Galaxy"),
    "M64": ("galaxy", "Black Eye Galaxy"),
    "M67": ("open_cluster", "Messier 67"),
    "M74": ("galaxy", "Messier 74"),
    "M77": ("galaxy", "Messier 77"),
    "M78": ("reflection_nebula", "Messier 78"),
    "M83": ("galaxy", "Southern Pinwheel Galaxy"),
    "M92": ("globular_cluster", "Messier 92"),
    "M94": ("galaxy", "Messier 94"),
    "M97": ("planetary_nebula", "Owl Nebula"),

    # NGC Objects
    "NGC7000": ("emission_nebula", "North America Nebula"),
    "NGC6960": ("emission_nebula", "Western Veil Nebula"),
    "NGC6992": ("emission_nebula", "Eastern Veil Nebula"),
    "NGC6995": ("emission_nebula", "Bat Nebula"),
    "NGC2237": ("emission_nebula", "Rosette Nebula"),
    "NGC2244": ("open_cluster", "Rosette Cluster"),
    "NGC6888": ("emission_nebula", "Crescent Nebula"),
    "NGC7023": ("reflection_nebula", "Iris Nebula"),
    "NGC7635": ("emission_nebula", "Bubble Nebula"),
    "NGC281": ("emission_nebula", "Pacman Nebula"),
    "NGC1499": ("emission_nebula", "California Nebula"),
    "NGC2174": ("emission_nebula", "Monkey Head Nebula"),
    "NGC2359": ("emission_nebula", "Thor's Helmet"),
    "NGC891": ("galaxy", "Outer Limits Galaxy"),
    "NGC253": ("galaxy", "Sculptor Galaxy"),
    "NGC4565": ("galaxy", "Needle Galaxy"),
    "NGC4631": ("galaxy", "Whale Galaxy"),
    "NGC5907": ("galaxy", "Splinter Galaxy"),
    "NGC7331": ("galaxy", "Deer Lick Group"),

    # IC Objects
    "IC1396": ("emission_nebula", "Elephant's Trunk Nebula"),
    "IC434": ("emission_nebula", "Horsehead Nebula"),
    "IC1805": ("emission_nebula", "Heart Nebula"),
    "IC1848": ("emission_nebula", "Soul Nebula"),
    "IC2118": ("reflection_nebula", "Witch Head Nebula"),
    "IC405": ("emission_nebula", "Flaming Star Nebula"),
    "IC410": ("emission_nebula", "Tadpole Nebula"),
    "IC443": ("emission_nebula", "Jellyfish Nebula"),

    # Barnard / Dark clouds
    "B33": ("dark_nebula", "Horsehead Nebula"),
    "B142": ("dark_nebula", "Barnard's E"),
    "LDN1622": ("dark_nebula", "Boogeyman Nebula"),
}

# Simbad 类别标识到标准目标类型的映射
SIMBAD_TYPE_MAPPING = {
    "G": "galaxy",
    "GL": "galaxy",
    "SYG": "galaxy",
    "AGN": "galaxy",
    "ZWG": "galaxy",
    "BLL": "galaxy",
    "QSO": "galaxy",

    "HII": "emission_nebula",
    "EMN": "emission_nebula",
    "SNR": "emission_nebula",
    "WR*": "emission_nebula",
    "BUB": "emission_nebula",
    "ISM": "emission_nebula",

    "RFN": "reflection_nebula",
    "PN": "planetary_nebula",

    "DKC": "dark_nebula",
    "MOC": "dark_nebula",

    "GLC": "globular_cluster",
    "GCL": "globular_cluster",

    "OPC": "open_cluster",
    "CL*": "open_cluster",
    "ASS": "open_cluster",
}


def query_sesame_online(name, timeout=3.0):
    """通过 CDS Sesame 命名解析器查询天体属性"""
    import urllib.request
    import urllib.parse
    import xml.etree.ElementTree as ET

    url = f"http://cdsweb.u-strasbg.fr/cgi-bin/nph-sesame/-ox/S?{urllib.parse.quote(name)}"
    headers = {"User-Agent": "Mozilla/5.0 Deep-Sky Processor Target Resolver"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            xml_data = response.read()
            return parse_sesame_xml(xml_data)
    except Exception as e:
        print(f"[自适应] 无法在线解析天体 '{name}': {e}")
        return None


def parse_sesame_xml(xml_bytes):
    """解析 Sesame 返回的 XML 格式响应"""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_bytes)
        target = root.find(".//Target")
        if target is None:
            return None

        res = {}
        # 提取 J2000 赤经赤纬度数
        ra_el = target.find(".//jradeg")
        dec_el = target.find(".//jdecdeg")
        if ra_el is not None and dec_el is not None:
            res["ra_deg"] = float(ra_el.text)
            res["dec_deg"] = float(dec_el.text)

        # 提取 Simbad 物理类型
        resolver = target.find(".//Resolver[@name='Simbad']")
        if resolver is not None:
            class_el = resolver.find("class")
            type_el = resolver.find("type")
            res_class = class_el.text if class_el is not None else None
            res_type = type_el.text if type_el is not None else None
            res["simbad_class"] = res_class or res_type

        # 提取官方规范化名称
        name_el = target.find("name")
        if name_el is not None:
            res["standard_name"] = name_el.text

        return res
    except Exception as e:
        print(f"[自适应] Sesame XML 解析失败: {e}")
        return None


def resolve_celestial_target(header=None, target_name=None):
    """
    智能天体属性识别入口。
    结合本地库与 Sesame 在线解析，自动解析天体类型与真实名称。

    返回:
      dict: {
        'resolved_name': 标准化后的天体名称 (如 'M42')
        'resolved_type': 八大天体类型之一
        'source': 'local_db' | 'online_sesame' | 'fallback'
        'ra_raw': Header 中的原始 RA 字段
        'dec_raw': Header 中的原始 DEC 字段
        'ra_deg': 在线解析获取的 RA 度数 (float)
        'dec_deg': 在线解析获取的 DEC 度数 (float)
      }
    """
    if not target_name and header:
        target_name = header.get('OBJECT') or header.get('TARGNAME')

    raw_name = target_name or ""
    normalized_name = normalize_target_name(raw_name)

    result = {
        'resolved_name': raw_name or "unknown",
        'resolved_type': 'unknown_deep_sky',
        'source': 'fallback',
        'ra_raw': None,
        'dec_raw': None,
        'ra_deg': None,
        'dec_deg': None,
    }

    # 提取 Header 的原始坐标
    if header:
        result['ra_raw'] = header.get('OBJCTRA') or header.get('RA')
        result['dec_raw'] = header.get('OBJCTDEC') or header.get('DEC')

    if not normalized_name:
        return result

    # 1. 尝试本地库检索
    if normalized_name in LOCAL_CELESTIAL_DB:
        target_type, standard_name = LOCAL_CELESTIAL_DB[normalized_name]
        result['resolved_type'] = target_type
        result['resolved_name'] = normalized_name
        result['source'] = 'local_db'
        print(f"[自适应] 识别天体（本地库命）：{raw_name} -> {normalized_name} ({target_type})")
        return result

    # 2. 尝试在线解析
    print(f"[自适应] 本地未命中，尝试在线解析：{raw_name} (标准化：{normalized_name})")
    online_res = query_sesame_online(normalized_name) or query_sesame_online(raw_name)

    if online_res:
        standard_name = online_res.get('standard_name', normalized_name)
        simbad_class = online_res.get('simbad_class')

        result['resolved_name'] = standard_name
        result['ra_deg'] = online_res.get('ra_deg')
        result['dec_deg'] = online_res.get('dec_deg')

        if simbad_class:
            mapped_type = SIMBAD_TYPE_MAPPING.get(simbad_class.upper())
            if not mapped_type:
                # 前缀部分匹配（例如 GCl -> GLC）
                for prefix, t_type in SIMBAD_TYPE_MAPPING.items():
                    if simbad_class.upper().startswith(prefix):
                        mapped_type = t_type
                        break

            if mapped_type:
                result['resolved_type'] = mapped_type
                result['source'] = 'online_sesame'
                print(f"[自适应] 识别天体（在线解析）：{raw_name} -> {standard_name} ({mapped_type})")
                return result
            else:
                print(f"[自适应] 在线解析完成，但无法映射 Simbad 分类: '{simbad_class}'")
        else:
            print(f"[自适应] 在线解析完成，但缺少 Simbad 物理分类")

    # 3. 回退
    result['source'] = 'fallback'
    return result


def get_fits_info(filepath):
    """快速获取 FITS 文件信息 (不加载全部数据)"""
    from astropy.io import fits

    with fits.open(filepath, memmap=False) as hdul:
        info = []
        for i, hdu in enumerate(hdul):
            h = hdu.header
            entry = {
                'hdu': i,
                'name': hdu.name,
                'shape': hdu.data.shape if hdu.data is not None else None,
                'dtype': str(hdu.data.dtype) if hdu.data is not None else None,
                'naxis': h.get('NAXIS', 0),
                'naxis1': h.get('NAXIS1', 0),
                'naxis2': h.get('NAXIS2', 0),
                'naxis3': h.get('NAXIS3', 0),
                'exposure': h.get('EXPTIME', h.get('EXPOSURE', None)),
                'filter': h.get('FILTER', None),
                'object': h.get('OBJECT', None),
                'date_obs': h.get('DATE-OBS', None),
                'bscale': h.get('BSCALE', 1.0),
                'bzero': h.get('BZERO', 0.0),
            }
            info.append(entry)
    return info


if __name__ == '__main__':
    # 测试：读取 FITS 文件信息
    import argparse
    p = argparse.ArgumentParser(description='FITS/图像 I/O 工具')
    p.add_argument('filepath', help='图像文件路径')
    p.add_argument('--info', action='store_true', help='仅显示文件信息')
    args = p.parse_args()

    if args.info:
        info = get_fits_info(args.filepath)
        for entry in info:
            print(f"\nHDU #{entry['hdu']} ({entry['name']}):")
            for k, v in entry.items():
                if v is not None:
                    print(f"  {k}: {v}")
    else:
        img, meta = read_image(args.filepath)
        print(f"\n格式: {meta['format']}")
        print(f"形状: {img.shape}")
        print(f"数据范围: [{img.min():.6f}, {img.max():.6f}]")
        print(f"均值: {img.mean():.6f}")
        print(f"线性数据: {meta['is_linear']}")
        if meta['header']:
            obj = meta['header'].get('OBJECT', 'N/A')
            exp = meta['header'].get('EXPTIME', 'N/A')
            print(f"天体: {obj}")
            print(f"曝光: {exp}s")
