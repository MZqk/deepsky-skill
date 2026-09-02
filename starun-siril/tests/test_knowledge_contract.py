from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import re
import shlex
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


import deep_sky_siril_contract as contract  # noqa: E402
import deep_sky_siril_validation as validation  # noqa: E402
import query_siril_manual as manual_query  # noqa: E402


_SSF_BLOCK = re.compile(r"^```ssf[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_PROTOCOL_HEADING = re.compile(r"^#\s+([a-z]+(?:[.-][a-z]+)*)\s*$", re.MULTILINE)
_STAGE_PROTOCOL_LINK = re.compile(r"\]\(protocols/([a-z0-9-]+)\.md\)")


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _protocol_identity(path: Path, text: str) -> str:
    match = _PROTOCOL_HEADING.search(text)
    assert match is not None, f"{path.name} lacks a protocol-id heading"
    return match.group(1)


def _ssf_commands(path: Path, text: str) -> set[str]:
    blocks = _SSF_BLOCK.findall(text)
    assert blocks, f"{path.name} lacks a parameterized SSF skeleton"
    commands: set[str] = set()
    for block in blocks:
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = shlex.split(line, posix=True)
            assert tokens, f"{path.name} contains an empty SSF command"
            commands.add(tokens[0].lower())
    return commands


def test_protocol_documents_policy_and_manual_command_closure() -> None:
    policy = _load_json(ROOT / "references" / "command-policy.json")
    command_index = _load_json(ROOT / "references" / "siril-manual" / "commands.json")
    protocol_commands = policy["protocol_commands"]
    assert isinstance(protocol_commands, dict)

    documents: dict[str, tuple[Path, str]] = {}
    for path in sorted((ROOT / "references" / "protocols").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        protocol = _protocol_identity(path, text)
        assert protocol not in documents, f"duplicate protocol document for {protocol}"
        documents[protocol] = (path, text)

    assert len(documents) == 12
    assert set(documents) == set(protocol_commands)

    manual_commands = {
        entry["name"].lower(): entry
        for entry in command_index["commands"]
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    for protocol, (path, text) in documents.items():
        allowed = protocol_commands[protocol]
        assert isinstance(allowed, list)
        skeleton_commands = _ssf_commands(path, text)
        for command_name in skeleton_commands:
            assert command_name in manual_commands, (
                f"{path.name}: {command_name!r} is absent from the frozen manual index"
            )
            assert manual_commands[command_name].get("scriptable") is True, (
                f"{path.name}: {command_name!r} is not scriptable in the frozen manual"
            )
            assert command_name in allowed, (
                f"{path.name}: {command_name!r} is not authorized for {protocol}"
            )


def test_default_stage_sequence_covers_protocol_catalog_in_order() -> None:
    stage_text = (ROOT / "references" / "stage-sequence.md").read_text(
        encoding="utf-8"
    )
    linked_paths = [
        ROOT / "references" / "protocols" / f"{slug}.md"
        for slug in _STAGE_PROTOCOL_LINK.findall(stage_text)
    ]
    protocols = [
        _protocol_identity(path, path.read_text(encoding="utf-8"))
        for path in linked_paths
    ]
    assert protocols == [
        "input.inspect",
        "geometry.crop-near-black",
        "background.subtract",
        "color.calibrate",
        "restoration.deconvolve",
        "restoration.denoise",
        "stars.separate",
        "stretch",
        "color.map",
        "stars.recompose",
        "color.finish",
        "delivery.render",
    ]
    protocol_catalog = {
        _protocol_identity(path, path.read_text(encoding="utf-8"))
        for path in (ROOT / "references" / "protocols").glob("*.md")
    }
    assert set(protocols) == protocol_catalog
    policy = _load_json(ROOT / "references" / "command-policy.json")
    assert set(protocols) == set(policy["protocol_commands"])


@pytest.fixture
def provenance_case(tmp_path: Path) -> dict[str, object]:
    session = tmp_path / "session"
    script = session / "scripts" / "120-delivery.ssf"
    script.parent.mkdir(parents=True)
    (session / "reports" / "manual-evidence").mkdir(parents=True)
    script.write_text(
        "\n".join(
            [
                "requires 1.4.4 1.5.0",
                "set32bits",
                'load "/abs/source.fit"',
                'savejpg "/abs/candidate" 95',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    provenance = script.with_suffix(".provenance.json")
    policy_path = ROOT / "references" / "command-policy.json"
    primary_path = ROOT / "references" / "protocols" / "delivery-render.md"
    script_fingerprint = {
        **contract.fingerprint(script, include_path=False),
        "path": "scripts/120-delivery.ssf",
    }
    policy_fingerprint = {
        **contract.fingerprint(policy_path, include_path=False),
        "path": "references/command-policy.json",
    }
    bundle_evidence = session / "reports" / "manual-evidence" / "bundle-verification.json"
    bundle_evidence.write_text("{}\n", encoding="utf-8")
    payload = {
        "schema": "starun-siril.ssf-provenance.v1",
        "contract_version": "1",
        "run_id": "120-delivery",
        "protocol": "delivery.render",
        "script": {
            "path": "scripts/120-delivery.ssf",
            "sha256": script_fingerprint["sha256"],
        },
        "references": [
            {
                "path": "references/protocols/delivery-render.md",
                "sha256": contract.sha256_file(primary_path),
                "role": "primary",
            }
        ],
        "command_policy": {
            "path": "references/command-policy.json",
            "sha256": policy_fingerprint["sha256"],
        },
        "manual_lookup": {
            "status": "not_needed",
            "reason": "The complete delivery skeleton is instantiated without semantic ambiguity.",
            "evidence": [],
        },
        "rationale": {
            "applicability": "The selected source is an accepted nonlinear delivery parent.",
            "parameter_choices": ["JPEG quality 95 is the protocol skeleton value."],
        },
    }
    session_payload = {
        "knowledge": {
            "schema": "starun-siril.knowledge.v1",
            "bundle_evidence": {
                "path": "reports/manual-evidence/bundle-verification.json",
                "sha256": contract.sha256_file(bundle_evidence),
                "size": bundle_evidence.stat().st_size,
            },
            "manual": {"version": "1.4.4"},
            "command_policy": policy_fingerprint,
        }
    }
    return {
        "session": session,
        "script": script,
        "provenance": provenance,
        "script_fingerprint": script_fingerprint,
        "policy_fingerprint": policy_fingerprint,
        "session_payload": session_payload,
        "payload": payload,
    }


def _validate_provenance(case: dict[str, object], payload: dict[str, object]) -> dict[str, object]:
    provenance = case["provenance"]
    assert isinstance(provenance, Path)
    provenance.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return validation.validate_ssf_provenance(
        provenance,
        session=case["session"],
        session_payload=case["session_payload"],
        protocol="delivery.render",
        script=case["script"],
        script_fingerprint=case["script_fingerprint"],
        commands=("requires", "set32bits", "load", "savejpg"),
        policy_fingerprint=case["policy_fingerprint"],
    )


def _assert_provenance_invalid(case: dict[str, object], payload: dict[str, object]) -> None:
    with pytest.raises(contract.ContractError) as exc_info:
        _validate_provenance(case, payload)
    assert exc_info.value.code == "ssf_provenance_invalid"


def _manual_evidence(
    case: dict[str, object],
    payload: dict[str, object],
    *arguments: str,
) -> dict[str, object]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    assert manual_query.run(arguments, stdout=stdout, stderr=stderr) == 0, stderr.getvalue()
    document = json.loads(stdout.getvalue())
    evidence = case["session"] / "reports" / "manual-evidence" / "lookup.json"
    evidence.write_text(stdout.getvalue(), encoding="utf-8")
    case["session_payload"]["knowledge"]["manual"] = document["manual"]
    payload["manual_lookup"] = {
        "status": "performed",
        "reason": "The Agent consulted this exact frozen manual result before authoring the fixture.",
        "evidence": [
            {
                "path": "reports/manual-evidence/lookup.json",
                "sha256": contract.sha256_file(evidence),
            }
        ],
    }
    return document


def test_valid_not_needed_provenance_matches_schema_and_runtime(
    provenance_case: dict[str, object],
) -> None:
    result = _validate_provenance(provenance_case, copy.deepcopy(provenance_case["payload"]))
    assert result["script_provenance"]["path"] == "scripts/120-delivery.provenance.json"
    assert result["knowledge_validation"]["references"][0]["role"] == "primary"
    assert result["knowledge_validation"]["manual_evidence"] == []


def test_stage_sequence_is_valid_as_a_supporting_reference(
    provenance_case: dict[str, object],
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    stage_path = ROOT / "references" / "stage-sequence.md"
    payload["references"].append(
        {
            "path": "references/stage-sequence.md",
            "sha256": contract.sha256_file(stage_path),
            "role": "supporting",
        }
    )

    result = _validate_provenance(provenance_case, payload)

    assert [
        item["role"] for item in result["knowledge_validation"]["references"]
    ] == ["primary", "supporting"]


@pytest.mark.parametrize(
    "changed_path",
    [
        "references/command-policy.json",
        "references/protocols/delivery-render.md",
    ],
)
def test_post_validation_detects_policy_or_reference_byte_drift(
    provenance_case: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    changed_path: str,
) -> None:
    result = _validate_provenance(
        provenance_case,
        copy.deepcopy(provenance_case["payload"]),
    )
    captured = {
        "script_fingerprint": provenance_case["script_fingerprint"],
        **result,
    }
    assert validation.script_binding_unchanged(provenance_case["session"], captured)
    assert validation.knowledge_bindings_unchanged(provenance_case["session"], captured)
    original = validation.read_skill_file

    def drifted_read(root: Path, relative: str) -> object:
        record = original(root, relative)
        if relative == changed_path:
            return SimpleNamespace(sha256="0" * 64, size_bytes=record.size_bytes)
        return record

    monkeypatch.setattr(validation, "read_skill_file", drifted_read)
    assert not validation.knowledge_bindings_unchanged(
        provenance_case["session"],
        captured,
    )


def test_valid_performed_command_evidence_is_hash_and_bundle_bound(
    provenance_case: dict[str, object],
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    document = _manual_evidence(provenance_case, payload, "--command", "savejpg")

    result = _validate_provenance(provenance_case, payload)

    assert document["mode"] == "command"
    assert result["knowledge_validation"]["manual_evidence"][0]["mode"] == "command"


@pytest.mark.parametrize("drift", ["bundle", "source"])
def test_performed_manual_evidence_rejects_frozen_source_drift(
    provenance_case: dict[str, object],
    drift: str,
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    document = _manual_evidence(provenance_case, payload, "--command", "savejpg")
    if drift == "bundle":
        document["manual"]["bundle_fingerprint"] = "0" * 64
    else:
        document["result"]["documentation"]["source_sha256"] = "0" * 64
    evidence = provenance_case["session"] / "reports" / "manual-evidence" / "lookup.json"
    evidence.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    payload["manual_lookup"]["evidence"][0]["sha256"] = contract.sha256_file(evidence)
    _assert_provenance_invalid(provenance_case, payload)


def test_search_only_manual_evidence_is_not_final_provenance_evidence(
    provenance_case: dict[str, object],
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    _manual_evidence(provenance_case, payload, "autostretch")
    _assert_provenance_invalid(provenance_case, payload)


def test_command_evidence_must_be_authorized_for_the_declared_protocol(
    provenance_case: dict[str, object],
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    _manual_evidence(provenance_case, payload, "--command", "autostretch")
    provenance = provenance_case["provenance"]
    provenance.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(contract.ContractError) as raised:
        validation.validate_ssf_provenance(
            provenance,
            session=provenance_case["session"],
            session_payload=provenance_case["session_payload"],
            protocol="delivery.render",
            script=provenance_case["script"],
            script_fingerprint=provenance_case["script_fingerprint"],
            commands=("requires", "set32bits", "load", "savejpg", "autostretch"),
            policy_fingerprint=provenance_case["policy_fingerprint"],
        )
    assert raised.value.code == "ssf_provenance_invalid"


def test_missing_provenance_sidecar_is_rejected(provenance_case: dict[str, object]) -> None:
    with pytest.raises(contract.ContractError) as exc_info:
        validation.validate_ssf_provenance(
            provenance_case["provenance"],
            session=provenance_case["session"],
            session_payload=provenance_case["session_payload"],
            protocol="delivery.render",
            script=provenance_case["script"],
            script_fingerprint=provenance_case["script_fingerprint"],
            commands=("requires", "set32bits", "load", "savejpg"),
            policy_fingerprint=provenance_case["policy_fingerprint"],
        )
    assert exc_info.value.code == "ssf_provenance_missing"


def test_provenance_sidecar_symlink_is_rejected(provenance_case: dict[str, object]) -> None:
    real = provenance_case["session"] / "scripts" / "real-provenance.json"
    real.write_text(json.dumps(provenance_case["payload"]) + "\n", encoding="utf-8")
    provenance_case["provenance"].symlink_to(real)

    with pytest.raises(contract.ContractError) as raised:
        validation.validate_ssf_provenance(
            provenance_case["provenance"],
            session=provenance_case["session"],
            session_payload=provenance_case["session_payload"],
            protocol="delivery.render",
            script=provenance_case["script"],
            script_fingerprint=provenance_case["script_fingerprint"],
            commands=("requires", "set32bits", "load", "savejpg"),
            policy_fingerprint=provenance_case["policy_fingerprint"],
        )
    assert raised.value.code == "ssf_provenance_missing"


@pytest.mark.parametrize("mutation", ["wrong-hash", "duplicate", "path-escape", "symlink"])
def test_manual_evidence_binding_rejects_unsafe_or_changed_inputs(
    provenance_case: dict[str, object],
    mutation: str,
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    _manual_evidence(provenance_case, payload, "--command", "savejpg")
    record = payload["manual_lookup"]["evidence"][0]
    evidence = provenance_case["session"] / record["path"]
    if mutation == "wrong-hash":
        record["sha256"] = "0" * 64
    elif mutation == "duplicate":
        payload["manual_lookup"]["evidence"].append(copy.deepcopy(record))
    elif mutation == "path-escape":
        record["path"] = "reports/manual-evidence/../lookup.json"
    else:
        data = evidence.read_bytes()
        evidence.unlink()
        target = provenance_case["session"] / "reports" / "lookup-target.json"
        target.write_bytes(data)
        evidence.symlink_to(target)
    _assert_provenance_invalid(provenance_case, payload)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda value: value.pop("rationale"), id="missing-root-field"),
        pytest.param(lambda value: value.update({"recipe": "forbidden"}), id="extra-root-field"),
        pytest.param(lambda value: value["script"].pop("sha256"), id="missing-script-field"),
        pytest.param(lambda value: value["script"].update({"size": 123}), id="extra-script-field"),
        pytest.param(lambda value: value["references"][0].pop("role"), id="missing-reference-field"),
        pytest.param(lambda value: value["references"][0].update({"title": "forbidden"}), id="extra-reference-field"),
        pytest.param(lambda value: value["command_policy"].pop("path"), id="missing-policy-field"),
        pytest.param(lambda value: value["command_policy"].update({"size": 123}), id="extra-policy-field"),
        pytest.param(lambda value: value["manual_lookup"].pop("reason"), id="missing-manual-field"),
        pytest.param(lambda value: value["manual_lookup"].update({"query": "forbidden"}), id="extra-manual-field"),
        pytest.param(lambda value: value["rationale"].pop("applicability"), id="missing-rationale-field"),
        pytest.param(lambda value: value["rationale"].update({"parameter_choices": []}), id="empty-parameter-reasons"),
        pytest.param(lambda value: value["rationale"].update({"score": 1}), id="extra-rationale-field"),
    ],
)
def test_provenance_rejects_missing_or_additional_fields(
    provenance_case: dict[str, object],
    mutation: object,
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    mutation(payload)
    _assert_provenance_invalid(provenance_case, payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("run_id", "121-other", id="wrong-run-id"),
        pytest.param("protocol", "stretch", id="wrong-protocol"),
    ],
)
def test_provenance_rejects_wrong_identity(
    provenance_case: dict[str, object],
    field: str,
    value: str,
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    payload[field] = value
    _assert_provenance_invalid(provenance_case, payload)


def test_provenance_rejects_wrong_script_hash(provenance_case: dict[str, object]) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    payload["script"]["sha256"] = "0" * 64
    _assert_provenance_invalid(provenance_case, payload)


def test_provenance_requires_exactly_one_protocol_primary_reference(
    provenance_case: dict[str, object],
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    payload["references"][0]["role"] = "supporting"
    _assert_provenance_invalid(provenance_case, payload)


def test_provenance_rejects_duplicate_reference_paths(provenance_case: dict[str, object]) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    duplicate = copy.deepcopy(payload["references"][0])
    duplicate["role"] = "supporting"
    payload["references"].append(duplicate)
    _assert_provenance_invalid(provenance_case, payload)


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("references/protocols/../command-policy.json", id="path-escape"),
        pytest.param("references/siril-manual/commands.json", id="manual-as-reference"),
    ],
)
def test_provenance_rejects_unsafe_or_manual_reference_paths(
    provenance_case: dict[str, object],
    path: str,
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    payload["references"][0]["path"] = path
    payload["references"][0]["sha256"] = "0" * 64
    _assert_provenance_invalid(provenance_case, payload)


@pytest.mark.parametrize("binding", ["command_policy", "references"])
def test_provenance_rejects_changed_policy_or_reference_hash(
    provenance_case: dict[str, object],
    binding: str,
) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    if binding == "command_policy":
        payload["command_policy"]["sha256"] = "0" * 64
    else:
        payload["references"][0]["sha256"] = "0" * 64
    _assert_provenance_invalid(provenance_case, payload)
