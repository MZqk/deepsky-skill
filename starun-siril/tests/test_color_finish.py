"""Native color finishing, paired reviews and nonfatal PCC fit warnings."""
from pathlib import Path
import subprocess
import json

import numpy as np
import pytest
from PIL import Image

from .test_deep_sky_siril import (
    _session, _input_inspect_run, _review, _write_ssf_provenance, _protection_regions,
    _write_fits, _write_jpeg, _write_delivery_outputs, _delivery_script, core,
)
from .test_tone_adjust import _tone_text
from .test_native_processing import native, metrics, write_fits, read_fits_pixels


@pytest.mark.parametrize("operation", ["satu 0.05 1 6", "rmgreen 3 0.10"])
def test_finish_requires_accepted_parent_and_paired_review(tmp_path, monkeypatch, operation):
    session, source, _ = _session(tmp_path, monkeypatch, color_ready=True)
    script = session / "scripts/100-finish.ssf"
    script.write_text(_tone_text(session, source, operation, "100-finish"))
    _write_ssf_provenance(session, script, "color.finish")
    outputs = [session / "artifacts/100-finish.fit", session / "previews/100-finish-parent.jpg",
               session / "previews/100-finish-candidate.jpg"]
    args = dict(protocol="color.finish", script_value=str(script), source_value=str(source),
                expected_values=list(map(str, outputs)), timeout=30)
    with pytest.raises(core.ContractError, match="verified input.inspect"):
        core.run_script(str(session), validate_only=True, **args)
    inspection, direct, diagnostic = _input_inspect_run(session, source, monkeypatch)
    _review(session, inspection["id"], diagnostic, parent_preview=direct, verdict="reject")
    with pytest.raises(core.ContractError, match="Accepted visual review"):
        core.run_script(str(session), validate_only=True, **args)
    _review(session, inspection["id"], diagnostic, parent_preview=direct)
    static = core.run_script(str(session), validate_only=True, **args)
    assert static["color_finish"] == [{"command": operation.split()[0], "parameters": operation.split()[1:]}]
    with pytest.raises(core.ContractError, match="undeclared outputs"):
        core.run_script(str(session), validate_only=True, **{**args, "expected_values": [str(outputs[0])]})

    def fake_run(command, **kwargs):
        _write_fits(outputs[0], color_ready=True, nonzero=True)
        for path in outputs[1:]:
            _write_jpeg(path)
        stat = "{} layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        return subprocess.CompletedProcess(command, 0, "Reading FITS: file finish.fit, 3 layer(s), 12x8 pixels, 16 bits\n" +
                                           "".join(stat.format(c) for c in ("Red", "Green", "Blue")))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(str(session), **args)
    assert receipt["status"] == "success" and receipt["color_finish"] == static["color_finish"]
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
    materials = {record['path'] for record in core.review_materials_for_run(str(session), delivery['id'])['inspected_materials']}
    assert outputs[2].relative_to(session).as_posix() in materials
    assert outputs[1].relative_to(session).as_posix() not in materials
    review = _review(session, delivery["id"], final, parent_preview=outputs[1])
    with pytest.raises(core.ContractError, match="verified parent preview"):
        core._validate_review(session, str(review), delivery["id"])
    review = _review(session, delivery["id"], final, parent_preview=outputs[2])
    core._validate_review(session, str(review), delivery["id"])


@pytest.mark.parametrize("replacement", [
    "", "satu 0 1 6", "satu nan 1 6", "satu 0.21 1 6", "satu 0.05 2 3",
    "rmgreen 3 0.21", "rmgreen 0 0.05", "satu 0.05 1 6\nrmgreen 3 0.05",
    'load "/abs/another.fit"\nsatu 0.05 1 6',
])
def test_finish_rejects_invalid_operations(tmp_path, replacement):
    source = tmp_path / "source.fit"
    write_fits(source, np.full((3, 32, 32), .1))
    text = _tone_text(tmp_path, source, replacement, "100-finish")
    with pytest.raises(core.ContractError):
        native.validate_adjustment(tmp_path, source, tmp_path / "artifacts/100-finish.fit",
                                   native.script_commands(text), "color.finish")


def test_finish_rejects_missing_preview_late_operation_and_mono(tmp_path):
    source = tmp_path / "source.fit"
    write_fits(source, np.full((3, 32, 32), .1))
    text = _tone_text(tmp_path, source, "satu 0.05 1 6", "100-finish")
    for changed in (text.replace('icc_assign sRGB\n', ''),
                    text.replace('-candidate', '-parent'),
                    text.replace(f'savejpg "{tmp_path}/previews/100-finish-parent" 95\n', ''),
                    text.replace(f'savejpg "{tmp_path}/previews/100-finish-candidate"',
                                 f'satu 0.05 1 6\nsavejpg "{tmp_path}/previews/100-finish-candidate"')):
        with pytest.raises(core.ContractError):
            native.validate_adjustment(tmp_path, source, tmp_path / "artifacts/100-finish.fit",
                                       native.script_commands(changed), "color.finish")
    write_fits(source, np.full((32, 32), .1))
    with pytest.raises(core.ContractError, match="three-channel"):
        native.validate_adjustment(tmp_path, source, tmp_path / "artifacts/100-finish.fit",
                                   native.script_commands(text), "color.finish")


@pytest.mark.parametrize("warning", [
    "The photometric color calibration seems to have found an imprecise solution, consider correcting the image gradient first",
    "The photometric color correction seems to have found an imprecise solution, consider correcting the image gradient first",
    "测光法色彩校准似乎不能精确校准，考虑先修正图像渐变",
])
def test_imprecise_pcc_warns_but_allows_reviewed_acceptance(tmp_path, monkeypatch, warning):
    session, source, _ = _session(tmp_path, monkeypatch, color_ready=True, input_state="linear")
    _, direct, diagnostic = _input_inspect_run(session, source, monkeypatch)
    script = session / "scripts/045-pcc.ssf"
    output, preview = session / "artifacts/045-pcc.fit", session / "previews/045-pcc.jpg"
    evidence = {"method": "pcc", "source_sha256": core.sha256_file(source),
                "protection_regions": _protection_regions(session, source),
                "equipment": {"pcc_compatibility": {"value": "standard_rgb", "source": "user",
                                                   "evidence": "User confirmed test RGB and IR blocking"}}}
    script.write_text('requires 1.4.4 1.5.0\nset32bits\n# color-calibration: ' + json.dumps(evidence) +
                      f'\nload "{source}"\npcc -catalog=gaia\n'
                      f'save "{output}" -chksum\nautostretch -linked -2.8 0.22\nsavejpg "{preview}" 95\nclose\n')
    _write_ssf_provenance(session, script, "color.calibrate")
    def fake_run(command, **kwargs):
        _write_fits(output, color_ready=True, nonzero=True)
        _write_jpeg(preview)
        stat = "{} layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        return subprocess.CompletedProcess(command, 0,
            "Found a solution for color calibration using 2215 stars. Factors:\n"
            "K0: 0.728 (deviation: 1.335)\nK1: 0.949 (deviation: 1.679)\nK2: 1.000 (deviation: 1.726)\n"
            + warning + "\nPhotometric color calibration successful.\n"
            + "Reading FITS: file pcc.fit, 3 layer(s), 12x8 pixels, 16 bits\n" +
            "".join(stat.format(c) for c in ("Red", "Green", "Blue")))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(str(session), protocol="color.calibrate", script_value=str(script),
                             source_value=str(source), expected_values=[str(output), str(preview)], timeout=30)
    assert receipt["status"] == "success" and receipt["log_diagnostics"]["status"] == "warning"
    assert [item["code"] for item in receipt["log_diagnostics"]["findings"]] == ["pcc_imprecise_solution"]
    review = _review(session, receipt["id"], preview, parent_preview=diagnostic)
    core._validate_review(session, str(review), receipt["id"])
    document = core.load_json(review)
    document["gates"]["color"] = "fail"
    core.atomic_write_json(review, document)
    with pytest.raises(core.ContractError, match="failed or uncertain gate"):
        core._validate_review(session, str(review), receipt["id"])


def test_pcc_requires_protection_regions_not_background_regions(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch, color_ready=True, input_state="linear")
    _input_inspect_run(session, source, monkeypatch)
    script = session / "scripts/045-pcc.ssf"
    output, preview = session / "artifacts/045-pcc.fit", session / "previews/045-pcc.jpg"
    evidence = {"method": "pcc", "source_sha256": core.sha256_file(source),
                "background_regions": _protection_regions(session, source),
                "equipment": {"pcc_compatibility": {"value": "standard_rgb", "source": "user",
                                                   "evidence": "User confirmed test RGB and IR blocking"}}}
    script.write_text('requires 1.4.4 1.5.0\nset32bits\n# color-calibration: ' + json.dumps(evidence) +
                      f'\nload "{source}"\npcc -catalog=gaia\n'
                      f'save "{output}" -chksum\nautostretch -linked -2.8 0.22\nsavejpg "{preview}" 95\nclose\n')
    _write_ssf_provenance(session, script, "color.calibrate")
    with pytest.raises(core.ContractError, match="protection_regions"):
        core.run_script(str(session), protocol="color.calibrate", script_value=str(script),
                        source_value=str(source), expected_values=[str(output), str(preview)], timeout=30)


def test_degenerate_pcc_fit_blocks_reviewed_acceptance(tmp_path, monkeypatch):
    session, source, _ = _session(tmp_path, monkeypatch, color_ready=True, input_state="linear")
    _, direct, diagnostic = _input_inspect_run(session, source, monkeypatch)
    script = session / "scripts/045-pcc.ssf"
    output, preview = session / "artifacts/045-pcc.fit", session / "previews/045-pcc.jpg"
    evidence = {"method": "pcc", "source_sha256": core.sha256_file(source),
                "protection_regions": _protection_regions(session, source),
                "equipment": {"pcc_compatibility": {"value": "standard_rgb", "source": "user",
                                                   "evidence": "User confirmed test RGB and IR blocking"}}}
    script.write_text('requires 1.4.4 1.5.0\nset32bits\n# color-calibration: ' + json.dumps(evidence) +
                      f'\nload "{source}"\npcc -catalog=gaia\n'
                      f'save "{output}" -chksum\nautostretch -linked -2.8 0.22\nsavejpg "{preview}" 95\nclose\n')
    _write_ssf_provenance(session, script, "color.calibrate")

    def fake_run(command, **kwargs):
        _write_fits(output, color_ready=True, nonzero=True)
        _write_jpeg(preview)
        stat = "{} layer: Mean: 100, Median: 90, Sigma: 5, Min: 1, Max: 255, bgnoise: 4, avgDev: 3, MAD: 2, sqrt(BWMV): 1\n"
        return subprocess.CompletedProcess(command, 0,
            "Image R/G = 1.473937 + -0.010339 * Catalog R/G (sigma: 0.927355)\n"
            "Image B/G = 0.830228 + -0.002359 * Catalog B/G (sigma: 0.520884)\n"
            "Found a solution for color calibration using 963 stars. Factors:\n"
            "K0: 0.562\nK1: 0.827\nK2: 1.000\n"
            "The photometric color calibration seems to have found an imprecise solution, consider correcting the image gradient first\n"
            "Photometric color calibration successful.\n"
            + "Reading FITS: file pcc.fit, 3 layer(s), 12x8 pixels, 16 bits\n" +
            "".join(stat.format(c) for c in ("Red", "Green", "Blue")))
    monkeypatch.setattr(core.subprocess, "run", fake_run)
    receipt = core.run_script(str(session), protocol="color.calibrate", script_value=str(script),
                              source_value=str(source), expected_values=[str(output), str(preview)], timeout=30)
    assert receipt["status"] == "success"
    quality = receipt["processing_checks"]["color_calibration"]["fit_quality"]
    assert quality["verdict"] == "degenerate"
    assert quality["white_balance"] == [0.562, 0.827, 1.0]
    assert receipt["processing_checks"]["color_calibration"]["warnings"] == ["pcc_imprecise_solution"]
    review = _review(session, receipt["id"], preview, parent_preview=diagnostic)
    with pytest.raises(core.ContractError, match="degenerate photometric fit"):
        core._validate_review(session, str(review), receipt["id"])
    document = core.load_json(review)
    document["verdict"] = "reject"
    core.atomic_write_json(review, document)
    core._validate_review(session, str(review), receipt["id"])
    review = _review(session, receipt["id"], preview, parent_preview=diagnostic, verdict="reject")
    core._validate_review(session, str(review), receipt["id"])


def test_real_siril_saturation_preserves_neutral_sky_and_geometry(tmp_path):
    binary = Path("/Applications/Siril.app/Contents/MacOS/siril-cli")
    if not binary.is_file():
        pytest.skip("Siril 1.4.4 unavailable")
    for directory in ("artifacts", "previews"):
        (tmp_path / directory).mkdir()
    values = np.full((3, 64, 64), .04)
    values[:, 16:32, 16:32] = np.array([.30, .22, .15])[:, None, None]
    values[:, 40:48, 40:48] = np.array([.70, .60, .30])[:, None, None]
    source = tmp_path / "source.fit"
    write_fits(source, values)
    original_hash = core.sha256_file(source)
    text = _tone_text(tmp_path, source, "satu 0.05 1 6", "100-finish")
    output = tmp_path / "artifacts/100-finish.fit"
    native.validate_adjustment(tmp_path, source, output, native.script_commands(text), "color.finish")
    script = tmp_path / "100-finish.ssf"
    script.write_text(text)
    config = tmp_path / "siril.ini"
    config.write_text('[core]\nforce_16bit=false\ncheck_updates=false\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}',
                             '--offline', f'--script={script}'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    raw, scale, zero = read_fits_pixels(output)
    adjusted = raw * scale + zero
    assert adjusted.shape == values.shape and np.isfinite(adjusted).all()
    assert np.allclose(adjusted[:, :8, :8], .04, atol=1e-6)
    assert np.ptp(adjusted[:, 20, 20]) > np.ptp(values[:, 20, 20])
    histogram = metrics.analyze_image_histogram(output)
    assert all(c['shadow_clip_rate'] == c['highlight_sat_rate'] == 0 for c in histogram['channels'])
    for suffix in ('parent', 'candidate'):
        with Image.open(tmp_path / f'previews/100-finish-{suffix}.jpg') as image:
            image.load()
            assert image.size == (64, 64)
    assert core.sha256_file(source) == original_hash


def test_native_srgb_assignment_preserves_pixels_and_fits_reopen(tmp_path):
    binary = Path('/Applications/Siril.app/Contents/MacOS/siril-cli')
    if not binary.is_file():
        pytest.skip('Siril 1.4.4 unavailable')
    source = tmp_path / 'untagged-nonlinear.fit'
    values = np.broadcast_to(np.array([.08, .12, .16])[:, None, None], (3, 32, 32)).copy()
    values[:, 20:24, 20:24] = np.array([.7, .5, .3])[:, None, None]
    write_fits(source, values)
    tagged = tmp_path / 'tagged.fit'
    config = tmp_path / 'siril.ini'
    config.write_text('[core]\nforce_16bit=false\nfits_save_icc=true\ncheck_updates=false\n[gui]\nicc_autoassignment=4\n')
    script = tmp_path / 'icc-reopen.ssf'
    script.write_text(f'requires 1.4.4 1.5.0\nset32bits\nload "{source}"\n'
                      f'icc_assign sRGBlinear\nsavejpg "{tmp_path}/linear-tagged" 95\nicc_assign sRGB\n'
                      f'save "{tagged}" -chksum\nsavejpg "{tmp_path}/assigned" 95\nclose\n'
                      f'load "{tagged}"\nsavejpg "{tmp_path}/reopened" 95\nclose\n')
    result = subprocess.run([str(binary), f'--initfile={config}', f'--directory={tmp_path}',
                             '--offline', f'--script={script}'], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    raw, scale, zero = read_fits_pixels(tagged)
    assert np.array_equal(raw * scale + zero, values.astype(np.float32))
    images = {}
    for name in ('linear-tagged', 'assigned', 'reopened'):
        with Image.open(tmp_path / f'{name}.jpg') as image:
            images[name] = np.asarray(image).copy()
    assert np.array_equal(images['assigned'], images['reopened'])
    expected = np.array([.08, .12, .16]) * 255
    assert np.allclose(np.median(images['assigned'][:8, :8], axis=(0, 1)), expected, atol=2)
    assert np.all(np.median(images['linear-tagged'][:8, :8], axis=(0, 1)) > expected * 1.8)


@pytest.mark.parametrize("command", ["icc_assign sRGBlinear", "icc_assign /abs/custom.icc", "icc_assign sRGB sRGB"])
def test_finish_rejects_ambiguous_display_profiles(command):
    with pytest.raises(core.ContractError, match="native sRGB"):
        native.validate_parameters(command.split(), "color.finish")
