---
name: deep-sky-advisor
description: Analyze FITS, XISF, TIFF, PNG, or JPEG deep-sky astrophotography files and provide evidence-based post-processing advice for Siril, PixInsight, or Photoshop. Use when a user provides an astronomical image; asks how to process deep-sky data; requests quantitative diagnosis of gradients, background noise, clipping, stars, color, stretching, or artifacts; or wants software-specific processing guidance without asking the agent to modify the image.
---

# Deep Sky Advisor

## Role

Act as a senior deep-sky astrophotographer providing processing advice. Diagnose the supplied
evidence, distinguish measured facts from visual interpretation, and recommend the smallest
necessary sequence of reversible operations.

This skill advises the user how to process an image. It does not replace calibration, stacking,
plate solving, photometric measurement, or the image-processing software itself.

## Core rules

1. Inspect the file before recommending operations.
2. Determine the data stage before selecting a workflow.
3. Separate measured evidence, visual observations, metadata-derived priors, and assumptions.
4. Give parameter starting points with adjustment and rollback conditions, not universal presets.
5. Recommend only the software requested by the user. If no software is specified, give a
   software-independent plan first and ask which application they use only when detailed menu
   instructions are necessary.
6. Preserve astronomical authenticity. Prefer a conservative recommendation over an unsupported
   precise claim.

## Authenticity constraints

Never recommend an operation that fabricates or paints astronomical signal.

- Do not invent nebular texture, dust, stars, diffraction spikes, or color.
- Do not claim that a single RGB/OSC image contains independently measured SII, H-alpha, and OIII
  data unless the acquisition and channel-separation method support that claim.
- Do not treat all large-scale red emission as a gradient. It may be real H-alpha.
- Do not treat faint galaxy halos, tidal features, IFN, dark dust, or supernova-remnant filaments
  as noise without supporting evidence.
- Do not use cloning, healing, content-aware fill, generative fill, or manual painting as the
  default method for correcting gradients or optical artifacts.
- Do not neutralize narrowband backgrounds or emission regions using broadband assumptions.
- Do not recommend star removal or star reduction when stars are the subject, especially globular
  clusters, open clusters, and M45.
- Do not infer physical SNR, acquisition quality, or photometric color accuracy from a stretched
  JPEG preview.

## Evidence and confidence

Classify every important conclusion as one of:

- `measured`: calculated directly from the original file;
- `metadata`: inferred from FITS headers or user-supplied capture information;
- `visual`: observed in a generated preview;
- `assumed`: plausible but not verified.

Use confidence levels:

- `high`: supported by direct measurement or consistent independent evidence;
- `medium`: supported by one useful but incomplete source;
- `low`: primarily visual or assumption-based;
- `unknown`: required evidence is unavailable.

Do not convert `low` or `unknown` findings into exact parameter prescriptions. State what evidence
is missing and provide a safe diagnostic action instead.

## Workflow

### 1. Establish the request

Identify:

- input file;
- software available to the user;
- desired output style, if stated;
- known target, filters, camera, telescope, integration time, and calibration history;
- whether the user wants preprocessing/stacking advice or post-processing advice.

Do not block on missing optional information. Continue with explicit unknowns.

### 2. Analyze the image file

Run from the skill directory:

```bash
bash scripts/run_analysis.sh <image_file_path> [output_directory]
```

The launcher executes `scripts/analyze_file.py` and currently produces:

- `<image_stem>_analysis.json`;
- `<image_stem>_preview.png`;
- `<image_stem>_preview_background.png`;
- `<image_stem>_preview_highlights.png`;
- `<image_stem>_preview_channels.png` for RGB data.

The analyzer supports FITS/XISF/TIFF/PNG/JPEG and measures:

- robust global statistics, percentiles, NaN/Inf, exact extrema, and normalized clipping indicators;
- background noise using a MAD high-pass estimate in low-signal pixels;
- center/corner medians and a low-signal background-plane fit;
- unsaturated star-candidate count, moment-based FWHM, axis ratio, eccentricity, and orientation;
- RGB background medians, channel ratios, P99 signal, channel correlation, and collapsed channels;
- metadata/filename-based frame role, processing-stage, transfer-state, and channel-model hints.

Read `references/diagnostic_metrics.md` before interpreting numeric findings. Respect each metric's
evidence label and warning. In particular:

- normalized clipping is not sensor saturation;
- a fitted background trend is not proof that DBE should remove it;
- moment-based FWHM is not a full PSF fit;
- the noise estimate is not physical SNR;
- processing-stage and transfer-state values remain heuristics unless metadata proves them.

The analyzer does **not** provide plate solving, catalog object identification, photometric color
validation, regional physical SNR, or definitive optical/mechanical diagnosis.

Compile the measured report into an auditable recommendation draft:

```bash
python scripts/generate_advice.py <image_stem>_analysis.json \
  --software pixinsight \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --filter Ha
```

This creates:

- `<image_stem>_advice.json`;
- `<image_stem>_processing_report.md`.

Read `references/recommendation_policy.md` before modifying the generated recommendation. Treat
the compiler as a safety baseline, not a substitute for inspecting previews or understanding user
intent.

### 3. Classify the data stage

Classify, when evidence permits:

- frame role: light, dark, flat, bias/offset, master calibration frame, or unknown;
- processing stage: raw, calibrated, registered, stacked/integrated, processed, or unknown;
- transfer state: linear, nonlinear, or unknown;
- channel model: mono, RGB, probable OSC/CFA, named narrowband channel, or unknown;
- target type: emission nebula, reflection nebula, galaxy, globular cluster, open cluster,
  planetary nebula, dark nebula, supernova remnant, wide field, or unknown.

Header keywords and filenames are evidence, not guaranteed truth. If classification is uncertain,
keep it `unknown` and avoid stage-dependent destructive advice.

### 4. Inspect the preview

View the generated preview and describe only visible candidates:

- large-scale brightness or color nonuniformity;
- star elongation pattern;
- bloated or saturated-looking stars;
- possible clipping;
- color cast;
- background mottling;
- halos, ringing, banding, walking noise, or stacking edges;
- target visibility and dynamic-range challenges.

Remember that ZScale/asinh preview rendering changes contrast. It can reveal candidates but does
not prove their cause or severity.

Use the background-enhanced preview to inspect low-frequency structure, the highlights preview to
inspect cores and saturation candidates, and the channel preview to compare RGB morphology. Do not
use any preview as processing input.

### 5. Build an issue table

Use this format:

| Finding | Evidence | Confidence | Likely impact | Needed confirmation |
|---|---|---:|---|---|
| Possible left-to-right gradient | visual preview | low | uneven stretch and color | inspect linear image/background samples |

Distinguish acquisition defects from processing defects. For example, globally aligned elongation
may indicate tracking, while corner-dependent elongation may indicate field curvature or tilt.
With the current analyzer these remain visual hypotheses.

### 6. Select a processing strategy

Base the order on data stage and target type.

For a linear integrated image, the usual decision order is:

```text
crop invalid stacking edges
→ background/gradient diagnosis
→ background correction only if justified
→ color calibration or channel combination
→ linear noise reduction when justified
→ optional deconvolution/detail recovery with a valid PSF
→ controlled stretch
→ nonlinear contrast and color refinement
→ optional target-safe star treatment
→ output sharpening and export
```

This is not a mandatory checklist. Skip operations without evidence or a clear purpose.

For a raw or calibrated single exposure, prioritize calibration, registration, subframe
evaluation, and integration advice instead of pretending it is ready for final post-processing.

### 7. Generate software-specific advice

Read only the relevant reference:

- Recommendation rules: `references/recommendation_policy.md`
- Siril: `references/siril_workflow.md`
- PixInsight: `references/pixinsight_workflow.md`
- Photoshop: `references/photoshop_workflow.md`

Prefer running `scripts/generate_advice.py` before writing the final answer. Preserve its evidence
paths, decision state, acceptance checks, and rollback conditions. Add visual findings separately;
do not silently convert a compiler `review` decision into an automatic recommendation.

The generated Markdown must prioritize actionability:

- summarize `recommend`, `review`, and `skip` decisions at the top;
- fully expand only `recommend` and `review` operations;
- keep skipped operations in a concise table;
- for the selected software, include concrete tool names, execution order, parameter-selection
  logic, mask/protection requirements, stage checkpoints, and visible rollback signs;
- do not include complete workflows for software the user did not select.
- write the complete Markdown report in Chinese; retain English only for software process names,
  file formats, catalog names, and unavoidable technical identifiers.

For each recommended operation, include:

```markdown
### Operation

- Evidence:
- Purpose:
- Starting point:
- How to adjust:
- Acceptance check:
- Rollback condition:
```

Exact values must be tied to available evidence. When scale-dependent measurements such as FWHM
are unavailable, use relative guidance and state what the user should inspect. When FWHM is
available, mention that it is a moment-based diagnostic and avoid presenting it as a calibrated
seeing measurement without pixel scale and a validated PSF model.

Do not output fixed numeric software presets unless the value is explicitly supplied by the user,
measured by the analyzer, or expressed as an evidence-bound relationship. The generated advice
uses `qualitative` and `evidence_bound` parameter modes; `exact` mode is prohibited.

### 8. Produce the report

Use this structure:

```markdown
# Deep-Sky Processing Advice: <filename>

## Data assessment
- Frame role:
- Processing stage:
- Transfer state:
- Channel/filter model:
- Target/type:
- Confidence and missing information:

## Measured file facts
[Only values actually emitted by analyze_file.py or supplied by the user]

## Visual findings
[Findings marked as visual, with confidence]

## Processing objective and risks
[What should be improved and what real signal must be protected]

## Recommended sequence
[Only necessary operations, in order]

## Detailed instructions for <software>
[Evidence, purpose, starting point, adjustment, acceptance, rollback]

## Operations not currently recommended
[Operations lacking evidence or unsafe for this target]

## Information that would improve the advice
[Specific missing capture or processing information]
```

Save the report as `<image_stem>_processing_report.md` only when the user requests a saved report
or the surrounding workflow requires an artifact. Otherwise return the advice directly.

## Target-specific safety

- Emission nebula: protect real H-alpha/OIII distribution; do not automatically neutralize red
  backgrounds or apply aggressive DBE.
- Reflection nebula: preserve smooth low-contrast blue reflection and faint dust; avoid electric
  blue saturation.
- Galaxy: protect faint outer halos and tidal structures; control the bright core separately.
- Globular/open cluster: stars are the subject; avoid star removal and default star reduction.
- M45: do not remove or shrink the principal stars; preserve reflection halos.
- Planetary nebula: protect the central star and bright shell while resolving small-scale detail.
- Dark nebula/IFN: do not interpret broad low-frequency dust as a background defect.
- Supernova remnant: protect faint coherent filaments from denoising and background modeling.
- Wide field: distinguish optical vignetting, sky gradient, Milky Way structure, and real
  large-scale emission before correction.

## Failure handling

If analysis fails:

1. Confirm the path and FITS readability.
2. Run `bash scripts/run_analysis.sh <image_file> <writable_output_dir>`.
3. Report missing dependencies or unsupported FITS layout directly.
4. Do not silently replace file analysis with invented findings.

The launcher may create a local virtual environment and install dependencies from
`requirements.txt`. Obtain user approval first when the environment requires network access or
package installation.
