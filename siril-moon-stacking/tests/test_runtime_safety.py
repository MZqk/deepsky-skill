from __future__ import annotations

import argparse
import builtins
import json
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import moon_stack as moon


def fake_siril(path: Path, output: str = "siril 1.4.4", code: int = 0) -> Path:
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\nexit {code}\n")
    path.chmod(0o755)
    return path


def stub_dependencies(monkeypatch, versions=None, installed=("opencv-python-headless",), no_packaging=False):
    values = {"numpy": "2.0.0", "astropy": "7.0.0", "cv2": "4.13.0", "skimage": "0.26.0"}
    values.update(versions or {})
    original_import, original_version = builtins.__import__, metadata.version

    def import_module(name, *args, **kwargs):
        if no_packaging and (name == "packaging" or name.startswith("packaging.")):
            raise ModuleNotFoundError("No module named 'packaging'")
        if name in values:
            value = values[name]
            if isinstance(value, Exception):
                raise value
            return SimpleNamespace(__version__=value)
        return original_import(name, *args, **kwargs)

    def distribution_version(name):
        if name in ("opencv-python", "opencv-python-headless"):
            if name in installed:
                return "4.13.0.92"
            raise metadata.PackageNotFoundError(name)
        return original_version(name)

    monkeypatch.setattr(builtins, "__import__", import_module)
    monkeypatch.setattr(metadata, "version", distribution_version)


def probe(capsys, siril):
    try:
        moon.cmd_probe(argparse.Namespace(siril=str(siril)))
    except SystemExit as exc:
        code = exc.code
    else:
        code = 0
    return code, json.loads(capsys.readouterr().out)


def test_probe_and_help_work_without_numpy(tmp_path):
    cli = fake_siril(tmp_path / "siril-cli")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    cmd = [sys.executable, "-S", str(SKILL / "scripts/moon_stack.py")]
    result = subprocess.run(cmd + ["--siril", str(cli), "probe"], capture_output=True, text=True, env=env)
    info = json.loads(result.stdout)
    assert result.returncode == 1 and info["ready"] is False
    assert info["numpy"] is None and any("numpy import failed" in e for e in info["errors"])
    assert subprocess.run(cmd + ["--help"], capture_output=True, text=True, env=env).returncode == 0
    work = tmp_path / "work"
    result = subprocess.run(cmd + ["all", "--input", str(tmp_path / "input"), "--work", str(work)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 1 and "numpy import failed" in result.stderr
    assert "Traceback" not in result.stderr and not work.exists()


@pytest.mark.parametrize("module,version", [
    ("numpy", "1.25.0"), ("astropy", "9.0.0"), ("cv2", "4.7.0"), ("skimage", "0.21.0"),
    ("numpy", "not-a-version"),
])
def test_probe_rejects_out_of_range_or_unverifiable_versions(tmp_path, monkeypatch, capsys, module, version):
    cli = fake_siril(tmp_path / "siril")
    stub_dependencies(monkeypatch, {module: version})
    code, info = probe(capsys, cli)
    assert code == 1 and info["ready"] is False
    assert any(module in error and ("does not satisfy" in error or "could not be verified" in error)
               for error in info["errors"])


@pytest.mark.parametrize("module,error", [
    ("astropy", ModuleNotFoundError("missing astropy")),
    ("cv2", OSError("broken native library")),
])
def test_probe_reports_import_failures(tmp_path, monkeypatch, capsys, module, error):
    cli = fake_siril(tmp_path / "siril")
    stub_dependencies(monkeypatch, {module: error})
    code, info = probe(capsys, cli)
    assert code == 1 and info[module] is None
    assert any(module in message and str(error) in message for message in info["errors"])


def test_probe_reports_unverified_version_comparison(tmp_path, monkeypatch, capsys):
    cli = fake_siril(tmp_path / "siril")
    stub_dependencies(monkeypatch, no_packaging=True)
    code, info = probe(capsys, cli)
    assert code == 1 and info["ready"] is False
    assert any("versions unverified" in message for message in info["errors"])


@pytest.mark.parametrize("extra", ["imageio>=2", "numpy<2"])
def test_probe_does_not_ignore_new_or_duplicate_requirements(tmp_path, monkeypatch, capsys, extra):
    cli = fake_siril(tmp_path / "siril", "siril 1.4.3")
    requirements = SKILL / "requirements.txt"
    original = Path.read_text
    content = requirements.read_text() + extra + "\n"

    def read_text(path, *args, **kwargs):
        return content if path == requirements else original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    stub_dependencies(monkeypatch)
    code, info = probe(capsys, cli)
    assert code == 1 and not info["ready"]
    assert any("unverified" in message for message in info["errors"])
    assert any("unsupported" in message for message in info["errors"])


@pytest.mark.parametrize("installed,ready,warning", [
    (("opencv-python-headless",), True, False),
    (("opencv-python",), True, True),
    ((), False, False),
])
def test_probe_opencv_distribution_choices(tmp_path, monkeypatch, capsys, installed, ready, warning):
    cli = fake_siril(tmp_path / "siril")
    stub_dependencies(monkeypatch, installed=installed)
    code, info = probe(capsys, cli)
    assert info["ready"] is ready and code == (0 if ready else 1)
    assert bool(info["warnings"]) is warning
    if not ready:
        assert any("OpenCV distribution unverified" in message for message in info["errors"])


@pytest.mark.parametrize("output,exit_code,expected", [
    ("siril 1.4.3", 0, "unsupported"),
    ("siril 1.4.4-rc1", 0, "unsupported"),
    ("not a version", 0, "could not be verified"),
    ("siril 1.4.4", 42, "exit code 42"),
])
def test_probe_rejects_siril_version_failures(tmp_path, monkeypatch, capsys, output, exit_code, expected):
    cli = fake_siril(tmp_path / "siril", output, exit_code)
    stub_dependencies(monkeypatch)
    code, info = probe(capsys, cli)
    assert code == 1 and info["siril_exists"] is True
    assert isinstance(info["siril_version"], list)
    assert any(expected in message for message in info["errors"])


def test_probe_handles_timeout_and_non_executable(tmp_path, monkeypatch, capsys):
    cli = fake_siril(tmp_path / "siril")
    stub_dependencies(monkeypatch)
    cli.chmod(0o644)
    code, info = probe(capsys, cli)
    assert code == 1 and info["siril_exists"] is True
    assert any("not executable" in message for message in info["errors"])
    cli.chmod(0o755)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 60)

    monkeypatch.setattr(moon.subprocess, "run", timeout)
    code, info = probe(capsys, cli)
    assert code == 1 and any("TimeoutExpired" in message for message in info["errors"])


def test_probe_and_processing_resolve_same_path_without_fallback(tmp_path, monkeypatch, capsys):
    cli = fake_siril(tmp_path / "siril-cli")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    stub_dependencies(monkeypatch)
    code, info = probe(capsys, "siril-cli")
    assert code == 0 and info["siril_resolved"] == str(cli.resolve())
    result = moon.run_siril_script("siril-cli", ["exit"], tmp_path, tmp_path / "logs/test.log")
    assert result["exit_code"] == 0
    explicit = fake_siril(tmp_path / "chosen-siril", "siril 1.4.5")
    code, info = probe(capsys, explicit)
    assert code == 0 and info["siril_resolved"] == str(explicit.resolve())
    assert info["siril_version"] == ["siril 1.4.5"]
    code, info = probe(capsys, tmp_path / "missing-siril")
    assert code == 1 and info["siril_exists"] is False and info["siril_resolved"] is None
    with pytest.raises(SystemExit):
        moon.run_siril_script(str(tmp_path / "missing-siril"), ["exit"], tmp_path, tmp_path / "other/test.log")
    assert not (tmp_path / "other").exists()


@pytest.fixture
def color_master(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    rng = np.random.default_rng(61)
    green = rng.random((256, 256), dtype=np.float32)
    data = np.stack([np.roll(green, 2, axis=1), green, np.roll(green, -2, axis=0)])
    master = work / "moon_master.fit"
    fits.writeto(master, data)
    return work, master


def post_args(work, *extra):
    return moon.build_parser().parse_args(["postprocess", "--work", str(work), *extra])


@pytest.mark.parametrize("requested,trusted,expected", [
    ("auto", True, ("sb", "trusted_optics")),
    ("auto", False, ("none", "missing_optical_metadata")),
    ("none", True, ("none", "user_disabled")),
    ("none", False, ("none", "user_disabled")),
    ("rl", False, ("rl", "user_selected")),
    ("sb", True, ("sb", "user_selected")),
    ("wiener", True, ("wiener", "user_selected")),
])
def test_deconvolution_decision_and_reason(requested, trusted, expected):
    assert moon._deconvolution_choice(requested, trusted) == expected


@pytest.mark.parametrize("method", ["sb", "wiener"])
def test_missing_optics_leaves_color_master_unchanged(color_master, monkeypatch, capsys, method):
    work, master = color_master
    before = master.read_bytes()
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work, "--deconv", method))
    assert "physical deconvolution needs trusted" in capsys.readouterr().err
    assert master.read_bytes() == before
    assert not (work / "adc_receipt.json").exists() and not list(work.glob(".moon-adc-*"))


def test_nonfinite_color_master_is_unchanged_before_adc(color_master, monkeypatch):
    work, master = color_master
    with fits.open(master, mode="update", memmap=False) as hdul:
        hdul[0].data[0, 0, 0] = np.nan
        hdul.flush()
    before = master.read_bytes()
    monkeypatch.setattr(moon, "align_rgb_channels", lambda *_: pytest.fail("ADC reached non-finite input"))
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work))
    assert master.read_bytes() == before and not list(work.glob(".moon-adc-*"))


def test_no_adc_does_not_stage_master(color_master, monkeypatch):
    work, master = color_master
    before = master.read_bytes()
    monkeypatch.setattr(moon, "align_rgb_channels", lambda *_: pytest.fail("ADC was disabled"))
    monkeypatch.setattr(moon, "_disk_available", lambda *_: pytest.fail("ADC should not reserve space"))
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work, "--no-adc", "--deconv", "sb"))
    assert master.read_bytes() == before and not list(work.glob(".moon-adc-*"))


@pytest.mark.parametrize("free_gb,cap", [(100, "0.000000001"), (5, None)])
def test_adc_respects_work_disk_budget(color_master, monkeypatch, capsys, free_gb, cap):
    work, master = color_master
    before = master.read_bytes()
    monkeypatch.setattr(moon.shutil, "disk_usage", lambda *_: SimpleNamespace(free=free_gb * 1024**3))
    options = ["--disk-budget-gb", cap] if cap else []
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work, *options))
    assert "insufficient ADC disk budget" in capsys.readouterr().err
    assert master.read_bytes() == before and not list(work.glob(".moon-adc-*"))


def test_adc_error_cleans_temporary_files(color_master, monkeypatch, capsys):
    work, master = color_master
    before = master.read_bytes()
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))

    def failing_adc(candidate):
        with fits.open(candidate, mode="update", memmap=False) as hdul:
            hdul[0].data[0, 0, 0] = 0
            hdul.flush()
        raise RuntimeError("ADC failure")

    monkeypatch.setattr(moon, "align_rgb_channels", failing_adc)
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work))
    assert "ADC failure" in capsys.readouterr().err
    assert master.read_bytes() == before and not list(work.glob(".moon-adc-*"))


def test_adc_copy_failure_keeps_master(color_master, monkeypatch, capsys):
    work, master = color_master
    before = master.read_bytes()
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))

    def fail_copy(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(moon.shutil, "copy2", fail_copy)
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work))
    assert "disk full" in capsys.readouterr().err
    assert master.read_bytes() == before and not list(work.glob(".moon-adc-*"))


@pytest.mark.parametrize("existing_receipt", [False, True])
def test_adc_commit_failure_restores_receipt_and_master(color_master, monkeypatch, capsys, existing_receipt):
    work, master = color_master
    before = master.read_bytes()
    receipt = work / "adc_receipt.json"
    if existing_receipt:
        receipt.write_text('{"previous": true}\n')
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))
    replace = moon.os.replace

    def failing_master_commit(src, dst):
        if Path(dst) == master.resolve():
            raise OSError("cannot replace master")
        return replace(src, dst)

    monkeypatch.setattr(moon.os, "replace", failing_master_commit)
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work))
    assert "ADC commit failed" in capsys.readouterr().err
    assert master.read_bytes() == before and not list(work.glob(".moon-adc-*"))
    if existing_receipt:
        assert receipt.read_text() == '{"previous": true}\n'
    else:
        assert not receipt.exists()


def test_adc_preserves_old_receipt_when_rollback_fails(color_master, monkeypatch, capsys):
    work, master = color_master
    before = master.read_bytes()
    receipt = work / "adc_receipt.json"
    receipt.write_text('{"previous": true}\n')
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))
    replace = moon.os.replace

    def failing_commit_and_rollback(src, dst):
        if Path(dst) == master.resolve() or Path(src).name.startswith(".moon-adc-previous-"):
            raise OSError("cannot replace file")
        return replace(src, dst)

    monkeypatch.setattr(moon.os, "replace", failing_commit_and_rollback)
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work))
    assert "receipt recovery" in capsys.readouterr().err
    assert master.read_bytes() == before
    backups = list(work.glob(".moon-adc-previous-*"))
    assert len(backups) == 1 and backups[0].read_text() == '{"previous": true}\n'
    assert not list(work.glob(".moon-adc-receipt-*"))


def test_external_master_and_symlink_are_rejected(color_master, tmp_path, capsys):
    work, master = color_master
    outside = tmp_path / "original.fit"
    outside.write_bytes(master.read_bytes())
    before = outside.read_bytes()
    (work / "linked.fit").symlink_to(outside)
    for name in (str(outside), "linked.fit"):
        with pytest.raises(SystemExit):
            moon.cmd_postprocess(post_args(work, "--master", name))
        assert "copy the file into --work first" in capsys.readouterr().err
        assert outside.read_bytes() == before
    assert not list(work.glob(".moon-adc-*"))


def test_adc_receipt_path_cannot_be_the_master(color_master, capsys):
    work, master = color_master
    receipt = work / "adc_receipt.json"
    master.rename(receipt)
    before = receipt.read_bytes()
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(post_args(work, "--master", receipt.name))
    assert "cannot use the ADC receipt path" in capsys.readouterr().err
    assert receipt.read_bytes() == before


@pytest.mark.parametrize("symlink_inside", [False, True])
def test_adc_commits_after_validation(color_master, monkeypatch, symlink_inside):
    work, master = color_master
    if symlink_inside:
        target = work / "actual.fit"
        master.rename(target)
        master.symlink_to(target.name)
    before = master.read_bytes()
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))

    class ReachedSiril(Exception):
        pass

    def stop_at_siril(*args, **kwargs):
        raise ReachedSiril

    monkeypatch.setattr(moon, "run_siril_script", stop_at_siril)
    with pytest.raises(ReachedSiril):
        moon.cmd_postprocess(post_args(work, "--deconv", "auto", "--wavelet-adapt", "off"))
    assert master.read_bytes() != before
    assert json.loads((work / "adc_receipt.json").read_text())["applied"] is True
    assert master.is_symlink() is symlink_inside
    assert not list(work.glob(".moon-adc-*"))


@pytest.mark.skipif(not Path(moon.DEFAULT_SIRIL).exists(), reason="Siril CLI unavailable")
def test_native_postprocess_after_adc(color_master, monkeypatch):
    work, master = color_master
    before = master.read_bytes()
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (10**11, 0, 10**11))
    moon.cmd_postprocess(post_args(work, "--deconv", "none", "--wavelet-adapt", "off",
                                  "--no-html-report", "--no-auto-margin"))
    assert master.read_bytes() != before
    assert json.loads((work / "adc_receipt.json").read_text())["applied"] is True
    receipt = json.loads((work / "postprocess_receipt.json").read_text())
    assert receipt["deconvolution"] == {"requested": "none", "applied": "none", "reason": "user_disabled"}
    assert (work / "moon_natural.tif").is_file() and not list(work.glob(".moon-adc-*"))
