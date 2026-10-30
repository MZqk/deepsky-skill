"""Source-bound masked saturation, native processing, review and replay contracts."""
from pathlib import Path
import copy
import json
import re
import subprocess

import numpy as np
import pytest

from .test_knowledge_contract import protocol_text
from PIL import Image

from .test_native_processing import native, write_fits, read_fits_pixels
from .test_deep_sky_siril import (
    core, session_state, _session, _input_inspect_run, _review,
    _write_ssf_provenance, _delivery_script,
)

ROOT = Path(__file__).parents[1]
BINARY = Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
RECIPE_POLICY = {"local_saturation":"luminance_peak_mask_v1","color_preservation":"vivid_preservation_v1"}
PARAMETERS = dict(amount=.08, low_start=.03, low_end=.08, high_start=.65, high_end=.90, sigma=2)


def local_text(root, source):
    for directory in ("artifacts", "previews", "scripts"):
        (root / directory).mkdir(exist_ok=True)
    reference = protocol_text("color.finish")
    fence = chr(96) * 3
    blocks = re.findall(r"^" + fence + r"ssf\s*\n(.*?)^" + fence + r"\s*$", reference, re.M | re.S)
    text = next(block for block in blocks if "# local-saturation:" in block)
    text = text.replace("/abs/session/artifacts/085-tone.fit", str(source))
    text = text.replace("$artifacts/085-tone$", native._variable(source, root))
    text = text.replace("/abs/session", str(root))
    primary = root / "artifacts/088-local-saturation.fit"
    commands = native.script_commands(text)
    outputs = []
    for row in commands:
        if row[0] in ("save", "savejpg"):
            outputs.append(core.validation_ops._normalized_write(row[0], row[1]))
        elif row[0] == "split":
            outputs.extend(native._path(value, root) for value in row[1:])
        elif row[0] == "rgbcomp":
            outputs.append(native._path(row[4].split("=", 1)[1], root))
    return text, primary, outputs


def scene():
    pixels = np.full((3, 128, 128), .02)
    pixels[:, 20:60, 20:60] = np.array([.30, .22, .15])[:, None, None]
    pixels[:, 70:100, 20:50] = np.array([.97, .15, .10])[:, None, None]
    pixels[:, 70:100, 70:100] = np.array([.04, .06, .10])[:, None, None]
    pixels[:, :16] = np.linspace(.02, .15, 128)[None, None, :]
    return pixels


def source_file(root, values=None, extra=()):
    if not (root/"session.json").exists():
        core.atomic_write_json(root/"session.json",{"execution_policy":RECIPE_POLICY.copy()})
    (root / "artifacts").mkdir(exist_ok=True)
    source = root / "artifacts/085-tone.fit"
    write_fits(source, scene() if values is None else values, extra)
    return source


def run_native(root, text):
    if not BINARY.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    script = root / "scripts/native-local.ssf"
    script.write_text(text)
    config = root / "local.ini"
    config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\nfits_save_icc=true\n[gui]\nicc_autoassignment=0\n")
    result = subprocess.run([str(BINARY), f"--initfile={config}", f"--directory={root}",
                             "--offline", f"--script={script}"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("key,value", [
    ("amount", 0), ("amount", .21), ("amount", True), ("amount", "0.1"),
    ("sigma", .49), ("sigma", 17), ("sigma", float("nan")), ("sigma", float("inf")),
    ("low_end", .03), ("high_end", .65), ("high_start", .01), ("low_start", -.01),
])
def test_declaration_rejects_invalid_parameters(key, value):
    document = {**PARAMETERS, key: value}
    with pytest.raises(core.ContractError):
        native.local_saturation_declaration("# local-saturation: " + json.dumps(document))


@pytest.mark.parametrize("text", [
    "# local-saturation: {}",
    "# local-saturation: []",
    "# local-saturation invalid",
    "# local-saturation: " + json.dumps({**PARAMETERS, "extra": 1}),
    "# local-saturation: " + json.dumps(PARAMETERS) + '\n# local-saturation: {}',
    "# local-saturation: " + json.dumps(PARAMETERS)[:-1] + ', "amount":0.1}',
])
def test_declaration_rejects_missing_unknown_duplicate_and_malformed(text):
    with pytest.raises(core.ContractError):
        native.local_saturation_declaration(text)


def test_documented_contract_and_plain_finish_isolation(tmp_path):
    source = source_file(tmp_path)
    text, primary, outputs = local_text(tmp_path, source)
    parameters = native.local_saturation_declaration(text)
    binding = native.validate_local_saturation(tmp_path, source, primary, native.script_commands(text), parameters)
    assert binding["previews"]["candidate"].endswith("-candidate.jpg")
    assert len(outputs) == len(set(outputs)) == 14
    assert parameters == PARAMETERS
    with pytest.raises(core.ContractError):
        native.validate_adjustment(tmp_path, source, primary, native.script_commands(text), "color.finish")
    with pytest.raises(core.ContractError):
        native.validate_parameters(["satu", ".08", "0", "6"], "color.finish")


@pytest.mark.parametrize("old,new", [
    ("satu 0.08 0 6", "satu 0.08 1 6"),
    ("satu 0.08 0 6", "satu 0.08 0 6\nrmgreen 3 .1"),
    ("gauss 2", "gauss 3"),
    ("min($artifacts/088-mask-smoothed$, $artifacts/088-mask-base$)", "$artifacts/088-mask-smoothed$"),
    (' -nosum', ' -rescale 0 1'),
    ("+ $artifacts/088-mask-rgb$ *", "+ 0.5 * $artifacts/088-mask-rgb$ *"),
    ("icc_assign sRGB", "icc_assign sRGBlinear"),
    ('savejpg "', 'savejpg "/different'),
])
def test_contract_rejects_operation_or_source_drift(tmp_path, old, new):
    source = source_file(tmp_path)
    text, primary, _ = local_text(tmp_path, source)
    with pytest.raises(core.ContractError):
        native.validate_local_saturation(tmp_path, source, primary,
                                         native.script_commands(text.replace(old, new, 1)), PARAMETERS)


@pytest.mark.parametrize("domain,separation,reviewed", [
    ("linear", "060-separate", True), ("unknown", "060-separate", True),
    ("nonlinear", None, True), ("nonlinear", "060-separate", False),
])
def test_requires_accepted_nonlinear_starless_parent(tmp_path, monkeypatch, domain, separation, reviewed):
    source = source_file(tmp_path)
    text, primary, _ = local_text(tmp_path, source)
    state = dict(domain=domain, separation_run=separation,
                 run={"id": "085-tone", "protocol": "tone.adjust", "transfer_chain": []})
    monkeypatch.setattr(native, "source_state", lambda *args: state)
    payload = {"execution_policy": core.session_state.EXECUTION_POLICY}
    def review(name):
        if not reviewed:
            raise core.ContractError("review_required", "Parent was not accepted")
    with pytest.raises(core.ContractError):
        native.prepare_processing(tmp_path, payload, "color.finish", source, primary,
                                  text, {}, None, None, None, review)


def test_requires_rgb_fits_and_new_policy(tmp_path, monkeypatch):
    source = source_file(tmp_path, np.full((128, 128), .2))
    text, primary, _ = local_text(tmp_path, source)
    with pytest.raises(core.ContractError, match="RGB FITS"):
        native.validate_local_saturation(tmp_path, source, primary, native.script_commands(text), PARAMETERS)
    write_fits(source, scene())
    monkeypatch.setattr(native, "source_state", lambda *args: dict(
        domain="nonlinear", separation_run="060-separate", run={"id": "085-tone"}))
    with pytest.raises(core.ContractError, match="current policy"):
        native.prepare_processing(tmp_path, {"execution_policy": {}}, "color.finish", source,
                                  primary, text, {}, None, None, None, lambda name: None)


def test_real_siril_mask_protection_metadata_and_reopen(tmp_path):
    wcs = [("CTYPE1", "'RA---TAN'"), ("CTYPE2", "'DEC--TAN'"), ("CRPIX1", 64),
           ("CRPIX2", 64), ("CRVAL1", 10), ("CRVAL2", 20),
           ("CD1_1", -.001), ("CD1_2", 0), ("CD2_1", 0), ("CD2_2", .001),
           ("EXPTIME", 30), ("LIVETIME", 3600), ("STACKCNT", 120)]
    source = source_file(tmp_path, extra=wcs)
    before = core.sha256_file(source)
    text, primary, outputs = local_text(tmp_path, source)
    binding = native.validate_local_saturation(tmp_path, source, primary, native.script_commands(text), PARAMETERS)
    run_native(tmp_path, text)
    checks = native.measure_local_saturation(tmp_path, source, primary, binding)
    assert max(checks["max_abs_errors"].values()) < 1e-6
    assert max(checks["protected_max_abs"]) < 1e-6
    assert checks["wcs_preserved"]
    assert checks["requires_visual_review"] is True
    assert checks["endpoint_counts"]["new_shadow"] == [0, 0, 0]
    assert checks["endpoint_counts"]["new_highlight"] == [0, 0, 0]
    raw, scale, zero = read_fits_pixels(primary)
    adjusted = raw * scale + zero
    original = scene()
    np.testing.assert_allclose(adjusted[:, 80:90, 25:45], original[:, 80:90, 25:45], atol=1e-6)
    assert np.ptp(adjusted[:, 40, 40]) > np.ptp(original[:, 40, 40])
    assert all(path.is_file() for path in outputs)
    for role in ("parent", "mask", "candidate"):
        with Image.open(tmp_path / binding["previews"][role]) as image:
            image.load()
            assert image.size == (128, 128)
    run_native(tmp_path, f'requires 1.4.4 1.5.0\nset32bits\nload "{primary}"\n'
                         f'savejpg "{tmp_path}/previews/reopened" 95\nclose\n')
    with Image.open(tmp_path / binding["previews"]["candidate"]) as a, Image.open(tmp_path / "previews/reopened.jpg") as b:
        np.testing.assert_array_equal(np.asarray(a), np.asarray(b))
    assert core.sha256_file(source) == before
    write_fits(primary, adjusted + .01, extra=wcs)
    with pytest.raises(core.ContractError, match="verification failed"):
        native.measure_local_saturation(tmp_path, source, primary, binding)


def test_native_local_to_recomposition_and_delivery(tmp_path, monkeypatch):
    from .test_native_processing import recompose_steps
    original = tmp_path / "full.fit"
    source = source_file(tmp_path)
    starless = tmp_path / "artifacts/060-starless.fit"
    write_fits(starless, scene())
    write_fits(original, scene() + .0005)
    for directory in ("runs",):
        (tmp_path / directory).mkdir()
    chain = [{"command": "mtf", "parameters": ["0", "0.5", "1"]}]
    separation = {"id": "060-separate", "protocol": "stars.separate", "source_path": "@input",
                  "primary_output": "artifacts/060-starless.fit", "image_domain": "linear",
                  "branch_binding": {"separation_run": "060-separate"}}
    stretch = {"id": "070-stretch", "protocol": "stretch", "source_path": separation["primary_output"],
               "primary_output": source.relative_to(tmp_path).as_posix(), "image_domain": "nonlinear",
               "transfer_chain": chain, "branch_binding": {"separation_run": separation["id"]}}
    receipts = [separation, stretch]
    monkeypatch.setattr(session_state, "_verified_success_receipts", lambda root: receipts)
    payload = {"input": {"path": str(original)}, "context": {"input_state": "linear", "target_type": "galaxy"},
               "execution_policy": RECIPE_POLICY.copy()}
    text, primary, _ = local_text(tmp_path, source)
    reviewed = []
    meta = native.prepare_processing(tmp_path, payload, "color.finish", source, primary, text, {},
                                     None, None, None, reviewed.append)
    assert set(reviewed) == {"060-separate", "070-stretch"}
    run_native(tmp_path, text)
    checks = native.validate_processing_outputs(tmp_path, source, primary, meta)
    assert checks["local_saturation"]["requires_visual_review"]
    local = {**meta, "id": "088-local", "protocol": "color.finish", "source_path": stretch["primary_output"]}
    receipts.append(local)
    for receipt in receipts:
        core.atomic_write_json(tmp_path / "runs" / (receipt["id"] + ".json"), receipt)
    steps, paths = recompose_steps(tmp_path, chain, original=original, starless=starless, source=primary)
    def render(row):
        if row[0] in {"load", "save", "savejpg", "pm"}:
            return row[0] + " " + json.dumps(row[1]) + (" " + " ".join(row[2:]) if len(row) > 2 else "")
        return " ".join(row)
    recomposition = "requires 1.4.4 1.5.0\nset32bits\n" + "\n".join(map(render, steps)) + "\n"
    loaded = {receipt["id"]: receipt for receipt in receipts}
    # This hand-written recomposition fixture exercises the legacy star transfer.
    payload["execution_policy"].pop("color_preservation",None)
    meta = native.prepare_processing(tmp_path, payload, "stars.recompose", primary, paths[-1], recomposition,
                                     {}, separation["id"], stretch["id"],
                                     lambda name: (tmp_path / "runs" / (name + ".json"), loaded[name]), reviewed.append)
    run_native(tmp_path, recomposition)
    closure = native.validate_processing_outputs(tmp_path, primary, paths[-1], meta)["closure"]
    assert max(closure["per_channel_max_abs"]) < 1e-5
    receipts.append({**meta, "id": "090-recompose", "protocol": "stars.recompose",
                     "source_path": primary.relative_to(tmp_path).as_posix()})
    payload["execution_policy"] = {k: v for k, v in payload["execution_policy"].items() if k != "delivery"}
    delivery = (f'requires 1.4.4 1.5.0\nset32bits\nload "{paths[-1]}"\n'
                f'icc_assign sRGB\nstat main\nsavejpg "{tmp_path}/previews/final" 95\nclose\n')
    native.prepare_processing(tmp_path, payload, "delivery.render", paths[-1], None, delivery, {},
                              None, None, None, reviewed.append)
    run_native(tmp_path, delivery)
    with Image.open(tmp_path / "previews/final.jpg") as image:
        image.load()
        assert image.size == (128, 128)


def test_core_static_review_replay_and_delivery_bindings(tmp_path, monkeypatch):
    real_run = subprocess.run
    root, original, _ = _session(tmp_path, monkeypatch, input_state="linear", color_ready=True)
    inspection, direct, diagnostic = _input_inspect_run(root, original, monkeypatch)
    _review(root, inspection["id"], diagnostic, parent_preview=direct)
    # Keep this binding fixture above the independent delivery histogram failure threshold.
    source = source_file(root, values=np.maximum(scene(), .08))
    starless = root / "artifacts/060-starless.fit"
    write_fits(starless, scene())
    frozen = [{"id": "060-separate", "protocol": "stars.separate", "status": "success", "source_path": "@input",
               "primary_output": "artifacts/060-starless.fit", "image_domain": "linear",
               "branch_binding": {"separation_run": "060-separate"}},
              {"id": "070-stretch", "protocol": "stretch", "status": "success",
               "source_path": "artifacts/060-starless.fit", "primary_output": source.relative_to(root).as_posix(),
               "image_domain": "nonlinear", "branch_binding": {"separation_run": "060-separate"},
               "transfer_chain": [{"command": "mtf", "parameters": ["0", "0.5", "1"]}]}]
    for receipt, artifact in zip(frozen, (starless, source)):
        receipt["outputs"] = [{"path": artifact.relative_to(root).as_posix(),
                               **core.fingerprint(artifact, include_path=False)}]
        core.atomic_write_json(root / "runs" / (receipt["id"] + ".json"), receipt)
        _review(root, receipt["id"], artifact, parent_preview=diagnostic)
    original_load = core._load_run
    def load(root, name):
        match = next((receipt for receipt in frozen if receipt["id"] == name), None)
        return (root / "runs" / (name + ".json"), match) if match else original_load(root, name)
    monkeypatch.setattr(core, "_load_run", load)
    def verified(root):
        result = [inspection, *frozen]
        for entry in core.load_json(root / "manifest.json")["runs"]:
            if entry["status"] == "success" and entry["id"] != inspection["id"]:
                result.append(core.load_json(root / "runs" / entry["receipt"]))
        return result
    monkeypatch.setattr(session_state, "_verified_success_receipts", verified)
    text, primary, outputs = local_text(root, source)
    script = root / "scripts/088-local.ssf"
    script.write_text(text)
    _write_ssf_provenance(root, script, "color.finish")
    args = dict(protocol="color.finish", script_value=str(script), source_value=str(source),
                expected_values=list(map(str, outputs)), primary_output_value=str(primary), timeout=60)
    with pytest.raises(core.ContractError, match="undeclared outputs"):
        core.run_script(str(root), validate_only=True, **{**args, "expected_values": [str(primary)]})
    static = core.run_script(str(root), validate_only=True, **args)
    assert static["executed"] is False and not primary.exists()
    def actual(command, **kwargs):
        return real_run([str(BINARY), *command[1:]], **kwargs)
    if not BINARY.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    monkeypatch.setattr(core.subprocess, "run", actual)
    receipt = core.run_script(str(root), **args)
    assert receipt["status"] == "success", receipt
    assert receipt["processing_checks"]["local_saturation"]["geometry_unchanged"]
    assert core.run_script(str(root), **args)["replayed"] is True
    review = core.review_materials_for_run(str(root), receipt["id"])
    review.update(verdict="accept", gates={name: "pass" for name in review["gates"]}, notes="Synthetic integration review fixture.")
    review_path = root / "reviews/088-local.json"
    core.atomic_write_json(review_path, review)
    core._validate_review(root, str(review_path), receipt["id"])
    for role in ("parent", "mask", "candidate"):
        changed = copy.deepcopy(review)
        changed["inspected_materials"] = [record for record in changed["inspected_materials"]
                                          if record["path"] != receipt["local_saturation"]["previews"][role]]
        core.atomic_write_json(review_path, changed)
        with pytest.raises(core.ContractError, match="parent, mask, candidate"):
            core._validate_review(root, str(review_path), receipt["id"])
    core.atomic_write_json(review_path, review)
    delivery_script, final = _delivery_script(root, primary)
    delivery = core.run_script(str(root), protocol="delivery.render", script_value=str(delivery_script),
                               source_value=str(primary), expected_values=[str(final), str(final.with_suffix(".fit"))], timeout=60)
    assert delivery["status"] == "success"
    materials = {record['path'] for record in core.review_materials_for_run(str(root), delivery['id'])['inspected_materials']}
    assert receipt['local_saturation']['previews']['candidate'] in materials
    assert receipt['local_saturation']['previews']['parent'] not in materials
    assert receipt['local_saturation']['previews']['mask'] not in materials
    parent_preview = root / receipt["local_saturation"]["previews"]["parent"]
    delivery_review = _review(root, delivery["id"], final, parent_preview=parent_preview)
    with pytest.raises(core.ContractError, match="verified parent preview"):
        core._validate_review(root, str(delivery_review), delivery["id"])
    candidate_preview = root / receipt["local_saturation"]["previews"]["candidate"]
    delivery_review = _review(root, delivery["id"], final, parent_preview=candidate_preview)
    core._validate_review(root, str(delivery_review), delivery["id"])
    forged = copy.deepcopy(receipt)
    forged["local_saturation"]["parameters"]["amount"] = .12
    assert not session_state._receipt_bindings_unchanged(root, core.load_json(root / "session.json"), forged)
    write_fits(root / receipt["local_saturation"]["layers"]["mask"], np.full((128, 128), .5))
    with pytest.raises(core.ContractError, match="binding|changed|invalid"):
        core.run_script(str(root), **args)


def test_pre_local_policy_keeps_existing_replay_and_finalize(tmp_path, monkeypatch):
    from .test_deep_sky_siril import _delivery_run, _selection
    root, source, _ = _session(tmp_path, monkeypatch)
    receipt, candidate = _delivery_run(root, source, monkeypatch)
    payload = core.load_json(root / "session.json")
    payload['execution_policy'] = {
        'log_diagnostics': 'fail_closed_v3', 'network': 'online_default_gaia_catalog_explicit_v3',
        'starnet': 'pinned_python_bridge_v1', 'background': 'heldout_material_gate_v3',
        'star_diagnostics': 'subject_roi_residual_v1', 'psf_provenance': 'native_star_samples_v1',
        'reporting': 'isolated_metrics_v1', 'neutral': 'heldout_benefit_safety_v1',
        'stretch': 'masked_dual_v1', 'visual_review': 'subject_visibility_v1',
        'guidance': 'evidence_bound_guidance_v1'}
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(root / "session.json", payload)
    assert session_state.known_execution_policy(payload["execution_policy"])
    assert not session_state.execution_policy_is_current(payload)
    args = dict(protocol="delivery.render", script_value=str(root / receipt["script"]["path"]),
                source_value=str(source), expected_values=[str(candidate), str(candidate.with_suffix(".fit"))], timeout=30)
    before = (root / "runs" / (receipt["id"] + ".json")).read_bytes()
    assert core.run_script(str(root), **args)["replayed"]
    assert (root / "runs" / (receipt["id"] + ".json")).read_bytes() == before
    review = _review(root, receipt["id"], candidate)
    selection = _selection(root, receipt["id"], candidate, review)
    assert core.finalize_session(str(root), selection_value=str(selection), keep_intermediates=True)["status"] == "success"
    fresh = root / "scripts/999-local.ssf"
    fresh.write_text('requires 1.4.4 1.5.0\nset32bits\n# local-saturation: {}\nclose\n')
    for validate_only in (False, True):
        with pytest.raises(core.ContractError) as error:
            core.run_script(str(root), **{**args, "protocol": "color.finish", "script_value": str(fresh)},
                            validate_only=validate_only)
        assert error.value.code == "legacy_session_read_only"
