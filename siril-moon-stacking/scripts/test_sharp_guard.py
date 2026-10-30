"""Full-chain sharpening safety and delivered-product regression checks."""

import json
from pathlib import Path
import sys

import cv2
import numpy as np
import pytest
from astropy.io import fits

sys.path.insert(0, str(Path(__file__).resolve().parent))
import moon_stack as moon


@pytest.fixture
def terrain():
    y, x = np.mgrid[:192, :224]
    mask = (x - 112) ** 2 + (y - 96) ** 2 < 78 ** 2
    rng = np.random.default_rng(7)
    texture = cv2.GaussianBlur(rng.normal(0, .02, x.shape).astype(np.float32), (0, 0), 1.2)
    return np.where(mask, .48 + .055 * np.sin(x * .28) * np.cos(y * .23) + texture, 0).astype(np.float32)


def contrast(base, gain):
    return np.where(base > 0, .48 + gain * (base - .48), 0).astype(np.float32)


@pytest.mark.parametrize("gain,status,alpha", [(1, "fallback", 0), (1.02, "accepted", 1),
                                              (1.32, "reduced", .25), (1.8, "fallback", 0)])
def test_highest_safe_gain(terrain, gain, status, alpha):
    ctx = moon._sharp_guard_context(terrain)
    masks = {key: ctx[key].copy() for key in ("valid", "flat", "structure", "neg", "pos")}
    result, receipt = moon._select_guarded_sharpening(terrain, contrast(terrain, gain), ctx)
    assert receipt["status"] == status and receipt["alpha"] == alpha
    np.testing.assert_allclose(result, terrain + alpha * (contrast(terrain, gain) - terrain), atol=1e-7)
    assert len(receipt["attempts"]) <= 4
    for key, mask in masks.items():
        np.testing.assert_array_equal(ctx[key], mask)


@pytest.mark.parametrize("metric,sign", [("dhr", -1), ("positive_overshoot_ratio", 1)])
def test_ringing_in_deconvolved_baseline_is_visible(terrain, metric, sign):
    ctx = moon._sharp_guard_context(terrain)
    edge = ctx["neg" if sign < 0 else "pos"] > .3
    damaged = terrain.copy()
    damaged[edge] += sign * .1
    full = moon._sharp_guard_metrics(ctx, damaged)
    old = moon._sharp_guard_metrics(moon._sharp_guard_context(damaged), damaged)
    assert full[metric] > .035
    assert old[metric] == 0
    _, receipt = moon._select_guarded_sharpening(terrain, damaged, ctx)
    assert metric in receipt["attempts"][0]["reasons"]
    assert receipt["alpha"] < 1


def test_clipping_chalk_noise_and_inherited_saturation(terrain):
    ctx = moon._sharp_guard_context(terrain)
    clipped = contrast(terrain, 1.02)
    clipped[80:90, 100:110] = 1.1
    assert "new_clipping" in moon._sharp_guard_reasons(ctx, moon._sharp_guard_metrics(ctx, clipped))
    chalky = terrain.copy()
    chalky[60:100, 80:120] = .99
    assert "csi" in moon._sharp_guard_reasons(ctx, moon._sharp_guard_metrics(ctx, chalky))
    noisy = contrast(terrain, 1.32)
    assert "noise_mad" in moon._sharp_guard_reasons(ctx, moon._sharp_guard_metrics(ctx, noisy))
    saturated = terrain.copy()
    saturated[60:100, 80:120] = 1
    saturated_ctx = moon._sharp_guard_context(saturated)
    metrics = moon._sharp_guard_metrics(saturated_ctx, saturated)
    assert metrics["new_clipped_pixels"] == 0
    assert not moon._sharp_guard_reasons(saturated_ctx, metrics, require_gain=False)


def test_brittle_gradients(terrain):
    ctx = moon._sharp_guard_context(terrain)
    brittle = terrain.copy()
    brittle[::12, ::12] += .45
    metrics = moon._sharp_guard_metrics(ctx, brittle)
    assert "gkm" in moon._sharp_guard_reasons(ctx, metrics)


@pytest.mark.parametrize("value", [np.nan, np.inf])
def test_invalid_pixels_fail_closed(terrain, value):
    bad = terrain.copy()
    bad[96, 112] = value
    with pytest.raises(ValueError, match="non-finite"):
        moon._sharp_guard_context(bad)
    result, receipt = moon._select_guarded_sharpening(terrain, bad, moon._sharp_guard_context(terrain))
    assert receipt["status"] == "fallback"
    assert receipt["attempts"][0]["reasons"] == ["non_finite_candidate"]
    np.testing.assert_array_equal(result, terrain)
    with pytest.raises(ValueError, match="non-finite"):
        moon._select_guarded_sharpening(terrain, bad, moon._sharp_guard_context(terrain), "off")


@pytest.mark.parametrize("shape", [(12, 12), (128, 128)])
def test_insufficient_or_constant_terrain(shape):
    base = np.full(shape, .6, np.float32)
    result, receipt = moon._select_guarded_sharpening(base, base + .05, moon._sharp_guard_context(base))
    assert receipt["status"] == "fallback" and receipt["alpha"] == 0
    assert receipt["baseline"]["dhr"] is None
    np.testing.assert_array_equal(result, base)


def test_missing_edges_are_not_zero_damage():
    base = np.tile(np.linspace(.45, .55, 512, dtype=np.float32), (128, 1))
    ctx = moon._sharp_guard_context(base)
    metrics = moon._sharp_guard_metrics(ctx, base)
    assert metrics["dhr"] is None and metrics["positive_overshoot_ratio"] is None


def test_rgb_uses_the_same_scalar_gain(terrain):
    base = np.stack([terrain * 1.03, terrain, terrain * .97])
    candidate = np.stack([contrast(terrain, 1.32) * 1.03, contrast(terrain, 1.32), contrast(terrain, 1.32) * .97])
    selected, receipt = moon._select_guarded_sharpening(base, candidate, moon._sharp_guard_context(base))
    assert 0 < receipt["alpha"] < 1
    np.testing.assert_allclose(selected, base + receipt["alpha"] * (candidate - base), atol=1e-7)
    clipped_channel = base.copy()
    clipped_channel[0, 90:100, 100:110] = 1.1
    assert moon._sharp_guard_metrics(moon._sharp_guard_context(base), clipped_channel)["new_clipped_pixels"] == 100


def test_off_keeps_candidate_and_cli_defaults(terrain):
    candidate = contrast(terrain, 1.8)
    selected, receipt = moon._select_guarded_sharpening(terrain, candidate, moon._sharp_guard_context(terrain), "off")
    np.testing.assert_array_equal(selected, candidate)
    assert receipt["status"] == "off" and receipt["alpha"] == 1
    parser = moon.build_parser()
    for command in ("all", "postprocess"):
        options = [command, "--work", "/tmp/moon-guard-test"]
        if command == "all":
            options += ["--input", "/tmp/moon-input"]
        assert parser.parse_args(options).sharp_guard == "auto"
        args = parser.parse_args(options + ["--sharp-mode", "crisp", "--wavelet-l1", "2", "--anti-ringing", "off"])
        assert args.sharp_guard == "auto" and args.anti_ringing == "off"
        assert parser.parse_args(options + ["--sharp-guard", "off"]).anti_ringing == "auto"


def test_zero_noise_floor_and_delivery_checks(terrain):
    ctx = moon._sharp_guard_context(terrain)
    ctx["baseline"]["noise_mad"] = 0
    metrics = moon._sharp_guard_metrics(ctx, contrast(terrain, 1.02))
    metrics["noise_mad"] = 1e-6
    assert "noise_mad" not in moon._sharp_guard_reasons(ctx, metrics)
    metrics["noise_mad"] = 2e-6
    assert "noise_mad" in moon._sharp_guard_reasons(ctx, metrics)
    # A broken display renderer must not turn a safe luminance candidate into a success.
    def render(data):
        image = data.copy()
        if np.max(np.abs(data - terrain)) > 1e-6:
            image[60:100, 80:120] = 1
        return image
    _, receipt = moon._select_guarded_sharpening(terrain, contrast(terrain, 1.02), moon._sharp_guard_context(terrain), render=render)
    assert receipt["status"] == "fallback"
    assert "delivery_new_clipping" in receipt["attempts"][0]["reasons"]


def test_nonfinite_master_rejected_before_siril(tmp_path, terrain, monkeypatch):
    bad = terrain.copy()
    bad[96, 112] = np.nan
    fits.writeto(tmp_path / "moon_master.fit", bad)
    def unexpected_siril(*args, **kwargs):
        pytest.fail("non-finite input reached Siril")
    monkeypatch.setattr(moon, "run_siril_script", unexpected_siril)
    args = moon.build_parser().parse_args(["postprocess", "--work", str(tmp_path), "--no-adc"])
    with pytest.raises(SystemExit):
        moon.cmd_postprocess(args)


def test_product_write_failure_is_reported(tmp_path, terrain, monkeypatch):
    monkeypatch.setattr(cv2, "imwrite", lambda *args, **kwargs: False)
    with pytest.raises(OSError, match="failed to write"):
        moon._write_moon_product(tmp_path, "moon_natural", terrain, fits.Header())


@pytest.mark.parametrize("channels,rotation", [(1, 0), (1, 90), (3, 180), (3, 270)])
def test_delivery_geometry_and_tampering(tmp_path, terrain, channels, rotation):
    data = terrain if channels == 1 else np.stack([terrain * 1.03, terrain, terrain * .97])
    moon._write_moon_product(tmp_path, "moon_natural", data, fits.Header())
    baseline = np.rint(data * 65535).astype(np.float32) / 65535
    fits.writeto(tmp_path / "moon_natural_baseline.fit", baseline)
    image = cv2.imread(str(tmp_path / "moon_natural.tif"), cv2.IMREAD_UNCHANGED)
    image = np.rot90(image, -(rotation // 90))
    padding = [7, 4, 9, 3]
    image = cv2.copyMakeBorder(image, *padding, cv2.BORDER_CONSTANT, value=0)
    cv2.imwrite(str(tmp_path / "moon_natural.tif"), image)
    info = {"rotation_deg": rotation, "adaptive_sky_margin": {"padding": padding}}
    guard = {"mode": "auto", "status": "fallback", "alpha": 0}
    result = moon._verify_sharp_guard(tmp_path, guard, info, 1)
    assert result["status"] == "passed" and result["max_error"] <= 1 / 65535
    image[20, 20] = 60000
    cv2.imwrite(str(tmp_path / "moon_natural.tif"), image)
    assert moon._verify_sharp_guard(tmp_path, guard, info, 1)["status"] == "failed"


@pytest.mark.skipif(not Path(moon.DEFAULT_SIRIL).exists(), reason="Siril CLI unavailable")
@pytest.mark.parametrize("mode,style,deconv,scale,rotation", [
    ("mono", "natural", "sb", 1, 90),
    ("mono", "natural", "none", 2, 0),
    ("lrgb", "natural", "sb", 2, 180),
    ("lrgb", "deep-cine", "wiener", 1, 270),
    ("legacy", "natural", "rl", 2, 90),
    ("legacy", "natural", "none", 1, 0),
])
def test_native_guarded_products(tmp_path, terrain, mode, style, deconv, scale, rotation):
    data = terrain + .008
    if mode != "mono":
        data = np.stack([data * 1.03, data, data * .97])
    header = fits.Header({"FOCALLEN": 250., "APERTURE": 50., "XPIXSZ": 2.9 / scale, "DRZSCALE": scale})
    fits.writeto(tmp_path / "moon_master.fit", data, header)
    args = moon.build_parser().parse_args([
        "postprocess", "--work", str(tmp_path), "--no-adc", "--no-html-report", "--no-auto-margin",
        "--mosaic-mode", "tile" if scale == 2 else "disc", "--mineral-mode", "legacy" if mode == "legacy" else "lrgb",
        "--mineral-style", style, "--deconv", deconv, "--rotate", str(rotation),
        "--sharp-mode", "crisp", "--wavelet-l1", "1.25", "--unsharp", "0.1", "--midtone", "0.42",
    ])
    moon.cmd_postprocess(args)
    receipt = json.loads((tmp_path / "postprocess_receipt.json").read_text())
    guard = receipt["sharp_guard"]
    assert receipt["deconvolution"]["reason"] == ("user_disabled" if deconv == "none" else "user_selected")
    assert guard["mode"] == "auto" and guard["alpha"] in (0, .25, .5, .75, 1)
    assert guard["delivery"]["status"] == "passed"
    assert set(guard["stages"]) == {"deconvolution", "wavelet", "combined"}
    assert guard["deconvolution"]["executed"] == deconv
    assert fits.getheader(tmp_path / "moon_natural_master.fit")["SHRPALPH"] == guard["alpha"]
    script = (tmp_path / "logs/03_postprocess.ssf").read_text()
    assert "wavelet 5 2" not in script
    adaptation = json.loads((tmp_path / "postprocess_receipt.json").read_text())["wavelet_adaptation"]
    extract_script = (tmp_path / "logs/03_wavelet_extract.ssf").read_text()
    assert extract_script.count(f"extract {adaptation['planes']}") == (1 if deconv == "none" else 2)
    assert guard["detail_support_file"] == "moon_wavelet_support.fit"
    if deconv != "none":
        assert script.count(f"\n{deconv} ") == 1
        assert script.index("save moon_unsharpened") < script.index(f"\n{deconv} ")
    if mode == "mono":
        assert not (tmp_path / "moon_mineral.jpg").exists()
    else:
        assert fits.getheader(tmp_path / "moon_mineral_master.fit")["SHRPALPH"] == guard["alpha"]
        assert (tmp_path / "moon_mineral_square.jpg").exists()
    assert (tmp_path / "moon_natural_square.tif").exists()
    np.testing.assert_array_equal(fits.getdata(tmp_path / "moon_master.fit"), data)
    moon.cmd_verify(moon.build_parser().parse_args(["verify", "--work", str(tmp_path), "--no-html-report"]))
    verified = json.loads((tmp_path / "verify_report.json").read_text())
    assert verified["sharp_guard"]["delivery"]["status"] == "passed"


@pytest.mark.skipif(not Path(moon.DEFAULT_SIRIL).exists(), reason="Siril CLI unavailable")
@pytest.mark.parametrize("guard_mode,anti_ringing", [("auto", "off"), ("off", "auto"), ("off", "off")])
def test_native_noop_and_independent_switches(tmp_path, terrain, guard_mode, anti_ringing):
    fits.writeto(tmp_path / "moon_master.fit", terrain)
    args = moon.build_parser().parse_args([
        "postprocess", "--work", str(tmp_path), "--no-adc", "--no-html-report", "--no-auto-margin",
        "--sharp-mode", "none", "--deconv", "none", "--sharp-guard", guard_mode,
        "--anti-ringing", anti_ringing,
    ])
    moon.cmd_postprocess(args)
    guard = json.loads((tmp_path / "postprocess_receipt.json").read_text())["sharp_guard"]
    assert guard["status"] == ("fallback" if guard_mode == "auto" else "off")
    if guard_mode == "auto":
        np.testing.assert_array_equal(fits.getdata(tmp_path / "moon_sharp.fit"), fits.getdata(tmp_path / "moon_unsharpened.fit"))
    if anti_ringing == "off":
        assert "anti_ringing" not in json.loads((tmp_path / "postprocess_receipt.json").read_text())
    else:
        assert "anti_ringing" in json.loads((tmp_path / "postprocess_receipt.json").read_text())


def test_old_work_directory_reports_unknown_guard(tmp_path, terrain):
    fits.writeto(tmp_path / "moon_master.fit", terrain)
    fits.writeto(tmp_path / "moon_base.fit", terrain)
    fits.writeto(tmp_path / "moon_sharp.fit", terrain)
    moon.cmd_verify(moon.build_parser().parse_args(["verify", "--work", str(tmp_path), "--no-html-report"]))
    report = json.loads((tmp_path / "verify_report.json").read_text())
    assert report["sharp_guard"]["status"] == "unknown"
    assert report["dark_halo_ratio"] == 0


def test_hud_reads_latest_guard_receipt(tmp_path):
    image = np.zeros((10, 10), np.uint8)
    cv2.imwrite(str(tmp_path / "moon_natural.jpg"), image)
    (tmp_path / "postprocess_receipt.json").write_text(json.dumps({"sharp_guard": {"status": "reduced", "alpha": .5}}))
    (tmp_path / "verify_report.json").write_text(json.dumps({"sharp_guard": {"status": "accepted", "alpha": 1}}))
    html = moon._generate_comparison_html(tmp_path).read_text()
    assert '"status": "reduced", "alpha": 0.5' in html
    assert "hud-sharp-guard" in html
