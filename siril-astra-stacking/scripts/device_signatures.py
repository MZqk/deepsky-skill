"""Device identification from FITS headers, plus calibration-master matching.

Identification is metadata-only by design. Image dimensions, bit depth and aspect
ratio are deliberately **not** used as evidence, because a wrong guess silently
selects wrong calibration and stacking parameters; a refusal is recoverable, a
silent mismatch is not.

Real header facts encoded in the signature table below (captured from actual
files, not documentation):

* ZWO Seestar S30 Pro writes ``TELESCOP= 'S30 Pro_10d57b1d'`` -- the value carries
  a per-unit serial suffix, and ``INSTRUME`` holds the *sensor* model
  (``'imx585  '``), not a device name. ``CREATOR`` is absent entirely.
* ZWO Seestar S50 writes ``INSTRUME= 'Seestar S50'`` and
  ``CREATOR = 'ZWO Seestar S50'``.
* String values carry trailing blanks (``BAYERPAT= 'GRBG    '``), so every
  pattern here is matched against a right-stripped value.
* Siril writes ``EQUINOX= 9.87654321E+107`` into real files; the parser reports
  it as a warning and ignores it rather than failing.

Usage:
    from device_signatures import identify_device, DeviceNotIdentified
    device = identify_device(header)
    print(device["id"], device["class"])
"""

from __future__ import annotations

import json
import os
import re

#: Exit / error codes shared with the CLI entry point.
E_DEVICE_UNKNOWN = 2
E_CALIBRATION_MISSING = 3


class DeviceNotIdentified(Exception):
    """Raised when no signature matches; carries the evidence that was seen."""

    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence or {}


#: Signature table. Order matters: the first entry whose ``match`` block fully
#: matches wins. ``match`` requires every listed keyword to match its pattern;
#: ``any_of`` is an optional disambiguator where at least one group must match
#: when present.
SIGNATURES = [
    {
        "id": "zwo_seestar_s30pro",
        "class": "smart",
        "description": "ZWO Seestar S30 Pro (IMX585)",
        "match": {
            "TELESCOP": r"^S30\s*Pro",
        },
        "prefer": {
            "INSTRUME": r"^imx585",
        },
        "profile": {
            "flats_available": False,
            "dark_embedded": True,
            "cfa_sensor": "IMX585",
            "mount": "altaz",
            "field_rotation_per_frame": True,
            "mosaic_capable": True,
            "live_stacking": True,
            "min_pairs": 4,
            "notes": "Darks captured internally at session start and already subtracted.",
        },
    },
    {
        "id": "zwo_seestar_s50",
        "class": "smart",
        "description": "ZWO Seestar S50 (IMX462)",
        "match": {
            "INSTRUME": r"(?i)^seestar",
        },
        "any_of": [
            {"TELESCOP": r"(?i)seestar"},
            {"CREATOR": r"(?i)seestar"},
        ],
        "profile": {
            "flats_available": False,
            "dark_embedded": True,
            "cfa_sensor": "IMX462",
            "mount": "altaz",
            "field_rotation_per_frame": True,
            "mosaic_capable": True,
            "live_stacking": True,
            "min_pairs": 4,
            "notes": "ZWO states flats add little at 50mm f/5 on this sensor.",
        },
    },
    {
        "id": "zwo_seestar_s30",
        "class": "smart",
        "description": "ZWO Seestar S30 (IMX462)",
        "match": {
            "TELESCOP": r"(?i)^s30\b",
        },
        "prefer": {
            "INSTRUME": r"(?i)^imx462",
        },
        "profile": {
            "flats_available": False,
            "dark_embedded": True,
            "cfa_sensor": "IMX462",
            "mount": "altaz",
            "field_rotation_per_frame": True,
            "mosaic_capable": True,
            "live_stacking": True,
            "min_pairs": 4,
            "notes": "Entry-level Seestar; AltAz field rotation applies.",
        },
    },
    {
        "id": "zwo_seestar_generic",
        "class": "smart",
        "description": "ZWO Seestar family, model not pinned",
        "match": {
            "CREATOR": r"(?i)seestar",
        },
        "any_of": [
            {"PRODUCER": r"(?i)zwo"},
            {"TELESCOP": r"(?i)seestar"},
        ],
        "profile": {
            "flats_available": False,
            "dark_embedded": True,
            "cfa_sensor": "unknown",
            "mount": "altaz",
            "field_rotation_per_frame": True,
            "mosaic_capable": True,
            "live_stacking": True,
            "min_pairs": 4,
            "notes": "Generic Seestar profile; sensor left unpinned.",
        },
    },
]

#: Keywords surfaced in the refusal message so the user can extend the table.
_EVIDENCE_KEYS = ("TELESCOP", "INSTRUME", "CREATOR", "PRODUCER", "FILTER",
                  "IMAGETYP", "XPIXSZ", "BAYERPAT", "FOCALLEN")


def _matches(value, pattern):
    if value is None:
        return False
    return re.search(pattern, str(value)) is not None


def _match_any_of(header, any_of):
    """Return True when each group in ``any_of`` has at least one matching pair."""
    for group in any_of:
        if not any(_matches(header.get(key), pattern)
                   for key, pattern in group.items()):
            return False
    return True


def _evidence(header):
    """Collect identification clues for the refusal message.

    Values are emitted verbatim after right-stripping, so trailing FITS blanks
    (``'IRCUT   '``) do not clutter the diagnostic.
    """
    collected = {}
    for key in _EVIDENCE_KEYS:
        value = header.get(key)
        if value is None:
            continue
        collected[key] = str(value).rstrip()
    return collected


def identify_device(header, overrides=None):
    """Identify the capture device from a parsed FITS header.

    Args:
        header: Dict from :func:`fits_probe.read_fits_header`.
        overrides: Optional path to a JSON file mapping a device id to a profile
            override, letting a user pin behaviour for an unlisted device without
            editing this file.

    Returns:
        ``{"id", "class", "description", "profile", "matched_by",
        "confidence"}``.

    Raises:
        DeviceNotIdentified: when nothing matches.
    """
    override_profiles = _load_overrides(overrides) if overrides else {}

    for signature in SIGNATURES:
        if not all(_matches(header.get(key), pattern)
                   for key, pattern in signature["match"].items()):
            continue
        if "any_of" in signature and not _match_any_of(header, signature["any_of"]):
            continue
        # ``prefer`` narrows a shared prefix; it never blocks an otherwise valid
        # match, because the table must not fail closed on a firmware rename.
        matched_by = "+".join(sorted(signature["match"]))
        profile = dict(signature["profile"])
        if signature["id"] in override_profiles:
            profile.update(override_profiles[signature["id"]])
        return {
            "id": signature["id"],
            "class": signature["class"],
            "description": signature["description"],
            "profile": profile,
            "matched_by": matched_by,
            "confidence": "signature_table",
        }

    evidence = _evidence(header)
    raise DeviceNotIdentified(
        "No device signature matches this header. Refusing to guess from image "
        "geometry, because silently mis-selecting calibration or stacking "
        "parameters is worse than refusing. Extend SIGNATURES in "
        "scripts/device_signatures.py, or supply an overrides JSON via "
        "--device-profile.",
        evidence,
    )


def _load_overrides(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise DeviceNotIdentified(
            "cannot read device profile overrides %s: %s" % (path, exc)
        )
    if not isinstance(data, dict):
        raise DeviceNotIdentified(
            "device profile overrides must be a JSON object mapping device id to "
            "profile fields; got %s" % type(data).__name__
        )
    return data


# --------------------------------------------------------------------------
# Calibration master matching
# --------------------------------------------------------------------------

class CalibrationMissing(Exception):
    """Raised when a required calibration frame cannot be matched."""

    def __init__(self, message, kind, candidates=None):
        super().__init__(message)
        self.kind = kind
        self.candidates = candidates or []


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def match_master(light_header, master_header, tolerance):
    """Return ``(matched, score, deltas)`` for a light/master pair.

    A master matches when every key in ``tolerance`` is present in both headers
    and within tolerance. The score is the sum of normalised deltas, so the
    tightest-fitting master wins.

    Args:
        light_header: Header of the light frame being calibrated.
        master_header: Header of the candidate master.
        tolerance: Dict of keyword to allowed absolute deviation, e.g.
            ``{"CCD-TEMP": 2.0, "GAIN": 3.0, "EXPTIME": 0.5}``.

    Returns:
        ``(True, score, deltas)`` on success, ``(False, None, None)`` otherwise.
    """
    deltas = {}
    score = 0.0
    for keyword, allowed in tolerance.items():
        light_value = _float(light_header.get(keyword))
        master_value = _float(master_header.get(keyword))
        if light_value is None or master_value is None:
            return False, None, None
        delta = abs(light_value - master_value)
        if delta > allowed:
            return False, None, None
        deltas[keyword] = delta
        score += delta / allowed if allowed else delta
    return True, score, deltas


def resolve_calibration_plan(light_header, device, calibration_dir, tolerance=None):
    """Decide which calibration frames to apply for a light sequence.

    Args:
        light_header: Header of the first light frame.
        device: Output of :func:`identify_device`.
        calibration_dir: Directory containing masters, or ``None``.
        tolerance: Overrides for the default temperature/gain/exposure window.

    Returns:
        Dict with ``bias``, ``dark``, ``flat`` (paths or ``None``), ``cc`` and
        ``warnings``. For smart scopes with ``dark_embedded`` the dark slot is
        ``None`` and ``dark_embedded`` is True; a flat slot is ``None`` with a
        recorded reason.

    Raises:
        CalibrationMissing: for traditional equipment when a required master is
            absent, since proceeding would silently produce uncalibrated data.
    """
    tolerance = tolerance or {"CCD-TEMP": 2.0, "GAIN": 3.0, "EXPTIME": 0.5}
    profile = device["profile"]
    warnings = []
    plan = {
        "bias": None,
        "dark": None,
        "flat": None,
        "dark_embedded": bool(profile.get("dark_embedded")),
        "flat_missing_reason": None,
        "cc": {"enabled": True, "siglo": None, "sighi": 3},
        "warnings": warnings,
    }

    if profile.get("dark_embedded"):
        plan["dark_embedded"] = True
        warnings.append(
            "smart-scope profile: darks are captured internally at session start "
            "and already subtracted from these frames; no master dark applied"
        )
    else:
        plan["dark"] = _pick_master(
            calibration_dir, "dark", light_header, tolerance
        )
        if plan["dark"] is None:
            raise CalibrationMissing(
                "traditional equipment requires a master dark matching "
                "CCD-TEMP +/-%s C, GAIN +/-%s, EXPTIME +/-%s s; none found in %s"
                % (tolerance["CCD-TEMP"], tolerance["GAIN"],
                   tolerance["EXPTIME"], calibration_dir or "<no directory>"),
                "dark",
            )

    if profile.get("flats_available"):
        plan["flat"] = _pick_master(calibration_dir, "flat", light_header, tolerance)
        if plan["flat"] is None:
            warnings.append(
                "master flat not found; proceeding without flat-field correction"
            )
    else:
        plan["flat_missing_reason"] = "smart_scope_no_flat"
        warnings.append(
            "smart-scope profile: no flat frames are produced by this device; "
            "residual vignetting will remain in the stacked master"
        )

    # Bias is optional in both branches: Siril accepts an expression such as
    # -bias="=64*$OFFSET" when the offset is known from the header instead.
    plan["bias"] = _pick_master(calibration_dir, "bias", light_header, tolerance)

    return plan


def _pick_master(directory, kind, light_header, tolerance):
    """Return the best-matching master of ``kind``, or ``None``."""
    if not directory or not os.path.isdir(directory):
        return None

    from fits_probe import read_fits_header

    best = None
    best_score = None
    for name in sorted(os.listdir(directory)):
        if not name.lower().endswith((".fit", ".fits", ".fts")):
            continue
        path = os.path.join(directory, name)
        lowered = name.lower()
        kind_tags = {
            "dark": ("dark", "masterdark"),
            "flat": ("flat", "masterflat"),
            "bias": ("bias", "masterbias"),
        }[kind]
        if not any(tag in lowered for tag in kind_tags):
            continue
        try:
            master_header = read_fits_header(path)
        except Exception:  # noqa: BLE001 - skip unusable master frame
            continue
        matched, score, _deltas = match_master(
            light_header, master_header, tolerance
        )
        if matched and (best_score is None or score < best_score):
            best, best_score = path, score
    return best