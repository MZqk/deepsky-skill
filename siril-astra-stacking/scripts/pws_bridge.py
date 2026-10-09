"""PWS hand-off bridge: hand Siril's registered frames to the PWS engine.

``--stack-engine=pws`` replaces Siril's ``stack`` step with the Partition-Weighted
Stacking engine (D.Cikey, MIT-licensed implementation vendored by the sibling
``pws-stacking`` skill). This module owns everything the hand-off needs beyond
the Siril front end:

* :func:`evaluate_gate` -- measured, per-run viability gate. PWS's gain is driven
  by frame-to-frame quality dispersion (paper section 6.1: C_i span 2.81x gave
  43.8 % peak improvement, 1.35x gave 3.7 %). Frames below the dispersion floor
  gain almost nothing, and frames below the count floor cannot express
  dispersion at all. The gate is computed from the same ``.seq`` regdata the
  chain decision already uses, so it costs nothing extra.
* :func:`prepare_pws_input` -- materialise a clean single-sequence directory of
  the ``ap_c_<stem>`` frames (renamed ``pwsNNNN.fit``, zero-indexed and gapless,
  because the engine reads the directory and stores frames by index).
* :func:`resolve_pws_engine` -- locate the sibling ``pws-stacking`` skill and
  refuse (never degrade) when its runner is missing or its third-party stack is
  not importable.
* :func:`convert_xisf_master` -- a stdlib-only reader for the subset of XISF the
  vendored engine writes (``XISF0100``, one uncompressed planar Float32 gray
  image), converting the PWS master into the 32-bit FITS this skill publishes.

Why stdlib here: this skill promises "Python 3.9+, standard library only"
(SKILL.md environment section). The numpy/scipy/astropy/photutils stack belongs
to the pws-stacking skill; this bridge only needs to recognise *that* stack's
absence and fail loudly (iron rule 2: refusal beats guessing), plus parse the
XISF container the engine emits -- which is plain XML plus a binary attachment.
"""

from __future__ import annotations

import os
import re
import struct
import xml.etree.ElementTree as ET
import zlib

from fits_probe import (
    FitsError,
    build_card as fits_probe_build_card,
    read_seq_regdata,
)

#: PWS viability gate: minimum frame count. The paper (section 3.4.1) suggests
#: N >~ 20 for the method to have something to work with; below that the
#: frame-quality dispersion the whole method feeds on cannot stabilise.
PWS_MIN_FRAMES = 20

#: PWS viability gate: weighted-FWHM dispersion floor. Peak PWS gains are tied
#: to the C_i span (paper tables 3-4: 1.35x span -> 3.7 % improvement; 2.81x ->
#: 43.8 %). A p90/p10 ratio at or below 1.4 sits in the "measured gain within
#: the noise" band, so the bridge refuses rather than burn 20+ minutes of
#: engine time on nothing.
PWS_DISPERSION_FLOOR = 1.4

#: Frame-count warning threshold: with fewer than this many frames the
#: per-frame weights rest on a thin sample even when the gate passes.
PWS_THIN_FRAMES = 30

#: Black-corner guard. A rotated frame registered onto a fixed canvas carries
#: triangular no-data regions; the engine has no coverage mask, so those
#: pixels poison its background-structure precheck (measured on the real
#: 81-frame M8 capture: the black share grows 3.5% -> 23.9% across the
#: sequence, and at ~20% the engine's precheck misreads the zero blocks as
#: "uncalibrated frames, 107% big scale structure" and refuses). Small
#: corners (<10%) stay harmless: stars are detected away from the edges and
#: the background spread remains far below its threshold, measured on the
#: synthetic 3.8-degree sequence at 2.4% black. Refusing above 10% names the
#: real cause instead of the precheck's misleading one.
PWS_BLACK_MAX_FRACTION = 0.10

#: Directory name (inside the work directory) holding the frames handed to the
#: PWS engine.
PWS_INPUT_DIRNAME = "pws_in"

#: Directory (outside the work directory, alongside the deliverables) where the
#: PWS engine writes its XISF master and diagnostics.
PWS_OUT_DIRNAME = "pws_out"

#: Temporary-frame directory the engine requires to be named explicitly (its
#: runner refuses to auto-pick a disk).
PWS_FRAMES_DIRNAME = "pws_frames"

#: Candidate locations of the sibling pws-stacking skill, relative to this
#: repository layout. The first existing ``pws_run.py`` wins.
PWS_SKILL_CANDIDATES = (
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "pws-stacking", "scripts"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "pws-stacking", "scripts"),
)


class PwsBridgeError(Exception):
    """Raised when the PWS hand-off cannot proceed.

    This maps to a clean, reported failure -- never a silent fallback to the
    Siril stack: the user explicitly asked for PWS, so quietly stacking with
    wfwhm weights instead would be fabrication.
    """


# ---------------------------------------------------------------------------
# Viability gate
# ---------------------------------------------------------------------------

def evaluate_gate(regdata, frame_kind):
    """Decide whether this run's frames give PWS something to work with.

    Args:
        regdata: Output of :func:`fits_probe.read_seq_regdata`.
        frame_kind: ``"cfa"`` or ``"rgb"`` from the pipeline structure.

    Returns:
        Dict with ``eligible``, ``verdict`` (``pass`` / ``fail`` / ``skip``),
        ``reasons`` and the measured quantities, ready to drop into
        ``report.stack_engine``.

    ``skip`` is returned when the caller should not run PWS for structural
    reasons that are nobody's fault: an RGB source with too few frames.
    """
    gate = {
        "eligible": False,
        "verdict": "pass",
        "frame_count": 0,
        "wfwhm_p10": None,
        "wfwhm_p90": None,
        "dispersion_ratio": None,
        "outlier_frames": 0,
        "reasons": [],
    }
    if frame_kind != "rgb":
        # Only the debayer/rgb chains feed PWS: the drizzle chain writes frames
        # of *differing sizes* (each mapped to its own canvas; measured on a
        # 2-degree synthetic rotation, seqapplyreg -drizzle -framing=max
        # produced 218x176 / 205x156 / 200x150), and the engine requires a
        # uniform shape. Refuse loudly instead of degrading.
        gate["verdict"] = "fail"
        gate["reasons"].append(
            "the drizzle chain writes per-frame canvases of differing sizes, "
            "which the PWS engine cannot consume; run the default Siril stack "
            "for mosaic sequences"
        )
        return gate

    frames = (regdata or {}).get("frames") or []
    values = sorted(
        f["weighted_fwhm"] for f in frames
        if f.get("weighted_fwhm") and f["weighted_fwhm"] > 0
    )
    gate["frame_count"] = len(frames)
    if len(values) < 2:
        gate["verdict"] = "fail"
        gate["reasons"].append(
            "registration data has no usable weighted_fwhm values; the PWS "
            "clarity weight C_i cannot be derived from this run"
        )
        return gate

    p10 = _percentile(values, 0.10)
    p90 = _percentile(values, 0.90)
    ratio = (p90 / p10) if p10 else None
    gate["wfwhm_p10"] = round(p10, 4) if p10 else None
    gate["wfwhm_p90"] = round(p90, 4) if p90 else None
    gate["dispersion_ratio"] = round(ratio, 4) if ratio else None

    median = _percentile(values, 0.5)
    gate["outlier_frames"] = sum(1 for v in values if v > median * 1.5)

    if len(frames) < PWS_MIN_FRAMES:
        gate["verdict"] = "fail"
        gate["reasons"].append(
            "only %d registered frames; PWS needs N >= %d for the "
            "frame-quality dispersion to mean anything (paper section 3.4.1)"
            % (len(frames), PWS_MIN_FRAMES)
        )
    elif ratio is not None and ratio <= PWS_DISPERSION_FLOOR:
        gate["verdict"] = "fail"
        gate["reasons"].append(
            "weighted_fwhm p90/p10 = %.3f is at or below the %.1f dispersion "
            "floor: measured PWS gains on such data (C_i span ~1.35x) were "
            "3.7%%, inside the measurement noise; the Siril stack is the "
            "honest choice" % (ratio, PWS_DISPERSION_FLOOR)
        )
    else:
        gate["eligible"] = True
        if len(frames) < PWS_THIN_FRAMES:
            gate["reasons"].append(
                "frame count %d is workable but thin; per-frame weights rest "
                "on a small sample" % len(frames)
            )
    return gate


def _percentile(sorted_values, fraction):
    """Percentile of a pre-sorted list, matching ``astra_stack._percentile``."""
    if not sorted_values:
        return None
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


# ---------------------------------------------------------------------------
# Input materialisation
# ---------------------------------------------------------------------------

def check_black_corners(input_dir, sample=6, threshold=PWS_BLACK_MAX_FRACTION):
    """Detect canvas black corners in the prepared engine inputs.

    Samples a few converted frames (first, last, middle spread) and measures
    the share of non-positive pixels. A rotated sequence on a fixed canvas
    grows this share monotonically towards the sequence tail, so the tail
    frames are the informative ones and are always included.

    Args:
        input_dir: Directory of ``pwsNNNN.fit`` frames from
            :func:`prepare_pws_input`.
        sample: Number of frames to inspect (at least 3).
        threshold: Maximum tolerated non-positive share.

    Returns:
        Dict with ``worst_fraction``, ``sampled`` and ``frames`` (per-frame
        fractions for the report).
    """
    names = sorted(n for n in os.listdir(input_dir) if n.endswith(".fit"))
    if not names:
        return {"worst_fraction": None, "sampled": 0, "frames": []}
    step = max(1, len(names) // sample)
    picks = sorted(set(names[::step] + names[-1:]))[: sample + 1]

    from fits_probe import data_offset, read_fits_header

    frames = []
    worst = 0.0
    for name in picks:
        path = os.path.join(input_dir, name)
        try:
            header = read_fits_header(path)
            width = int(header["NAXIS1"])
            height = int(header["NAXIS2"])
            offset = data_offset(path)
            plane = width * height
            with open(path, "rb") as handle:
                handle.seek(offset)
                blob = handle.read(plane * 4)
            if len(blob) < plane * 4:
                continue
            values = struct.unpack(">%df" % plane, blob)
            black = sum(1 for v in values if v <= 0.0)
            fraction = black / plane
        except (FitsError, OSError, struct.error):
            continue
        frames.append({"frame": name, "black_fraction": round(fraction, 4)})
        worst = max(worst, fraction)

    return {
        "worst_fraction": round(worst, 4),
        "sampled": len(frames),
        "frames": frames,
        "threshold": threshold,
    }


def prepare_pws_input(work_dir, stem, registered_seq=None):
    """Convert the registered frames into engine-ready grayscale inputs.

    Two transformations happen here, both forced by measured facts about the
    engine's reader:

    1. **RGB -> grayscale.** The engine's FITS reader treats a 3-axis frame as
       channels-*last* (``data[..., :3].mean(axis=-1)``), but FITS/Astropy
       order is channels-*first* — measured: a Siril-debayered 200x150 RGB
       frame reads back as shape ``(3, 150, 200)`` and the engine's collapse
       produces a nonsensical ``(3, 150)`` image with zero detectable stars.
       This skill cannot patch the vendored reader (bit-identical guarantee,
       ``extract.py --check``), so the bridge collapses the channels itself:
       mean over axis 0, written as a 2-axis float32 FITS, which the engine's
       reader passes through unchanged. Luminance is also exactly what the
       paper's mono-camera workflow measures C_i on.
    2. **Renaming.** The engine reads a directory and indexes frames by
       position, so copies are ``pwsNNNN.fit``: zero-indexed, gapless, no
       other sequence anywhere near it.

    Args:
        work_dir: Pipeline work directory holding ``ap_c_<stem>NNNN.fit``.
        stem: Sequence stem (``sequence_stem(target)``).
        registered_seq: Actual registered sequence; defaults to ``ap_c_<stem>``.

    Returns:
        Dict with ``input_dir`` and ``frame_count``.

    Raises:
        PwsBridgeError: when no registered frames exist.
    """
    prefix = registered_seq if registered_seq is not None else "ap_c_%s" % stem
    frames = sorted(
        name for name in os.listdir(work_dir)
        if name.startswith(prefix) and name.endswith(".fit")
    )
    if not frames:
        raise PwsBridgeError(
            "no registered frames (%sNNNN.fit) found in %s; the stack stage "
            "cannot hand off to PWS" % (prefix, work_dir)
        )

    input_dir = os.path.join(work_dir, PWS_INPUT_DIRNAME)
    os.makedirs(input_dir, exist_ok=True)
    for position, name in enumerate(frames):
        destination = os.path.join(input_dir, "pws%04d.fit" % position)
        _convert_frame_to_grayscale(
            os.path.join(work_dir, name), destination
        )
    return {"input_dir": input_dir, "frame_count": len(frames)}


def _convert_frame_to_grayscale(source, destination):
    """Rewrite one registered frame as a 2-axis float32 FITS.

    A 3-axis frame is read big-endian float32 straight from the data unit
    (FITS stores channels first: ``(NAXIS3, NAXIS2, NAXIS1)``) and averaged
    over the channel axis. A 2-axis frame is copied unchanged in structure,
    re-emitted as float32 either way so the engine sees one uniform format.
    """
    from fits_probe import data_offset, read_fits_header

    header = read_fits_header(source)
    width = int(header["NAXIS1"])
    height = int(header["NAXIS2"])
    naxis = int(header["NAXIS"])
    channels = int(header.get("NAXIS3") or 1) if naxis == 3 else 1
    offset = data_offset(source)

    pixels_per_plane = width * height
    with open(source, "rb") as handle:
        handle.seek(offset)
        blob = handle.read(pixels_per_plane * channels * 4)
    if len(blob) < pixels_per_plane * channels * 4:
        raise PwsBridgeError(
            "%s: data unit truncated (expected %d bytes, got %d)"
            % (os.path.basename(source), pixels_per_plane * channels * 4,
               len(blob))
        )

    values = struct.unpack(">%df" % (pixels_per_plane * channels,), blob)
    if channels > 1:
        total = pixels_per_plane * channels
        gray = [0.0] * pixels_per_plane
        for plane in range(channels):
            base = plane * pixels_per_plane
            for p in range(pixels_per_plane):
                gray[p] += values[base + p]
        for p in range(pixels_per_plane):
            gray[p] /= channels
        values = gray

    cards = [
        build_card("SIMPLE", "T", "conforms to FITS standard"),
        build_card("BITPIX", "-32"),
        build_card("NAXIS", "2"),
        build_card("NAXIS1", str(width)),
        build_card("NAXIS2", str(height)),
        build_card("BUNIT", "'PWS_IN '"),
    ]
    cards.append(build_card("END", ""))
    header_bytes = _pad_block("".join(cards).encode("ascii"))
    payload = bytearray()
    for value in values:
        payload += struct.pack(">f", value)
    with open(destination, "wb") as handle:
        handle.write(header_bytes)
        handle.write(_pad_block(bytes(payload)))


# ---------------------------------------------------------------------------
# Engine discovery
# ---------------------------------------------------------------------------

def resolve_pws_engine(sibling_root=None):
    """Locate the sibling pws-stacking skill's runner.

    Args:
        sibling_root: Explicit path from ``--pws-engine`` (directory containing
            ``pws_run.py``).

    Returns:
        Dict with ``runner`` (absolute path to ``pws_run.py``) and ``root``.

    Raises:
        PwsBridgeError: when the runner is missing, or when its third-party
            stack (numpy/scipy/astropy/photutils -- the *engine's* dependency,
            not ours) is not importable in this interpreter.
    """
    candidates = ([sibling_root] if sibling_root else [])
    candidates += list(PWS_SKILL_CANDIDATES)

    runner = None
    for candidate in candidates:
        if not candidate:
            continue
        candidate = os.path.abspath(candidate)
        if os.path.basename(candidate) == "pws_run.py" and os.path.isfile(candidate):
            runner = candidate
            break
        nested = os.path.join(candidate, "pws_run.py")
        if os.path.isfile(nested):
            runner = nested
            break
    if runner is None:
        raise PwsBridgeError(
            "pws-stacking skill not found (searched %s). Install it next to "
            "siril-astra-stacking, or pass --pws-engine /path/to/pws-stacking/scripts"
            % ", ".join(str(c) for c in candidates if c)
        )

    missing = _missing_engine_deps()
    if missing:
        raise PwsBridgeError(
            "the PWS engine's dependencies are not importable: %s. Install "
            "them (see pws-stacking/requirements.txt) -- this skill itself "
            "stays stdlib-only" % ", ".join(missing)
        )
    return {"runner": runner, "root": os.path.dirname(runner)}


def _missing_engine_deps():
    """Return the engine's third-party requirements that cannot be imported."""
    missing = []
    for module in ("numpy", "scipy", "astropy", "photutils"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    return missing


# ---------------------------------------------------------------------------
# XISF master conversion (stdlib reader for the engine's output subset)
# ---------------------------------------------------------------------------

class XisfReadError(FitsError):
    """Raised when an XISF file is outside the subset this bridge parses."""


def convert_xisf_master(xisf_path, fits_path):
    """Convert the engine's XISF master into a 32-bit FITS master.

    Parses only what :func:`pws.save_linear_xisf` writes: the ``XISF0100``
    signature, one ``<Image>`` with ``sampleFormat="Float32"``,
    ``colorSpace="Gray"``, ``pixelStorage="Planar"``, an uncompressed
    ``location="attachment:..."`` block, and ``FITSKeyword`` children. Anything
    else (compression, multiple images, cube storage, non-float samples) is
    refused with a message saying exactly what was unexpected -- this reader is
    a bridge, not a general XISF implementation.

    Args:
        xisf_path: Path to ``stack_<tag>.xisf`` written by the engine.
        fits_path: Destination FITS path.

    Returns:
        Dict with ``width``, ``height``, ``keywords`` (the FITSKeyword triples,
        including the engine's ``STACKMODE`` provenance line) and ``bytes``.
    """
    with open(xisf_path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 16 or blob[:8] != b"XISF0100":
        raise XisfReadError(
            "%s is not an XISF 1.0 file (signature %r)"
            % (os.path.basename(xisf_path), blob[:8])
        )
    header_length = struct.unpack("<I", blob[8:12])[0]
    xml_bytes = blob[16:16 + header_length]
    try:
        root = ET.fromstring(xml_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise XisfReadError("XISF XML header is malformed: %s" % exc) from exc

    # The writer sets xmlns="http://www.pixinsight.com/xisf", so every element
    # is namespaced; match both namespaced and bare forms for robustness.
    ns = {"x": "http://www.pixinsight.com/xisf"}
    images = root.findall("x:Image", ns) or root.findall("Image")
    if len(images) != 1:
        raise XisfReadError(
            "expected exactly one Image element, found %d" % len(images)
        )
    image = images[0]

    if image.get("sampleFormat") != "Float32":
        raise XisfReadError(
            "unsupported sampleFormat %r; the bridge only reads the Float32 "
            "grayscale master the PWS engine writes" % image.get("sampleFormat")
        )
    if image.get("colorSpace") != "Gray":
        raise XisfReadError(
            "unsupported colorSpace %r; expected the engine's Gray master"
            % image.get("colorSpace")
        )
    if image.get("pixelStorage") != "Planar":
        raise XisfReadError(
            "unsupported pixelStorage %r; expected Planar"
            % image.get("pixelStorage")
        )
    if image.get("compression"):
        raise XisfReadError(
            "compressed XISF is not supported by the bridge: %r"
            % image.get("compression")
        )

    geometry = image.get("geometry", "")
    parts = geometry.split(":")
    if len(parts) != 3:
        raise XisfReadError("malformed geometry %r" % geometry)
    width, height, channels = (int(p) for p in parts)
    if channels != 1:
        raise XisfReadError(
            "geometry declares %d channels; expected 1 (Gray)" % channels
        )

    location = image.get("location", "")
    match = re.match(r"^attachment:(\d+):(\d+)$", location)
    if not match:
        raise XisfReadError(
            "unsupported location %r; the bridge reads only uncompressed "
            "attachment storage" % location
        )
    offset, size = int(match.group(1)), int(match.group(2))
    expected = width * height * 4
    if size != expected:
        raise XisfReadError(
            "attachment size %d does not match geometry (%dx%d float32 = %d)"
            % (size, width, height, expected)
        )
    if offset + size > len(blob):
        raise XisfReadError(
            "attachment range [%d, %d) exceeds file size %d"
            % (offset, offset + size, len(blob))
        )

    raw = blob[offset:offset + size]
    keywords = [
        (kw.get("name"), kw.get("value"), kw.get("comment", ""))
        for kw in (image.findall("x:FITSKeyword", ns) or
                   image.findall("FITSKeyword"))
    ]

    # XISF stores float data little-endian (verified against the vendored
    # engine's writer: the LE byte pattern of a known pixel value appears in
    # the attachment, the BE pattern does not); FITS stores big-endian.
    payload = bytearray()
    for value in struct.unpack("<%df" % (width * height,), raw):
        payload += struct.pack(">f", value)
    header = _fits_header(width, height, keywords)
    with open(fits_path, "wb") as handle:
        handle.write(header)
        handle.write(_pad_block(bytes(payload)))
    return {
        "width": width,
        "height": height,
        "keywords": keywords,
        "bytes": len(payload),
    }


def _pad_block(payload):
    """Pad a byte string to the next 2880-byte FITS block."""
    block = 2880
    remainder = len(payload) % block
    return payload if remainder == 0 else payload + b"\x00" * (block - remainder)


def build_card(key, value, comment=""):
    """FITS card formatter; delegates to :func:`fits_probe.build_card`.

    The docstring previously claimed a local copy was needed "before package
    initialisation" -- false, the bridge already imports fits_probe at module
    level. Worse, the local copy lacked the END special case: it emitted
    ``END     = `` (with ``= `` and a padded value field), which
    :func:`fits_probe.data_offset` then failed to recognise ("no END card
    found"), so every converted frame shipped an unparseable header. Measured
    on the real 81-frame M8 hand-off.
    """
    return fits_probe_build_card(key, value, comment)


def _fits_header(width, height, keywords):
    """Build a minimal FITS header for the converted master."""
    cards = [
        build_card("SIMPLE", "T", "conforms to FITS standard"),
        build_card("BITPIX", "-32"),
        build_card("NAXIS", "2"),
        build_card("NAXIS1", str(width)),
        build_card("NAXIS2", str(height)),
    ]
    for name, value, comment in keywords:
        if not name or name in ("SIMPLE", "BITPIX", "NAXIS", "NAXIS1",
                                "NAXIS2", "END"):
            continue
        text = str(value)
        # FITS fixed-format string values live in a 20-column field; anything
        # longer overflows the 80-char card and the closing quote gets cut
        # (measured: the parser then silently drops the keyword). Truncate the
        # enclosed text so the whole quoted token fits.
        if text.startswith("'") and text.endswith("'") and len(text) >= 2:
            quoted = text
        else:
            try:
                float(text)
                quoted = None
            except ValueError:
                inner = text[:18]
                quoted = "'%s'" % inner
        if quoted is None:
            cards.append(build_card(name, text, comment))
        else:
            cards.append(build_card(name, quoted, comment))
    cards.append(build_card("END", ""))
    header = "".join(cards).encode("ascii")
    return _pad_block(header)


def pad_to_block(payload):
    """Public alias kept for symmetry with ``fits_probe.pad_to_block``."""
    return _pad_block(payload)


__all__ = [
    "PwsBridgeError",
    "XisfReadError",
    "PWS_BLACK_MAX_FRACTION",
    "PWS_DISPERSION_FLOOR",
    "PWS_MIN_FRAMES",
    "PWS_THIN_FRAMES",
    "evaluate_gate",
    "check_black_corners",
    "prepare_pws_input",
    "resolve_pws_engine",
    "convert_xisf_master",
    "pad_to_block",
    "zlib",  # re-exported for tests that stub compression handling
]
