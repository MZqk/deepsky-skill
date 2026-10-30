#!/usr/bin/env python3
"""Deterministic safety and execution primitives for standalone contract 1.

The module never chooses an image-processing workflow and never edits pixels.
Agents write Siril scripts from the reference protocols; this module validates,
executes, fingerprints, and publishes their declared artifacts.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Sequence

import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_session as session_state
import deep_sky_siril_tooling as tooling
import deep_sky_siril_validation as validation_ops
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    PAIRED_DELIVERY_POLICIES,
    DISPLAY_IMAGE_SUFFIXES,
    HASH_PATTERN,
    IMAGE_SUFFIXES,
    LIMITATION_CODE_PATTERN,
    RUN_ID_PATTERN,
    SCHEMA_PREFIX,
    ContractError,
    _absolute,
    _fsync_directory,
    atomic_write_json,
    atomic_write_text,
    canonical_json,
    fingerprint,
    fingerprint_matches,
    load_json,
    relative_session_path,
    session_path,
    sha256_file,
    stable_hash,
    utc_now,
)

def _decode_validation_result(
    *,
    passed: bool,
    validation_mode: str,
    decoder: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    strict_container: dict[str, Any] | None = None,
    reason_code: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "passed": passed,
        "container_validation": validation_mode,
        "decoder": decoder,
    }
    if metadata is not None:
        result["format"] = metadata["format"]
        result["geometry"] = metadata["geometry"]
        result["siril_reopen"] = metadata
    if strict_container is not None:
        result["strict_container"] = strict_container
    if reason_code is not None:
        result["reason_code"] = reason_code
        result["reason"] = reason or reason_code
    return result


def _decode_scientific_output(
    path: Path,
    *,
    session: Path,
    run_id: str,
    output_index: int,
    siril: dict[str, Any],
    timeout: int,
    expected_format: str,
    validation_mode: str,
    expected_artifact_fingerprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reopen one scientific artifact in a fresh offline Siril process."""

    decoder_identity: dict[str, Any] = {
        "name": "siril-cli",
        "version": siril.get("version"),
        "method": "siril-reopen-stat",
    }

    def failed(
        reason_code: str,
        reason: str,
        *,
        decoder: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        strict_container: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _decode_validation_result(
            passed=False,
            validation_mode=validation_mode,
            decoder=decoder or decoder_identity,
            metadata=metadata,
            strict_container=strict_container,
            reason_code=reason_code,
            reason=reason,
        )

    try:
        current = fingerprint(path, include_path=False)
    except ContractError:
        return failed(
            "artifact_changed_during_validation",
            "Scientific artifact disappeared before Siril reopen validation",
        )
    if (
        expected_artifact_fingerprint is not None
        and current != expected_artifact_fingerprint
    ):
        return failed(
            "artifact_changed_during_validation",
            "Scientific artifact changed before Siril reopen validation",
        )
    before = current

    # In strict mode the full container walk is the admission gate for Siril.
    # This ordering prevents malformed/truncated containers from reaching the
    # external decoder.  Fingerprinting on both sides of the walk binds the
    # static metadata to the exact bytes that Siril will subsequently reopen.
    strict_container: dict[str, Any] | None = None
    if validation_mode == "strict":
        try:
            strict_container = (
                artifacts.inspect_fits_container(path)
                if expected_format == "FITS"
                else artifacts.inspect_xisf_container(path)
            )
        except artifacts._ArtifactValidationError as exc:
            return failed(exc.reason_code, str(exc))
        try:
            after_container_scan = fingerprint(path, include_path=False)
        except ContractError:
            return failed(
                "artifact_changed_during_validation",
                "Scientific artifact disappeared during strict container validation",
                strict_container=strict_container,
            )
        if after_container_scan != before:
            return failed(
                "artifact_changed_during_validation",
                "Scientific artifact changed during strict container validation",
                strict_container=strict_container,
            )
        strict_container["artifact_fingerprint"] = after_container_scan

    decode_dir = session_path(
        session,
        session / "runtime" / "decode-validation",
        allowed_roots=("runtime",),
    )
    decode_dir.mkdir(parents=True, exist_ok=True)
    label = f"{run_id}-{output_index:02d}"
    script = decode_dir / f"{label}.ssf"
    config = session / "runtime" / "siril-configs" / f"{run_id}-decode-{output_index:02d}.ini"
    log_path = session / "logs" / f"{run_id}-decode-{output_index:02d}.log"
    for owned_path in (script, config, log_path):
        if owned_path.exists() or owned_path.is_symlink():
            raise ContractError(
                "run_id_already_used",
                f"Decode-validation artifact already exists: {owned_path}",
            )

    quoted_path = json.dumps(str(path), ensure_ascii=False)
    atomic_write_text(
        script,
        "requires 1.4.4 1.5.0\n"
        "set32bits\n"
        f"load {quoted_path}\n"
        "stat main\n"
        "close\n",
    )
    atomic_write_text(
        config,
        "# starun-siril decode validation\n"
        "[core]\n"
        "force_16bit=false\n"
        "script_check_requires=true\n"
        "pipe_check_requires=false\n"
        "check_updates=false\n"
        "[gui]\n"
        "icc_autoassignment=0\n"
        "use_scripts_repository=false\n"
        "use_spcc_repository=false\n"
        "auto_update_scripts=false\n"
        "auto_update_spcc=false\n",
    )
    command = [
        str(Path(str(siril["path"]))),
        f"--initfile={config}",
        f"--directory={session}",
        "--offline",
        f"--script={script}",
    ]
    decode_timeout = max(1, min(int(timeout), 300))
    started = utc_now()
    timed_out = False
    runtime_fingerprint = siril.get("fingerprint")
    runtime_verified_before = (
        runtime_fingerprint is None or fingerprint_matches(runtime_fingerprint)
    )
    if not runtime_verified_before:
        output = "Frozen siril-cli fingerprint changed before decode validation.\n"
        exit_code = 126
    else:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                timeout=decode_timeout,
                check=False,
                env=tooling._subprocess_environment("siril_runtime"),
            cwd=session,
            )
            output = completed.stdout
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as exc:
            raw = exc.stdout or ""
            output = (
                raw.decode("utf-8", errors="replace")
                if isinstance(raw, bytes)
                else str(raw)
            )
            output += f"\nDecode validation timed out after {decode_timeout} seconds.\n"
            exit_code = 124
            timed_out = True
        except OSError as exc:
            output = f"{type(exc).__name__}: {exc}\n"
            exit_code = 127
    log_text = f"started_at={started}\n{output}"
    atomic_write_text(log_path, log_text)

    decoder: dict[str, Any] = {
        **decoder_identity,
        "timeout_seconds": decode_timeout,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "runtime_fingerprint": runtime_fingerprint,
        "script": {
            **fingerprint(script, include_path=False),
            "path": relative_session_path(session, script),
        },
        "config": {
            **fingerprint(config, include_path=False),
            "path": relative_session_path(session, config),
        },
        "log": {
            **fingerprint(log_path, include_path=False),
            "path": relative_session_path(session, log_path),
        },
    }
    decoder["log_diagnostics"] = artifacts.diagnose_siril_log(
        log_text,
        exit_code=exit_code,
        timed_out=timed_out,
        execution_valid=False,
    )
    runtime_verified_after = (
        runtime_fingerprint is None or fingerprint_matches(runtime_fingerprint)
    )
    decoder["runtime_binding_unchanged"] = bool(
        runtime_verified_before and runtime_verified_after
    )
    if not decoder["runtime_binding_unchanged"]:
        return failed(
            "decoder_runtime_changed",
            "Frozen siril-cli changed during decode validation",
            decoder=decoder,
        )
    if timed_out:
        return failed(
            "decode_timeout",
            f"Siril decode validation timed out after {decode_timeout} seconds",
            decoder=decoder,
        )
    if exit_code != 0:
        return failed(
            "decode_failed",
            f"Siril decode validation exited with status {exit_code}",
            decoder=decoder,
        )

    try:
        metadata = artifacts.parse_siril_reopen_metadata(
            output,
            expected_format=expected_format,
        )
    except artifacts._ArtifactValidationError as exc:
        return failed(exc.reason_code, str(exc), decoder=decoder)

    samples = artifacts.parse_statistics_samples(output)
    expected_channels = int(metadata["geometry"]["channels"])
    channels: dict[str, Any] = {}
    if len(samples) == 1 and isinstance(samples[0].get("channels"), dict):
        channels = samples[0]["channels"]
    finite_statistics = bool(channels) and all(
        isinstance(channel, dict)
        and isinstance(channel.get("adu_16_equivalent"), dict)
        and all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in channel["adu_16_equivalent"].values()
        )
        for channel in channels.values()
    )
    decoder["statistics_channels"] = sorted(channels)
    decoder["statistics_channel_count"] = len(channels)
    if len(channels) != expected_channels or not finite_statistics:
        return failed(
            "statistics_missing",
            "Siril did not report finite statistics for every reopened image channel",
            decoder=decoder,
            metadata=metadata,
        )

    if strict_container is not None:
        siril_geometry = metadata["geometry"]
        strict_geometry = strict_container.get("geometry", {})
        if (
            strict_container.get("format") != expected_format
            or any(
                strict_geometry.get(key) != siril_geometry.get(key)
                for key in ("width", "height", "channels")
            )
        ):
            return failed(
                "container_metadata_mismatch",
                "Strict container metadata differs from Siril reopen metadata",
                decoder=decoder,
                metadata=metadata,
                strict_container=strict_container,
            )

    try:
        after = fingerprint(path, include_path=False)
    except ContractError:
        return failed(
            "artifact_changed_during_validation",
            "Scientific artifact disappeared during validation",
            decoder=decoder,
            metadata=metadata,
            strict_container=strict_container,
        )
    if after != before:
        return failed(
            "artifact_changed_during_validation",
            "Scientific artifact changed during validation",
            decoder=decoder,
            metadata=metadata,
            strict_container=strict_container,
        )
    decoder["log_diagnostics"] = artifacts.diagnose_siril_log(
        log_text,
        exit_code=exit_code,
        timed_out=timed_out,
        execution_valid=True,
    )
    if decoder["log_diagnostics"]["status"] == "failed":
        return failed(
            "decode_log_rejected",
            "Siril decode validation emitted an unclassified runtime error",
            decoder=decoder,
            metadata=metadata,
            strict_container=strict_container,
        )
    decoder["artifact_fingerprint"] = after
    return _decode_validation_result(
        passed=True,
        validation_mode=validation_mode,
        decoder=decoder,
        metadata=metadata,
        strict_container=strict_container,
    )


def _validate_output(
    path: Path,
    *,
    session: Path,
    run_id: str,
    output_index: int,
    siril: dict[str, Any],
    timeout: int,
    container_validation: str,
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size < 1:
        return {
            "passed": False,
            "reason_code": "missing_or_empty",
            "reason": "missing_or_empty",
        }
    suffix = path.suffix.lower()
    try:
        initial_fingerprint = fingerprint(path, include_path=False)
        if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            return artifacts.validate_display(path)
        if suffix in {".fit", ".fits", ".fts", ".xisf"}:
            return _decode_scientific_output(
                path,
                session=session,
                run_id=run_id,
                output_index=output_index,
                siril=siril,
                timeout=timeout,
                expected_format="XISF" if suffix == ".xisf" else "FITS",
                validation_mode=container_validation,
                expected_artifact_fingerprint=initial_fingerprint,
            )
        if suffix == ".json":
            load_json(path)
            return {"passed": True, "format": "JSON"}
        return {"passed": True, "format": suffix.lstrip(".") or "unknown"}
    except artifacts._ArtifactValidationError as exc:
        return {
            "passed": False,
            "reason_code": exc.reason_code,
            "reason": str(exc),
        }
    except ContractError as exc:
        return {
            "passed": False,
            "reason_code": "container_invalid",
            "reason": str(exc),
        }


def _unknown_context_fields(payload: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        field
        for field in ("input_state", "channel_mode")
        if payload["context"].get(field, "unknown") == "unknown"
    )


def _validate_protocol_applicability(
    session: Path,
    payload: dict[str, Any],
    *,
    protocol: str,
    source: Path,
) -> None:
    original = Path(payload["input"]["path"]).resolve()
    if protocol == "input.inspect" and source.resolve() != original:
        raise ContractError(
            "protocol_not_applicable",
            "input.inspect must use the immutable @input source",
        )
    unknown_fields = _unknown_context_fields(payload)
    if not unknown_fields:
        return
    if protocol != "input.inspect":
        raise ContractError(
            "protocol_not_applicable",
            f"Unknown {', '.join(unknown_fields)} permits only Stage 1 input.inspect; after an accepted inspection, use confirm-input with reliable source evidence for every unknown field",
        )


def _replay_run_receipt(
    *,
    session: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any],
    receipt_path: Path,
    script: Path,
    provenance: Path,
    protocol: str,
    source_value: str,
    expected_values: Sequence[str],
    primary_output_value: str | None = None,
    separation_run: str | None = None,
    stretch_run: str | None = None,
) -> dict[str, Any]:
    """Replay only an immutable receipt matching the complete requested lineage."""
    if receipt_path.is_symlink():
        raise ContractError("run_id_already_used", "Run receipt is a symlink")
    receipt = load_json(receipt_path)
    run_id = script.stem
    entries = manifest.get("runs")
    matching_entries = (
        [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("id") == run_id
        ]
        if isinstance(entries, list)
        else []
    )
    if (
        len(matching_entries) != 1
        or matching_entries[0].get("receipt") != receipt_path.name
        or matching_entries[0].get("receipt_sha256") != sha256_file(receipt_path)
        or receipt.get("schema") != f"{SCHEMA_PREFIX}.run-receipt.v1"
        or receipt.get("contract_version") != CONTRACT_VERSION
        or receipt.get("id") != run_id
        or receipt.get("protocol") != protocol
        or receipt.get("status") != "success"
    ):
        raise ContractError(
            "run_id_already_used",
            f"Run receipt does not match the requested lineage: {run_id}",
        )

    if "primary_output" in receipt:
        from deep_sky_siril_processing import primary_output
        requested = primary_output(session, [session_path(session, v) for v in expected_values], primary_output_value)
        if (receipt["primary_output"] != (relative_session_path(session, requested) if requested else None)
            or receipt.get("protocol") == "stars.recompose" and
            (receipt.get("branch_binding", {}).get("separation_run") != separation_run
             or receipt.get("branch_binding", {}).get("stretch_run") != stretch_run)):
            raise ContractError("run_id_already_used", "Primary output or branch arguments differ from frozen receipt")
    source_raw = _absolute(source_value)
    if source_raw.is_symlink() or not source_raw.is_file():
        raise ContractError("source_missing", f"Run source is missing or unsafe: {source_raw}")
    source = source_raw.resolve()
    if not session_state._source_is_bound(session, payload, source):
        raise ContractError("source_unbound", "Run source is not the input or a verified prior output")
    _validate_protocol_applicability(
        session,
        payload,
        protocol=protocol,
        source=source,
    )
    source_record = fingerprint(source, include_path=False)
    requested_source_path = (
        relative_session_path(session, source)
        if source.is_relative_to(session)
        else "@input"
    )
    if (
        receipt.get("source") != source_record
        or receipt.get("source_path") != requested_source_path
    ):
        raise ContractError(
            "run_id_already_used",
            f"Run source differs from the committed receipt: {run_id}",
        )

    script_record = receipt.get("script")
    expected_script = {
        **fingerprint(script, include_path=False),
        "path": relative_session_path(session, script),
    }
    if script_record != expected_script:
        raise ContractError(
            "run_id_already_used",
            f"Run script differs from the committed receipt: {run_id}",
        )

    if not expected_values:
        raise ContractError("expected_outputs_missing", "run requires at least one --expect")
    expected: list[Path] = []
    seen: set[Path] = set()
    for value in expected_values:
        path = session_path(
            session,
            value,
            allowed_roots=("artifacts", "previews", "reports"),
        )
        if path in seen:
            raise ContractError("expected_output_duplicate", f"Duplicate expected output: {path}")
        seen.add(path)
        expected.append(path)

    output_records = receipt.get("outputs")
    if not isinstance(output_records, list) or len(output_records) != len(expected):
        raise ContractError(
            "run_id_already_used",
            f"Run outputs differ from the committed receipt: {run_id}",
        )
    for path, record in zip(expected, output_records, strict=True):
        if (
            not isinstance(record, dict)
            or record.get("path") != relative_session_path(session, path)
            or not fingerprint_matches({**record, "path": str(path)})
        ):
            raise ContractError(
                "run_id_already_used",
                f"Run output changed or differs from the committed receipt: {run_id}",
            )

    if not session_state.execution_policy_is_current(payload):
        if not session_state._receipt_bindings_unchanged(session, payload, receipt):
            raise ContractError(
                "run_id_already_used",
                f"Legacy run evidence differs from the committed receipt: {run_id}",
            )
        return {**receipt, "replayed": True}

    frozen_probe = load_json(session / "reports" / "tool-probe.json")
    validation = validation_ops.validate_script_file(
        script,
        provenance=provenance,
        session=session,
        session_payload=payload,
        protocol=protocol,
        source=source,
        expected_outputs=expected,
        probe=frozen_probe,
        replaying=True,
    )
    runtime = receipt.get("runtime")
    if (
        receipt.get("commands") != validation["commands"]
        or receipt.get("script") != validation["script_fingerprint"]
        or receipt.get("script_provenance") != validation["script_provenance"]
        or receipt.get("knowledge_validation") != validation["knowledge_validation"]
        or receipt.get("knowledge_bindings_unchanged") is not True
        or receipt.get("script_unchanged") is not True
        or receipt.get("command_knowledge") != validation["command_knowledge"]
        or receipt.get("source_unchanged") is not True
        or not validation_ops.script_binding_unchanged(session, validation)
        or not _knowledge_bindings_unchanged(session, payload, validation)
        or not isinstance(runtime, dict)
        or runtime.get("container_validation")
        != payload["context"]["container_validation"]
        or runtime.get("adapters", []) != validation.get("adapters", [])
        or (
            session_state.execution_policy_is_current(payload)
            and runtime.get("network") != validation.get("network")
        )
        or not session_state._receipt_bindings_unchanged(session, payload, receipt)
    ):
        raise ContractError(
            "run_id_already_used",
            f"Run policy or runtime binding differs from the committed receipt: {run_id}",
        )
    return {**receipt, "replayed": True}


def _knowledge_bindings_unchanged(
    session: Path,
    payload: dict[str, Any],
    validation: dict[str, Any],
) -> bool:
    try:
        current = session_state._verify_session_knowledge(session, payload)
        effective = session_state.effective_session_payload(session, payload)
        if effective.get("input_confirmation") != payload.get("input_confirmation"):
            return False
    except ContractError:
        return False
    captured = validation.get("knowledge_validation")
    return bool(
        isinstance(captured, dict)
        and all(captured.get(key) == value for key, value in current.items())
        and validation_ops.knowledge_bindings_unchanged(session, validation)
    )


def _quarantine_separation_outputs(session: Path, run_id: str, expected: Sequence[Path]):
    records, errors = [], []
    for path in expected:
        original = path.relative_to(session).as_posix()
        if Path(original).parts[0] not in {'artifacts', 'previews'}:
            continue
        if not path.exists() and not path.is_symlink():
            continue
        try:
            source = session_path(session, path, must_exist=True, allowed_roots=('artifacts', 'previews'))
            before = fingerprint(source, include_path=False)
            target = session_path(session, session / 'reports' / run_id / 'rejected' / original,
                                  allowed_roots=('reports',))
            target.parent.mkdir(parents=True, exist_ok=True)
            session_path(session, target, allowed_roots=('reports',))
            # An exclusive link avoids overwriting evidence before unlinking the original.
            os.link(source, target, follow_symlinks=False)
            if fingerprint(target, include_path=False) != before or fingerprint(source, include_path=False) != before:
                raise ContractError('artifact_changed_during_quarantine', 'Output changed while isolating it')
            source.unlink()
            _fsync_directory(source.parent)
            _fsync_directory(target.parent)
            records.append({'original_path': original, 'path': relative_session_path(session, target), **before})
        except (ContractError, OSError, ValueError) as exc:
            errors.append({'path': original, 'reason': str(exc)})
    return records, errors


def _accepted_review(session: Path, run_name: str):
    """Require one verified actual acceptance for a scientific parent."""
    matches = [p for p in (session / "reviews").glob("*.json") if load_json(p).get("run_id") == run_name]
    if len(matches) != 1:
        raise ContractError("review_required", f"Exactly one actual review is required for {run_name}")
    review, binding = _validate_review(session, str(matches[0]), run_name)
    if review["verdict"] != "accept":
        raise ContractError("review_required", f"Accepted visual review is required for {run_name}")
    return review, binding


def run_script(
    session_value: str,
    *,
    protocol: str,
    script_value: str,
    source_value: str,
    expected_values: Sequence[str],
    timeout: int,
    validate_only: bool = False,
    primary_output_value: str | None = None,
    separation_run: str | None = None,
    stretch_run: str | None = None,
) -> dict[str, Any]:
    session, payload, manifest = session_state.load_session(session_value)
    payload = session_state.effective_session_payload(session, payload, manifest)
    script = session_path(session, script_value, must_exist=True, allowed_roots=("scripts",))
    if script.suffix != ".ssf" or RUN_ID_PATTERN.fullmatch(script.stem) is None:
        raise ContractError("script_name_invalid", "Script name must be NNN-name.ssf")
    run_id = script.stem
    provenance = script.with_suffix(".provenance.json")
    receipt_path = session / "runs" / f"{run_id}.json"
    if not validate_only and (receipt_path.exists() or receipt_path.is_symlink()):
        replay = _replay_run_receipt(
            session=session,
            payload=payload,
            manifest=manifest,
            receipt_path=receipt_path,
            script=script,
            provenance=provenance,
            protocol=protocol,
            source_value=source_value,
            expected_values=expected_values,
            primary_output_value=primary_output_value,
            separation_run=separation_run,
            stretch_run=stretch_run,
        )
        return _with_metric_report_warnings(session, replay)
    if not session_state.execution_policy_is_current(payload):
        raise ContractError(
            "legacy_session_read_only",
            "Legacy standalone v1 sessions allow existing receipt replay/finalize only; initialize a new session for validation or execution",
        )

    source_raw = _absolute(source_value)
    if source_raw.is_symlink() or not source_raw.is_file():
        raise ContractError("source_missing", f"Run source is missing or unsafe: {source_raw}")
    source = source_raw.resolve()
    if not session_state._source_is_bound(session, payload, source):
        raise ContractError("source_unbound", "Run source is not the input or a verified prior output")
    _validate_protocol_applicability(
        session,
        payload,
        protocol=protocol,
        source=source,
    )
    source_record = fingerprint(source, include_path=False)
    if not expected_values:
        raise ContractError("expected_outputs_missing", "run requires at least one --expect")
    expected: list[Path] = []
    seen: set[Path] = set()
    for value in expected_values:
        path = session_path(
            session,
            value,
            allowed_roots=("artifacts", "previews", "reports"),
        )
        if path in seen:
            raise ContractError("expected_output_duplicate", f"Duplicate expected output: {path}")
        if path.exists() or path.is_symlink():
            raise ContractError("output_write_conflict", f"Expected output already exists: {path}")
        seen.add(path)
        expected.append(path)
    timeout = int(timeout)
    if not 1 <= timeout <= 86400:
        raise ContractError("invalid_timeout", "Timeout must be within 1..86400 seconds")

    offline = bool(payload["context"].get("offline"))
    container_validation = str(payload["context"]["container_validation"])
    frozen_probe = load_json(session / "reports" / "tool-probe.json")
    defer_local_probe = (protocol == 'structure.local-contrast'
                         and payload['execution_policy'].get('local_contrast') in {'texture_hf_review_v1', 'wavelet_clahe_competition_v2'})
    probe = (
        frozen_probe
        if validate_only or defer_local_probe or protocol in {"background.subtract", "stars.separate"}
        else tooling.probe_tools(offline=offline)
    )
    siril = probe.get("tools", {}).get("siril_cli", {})
    from deep_sky_siril_processing import ProcessingEvidenceError, primary_output, prepare_processing, validate_processing_outputs
    primary = primary_output(session, expected, primary_output_value)
    review_bindings = []

    def accepted_review(run_name):
        review, binding = _accepted_review(session, run_name)
        if binding not in review_bindings:
            review_bindings.append(binding)
        return review

    processing = prepare_processing(session, payload, protocol, source, primary,
        script.read_text(encoding="utf-8"), frozen_probe, separation_run, stretch_run,
        lambda name: _load_run(session, name), accepted_review, run_id=run_id)
    if defer_local_probe and not validate_only:
        probe = tooling.probe_tools(offline=offline)
        siril = probe.get('tools', {}).get('siril_cli', {})
    if protocol == "stars.separate":
        processing["branch_binding"]["separation_run"] = run_id
    processing["review_bindings"] = review_bindings
    if "input_confirmation" in payload:
        processing["input_confirmation"] = payload["input_confirmation"]
    if protocol == "astrometry.solve":
        probe["tools"]["local_gaia_astro"] = frozen_probe["tools"].get("local_gaia_astro", {})
        catalogue = processing.get("astrometry", {}).get("catalogue")
        if catalogue and not fingerprint_matches(catalogue):
            raise ContractError("runtime_dependency_changed", "Frozen astrometric catalogue changed")
    validation = validation_ops.validate_script_file(
        script,
        provenance=provenance,
        session=session,
        session_payload=payload,
        protocol=protocol,
        source=source,
        expected_outputs=expected,
        probe=probe,
    )

    if validate_only:
        script_unchanged = validation_ops.script_binding_unchanged(
            session,
            validation,
        )
        knowledge_bindings_unchanged = _knowledge_bindings_unchanged(
            session,
            payload,
            validation,
        )
        if (
            not fingerprint_matches(payload["input"])
            or fingerprint(source, include_path=False) != source_record
        ):
            raise ContractError("source_unbound", "Session input changed during static validation")
        if not script_unchanged or not knowledge_bindings_unchanged:
            raise ContractError(
                "knowledge_binding_drift",
                "Script, provenance, or frozen knowledge changed during static validation",
            )
        validation_path = session / "reports" / f"{run_id}-static-validation.json"
        expected_relative = [relative_session_path(session, path) for path in expected]
        candidate = {
            "schema": f"{SCHEMA_PREFIX}.static-validation.v1",
            "contract_version": CONTRACT_VERSION,
            "status": "success",
            "mode": "validate_only",
            "executed": False,
            "id": run_id,
            "protocol": protocol,
            "script": {
                **validation["script_fingerprint"],
            },
            "script_provenance": validation["script_provenance"],
            "script_unchanged": script_unchanged,
            "knowledge_validation": validation["knowledge_validation"],
            "knowledge_bindings_unchanged": knowledge_bindings_unchanged,
            "command_knowledge": validation["command_knowledge"],
            "source": source_record,
            "source_path": (
                relative_session_path(session, source)
                if source.is_relative_to(session)
                else "@input"
            ),
            "expected_outputs": expected_relative,
            **processing,
            "network": validation["network"],
            "validation": validation,
            "scope": {
                "checked": [
                    "protocol_command_allowlist",
                    "path_confinement",
                    "declared_writes",
                    "frozen_runtime_binding",
                    "protocol_applicability",
                    "ssf_provenance",
                    "pinned_command_knowledge",
                    "network_policy",
                ],
                "not_checked": [
                    "visual_quality",
                    "siril_execution",
                ],
            },
        }
        if validation_path.exists():
            prior = load_json(validation_path)
            # The current validators have already run above. ``scope`` records
            # the checks reported by that historical receipt, not its lineage.
            informational = {"validated_at", "replayed", "scope"}
            comparable = {
                key: value for key, value in prior.items() if key not in informational
            }
            current = {
                key: value for key, value in candidate.items() if key not in informational
            }
            if comparable != current:
                raise ContractError("run_id_already_used", f"Static validation ID cannot be reused: {run_id}")
            return {**prior, "replayed": True}
        result = {**candidate, "validated_at": utc_now()}
        atomic_write_json(validation_path, result)
        return result

    if not isinstance(siril, dict) or siril.get("compatible") is not True:
        raise ContractError(
            "runtime_dependency_missing",
            "Compatible siril-cli >=1.4.4,<1.5 is required",
            missing_dependencies=("siril-cli>=1.4.4,<1.5",),
        )
    tooling.verify_tool_fingerprint(siril.get("fingerprint"), name="siril-cli")

    config = session / "runtime" / "siril-configs" / f"{run_id}.ini"
    if config.exists():
        raise ContractError("run_id_already_used", f"Runtime config already exists: {run_id}")
    atomic_write_text(
        config,
        "# starun-siril session-local configuration\n"
        "[core]\n"
        "force_16bit=false\n"
        "script_check_requires=true\n"
        "pipe_check_requires=false\n"
        "check_updates=false\n"
        "[gui]\n"
        "icc_autoassignment=0\n"
        "use_scripts_repository=false\n"
        "use_spcc_repository=false\n"
        "auto_update_scripts=false\n"
        "auto_update_spcc=false\n",
    )
    command = [
        str(Path(siril["path"])),
        f"--initfile={config}",
        f"--directory={session}",
    ]
    effective_offline = bool(validation["network"]["effective_offline"])
    if effective_offline:
        command.append("--offline")
    command.append(f"--script={script}")
    started = utc_now()
    timed_out = False
    if protocol == 'stretch' and processing.get('hdr_transfer', {}).get('strategy'):
        from deep_sky_siril_stretch_profiles import claim_attempt, load_plan
        from deep_sky_siril_stretch_trials import enabled as archive_enabled, directory as archive_directory
        if archive_enabled(payload) and archive_directory(session, run_id).exists():
            raise ContractError('trial_archive_invalid', 'Trial artifacts already exist; use a new run ID')
        plan, _ = load_plan(session, payload, processing['hdr_transfer']['strategy'])
        if plan.get('schema') == 'starun-siril.stretch-strategy.v3':
            from deep_sky_siril_stretch_stepwise import claim_formal
            claim_formal(session, payload, processing['hdr_transfer']['stepwise_parameters'], run_id)
        else:
            claim_attempt(session, payload, run_id, {'source': plan['source'], 'strategy': processing['hdr_transfer']['strategy']})
    elif protocol == 'stretch' and processing.get('adaptive_parameters'):
        from deep_sky_siril_stretch import bound_file
        binding = processing['adaptive_parameters']['parameters']
        if load_json(bound_file(session, binding)).get('schema') == 'starun-siril.stretch-parameters.v3':
            from deep_sky_siril_stretch_stepwise import claim_formal
            claim_formal(session, payload, binding, run_id)
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            env=tooling._subprocess_environment("siril_runtime"),
            cwd=session,
        )
        output = completed.stdout
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or ""
        output = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        output += f"\nTimed out after {timeout} seconds.\n"
        exit_code = 124
        timed_out = True
    except OSError as exc:
        output = f"{type(exc).__name__}: {exc}\n"
        exit_code = 127
    log_path = session / "logs" / f"{run_id}.log"
    log_text = f"started_at={started}\n{output}"
    atomic_write_text(log_path, log_text)

    output_records: list[dict[str, Any]] = []
    validations: dict[str, Any] = {}
    all_outputs_valid = True
    for output_index, path in enumerate(expected, start=1):
        result = _validate_output(
            path,
            session=session,
            run_id=run_id,
            output_index=output_index,
            siril=siril,
            timeout=timeout,
            container_validation=container_validation,
        )
        relative = relative_session_path(session, path)
        validations[relative] = result
        if result.get("passed") is not True:
            all_outputs_valid = False
            continue
        if path.suffix.lower() in {".fit", ".fits", ".fts", ".xisf"}:
            decoder = result.get("decoder")
            verified_fingerprint = (
                decoder.get("artifact_fingerprint")
                if isinstance(decoder, dict)
                else None
            )
            try:
                current_fingerprint = fingerprint(path, include_path=False)
            except ContractError:
                current_fingerprint = None
            if (
                not isinstance(verified_fingerprint, dict)
                or current_fingerprint != verified_fingerprint
            ):
                result["passed"] = False
                result["reason_code"] = "artifact_changed_during_validation"
                result["reason"] = (
                    "Scientific artifact changed after decoder validation"
                )
                all_outputs_valid = False
                continue
            output_records.append({**verified_fingerprint, "path": relative})
        else:
            output_records.append({**fingerprint(path, include_path=False), "path": relative})
    validated_display_names = [
        Path(path).name
        for path, result in validations.items()
        if Path(path).suffix.lower() in DISPLAY_IMAGE_SUFFIXES
        and result.get("passed") is True
    ]
    log_diagnostics = artifacts.diagnose_siril_log(
        log_text,
        exit_code=exit_code,
        timed_out=timed_out,
        execution_valid=(exit_code == 0 and not timed_out and all_outputs_valid),
        validated_display_names=validated_display_names,
    )
    input_unchanged = fingerprint_matches(payload["input"])
    try:
        source_unchanged = fingerprint(source, include_path=False) == source_record
    except ContractError:
        source_unchanged = False
    knowledge_bindings_unchanged = _knowledge_bindings_unchanged(
        session,
        payload,
        validation,
    )
    script_unchanged = validation_ops.script_binding_unchanged(
        session,
        validation,
    )
    adapters_unchanged = all(
        fingerprint_matches(record) for record in validation.get("adapters", [])
    )
    starnet_runtime = (
        probe.get("tools", {}).get("starnet")
        if protocol == "stars.separate"
        else None
    )
    starnet_unchanged = True
    if protocol == "stars.separate":
        starnet_unchanged = tooling.starnet_runtime_unchanged(starnet_runtime)
    deepsnr_runtime = processing.get('linear_denoise', {}).get('runtime')
    deepsnr_unchanged = deepsnr_runtime is None or tooling.deepsnr_runtime_unchanged(deepsnr_runtime)
    sirilpy_runtime = validation.get("sirilpy_bridge")
    sirilpy_unchanged = True
    if protocol in {"background.subtract", "restoration.deconvolve"} and isinstance(sirilpy_runtime, dict):
        sirilpy_unchanged = (
            sirilpy_runtime.get("status") == "runtime_check_required"
            and sirilpy_runtime.get("required_version") == "1.0.25"
        )
    siril_unchanged = fingerprint_matches(siril.get("fingerprint"))
    runtime_bindings_unchanged = bool(
        siril_unchanged
        and adapters_unchanged
        and starnet_unchanged
        and deepsnr_unchanged
        and sirilpy_unchanged
    )
    success = (
        exit_code == 0
        and not timed_out
        and all_outputs_valid
        and log_diagnostics["status"] != "failed"
        and input_unchanged
        and source_unchanged
        and script_unchanged
        and knowledge_bindings_unchanged
        and runtime_bindings_unchanged
    )
    processing_checks = {"status": "not_run"}
    separation_failure = None
    bridge_checks = None
    if protocol == 'stars.separate':
        from deep_sky_siril_processing import validate_separation_bridge
        try:
            bridge_checks = validate_separation_bridge(session, primary, processing['star_separation'])
        except (ContractError, ValueError, OSError, KeyError, TypeError) as exc:
            reason_code = 'starnet_bridge_unverified'
            reason = str(exc)
            try:
                separation = processing['star_separation']
                document = load_json(session / separation['contract'])
                full = session_path(session, separation['full_source'], must_exist=True, allowed_roots=('artifacts',))
                bitpix = artifacts.read_fits_header(full).get('BITPIX')
                if document.get('schema') == 'starun-siril.starnet-contract.v2' and bitpix != -32:
                    reason_code = 'native_input_precision_invalid'
                    reason = f'Native full-source requires float32 FITS (BITPIX=-32), got BITPIX={bitpix}. {exc}'
            except (ContractError, ValueError, OSError, KeyError, TypeError):
                pass
            separation_failure = {'reason_code': reason_code, 'reason': reason,
                                  'log_path': relative_session_path(session, log_path)}
            bridge_checks = {'status': 'failed', 'reason_code': reason_code, 'reason': reason}
            success = False
            processing_checks = {'status': 'failed', 'reason': reason}
    deconvolution_checks = None
    if processing.get('deconvolution_binding'):
        from deep_sky_siril_psf import measure_deconvolution
        try:
            deconvolution_checks = measure_deconvolution(session, source, primary,
                                                         processing['deconvolution_binding'], log_text, write_reports=True)
        except ProcessingEvidenceError as exc:
            success = False
            processing_checks = {'status': 'failed', 'reason': str(exc), **exc.processing_checks}
    if success:
        try:
            checks = validate_processing_outputs(session, source, primary, processing, execution_policy=payload['execution_policy'], write_diagnostics=True, log_text=log_text)
            if processing.get("astrometry", {}).get("catalogue") and not fingerprint_matches(processing["astrometry"]["catalogue"]):
                raise ContractError("runtime_dependency_changed", "Astrometric catalogue changed")
            if any(sha256_file(session / b["path"]) != b["sha256"] for b in review_bindings):
                raise ContractError("review_changed", "Parent visual review changed during execution")
            processing_checks = {"status": "passed", **checks}
            if protocol == 'stretch':
                from deep_sky_siril_visibility import build
                visibility = build(session, payload, source, primary, processing, run_id, timeout)
                if visibility is not None:
                    processing['stretch_visibility'] = visibility
            if "astrometry" in checks:
                # The receipt records the achieved solve, never the pre-execution request.
                processing["astrometry"] = checks["astrometry"]
            if checks.get('background_validation', {}).get('status', 'passed') != 'passed':
                success = False
                processing_checks['status'] = 'failed'
                processing_checks['reason'] = '; '.join(checks['background_validation']['reasons'])
        except (ContractError, ValueError, OSError) as exc:
            success = False
            processing_checks = {"status": "failed", "reason": str(exc)}
            if isinstance(exc, ProcessingEvidenceError):
                processing_checks.update(exc.processing_checks)
    if (protocol == 'stars.recompose'
            and payload['execution_policy'].get('star_diagnostics') == 'subject_roi_residual_v2'
            and 'closure' not in processing_checks):
        from deep_sky_siril_processing import _measure_recomposition
        processing_checks['status'] = 'failed'
        try:
            processing_checks['closure'] = _measure_recomposition(
                session, processing['branch_binding'], payload['context']['target_type'],
                source=source, primary=primary, policy='subject_roi_residual_v2')
        except ProcessingEvidenceError as exc:
            processing_checks.update(exc.processing_checks)
            processing_checks.setdefault('reason', str(exc))
        except (ContractError, ValueError, OSError, KeyError, TypeError) as exc:
            processing_checks.setdefault('reason', str(exc))
    if protocol == 'stars.separate':
        processing_checks['bridge'] = bridge_checks
        if not success:
            reason = processing_checks.get('reason') or (
                f'Star separation failed: exit_code={exit_code}, timed_out={timed_out}, '
                f'outputs_valid={all_outputs_valid}, log_status={log_diagnostics["status"]}, '
                f'input_unchanged={input_unchanged}, source_unchanged={source_unchanged}, '
                f'script_unchanged={script_unchanged}, knowledge_unchanged={knowledge_bindings_unchanged}, '
                f'runtime_unchanged={runtime_bindings_unchanged}')
            processing_checks.update(status='failed', reason=reason)
            separation_failure = separation_failure or {
                'reason_code': 'stars_separation_failed', 'reason': reason,
                'log_path': relative_session_path(session, log_path)}
    if deconvolution_checks is not None:
        processing_checks['deconvolution'] = deconvolution_checks
    receipt = {
        "schema": f"{SCHEMA_PREFIX}.run-receipt.v1",
        "contract_version": CONTRACT_VERSION,
        "id": run_id,
        "protocol": protocol,
        "status": "success" if success else "failed",
        "started_at": started,
        "completed_at": utc_now(),
        "source": source_record,
        "source_path": (
            relative_session_path(session, source)
            if source.is_relative_to(session)
            else "@input"
        ),
        "script": validation["script_fingerprint"],
        "script_provenance": validation["script_provenance"],
        "script_unchanged": script_unchanged,
        "knowledge_validation": validation["knowledge_validation"],
        "knowledge_bindings_unchanged": knowledge_bindings_unchanged,
        "command_knowledge": validation["command_knowledge"],
        "commands": validation["commands"],
        "runtime": {
            "siril": siril["fingerprint"],
            "version": siril["version"],
            "offline": offline,
            "network": validation["network"],
            "container_validation": container_validation,
            "adapters": validation.get("adapters", []),
            **({"starnet": starnet_runtime} if starnet_runtime is not None else {}),
            **({'deepsnr': deepsnr_runtime} if deepsnr_runtime is not None else {}),
            **(
                {"sirilpy_bridge": sirilpy_runtime}
                if sirilpy_runtime is not None
                else {}
            ),
        },
        "invocation": command,
        "timeout_seconds": timeout,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "log": {**fingerprint(log_path, include_path=False), "path": relative_session_path(session, log_path)},
        "log_diagnostics": log_diagnostics,
        "outputs": output_records,
        "output_validations": validations,
        "input_unchanged": input_unchanged,
        "source_unchanged": source_unchanged,
        "runtime_bindings_unchanged": runtime_bindings_unchanged,
        "statistics_samples": artifacts.parse_statistics_samples(output),
        "autostretch_mtf": artifacts.parse_autostretch_mtf(output),
        **processing,
        "processing_checks": processing_checks,
    }
    if protocol == 'stars.separate' and not success:
        rejected, quarantine_errors = _quarantine_separation_outputs(session, run_id, expected)
        receipt.update(outputs=[], primary_output=None, failure=separation_failure,
                       rejected_outputs=rejected, quarantine_errors=quarantine_errors)
    if deepsnr_runtime is not None and not success:
        rejected, quarantine_errors = _quarantine_separation_outputs(session, run_id, expected)
        receipt.update(outputs=[], primary_output=None, rejected_outputs=rejected, quarantine_errors=quarantine_errors)
    if protocol == 'stretch' and receipt.get('hdr_transfer'):
        from deep_sky_siril_stretch_trials import enabled as archive_enabled, create as archive_trial
        if archive_enabled(payload):
            try:
                receipt['trial_artifacts'] = archive_trial(session, receipt)
            except (ContractError, OSError, ValueError) as exc:
                success = False
                receipt['status'] = 'failed'
                processing_checks.update(status='failed', reason='Stage 7 trial archiving failed: '+str(exc))
    atomic_write_json(receipt_path, receipt)
    session_state._append_run(session / "manifest.json", manifest, receipt_path, receipt)
    if success:
        if protocol == "stars.separate":
            # Commit scientific outputs and receipt before deleting temporary handoffs.
            work = session / "runtime" / "starnet" / run_id
            if work.is_dir() and not work.is_symlink():
                shutil.rmtree(work)
        try:
            _try_generate_run_metrics(session, run_id, protocol, source, expected, primary,
                                      background_validation=processing_checks.get('background_validation'),
                                      neutral_validation=processing_checks.get('color_calibration', {}).get('neutral_validation'))
        except Exception as exc:
            warning = _metric_report_warning(run_id, "metrics_generation_failed", f"{type(exc).__name__}: {exc}")
            try:
                _write_metric_evidence(session, run_id, "metrics-error.json", {
                    "schema": f"{SCHEMA_PREFIX}.metric-error.v1", "run_id": run_id,
                    "run_receipt_sha256": sha256_file(receipt_path), "created_at": utc_now(),
                    "warning": warning,
                })
            except Exception as record_error:
                warning["persistence_error"] = f"{type(record_error).__name__}: {record_error}"
            return {**receipt, "report_warnings": [warning]}
    return _with_metric_report_warnings(session, receipt) if success else receipt


def _try_generate_run_metrics(session: Path, run_id: str, protocol: str, source: Path,
                              expected_paths: Sequence[Path], primary: Path | None, *, background_validation=None, neutral_validation=None) -> None:
    import deep_sky_siril_metrics as metrics_mod
    stars_tsv = next((p for p in expected_paths if p.suffix == ".tsv" and "star" in p.name), None)
    # input.inspect reports the scientific input, never the JPEG quantization as source clipping.
    candidate = primary or source
    metrics_mod.generate_metric_report(run_id=run_id, protocol=protocol, candidate_path=candidate,
        parent_path=source, stars_tsv_path=stars_tsv,
        background_validation=background_validation, neutral_validation=neutral_validation,
        output_path=session_path(session, session / "reports" / run_id / "metrics.json", allowed_roots=("reports",)))


def _metric_report_warning(run_id: str, code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "report_path": f"reports/{run_id}/metrics.json",
            "recovery_hint": "Use deep_sky_siril_metrics.py --session SESSION --run-id " + run_id
                             + "; recovery requires a current-policy session with intact evidence."}


def _write_metric_evidence(session: Path, run_id: str, name: str, record: dict[str, Any]) -> Path:
    """Publish complete supplemental evidence without replacing an existing file."""
    path = session_path(session, session / "reports" / run_id / name, allowed_roots=("reports",))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, prefix=f".{name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(canonical_json(record) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _fsync_directory(path.parent)
    except FileExistsError as exc:
        raise ContractError("write_conflict", f"Metric evidence already exists: {path}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def confirm_input(
    session_value: str, run_id: str, *, input_state: str | None,
    channel_mode: str | None, state_evidence: Sequence[str],
) -> dict[str, Any]:
    """Commit explicit post-inspection declarations while preserving frozen evidence."""
    session, payload, manifest = session_state.load_session(session_value)
    if not session_state.execution_policy_is_current(payload):
        raise ContractError("legacy_session_read_only", "Legacy sessions cannot confirm input context")
    if manifest.get("finalization") is not None:
        raise ContractError("input_confirmation_invalid", "Finalized sessions cannot confirm input context")
    probe = load_json(session / "reports/tool-probe.json")
    tooling.verify_tool_fingerprint(probe.get("tools", {}).get("siril_cli", {}).get("fingerprint"), name="siril_cli")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ContractError("input_confirmation_invalid", "Inspect run ID must be NNN-name")
    fields = {}
    for key, value in (("input_state", input_state), ("channel_mode", channel_mode)):
        if value is None:
            continue
        original = payload["context"].get(key, "unknown")
        if original == "unknown":
            fields[key] = value
        elif original != value:
            raise ContractError("input_evidence_conflict", f"Confirmation conflicts with known {key}")
    evidence = [" ".join(item.split()) for item in state_evidence]
    context = session_state.confirmed_input_context(payload, fields, evidence)
    path = session_path(session, session_state.INPUT_CONFIRMATION_PATH, allowed_roots=("reports",))
    reused = path.exists()
    existing = session_state.read_input_confirmation(path)[0] if reused else None
    if existing is not None:
        session_state.validate_input_confirmation(session, payload, existing)
    if manifest.get("input_confirmation") is not None:
        if (existing["run_id"] != run_id or existing["confirmed_fields"] != fields
            or existing["state_evidence"] != evidence):
            raise ContractError("input_evidence_conflict", "Committed input confirmation cannot be replaced")
        _load_run(session, run_id)
        _validate_review(session, str(session / existing["review"]["path"]), run_id)
        return {"status": "success", "context": context,
                "confirmation": manifest["input_confirmation"], "reused": True}
    entries = manifest.get("runs")
    if not isinstance(entries, list):
        raise ContractError("manifest_invalid", "Manifest run index is invalid")
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or RUN_ID_PATTERN.fullmatch(entry["id"]) is None:
            raise ContractError("manifest_invalid", "Manifest run entry is invalid")
        receipt_path = session_path(session, f"runs/{entry['id']}.json", allowed_roots=("runs",))
        receipt = load_json(receipt_path)
        if (entry.get("receipt") != receipt_path.name or entry.get("receipt_sha256") != sha256_file(receipt_path)
            or receipt.get("id") != entry["id"] or receipt.get("status") != entry.get("status")):
            raise ContractError("manifest_invalid", "Manifest inspect binding changed")
        if receipt.get("protocol") != "input.inspect" or receipt.get("source_path") != "@input":
            raise ContractError("input_confirmation_invalid", "Only input.inspect may precede input confirmation")
    if not any(entry["id"] == run_id and entry.get("status") == "success" for entry in entries):
        raise ContractError("input_confirmation_invalid", "Confirmation requires a successful indexed input inspection")
    run_path, run = _load_run(session, run_id)
    if run.get("protocol") != "input.inspect" or run.get("source_path") != "@input":
        raise ContractError("input_confirmation_invalid", "Confirmation requires immutable @input inspection")
    reviews = [p for p in (session / "reviews").glob("*.json") if load_json(p).get("run_id") == run_id]
    if len(reviews) != 1:
        raise ContractError("review_required", "Confirmation requires exactly one accepted input inspection review")
    review, _ = _validate_review(session, str(reviews[0]), run_id)
    if review["verdict"] != "accept":
        raise ContractError("review_required", "Confirmation requires an accepted input inspection review")
    record = {
        "schema": f"{SCHEMA_PREFIX}.input-confirmation.v1",
        "session_sha256": payload["session_sha256"],
        "input": {key: payload["input"][key] for key in ("sha256", "size")},
        "run_id": run_id,
        "run_receipt": {"path": relative_session_path(session, run_path), **fingerprint(run_path, include_path=False)},
        "review": {"path": relative_session_path(session, reviews[0]), **fingerprint(reviews[0], include_path=False)},
        "confirmed_fields": fields, "state_evidence": evidence,
        "created_at": existing["created_at"] if existing else utc_now(),
    }
    session_state.validate_input_confirmation(session, payload, record)
    if existing is not None and existing != record:
        raise ContractError("input_evidence_conflict", "Uncommitted input confirmation conflicts with this request")
    _, after_payload, after_manifest = session_state.load_session(session)
    if after_payload != payload or after_manifest != manifest:
        raise ContractError("input_confirmation_drift", "Session changed during input confirmation")
    if not reused:
        _write_metric_evidence(session, "", path.name, record)
    published, observed = session_state.read_input_confirmation(path)
    if published != record:
        raise ContractError("input_confirmation_drift", "Input confirmation changed during publication")
    session_state.validate_input_confirmation(session, payload, published)
    manifest["input_confirmation"] = {"path": session_state.INPUT_CONFIRMATION_PATH, **observed}
    manifest["updated_at"] = utc_now()
    atomic_write_json(session / "manifest.json", manifest)
    return {"status": "success", "context": context,
            "confirmation": manifest["input_confirmation"], "reused": reused}


def _metric_recovery_inputs(session_value: str | Path, run_id: str, *, allow_legacy: bool = False) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Reuse immutable replay verification; never execute the recorded script."""
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ContractError("script_name_invalid", "Run ID must be NNN-name")
    session, payload, manifest = session_state.load_session(session_value)
    payload = session_state.effective_session_payload(session, payload, manifest)
    if not allow_legacy and not session_state.execution_policy_is_current(payload):
        raise ContractError("legacy_session_read_only", "Legacy sessions allow receipt replay/finalize only, not metric recovery")
    run_path, receipt = _load_run(session, run_id)
    source = Path(payload["input"]["path"]) if receipt["source_path"] == "@input" else session_path(
        session, session / receipt["source_path"], must_exist=True, allowed_roots=("artifacts",))
    script = session_path(session, session / receipt["script"]["path"], must_exist=True, allowed_roots=("scripts",))
    expected = [session_path(session, session / record["path"], must_exist=True) for record in receipt["outputs"]]
    primary_value = receipt.get("primary_output")
    primary = session_path(session, session / primary_value, must_exist=True) if primary_value else None
    branch = receipt.get("branch_binding", {})
    _replay_run_receipt(session=session, payload=payload, manifest=manifest, receipt_path=run_path,
        script=script, provenance=script.with_suffix(".provenance.json"), protocol=receipt["protocol"],
        source_value=str(source), expected_values=[str(path) for path in expected],
        primary_output_value=str(primary) if primary else None,
        separation_run=branch.get("separation_run"), stretch_run=branch.get("stretch_run"))
    candidate = primary or source
    stars = next((p for p in expected if p.suffix == ".tsv" and "star" in p.name), None)
    background = receipt.get("processing_checks", {}).get("background_validation")
    neutral = receipt.get('processing_checks', {}).get('color_calibration', {}).get('neutral_validation')
    paths = {"parent": source, "candidate": candidate}
    if neutral is not None:
        paths['neutral_regions'] = session_path(session, receipt['color_calibration']['background_regions'], must_exist=True, allowed_roots=('reports',))
    if stars is not None:
        paths["stars_tsv"] = stars
    if background is not None:
        paths["background_validation"] = session_path(session, session / background["path"], must_exist=True)
    binding = {"run_receipt_sha256": sha256_file(run_path),
               "inputs": {role: fingerprint(path) for role, path in paths.items()}}
    arguments = {"run_id": run_id, "protocol": receipt["protocol"], "candidate_path": candidate,
                 "parent_path": source, "stars_tsv_path": stars, "background_validation": background,
                 'neutral_validation': neutral}
    return session, binding, arguments


def _validate_metric_recovery(session: Path, run_id: str, path: Path) -> dict[str, Any]:
    import deep_sky_siril_metrics as metrics_mod
    expected_path = session / "reports" / run_id / "metrics-recovery.json"
    if path != expected_path:
        raise ContractError("skill_output_invalid", "Metric recovery belongs to another run")
    report = load_json(path)
    _, binding, arguments = _metric_recovery_inputs(session, run_id, allow_legacy=True)
    recovery = report.get("recovery_binding", {})
    gates = report.get("gate_evaluations")
    candidate, parent = report.get("candidate"), report.get("parent")
    if (report.get("schema") != metrics_mod.METRIC_REPORT_SCHEMA or report.get("run_id") != run_id
        or report.get("protocol") != arguments["protocol"] or not isinstance(recovery, dict)
        or set(recovery) != {"run_receipt_sha256", "inputs", "created_at", "report_sha256"}
        or not isinstance(recovery.get("created_at"), str) or not recovery["created_at"]
        or {key: recovery.get(key) for key in binding} != binding
        or recovery.get("report_sha256") != stable_hash({k: v for k, v in report.items() if k != "recovery_binding"})
        or not isinstance(candidate, dict) or candidate.get("path") != str(arguments["candidate_path"])
        or not isinstance(parent, dict) or parent.get("path") != str(arguments["parent_path"])
        or report.get("background_validation") != arguments["background_validation"]
        or report.get('neutral_validation') != arguments['neutral_validation']
        or report.get("recommended_verdict") not in {"reject", "uncertain"}
        or not isinstance(gates, dict) or set(gates) != {"structure", "background", "color", "stars", "geometry"}
        or any(not isinstance(gate, dict) or gate.get("verdict") not in {"fail", "uncertain"} for gate in gates.values())):
        raise ContractError("skill_output_invalid", "Metric recovery binding or report is invalid")
    return report


def recover_run_metrics(session_value: str, run_id: str) -> dict[str, Any]:
    """Add a bound report; preserve original execution and all existing evidence."""
    import deep_sky_siril_metrics as metrics_mod
    session, binding, arguments = _metric_recovery_inputs(session_value, run_id)
    path = session_path(session, session / "reports" / run_id / "metrics-recovery.json", allowed_roots=("reports",))
    reused = path.exists()
    if reused:
        _validate_metric_recovery(session, run_id, path)
    else:
        report = metrics_mod.generate_metric_report(**arguments)
        _, after, _ = _metric_recovery_inputs(session, run_id)
        if after != binding:
            raise ContractError("skill_output_invalid", "Metric recovery evidence changed during measurement")
        report["recovery_binding"] = {**binding, "created_at": utc_now(), "report_sha256": stable_hash(report)}
        _write_metric_evidence(session, run_id, path.name, report)
    return {"status": "success", "run_id": run_id, "reused": reused,
            "report": {**fingerprint(path, include_path=False), "path": relative_session_path(session, path)}}


def _with_metric_report_warnings(session: Path, receipt: dict[str, Any]) -> dict[str, Any]:
    """Inspect optional reporting state without changing immutable replay evidence."""
    run_id = receipt["id"]
    try:
        path = session_path(session, session / "reports" / run_id / "metrics.json", must_exist=True)
        report = load_json(path)
        if report.get("run_id") != run_id or report.get("protocol") != receipt["protocol"]:
            raise ContractError("skill_output_invalid", "Metric report belongs to another run")
        errors = [f"{role}: {record['metrics']['error']}" for role in ("candidate", "parent")
                  if isinstance(record := report.get(role), dict)
                  and isinstance(record.get("metrics"), dict) and record["metrics"].get("error")]
        if not errors:
            return receipt
        warning = _metric_report_warning(run_id, "metrics_measurement_unavailable", "; ".join(errors))
    except Exception as exc:
        warning = _metric_report_warning(run_id, "metrics_report_unavailable", f"{type(exc).__name__}: {exc}")
    recovery_path = session / "reports" / run_id / "metrics-recovery.json"
    try:
        if recovery_path.exists() or recovery_path.is_symlink():
            recovery_path = session_path(session, recovery_path, must_exist=True, allowed_roots=("reports",))
            _validate_metric_recovery(session, run_id, recovery_path)
            warning["supplemental_report"] = {**fingerprint(recovery_path, include_path=False),
                                               "path": relative_session_path(session, recovery_path)}
    except Exception as exc:
        warning["recovery_error"] = f"{type(exc).__name__}: {exc}"
    return {**receipt, "report_warnings": [warning]}


def _validate_selection(selection: dict[str, Any]) -> None:
    base_fields = {
        "schema",
        "status",
        "selected_runs",
        "review_receipts",
        "limitations",
        "stars_required",
        "output_contains_stars",
    }
    if selection.get("schema") != f"{SCHEMA_PREFIX}.final-selection.v1":
        raise ContractError("skill_output_invalid", "Final selection schema is invalid")
    status = selection.get("status")
    if status not in {"success", "partial_success", "review_required", "failed"}:
        raise ContractError("skill_output_invalid", "Final selection status is invalid")
    expected_fields = base_fields | ({"error"} if status == "failed" else {"candidate_image"})
    if set(selection) != expected_fields:
        raise ContractError(
            "skill_output_invalid",
            "Final selection has missing or extra fields",
        )
    for key in ("selected_runs", "review_receipts", "limitations"):
        if not isinstance(selection.get(key), list) or len(selection[key]) > 32:
            raise ContractError("skill_output_invalid", f"Final selection {key} is invalid")
    run_ids = selection["selected_runs"]
    if any(
        not isinstance(value, str) or RUN_ID_PATTERN.fullmatch(value) is None
        for value in run_ids
    ) or len(set(run_ids)) != len(run_ids):
        raise ContractError(
            "skill_output_invalid",
            "Selected run IDs are invalid or duplicated",
        )
    if not isinstance(selection.get("stars_required"), bool):
        raise ContractError("skill_output_invalid", "stars_required must be boolean")
    contains_stars = selection.get("output_contains_stars")
    if contains_stars is not None and not isinstance(contains_stars, bool):
        raise ContractError("skill_output_invalid", "output_contains_stars is invalid")
    review_receipts = selection["review_receipts"]
    if any(
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 300
        for value in review_receipts
    ) or len(set(review_receipts)) != len(review_receipts):
        raise ContractError(
            "skill_output_invalid",
            "Review receipt paths are invalid or duplicated",
        )
    for limitation in selection["limitations"]:
        if not isinstance(limitation, dict) or set(limitation) != {"code", "message"}:
            raise ContractError("skill_output_invalid", "Limitation entries are invalid")
        if (
            not isinstance(limitation["code"], str)
            or LIMITATION_CODE_PATTERN.fullmatch(limitation["code"]) is None
        ):
            raise ContractError("skill_output_invalid", "Limitation code is invalid")
        if (
            not isinstance(limitation["message"], str)
            or not limitation["message"].strip()
            or len(limitation["message"]) > 500
        ):
            raise ContractError("skill_output_invalid", "Limitation message is invalid")
    if status == "partial_success" and not selection["limitations"]:
        raise ContractError(
            "skill_output_invalid",
            "partial_success requires a concrete limitation",
        )
    if status != "failed":
        candidate = selection.get("candidate_image")
        if (
            not isinstance(candidate, str)
            or not candidate.strip()
            or len(candidate) > 300
        ):
            raise ContractError(
                "skill_output_invalid",
                "Non-failed selection requires candidate_image",
            )
        return

    error = selection.get("error")
    if not isinstance(error, dict):
        raise ContractError(
            "skill_output_invalid",
            "Failed selection requires a structured error",
        )
    allowed_error_fields = {"code", "message", "missing_dependencies"}
    if (
        set(error) - allowed_error_fields
        or not {"code", "message"} <= set(error)
        or error.get("code")
        not in {
            "runtime_dependency_missing",
            "skill_command_failed",
            "skill_output_missing",
            "skill_output_invalid",
        }
        or not isinstance(error.get("message"), str)
        or not error["message"].strip()
        or len(error["message"]) > 1000
    ):
        raise ContractError("skill_output_invalid", "Failed selection error is invalid")
    missing = error.get("missing_dependencies", [])
    if (
        not isinstance(missing, list)
        or len(missing) > 32
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 200
            for item in missing
        )
    ):
        raise ContractError(
            "skill_output_invalid",
            "Failed selection dependencies are invalid",
        )


def _load_run(session: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    path = session_path(
        session,
        session / "runs" / f"{run_id}.json",
        must_exist=True,
        allowed_roots=("runs",),
    )
    payload = load_json(path)
    session_payload = load_json(session / "session.json")
    manifest = load_json(session / "manifest.json")
    runs = manifest.get("runs")
    matching = (
        [
            entry
            for entry in runs
            if isinstance(entry, dict) and entry.get("id") == run_id
        ]
        if isinstance(runs, list)
        else []
    )
    if (
        len(matching) != 1
        or matching[0].get("status") != "success"
        or matching[0].get("receipt") != path.name
        or matching[0].get("receipt_sha256") != sha256_file(path)
        or payload.get("schema") != f"{SCHEMA_PREFIX}.run-receipt.v1"
        or payload.get("contract_version") != CONTRACT_VERSION
        or payload.get("id") != run_id
        or not session_state._receipt_bindings_unchanged(
            session,
            session_payload,
            payload,
        )
    ):
        raise ContractError("skill_output_invalid", f"Run receipt is invalid: {run_id}")
    if payload.get("status") != "success":
        raise ContractError(
            "skill_output_invalid",
            f"Selected run did not succeed: {run_id}",
        )
    return path, payload


def review_materials_for_run(session_value: str, run_id: str) -> dict[str, Any]:
    """Derive the review skeleton for one run without writing anything.

    The Agent still opens every image and writes its own verdict and notes. This only
    removes the mechanical part of authoring a review: binding the run receipt plus every
    existing candidate, parent preview, report and diagnostic the protocol produced.
    """
    session, _payload, _manifest = session_state.load_session(session_value)
    run_path, run = _load_run(session, run_id)
    if run.get('chroma_denoise'):
        from deep_sky_siril_chroma import review_paths
        paths = review_paths(session, run)
        paths[str(run_path.relative_to(session))] = run_path
        return _review_skeleton(run_path, run, [{'path': ref, 'sha256': sha256_file(path)} for ref, path in paths.items()])
    if run.get('dualband_transfer'):
        from deep_sky_siril_dualband import review_paths
        paths = review_paths(session, run)
        return _review_skeleton(run_path, run, [{'path': ref, 'sha256': sha256_file(path)} for ref, path in paths.items()])
    if run.get('local_contrast_guard'):
        paths = _local_contrast_review_paths(session, run)
        materials = [{'path': reference, 'sha256': sha256_file(path)} for reference, path in sorted(paths.items())]
        return _review_skeleton(run_path, run, materials)
    if run.get('adaptive_parameters'):
        from deep_sky_siril_stretch_v2 import review_paths
        paths = review_paths(session, run['adaptive_parameters'])
        if paths:
            paths[run_path.relative_to(session).as_posix()] = run_path
            for record in run['outputs']:
                if Path(record['path']).suffix.lower() in DISPLAY_IMAGE_SUFFIXES:
                    paths[record['path']] = session_path(session, record['path'], must_exist=True)
            return _review_skeleton(run_path, run, [{'path': ref, 'sha256': sha256_file(path)} for ref, path in paths.items()])
    if run.get('hdr_transfer', {}).get('protection') and run.get('protocol') == 'stretch':
        from deep_sky_siril_hdr import protection_review_paths
        paths = protection_review_paths(session, run['hdr_transfer'], run)
        for ref in run['hdr_transfer']['previews'].values():
            paths[ref] = session_path(session, ref, must_exist=True)
        paths[run_path.relative_to(session).as_posix()] = run_path
        return _review_skeleton(run_path, run, [{'path': ref, 'sha256': sha256_file(path)} for ref, path in paths.items()])
    if run.get('protocol') == 'stars.recompose':
        paths = _recomposition_review_paths(session, run)
        materials = [{'path': reference, 'sha256': sha256_file(path)} for reference, path in paths.items()]
        materials.append(_recomposition_metric_material(session, run))
        return _review_skeleton(run_path, run, sorted(materials, key=lambda record: record['path']))
    from deep_sky_siril_processing import script_commands
    script = session_path(session, run["script"]["path"], must_exist=True, allowed_roots=("scripts",))
    tokens = script_commands(script.read_text(encoding="utf-8"))
    from deep_sky_siril_validation import _normalized_write
    parents: list[Path] = []
    for row in tokens:
        if row[0] != "load" or len(row) != 2:
            continue
        target = _absolute(row[1])
        if target not in parents:
            parents.append(target)
    if run.get('processing_checks', {}).get('closure', {}).get('warnings') and run.get('source_path') != '@input':
        parent = session_path(session, run['source_path'], must_exist=True, allowed_roots=('artifacts',))
        if parent not in parents:
            parents.append(parent)
    if run.get("protocol") == "delivery.render":
        previews = _delivery_parent_preview_paths(session, run)
        if not previews:
            raise ContractError("skill_output_invalid", "Final review requires a verified parent preview")
        parents.extend(sorted(previews))
    candidates: list[Path] = []
    for record in run.get("outputs", []):
        if isinstance(record, dict) and isinstance(record.get("path"), str):
            candidate = session_path(session, session / record["path"], must_exist=True)
            if candidate not in candidates:
                candidates.append(candidate)
    for preview in _adjustment_preview_paths(session, run):
        if preview not in candidates:
            candidates.append(preview)
    materials: list[dict[str, str]] = []
    seen: set[Path] = set()
    for target in [*candidates, *parents]:
        if target in seen or target.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        seen.add(target)
        # The scientific parent is the immutable @input master and lives outside the session.
        reference = (relative_session_path(session, target)
                     if target.is_relative_to(session) else "@input")
        materials.append({"path": reference, "sha256": sha256_file(target)})
    if run.get('local_saturation') or run.get('color_curve') or run.get('local_tone'):
        materials.append({'path': relative_session_path(session, run_path), 'sha256': sha256_file(run_path)})
    reports = []
    if run.get('local_tone'):
        reports.append({key: run['local_tone']['regions'][key] for key in ('path', 'sha256')})
    checks = run.get("processing_checks") or {}
    curve = (run.get('color_curve') or run.get('local_saturation', {})).get('curve_parameters')
    if curve:
        from deep_sky_siril_stretch import bound_file
        parameter_path = bound_file(session, curve['report'])
        reports.extend({key: curve[role][key] for key in ('path', 'sha256')}
                       for role in ('report', 'regions', 'configuration'))
        for trial in load_json(parameter_path)['trials']:
            reports.extend({key: trial[role][key] for key in ('path', 'sha256')} for role in ('script', 'log'))
    if run.get('color_calibration', {}).get('policy') == 'scientific_native_flow_v2':
        for reference in (relative_session_path(session, run_path), run['color_calibration']['background_regions']):
            report = session_path(session, reference, must_exist=True, allowed_roots=('runs', 'reports'))
            reports.append({'path': reference, 'sha256': sha256_file(report)})
    if run.get('restoration_validation'):
        reports.append({'path': relative_session_path(session, run_path), 'sha256': sha256_file(run_path)})
    for record in checks.get('deconvolution', {}).get('spatial_reports', {}).get('reports', {}).values():
        reports.append({'path': record['path'], 'sha256': record['sha256']})
    for key in ("background_validation", "hdr", "star_separation", "color_calibration"):
        record = checks.get(key)
        path = record.get("path") if isinstance(record, dict) else None
        if isinstance(path, str):
            report = session_path(session, session / path, must_exist=True, allowed_roots=("reports",))
            reports.append({"path": relative_session_path(session, report), "sha256": sha256_file(report)})
    closure = checks.get('closure', {})
    histogram = checks.get('delivery_export', {}).get('histogram_gate', {})
    if closure.get('warnings') or histogram.get('warnings'):
        reports.append({'path': relative_session_path(session, run_path), 'sha256': sha256_file(run_path)})
    if reports:
        metrics_report = session / "reports" / run_id / "metrics.json"
        if metrics_report.is_file():
            reports.append({"path": relative_session_path(session, metrics_report),
                            "sha256": sha256_file(metrics_report)})
    for material in reports:
        if material not in materials:
            materials.append(material)
    return _review_skeleton(run_path, run, materials)


def _local_contrast_review_paths(session: Path, run: dict[str, Any]) -> dict[str, Path]:
    guard = run['local_contrast_guard']
    references = {run['source_path'], run['primary_output'], f"runs/{run['id']}.json",
                  guard['regions']['path'], *guard['previews'].values()}
    references.update(record['path'] for record in run['outputs'])
    if guard.get('parameters'):
        from deep_sky_siril_local_contrast import parameter_review_paths
        references.update(parameter_review_paths(session, guard['parameters']['path']))
    if guard.get('subject_mask'):
        from deep_sky_siril_subject_mask import review_paths as subject_paths
        references.update(subject_paths(session,session/run['source_path'],guard['subject_mask']))
    return {reference: session_path(session, reference, must_exist=True) for reference in references}


def _recomposition_review_paths(session: Path, run: dict[str, Any]) -> dict[str, Path]:
    from deep_sky_siril_processing import script_commands
    payload = session_state.load_session(session)[1]
    input_path = _absolute(payload['input']['path'])
    paths: dict[str, Path] = {}
    try:
        for record in run['outputs']:
            if Path(record['path']).suffix.lower() in IMAGE_SUFFIXES:
                paths[record['path']] = session_path(session, record['path'], must_exist=True,
                                                     allowed_roots=('artifacts', 'previews'))
        if not any(path.suffix.lower() in DISPLAY_IMAGE_SUFFIXES for path in paths.values()):
            raise ContractError('skill_output_invalid', 'Stage 9 review requires a declared candidate preview')
        script = session_path(session, run['script']['path'], must_exist=True, allowed_roots=('scripts',))
        auxiliary = None
        if run.get('branch_binding', {}).get('contour_report'):
            from deep_sky_star_reduction import bound_file
            contour = load_json(bound_file(session, run['branch_binding']['contour_report']))
            auxiliary = bound_file(session, contour['weights'])
        for row in script_commands(script.read_text(encoding='utf-8')):
            if row[0] == 'load' and len(row) == 2:
                target = _absolute(row[1])
                if target == auxiliary:
                    continue
                if target == input_path:
                    paths['@input'] = input_path
                else:
                    target = session_path(session, target, must_exist=True, allowed_roots=('artifacts',))
                    paths[relative_session_path(session, target)] = target
        required = [run['source_path'], run['primary_output'],
                    *(run['branch_binding'][role] for role in ('full_baseline', 'original_starless', 'display_stars'))]
        for reference in required:
            paths[reference] = (input_path if reference == '@input' else
                                session_path(session, reference, must_exist=True, allowed_roots=('artifacts',)))
        reference = f"runs/{run['id']}.json"
        paths[reference] = session_path(session, reference, must_exist=True, allowed_roots=('runs',))
    except (OSError, KeyError) as exc:
        raise ContractError('skill_output_invalid', f'Stage 9 review material is missing: {exc}') from exc
    return dict(sorted(paths.items()))


def _recomposition_metric_material(session: Path, run: dict[str, Any], inspected: set[str] | None = None) -> dict[str, str]:
    from deep_sky_siril_metrics import METRIC_REPORT_SCHEMA
    from siril_manual_bundle import BundleError, strict_json_bytes
    payload = session_state.load_session(session)[1]
    parent = (Path(payload['input']['path']) if run['source_path'] == '@input' else session / run['source_path'])
    candidate = session / run['primary_output']
    errors = []
    for name in ('metrics.json', 'metrics-recovery.json'):
        reference = f"reports/{run['id']}/{name}"
        if inspected is not None and reference not in inspected:
            errors.append(f'{reference}: not inspected')
            continue
        path = session_path(session, reference, allowed_roots=('reports',))
        if not path.exists():
            errors.append(f'{reference}: missing')
            continue
        before = fingerprint(path, include_path=False)
        try:
            report = strict_json_bytes(path.read_bytes(), document=str(path))
        except BundleError as exc:
            report = None
            errors.append(f'{reference}: {exc}')
        except OSError as exc:
            raise ContractError('skill_output_invalid', f'Stage 9 metric report cannot be read: {reference}: {exc}') from exc
        if fingerprint(path, include_path=False) != before:
            raise ContractError('skill_output_invalid', f'Stage 9 metric report changed while reading: {reference}')
        try:
            if name == 'metrics-recovery.json':
                _validate_metric_recovery(session, run['id'], path)
            elif (not isinstance(report, dict) or report.get('schema') != METRIC_REPORT_SCHEMA
                  or report.get('run_id') != run['id'] or report.get('protocol') != run['protocol']
                  or 'recovery_binding' in report
                  or any(not isinstance(report.get(role), dict)
                         or report[role].get('path') != str(expected)
                         or not isinstance(report[role].get('metrics'), dict)
                         for role, expected in (('candidate', candidate), ('parent', parent)))):
                raise ContractError('skill_output_invalid', f'Stage 9 metric report binding is invalid: {reference}')
        except ContractError as exc:
            errors.append(str(exc))
            continue
        if report is not None:
            if fingerprint(path, include_path=False) != before:
                raise ContractError('skill_output_invalid', f'Stage 9 metric report changed while validating: {reference}')
            return {'path': reference, 'sha256': before['sha256']}
    hint = _metric_report_warning(run['id'], 'metrics_report_unavailable', '')['recovery_hint']
    raise ContractError('skill_output_invalid', 'Stage 9 review requires a valid metrics report: '
                        + '; '.join(errors) + '. ' + hint)


def _color_protection_skeleton(run):
    evidence = run.get('processing_checks', {}).get('color_protection')
    if evidence is None:
        return None
    return {'regions_path': evidence['regions_path'],
            'structures': {name: {'region_ids': [], 'not_applicable_reason': ''}
                           for name in ('diffuse_red', 'reflection_blue')},
            'regions': [{'id': r['id'], 'continuity': '', 'color': '', 'weak_edges': '', 'assessment': None}
                        for r in evidence['regions']]}


def _validate_color_protection(run, review):
    expected = _color_protection_skeleton(run)
    block = review.get('color_protection_assessment')
    if expected is None:
        if block is not None:
            raise ContractError('skill_output_invalid', 'Color protection assessment has no bound diagnostics')
        return
    def text(value):
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000
    if (not isinstance(block, dict) or set(block) != set(expected) or block['regions_path'] != expected['regions_path']
        or not isinstance(block['structures'], dict) or set(block['structures']) != set(expected['structures'])
        or not isinstance(block['regions'], list) or len(block['regions']) != len(expected['regions'])):
        raise ContractError('skill_output_invalid', 'Review each color protection region and red/blue applicability')
    ids = {r['id'] for r in expected['regions']}
    for value in block['structures'].values():
        if (not isinstance(value, dict) or set(value) != {'region_ids', 'not_applicable_reason'}
            or not isinstance(value['region_ids'], list) or any(not isinstance(v,str) or v not in ids for v in value['region_ids'])
            or len(set(value['region_ids'])) != len(value['region_ids'])
            or (not value['region_ids'] and not text(value['not_applicable_reason']))):
            raise ContractError('skill_output_invalid', 'Declare observed structure IDs or a reason it is not applicable; do not infer spectral origin')
    for row, required in zip(block['regions'], expected['regions']):
        if (not isinstance(row, dict) or set(row) != set(required) or row['id'] != required['id']
            or any(not text(row[k]) for k in ('continuity','color','weak_edges'))
            or row['assessment'] not in ('acceptable','degraded','uncertain')
            or (review['verdict'] == 'accept' and row['assessment'] != 'acceptable')):
            raise ContractError('skill_output_invalid', 'Neutral sky cannot offset subject color, continuity or weak-edge regression')
    if expected['regions_path'] not in {r['path'] for r in review['inspected_materials']}:
        raise ContractError('skill_output_invalid', 'Inspect the source-bound protection region contract')


def _review_skeleton(run_path: Path, run: dict[str, Any], materials: list[dict[str, str]]) -> dict[str, Any]:
    color = None
    if run.get('color_evaluation'):
        from deep_sky_siril_color import review_paths as color_paths, assessment_skeleton as color_skeleton
        materials = list({item['path']: item for item in [*materials,
            *[{'path': name, 'sha256': sha256_file(path)} for name, path in color_paths(run_path.parent.parent, run).items()]]}.values())
        color = color_skeleton(run_path.parent.parent, run)
    tone = None
    if run.get('tone_proposal'):
        from deep_sky_siril_tone import review_paths as tone_paths, assessment_skeleton as tone_skeleton
        materials = sorted({item['path']: item for item in [*materials,
            *[{'path': name, 'sha256': sha256_file(path)} for name, path in tone_paths(run_path.parent.parent, run).items()]]}.values(),
            key=lambda item: item['path'])
        tone = tone_skeleton(run_path.parent.parent, run)
    recomposition = None
    if run.get('recomposition_proposal'):
        from deep_sky_star_reduction import review_paths as recomposition_paths, assessment_skeleton as recomposition_skeleton
        materials = sorted({item['path']: item for item in [*materials,
            *[{'path': name, 'sha256': sha256_file(path)} for name, path in recomposition_paths(run_path.parent.parent, run).items()]]}.values(),
            key=lambda item: item['path'])
        recomposition = recomposition_skeleton(run_path.parent.parent, run)
    denoise = None
    if run.get('chroma_denoise'):
        from deep_sky_siril_chroma import review_paths as denoise_paths, assessment_skeleton as denoise_skeleton
        materials = list({item['path']: item for item in [*materials,
            *[{'path': name, 'sha256': sha256_file(path)} for name, path in denoise_paths(run_path.parent.parent, run).items()]]}.values())
        denoise = denoise_skeleton(run_path.parent.parent, run)
    if run.get('linear_denoise') or run.get('dualband_transfer', {}).get('linear_denoise'):
        from deep_sky_siril_denoise import review_paths as denoise_paths, assessment_skeleton as denoise_skeleton
        materials = list({item['path']: item for item in [*materials,
            *[{'path': name, 'sha256': sha256_file(path)} for name, path in denoise_paths(run_path.parent.parent, run).items()]]}.values())
        denoise = denoise_skeleton(run_path.parent.parent, run)
    if run.get('stretch_visibility'):
        from deep_sky_siril_visibility import review_paths
        session = run_path.parent.parent
        materials = list({item['path']: item for item in [*materials,
            *[{'path': name, 'sha256': sha256_file(path)} for name, path in review_paths(session, run['stretch_visibility'], run).items()]]}.values())
    result = {
        "schema": f"{SCHEMA_PREFIX}.review.v1",
        "run_id": run['id'],
        "run_receipt_sha256": sha256_file(run_path),
        "protocol": run.get("protocol"),
        "inspected_materials": materials,
        "verdict": None,
        "gates": {name: None for name in ("structure", "background", "color", "stars", "geometry")},
        "notes": "",
    }

    if run.get('stretch_visibility'):
        from deep_sky_siril_visibility import assessment_skeleton
        assessment = assessment_skeleton(run_path.parent.parent, run['stretch_visibility'])
        if assessment is not None:
            from deep_sky_siril_artifact_focus import enabled as artifact_enabled, formal_skeleton
            payload = load_json(run_path.parent.parent/'session.json')
            if artifact_enabled(payload):
                block, materials = formal_skeleton(run_path.parent.parent, payload, run,
                    {'path': run_path.relative_to(run_path.parent.parent).as_posix(), **fingerprint(run_path, include_path=False)}, materials)
                assessment['artifacts'] = block
                result['inspected_materials'] = materials
            result['stretch_assessment'] = assessment
    if denoise is not None:
        result['denoise_assessment'] = denoise
    if recomposition is not None:
        result['recomposition_assessment'] = recomposition
    if tone is not None:
        result['tone_assessment'] = tone
    if color is not None:
        result['color_assessment'] = color
    protection = _color_protection_skeleton(run)
    if protection is not None:
        result['color_protection_assessment'] = protection
        path = run_path.parent.parent/protection['regions_path']
        if not any(item['path'] == protection['regions_path'] for item in materials):
            materials.append({'path': protection['regions_path'], 'sha256': sha256_file(path)})
    return result


def check_review(session: Path, run_id: str, review_value: str | None) -> dict[str, Any]:
    """Validate an existing review receipt, or report what a valid one must contain."""
    session = Path(session).expanduser().resolve()
    if review_value is None:
        return {"status": "review_required", "review": review_materials_for_run(session, run_id)}
    review, _binding = _validate_review(session, str(review_value), run_id)
    result = {"status": "review_valid", "review": review}
    if load_json(session/'session.json').get('execution_policy', {}).get('recovery_summary') == 'explicit_stage_decisions_v1':
        from deep_sky_siril_resume import decision_summary
        result['continuation'] = decision_summary(session,run_id,review,session_path(
            session,review_value,must_exist=True,allowed_roots=('reviews',)))
    return result


def _adjustment_preview_paths(session, run):
    for key in ('chroma_denoise', 'local_saturation', 'color_curve', 'local_tone'):
        if run.get(key):
            return [session / run[key]['previews'][role] for role in ('parent', 'candidate')]
    from deep_sky_siril_processing import script_commands
    script = session_path(session, run["script"]["path"], must_exist=True, allowed_roots=("scripts",))
    return [validation_ops._normalized_write("savejpg", tokens[1])
            for tokens in script_commands(script.read_text(encoding="utf-8")) if tokens[0] == "savejpg"]


def _delivery_parent_preview_paths(session: Path, run: dict[str, Any]) -> set[Path]:
    source_path = run.get("source_path")
    try:
        verified_outputs = session_state._verified_run_outputs(session)
        prior_runs = session_state._verified_success_receipts(session)
    except OSError as exc:
        raise ContractError("skill_output_invalid", f"Final review parent preview verification failed: {exc}") from exc
    parent_previews: set[Path] = set()
    for prior_run in prior_runs:
        if prior_run.get("id") == run["id"]:
            continue
        prior_outputs = prior_run.get("outputs", [])
        owns_parent = (
            source_path == "@input"
            and prior_run.get("protocol") == "input.inspect"
            and prior_run.get("source_path") == "@input"
        ) or any(
            isinstance(record, dict) and record.get("path") == source_path
            for record in prior_outputs
        )
        if not owns_parent:
            continue
        adjustment_candidate_preview = None
        if (prior_run.get("protocol") == "tone.adjust" or "color_finish" in prior_run
            or prior_run.get("local_saturation")):
            adjustment_candidate_preview = _adjustment_preview_paths(session, prior_run)[-1]
        if prior_run.get('background_validation', {}).get('display'):
            adjustment_candidate_preview = session / prior_run['background_validation']['display']['candidate_preview']
        if prior_run.get('color_calibration', {}).get('display'):
            adjustment_candidate_preview = session / prior_run['color_calibration']['display']['candidate_preview']
        if prior_run.get('restoration_validation', {}).get('display'):
            adjustment_candidate_preview = session / prior_run['restoration_validation']['display']['candidate_preview']
        if prior_run.get('chroma_denoise'):
            adjustment_candidate_preview = session / prior_run['chroma_denoise']['previews']['candidate']
        if prior_run.get('local_contrast_guard'):
            adjustment_candidate_preview = session / prior_run['local_contrast_guard']['previews']['candidate']
        for record in prior_outputs:
            if not isinstance(record, dict) or not isinstance(
                record.get("path"), str
            ):
                continue
            candidate = (session / record["path"]).resolve(strict=False)
            if (
                candidate in verified_outputs
                and candidate.suffix.lower() in DISPLAY_IMAGE_SUFFIXES
                and (adjustment_candidate_preview is None or candidate == adjustment_candidate_preview)
            ):
                parent_previews.add(candidate)
    return parent_previews


def _validate_review(
    session: Path,
    path_value: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = session_path(
        session,
        path_value,
        must_exist=True,
        allowed_roots=("reviews",),
    )
    review = load_json(path)
    required = {
        "schema",
        "run_id",
        "run_receipt_sha256",
        "protocol",
        "inspected_materials",
        "verdict",
        "gates",
        "notes",
    }
    if (
        set(review) - {"stretch_assessment", "denoise_assessment", "recomposition_assessment", "tone_assessment", "color_assessment", "color_protection_assessment"} != required
        or review.get("schema") != f"{SCHEMA_PREFIX}.review.v1"
        or review.get("run_id") != run_id
        or not isinstance(review.get("notes"), str)
        or not review["notes"].strip()
        or len(review["notes"]) > 2000
    ):
        raise ContractError(
            "skill_output_invalid",
            f"Review receipt is invalid: {path.name}",
        )
    run_path, run = _load_run(session, run_id)
    if (
        review.get("run_receipt_sha256") != sha256_file(run_path)
        or review.get("protocol") != run.get("protocol")
    ):
        raise ContractError(
            "skill_output_invalid",
            f"Review receipt is not bound to run: {run_id}",
        )
    materials = review.get("inspected_materials")
    if not isinstance(materials, list) or not 1 <= len(materials) <= 32:
        raise ContractError(
            "skill_output_invalid",
            f"Review materials are invalid: {run_id}",
        )
    seen: set[str] = set()
    image_materials: dict[str, Path] = {}
    for material in materials:
        if not isinstance(material, dict) or set(material) != {"path", "sha256"}:
            raise ContractError(
                "skill_output_invalid",
                f"Review material is invalid: {run_id}",
            )
        relative = material["path"]
        if (
            not isinstance(relative, str)
            or not relative.strip()
            or len(relative) > 300
            or relative in seen
            or not isinstance(material["sha256"], str)
            or HASH_PATTERN.fullmatch(material["sha256"]) is None
        ):
            raise ContractError(
                "skill_output_invalid",
                f"Review material is invalid or duplicated: {run_id}",
            )
        seen.add(relative)
        try:
            material_path = (Path(session_state.load_session(session)[1]['input']['path']) if relative == '@input'
                             else session_path(session, session / relative, must_exist=True))
            observed = sha256_file(material_path)
        except OSError as exc:
            raise ContractError('skill_output_invalid', f'Reviewed material is missing or unreadable: {relative}') from exc
        if material["sha256"] != observed:
            raise ContractError(
                "skill_output_invalid",
                f"Reviewed material changed: {relative}",
            )
        if material_path.suffix.lower() in IMAGE_SUFFIXES:
            image_materials[relative] = material_path.resolve()
        elif material_path.suffix.lower() == ".json":
            try:
                material_document = load_json(material_path)
            except ContractError:
                material_document = {}
            if material_path.name == "metrics-recovery.json" or "recovery_binding" in material_document:
                _validate_metric_recovery(session, run_id, material_path)
    gates = review.get("gates")
    gate_names = {"structure", "background", "color", "stars", "geometry"}
    values = {"pass", "fail", "not_applicable", "uncertain"}
    if (
        not isinstance(gates, dict)
        or set(gates) != gate_names
        or any(value not in values for value in gates.values())
    ):
        raise ContractError(
            "skill_output_invalid",
            f"Review gates are invalid: {run_id}",
        )
    if review.get("verdict") not in {"accept", "reject", "uncertain"}:
        raise ContractError(
            "skill_output_invalid",
            f"Review verdict is invalid: {run_id}",
        )
    if review["verdict"] == "accept" and any(
        value in {"fail", "uncertain"} for value in gates.values()
    ):
        raise ContractError(
            "skill_output_invalid",
            f"Accepted review contains a failed or uncertain gate: {run_id}",
        )
    checks = run.get('processing_checks', {})
    if run.get('stretch_visibility') or 'stretch_assessment' in review:
        from deep_sky_siril_visibility import validate_assessment
        validate_assessment(session, run, review)
    if run.get('stretch_visibility'):
        from deep_sky_siril_artifact_focus import enabled as artifact_enabled, formal_validate
        if artifact_enabled(load_json(session/'session.json')):
            seen.add(formal_validate(session, load_json(session/'session.json'), run, review))
        from deep_sky_siril_visibility import review_paths
        if not set(review_paths(session, run['stretch_visibility'], run)).issubset(seen):
            raise ContractError('skill_output_invalid', 'Stage 7 review requires its Stage 6 autostretch reference and matching local comparisons')
    if run.get('adaptive_parameters'):
        from deep_sky_siril_stretch_v2 import review_paths
        if not set(review_paths(session, run['adaptive_parameters'])).issubset(seen):
            raise ContractError('skill_output_invalid', 'Protected stretch review requires its parameter report, matching full replay and fixed ROI panel')
    if run.get('hdr_transfer', {}).get('protection') and run.get('protocol') == 'stretch':
        from deep_sky_siril_hdr import protection_review_paths
        if not set(protection_review_paths(session, run['hdr_transfer'], run)).issubset(seen):
            raise ContractError('skill_output_invalid', 'HDR review is missing its protected regions or same-mask full replay')
    if run.get('dualband_transfer'):
        from deep_sky_siril_dualband import review_paths
        if not set(review_paths(session, run)).issubset(seen):
            raise ContractError('skill_output_invalid', 'Dualband review requires all bound monochrome comparisons, RGB baseline, HOO pixels, local previews and reports')
        if review['verdict'] == 'accept' and any(v != 'pass' for v in gates.values()):
            raise ContractError('skill_output_invalid', 'Accepted dualband review requires all five gates to pass')
    if run.get('local_contrast_guard') and review['verdict'] == 'accept':
        missing = set(_local_contrast_review_paths(session, run)) - seen
        if missing:
            raise ContractError('skill_output_invalid', 'Local contrast review is missing bound materials: ' + ', '.join(sorted(missing)))
        local = checks.get('local_contrast', {})
        if local.get('status') != 'measured' or local.get('clipping_verdict') == 'reject':
            raise ContractError('skill_output_invalid', 'Local contrast evidence is uncertain or fails the existing scientific clipping gate')
        if gates['structure'] != 'pass' or gates['background'] != 'pass':
            raise ContractError('skill_output_invalid', 'Local contrast requires actual structure and background review, including high-frequency warnings')
    restoration = run.get('restoration_validation')
    if run.get('recomposition_proposal') or 'recomposition_assessment' in review:
        from deep_sky_star_reduction import validate_assessment as validate_recomposition_assessment
        validate_recomposition_assessment(session, run, review)
    if run.get('chroma_denoise'):
        from deep_sky_siril_chroma import validate_assessment
        validate_assessment(session, run, review)
    if run.get('linear_denoise') or run.get('dualband_transfer', {}).get('linear_denoise') or ('denoise_assessment' in review and not run.get('chroma_denoise')):
        from deep_sky_siril_denoise import validate_assessment as validate_denoise_assessment
        validate_denoise_assessment(session, run, review)
    if restoration and review['verdict'] == 'accept':
        required = {restoration['display']['parent_preview'], restoration['display']['candidate_preview'], f'runs/{run_id}.json'}
        if not required.issubset(seen):
            raise ContractError('skill_output_invalid', 'Stage 5 review requires the receipt and both matching-transfer previews')
    neutral = run.get('color_calibration', {})
    if neutral.get('policy') == 'scientific_native_flow_v2' and review['verdict'] == 'accept':
        if not {neutral['display']['parent_preview'], neutral['display']['candidate_preview']}.issubset(seen):
            raise ContractError('skill_output_invalid', 'Native neutral review must inspect both matching-transfer previews')
    fit_quality = (checks.get('color_calibration') or {}).get('fit_quality') or {}
    if (review['verdict'] == 'accept' and fit_quality.get('verdict') == 'degenerate'
            and (run.get('color_calibration') or {}).get('claim') in {'photometric', 'spectrophotometric'}):
        raise ContractError('skill_output_invalid',
                            'A degenerate photometric fit cannot be accepted; keep the uncalibrated parent and continue down the route ladder')
    closure = checks.get('closure', {})
    histogram = checks.get('delivery_export', {}).get('histogram_gate', {})
    if (run.get('protocol') == 'stars.recompose' and review['verdict'] == 'accept'
        and session_state.load_session(session)[1].get('execution_policy', {}).get('recomposition_review') in {'fixed_materials_v1', 'stellar_color_assessment_v2'}):
        missing = set(_recomposition_review_paths(session, run)) - seen
        if missing:
            raise ContractError('skill_output_invalid', 'Stage 9 review is missing required materials: '
                                + ', '.join(sorted(missing)) + f'. Use review-check --session {session} --run-id {run_id} for the full list')
        _recomposition_metric_material(session, run, seen)
    if review['verdict'] == 'accept' and (closure.get('warnings') or histogram.get('warnings')):
        required_materials = {f'runs/{run_id}.json'}
        if closure.get('policy') == 'subject_roi_residual_v2':
            required_materials.update(run['branch_binding'][role] for role in
                                      ('full_baseline', 'original_starless', 'display_stars'))
            required_materials.update((run['source_path'], run['primary_output']))
        if histogram.get('policy') == 'delivery_histogram_gate_v2':
            required_materials.update(run['delivery_export'][role] for role in ('fits', 'jpeg'))
        if not required_materials.issubset(seen):
            raise ContractError('skill_output_invalid', 'Warning review must inspect the bound receipt and actual scientific/display comparisons')
        if review['verdict'] == 'accept' and any(value != 'pass' for value in gates.values()):
            raise ContractError('skill_output_invalid', 'Accepted warning review requires all five gates to pass')
    deconvolution = run.get('processing_checks', {}).get('deconvolution', {})
    if review['verdict'] == 'accept' and deconvolution.get('policy') == 'native_star_samples_v2':
        spatial = deconvolution.get('spatial_reports', {})
        records = spatial.get('reports', {})
        if spatial.get('status') != 'bound' or set(records) != {'json', 'markdown'}:
            raise ContractError('skill_output_invalid', 'Accepted PSF v2 review requires both bound spatial reports')
        for record in records.values():
            report_path = session_path(session, record['path'], must_exist=True, allowed_roots=('reports',))
            if (record['path'] not in seen or fingerprint(report_path, include_path=False)
                != {k: record[k] for k in ('sha256', 'size')}):
                raise ContractError('skill_output_invalid', 'Accepted PSF v2 review must inspect unchanged JSON and Markdown reports')
        required_pixels = {run['source_path'], run['primary_output']} | {
            record['path'] for record in run.get('outputs', []) if Path(record['path']).suffix.lower() in IMAGE_SUFFIXES}
        if not required_pixels.issubset(image_materials):
            raise ContractError('skill_output_invalid', 'Accepted PSF v2 review must inspect actual parent and candidate pixels')
    neutral = run.get('processing_checks', {}).get('color_calibration', {}).get('neutral_validation')
    if neutral is not None:
        required = {f'runs/{run_id}.json', run['color_calibration']['background_regions']}
        if not required.issubset(seen):
            raise ContractError('skill_output_invalid', 'Neutral review must inspect the bound receipt evidence and region contract')
        if review['verdict'] == 'accept':
            candidate_images = {run['primary_output']}
            from deep_sky_siril_processing import script_commands
            commands = script_commands((session / run['script']['path']).read_text(encoding='utf-8'))
            last_load = max(i for i, row in enumerate(commands) if row[0] == 'load')
            for row in commands[last_load+1:]:
                if row[0] == 'savejpg':
                    preview = validation_ops._normalized_write('savejpg', row[1])
                    candidate_images.add(relative_session_path(session, session_path(session, preview, must_exist=True)))
            parent_images = {run['source_path']} if run['source_path'] != '@input' else set()
            if run['color_calibration'].get('policy') == 'scientific_native_flow_v2':
                parent_images.add(run['color_calibration']['display']['parent_preview'])
            for owner in session_state._verified_success_receipts(session):
                if owner['id'] == run_id:
                    continue
                owns_parent = (run['source_path'] == '@input' and owner['protocol'] == 'input.inspect'
                               and owner['source_path'] == '@input') or owner.get('primary_output') == run['source_path']
                if owns_parent:
                    parent_images.update(record['path'] for record in owner['outputs']
                                         if Path(record['path']).suffix.lower() in DISPLAY_IMAGE_SUFFIXES)
            if not candidate_images.intersection(image_materials) or not parent_images.intersection(image_materials):
                raise ContractError('skill_output_invalid', 'Accepted neutral review must inspect actual parent and candidate images')
    if run.get('protocol') == 'background.subtract' and run.get('processing_checks', {}).get('background_validation', {}).get('decision'):
        bg = run['processing_checks']['background_validation']
        if review['verdict'] == 'accept' and bg['decision'] != 'review_required':
            raise ContractError('skill_output_invalid', 'Skipped or blocked background candidates cannot be accepted')
        display = run['background_validation']['display']
        required_materials = {display['parent_preview'], display['candidate_preview'], bg['path']}
        if not required_materials.issubset(seen):
            raise ContractError('skill_output_invalid', 'Background review must inspect both matching-transfer previews and the bound background report')
    if run.get('protocol') == 'stretch' and run.get('hdr_transfer'):
        if not set(run['hdr_transfer']['previews'].values()).issubset(image_materials):
            raise ContractError('skill_output_invalid', 'HDR stretch review must inspect gentle, faint, mask and candidate JPEGs')
    if run.get('local_tone'):
        required = set(run['local_tone']['previews'].values()) | {f'runs/{run_id}.json', run['local_tone']['regions']['path']}
        if not required.issubset(seen):
            raise ContractError('skill_output_invalid', 'Local tone review must inspect parent, mask, candidate, regions and run evidence')
    if run.get('local_saturation') or run.get('color_curve'):
        color_binding = run.get('color_curve') or run['local_saturation']
        required = set(color_binding['previews'].values()) | {f'runs/{run_id}.json'}
        curve = color_binding.get('curve_parameters')
        if curve:
            from deep_sky_siril_stretch import bound_file
            report = load_json(bound_file(session, curve['report']))
            required.update(curve[key]['path'] for key in ('report', 'regions', 'configuration'))
            required.update(trial[key]['path'] for trial in report['trials'] for key in ('script', 'log'))
        if not required.issubset(seen):
            raise ContractError('skill_output_invalid', 'Local saturation review must inspect parent, mask, candidate and run evidence')
    if run.get("protocol") == "tone.adjust" or "color_finish" in run:
        previews = {relative_session_path(session, path) for path in _adjustment_preview_paths(session, run)}
        if not previews.issubset(image_materials):
            raise ContractError("skill_output_invalid", "Adjustment review must inspect both direct parent and candidate JPEGs")
    if run.get('tone_proposal') or 'tone_assessment' in review:
        from deep_sky_siril_tone import validate_assessment as validate_tone_assessment
        validate_tone_assessment(session, run, review)
    _validate_color_protection(run, review)
    if run.get('color_evaluation') or 'color_assessment' in review:
        from deep_sky_siril_color import validate_assessment as validate_color_assessment
        validate_color_assessment(session, run, review)
    if run.get("protocol") == "delivery.render":
        delivery_images = {
            str(record["path"])
            for record in run.get("outputs", [])
            if isinstance(record, dict)
            and isinstance(record.get("path"), str)
            and Path(record["path"]).suffix.lower() in IMAGE_SUFFIXES
        }
        if run.get('delivery_export'):
            required = {run['delivery_export'][role] for role in ('fits', 'jpeg')}
            if not required.issubset(image_materials):
                raise ContractError('skill_output_invalid', 'Final review must inspect the delivery JPEG and paired FITS')
            delivery_images = {run['delivery_export']['jpeg']}
        inspected_delivery = set(image_materials) & delivery_images
        parent_previews = _delivery_parent_preview_paths(session, run)
        inspected_parent = set(image_materials.values()) & parent_previews
        if not inspected_delivery or not inspected_parent:
            raise ContractError(
                "skill_output_invalid",
                "Final review must inspect the delivery JPEG and its verified parent preview",
            )
        if review["verdict"] == "accept" and any(
            value != "pass" for value in gates.values()
        ):
            raise ContractError(
                "skill_output_invalid",
                "Accepted final review requires all five gates to pass",
            )
        checks = run.get('processing_checks', {}).get('delivery_export', {})
        hist_gate = checks.get('histogram_gate', {})
        if hist_gate.get('status') == 'failed':
            reasons_str = '; '.join(hist_gate.get('reasons', []))
            if review["verdict"] == "accept":
                raise ContractError(
                    "skill_output_invalid",
                    f"Accepted final review violates delivery histogram gate: {reasons_str}",
                )
            if gates.get("background") == "pass":
                raise ContractError(
                    "skill_output_invalid",
                    f"Delivery background gate cannot pass when histogram gate failed: {reasons_str}",
                )
    return review, {
        "path": relative_session_path(session, path),
        "sha256": sha256_file(path),
    }


def _copy_atomic(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_file():
        raise ContractError("artifact_missing", f"Copy source is missing or unsafe: {source}")
    if destination.is_symlink():
        raise ContractError("unsafe_path", f"Refusing to replace a symlink: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise ContractError(
                "finalization_conflict",
                f"Copy target already exists: {destination}",
            ) from exc
        _fsync_directory(destination.parent)
    except FileExistsError as exc:
        raise ContractError(
            "write_conflict",
            f"Temporary copy conflict: {destination}",
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_copy(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str | None = None,
) -> str:
    if source.is_symlink() or not source.is_file():
        raise ContractError("artifact_missing", f"Copy source is missing or unsafe: {source}")
    source_sha256 = sha256_file(source)
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ContractError("skill_output_invalid", f"Copy source hash changed: {source}")
    if destination.is_symlink():
        raise ContractError("unsafe_path", f"Refusing to reuse a symlink: {destination}")
    if destination.exists():
        if not destination.is_file() or sha256_file(destination) != source_sha256:
            raise ContractError(
                "finalization_conflict",
                f"Existing finalized output differs from the verified source: {destination}",
            )
        return source_sha256
    _copy_atomic(source, destination)
    if sha256_file(destination) != source_sha256:
        raise ContractError("skill_output_invalid", f"Copied output hash changed: {destination}")
    return source_sha256


def _list_intermediate_images(session: Path, *, preserve=()) -> list[str]:
    images: list[str] = []
    for root_name in ("artifacts", "previews", "reports"):
        root = session / root_name
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_symlink():
                raise ContractError("unsafe_path", f"Refusing to prune a symlink: {path}")
            if (path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
                and relative_session_path(session, path) not in preserve):
                images.append(relative_session_path(session, path))
    return sorted(images)


def _complete_prune_plan(session: Path, planned: Any) -> list[str]:
    if (
        not isinstance(planned, list)
        or len(set(planned)) != len(planned)
        or any(not isinstance(relative, str) for relative in planned)
    ):
        raise ContractError("manifest_invalid", "Intermediate cleanup plan is invalid")
    normalized = sorted(planned)
    for relative in normalized:
        path = session_path(
            session,
            session / relative,
            allowed_roots=("artifacts", "previews", "reports"),
        )
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            raise ContractError(
                "manifest_invalid",
                f"Cleanup plan contains a non-image: {relative}",
            )
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ContractError(
                "unsafe_path",
                f"Refusing to prune an unsafe path: {path}",
            )
        path.unlink()
    for root_name in ("artifacts", "previews", "reports"):
        root = session / root_name
        for path in sorted(
            (item for item in root.rglob("*") if item.is_dir()),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
    return normalized


def _verified_input_reference(
    session: Path,
    payload: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    input_runs = [
        receipt
        for receipt in session_state._verified_success_receipts(session)
        if receipt.get("protocol") == "input.inspect"
        and receipt.get("source_path") == "@input"
    ]
    if len(input_runs) != 1:
        raise ContractError(
            "skill_output_invalid",
            "Finalization requires exactly one verified input.inspect run",
        )

    references: dict[str, Path] = {}
    for record in input_runs[0].get("outputs", []):
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ContractError("skill_output_invalid", "Input preview record is invalid")
        candidate = session_path(
            session,
            session / record["path"],
            must_exist=True,
            allowed_roots=("previews",),
        )
        if not fingerprint_matches({**record, "path": str(candidate)}):
            raise ContractError("skill_output_invalid", "Input preview hash changed")
        stem = candidate.stem.lower()
        role = (
            "autostretch"
            if stem.endswith("input-autostretch")
            else "direct"
            if stem.endswith("input-direct")
            else None
        )
        if role is not None:
            if role in references:
                raise ContractError(
                    "skill_output_invalid",
                    "Input preview role is duplicated",
                )
            references[role] = candidate
    if set(references) != {"direct", "autostretch"}:
        raise ContractError(
            "skill_output_invalid",
            "input.inspect must publish direct and autostretch JPEG previews",
        )

    input_state = payload["context"].get("input_state")
    if input_state == "linear":
        selected = references["autostretch"]
    elif input_state == "nonlinear":
        selected = references["direct"]
    else:
        raise ContractError("skill_output_invalid", "Session input state is invalid")

    display = artifacts.validate_display(selected)
    if display.get("passed") is not True or display.get("format") != "JPEG":
        raise ContractError(
            "skill_output_invalid",
            "Input reference is not a valid JPEG",
        )
    return selected, display


def _verified_committed_file(path: Path, expected_sha256: Any, label: str) -> Path:
    if (
        not isinstance(expected_sha256, str)
        or HASH_PATTERN.fullmatch(expected_sha256) is None
        or path.is_symlink()
        or not path.is_file()
        or sha256_file(path) != expected_sha256
    ):
        raise ContractError(
            "skill_output_invalid",
            f"Committed {label} is missing or changed",
        )
    return path


def _load_committed_finalization(
    session: Path,
    manifest: dict[str, Any],
    selection_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], str, bool]:
    session_payload = load_json(session / "session.json")
    current_knowledge = session_state._knowledge_record(session_payload)
    finalization = manifest.get("finalization")
    if not isinstance(finalization, dict):
        raise ContractError("manifest_invalid", "Manifest finalization is invalid")
    if finalization.get("selection_sha256") != selection_sha256:
        raise ContractError(
            "finalization_conflict",
            "Session was finalized with another selection",
        )
    status = finalization.get("status")
    delivered = status in {"success", "partial_success"}
    required = {
        "schema",
        "state",
        "selection_sha256",
        "status",
        "final_result_sha256",
        "audit_sha256",
        "session_knowledge_sha256",
        "retention_policy",
        "cleanup_completed",
    }
    paired = (session_payload.get('execution_policy') or {}).get('delivery') in PAIRED_DELIVERY_POLICIES
    if delivered:
        required |= {"reference_sha256", "final_sha256"}
        if paired:
            required.add('final_fits_sha256')
    if (
        set(finalization) != required
        or finalization.get("schema") != f"{SCHEMA_PREFIX}.finalization.v1"
        or finalization.get("state") != "committed"
        or status not in {"success", "partial_success", "review_required", "failed"}
        or finalization.get("session_knowledge_sha256")
        != current_knowledge["session_knowledge_sha256"]
    ):
        raise ContractError(
            "manifest_invalid",
            "Manifest finalization commit is invalid",
        )
    retention_policy = finalization.get("retention_policy")
    cleanup_completed = finalization.get("cleanup_completed")
    if (
        retention_policy not in {"preserve", "prune"}
        or not isinstance(cleanup_completed, bool)
        or (not delivered and retention_policy != "preserve")
    ):
        raise ContractError("manifest_invalid", "Manifest cleanup state is invalid")

    audit_path = _verified_committed_file(
        session / "reports" / "final-audit.json",
        finalization.get("audit_sha256"),
        "final audit",
    )
    result_path = _verified_committed_file(
        session / "reports" / "final-result.json",
        finalization.get("final_result_sha256"),
        "final result",
    )
    audit = load_json(audit_path)
    result = load_json(result_path)
    if (
        audit.get("schema") != f"{SCHEMA_PREFIX}.final-audit.v1"
        or audit.get("contract_version") != CONTRACT_VERSION
        or audit.get("status") != status
        or not isinstance(audit.get("selection"), dict)
        or audit["selection"].get("sha256") != selection_sha256
        or result.get("schema") != f"{SCHEMA_PREFIX}.final-result.v1"
        or result.get("contract_version") != CONTRACT_VERSION
        or result.get("status") != status
        or not isinstance(result.get("selection"), dict)
        or result["selection"].get("sha256") != selection_sha256
        or not isinstance(result.get("audit"), dict)
        or result["audit"].get("sha256") != finalization.get("audit_sha256")
        or result.get("retention_policy") != retention_policy
        or audit.get("knowledge_validation") != current_knowledge
        or result.get("knowledge_validation") != current_knowledge
    ):
        raise ContractError(
            "skill_output_invalid",
            "Committed final result binding is invalid",
        )

    final_path = session / "outputs" / "final.jpg"
    reference_path = session / "outputs" / "reference.jpg"
    if delivered:
        _verified_committed_file(
            reference_path,
            finalization.get("reference_sha256"),
            "reference image",
        )
        _verified_committed_file(
            final_path,
            finalization.get("final_sha256"),
            "final image",
        )
        if (
            not isinstance(result.get("reference"), dict)
            or result["reference"].get("sha256")
            != finalization.get("reference_sha256")
            or not isinstance(result.get("final"), dict)
            or result["final"].get("sha256") != finalization.get("final_sha256")
        ):
            raise ContractError(
                "skill_output_invalid",
                "Committed image binding is invalid",
            )
        if paired:
            _verified_committed_file(session / 'outputs/final.fit', finalization.get('final_fits_sha256'), 'final FITS')
            for document in (result, audit):
                record = document.get('final_fits', {})
                if (not isinstance(record, dict) or record.get('path') != 'outputs/final.fit'
                    or record.get('sha256') != finalization['final_fits_sha256']
                    or record.get('size') != (session / 'outputs/final.fit').stat().st_size):
                    raise ContractError('skill_output_invalid', 'Committed final FITS binding is invalid')
    else:
        if paired and ((session / 'outputs/final.fit').exists() or (session / 'outputs/final.fit').is_symlink()):
            raise ContractError('manifest_invalid', 'Non-delivery finalization contains outputs/final.fit')
        if final_path.exists() or final_path.is_symlink():
            raise ContractError(
                "manifest_invalid",
                "Non-delivery finalization contains outputs/final.jpg",
            )
        if reference_path.exists() or reference_path.is_symlink():
            raise ContractError(
                "manifest_invalid",
                "Non-delivery finalization contains outputs/reference.jpg",
            )
        if status == "review_required":
            candidate = result.get("candidate")
            if not isinstance(candidate, dict) or not isinstance(
                candidate.get("path"), str
            ):
                raise ContractError(
                    "skill_output_invalid",
                    "Review-required result is missing its candidate binding",
                )
            candidate_path = session_path(
                session,
                session / candidate["path"],
                must_exist=True,
                allowed_roots=("artifacts", "previews"),
            )
            if not fingerprint_matches({**candidate, "path": str(candidate_path)}):
                raise ContractError(
                    "skill_output_invalid",
                    "Review-required candidate changed",
                )
        elif status == "failed" and not isinstance(result.get("error"), dict):
            raise ContractError(
                "skill_output_invalid",
                "Failed result is missing its error",
            )
    if session_payload.get('execution_policy', {}).get('delivery') == 'paired_fits_jpeg_v2':
        evidence = audit.get('evidence_files')
        if not isinstance(evidence, list) or not evidence:
            raise ContractError('skill_output_invalid', 'Committed v2 evidence inventory is missing')
        paths = set()
        pruned = set(audit.get('pruned_intermediate_images', [])) if retention_policy == 'prune' else set()
        for record in evidence:
            if (not isinstance(record, dict) or set(record) != {'path', 'sha256', 'size'}
                or not isinstance(record['path'], str) or record['path'] in paths
                or not isinstance(record['sha256'], str) or HASH_PATTERN.fullmatch(record['sha256']) is None
                or type(record['size']) is not int or record['size'] < 0):
                raise ContractError('skill_output_invalid', 'Committed v2 evidence inventory is invalid')
            paths.add(record['path'])
            path = session_path(session, record['path'], allowed_roots=('scripts', 'runs', 'reviews', 'reports', 'logs', 'artifacts', 'previews'))
            if path.exists():
                if fingerprint(path, include_path=False) != {k: record[k] for k in ('sha256', 'size')}:
                    raise ContractError('skill_output_invalid', 'Committed evidence changed: ' + record['path'])
            elif record['path'] not in pruned:
                raise ContractError('skill_output_invalid', 'Committed evidence is missing: ' + record['path'])
        required = {f'runs/{run_id}.json' for run_id in audit.get('selected_runs', [])}
        required.update(record['path'] for record in audit.get('reviews', []))
        from deep_sky_siril_stretch_trials import retained_paths
        trial_paths = retained_paths(session, session_payload)
        from deep_sky_siril_morphology import retained_paths as morphology_paths
        trial_paths.update(morphology_paths(session,session_payload,require_sources=False))
        from deep_sky_siril_artifact_focus import retained_paths as artifact_paths
        trial_paths.update(artifact_paths(session, session_payload, require_sources=False))
        from deep_sky_siril_display_scale import retained_paths as display_scale_paths
        trial_paths.update(display_scale_paths(session, session_payload))
        if trial_paths & pruned:
            raise ContractError('skill_output_invalid', 'Committed cleanup cannot remove Stage 7 trial artifacts')
        required.update(trial_paths)
        if not required.issubset(paths):
            raise ContractError('skill_output_invalid', 'Committed v2 evidence omits selected receipts or reviews')
    return result, audit, retention_policy, cleanup_completed


def _validated_candidate(
    session: Path,
    selection: dict[str, Any],
) -> tuple[
    Path,
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
    dict[str, Any],
    dict[str, Any],
]:
    selected_runs = selection["selected_runs"]
    review_by_run: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for value in selection["review_receipts"]:
        if not isinstance(value, str):
            raise ContractError(
                "skill_output_invalid",
                "Review path must be a string",
            )
        path = session_path(
            session,
            value,
            must_exist=True,
            allowed_roots=("reviews",),
        )
        raw = load_json(path)
        run_id = raw.get("run_id")
        if not isinstance(run_id, str) or run_id in review_by_run:
            raise ContractError(
                "skill_output_invalid",
                "Review receipts are invalid or duplicated",
            )
        review_by_run[run_id] = _validate_review(session, value, run_id)
    if set(selected_runs) != set(review_by_run):
        raise ContractError(
            "skill_output_invalid",
            "Every selected run requires exactly one review receipt",
        )
    if selection["status"] in {"success", "partial_success"} and any(
        review[0].get("verdict") != "accept"
        for review in review_by_run.values()
    ):
        raise ContractError(
            "skill_output_invalid",
            "Successful delivery contains an unaccepted review",
        )

    candidate = session_path(
        session,
        selection["candidate_image"],
        must_exist=True,
        allowed_roots=("artifacts", "previews"),
    )
    owner: dict[str, Any] | None = None
    for run_id in selected_runs:
        _run_path, run = _load_run(session, run_id)
        for record in run.get("outputs", []):
            if (
                isinstance(record, dict)
                and (session / str(record.get("path", ""))).resolve(strict=False)
                == candidate.resolve()
            ):
                if not fingerprint_matches({**record, "path": str(candidate)}):
                    raise ContractError(
                        "skill_output_invalid",
                        "Final candidate hash changed",
                    )
                owner = run
    if owner is None or owner.get("protocol") != "delivery.render":
        raise ContractError(
            "skill_output_invalid",
            "Candidate image is not a verified delivery.render output",
        )
    display = artifacts.validate_display(candidate)
    if display.get("passed") is not True or display.get("format") != "JPEG":
        raise ContractError(
            "skill_output_invalid",
            "Candidate image is not a valid JPEG",
        )
    return candidate, review_by_run, display, owner


def finalize_session(
    session_value: str,
    *,
    selection_value: str,
    keep_intermediates: bool,
) -> dict[str, Any]:
    session, payload, manifest = session_state.load_session(session_value)
    payload = session_state.effective_session_payload(session, payload, manifest)
    selection_path = session_path(session, selection_value, must_exist=True)
    selection = load_json(selection_path)
    _validate_selection(selection)
    selection_sha256 = sha256_file(selection_path)

    prior = manifest.get("finalization")
    committed_v2 = isinstance(prior, dict) and payload.get('execution_policy', {}).get('delivery') == 'paired_fits_jpeg_v2'
    if not committed_v2:
        for run_id in selection["selected_runs"]:
            _, selected_run = _load_run(session, run_id)
            if selected_run.get('processing_checks', {}).get('background_validation', {}).get('decision') == 'skip':
                raise ContractError('skill_output_invalid', 'Skipped background candidates cannot enter selected lineage')
            hist_gate = selected_run.get('processing_checks', {}).get('delivery_export', {}).get('histogram_gate', {})
            if hist_gate.get('status') == 'failed' and selection["status"] in {"success", "partial_success"}:
                reasons_str = '; '.join(hist_gate.get('reasons', []))
                raise ContractError('skill_output_invalid', f"Finalization rejected: delivery candidate failed histogram gate ({reasons_str})")

    if isinstance(prior, dict):
        result, audit, retention_policy, cleanup_completed = (
            _load_committed_finalization(
                session,
                manifest,
                selection_sha256,
            )
        )
        if retention_policy == "prune" and not cleanup_completed:
            _complete_prune_plan(
                session,
                audit.get("pruned_intermediate_images"),
            )
            updated = dict(prior)
            updated["cleanup_completed"] = True
            manifest["finalization"] = updated
            manifest["updated_at"] = utc_now()
            atomic_write_json(session / "manifest.json", manifest)
        return result
    if prior is not None:
        raise ContractError("manifest_invalid", "Manifest finalization is invalid")

    status = selection["status"]
    paired = (payload.get('execution_policy') or {}).get('delivery') in PAIRED_DELIVERY_POLICIES
    if bool(selection["stars_required"]) != (
        payload["context"].get("stars") != "standalone-starless"
    ):
        raise ContractError(
            "skill_output_invalid",
            "Final stars policy differs from session intent",
        )
    if (
        status in {"success", "partial_success"}
        and selection["stars_required"]
        and selection["output_contains_stars"] is not True
    ):
        raise ContractError(
            "skill_output_invalid",
            "Required stars are absent from the delivered output",
        )
    unknown_fields = _unknown_context_fields(payload)
    if unknown_fields and status != "failed":
        raise ContractError(
            "skill_output_invalid",
            f"Unknown {', '.join(unknown_fields)} stops after Stage 1 and cannot be finalized with a candidate; use confirm-input with reliable source evidence after an accepted inspection",
        )

    selection_record = {
        "path": relative_session_path(session, selection_path),
        "sha256": selection_sha256,
    }
    delivered = status in {"success", "partial_success"}
    should_keep = bool(payload["context"].get("keep_intermediates")) or bool(
        keep_intermediates
    )
    retention_policy = (
        "preserve" if should_keep or not delivered else "prune"
    )
    prune_plan: list[str] = []
    from deep_sky_siril_stretch_trials import retained_paths
    trial_paths = retained_paths(session, payload)
    from deep_sky_siril_morphology import retained_paths as morphology_paths
    trial_paths.update(morphology_paths(session,payload))
    from deep_sky_siril_artifact_focus import retained_paths as artifact_paths
    trial_paths.update(artifact_paths(session, payload))
    from deep_sky_siril_display_scale import retained_paths as display_scale_paths
    trial_paths.update(display_scale_paths(session, payload))

    audit: dict[str, Any] = {
        "schema": f"{SCHEMA_PREFIX}.final-audit.v1",
        "contract_version": CONTRACT_VERSION,
        "created_at": utc_now(),
        "status": status,
        "selection": selection_record,
        "input": {"sha256": payload["input"]["sha256"], "unchanged": True},
        "limitations": selection["limitations"],
        "stars_required": selection["stars_required"],
        "output_contains_stars": selection["output_contains_stars"],
        "retention_policy": retention_policy,
        "pruned_intermediate_images": prune_plan,
        "knowledge_validation": session_state._knowledge_record(payload),
    }
    result: dict[str, Any] = {
        "schema": f"{SCHEMA_PREFIX}.final-result.v1",
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "selection": selection_record,
        "limitations": selection["limitations"],
        "retention_policy": retention_policy,
        "intermediates_preserved": retention_policy == "preserve",
        "knowledge_validation": session_state._knowledge_record(payload),
    }
    if "input_confirmation" in payload:
        audit["input_confirmation"] = payload["input_confirmation"]

    if status == "failed":
        for official in (
            session / "outputs" / "reference.jpg",
            session / "outputs" / "final.jpg",
            *([session / "outputs" / "final.fit"] if paired else []),
        ):
            if official.exists() or official.is_symlink():
                raise ContractError(
                    "finalization_conflict",
                    "Failed finalization cannot contain formal outputs",
                )
        audit["error"] = selection["error"]
        result["error"] = selection["error"]
    else:
        candidate, review_by_run, display, owner = _validated_candidate(
            session,
            selection,
        )
        candidate_record = {
            **fingerprint(candidate, include_path=False),
            "path": relative_session_path(session, candidate),
        }
        audit["candidate"] = {**candidate_record, "validation": display}
        audit["selected_runs"] = selection["selected_runs"]
        if (payload.get('execution_policy') or {}).get('delivery') == 'paired_fits_jpeg_v2':
            evidence = set()
            evidence.update(session/relative for relative in trial_paths)
            for directory in ('scripts', 'runs', 'reviews', 'reports', 'logs'):
                evidence.update(path for path in (session / directory).rglob('*') if path.is_file())
            for selected_id in selection['selected_runs']:
                _, selected_receipt = _load_run(session, selected_id)
                evidence.update(session_path(session, record['path'], must_exist=True)
                                for record in selected_receipt.get('outputs', []))
            evidence.difference_update((session / 'reports/final-audit.json', session / 'reports/final-result.json'))
            audit['evidence_files'] = [
                {'path': relative_session_path(session, path), **fingerprint(session_path(session, path, must_exist=True,
                    allowed_roots=('scripts', 'runs', 'reviews', 'reports', 'logs', 'artifacts', 'previews')), include_path=False)}
                for path in sorted(evidence)]
        audit["reviews"] = [
            review_by_run[run_id][1] for run_id in selection["selected_runs"]
        ]
        if status == "review_required":
            for official in (
                session / "outputs" / "reference.jpg",
                session / "outputs" / "final.jpg",
                *([session / "outputs" / "final.fit"] if paired else []),
            ):
                if official.exists() or official.is_symlink():
                    raise ContractError(
                        "finalization_conflict",
                        "Review-required finalization cannot contain formal outputs",
                    )
            result["candidate"] = candidate_record
        else:
            reference_source, reference_display = _verified_input_reference(
                session,
                payload,
            )
            reference_path = session / "outputs" / "reference.jpg"
            final_path = session / "outputs" / "final.jpg"
            if paired:
                binding = owner.get('delivery_export', {})
                checks = owner.get('processing_checks', {}).get('delivery_export', {})
                if (binding.get('policy') != payload['execution_policy']['delivery']
                    or binding.get('jpeg') != relative_session_path(session, candidate)
                    or binding.get('fits') != owner.get('primary_output') or checks.get('status') != 'passed'):
                    raise ContractError('skill_output_invalid', 'Final JPEG is not bound to a verified scientific FITS')
                science = session_path(session, binding['fits'], must_exist=True, allowed_roots=('artifacts',))
                record = checks.get('artifacts', {}).get('fits', {})
                target = session / 'outputs/final.fit'
                _ensure_copy(science, target, expected_sha256=record.get('sha256'))
                final_fits = {'path': 'outputs/final.fit', **fingerprint(target, include_path=False)}
                audit['final_fits'] = {**final_fits, 'validation': checks}
                result['final_fits'] = final_fits
            _ensure_copy(reference_source, reference_path)
            _ensure_copy(candidate, final_path)
            reference_record = {
                **fingerprint(reference_path, include_path=False),
                "path": "outputs/reference.jpg",
            }
            final_record = {
                **fingerprint(final_path, include_path=False),
                "path": "outputs/final.jpg",
            }
            audit["reference"] = {
                **reference_record,
                "validation": reference_display,
            }
            audit["final"] = {**final_record, "validation": display}
            result["reference"] = reference_record
            result["final"] = final_record
            if retention_policy == "prune":
                prune_plan.extend(_list_intermediate_images(session, preserve=trial_paths))

    audit_path = session / "reports" / "final-audit.json"
    atomic_write_json(audit_path, audit)
    audit_record = {
        "path": "reports/final-audit.json",
        "sha256": sha256_file(audit_path),
    }
    result["audit"] = audit_record
    result_path = session / "reports" / "final-result.json"
    atomic_write_json(result_path, result)

    finalization: dict[str, Any] = {
        "schema": f"{SCHEMA_PREFIX}.finalization.v1",
        "state": "committed",
        "selection_sha256": selection_sha256,
        "status": status,
        "final_result_sha256": sha256_file(result_path),
        "audit_sha256": audit_record["sha256"],
        "session_knowledge_sha256": session_state._knowledge_record(payload)[
            "session_knowledge_sha256"
        ],
        "retention_policy": retention_policy,
        "cleanup_completed": retention_policy == "preserve",
    }
    if delivered:
        finalization["reference_sha256"] = result["reference"]["sha256"]
        finalization["final_sha256"] = result["final"]["sha256"]
        if paired:
            finalization["final_fits_sha256"] = result["final_fits"]["sha256"]
    manifest["finalization"] = finalization
    manifest["updated_at"] = utc_now()
    atomic_write_json(session / "manifest.json", manifest)

    if retention_policy == "prune":
        _complete_prune_plan(session, prune_plan)
        committed = dict(finalization)
        committed["cleanup_completed"] = True
        manifest["finalization"] = committed
        manifest["updated_at"] = utc_now()
        atomic_write_json(session / "manifest.json", manifest)
    return result


__all__ = ("check_review", "finalize_session", "review_materials_for_run", "run_script")
