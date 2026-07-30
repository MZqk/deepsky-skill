# Quantitative diagnostic interpretation

Use this reference when reading `*_analysis.json` produced by `scripts/analyze_file.py`.

## Contents

- Evidence model
- Statistics
- Clipping
- Noise
- Background and gradient
- Stars
- Color
- Classification

## Evidence model

Every diagnostic section includes an `evidence` field:

- `measured`: calculated directly from the supplied pixel data;
- `measured_on_robust_normalization`: calculated after mapping luminance P0.1–P99.9 to 0–1;
- `metadata_and_filename_heuristic`: inferred from headers and naming;
- `unavailable`: insufficient valid samples;
- `not_applicable`: the metric does not apply to this channel model.

Do not promote heuristic or unavailable fields into measured facts.

## Statistics

`statistics` preserves the original numeric range and reports robust percentiles, MAD sigma,
NaN/Inf ratios, and exact/near extrema.

- High `exact_max_ratio` can indicate clipping, integer limits, masks, or synthetic borders.
- High `exact_min_ratio` can indicate clipped black pixels, stacking borders, masks, or valid zero
  backgrounds.
- These fields do not identify the physical cause.

## Clipping

`clipping` is measured after robust P0.1–P99.9 normalization.

- `highlight_ratio_ge_0_999` identifies pixels at the bright end of the review mapping.
- `shadow_ratio_le_0_001` identifies pixels at the dark end.
- Do not call these pixels sensor-saturated without checking original ADU limits, bit depth,
  calibration history, and star cores.
- Do not call shadows black-clipped without inspecting the original histogram and invalid borders.

Use the highlights preview for visual confirmation.

## Noise

`noise.background_noise_sigma_normalized` is the MAD sigma of a 3×3 median-filter residual,
sampled from the darkest 35% of normalized luminance.

It is useful for:

- comparing versions of the same registered image;
- identifying unusually noisy backgrounds;
- deciding whether linear denoising deserves inspection.

It is not:

- physical SNR;
- read noise in electrons;
- valid proof that faint low-frequency structure is noise.

Resampling, drizzle, compression, previous denoising, undersampling, and real faint signal can
change the estimate. Check `block_sigma_median`, `block_sigma_p90`, and sample count.

## Background and gradient

`background.plane` fits a plane to low-signal sampled pixels:

- `x_change_across_frame`;
- `y_change_across_frame`;
- `magnitude_across_frame`;
- `angle_degrees`;
- `r_squared`;
- `residual_rms`.

`region_medians_normalized` and `corner_mean_over_center` provide independent spatial anchors.

Interpretation:

- high magnitude with high R² supports a coherent low-frequency trend;
- low R² means a plane does not explain the background well;
- low corner/center ratio can be consistent with vignetting, but also with target placement or
  real sky structure;
- different RGB plane vectors can indicate chromatic gradient or real line emission.

Never recommend background subtraction from these values alone. Inspect the background preview,
target type, framing, flats, mosaics, H-alpha/IFN/dust risk, and a trial background model.

## Stars

`stars` detects local maxima and calculates background-subtracted second moments for unsaturated
star-like patches.

Useful fields:

- `usable_star_count`;
- `fwhm_major_median_px` and `fwhm_minor_median_px`;
- `axis_ratio_median`;
- `eccentricity_median` and `eccentricity_p90`;
- `position_angle_median_deg`.

Limitations:

- this is not a nonlinear PSF fit;
- blends, nebular knots, diffraction spikes, undersampling, and processed stars can bias results;
- one median angle cannot diagnose tracking without checking spatial consistency;
- FWHM in pixels is not seeing in arcseconds without reliable pixel scale;
- final processed images cannot be used to judge acquisition FWHM reliably.

If fewer than five candidates pass validation, the section reports `unavailable`. Do not invent
star-shape conclusions.

## Color

`color` reports background channel medians, channel P99, correlation, and collapsed channels.

- Background imbalance can result from light pollution, calibration, filter response, or real
  emission.
- High red signal in an emission field is not automatically a red cast.
- Narrowband and dual-band data do not obey broadband white-balance assumptions.
- Channel correlation describes morphology similarity, not color accuracy.

Catalog-based color accuracy requires WCS, suitable stellar photometry, instrument response, and
unsaturated-star measurements; this analyzer does not perform that validation.

## Classification

`classification` uses FITS/XISF metadata and filenames.

- `frame_role`: probable light/dark/flat/bias or unknown;
- `processing_stage`: probable integrated/master state or unknown;
- `transfer_state`: likely linear for astronomical containers, but not guaranteed;
- `channel_model`: inferred from dimensions, filter, and Bayer metadata.

Treat all classification fields as provisional unless acquisition history confirms them.
