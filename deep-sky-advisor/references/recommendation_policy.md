# Recommendation policy

> **Authoritative for POLICY only.** This file defines decision states, evidence
> requirements, parameter rules, phase ownership, and the dual-track output contract.
>
> It is NOT authoritative for operational content. Per-application tools, steps,
> parameter logic, and mask strategy live in `scripts/software_guidance.py`;
> per-operation purpose, starting point, adjustment, acceptance, and rollback live
> in `scripts/generate_advice.py`. Editing this file does not change the generated
> report's operational content.

Use this reference when converting `*_analysis.json` into processing instructions.

## Contents

- Decision states
- Evidence requirements
- Parameter rules
- Stage rules
- Target and filter safety
- Acceptance and rollback
- Phase ownership
- Dual-track output
- Downstream finishing stage

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

Stage semantics — what each stage means, what belongs to it, its entry condition, and its
stage-level completion and rollback criteria — are in `references/pipeline_stages.md`. The rules
below are about *what to recommend* given the stage the file is in.

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

## Phase ownership

Every operation belongs to exactly one phase. The phase decides which software may own it.

| phase | meaning | Siril | PixInsight | Photoshop |
|---|---|---|---|---|
| `linear` | calibration, registration, stacking, crop, background modelling, color calibration, narrowband mapping, linear denoise, star-shape diagnosis | yes | yes | **no** |
| `nonlinear` | controlled stretch, highlight protection, star treatment | yes | yes | fine-tuning only |
| `finishing` | refinement of the primary track's output. Currently implemented: `color_refinement` (residual color cast). Reserved for local contrast and output-grade noise reduction/sharpening | optional | optional | yes |
| `export` | master preservation and delivery export | yes | yes | yes |

Photoshop must never own a `linear` phase operation. It cannot calibrate, register, stack, model the
background, or perform photometric color calibration, and it must not be used to fabricate the
result of those steps. Linear work belongs to a primary track; Photoshop only finishes what the
primary track produced.

## Dual-track output

The report presents one shared diagnostic layer and two parallel primary tracks (Siril and
PixInsight by default):

- the shared layer — evidence, purpose, acceptance checks, rollback conditions — is computed once
  and is identical for every track;
- each operation carries a per-software `implementations` map, and a track expands only the
  operations whose phase it owns;
- the two tracks are **alternatives, not consecutive steps**. State this explicitly and never
  instruct the user to run both.

For each track, every expanded operation must provide:

- key tools or process entry points;
- one signature tool used by the cross-track comparison table;
- ordered execution steps;
- parameter-selection logic tied to evidence;
- mask or protection strategy;
- stage checkpoints;
- visible failure signs and rollback conditions.

Also emit a cross-track comparison table pairing the signature tool of each track per operation, so
the user can see which step in one application corresponds to which step in the other.

## Downstream finishing stage

Photoshop is a downstream stage, never a third parallel option. Its section must contain:

- a prerequisite checklist derived from the primary tracks' `linear` operations — the steps that
  must already be complete upstream;
- the operations Photoshop actually owns, with executable finishing instructions. Only operations
  with a `finishing` or `export` phase and a `review`/`recommend` decision belong here, and they
  must be framed as finishing what the primary track produced;
- a short table of the operations Photoshop must not perform, with the upstream destination;
- a handoff contract recording bit depth and format, color space and conversion method, whether
  photometric color calibration was completed, whether a narrowband mapping was used, whether star
  separation was performed, and the irreversible operations that remain forbidden.

A Photoshop section that only says "return to the upstream application" is incomplete and must not
be emitted.

Summarize skipped operations instead of emitting full instructions for them. Do not duplicate the
shared diagnostic layer inside each track.
