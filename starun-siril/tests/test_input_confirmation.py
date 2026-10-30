from __future__ import annotations

import json
import subprocess

import pytest

from .test_deep_sky_siril import (
    _delivery_script, _input_inspect_run, _review, _selection,
    _session, _write_delivery_outputs, _write_fits, _write_jpeg, _write_ssf_provenance,
    cli, core, session_state, tooling, validation_rules,
)
from deep_sky_siril_processing import source_state


def _inspected(tmp_path, monkeypatch, *, input_state="unknown", channel_mode="broadband"):
    session, source, siril = _session(tmp_path, monkeypatch, input_state=input_state, channel_mode=channel_mode)
    receipt, direct, auto = _input_inspect_run(session, source, monkeypatch)
    review = core.review_materials_for_run(str(session), receipt["id"])
    review.update(verdict="accept", gates={key: "pass" for key in review["gates"]},
                  notes="Inspected direct and autostretched input pixels; state requires separate source evidence.")
    review_path = session / "reviews" / f"{receipt['id']}.json"
    core.atomic_write_json(review_path, review)
    return session, source, siril, receipt, review_path, direct, auto


def _confirm(session, *, state="linear", channel=None, evidence=None, run_id="010-input"):
    if evidence is None:
        evidence = [f"user confirmed input_state={state}; stacked, unstretched master"
                    + (f"; channel_mode={channel}" if channel else "")]
    return core.confirm_input(str(session), run_id, input_state=state,
                              channel_mode=channel, state_evidence=evidence)


def _bytes(session):
    return {p.relative_to(session): p.read_bytes() for p in session.rglob("*") if p.is_file()}


def _replay(session, source, receipt):
    return core.run_script(str(session), protocol="input.inspect",
        script_value=str(session / receipt["script"]["path"]), source_value=str(source),
        expected_values=[str(session / item["path"]) for item in receipt["outputs"]], timeout=30)


@pytest.mark.parametrize("state", ["linear", "nonlinear"])
def test_confirmation_preserves_frozen_evidence_and_replays_inspection(tmp_path, monkeypatch, state):
    session, source, _, receipt, review, _, _ = _inspected(tmp_path, monkeypatch)
    before = _bytes(session)
    result = _confirm(session, state=state)
    assert result["status"] == "success" and result["reused"] is False
    assert result["context"]["input_state"] == state
    assert result["context"]["channel_map"] is None
    for name, data in before.items():
        if str(name) != "manifest.json":
            assert (session / name).read_bytes() == data
    _, raw, manifest = session_state.load_session(session)
    assert raw["context"]["input_state"] == "unknown"
    assert source_state(session, raw, source)["domain"] == state
    assert manifest["input_confirmation"] == result["confirmation"]
    assert _replay(session, source, receipt)["image_domain"] == "unknown"
    committed = _bytes(session)
    assert _confirm(session, state=state)["reused"] is True
    assert _bytes(session) == committed
    with pytest.raises(core.ContractError):
        _confirm(session, state="nonlinear" if state == "linear" else "linear")
    assert _bytes(session) == committed


@pytest.mark.parametrize("policy_name", [
    "_PRE_LOCAL_HDR_EXECUTION_POLICY", "_PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY",
])
def test_predecessor_confirmed_session_loads_and_replays_without_migration(tmp_path, monkeypatch, policy_name):
    previous = getattr(session_state, policy_name)
    with monkeypatch.context() as old:
        old.setattr(session_state, "EXECUTION_POLICY", previous)
        session, source, _, receipt, *_ = _inspected(tmp_path, monkeypatch)
        confirmed = _confirm(session)
    before = _bytes(session)
    assert session_state.known_execution_policy(previous)
    _, raw, manifest = session_state.load_session(session)
    assert raw["execution_policy"] == previous
    assert not session_state.execution_policy_is_current(raw)
    assert raw["context"]["input_state"] == "unknown"
    effective = session_state.effective_session_payload(session, raw, manifest)
    assert effective["context"]["input_state"] == "linear"
    assert manifest["input_confirmation"] == confirmed["confirmation"]
    assert source_state(session, raw, source)["domain"] == "linear"
    assert _replay(session, source, receipt)["replayed"] is True
    with pytest.raises(core.ContractError) as error:
        _confirm(session)
    assert error.value.code == "legacy_session_read_only"
    assert _bytes(session) == before


@pytest.mark.parametrize("policy_name", [
    "_PRE_LOCAL_HDR_EXECUTION_POLICY", "_PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY",
])
def test_predecessor_inspect_reference_still_requires_exact_fingerprint(policy_name):
    payload = {"execution_policy": getattr(session_state, policy_name)}
    sha256, size = session_state._PRE_CONFIRMATION_REFERENCES["SKILL.md"]
    record = {"path": "SKILL.md", "sha256": sha256, "size": size}
    check = session_state.pre_confirmation_inspect_reference
    assert check(payload, "input.inspect", record)
    assert not check(payload, "input.inspect", {**record, "sha256": "0"*64})
    assert not check(payload, "input.inspect", {**record, "size": size+1})
    assert not check(payload, "stretch", record)
    assert not check({"execution_policy": {**payload["execution_policy"], "unknown": "unsupported"}}, "input.inspect", record)
    assert not check({"execution_policy": None}, "input.inspect", record)


def test_unknown_policy_still_blocks_a_confirmed_session(tmp_path, monkeypatch):
    session, *_ = _inspected(tmp_path, monkeypatch)
    _confirm(session)
    path = session/"session.json"
    payload = core.load_json(path)
    payload["execution_policy"]["unknown"] = "unsupported"
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(path, payload)
    before = _bytes(session)
    with pytest.raises(core.ContractError) as error:
        session_state.load_session(session)
    assert error.value.code == "unsupported_session_contract"
    assert _bytes(session) == before


@pytest.mark.parametrize(("state", "channel", "evidence", "code"), [
    ("linear", None, [], "state_evidence_invalid"),
    ("linear", None, [""], "state_evidence_invalid"),
    ("linear", None, ["median=0.002; looks linear"], "state_evidence_invalid"),
    ("linear", None, ["user confirmed stacked master"], "state_evidence_missing"),
    ("linear", None, ["user confirmed input_state=nonlinear"], "state_evidence_missing"),
    ("linear", None, ["user confirmed input_state=linear; input_state=nonlinear"], "input_evidence_conflict"),
    ("linear", "dualband-osc", None, "input_evidence_conflict"),
    ("linear_probable", None, None, "input_context_unresolved"),
])
def test_confirmation_rejects_invalid_evidence_without_writes(tmp_path, monkeypatch, state, channel, evidence, code):
    session, *_ = _inspected(tmp_path, monkeypatch)
    before = _bytes(session)
    with pytest.raises(core.ContractError) as error:
        _confirm(session, state=state, channel=channel, evidence=evidence)
    assert error.value.code == code
    assert _bytes(session) == before


def test_confirmation_requires_every_unknown_field(tmp_path, monkeypatch):
    session, *_ = _inspected(tmp_path, monkeypatch, channel_mode="unknown")
    before = _bytes(session)
    with pytest.raises(core.ContractError, match="channel_mode"):
        _confirm(session)
    assert _bytes(session) == before
    result = _confirm(session, channel="broadband")
    assert result["context"]["channel_mode"] == "broadband"


def test_confirmation_can_resolve_only_unknown_channel(tmp_path, monkeypatch):
    session, *_ = _inspected(tmp_path, monkeypatch, input_state="linear", channel_mode="unknown")
    result = _confirm(session, state=None, channel="broadband",
                      evidence=["acquisition record: channel_mode=broadband; RGB integration with IRCUT"])
    assert result["context"]["input_state"] == "linear"
    assert result["context"]["channel_mode"] == "broadband"


@pytest.mark.parametrize("case", ["missing_review", "reject", "uncertain", "finalized", "runtime_drift", "other_protocol", "legacy"])
def test_confirmation_requires_an_accepted_live_inspection(tmp_path, monkeypatch, case):
    session, _, siril, _, review, _, _ = _inspected(tmp_path, monkeypatch)
    if case == "missing_review":
        review.unlink()
    elif case in {"reject", "uncertain"}:
        document = core.load_json(review)
        document["verdict"] = case
        core.atomic_write_json(review, document)
    elif case in {"finalized", "other_protocol"}:
        manifest = core.load_json(session / "manifest.json")
        if case == "finalized":
            manifest["finalization"] = {"state": "committed"}
        else:
            path = session / "runs/020-other.json"
            core.atomic_write_json(path, {"id": "020-other", "status": "failed", "protocol": "stretch", "source_path": "@input"})
            session_state._append_run(session / "manifest.json", manifest, path, core.load_json(path))
        core.atomic_write_json(session / "manifest.json", manifest)
    elif case == "runtime_drift":
        siril.write_text("changed")
    elif case == "legacy":
        document = core.load_json(session / "session.json")
        document.pop("execution_policy")
        document.pop("session_sha256")
        document["session_sha256"] = core.stable_hash(document)
        core.atomic_write_json(session / "session.json", document)
    before = _bytes(session)
    with pytest.raises(core.ContractError):
        _confirm(session)
    assert _bytes(session) == before


@pytest.mark.parametrize(("target", "mutation"), [
    ("confirmation", "change"), ("confirmation", "remove"), ("confirmation", "symlink"),
    ("review", "change"), ("review", "remove"), ("review", "symlink"),
    ("receipt", "change"), ("receipt", "remove"), ("receipt", "symlink"),
    ("input", "change"), ("input", "remove"), ("input", "symlink"),
])
def test_confirmation_drift_blocks_session_and_execution(tmp_path, monkeypatch, target, mutation):
    session, source, _, receipt, review, _, _ = _inspected(tmp_path, monkeypatch)
    result = _confirm(session)
    path = {"confirmation": session / result["confirmation"]["path"], "review": review,
            "receipt": session / "runs" / f"{receipt['id']}.json",
            "input": source}[target]
    if mutation == "change":
        path.write_bytes(path.read_bytes() + b" ")
    else:
        data = path.read_bytes()
        path.unlink()
        if mutation == "symlink":
            outside = tmp_path / "replacement"
            outside.write_bytes(data)
            path.symlink_to(outside)
    with pytest.raises(core.ContractError):
        session_state.load_session(session)
    with pytest.raises(core.ContractError):
        _replay(session, source, receipt)


def test_confirmation_recovers_after_manifest_commit_interruption(tmp_path, monkeypatch):
    session, source, _, _, _, _, _ = _inspected(tmp_path, monkeypatch)
    original = core.atomic_write_json
    with monkeypatch.context() as patch:
        def interrupted(path, record):
            if path.name == "manifest.json":
                raise OSError("simulated interruption")
            original(path, record)
        patch.setattr(core, "atomic_write_json", interrupted)
        with pytest.raises(OSError, match="interruption"):
            _confirm(session)
    path = session / session_state.INPUT_CONFIRMATION_PATH
    orphan = path.read_bytes()
    _, raw, _ = session_state.load_session(session)
    assert source_state(session, raw, source)["domain"] == "unknown"
    before = _bytes(session)
    with pytest.raises(core.ContractError):
        _confirm(session, state="nonlinear")
    assert _bytes(session) == before
    assert _confirm(session)["reused"] is True
    assert path.read_bytes() == orphan


def test_confirm_input_cli_and_same_master_auto_recovery(tmp_path, monkeypatch, capsys):
    session, source, *_ = _inspected(tmp_path, monkeypatch, channel_mode="unknown")
    evidence = "user confirmed input_state=linear; channel_mode=broadband; stacked, unstretched master"
    assert cli.main(["confirm-input", "--session", str(session), "--run-id", "010-input",
                     "--input-state", "linear", "--channel-mode", "broadband", "--state-evidence", evidence]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["context"]["input_state"] == "linear"
    _, raw, _ = session_state.load_session(session)
    kwargs = {key: value for key, value in raw["context"].items() if key != "filter_profile"}
    kwargs.update(input_state="auto", channel_mode="unknown", state_evidence=[])
    recovered = session_state.init_session(str(source), str(tmp_path / "recovered"), **kwargs)
    assert recovered["context"]["input_state"] == "linear"
    assert recovered["context"]["channel_mode"] == "broadband"
    assert result["confirmation"]["sha256"] in recovered["context"]["state_evidence"][0]


@pytest.mark.parametrize("state", ["linear", "nonlinear"])
def test_confirmed_domain_controls_linear_protocols_and_static_binding(tmp_path, monkeypatch, state):
    session, source, *_ = _inspected(tmp_path, monkeypatch)
    candidate = session / "artifacts/070-stretched.fit"
    preview = session / "previews/070-stretched.jpg"
    script = session / "scripts/070-stretch.ssf"
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nmtf 0 0.1 1\nasinh -human 2 0 -clipmode=rgbblend\nsave "{candidate}" -chksum\nsavejpg "{preview.with_suffix("")}" 95\nclose\n')
    _write_ssf_provenance(session, script, "stretch")
    args = dict(protocol="stretch", script_value=str(script), source_value=str(source),
                expected_values=[str(candidate), str(preview)], timeout=30, validate_only=True)
    before = _bytes(session)
    with pytest.raises(core.ContractError, match="only Stage 1"):
        core.run_script(str(session), **args)
    assert _bytes(session) == before
    confirmed = _confirm(session, state=state)
    if state == "nonlinear":
        with pytest.raises(core.ContractError, match="linear scientific parent"):
            core.run_script(str(session), **args)
    else:
        assert confirmed['context']['input_state'] == 'linear'
        with pytest.raises(core.ContractError, match='Unbound stretch SSF'):
            core.run_script(str(session), **args)
        assert not candidate.exists()


def test_confirmed_nonlinear_input_can_finalize_in_same_session(tmp_path, monkeypatch):
    session, source, _, inspection, _, direct, _ = _inspected(tmp_path, monkeypatch)
    confirmed = _confirm(session, state="nonlinear")
    script, candidate = _delivery_script(session, source)
    def execute(command, **_kwargs):
        _write_delivery_outputs(source, candidate)
        return subprocess.CompletedProcess(command, 0, "Red layer: Mean: 100, Median: 90, Sigma: 5, Min: 0, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n")
    monkeypatch.setattr(core.subprocess, "run", execute)
    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **_kwargs:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    receipt = core.run_script(str(session), protocol="delivery.render", script_value=str(script),
        source_value=str(source), expected_values=[str(candidate), str(candidate.with_suffix(".fit"))], timeout=30)
    assert receipt["status"] == "success"
    assert receipt["input_confirmation"] == confirmed["confirmation"]
    review = _review(session, receipt["id"], candidate, parent_preview=direct)
    selection = _selection(session, receipt["id"], candidate, review)
    result = core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)
    assert result["status"] == "success"
    audit = core.load_json(session / "reports/final-audit.json")
    assert audit["input_confirmation"] == confirmed["confirmation"]
    assert audit["reference"]["sha256"] == core.sha256_file(direct)
    assert core.load_json(session / "runs" / f"{inspection['id']}.json")["image_domain"] == "unknown"
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)["status"] == "success"


def test_pre_upgrade_inspection_can_be_confirmed_and_replayed_but_not_used_by_new_scripts(tmp_path, monkeypatch):
    session, source, _, receipt, review_path, *_ = _inspected(tmp_path, monkeypatch)
    old_hash = "26a39e9af001eefdea37add6ad14150c36b5701a9c46ae5e20dbfd9cba87eac0"
    old_size = 5917
    provenance_path = session / receipt["script_provenance"]["path"]
    provenance = core.load_json(provenance_path)
    for reference in provenance["references"]:
        if reference["role"] == "primary":
            reference["sha256"] = old_hash
    core.atomic_write_json(provenance_path, provenance)
    receipt["script_provenance"] = {"path": receipt["script_provenance"]["path"],
                                    **core.fingerprint(provenance_path, include_path=False)}
    for reference in receipt["knowledge_validation"]["references"]:
        if reference["role"] == "primary":
            reference.update(sha256=old_hash, size=old_size)
    receipt_path = session / "runs" / f"{receipt['id']}.json"
    core.atomic_write_json(receipt_path, receipt)
    manifest = core.load_json(session / "manifest.json")
    manifest["runs"][0]["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)
    review = core.load_json(review_path)
    review["run_receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(review_path, review)
    before = {path: path.read_bytes() for path in (receipt_path, review_path, provenance_path)}
    assert core.check_review(session, receipt["id"], str(review_path))["status"] == "review_valid"
    assert _replay(session, source, receipt)["replayed"] is True
    assert _confirm(session)["status"] == "success"
    assert _replay(session, source, receipt)["replayed"] is True
    for path, data in before.items():
        assert path.read_bytes() == data
    with pytest.raises(core.ContractError, match="reference hash changed"):
        validation_rules.validate_script_file(session / receipt["script"]["path"], session=session,
            protocol="input.inspect", source=source,
            expected_outputs=[session / item["path"] for item in receipt["outputs"]],
            probe=core.load_json(session / "reports/tool-probe.json"))


def test_missing_inspect_run_and_missing_cli_evidence_are_structured_errors(tmp_path, monkeypatch, capsys):
    session, *_ = _inspected(tmp_path, monkeypatch)
    before = _bytes(session)
    assert cli.main(["confirm-input", "--session", str(session), "--run-id", "099-missing",
                     "--input-state", "linear", "--state-evidence", "user confirmed input_state=linear"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "input_confirmation_invalid"
    assert cli.main(["confirm-input", "--session", str(session), "--run-id", "010-input"]) == 2
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "invalid_arguments"
    assert _bytes(session) == before


def test_nonlinear_low_median_does_not_automatically_confirm_linear_input(tmp_path, monkeypatch):
    from statistics import median
    pixels = [0.001] * 990 + [0.005] * 9 + [0.2]
    stretched = [(-0.75 * x) / (-0.5 * x - 0.25) for x in pixels]
    assert median(stretched) < 0.05
    assert sum(x < 0.05 for x in stretched) / len(stretched) > 0.99
    session, *_ = _inspected(tmp_path, monkeypatch)
    before = _bytes(session)
    with pytest.raises(core.ContractError):
        _confirm(session, evidence=[f"Histogram median={median(stretched)}; apparently linear"])
    assert _bytes(session) == before
    assert core.load_json(session / "session.json")["context"]["input_state"] == "unknown"


def test_duplicate_confirmation_keys_are_rejected(tmp_path, monkeypatch):
    session, *_ = _inspected(tmp_path, monkeypatch)
    path = session / session_state.INPUT_CONFIRMATION_PATH
    path.write_text('{"schema": "invalid", "schema": "starun-siril.input-confirmation.v1"}')
    before = _bytes(session)
    with pytest.raises(core.ContractError):
        _confirm(session)
    assert _bytes(session) == before


@pytest.mark.parametrize("case", ["missing_index", "changed_index", "missing_binding", "invalid_fields"])
def test_confirmation_cannot_be_detached_from_lineage(tmp_path, monkeypatch, case):
    session, source, _, receipt, *_ = _inspected(tmp_path, monkeypatch)
    confirmed = _confirm(session)
    manifest_path = session / "manifest.json"
    manifest = core.load_json(manifest_path)
    if case == "missing_index":
        manifest["runs"] = []
    elif case == "changed_index":
        manifest["runs"][0]["receipt_sha256"] = "0" * 64
    elif case == "missing_binding":
        manifest.pop("input_confirmation")
    else:
        path = session / confirmed["confirmation"]["path"]
        record = core.load_json(path)
        record["confirmed_fields"]["input_state"] = []
        core.atomic_write_json(path, record)
        manifest["input_confirmation"].update(core.fingerprint(path, include_path=False))
    core.atomic_write_json(manifest_path, manifest)
    if case == "missing_binding":
        _, raw, _ = session_state.load_session(session)
        assert source_state(session, raw, source)["domain"] == "unknown"
    else:
        with pytest.raises(core.ContractError):
            session_state.load_session(session)


def test_confirmation_binding_is_verified_against_the_bytes_actually_read(tmp_path, monkeypatch):
    session, *_ = _inspected(tmp_path, monkeypatch)
    confirmed = _confirm(session)
    path = session / confirmed["confirmation"]["path"]
    read = session_state.read_input_confirmation
    def changed_before_read(target):
        if target == path:
            target.write_bytes(target.read_bytes() + b" ")
        return read(target)
    monkeypatch.setattr(session_state, "read_input_confirmation", changed_before_read)
    with pytest.raises(core.ContractError, match="confirmation changed"):
        session_state.load_session(session)
