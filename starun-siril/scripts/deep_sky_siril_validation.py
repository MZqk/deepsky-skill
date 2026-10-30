#!/usr/bin/env python3
"""Static Siril script and protocol validation for standalone v1."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
import shlex
from typing import Any, Sequence

import deep_sky_siril_artifacts as artifacts
import deep_sky_siril_session as session_state
import deep_sky_siril_tooling as tooling
import query_siril_manual as manual_query
from deep_sky_siril_contract import (
    CONTRACT_VERSION,
    HASH_PATTERN,
    PROTOCOL_STAGE_REFERENCES,
    RUN_ID_PATTERN,
    SCHEMA_PREFIX,
    SKILL_ROOT,
    SCRIPTS_ROOT,
    ContractError,
    _absolute,
    classify_siril_network,
    fingerprint,
    fingerprint_matches,
    load_json,
    protocol_reference_bindings,
    relative_session_path,
    session_path,
    sha256_file,
)
from siril_manual_bundle import BundleError, read_skill_file, strict_json_bytes

_PIXELMATH_PATH = re.compile(r"\$([^$]+)\$")


def _policy_snapshot() -> tuple[dict[str, Any], dict[str, Any]]:
    relative = "references/command-policy.json"
    try:
        capture = read_skill_file(SKILL_ROOT, relative)
        policy = strict_json_bytes(capture.data, document=relative)
    except BundleError as exc:
        raise ContractError("command_policy_invalid", str(exc)) from exc
    if not isinstance(policy, dict):
        raise ContractError("command_policy_invalid", "Command policy must be an object")
    if policy.get("schema") != f"{SCHEMA_PREFIX}.command-policy.v1" or policy.get("contract_version") != CONTRACT_VERSION:
        raise ContractError(
            "command_policy_invalid",
            "Command policy does not match standalone v1",
        )
    return policy, {
        "path": relative,
        "sha256": capture.sha256,
        "size": capture.size_bytes,
    }


def _policy() -> dict[str, Any]:
    return _policy_snapshot()[0]


def public_protocols() -> tuple[str, ...]:
    return tuple(_policy()["protocol_commands"])


def _effective_lines(script_text: str) -> list[str]:
    return [
        line.strip()
        for line in script_text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _option_value(tokens: Sequence[str], name: str) -> str | None:
    prefix = f"-{name}="
    for token in tokens[1:]:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _normalized_write(command: str, value: str) -> Path:
    path = _absolute(value)
    if command == "save" and path.suffix.lower() not in {".fit", ".fits", ".fts", ".tif", ".tiff", ".xisf"}:
        path = path.with_suffix(".fit")
    elif command == "savejpg" and path.suffix.lower() not in {".jpg", ".jpeg"}:
        path = path.with_suffix(".jpg")
    elif command == "savetif32" and path.suffix.lower() not in {".tif", ".tiff"}:
        path = path.with_suffix(".tif")
    elif command == "split":
        # Siril appends its FITS extension even when the supplied stem has a suffix.
        path = Path(str(path) + ".fit")
    return path.resolve(strict=False)


def _require_known_load(
    value: str,
    *,
    session: Path,
    source: Path,
    generated: set[Path],
    verified_inputs: set[Path],
) -> None:
    path = _absolute(value).resolve(strict=False)
    if path == source.resolve() or path in generated or path in verified_inputs:
        return
    session_path(session, path)
    raise ContractError("unsafe_siril_script", f"load reads an unverified session artifact: {path}")


def _validate_background_sample_contract(
    contract_path: Path,
    source: Path,
) -> None:
    contract = load_json(contract_path)
    required = {"schema", "source", "fit_samples"}
    if (
        set(contract) != required
        or contract.get("schema") != f"{SCHEMA_PREFIX}.background-sample-contract.v1"
    ):
        raise ContractError("unsafe_siril_script", "Background sample contract is invalid")
    source_record = contract.get("source")
    if not isinstance(source_record, dict) or set(source_record) != {"path", "sha256", "width", "height"}:
        raise ContractError("unsafe_siril_script", "Background source binding is invalid")
    source_path = source_record.get("path")
    source_sha256 = source_record.get("sha256")
    if (
        not isinstance(source_path, str)
        or not 1 <= len(source_path) <= 500
        or not source_path.strip()
        or Path(source_path).expanduser().resolve(strict=False) != source.resolve()
    ):
        raise ContractError("unsafe_siril_script", "Background contract is bound to another source")
    if (
        not isinstance(source_sha256, str)
        or HASH_PATTERN.fullmatch(source_sha256) is None
        or source_sha256 != sha256_file(source)
    ):
        raise ContractError("unsafe_siril_script", "Background contract source hash changed")
    width = source_record.get("width")
    height = source_record.get("height")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width < 1
        or height < 1
    ):
        raise ContractError("unsafe_siril_script", "Background source dimensions are invalid")
    try:
        pixels, _ = artifacts.read_scientific_pixels(source)
        geometry = {'width': pixels.shape[2], 'height': pixels.shape[1]}
    except (artifacts._ArtifactValidationError, ValueError, OSError) as exc:
        raise ContractError(
            "unsafe_siril_script",
            "Background source geometry cannot be verified: " + str(exc),
        ) from exc
    if geometry["width"] != width or geometry["height"] != height:
        raise ContractError("unsafe_siril_script", "Background source dimensions changed")

    sample_ids: set[str] = set()
    positions: set[tuple[float, float]] = set()
    samples = contract.get("fit_samples")
    if not isinstance(samples, list) or not 1 <= len(samples) <= 256:
        raise ContractError("unsafe_siril_script", "Background fit_samples count is invalid")
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"id", "x", "y"}:
            raise ContractError("unsafe_siril_script", "Background fit_samples entry is invalid")
        identifier = sample["id"]
        x = sample["x"]
        y = sample["y"]
        if (
            not isinstance(identifier, str)
            or re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", identifier) is None
            or identifier in sample_ids
            or isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(float(x))
            or not math.isfinite(float(y))
        ):
            raise ContractError("unsafe_siril_script", "Background fit_samples sample is invalid")
        position = (float(x), float(y))
        if (
            position in positions
            or not 0 <= position[0] < width
            or not 0 <= position[1] < height
        ):
            raise ContractError("unsafe_siril_script", "Background fit_samples sample is unsafe")
        sample_ids.add(identifier)
        positions.add(position)


def _validate_pyscript(
    protocol: str,
    tokens: Sequence[str],
    *,
    session: Path,
    source: Path,
    policy: dict[str, Any],
    probe: dict[str, Any] | None = None,
    replaying: bool = False,
    run_id: str | None = None,
    execution_policy: dict[str, Any] | None = None,
) -> Path:
    expected_name = policy["pyscript_protocols"].get(protocol)
    if not expected_name or len(tokens) != 10:
        raise ContractError("unsafe_siril_script", f"pyscript is forbidden for {protocol}")
    if Path(tokens[1]).expanduser().is_symlink():
        raise ContractError("unsafe_siril_script", "Pinned bridge cannot be a symlink")
    adapter = Path(tokens[1]).expanduser().resolve(strict=False)
    expected_names = expected_name if isinstance(expected_name, list) else [expected_name]
    if any(not isinstance(name, str) or Path(name).is_absolute() or Path(name).name != name for name in expected_names):
        raise ContractError("unsafe_siril_script", "pyscript adapter is not the bundled audited bridge")
    expected = SCRIPTS_ROOT / adapter.name
    if adapter.name not in expected_names:
        raise ContractError("unsafe_siril_script", "pyscript adapter is not the bundled audited bridge")
    if adapter != expected or not expected.is_file() or expected.is_symlink():
        raise ContractError("unsafe_siril_script", "pyscript adapter is not the bundled audited bridge")
    option_names = tokens[2::2]
    required_options = ["--contract", "--contract-sha256", "--expected-source", "--receipt"]
    if option_names != required_options:
        raise ContractError("unsafe_siril_script", "Pinned bridge options are incomplete or reordered")
    values = dict(zip(option_names, tokens[3::2], strict=True))
    contract_path = session_path(
        session,
        values["--contract"],
        must_exist=True,
        allowed_roots=("reports",),
    )
    receipt_path = session_path(session, values["--receipt"], allowed_roots=("reports",))
    if receipt_path.parent != contract_path.parent:
        raise ContractError("unsafe_siril_script", "Bridge contract and receipt directories differ")
    if (receipt_path.exists() and not replaying) or receipt_path.is_symlink():
        raise ContractError("unsafe_siril_script", "Bridge receipt path is unsafe or already exists")
    if protocol == "stars.separate" and contract_path.parent.name != run_id:
        raise ContractError("unsafe_siril_script", "StarNet contract directory must match the SSF run ID")
    contract_sha = values["--contract-sha256"]
    if HASH_PATTERN.fullmatch(contract_sha) is None or sha256_file(contract_path) != contract_sha:
        raise ContractError("unsafe_siril_script", "Bridge contract SHA-256 is missing or changed")
    if Path(values["--expected-source"]).expanduser().resolve(strict=False) != source.resolve():
        raise ContractError("unsafe_siril_script", "Bridge source differs from run source")
    if protocol == "background.subtract":
        background_policy = (execution_policy or {}).get('background', 'heldout_material_gate_v3')
        required_name = ('siril_background_samples_scientific.py' if background_policy in {'heldout_material_gate_v4', 'heldout_review_gate_v5'}
                         else 'siril_background_samples.py')
        if adapter.name != required_name:
            raise ContractError('unsafe_siril_script', 'Background bridge differs from the frozen policy route')
        _validate_background_sample_contract(contract_path, source)
    elif protocol == 'restoration.denoise':
        if (execution_policy or {}).get('linear_denoise_backend') != 'deepsnr_primary_v1':
            raise ContractError('unsafe_siril_script', 'DeepSNR bridge requires the frozen Stage 5 backend policy')
        from siril_deepsnr import validate_contract
        try:
            document = load_json(contract_path)
            validate_contract(document, source, (probe or {}).get('tools', {}).get('deepsnr', {}),
                              session, run_id, replaying=replaying)
        except (ValueError, KeyError, TypeError) as exc:
            raise ContractError('unsafe_siril_script', str(exc)) from exc
    elif protocol == 'restoration.deconvolve':
        from siril_psf_samples import validate_contract
        try:
            document = validate_contract(load_json(contract_path), source, contract_path.parent)
            from deep_sky_siril_artifacts import fits_geometry
            if source.suffix.lower() in {'.fit', '.fits', '.fts'}:
                geometry = fits_geometry(source)
                if any(geometry[k] != document['source'][k] for k in ('width', 'height', 'channels')):
                    raise ValueError('PSF contract geometry differs from the scientific source')
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise ContractError('unsafe_siril_script', str(exc)) from exc
        if contract_path.parent.name != run_id:
            raise ContractError('unsafe_siril_script', 'PSF contract directory must match the SSF run ID')
    else:
        runtime = (probe or {}).get("tools", {}).get("starnet", {})
        native = runtime.get("version") == "2.6.2"
        required_adapter = SCRIPTS_ROOT / ("siril_starnet_linear.py" if native else "siril_starnet.py")
        if adapter != required_adapter or runtime.get("adapter", {}).get("path") != str(adapter):
            raise ContractError("unsafe_siril_script", "StarNet bridge differs from the frozen version route")
        if native:
            from siril_starnet_linear import validate_contract, load_json as load_starnet_json
        else:
            from siril_starnet import validate_contract
            load_starnet_json = load_json
        try:
            document = load_starnet_json(contract_path)
            validate_contract(document, source, (probe or {})["tools"]["starnet"], session, contract_path.parent.name, replaying=replaying)
        except (ValueError, KeyError, TypeError) as exc:
            raise ContractError("unsafe_siril_script", str(exc)) from exc
    return receipt_path


def _validate_runtime_setting(
    protocol: str,
    tokens: Sequence[str],
    *,
    probe: dict[str, Any],
) -> str:
    if len(tokens) != 2 or "=" not in tokens[1]:
        raise ContractError("unsafe_siril_script", "set requires one variable=value argument")
    key, value = tokens[1].split("=", 1)
    if protocol == "color.calibrate" and key == "core.catalogue_gaia_photo":
        local = probe.get("tools", {}).get("local_gaia", {})
        if not isinstance(local, dict) or local.get("compatible") is not True:
            raise ContractError("runtime_dependency_missing", "Local Gaia catalogue is unavailable")
        expected = local.get("path")
    elif protocol == "astrometry.solve" and key == "core.catalogue_gaia_astro":
        local = probe.get("tools", {}).get("local_gaia_astro", {})
        if not local.get("compatible"):
            raise ContractError("local_gaia_astro_missing", "Local Gaia astrometric catalogue is unavailable")
        expected = local.get("path")
    else:
        raise ContractError("unsafe_siril_script", f"Setting {key or '<empty>'} is forbidden for {protocol}")
    if not isinstance(expected, str) or Path(value).expanduser().resolve(strict=False) != Path(expected).resolve():
        raise ContractError("unsafe_siril_script", f"Setting {key} differs from the frozen probe")
    return key


def _provenance_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ContractError("ssf_provenance_invalid", f"{label} is invalid")
    return value


def _provenance_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or HASH_PATTERN.fullmatch(value) is None:
        raise ContractError("ssf_provenance_invalid", f"{label} is not a SHA-256")
    return value


def _strict_session_json(path: Path, label: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ContractError("ssf_provenance_invalid", f"Cannot read {label}: {path}") from exc
    record = {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "path": "",
    }
    try:
        document = strict_json_bytes(raw, document=label)
    except BundleError as exc:
        raise ContractError("ssf_provenance_invalid", f"{label} is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise ContractError("ssf_provenance_invalid", f"{label} must be a JSON object")
    current = fingerprint(path, include_path=False)
    if current != {"sha256": record["sha256"], "size": record["size"]}:
        raise ContractError("ssf_provenance_invalid", f"{label} changed while being read")
    return document, record


def _capture_reference(relative: str, *, role: str) -> dict[str, Any]:
    if (
        not isinstance(relative, str)
        or not relative.startswith("references/")
        or len(relative) > 300
        or "\\" in relative
        or Path(relative).is_absolute()
        or any(part in {"", ".", ".."} for part in relative.split("/"))
        or relative == "references/siril-manual"
        or relative.startswith("references/siril-manual/")
    ):
        raise ContractError(
            "ssf_provenance_invalid",
            f"Reference path is unsafe or bypasses manual query evidence: {relative!r}",
        )
    try:
        capture = read_skill_file(SKILL_ROOT, relative)
    except BundleError as exc:
        raise ContractError(
            "ssf_provenance_invalid",
            f"Referenced Skill file is missing or unsafe: {relative}",
        ) from exc
    return {
        "path": relative,
        "sha256": capture.sha256,
        "size": capture.size_bytes,
        "role": role,
    }


def validate_ssf_provenance(
    provenance: Path,
    *,
    session: Path,
    session_payload: dict[str, Any],
    protocol: str,
    script: Path,
    script_fingerprint: dict[str, Any],
    commands: Sequence[str],
    policy_fingerprint: dict[str, Any],
    replaying: bool = False,
    script_text: str | None = None,
) -> dict[str, Any]:
    """Validate one Agent-authored sidecar without generating or repairing it."""

    expected_provenance = script.with_suffix(".provenance.json")
    if provenance != expected_provenance or provenance.is_symlink() or not provenance.is_file():
        raise ContractError(
            "ssf_provenance_missing",
            "run requires a same-stem scripts/<run-id>.provenance.json sidecar",
        )
    document, provenance_record = _strict_session_json(provenance, "SSF provenance")
    provenance_record["path"] = relative_session_path(session, provenance)
    required = {
        "schema",
        "contract_version",
        "run_id",
        "protocol",
        "script",
        "references",
        "command_policy",
        "manual_lookup",
        "rationale",
    }
    if (
        set(document) != required
        or document.get("schema") != f"{SCHEMA_PREFIX}.ssf-provenance.v1"
        or document.get("contract_version") != CONTRACT_VERSION
        or document.get("run_id") != script.stem
        or RUN_ID_PATTERN.fullmatch(str(document.get("run_id", ""))) is None
        or document.get("protocol") != protocol
    ):
        raise ContractError(
            "ssf_provenance_invalid",
            "SSF provenance identity or standalone v1 schema is invalid",
        )

    script_binding = document.get("script")
    expected_script_binding = {
        "path": relative_session_path(session, script),
        "sha256": script_fingerprint["sha256"],
    }
    if not isinstance(script_binding, dict) or script_binding != expected_script_binding:
        raise ContractError(
            "ssf_provenance_invalid",
            "SSF provenance is bound to another script or script hash",
        )

    supplied_policy = document.get("command_policy")
    expected_policy = {
        "path": "references/command-policy.json",
        "sha256": policy_fingerprint["sha256"],
    }
    session_policy = session_payload.get("knowledge", {}).get("command_policy")
    if (
        not isinstance(supplied_policy, dict)
        or supplied_policy != expected_policy
        or not isinstance(session_policy, dict)
        or session_policy.get("path") != expected_policy["path"]
        or session_policy.get("sha256") != expected_policy["sha256"]
    ):
        raise ContractError(
            "ssf_provenance_invalid",
            "SSF provenance command policy binding is invalid or changed",
        )

    raw_references = document.get("references")
    if not isinstance(raw_references, list) or not 1 <= len(raw_references) <= 32:
        raise ContractError("ssf_provenance_invalid", "SSF provenance references are invalid")
    reference_records: list[dict[str, Any]] = []
    committed_replay_references: list[dict[str, Any]] = []
    seen_references: set[str] = set()
    primary_paths: list[str] = []
    for index, raw_reference in enumerate(raw_references):
        if not isinstance(raw_reference, dict) or set(raw_reference) != {"path", "sha256", "role"}:
            raise ContractError(
                "ssf_provenance_invalid",
                f"SSF provenance reference {index} is invalid",
            )
        path = _provenance_text(raw_reference.get("path"), f"references[{index}].path", 300)
        folded = path.casefold()
        role = raw_reference.get("role")
        if folded in seen_references or role not in {"primary", "supporting"}:
            raise ContractError(
                "ssf_provenance_invalid",
                "SSF provenance references are duplicated or have an invalid role",
            )
        seen_references.add(folded)
        captured = _capture_reference(path, role=str(role))
        if replaying and raw_reference.get("sha256") != captured["sha256"]:
            previous = session_state.committed_replay_reference(session, session_payload, script.stem, raw_reference, protocol)
            if previous is not None:
                captured = previous
                committed_replay_references.append(previous)
        if _provenance_hash(raw_reference.get("sha256"), f"references[{index}].sha256") != captured["sha256"]:
            raise ContractError(
                "ssf_provenance_invalid",
                f"SSF provenance reference hash changed: {path}",
            )
        reference_records.append(captured)
        if role == "primary":
            primary_paths.append(path)
    expected_primary = PROTOCOL_STAGE_REFERENCES[protocol][0]
    if replaying and protocol == 'restoration.denoise-nonlinear' and not session_payload.get('execution_policy', {}).get('chroma_denoise'):
        expected_primary = 'references/stages/stage-10-delivery-finishing.md'
    if primary_paths != [expected_primary]:
        raise ContractError(
            "ssf_provenance_invalid",
            f"SSF provenance requires exactly one primary reference: {expected_primary}",
        )

    required_supporting = list(PROTOCOL_STAGE_REFERENCES[protocol][1:])
    if protocol == 'stretch' and session_payload.get('execution_policy', {}).get('stage7_references') == 'split_variants_v1':
        from deep_sky_siril_hdr import declaration as hdr_declaration
        from deep_sky_siril_dualband import declaration as hoo_declaration
        text = script.read_text(encoding='utf-8') if script_text is None else script_text
        hdr = hdr_declaration(text)
        hoo = hoo_declaration(text)
        if hdr is not None and hoo is not None:
            raise ContractError('ssf_provenance_invalid', 'A stretch script cannot combine HDR and HOO variants')
        variant = 'hdr' if hdr is not None else 'hoo' if hoo is not None else None
        if variant:
            required_supporting.extend(record['path'] for record in protocol_reference_bindings(protocol, variant=variant)[1:])
    for stage_path in required_supporting:
        if not any(record["path"] == stage_path and record["role"] == "supporting"
                   for record in reference_records):
            raise ContractError(
                "ssf_provenance_invalid",
                f"SSF provenance requires a supporting stage body: {stage_path}",
            )

    manual_lookup = document.get("manual_lookup")
    if not isinstance(manual_lookup, dict) or set(manual_lookup) != {"status", "reason", "evidence"}:
        raise ContractError("ssf_provenance_invalid", "manual_lookup is invalid")
    lookup_status = manual_lookup.get("status")
    _provenance_text(manual_lookup.get("reason"), "manual_lookup.reason", 2000)
    raw_evidence = manual_lookup.get("evidence")
    if (
        lookup_status not in {"not_needed", "performed"}
        or not isinstance(raw_evidence, list)
        or len(raw_evidence) > 32
        or (lookup_status == "not_needed" and raw_evidence)
        or (lookup_status == "performed" and not raw_evidence)
    ):
        raise ContractError(
            "ssf_provenance_invalid",
            "manual_lookup status and evidence cardinality are inconsistent",
        )
    evidence_records: list[dict[str, Any]] = []
    evidence_documents: list[dict[str, Any]] = []
    seen_evidence: set[str] = set()
    for index, raw_record in enumerate(raw_evidence):
        if not isinstance(raw_record, dict) or set(raw_record) != {"path", "sha256"}:
            raise ContractError(
                "ssf_provenance_invalid",
                f"manual_lookup evidence {index} is invalid",
            )
        relative = _provenance_text(raw_record.get("path"), f"manual_lookup.evidence[{index}].path", 300)
        if (
            not relative.startswith("reports/manual-evidence/")
            or not relative.endswith(".json")
            or "\\" in relative
            or Path(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))
            or relative.casefold() in seen_evidence
        ):
            raise ContractError(
                "ssf_provenance_invalid",
                "Manual evidence path is outside reports/manual-evidence or duplicated",
            )
        seen_evidence.add(relative.casefold())
        try:
            evidence_path = session_path(
                session,
                session / relative,
                must_exist=True,
                allowed_roots=("reports",),
            )
        except (ContractError, OSError) as exc:
            raise ContractError(
                "ssf_provenance_invalid",
                f"Manual evidence path is missing or unsafe: {relative}",
            ) from exc
        evidence_document, evidence_fingerprint = _strict_session_json(
            evidence_path,
            f"manual evidence {relative}",
        )
        evidence_fingerprint["path"] = relative
        if _provenance_hash(raw_record.get("sha256"), f"manual_lookup.evidence[{index}].sha256") != evidence_fingerprint["sha256"]:
            raise ContractError(
                "ssf_provenance_invalid",
                f"Manual evidence hash changed: {relative}",
            )
        evidence_documents.append(evidence_document)
        evidence_records.append(evidence_fingerprint)
    if evidence_documents:
        try:
            verified_documents = manual_query.verify_query_evidence_documents(
                evidence_documents,
                skill_root=SKILL_ROOT,
            )
        except BundleError as exc:
            raise ContractError(
                "ssf_provenance_invalid",
                f"Manual query evidence is invalid: {exc}",
            ) from exc
        for record, verified_document in zip(
            evidence_records,
            verified_documents,
            strict=True,
        ):
            record["mode"] = verified_document["mode"]
            if verified_document["manual"] != session_payload.get("knowledge", {}).get("manual"):
                raise ContractError(
                    "ssf_provenance_invalid",
                    "Manual evidence is bound to another Bundle",
                )
            if verified_document["mode"] == "command":
                result = verified_document["result"]
                execution_policy = result.get("execution_policy")
                command = str(result.get("command", "")).lower()
                if (
                    not isinstance(execution_policy, dict)
                    or execution_policy.get("state") != "allowed"
                    or protocol not in execution_policy.get("allowed_protocols", [])
                    or execution_policy.get("policy_sha256") != policy_fingerprint["sha256"]
                    or command not in commands
                ):
                    raise ContractError(
                        "ssf_provenance_invalid",
                        "Manual command evidence is not authorized for this script and protocol",
                    )

    rationale = document.get("rationale")
    if not isinstance(rationale, dict) or set(rationale) != {"applicability", "parameter_choices"}:
        raise ContractError("ssf_provenance_invalid", "SSF provenance rationale is invalid")
    _provenance_text(rationale.get("applicability"), "rationale.applicability", 2000)
    parameter_choices = rationale.get("parameter_choices")
    if (
        not isinstance(parameter_choices, list)
        or not 1 <= len(parameter_choices) <= 64
        or any(
            not isinstance(value, str) or not value.strip() or len(value) > 1000
            for value in parameter_choices
        )
    ):
        raise ContractError("ssf_provenance_invalid", "rationale.parameter_choices is invalid")

    knowledge = session_state._knowledge_record(session_payload)
    return {
        "script_provenance": provenance_record,
        **({"committed_replay_references": committed_replay_references} if committed_replay_references else {}),
        "knowledge_validation": {
            **knowledge,
            "references": reference_records,
            "manual_evidence": evidence_records,
        },
    }


def script_binding_unchanged(
    session: Path,
    validation: dict[str, Any],
) -> bool:
    record = validation.get("script_fingerprint")
    if not isinstance(record, dict) or not isinstance(record.get("path"), str):
        return False
    try:
        path = session_path(
            session,
            session / record["path"],
            must_exist=True,
            allowed_roots=("scripts",),
        )
    except ContractError:
        return False
    return fingerprint_matches({**record, "path": str(path)})


def knowledge_bindings_unchanged(
    session: Path,
    validation: dict[str, Any],
) -> bool:
    """Recheck provenance and knowledge bytes captured before execution."""

    def matches_session(record: Any, allowed_roots: Sequence[str]) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            return False
        try:
            path = session_path(
                session,
                session / record["path"],
                must_exist=True,
                allowed_roots=allowed_roots,
            )
        except ContractError:
            return False
        return fingerprint_matches({**record, "path": str(path)})

    def matches_skill(record: Any) -> bool:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            return False
        try:
            capture = read_skill_file(SKILL_ROOT, record["path"])
        except BundleError:
            return False
        if record in validation.get("committed_replay_references", []):
            return True
        return capture.sha256 == record.get("sha256") and capture.size_bytes == record.get("size")

    provenance_record = validation.get("script_provenance")
    knowledge = validation.get("knowledge_validation")
    if (
        not matches_session(provenance_record, ("scripts",))
        or not isinstance(knowledge, dict)
        or not matches_session(knowledge.get("bundle_evidence"), ("reports",))
        or not matches_skill(knowledge.get("command_policy"))
    ):
        return False
    references = knowledge.get("references")
    evidence = knowledge.get("manual_evidence")
    return bool(
        isinstance(references, list)
        and all(matches_skill(record) for record in references)
        and isinstance(evidence, list)
        and all(matches_session(record, ("reports",)) for record in evidence)
    )


def validate_script_file(
    script: Path,
    *,
    session: Path,
    protocol: str,
    source: Path,
    expected_outputs: Sequence[Path],
    probe: dict[str, Any],
    provenance: Path | None = None,
    session_payload: dict[str, Any] | None = None,
    replaying: bool = False,
) -> dict[str, Any]:
    provenance = provenance if provenance is not None else script.with_suffix(".provenance.json")
    session_payload = (
        session_payload
        if session_payload is not None
        else load_json(session / "session.json")
    )
    policy, policy_fingerprint = _policy_snapshot()
    allowed = policy.get("protocol_commands", {}).get(protocol)
    if not isinstance(allowed, list):
        raise ContractError("protocol_unknown", f"Unknown protocol: {protocol}")
    protocol_runtime = (None if protocol == 'restoration.deconvolve'
                        else tooling._require_protocol_runtime_dependency(protocol, probe))
    if script.is_symlink() or not script.is_file():
        raise ContractError("script_missing", f"Siril script is missing or unsafe: {script}")
    raw = script.read_bytes()
    if len(raw) > int(policy["max_script_bytes"]):
        raise ContractError("unsafe_siril_script", "Siril script exceeds the size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("unsafe_siril_script", "Siril script is not UTF-8") from exc
    if "\0" in text or "\r" in text:
        raise ContractError("unsafe_siril_script", "Siril script contains control characters")
    from deep_sky_siril_processing import local_saturation_declaration
    local_parameters = local_saturation_declaration(text) if protocol == 'color.finish' else None
    local_saturation = local_parameters is not None
    lines = _effective_lines(text)
    prefix = policy["required_prefix"]
    if lines[: len(prefix)] != prefix:
        raise ContractError(
            "unsafe_siril_script",
            "Siril script lacks the exact standalone v1 prefix",
        )
    if len(lines) > int(policy["max_effective_lines"]):
        raise ContractError("unsafe_siril_script", "Siril script has too many commands")

    expected = {path.resolve(strict=False) for path in expected_outputs}
    generated: set[Path] = set()
    verified_inputs = session_state._verified_run_outputs(session)
    if session_payload.get("input", {}).get("path"):
        verified_inputs.add(Path(session_payload["input"]["path"]).resolve())
    if protocol == 'stars.recompose':
        from deep_sky_star_reduction import contour_input, bound_file
        contour = contour_input(session, session_payload, text)
        if contour:
            verified_inputs.add(bound_file(session, contour[0]['weights']))
    spatial_reference = local_parameters.get('subject_mask') if local_parameters else None
    if protocol == 'structure.local-contrast':
        declarations = [row.split(':',1)[1] for row in text.splitlines() if row.startswith('# local-contrast:')]
        if len(declarations) == 1:
            declared = strict_json_bytes(declarations[0].encode(),document='local contrast declaration')
            if isinstance(declared,dict):
                spatial_reference = declared.get('subject_mask')
    if spatial_reference is not None:
        from deep_sky_siril_subject_mask import load as load_subject_mask
        spatial = load_subject_mask(session,source,spatial_reference)[0]
        verified_inputs.add(session_path(session,spatial['mask']['path'],must_exist=True,allowed_roots=('reports',)))
    commands: list[str] = []
    runtime_settings: set[str] = set()
    adapters: list[dict[str, Any]] = []
    for line in lines:
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            raise ContractError("unsafe_siril_script", f"Cannot parse Siril command: {line[:120]}") from exc
        if not tokens:
            continue
        command = tokens[0].lower()
        from deep_sky_siril_processing import validate_parameters
        validate_parameters(tokens, protocol, local_saturation=local_saturation)
        commands.append(command)
        if command not in allowed:
            raise ContractError("unsafe_siril_script", f"Command {command} is forbidden for {protocol}")
        if command == "requires" and line != "requires 1.4.4 1.5.0":
            raise ContractError("unsafe_siril_script", "Siril version gate changed")
        if command == "set32bits" and len(tokens) != 1:
            raise ContractError("unsafe_siril_script", "set32bits accepts no arguments")
        if command == "load":
            if len(tokens) != 2:
                raise ContractError("unsafe_siril_script", "load requires exactly one path")
            _require_known_load(
                tokens[1],
                session=session,
                source=source,
                generated=generated,
                verified_inputs=verified_inputs,
            )
        elif command in {"save", "savejpg", "savetif32"}:
            if len(tokens) < 2:
                raise ContractError("unsafe_siril_script", f"{command} lacks an output path")
            target = _normalized_write(command, tokens[1])
            session_path(session, target, allowed_roots=("artifacts", "previews", "reports"))
            generated.add(target)
        elif command == "split":
            if len(tokens) != 4:
                raise ContractError("unsafe_siril_script", "split requires exactly three outputs")
            for value in tokens[1:4]:
                target = _normalized_write(command, value)
                session_path(session, target, allowed_roots=("artifacts", "reports"))
                generated.add(target)
        elif command == "rgbcomp":
            for value in tokens[1:4]:
                _require_known_load(
                    value,
                    session=session,
                    source=source,
                    generated=generated,
                    verified_inputs=verified_inputs,
                )
            value = _option_value(tokens, "out")
            if value is None:
                raise ContractError("unsafe_siril_script", "rgbcomp requires -out")
            target = _normalized_write("save", value)
            session_path(session, target, allowed_roots=("artifacts",))
            generated.add(target)
        elif command in {"findstar", "makepsf"}:
            option = "out" if command == "findstar" else "savepsf"
            value = _option_value(tokens, option)
            if value is None:
                raise ContractError("unsafe_siril_script", f"{command} requires -{option}")
            target = _absolute(value).resolve(strict=False)
            session_path(session, target, allowed_roots=("reports",))
            generated.add(target)
        elif command == "pyscript":
            if protocol in {'restoration.deconvolve', 'restoration.denoise'}:
                protocol_runtime = tooling._require_protocol_runtime_dependency(protocol, probe,
                    native_denoise=protocol == 'restoration.denoise')
            generated.add(
                _validate_pyscript(
                    protocol,
                    tokens,
                    session=session,
                    source=source,
                    policy=policy,
                    probe=probe,
                    replaying=replaying,
                    run_id=script.stem,
                    execution_policy=session_payload.get("execution_policy"),
                )
            )
            if protocol == 'restoration.deconvolve':
                document = load_json(Path(tokens[3]))
                for value in (document['stars_tsv'], document['psf']['path']):
                    generated.add(session_path(session, value, allowed_roots=('reports',)))
            adapters.append(
                fingerprint(
                    Path(tokens[1]).expanduser().resolve(),
                    role={'background.subtract': 'background_sample_adapter',
                          'restoration.denoise': 'deepsnr_adapter',
                          'restoration.deconvolve': 'psf_sample_adapter'}.get(protocol, 'starnet_adapter'),
                )
            )
        elif command == "set":
            key = _validate_runtime_setting(protocol, tokens, probe=probe)
            if key in runtime_settings:
                raise ContractError("unsafe_siril_script", f"Setting {key} is duplicated")
            runtime_settings.add(key)
        elif command == "pm":
            for value in _PIXELMATH_PATH.findall(line):
                absolute_parent = (local_parameters is not None and 'D' in local_parameters
                                   and value == source.with_suffix('').as_posix())
                if (Path(value).is_absolute() and not absolute_parent) or ".." in Path(value).parts:
                    raise ContractError("unsafe_siril_script", "PixelMath path escaped the session")
                base = source if absolute_parent else session_path(session, session / value)
                candidates = {base.resolve(strict=False)}
                if not base.suffix:
                    candidates.update(
                        base.with_suffix(suffix).resolve(strict=False)
                        for suffix in (".fit", ".fits", ".fts", ".tif", ".tiff", ".xisf")
                    )
                allowed_inputs = generated | verified_inputs | {source.resolve()}
                if candidates.isdisjoint(allowed_inputs):
                    raise ContractError(
                        "unsafe_siril_script",
                        f"PixelMath reads an unverified session artifact: {value}",
                    )

    if commands.count("requires") != 1 or commands.count("set32bits") != 1:
        raise ContractError("unsafe_siril_script", "The contract prefix commands must appear exactly once")
    if protocol == "stars.separate":
        if runtime_settings or commands.count("pyscript") != 1:
            raise ContractError("unsafe_siril_script", "StarNet protocol requires one pinned Python invocation without global settings")

    if protocol == "astrometry.solve":
        local_astro = any(shlex.split(line)[0] == "platesolve" and "-catalog=localgaia" in shlex.split(line) for line in lines)
        if local_astro != ("core.catalogue_gaia_astro" in runtime_settings):
            raise ContractError("unsafe_siril_script", "Local astrometry requires exactly one frozen catalogue setting; remote astrometry forbids it")
    session_offline = bool(session_payload.get("context", {}).get("offline"))
    if session_state.execution_policy_is_current(session_payload):
        network = classify_siril_network(
            text,
            protocol=protocol,
            session_offline=session_offline,
        )
        local_gaia_setting = "core.catalogue_gaia_photo" in runtime_settings
        uses_local_gaia = "localgaia" in network["catalogues"]
        if protocol == "color.calibrate" and local_gaia_setting != uses_local_gaia:
            raise ContractError(
                "unsafe_siril_script",
                "Local Gaia calibration requires exactly one frozen core.catalogue_gaia_photo setting",
            )
    else:
        network = {
            "policy": "legacy_session",
            "session_offline": session_offline,
            "effective_offline": session_offline,
            "reason": "legacy_session",
            "catalogues": [],
        }

    undeclared = generated - expected
    if undeclared:
        names = ", ".join(sorted(relative_session_path(session, path) for path in undeclared))
        arguments = " ".join(shlex.join(["--expect", str(path)]) for path in sorted(undeclared))
        raise ContractError(
            "unsafe_siril_script",
            f"Script writes undeclared outputs: {names}. Add to the run command: {arguments}",
        )
    if not generated:
        raise ContractError("unsafe_siril_script", "Siril script declares no image or report output")
    script_record = {
        "path": relative_session_path(session, script),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
    }
    try:
        command_knowledge = manual_query.map_script_commands(
            commands,
            protocol=protocol,
            skill_root=SKILL_ROOT,
        )
    except BundleError as exc:
        raise ContractError(
            "command_knowledge_invalid",
            f"SSF commands cannot be mapped to the pinned manual: {exc}",
        ) from exc
    if (
        command_knowledge.get("manual") != session_payload.get("knowledge", {}).get("manual")
        or command_knowledge.get("command_policy") != policy_fingerprint
    ):
        raise ContractError(
            "command_knowledge_invalid",
            "SSF command knowledge differs from the frozen session Bundle",
        )
    provenance_validation = validate_ssf_provenance(
        provenance,
        session=session,
        session_payload=session_payload,
        protocol=protocol,
        script=script,
        script_fingerprint=script_record,
        commands=commands,
        policy_fingerprint=policy_fingerprint,
        replaying=replaying,
        script_text=text,
    )
    result = {
        "script_sha256": script_record["sha256"],
        "script_fingerprint": script_record,
        **provenance_validation,
        "command_knowledge": command_knowledge,
        "commands": commands,
        "network": network,
        "declared_writes": sorted(relative_session_path(session, path) for path in generated),
        "adapters": adapters,
    }
    if protocol_runtime is not None:
        result["sirilpy_bridge"] = protocol_runtime
    return result

__all__ = [
    "public_protocols",
    "knowledge_bindings_unchanged",
    "script_binding_unchanged",
    "validate_ssf_provenance",
    "validate_script_file",
]
