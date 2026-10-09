"""Parameter derivation: turns probe results into Siril command parameters.

This module owns the twelve behavioural decisions. It is pure logic -- no file
writes, no Siril invocation -- so every branch is unit-testable and the generated
``.sir`` can be asserted without executing anything.

Two chains exist for CFA data and they are strictly mutually exclusive:

* **drizzle chain** -- ``calibrate -cfa`` (no debayer) -> ``register -2pass``
  probe -> ``seqapplyreg -drizzle -framing=max`` -> ``stack rej ... -maximize``.
  Required when the sequence spans more than one field or rotates materially per
  frame.
* **debayer chain** -- ``calibrate -cfa -debayer -equalize_cfa`` ->
  ``register`` -> ``seqapplyreg -framing=current`` -> ``stack rej ...``.

Why they cannot be mixed: Siril refuses drizzle on non-Bayer data
(``Cannot use drizzle on non-bayer sensors, aborting.``) and warns against plain
interpolation on CFA data (``Applying interpolation on a sequence opened as CFA
is a bad idea.``). Debayering before registration would make the drizzle route
unavailable; staying CFA makes ordinary interpolation lossy. The probe therefore
decides the chain *before* any transformation is applied.

Two further Siril 1.4 traps are enforced here rather than left to the runtime:

* ``Cannot upscale or maximize framing with median stacking. Disabling`` -- the
  downgrade is silent, so ``median`` is rejected outright whenever ``-maximize``
  is requested.
* ``Drizzle stacking cannot be performed because drizzle weights are missing.``
  -- ``-framing=max`` is implemented on top of drizzle weights, so framing=max is
  only legal on the drizzle chain.

Usage:
    from param_derive import derive_pipeline
    plan = derive_pipeline(header, device, calibration_plan, transform_summary)
    print(plan["chain"], plan["stack"]["method"])
"""

from __future__ import annotations

import math

#: Frame rotation above which a global star-alignment model is no longer
#: trustworthy and drizzle is required. AltAz smart telescopes routinely exceed
#: this; an equatorial mount rarely does.
ROTATION_THRESHOLD_DEG = 5.0

#: Scale deviation from the median frame scale that indicates a zoom or framing
#: change, which also requires drizzle.
SCALE_DEV_THRESHOLD = 0.05

#: A sequence spanning more than this multiple of the frame size is a mosaic.
#: The span is measured reference-frame-independently, so it does not depend on
#: which frame Siril picked as reference.
MOSAIC_SPAN_FACTOR = 1.2

#: Frame-selection filters per device class. ``-filter-quality`` is intentionally
#: absent: it is only populated by planetary DFT / Kombat registration and stays
#: empty for star alignment, so passing it would be a silent no-op.
#:
#: Three measured facts drive these values (134-frame Seestar S30 Pro capture of
#: SH2-296, 2160x3840, IMX585, 20s each):
#:
#: 1. ``k``-sigma forms are unsafe here. ``weighted_fwhm`` had a median of 4.12
#:    with six frames between 7.24 and 11.13, inflating sigma from roughly 0.35 to
#:    1.11. At ``0.8k`` the threshold landed at 5.01 and rejected **nothing**.
#:    Percentile forms cut the tail as intended.
#: 2. ``N%`` means "keep the best N%", **not** "discard the worst N%". Verified by
#:    isolating each filter on the registered sequence: ``-filter-nbstars=25%``
#:    kept 35 of 134 frames, ``10%`` kept 16, ``5%`` kept 8, ``2%`` kept 4. So a
#:    *larger* number keeps *more* data, and conservative filtering means a high
#:    percentage.
#: 3. On that dataset the quality was genuinely concentrated -- FWHM max was only
#:    1.48x the median, i.e. no defocused frames -- so aggressive rejection would
#:    discard good integration time for nothing. The values below keep roughly
#:    three quarters of the sequence and cut only the tail.
#:
#: ``-filter-round=0.30`` never bound on this dataset (observed roundness
#: 0.58-0.87); it is a guard against genuinely elongated stars, not a routine cut.
#: ZWO's own Seestar_Preprocessing script documents ``-filter-round=`` as the
#: knob to relax when too many frames get discarded.
SMART_FILTERS = ["-filter-wfwhm=90%", "-filter-round=0.30",
                 "-filter-nbstars=85%"]
TRADITIONAL_FILTERS = ["-filter-wfwhm=90%"]


def classify_input(header):
    """Classify a FITS header into the structural branches the pipeline knows.

    Args:
        header: Dict from :func:`fits_probe.read_fits_header`.

    Returns:
        Dict with ``naxis``, ``is_cfa``, ``is_rgb``, ``bayerpat``,
        ``bitpix``, ``width``, ``height``, ``frame_kind`` and ``warnings``.

    Raises:
        ValueError: when the structure is unsupported -- notably CFA data with no
            ``BAYERPAT``, which would make debayering ambiguous.
    """
    warnings = []
    naxis = _int(header.get("NAXIS"))
    if naxis is None:
        raise ValueError("header has no NAXIS; not a usable FITS image")
    if naxis not in (2, 3):
        raise ValueError(
            "NAXIS=%s is neither 2 (CFA) nor 3 (RGB); this tool does not handle "
            "cube or higher-dimensional data" % naxis
        )

    bayerpat = header.get("BAYERPAT")
    is_cfa = naxis == 2
    if is_cfa and not bayerpat:
        raise ValueError(
            "NAXIS=2 indicates a Bayer CFA frame but BAYERPAT is absent; the "
            "colour filter array pattern cannot be guessed safely. Re-save the "
            "data with a BAYERPAT keyword."
        )
    if bayerpat:
        bayerpat = str(bayerpat).strip().upper()
        if bayerpat not in ("RGGB", "BGGR", "GRBG", "GBRG"):
            warnings.append(
                "BAYERPAT=%r is not one of RGGB/BGGR/GRBG/GBRG; passing it "
                "through unchanged" % bayerpat
            )

    return {
        "naxis": naxis,
        "is_cfa": is_cfa,
        "is_rgb": naxis == 3,
        "bayerpat": bayerpat,
        "bitpix": _int(header.get("BITPIX")),
        "width": _int(header.get("NAXIS1")),
        "height": _int(header.get("NAXIS2")),
        "frame_kind": "cfa" if is_cfa else "rgb",
        "warnings": warnings,
    }


def decide_chain(structure, transform_summary, device, force=None):
    """Choose between the drizzle and debayer chains.

    Args:
        structure: Output of :func:`classify_input`.
        transform_summary: Output of :func:`fits_probe.summarize_transforms`, or
            ``None`` when registration has not run yet.
        device: Output of :func:`device_signatures.identify_device`.
        force: Optional ``"drizzle"`` / ``"debayer"`` to bypass detection.

    Returns:
        Dict with ``chain``, ``reasons``, ``mosaic``, ``rotation_max_deg``,
        ``scale_dev_max``, ``span_px`` and ``degradations``.

    Notes:
        ``-framing=max`` is only reachable through the drizzle chain, so a mosaic
        sequence *must* take drizzle even when rotation is negligible.
    """
    degradations = []

    if not structure["is_cfa"]:
        # RGB data has already been demosaiced by the capture software or an
        # earlier step; drizzle is illegal on it (non-Bayer sensor).
        return {
            "chain": "rgb",
            "reasons": ["NAXIS=3: already demosaiced, drizzle not applicable"],
            "mosaic": bool(transform_summary and transform_summary.get("mosaic")),
            "rotation_max_deg": transform_summary.get("rotation_max_deg", 0.0)
            if transform_summary else 0.0,
            "scale_dev_max": transform_summary.get("scale_dev_max", 0.0)
            if transform_summary else 0.0,
            "span_px": transform_summary.get("span", 0.0)
            if transform_summary else 0.0,
            "degradations": degradations,
        }

    if force in ("drizzle", "debayer"):
        return {
            "chain": force,
            "reasons": ["forced via %s" % ("--drizzle" if force == "drizzle"
                                           else "--no-drizzle")],
            "mosaic": bool(transform_summary and transform_summary.get("mosaic")),
            "rotation_max_deg": transform_summary.get("rotation_max_deg", 0.0)
            if transform_summary else 0.0,
            "scale_dev_max": transform_summary.get("scale_dev_max", 0.0)
            if transform_summary else 0.0,
            "span_px": transform_summary.get("span", 0.0)
            if transform_summary else 0.0,
            "degradations": degradations,
        }

    reasons = []
    mosaic = bool(transform_summary and transform_summary.get("mosaic"))
    rotation = transform_summary.get("rotation_max_deg", 0.0) if transform_summary else 0.0
    scale_dev = transform_summary.get("scale_dev_max", 0.0) if transform_summary else 0.0
    span = transform_summary.get("span", 0.0) if transform_summary else 0.0

    if mosaic:
        reasons.append(
            "displacement span %.1f px exceeds %.1fx the frame size: mosaic needs "
            "-framing=max, which only exists on the drizzle chain"
            % (span, MOSAIC_SPAN_FACTOR)
        )
    if rotation > ROTATION_THRESHOLD_DEG:
        reasons.append(
            "per-frame rotation %.2f deg exceeds %.1f deg: a single global model "
            "must interpolate from outside the source frame near the edges, "
            "losing data and producing edge artifacts"
            % (rotation, ROTATION_THRESHOLD_DEG)
        )
    if scale_dev > SCALE_DEV_THRESHOLD:
        reasons.append(
            "frame scale deviates by %.1f%%, indicating zoom or framing change"
            % (scale_dev * 100.0)
        )

    chain = "drizzle" if reasons else "debayer"
    if not reasons:
        reasons.append(
            "span %.1f px, rotation %.2f deg, scale deviation %.2f%% all within "
            "thresholds; the simpler debayer chain is sufficient"
            % (span, rotation, scale_dev * 100.0)
        )

    profile = device["profile"]
    if chain == "drizzle" and not profile.get("flats_available"):
        degradations.append("drizzle_without_flat_weights")

    return {
        "chain": chain,
        "reasons": reasons,
        "mosaic": mosaic,
        "rotation_max_deg": rotation,
        "scale_dev_max": scale_dev,
        "span_px": span,
        "degradations": degradations,
    }


def derive_calibration(calibration_plan, structure, chain):
    """Translate a calibration plan into ``calibrate`` command parameters.

    The CFA/debayer choice is folded in here because it determines whether
    ``-debayer`` and ``-equalize_cfa`` appear, which in turn determines whether
    downstream registration may still use drizzle.
    """
    args = []
    if calibration_plan.get("bias"):
        args.append("-bias=%s" % calibration_plan["bias"])
    if calibration_plan.get("dark"):
        args.append("-dark=%s" % calibration_plan["dark"])
    if calibration_plan.get("flat"):
        args.append("-flat=%s" % calibration_plan["flat"])

    cc = calibration_plan.get("cc") or {}
    if cc.get("enabled") and calibration_plan.get("dark"):
        # -cc=dark derives hot/cold pixel maps from a *master dark*. Without one it
        # is a no-op at best, so only emit it when a dark will actually be applied.
        # Smart scopes have their darks already subtracted and expose no master.
        if cc.get("siglo") is not None:
            args.append("-cc=dark %s %s" % (cc["siglo"], cc["sighi"]))
        else:
            args.append("-cc=dark %s" % cc["sighi"])

    if structure["is_cfa"]:
        args.append("-cfa")
        if chain == "debayer":
            args.append("-debayer")
            # -equalize_cfa balances the RGB layer means of the *master flat*.
            # It is meaningless without a flat and does not perform per-frame
            # background equalisation, which is an explicit stack option.
            if calibration_plan.get("flat"):
                args.append("-equalize_cfa")
    return args


def derive_registration(structure, chain, device, transform_summary):
    """Derive ``register`` parameters for the probe and formal passes."""
    min_pairs = device["profile"].get("min_pairs", 4)
    args = ["-transf=homography", "-minpairs=%d" % min_pairs]
    reasons = []

    if structure["is_cfa"]:
        reasons.append(
            "CFA input: star detection runs on green pixels by construction"
        )
    if chain == "drizzle":
        reasons.append(
            "drizzle chain selected, so the transform model must stay global "
            "(homography) and interpolation is deferred to drizzle"
        )
    return {
        "probe_args": list(args) + ["-2pass"],
        "apply_args": list(args),
        "reasons": reasons,
    }


def derive_apply(structure, chain, device):
    """Derive ``seqapplyreg`` parameters for the chosen chain."""
    if chain == "drizzle":
        args = [
            "-drizzle",
            "-pixfrac=1.0",
            "-kernel=square",
            "-framing=max",
        ]
        notes = [
            "framing=max paired with stack -maximize to realise the bounding box",
            "no -flat= : smart scopes produce no flats, so drizzle weights are "
            "unavailable; recorded as a quality risk rather than synthesised",
        ]
        if structure["is_cfa"] and structure["bayerpat"]:
            notes.append(
                "drizzle reads the raw mosaic, so BAYERPAT=%s stays in effect"
                % structure["bayerpat"]
            )
    else:
        args = ["-framing=current"]
        notes = ["framing=current: no mosaic re-framing requested"]
    return {"args": args, "notes": notes}


def derive_stacking(device, chain, structure):
    """Derive ``stack`` parameters, enforcing the 1.4 median/maximize conflict."""
    profile = device["profile"]
    is_smart = device["class"] == "smart"

    filters = list(SMART_FILTERS if is_smart else TRADITIONAL_FILTERS)
    if is_smart:
        notes = [
            "smart-scope filter set: weighted FWHM keeping the best 90%, relaxed "
            "roundness (ZWO's own Seestar script documents -filter-round= as the "
            "discard-throttling knob), star count keeping the best 85%",
            "percentile forms are used instead of k-sigma because k-sigma "
            "thresholds are derived from mean and standard deviation, which the "
            "outliers these filters target inflate; on real 134-frame Seestar data "
            "0.8k rejected nothing while 90% removed the tail",
            "N% means 'keep the best N%', so a HIGHER number is the conservative "
            "choice: measured isolated on the same data, nbstars=25% kept 35 of "
            "134 frames, 10% kept 16, 5% kept 8, 2% kept 4",
            "-filter-quality is omitted on purpose: it is only populated by "
            "planetary DFT/Kombat registration and is empty for star alignment",
        ]
    else:
        notes = ["traditional filter set: weighted FWHM only"]

    # median is banned whenever -maximize is in play because Siril 1.4 only logs
    # "Cannot upscale or maximize framing with median stacking. Disabling".
    maximize = chain == "drizzle"
    if maximize:
        notes.append(
            "median stacking is not offered here: combined with -maximize Siril "
            "1.4 silently disables the framing instead of erroring"
        )

    args = ["rej", "w", "4", "3"]
    args += ["-norm=addscale"]
    args += ["-weight=wfwhm"]
    if maximize:
        args.append("-maximize")
    args += filters
    args.append("-rejmap")
    args.append("-32b")

    return {
        "method": "rej",
        "rejection": "winsorized",
        "sigma_low": 4.0,
        "sigma_high": 3.0,
        "norm": "addscale",
        "weight": "wfwhm",
        "maximize": maximize,
        "rejmap": True,
        "filters": filters,
        "args": args,
        "notes": notes + [
            "weight=wfwhm uses the star-count-weighted FWHM recorded during "
            "registration, which rejects spurious point sources better than raw "
            "FWHM",
        ],
    }


def derive_preview_mtf(channel_medians):
    """Derive a linked display stretch from whole-image channel medians.

    The minimum finite channel level sets an explicit midpoint above that level,
    clipped to 0.01..0.4. This is a reproducible display heuristic, not a sky-only
    estimate or color correction; object emission and padding affect the input.
    """
    values = [abs(v) for v in (channel_medians or {}).values()
              if isinstance(v, (int, float)) and math.isfinite(v)]
    if values:
        background = min(values)
        mid = background * 2.0
        mid = min(max(mid, 0.01), 0.4)
        derived_from = "minimum whole-image channel median %.6f x 2.0" % background
    else:
        mid = 0.028
        derived_from = "default; no channel medians available"
    return {
        "method": "mtf",
        "low": 0.0,
        "mid": round(mid, 6),
        "high": 1.0,
        "derived_from": derived_from,
    }


def derive_pipeline(header, device, calibration_plan, transform_summary=None,
                    force_chain=None):
    """Derive the complete parameter set for one target.

    Args:
        header: Header of the first light frame.
        device: Output of :func:`device_signatures.identify_device`.
        calibration_plan: Output of
            :func:`device_signatures.resolve_calibration_plan`.
        transform_summary: Output of
            :func:`fits_probe.summarize_transforms`, or ``None`` on the first
            pass when the chain has not been probed yet.
        force_chain: Optional ``"drizzle"`` / ``"debayer"`` override.

    Returns:
        Dict with ``structure``, ``chain``, ``calibrate``, ``register``,
        ``apply``, ``stack`` and ``notes``.
    """
    structure = classify_input(header)
    chain_info = decide_chain(
        structure, transform_summary, device, force=force_chain
    )
    chain = chain_info["chain"]
    calibration_args = derive_calibration(calibration_plan, structure, chain)
    registration = derive_registration(structure, chain, device, transform_summary)
    apply_params = derive_apply(structure, chain, device)
    stacking = derive_stacking(device, chain, structure)

    return {
        "structure": structure,
        "chain": chain,
        "chain_info": chain_info,
        "calibrate": calibration_args,
        "register": registration,
        "apply": apply_params,
        "stack": stacking,
        "notes": chain_info["reasons"] + apply_params["notes"] + stacking["notes"],
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None