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
from typing import Any, Sequence

import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_session as session_state
import deep_sky_siril_tooling as tooling
import deep_sky_siril_validation as validation_ops
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
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
    fingerprint,
    fingerprint_matches,
    load_json,
    relative_session_path,
    resource_fingerprint_matches,
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
    if payload["context"].get("input_state") != "unknown":
        return
    if protocol != "input.inspect":
        raise ContractError(
            "protocol_not_applicable",
            "Unknown input permits only Stage 1 input.inspect; create a new session after obtaining reliable state evidence",
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
    except ContractError:
        return False
    captured = validation.get("knowledge_validation")
    return bool(
        isinstance(captured, dict)
        and all(captured.get(key) == value for key, value in current.items())
        and validation_ops.knowledge_bindings_unchanged(session, validation)
    )


def run_script(
    session_value: str,
    *,
    protocol: str,
    script_value: str,
    source_value: str,
    expected_values: Sequence[str],
    timeout: int,
    validate_only: bool = False,
) -> dict[str, Any]:
    session, payload, manifest = session_state.load_session(session_value)
    script = session_path(session, script_value, must_exist=True, allowed_roots=("scripts",))
    if script.suffix != ".ssf" or RUN_ID_PATTERN.fullmatch(script.stem) is None:
        raise ContractError("script_name_invalid", "Script name must be NNN-name.ssf")
    run_id = script.stem
    provenance = script.with_suffix(".provenance.json")
    receipt_path = session / "runs" / f"{run_id}.json"
    if not validate_only and (receipt_path.exists() or receipt_path.is_symlink()):
        return _replay_run_receipt(
            session=session,
            payload=payload,
            manifest=manifest,
            receipt_path=receipt_path,
            script=script,
            provenance=provenance,
            protocol=protocol,
            source_value=source_value,
            expected_values=expected_values,
        )
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
    probe = (
        frozen_probe
        if validate_only or protocol == "background.subtract"
        else tooling.probe_tools(offline=offline)
    )
    siril = probe.get("tools", {}).get("siril_cli", {})
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
        executable_record = (
            starnet_runtime.get("executable")
            if isinstance(starnet_runtime, dict)
            else None
        )
        model_record = (
            starnet_runtime.get("model")
            if isinstance(starnet_runtime, dict)
            else None
        )
        starnet_unchanged = fingerprint_matches(executable_record) and resource_fingerprint_matches(
            model_record
        )
    sirilpy_runtime = validation.get("sirilpy_bridge")
    sirilpy_unchanged = True
    if protocol == "background.subtract" and isinstance(sirilpy_runtime, dict):
        sirilpy_unchanged = (
            sirilpy_runtime.get("status") == "runtime_check_required"
            and sirilpy_runtime.get("required_version") == "1.0.25"
        )
    siril_unchanged = fingerprint_matches(siril.get("fingerprint"))
    runtime_bindings_unchanged = bool(
        siril_unchanged
        and adapters_unchanged
        and starnet_unchanged
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
    }
    atomic_write_json(receipt_path, receipt)
    session_state._append_run(session / "manifest.json", manifest, receipt_path, receipt)
    return receipt


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
        set(review) != required
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
        material_path = session_path(session, session / relative, must_exist=True)
        if material["sha256"] != sha256_file(material_path):
            raise ContractError(
                "skill_output_invalid",
                f"Reviewed material changed: {relative}",
            )
        if material_path.suffix.lower() in IMAGE_SUFFIXES:
            image_materials[relative] = material_path.resolve()
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
    if run.get("protocol") == "delivery.render":
        delivery_images = {
            str(record["path"])
            for record in run.get("outputs", [])
            if isinstance(record, dict)
            and isinstance(record.get("path"), str)
            and Path(record["path"]).suffix.lower() in IMAGE_SUFFIXES
        }
        inspected_delivery = set(image_materials) & delivery_images
        source_path = run.get("source_path")
        verified_outputs = session_state._verified_run_outputs(session)
        parent_previews: set[Path] = set()
        for prior_run in session_state._verified_success_receipts(session):
            if prior_run.get("id") == run_id:
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
            for record in prior_outputs:
                if not isinstance(record, dict) or not isinstance(
                    record.get("path"), str
                ):
                    continue
                candidate = (session / record["path"]).resolve(strict=False)
                if (
                    candidate in verified_outputs
                    and candidate.suffix.lower() in DISPLAY_IMAGE_SUFFIXES
                ):
                    parent_previews.add(candidate)
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


def _list_intermediate_images(session: Path) -> list[str]:
    images: list[str] = []
    for root_name in ("artifacts", "previews", "reports"):
        root = session / root_name
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_symlink():
                raise ContractError("unsafe_path", f"Refusing to prune a symlink: {path}")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
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
    if delivered:
        required |= {"reference_sha256", "final_sha256"}
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
    else:
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
    return result, audit, retention_policy, cleanup_completed


def _validated_candidate(
    session: Path,
    selection: dict[str, Any],
) -> tuple[
    Path,
    dict[str, tuple[dict[str, Any], dict[str, Any]]],
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
    return candidate, review_by_run, display


def finalize_session(
    session_value: str,
    *,
    selection_value: str,
    keep_intermediates: bool,
) -> dict[str, Any]:
    session, payload, manifest = session_state.load_session(session_value)
    selection_path = session_path(session, selection_value, must_exist=True)
    selection = load_json(selection_path)
    _validate_selection(selection)
    selection_sha256 = sha256_file(selection_path)
    for run_id in selection["selected_runs"]:
        _load_run(session, run_id)

    prior = manifest.get("finalization")
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
    input_state = payload["context"].get("input_state")
    if input_state == "unknown" and status != "failed":
        raise ContractError(
            "skill_output_invalid",
            "Unknown input stops after Stage 1 and cannot be finalized with a candidate; create a new session after obtaining reliable state evidence",
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

    if status == "failed":
        for official in (
            session / "outputs" / "reference.jpg",
            session / "outputs" / "final.jpg",
        ):
            if official.exists() or official.is_symlink():
                raise ContractError(
                    "finalization_conflict",
                    "Failed finalization cannot contain formal outputs",
                )
        audit["error"] = selection["error"]
        result["error"] = selection["error"]
    else:
        candidate, review_by_run, display = _validated_candidate(
            session,
            selection,
        )
        candidate_record = {
            **fingerprint(candidate, include_path=False),
            "path": relative_session_path(session, candidate),
        }
        audit["candidate"] = {**candidate_record, "validation": display}
        audit["selected_runs"] = selection["selected_runs"]
        audit["reviews"] = [
            review_by_run[run_id][1] for run_id in selection["selected_runs"]
        ]
        if status == "review_required":
            for official in (
                session / "outputs" / "reference.jpg",
                session / "outputs" / "final.jpg",
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
                prune_plan.extend(_list_intermediate_images(session))

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


__all__ = ("finalize_session", "run_script")
