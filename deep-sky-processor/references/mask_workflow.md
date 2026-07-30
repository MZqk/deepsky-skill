# Intelligent multi-scale mask workflow

Use masks to constrain an adjustment to existing signal. The model specifies
semantic selection criteria; deterministic code creates and feathers the mask.

## Mask types

### Range mask

```json
{
  "type": "range",
  "low": 0.08,
  "high": 0.75,
  "feather": 0.03,
  "scales": [0, 2, 8]
}
```

Use `invert: true` to protect rather than select the range.

### Color mask

```json
{
  "type": "color",
  "preset": "oiii",
  "saturation_min": 0.12,
  "value_min": 0.03,
  "feather": 0.025
}
```

Built-in presets are `ha`, `oiii`, `blue`, and `green`. A custom normalized
HSV interval may be supplied as `hue_range: [low, high]`.

Color masks are reliable only after color calibration and when the signal has
enough saturation. Do not use hue selection to infer emission-line identity
from monochrome data.

### Star mask

```json
{
  "type": "star",
  "threshold": 0.85,
  "expand": 2,
  "scales": [0, 1.5, 4],
  "invert": true
}
```

This uses the FWHM-aware multi-scale detector. Inspect detection confidence and
mask coverage before accepting an adjustment.

### Combined mask

```json
{
  "type": "combine",
  "mode": "and",
  "masks": [
    {"type": "color", "preset": "oiii"},
    {"type": "range", "low": 0.06, "high": 0.8},
    {"type": "star", "invert": true}
  ]
}
```

Modes are `and`, `or`, and `subtract`.

## Masked adjustment

Example: enhance only OIII-colored shell signal while locking dark background
and protecting stars:

```json
{
  "operation": "masked_adjustment",
  "mask": {
    "type": "combine",
    "mode": "and",
    "masks": [
      {
        "type": "color",
        "preset": "oiii",
        "saturation_min": 0.1,
        "value_min": 0.025
      },
      {
        "type": "range",
        "low": 0.06,
        "high": 0.75,
        "feather": 0.025
      },
      {
        "type": "star",
        "threshold": 0.85,
        "expand": 2,
        "invert": true
      }
    ]
  },
  "adjustment": {
    "method": "arcsinh",
    "factor": 35,
    "strength": 0.8
  }
}
```

Supported adjustment methods:

- `arcsinh`
- `saturation`
- `local_contrast`
- `curves`

Always inspect `mask.jpg`, mask coverage, before/after previews, and difference
images. Reject near-empty masks, masks covering most of the frame without a
clear reason, hard boundaries, and masks driven mainly by background color
noise.
