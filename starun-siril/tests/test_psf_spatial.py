"""PSF v2 axial parameters, native-grid diagnostics and immutable review evidence."""
import copy
import json
import math
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from .test_native_processing import ROOT, write_fits
from .test_psf_provenance import (SETTINGS, FakeNativeSiril, deconvolution_session,
                                  material_fixture, native_model, sample_contract, star_table)
import deep_sky_siril_psf as diagnostics
import siril_psf_samples as bridge
from deep_sky_siril_contract import sha256_file


def records(*models):
    return bridge.serialize_stars(models)


@pytest.mark.parametrize("angles,expected", [([89, -89], -90), ([10, 190, -170], 10),
                                             ([179, -1], -1), ([-271, 89], 89)])
def test_axial_mean_wraps_equivalent_angles(angles, expected):
    stars = records(*(native_model(angle=a) for a in angles))
    result = bridge.orientation_statistics(stars)
    assert result["status"] == "measured"
    assert bridge.wrap_angle(result["mean_angle_deg"] - expected) == pytest.approx(0, abs=1e-12)
    assert 0 <= result["concentration"] <= 1
    assert bridge.model_parameters(stars)["angle"] == result["mean_angle_deg"]


def test_axis_swap_and_native_float_means_preserve_equivalent_models():
    old = records(native_model(fwhmx=4, fwhmy=2, angle=10))
    swapped = records(native_model(fwhmx=2, fwhmy=4, angle=-80))
    assert bridge.model_parameters(old) == bridge.model_parameters(swapped)
    mixed = records(native_model(fwhmx=4, fwhmy=2, angle=10),
                    native_model(fwhmx=2, fwhmy=4, angle=-80))
    assert bridge.model_parameters(mixed) == bridge.model_parameters(old)
    legacy = bridge.model_parameters(records(native_model(angle=89), native_model(angle=-89)),
                                     policy=bridge.LEGACY_POLICY)
    assert legacy["angle"] == 0
    assert bridge.model_parameters(records(native_model(angle=89), native_model(angle=-89)))["angle"] == -90


@pytest.mark.parametrize("symmetric,circular", [(True, False), (False, True), (True, True)])
def test_circular_and_symmetric_kernels_have_no_direction(symmetric, circular):
    stars = records(native_model(fwhmy=4 if circular else 3, angle=47))
    result = bridge.orientation_statistics(stars, symmetric=symmetric)
    assert result["status"] == "not_applicable" and result["mean_angle_deg"] is None
    params = bridge.model_parameters(stars, symmetric=symmetric)
    assert params["angle"] == 0 and params["ratio"] == 1


def test_nearly_circular_star_has_small_direction_weight():
    stars = records(native_model(fwhmx=4, fwhmy=2, angle=20),
                    native_model(fwhmx=4, fwhmy=3.999999, angle=-60))
    assert bridge.orientation_statistics(stars)["mean_angle_deg"] == pytest.approx(20, abs=1e-4)


def test_degenerate_non_circular_capture_stops_before_native_fallback(tmp_path):
    _, _, reports, _, document = material_fixture(tmp_path)
    native = FakeNativeSiril(document, [native_model(angle=0), native_model(angle=90)])
    with pytest.raises(bridge.PsfGenerationError) as error:
        bridge.capture(native, document)
    evidence = error.value.evidence
    assert evidence["status"] == "failed" and evidence["orientation"]["status"] == "degenerate"
    assert evidence["psf"]["method"] == "indeterminate_direction"
    assert evidence["psf"]["native_command"] is None and native.commands == []
    assert len(diagnostics.parse_native_stars(reports / "before.tsv")["rows"]) == 2
    assert not (reports / "psf.fit").exists()


def grid_report(tmp_path, before, after, *, geometry=True, reason=None):
    a = star_table(tmp_path / "before.tsv", before)
    b = star_table(tmp_path / "after.tsv", after)
    match = diagnostics.matched_fwhm(a, b, geometry_unchanged=geometry, policy=bridge.POLICY)
    evidence = {"images": {}, "materials": {}, "matched_fwhm": match,
                "psf_samples": {"status": "uncertain"}}
    sizes = {"parent_tsv": {"width": 90, "height": 90, "channels": 1},
             "candidate_tsv": {"width": 90, "height": 90, "channels": 1}} if geometry else {}
    report = diagnostics.spatial_report({"parent_tsv": a, "candidate_tsv": b}, sizes, evidence, {})
    return report, match


@pytest.mark.parametrize("pattern", ["constant", "radial", "corner-worse", "sparse"])
def test_grid_describes_regional_psf_without_suitability_claims(tmp_path, pattern):
    before, after = [], []
    for cell in range(9):
        row, col = divmod(cell, 3)
        if pattern == "sparse" and cell != 4:
            continue
        old = 4 if pattern != "radial" else 4 + math.hypot(col-1, row-1)
        new = old if pattern in ("constant", "radial") else old-1 if cell == 4 else old+1
        before.append((cell+1, col*30+15, row*30+15, old, old/2, 0))
        after.append((cell+101, col*30+15, row*30+15, new, new/2, 0))
    report, matched = grid_report(tmp_path, before, after)
    assert report["summary"]["matched_count"] == len(matched["pairs"])
    assert sum(c["matched"]["count"] for c in report["cells"]) == len(matched["pairs"])
    if pattern == "corner-worse":
        assert report["cells"][4]["matched"]["groups"][0]["metrics"]["fwhm_mean"]["difference"]["median"] < 0
        assert report["cells"][0]["matched"]["groups"][0]["metrics"]["fwhm_mean"]["difference"]["median"] > 0
    elif pattern == "radial":
        assert report["cells"][0]["parent"]["groups"][0]["metrics"]["fwhmx"]["median"] > 4
    elif pattern == "sparse":
        assert len(report["summary"]["empty_cells"]) == 8
        assert report["cells"][0]["matched"]["status"] == "uncertain"
    assert "applicable" not in json.dumps(report)
    json.dumps(report, allow_nan=False)


def test_half_open_boundaries_bad_rows_duplicates_and_cross_cell_moves(tmp_path):
    before = [(1, 0, 0, 4, 2, 0), (2, 30, 30, 4, 2, 0), (3, 60, 60, 4, 2, 1),
              (4, 15, 15, 4, 2, 0), (4, 16, 16, 4, 2, 0),
              (6, 70, 10, -1, 2, 0), (7, 29.75, 15, 4, 2, 0)]
    star_table(tmp_path / "before.tsv", before)
    a = diagnostics.parse_native_stars(tmp_path / "before.tsv", spatial=True)
    b = star_table(tmp_path / "after.tsv", [(101, 0, 0, 3, 1.5, 0), (102, 30, 30, 3, 1.5, 0),
                                            (107, 30.25, 15, 3, 1.5, 0)])
    original = diagnostics.matched_fwhm(a, b, geometry_unchanged=True, policy=bridge.POLICY)
    report = diagnostics.spatial_report({"parent_tsv": a, "candidate_tsv": b},
        {"parent_tsv": {"width": 90, "height": 90, "channels": 1},
         "candidate_tsv": {"width": 90, "height": 90, "channels": 1}},
        {"images": {}, "materials": {}, "matched_fwhm": original, "psf_samples": {"status": "uncertain"}}, {})
    assert report["cells"][0]["parent"]["counts"]["duplicate"] == 2
    assert report["cells"][4]["parent"]["counts"]["captured"] == 1
    assert report["cells"][8]["parent"]["counts"]["saturated"] == 1
    assert report["cells"][2]["parent"]["counts"]["invalid"] == 1
    assert report["summary"]["cross_cell_moves"] == 1
    assert diagnostics._cell([90, 10], {"width": 90, "height": 90}) is None
    assert diagnostics._cell([30, 30], {"width": 90, "height": 90}) == 4
    assert original == diagnostics.matched_fwhm(a, b, geometry_unchanged=True, policy=bridge.POLICY)


@pytest.mark.parametrize("issue", ["geometry", "settings", "layer", "domain", "empty"])
def test_incomparable_or_unpaired_regions_do_not_report_improvement(tmp_path, issue):
    a = star_table(tmp_path / "before.tsv", [(1, 15, 15, 4, 2, 0)])
    b = star_table(tmp_path / "after.tsv", [] if issue == "empty" else [(101, 15, 15, 3, 1.5, 0)])
    if issue == "settings":
        b["settings"]["sigma"] = 2
    if issue == "layer":
        b["rows"][0]["layer"] = 1
    match = diagnostics.matched_fwhm(a, b, geometry_unchanged=issue not in ("geometry", "domain"), policy=bridge.POLICY)
    report = diagnostics.spatial_report({"parent_tsv": a, "candidate_tsv": b},
        {"parent_tsv": {"width": 90, "height": 90, "channels": 1}},
        {"images": {}, "materials": {}, "matched_fwhm": match, "psf_samples": {"status": "uncertain"}}, {})
    assert report["status"] == "uncertain"
    assert all(c["matched"]["status"] == "uncertain" and not c["matched"]["groups"] for c in report["cells"])


def test_gaussian_and_moffat_groups_and_pair_direction_are_separate(tmp_path):
    a = star_table(tmp_path / "before.tsv", [(1, 10, 10, 4, 2, 0), (2, 20, 20, 4, 4, 0)])
    b = star_table(tmp_path / "after.tsv", [(101, 10, 10, 3, 1.5, 0), (102, 20, 20, 3, 3, 0)])
    for table in (a, b):
        table["rows"][1].update(profile="Gaussian", layer=1)
    a["rows"][0]["angle"], b["rows"][0]["angle"] = 89, -89
    match = diagnostics.matched_fwhm(a, b, geometry_unchanged=True, policy=bridge.POLICY)
    assert match["pairs"][0]["measurements"]["direction"]["difference_deg"] == 2
    assert match["pairs"][1]["measurements"]["direction"]["status"] == "not_applicable"
    report = diagnostics.spatial_report({"parent_tsv": a, "candidate_tsv": b},
        {"parent_tsv": {"width": 90, "height": 90, "channels": 3}},
        {"images": {}, "materials": {}, "matched_fwhm": match, "psf_samples": {"status": "uncertain"}}, {})
    groups = report["cells"][0]["parent"]["groups"]
    assert len(groups) == 2 and "beta" not in groups[1]["metrics"]
    assert report["cells"][0]["matched"]["groups"][0]["direction_change"]["absolute_difference_deg"]["median"] == 2


def v2_run(tmp_path, monkeypatch, *, legacy=False):
    from .test_deep_sky_siril import core, _write_fits
    session, source, reports, document, script, kwargs = deconvolution_session(tmp_path, monkeypatch, legacy=legacy)
    def fake_run(command, **_kwargs):
        write_fits(Path(kwargs["primary_output_value"]), np.zeros((8, 12)))
        from .test_deep_sky_siril import _write_jpeg
        for name in ("055-parent", "055-candidate"):
            _write_jpeg(session / "previews" / (name + ".jpg"), size=(12, 8))
        receipt = bridge.capture(FakeNativeSiril(document, [native_model()]), document)
        receipt.update(contract_sha256=sha256_file(reports / "contract.json"), sirilpy_version=bridge.VERSION)
        (reports / "bridge.json").write_text(json.dumps(receipt))
        bridge.export_models(reports / "after.tsv", records(native_model(fwhmx=3, fwhmy=2)), document["detection"])
        return subprocess.CompletedProcess(command, 0, "")
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    monkeypatch.setattr(core, "_decode_scientific_output", lambda path, **k:
        dict(passed=True, decoder={"artifact_fingerprint": core.fingerprint(path, include_path=False)}))
    return session, reports, kwargs, core.run_script(str(session), **kwargs)


@pytest.mark.parametrize("damage", ["missing-json", "missing-markdown", "tamper-json", "tamper-markdown"])
def test_bound_report_drift_rejects_replay_and_adoption(tmp_path, monkeypatch, damage):
    from .test_deep_sky_siril import core
    session, reports, kwargs, run = v2_run(tmp_path, monkeypatch)
    path = reports / ("psf-spatial.json" if damage.endswith("json") else "psf-spatial.md")
    if damage.startswith("missing"):
        path.unlink()
    else:
        path.write_text(path.read_text()+"\n")
    before = {p: p.read_bytes() for p in reports.iterdir() if p.is_file()}
    with pytest.raises(core.ContractError):
        core.run_script(str(session), **kwargs)
    with pytest.raises(core.ContractError):
        core.review_materials_for_run(str(session), run["id"])
    assert {p: p.read_bytes() for p in reports.iterdir() if p.is_file()} == before


@pytest.mark.parametrize("failure", ["json-write", "markdown-write"])
def test_report_io_failure_preserves_science_replay_but_blocks_acceptance(tmp_path, monkeypatch, failure):
    from .test_deep_sky_siril import core
    original = diagnostics.bind_spatial_reports
    def fail_write(session, binding, report, *, write):
        if write:
            if failure == "markdown-write":
                path = session / binding["spatial_reports"]["json"]
                path.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
            raise OSError("Controlled report failure")
        return original(session, binding, report, write=False)
    monkeypatch.setattr(diagnostics, "bind_spatial_reports", fail_write)
    session, reports, kwargs, run = v2_run(tmp_path, monkeypatch)
    check = run["processing_checks"]["deconvolution"]
    assert run["status"] == "success" and run["processing_checks"]["status"] == "passed"
    assert check["spatial_reports"]["status"] == "unavailable" and check["report_warnings"]
    before = {p: p.read_bytes() for p in reports.iterdir()}
    assert core.run_script(str(session), **kwargs)["replayed"]
    assert {p: p.read_bytes() for p in reports.iterdir()} == before
    review = core.review_materials_for_run(str(session), run["id"])
    review.update(verdict="accept", gates={k: "pass" for k in review["gates"]},
                  notes="Central region measured; sparse corners inspected; controlled test acceptance.")
    path = session / "reviews/055-deconvolve.json"
    path.write_text(json.dumps(review))
    with pytest.raises(core.ContractError, match="both bound spatial reports"):
        core.check_review(session, run["id"], str(path))


@pytest.mark.parametrize("missing", ["json", "markdown", "parent", "candidate", "psf", None])
def test_v2_review_requires_reports_and_actual_pixels(tmp_path, monkeypatch, missing):
    from .test_deep_sky_siril import core
    session, _, kwargs, run = v2_run(tmp_path, monkeypatch)
    check = run["processing_checks"]["deconvolution"]
    assert check["spatial_reports"]["status"] == "bound"
    report = json.loads((session / check["spatial_reports"]["reports"]["json"]["path"]).read_text())
    assert report["bindings"]["images"] == check["images"]
    assert report["kernel"]["orientation"]["mean_angle_deg"] == check["psf_samples"]["parameters"]["angle"]
    assert report["summary"]["kernel_sample_count"] == 1 and report["summary"]["truncated"] is False
    review = core.review_materials_for_run(str(session), run["id"])
    required = {"json": check["spatial_reports"]["reports"]["json"]["path"],
                "markdown": check["spatial_reports"]["reports"]["markdown"]["path"],
                "parent": run["source_path"], "candidate": run["primary_output"], "psf": check["materials"]["psf"]["path"]}
    if missing:
        review["inspected_materials"] = [r for r in review["inspected_materials"] if r["path"] != required[missing]]
    review.update(verdict="accept", gates={k: "pass" for k in review["gates"]},
                  notes="Central sample measured; sparse cells limit inference; actual pixels inspected.")
    path = session / "reviews/055-deconvolve.json"
    path.write_text(json.dumps(review))
    if missing:
        with pytest.raises(core.ContractError):
            core.check_review(session, run["id"], str(path))
    else:
        assert core.check_review(session, run["id"], str(path))["status"] == "review_valid"
        frozen = {p: p.read_bytes() for p in session.rglob("*") if p.is_file()}
        assert core.run_script(str(session), **kwargs)["replayed"]
        assert all(p.read_bytes() == raw for p, raw in frozen.items())


def test_capture_truncation_is_reported_without_inventing_fallback_kernel_count(tmp_path):
    source, candidate, reports, contract, document = material_fixture(tmp_path)
    document["detection"]["maxstars"] = 20
    contract.write_text(json.dumps(document))
    models = [native_model(xpos=i % 12, ypos=i // 12) for i in range(21)]
    receipt = bridge.capture(FakeNativeSiril(document, models), document)
    receipt.update(contract_sha256=sha256_file(contract), sirilpy_version=bridge.VERSION)
    (reports / "bridge.json").write_text(json.dumps(receipt))
    bridge.export_models(reports / "after.tsv", records(*models[:20]), document["detection"])
    binding = {"policy": bridge.POLICY, "contract": "reports/055-deconvolve/contract.json",
        "contract_sha256": sha256_file(contract), "bridge_receipt": "reports/055-deconvolve/bridge.json",
        "parent_tsv": "reports/055-deconvolve/before.tsv", "candidate_tsv": "reports/055-deconvolve/after.tsv",
        "psf": "reports/055-deconvolve/psf.fit",
        "spatial_reports": {"json": "reports/055-deconvolve/psf-spatial.json",
                            "markdown": "reports/055-deconvolve/psf-spatial.md"}}
    result = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, "", write_reports=True)
    summary = result["spatial_summary"]
    assert summary["captured_count"] == 20 and summary["native_detected_count"] == 21
    assert summary["truncated"] is True and summary["kernel_sample_count"] == 20
    binding.pop("bridge_receipt")
    binding.pop("contract")
    fallback = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, "")
    assert fallback["spatial_summary"]["kernel_sample_count"] is None


@pytest.mark.parametrize("inline", [False, True])
def test_frozen_v1_deconvolution_replays_and_finalizes_without_spatial_backfill(tmp_path, monkeypatch, inline):
    from .test_deep_sky_siril import core, _delivery_run, _review, _selection
    from deep_sky_siril_processing import script_commands
    import deep_sky_siril_session as state
    session, reports, kwargs, run = v2_run(tmp_path, monkeypatch, legacy=True)
    delivery, image = _delivery_run(session, Path(core.load_json(session / "session.json")["input"]["path"]), monkeypatch)
    bridge_path = reports / "bridge.json"
    sample = json.loads(bridge_path.read_text())
    sample["policy"] = bridge.LEGACY_POLICY
    bridge_path.write_text(json.dumps(sample))
    source = Path(core.load_json(session / "session.json")["input"]["path"])
    primary = session / run["primary_output"]
    binding = diagnostics.prepare_deconvolution(session, source, primary,
        script_commands((session / run["script"]["path"]).read_text()), policy=bridge.LEGACY_POLICY, run_id=run["id"])
    run["deconvolution_binding"] = binding
    run["processing_checks"]["deconvolution"] = diagnostics.measure_deconvolution(
        session, source, primary, binding, (session / run["log"]["path"]).read_text())
    check = run["processing_checks"]["deconvolution"]
    assert check["policy"] == bridge.LEGACY_POLICY and "spatial_reports" not in check
    if inline:
        check["psf_samples"]["captured_samples"] = sample["stars"]
        check["psf_samples"]["samples"] = sample["stars"]
        check["matched_fwhm"]["pairs"] = diagnostics.matched_fwhm(
            diagnostics.parse_native_stars(reports / "before.tsv"),
            diagnostics.parse_native_stars(reports / "after.tsv"), geometry_unchanged=True)["pairs"]
    for record in run["outputs"]:
        if record["path"] == "reports/055-deconvolve/bridge.json":
            record.update(core.fingerprint(bridge_path, include_path=False))
    path = session / "runs/055-deconvolve.json"
    core.atomic_write_json(path, run)
    manifest = core.load_json(session / "manifest.json")
    next(r for r in manifest["runs"] if r["id"] == run["id"])["receipt_sha256"] = sha256_file(path)
    core.atomic_write_json(session / "manifest.json", manifest)
    payload = core.load_json(session / "session.json")
    payload['execution_policy']['star_diagnostics'] = 'subject_roi_residual_v1'
    payload["execution_policy"]["guidance"] = "evidence_bound_guidance_v1"
    payload["execution_policy"]["starnet"] = "pinned_python_bridge_v1"
    payload["execution_policy"]["background"] = "heldout_material_gate_v3"
    payload["execution_policy"].pop("delivery")
    payload["execution_policy"]["psf_provenance"] = bridge.LEGACY_POLICY
    payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
    for key in ('recomposition_review', 'filter_metadata', 'background_sampling', 'restoration'):
        payload['execution_policy'].pop(key, None)
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(session / "session.json", payload)
    assert state.known_execution_policy(payload["execution_policy"])
    for report in reports.glob("psf-spatial.*"):
        report.unlink()
    frozen = {p: p.read_bytes() for p in session.rglob("*") if p.is_file()}
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: pytest.fail("Legacy replay executed Siril"))
    assert core.run_script(str(session), **kwargs)["replayed"]
    assert all(p.read_bytes() == data for p, data in frozen.items())
    assert not list(reports.glob("psf-spatial.*"))
    review = _review(session, delivery["id"], image)
    selection = _selection(session, delivery["id"], image, review)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)["status"] == "success"
    assert not list(reports.glob("psf-spatial.*"))
    fresh = session / "scripts/999-deconvolve.ssf"
    fresh.write_text((session / run["script"]["path"]).read_text())
    with pytest.raises(core.ContractError) as error:
        core.run_script(str(session), **{**kwargs, "script_value": str(fresh)}, validate_only=True)
    assert error.value.code == "legacy_session_read_only"


def test_real_siril_periodic_psf_kernel_and_rl(tmp_path):
    from deep_sky_siril_artifacts import read_fits_pixels
    binary = shutil.which("siril-cli") or "/Applications/Siril.app/Contents/MacOS/siril-cli"
    if not Path(binary).is_file():
        pytest.skip("Real Siril is unavailable")
    yy, xx = np.mgrid[:384, :384]
    plane = np.full((384, 384), .025)
    for i, y in enumerate(range(40, 350, 45)):
        for j, x in enumerate(range(40, 350, 45)):
            angle = math.radians(89 if (i+j) % 2 else -89)
            u = (xx-x)*math.cos(angle)+(yy-y)*math.sin(angle)
            v = -(xx-x)*math.sin(angle)+(yy-y)*math.cos(angle)
            plane += .15 * (1 + u*u/16 + v*v/6.25)**-3
    source = tmp_path / "source.fit"
    write_fits(source, plane)
    reports = tmp_path / "reports/055-deconvolve"
    reports.mkdir(parents=True)
    candidate = tmp_path / "artifacts/055-deconvolve.fit"
    candidate.parent.mkdir()
    models = [native_model(xpos=x+.5, ypos=384-y-.5, fwhmx=4, fwhmy=2.5,
                           angle=89 if (i+j) % 2 else -89)
              for i, y in enumerate(range(40, 350, 45)) for j, x in enumerate(range(40, 350, 45))]
    controlled = records(*models)
    params = bridge.model_parameters(controlled)
    document = sample_contract(source, reports)
    bridge.export_models(reports / "before.tsv", controlled, document["detection"])
    script = tmp_path / "periodic.ssf"
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nsetfindstar ' + ' '.join(SETTINGS) + '\n'
        + 'makepsf manual -moffat ' + ' '.join(f'-{k}={v:.17g}' for k, v in params.items() if k != 'profile')
        + f' -ks=31 "-savepsf={reports}/psf.fit"\n'
        + f'rl "-loadpsf={reports}/psf.fit" -iters=6 -tv -alpha=3000.0\n'
        + f'save "{candidate}" -chksum\nclose\nload "{candidate}"\nsetfindstar ' + ' '.join(SETTINGS) + '\n'
        + f'findstar -layer=0 -maxstars=500 "-out={reports}/after.tsv"\nclose\n')
    config = tmp_path / "s.ini"
    config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\n")
    result = subprocess.run([binary, f"--initfile={config}", f"--directory={tmp_path}", "--offline", f"--script={script}"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    assert abs(params["angle"]) > 85 and params["ratio"] > 1.4
    assert "makepsf" in result.stdout + result.stderr and "rl" in result.stdout + result.stderr
    raw, scale, zero = read_fits_pixels(reports / "psf.fit")
    kernel = np.asarray(raw[0], dtype=float)*scale+zero
    y, x = np.mgrid[:kernel.shape[0], :kernel.shape[1]]
    assert np.isfinite(kernel).all() and kernel.min() >= 0 and kernel.sum() > 0
    assert np.sum(kernel*(y-15)**2) > np.sum(kernel*(x-15)**2)*1.5
    raw, scale, zero = read_fits_pixels(candidate)
    assert np.isfinite(np.asarray(raw)*scale+zero).all()
    binding = {"policy": bridge.POLICY, "parent_tsv": "reports/055-deconvolve/before.tsv",
        "candidate_tsv": "reports/055-deconvolve/after.tsv", "psf": "reports/055-deconvolve/psf.fit",
        "spatial_reports": {"json": "reports/055-deconvolve/psf-spatial.json",
                            "markdown": "reports/055-deconvolve/psf-spatial.md"}}
    check = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, result.stdout+result.stderr, write_reports=True)
    assert check["psf_samples"]["status"] == "uncertain"
    assert check["spatial_reports"]["status"] == "bound" and check["spatial_summary"]["occupied_cells"] == 9
    assert bridge.orientation_statistics(controlled)["mean_angle_deg"] == params["angle"]
    assert check["spatial_summary"]["kernel_sample_count"] is None
    frozen = {p: p.read_bytes() for p in reports.iterdir()}
    replay = diagnostics.measure_deconvolution(tmp_path, source, candidate, binding, result.stdout+result.stderr)
    assert diagnostics.deconvolution_evidence_matches(replay, check)
    assert {p: p.read_bytes() for p in reports.iterdir()} == frozen


def test_numeric_cancellation_cutoff_and_symmetric_override():
    cancelling = records(native_model(angle=0), native_model(angle=90))
    assert bridge.orientation_statistics(cancelling)["status"] == "degenerate"
    resolved = records(native_model(angle=0), native_model(angle=90-1e-10))
    assert bridge.orientation_statistics(resolved)["status"] == "measured"
    assert bridge.model_parameters(cancelling, symmetric=True)["angle"] == 0


def test_legacy_empty_pairs_normalize_without_backfill():
    evidence = {"policy": bridge.LEGACY_POLICY, "matched_fwhm": {"pairs": [], "status": "uncertain"}}
    assert diagnostics.deconvolution_evidence_matches(evidence, evidence)


def test_native_fallback_detected_after_display_transform_is_incomparable(tmp_path, monkeypatch):
    from .test_deep_sky_siril import _write_fits
    from deep_sky_siril_processing import script_commands
    session, source, reports, document, script, kwargs = deconvolution_session(tmp_path, monkeypatch, bridge_enabled=False)
    primary = Path(kwargs["primary_output_value"])
    _write_fits(primary)
    commands = [row for row in script_commands(script.read_text()) if row[0] != "fmul"]
    post = next(i for i, row in enumerate(commands) if row[0] == "findstar" and "after.tsv" in " ".join(row))
    commands.insert(post-1, ["autostretch", "-linked", "-2.8", "0.22"])
    binding = diagnostics.prepare_deconvolution(session, source, primary, commands, policy=bridge.POLICY)
    assert "domain" in binding["candidate_table_reason"]
    legacy = diagnostics.prepare_deconvolution(session, source, primary, commands, policy=bridge.LEGACY_POLICY)
    assert "candidate_table_reason" not in legacy
    bridge.export_models(reports / "before.tsv", records(native_model()), document["detection"])
    bridge.export_models(reports / "after.tsv", records(native_model(fwhmx=3, fwhmy=2)), document["detection"])
    result = diagnostics.measure_deconvolution(session, source, primary, binding, "", write_reports=True)
    assert result["matched_fwhm"]["status"] == "uncertain"
    report = json.loads((session / binding["spatial_reports"]["json"]).read_text())
    assert all(not cell["matched"]["groups"] for cell in report["cells"])


@pytest.mark.parametrize('damage',['metadata','checks','display','units'])
def test_restoration_binding_is_required_during_receipt_replay(tmp_path,monkeypatch,damage):
    from .test_deep_sky_siril import core
    session,_,_,run=v2_run(tmp_path,monkeypatch)
    changed=copy.deepcopy(run)
    if damage=='metadata':changed.pop('restoration_validation')
    if damage=='checks':changed['processing_checks'].pop('restoration_validation')
    if damage=='display':changed['restoration_validation']['display']['parent_preview']=changed['restoration_validation']['display']['candidate_preview']
    if damage=='units':changed['restoration_validation']['scientific_source']['units']='unverified'
    payload=core.session_state.load_session(session)[1]
    assert not core.session_state._receipt_bindings_unchanged(session,payload,changed)
