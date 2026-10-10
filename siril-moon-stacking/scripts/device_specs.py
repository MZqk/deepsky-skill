#!/usr/bin/env python3
"""Built-in smart-telescope specification table and device identification.

Why this exists
---------------
Video and SER captures from integrated smart telescopes usually carry no
``FOCALLEN`` / ``APERTURE``, and sometimes no pixel size either.  Without those,
``--drizzle auto`` cannot decide anything and the postprocess optical inference
falls back to hardcoded defaults (3.73um / 80mm / 400mm) that can be wrong by
several times.

This module supplies **priors** for fixed-optics devices whose specifications are
published.  Priors are *assumed* evidence, never measured facts: every value that
reaches a FITS header is tagged with ``OPTISRC = 'device_table'`` plus the device
id and the signal it was matched from, so downstream code and reports can always
tell a lookup apart from a measurement.

Scope
-----
Smart telescopes only.  DSLR + telephoto combinations and arbitrary
camera + telescope pairs have an unbounded configuration space and cannot be
tabulated; those rely on the geometric inversion of the lunar limb instead.

Source grades
-------------
``official``               vendor specification page
``official+chip``          vendor body spec + sensor datasheet pixel pitch
``official+third_party``   vendor body spec, pixel pitch from a third party
``dealer+third_party``     discontinued model; dealer page + teardown
``disputed``               vendor and third party disagree on the sensor

``sensor_status`` is ``confirmed`` / ``disputed`` / ``unpublished``.  Only
``confirmed`` sensors are used as an identification signal.
"""

from __future__ import annotations

import re

SOURCE_GRADES = (
    "official",
    "official+chip",
    "official+third_party",
    "dealer+third_party",
    "disputed",
)

SENSOR_STATUS = ("confirmed", "disputed", "unpublished")

# Longest token wins, so model-specific tokens must be listed before the short
# model token that would otherwise swallow them (e.g. "seestar s50 pro" before
# "seestar s50" before "s50").
DEVICE_SPECS: dict[str, dict] = {
    # ---------------------------------------------------------------- ZWO
    "seestar-s50": {
        "label": "ZWO Seestar S50",
        "brand": "ZWO",
        "tokens": ("seestar s50", "seestar-s50", "seestar_s50", "s50"),
        "brand_tokens": ("seestar", "zwo"),
        "sensor": "Sony IMX462",
        "sensor_status": "confirmed",
        "focal_length_mm": 250.0,
        "aperture_mm": 50.0,
        "f_ratio": 5.0,
        "pixel_size_um": 2.9,
        "source_grade": "official",
        "notes": "",
    },
    "seestar-s50-pro": {
        "label": "ZWO Seestar S50 Pro",
        "brand": "ZWO",
        "tokens": ("seestar s50 pro", "seestar-s50-pro", "seestar_s50_pro", "s50 pro", "s50pro"),
        "brand_tokens": ("seestar", "zwo"),
        "sensor": None,
        "sensor_status": "unpublished",
        "sensor_candidates": ("OmniVision OS08B10 (third-party teardown, vendor never published)",),
        "focal_length_mm": 260.0,
        "aperture_mm": 50.0,
        "f_ratio": 5.2,
        "pixel_size_um": 2.9,
        "source_grade": "official",
        "notes": "Vendor publishes 1/1.2in 8.3MP and 2.9um but not the sensor part number",
    },
    "seestar-s30": {
        "label": "ZWO Seestar S30",
        "brand": "ZWO",
        "tokens": ("seestar s30", "seestar-s30", "seestar_s30", "s30"),
        "brand_tokens": ("seestar", "zwo"),
        "sensor": "Sony IMX662",
        "sensor_status": "confirmed",
        "focal_length_mm": 150.0,
        "aperture_mm": 30.0,
        "f_ratio": 5.0,
        "pixel_size_um": 2.9,
        "source_grade": "official",
        "notes": "",
    },
    "seestar-s30-pro": {
        "label": "ZWO Seestar S30 Pro",
        "brand": "ZWO",
        "tokens": ("seestar s30 pro", "seestar-s30-pro", "seestar_s30_pro", "s30 pro", "s30pro"),
        "brand_tokens": ("seestar", "zwo"),
        "sensor": "Sony IMX585",
        "sensor_status": "confirmed",
        "focal_length_mm": 160.0,
        "aperture_mm": 30.0,
        "f_ratio": 5.3,
        "pixel_size_um": 2.9,
        "source_grade": "official",
        "notes": "",
    },
    # ----------------------------------------------------------- DWARFLAB
    "dwarf-2": {
        "label": "DWARFLAB DWARF 2",
        "brand": "DWARFLAB",
        "tokens": ("dwarf 2", "dwarf-2", "dwarf_2", "dwarf2", "dwarf ii"),
        "brand_tokens": ("dwarf", "dwarflab"),
        "sensor": "Sony IMX415",
        "sensor_status": "confirmed",
        "focal_length_mm": 100.0,
        "aperture_mm": 24.0,
        "f_ratio": 4.2,
        "pixel_size_um": 1.45,
        "source_grade": "official+third_party",
        "notes": "Focal length and aperture are official; f-ratio and pixel pitch corroborated by third parties",
    },
    "dwarf-3": {
        "label": "DWARFLAB DWARF 3",
        "brand": "DWARFLAB",
        "tokens": ("dwarf 3", "dwarf-3", "dwarf_3", "dwarf3", "dwarf iii"),
        "brand_tokens": ("dwarf", "dwarflab"),
        "sensor": "Sony IMX678",
        "sensor_status": "confirmed",
        "focal_length_mm": 150.0,
        "aperture_mm": 35.0,
        "f_ratio": 4.3,
        "pixel_size_um": 2.0,
        "source_grade": "official",
        "notes": "",
    },
    "dwarf-mini": {
        "label": "DWARFLAB DWARF mini",
        "brand": "DWARFLAB",
        "tokens": ("dwarf mini", "dwarf-mini", "dwarf_mini", "dwarfmini"),
        "brand_tokens": ("dwarf", "dwarflab"),
        "sensor": "Sony IMX662",
        "sensor_status": "confirmed",
        "focal_length_mm": 150.0,
        "aperture_mm": 30.0,
        "f_ratio": 5.0,
        "pixel_size_um": 2.9,
        "source_grade": "official",
        "notes": "",
    },
    "dwarf-draco": {
        "label": "DWARFLAB DRACO",
        "brand": "DWARFLAB",
        "tokens": ("dwarf draco", "dwarf-draco", "dwarf_draco", "draco"),
        "brand_tokens": ("dwarf", "dwarflab"),
        "sensor": "OmniVision OV50Q40",
        "sensor_status": "confirmed",
        "focal_length_mm": 340.0,
        "aperture_mm": 90.0,
        "f_ratio": 3.8,
        "pixel_size_um": 2.394,          # effective output pitch written to XPIXSZ
        "pixel_size_native_um": 1.197,   # native pitch, documentation/query only
        "source_grade": "official",
        "notes": "Native 1.197um, 2x2 binned output 2.394um; the binned value is what reaches XPIXSZ",
    },
    # ------------------------------------------------------------- Vaonis
    "vaonis-stellina": {
        "label": "Vaonis Stellina",
        "brand": "Vaonis",
        "tokens": ("stellina",),
        "brand_tokens": ("vaonis",),
        "sensor": "Sony IMX178",
        "sensor_status": "confirmed",
        "focal_length_mm": 400.0,
        "aperture_mm": 80.0,
        "f_ratio": 5.0,
        "pixel_size_um": 2.4,
        "source_grade": "official+chip",
        "notes": "",
    },
    "vaonis-vespera": {
        "label": "Vaonis Vespera",
        "brand": "Vaonis",
        "tokens": ("vespera",),
        "brand_tokens": ("vaonis",),
        "sensor": "Sony IMX462",
        "sensor_status": "confirmed",
        "focal_length_mm": 200.0,
        "aperture_mm": 50.0,
        "f_ratio": 4.0,
        "pixel_size_um": 2.9,
        "source_grade": "dealer+third_party",
        "notes": "Discontinued; the vendor product page is gone, figures come from a regional dealer and teardown",
    },
    "vaonis-vespera-pro": {
        "label": "Vaonis Vespera Pro",
        "brand": "Vaonis",
        "tokens": ("vespera pro", "vespera-pro", "vespera_pro", "vesperapro"),
        "brand_tokens": ("vaonis",),
        "sensor": "Sony IMX676",
        "sensor_status": "confirmed",
        "focal_length_mm": 250.0,
        "aperture_mm": 50.0,
        "f_ratio": 5.0,
        "pixel_size_um": 2.0,
        "source_grade": "official",
        "notes": "",
    },
    "vaonis-vespera-ii": {
        "label": "Vaonis Vespera II",
        "brand": "Vaonis",
        "tokens": ("vespera ii", "vespera-ii", "vespera_ii", "vespera 2", "vespera2"),
        "brand_tokens": ("vaonis",),
        "sensor": "Sony IMX585",
        "sensor_status": "confirmed",
        "focal_length_mm": 250.0,
        "aperture_mm": 50.0,
        "f_ratio": 5.0,
        "pixel_size_um": 2.9,
        "source_grade": "official",
        "notes": "",
    },
    "vaonis-vespera-pro-2": {
        "label": "Vaonis Vespera Pro 2",
        "brand": "Vaonis",
        "tokens": ("vespera pro 2", "vespera-pro-2", "vespera pro ii", "vespera pro2"),
        "brand_tokens": ("vaonis",),
        "sensor": "Sony IMX676",
        "sensor_status": "confirmed",
        "focal_length_mm": 245.0,
        "aperture_mm": 50.0,
        "f_ratio": 4.9,
        "pixel_size_um": 2.0,
        "source_grade": "official",
        "notes": "",
    },
    # --------------------------------------------------------- Unistellar
    "unistellar-evscope-2": {
        "label": "Unistellar eVscope 2",
        "brand": "Unistellar",
        "tokens": ("evscope 2", "evscope-2", "evscope2", "evscope"),
        "brand_tokens": ("unistellar",),
        "sensor": None,
        "sensor_status": "disputed",
        "sensor_candidates": ("Sony IMX224 (vendor)", "Sony IMX347 (third-party teardown)"),
        "focal_length_mm": 450.0,
        "aperture_mm": 114.0,
        "f_ratio": 4.0,
        "pixel_size_um": 2.9,
        "source_grade": "disputed",
        "notes": "Vendor states IMX224 but teardowns identify IMX347 (2.9um); never use the sensor as a match signal",
    },
    "unistellar-equinox-2": {
        "label": "Unistellar eQuinox 2",
        "brand": "Unistellar",
        "tokens": ("equinox 2", "equinox-2", "equinox2", "equinox"),
        "brand_tokens": ("unistellar",),
        "sensor": None,
        "sensor_status": "disputed",
        "sensor_candidates": ("Sony IMX224 (vendor)", "Sony IMX347 (third-party teardown)"),
        "focal_length_mm": 450.0,
        "aperture_mm": 114.0,
        "f_ratio": 4.0,
        "pixel_size_um": 2.9,
        "source_grade": "disputed",
        "notes": "Same sensor dispute as the eVscope 2; never use the sensor as a match signal",
    },
    "unistellar-odyssey": {
        "label": "Unistellar Odyssey",
        "brand": "Unistellar",
        # No "odyssey pro" here: the Pro entry owns that longer token, and
        # longest-token-wins then routes "Odyssey Pro" correctly.
        "tokens": ("unistellar odyssey", "odyssey"),
        "brand_tokens": ("unistellar",),
        "sensor": None,
        "sensor_status": "unpublished",
        "focal_length_mm": 320.0,
        "aperture_mm": 85.0,
        "f_ratio": 3.9,
        "pixel_size_um": 1.45,
        "source_grade": "official",
        "notes": "Vendor publishes 1.45um but no sensor part number",
    },
    "unistellar-odyssey-pro": {
        "label": "Unistellar Odyssey Pro",
        "brand": "Unistellar",
        "tokens": ("unistellar odyssey pro", "odyssey pro", "odyssey-pro", "odyssey_pro"),
        "brand_tokens": ("unistellar",),
        "sensor": None,
        "sensor_status": "unpublished",
        "focal_length_mm": 320.0,
        "aperture_mm": 85.0,
        "f_ratio": 3.9,
        "pixel_size_um": 1.45,
        "source_grade": "official",
        "notes": "Optically identical to the Odyssey; the Pro adds a Nikon eyepiece",
    },
    # ---------------------------------------------------------- Celestron
    "celestron-origin": {
        "label": "Celestron Origin",
        "brand": "Celestron",
        # No bare "origin": it would false-match unrelated filenames. The Mark II
        # entry carries the longer "celestron origin mark ii" token instead.
        "tokens": ("celestron origin", "celestron-origin"),
        "brand_tokens": ("celestron",),
        "sensor": "Sony IMX178",
        "sensor_status": "confirmed",
        "focal_length_mm": 335.0,
        "aperture_mm": 152.0,
        "f_ratio": 2.2,
        "pixel_size_um": 2.4,
        "source_grade": "official",
        "notes": "",
    },
    "celestron-origin-mk2": {
        "label": "Celestron Origin Mark II",
        "brand": "Celestron",
        "tokens": ("celestron origin mark ii", "celestron origin mark 2",
                   "origin mark ii", "origin mark 2", "origin-mk2", "origin mk2", "origin ii"),
        "brand_tokens": ("celestron",),
        "sensor": "Sony IMX678",
        "sensor_status": "confirmed",
        "focal_length_mm": 335.0,
        "aperture_mm": 152.0,
        "f_ratio": 2.2,
        "pixel_size_um": 2.0,
        "source_grade": "official",
        "notes": "",
    },
}

# Deliberately not tabulated: Vaonis Hestia has no built-in sensor (it images
# through a phone camera), so its pixel scale is not a fixed device property.
EXCLUDED_DEVICES = {
    "vaonis-hestia": "No built-in sensor: images through the phone camera, so the pixel scale is not fixed",
}

_SENSOR_RE = re.compile(r"(imx\s?\d{3,4}|ov\s?\w{4,8})", re.IGNORECASE)


def _token_present(text: str, token: str) -> bool:
    """Word-boundary token match (same semantics as the sibling deep-sky-advisor skill)."""
    return re.search(r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])", text) is not None


def _norm_sensor(value) -> str | None:
    """Reduce a free-form sensor string to a canonical code such as 'imx585'."""
    if not value:
        return None
    m = _SENSOR_RE.search(str(value))
    if not m:
        return None
    return m.group(1).replace(" ", "").lower()


def list_devices() -> list[dict]:
    """All device specs, sorted by id, with the id injected into each entry."""
    out = []
    for did in sorted(DEVICE_SPECS):
        spec = dict(DEVICE_SPECS[did])
        spec["id"] = did
        out.append(spec)
    return out


def get_device(device_id: str) -> dict | None:
    spec = DEVICE_SPECS.get(device_id)
    if spec is None:
        return None
    out = dict(spec)
    out["id"] = device_id
    return out


def to_fits_priors(spec: dict) -> dict:
    """Physical quantities of a spec, keyed by the FITS keywords consumers read."""
    px = spec.get("pixel_size_um")
    priors = {
        "FOCALLEN": spec.get("focal_length_mm"),
        "APERTURE": spec.get("aperture_mm"),
        "XPIXSZ": px,
        "YPIXSZ": px,
    }
    return {k: float(v) for k, v in priors.items() if v is not None}


def sensor_groups() -> dict[str, list[str]]:
    """Canonical sensor code -> device ids that use it (confirmed sensors only)."""
    groups: dict[str, list[str]] = {}
    for did, spec in DEVICE_SPECS.items():
        if spec.get("sensor_status") != "confirmed":
            continue
        code = _norm_sensor(spec.get("sensor"))
        if code:
            groups.setdefault(code, []).append(did)
    return groups


def _pack(spec: dict, device_id: str | None, match_source: str, confidence: str,
          **extra) -> dict:
    info = {
        "id": device_id,
        "label": spec.get("label"),
        "brand": spec.get("brand"),
        "match_source": match_source,
        "confidence": confidence,
        "ambiguous": False,
        "brand_only": False,
        "sensor": spec.get("sensor"),
        "sensor_status": spec.get("sensor_status"),
        "source_grade": spec.get("source_grade"),
        "notes": spec.get("notes", ""),
        "priors": to_fits_priors(spec),
    }
    info.update(extra)
    return info


def _brand_of(text: str) -> str | None:
    """Brand token found in `text`, if any (used to disambiguate sensor matches)."""
    for spec in DEVICE_SPECS.values():
        for bt in spec["brand_tokens"]:
            if _token_present(text, bt):
                return bt
    return None


def identify_device(
    cli_device: str | None = None,
    header=None,
    sidecar: dict | None = None,
    filename: str | None = None,
    sensor: str | None = None,
) -> dict | None:
    """Identify the capture device and return its optics priors.

    Signal precedence: ``--device`` override > FITS header ``TELESCOP``/``INSTRUME``
    > capture sidecar > filename > sensor model.  A sensor-only match is accepted
    only when every device sharing that sensor agrees on the numbers, or when a
    brand hint narrows the group to a single device; otherwise the pixel pitch is
    returned alone (``OPTISRC = 'sensor_pixel_only'``) or nothing at all.

    Returns ``None`` when nothing can be established.  Priors are always labelled
    with ``match_source`` and ``confidence`` so callers can record provenance.
    """
    cli_device = (cli_device or "auto").strip()
    if cli_device == "none":
        return None
    if cli_device not in ("auto", ""):
        spec = DEVICE_SPECS.get(cli_device)
        if spec is not None:
            return _pack(spec, cli_device, "cli", "user")
        # Unknown ids are validated by the CLI layer, which can exit cleanly.

    header_text = " ".join(
        str(header.get(k, "")) for k in ("TELESCOP", "INSTRUME")
    ).strip().lower() if header is not None else ""
    sidecar_text = " ".join(
        str(v) for k, v in (sidecar or {}).items() if k in ("SENSOR", "TELESCOP", "INSTRUME")
    ).strip().lower()
    filename_text = (filename or "").lower()

    texts = [("header", header_text), ("sidecar", sidecar_text), ("filename", filename_text)]

    # ---- model tokens: longest token wins, so "s30 pro" beats "s30" ----------
    matches: list[tuple[int, int, str, str]] = []
    for rank, (src, text) in enumerate(texts):
        if not text:
            continue
        for did, spec in DEVICE_SPECS.items():
            for tok in spec["tokens"]:
                if _token_present(text, tok):
                    matches.append((len(tok), rank, did, src))
    if matches:
        matches.sort(key=lambda m: (-m[0], m[1]))
        _, rank, did, src = matches[0]
        return _pack(DEVICE_SPECS[did], did, src, "high" if rank == 0 else "medium")

    # ---- brand only: record it for disambiguation, inject no priors ----------
    brand_hint = None
    brand_source = None
    for rank, (src, text) in enumerate(texts):
        if not text:
            continue
        brand_hint = _brand_of(text)
        if brand_hint:
            brand_source = src
            break

    # ---- sensor fallback ----------------------------------------------------
    sens = _norm_sensor(
        sensor
        or (sidecar or {}).get("SENSOR")
        or (header or {}).get("INSTRUME")
    )
    if sens:
        group = [
            (did, spec) for did, spec in DEVICE_SPECS.items()
            if spec.get("sensor_status") == "confirmed" and _norm_sensor(spec.get("sensor")) == sens
        ]
        if group and brand_hint:
            narrowed = [g for g in group
                        if brand_hint in [b.lower() for b in g[1]["brand_tokens"]]]
            if narrowed:
                group = narrowed
        if group:
            keys = {(s["focal_length_mm"], s["aperture_mm"], s["pixel_size_um"])
                    for _, s in group}
            if len(keys) == 1:
                did, spec = group[0]
                # Several devices may share a sensor, but when their optics are
                # identical the priors are unambiguous even though the model name
                # is not.  Record which devices share it instead of crying wolf.
                shared = [d for d, _ in group]
                note = spec.get("notes", "")
                if len(shared) > 1:
                    note = (note + " " if note else "") + \
                        f"(sensor {sens} is shared by {len(shared)} identical devices: {', '.join(shared)})"
                return _pack(spec, did, "sensor", "low", shared_by=shared, notes=note)
            pitches = {s["pixel_size_um"] for _, s in group}
            if len(pitches) == 1:
                # Same sensor, disagreeing optics: the pitch is still safe to use.
                px = pitches.pop()
                return {
                    "id": None,
                    "label": f"sensor {sens} (model ambiguous)",
                    "brand": None,
                    "match_source": "sensor",
                    "confidence": "low",
                    "ambiguous": True,
                    "brand_only": False,
                    "sensor": sens,
                    "sensor_status": "confirmed",
                    "source_grade": "official",
                    "notes": ("sensor shared by devices with different optics "
                              f"({', '.join(d for d, _ in group)}); only the pixel pitch is used"),
                    "priors": {"XPIXSZ": float(px), "YPIXSZ": float(px)},
                    "device_source": "sensor_pixel_only",
                }
            return None

    if brand_hint:
        return {
            "id": None,
            "label": f"{brand_hint} (model unknown)",
            "brand": brand_hint,
            "match_source": brand_source,
            "confidence": "low",
            "ambiguous": True,
            "brand_only": True,
            "sensor": None,
            "sensor_status": None,
            "source_grade": None,
            "notes": "brand recognised but not the model; no optics priors applied",
            "priors": {},
        }

    return None
