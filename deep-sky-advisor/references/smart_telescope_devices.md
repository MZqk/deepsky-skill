# Smart telescope device priors

Use this reference when the supplied file may originate from an all-in-one smart telescope
(DWARFLAB DWARF 3 / DWARF mini / Draco, ZWO Seestar S30 / S30 Pro / S50 / S50 Pro).

Device priors are **metadata/assumed evidence**. They refine interpretation and safety rules;
they never override measured evidence from the file itself.

## Contents

- Identification signals
- Telephoto optical and sensor priors
- Secondary wide-field cameras
- Built-in filters and channel-model implications
- Acquisition traits that shape diagnostics
- Processing implications
- Sensor evidence levels
- Device-specific safety rules

## Identification signals

`scripts/analyze_file.py` emits `classification.device` when FITS/XISF headers
(`TELESCOP`, `INSTRUME`) or the filename contain brand/model tokens.

- Header matches are stronger evidence than filename matches; both remain heuristic.
- Community-reported header values vary by firmware and app version. Confirm against the actual
  header of the supplied file before repeating any header value in the report.
- A device match without a model (brand token only) activates brand-level priors only; do not
  assume a specific sensor or focal length.
- If the device is `unknown`, continue with explicit unknowns instead of guessing.

## Telephoto optical and sensor priors

Image scale below is geometric: `206.265 × pixel_size(µm) / focal_length(mm)`. Values marked
*(chip spec)* come from the sensor datasheet, not the device vendor; values marked *(official)*
come from the vendor; values marked *(inferred)* lack official or measured confirmation.

| Device | FL / aperture | f-ratio | Sensor | Pixel | Scale (″/px) | Max sub |
|---|---|---|---|---|---|---|
| DWARF 3 | 150 mm / 35 mm | F/4.3 | Sony IMX678 (1/1.8″) | 2.0 µm *(chip spec)* | ≈2.75 | 120 s (60 s advised) |
| DWARF mini | 150 mm / 30 mm | f/5 | Sony IMX662 (1/2.8″) | 2.9 µm *(chip spec)* | ≈3.99 | 180 s beta (120 s advised) |
| DWARF Draco | 340 mm / 90 mm | F/3.8 *(inferred)* | OmniVision OV50Q40 (1/1.3″, 50.3 MP) | 1.197 µm native / 2.394 µm 2×2 bin *(official)* | ≈1.45 (binned) | 300 s (guided + physical derotation) |
| Seestar S30 | 150 mm / 30 mm | f/5 | Sony IMX662 (1/2.8″) | 2.9 µm *(chip spec)* | ≈3.99 | 60 s |
| Seestar S30 Pro | 160 mm / 30 mm | f/5.3 | Sony IMX585 (1/1.2″) | 2.9 µm *(chip spec)* | ≈3.74 | 60 s |
| Seestar S50 | 250 mm / 50 mm | f/5 | Sony IMX462 (1/2.8″) | 2.9 µm *(chip spec)* | ≈2.39 | 30 s (20 s advised) |
| Seestar S50 Pro | 260 mm / 50 mm | F/5.2 *(official)* | OmniVision OS08B10 (1/1.2″) | 2.9 µm *(official)* | ≈2.30 | 60 s |

Consequences:

- All devices are OSC/CFA color cameras with 12-bit-class ADCs. Do not infer 16-bit dynamic
  range from a converted FITS container.
- Scales of 2.3–4 ″/px mean stars span few pixels; moment-based FWHM near 2–3 px is normal and
  is not evidence of poor focus. Deconvolution at these scales is rarely justified.
- Small full wells (IMX462/IMX678 ≈11 ke, chip spec) saturate bright star cores early; expect
  clipped cores in long subs and treat them as acquisition reality, not a processing failure.

## Secondary wide-field cameras

Most devices carry a second wide-field camera (DWARF 3/mini/Draco, S30 Pro, S50 Pro; S50 has
none). These serve finder/framing, daytime, and Milky-Way-wide modes (e.g. 6 mm class optics,
IMX586/IMX307/OV50E40 sensors, tens of arcseconds per pixel).

- A wide-field file is not a defect of the telephoto system. Classify it as wide field and apply
  wide-field safety rules (distinguish vignetting, sky gradient, and Milky Way structure).
- S30's wide camera is daylight-only per the comparison data; do not assume deep-sky capability.

## Built-in filters and channel-model implications

| Device | Built-in filters |
|---|---|
| DWARF 3 | Standard (UV/IR), astro, dual-band |
| DWARF mini | Astro, dual-band, dark-frame |
| DWARF Draco | Tele: Astro 440–680 nm, dark, H+O dual-band (OIII 500.7 / Hα 656.3, FWHM 13 nm), ND OD5; SHO edition adds S II 671.6 nm. Wide: VIS 430–565 nm + ND |
| Seestar S30 / S30 Pro / S50 | Astro (UV/IR), light-pollution dual-band |
| Seestar S50 Pro | UV/IR-Cut (tele + wide), dual narrowband OIII 30 nm / Hα 20 nm (tele only), dark-frame (tele only) |

Implications:

- Dual-band on an OSC sensor physically routes Hα into the red channel and OIII mainly into
  green and blue. Channel-separated "Ha ≈ R, OIII ≈ (G+B)/2" two-channel narrowband processing
  is legitimate **only when the duo-band filter and target type support it**; document the
  mapping and never present it as measured SHO.
- Do not apply broadband white balance to duo-band emission data (already a global rule; doubly
  relevant here because these devices ship duo-band by default in city use).
- The mechanical "dark-frame filter" (DWARF mini, S50 Pro, Draco) produces true darks; a file
  taken with it is a calibration frame, not a light. Watch for unexpected near-zero images.
- Narrowband bandpasses differ per device (13 nm vs 20/30 nm class); do not transfer
  noise/balance expectations between devices.

## Acquisition traits that shape diagnostics

- **Alt-az tracking by default.** All devices except their EQ modes track in alt-az; long
  integrations show field rotation, worst at frame corners. Corner-star elongation that grows
  with radius and points tangentially is consistent with rotation, not tilt. Seestar EQ mode
  and Draco's physical CMOS derotation + internal guider reduce this; treat residual elongation
  on those as worth a separate look.
- **Sub-exposure caps** (table above) explain heavy stack counts and modest per-sub signal;
  `STACKCNT`-style headers, when present, are the norm rather than a red flag.
- **On-device stacking.** App exports are commonly already-stacked, already-stretched JPEG/TIFF;
  raw FITS subs exist only when the user enabled frame saving. A device-exported JPEG/TIFF is
  nonlinear and processed: do not recommend linear-stage operations for it. A device-stacked
  FITS may lack `NCOMBINE`; stage classification stays `unknown` without other evidence.
- **No dithering** on most firmware: expect walking noise / fixed-pattern residuals in
  on-device stacks; this favors conservative linear denoise review over aggressive background
  modeling.
- **No cooled sensor**: dark current and amp glow scale with ambient temperature and sub length;
  hot pixels are expected in uncalibrated subs and are not proof of sensor defects.

## Processing implications

Map device context onto the standard advice operations:

- `crop_edges`: alt-az rotation plus registration produces triangular invalid wedges; crop only
  the truly invalid border — rotation wedges are expected even in good data.
- `background_review`: city duo-band data shows strong sky-glow gradients; still require a trial
  model review, because Hα fields are exactly what duo-band users shoot.
- `narrowband_mapping`: trigger review when the device prior or FILTER header indicates a
  duo-band filter, even if the filename says nothing.
- `linear_denoise`: walking noise from undithered stacks is spatially correlated; the MAD
  high-pass estimate underestimates it. Inspect the background preview before deciding.
- `star_shape_review`: at 2–4 ″/px, judge elongation spatially (rotation pattern) before
  blaming focus or optics.
- `controlled_stretch`: on-device JPEG/TIFF exports are already stretched; re-stretching them
  amplifies banding in 8-bit data. Prefer requesting the FITS master when heavy work is needed.

## Sensor evidence levels

Follow the comparison data's grading; do not quote inferred numbers as device facts.

- **IMX585 (S30 Pro)**: measured by camera vendors — ≈0.7 e⁻ read noise, 40–54 ke⁻ full well,
  mono QE ≈91% peak / 80.9% Hα / 91.2% OIII; zero amp glow reported in 300 s darks. Strong
  evidence base.
- **OS08B10 (S50 Pro)**: read noise, full well, QE, dark current all *inferred* — no official
  curves, no production astro camera, no mono variant. Treat any numeric claim about it as low
  confidence; the inference interval midpoints trail IMX585 measured values on every comparable
  metric.
- **IMX662 / IMX462 / IMX678**: datasheet-level figures only (see table); device-level gain
  curves are unpublished.
- **OV50Q40 (Draco)**: vendor publishes format and binning only; noise/QE unknown. Its strength
  is optical (90 mm aperture, guided 300 s subs), not sensor characterization.

## Device-specific safety rules

- Do not recommend star removal on any smart-telescope wide-field Milky Way export; stars are
  the subject.
- Do not diagnose optics from an on-device processed export (denoise/sharpen chains distort PSF
  shape); require raw subs for star-shape acquisition claims.
- Do not treat the S50 Pro's missing noise/QE specs as evidence of poor performance; state the
  evidence gap instead.
- Do not assume the wide and tele cameras share calibration, filters, or color response; they
  are independent optical systems.
- Do not quote device prices, release dates, or marketing claims in processing reports; only
  acquisition-relevant parameters belong in the advice.
