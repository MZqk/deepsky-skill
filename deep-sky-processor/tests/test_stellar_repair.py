import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, shift


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stellar_repair as stellar_repair_module
from stellar_repair import (
    analyze_stellar_repair,
    build_stellar_repair_preview,
    run_stellar_repair,
)
from stellar_shape_repair import analyze_stellar_shape


STAR_SPECS = [
    (20, 22, 0.60),
    (24, 70, 0.52),
    (42, 46, 0.58),
    (58, 82, 0.48),
    (72, 28, 0.55),
    (88, 62, 0.62),
    (100, 94, 0.50),
    (106, 34, 0.46),
]


def _impulses(shape=(128, 128)):
    values = np.zeros(shape, dtype=np.float32)
    for y, x, peak in STAR_SPECS:
        values[y, x] = peak
    return values


def _clean_rgb():
    stars = gaussian_filter(_impulses(), sigma=1.35)
    image = np.full((*stars.shape, 3), 0.02, dtype=np.float32)
    image += stars[..., None]
    return np.clip(image, 0.0, 1.0)


def _misregistered_rgb():
    stars = gaussian_filter(_impulses(), sigma=1.35)
    image = np.full((*stars.shape, 3), 0.02, dtype=np.float32)
    image[..., 0] += shift(
        stars,
        shift=(0.55, -0.35),
        order=1,
        mode="nearest",
        prefilter=False,
    )
    image[..., 1] += stars
    image[..., 2] += shift(
        stars,
        shift=(-0.45, 0.30),
        order=1,
        mode="nearest",
        prefilter=False,
    )
    return np.clip(image, 0.0, 1.0)


def _purple_halo_rgb():
    impulses = _impulses()
    narrow = gaussian_filter(impulses, sigma=1.15)
    broad = gaussian_filter(impulses, sigma=2.0)
    image = np.full((*narrow.shape, 3), 0.02, dtype=np.float32)
    image[..., 0] += broad * 1.25
    image[..., 1] += narrow
    image[..., 2] += broad * 1.25
    return np.clip(image, 0.0, 1.0)


def _elongated_starfield(*, channels=3):
    height = width = 256
    yy, xx = np.mgrid[:height, :width]
    mono = np.full((height, width), 0.03, dtype=np.float32)
    for cy in range(16, 240, 28):
        for cx in range(16, 240, 28):
            amplitude = 0.35 + 0.50 * ((cx + cy) % 47) / 47.0
            mono += (
                np.exp(
                    -0.5
                    * (
                        ((xx - cx) / 1.1) ** 2
                        + ((yy - cy) / 2.2) ** 2
                    )
                )
                * amplitude
            ).astype(np.float32)
    mono = np.clip(mono, 0.0, 1.0)
    if channels == 1:
        return mono
    return mono[..., None] * np.asarray(
        [1.0, 0.98, 0.96],
        dtype=np.float32,
    )


def test_clean_linear_rgb_is_skipped():
    image = _clean_rgb()

    report = analyze_stellar_repair(image, is_linear=True)
    corrected, correction = run_stellar_repair(
        image,
        is_linear=True,
    )

    assert report["decision"] == "skip"
    assert correction["status"] == "skipped"
    np.testing.assert_array_equal(corrected, image)


def test_systematic_rgb_offset_is_detected_and_repaired_with_paired_gates():
    image = _misregistered_rgb()

    before = analyze_stellar_repair(image, is_linear=True)
    corrected, report = run_stellar_repair(image, is_linear=True)

    assert before["decision"] == "apply"
    assert before["systematic_shift_detected"] is True
    assert report["status"] == "applied"
    assert report["applied"] is True
    assert report["quality_gates"]["channel_alignment_improved"] is True
    assert report["quality_gates"]["fwhm_preserved"] is True
    assert (
        report["after"]["systematic_channel_offset_px"]
        < before["systematic_channel_offset_px"]
    )
    assert not np.array_equal(corrected, image)


def test_stellar_purple_halo_is_detected_and_reduced_without_core_rebuild():
    image = _purple_halo_rgb()

    before = analyze_stellar_repair(image, is_linear=True)
    corrected, report = run_stellar_repair(image, is_linear=True)

    assert before["purple_fringe_detected"] is True
    assert before["shape_review_required"] is False
    assert report["status"] == "applied"
    assert report["quality_gates"]["purple_halo_improved"] is True
    assert report["quality_gates"]["roundness_not_regressed"] is True
    assert (
        report["after"]["purple_halo_excess_p90"]
        < before["purple_halo_excess_p90"]
    )
    assert not np.array_equal(corrected, image)


def test_purple_halo_suppression_preserves_red_emission_signal():
    height = width = 64
    yy, xx = np.mgrid[:height, :width]
    radius = np.hypot(xx - 32, yy - 32)
    image = np.full((height, width, 3), 0.02, dtype=np.float32)
    image[..., 0] += 0.22 * np.exp(-((radius / 12.0) ** 2))
    blue_ring = 0.10 * np.exp(-(((radius - 3.2) / 0.9) ** 2))
    image[..., 2] += blue_ring

    corrected, operation = (
        stellar_repair_module._apply_purple_halo_suppression(
            image,
            strength=0.5,
            centers_rc=[[32, 32]] * 6,
        )
    )

    np.testing.assert_array_equal(corrected[..., 0], image[..., 0])
    assert float(np.mean(corrected[..., 2][blue_ring > 0.02])) < float(
        np.mean(image[..., 2][blue_ring > 0.02])
    )
    assert operation["source_red_preserved"] is True
    assert operation["correction_basis"] == "stellar_blue_over_green_excess"


def test_purple_halo_repair_selects_lowest_strength_passing_all_gates(
    monkeypatch,
):
    image = _purple_halo_rgb()
    original_apply = stellar_repair_module._apply_purple_halo_suppression

    def staged_apply(candidate, *, strength, centers_rc=None):
        if strength < 0.2:
            return candidate.copy(), {
                "method": (
                    "stellar_halo_luminance_preserving_chroma_suppression"
                ),
                "strength": float(strength),
            }
        return original_apply(
            candidate,
            strength=strength,
            centers_rc=centers_rc,
        )

    monkeypatch.setattr(
        stellar_repair_module,
        "_apply_purple_halo_suppression",
        staged_apply,
    )

    corrected, report = run_stellar_repair(
        image,
        is_linear=True,
        strength_profile="conservative",
    )

    operation = next(
        item
        for item in report["operations"]
        if item["method"]
        == "stellar_halo_luminance_preserving_chroma_suppression"
    )
    assert report["status"] == "applied"
    assert operation["strength"] == 0.2
    assert [
        trial["strength"] for trial in operation["candidate_trials"]
    ] == [0.1, 0.15, 0.2]
    assert operation["candidate_trials"][0]["failed_gates"] == [
        "purple_halo_improved"
    ]
    assert operation["candidate_trials"][-1]["accepted"] is True
    assert operation["selection_policy"] == (
        "lowest_strength_passing_all_hard_gates"
    )
    assert not np.array_equal(corrected, image)


def test_significant_stellar_shape_aberration_requires_review_and_is_not_rebuilt():
    elongated = gaussian_filter(_impulses(), sigma=(1.0, 2.6))
    image = np.full((*elongated.shape, 3), 0.02, dtype=np.float32)
    image += elongated[..., None]

    analysis = analyze_stellar_repair(image, is_linear=True)
    corrected, report = run_stellar_repair(image, is_linear=True)

    assert analysis["decision"] == "review_required"
    assert analysis["shape_review_required"] is True
    assert report["status"] == "review_required"
    assert report["applied"] is False
    np.testing.assert_array_equal(corrected, image)


def test_color_only_repairs_safe_color_defects_while_preserving_reviewed_shape():
    elongated = gaussian_filter(_impulses(), sigma=(1.0, 2.6))
    image = np.full((*elongated.shape, 3), 0.02, dtype=np.float32)
    image[..., 0] += 1.20 * shift(
        elongated,
        shift=(0.55, -0.35),
        order=1,
        mode="nearest",
        prefilter=False,
    )
    image[..., 1] += elongated
    image[..., 2] += 1.20 * shift(
        elongated,
        shift=(-0.45, 0.30),
        order=1,
        mode="nearest",
        prefilter=False,
    )
    image = np.clip(image, 0.0, 1.0)

    before = analyze_stellar_repair(image, is_linear=True)
    corrected, report = run_stellar_repair(
        image,
        is_linear=True,
        mode="color_only",
        strength_profile="conservative",
    )

    assert before["shape_review_required"] is True
    assert before["systematic_shift_detected"] is True
    assert before["purple_fringe_detected"] is True
    assert report["status"] == "applied"
    assert report["applied"] is True
    assert all(report["quality_gates"].values())
    assert report["quality_gates"]["centroid_preserved"] is True
    assert report["quality_gates"]["stellar_brightness_preserved"] is True
    assert report["shape_repair"]["status"] == "preserved_unmodified"
    assert report["shape_repair"]["applied"] is False
    assert report["shape_repair"]["automatic_geometry_reconstruction"] is False
    assert (
        "source_stellar_shape_preserved_unmodified"
        in report["preserved_limitations"]
    )
    assert (
        report["after"]["systematic_channel_offset_px"]
        < before["systematic_channel_offset_px"]
    )
    assert (
        report["after"]["purple_halo_excess_p90"]
        <= before["purple_halo_excess_p90"] * 0.92
    )
    assert not np.array_equal(corrected, image)


def test_nonlinear_input_never_enters_automatic_repair_and_clean_mono_skips():
    rgb = _misregistered_rgb()
    mono = rgb[..., 1]

    nonlinear = analyze_stellar_repair(rgb, is_linear=False)
    monochrome = analyze_stellar_repair(mono, is_linear=True)

    assert nonlinear["decision"] == "skip"
    assert nonlinear["reason"] == "input_is_not_confirmed_linear"
    assert monochrome["decision"] == "skip"
    assert monochrome["shape_repair_detected"] is False


def test_linear_broadband_rgb_shape_repair_passes_all_hard_gates():
    image = _elongated_starfield(channels=3)

    before = analyze_stellar_repair(
        image,
        is_linear=True,
        channel_semantics="broadband_rgb",
    )
    corrected, report = run_stellar_repair(
        image,
        is_linear=True,
        channel_semantics="broadband_rgb",
        mode="shape_only",
    )

    assert before["decision"] == "apply"
    assert before["shape_repair_detected"] is True
    assert report["status"] == "applied"
    assert report["shape_repair"]["selected_strength"] == 0.25
    assert all(report["shape_repair"]["quality_gates"].values())
    assert (
        report["shape_repair"]["paired_star_geometry"][
            "roundness_deficit_improvement"
        ]
        >= 0.20
    )
    assert not np.array_equal(corrected, image)


def test_linear_narrowband_mono_runs_shape_only_repair():
    image = _elongated_starfield(channels=1)

    corrected, report = run_stellar_repair(
        image,
        is_linear=True,
        channel_semantics="monochrome",
    )

    assert report["status"] == "applied"
    assert report["shape_repair"]["applied"] is True
    assert all(
        operation["method"] == "stellar_shape_psf_mapping"
        for operation in report["operations"]
    )
    assert corrected.ndim == 2
    assert not np.array_equal(corrected, image)


def test_failed_required_background_forces_diagnostic_only():
    image = _elongated_starfield(channels=1)

    corrected, report = run_stellar_repair(
        image,
        is_linear=True,
        channel_semantics="monochrome",
        execute_repairs=False,
    )

    assert report["status"] == "review_required"
    assert report["diagnostic_only"] is True
    assert report["operations"] == []
    np.testing.assert_array_equal(corrected, image)


def test_shape_analyzer_handles_more_than_3000_stars_with_bounded_fit_count():
    size = 1024
    impulses = np.zeros((size, size), dtype=np.float32)
    for y in range(12, size - 12, 16):
        for x in range(12, size - 12, 16):
            impulses[y, x] = 0.8
    image = 0.02 + gaussian_filter(impulses, sigma=(1.0, 2.0))

    report = analyze_stellar_shape(image, is_linear=True)

    assert report["n_paired_stars"] >= 3000
    assert report["n_paired_stars"] <= 4096
    assert report["occupied_grid_cells"] == 16
    assert report["decision"] == "apply"


def test_smooth_spatial_psf_field_is_fitted_and_repaired():
    size = 320
    image = np.full((size, size), 0.02, dtype=np.float32)
    yy, xx = np.mgrid[-8:9, -8:9]
    for cy in range(16, size - 16, 24):
        for cx in range(16, size - 16, 24):
            sigma_x = 1.15 + 0.65 * cx / (size - 1)
            patch = 0.65 * np.exp(
                -0.5 * ((xx / sigma_x) ** 2 + (yy / 2.1) ** 2)
            )
            image[cy - 8:cy + 9, cx - 8:cx + 9] += patch.astype(
                np.float32
            )

    analysis = analyze_stellar_shape(image, is_linear=True)
    corrected, report = run_stellar_repair(
        image,
        is_linear=True,
        channel_semantics="monochrome",
        include_preview_image=True,
    )

    assert analysis["n_paired_stars"] >= 80
    assert analysis["occupied_grid_cells"] >= 12
    assert analysis["spatial_variation_detected"] is True
    assert analysis["shape_model_kind"] == "spatial_quadratic"
    assert report["status"] == "applied"
    assert report["shape_repair"]["psf_field"]["kind"] == "spatial_quadratic"
    assert all(report["shape_repair"]["quality_gates"].values())
    assert not np.array_equal(corrected, image)

    preview = report.pop("_preview_image")
    rebuilt_preview = build_stellar_repair_preview(image, corrected, report)
    assert preview is not None
    assert preview.dtype == np.float32
    assert preview.shape[0] > 0
    assert preview.shape[1] > 0
    np.testing.assert_array_equal(rebuilt_preview, preview)


def test_pipeline_executes_stellar_repair_when_requested(tmp_path):
    from pipeline import run_pipeline
    from fits_io import write_image

    image = _misregistered_rgb()
    input_fits = tmp_path / "test_input.fits"
    output_jpg = tmp_path / "test_output.jpg"
    write_image(image, str(input_fits))

    result = run_pipeline(
        str(input_fits),
        str(output_jpg),
        steps="color,stellar_repair,stretch",
        work_dir=str(tmp_path / "work"),
        save_intermediates=True,
    )

    assert result["stellar_repair"] is not None
    assert result["stellar_repair"]["status"] == "applied"
    assert (tmp_path / "work" / "02b_stellar_repair_report.json").exists()


def test_pipeline_skips_stellar_repair_for_nonlinear_input(tmp_path):
    from pipeline import run_pipeline
    from skimage.io import imsave

    image = _misregistered_rgb()
    input_png = tmp_path / "test_input.png"
    output_jpg = tmp_path / "test_output.jpg"
    imsave(str(input_png), (image * 255).astype(np.uint8))

    result = run_pipeline(
        str(input_png),
        str(output_jpg),
        steps="color,stellar_repair,stretch",
        work_dir=str(tmp_path / "work"),
        save_intermediates=True,
    )

    assert result["stellar_repair"] is None
