# Computer Vision Guardrails
## 内容导航

- [Mandatory image state](#mandatory-image-state)
- [OpenCV rules](#opencv-rules)
- [scikit-image rules](#scikit-image-rules)
- [CLAHE and local contrast](#clahe-and-local-contrast)
- [Linear vs nonlinear boundaries](#linear-vs-nonlinear-boundaries)
- [DeepSNR and external denoise guardrails](#deepsnr-and-external-denoise-guardrails)
- [Target-type safety rules](#target-type-safety-rules)


> **Authority note**: This document defines invariants for CV-based processing.
> When runtime code already enforces a stricter rule or fallback, the code path
> is authoritative; this file explains the safety intent and review criteria.

OpenCV and scikit-image are infrastructure libraries for controlled pixel-space
operations. They are not evidence that new astronomical structure exists.

## Mandatory image state

Any CV stage that writes an artifact or JSON report should preserve these
fields when practical:

```json
{
  "array_dtype": "float32",
  "value_range": "0..1",
  "data_domain": "linear|nonlinear|display_preview|mask|unknown",
  "channel_order": "RGB|BGR|gray|alpha|unknown",
  "bit_depth_source": "float32|uint16|uint8|fits_bitpix_-32|unknown",
  "is_preview": false
}
```

Hard rules:

- Linear FITS/XISF data must not be converted to `uint8` or display-stretched
  arrays for processing input.
- Do not round-trip very dark linear FITS/XISF data through OpenCV Lab for
  SCNR or calibration. Use float32 RGB channel arithmetic; Lab lookup-table
  precision can collapse broad background regions to one value and create
  contour bands after stretching.
- Display previews are review artifacts only and must never feed later
  processing stages.
- Masks are scalar float or boolean arrays; do not treat masks as luminance
  images.
- Integer conversion is allowed only for output, preview, or a documented
  external-algorithm boundary. StarNet2 is no longer such a boundary only
  when the active binary's machine-readable capabilities declare float FITS
  input and output:
  serialize the reversible-MTF RGB input as float32 FITS and require a
  BITPIX=-32 FITS result before inverse MTF. Retain the float32 source and use
  uint16 TIFF whenever that runtime capability is not explicitly declared;
  never infer float FITS support from the version string alone.
- If an operation needs 8-bit or 16-bit integer input, keep the original float
  source and mark the result with `precision_reduced_for_algorithm=true`.
- DeepSNR 1.2.3+ is not an integer-only boundary. For both linear `pre` and
  nonlinear `final` stages, capability-gated runs must exchange float32 FITS
  and require a float32 `BITPIX=-32` FITS result. Only legacy capability
  fallback may use uint16 TIFF, with
  `precision_reduced_for_algorithm=true`.
- Master outputs should preserve float TIFF/FITS when requested. JPG/PNG are
  display deliverables, not scientific intermediates.

## OpenCV rules

Project-internal arrays are RGB-oriented float arrays unless a function
documents otherwise. OpenCV boundaries must explicitly handle BGR/RGB
conversion.

- All `cv2.imread` calls must check for `None`.
- Direct OpenCV reads of 16-bit or float files must use `cv2.IMREAD_UNCHANGED`
  unless the caller explicitly wants an 8-bit preview.
- OpenCV I/O should record `channel_order="BGR"` at read time and
  `channel_order="RGB"` after conversion.
- Prefer OpenCV/NumPy vectorized operations over Python pixel loops, but never
  trade away faint-signal preservation for speed.

### Inpaint limits

Telea and Navier-Stokes inpainting may be used only for bounded star-mask
repair. They must not synthesize missing nebula, dust, stars, or catalog
predicted structures.

| Check | Default limit | Action when exceeded |
|---|---:|---|
| `star_mask_confidence` | `>= 0.30` | prefer external starless or review |
| full-frame `mask_coverage_ratio` | `<= 0.08` for nebula/galaxy, `<= 0.03` for clusters | review required |
| largest component area | `<= 1.5%` of frame | reject as likely non-star structure |
| edge-touching masked area | `<= 20%` of total mask | review crop/edge artifact first |
| inpaint radius | default `<= 5 px`; `> 8 px` requires review | review required |
| post-inpaint local difference | inside dilated mask should match neighboring background statistics | review if outlier |

These are conservative defaults. Target-specific flows may tighten them, but
automatic processing should not loosen them without recording the reason.

### Edge diagnostics

Canny, Sobel, contour count, and `edge_energy_delta` are risk indicators only.
They may support warnings such as halo risk, hard-edge risk, ringing risk, or
over-sharpening risk. They must not be reported as recovered real detail unless
the structure is visible in the linear or pre-enhancement source.

## scikit-image rules

scikit-image is the scientific image-analysis and measurement layer.

Required state labels:

| State | Meaning | Allowed CV operations |
|---|---|---|
| `linear` | unstretched sensor/stack data, possibly normalized with scale metadata | diagnostics, conservative denoise, star metrics, masks, DBE support |
| `nonlinear` | stretched processing image | style, local contrast, final denoise, artifact checks |
| `display_preview` | clipped/gamma/display-safe review image | visual review only |
| `mask` | scalar/boolean selection map | morphology, coverage, component stats |
| `master_output` | float TIFF/FITS or high-quality deliverable | final write/review only |

Do not feed `img_as_ubyte()` output back into processing steps. Do not use
preview PNG/JPG files as evidence for faint signal, SNR, FWHM, color truth, or
texture existence.

### Coordinates

scikit-image uses `(row, col)`. Display, OpenCV, WCS, crop arguments, and
user-facing pixels usually use `(x, y)`.

New JSON fields must encode convention:

| Field suffix | Convention |
|---|---|
| `_rc` | `[row, col]`, array indexing / scikit-image |
| `_xy` | `[x, y]`, display / OpenCV / user-facing pixels |
| `_norm_xy` | normalized `[x / width, y / height]` |
| `_bbox_rc` | `[row_min, col_min, row_max, col_max]` |
| `_bbox_xywh` | `[x, y, width, height]` |

Avoid generic names such as `center`, `bbox`, `point`, or `pixel` in new JSON
contracts unless the parent schema explicitly defines the convention.

### Object-level metrics

Useful measurements:

- connected component count;
- area and equivalent diameter;
- perimeter and circularity;
- eccentricity or axis ratio;
- bounding-box fill ratio;
- edge-contact ratio;
- mask coverage ratio;
- center-vs-corner spatial distribution.

Very high component count after thresholding usually indicates noise,
compression, or residual gradient. A sudden component-count increase after
sharpening, CLAHE, or local enhancement is an artifact risk. Component count
must be paired with area distribution, circularity/eccentricity, and
source-stage comparison before being interpreted.

Object-level metrics are diagnostics. They can say "mask likely overbroad",
"star residual risk", "halo risk", or "edge artifact risk". They must not say
"real detail increased", "nebula structure recovered", or "resolution improved"
without support from linear data, WCS/pixel scale, and visual review.

## CLAHE and local contrast

CLAHE and local contrast can reveal faint structure but can also create halos,
hard transitions, plastic texture, and false-looking high-frequency detail.

- Do not apply CLAHE to display previews and reuse the result for processing.
- Do not apply aggressive CLAHE before star/mask detection unless the mask is
  explicitly marked as preview-only.
- If `high_frequency_energy_ratio` rises while local SNR or source-stage
  structure support is absent, report artifact risk rather than detail gain.
- Any visible halo along mask or luminance boundaries requires review or
  parameter rollback.

## Linear vs nonlinear boundaries

| Stage | Allowed CV use | Restricted CV use |
|---|---|---|
| Linear input | FITS/XISF diagnostics, background models, star FWHM, conservative denoise, mask seeds | CLAHE/style/local contrast as processing input |
| Safe preview | AI/human visual review, channel inspection | downstream processing or metric truth |
| Linear starless/star layer | star mask confidence, inpaint validation, layer difference checks | broad inpaint, texture creation, aggressive sharpening |
| Nonlinear image | final color/style/local contrast, artifact detection, JPG review | physical SNR claims, true resolution claims |
| Final output | quality gates, visual review, artifact warnings | sensor/stack quality claims |

When a CV metric is computed on nonlinear or preview data, label it as
aesthetic or artifact-review evidence, not physical evidence.

## DeepSNR and external denoise guardrails

DeepSNR and other AI denoise outputs are treated as statistical estimates, not
new evidence. The pipeline may use them only after deterministic validation:

- Input/output shape and channel count must match exactly after documented
  grayscale/RGB handling.
- DeepSNR 1.2.3+ FITS output must be a standard image HDU with
  `BITPIX=-32`; grayscale is 2D and RGB is a simple 3-channel cube. Invalid
  sample type, layout, non-finite values, or material range overflow is a CLI
  failure before the perceptual quality gate.
- Outputs with NaN/Inf, large luminance drift, channel mean drift, negative
  noise reduction, low detail retention, or low edge retention are rejected.
- Flat or low-edge backgrounds may pass only when drift and noise-increase
  checks remain safe.
- Rejected DeepSNR output must fall back to internal denoise and mark
  `review_required`; it must not silently replace the image.
- Accepted DeepSNR output suppresses later internal denoise on the same path to
  avoid double smoothing.
- External denoised images provided by the user take precedence over DeepSNR
  and internal final denoise.

## Target-type safety rules

| Target type | Safety rule |
|---|---|
| Emission nebula | Protect filamentary H-alpha/OIII structure from star-mask overreach; inpaint masks crossing bright filaments require review. |
| Reflection nebula | Avoid aggressive CLAHE and chroma denoise that can destroy soft dust gradients; edge-energy increases are usually risk signals. |
| Galaxy | Do not classify dust lanes, knots, or core structures as stars/noise; largest connected components near the core require review. |
| Globular/open cluster | Stars are the subject; star removal/reduction masks should be disabled or extremely conservative. DeepSNR auto-denoise is disabled unless explicitly forced. Mask coverage limit defaults to `<= 0.03`. |
| Dark nebula | Do not treat dark dust lanes as background defects; DBE, thresholding, and local contrast need source-stage comparison. |
| Planetary nebula | Small high-gradient structure is often real; edge/contour metrics must not trigger automatic smoothing without review. |
| Wide-field | Gradients and dense star fields make thresholding fragile; require stronger component filtering and avoid broad inpaint. |

If target type is unknown, use conservative nebula/galaxy limits and require
review before broad inpaint, aggressive CLAHE, or star reduction.
