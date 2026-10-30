"""Stack monochrome FILTER buckets, then prepare a composition bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from astra_stack import find_siril, siril_version, write_report
from compose_channels import ALIASES
from device_signatures import identify_device, resolve_calibration_plan
from fits_probe import FitsError, read_fits_header_with_warnings
from prepare_composition import inspect_pixels, sha256_file, validate_metadata

SCRIPTS = Path(__file__).resolve().parent
CALIBRATION_KINDS = ("bias", "dark", "flat")
STATES = {"pending", "running", "ok", "failed"}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def source_record(path):
    path = Path(path).resolve(strict=True)
    return {"path": str(path), "sha256": sha256_file(path)}


def verify_sources(records):
    unique = {}
    for record in records:
        if record["path"] in unique and unique[record["path"]]["sha256"] != record["sha256"]:
            raise FitsError("conflicting input fingerprints: %s" % record["path"])
        unique[record["path"]] = record
    for record in unique.values():
        if sha256_file(record["path"]) != record["sha256"]:
            raise FitsError("input or calibration changed: %s" % record["path"])


def verify_inputs(config, buckets):
    records = [source for bucket in buckets.values() for source in bucket["sources"]]
    records += [master for bucket in buckets.values() for master in bucket["calibration"].values() if master]
    if config["device_profile"]:
        records.append(config["device_profile"])
    verify_sources(records)


def preflight(args):
    directory = Path(args.input).resolve(strict=True)
    frames = sorted(path for path in directory.iterdir()
                    if path.is_file() and path.suffix.lower() in (".fit", ".fits", ".fts"))
    if not frames:
        raise FitsError("no FITS light frames in input directory")
    profile = source_record(args.device_profile) if args.device_profile else None
    buckets, identities, seen_paths, master_records = {}, {}, set(), {}
    common = None
    for path in frames:
        record = source_record(path)
        if record["path"] in seen_paths:
            raise FitsError("the same light frame appears more than once: %s" % path)
        seen_paths.add(record["path"])
        header, warnings = read_fits_header_with_warnings(str(path))
        device = identify_device(header, overrides=args.device_profile)
        if header.get("NAXIS") != 2 or not device["profile"].get("mono_sensor_capable"):
            raise FitsError("batch processing requires a confirmed monochrome sensor")
        label = str(header.get("FILTER") or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_-]*", label):
            raise FitsError("missing or unsafe FILTER: %s" % path)
        identity = ALIASES.get(label, label)
        if identity in identities and identities[identity] != label:
            raise FitsError("duplicate channel aliases: %s and %s" % (identities[identity], label))
        identities[identity] = label
        target = header.get("OBJECT")
        if not isinstance(target, str) or not target.strip():
            raise FitsError("missing OBJECT: %s" % path)
        if any(ord(char) < 32 or ord(char) > 126 or char == '"' for char in target):
            raise FitsError("unsupported characters in OBJECT: %s" % path)
        if any(type(header.get(key)) is not int or header[key] < 1
               for key in ("NAXIS1", "NAXIS2")):
            raise FitsError("invalid image dimensions: %s" % path)
        for key in ("EXPTIME", "GAIN", "OFFSET", "CCD-TEMP", "XBINNING", "YBINNING"):
            value = header.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise FitsError("invalid %s: %s" % (key, path))
        if header.get("EXPTIME") is None or header["EXPTIME"] <= 0:
            raise FitsError("EXPTIME must be finite and positive: %s" % path)
        if any(header.get(key) is not None and header[key] <= 0
               for key in ("XBINNING", "YBINNING")):
            raise FitsError("invalid binning: %s" % path)
        global_settings = (target.strip(), device["id"], device["profile"],
                           str(header.get("INSTRUME") or "").strip().upper(),
                           str(header.get("TELESCOP") or "").strip().upper(),
                           *(header.get(key) for key in ("NAXIS1", "NAXIS2", "XBINNING", "YBINNING")))
        if common is not None and global_settings != common:
            raise FitsError("frames have different OBJECT, device, dimensions or binning")
        common = global_settings
        settings = {key: header.get(key) for key in
                    ("EXPTIME", "GAIN", "OFFSET", "BITPIX", "BZERO", "BSCALE", "BAYERPAT", "CCD-TEMP")}
        # Temperature may drift if every frame still selects the same masters.
        settings["CCD-TEMP"] = header.get("CCD-TEMP") is not None
        plan = resolve_calibration_plan(header, device, args.calibration_dir,
                                        allow_missing_dark=args.allow_missing_dark, strict=True)
        masters = {}
        for kind in CALIBRATION_KINDS:
            master_path = str(Path(plan[kind]).resolve(strict=True)) if plan[kind] else None
            if master_path and master_path not in master_records:
                master_records[master_path] = source_record(master_path)
            masters[kind] = master_records[master_path] if master_path else None
        bucket = buckets.setdefault(label, {
            "filter": label, "header": header, "settings": settings, "device": device,
            "sources": [], "calibration": masters, "calibration_plan": plan, "warnings": [],
        })
        if settings != bucket["settings"] or masters != bucket["calibration"]:
            raise FitsError("%s: mixed capture settings or calibration plans; split the inputs" % label)
        bucket["sources"].append(dict(record, filter=header["FILTER"]))
        bucket["warnings"] = list(dict.fromkeys(bucket["warnings"] + warnings + plan["warnings"]))
    if not 2 <= len(buckets) <= 8 or any(len(bucket["sources"]) < 2 for bucket in buckets.values()):
        raise FitsError("expected 2 to 8 FILTER buckets, each with at least two frames")
    siril = find_siril(args.siril)
    version = siril_version(siril) if siril else ""
    if not siril or not re.search(r"\b1\.4\.", version or ""):
        raise FitsError("Siril CLI 1.4.x is required")
    config = {
        "implementation_version": "1.9.0", "input": str(directory),
        "siril": str(Path(siril).resolve()), "siril_version": version,
        "device_profile": profile, "monochrome": True,
        "calibration_dir": str(Path(args.calibration_dir).resolve()) if args.calibration_dir else None,
        "allow_missing_dark": args.allow_missing_dark, "timeout": args.timeout,
    }
    buckets = dict(sorted(buckets.items()))
    digest = fingerprint({"config": config, "buckets": buckets})
    verify_inputs(config, buckets)
    return config, buckets, digest


def local_path(root, relative):
    path = root / relative
    if (Path(relative).is_absolute() or ".." in Path(relative).parts
            or path.resolve() != path or not path.is_relative_to(root)):
        raise FitsError("unsafe batch artifact path: %s" % relative)
    return path


def artifact_record(root, path):
    relative = str(path.relative_to(root))
    path = local_path(root, relative)
    if not path.is_file() or not path.stat().st_size:
        raise FitsError("missing or empty artifact: %s" % path)
    return {"path": relative, "sha256": sha256_file(path)}


def tree_records(root, directory):
    records = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise FitsError("unexpected symlink in published bundle: %s" % path)
        if path.is_file():
            records.append(artifact_record(root, path))
    if not records:
        raise FitsError("missing composition bundle")
    return records


def stack_paths(root, label):
    directory = root / "stacks" / label
    return {"master": directory / (label + "_stacked_32bit.fits"),
            "preview": directory / (label + ".png"),
            "report": directory / (label + "_report.json")}


def validate_stack(root, label, bucket):
    paths = stack_paths(root, label)
    artifacts = {role: artifact_record(root, path) for role, path in paths.items()}
    report = json.loads(paths["report"].read_text())
    if report.get("status") != "ok" or "commit" not in report.get("completed_stages", []):
        raise FitsError("%s: stack did not commit successfully" % label)
    if report.get("artifacts") != {role: str(path) for role, path in paths.items()}:
        raise FitsError("%s: stack artifact paths disagree" % label)
    header, _, _ = inspect_pixels(paths["master"])
    count = header.get("STACKCNT")
    if (str(header.get("FILTER") or "").strip().upper() != label
            or str(header.get("OBJECT") or "").strip() != bucket["header"]["OBJECT"].strip()
            or type(count) is not int or not 1 <= count <= len(bucket["sources"])
            or count != report.get("stacking", {}).get("frames_stacked")
            or header.get("EXPTIME") != bucket["header"]["EXPTIME"]
            or type(header.get("LIVETIME")) not in (int, float)
            or not math.isfinite(header["LIVETIME"])
            or not math.isclose(header["LIVETIME"], count * header["EXPTIME"], rel_tol=1e-9)):
        raise FitsError("%s: stack metadata disagrees with batch input" % label)
    for kind, master in bucket["calibration"].items():
        actual = report.get("calibration", {}).get(kind)
        if (str(Path(actual).resolve()) if actual else None) != (master["path"] if master else None):
            raise FitsError("%s: selected %s changed in child process" % (label, kind))
    return artifacts


def validate_bundle(root, buckets):
    directory = root / "composition_inputs"
    manifest = json.loads((directory / "diagnostics/composition_manifest.json").read_text())
    if (manifest.get("schema_version") != "1.0" or manifest.get("status") != "ok"
            or manifest.get("monochrome_confirmed") is not True
            or sorted(layer["filter"] for layer in manifest["layers"]) != sorted(buckets)):
        raise FitsError("invalid prepared bundle manifest")
    for layer in manifest["layers"]:
        label = layer["filter"]
        path = directory / (label + ".fit")
        header, _, _ = inspect_pixels(path)
        validate_metadata(layer, header)
        if (layer.get("format_ready") is not True
                or layer["source_sha256"] != sha256_file(stack_paths(root, label)["master"])
                or [header["NAXIS1"], header["NAXIS2"]] != manifest["output_size"]):
            raise FitsError("prepared bundle disagrees with successful stacks")
    return tree_records(root, directory)


def link_source(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() and destination.resolve() == Path(source):
        return
    if destination.exists() or destination.is_symlink():
        raise FitsError("unexpected staged input: %s" % destination)
    destination.symlink_to(source)


def archive_attempt(root, name, paths):
    existing = [path for path in paths if path.exists() or path.is_symlink()]
    if not existing:
        return
    parent = root / "diagnostics/attempts"
    parent.mkdir(parents=True, exist_ok=True)
    archive = Path(tempfile.mkdtemp(prefix=name + "-", dir=str(parent)))
    for path in existing:
        local_path(root, str(path.relative_to(root))).rename(archive / path.name)


def run_child(command, log):
    with log.open("w", encoding="utf-8") as output:
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                              env=dict(os.environ, LC_ALL="C", PYTHONDONTWRITEBYTECODE="1")).returncode


def batch(args):
    if not args.monochrome or args.timeout <= 0:
        raise ValueError("--monochrome and a positive --timeout are required")
    out = Path(args.out).absolute()
    if out.is_symlink() or (out.exists() and not args.resume):
        raise FitsError("output already exists; use --resume only for an unchanged batch")
    out = out.resolve()
    report_path = out / "batch_report.json"
    previous = None
    if args.resume:
        previous = json.loads(report_path.read_text())
        if (previous.get("schema_version") != "1.0" or previous.get("kind") != "batch_filters"
                or previous.get("status") not in STATES
                or previous.get("output_dir") != str(out)):
            raise FitsError("--resume requires this directory's batch report")
    config, buckets, digest = preflight(args)
    if previous is not None:
        if previous.get("fingerprint") != digest or set(previous.get("buckets", {})) != set(buckets):
            raise FitsError("batch inputs, calibration or parameters changed; use a new output directory")
        for label, state in previous["buckets"].items():
            if state.get("status") not in STATES:
                raise FitsError("invalid batch checkpoint")
            if state["status"] == "ok" and validate_stack(out, label, buckets[label]) != state.get("artifacts"):
                raise FitsError("successful stack artifacts changed; use a new output directory")
        preparation = previous.get("preparation", {})
        if preparation.get("status") not in STATES:
            raise FitsError("invalid preparation checkpoint")
        if preparation["status"] == "ok":
            if (any(state["status"] != "ok" for state in previous["buckets"].values())
                    or validate_bundle(out, buckets) != preparation.get("artifacts")):
                raise FitsError("successful preparation artifacts changed; use a new output directory")
        if previous.get("status") == "ok":
            if preparation["status"] != "ok":
                raise FitsError("incomplete successful batch checkpoint")
            print("[batch] OK: unchanged batch already complete; report=%s" % report_path, file=sys.stderr)
            return previous
    else:
        out.mkdir(parents=True, exist_ok=False)
    report = previous or {
        "schema_version": "1.0", "kind": "batch_filters", "output_dir": str(out),
        "fingerprint": digest, "config": config, "inputs": buckets,
        "buckets": {label: {"status": "pending", "reused": False, "exit_code": None,
                            "log": "diagnostics/%s.log" % label, "artifacts": {}} for label in buckets},
        "preparation": {"status": "pending", "reused": False, "exit_code": None,
                        "log": "diagnostics/prepare.log", "artifacts": []},
    }
    report.update(status="running", failed_stage="stage_inputs", error=None)
    write_report(str(report_path), report)
    try:
        diagnostics = local_path(out, "diagnostics")
        diagnostics.mkdir(exist_ok=True)
        for label, bucket in buckets.items():
            state = report["buckets"][label]
            state["reused"] = state["status"] == "ok"
            if state["reused"]:
                print("[batch] reuse: %s" % label, file=sys.stderr, flush=True)
                continue
            report["failed_stage"] = "stack:" + label
            input_dir = local_path(out, "diagnostics/inputs/" + label)
            master_dir = local_path(out, "diagnostics/calibration/" + label)
            for index, source in enumerate(bucket["sources"]):
                link_source(source["path"], input_dir / ("%06d.fit" % index))
            master_dir.mkdir(parents=True, exist_ok=True)
            for kind, master in bucket["calibration"].items():
                if master:
                    link_source(master["path"], master_dir / ("master_%s.fit" % kind))
            paths = stack_paths(out, label)
            log = diagnostics / (label + ".log")
            archive_attempt(out, label, [paths["master"].parent, log])
            state.update(status="running", exit_code=None, artifacts={})
            write_report(str(report_path), report)
            verify_inputs(config, {label: bucket})
            command = [sys.executable, "-B", str(SCRIPTS / "astra_stack.py"), str(input_dir),
                       "--out", str(paths["master"].parent), "--target", label,
                       "--calibration-dir", str(master_dir), "--siril", config["siril"],
                       "--timeout", str(args.timeout)]
            if args.device_profile:
                command += ["--device-profile", str(Path(args.device_profile).resolve())]
            if args.allow_missing_dark:
                command.append("--allow-missing-dark")
            print("[batch] stack: %s (%d frames)" % (label, len(bucket["sources"])), file=sys.stderr, flush=True)
            state["exit_code"] = run_child(command, log)
            if state["exit_code"]:
                raise FitsError("%s failed (exit %s); see %s" % (label, state["exit_code"], log))
            verify_inputs(config, {label: bucket})
            state["artifacts"] = validate_stack(out, label, bucket)
            state["status"] = "ok"
            write_report(str(report_path), report)
        report["failed_stage"] = "prepare"
        preparation = report["preparation"]
        verify_inputs(config, buckets)
        for label, bucket in buckets.items():
            if validate_stack(out, label, bucket) != report["buckets"][label]["artifacts"]:
                raise FitsError("successful stack artifacts changed before preparation")
        if preparation["status"] != "ok":
            log = diagnostics / "prepare.log"
            archive_attempt(out, "prepare", [out / "composition_inputs", log])
            preparation.update(status="running", reused=False, exit_code=None, artifacts=[])
            write_report(str(report_path), report)
            command = [sys.executable, "-B", str(SCRIPTS / "prepare_composition.py"), str(out / "stacks"),
                       "--out", str(out / "composition_inputs"), "--monochrome", "--bundle",
                       "--siril", config["siril"], "--timeout", str(args.timeout)]
            print("[batch] prepare: all filters", file=sys.stderr, flush=True)
            preparation["exit_code"] = run_child(command, log)
            if preparation["exit_code"]:
                raise FitsError("composition preparation failed; see %s" % log)
            preparation["artifacts"] = validate_bundle(out, buckets)
            preparation["status"] = "ok"
        else:
            preparation["reused"] = True
        report["failed_stage"] = "verify"
        verify_inputs(config, buckets)
        report.update(status="ok", failed_stage=None)
        write_report(str(report_path), report)
        print("[batch] OK: report=%s" % report_path, file=sys.stderr)
        return report
    except BaseException as exc:
        if (report["failed_stage"] in ("prepare", "verify")
                and report["preparation"]["status"] in ("running", "ok")):
            archive_attempt(out, "prepare", [out / "composition_inputs", out / "diagnostics/prepare.log"])
            report["preparation"].update(status="failed", artifacts=[])
        for state in list(report["buckets"].values()) + [report["preparation"]]:
            if state["status"] == "running":
                state["status"] = "failed"
        report.update(status="failed", error="%s: %s" % (type(exc).__name__, exc))
        write_report(str(report_path), report)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="flat directory containing monochrome FITS lights")
    parser.add_argument("--out", required=True, help="batch output directory")
    parser.add_argument("--monochrome", action="store_true", required=True, help="confirm a monochrome sensor")
    parser.add_argument("--calibration-dir", help="shared calibration master directory")
    parser.add_argument("--device-profile", help="device profile overrides JSON")
    parser.add_argument("--siril", help="explicit Siril CLI executable")
    parser.add_argument("--timeout", type=int, default=3600, help="timeout per Siril invocation")
    parser.add_argument("--allow-missing-dark", action="store_true", help="explicitly omit a missing master dark")
    parser.add_argument("--resume", action="store_true", help="reuse verified successful buckets from this batch")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        batch(args)
    except (Exception, KeyboardInterrupt) as exc:
        print("[batch] FAILED: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
