import numpy as np
import tempfile
from pathlib import Path
from skimage.io import imsave

from scripts.reference_grade import (
    _global_match_score,
    match_reference_grade,
    optimize_reference_grade,
)
from scripts.pipeline import run_pipeline


def test_reference_grade_preserves_shape_and_range():
    y, x = np.mgrid[0:64, 0:96]
    source = np.stack(
        [
            0.04 + x / 600,
            0.03 + y / 700,
            0.025 + (x + y) / 1400,
        ],
        axis=-1,
    ).astype(np.float32)
    reference = np.clip(source * np.array([1.2, 1.05, 0.95]) + 0.04, 0, 1)

    result, report = match_reference_grade(source, reference)

    assert result.shape == source.shape
    assert np.isfinite(result).all()
    assert float(result.min()) >= 0
    assert float(result.max()) <= 1
    assert report["structural_transfer"] is False


def test_reference_grade_is_monotonic_for_gray_ramp():
    ramp = np.linspace(0.01, 0.8, 256, dtype=np.float32)
    source = np.stack([np.tile(ramp, (32, 1))] * 3, axis=-1)
    reference = np.power(source, 0.8)

    result, _ = match_reference_grade(
        source,
        reference,
        local_contrast=0,
    )
    output_ramp = result[0, :, 0]

    assert np.all(np.diff(output_ramp) >= -1e-5)


def test_reference_search_handles_very_dark_input_and_reports_constraints():
    y, x = np.mgrid[0:64, 0:96]
    signal = np.exp(-(((x - 48) / 18) ** 2 + ((y - 32) / 12) ** 2))
    source = np.zeros((64, 96, 3), dtype=np.float32)
    source += [0.0002, 0.0001, 0.00008]
    source += signal[..., None] * [0.008, 0.003, 0.002]
    reference = np.clip(
        np.power(source / max(float(source.max()), 1e-6), 0.5) * 0.7,
        0,
        1,
    )

    result, report = optimize_reference_grade(
        source,
        reference,
        preview_size=128,
        local_contrast=0,
    )

    assert result.shape == source.shape
    assert report["structural_transfer"] is False
    assert report["spatial_alignment_used"] is False
    assert report["source_brightness"]["very_dark"] is True
    assert report["evaluated_candidates"] > 0
    assert report["selected_params"]["stretch_factor"] > 0


def test_reference_search_improves_global_score():
    ramp = np.linspace(0.01, 0.35, 128, dtype=np.float32)
    source = np.stack([np.tile(ramp, (48, 1))] * 3, axis=-1)
    reference = np.power(source, 0.55)
    before, _ = _global_match_score(source, reference)

    result, report = optimize_reference_grade(
        source,
        reference,
        preview_size=128,
        local_contrast=0,
    )
    after, _ = _global_match_score(result, reference)

    assert after < before
    assert report["final_global_score"] == after


def test_pipeline_reference_mode_reports_non_structural_match():
    source = np.full((32, 48, 3), [0.04, 0.03, 0.02], dtype=np.float32)
    source[10:22, 16:32] += [0.12, 0.04, 0.03]
    reference = np.clip(np.power(source, 0.75) * 1.2, 0, 1)
    with tempfile.TemporaryDirectory() as td:
        source_path = Path(td) / "source.png"
        reference_path = Path(td) / "reference.png"
        output_path = Path(td) / "output.tif"
        imsave(source_path, (source * 255).astype(np.uint8))
        imsave(reference_path, (reference * 255).astype(np.uint8))

        result = run_pipeline(
            str(source_path),
            str(output_path),
            steps="stretch",
            preset="light",
            reference_image=str(reference_path),
            reference_auto_search=True,
            cleanup=True,
        )

        report = result["reference_grade"]
        assert report["structural_transfer"] is False
        assert report["spatial_alignment_used"] is False
        assert report["evaluated_candidates"] > 0
