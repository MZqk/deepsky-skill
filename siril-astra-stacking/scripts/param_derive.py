"""Parameter derivation: turns probe results into Siril command parameters.

This module owns the twelve behavioural decisions. It is pure logic -- no file
writes, no Siril invocation -- so every branch is unit-testable and the generated
``.sir`` can be asserted without executing anything.

Three chains exist for CFA data. They are strictly mutually exclusive:

* **drizzle chain** -- ``calibrate -cfa`` (no debayer) -> ``register -2pass``
  probe -> ``seqapplyreg -drizzle -framing=max`` -> ``stack rej ... -maximize``.
  Required when the sequence spans more than one field or rotates materially per
  frame.
* **debayer chain** -- ``calibrate -cfa -debayer -equalize_cfa`` ->
  ``register`` -> ``seqapplyreg -framing=current`` -> ``stack rej ...``.
* **mono chain** -- ``calibrate -cfa`` (no debayer, no equalize) ->
  ``register`` -> ``seqapplyreg -framing=current`` -> ``stack rej ...``.
  Identical to the debayer chain minus the demosaic, so the master stays
  ``NAXIS=2`` with ``BAYERPAT`` intact. Chosen only when the device profile
  marks a monochrome sensor, because for a colour sensor the demosaic is what
  turns the Bayer mosaic into an image and dropping it would discard the
  colour data outright.

Why they cannot be mixed: Siril refuses drizzle on non-Bayer data
(``Cannot use drizzle on non-bayer sensors, aborting.`` / ``This sequence is not
mono / CFA, cannot drizzle.``) and warns against plain interpolation on CFA data
(``Applying interpolation on a sequence opened as CFA is a bad idea.``).
Debayering before registration would make the drizzle route unavailable; staying
CFA makes ordinary interpolation lossy. The probe therefore decides the chain
*before* any transformation is applied.

The drizzle and debayer chains both end in a three-channel RGB master (measured
on 1.4.4); only the timing of the demosaic differs. The debayer chain converts in
``calibrate``; the drizzle chain leaves the data CFA and lets
``seqapplyreg -drizzle`` do it, which is why it refuses a sequence that was
already debayered. **Siril 1.4.4 has no ``-mono`` flag** (verified against the
binary), so mono is not a stack option: it is the *absence* of ``-debayer``.
That also makes mono unreachable from the drizzle branch, since drizzle
demosaics internally no matter how the frames were calibrated.

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
import os

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

#: Chain identifier for the monochrome-sensor path: the debayer chain without
#: the demosaic, so the master stays ``NAXIS=2``. Not a Siril mode but the
#: absence of ``-debayer``; Siril 1.4.4 exposes no ``-mono`` flag.
MONO_CHAIN = "mono"

#: Chain identifiers a caller may pin with ``--force-chain``.
FORCEABLE_CHAINS = ("drizzle", "debayer", MONO_CHAIN)

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
        force: Optional ``"drizzle"`` / ``"debayer"`` / ``"mono"`` to bypass
            detection. ``"mono"`` bypasses the thresholds outright: otherwise
            the override would be a silent no-op on exactly the wide-field
            data that needs it most.

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
            "mono_requested": False,
            "mosaic": bool(transform_summary and transform_summary.get("mosaic")),
            "rotation_max_deg": transform_summary.get("rotation_max_deg", 0.0)
            if transform_summary else 0.0,
            "scale_dev_max": transform_summary.get("scale_dev_max", 0.0)
            if transform_summary else 0.0,
            "span_px": transform_summary.get("span", 0.0)
            if transform_summary else 0.0,
            "degradations": degradations,
        }

    profile = device["profile"]
    # Mono is a property of the sensor, not of this run: a monochrome body
    # behind a filter wheel carries no per-pixel colour, so demosaicing it
    # only interpolates one and the same signal into three copies. Colour
    # sensors must never take this path -- for them the demosaic *is* the
    # image, and dropping it discards the colour data outright.
    mono_requested = force == MONO_CHAIN or bool(
        profile.get("mono_sensor_capable"))

    if force in FORCEABLE_CHAINS:
        if force == MONO_CHAIN and not profile.get("mono_sensor_capable"):
            # Refuse rather than silently demosaicing a colour sensor: the
            # three planes are not three views of one signal, they are the
            # only copy of the colour information in the data.
            return {
                "chain": "debayer",
                "reasons": [
                    "--force-chain mono was requested but the device profile "
                    "does not mark a monochrome sensor; a Bayer colour mosaic "
                    "needs -debayer to become an image, and skipping it would "
                    "discard the colour data. Remove the flag, or point "
                    "--device-profile at an override only if the sensor really "
                    "is monochrome."
                ],
                "mono_requested": True,
                "mosaic": bool(transform_summary and transform_summary.get("mosaic")),
                "rotation_max_deg": transform_summary.get("rotation_max_deg", 0.0)
                if transform_summary else 0.0,
                "scale_dev_max": transform_summary.get("scale_dev_max", 0.0)
                if transform_summary else 0.0,
                "span_px": transform_summary.get("span", 0.0)
                if transform_summary else 0.0,
                "degradations": degradations,
            }
        return {
            "chain": force,
            # Name the flag that is actually accepted. The old text cited
            # "--drizzle" and "--no-drizzle", neither of which exists.
            "reasons": ["forced via --force-chain %s" % force],
            "mono_requested": mono_requested,
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

    if reasons:
        chain = "drizzle"
        if mono_requested:
            # Say why mono is not available *here*, and how to get it anyway.
            # Silently handing back an RGB master would leave the caller with
            # a three-channel file they never asked for.
            reasons.append(
                "the device profile marks a monochrome sensor, but the measured "
                "geometry requires drizzle (%s); seqapplyreg -drizzle demosaics "
                "internally, so a NAXIS=2 master is unreachable once drizzle is "
                "chosen. Pass --force-chain mono to override and accept the "
                "edge data that -framing=current will interpolate away."
                % "; ".join(reasons)
            )
    elif mono_requested:
        chain = MONO_CHAIN
        reasons.append(
            "device profile marks a monochrome sensor and the measured geometry "
            "is within thresholds (span %.1f px, rotation %.2f deg, scale "
            "deviation %.2f%%); nothing is demosaiced, so the master stays "
            "NAXIS=2 with BAYERPAT=%s intact. Siril 1.4.4 has no -mono flag: "
            "mono is the absence of -debayer."
            % (span, rotation, scale_dev * 100.0, structure["bayerpat"])
        )
    else:
        chain = "debayer"
        reasons.append(
            "span %.1f px, rotation %.2f deg, scale deviation %.2f%% all within "
            "thresholds; the simpler debayer chain is sufficient"
            % (span, rotation, scale_dev * 100.0)
        )

    if chain == "drizzle" and not profile.get("flats_available"):
        degradations.append("drizzle_without_flat_weights")

    return {
        "chain": chain,
        "reasons": reasons,
        "mono_requested": mono_requested,
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
        bias_val = str(calibration_plan["bias"])
        if not bias_val.startswith("="):
            bias_val = os.path.abspath(bias_val)
        args.append("-bias=%s" % bias_val)
    if calibration_plan.get("dark"):
        args.append("-dark=%s" % os.path.abspath(str(calibration_plan["dark"])))
    if calibration_plan.get("flat"):
        args.append("-flat=%s" % os.path.abspath(str(calibration_plan["flat"])))

    cc = calibration_plan.get("cc") or {}
    if cc.get("enabled") and calibration_plan.get("dark"):
        # -cc=dark derives hot/cold pixel maps from a *master dark*. Without one it
        # is a no-op at best, so only emit it when a dark will actually be applied.
        # Smart scopes have their darks already subtracted and expose no master.
        #
        # Measured on siril-cli 1.4.4: only the BARE form is scriptable. The
        # manual documents "-cc=dark [siglo] sighi", but "-cc=dark 3" aborts
        # with "Unknown parameter 3" (the parser never consumes the values --
        # the "sigma 3.00 for hot pixels" log line is the built-in default,
        # not the argument), "-cc=dark=3" is rejected outright, and quoting
        # "-cc=dark 3" as one token is rejected as an unknown argument. Bare
        # "-cc=dark" means hot-only sigma 3 with cold pixels disabled, which
        # is exactly the plan default (siglo=None, sighi=3).
        if cc.get("siglo") is not None or cc.get("sighi") != 3:
            raise ValueError(
                "-cc=dark sigmas (siglo=%s, sighi=%s) cannot be expressed in a "
                "Siril 1.4.4 script: the CLI parser rejects '-cc=dark 3' "
                "('Unknown parameter'), '-cc=dark=3' and the quoted single "
                "token ('Unknown argument'); only the bare default form "
                "(-cc=dark: hot sigma 3, cold disabled) is supported"
                % (cc.get("siglo"), cc.get("sighi"))
            )
        args.append("-cc=dark")

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
    elif chain == MONO_CHAIN:
        reasons.append(
            "mono chain selected, so the frames stay CFA end to end; star "
            "detection still runs on the green pixels, which is what the "
            "BAYERPAT layout puts at the sampled positions"
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
            "no -flat= on seqapplyreg: drizzle flat weighting stays off; this is "
            "independent of calibration flats, which were already applied during "
            "calibration when the device produces them",
        ]
        if structure["is_cfa"] and structure["bayerpat"]:
            notes.append(
                "drizzle reads the raw mosaic, so BAYERPAT=%s stays in effect"
                % structure["bayerpat"]
            )
    else:
        args = ["-framing=current"]
        notes = ["framing=current: no mosaic re-framing requested"]
        if chain == MONO_CHAIN:
            notes.append(
                "Siril warns 'Applying interpolation on a sequence opened as "
                "CFA is a bad idea'; that warning targets Bayer colour mosaics. "
                "On a monochrome sensor every position measures the same "
                "narrowband signal, so interpolation copies one signal into "
                "three planes instead of mixing colours, and the master keeps "
                "BAYERPAT"
            )
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


def derive_preview_mtf(channel_medians, mono=False):
    """Derive a linked display stretch from whole-image channel medians.

    The minimum finite channel level sets an explicit midpoint above that level,
    clipped to 0.01..0.4. This is a reproducible display heuristic, not a sky-only
    estimate or color correction; object emission and padding affect the input.

    Args:
        channel_medians: Measured medians keyed by channel, or ``None``.
        mono: True for a single-channel master, whose dict has one key. The
            value is used directly instead of being called a "channel"
            minimum, which would misdescribe a mono master as one of three.
    """
    values = [abs(v) for v in (channel_medians or {}).values()
              if isinstance(v, (int, float)) and math.isfinite(v)]
    if values:
        background = min(values)
        mid = background * 2.0
        mid = min(max(mid, 0.01), 0.4)
        derived_from = (
            "whole-image mono median %.6f x 2.0" % background if mono
            else "minimum whole-image channel median %.6f x 2.0" % background
        )
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
        force_chain: Optional ``"drizzle"`` / ``"debayer"`` / ``"mono"`` override.
            ``"mono"`` requires a profile that marks a monochrome sensor;
            asking for it on a colour sensor is refused rather than honoured.

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