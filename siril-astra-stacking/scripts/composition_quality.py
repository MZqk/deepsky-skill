"""Bounded-memory coverage and display statistics for prepared channels."""
from __future__ import annotations

import math
from pathlib import Path
import statistics
import struct

from fits_probe import FitsError, data_offset, read_fits_header_with_warnings


def image_rows(path, rect=None):
    """Yield physical values in Siril's top-left order, plane by plane."""
    header, _ = read_fits_header_with_warnings(str(path))
    if header.get("BITPIX") != -32 or header.get("NAXIS") not in (2, 3):
        raise FitsError("expected Float32 composition image")
    width, height = header["NAXIS1"], header["NAXIS2"]
    planes = 1 if header["NAXIS"] == 2 else header["NAXIS3"]
    rect = rect or dict(x=0, y=0, width=width, height=height)
    x, y, w, h = (rect[key] for key in ("x", "y", "width", "height"))
    if min(x, y) < 0 or min(w, h) < 1 or x + w > width or y + h > height:
        raise FitsError("statistics region lies outside image")
    offset = data_offset(str(path))
    scale, zero = header.get("BSCALE", 1), header.get("BZERO", 0)
    with Path(path).open("rb") as handle:
        for plane in range(planes):
            for row in range(h):
                stored_y = height - 1 - y - row
                handle.seek(offset + ((plane * height + stored_y) * width + x) * 4)
                raw = handle.read(w * 4)
                if len(raw) != w * 4:
                    raise FitsError("truncated composition image")
                values = struct.unpack(">%df" % w, raw)
                if scale != 1 or zero != 0:
                    values = tuple(value * scale + zero for value in values)
                yield plane, row, values


def largest_rectangle(rows, width):
    """Find the largest rectangle in contiguous row intervals; ties favor top-left."""
    heights = [0] * width
    best = None
    best_key = (0, 0, 0, 0)
    end = 0
    index = 0
    while index < len(rows):
        left, right = rows[index]
        stop = index + 1
        while stop < len(rows) and rows[stop] == rows[index]:
            stop += 1
        count = stop - index
        end += count
        # Repeated intervals are one weighted histogram row, important for large masters.
        for x in range(width):
            heights[x] = heights[x] + count if left <= x < right else 0
        stack = []
        for x in range(width + 1):
            height = heights[x] if x < width else 0
            start = x
            while stack and stack[-1][1] > height:
                start, previous = stack.pop()
                w, y = x - start, end - previous
                key = (w * previous, -y, -start, w)
                if key > best_key:
                    best_key = key
                    best = dict(x=start, y=y, width=w, height=previous)
            if height and (not stack or stack[-1][1] < height):
                stack.append((start, height))
        index = stop
    return best


def edge_coverage(path):
    # shortcut: nonzero padding and internal holes need explicit masks when true coverage is required.
    header, _ = read_fits_header_with_warnings(str(path))
    if header.get("NAXIS") != 2:
        raise FitsError("coverage requires a monochrome channel")
    width, height = header["NAXIS1"], header["NAXIS2"]
    rows = []
    for _, _, values in image_rows(path):
        left, right = 0, width
        while left < right and values[left] == 0:
            left += 1
        while right > left and values[right - 1] == 0:
            right -= 1
        rows.append((left, right))
    candidate = largest_rectangle(rows, width)
    count = sum(right - left for left, right in rows)
    return rows, {
        "method": "edge_zero_runs", "coordinate_origin": "siril_top_left",
        "input_size": [width, height], "candidate_rect": candidate,
        "edge_zero_count": width * height - count,
        "candidate_loss_pct": 100 * (1 - candidate["width"] * candidate["height"] / (width * height))
        if candidate else 100.0,
        "heuristic": True,
    }


def common_coverage(intervals, width):
    rows = [(max(row[0] for row in group), min(row[1] for row in group))
            for group in zip(*intervals)]
    return largest_rectangle(rows, width)


def sample_statistics(path, rect, max_samples=200000):
    header, _ = read_fits_header_with_warnings(str(path))
    names = ("m",) if header["NAXIS"] == 2 else ("r", "g", "b")
    budget = max_samples // len(names)
    stride = max(1, math.ceil(rect["width"] * rect["height"] / budget))
    samples = {name: [] for name in names}
    for plane, row, values in image_rows(path, rect):
        start = (-row * rect["width"]) % stride
        samples[names[plane]].extend(values[start::stride])
    channels = {}
    for name, values in samples.items():
        if not values or not all(math.isfinite(value) for value in values):
            raise FitsError("no finite samples for composition statistics")
        median = statistics.median(values)
        mad = statistics.median(abs(value - median) for value in values)
        channels[name] = dict(sample_count=len(values), median=median, mad=mad, sigma=1.4826 * mad)
    return dict(region=rect, sample_budget=max_samples, channels=channels)


def background_mtf(stats, target):
    channels = list(stats["channels"].values())
    median = min(channel["median"] for channel in channels)
    low = max(0.0, min(channel["median"] - 2.8 * channel["sigma"] for channel in channels))
    warnings = []
    if low >= median and any(channel["mad"] == 0 for channel in channels):
        low = 0.0
        warnings.append("zero MAD: background black point reset to zero")
    if low >= 1:
        raise FitsError("background stretch has no valid normalized median")
    x = (median - low) / (1 - low)
    if not 0 < x < 1:
        raise FitsError("background stretch requires normalized median in (0,1)")
    mid = x * (1 - target) / (x + target - 2 * x * target)
    if not math.isfinite(mid) or not 0 < mid < 1:
        raise FitsError("background stretch produced an invalid midpoint")
    return {
        "method": "mtf", "strategy": "background", "low": low, "mid": mid, "high": 1.0,
        "target_bg": target, "statistics": stats,
        "derived_from": "minimum ROI median; black point = min(median - 2.8 * 1.4826 * MAD)",
    }, warnings


def clipping_counts(path, rect, low, high):
    below = above = total = 0
    for _, _, values in image_rows(path, rect):
        total += len(values)
        below += sum(value < low for value in values)
        above += sum(value > high for value in values)
    return dict(region=rect, pixel_count=total, below_low=below, above_high=above,
                low=low, high=high, interpretation="display clipping risk, not sensor saturation")
