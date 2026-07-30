# Recommendation policy

Use this reference when converting `*_analysis.json` into processing instructions.

## Contents

- Decision states
- Evidence requirements
- Parameter rules
- Stage rules
- Target and filter safety
- Acceptance and rollback

## Decision states

- `recommend`: enough evidence exists to include the operation in the default sequence.
- `review`: evidence indicates a possible need, but visual or external confirmation is required.
- `skip`: current evidence does not justify the operation or a safety rule prohibits it.

Never present `review` as an automatic action.

## Evidence requirements

Every `recommend` or `review` operation must cite one or more exact JSON paths from the analysis
report or explicit user context.

Valid examples:

- `background.plane.magnitude_across_frame`
- `noise.background_noise_sigma_normalized`
- `stars.fwhm_major_median_px`
- `classification.processing_stage`

Do not cite a preview impression as measured evidence. Record it separately as visual evidence.

## Parameter rules

Use one of:

- `qualitative`: no numeric value is justified; describe direction and inspection criteria.
- `evidence_bound`: derive a relative value from a measured field, such as mask scale relative to
  measured FWHM.
- `unavailable`: required evidence does not exist.

Do not emit an `exact` parameter mode. Fixed software presets are not evidence.

When using `evidence_bound`, include the source JSON path and state the measurement limitation.

## Stage rules

- Calibration frames: recommend calibration use, not aesthetic post-processing.
- Unintegrated light frame: prioritize calibration, registration, subframe evaluation, and
  integration; stop before final stretch advice.
- Integrated likely-linear master: background review, color/channel work, optional linear
  denoise, stretch, target-safe finishing.
- Unknown stage: request confirmation and avoid irreversible stage-dependent operations.
- Already nonlinear image: do not recommend linear-only operations as though the data were linear.

## Target and filter safety

- Emission nebula, dark nebula, IFN, reflection nebula, supernova remnant, and wide field:
  background correction always requires model inspection.
- Globular cluster, open cluster, and M45: skip star removal and global star reduction.
- Narrowband/dual-band: do not recommend broadband white balance for nebular emission.
- Galaxy: protect outer halo, tidal features, and bright core.
- Planetary nebula: protect the central star and shell transitions.

## Acceptance and rollback

Every `recommend` or `review` operation must contain:

- at least one acceptance check describing a successful result;
- at least one rollback condition identifying lost signal, artifacts, clipping, color damage, or
  target-specific failure.

Advice without acceptance and rollback criteria is incomplete.

## Software-specific output

For the selected application, every expanded operation must provide:

- key tools or process entry points;
- ordered execution steps;
- parameter-selection logic tied to evidence;
- mask or protection strategy;
- stage checkpoints;
- visible failure signs and rollback conditions.

Do not repeat full Siril, PixInsight, and Photoshop workflows in one report. Expand only the
software selected by the user. Summarize skipped operations instead of emitting full instructions
for them.
