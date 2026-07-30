# Agent-in-the-loop protocol

Use this protocol when the model should direct processing across multiple
visual review rounds instead of planning once and judging only the final image.

## Recognition evidence order

FITS/XISF recognition uses this fixed order:

1. Header and WCS evidence.
2. Numeric diagnostics on the original linear file.
3. A display-only safe preview with zero shadow percentile clipping.
4. An explicit AI visual review result.
5. Local CV as auxiliary validation only.

The safe preview never becomes a processing input. If visual review is
unavailable, the workflow remains `awaiting_ai_visual_review`. Local CV cannot
silently override a target identified by Header or WCS; conflicts are recorded
and routed to human review.

## Session workflow

1. Initialize a session with `agent_workflow.py init`.
2. Inspect `analysis.json` and `session.json`.
3. Submit one `run_step` action or request 2–5 low-cost variants.
4. Inspect the generated before/after/difference previews and `review.json`.
5. Return a structured verdict with executable actions.
6. Accept, retry, replace the step, or roll back.

Do not accept an artifact while `pending_review` is set unless the visual
evidence has been inspected.

## Action schema

```json
{
  "operation": "run_step",
  "step": "stretch",
  "intent": {
    "background": "slightly_darker",
    "subject_visibility": "increase_slightly",
    "core_protection": "strong",
    "noise_tolerance": "preserve_detail"
  },
  "params": {
    "target_bg": 0.075
  }
}
```

Allowed operations:

- `run_step`
- `create_variants`
- `masked_adjustment`
- `accept`
- `rollback`
- `request_human_review`

Use semantic `intent` first. Use `params` only for a justified override.

For a local step such as `local_enhance`, identify the region with normalized
coordinates. Code converts it to deterministic pixel coordinates and generates
the local mask:

```json
{
  "operation": "run_step",
  "step": "local_enhance",
  "region": {
    "bbox": [0.35, 0.28, 0.30, 0.34]
  },
  "local_strength": 0.24
}
```

Do not generate bitmap masks with the model.

Use `masked_adjustment` for signal-specific changes. Define the selection with
the recursive mask DSL in `references/mask_workflow.md`; inspect the generated
mask preview before accepting the result.

If plate solving and catalog association are available, select a catalog object
without manually guessing image coordinates:

```json
{
  "operation": "run_step",
  "step": "local_enhance",
  "object_name": "NGC6888",
  "object_radius": 0.12,
  "local_strength": 0.2
}
```

## Variant schema

```json
{
  "operation": "create_variants",
  "step": "stretch",
  "variants": [
    {"id": "conservative", "params": {"target_bg": 0.06}},
    {"id": "balanced", "params": {"target_bg": 0.08}},
    {"id": "bright", "params": {"target_bg": 0.11}}
  ]
}
```

Rank candidates by visual evidence. Do not compare them only by numeric metrics.

## Review schema

```json
{
  "verdict": "retry",
  "issues": [
    {
      "code": "CORE_OVERSTRETCHED",
      "severity": "high",
      "region": "nebula_core"
    }
  ],
  "actions": [
    {
      "operation": "run_step",
      "step": "stretch",
      "params": {"ghs_protect_strength": 0.75}
    }
  ]
}
```

Allowed verdicts are `accept`, `retry`, `rollback`, and `review_required`.

## Evidence requirements

For every processed candidate inspect:

- before preview;
- after preview;
- absolute difference;
- signed luminance difference;
- quantitative metrics and quality gates;
- target-specific critic checklist.

Treat numeric gates as review triggers, not proof of visual quality. The
authenticity critic has veto power over the aesthetic critic.
