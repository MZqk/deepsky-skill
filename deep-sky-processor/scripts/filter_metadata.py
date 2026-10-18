"""Read filter provenance without inferring pixel domain or spectral channel roles.

This stdlib-only module is also shipped with deep-sky-processor; keep the copies
identical so either skill can run independently.
"""

import re
import unicodedata


FILTER_KEYS = ("FILTER", "FILTERID", "FILTNAME")
_SEESTAR_REFERENCES = {
    "s30-pro": "https://us.seestar.com/products/seestar-s30-pro",
    "s30": "https://us.seestar.com/products/seestar-s30-all-in-one-smart-telescope",
    "s50": "https://us.seestar.com/blogs/faq/s50",
}
_DEVICE_KEYS = ("INSTRUME", "TELESCOP", "TELESCOPE", "CREATOR", "ORIGIN")
_DWARF_REFERENCES = {
    "dwarf-mini": "https://help.dwarflab.com/en/docs/DWARF-mini-Smart-Telescope-User-Manual",
    "dwarf-ii": "https://www.dwarflab.com/gb/pages/astronomy-with-dwarf-3-smart-telescope",
    "dwarf-3": "https://help.dwarflab.com/en/docs/DWARF-3-Smart-Telescope-User-Manual-Part1-App-Interface-Introduction?product=dwarf-3",
}


def _normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text.replace("_", "-").replace("α", "alpha")).strip()


def _dwarf_models(header):
    models = set()
    for key in _DEVICE_KEYS:
        text = re.sub(r"[\s_\-‐‑‒–—−]+", " ", _normalize(header.get(key)))
        match = re.fullmatch(r"(?:dwarflab ?)?dwarf ?(mini|ii|2|iii|3)", text)
        if match:
            models.add({"mini": "dwarf-mini", "ii": "dwarf-ii", "2": "dwarf-ii",
                        "iii": "dwarf-3", "3": "dwarf-3"}[match[1]])
    return models


def _seestar_models(header):
    models = set()
    for key in _DEVICE_KEYS:
        val = header.get(key)
        if not val:
            continue
        text = re.sub(r"[\s_\-‐‑‒–—−]+", " ", _normalize(val))
        if "dwarf" in text:
            continue
        if re.search(r"\b(?:seestar ?)?s ?30 ?(?:pro|p)\b", text):
            models.add("s30-pro")
        elif re.search(r"\b(?:seestar ?)?s ?30\b(?![ -]*(?:pro|p))", text):
            models.add("s30")
        elif re.search(r"\b(?:seestar ?)?s ?50\b", text):
            models.add("s50")
    return models


def _dwarf_telephoto(header):
    if header.get("WIDECAM", 0) not in (0, "0", False):
        return False
    return all(header.get(key) is None or _normalize(str(header[key])) in {"", "tele", "telephoto", "telephoto lens"}
               for key in ("CAMERA", "LENS", "LENSNAME"))


def classify_filter(filter_name, header=None):
    """Classify explicit bands first; generic dual-band names do not name lines."""
    raw = str(filter_name or "").strip()
    text = _normalize(raw)
    profile = {"class": "unknown", "normalized": raw or None, "lines": []}
    header = header or {}
    dwarf_models = _dwarf_models(header)
    seestar_models = _seestar_models(header)
    if len(dwarf_models) == 1:
        profile["device_model"] = next(iter(dwarf_models))
    elif len(seestar_models) == 1:
        profile["device_model"] = next(iter(seestar_models))
    if not text:
        return profile
    patterns = (
        ("H-alpha", r"(?<![a-z0-9])h[ -]*(?:alpha|a)(?![a-z0-9])"),
        ("OIII", r"(?<![a-z0-9])(?:o[ -]*(?:iii|3)|oxygen)(?![a-z0-9])"),
        ("SII", r"(?<![a-z0-9])(?:s[ -]*(?:ii|2)|sulfur|sulphur)(?![a-z0-9])"),
    )
    lines = [name for name, pattern in patterns if re.search(pattern, text)]
    if lines:
        profile.update({
            "class": {1: "narrowband", 2: "dual_band"}.get(len(lines), "multi_band"),
            "lines": lines, "normalized": lines[0] if len(lines) == 1 else raw,
        })
        return profile
    if re.search(r"(?<![a-z0-9])(?:l[ -]*(?:extreme|enhance|ultimate)|nbz|alp[ -]*t)(?![a-z0-9])", text):
        profile.update({"class": "dual_band", "lines": ["H-alpha", "OIII"]})
        return profile
    device = " ".join(_normalize(header.get(key)) for key in _DEVICE_KEYS)
    model = profile.get("device_model")
    if model and "seestar" not in device and _dwarf_telephoto(header):
        duo = model != "dwarf-ii" and re.fullmatch(r"(?:duo|dual)[ -]*band", text)
        broad = text in ({"vis", "vis filter"} if model == "dwarf-ii" else
                         {"astro", "astro filter", "vis", "vis filter"} if model == "dwarf-3" else
                         {"astro", "astro filter"})
        if duo or broad:
            profile.update({"class": "dual_band" if duo else "broadband",
                            "lines": ["H-alpha", "OIII"] if duo else [],
                            "matched_by": "device_filter_mapping", "reference": _DWARF_REFERENCES[model]})
            return profile
    if text in {"lp", "seestar lp", "zwo seestar lp"}:
        if not dwarf_models and len(seestar_models) == 1 and (header or {}).get("WIDECAM", 0) in (0, "0", False):
            seestar_model = next(iter(seestar_models))
            profile.update({
                "class": "dual_band", "lines": ["H-alpha", "OIII"],
                "matched_by": "device_filter_mapping", "reference": _SEESTAR_REFERENCES[seestar_model],
            })
            return profile
    if re.search(r"\b(?:dual|duo)[ -]*(?:band)?\b", text):
        profile["class"] = "dual_band"
    elif text in {"l", "lum", "luminance"}:
        profile.update({"class": "luminance", "normalized": "L"})
    elif text in {"r", "g", "b", "red", "green", "blue"}:
        profile.update({"class": "broadband_channel", "normalized": {"red": "R", "green": "G", "blue": "B"}.get(text, text.upper())})
    elif re.search(r"\b(?:uv[ /-]*ir(?:[ -]*cut)?|ir[ -]*cut|cls|broadband|clear)\b", text):
        profile["class"] = "broadband"
    return profile


def filter_from_header(header):
    """Use the first nonempty documented alias and retain its evidence source."""
    header = header or {}
    for key in FILTER_KEYS:
        value = header.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("value")
        if value is None or not str(value).strip():
            continue
        profile = classify_filter(value, header)
        profile.update(raw_filter=str(value).strip(), header_key=key, source="header")
        return profile
    return {**classify_filter(None, header), "raw_filter": None, "header_key": None, "source": "unavailable"}
