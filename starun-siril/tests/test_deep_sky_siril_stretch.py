"""Adaptive parameter requests, native trials, immutable chain binding and legacy compatibility."""
from ._stretch_factory import bind_exception, solve_bound

import copy
import json
from pathlib import Path
import subprocess

import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

import deep_sky_siril_stretch as stretch
import deep_sky_siril_session as session_state
import deep_sky_siril_processing as native
from deep_sky_siril_contract import ContractError, EXECUTION_POLICY, fingerprint, sha256_file
from .test_deep_sky_siril import _fake_probe, _write_ssf_provenance, core, tooling

BINARY = Path("/Applications/Siril.app/Contents/MacOS/siril-cli")


@pytest.fixture(autouse=True)
def retired_operator_producer(monkeypatch):
    from ._policy_pins import outgoing_stretch_producer
    outgoing_stretch_producer(monkeypatch)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fits.writeto(path, np.asarray(data), overwrite=True)
    return path


def make_request(root, source, methods=("mtf",)):
    directory = root/"reports/070-stretch"
    directory.mkdir(parents=True, exist_ok=True)
    region_path = directory/"regions.json"
    regions = [{"id": "sky", "role": "background", "rect": [8, 8, 16, 16]},
               {"id": "faint", "role": "faint", "rect": [16, 0, 32, 16]},
               {"id": "core", "role": "core", "rect": [0, 16, 16, 32]},
               {"id": "stars", "role": "stars", "rect": [16, 16, 32, 32]}]
    region_path.write_text(json.dumps({"schema": "starun-siril.regions.v1",
                                     "reference": {"sha256": sha256_file(source), "width": 32, "height": 32},
                                     "regions": regions}))
    operators = []
    for method in methods:
        step = {"method": method, "purpose": "Reveal faint structure while protecting sky and core",
                "constraints": [{"region": r, "statistic": "median", "min": 0, "max": 1}
                                for r in ("sky", "faint", "core", "stars")]}
        if method == "ght":
            step["landmarks"] = {k: {"region": r, "statistic": "median"}
                                 for k, r in (("LP", "sky"), ("SP", "faint"), ("HP", "core"))}
        operators.append(step)
    request = {"schema": "starun-siril.stretch-request.v1",
               "source": {"path": "@input", "sha256": sha256_file(source)},
               "regions": {"path": region_path.relative_to(root).as_posix(), "sha256": sha256_file(region_path)},
               "operators": operators,
               "manual_lookup": {"status": "not_needed", "reason": "Complete documented explicit protocol skeleton",
                                 "evidence": []}}
    path = directory/"request.json"
    path.write_text(json.dumps(request))
    return path, request, regions


def source_data(channels=1):
    y, x = np.mgrid[:32, :32]
    data = .02 + x*.00001 + y*.00002
    data[16:, 16:] += .02
    data[:16, :16] += .06
    data[:16, 16:] += .08
    return data.astype(np.float32) if channels == 1 else np.stack([data, data*1.05, data*.95]).astype(np.float32)


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path/"session"
    for name in ("scripts", "artifacts", "previews", "runs", "reviews", "reports"):
        (root/name).mkdir(parents=True)
    source = write(tmp_path/"input.fit", source_data())
    runtime = tmp_path/"siril-cli"
    runtime.write_text("#!/bin/sh\nexit 0\n")
    runtime.chmod(0o755)
    payload = {"input": {"path": str(source), **fingerprint(source, include_path=False)},
               "context": {"input_state": "linear", "channel_mode": "broadband", "target_type": "nebula"},
               "execution_policy": copy.deepcopy(EXECUTION_POLICY)}
    (root/"session.json").write_text(json.dumps(payload))
    probe = _fake_probe(runtime)
    (root/"reports/tool-probe.json").write_text(json.dumps(probe))
    monkeypatch.setattr(session_state, "load_session", lambda _: (root, payload, {}))
    monkeypatch.setattr(stretch, "accepted_parent", lambda *args: None)
    request_path, request, regions = make_request(root, source)
    return root, source, payload, request_path, request, regions


def mock_trials(monkeypatch):
    def run(self, source, chain):
        self._claim_attempt()
        self.count += 1
        data = stretch.pixels(source)
        for step in chain:
            if step["command"] == "mtf":
                low, mid, high = map(float, step["parameters"])
                x = np.clip((data-low)/(high-low), 0, 1)
                data = (mid-1)*x/((2*mid-1)*x-mid)
            elif step["command"] == "asinh":
                args = step["parameters"]
                assert args[0] == '-human' and args[3:] == ['-clipmode=rgbblend']
                luminance = np.sum(data*np.array([.2126, .7152, .0722])[:, None, None], axis=0)
                value = np.arcsinh(float(args[1])*luminance)/np.arcsinh(float(args[1]))
                data = (data-float(args[2]))*np.divide(value, luminance, out=np.ones_like(value), where=luminance > 0)
                assert np.all((data >= 0) & (data <= 1)), 'Fixture requires no RGB clipping'
            else:
                raise AssertionError("Unsupported mock transfer")
        output = write(self.directory/f"trial-{self.count}.fit", data.astype(np.float32))
        rgb=np.repeat(data,3,axis=0) if len(data)==1 else data
        Image.fromarray(np.uint8(np.clip(rgb.transpose(1,2,0),0,1)*255)).save(output.with_suffix(".jpg"))
        script = self.evidence_dir/f"trial-{self.count}.ssf"
        log = self.evidence_dir/f"trial-{self.count}.log"
        commands = "\n".join(" ".join([step["command"], *step["parameters"]]) for step in chain)
        script.write_text('requires 1.4.4 1.5.0\nset32bits\nload '+json.dumps(str(source))+
                          "\n"+commands+"\nsave "+json.dumps(str(output))+" -chksum\nclose\n")
        log.write_text("Mock native pixel response")
        return output, {"script": stretch.record(self.session, script), "log": stretch.record(self.session, log),
                        "output_sha256": sha256_file(output)}
    monkeypatch.setattr(stretch.NativeTrials, "run", run)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16, np.float32])
def test_fits_storage_normalization_and_float_outliers(tmp_path, dtype):
    values = np.array([[10, 20], [30, 40]], dtype=dtype)
    if dtype == np.float32:
        values = np.array([[.25, .25], [.25, 1.01]], dtype=dtype)
    source = write(tmp_path/"input.fit", values)
    data = stretch.pixels(source)
    if dtype == np.float32:
        assert np.median(data) == .25
        assert data.max() > 1
        assert stretch.calculate_adaptive_mtf(source)["diagnostics"]["median"] == .25
    else:
        assert np.allclose(data[0], values/(255 if dtype == np.uint8 else 65535))


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -.1, 1.2])
def test_legacy_rejects_invalid_targets(tmp_path, bad):
    source = write(tmp_path/"input.fit", source_data())
    with pytest.raises(ContractError):
        stretch.calculate_adaptive_mtf(source, target_bg=bad)


def test_flat_and_nonfinite_data(tmp_path):
    source = write(tmp_path/"input.fit", np.full((32, 32), .1, dtype=np.float32))
    result = stretch.calculate_adaptive_mtf(source)
    assert 0 <= result["shadow"] < .1
    assert 0 < result["midtones"] < 1
    write(source, np.full((32, 32), np.nan, dtype=np.float32))
    with pytest.raises(ContractError, match="finite"):
        stretch.calculate_adaptive_mtf(source)


def test_blackpoint_strict_count_and_rgb_safety():
    data = np.random.default_rng(42).normal(.25, .005, (3, 100, 100)).astype(np.float32)
    black = stretch.safe_blackpoint([data])
    assert all(np.mean(plane <= black) < .0001 for plane in data)
    assert black < float(np.median(data)-2.8*1.4826*np.median(np.abs(data-np.median(data))))
    data[0, 0, 0] = 0
    with pytest.raises(ContractError, match="already reaches"):
        stretch.safe_blackpoint([data])


@pytest.mark.parametrize("change", ["hash", "rect", "constraints", "landmarks", "duplicate"])
def test_request_evidence_validation(context, change):
    root, source, payload, path, request, _ = context
    if change == "hash":
        request["source"]["sha256"] = "0"*64
    elif change == "constraints":
        request["operators"][0]["constraints"] = request["operators"][0]["constraints"][:1]
    elif change == "landmarks":
        request["operators"][0]["method"] = "ght"
        request["operators"][0]["landmarks"] = {}
    else:
        regions_path = root/request["regions"]["path"]
        regions = json.loads(regions_path.read_text())
        if change == "rect":
            regions["regions"][0]["rect"] = [0, 0, 40, 40]
        else:
            regions["regions"][1]["id"] = "sky"
        regions_path.write_text(json.dumps(regions))
        request["regions"]["sha256"] = sha256_file(regions_path)
    path.write_text(json.dumps(request))
    with pytest.raises((ContractError, ValueError)):
        stretch.read_request(root, payload, path)


def test_top_left_regions_and_rgb_luminance():
    data = np.zeros((3, 4, 4))
    data[:, 2:, :2] = np.array([.1, .2, .3])[:, None, None]
    region = [{"id": "top-left", "rect": [0, 0, 2, 2]}]
    values = stretch.statistics(data, region)["top-left"]
    assert values["median"] == pytest.approx(.2126*.1+.7152*.2+.0722*.3)
    assert values["span"] == 0


def test_solve_generate_binding_no_execution_or_accept(context, monkeypatch):
    root, source, payload, path, request, _ = context
    mock_trials(monkeypatch)
    solved = solve_bound(root, path, "070-stretch")
    assert solved["status"] == "solved"
    assert solved["report"]["steps"][0]["native_evaluation_count"] <= 128
    monkeypatch.setattr(stretch.NativeTrials, "run", lambda *a: pytest.fail("Generate must not execute"))
    generated = stretch.generate_stretch_assets(root, parameters=solved["parameters_path"])
    text = Path(generated["script_path"]).read_text()
    binding = stretch.adaptive_binding(root, payload, source, text, solved["report"]["transfer_chain"], "070-stretch")
    assert binding["parameters"]["sha256"] == sha256_file(Path(solved["parameters_path"]))
    assert all(json.loads(p.read_text())["protocol"]=="input.inspect" for p in (root/"runs").glob("*.json"))
    assert not list((root/"reviews").glob("070-stretch*.json"))
    with pytest.raises(ContractError, match="already exists"):
        stretch.generate_stretch_assets(root, parameters=solved["parameters_path"])
    changed = copy.deepcopy(solved["report"]["transfer_chain"])
    changed[0]["parameters"][1] = ".1"
    with pytest.raises(ContractError, match="differs"):
        stretch.adaptive_binding(root, payload, source, text, changed, "070-stretch")
    report_path = Path(solved["parameters_path"])
    report_path.write_text(report_path.read_text()+" ")
    with pytest.raises(ContractError, match="hash changed"):
        stretch.adaptive_binding(root, payload, source, text, solved["report"]["transfer_chain"], "070-stretch")


def test_conflicts_budget_timeout_and_drift(context, monkeypatch):
    root, source, payload, path, request, _ = context
    mock_trials(monkeypatch)
    request["operators"][0]["constraints"][1].update(min=2, max=3)
    path.write_text(json.dumps(request))
    with pytest.raises(ContractError, match="constraint preflight needs revision"):
        solve_bound(root, path, "071-conflict")
    request["operators"][0]["constraints"][1].update(min=0, max=1)
    path.write_text(json.dumps(request))
    def timeout(*args):
        raise TimeoutError("Test deadline")
    monkeypatch.setattr(stretch.NativeTrials, "run", timeout)
    assert solve_bound(root, path, "072-timeout")["status"] == "timed_out"
    item = bind_exception(root, request, "073-drift")
    bound_request = Path(item['request'])
    def drift(self, *args):
        bound_request.write_text(bound_request.read_text()+" ")
        raise TimeoutError("Test drift")
    monkeypatch.setattr(stretch.NativeTrials, "run", drift)
    assert stretch.solve_stretch_parameters(root, item['request'], item['run_id'], 60)["status"] == "failed"


def test_combination_measures_current_domain_and_native_output(context, monkeypatch):
    root, source, payload, path, request, regions = context
    mock_trials(monkeypatch)
    request["operators"].append(copy.deepcopy(request["operators"][0]))
    path.write_text(json.dumps(request))
    solved = solve_bound(root, path, "070-stretch")
    assert solved["status"] == "solved"
    first, second = solved["report"]["steps"]
    assert second["input_domain"] == "nonlinear_intermediate"
    assert first["input_statistics"] != second["input_statistics"]
    assert second["input_sha256"] == first["selected_replay"]["output_sha256"]
    chain = solved["report"]["transfer_chain"]
    primary, _ = stretch.NativeTrials(root, {}, root/"artifacts", root/"reports", 0).run(source, chain)
    generated = stretch.generate_stretch_assets(root, parameters=solved["parameters_path"])
    binding = stretch.adaptive_binding(root, payload, source, Path(generated["script_path"]).read_text(), chain, "070-stretch")
    assert stretch.measure_adaptive_output(root, source, primary, binding)["requires_visual_review"] is True
    write(primary, np.zeros_like(source_data()))
    with pytest.raises(ContractError, match="differs"):
        stretch.measure_adaptive_output(root, source, primary, binding)


def test_legacy_is_explicit_and_preserves_rules(context, monkeypatch):
    root, source, payload, _, _, _ = context
    with pytest.raises(ContractError, match="explicitly"):
        stretch.generate_stretch_assets(root)
    monkeypatch.setattr(session_state, "_verified_success_receipts", lambda _: [])
    with pytest.raises(ContractError,match='replay-only'):
        stretch.generate_stretch_assets(root,legacy_mtf=True)
    assert stretch.calculate_adaptive_mtf(source, "galaxy")["diagnostics"]["target_bg"] == .20
    assert stretch.calculate_adaptive_mtf(source, "cluster")["diagnostics"]["target_bg"] == .18


@pytest.mark.parametrize("methods,channels", [(("mtf",), 1), (("asinh",), 3), (("ght",), 3),
                                             (("autostretch",), 1), (("mtf", "ght"), 3)])
def test_real_siril_selected_operators(context, monkeypatch, methods, channels):
    if not BINARY.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    root, source, payload, _, _, _ = context
    write(source, source_data(channels))
    payload["input"].update(fingerprint(source, include_path=False))
    (root/"session.json").write_text(json.dumps(payload))
    (root/"reports/tool-probe.json").write_text(json.dumps(_fake_probe(BINARY)))
    path, request, _ = make_request(root, source, methods)
    solved = solve_bound(root, path, "070-stretch", timeout=120)
    assert solved["status"] == "solved", solved["report"].get("reason")
    assert [c["command"] for c in solved["report"]["transfer_chain"]] == list(methods)
    assert all(s["native_evaluation_count"] <= 128 for s in solved["report"]["steps"])
    assert all(json.loads(p.read_text())["protocol"]=="input.inspect" for p in (root/"runs").glob("*.json"))
    assert not list((root/"reviews").glob("070-stretch*.json"))



def test_full_branch_checks_budget_and_forbids_autostretch(context, monkeypatch):
    root, source, payload, path, request, _ = context
    full = write(root/"artifacts/full.fit", source_data()+.001)
    monkeypatch.setattr(stretch, "accepted_parent", lambda *args: full)
    mock_trials(monkeypatch)
    solved = solve_bound(root, path, "070-stretch")
    assert solved["status"] == "solved"
    assert solved["report"]["bindings"]["full_source"]["sha256"] == sha256_file(full)
    assert solved["report"]["steps"][0]["trials"][0]["full_audit"]["clipping"]
    assert solved["report"]["steps"][0]["native_evaluation_count"] <= 128
    request["operators"][0]["method"] = "autostretch"
    path.write_text(json.dumps(request))
    with pytest.raises(ContractError, match="Starless"):
        solve_bound(root, path, "071-auto")


def test_full_branch_geometry_conflict(context, monkeypatch):
    root, source, payload, path, request, regions = context
    full = write(root/"artifacts/full.fit", np.full((16, 16), .01, dtype=np.float32))
    monkeypatch.setattr(stretch, "accepted_parent", lambda *args: full)
    mock_trials(monkeypatch)
    from ._stretch_factory import explicit_request
    from .test_stretch_routing_v29 import prepare,goals_for
    current=(root,source,payload,path,explicit_request(root,request),regions)
    prepared=prepare(current,goals_for(current),'071-geometry')
    item=prepared['candidates']['profile']
    solved=stretch.solve_stretch_parameters(root,item['request'],item['run_id'])
    assert solved["status"] == "failed"
    assert "geometry" in solved["report"]["reason"]


def test_runtime_and_trial_evidence_drift(context, monkeypatch):
    root, source, payload, path, request, _ = context
    mock_trials(monkeypatch)
    solved = solve_bound(root, path, "070-stretch")
    trace = solved["report"]["steps"][0]["trials"][0]["evidence"]
    (root/trace["log"]["path"]).write_text("changed native log")
    with pytest.raises(ContractError, match="hash changed"):
        stretch.generate_stretch_assets(root, parameters=solved["parameters_path"])


def test_real_run_receipt_replay_and_constraints(tmp_path, monkeypatch):
    if not BINARY.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    from .test_deep_sky_siril import _review
    root, source = tmp_path/"session", write(tmp_path/"input.fit", source_data())
    monkeypatch.setattr(tooling, "probe_tools", lambda **kw: _fake_probe(BINARY, offline=kw.get("offline", False)))
    session_state.init_session(str(source), str(root), input_state="linear", state_evidence=["user confirmed input_state=linear; synthetic data"],
                               channel_mode="broadband", channel_map=None, target_name="Synthetic",
                               target_type="nebula", style="natural", stars="preserve", offline=True,
                               keep_intermediates=True)
    direct, auto = root/"previews/010-input-direct.jpg", root/"previews/010-input-autostretch.jpg"
    script = root/"scripts/010-input.ssf"
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nstat main\nsavejpg "{direct.with_suffix("")}" 95\nclose\n'
                      f'load "{source}"\nautostretch -linked -2.8 .22\nsavejpg "{auto.with_suffix("")}" 95\nclose\n')
    _write_ssf_provenance(root, script, "input.inspect")
    inspected = core.run_script(str(root), protocol="input.inspect", script_value=str(script), source_value=str(source),
                                expected_values=[str(direct), str(auto)], timeout=60)
    assert inspected["status"] == "success"
    _review(root, "010-input", auto, parent_preview=direct)
    path, request, _ = make_request(root, source)
    solved = solve_bound(root, path, "070-stretch", timeout=60)
    assert solved["status"] == "solved", solved["report"].get("reason")
    generated = stretch.generate_stretch_assets(root, parameters=solved["parameters_path"])
    args = dict(protocol="stretch", script_value=generated["script_path"], source_value=str(source),
                expected_values=[generated["primary_output"], generated["preview_output"]],
                primary_output_value=generated["primary_output"], timeout=60)
    receipt = core.run_script(str(root), **args)
    assert receipt["status"] == "success", receipt.get("processing_checks")
    assert receipt["processing_checks"]["adaptive_parameters"]["requires_visual_review"] is True
    assert not (root/"reviews/070-stretch.json").exists()
    assert core.run_script(str(root), **args)["status"] == "success"
    payload = session_state.load_session(root)[1]
    assert session_state._receipt_bindings_unchanged(root, payload, receipt)
    parameter_path = Path(solved["parameters_path"])
    parameter_path.write_text(parameter_path.read_text()+" ")
    assert not session_state._receipt_bindings_unchanged(root, payload, receipt)
    with pytest.raises(ContractError):
        core.run_script(str(root), **args)


def test_legacy_stretch_uses_explicit_reviewed_linear_parent(context, monkeypatch):
    root, source, _, _, _, _ = context
    parent = write(root / "artifacts/031-background.fit", source_data() + .005)
    monkeypatch.setattr(native, "source_state", lambda *args: {"domain": "linear"})
    before = parent.read_bytes()
    with pytest.raises(ContractError,match='replay-only'):
        stretch.generate_stretch_assets(root,legacy_mtf=True,parent_path=parent)
    assert parent.read_bytes()==before
