"""Tone contracts, lineage, receipt replay and native Siril smoke checks."""
from pathlib import Path
import copy
import shlex
import subprocess

import numpy as np
import pytest
from PIL import Image

try:
    from .test_deep_sky_siril import (
        _session, _input_inspect_run, _review, _write_ssf_provenance,
        _write_fits, _write_jpeg, _write_delivery_outputs, _delivery_script, core, session_state,
    )
    from .test_native_processing import write_fits, recompose_steps, native, metrics, read_fits_pixels
except (ImportError, ValueError):
    from tests.test_deep_sky_siril import (
        _session, _input_inspect_run, _review, _write_ssf_provenance,
        _write_fits, _write_jpeg, _write_delivery_outputs, _delivery_script, core, session_state,
    )
    from tests.test_native_processing import write_fits, recompose_steps, native, metrics, read_fits_pixels

GHS = "ght -D=0.2 -B=0 -LP=0.02 -SP=0.15 -HP=0.90 -human -clipmode=rgbblend"


def _tone_proposal(session, source, value, run_id):
    """Supply an explicit synthetic-fixture decision, never a production default."""
    import json
    from deep_sky_siril_artifacts import read_scientific_pixels
    from deep_sky_siril_stretch import record
    if core.load_json(session/'session.json')['execution_policy'].get('tone_adjustment') != 'remaining_goal_v1':
        return value
    data = read_scientific_pixels(source)[0]; height, width = data.shape[1:]
    path = session/'reports'/(run_id+'-regions.json')
    core.atomic_write_json(path, {'schema': 'starun-siril.regions.v1',
        'reference': {'sha256': core.sha256_file(source), 'width': width, 'height': height},
        'regions': [{'id': 'subject', 'role': 'faint', 'rect': [0, 0, width, height]}]})
    p = {'schema': 'starun-siril.tone-proposal.v1', 'regions': record(session, path),
         'goal': 'Synthetic fixture tone change', 'upstream_assessment': 'Known synthetic upstream evidence inspected',
         'remaining_issue': 'The fixture intentionally requests an additional tone change',
         'parameter_reason': 'Explicit operator supplied by the regression case'}
    return '# tone-adjust: '+json.dumps(p)+'\n'+value


def _tone_text(session, source, operations="mtf 0 0.55 1", run_id="085-tone"):
    return (
        f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nicc_assign sRGB\nstat main\n'
        f'savejpg "{session}/previews/{run_id}-parent" 95\n{operations}\nstat main\n'
        f'save "{session}/artifacts/{run_id}" -chksum\n'
        f'savejpg "{session}/previews/{run_id}-candidate" 95\nclose\n'
    )


def _tone_script(session, source, operations="mtf 0 0.55 1"):
    script = session / "scripts/085-tone.ssf"
    script.write_text(_tone_proposal(session, source, _tone_text(session, source, operations), '085-tone'))
    _write_ssf_provenance(session, script, "tone.adjust")
    outputs = [session / "artifacts/085-tone.fit",
               session / "previews/085-tone-parent.jpg",
               session / "previews/085-tone-candidate.jpg"]
    return script, outputs


@pytest.mark.parametrize("operations,names", [
    ("mtf 0 0.55 1", ["mtf"]), (GHS, ["ght"]), ("mtf 0 0.55 1\n" + GHS, ["mtf", "ght"]),
])
def test_tone_static_run_replay_and_delivery_review(tmp_path, monkeypatch, operations, names):
    session, source, _ = _session(tmp_path, monkeypatch)
    inspection, direct, diagnostic = _input_inspect_run(session, source, monkeypatch)
    _review(session, inspection["id"], diagnostic, parent_preview=direct)
    script, outputs = _tone_script(session, source, operations)
    args = dict(protocol="tone.adjust", script_value=str(script), source_value=str(source),
                expected_values=list(map(str, outputs)), timeout=30)
    static = core.run_script(str(session), validate_only=True, **args)
    assert static["executed"] is False and not any(path.exists() for path in outputs)
    assert [item["command"] for item in static["tone_chain"]] == names
    assert static["transfer_chain"] == []
    assert static["network"]["effective_offline"] is False

    def fake_run(command, **kwargs):
        _write_fits(outputs[0], nonzero=True)
        for path in outputs[1:]:
            _write_jpeg(path)
        return subprocess.CompletedProcess(command, 0,
            "Reading FITS: file tone.fit, 1 layer(s), 12x8 pixels, 16 bits\n"
            "Gray layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, "
            "bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n")

    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(str(session), **args)
    assert receipt["status"] == "success"
    assert receipt["tone_chain"] == static["tone_chain"]
    assert receipt["review_bindings"]
    assert core.run_script(str(session), **args)["replayed"] is True

    review = _review(session, receipt["id"], outputs[2], parent_preview=outputs[1])
    core._validate_review(session, str(review), receipt["id"])
    document = core.load_json(review)
    document["inspected_materials"] = document["inspected_materials"][:1]
    core.atomic_write_json(review, document)
    with pytest.raises(core.ContractError, match="both direct parent and candidate"):
        core._validate_review(session, str(review), receipt["id"])
    _review(session, receipt["id"], outputs[2], parent_preview=outputs[1])

    delivery_script, final = _delivery_script(session, outputs[0])
    def fake_delivery(command, **kwargs):
        _write_delivery_outputs(outputs[0], final)
        return subprocess.CompletedProcess(command, 0, "")
    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **k:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    monkeypatch.setattr(core.subprocess, "run", fake_delivery)
    delivery = core.run_script(str(session), protocol="delivery.render", script_value=str(delivery_script),
                               source_value=str(outputs[0]), expected_values=[str(final), str(final.with_suffix(".fit"))], timeout=30)
    assert delivery["status"] == "success"
    materials = {record['path'] for record in core.review_materials_for_run(str(session), delivery['id'])['inspected_materials']}
    assert outputs[2].relative_to(session).as_posix() in materials
    assert outputs[1].relative_to(session).as_posix() not in materials
    review = _review(session, delivery["id"], final, parent_preview=outputs[1])
    with pytest.raises(core.ContractError, match="verified parent preview"):
        core._validate_review(session, str(review), delivery["id"])
    review = _review(session, delivery["id"], final, parent_preview=outputs[2])
    core._validate_review(session, str(review), delivery["id"])

    receipt_path = session / "runs/085-tone.json"
    forged = copy.deepcopy(receipt)
    forged["tone_chain"][0]["parameters"] = ["0", "0.9", "1"]
    core.atomic_write_json(receipt_path, forged)
    manifest = core.load_json(session / "manifest.json")
    next(entry for entry in manifest["runs"] if entry["id"] == receipt["id"])["receipt_sha256"] = core.sha256_file(receipt_path)
    core.atomic_write_json(session / "manifest.json", manifest)
    with pytest.raises(core.ContractError, match="binding|invalid"):
        core.run_script(str(session), **args)


@pytest.mark.parametrize("state,code", [
    ("linear", "processing_contract_invalid"), ("unknown", "protocol_not_applicable"),
])
def test_tone_rejects_wrong_input_domain(tmp_path, monkeypatch, state, code):
    session, source, _ = _session(tmp_path, monkeypatch, input_state=state)
    script, outputs = _tone_script(session, source)
    with pytest.raises(core.ContractError) as raised:
        core.run_script(str(session), protocol="tone.adjust", script_value=str(script),
                        source_value=str(source), expected_values=list(map(str, outputs)),
                        timeout=30, validate_only=True)
    assert raised.value.code == code


def test_tone_requires_accepted_input_inspection_and_declared_previews(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch)
    script, outputs = _tone_script(session, source)
    args = dict(protocol="tone.adjust", script_value=str(script), source_value=str(source),
                expected_values=list(map(str, outputs)), timeout=30, validate_only=True)
    with pytest.raises(core.ContractError, match="verified input.inspect"):
        core.run_script(str(session), **args)
    inspection, direct, diagnostic = _input_inspect_run(session, source, monkeypatch)
    with pytest.raises(core.ContractError, match="review"):
        core.run_script(str(session), **args)
    _review(session, inspection["id"], diagnostic, parent_preview=direct, verdict="reject")
    with pytest.raises(core.ContractError, match="Accepted visual review"):
        core.run_script(str(session), **args)
    _review(session, inspection["id"], diagnostic, parent_preview=direct)
    with pytest.raises(core.ContractError, match="undeclared outputs"):
        core.run_script(str(session), **{**args, "expected_values": [str(outputs[0])]})
    text = script.read_text().replace("mtf 0 0.55 1", "mtf 0 0.55 1\nautostretch -linked -4.5 0.12")
    script.write_text(text)
    _write_ssf_provenance(session, script, "tone.adjust")
    with pytest.raises(core.ContractError, match="autostretch is forbidden"):
        core.run_script(str(session), **args)


@pytest.mark.parametrize("old,new", [
    ("mtf 0 0.55 1", ""), ("mtf 0 0.55 1", "mtf 0 0.5 1"),
    ("mtf 0 0.55 1", "mtf 0 nan 1"), ("mtf 0 0.55 1", "mtf 0 0.55 1 R"),
    ("mtf 0 0.55 1", GHS.replace("-D=0.2", "-D=11")),
    ("mtf 0 0.55 1", GHS.replace("-HP=0.90 ", "")),
    ("mtf 0 0.55 1", GHS.replace("-human", "-independent")),
    ("mtf 0 0.55 1", GHS.replace("-D=0.2", "-D=0")),
    ('mtf 0 0.55 1', 'load "/abs/other.fit"\nmtf 0 0.55 1'),
    ("stat main\nsave ", "mtf 0 0.55 1\nstat main\nsave "),
    ("-candidate", "-parent"),
    ('stat main\nsavejpg "{session}/previews/085-tone-parent" 95\n', ""),
])
def test_tone_rejects_invalid_parameters_and_operation_order(tmp_path, old, new):
    source = tmp_path / "source.fit"
    primary = tmp_path / "artifacts/085-tone.fit"
    text = _tone_text(tmp_path, source)
    old, new = old.replace("{session}", str(tmp_path)), new.replace("{session}", str(tmp_path))
    if old == "stat main\nsave ":
        text = text.replace("savejpg", "mtf 0 0.55 1\nsavejpg", 1)
    else:
        text = text.replace(old, new)
    with pytest.raises(core.ContractError):
        native.validate_adjustment(tmp_path, source, primary, native.script_commands(text))


def test_tone_rejects_transform_after_primary_and_duplicate_primary(tmp_path):
    source = tmp_path / "source.fit"
    primary = tmp_path / "artifacts/085-tone.fit"
    text = _tone_text(tmp_path, source)
    for extra in ("mtf 0 0.6 1\n", f'save "{primary}" -chksum\n'):
        modified = text.replace(f'savejpg "{tmp_path}/previews/085-tone-candidate"', extra + f'savejpg "{tmp_path}/previews/085-tone-candidate"')
        with pytest.raises(core.ContractError):
            native.validate_adjustment(tmp_path, source, primary, native.script_commands(modified))


@pytest.mark.parametrize("position", ["before", "after"])
def test_tone_inherits_lineage_and_preserves_recomposition_chain(tmp_path, monkeypatch, position):
    a = tmp_path / "artifacts"
    a.mkdir()
    original, linear, stretched, full = tmp_path / "i.fit", a / "a.fit", a / "f-a.fit", a / "full-parent.fit"
    primary = a / "085-tone.fit"
    for path in (original, linear, stretched, full, primary):
        write_fits(path, np.full((3, 32, 32), .1))
    chain = [{"command": "mtf", "parameters": ["0", ".1", "1"]}]
    separation = dict(id="060-separate", protocol="stars.separate", source_path="@input",
                      primary_output="artifacts/a.fit", image_domain="linear", branch_binding={"separation_run": "060-separate"})
    stretch = dict(id="070-stretch", protocol="stretch", source_path="artifacts/a.fit",
                   primary_output="artifacts/f-a.fit", image_domain="nonlinear", transfer_chain=chain,
                   branch_binding={"separation_run": "060-separate"})
    receipts = [separation, stretch]
    if position == "after":
        receipts.append(dict(id="090-recompose", protocol="stars.recompose", source_path="artifacts/f-a.fit",
                             primary_output="artifacts/full-parent.fit", image_domain="nonlinear", transfer_chain=chain,
                             branch_binding={"separation_run": None}))
    monkeypatch.setattr(session_state, "_verified_success_receipts", lambda _: receipts)
    payload = {"input": {"path": str(original)}, "context": {"input_state": "linear"}}
    source = stretched if position == "before" else full
    reviewed = []
    def review(name):
        reviewed.append(name)
    metadata = native.prepare_processing(tmp_path, payload, "tone.adjust", source, primary,
                                         _tone_text(tmp_path, source), {}, None, None, None, review)
    assert reviewed == [receipts[-1]["id"]]
    assert metadata["transfer_chain"] == chain
    assert metadata["tone_chain"] == [{"command": "mtf", "parameters": ["0", "0.55", "1"]}]
    assert metadata["branch_binding"]["separation_run"] == ("060-separate" if position == "before" else None)
    def rejected(_):
        raise core.ContractError("review_required", "Parent review rejected")
    with pytest.raises(core.ContractError, match="Parent review rejected"):
        native.prepare_processing(tmp_path, payload, "tone.adjust", source, primary,
                                  _tone_text(tmp_path, source), {}, None, None, None, rejected)
    for auxiliary in (tmp_path / "previews/source.jpg", a / "auxiliary.fit"):
        with pytest.raises(core.ContractError, match="Scientific parents"):
            native.prepare_processing(tmp_path, payload, "tone.adjust", auxiliary, primary,
                                      _tone_text(tmp_path, auxiliary), {}, None, None, None, review)
    if position == "before":
        receipts.append(dict(id="085-tone", protocol="tone.adjust", source_path="artifacts/f-a.fit", **metadata))
        record = tmp_path / "receipt.json"
        record.write_text("{}")
        steps, paths = recompose_steps(tmp_path, chain, original=original, starless=linear, source=primary)
        text = "\n".join(" ".join(shlex.quote(v) for v in row) for row in steps)
        recomposed = native.prepare_processing(tmp_path, payload, "stars.recompose", primary, paths[-1], text, {},
                                              "060-separate", "070-stretch",
                                              lambda name: (record, separation if name == "060-separate" else stretch), review)
        assert recomposed["transfer_chain"] == chain
        assert "tone_chain" not in recomposed
        assert recomposed["branch_binding"]["stretch_run"] == "070-stretch"


@pytest.mark.parametrize("channels", [1, 3])
def test_real_siril_tone_operators_and_direct_previews(tmp_path, channels):
    binary = Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
    if not binary.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "previews").mkdir()
    source = tmp_path / "source.fit"
    values = np.tile(np.linspace(.02, .4, 64), (channels, 64, 1))
    write_fits(source, values)
    original_hash = core.sha256_file(source)
    for number, operations in enumerate(("mtf 0 0.55 1", GHS, "mtf 0 0.55 1\n" + GHS)):
        run_id = f"08{number}-tone"
        text = _tone_text(tmp_path, source, operations, run_id)
        output = tmp_path / f"artifacts/{run_id}.fit"
        native.validate_adjustment(tmp_path, source, output, native.script_commands(text))
        script = tmp_path / f"{run_id}.ssf"
        script.write_text(text)
        config = tmp_path / f"{run_id}.ini"
        config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\n")
        result = subprocess.run([str(binary), f"--initfile={config}", f"--directory={tmp_path}", "--offline",
                                 f"--script={script}"], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        raw, scale, zero = read_fits_pixels(output)
        adjusted = raw * scale + zero
        assert adjusted.shape == values.shape and np.isfinite(adjusted).all()
        assert np.max(np.abs(adjusted - values)) > 1e-5
        if number == 0:
            expected = ((.55 - 1) * values) / ((2 * .55 - 1) * values - .55)
            assert np.allclose(adjusted, expected, atol=1e-6)
        histogram = metrics.analyze_image_histogram(output)
        assert all(c["shadow_clip_rate"] == 0 for c in histogram["channels"])
        assert metrics.evaluate_stage_gates("tone.adjust", histogram)[1] == "uncertain"
        for suffix in ("parent", "candidate"):
            with Image.open(tmp_path / f"previews/{run_id}-{suffix}.jpg") as image:
                image.load()
                assert image.size == (64, 64)
    assert core.sha256_file(source) == original_hash
