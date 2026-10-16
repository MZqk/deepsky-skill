"""Native signed wavelets, frozen confidence, scale mapping and delivery checks."""

import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent))
import moon_stack as moon

native = pytest.mark.skipif(not Path(moon.DEFAULT_SIRIL).exists(), reason="Siril CLI unavailable")


@pytest.fixture
def lunar():
    y, x = np.mgrid[:384, :384]
    disc = (x - 192)**2 + (y - 192)**2 < 170**2
    patch = cv2.GaussianBlur(((x > 100) & (x < 220) & (y > 130) & (y < 260)).astype(np.float32), (0, 0), 5)
    data = .43 + np.random.default_rng(4).normal(0, .001, x.shape)
    data += patch * (.04 * np.sin(x * .55) + .025 * np.cos(y * .7))
    data[(x - 280)**2 + (y - 140)**2 < 17**2] = .9
    return np.where(disc, data, 0).astype(np.float32)


def adapt(work, baseline, gains=(1.2, 1.2, 1.2, 1.1, 1), scale=1, post=None):
    fits.writeto(work / "moon_unsharpened.fit", baseline, overwrite=True)
    if post is not None:
        fits.writeto(work / "moon_base.fit", post, overwrite=True)
    args = moon.build_parser().parse_args(["postprocess", "--work", str(work)])
    return moon._run_wavelet_adaptation(args, work, baseline, baseline if post is None else post,
                                      "moon", gains, scale, post is not None)


@pytest.mark.parametrize("scale,planes", [(1, 5), (2, 6), (3, 7)])
def test_physical_scale_mapping(scale, planes):
    assert moon._wavelet_plane_count((3, 1024, 1024), scale) == planes
    gains = [1.2, 1.4, 1.6, 1.3, 1]
    schedule = moon._wavelet_gain_schedule(gains, planes, scale)
    for layer in schedule:
        index = layer["native_index"]
        expected = np.interp(index, np.arange(4), gains[:4]) if 0 <= index <= 3 else 1
        assert layer["requested_gain"] == pytest.approx(expected)
        if index < 0:
            assert layer["reason"] == "subnative_scale"
        elif index > 3:
            assert layer["reason"] == "unsupported_coarse_scale"
    if scale == 2:
        assert [entry["requested_gain"] for entry in schedule] == [1, *gains[:4]]


@pytest.mark.parametrize("size,count", [(4, 0), (8, 1), (16, 2), (64, 4), (128, 5)])
def test_small_image_plane_limit(size, count):
    assert moon._wavelet_plane_count((size, size), 1) == count


@pytest.mark.parametrize("gains", [[1]*6, [1, 1, 1, 1, 2], [float("nan"), 1, 1, 1, 1], [-1, 1, 1, 1, 1]])
def test_invalid_gain_contract(gains):
    with pytest.raises(ValueError, match="four finite"):
        moon._wavelet_gain_schedule(gains, 5)


def test_cli_defaults_and_independent_switches():
    for command in ("postprocess", "all"):
        argv = [command, "--work", "/tmp/moon"] + (["--input", "/tmp/source"] if command == "all" else [])
        args = moon.build_parser().parse_args(argv)
        assert args.wavelet_adapt == args.sharp_guard == "auto"
        args = moon.build_parser().parse_args(argv + ["--wavelet-adapt", "off", "--disk-budget-gb", "3"])
        assert args.sharp_guard == "auto" and args.disk_budget_gb == 3
        args = moon.build_parser().parse_args(argv + ["--sharp-guard", "off"])
        assert args.wavelet_adapt == "auto"


@native
@pytest.mark.parametrize("rgb", [False, True])
def test_native_signed_planes_match_bspline_and_identity(tmp_path, lunar, rgb, monkeypatch):
    base = np.stack([lunar * 1.03, lunar, lunar * .97]) if rgb else lunar
    fits.writeto(tmp_path / "source.fit", base)
    layers = tmp_path / "layers"
    layers.mkdir()
    gains = [1.2, 1.3, 1.1, 1.05, 1]
    receipt = moon.run_siril_script(moon.DEFAULT_SIRIL, [
        "load source", f'cd "{layers}"', "extract 5", f'cd "{tmp_path}"', "load source",
        "wavelet 5 2", "wrecons " + " ".join(map(str, gains)), "save expected",
        "load source", "wavelet 5 2", "wrecons " + " ".join(map(str, gains)) + " 10", "save old_six",
    ], tmp_path, tmp_path / "native.log", 120)
    assert receipt["exit_code"] == 0
    planes = [fits.getdata(layers / f"layer{index:02d}.fit") for index in range(5)]
    assert min(float(layer.min()) for layer in planes[:-1]) < 0
    np.testing.assert_allclose(sum(planes), base, atol=1e-6, rtol=0)
    identical, _, _ = moon._reconstruct_trusted_wavelets(base, base, layers, layers, 5, [1]*5)
    np.testing.assert_array_equal(identical, base)
    monkeypatch.setattr(moon, "_wavelet_confidence", lambda detail, ctx, index: (np.ones(detail.shape[-2:], np.float32), {}))
    reconstructed, _, info = moon._reconstruct_trusted_wavelets(base, base, layers, layers, 5, gains)
    np.testing.assert_allclose(reconstructed, fits.getdata(tmp_path / "expected.fit"), atol=1e-6, rtol=0)
    np.testing.assert_array_equal(fits.getdata(tmp_path / "expected.fit"), fits.getdata(tmp_path / "old_six.fit"))
    assert info["residual_gain"] == 1 and max(info["reconstruction_error"]) <= 1e-6


@native
@pytest.mark.parametrize("scale", [1, 2, 3])
def test_native_trust_and_sampling_response(tmp_path, lunar, scale):
    base = cv2.resize(lunar, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else lunar
    candidate, support, receipt = adapt(tmp_path, base, scale=scale)
    assert receipt["status"] == "applied" and support.sum() >= 32
    delta = candidate - base
    patch = delta[150*scale:235*scale, 120*scale:200*scale]
    flat = delta[235*scale:295*scale, 245*scale:295*scale]
    assert patch.std() > 4 * flat.std()
    assert patch.std() > .001
    assert not np.any(support[:8*scale])
    assert max(receipt["reconstruction_error"]) <= 1e-6
    assert not list(tmp_path.glob(".moon-wavelets-*"))
    if scale == 1:
        ctx = moon._sharp_guard_context(base, scale, support)
        _, guard = moon._select_guarded_sharpening(base, candidate, ctx)
        assert guard["status"] == "accepted" and guard["alpha"] == 1
        # Delivery uses exactly the frozen local gain ROI, even when brightness changes.
        quantized = np.rint(base * 65535).astype(np.float32) / 65535
        np.testing.assert_array_equal(moon._sharp_guard_context(quantized, scale, support)["structure"], support)
        damaged = candidate.copy()
        outside = ctx["flat"] & ~support
        damaged[outside] += np.random.default_rng(9).normal(0, .05, outside.sum())
        assert "noise_mad" in moon._sharp_guard_reasons(ctx, moon._sharp_guard_metrics(ctx, damaged))
        clipped = candidate.copy()
        clipped[outside] = 1.1
        assert "new_clipping" in moon._sharp_guard_reasons(ctx, moon._sharp_guard_metrics(ctx, clipped))
        for side, metric, sign in [("neg", "dhr", -1), ("pos", "positive_overshoot_ratio", 1)]:
            ringing = candidate.copy()
            ringing[ctx[side] > .3] += sign * .1
            assert metric in moon._sharp_guard_reasons(ctx, moon._sharp_guard_metrics(ctx, ringing))


@native
@pytest.mark.parametrize("noise", [0, .01, .03])
def test_noise_or_constant_cannot_pass_guard(tmp_path, noise):
    y, x = np.mgrid[:384, :384]
    base = np.where((x-192)**2+(y-192)**2 < 170**2,
                    .43 + np.random.default_rng(5).normal(0, noise, x.shape), 0).astype(np.float32)
    candidate, support, _ = adapt(tmp_path, base)
    _, guard = moon._select_guarded_sharpening(base, candidate, moon._sharp_guard_context(base, 1, support))
    assert guard["status"] == "fallback" and guard["alpha"] == 0


def test_local_noise_and_continuous_protection(lunar):
    ctx = moon._sharp_guard_context(lunar)
    ctx["flat"] = ctx["valid"].copy()
    detail = np.random.default_rng(1).normal(0, .002, lunar.shape).astype(np.float32)
    detail[:, 192:] *= 8
    weight, stats = moon._wavelet_confidence(detail, ctx, 0)
    assert stats["local_noise_range"][0] >= stats["noise_sigma"] - 1e-8
    assert stats["local_noise_range"][1] > 2 * stats["noise_sigma"]
    assert np.all((weight >= 0) & (weight <= 1))
    assert np.max(weight) < .5
    y, x = np.mgrid[:384, :384]
    wave = (.04 * np.sin(x * .55)).astype(np.float32)
    ctx["flat"] = np.zeros_like(ctx["valid"])
    ctx["flat"][240:280, 230:270] = True
    wave[ctx["flat"]] = 0
    weight, _ = moon._wavelet_confidence(wave, ctx, 0)
    assert weight[192, 192] > .99
    assert weight[192, 22] == 0
    assert np.max(np.abs(np.diff(weight[192, 22:45]))) < .3
    ctx["flat"][:] = False
    weight, stats = moon._wavelet_confidence(wave, ctx, 0)
    assert stats["reason"] == "insufficient_noise_samples" and not weight.any()


def test_local_gain_roi_does_not_require_global_texture_growth(lunar):
    global_ctx = moon._sharp_guard_context(lunar)
    region = np.zeros(lunar.shape, bool)
    region[160:220, 125:185] = True
    support = region & global_ctx["structure"]
    candidate = lunar.copy()
    candidate[region] += .02 * (lunar[region] - .43)
    assert "insufficient_detail_gain" in moon._sharp_guard_reasons(global_ctx, moon._sharp_guard_metrics(global_ctx, candidate))
    local_ctx = moon._sharp_guard_context(lunar, 1, support)
    _, receipt = moon._select_guarded_sharpening(lunar, candidate, local_ctx)
    assert receipt["status"] == "accepted" and receipt["alpha"] == 1


@native
def test_deconvolution_cannot_create_confidence(tmp_path, lunar):
    clean, support, reference = adapt(tmp_path, lunar)
    ctx = moon._sharp_guard_context(lunar)
    damaged = lunar.copy()
    damaged[ctx["neg"] > .3] -= .1
    candidate, damaged_support, info = adapt(tmp_path, lunar, post=damaged)
    np.testing.assert_array_equal(damaged_support, support)
    for before, after in zip(reference["layers"], info["layers"]):
        assert before["noise_sigma"] == after["noise_sigma"]
        assert before["effective_gain_range"] == after["effective_gain_range"]
    frozen = moon._sharp_guard_context(lunar, 1, support)
    _, guard = moon._select_guarded_sharpening(lunar, candidate, frozen)
    assert "dhr" in guard["attempts"][0]["reasons"] and guard["alpha"] < 1
    rgb = np.stack([lunar * 1.03, lunar, lunar * .97])
    rgb_candidate, rgb_support, _ = adapt(tmp_path, rgb)
    # Fixed luminance produces one scalar confidence map for all three channels.
    increments = rgb_candidate - rgb
    np.testing.assert_allclose(increments[0] / 1.03, increments[1], atol=2e-7, rtol=0)
    np.testing.assert_allclose(increments[2] / .97, increments[1], atol=2e-7, rtol=0)
    assert rgb_support.sum() >= 32 and np.max(np.abs(clean - lunar)) > .001


def test_small_image_bypass_and_identity_do_not_extract(tmp_path, monkeypatch):
    def unexpected_siril(*args, **kwargs):
        pytest.fail("a bypassed reconstruction called Siril")
    monkeypatch.setattr(moon, "run_siril_script", unexpected_siril)
    base = np.full((8, 8), .4, np.float32)
    candidate, support, info = adapt(tmp_path, base)
    np.testing.assert_array_equal(candidate, base)
    assert info["reason"] == "insufficient_image_size" and not support.any()
    candidate, _, info = adapt(tmp_path, base, gains=[1]*5)
    np.testing.assert_array_equal(candidate, base)
    assert info["status"] == "bypassed" and info["reason"] == "identity_gains"


def test_failures_cleanup_and_budget(tmp_path, lunar, monkeypatch):
    def extraction_failure(*args, **kwargs):
        return {"exit_code": 1, "log": "failure.log"}
    monkeypatch.setattr(moon, "run_siril_script", extraction_failure)
    with pytest.raises(SystemExit):
        adapt(tmp_path, lunar)
    assert not list(tmp_path.glob(".moon-wavelets-*"))
    monkeypatch.setattr(moon, "_disk_available", lambda *args: (100, 90, 10))
    with pytest.raises(SystemExit):
        adapt(tmp_path, lunar)
    assert not list(tmp_path.glob(".moon-wavelets-*"))


@pytest.mark.parametrize("problem", ["missing", "shape", "nonfinite"])
def test_invalid_planes_are_errors(tmp_path, lunar, problem):
    plane = lunar if problem == "missing" else np.zeros((8, 8), np.float32) if problem == "shape" else lunar * np.nan
    if problem != "missing":
        fits.writeto(tmp_path / "layer00.fit", plane)
    with pytest.raises((OSError, ValueError)):
        moon._reconstruct_trusted_wavelets(lunar, lunar, tmp_path, tmp_path, 5, [1.2]*4+[1])


def test_support_hash_and_off_ignores_stale_file(tmp_path, lunar):
    support = moon._sharp_guard_context(lunar)["structure"]
    fits.writeto(tmp_path / "moon_wavelet_support.fit", support.astype(np.uint8))
    guard = {"detail_support_file": "moon_wavelet_support.fit",
             "detail_support_sha256": hashlib.sha256(support.astype(np.uint8).tobytes()).hexdigest()}
    np.testing.assert_array_equal(moon._load_wavelet_support(tmp_path, guard), support)
    assert moon._load_wavelet_support(tmp_path, {}) is None
    support[192, 192] = ~support[192, 192]
    fits.writeto(tmp_path / "moon_wavelet_support.fit", support.astype(np.uint8), overwrite=True)
    with pytest.raises(ValueError, match="hash mismatch"):
        moon._load_wavelet_support(tmp_path, guard)


@native
@pytest.mark.parametrize("guard_mode", ["auto", "off"])
def test_global_off_compatibility_and_green_luminance(tmp_path, lunar, guard_mode):
    fits.writeto(tmp_path / "moon_master.fit", np.stack([lunar * 1.03, lunar, lunar * .97]))
    base_options = ["postprocess", "--work", str(tmp_path), "--no-adc", "--no-html-report", "--no-auto-margin",
                    "--luminance-channel", "green", "--deconv", "none", "--sharp-guard", guard_mode, "--anti-ringing", "off"]
    moon.cmd_postprocess(moon.build_parser().parse_args(base_options + ["--wavelet-l1", "1.2"]))
    assert (tmp_path / "moon_wavelet_support.fit").exists()
    receipt = json.loads((tmp_path / "postprocess_receipt.json").read_text())
    assert receipt["wavelet_adaptation"]["requested_coefficients"][0] == 1.2
    assert receipt["sharp_guard"]["mode"] == guard_mode
    moon.cmd_verify(moon.build_parser().parse_args(["verify", "--work", str(tmp_path), "--no-html-report"]))
    verified = json.loads((tmp_path / "verify_report.json").read_text())
    assert verified["sharp_guard"]["delivery"]["status"] in ("passed", "off", "unavailable")
    assert verified["wavelet_adaptation"]["support_pixels"] == receipt["wavelet_adaptation"]["support_pixels"]
    moon.cmd_postprocess(moon.build_parser().parse_args(base_options + ["--wavelet-adapt", "off", "--wavelet-l1", "1.2"]))
    receipt = json.loads((tmp_path / "postprocess_receipt.json").read_text())
    assert receipt["wavelet_adaptation"]["status"] == "off"
    assert "detail_support_file" not in receipt["sharp_guard"]
    assert not (tmp_path / "moon_wavelet_support.fit").exists()
    assert "wavelet 5 2" in (tmp_path / "logs/03_postprocess.ssf").read_text()
    moon.cmd_verify(moon.build_parser().parse_args(["verify", "--work", str(tmp_path), "--no-html-report"]))
    assert json.loads((tmp_path / "verify_report.json").read_text())["wavelet_adaptation"]["status"] == "off"


def test_hud_adaptation_status(tmp_path):
    cv2.imwrite(str(tmp_path / "moon_natural.jpg"), np.zeros((10, 10), np.uint8))
    (tmp_path / "postprocess_receipt.json").write_text(json.dumps({
        "wavelet_adaptation": {"status": "applied", "support_coverage": .25}}))
    html = moon._generate_comparison_html(tmp_path).read_text()
    assert '"status": "applied", "support_coverage": 0.25' in html
    assert 'id="hud-wavelet-adapt"' in html
