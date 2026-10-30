from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import re
import shlex
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


import deep_sky_siril_contract as contract  # noqa: E402
import deep_sky_siril_validation as validation  # noqa: E402
import query_siril_manual as manual_query  # noqa: E402


def test_large_galaxy_pcc_guidance_qualifies_cause_and_acceptance():
    text = ' '.join((ROOT / 'references/stages/stage-04-astrometry-color.md').read_text().split())
    assert '1550 颗恒星' in text and '1860 颗在图像外' in text
    assert '不能凭天体名称或标称视场确认原因' in text
    assert '不将微弱外盘当天空强制中性化' in text
    assert '适用门通过后才可保留告警接受' in text
    assert '核心看起来自然不能证明测光准确' in text


def test_diffuse_emission_manual_guide_preserves_sky_and_model_limits():
    text = ' '.join((ROOT / 'references/stages/stage-03-background.md').read_text().split())
    assert '长宽各扩大至 1.5 倍' in text
    assert '边界向外取整并裁到画幅' in text
    assert '仅四角不能约束中部' in text
    assert '跳过，保留父源' in text
    assert '不是 15–20 像素框尺寸' in text
    assert '一般二阶曲面不必中心对称' in text
    assert '减曲面不修复乘性暗角' in text
    assert '其他天区同样按实际证据选择' in text
    case = (ROOT.parent / 'deep-sky-processor/references/case_ngc6888_rgb.md').read_text()
    assert '全局梯度不显著，应跳过 DBE' in case
    assert 'Its faint Cygnus emission is signal' in case


_SSF_BLOCK = re.compile(r"^```ssf[ \t]*\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_STAGE_PROTOCOL_LINK = re.compile(r"\]\((?:stages|shared-protocols)/[^)#]+#([a-z0-9-]+)\)")


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_v18_command_policy(record):
    """Preserve exact old authority and permit the scoped native additions."""
    baseline = _load_json(ROOT/'tests/fixtures/vivid-policy-v18.json')
    assert {k: record[k] for k in ('sha256', 'size')} == baseline['documents']['references/command-policy.json']
    policy = _load_json(ROOT/'references/command-policy.json')
    policy['protocol_commands']['stars.recompose'].remove('rgbcomp')
    policy['protocol_commands']['stars.recompose'].remove('update_key')
    policy['protocol_commands']['stars.recompose'].remove('modasinh')
    for command in ('epf','split','pm','rgbcomp','icc_assign'):
        policy['protocol_commands']['restoration.denoise-nonlinear'].remove(command)
    policy['protocol_commands']['color.finish'].remove('ght')
    policy['protocol_commands']['color.finish'].remove('invght')
    policy['protocol_commands']['color.finish'].remove('fill')
    for command in ('fmul', 'denoise', 'crop'):
        policy['protocol_commands']['stretch'].remove(command)
    for command in ('wavelet', 'wrecons'):
        policy['protocol_commands']['structure.local-contrast'].remove(command)
    for command in ('split', 'pm', 'gauss', 'rgbcomp'):
        policy['protocol_commands']['tone.adjust'].remove(command)
    policy['protocol_commands']['restoration.denoise'].remove('pyscript')
    assert policy['pyscript_protocols'].pop('restoration.denoise') == 'siril_deepsnr.py'
    assert policy == json.loads(baseline['command_policy'])


def protocol_text(protocol: str) -> str:
    """Read each canonical stage section without including adjacent protocols."""
    slug = protocol.replace(".", "-")
    sections = []
    for relative in contract.PROTOCOL_STAGE_REFERENCES[protocol]:
        text = (ROOT / relative).read_text(encoding="utf-8")
        section = text.split(f'<a id="{slug}"></a>\n', 1)[1]
        sections.append(re.split(r'\n<a id="[a-z0-9-]+"></a>\n## ', section, maxsplit=1)[0])
    if protocol == 'stretch':
        sections.extend((ROOT / f'references/stage7-{kind}.md').read_text()
                        for kind in ('hdr', 'hoo', 'diagnostics', 'legacy'))
    return "\n".join(sections)


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

    assert len(contract.PROTOCOL_STAGE_REFERENCES) == 16
    assert set(contract.PROTOCOL_STAGE_REFERENCES) == set(protocol_commands)
    documents = {
        protocol: (ROOT / paths[0], protocol_text(protocol))
        for protocol, paths in contract.PROTOCOL_STAGE_REFERENCES.items()
    }

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
    identities = {protocol.replace(".", "-"): protocol for protocol in contract.PROTOCOL_STAGE_REFERENCES}
    protocols = [identities[slug] for _guide, slugs in _stage_routes(stage_text).values() for slug in slugs]
    assert protocols == [
        "input.inspect",
        "geometry.crop-near-black",
        "background.subtract",
        "color.calibrate",
        "astrometry.solve",
        "restoration.denoise",
        "restoration.deconvolve",
        "stars.separate",
        "stretch",
        "structure.local-contrast",
        "color.map",
        "tone.adjust",
        "restoration.denoise-nonlinear",
        "color.finish",
        "stars.recompose",
        "restoration.denoise-nonlinear",
        "tone.adjust",
        "color.finish",
        "delivery.render",
    ]
    protocol_catalog = set(contract.PROTOCOL_STAGE_REFERENCES)
    assert set(protocols) == protocol_catalog
    policy = _load_json(ROOT / "references" / "command-policy.json")
    assert set(protocols) == set(policy["protocol_commands"])


def _stage_routes(text: str) -> dict[int, tuple[str, list[str]]]:
    routes: dict[int, tuple[str, list[tuple[int, str]]]] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        columns = line.split("|")
        label = columns[1].strip()
        if not re.match(r"\d", label):
            continue
        stages = [re.match(r"(\d+)([a-z]?)", part.strip()) for part in label.split("/")]
        assert all(stages), f"Invalid stage label: {label}"
        guides = re.findall(r"\]\((stages/[^)#]+)(?:#[^)]*)?\)", columns[3])
        protocols = _STAGE_PROTOCOL_LINK.findall(columns[4])
        assert len(guides) == len(stages) and protocols, f"Incomplete stage route: {line}"
        for match, guide in zip(stages, guides):
            stage = int(match.group(1))
            start = ord(match.group(2)) - ord("a") if match.group(2) else 0
            current_guide, current_protocols = routes.setdefault(stage, (guide, []))
            assert current_guide == guide, f"Stage {stage} has conflicting guides"
            current_protocols.extend((start + offset, slug) for offset, slug in enumerate(protocols))
    assert set(routes) == set(range(1, 11)), "Stage routes must cover stages 1 through 10"
    for stage, (_, protocols) in routes.items():
        assert len({order for order, _ in protocols}) == len(protocols), f"Duplicate substage in stage {stage}"
    return {stage: (guide, [slug for _, slug in sorted(protocols)])
            for stage, (guide, protocols) in routes.items()}


def test_stage_guides_and_protocol_index_cover_protocol_catalog() -> None:
    references = ROOT / "references"
    expected = _stage_routes((references / "stage-sequence.md").read_text(encoding="utf-8"))
    indexed = re.findall(r"\]\((?:stages|shared-protocols)/[^)#]+#([a-z0-9-]+)\)", (references / "protocol-index.md").read_text(encoding="utf-8").split("| 协议", 1)[1])
    catalog = {protocol.replace(".", "-") for protocol in contract.PROTOCOL_STAGE_REFERENCES}
    assert len(indexed) == len(set(indexed)), "Protocol index contains duplicate operations"
    assert set(indexed) == catalog, "Protocol index does not cover the complete protocol catalog"
    release_files = set((ROOT / "release-files.txt").read_text(encoding="utf-8").splitlines())
    guide_paths = {references / guide for guide, _ in expected.values()}
    assert guide_paths == set((references / "stages").glob("*.md")), "Unmapped or missing stage guide"
    for guide, protocols in expected.values():
        path = references / guide
        text = path.read_text(encoding="utf-8")
        assert f"references/{guide}" in release_files, f"{guide} is missing from the release inventory"
    assert not (references / "protocols").exists()
    for protocol, body_paths in contract.PROTOCOL_STAGE_REFERENCES.items():
        slug = protocol.replace(".", "-")
        for body_path in body_paths:
            assert body_path in release_files
            assert f'<a id="{slug}"></a>\n## {protocol}\n' in (ROOT / body_path).read_text(encoding="utf-8")
    declared = {
        protocol for path in guide_paths | set((references / "shared-protocols").glob("*.md"))
        for protocol in re.findall(r"^## ([a-z]+(?:[.-][a-z]+)*)$", path.read_text(), re.MULTILINE)
    }
    assert declared == set(contract.PROTOCOL_STAGE_REFERENCES)


def test_stage_body_links_resolve_to_canonical_sections() -> None:
    references = ROOT / "references"
    documents = [ROOT / "SKILL.md", *references.glob("*.md"),
                 *(references / "stages").glob("*.md"), *(references / "shared-protocols").glob("*.md")]
    for document in documents:
        text = document.read_text(encoding="utf-8")
        assert "references/protocols/" not in text, f"{document.name}: retired protocol reference"
        for link in re.findall(r"\]\(([^)\s]+)\)", text):
            relative, separator, fragment = link.partition("#")
            if not separator or ":" in relative:
                continue
            target = (document.parent / relative).resolve()
            if target.parent in {references / "stages", references / "shared-protocols"}:
                assert target.is_file(), f"{document.name}: missing stage {link}"
                assert f'<a id="{fragment}"></a>' in target.read_text(encoding="utf-8"), (
                    f"{document.name}: missing stage-body section {link}"
                )


def test_deconvolution_skeleton_uses_documented_options_and_generated_psf() -> None:
    blocks = _SSF_BLOCK.findall(protocol_text("restoration.deconvolve"))
    assert blocks, "Deconvolution protocol lacks its executable example"
    manual = _load_json(ROOT / "references/siril-manual/commands.json")
    rl = next(command for command in manual["commands"] if command["name"] == "rl")
    allowed_options = set(re.findall(r"-([a-z]+)", rl["usage"]))
    for block in blocks:
        commands = [shlex.split(line) for line in block.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        names = [tokens[0] for tokens in commands]
        assert names.count("rl") == names.count("pyscript") == names.count("findstar") == 1
        assert "makepsf" not in names  # The fixed bridge invokes native manual PSF generation.
        bridge_index = names.index("pyscript")
        assert names[bridge_index - 3:bridge_index + 2] == ["load", "fmul", "setfindstar", "pyscript", "rl"]
        assert names.index("rl") < names.index("findstar") < names.index("mtf")
        before_settings = commands[bridge_index - 1]
        after_index = names.index("findstar")
        assert commands[after_index - 1] == before_settings
        assert names[after_index - 2] == "load"
        adapter = commands[bridge_index]
        assert adapter[1] == "/abs/starun-siril/scripts/siril_psf_samples.py"
        assert adapter[2::2] == ["--contract", "--contract-sha256", "--expected-source", "--receipt"]
        tokens = commands[names.index("rl")]
        assert all(token.startswith("-") for token in tokens[1:]), "Unexpected positional rl argument"
        options = [token.lstrip("-").split("=", 1)[0] for token in tokens[1:]]
        assert len(options) == len(set(options)), "Duplicate rl option"
        assert set(options) <= allowed_options, f"Undocumented rl options: {set(options) - allowed_options}"
        assert {"loadpsf", "iters", "tv", "alpha"} <= set(options), "Conservative RL skeleton is incomplete"
        document = json.loads(re.findall(r"```json\n(.*?)```", protocol_text("restoration.deconvolve"), re.DOTALL)[0])
        psf = [document['psf']['path']]
        loaded = [token.split("=", 1)[1] for token in tokens if token.startswith("-loadpsf=")]
        assert len(psf) == 1 and psf == loaded, "RL must use the PSF generated from this parent"


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
        "references": contract.protocol_reference_bindings("delivery.render"),
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
        "references/stages/stage-10-delivery-finishing.md",
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


@pytest.mark.parametrize("protocol", ["tone.adjust", "color.finish"])
@pytest.mark.parametrize("issue", [None, "stage10", "stage8", "role", "hash", "second-primary"])
def test_shared_protocol_requires_sole_canonical_primary(provenance_case, protocol, issue):
    case = copy.deepcopy(provenance_case)
    case["payload"]["protocol"] = protocol
    case["payload"]["references"] = contract.protocol_reference_bindings(protocol)
    references = case["payload"]["references"]
    if issue in {"stage10", "stage8"}:
        path = "references/stages/" + ("stage-10-delivery-finishing.md" if issue == "stage10" else "stage-08-starless-enhance.md")
        references[0].update(path=path, sha256=contract.sha256_file(ROOT / path))
    elif issue == "role":
        references[0]["role"] = "supporting"
    elif issue == "hash":
        references[0]["sha256"] = "0" * 64
    elif issue == "second-primary":
        references += contract.protocol_reference_bindings("delivery.render")
    arguments = dict(session=case["session"], session_payload=case["session_payload"], protocol=protocol,
                     script=case["script"], script_fingerprint=case["script_fingerprint"],
                     commands=("requires", "set32bits", "load", "savejpg"), policy_fingerprint=case["policy_fingerprint"])
    case["provenance"].write_text(json.dumps(case["payload"]))
    if issue is not None:
        with pytest.raises(contract.ContractError) as error:
            validation.validate_ssf_provenance(case["provenance"], **arguments)
        assert error.value.code == "ssf_provenance_invalid"
    else:
        result = validation.validate_ssf_provenance(case["provenance"], **arguments)
        assert [(record["path"], record["role"]) for record in result["knowledge_validation"]["references"]] == [
            (f"references/shared-protocols/{protocol.replace('.', '-')}.md", "primary")]


def test_provenance_rejects_wrong_primary_stage(provenance_case: dict[str, object]) -> None:
    payload = copy.deepcopy(provenance_case["payload"])
    payload["references"] = contract.protocol_reference_bindings("input.inspect")
    _assert_provenance_invalid(provenance_case, payload)


def test_provenance_requires_exactly_one_stage_primary_reference(
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
        pytest.param("references/stages/../command-policy.json", id="path-escape"),
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


def test_native_dualband_neutral_guide_preserves_scientific_limits():
    text = ' '.join((ROOT / 'references/stages/stage-04-astrometry-color.md').read_text().split())
    assert 'neutral=scientific_native_flow_v2' in text
    assert '不宣称物理 Hα/OIII 比或最终非线性图的线强度比' in text
    assert '拟合背景汇总对齐不代表每个独立天空 ROI 都精确相等' in text
    assert '四角、低亮度或自动蒙版不证明纯天空' in text
    assert '公式闭合容差' in text and '`1e-6`' in text
    assert 'fmul 1' in text and 'rgbcomp' in text
    assert 'SPCC/PCC 仅按用户明确选择' in text
