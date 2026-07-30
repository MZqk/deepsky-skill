import numpy as np

from scripts.compose_starnet_layers import beautify_starless, luminance


def test_beautify_starless_reaches_requested_median_without_clipping():
    y, x = np.mgrid[0:96, 0:128]
    luma = 0.055 + 0.045 * (x / x.max()) + 0.025 * (y / y.max())
    source = np.stack(
        [luma * 1.12, luma, luma * 0.82],
        axis=-1,
    ).astype(np.float32)

    result = beautify_starless(
        source,
        gamma=0.88,
        saturation=1.18,
        local_contrast=0.16,
        target_median=0.14,
    )

    assert result.shape == source.shape
    assert np.isfinite(result).all()
    assert float(result.min()) >= 0
    assert float(result.max()) <= 1
    assert abs(float(np.median(luminance(result))) - 0.14) < 0.01
