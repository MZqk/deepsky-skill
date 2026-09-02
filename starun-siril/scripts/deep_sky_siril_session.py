#!/usr/bin/env python3
"""Transactional session state and receipt lineage for standalone contract 1."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Sequence

import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_tooling as tooling
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    DISPLAY_IMAGE_SUFFIXES,
    EXECUTION_POLICY,
    HASH_PATTERN,
    INPUT_EXTENSIONS,
    SCHEMA_PREFIX,
    SESSION_DIRECTORIES,
    SKILL_ROOT,
    ContractError,
    _absolute,
    _fsync_directory,
    atomic_write_json,
    classify_siril_network,
    fingerprint,
    fingerprint_matches,
    load_json,
    safe_session_root,
    session_path,
    sha256_file,
    stable_hash,
    utc_now,
)
from siril_manual_bundle import (
    BundleError,
    bundle_verification_document,
    read_skill_file,
    strict_json_bytes,
    verify_bundle,
)


_POLICY_PATH = "references/command-policy.json"
_BUNDLE_EVIDENCE_PATH = "reports/manual-evidence/bundle-verification.json"
_LEGACY_REFERENCE_FINGERPRINTS = {
    "references/protocols/color-calibrate.md": {
        ("1d241f549828c02a55e45251c0a3374eab64aa982914a71b503b084df82c1a0b", 1707)
    },
}


def execution_policy_is_current(payload: dict[str, Any]) -> bool:
    return payload.get("execution_policy") == EXECUTION_POLICY


def _current_knowledge_sources() -> tuple[dict[str, Any], Any]:
    try:
        snapshot = verify_bundle(SKILL_ROOT)
        policy_capture = read_skill_file(SKILL_ROOT, _POLICY_PATH)
        policy = strict_json_bytes(policy_capture.data, document=_POLICY_PATH)
    except BundleError as exc:
        raise ContractError(
            "knowledge_bundle_invalid",
            f"Pinned Siril knowledge Bundle is invalid: {exc}",
        ) from exc
    if (
        not isinstance(policy, dict)
        or policy.get("schema") != f"{SCHEMA_PREFIX}.command-policy.v1"
        or policy.get("contract_version") != CONTRACT_VERSION
    ):
        raise ContractError(
            "knowledge_bundle_invalid",
            "Command policy does not match standalone v1",
        )
    return bundle_verification_document(snapshot), policy_capture


def _knowledge_record(payload: dict[str, Any]) -> dict[str, Any]:
    knowledge = payload.get("knowledge")
    if not isinstance(knowledge, dict):
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session lacks the required knowledge binding",
        )
    return {
        "status": "passed",
        "session_knowledge_sha256": stable_hash(knowledge),
        "bundle_evidence": knowledge.get("bundle_evidence"),
        "manual": knowledge.get("manual"),
        "command_policy": knowledge.get("command_policy"),
    }


def _verify_session_knowledge(
    session: Path,
    payload: dict[str, Any],
) -> dict[str, Any]:
    knowledge = payload.get("knowledge")
    if not isinstance(knowledge, dict) or set(knowledge) != {
        "schema",
        "bundle_evidence",
        "manual",
        "command_policy",
    }:
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session knowledge binding is missing or invalid",
        )
    if knowledge.get("schema") != f"{SCHEMA_PREFIX}.knowledge.v1":
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session knowledge schema is invalid",
        )
    evidence_record = knowledge.get("bundle_evidence")
    policy_record = knowledge.get("command_policy")
    if (
        not isinstance(evidence_record, dict)
        or set(evidence_record) != {"path", "sha256", "size"}
        or evidence_record.get("path") != _BUNDLE_EVIDENCE_PATH
        or HASH_PATTERN.fullmatch(str(evidence_record.get("sha256", ""))) is None
        or isinstance(evidence_record.get("size"), bool)
        or not isinstance(evidence_record.get("size"), int)
        or evidence_record["size"] < 1
        or not isinstance(policy_record, dict)
        or set(policy_record) != {"path", "sha256", "size"}
        or policy_record.get("path") != _POLICY_PATH
        or HASH_PATTERN.fullmatch(str(policy_record.get("sha256", ""))) is None
        or isinstance(policy_record.get("size"), bool)
        or not isinstance(policy_record.get("size"), int)
        or policy_record["size"] < 1
    ):
        raise ContractError(
            "unsupported_session_contract",
            "Standalone v1 session knowledge fingerprints are invalid",
        )
    evidence_path = session_path(
        session,
        session / _BUNDLE_EVIDENCE_PATH,
        must_exist=True,
        allowed_roots=("reports",),
    )
    if not fingerprint_matches({**evidence_record, "path": str(evidence_path)}):
        raise ContractError(
            "knowledge_binding_drift",
            "Frozen Bundle verification evidence changed",
        )
    current_document, policy_capture = _current_knowledge_sources()
    if load_json(evidence_path) != current_document:
        raise ContractError(
            "knowledge_binding_drift",
            "Frozen Bundle verification evidence no longer matches the pinned Bundle",
        )
    if knowledge.get("manual") != current_document.get("manual"):
        raise ContractError(
            "knowledge_binding_drift",
            "Pinned Siril manual identity changed after session initialization",
        )
    if (
        policy_record.get("sha256") != policy_capture.sha256
        or policy_record.get("size") != policy_capture.size_bytes
    ):
        raise ContractError(
            "knowledge_binding_drift",
            "Command policy changed after session initialization",
        )
    return _knowledge_record(payload)

def _preflight_session_target(path: Path) -> tuple[int, int] | None:
    """Validate a prospective session root without creating or changing it."""
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        raise ContractError("unsafe_session", f"Session root is a symlink: {path}")
    try:
        if not path.is_dir():
            raise ContractError("unsafe_session", f"Session root is not a directory: {path}")
        if any(path.iterdir()):
            raise ContractError("session_not_empty", f"Session root must be empty: {path}")
        status = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ContractError("unsafe_session", f"Cannot inspect session root: {path}") from exc
    return status.st_dev, status.st_ino


def _session_target_unchanged(path: Path, identity: tuple[int, int] | None) -> bool:
    """Best-effort compare immediately before the atomic directory commit."""
    try:
        if identity is None:
            return not os.path.lexists(path)
        if not os.path.lexists(path) or path.is_symlink() or not path.is_dir():
            return False
        if any(path.iterdir()):
            return False
        status = path.stat(follow_symlinks=False)
        return (status.st_dev, status.st_ino) == identity
    except OSError:
        return False


def init_session(
    input_value: str,
    session_value: str,
    *,
    input_state: str,
    state_evidence: Sequence[str],
    channel_mode: str,
    channel_map: dict[str, str] | None,
    target_name: str | None,
    target_type: str,
    style: str,
    stars: str,
    offline: bool,
    keep_intermediates: bool,
    container_validation: str = "siril",
) -> dict[str, Any]:
    input_raw = _absolute(input_value)
    if input_raw.is_symlink() or not input_raw.is_file():
        raise ContractError("input_missing", f"Input master is missing or unsafe: {input_raw}")
    input_path = input_raw.resolve()
    if input_path.suffix.lower() not in INPUT_EXTENSIONS:
        raise ContractError("input_format_unsupported", f"Unsupported master format: {input_path.suffix}")
    if input_state not in {"auto", "linear", "nonlinear", "unknown"}:
        raise ContractError("input_state_invalid", f"Unsupported input state: {input_state}")
    evidence = [" ".join(str(item).split())[:500] for item in state_evidence if str(item).strip()]
    if input_state in {"linear", "nonlinear"} and not evidence:
        raise ContractError("state_evidence_missing", "Known input state requires concrete evidence")
    if input_state in {"auto", "unknown"} and evidence:
        raise ContractError("state_evidence_invalid", "Unknown input state cannot include state evidence")
    if channel_mode not in {"mono", "broadband", "narrowband", "dualband-osc", "unknown"}:
        raise ContractError("channel_mode_invalid", f"Unsupported channel mode: {channel_mode}")
    if style not in {"natural", "balanced", "artistic"}:
        raise ContractError("style_invalid", f"Unsupported style: {style}")
    if stars not in {"adaptive", "preserve", "standalone-starless"}:
        raise ContractError("stars_policy_invalid", f"Unsupported star policy: {stars}")
    if container_validation not in {"siril", "strict"}:
        raise ContractError(
            "container_validation_invalid",
            f"Unsupported container validation mode: {container_validation}",
        )

    session = _absolute(session_value)
    target_identity = _preflight_session_target(session)
    input_record = fingerprint(input_path, role="stacked_master")
    bundle_document, policy_capture = _current_knowledge_sources()
    probe = tooling.probe_tools(offline=offline)
    staging: Path | None = None
    try:
        session.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{session.name or 'session'}.staging-",
                dir=session.parent,
            )
        )
        staging.chmod(0o700)
        for relative in SESSION_DIRECTORIES:
            (staging / relative).mkdir(parents=True, exist_ok=False)
        probe_path = staging / "reports" / "tool-probe.json"
        atomic_write_json(probe_path, probe)
        bundle_evidence_path = staging / _BUNDLE_EVIDENCE_PATH
        atomic_write_json(bundle_evidence_path, bundle_document)
        knowledge = {
            "schema": f"{SCHEMA_PREFIX}.knowledge.v1",
            "bundle_evidence": {
                **fingerprint(bundle_evidence_path, include_path=False),
                "path": _BUNDLE_EVIDENCE_PATH,
            },
            "manual": bundle_document["manual"],
            "command_policy": {
                "path": _POLICY_PATH,
                "sha256": policy_capture.sha256,
                "size": policy_capture.size_bytes,
            },
        }
        session_payload = {
            "schema": f"{SCHEMA_PREFIX}.session.v1",
            "contract_version": CONTRACT_VERSION,
            "created_at": utc_now(),
            "execution_policy": dict(EXECUTION_POLICY),
            "input": input_record,
            "context": {
                "input_state": "unknown" if input_state == "auto" else input_state,
                "state_evidence": evidence,
                "channel_mode": channel_mode,
                "channel_map": channel_map,
                "target_name": target_name,
                "target_type": target_type,
                "style": style,
                "stars": stars,
                "offline": bool(offline),
                "keep_intermediates": bool(keep_intermediates),
                "container_validation": container_validation,
            },
            "tool_probe": {
                "path": "reports/tool-probe.json",
                "sha256": sha256_file(probe_path),
            },
            "knowledge": knowledge,
        }
        session_payload["session_sha256"] = stable_hash(session_payload)
        atomic_write_json(staging / "session.json", session_payload)
        manifest = {
            "schema": f"{SCHEMA_PREFIX}.manifest.v1",
            "contract_version": CONTRACT_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "input_sha256": input_record["sha256"],
            "runs": [],
            "finalization": None,
        }
        atomic_write_json(staging / "manifest.json", manifest)
        current_bundle_document, current_policy_capture = _current_knowledge_sources()
        if (
            current_bundle_document != bundle_document
            or current_policy_capture.sha256 != policy_capture.sha256
            or current_policy_capture.size_bytes != policy_capture.size_bytes
        ):
            raise ContractError(
                "knowledge_binding_drift",
                "Pinned Bundle or command policy changed during session initialization",
            )
        _fsync_directory(staging)
        if not _session_target_unchanged(session, target_identity):
            raise ContractError(
                "session_not_empty",
                f"Session root changed or became occupied during initialization: {session}",
            )
        try:
            os.replace(staging, session)
        except OSError as exc:
            raise ContractError(
                "session_init_failed",
                f"Cannot atomically commit session root: {session}",
            ) from exc
        staging = None
        _fsync_directory(session.parent)
    except ContractError:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    except OSError as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        raise ContractError(
            "session_init_failed",
            f"Cannot initialize session root: {session}",
        ) from exc

    session = session.resolve()
    return {
        "schema": f"{SCHEMA_PREFIX}.init-result.v1",
        "status": "ready",
        "session": str(session),
        "contract_version": CONTRACT_VERSION,
        "execution_policy": dict(EXECUTION_POLICY),
        "input": input_record,
        "context": session_payload["context"],
        "tool_probe": probe,
        "knowledge": knowledge,
    }


def load_session(session_value: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    session = safe_session_root(session_value)
    payload = load_json(session / "session.json")
    manifest = load_json(session / "manifest.json")
    if payload.get("schema") != f"{SCHEMA_PREFIX}.session.v1" or payload.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("unsupported_session_contract", "Only standalone contract 1 sessions are supported")
    unsigned = dict(payload)
    session_hash = unsigned.pop("session_sha256", None)
    if session_hash != stable_hash(unsigned):
        raise ContractError("session_hash_drift", "session.json changed after initialization")
    if manifest.get("schema") != f"{SCHEMA_PREFIX}.manifest.v1" or manifest.get("contract_version") != CONTRACT_VERSION:
        raise ContractError("unsupported_session_contract", "Manifest is not standalone contract 1")
    context = payload.get("context")
    if (
        not isinstance(context, dict)
        or context.get("container_validation") not in {"siril", "strict"}
    ):
        raise ContractError(
            "unsupported_session_contract",
            "Session container validation mode is invalid",
        )
    execution_policy = payload.get("execution_policy")
    if execution_policy is not None and execution_policy != EXECUTION_POLICY:
        raise ContractError(
            "unsupported_session_contract",
            "Session execution policy is not supported",
        )
    if manifest.get("input_sha256") != payload.get("input", {}).get("sha256"):
        raise ContractError("session_hash_drift", "Manifest input differs from session input")
    if not fingerprint_matches(payload.get("input")):
        raise ContractError("input_hash_drift", "Original stacked master changed")
    probe_record = payload.get("tool_probe")
    if (
        not isinstance(probe_record, dict)
        or set(probe_record) != {"path", "sha256"}
        or probe_record.get("path") != "reports/tool-probe.json"
        or HASH_PATTERN.fullmatch(str(probe_record.get("sha256", ""))) is None
    ):
        raise ContractError("session_hash_drift", "Session tool probe binding is invalid")
    probe_path = session_path(
        session,
        session / "reports" / "tool-probe.json",
        must_exist=True,
        allowed_roots=("reports",),
    )
    if sha256_file(probe_path) != probe_record["sha256"]:
        raise ContractError("session_hash_drift", "Frozen tool probe changed")
    _verify_session_knowledge(session, payload)
    return session, payload, manifest


def _receipt_bindings_unchanged(
    session: Path,
    payload: dict[str, Any],
    receipt: dict[str, Any],
) -> bool:
    def session_file_matches(record: Any, roots: Sequence[str]) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            return False
        try:
            path = session_path(
                session,
                session / record["path"],
                must_exist=True,
                allowed_roots=roots,
            )
        except ContractError:
            return False
        return fingerprint_matches({**record, "path": str(path)})

    def skill_file_matches(record: Any) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            return False
        try:
            capture = read_skill_file(SKILL_ROOT, record["path"])
        except BundleError:
            return False
        if capture.sha256 == record.get("sha256") and capture.size_bytes == record.get("size"):
            return True
        trusted = _LEGACY_REFERENCE_FINGERPRINTS.get(record["path"], set())
        return bool(
            not execution_policy_is_current(payload)
            and (record.get("sha256"), record.get("size")) in trusted
        )

    def current_receipt_evidence_matches() -> bool:
        if not execution_policy_is_current(payload):
            return True
        log_record = receipt.get("log")
        if not session_file_matches(log_record, ("logs",)):
            return False
        assert isinstance(log_record, dict)
        try:
            log_path = session_path(
                session,
                session / str(log_record["path"]),
                must_exist=True,
                allowed_roots=("logs",),
            )
            log_text = log_path.read_text(encoding="utf-8")
            script_record = receipt.get("script")
            if not isinstance(script_record, dict):
                return False
            script_path = session_path(
                session,
                session / str(script_record.get("path", "")),
                must_exist=True,
                allowed_roots=("scripts",),
            )
            current_network = classify_siril_network(
                script_path.read_text(encoding="utf-8"),
                protocol=str(receipt.get("protocol", "")),
                session_offline=bool(payload.get("context", {}).get("offline")),
            )
        except (ContractError, OSError, TypeError, UnicodeError, ValueError):
            return False
        validations = receipt.get("output_validations")
        if not isinstance(validations, dict) or not validations:
            return False
        outputs_valid = all(
            isinstance(result, dict) and result.get("passed") is True
            for result in validations.values()
        )
        display_names = [
            Path(path).name
            for path, result in validations.items()
            if isinstance(path, str)
            and Path(path).suffix.lower() in DISPLAY_IMAGE_SUFFIXES
            and isinstance(result, dict)
            and result.get("passed") is True
        ]
        diagnostics = artifacts.diagnose_siril_log(
            log_text,
            exit_code=int(receipt.get("exit_code", -1)),
            timed_out=receipt.get("timed_out") is True,
            execution_valid=(
                receipt.get("exit_code") == 0
                and receipt.get("timed_out") is False
                and outputs_valid
            ),
            validated_display_names=display_names,
        )
        runtime = receipt.get("runtime")
        invocation = receipt.get("invocation")
        return bool(
            receipt.get("log_diagnostics") == diagnostics
            and isinstance(runtime, dict)
            and runtime.get("offline")
            == bool(payload.get("context", {}).get("offline"))
            and runtime.get("network") == current_network
            and isinstance(invocation, list)
            and all(isinstance(value, str) for value in invocation)
            and ("--offline" in invocation)
            == bool(current_network["effective_offline"])
        )

    knowledge = receipt.get("knowledge_validation")
    current = _knowledge_record(payload)
    if (
        receipt.get("knowledge_bindings_unchanged") is not True
        or receipt.get("script_unchanged") is not True
        or receipt.get("source_unchanged") is not True
        or not session_file_matches(receipt.get("script"), ("scripts",))
        or not session_file_matches(receipt.get("script_provenance"), ("scripts",))
        or not isinstance(knowledge, dict)
        or any(knowledge.get(key) != value for key, value in current.items())
        or not session_file_matches(knowledge.get("bundle_evidence"), ("reports",))
        or not skill_file_matches(knowledge.get("command_policy"))
        or not current_receipt_evidence_matches()
    ):
        return False
    references = knowledge.get("references")
    evidence = knowledge.get("manual_evidence")
    command_knowledge = receipt.get("command_knowledge")
    receipt_commands = receipt.get("commands")
    if not isinstance(receipt_commands, list) or any(
        not isinstance(command, str) for command in receipt_commands
    ):
        return False
    unique_commands = list(dict.fromkeys(receipt_commands))
    mapped_commands = (
        command_knowledge.get("commands")
        if isinstance(command_knowledge, dict)
        else None
    )
    valid_command_entries = bool(
        isinstance(mapped_commands, list)
        and all(
            isinstance(item, dict)
            and set(item)
            == {
                "command",
                "path",
                "source_sha256",
                "section_id",
                "entry_sha256",
                "scriptable",
                "policy_authorized",
            }
            and item.get("scriptable") is True
            and item.get("policy_authorized") is True
            and HASH_PATTERN.fullmatch(str(item.get("source_sha256", "")))
            is not None
            and HASH_PATTERN.fullmatch(str(item.get("entry_sha256", "")))
            is not None
            for item in mapped_commands
        )
    )
    return bool(
        isinstance(references, list)
        and references
        and all(skill_file_matches(record) for record in references)
        and isinstance(evidence, list)
        and all(session_file_matches(record, ("reports",)) for record in evidence)
        and isinstance(command_knowledge, dict)
        and command_knowledge.get("manual") == current["manual"]
        and command_knowledge.get("command_policy") == current["command_policy"]
        and command_knowledge.get("protocol") == receipt.get("protocol")
        and valid_command_entries
        and [str(item.get("command", "")).lower() for item in mapped_commands]
        == unique_commands
    )


def _verified_success_receipts(session: Path) -> list[dict[str, Any]]:
    manifest = load_json(session / "manifest.json")
    session_payload = load_json(session / "session.json")
    entries = manifest.get("runs")
    if manifest.get("schema") != f"{SCHEMA_PREFIX}.manifest.v1" or not isinstance(entries, list):
        raise ContractError("manifest_invalid", "Manifest run index is invalid")
    receipts: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("status") != "success":
            continue
        name = entry.get("receipt")
        if not isinstance(name, str) or Path(name).name != name:
            raise ContractError("manifest_invalid", "Manifest receipt path is invalid")
        receipt_path = session_path(
            session,
            session / "runs" / name,
            must_exist=True,
            allowed_roots=("runs",),
        )
        if entry.get("receipt_sha256") != sha256_file(receipt_path):
            raise ContractError("manifest_invalid", f"Run receipt changed: {name}")
        receipt = load_json(receipt_path)
        if (
            receipt.get("schema") != f"{SCHEMA_PREFIX}.run-receipt.v1"
            or receipt.get("status") != "success"
            or receipt.get("id") != entry.get("id")
            or not _receipt_bindings_unchanged(session, session_payload, receipt)
        ):
            raise ContractError("manifest_invalid", f"Run receipt is invalid: {name}")
        receipts.append(receipt)
    return receipts


def _verified_run_outputs(session: Path) -> set[Path]:
    outputs: set[Path] = set()
    for receipt in _verified_success_receipts(session):
        for record in receipt.get("outputs", []):
            if not isinstance(record, dict) or not isinstance(record.get("path"), str):
                raise ContractError("manifest_invalid", "Run output record is invalid")
            candidate = session_path(session, session / record["path"], must_exist=True)
            if not fingerprint_matches({**record, "path": str(candidate)}):
                raise ContractError("manifest_invalid", f"Run output changed: {record['path']}")
            outputs.add(candidate.resolve())
    return outputs


def _source_is_bound(session: Path, payload: dict[str, Any], source: Path) -> bool:
    original = payload["input"]
    if source.resolve() == Path(original["path"]).resolve():
        return fingerprint_matches(original)
    return source.resolve() in _verified_run_outputs(session)


def _append_run(manifest_path: Path, manifest: dict[str, Any], receipt_path: Path, receipt: dict[str, Any]) -> None:
    runs = manifest.get("runs")
    if not isinstance(runs, list):
        raise ContractError("manifest_invalid", "Manifest run index is invalid")
    runs.append(
        {
            "id": receipt["id"],
            "status": receipt["status"],
            "receipt": receipt_path.name,
            "receipt_sha256": sha256_file(receipt_path),
        }
    )
    manifest["updated_at"] = utc_now()
    atomic_write_json(manifest_path, manifest)

__all__ = [
    "execution_policy_is_current",
    "init_session",
    "load_session",
]
