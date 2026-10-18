# Computer Vision Guardrails
## 内容导航

- [Mandatory image state](#mandatory-image-state)
- [OpenCV rules](#opencv-rules)
- [scikit-image rules](#scikit-image-rules)
- [CLAHE and local contrast](#clahe-and-local-contrast)
- [Linear vs nonlinear boundaries](#linear-vs-nonlinear-boundaries)
- [DeepSNR and external denoise guardrails](#deepsnr-and-external-denoise-guardrails)
- [Paired star artifact attribution](#paired-star-artifact-attribution)
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
- DeepSNR pre-denoise requires CLI 1.2.3+ with explicitly declared float32
  FITS input/output capabilities. Exchange float32 FITS with zero-shadow,
  reversible MTF and require a standard `BITPIX=-32` result. Unsupported
  runtimes fall back to internal Starlet, without integer transport.
- CLI 1.3.2+ uses the standard FITS RGB contract (verified on local 1.3.2
  CoreML); no per-image calibration inference is needed. Legacy versions
  require a known constant color probe, never a guess from the target's colors.
  Record the order source and reverse legacy BGR output before inverse MTF.
- Do not enable 1.3.2 native `--linear` by default: its capability declaration
  states that clipped source samples are not restored. Keep the zero-shadow
  external MTF bridge to avoid irreversible shadow clipping.
- Master outputs should preserve float TIFF/FITS when requested. JPG/PNG are
  display deliverables, not scientific intermediates.

## Paired star artifact attribution

`artifact_gates.py` uses reference star positions and a shared neighbor mask.
A clipped local plane replaces the scalar outer-annulus background. The p90
absolute residual over all unmasked fit samples remains an uncertainty measure,
including samples excluded from the clipped fit. A curved filament must not
become a reliable background solely because clipping removed it.

- `STAR_RINGING` requires absolute depth and paired depth increment to each
  exceed `max(0.003, 0.12 * local_background)`. `STAR_HOLES` uses
  `max(0.003, 0.20 * local_background)` for both depths. The failed count remains
  `max(2, ceil(0.08 * valid_samples))`; edge and unreliable samples are excluded.
- Fit the candidate-minus-reference background on identical samples. This
  cancels inherited field structure without fitting away a new inner deficit.
  `reference_dip_abs`, `added_dip_abs`, and `delta_background_error` describe
  each suspect at the same radius or stellar center.
- Inherited deficits and unreliable absolute backgrounds with a reliable,
  unchanged paired contrast remain diagnostic warnings with `escalate=false`.
  New deficits and uncertain paired backgrounds retain escalation. Random
  background hit rates or starless-layer correlation do not exempt a frame.
- Paired attribution requires aligned, equal-shape images in the same intensity
  domain. Agent steps carry `input_is_linear` and `output_is_linear`; resizing
  review previews does not establish geometric comparability. Known domain or
  geometry mismatches require review and cannot be labeled preexisting.
- The half-height connected stellar core supplies a diagnostic sampling width
  when available, avoiding moment inflation from faint filaments. It is not a
  physical PSF or resolution measurement. Explicit `fwhm` inputs remain valid.
- Pipeline reports label the reference as `post_star_combine` and the candidate
  as `final`. This pair covers terminal processing only. DeepSNR's saved
  pre-denoise before/after/residual artifacts remain the evidence for that stage;
  do not compare a linear pre-denoise image directly with a stretched final image.

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
- Outputs with NaN/Inf, large luminance drift, channel mean drift, increased
  background noise, low detail retention, or low edge retention are rejected.
  High-frequency energy loss alone is not a rejection: noise removal lowers it.
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

## 星点处理引擎 v2 实现细节（`scripts/star_tools.py`）

### 多尺度检测

- 自动从图像估计 FWHM（2D 高斯拟合亮星候选，MAD 剔除异常值）
- 基于 FWHM 生成 4 个尺度的结构元素：小星(0.4x)、中星(0.7x)、大星(1.1x)、星芒(1.8x)
- 各尺度独立 White Top-hat 检测，合并响应

### 连通域特征过滤

对每个检测到的亮结构计算面积、等效直径、圆度(4πA/P²)、峰值亮度、长宽比、包围盒填充率。

自动过滤：

- **热像素**：面积 < π(0.3·FWHM)²
- **星云亮核**：面积 > π(4·FWHM)² 且圆度 < 0.25
- **噪声**：峰值 < 0.3·阈值
- **星云细丝**：长宽比 > 5 且圆度 < 0.3

### 梯度感知阈值

计算 Sobel 梯度幅值；在星云高梯度区域（亮丝、边缘）自动降低检测响应，避免误检。

### 修复方法

- **默认**：OpenCV Telea 快速行进法（小/中星点）
- **大区域/星芒**：自动降级为 Navier-Stokes 流体动力学修复
- **OpenCV 不可用时**：回退到高斯模糊修复

### 置信度系统

`detect_stars_multiscale()` 返回 `confidence`（0-1），基于保留比例、圆度一致性、
尺寸一致性、密度合理性、热像素比例综合评分。`separate_stars()` 默认
`min_confidence=0.3`；置信度 < 0.3 时输出警告，建议改用外部 StarNet++。

### CLI 使用

```bash
# 检测并输出掩膜 + 详情
python3 scripts/star_tools.py detect input.jpg mask.tif --details

# 分离星点（使用 Telea 修复）
python3 scripts/star_tools.py separate input.jpg starless.tif --method telea

# 使用旧版单尺度检测
python3 scripts/star_tools.py separate input.jpg starless.tif --legacy
```

## FITS I/O 归一化与输出精度契约（`scripts/fits_io.py`）

### 输入：不使用百分位裁剪

旧版用 `p0.1-p99.9` 百分位裁剪归一化，会截断 p0.1 以下的暗部信号为 0、把 p99.9 以上
的亮部（亮星核心、星云饱和区）压到 1，原始线性范围与测光信息丢失。现行行为：

- BSCALE/BZERO 由 astropy 在 `fits.open()` 阶段按 FITS 标准自动应用（含
  `BZERO=2**(BITPIX-1)` 的伪无符号 uint16 约定），**管线不再手动叠加**；header 原值
  仅记录在 `scale_info` / `meta` 中供溯源。
- 使用绝对值最大值归一化 `data = data / max(|min|, |max|)`，保留负值（噪声可以有负值），
  完整动态范围进入管线，不做任何截断。
- 归一化因子 `data_scale` 记录在 meta 中，供输出时恢复。

### DBE 阶段的归一化

黑点取**背景稳健中心**（逐通道 DBE 后背景以 0 为中心、噪声对称），白点**锚定真实峰值**，
并保留一个 3σ 的 pedestal 让背景噪声完整通过。

- 旧的 `p99.7` 白点对「亮核很小、暗晕很大」的天体是灾难：该分位落在很暗的外缘
  （实测 M31 的 p99.7 比峰值低 36.6 倍），等于把整幅图放大 ~37 倍 —— 噪声被放大到与
  星点可比，星点检测会把噪声当成星。
- 旧的黑点是「正值像素的 p0.3」，等于在 `clip(0)` 之后再抬一次黑位，会把噪声底整个裁掉
  （实测 28~44% 画面变纯 0）。
- 配置项 `dbe_pctl_low` 已不再作为黑点使用（保留仅为兼容既有配置）；`dbe_pctl_high`
  仅作为白点的下界保留。

### 输出：float32 替代 uint16

旧版把 `[0,1]` float 乘 65535 转 uint16，丢失 32-bit 浮点精度、把非线性数据硬编码为
16-bit（语义不匹配），且 header 中的 BSCALE/BZERO 不再正确描述数据。现行行为：

- 输出 `BITPIX=-32`（IEEE float32），`BSCALE=1.0, BZERO=0.0`，header 语义一致。
- 添加 `HISTORY` 记录处理信息；可选恢复原始 `data_scale`（处理前线性数据时有用）。
- 中间 `.tif` 继续使用 float32；JPG/PNG 输出行为不变（仍基于 `[0,1]` 范围）。
