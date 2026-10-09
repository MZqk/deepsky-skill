"""FITS header probing and sequence-file (``.seq``) parsing for Siril 1.4.

This module is deliberately dependency-free: it uses only the Python standard
library so that it runs both outside Siril and inside ``pyscript``. There is no
optional astropy cross-check -- every header field is parsed by the hand-rolled
reader below, so its behaviour does not vary with what happens to be installed.

Two responsibilities live here:

1. :func:`read_fits_header` -- parse the primary HDU header of a FITS file into a
   plain dict, tolerating real-world defects such as trailing blanks in string
   values and the garbage ``EQUINOX=9.87654321E+107`` that Siril itself writes.
2. :func:`read_seq_regdata` -- parse a Siril ``.seq`` sidecar file and return the
   per-frame registration data (FWHM, wFWHM, roundness, background, star count
   and the homography coefficients).

Why ``.seq`` instead of ``sirilpy``
----------------------------------
The documented way to read registration data back is
``sirilpy.SirilInterface.get_seq_regdata(frame, channel)``. That path requires a
live socket between the Python process and the Siril process. In Siril 1.4.4 CLI
mode that socket is not always established: ``pyscript`` fails with
``SirilConnectionError: Error in _send_command(): 'NoneType' object has no
attribute 'sendall'``. The ``.seq`` file, by contrast, is written by ``register``
in every run and is plain line-oriented text, so parsing it works in all cases.

Sign convention (calibrated empirically)
----------------------------------------
The homography maps a frame onto the reference frame as::

    [x']   [h00 h01 h02] [x]
    [y'] = [h10 h11 h12]·[y]
    [w']   [h20 h21 h22] [1]

Calibration against a synthetic sequence with known per-frame translations
(dx=+3i, dy=-2i) produced::

    frame 1: h02=+3.01769  h12=+2.01224     (truth   +3, -2)
    frame 2: h02=+6.01660  h12=+4.01090     (truth   +6, -4)
    frame 3: h02=+9.01816  h12=+5.99981     (truth   +9, -6)

Hence ``dx = h02`` and ``dy = h12`` with **no sign flip**. Rotation is
``atan2(h10, h00)`` and the scale factor is ``sqrt(det(M))`` where
``M = [[h00, h01], [h10, h11]]``. These relations are asserted by
``selftest.py`` against freshly generated synthetic data; never hard-code an
assumed sign.

Usage:
    from fits_probe import read_fits_header, read_seq_regdata
    header = read_fits_header("M42_001.fit")
    reg = read_seq_regdata("cal.seq")
"""

from __future__ import annotations

import math
import os
import re
import struct

BLOCK_SIZE = 2880
CARD_SIZE = 80

#: Suffixes accepted as FITS input. Everything else is rejected outright.
FITS_SUFFIXES = (".fit", ".fits", ".fts")

#: Suffixes we recognise but refuse, so the error can name the reason.
REJECTED_SUFFIXES = {
    ".avi": "video container",
    ".mov": "video container",
    ".mp4": "video container",
    ".mkv": "video container",
    ".ser": "SER sequence",
    ".jpg": "8-bit preview image",
    ".jpeg": "8-bit preview image",
    ".png": "8-bit preview image",
    ".webp": "8-bit preview image",
}

#: FITS keywords whose numeric value must stay within a sane range. Siril emits
#: out-of-range values such as ``EQUINOX = 9.87654321E+107``; these are recorded
#: as warnings instead of aborting the parse.
_NUMERIC_SANITY_LIMITS = {
    "EQUINOX": (0.0, 100.0),
    "RADESYS": None,
}

_HIERARCH_RE = re.compile(r"^HIERARCH\s+(\S+)\s*=\s*(.*)$")
_STRING_RE = re.compile(r"^'((?:[^']|'')*)'\s*(?:/(.*))?$")


class FitsError(Exception):
    """Raised when a file cannot be parsed as FITS at all."""


class UnsupportedInputError(Exception):
    """Raised for inputs this tool refuses to guess about (video, previews)."""


def pad_to_block(payload: bytes) -> bytes:
    """Pad ``payload`` up to the next 2880-byte FITS block boundary."""
    remainder = len(payload) % BLOCK_SIZE
    if remainder == 0:
        return payload
    return payload + b"\x00" * (BLOCK_SIZE - remainder)


def _coerce_number(text: str):
    """Parse a FITS numeric field, tolerating Fortran ``D`` exponents."""
    cleaned = text.strip().replace("D", "E").replace("d", "e")
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        return float(cleaned)
    except ValueError:
        return None


def _iter_cards(blob: bytes):
    """Yield raw 80-character header cards, stopping after ``END``."""
    for offset in range(0, len(blob), CARD_SIZE):
        card = blob[offset:offset + CARD_SIZE].decode("ascii", "replace")
        if card.startswith("END") and card[3:].strip() == "":
            return
        yield card


def _flush_continuation(header, key, chunks):
    if key is not None and chunks:
        header[key] = "".join(chunks).strip()


def read_fits_header(path: str):
    """Read the primary HDU header of a FITS file.

    Args:
        path: Path to a ``.fit`` / ``.fits`` / ``.fts`` file.

    Returns:
        A ``dict`` of header keywords. String values are right-stripped of
        trailing blanks (real headers carry ``BAYERPAT = 'GRBG    '``).
        Numeric fields that violate sanity limits are still returned as parsed;
        :func:`read_fits_header_with_warnings` reports them.

    Raises:
        UnsupportedInputError: for video / SER / preview-image suffixes.
        FitsError: when the file is not parseable as FITS.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix in REJECTED_SUFFIXES:
        raise UnsupportedInputError(
            "%s is a %s; this tool only accepts FITS light frames "
            "(.fit/.fits/.fts). Preflight the file manually if you believe it "
            "contains usable data." % (os.path.basename(path), REJECTED_SUFFIXES[suffix])
        )
    if suffix not in FITS_SUFFIXES:
        raise UnsupportedInputError(
            "%s has unsupported extension '%s'; expected one of %s"
            % (os.path.basename(path), suffix, ", ".join(FITS_SUFFIXES))
        )

    header = {}
    try:
        with open(path, "rb") as handle:
            blob = handle.read(BLOCK_SIZE * 64)
    except OSError as exc:
        raise FitsError("cannot read %s: %s" % (path, exc)) from exc

    if not blob.startswith(b"SIMPLE  ="):
        raise FitsError(
            "%s does not start with a FITS SIMPLE card; it may be truncated "
            "or not a FITS file" % os.path.basename(path)
        )

    comment_lines = []
    pending_key = None
    pending_chunks = None

    for card in _iter_cards(blob):
        key = card[:8].strip()
        if key in ("COMMENT", "HISTORY"):
            comment_lines.append(card[8:].rstrip())
            continue
        if key in ("CONTINUE", "CONTINUATION") and pending_chunks is not None:
            pending_chunks.append(card[8:])
            continue

        _flush_continuation(header, pending_key, pending_chunks)
        pending_key, pending_chunks = None, None

        if not key:
            continue

        if card[8:10] == "= ":
            name = key
            rest = card[10:]
            value_field = rest[:20].split("/", 1)[0]
            match = _STRING_RE.match(rest)
            if match:
                header[name] = match.group(1).replace("''", "'").rstrip()
                pending_key, pending_chunks = name, []
            else:
                number = _coerce_number(value_field)
                if number is None:
                    header[name] = value_field.strip()
                else:
                    header[name] = number
        else:
            hierarchy = _HIERARCH_RE.match(card)
            if hierarchy:
                header[hierarchy.group(1)] = hierarchy.group(2).split("/")[0].strip().rstrip()

    _flush_continuation(header, pending_key, pending_chunks)

    if comment_lines:
        header["_COMMENT_LINES"] = comment_lines
    return header


def read_fits_header_with_warnings(path: str):
    """Return ``(header, warnings)`` for ``path``.

    Out-of-range numeric fields produce warnings rather than exceptions, because
    Siril writes such values itself and refusing to parse them would make real
    data unusable.
    """
    header = read_fits_header(path)
    warnings = []
    for keyword, limits in _NUMERIC_SANITY_LIMITS.items():
        if limits is None or keyword not in header:
            continue
        value = header[keyword]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        low, high = limits
        if not (low <= value <= high):
            warnings.append(
                "%s=%r is outside the plausible range [%s, %s]; parsed but ignored"
                % (keyword, value, low, high)
            )
    return header, warnings


def read_seq_regdata(path: str):
    """Parse a Siril ``.seq`` file into per-frame registration data.

    The format is line oriented. Registration rows look like::

        R0 4.56 4.56 0.999269 0 0.535818 6 H 1 0 0 0 1 0 0 0 1

    that is ``R<channel> fwhm wfwhm roundness quality background nbstars H h00
    .. h22 pair_matched inliers``.

    Args:
        path: Path to the ``.seq`` file next to the sequence frames.

    Returns:
        ``{"frames": [FrameReg, ...], "reference_image": int|None,
        "drizzle_flag": int, "warnings": [...]}`` where ``FrameReg`` is a dict
        with ``index``, ``fwhm``, ``weighted_fwhm``, ``roundness``,
        ``background_lvl``, ``number_of_stars``, the ``h`` homography tuple,
        ``pair_matched``, ``inliers`` and the derived ``dx``/``dy``/``rotation``
        /``scale``.
    """
    if not os.path.exists(path):
        raise FitsError("sequence file not found: %s" % path)

    frames = []
    rows = []
    image_order = []
    warnings = []
    reference_image = None
    drizzle_flag = 0

    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("S "):
                # Verified column layout of the S record:
                #   [0]S [1]'name' [2]start [3]nb_images [4]nb_selected
                #   [5]fixed_len [6]reference_image [7]version
                #   [8]variable_size [9]fz_flag [10]drizzle
                parts = line.split()
                if len(parts) >= 8:
                    reference_image = _coerce_number(parts[6])
                if len(parts) > 10:
                    drizzle_flag = _coerce_number(parts[10]) or 0
                continue
            if line.startswith("I "):
                parts = line.split()
                if len(parts) >= 2:
                    frame_index = _coerce_number(parts[1])
                    if frame_index is not None:
                        image_order.append(int(frame_index))
                continue
            if not line.startswith("R"):
                continue
            if line.startswith("R_DRIZZLE") or line.startswith("RREF"):
                continue

            parts = line.split()
            # R<channel> fwhm wfwhm roundness quality background nbstars H h*...
            if len(parts) < 17 or parts[7] != "H":
                continue
            try:
                channel = int(parts[0][1:])
                h = tuple(float(v) for v in parts[8:17])
                entry = {
                    "channel": channel,
                    "fwhm": float(parts[1]),
                    "weighted_fwhm": float(parts[2]),
                    "roundness": float(parts[3]),
                    "quality": float(parts[4]),
                    "background_lvl": float(parts[5]),
                    "number_of_stars": int(float(parts[6])),
                    "h": h,
                    "pair_matched": int(float(parts[17])) if len(parts) > 17 else 0,
                    "inliers": int(float(parts[18])) if len(parts) > 18 else 0,
                }
            except (ValueError, IndexError):
                warnings.append("unparsable registration row: %s" % line)
                continue
            entry.update(derive_transform(entry["h"]))
            rows.append((channel, entry))

    # ``R<channel>`` carries the layer, not the frame index, and after ``-2pass``
    # the reference frame is the best-quality one -- which is usually not file
    # position0. Rows are therefore ordered by matching the ``I`` records, which
    # do carry the frame index in file order.
    #
    # Transform semantics: each homography maps its own frame onto the reference
    # frame, so displacements are expressed relative to the reference and may be
    # negative relative to the scene's own progression. Only the *span* of the
    # displacement set is meaningful for mosaic detection, never the sign.
    if image_order:
        for position, frame_index in enumerate(image_order):
            if position < len(rows):
                rows[position][1]["index"] = frame_index
                rows[position][1]["row_order"] = position
    else:
        for position, (_channel, entry) in enumerate(rows):
            entry["index"] = position
            entry["row_order"] = position

    frames = [entry for _channel, entry in rows]
    return {
        "frames": frames,
        "reference_image": reference_image,
        "drizzle_flag": drizzle_flag,
        "image_order": image_order,
        "warnings": warnings,
    }


def derive_transform(h):
    """Derive displacement, rotation and scale from homography coefficients.

    Args:
        h: Sequence ``(h00, h01, h02, h10, h11, h12, h20, h21, h22)``.

    Returns:
        Dict with ``dx``, ``dy``, ``rotation`` (radians), ``rotation_deg``,
        ``scale``, ``is_pure_translation`` and ``has_projection``.
        ``dx``/``dy`` follow the empirically calibrated relation ``dx = h02``,
        ``dy = h12`` (no sign flip).
    """
    h00, h01, h02, h10, h11, h12 = h[0], h[1], h[2], h[3], h[4], h[5]
    determinant = h00 * h11 - h01 * h10
    scale = math.sqrt(abs(determinant)) if determinant > 0 else float("nan")
    return {
        "dx": h02,
        "dy": h12,
        "rotation": math.atan2(h10, h00),
        "rotation_deg": math.degrees(math.atan2(h10, h00)),
        "scale": scale,
        "is_pure_translation": (
            abs(h00 - 1.0) < 1e-3
            and abs(h11 - 1.0) < 1e-3
            and abs(h01) < 1e-4
            and abs(h10) < 1e-4
        ),
        "has_projection": abs(h[6]) > 1e-9 or abs(h[7]) > 1e-9,
    }


def summarize_transforms(regdata, fov_width, fov_height, mosaic_factor=1.2):
    """Aggregate per-frame transforms into the quantities the pipeline thresholds on.

    ``span`` is computed as the reference-frame-independent range of the
    displacement vectors, so that a reference frame sitting in the middle of a
    mosaic still triggers the mosaic branch (unlike ``max(|dx|, |dy|)``).

    Args:
        regdata: Output of :func:`read_seq_regdata`.
        fov_width: Frame width in pixels.
        fov_height: Frame height in pixels.
        mosaic_factor: Multiple of the field of frame beyond which the sequence
            counts as a mosaic. The owning constant is
            ``param_derive.MOSAIC_SPAN_FACTOR``; it is injected rather than
            imported so this module stays free of policy dependencies.

    Returns:
        Dict with ``span_x``, ``span_y``, ``span`` (max of the two),
        ``rotation_max_deg``, ``scale_dev_max``, ``median_scale``,
        ``mosaic`` (span > ``mosaic_factor`` * FOV) and ``frame_count``.
    """
    frames = regdata.get("frames", [])
    if not frames:
        return {
            "span_x": 0.0, "span_y": 0.0, "span": 0.0,
            "rotation_max_deg": 0.0, "scale_dev_max": 0.0,
            "median_scale": 1.0, "mosaic": False, "frame_count": 0,
        }

    xs = [f["dx"] for f in frames]
    ys = [f["dy"] for f in frames]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)

    scales = [f["scale"] for f in frames if not math.isnan(f["scale"])]
    median_scale = sorted(scales)[len(scales) // 2] if scales else 1.0
    scale_dev_max = max(
        (abs(s - median_scale) / median_scale for s in scales), default=0.0
    )

    span = max(span_x, span_y)
    fov = max(fov_width, fov_height, 1)
    return {
        "span_x": span_x,
        "span_y": span_y,
        "span": span,
        "rotation_max_deg": max(abs(f["rotation_deg"]) for f in frames),
        "scale_dev_max": scale_dev_max,
        "median_scale": median_scale,
        "mosaic": span > mosaic_factor * fov,
        "frame_count": len(frames),
    }


def build_card(key, value, comment=""):
    """Format a single 80-character FITS header card.

    Follows the convention Siril itself writes: the keyword occupies columns 1-8,
    ``= `` sits at columns 9-10, and the value starts at column 11. Numeric values
    are right-aligned within the 20-character field while string values (already
    including their surrounding single quotes) are left-aligned, matching real
    headers such as ``TELESCOP= 'S30 Pro_10d57b1d'``.

    ``END`` is emitted as a bare, blank-padded card rather than ``END = ...``;
    callers must not strip it, since every card occupies exactly 80 bytes and the
    data unit starts at the next block boundary.
    """
    if key == "END":
        return "END".ljust(CARD_SIZE)
    is_string = isinstance(value, str) and value.startswith("'")
    field = "%-20s" % value if is_string else "%20s" % value
    text = "%-8s= %s" % (key[:8], field)
    if comment:
        text = "%s / %s" % (text, comment)
    return text[:CARD_SIZE].ljust(CARD_SIZE)


def data_offset(path):
    """Return the byte offset of the primary HDU's data unit.

    Args:
        path: Path to a FITS file.

    Returns:
        Byte offset of the data, which is the header length rounded up to a
        2880-byte block boundary.

    Raises:
        FitsError: when no ``END`` card is found.
    """
    with open(path, "rb") as handle:
        header = handle.read(BLOCK_SIZE * 8)
    for offset in range(0, len(header), CARD_SIZE):
        card = header[offset:offset + CARD_SIZE].decode("ascii", "replace")
        if card.startswith("END") and card[3:].strip() == "":
            return ((offset + BLOCK_SIZE - 1) // BLOCK_SIZE) * BLOCK_SIZE
    raise FitsError("%s: no END card found in the first %d bytes" % (path, len(header)))


def estimate_channel_medians(path, max_samples=200000):
    """Sample physical RGB values from a planar Float32 FITS primary image.

    FITS stores x fastest, then y, then the channel planes. The same spatial
    positions are sampled in each plane within the total sample budget. These
    are whole-image medians (including padding), not a sky-background estimate.
    """
    header = read_fits_header(path)
    if (header.get("BITPIX") != -32 or header.get("NAXIS") != 3
            or header.get("NAXIS3") != 3):
        return {}
    width, height = header.get("NAXIS1"), header.get("NAXIS2")
    if type(width) is not int or type(height) is not int or min(width, height) < 1:
        raise FitsError("invalid RGB image dimensions")
    if type(max_samples) is not int or max_samples < 3:
        raise ValueError("RGB sampling budget must be at least three")
    scale, zero = header.get("BSCALE", 1), header.get("BZERO", 0)
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (scale, zero)):
        raise FitsError("invalid FITS pixel scaling")
    pixels = width * height
    stride = max(1, (pixels + max_samples // 3 - 1) // (max_samples // 3))
    row_bytes, plane_bytes = width * 4, pixels * 4
    offset = data_offset(path)
    if os.path.getsize(path) < offset + plane_bytes * 3:
        raise FitsError("truncated RGB FITS primary image")
    medians = {}
    with open(path, "rb") as handle:
        for channel, name in enumerate(("r", "g", "b")):
            collected = []
            for y in range(height):
                first = (-y * width) % stride
                if first >= width:
                    continue
                handle.seek(offset + channel * plane_bytes + y * row_bytes)
                row = handle.read(row_bytes)
                if len(row) != row_bytes:
                    raise FitsError("truncated RGB FITS row")
                values = struct.unpack(">%df" % width, row)
                for x in range(first, width, stride):
                    value = values[x] * scale + zero
                    if math.isfinite(value):
                        collected.append(value)
            if collected:
                import statistics
                medians[name] = statistics.median(collected)
    return medians
