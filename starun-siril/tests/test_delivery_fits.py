"""Paired FITS/JPEG delivery and immutable three-file finalization."""
import copy
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from .test_native_processing import write_fits
from .test_deep_sky_siril import core, _session, _delivery_run, _review, _selection, _write_ssf_provenance
from deep_sky_siril_artifacts import read_fits_header, read_fits_pixels
from deep_sky_siril_processing import script_commands, validate_delivery_export, measure_delivery_export

WCS = [("CTYPE1", "'RA---TAN'"), ("CTYPE2", "'DEC--TAN'"), ("CRPIX1", 6), ("CRPIX2", 4),
       ("CRVAL1", 10), ("CRVAL2", 20), ("CD1_1", -.001), ("CD1_2", 0), ("CD2_1", 0), ("CD2_2", .001)]


def export_case(tmp_path):
    source = tmp_path / "source.fit"
    values = np.tile(np.array([-.1, 0, .1, .2, .3, .4, .5, .6, .7, .8, 1, 1.1]), (8, 1))
    write_fits(source, values, extra=WCS)
    folder = tmp_path / "artifacts"
    folder.mkdir()
    science, jpeg = folder / "120-final.fit", folder / "120-final.jpg"
    write_fits(science, values, extra=WCS)
    Image.new("RGB", (12, 8), (20, 30, 40)).save(jpeg)
    text = (f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\nicc_assign sRGB\n'
            f'save "{science}" -chksum\nclose\nload "{science}"\nstat main\n'
            f'savejpg "{jpeg.with_suffix("")}" 95\nclose\nload "{jpeg}"\nstat main\nclose\n')
    return source, science, jpeg, values, script_commands(text)


@pytest.mark.parametrize("issue", ["missing-save", "wrong-order", "transform", "wrong-primary", "wrong-source",
                                  "wrong-jpeg-source", "no-fits-reload", "no-jpeg-reload", "extra-save", "extra-jpeg"])
def test_delivery_contract_rejects_unpaired_or_changed_export(tmp_path, issue):
    source, science, jpeg, _, commands = export_case(tmp_path)
    if issue == "missing-save":
        commands = [r for r in commands if r[0] != "save"]
    elif issue == "wrong-order":
        commands[3], commands[4] = commands[4], commands[3]
    elif issue == "transform":
        commands.insert(4, ["mtf", "0", ".2", "1"])
    elif issue == "wrong-primary":
        science = jpeg
    elif issue == "wrong-source":
        commands[2][1] = str(science)
    elif issue == "wrong-jpeg-source":
        next(r for r in commands if r[0] == "load" and r[1] == str(science))[1] = str(source)
    elif issue == "no-fits-reload":
        commands.remove(next(r for r in commands if r[0] == "load" and r[1] == str(science)))
    elif issue == "no-jpeg-reload":
        commands = commands[:10]
    elif issue == "extra-save":
        commands.insert(4, ["save", str(jpeg.with_suffix(".fits")), "-chksum"])
    else:
        commands.insert(9, ["savejpg", str(jpeg.with_name("extra.jpg")), "95"])
    with pytest.raises(core.ContractError):
        validate_delivery_export(tmp_path, source, science, commands)


@pytest.mark.parametrize("issue", ["nonfinite", "precision", "endpoints", "range", "wcs", "geometry", "bitpix"])
def test_scientific_export_rejects_data_loss(tmp_path, issue):
    source, science, jpeg, values, commands = export_case(tmp_path)
    binding = validate_delivery_export(tmp_path, source, science, commands)
    if issue == "nonfinite":
        values[0, 3] = np.nan
    elif issue == "precision":
        values[0, 3] += .001
    elif issue == "endpoints":
        values[0, 1] = np.nextafter(np.float32(0), np.float32(1))
    elif issue == "range":
        values = np.clip(values, 0, 1)
    elif issue == "geometry":
        Image.new("RGB", (13, 8)).save(jpeg)
    elif issue == "wcs":
        write_fits(science, values, extra=[(k, v if k != "CRVAL1" else 11) for k, v in WCS])
    if issue not in ("wcs", "geometry"):
        write_fits(science, values, extra=WCS)
    if issue == "bitpix":
        science.write_bytes(science.read_bytes().replace(b"BITPIX  =                  -32", b"BITPIX  =                   32"))
    with pytest.raises(core.ContractError):
        measure_delivery_export(tmp_path, source, binding)


def test_scientific_export_preserves_pixels_and_all_roles(tmp_path):
    source, science, jpeg, values, commands = export_case(tmp_path)
    binding = validate_delivery_export(tmp_path, source, science, commands)
    result = measure_delivery_export(tmp_path, source, binding)
    assert result["status"] == "passed" and result["bitpix"] == -32
    assert result["pixel_identity"]["range_preserved"] is True
    assert result["pixel_identity"]["existing_wcs_preserved"] is True
    assert result["artifacts"]["fits"]["sha256"] == core.sha256_file(science)
    raw, _, _ = read_fits_pixels(science)
    assert raw.min() < 0 and raw.max() > 1


@pytest.mark.parametrize("keep,status", [(False, "success"), (True, "success"), (False, "partial_success")])
def test_three_formal_files_survive_cleanup_and_replay(tmp_path, monkeypatch, keep, status):
    session, source, _ = _session(tmp_path, monkeypatch)
    run, jpeg = _delivery_run(session, source, monkeypatch)
    assert run["status"] == "success"
    science = session / run["delivery_export"]["fits"]
    sha = core.sha256_file(science)
    review = _review(session, run["id"], jpeg)
    selection = _selection(session, run["id"], jpeg, review, status=status,
                           limitations=[{"code": "test_limit", "message": "Controlled limitation"}] if status == "partial_success" else None)
    result = core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=keep)
    assert result["final_fits"]["path"] == "outputs/final.fit"
    assert result["final_fits"]["sha256"] == sha
    assert set(p.name for p in (session / "outputs").iterdir()) == {"reference.jpg", "final.jpg", "final.fit"}
    assert read_fits_header(session / "outputs/final.fit")["BITPIX"] == -32
    assert science.exists() is keep
    frozen = {p: p.read_bytes() for p in session.rglob("*") if p.is_file()}
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=keep) == result
    assert all(p.read_bytes() == data for p, data in frozen.items())


@pytest.mark.parametrize("missing", ["fits", "jpeg", "parent"])
def test_review_requires_all_three_materials(tmp_path, monkeypatch, missing):
    session, source, _ = _session(tmp_path, monkeypatch)
    run, jpeg = _delivery_run(session, source, monkeypatch)
    review = _review(session, run["id"], jpeg)
    document = core.load_json(review)
    role = {"fits": run["delivery_export"]["fits"], "jpeg": run["delivery_export"]["jpeg"],
            "parent": "previews/010-input-direct.jpg"}[missing]
    document["inspected_materials"] = [r for r in document["inspected_materials"] if r["path"] != role]
    core.atomic_write_json(review, document)
    selection = _selection(session, run["id"], jpeg, review)
    with pytest.raises(core.ContractError):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)
    assert not (session / "outputs/final.fit").exists()


@pytest.mark.parametrize("target", ["candidate-fits", "candidate-jpeg", "formal-fits", "formal-jpeg", "formal-reference", "role"])
def test_delivery_and_committed_drift_are_rejected(tmp_path, monkeypatch, target):
    session, source, _ = _session(tmp_path, monkeypatch)
    run, jpeg = _delivery_run(session, source, monkeypatch)
    review = _review(session, run["id"], jpeg)
    selection = _selection(session, run["id"], jpeg, review)
    if target.startswith("formal"):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)
        path = session / {"formal-fits": "outputs/final.fit", "formal-jpeg": "outputs/final.jpg",
                          "formal-reference": "outputs/reference.jpg"}[target]
        path.write_bytes(path.read_bytes()+b"drift")
    elif target == "role":
        receipt = session / f'runs/{run["id"]}.json'
        run["delivery_export"]["fits"] = run["delivery_export"]["jpeg"]
        core.atomic_write_json(receipt, run)
        manifest = core.load_json(session / "manifest.json")
        next(r for r in manifest["runs"] if r["id"] == run["id"])["receipt_sha256"] = core.sha256_file(receipt)
        core.atomic_write_json(session / "manifest.json", manifest)
    else:
        path = session / run["delivery_export"]["fits"] if target.endswith("fits") else jpeg
        path.write_bytes(path.read_bytes()+b"drift")
    with pytest.raises(core.ContractError):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=True)


@pytest.mark.parametrize("status", ["review_required", "failed"])
def test_pending_and_failed_publish_no_formal_fits(tmp_path, monkeypatch, status):
    from .test_deep_sky_siril import _failed_selection
    session, source, _ = _session(tmp_path, monkeypatch)
    if status == "failed":
        selection = _failed_selection(session)
    else:
        run, jpeg = _delivery_run(session, source, monkeypatch)
        review = _review(session, run["id"], jpeg, verdict="uncertain")
        selection = _selection(session, run["id"], jpeg, review, status=status)
    result = core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)
    assert result["status"] == status and not list((session / "outputs").iterdir())


def test_interrupted_copy_can_retry_without_overwrite(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch)
    run, jpeg = _delivery_run(session, source, monkeypatch)
    review = _review(session, run["id"], jpeg)
    selection = _selection(session, run["id"], jpeg, review)
    copy_file = core._ensure_copy
    def fail_jpeg(source, destination, **kwargs):
        if destination.name == "final.jpg":
            raise OSError("Controlled copy interruption")
        return copy_file(source, destination, **kwargs)
    monkeypatch.setattr(core, "_ensure_copy", fail_jpeg)
    with pytest.raises(OSError):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)
    assert (session / "outputs/final.fit").exists()
    assert core.load_json(session / "manifest.json")["finalization"] is None
    assert jpeg.exists()
    monkeypatch.setattr(core, "_ensure_copy", copy_file)
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)["status"] == "success"


def test_existing_conflicting_fits_is_never_overwritten(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch)
    run, jpeg = _delivery_run(session, source, monkeypatch)
    review = _review(session, run["id"], jpeg)
    selection = _selection(session, run["id"], jpeg, review)
    path = session / "outputs/final.fit"
    path.write_bytes(b"Existing conflict")
    with pytest.raises(core.ContractError):
        core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)
    assert path.read_bytes() == b"Existing conflict"


@pytest.mark.parametrize("kind,channels", [("fits32", 1), ("fits32", 3), ("fits16", 1), ("fits16", 3),
                                           ("tiff", 1), ("tiff", 3), ("tiff32", 1), ("tiff32", 3), ("xisf", 1), ("xisf", 3)])
def test_real_siril_paired_export(tmp_path, kind, channels):
    binary = shutil.which("siril-cli") or "/Applications/Siril.app/Contents/MacOS/siril-cli"
    if not Path(binary).is_file():
        pytest.skip("Real Siril unavailable")
    from .test_container_validation import _xisf_file
    from ._fits_bytes import _fits_hdu
    from deep_sky_siril_contract import sha256_file
    values = np.linspace(.1, .8, 48*32).reshape(32, 48)
    if kind == "fits32":
        values[0, :4] = [-.01, 0, 1, 1.01]
    planes = values if channels == 1 else np.stack([values, values*.8, values*.9])
    source = tmp_path / ("source." + {"fits32": "fit", "fits16": "fit", "tiff": "tif", "tiff32": "tif", "xisf": "xisf"}[kind])
    if kind == "fits32":
        write_fits(source, planes, extra=WCS)
    elif kind == "fits16":
        unsigned = np.round(planes*65535).astype(np.uint16)
        data = (unsigned.astype(np.int32)-32768).astype(">i2").tobytes()
        cards = [("SIMPLE", True), ("BITPIX", 16), ("NAXIS", 2 if channels == 1 else 3),
                 ("NAXIS1", 48), ("NAXIS2", 32)]
        if channels == 3:
            cards.append(("NAXIS3", 3))
        source.write_bytes(_fits_hdu(cards+[("BZERO", 32768), ("BSCALE", 1)], data))
    elif kind == "tiff32":
        tifffile = pytest.importorskip("tifffile")
        image = np.moveaxis(planes, 0, 2) if channels == 3 else planes
        tifffile.imwrite(source, np.asarray(image, dtype=np.float32), photometric="rgb" if channels == 3 else "minisblack")
    elif kind == "tiff":
        image = (np.moveaxis(planes, 0, 2) if channels == 3 else planes)
        Image.fromarray(np.uint8(image*255)).save(source)
    else:
        source.write_bytes(_xisf_file(np.asarray(planes, dtype="<f4").tobytes(),
            geometry=f"48:32:{channels}", sample_format="Float32", color_space="Gray" if channels == 1 else "RGB"))
    before = sha256_file(source)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    science, jpeg = artifacts / "120-final.fit", artifacts / "120-final.jpg"
    script = tmp_path / "export.ssf"
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nfmul 1\nicc_assign sRGB\n'
                      f'save "{science}" -chksum\nclose\nload "{science}"\nstat main\n'
                      f'savejpg "{jpeg.with_suffix("")}" 95\nclose\nload "{jpeg}"\nstat main\nclose\n')
    config = tmp_path / "s.ini"
    config.write_text("[core]\nforce_16bit=false\ncheck_updates=false\n")
    result = subprocess.run([binary, f"--initfile={config}", f"--directory={tmp_path}", "--offline", f"--script={script}"],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout+result.stderr
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "export.log"
    log.write_text(result.stdout+result.stderr)
    binding = validate_delivery_export(tmp_path, source, science, script_commands(script.read_text()), native_log=str(log))
    measured = measure_delivery_export(tmp_path, source, binding)
    assert measured["status"] == "passed" and measured["bitpix"] == -32
    assert measured["geometry"] == {"width": 48, "height": 32, "channels": channels}
    assert sha256_file(source) == before
    if kind == "fits32":
        raw, scale, zero = read_fits_pixels(science)
        actual = np.asarray(raw)*scale+zero
        assert actual.min() < 0 and actual.max() > 1


def test_genuine_historical_two_jpeg_receipt_keeps_immutable_finalize(tmp_path, monkeypatch):
    import deep_sky_siril_session as state
    session, source, _ = _session(tmp_path, monkeypatch)
    run, jpeg = _delivery_run(session, source, monkeypatch)
    script = session / run["script"]["path"]
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\nicc_assign sRGB\nstat main\n'
                      f'savejpg "{jpeg.with_suffix("")}" 95\nclose\nload "{jpeg}"\nstat main\nclose\n')
    provenance = _write_ssf_provenance(session, script, "delivery.render")
    historical_sha, historical_size = "fb1c015265e6dc458f67195ad8735f3e137ad5109dab833a1c4865b84b76f8ef", 2472
    document = core.load_json(provenance)
    document["references"] = [{"path": "references/protocols/delivery-render.md",
                               "sha256": historical_sha, "role": "primary"}]
    core.atomic_write_json(provenance, document)
    run["script"].update(core.fingerprint(script, include_path=False))
    run["script_provenance"].update(core.fingerprint(provenance, include_path=False))
    run["commands"] = list(dict.fromkeys(r[0] for r in script_commands(script.read_text())))
    mapping = {r["command"].lower(): r for r in run["command_knowledge"]["commands"]}
    run["command_knowledge"]["commands"] = [mapping[name] for name in run["commands"]]
    run["outputs"] = [r for r in run["outputs"] if r["path"] == run["delivery_export"]["jpeg"]]
    run["output_validations"] = {k: v for k, v in run["output_validations"].items() if k.endswith(".jpg")}
    science = session / run["delivery_export"]["fits"]
    run.pop("delivery_export")
    run["processing_checks"].pop("delivery_export")
    run["primary_output"] = None
    run["knowledge_validation"]["references"] = [{"path": "references/protocols/delivery-render.md",
                                                   "sha256": historical_sha, "size": historical_size, "role": "primary"}]
    receipt = session / f'runs/{run["id"]}.json'
    core.atomic_write_json(receipt, run)
    manifest = core.load_json(session / "manifest.json")
    next(r for r in manifest["runs"] if r["id"] == run["id"])["receipt_sha256"] = core.sha256_file(receipt)
    core.atomic_write_json(session / "manifest.json", manifest)
    payload = core.load_json(session / "session.json")
    payload["execution_policy"]["star_diagnostics"] = "subject_roi_residual_v1"
    payload["execution_policy"]["guidance"] = "evidence_bound_guidance_v1"
    payload["execution_policy"]["starnet"] = "pinned_python_bridge_v1"
    payload["execution_policy"]["background"] = "heldout_material_gate_v3"
    payload["execution_policy"].pop("delivery")
    payload["execution_policy"].pop("recomposition_review")
    payload["execution_policy"].pop("filter_metadata")
    payload["execution_policy"].pop("background_sampling")
    for key in ('recomposition_contour','artifact_review','stretch_execution','stretch_hdr','stage7_references','stretch_optimization','subject_mask','stretch_routing','stretch_trial_artifacts','stretch_constraint_preflight','stretch_morphology','stretch_tail_comparison','recovery_summary','evidence_admission','color_curve_variants','local_contrast_mask','color_region_goals','color_preservation','color_evaluation','linear_denoise_backend','linear_denoise','recomposition_geometry','recomposition_parameters','local_contrast_hdr','tone_adjustment','subject_enhancement','stretch_noise'):
        payload['execution_policy'].pop(key,None)
    payload['execution_policy'].pop('chroma_denoise', None)
    payload['execution_policy'].pop('restoration', None)
    payload['execution_policy'].pop('color_curve', None)
    payload['execution_policy'].pop('dualband', None)
    payload['execution_policy'].pop('stretch_strategy', None)
    payload['execution_policy'].pop('starless_stretch', None)
    payload['execution_policy'].pop('stretch_parameters', None)
    payload['execution_policy']['stretch'] = 'masked_dual_v1'
    payload['execution_policy'].pop('local_contrast', None)
    payload['execution_policy']['neutral'] = 'heldout_benefit_safety_v1'
    payload.pop("session_sha256")
    payload["session_sha256"] = core.stable_hash(payload)
    core.atomic_write_json(session / "session.json", payload)
    science.unlink()
    assert state.known_execution_policy(payload["execution_policy"])
    frozen = {p: p.read_bytes() for p in session.rglob("*") if p.is_file()}
    monkeypatch.setattr(core.subprocess, "run", lambda *a, **k: pytest.fail("Legacy replay executed Siril"))
    assert core.run_script(str(session), protocol="delivery.render", script_value=str(script),
                           source_value=str(source), expected_values=[str(jpeg)], timeout=30)["replayed"]
    assert all(p.read_bytes() == data for p, data in frozen.items())
    review = _review(session, run["id"], jpeg)
    selection = _selection(session, run["id"], jpeg, review)
    result = core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False)
    assert "final_fits" not in result and not (session / "outputs/final.fit").exists()
    assert core.finalize_session(str(session), selection_value=str(selection), keep_intermediates=False) == result
    assert not (session / "outputs/final.fit").exists()
