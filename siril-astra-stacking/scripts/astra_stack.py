"""siril-astra-stacking entry point: probe, derive, run, report, commit.

Failure semantics are the reason this is not a thin wrapper around Siril. All
intermediate work happens inside ``<target>.work/``; the three deliverables are
moved into their final location only after every stage has succeeded, by renaming
within the same filesystem. A crashed or failed run therefore cannot be mistaken
for a good one -- the previous master is either intact or replaced, never
half-overwritten.

Stages, in order:

1. ``probe``    -- locate frames, read headers, classify CFA vs RGB.
2. ``identify`` -- match the device signature; refuse when unknown.
3. ``plan``     -- resolve calibration masters and derive parameters.
4. ``probe-reg``-- ``register -2pass`` feeding the chain decision and reusable transforms.
5. ``stack``    -- generate and execute the ``.sir``.
6. ``preview``  -- MTF-stretch the master into a PNG.
7. ``commit``   -- atomically publish the three artifacts.

Exit codes:
    0 success | 1 Siril unavailable | 2 device unidentified
    3 calibration missing | 4 no usable frames | 5 Siril failed
    6 unsupported input | 7 commit failed

Usage:
    python3 astra_stack.py /data/M42 --out /results
    python3 astra_stack.py /data/M42 --out /results --dry-run
    python3 astra_stack.py /data/M42 --out /results --resume

Requires: ``siril-cli`` 1.4.x on PATH, at an absolute location, or pointed at by
``--siril``. Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from device_signatures import (
    CalibrationMissing,
    DeviceNotIdentified,
    E_CALIBRATION_MISSING,
    E_DEVICE_UNKNOWN,
    identify_device,
    resolve_calibration_plan,
)
from fits_probe import (
    REJECTED_SUFFIXES,
    FitsError,
    UnsupportedInputError,
    estimate_channel_medians,
    read_fits_header_with_warnings,
    read_seq_regdata,
    summarize_transforms,
)
from param_derive import (
    MOSAIC_SPAN_FACTOR,
    classify_input,
    decide_chain,
    derive_pipeline,
    derive_preview_mtf,
)
from pws_bridge import (
    PWS_FRAMES_DIRNAME,
    PWS_INPUT_DIRNAME,
    PWS_OUT_DIRNAME,
    PwsBridgeError,
    check_black_corners,
    convert_xisf_master,
    evaluate_gate,
    prepare_pws_input,
    resolve_pws_engine,
)
from siril_script_gen import ScriptBuilder, ScriptValidationError

EXIT_OK = 0
EXIT_SIRIL_MISSING = 1
EXIT_DEVICE_UNKNOWN = E_DEVICE_UNKNOWN
EXIT_CALIBRATION_MISSING = E_CALIBRATION_MISSING
EXIT_NO_FRAMES = 4
EXIT_SIRIL_FAILED = 5
EXIT_UNSUPPORTED_INPUT = 6
EXIT_COMMIT_FAILED = 7

SCHEMA_VERSION = "1.2"

#: ``--stack-engine`` choices. ``siril`` (default) is the wfwhm-weighted
#: rejection stack; ``pws`` hands the registered frames to the Partition-Weighted
#: Stacking engine (sibling pws-stacking skill) after the gate passes.
STACK_ENGINES = ("siril", "pws")

#: HowSiril splits a filename into sequence stem and frame index. A trailing
#: run of digits is claimed by the index, so a stem must not end in a digit.
#: Directly observed with frames ``astra_t10000.fit`` .. ``astra_t10003.fit``:
#: Siril reported ``Sequence found: astra_t 10000->10003``, i.e. it took
#: ``astra_t`` as the stem and the leading digit of the frame number as part of
#: the index. Requesting ``register astra_t1`` then failed with
#: ``invalid input sequence``. Renaming to a stem that does not end in a digit
#: (``astra_s``) resolves it.
SEQUENCE_STEM_MAX = 8


def sequence_stem(target_name):
    """Return a Siril-safe sequence stem.

    Args:
        target_name: Full target name, possibly containing spaces.

    Returns:
        A stem of ASCII alphanumerics and underscores, at most
        :data:`SEQUENCE_STEM_MAX` characters, never empty, and never ending in a
        digit (see :data:`SEQUENCE_STEM_MAX` for why).
    """
    cleaned = "".join(
        ch if (ch.isalnum() and ch.isascii()) else "_" for ch in target_name
    ).strip("_")
    cleaned = cleaned or "astra"
    if cleaned[-1].isdigit():
        # Appending before truncating would be undone by the truncation itself, so
        # the letter replaces the offending digit slot: "m42" -> "m4s".
        cleaned = cleaned[:-1] + "s"
    return cleaned[:SEQUENCE_STEM_MAX] or "astra_s"

#: Candidate locations for siril-cli. Siril is frequently absent from PATH on
#: macOS even when installed, so the bundle location is checked explicitly.
SIRIL_CANDIDATES = [
    "/Applications/Siril.app/Contents/MacOS/siril-cli",
    "/usr/local/bin/siril-cli",
    "/usr/bin/siril-cli",
    "/opt/homebrew/bin/siril-cli",
    "/opt/siril/bin/siril-cli",
]


def find_siril(explicit=None):
    """Locate siril-cli.

    Args:
        explicit: Path given via ``--siril``.

    Returns:
        Path to siril-cli, or ``None`` when not found.
    """
    if explicit:
        return explicit if os.access(explicit, os.X_OK) else None
    from shutil import which

    found = which("siril-cli")
    if found:
        return found
    for candidate in SIRIL_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def siril_version(siril_path):
    """Return the Siril version string, or ``None`` when unavailable."""
    try:
        result = subprocess.run(
            [siril_path, "--version"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (result.stdout + result.stderr).splitlines():
        if "siril" in line.lower() and any(ch.isdigit() for ch in line):
            return line.strip()
    return None


def collect_frames(input_dir):
    """Return sorted FITS frames in ``input_dir``.

    Raises:
        UnsupportedInputError: when the directory holds only refused formats, so
            the user learns why their video or preview JPEGs were skipped.
    """
    frames = sorted(
        glob.glob(os.path.join(input_dir, "*.fit"))
        + glob.glob(os.path.join(input_dir, "*.fits"))
        + glob.glob(os.path.join(input_dir, "*.fts"))
    )
    if frames:
        return frames
    rejected = []
    for entry in sorted(os.listdir(input_dir)):
        suffix = os.path.splitext(entry)[1].lower()
        if suffix in REJECTED_SUFFIXES:
            rejected.append(entry)
    if rejected:
        raise UnsupportedInputError(
            "no FITS frames in %s; found %d refused file(s): %s. Video and 8-bit "
            "preview files are rejected by design -- export the individual light "
            "frames as FITS on the device first."
            % (input_dir, len(rejected), ", ".join(rejected[:5]))
        )
    raise UnsupportedInputError("no FITS frames found in %s" % input_dir)


def stage_workdir(input_dir, out_dir, target_name, force=False):
    """Create or reuse ``<out_dir>/<target>.work`` with symlinked inputs.

    Args:
        input_dir: Directory holding the source FITS frames.
        out_dir: Output directory that will contain the work directory.
        target_name: Full target name used for the work directory and artifacts.
        force: Remove an existing work directory even if it looks foreign.

    Returns:
        ``(work_dir, state_path)``.
    """
    work = os.path.join(out_dir, "%s.work" % target_name)
    if os.path.exists(work):
        if not force and not os.path.exists(os.path.join(work, ".astra-state.json")):
            raise RuntimeError(
                "%s exists but was not created by this tool; remove it or pass "
                "--force" % work
            )
        shutil.rmtree(work)
    os.makedirs(work)

    # Siril resolves sequence names relative to its working directory, which
    # ``-d`` sets to the work directory. Frames must therefore sit at the *top*
    # level of the work directory, not in a sub-directory, or "No sequence found"
    # is reported. Each frame is linked as ``<stem>NNNN.fit`` so that a single
    # truncated-safe sequence stem addresses them all in a stable order.
    stem = sequence_stem(target_name)
    for position, frame in enumerate(collect_frames(input_dir)):
        suffix = os.path.splitext(frame)[1] or ".fit"
        destination = os.path.join(work, "%s%04d%s" % (stem, position, suffix))
        if not os.path.exists(destination):
            os.symlink(os.path.abspath(frame), destination)

    state_path = os.path.join(work, ".astra-state.json")
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "target": target_name,
                "sequence_stem": stem,
                "input_dir": os.path.abspath(input_dir),
            },
            handle,
            indent=2,
        )
    return work, state_path


def atomic_publish(work_dir, out_dir, target_name, artifacts):
    """Move finished artifacts from the work directory into place.

    Each artifact is renamed within ``out_dir`` so no partial file is ever
    visible under the final name.

    Args:
        work_dir: Scratch directory holding the finished files.
        out_dir: Destination directory (must share a filesystem with work_dir).
        target_name: Base name for published files.
        artifacts: Dict of role to absolute source path inside ``work_dir``.

    Returns:
        Dict of role to published path.

    Raises:
        RuntimeError: on any rename failure.
    """
    published = {}
    try:
        os.makedirs(out_dir, exist_ok=True)
        for role, source in artifacts.items():
            if not os.path.exists(source):
                raise RuntimeError("expected artifact missing: %s" % source)
            final_name = os.path.basename(source)
            if role == "master":
                final_name = "%s_stacked_32bit.fits" % target_name
            elif role == "preview":
                final_name = "%s.png" % target_name
            elif role == "report":
                final_name = "%s_report.json" % target_name
            destination = os.path.join(out_dir, final_name)
            if os.path.exists(destination):
                os.remove(destination)
            os.rename(source, destination)
            published[role] = destination
    except (OSError, RuntimeError) as exc:
        raise RuntimeError("atomic publish failed: %s" % exc) from exc
    return published


def build_probe_script(work_dir, target_name, calibrated_seq, min_pairs=4):
    """Build the standalone ``register -2pass`` probe script.

    Run standalone because its output (regdata in the ``.seq``) is what the chain
    decision consumes. The main script is generated after this decision and
    reuses the probe data when the calibrated pixels remain unchanged.

    The probe must run on the *calibrated* sequence. Running it before
    ``calibrate`` works but is useless: ``calibrate`` writes a new sequence and
    resets its registration data, so ``seqapplyreg`` later aborts with
    "Existing registration data is a set of identity matrices, no transformation
    would be applied".

    Args:
        work_dir: Work directory that will become Siril's CWD.
        target_name: Full target name.
        calibrated_seq: Name of the calibrated sequence to probe.
        min_pairs: Minimum star pairs required to keep a frame. Siril's default is
            8, which silently drops every frame when the field is sparse -- smart
            telescopes with a small sensor often yield fewer.

    Returns:
        ``(builder, calibrated_seq)``.
    """
    builder = ScriptBuilder(header_comment=[
        "target: %s" % target_name,
        "stage: transform probe on the calibrated sequence (no image output)",
    ])
    builder.add(
        "register",
        [calibrated_seq, "-prefix=probe_", "-transf=homography",
         "-minpairs=%d" % min_pairs, "-2pass"],
        comment="compute transforms only; the chain decision reads the regdata "
                "this writes into the .seq",
    )
    return builder, calibrated_seq


def build_script(work_dir, target_name, pipeline, calibration_plan, master_path,
                 include_calibration=True, stack_engine="siril",
                 registration_available=False, debayer_only=False, rgb_equal=False):
    """Assemble the ``.sir`` script for registration through stacking.

    Sequence naming is the fragile part. Siril derives each stage's sequence name
    from its ``-prefix``, prepended to the *previous* stage's name: ``calibrate``
    writes ``c_<stem>`` and the next stage writes ``ap_c_<stem>`` (observed
    directly). Using the full target name produced ``r_astra_t1``, which matched no
    file and failed with "invalid input sequence".

    ``-2pass`` is not optional here. Measured against Siril 1.4.4: a plain
    ``register`` writes identity homographies into the output sequence's ``.seq``
    (all nine coefficients read ``1 0 0 0 1 0 0 0 1``), and the following
    ``seqapplyreg`` then aborts with "Existing registration data is a set of
    identity matrices, no transformation would be applied". The ``-2pass`` pass
    instead leaves the measured transforms on the *source* sequence, which
    ``seqapplyreg`` consumes directly. So a second, plain ``register`` is not run.

    Args:
        work_dir: Work directory used for diagnostics only; paths in the script
            are relative because Siril resolves them against its own CWD.
        target_name: Full target name.
        pipeline: Output of :func:`param_derive.derive_pipeline`.
        calibration_plan: Calibration plan, kept for signature symmetry.
        master_path: Absolute path for the stacked output.
        include_calibration: Apply the full calibration plan to the source.
        registration_available: Reuse validated probe data on unchanged pixels.
        rgb_equal: Explicitly enable native RGB background normalization at stack.
        debayer_only: Demosaic the calibrated CFA sequence, then register RGB;
            calibration masters must not be applied again.
        stack_engine: ``"siril"`` emits the ``stack`` command; ``"pws"`` stops the
            script after ``seqapplyreg`` because the registered ``ap_`` frames are
            handed to the PWS engine instead. The ``master_path`` argument is
            unused in that case (the master is produced outside Siril).

    Returns:
        ``(builder, registered_seq)`` where ``registered_seq`` is the sequence that
        carries the registration data, i.e. the one ``stack`` consumes.
    """
    if rgb_equal and stack_engine != "siril":
        raise ScriptValidationError("RGB background equalisation requires the Siril engine")
    stem = sequence_stem(target_name)
    calibrated = "c_%s" % stem
    if include_calibration and debayer_only:
        raise ScriptValidationError("full calibration and debayer-only are exclusive")
    registration_available = registration_available and not (
        include_calibration or debayer_only
    )
    applied = "ap_%s" % ("d_%s" % calibrated if debayer_only else calibrated)

    builder = ScriptBuilder(
        header_comment=[
            "target: %s" % target_name,
            "sequence stem: %s" % stem,
            "chain: %s" % pipeline["chain"],
        ],
        registration_available=registration_available,
    )

    calibrate_args = [stem, "-prefix=c_"] + list(pipeline["calibrate"])
    if include_calibration:
        builder.add("calibrate", calibrate_args, comment="calibration")
    elif debayer_only:
        builder.add("calibrate", [calibrated, "-prefix=d_", "-cfa", "-debayer"],
                    comment="debayer calibrated CFA only; no calibration masters")
        calibrated = "d_%s" % calibrated

    # -2pass keeps the measured transforms on the calibrated sequence, which is
    # what seqapplyreg needs; a plain register would leave identity matrices.
    if not registration_available:
        builder.add(
            "register",
            [calibrated] + list(pipeline["register"]["apply_args"]) + ["-2pass"],
            comment="compute registration transforms (-2pass, no image output)",
        )

    filters = list(pipeline["stack"]["filters"]) if stack_engine == "siril" else []
    builder.add(
        "seqapplyreg",
        [calibrated, "-prefix=ap_"] + list(pipeline["apply"]["args"]) + filters,
        comment="select frames once, then apply transforms (%s)" % pipeline["chain"]
                if filters else "apply transforms (%s)" % pipeline["chain"],
    )

    if stack_engine == "pws":
        # The registered frames ap_c_<stem>NNNN.fit are the hand-off product;
        # stacking happens in the PWS engine, outside Siril.
        builder.add(
            "seqsubsky", [applied, "1", "-prefix=sk_"],
            comment="per-frame synthetic background subtraction so the PWS "
                    "engine's additive sky translation works on a near-zero "
                    "common sky level (1.4 seqsubsky <degree> -prefix=)",
        )
        return builder, applied

    stack_args = [applied] + [arg for arg in pipeline["stack"]["args"]
                              if arg not in filters]
    if rgb_equal:
        stack_args.append("-rgb_equal")
    # -out takes a real path; unlike sequence names it is not CWD-relative.
    stack_args += ["-out=%s" % master_path]
    builder.add("stack", stack_args, comment="stack all exported frames; reject pixel outliers")

    return builder, applied


def _reference_filter_fallback(builder, pipeline):
    """Reuse the final source and matrices when current framing excludes its reference."""
    fallback = ScriptBuilder(header_comment=builder.header_comment + [
        "reference excluded: apply all frames, select once during stacking"
    ], registration_available=True)
    filters = pipeline["stack"]["filters"]
    for _comment, command, args in builder.commands:
        if command == "seqapplyreg":
            fallback.add(command, [arg for arg in args if arg not in filters],
                         comment="apply all frames on the original reference canvas")
        elif command == "stack":
            fallback.add(command, args + list(filters),
                         comment="select from the full sequence and stack")
    return fallback


def _run_pws_engine_stage(args, report, work_dir, out_dir, target_name, stem,
                          registered_seq, front_end, gate_report, engine,
                          started, master_path, script_path=None):
    """Run the PWS engine on the registered frames and finish the pipeline.

    Called after the Siril front end (calibrate -> register -2pass ->
    seqapplyreg -> seqsubsky) has completed. Everything from here on mirrors
    the Siril path's post-stack stages: XISF-to-FITS conversion, preview,
    report, atomic publish -- so the deliverable contract does not change with
    the engine.

    Returns:
        Process exit code.
    """
    report["completed_stages"].append("stack(front-end)")
    if front_end.returncode != 0:
        report["failed_stage"] = "stack"
        report["error"] = (
            "Siril front end failed (exit %d); no registered frames for the "
            "PWS engine. Log: %s" % (front_end.returncode, script_path)
        )
        report["notes"].extend(_parse_siril_log(front_end.stdout))
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    try:
        handoff = prepare_pws_input(work_dir, stem, registered_seq=registered_seq)
    except PwsBridgeError as exc:
        report["failed_stage"] = "stack"
        report["error"] = str(exc)
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED
    gate_report["input_dir"] = handoff["input_dir"]
    gate_report["input_frame_count"] = handoff["frame_count"]

    # Black-corner guard: rotated sequences on a fixed canvas carry triangular
    # no-data regions that the engine (which has no coverage mask) reads as
    # background structure, and its precheck then refuses with a misleading
    # "uncalibrated" message. Detect the real cause here and refuse with an
    # accurate one.
    corners = check_black_corners(handoff["input_dir"])
    gate_report["black_corner_check"] = corners
    if (corners.get("worst_fraction") or 0.0) > (corners.get("threshold") or 0.02):
        report["failed_stage"] = "stack"
        report["error"] = (
            "PWS hand-off refused: %.1f%% of the sampled registered frames' "
            "area is no-data black corner (worst frame %.1f%%, threshold "
            "%.1f%%). Rotated sequences registered onto a fixed canvas carry "
            "triangular gaps the PWS engine cannot mask; use the default "
            "Siril stack (drizzle chain handles rotation natively) or crop "
            "the sequence to the common covered area first"
            % (100.0 * corners["worst_fraction"],
               100.0 * corners["worst_fraction"],
               100.0 * corners.get("threshold", 0.02))
        )
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED
    report["notes"].append(
        "PWS black-corner check: worst sampled frame %.1f%% no-data pixels "
        "(threshold %.1f%%)"
        % (100.0 * (corners.get("worst_fraction") or 0.0),
           100.0 * corners.get("threshold", 0.02))
    )

    # The engine requires an explicitly named spill directory and refuses to
    # run when its output directory equals the input directory (a *different*
    # safeguard from ours); give each its own place inside the work tree.
    frames_dir = os.path.join(work_dir, PWS_FRAMES_DIRNAME)
    pws_out_dir = os.path.join(work_dir, PWS_OUT_DIRNAME)
    tag = "astra"

    runner_result = _invoke_pws_runner(
        engine["runner"], handoff["input_dir"], pws_out_dir, frames_dir, tag,
        timeout=args.timeout,
    )
    report["stack_engine_run"] = runner_result

    xisf_master = os.path.join(pws_out_dir, "stack_%s.xisf" % tag)
    if runner_result["exit_code"] not in (0, 10) or not os.path.exists(xisf_master):
        report["failed_stage"] = "stack"
        report["error"] = (
            "PWS engine failed (runner exit %d); see pws_run.log in the work "
            "directory" % runner_result["exit_code"]
        )
        report["notes"].extend(runner_result["stderr_notes"])
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    try:
        conversion = convert_xisf_master(xisf_master, master_path)
    except FitsError as exc:
        report["failed_stage"] = "stack"
        report["error"] = "PWS master conversion failed: %s" % exc
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED
    report["stack_engine_run"]["master_conversion"] = {
        "width": conversion["width"],
        "height": conversion["height"],
        "stackmode": [kw for kw in conversion["keywords"]
                      if kw[0] == "STACKMODE"],
    }
    report["completed_stages"].append("stack")

    # -- post-analysis: report the engine's numbers --------------------------
    engine_json = runner_result.get("summary") or {}
    result = engine_json.get("result") or {}
    report["stacking"] = {
        "method": "pws",
        "engine": "pws-stacking (Partition-Weighted Stacking, D.Cikey)",
        "A": result.get("A"),
        "B": result.get("B"),
        "frames_stacked": result.get("n_frames"),
        "fwhm_median_px": result.get("fwhm_med_px"),
        "fwhm_master_px": result.get("fwhm_out_px"),
        "fwhm_improve_pct": result.get("fwhm_improve_pct"),
        "neff_median": result.get("neff_median"),
        "rej_rate": result.get("rej_rate"),
        "outputs": result.get("outputs"),
        "notes": [
            "weights are per-frame PWS clarity/SNR weights; see weights_astra.txt "
            "in the pws output directory",
            "the master is linear 32-bit FITS converted from the engine's XISF; "
            "STACKMODE records the engine parameters",
        ],
    }
    report["registration"] = {
        "chain": "debayer",
        "transform_model": "homography",
        "reasons": [
            "PWS engine consumes the registered frames directly; the chain is "
            "fixed to debayer because the drizzle chain writes per-frame "
            "canvases of differing sizes that the engine cannot consume"
        ],
        "mosaic": False,
        "degradations": [],
        "apply_args": ["-framing=current"],
    }

    # -- stage: preview (same contract as the Siril path) --------------------
    preview_path = os.path.join(work_dir, "%s.png" % target_name)
    try:
        channel_statistics = _channel_statistics_from_args(args, master_path)
    except FitsError as exc:
        report["failed_stage"] = "post-analysis"
        report["error"] = str(exc)
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED
    mtf = derive_preview_mtf(channel_statistics["medians"])
    siril_path = find_siril(args.siril)
    preview_ok, preview_note = _make_preview(
        siril_path, work_dir, master_path, preview_path, mtf
    )
    report["preview"] = dict(mtf)
    report["preview"]["produced"] = preview_ok
    if preview_note:
        report["notes"].append(preview_note)
    report["channel_stats"] = channel_statistics["report"]
    if preview_ok:
        report["completed_stages"].append("preview")

    # -- stage: commit --------------------------------------------------------
    report_path = os.path.join(work_dir, "%s_report.json" % target_name)
    write_report(report_path, dict(report, status="ok"))
    artifacts = {"master": master_path, "preview": preview_path,
                 "report": report_path}
    artifacts = {role: path for role, path in artifacts.items()
                 if os.path.exists(path)}
    try:
        published = atomic_publish(work_dir, out_dir, target_name, artifacts)
    except RuntimeError as exc:
        report["failed_stage"] = "commit"
        report["error"] = str(exc)
        _emit_final(report, out_dir, target_name)
        return EXIT_COMMIT_FAILED

    report["artifacts"] = published
    report["status"] = "ok"
    report["completed_stages"].append("commit")
    report["timings"]["total_s"] = round(time.time() - started, 3)
    if args.keep_work:
        report["work_dir"] = work_dir
        report["notes"].append(
            "--keep-work: scratch directory retained at %s" % work_dir
        )
    else:
        shutil.rmtree(work_dir, ignore_errors=True)
    _emit_final(report, out_dir, target_name)
    return EXIT_OK


def _invoke_pws_runner(runner, photos, out_dir, frames_dir, tag, timeout):
    """Call the sibling pws-stacking runner as a subprocess.

    Subprocess, not import: the engine's dependency stack (numpy/scipy/
    astropy/photutils) must never be loaded into this process, and the runner's
    precheck/JSON contract is already the audited interface.

    Returns:
        Dict with ``exit_code``, ``summary`` (the runner's JSON, parsed) and
        ``stderr_notes`` (the tail of the runner's stderr for report.notes).
    """
    command = [
        sys.executable, runner,
        "--photos", photos,
        "--out", out_dir,
        "--frames-dir", frames_dir,
        "--tag", tag,
    ]
    result = {"command": command, "exit_code": None, "summary": None,
              "stderr_notes": []}
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        result["exit_code"] = -1
        result["stderr_notes"] = ["pws runner invocation failed: %s" % exc]
        return result

    result["exit_code"] = completed.returncode
    try:
        result["summary"] = json.loads(completed.stdout)
    except ValueError:
        first = (completed.stdout.splitlines() or [""])[0]
        result["stderr_notes"] = [
            "pws runner stdout was not valid JSON; first line: %r" % first[:120]
        ]
    stderr_lines = [line for line in (completed.stderr or "").splitlines()
                    if line.strip()]
    result["stderr_notes"] = ["pws: %s" % line for line in stderr_lines[-8:]]
    return result


def write_report(path, payload):
    """Serialise the run report."""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def run_pipeline(args):
    """Execute one target end to end.

    Returns:
        Process exit code.
    """
    started = time.time()
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    target_name = args.target or os.path.basename(
        os.path.abspath(args.input.rstrip("/"))
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": "failed",
        "failed_stage": None,
        "completed_stages": [],
        "target": target_name,
        "input_dir": os.path.abspath(args.input),
        "siril": {"path": args.siril, "version": None},
        "input": {},
        "device": None,
        "calibration": None,
        "registration": None,
        "stacking": None,
        "preview": None,
        "channel_stats": None,
        "artifacts": {},
        "warnings": [],
        "notes": [],
        "timings": {},
    }

    siril_path = find_siril(args.siril)
    if siril_path is None:
        report["failed_stage"] = "probe"
        report["error"] = (
            "siril-cli not found. Searched PATH and %s. Install Siril 1.4.x, or "
            "pass --siril /path/to/siril-cli." % ", ".join(SIRIL_CANDIDATES)
        )
        report["timings"]["total_s"] = round(time.time() - started, 3)
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_MISSING
    report["siril"]["path"] = siril_path
    report["siril"]["version"] = siril_version(siril_path)

    version = report["siril"]["version"] or ""
    if "1.4" not in version:
        report["warnings"].append(
            "expected Siril 1.4.x but detected '%s'; commands outside 1.4 may "
            "be rejected" % (version or "unknown")
        )

    # -- stage: probe -------------------------------------------------------
    try:
        frames = collect_frames(args.input)
    except UnsupportedInputError as exc:
        report["failed_stage"] = "probe"
        report["error"] = str(exc)
        _emit_final(report, out_dir, target_name)
        return EXIT_UNSUPPORTED_INPUT

    header, header_warnings = read_fits_header_with_warnings(frames[0])
    report["completed_stages"].append("probe")
    report["input"] = {
        "frame_count": len(frames),
        "naxis": header.get("NAXIS"),
        "bitpix": header.get("BITPIX"),
        "bayerpat": (str(header.get("BAYERPAT")).strip()
                     if header.get("BAYERPAT") else None),
        "width": header.get("NAXIS1"),
        "height": header.get("NAXIS2"),
        "object": header.get("OBJECT"),
        "exptime": header.get("EXPTIME"),
        "warnings": header_warnings,
    }
    report["warnings"].extend(header_warnings)
    if len(frames) < 2:
        report["failed_stage"] = "probe"
        report["error"] = ("at least 2 light frames are required for stacking; "
                           "found %d" % len(frames))
        _emit_final(report, out_dir, target_name)
        return EXIT_NO_FRAMES

    # -- stage: identify ----------------------------------------------------
    try:
        device = identify_device(header, overrides=args.device_profile)
    except DeviceNotIdentified as exc:
        report["failed_stage"] = "identify"
        report["error"] = str(exc)
        report["device"] = {"identified": False, "evidence": exc.evidence}
        _emit_final(report, out_dir, target_name)
        return EXIT_DEVICE_UNKNOWN
    report["completed_stages"].append("identify")
    report["device"] = {
        "identified": True,
        "id": device["id"],
        "class": device["class"],
        "description": device["description"],
        "matched_by": device["matched_by"],
        "confidence": device["confidence"],
        "profile": device["profile"],
    }

    # -- stage: plan --------------------------------------------------------
    try:
        calibration_plan = resolve_calibration_plan(
            header, device, args.calibration_dir
        )
    except CalibrationMissing as exc:
        report["failed_stage"] = "plan"
        report["error"] = str(exc)
        report["calibration"] = {"kind": exc.kind, "candidates": exc.candidates}
        _emit_final(report, out_dir, target_name)
        return EXIT_CALIBRATION_MISSING

    report["calibration"] = calibration_plan
    report["warnings"].extend(calibration_plan.get("warnings", []))

    report["completed_stages"].append("plan")

    # -- stage: workdir -----------------------------------------------------
    work_dir, state_path = stage_workdir(
        args.input, out_dir, target_name, force=not args.resume
    )
    if args.resume:
        report["warnings"].append(
            "--resume requested; the work directory was rebuilt because "
            "cross-run checkpoint reuse is not implemented. Validated registration "
            "is reused between Siril calls within this run."
        )

    # -- stage: calibrate ---------------------------------------------------
    # Calibration runs first and on its own. It writes a new sequence and *resets*
    # registration data, so the transform probe has to come after it -- probing
    # first produces regdata that calibration then discards, and seqapplyreg later
    # aborts with "Existing registration data is a set of identity matrices".
    stem = sequence_stem(target_name)
    calibrated_seq = "c_%s" % stem

    probe_summary = None
    initial_chain = args.force_chain
    if (not args.dry_run and initial_chain is None
            and header.get("NAXIS") == 2 and not calibration_plan.get("flat")):
        initial_chain = "drizzle"  # Keep CFA until the measured chain is known.
    pipeline = derive_pipeline(header, device, calibration_plan, None,
                               force_chain=initial_chain)
    initial_calibration = list(pipeline["calibrate"])

    calibrate_args = [stem, "-prefix=c_"] + list(pipeline["calibrate"])
    calibrate_builder = None
    if len(calibrate_args) > 1:
        calibrate_builder = ScriptBuilder(header_comment=[
            "target: %s" % target_name,
            "stage: calibration only",
        ])
        calibrate_builder.add("calibrate", calibrate_args, comment="calibration")

    if args.dry_run:
        report["completed_stages"].append("calibrate(dry-run)")
    elif calibrate_builder is not None:
        calibrate_script = os.path.join(work_dir, "astra_calibrate.sir")
        try:
            calibrate_builder.write(calibrate_script)
            _record_cosmetic_correction(report, calibration_plan, calibrate_builder,
                                        calibrate_script, "planned")
            exit_code, stdout, log_path = _run_siril(
                siril_path, work_dir, calibrate_script, args.timeout
            )
        except (ScriptValidationError, OSError,
                subprocess.SubprocessError) as exc:
            report["failed_stage"] = "calibrate"
            report["error"] = "calibration stage failed: %s" % exc
            _record_cosmetic_correction(report, calibration_plan, calibrate_builder,
                                        calibrate_script, "unconfirmed")
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED
        calibrated_dir = os.path.join(work_dir, "%s0000.fit" % calibrated_seq)
        if exit_code != 0 or not os.path.exists(calibrated_dir):
            report["failed_stage"] = "calibrate"
            report["error"] = (
                "siril-cli failed during calibration (exit %d); no calibrated "
                "sequence was produced. Log: %s" % (exit_code, log_path)
            )
            report["notes"].extend(_parse_siril_log(stdout))
            _record_cosmetic_correction(report, calibration_plan, calibrate_builder,
                                        calibrate_script, "unconfirmed")
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED
        report["completed_stages"].append("calibrate")
        _record_cosmetic_correction(report, calibration_plan, calibrate_builder,
                                    calibrate_script, "completed")

    # -- stage: transform probe ---------------------------------------------
    # Probe transforms can also serve the final apply step when pixels stay unchanged.
    probe_builder, probe_stem = build_probe_script(
        work_dir, target_name, calibrated_seq,
        min_pairs=device["profile"].get("min_pairs", 4),
    )
    probe_script_path = os.path.join(work_dir, "astra_probe.sir")
    try:
        probe_builder.write(probe_script_path)
    except ScriptValidationError as exc:
        report["failed_stage"] = "probe-reg"
        report["error"] = "probe script is invalid: %s" % exc
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    if not args.dry_run:
        probe_log = os.path.join(work_dir, "probe.log")
        try:
            probe_run = subprocess.run(
                [siril_path, "-d", work_dir, "-s", probe_script_path],
                capture_output=True, text=True, timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            report["failed_stage"] = "probe-reg"
            report["error"] = ("transform probe timed out after %ss"
                               % args.timeout)
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED
        with open(probe_log, "w", encoding="utf-8") as handle:
            handle.write(probe_run.stdout + "\n" + probe_run.stderr)

        # -2pass writes the registration data into the source sequence's .seq rather
        # than a prefixed copy, so the probe data is read from the input .seq.
        probe_seq = os.path.join(work_dir, "%s.seq" % probe_stem)
        if probe_run.returncode != 0 or not os.path.exists(probe_seq):
            report["failed_stage"] = "probe-reg"
            report["error"] = (
                "transform probe failed (exit %d); no registration data was "
                "produced, so the chain decision would be guesswork. Log: %s"
                % (probe_run.returncode, probe_log)
            )
            report["notes"].extend(_parse_siril_log(probe_run.stdout))
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED

        try:
            probe_regdata = read_seq_regdata(probe_seq)
            probe_frames = probe_regdata.get("frames") or []
            if (not probe_frames or probe_regdata.get("warnings")
                    or probe_regdata.get("reference_image")
                    not in probe_regdata.get("image_order", [])
                    or any(not all(math.isfinite(v) for v in frame["h"])
                           or not math.isfinite(frame["scale"])
                           or frame["scale"] <= 0 for frame in probe_frames)):
                raise FitsError("probe sequence has invalid registration data")
            probe_summary = summarize_transforms(
                probe_regdata,
                report["input"]["width"] or 0,
                report["input"]["height"] or 0,
                mosaic_factor=MOSAIC_SPAN_FACTOR,
            )
            report["completed_stages"].append("probe-reg")
        except FitsError as exc:
            report["failed_stage"] = "probe-reg"
            report["error"] = "transform probe registration is invalid: %s" % exc
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED

    # Re-derive now that real transform data exists, so the chain decision is
    # evidence-based rather than a guess.
    pipeline = derive_pipeline(header, device, calibration_plan, probe_summary,
                               force_chain=args.force_chain)
    report["registration_probe"] = probe_summary
    if probe_summary and args.force_chain is None:
        report["notes"].append(
            "chain '%s' chosen from measured transforms: span %.1f px, rotation "
            "%.2f deg, scale deviation %.3f"
            % (pipeline["chain"], probe_summary.get("span", 0.0),
               probe_summary.get("rotation_max_deg", 0.0),
               probe_summary.get("scale_dev_max", 0.0))
        )

    master_path = os.path.join(work_dir, "%s_stacked_32bit.fits" % target_name)

    # -- PWS gate ------------------------------------------------------------
    # Computed from the probe regdata (the same data the chain decision used),
    # so refusing costs nothing. A refusal exits 5 with the reasons in the
    # report: the user explicitly asked for PWS, silently falling back to the
    # Siril stack would hide the decision instead of documenting it.
    stack_engine = "pws" if args.stack_engine == "pws" else "siril"
    gate_report = None
    engine = None
    probe_seq_path = (os.path.join(work_dir, "%s.seq" % probe_stem)
                      if probe_stem else None)
    if stack_engine == "pws" and args.dry_run:
        report["failed_stage"] = "stack"
        report["error"] = (
            "--stack-engine=pws cannot be combined with --dry-run: the gate "
            "and the engine need real registration data and a real runner"
        )
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED
    if stack_engine == "pws":
        try:
            probe_regdata = (read_seq_regdata(probe_seq_path)
                             if probe_seq_path and os.path.exists(probe_seq_path)
                             else None)
        except FitsError:
            probe_regdata = None
        # The engine consumes the frames *after* the chain's apply step: a CFA
        # input on the debayer chain becomes RGB by then, so the gate keys on
        # the decided chain, not the raw input kind. Only the drizzle chain
        # (which writes per-frame canvases of differing sizes) is structurally
        # out of reach.
        gate_kind = "rgb" if pipeline["chain"] in ("debayer", "rgb") else "cfa"
        gate_report = evaluate_gate(probe_regdata, gate_kind)
        report["stack_engine"] = gate_report
        if not gate_report["eligible"]:
            report["failed_stage"] = "stack"
            report["error"] = (
                "PWS gate refused this run: %s" % "; ".join(gate_report["reasons"])
            )
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED
        engine = resolve_pws_engine(args.pws_engine)
        report["notes"].append(
            "PWS engine runner: %s" % engine["runner"]
        )

    debayer_only = (not args.dry_run and "-debayer" not in initial_calibration
                    and "-debayer" in pipeline["calibrate"]
                    and not calibration_plan.get("flat"))
    include_calibration = (args.dry_run or (
        initial_calibration != pipeline["calibrate"] and not debayer_only
    ))
    reuse_registration = (not args.dry_run and probe_summary is not None
                          and not include_calibration and not debayer_only)
    if reuse_registration:
        report["notes"].append(
            "validated probe registration reused on the unchanged calibrated sequence; "
            "no repeated calibration or registration"
        )
    elif debayer_only:
        report["notes"].append(
            "calibrated CFA demosaiced without reapplying masters; RGB registered "
            "again because debayering resets registration data"
        )
    elif not args.dry_run:
        report["notes"].append(
            "calibration parameters changed after the probe; source recalibrated "
            "and registered for the final chain"
        )
    builder, registered_seq = build_script(
        work_dir, target_name, pipeline, calibration_plan, master_path,
        include_calibration=include_calibration, stack_engine=stack_engine,
        registration_available=reuse_registration, debayer_only=debayer_only,
        rgb_equal=args.rgb_equal,
    )
    try:
        script_text = builder.render()
    except ScriptValidationError as exc:
        report["failed_stage"] = "plan"
        report["error"] = "generated script is invalid: %s" % exc
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    script_path = os.path.join(work_dir, "astra_%s.sir" % target_name)
    with open(script_path, "w", encoding="ascii") as handle:
        handle.write(script_text)
    report["script"] = script_path
    report["script_text"] = script_text
    _record_cosmetic_correction(report, calibration_plan, builder, script_path, "planned")

    # -- stage: execute -----------------------------------------------------
    if args.dry_run:
        report["completed_stages"].append("stack(dry-run)")
        report["registration"] = _registration_report(pipeline, None)
        report["stacking"] = _stacking_report(pipeline, None)
        report["preview"] = dict(
            derive_preview_mtf(None),
            produced=False,
            note="not attempted in --dry-run",
        )
        report["channel_stats"] = _channel_statistics_from_args(args)["report"]
        report["notes"].append(
            "--dry-run: parameters derived and script generated, Siril not invoked. "
            "The chain shown here is the conservative fallback: without probe data "
            "the CFA decision cannot use measured transforms. Use --force-chain to "
            "pin it, or run for real."
        )
        report["status"] = "ok"
        report["timings"]["total_s"] = round(time.time() - started, 3)
        _emit_final(report, out_dir, target_name)
        return EXIT_OK

    log_path = os.path.join(work_dir, "siril.log")
    command = [siril_path, "-d", work_dir, "-s", script_path]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=args.timeout, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.SubprocessError) as exc:
        report["failed_stage"] = "stack"
        report["error"] = ("siril-cli timed out after %ss" % args.timeout
                           if isinstance(exc, subprocess.TimeoutExpired)
                           else "siril-cli invocation failed: %s" % exc)
        _record_cosmetic_correction(report, calibration_plan, builder, script_path, "unconfirmed")
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    if completed.returncode != 0:
        _record_cosmetic_correction(report, calibration_plan, builder, script_path, "unconfirmed")
    else:
        products_ready = (os.path.exists(master_path) if stack_engine == "siril"
                          else os.path.exists(os.path.join(work_dir, "%s0000.fit" % calibrated_seq)))
        _record_cosmetic_correction(report, calibration_plan, builder, script_path,
                                    "completed" if products_ready else "unconfirmed")

    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(completed.stdout + "\n" + completed.stderr)
    early_selection = True
    diagnostics = completed.stdout + "\n" + completed.stderr
    if (stack_engine == "siril" and pipeline["chain"] == "debayer"
            and completed.returncode != 0
            and "Reference image is not included in the filtered list, aborting" in diagnostics
            and 'This is not compatible with framing mode "current"' in diagnostics):
        fallback = _reference_filter_fallback(builder, pipeline)
        fallback_path = os.path.join(work_dir, "astra_%s_reference_fallback.sir" % target_name)
        fallback_text = fallback.write(fallback_path)
        report["notes"].append(
            "current framing reference excluded by native filters; retrying once "
            "with all-frame transforms and original stack filters, without calibration "
            "or registration; first attempt: %s, %s" % (script_path, log_path)
        )
        script_path = fallback_path
        log_path = os.path.join(work_dir, "siril_reference_fallback.log")
        report["script"] = script_path
        report["script_text"] = fallback_text
        try:
            completed = subprocess.run(
                [siril_path, "-d", work_dir, "-s", script_path],
                capture_output=True, text=True, timeout=args.timeout,
                env=dict(os.environ, LC_ALL="C"),
            )
        except subprocess.TimeoutExpired:
            report["failed_stage"] = "stack"
            report["error"] = "reference fallback timed out after %ss" % args.timeout
            _emit_final(report, out_dir, target_name)
            return EXIT_SIRIL_FAILED
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(completed.stdout + "\n" + completed.stderr)
        early_selection = False
    report["siril_exit_code"] = completed.returncode

    if stack_engine == "pws":
        # The Siril front end stopped after seqsubsky; there is no master yet.
        return _run_pws_engine_stage(
            args, report, work_dir, out_dir, target_name, stem,
            registered_seq, completed, gate_report, engine, started,
            master_path, script_path=script_path,
        )

    if completed.returncode != 0 or not os.path.exists(master_path):
        report["failed_stage"] = "stack"
        report["error"] = (
            "siril-cli failed (exit %d); master not produced. Log: %s"
            % (completed.returncode, log_path)
        )
        report["notes"].extend(_parse_siril_log(completed.stdout))
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    report["completed_stages"].append("stack")

    # -- stage: post-analysis ----------------------------------------------
    try:
        source_regdata, regdata, frames_stacked = _read_stack_evidence(
            work_dir, registered_seq, master_path, completed.stdout, early_selection,
        )
    except FitsError as exc:
        report["failed_stage"] = "post-analysis"
        report["error"] = "invalid stacking evidence: %s" % exc
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED

    transform_summary = summarize_transforms(
        regdata, report["input"]["width"] or 0, report["input"]["height"] or 0,
        mosaic_factor=MOSAIC_SPAN_FACTOR,
    )
    source_summary = summarize_transforms(
        source_regdata, report["input"]["width"] or 0, report["input"]["height"] or 0,
        mosaic_factor=MOSAIC_SPAN_FACTOR,
    )
    report["frame_quality"] = _frame_quality_report(source_regdata)
    report["registration"] = _registration_report(pipeline, transform_summary)
    report["stacking"] = _stacking_report(pipeline, source_summary, frames_stacked)
    if early_selection:
        kept = regdata["image_order"]
        excluded = sorted(set(source_regdata["image_order"]) - set(kept))
        report["notes"].append(
            "native frame selection before transforms; %d/%d exported; stack reused "
            "the output sequence without quality filters; kept source file indices: %s; "
            "excluded indices: %s" % (len(kept), len(source_regdata["image_order"]),
                                      kept, excluded)
        )
    report["notes"].extend(_chain_disagreement_notes(
        pipeline, source_summary, header, device, force_chain=args.force_chain,
    ))

    # -- stage: preview -----------------------------------------------------
    preview_path = os.path.join(work_dir, "%s.png" % target_name)
    # Medians come from the stacked master, so the stretch adapts to the target's
    # actual brightness instead of relying on a hardcoded midpoint.
    try:
        channel_statistics = _channel_statistics_from_args(args, master_path)
    except FitsError as exc:
        report["failed_stage"] = "post-analysis"
        report["error"] = str(exc)
        _emit_final(report, out_dir, target_name)
        return EXIT_SIRIL_FAILED
    mtf = derive_preview_mtf(channel_statistics["medians"])
    preview_ok, preview_note = _make_preview(
        siril_path, work_dir, master_path, preview_path, mtf
    )
    report["preview"] = dict(mtf)
    report["preview"]["produced"] = preview_ok
    if preview_note:
        report["notes"].append(preview_note)
    report["channel_stats"] = channel_statistics["report"]
    if preview_ok:
        report["completed_stages"].append("preview")

    report["notes"].extend(_parse_siril_log(completed.stdout))
    report["warnings"].extend(pipeline["chain_info"].get("degradations", []))

    # -- stage: commit ------------------------------------------------------
    report_path = os.path.join(work_dir, "%s_report.json" % target_name)
    write_report(report_path, dict(report, status="ok"))
    artifacts = {"master": master_path, "preview": preview_path,
                 "report": report_path}
    artifacts = {role: path for role, path in artifacts.items()
                 if os.path.exists(path)}
    try:
        published = atomic_publish(work_dir, out_dir, target_name, artifacts)
    except RuntimeError as exc:
        report["failed_stage"] = "commit"
        report["error"] = str(exc)
        _emit_final(report, out_dir, target_name)
        return EXIT_COMMIT_FAILED

    report["artifacts"] = published
    report["status"] = "ok"
    report["completed_stages"].append("commit")
    report["timings"]["total_s"] = round(time.time() - started, 3)
    if args.keep_work:
        # Keep the scratch directory for inspection. Registration data in the .seq
        # files is the only way to check whether frame filtering actually removed
        # anything, since the report only records the final frame count.
        report["work_dir"] = work_dir
        report["notes"].append(
            "--keep-work: scratch directory retained at %s" % work_dir
        )
    else:
        shutil.rmtree(work_dir, ignore_errors=True)
    _emit_final(report, out_dir, target_name)
    return EXIT_OK


def _record_cosmetic_correction(report, calibration_plan, builder, script_path, status):
    """Describe each calibration attempt without treating policy as execution."""
    calibrations = [args for _comment, command, args in builder.commands
                    if command == "calibrate"]
    if not calibrations:
        return
    requested = any(arg.split()[0] == "-cc=dark"
                    for args in calibrations for arg in args)
    if not requested:
        if status != "planned":
            return
        if not (calibration_plan.get("cc") or {}).get("enabled"):
            detail = "disabled by calibration policy; not requested in this script"
        elif not calibration_plan.get("dark"):
            detail = ("not applicable: no master dark; Siril dark-based cosmetic "
                      "correction was not requested; device-side correction is not verified")
        else:
            detail = "not requested in this calibration command; no -cc=dark parameter"
    else:
        detail = {
            "planned": "planned with -cc=dark; not yet executed",
            "completed": ("calibration command with -cc=dark completed successfully; "
                          "cosmetic correction effectiveness was not measured"),
            "unconfirmed": "failed or completion unconfirmed; partial processing may have occurred",
        }[status]
    report["notes"].append(
        "dark-based cosmetic correction [%s / calibrate %s]: %s"
        % (os.path.basename(script_path), ", ".join(args[0] for args in calibrations), detail)
    )


def _run_siril(siril_path, work_dir, script_path, timeout):
    """Execute a generated script and capture its log.

    Args:
        siril_path: Path to siril-cli.
        work_dir: Passed as ``-d`` so Siril resolves sequences in this directory.
        script_path: Absolute path to the ``.sir`` file.
        timeout: Seconds before the run is abandoned.

    Returns:
        ``(exit_code, stdout, log_path)``. The log is always written, including on
        failure, so a stage can be diagnosed after the fact.
    """
    completed = subprocess.run(
        [siril_path, "-d", work_dir, "-s", script_path],
        capture_output=True, text=True, timeout=timeout,
    )
    log_path = os.path.join(
        work_dir, "%s.log" % os.path.basename(script_path).replace(".sir", "")
    )
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(completed.stdout + "\n" + completed.stderr)
    return completed.returncode, completed.stdout, log_path


def _channel_statistics_from_args(args, master_path=None):
    """Separate measured master statistics from optional preview-only input."""
    notes = [
        "medians_after are whole-image planar RGB samples after stacking, including "
        "padding and object emission; they are not a sky-only measurement",
        "medians_before were not measured; no uniform per-channel gains were applied",
    ]
    measured = None
    rgb_master = False
    if master_path and os.path.exists(master_path) and not args.dry_run:
        try:
            header, _warnings = read_fits_header_with_warnings(master_path)
            rgb_master = header.get("NAXIS") == 3 and header.get("NAXIS3") == 3
            measured = estimate_channel_medians(master_path) or None
            if not rgb_master:
                notes.append("non-RGB master: RGB channel statistics are not applicable")
            elif measured is None or len(measured) != 3:
                raise FitsError("RGB master has no finite sampled values in one or more channels")
        except (FitsError, OSError) as exc:
            if rgb_master:
                raise FitsError("could not validate RGB master statistics: %s" % exc) from exc
            notes.append("could not sample the master for channel medians: %s" % exc)
    preview_medians = measured
    if not preview_medians and args.channel_medians:
        try:
            with open(args.channel_medians, "r", encoding="utf-8") as handle:
                supplied = json.load(handle)
            if not isinstance(supplied, dict):
                raise ValueError("expected an RGB median object")
            preview_medians = {key: supplied[key] for key in ("r", "g", "b")
                               if type(supplied.get(key)) in (int, float)
                               and math.isfinite(supplied[key])}
            if not preview_medians:
                raise ValueError("no finite RGB medians")
            notes.append("preview-only medians read from %s; not measured master data"
                         % args.channel_medians)
        except (OSError, ValueError) as exc:
            notes.append("could not read preview medians: %s" % exc)
    requested = args.rgb_equal
    applied = bool(requested and master_path and rgb_master and not args.dry_run)
    if requested:
        notes.append(
            "native stack -rgb_equal requested: per-frame/channel background "
            "normalization, not PCC/SPCC or a single RGB gain"
        )
    if args.dry_run:
        notes.append("not executed in --dry-run")
    if not preview_medians:
        notes.append("no usable preview medians; using the default stretch")
    return {"medians": preview_medians, "report": {
        "medians_before": None,
        "medians_after": measured,
        "gains_applied": None,
        "background_equalization": {
            "method": "siril_stack_rgb_equal" if requested else None,
            "requested": requested,
            "applied": applied,
        },
        "notes": notes,
    }}


def _percentile(sorted_values, fraction):
    """Return the value at ``fraction`` of a pre-sorted list."""
    if not sorted_values:
        return None
    index = min(int(len(sorted_values) * fraction), len(sorted_values) - 1)
    return sorted_values[index]


def _frame_quality_report(regdata):
    """Summarise per-frame quality so filter tuning can be judged.

    Percentiles are reported alongside the extremes because the mean and standard
    deviation are the wrong summary for this distribution: the outlier frames that
    frame selection exists to remove are exactly what inflates them.

    Args:
        regdata: Output of :func:`fits_probe.read_seq_regdata`, or ``None``.

    Returns:
        Dict with ``frame_count``, per-metric percentile tables and
        ``weighted_fwhm_outliers`` (frames beyond 1.5x the median).
    """
    if not regdata or not regdata.get("frames"):
        return {"frame_count": 0, "note": "no registration data available"}

    frames = regdata["frames"]
    metrics = {
        "fwhm": ("fwhm", "lower is better"),
        "weighted_fwhm": ("weighted_fwhm", "lower is better"),
        "roundness": ("roundness", "higher is better"),
        "number_of_stars": ("number_of_stars", "higher is better"),
        "background_lvl": ("background_lvl", "lower is better"),
    }

    report = {"frame_count": len(frames), "metrics": {}}
    for label, (key, sense) in metrics.items():
        values = sorted(f[key] for f in frames if f.get(key) is not None)
        if not values:
            continue
        middle = _percentile(values, 0.5)
        report["metrics"][label] = {
            "sense": sense,
            "min": values[0],
            "p10": _percentile(values, 0.10),
            "p50": middle,
            "p90": _percentile(values, 0.90),
            "p99": _percentile(values, 0.99),
            "max": values[-1],
        }
        if middle:
            outliers = [v for v in values if v > middle * 1.5]
            if outliers:
                report["metrics"][label]["beyond_1p5x_median"] = len(outliers)

    # A k-sigma filter is only safe when this is small; a large value means the
    # sigma-based threshold has been inflated by the very frames it should reject.
    weighted = [f["weighted_fwhm"] for f in frames if f.get("weighted_fwhm")]
    if len(weighted) > 1:
        import statistics

        median = statistics.median(weighted)
        sigma = statistics.stdev(weighted)
        report["weighted_fwhm_sigma_ratio"] = round(sigma / median, 4) if median else None
        if sigma and median and sigma / median > 0.15:
            report["note"] = (
                "weighted_fwhm sigma is %.0f%% of the median, so k-sigma filters "
                "would be inflated by outliers; percentile-based filters are used"
                % (100.0 * sigma / median)
            )

        # Viability estimate for quality-dispersion-sensitive engines (PWS).
        # Peak PWS gains track the C_i span (paper tables 3-4); a p90/p10 at or
        # below 1.4 measured gains inside the noise (M81: 1.35x -> 3.7 %).
        p10 = report["metrics"]["weighted_fwhm"]["p10"]
        p90 = report["metrics"]["weighted_fwhm"]["p90"]
        report["weighted_fwhm_p90_over_p10"] = round(p90 / p10, 4) if p10 else None
        if p10 and p90 / p10 <= 1.4:
            report["dispersion_note"] = (
                "weighted_fwhm p90/p10 = %.3f: the frames are too uniform for "
                "dispersion-driven weighting (PWS) to pay off; per-frame "
                "quality weights have little to act on" % (p90 / p10)
            )
    return report


def _make_preview(siril_path, work_dir, master_path, preview_path, mtf):
    """Render the preview PNG with an explicit MTF.

    ``savepng`` appends ``.png`` itself -- given ``out.png`` it writes
    ``out.png.png`` -- so the path handed to Siril has no extension, while the
    existence check uses the extension-appended name.

    Returns:
        ``(produced, note)``.
    """
    stem = os.path.splitext(preview_path)[0]
    script = ScriptBuilder(header_comment=[
        "preview: explicit MTF so the output is reproducible",
        "autostretch is avoided because it cannot report the parameters it used",
    ])
    script.add("load", [master_path])
    script.add("mtf", [str(mtf["low"]), str(mtf["mid"]), str(mtf["high"])])
    script.add("savepng", [stem])
    script_path = os.path.join(work_dir, "astra_preview.sir")
    try:
        script.write(script_path)
    except ScriptValidationError as exc:
        return False, "preview script invalid: %s" % exc

    try:
        completed = subprocess.run(
            [siril_path, "-d", work_dir, "-s", script_path],
            capture_output=True, text=True, timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "preview invocation failed: %s" % exc
    if completed.returncode != 0 or not os.path.exists(stem + ".png"):
        return False, ("preview not produced (exit %d); the 32-bit master is "
                       "unaffected" % completed.returncode)
    if stem + ".png" != preview_path:
        os.replace(stem + ".png", preview_path)
    return True, None


def _registration_report(pipeline, transform_summary):
    info = pipeline["chain_info"]
    report = {
        "chain": pipeline["chain"],
        "transform_model": "homography",
        "reasons": info["reasons"],
        "mosaic": info["mosaic"],
        "rotation_max_deg": info["rotation_max_deg"],
        "scale_dev_max": info["scale_dev_max"],
        "span_px": info["span_px"],
        "degradations": info.get("degradations", []),
        "apply_args": list(pipeline["apply"]["args"]),
    }
    if pipeline["chain"] == "drizzle":
        report["flat_used"] = "none"
        report["flat_note"] = (
            "drizzle ran without -flat= because this device produces no flat "
            "frames; residual vignetting weighting is absent"
        )
    if transform_summary:
        report["observed_after_run"] = {
            key: transform_summary[key]
            for key in ("span_x", "span_y", "span", "rotation_max_deg",
                        "scale_dev_max", "median_scale", "frame_count")
        }
    return report


def _stacking_report(pipeline, transform_summary, filtered_in=None):
    """Build the stacking section of the report.

    Args:
        pipeline: Output of :func:`param_derive.derive_pipeline`.
        transform_summary: Output of :func:`fits_probe.summarize_transforms`, or
            ``None``.
        filtered_in: Actual frame count validated against STACKCNT and the
            definitive stacking completion log. This is the number that actually
            went into the stack, and it
            is **not** the same as the regdata row count -- registration runs on
            every frame before filtering happens.
    """
    stacking = pipeline["stack"]
    report = {
        "method": stacking["method"],
        "rejection": stacking["rejection"],
        "sigma_low": stacking["sigma_low"],
        "sigma_high": stacking["sigma_high"],
        "norm": stacking["norm"],
        "weight": stacking["weight"],
        "maximize": stacking["maximize"],
        "rejmap": stacking["rejmap"],
        "filters": stacking["filters"],
        "notes": stacking["notes"],
    }
    if transform_summary:
        report["frames_registered"] = transform_summary.get("frame_count")
    if filtered_in is not None:
        report["frames_stacked"] = filtered_in
        if transform_summary:
            registered = transform_summary.get("frame_count") or 0
            report["frames_discarded"] = max(0, registered - filtered_in)
            if registered:
                report["frames_stacked_pct"] = round(
                    100.0 * filtered_in / registered, 1
                )
    return report


def _chain_disagreement_notes(pipeline, source_summary, header, device,
                              force_chain=None):
    """Review automatic selection against the final pre-transform registration."""
    if not source_summary or force_chain is not None:
        return []
    recommendation = decide_chain(classify_input(header), source_summary, device)
    if recommendation["chain"] == "rgb" or pipeline["chain"] == recommendation["chain"]:
        return []
    return [
        "automatic chain '%s' differs from '%s' recommended by the final "
        "pre-transform registration: %s"
        % (pipeline["chain"], recommendation["chain"],
           "; ".join(recommendation["reasons"]))
    ]


def _parse_stacked_frames(stdout):
    """Read the definitive completion count, never an individual filter's count."""
    counts = [int(match.group(1)) for match in re.finditer(
        r"\b(\d+) images have been stacked\.", stdout or ""
    )]
    return counts[-1] if counts else None


def _read_stack_evidence(work_dir, registered_seq, master_path, stdout,
                         early_selection=True):
    """Validate native membership and actual stack counts before publishing."""
    if not registered_seq.startswith("ap_"):
        raise FitsError("unexpected applied sequence name: %s" % registered_seq)
    source = read_seq_regdata(os.path.join(work_dir, registered_seq[3:] + ".seq"))
    applied = read_seq_regdata(os.path.join(work_dir, registered_seq + ".seq"))
    for label, data in (("source", source), ("applied", applied)):
        indices = data["image_order"]
        reference = data["reference_image"]
        if (data["warnings"] or not indices or len(set(indices)) != len(indices)
                or len(data["frames"]) != len(indices)
                or not isinstance(reference, int) or not 0 <= reference < len(indices)
                or any(not all(math.isfinite(v) for v in frame["h"])
                       or not math.isfinite(frame["scale"]) or frame["scale"] <= 0
                       for frame in data["frames"])):
            raise FitsError("invalid %s registration sequence" % label)
    if not set(applied["image_order"]).issubset(source["image_order"]):
        raise FitsError("applied sequence contains unknown source file indices")
    for index in applied["image_order"]:
        path = os.path.join(work_dir, "%s%04d.fit" % (registered_seq, index))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise FitsError("applied frame missing or empty: %s" % path)
    header, _warnings = read_fits_header_with_warnings(master_path)
    count = header.get("STACKCNT")
    logged = _parse_stacked_frames(stdout)
    if count is not None and (type(count) is not int or count < 2):
        raise FitsError("invalid STACKCNT: %s" % count)
    if count is not None and logged is not None and count != logged:
        raise FitsError("STACKCNT %d disagrees with completion log %d" % (count, logged))
    count = count if count is not None else logged
    if count is None or not 2 <= count <= len(applied["image_order"]):
        raise FitsError("missing or out-of-range actual stacking count")
    if early_selection and count != len(applied["image_order"]):
        raise FitsError("stack count differs from frozen applied membership")
    return source, applied, count


def _parse_siril_log(stdout):
    """Extract warnings worth surfacing from Siril's own output."""
    markers = (
        "Disabling",
        "Cannot",
        "not enough",
        "No image was registered",
        "partially succeeded",
        "Reference image is not included",
        "bad idea",
    )
    notes = []
    for line in stdout.splitlines():
        for marker in markers:
            if marker in line:
                notes.append("siril: %s" % line.strip().lstrip("log: ").strip())
                break
    return notes[:12]


def _emit_final(report, out_dir, target_name):
    """Write the report into the output directory even when a stage failed."""
    report.setdefault("timings", {})
    path = os.path.join(out_dir, "%s_report.json" % target_name)
    try:
        write_report(path, report)
        report["report_path"] = path
    except OSError:
        pass


def build_parser():
    parser = argparse.ArgumentParser(
        description="Stack astronomical light frames with Siril, adapting "
                    "parameters to the capture device.",
    )
    parser.add_argument("input", help="directory of FITS light frames")
    parser.add_argument("--out", default="./astra-out",
                        help="output directory (default: ./astra-out)")
    parser.add_argument("--target", default=None,
                        help="output base name (default: input directory name)")
    parser.add_argument("--siril", default=None,
                        help="explicit path to siril-cli")
    parser.add_argument("--calibration-dir", default=None,
                        help="directory containing master bias/dark/flat")
    parser.add_argument("--device-profile", default=None,
                        help="JSON file of device profile overrides")
    parser.add_argument("--channel-medians", default=None,
                        help="JSON file with per-channel medians for the preview")
    parser.add_argument("--rgb-equal", action="store_true",
                        help="explicitly equalize RGB backgrounds in Siril stacking "
                             "(default: off; not PCC/SPCC photometric calibration)")
    parser.add_argument("--force-chain", choices=["drizzle", "debayer"],
                        default=None,
                        help="override the automatic CFA chain decision")
    parser.add_argument("--stack-engine", choices=list(STACK_ENGINES),
                        default="siril",
                        help="stacking engine: 'siril' (default) uses the "
                             "wfwhm-weighted rejection stack; 'pws' hands the "
                             "registered frames to the Partition-Weighted "
                             "Stacking engine (sibling pws-stacking skill) "
                             "behind a measured viability gate")
    parser.add_argument("--pws-engine", default=None,
                        help="path to the pws-stacking skill's scripts "
                             "directory containing pws_run.py (default: "
                             "sibling directory of this skill)")
    parser.add_argument("--dry-run", action="store_true",
                        help="derive parameters and print the script, but do not "
                             "invoke Siril")
    parser.add_argument("--resume", action="store_true",
                        help="reuse an existing work directory where possible")
    parser.add_argument("--keep-work", action="store_true",
                        help="keep the scratch directory even on success, so the "
                             ".seq registration data can be inspected")
    parser.add_argument("--timeout", type=int, default=3600,
                        help="Siril timeout in seconds (default: 3600)")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.rgb_equal and args.stack_engine != "siril":
        parser.error("--rgb-equal requires --stack-engine=siril; PWS produces grayscale")
    try:
        return run_pipeline(args)
    except KeyboardInterrupt:
        print("interrupted; the work directory was kept for inspection",
              file=sys.stderr)
        return EXIT_SIRIL_FAILED
    except Exception as exc:  # noqa: BLE001 - report, never exit 0 on a crash
        # An uncaught exception must not be reported as success. The report is
        # written best-effort so the failure is still machine-readable.
        traceback_text = traceback.format_exc()
        try:
            out_dir = os.path.abspath(args.out)
            os.makedirs(out_dir, exist_ok=True)
            target = args.target or os.path.basename(
                os.path.abspath(args.input.rstrip("/"))
            )
            path = os.path.join(out_dir, "%s_report.json" % target)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "status": "failed",
                        "failed_stage": "internal",
                        "error": "%s: %s" % (type(exc).__name__, exc),
                        "traceback": traceback_text.splitlines()[-12:],
                        "target": target,
                        "input_dir": os.path.abspath(args.input),
                    },
                    handle,
                    indent=2,
                )
                handle.write("\n")
        except OSError:
            pass
        traceback.print_exc()
        return EXIT_SIRIL_FAILED


if __name__ == "__main__":
    raise SystemExit(main())