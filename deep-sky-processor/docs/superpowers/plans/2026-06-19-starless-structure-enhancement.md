# Starless Multi-Scale Structure Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic CLI that enhances already-stretched starless TIFF/PNG images by target type, generates LOW/MEDIUM/HIGH candidates, processes an independently supplied StarNet stars layer for each candidate, recomposes final images, and enforces measurable quality gates.

**Architecture:** Keep the existing `compose_starnet_layers.py` behavior compatible while moving new behavior into focused modules: target profiles, diagnostics, mask generation, multiscale enhancement, stellar recomposition, quality gates, and CLI orchestration. All pixel transforms operate on float32 RGB arrays in `[0, 1]`; every candidate returns JSON-safe diagnostics, parameters, gates, and downgrade history.

**Tech Stack:** Python 3, NumPy, SciPy `ndimage`, scikit-image, tifffile, existing `fits_io.py`, pytest/unittest.

**Repository note:** `/Users/mz/dev/skills` is not a Git repository and the user explicitly requested no Git submission. This plan therefore uses verification checkpoints instead of commit steps.

---

## File Structure

Create these focused modules:

- `deep-sky-processor/scripts/starless_profiles.py`
  - Canonical target-type normalization.
  - Rejected target rules.
  - Per-target scale/color/safety profiles.
  - LOW/MEDIUM/HIGH candidate parameter expansion.
- `deep-sky-processor/scripts/starless_diagnostics.py`
  - Input validation.
  - Luminance/noise/background/color/structure diagnostics.
  - Suspected star-removal artifact map.
- `deep-sky-processor/scripts/starless_masks.py`
  - Subject, dark-structure, and protection masks.
  - Mask statistics and coverage warnings.
- `deep-sky-processor/scripts/starless_multiscale.py`
  - Adaptive scale selection.
  - Large/medium/small detail decomposition.
  - Bounded bright-structure, dark-structure, tone, and color fusion.
- `deep-sky-processor/scripts/stellar_recompose.py`
  - Stars-layer validation.
  - Per-candidate stellar processing.
  - Positive additive recomposition.
- `deep-sky-processor/scripts/starless_quality.py`
  - Baseline/candidate comparison.
  - Hard gates, review warnings, and one-pass parameter downgrade.
- `deep-sky-processor/scripts/enhance_starless.py`
  - CLI and orchestration.
  - Output directory and JSON report creation.

Modify:

- `deep-sky-processor/scripts/compose_starnet_layers.py`
  - Delegate stars processing and additive recomposition to `stellar_recompose.py`.
  - Preserve existing CLI defaults and `beautify_starless()` compatibility.
- `deep-sky-processor/SKILL.md`
  - Document the new stretched-starless workflow and rejection rules.
- `deep-sky-processor/references/external_tools.md`
  - Document StarNet stars-layer requirements and the new CLI.
- `deep-sky-processor/references/case_ngc6888_rgb.md`
  - Replace the old single-output recommendation with the new three-candidate workflow.

Create tests:

- `deep-sky-processor/tests/test_starless_profiles.py`
- `deep-sky-processor/tests/test_starless_diagnostics.py`
- `deep-sky-processor/tests/test_starless_masks.py`
- `deep-sky-processor/tests/test_starless_multiscale.py`
- `deep-sky-processor/tests/test_stellar_recompose.py`
- `deep-sky-processor/tests/test_starless_quality.py`
- `deep-sky-processor/tests/test_enhance_starless_cli.py`

---

### Task 1: Target Profiles and Rejection Rules

**Files:**
- Create: `deep-sky-processor/scripts/starless_profiles.py`
- Create: `deep-sky-processor/tests/test_starless_profiles.py`

- [ ] **Step 1: Write failing tests for profile normalization, rejection, and candidate ordering**

```python
# deep-sky-processor/tests/test_starless_profiles.py
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

from starless_profiles import (
    CandidateLevel,
    RejectedStarlessTarget,
    build_candidate_params,
    get_target_profile,
    normalize_target_type,
    validate_starless_target,
)


def test_normalize_galaxy_subtype_to_galaxy():
    assert normalize_target_type("spiral_galaxy") == "galaxy"
    assert normalize_target_type("barred galaxy") == "galaxy"


@pytest.mark.parametrize(
    ("target_type", "target_name"),
    [
        ("globular_cluster", None),
        ("open_cluster", None),
        ("star_cluster", None),
        ("reflection_nebula", "M45"),
        ("reflection_nebula", "Pleiades"),
    ],
)
def test_rejects_star_dominant_targets(target_type, target_name):
    with pytest.raises(RejectedStarlessTarget):
        validate_starless_target(target_type, target_name)


def test_emission_profile_prioritizes_medium_scale():
    profile = get_target_profile("emission_nebula")
    assert profile.scale_weights["medium"] > profile.scale_weights["large"]
    assert profile.scale_weights["medium"] > profile.scale_weights["small"]


def test_candidate_params_are_monotonic_and_bounded():
    profile = get_target_profile("emission_nebula")
    low = build_candidate_params(profile, CandidateLevel.LOW)
    medium = build_candidate_params(profile, CandidateLevel.MEDIUM)
    high = build_candidate_params(profile, CandidateLevel.HIGH)

    assert low.structure_multiplier < medium.structure_multiplier < high.structure_multiplier
    assert low.star_strength > medium.star_strength > high.star_strength
    assert high.structure_multiplier <= profile.max_structure_multiplier
    assert 0.0 <= high.saturation_gain <= profile.max_saturation_gain
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run:

```bash
cd /Users/mz/dev/skills/deep-sky-processor
.venv/bin/python -m pytest tests/test_starless_profiles.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'starless_profiles'`.

- [ ] **Step 3: Implement immutable profile and candidate types**

```python
# deep-sky-processor/scripts/starless_profiles.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RejectedStarlessTarget(ValueError):
    pass


class CandidateLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class TargetProfile:
    name: str
    scale_weights: dict[str, float]
    dark_structure_weight: float
    tone_strength: float
    saturation_gain: float
    max_saturation_gain: float
    max_structure_multiplier: float
    background_protection: float
    highlight_protection: float
    continuity_required: bool


@dataclass(frozen=True)
class CandidateParams:
    level: CandidateLevel
    structure_multiplier: float
    scale_weights: dict[str, float]
    dark_structure_weight: float
    tone_strength: float
    saturation_gain: float
    star_strength: float
    star_saturation: float
    star_softness: float


_ALIASES = {
    "spiral_galaxy": "galaxy",
    "barred_galaxy": "galaxy",
    "elliptical_galaxy": "galaxy",
    "irregular_galaxy": "galaxy",
    "molecular_cloud": "dark_nebula",
    "milky_way": "wide_field",
}

_REJECTED_TYPES = {"globular_cluster", "open_cluster", "star_cluster"}
_REJECTED_NAMES = {"M45", "PLEIADES", "PLEIADES CLUSTER"}

PROFILES = {
    "emission_nebula": TargetProfile(
        name="emission_nebula",
        scale_weights={"large": 0.75, "medium": 1.0, "small": 0.55},
        dark_structure_weight=0.35,
        tone_strength=0.16,
        saturation_gain=0.16,
        max_saturation_gain=0.28,
        max_structure_multiplier=1.35,
        background_protection=0.85,
        highlight_protection=0.75,
        continuity_required=True,
    ),
    "reflection_nebula": TargetProfile(
        name="reflection_nebula",
        scale_weights={"large": 1.0, "medium": 0.75, "small": 0.25},
        dark_structure_weight=0.30,
        tone_strength=0.12,
        saturation_gain=0.08,
        max_saturation_gain=0.16,
        max_structure_multiplier=1.20,
        background_protection=0.90,
        highlight_protection=0.80,
        continuity_required=False,
    ),
    "dark_nebula": TargetProfile(
        name="dark_nebula",
        scale_weights={"large": 0.90, "medium": 0.80, "small": 0.35},
        dark_structure_weight=1.0,
        tone_strength=0.10,
        saturation_gain=0.06,
        max_saturation_gain=0.14,
        max_structure_multiplier=1.25,
        background_protection=0.88,
        highlight_protection=0.70,
        continuity_required=True,
    ),
    "galaxy": TargetProfile(
        name="galaxy",
        scale_weights={"large": 0.70, "medium": 1.0, "small": 0.65},
        dark_structure_weight=0.85,
        tone_strength=0.16,
        saturation_gain=0.10,
        max_saturation_gain=0.20,
        max_structure_multiplier=1.30,
        background_protection=0.90,
        highlight_protection=0.90,
        continuity_required=True,
    ),
    "planetary_nebula": TargetProfile(
        name="planetary_nebula",
        scale_weights={"large": 0.40, "medium": 0.95, "small": 1.0},
        dark_structure_weight=0.25,
        tone_strength=0.18,
        saturation_gain=0.12,
        max_saturation_gain=0.22,
        max_structure_multiplier=1.35,
        background_protection=0.95,
        highlight_protection=0.92,
        continuity_required=True,
    ),
    "supernova_remnant": TargetProfile(
        name="supernova_remnant",
        scale_weights={"large": 0.55, "medium": 0.90, "small": 0.90},
        dark_structure_weight=0.20,
        tone_strength=0.14,
        saturation_gain=0.10,
        max_saturation_gain=0.18,
        max_structure_multiplier=1.25,
        background_protection=0.95,
        highlight_protection=0.75,
        continuity_required=True,
    ),
    "wide_field": TargetProfile(
        name="wide_field",
        scale_weights={"large": 1.0, "medium": 0.60, "small": 0.15},
        dark_structure_weight=0.70,
        tone_strength=0.12,
        saturation_gain=0.08,
        max_saturation_gain=0.16,
        max_structure_multiplier=1.20,
        background_protection=0.95,
        highlight_protection=0.75,
        continuity_required=True,
    ),
    "generic": TargetProfile(
        name="generic",
        scale_weights={"large": 0.55, "medium": 0.45, "small": 0.0},
        dark_structure_weight=0.20,
        tone_strength=0.08,
        saturation_gain=0.04,
        max_saturation_gain=0.10,
        max_structure_multiplier=1.0,
        background_protection=0.95,
        highlight_protection=0.90,
        continuity_required=False,
    ),
}

_LEVELS = {
    CandidateLevel.LOW: (0.55, 0.90, 0.96, 0.18),
    CandidateLevel.MEDIUM: (1.00, 0.78, 0.92, 0.35),
    CandidateLevel.HIGH: (1.35, 0.66, 0.88, 0.50),
}


def normalize_target_type(target_type: str) -> str:
    normalized = str(target_type or "").strip().lower().replace(" ", "_")
    return _ALIASES.get(normalized, normalized)


def validate_starless_target(target_type: str, target_name: str | None = None) -> str:
    normalized = normalize_target_type(target_type)
    normalized_name = str(target_name or "").strip().upper()
    if normalized in _REJECTED_TYPES or normalized_name in _REJECTED_NAMES:
        raise RejectedStarlessTarget(
            f"starless workflow is unsafe for star-dominant target: "
            f"{target_name or target_type}"
        )
    if not normalized:
        raise ValueError("target_type is required")
    return normalized


def get_target_profile(target_type: str) -> TargetProfile:
    normalized = validate_starless_target(target_type)
    return PROFILES.get(normalized, PROFILES["generic"])


def build_candidate_params(
    profile: TargetProfile,
    level: CandidateLevel,
) -> CandidateParams:
    multiplier, star_strength, star_saturation, star_softness = _LEVELS[level]
    bounded = min(multiplier, profile.max_structure_multiplier)
    return CandidateParams(
        level=level,
        structure_multiplier=bounded,
        scale_weights={
            key: value * bounded for key, value in profile.scale_weights.items()
        },
        dark_structure_weight=profile.dark_structure_weight * bounded,
        tone_strength=profile.tone_strength * bounded,
        saturation_gain=min(
            profile.saturation_gain * bounded,
            profile.max_saturation_gain,
        ),
        star_strength=star_strength,
        star_saturation=star_saturation,
        star_softness=star_softness,
    )
```

- [ ] **Step 4: Run the profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_profiles.py -v
```

Expected: all profile tests pass.

- [ ] **Step 5: Verification checkpoint**

Run:

```bash
.venv/bin/python -m pytest tests/test_smart_style_selector.py tests/test_star_step_dependencies.py -q
```

Expected: existing target-style and star-safety tests pass unchanged.

---

### Task 2: Input Validation and Starless Diagnostics

**Files:**
- Create: `deep-sky-processor/scripts/starless_diagnostics.py`
- Create: `deep-sky-processor/tests/test_starless_diagnostics.py`

- [ ] **Step 1: Write failing tests for accepted formats, RGB normalization, noise, and artifact detection**

```python
# deep-sky-processor/tests/test_starless_diagnostics.py
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from starless_diagnostics import (
    diagnose_starless,
    ensure_rgb_float32,
    validate_stretched_input_meta,
)


def test_ensure_rgb_float32_expands_grayscale():
    image = np.full((32, 48), 0.1, dtype=np.float64)
    result = ensure_rgb_float32(image)
    assert result.shape == (32, 48, 3)
    assert result.dtype == np.float32


@pytest.mark.parametrize("fmt", ["fits", "xisf", "jpg"])
def test_rejects_non_tiff_png_inputs(fmt):
    with pytest.raises(ValueError, match="TIFF or PNG"):
        validate_stretched_input_meta({"format": fmt, "is_linear": fmt != "jpg"})


def test_diagnostics_reports_dark_noise_and_artifact_mask():
    rng = np.random.default_rng(7)
    image = np.full((96, 128, 3), 0.03, dtype=np.float32)
    image += rng.normal(0, 0.002, image.shape).astype(np.float32)
    image[40:45, 60:65] = 0.0
    report, artifact_mask = diagnose_starless(np.clip(image, 0, 1))

    assert report["median"] > 0
    assert report["dark_noise_sigma"] > 0
    assert report["scales"]["small_energy"] >= 0
    assert artifact_mask.shape == image.shape[:2]
    assert float(artifact_mask[42, 62]) > float(np.median(artifact_mask))
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_diagnostics.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement input normalization and diagnostics**

Implement these public functions:

```python
def ensure_rgb_float32(image: np.ndarray) -> np.ndarray:
    """Return finite float32 RGB clipped to [0, 1]."""


def validate_stretched_input_meta(meta: dict) -> None:
    """Accept only non-linear TIFF/TIF/PNG metadata."""


def diagnose_starless(
    image: np.ndarray,
) -> tuple[dict, np.ndarray]:
    """Return JSON-safe diagnostics and a float32 suspected-artifact mask."""
```

Use:

```python
gray = (
    0.2126 * image[..., 0]
    + 0.7152 * image[..., 1]
    + 0.0722 * image[..., 2]
)
dark_limit = np.percentile(gray, 35)
dark_pixels = gray[gray <= dark_limit]
dark_noise_sigma = 1.4826 * np.median(
    np.abs(dark_pixels - np.median(dark_pixels))
)

small = gray - gaussian_filter(gray, sigma=1.5)
medium = gray - gaussian_filter(gray, sigma=6.0)
large = gray - gaussian_filter(gray, sigma=24.0)

local = gaussian_filter(gray, sigma=2.0)
dark_pit = np.clip(local - gray, 0, None)
ring = np.clip(
    gaussian_filter(gray, sigma=1.0) - gaussian_filter(gray, sigma=3.0),
    0,
    None,
)
artifact_raw = dark_pit + 0.5 * ring
artifact_scale = max(float(np.percentile(artifact_raw, 99.5)), 1e-8)
artifact_mask = gaussian_filter(
    np.clip(artifact_raw / artifact_scale, 0, 1),
    sigma=1.0,
).astype(np.float32)
```

The report must include:

```python
{
    "median": float,
    "p1": float,
    "p99": float,
    "near_black_ratio": float,
    "highlight_clip_ratio_by_channel": {"r": float, "g": float, "b": float},
    "corner_uniformity_ratio": float,
    "dark_noise_sigma": float,
    "background_chroma_sigma": float,
    "artifact_coverage": float,
    "scales": {
        "small_energy": float,
        "medium_energy": float,
        "large_energy": float,
    },
}
```

- [ ] **Step 4: Run diagnostics tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_diagnostics.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Verify existing image I/O remains unchanged**

Run:

```bash
.venv/bin/python -m pytest tests/test_integration.py::IntegrationTests::test_quality_metrics_report_dynamic_range_and_channel_collapse -v
```

Expected: PASS.

---

### Task 3: Subject, Dark-Structure, and Protection Masks

**Files:**
- Create: `deep-sky-processor/scripts/starless_masks.py`
- Create: `deep-sky-processor/tests/test_starless_masks.py`

- [ ] **Step 1: Write failing mask tests using synthetic bright shells and dark lanes**

```python
# deep-sky-processor/tests/test_starless_masks.py
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from starless_masks import build_starless_masks
from starless_profiles import get_target_profile


def synthetic_shell_with_dark_lane():
    yy, xx = np.mgrid[:128, :160]
    radius = np.sqrt((xx - 80) ** 2 + (yy - 64) ** 2)
    shell = np.exp(-((radius - 30) / 5) ** 2)
    image = np.full((128, 160, 3), 0.025, dtype=np.float32)
    image[..., 0] += shell * 0.35
    image[..., 1] += shell * 0.10
    image[..., 2] += shell * 0.07
    image[50:78, 72:88] *= 0.35
    return np.clip(image, 0, 1), shell


def test_subject_mask_selects_shell_and_rejects_corner_background():
    image, shell = synthetic_shell_with_dark_lane()
    masks, report = build_starless_masks(
        image,
        diagnostics={"dark_noise_sigma": 0.002},
        artifact_mask=np.zeros(image.shape[:2], dtype=np.float32),
        profile=get_target_profile("emission_nebula"),
    )
    assert float(masks["subject"][shell > 0.8].mean()) > 0.5
    assert float(masks["subject"][:12, :12].mean()) < 0.15
    assert 0.005 <= report["subject"]["coverage_soft"] <= 0.85


def test_dark_structure_mask_selects_lane_without_forcing_background():
    image, _shell = synthetic_shell_with_dark_lane()
    masks, _report = build_starless_masks(
        image,
        diagnostics={"dark_noise_sigma": 0.002},
        artifact_mask=np.zeros(image.shape[:2], dtype=np.float32),
        profile=get_target_profile("dark_nebula"),
    )
    assert float(masks["dark_structure"][58:70, 76:84].mean()) > 0.3
    assert float(masks["dark_structure"][:12, :12].mean()) < 0.2


def test_protection_mask_contains_artifact_and_highlight():
    image, _shell = synthetic_shell_with_dark_lane()
    image[20:24, 20:24] = 1.0
    artifact = np.zeros(image.shape[:2], dtype=np.float32)
    artifact[90:95, 110:115] = 1.0
    masks, _report = build_starless_masks(
        image,
        diagnostics={"dark_noise_sigma": 0.002},
        artifact_mask=artifact,
        profile=get_target_profile("galaxy"),
    )
    assert float(masks["protection"][21, 21]) > 0.7
    assert float(masks["protection"][92, 112]) > 0.7
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_masks.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the mask builder**

Define `build_starless_masks(image: np.ndarray, diagnostics: dict,
artifact_mask: np.ndarray, profile: TargetProfile) ->
tuple[dict[str, np.ndarray], dict]` using the concrete mask calculations below.

Implement masks from luminance:

```python
background = gaussian_filter(luma, sigma=24.0)
local = gaussian_filter(luma, sigma=5.0)
signal = np.clip(local - np.percentile(local, 30), 0, None)
signal_scale = max(float(np.percentile(signal, 99.0)), 1e-8)
subject = multiscale_feather(
    np.clip(signal / signal_scale, 0, 1),
    scales=(0.0, 2.0, 8.0),
)

dark_residual = np.clip(local - luma, 0, None)
dark_scale = max(float(np.percentile(dark_residual[subject > 0.1], 98.0)), 1e-8)
dark_structure = multiscale_feather(
    np.clip(dark_residual / dark_scale, 0, 1) * subject,
    scales=(0.0, 1.5, 4.0),
)

noise_floor = max(float(diagnostics["dark_noise_sigma"]) * 4.0, 1e-4)
background_protection = 1.0 - np.clip(signal / noise_floor, 0, 1)
highlight_protection = np.clip((luma - 0.82) / 0.15, 0, 1)
protection = np.maximum.reduce(
    [background_protection, highlight_protection, artifact_mask]
)
protection = multiscale_feather(protection, scales=(0.0, 2.0, 6.0))
```

Use existing `mask_tools.mask_statistics()` for reports. Add gate labels:

```python
if subject_stats["coverage_soft"] < 0.005:
    warnings.append("SUBJECT_MASK_NEAR_EMPTY")
if subject_stats["coverage_soft"] > 0.85:
    warnings.append("SUBJECT_MASK_TOO_BROAD")
```

- [ ] **Step 4: Run mask tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_masks.py tests/test_mask_tools.py -q
```

Expected: all new and existing mask tests pass.

---

### Task 4: Adaptive Multi-Scale Enhancement

**Files:**
- Create: `deep-sky-processor/scripts/starless_multiscale.py`
- Create: `deep-sky-processor/tests/test_starless_multiscale.py`

- [ ] **Step 1: Write failing tests for adaptive scales, localized enhancement, and dark-lane preservation**

```python
# deep-sky-processor/tests/test_starless_multiscale.py
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from starless_multiscale import (
    choose_adaptive_scales,
    decompose_luminance,
    enhance_starless_candidate,
)
from starless_profiles import CandidateLevel, build_candidate_params, get_target_profile


def synthetic_galaxy():
    yy, xx = np.mgrid[:128, :160]
    x = (xx - 80) / 38
    y = (yy - 64) / 22
    disk = np.exp(-(x * x + y * y))
    lane = np.exp(-((y - 0.12 * np.sin(x * 4)) / 0.10) ** 2) * disk
    image = np.full((128, 160, 3), 0.02, dtype=np.float32)
    image[..., 0] += disk * 0.35
    image[..., 1] += disk * 0.27
    image[..., 2] += disk * 0.22
    image *= (1.0 - lane[..., None] * 0.35)
    return np.clip(image, 0, 1), disk, lane


def test_adaptive_scales_grow_with_image_size():
    small = choose_adaptive_scales((128, 160))
    large = choose_adaptive_scales((1024, 1280))
    assert small["small"] < small["medium"] < small["large"]
    assert large["medium"] > small["medium"]


def test_decomposition_reconstructs_luminance():
    image, _disk, _lane = synthetic_galaxy()
    luma = np.mean(image, axis=2)
    layers = decompose_luminance(luma, choose_adaptive_scales(luma.shape))
    reconstructed = (
        layers["base"]
        + layers["large"]
        + layers["medium"]
        + layers["small"]
    )
    np.testing.assert_allclose(reconstructed, luma, atol=1e-5)


def test_high_candidate_enhances_subject_more_than_background_without_crushing_lane():
    image, disk, lane = synthetic_galaxy()
    subject = np.clip(disk, 0, 1).astype(np.float32)
    dark = np.clip(lane, 0, 1).astype(np.float32)
    protection = np.zeros_like(subject)
    params = build_candidate_params(
        get_target_profile("galaxy"),
        CandidateLevel.HIGH,
    )

    result, report, artifacts = enhance_starless_candidate(
        image,
        masks={
            "subject": subject,
            "dark_structure": dark,
            "protection": protection,
        },
        params=params,
    )

    subject_delta = np.mean(np.abs(result - image)[disk > 0.5])
    background_delta = np.mean(np.abs(result - image)[disk < 0.05])
    assert subject_delta > background_delta * 3
    assert np.mean(result[lane > 0.7]) >= np.mean(image[lane > 0.7]) - 0.01
    assert report["level"] == "high"
    assert set(artifacts) == {"detail_large", "detail_medium", "detail_small"}
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_multiscale.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement adaptive scales and exact reconstruction**

```python
def choose_adaptive_scales(shape: tuple[int, int]) -> dict[str, float]:
    short_side = float(min(shape))
    return {
        "small": max(1.2, short_side / 160.0),
        "medium": max(4.0, short_side / 40.0),
        "large": max(16.0, short_side / 10.0),
    }


def decompose_luminance(
    luminance: np.ndarray,
    scales: dict[str, float],
) -> dict[str, np.ndarray]:
    smooth_small = gaussian_filter(luminance, sigma=scales["small"])
    smooth_medium = gaussian_filter(luminance, sigma=scales["medium"])
    smooth_large = gaussian_filter(luminance, sigma=scales["large"])
    return {
        "small": luminance - smooth_small,
        "medium": smooth_small - smooth_medium,
        "large": smooth_medium - smooth_large,
        "base": smooth_large,
    }
```

- [ ] **Step 4: Implement bounded candidate enhancement**

Define `enhance_starless_candidate(image: np.ndarray,
masks: dict[str, np.ndarray], params: CandidateParams) ->
tuple[np.ndarray, dict, dict[str, np.ndarray]]` using the concrete calculations
below.

Use Lab luminance via existing safe converters:

```python
lab = safe_rgb2lab(source)
luma = np.clip(lab[..., 0] / 100.0, 0, 1)
layers = decompose_luminance(luma, choose_adaptive_scales(luma.shape))
allowed = np.clip(masks["subject"] * (1.0 - masks["protection"]), 0, 1)

detail_gain = (
    layers["large"] * params.scale_weights["large"]
    + layers["medium"] * params.scale_weights["medium"]
    + layers["small"] * params.scale_weights["small"]
)
detail_gain *= allowed

dark_context = np.clip(
    gaussian_filter(luma, sigma=6.0) - luma,
    0,
    None,
)
dark_scale = max(float(np.percentile(dark_context, 99.0)), 1e-8)
dark_lift = (
    np.clip(dark_context / dark_scale, 0, 1)
    * masks["dark_structure"]
    * (1.0 - masks["protection"])
)

target_luma = np.clip(
    luma
    + detail_gain
    + params.dark_structure_weight * dark_lift * (1.0 - luma) * 0.04,
    0,
    1,
)
```

Apply a bounded tone curve only through `allowed`; adjust Lab chroma only where the subject mask is nonzero. The function report must include:

```python
{
    "level": params.level.value,
    "scales": scales,
    "effective_change_ratio": float,
    "subject_change_energy_ratio": float,
    "params": dataclasses.asdict(params),
}
```

Return the scale layers separately so the CLI can write review artifacts without
putting NumPy arrays into the JSON report:

```python
artifacts = {
    "detail_large": layers["large"].astype(np.float32),
    "detail_medium": layers["medium"].astype(np.float32),
    "detail_small": layers["small"].astype(np.float32),
}
return result.astype(np.float32), report, artifacts
```

- [ ] **Step 5: Run multiscale tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_multiscale.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run numerical regression tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_hdr_sharpen.py \
  tests/test_compose_starnet_layers.py \
  tests/test_integration.py::IntegrationTests::test_positive_starless_detail_never_darkens_pixels \
  -q
```

Expected: PASS.

---

### Task 5: Stars-Layer Validation, Processing, and Recomposition

**Files:**
- Create: `deep-sky-processor/scripts/stellar_recompose.py`
- Create: `deep-sky-processor/tests/test_stellar_recompose.py`
- Modify: `deep-sky-processor/scripts/compose_starnet_layers.py:111-190`
- Modify: `deep-sky-processor/tests/test_compose_starnet_layers.py`

- [ ] **Step 1: Write failing tests for black-background validation and topology preservation**

```python
# deep-sky-processor/tests/test_stellar_recompose.py
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.ndimage import label

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from starless_profiles import CandidateLevel, build_candidate_params, get_target_profile
from stellar_recompose import (
    process_stars_layer,
    recompose_additive,
    validate_stars_layer,
)


def sparse_stars():
    stars = np.zeros((96, 128, 3), dtype=np.float32)
    for y, x, color in [
        (20, 20, (0.9, 0.8, 0.7)),
        (45, 70, (0.7, 0.8, 1.0)),
        (75, 105, (1.0, 0.7, 0.6)),
    ]:
        stars[y - 1:y + 2, x - 1:x + 2] = color
    return stars


def test_rejects_stars_layer_with_bright_background():
    stars = np.full((64, 64, 3), 0.08, dtype=np.float32)
    with pytest.raises(ValueError, match="black background"):
        validate_stars_layer(stars)


def test_processing_preserves_connected_component_count():
    stars = sparse_stars()
    params = build_candidate_params(
        get_target_profile("emission_nebula"),
        CandidateLevel.HIGH,
    )
    processed, report = process_stars_layer(stars, params)
    before_count = label(np.max(stars, axis=2) > 0.02)[1]
    after_count = label(np.max(processed, axis=2) > 0.02)[1]
    assert before_count == after_count
    assert report["star_strength"] == params.star_strength


def test_additive_recompose_is_clipped_and_positive():
    base = np.full((32, 32, 3), 0.2, dtype=np.float32)
    stars = np.zeros_like(base)
    stars[15:18, 15:18] = 0.9
    result = recompose_additive(base, stars)
    assert float(result.max()) <= 1.0
    assert np.all(result >= base)
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_stellar_recompose.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement stars validation and processing**

```python
def validate_stars_layer(stars: np.ndarray) -> dict:
    source = ensure_rgb_float32(stars)
    luma = luminance(source)
    background_level = float(np.percentile(luma, 50.0))
    nonzero_ratio = float(np.mean(luma > 0.002))
    if background_level > 0.01 or nonzero_ratio > 0.35:
        raise ValueError(
            "stars_input must be an independent positive stars layer "
            "on a black background"
        )
    return {
        "background_median": background_level,
        "nonzero_ratio": nonzero_ratio,
        "highlight_clip_ratio": float(np.mean(source >= 0.995)),
    }


def process_stars_layer(
    stars: np.ndarray,
    params: CandidateParams,
) -> tuple[np.ndarray, dict]:
    validate_stars_layer(stars)
    layer = ensure_rgb_float32(stars)
    if params.star_softness > 0:
        softened = gaussian_filter(
            layer,
            sigma=(params.star_softness, params.star_softness, 0),
        )
        layer = np.maximum(layer * 0.78, softened * 0.92)
    luma = luminance(layer)
    chroma = layer - luma[..., None]
    layer = np.clip(
        luma[..., None] + chroma * params.star_saturation,
        0,
        1,
    )
    layer = np.clip(layer * params.star_strength, 0, 1)
    return layer.astype(np.float32), {
        "star_strength": params.star_strength,
        "star_saturation": params.star_saturation,
        "star_softness": params.star_softness,
    }


def recompose_additive(
    starless: np.ndarray,
    processed_stars: np.ndarray,
) -> np.ndarray:
    if starless.shape != processed_stars.shape:
        raise ValueError("starless and stars layers must have identical shapes")
    return np.clip(starless + processed_stars, 0, 1).astype(np.float32)
```

- [ ] **Step 4: Delegate the old compose script to the shared implementation**

In `compose_starnet_layers.py`:

```python
from starless_profiles import CandidateLevel, CandidateParams
from stellar_recompose import process_stars_layer, recompose_additive
```

Keep the existing `process_stars(stars, strength, saturation, softness)` signature, but construct a compatibility `CandidateParams` whose unused structure fields are zero, call `process_stars_layer()`, and return only the image.

Replace:

```python
combined = np.empty_like(enhanced_starless)
np.add(enhanced_starless, processed_stars, out=combined)
np.clip(combined, 0, 1, out=combined)
```

with:

```python
combined = recompose_additive(enhanced_starless, processed_stars)
```

- [ ] **Step 5: Add compatibility assertions to the existing test**

Append:

```python
from scripts.compose_starnet_layers import process_stars


def test_legacy_process_stars_signature_remains_supported():
    stars = np.zeros((32, 32, 3), dtype=np.float32)
    stars[15:18, 15:18] = 0.8
    result = process_stars(
        stars,
        strength=0.82,
        saturation=0.92,
        softness=0.35,
    )
    assert result.shape == stars.shape
    assert float(result.max()) > 0
```

- [ ] **Step 6: Run stellar and compose tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_stellar_recompose.py \
  tests/test_compose_starnet_layers.py \
  -v
```

Expected: PASS.

---

### Task 6: Quality Gates and One-Pass Downgrade

**Files:**
- Create: `deep-sky-processor/scripts/starless_quality.py`
- Create: `deep-sky-processor/tests/test_starless_quality.py`

- [ ] **Step 1: Write failing tests for required gates and targeted downgrade**

```python
# deep-sky-processor/tests/test_starless_quality.py
from dataclasses import replace
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from starless_profiles import CandidateLevel, build_candidate_params, get_target_profile
from starless_quality import evaluate_candidate, downgrade_params


def test_difference_energy_must_stay_inside_structure_masks():
    baseline = np.full((64, 64, 3), 0.1, dtype=np.float32)
    candidate = baseline.copy()
    candidate[:20, :20] += 0.1
    subject = np.zeros((64, 64), dtype=np.float32)
    subject[30:50, 30:50] = 1.0
    report = evaluate_candidate(
        baseline_starless=baseline,
        candidate_starless=np.clip(candidate, 0, 1),
        baseline_final=baseline,
        candidate_final=np.clip(candidate, 0, 1),
        stars_input=np.zeros_like(baseline),
        masks={
            "subject": subject,
            "dark_structure": np.zeros_like(subject),
            "protection": np.zeros_like(subject),
        },
        level=CandidateLevel.MEDIUM,
        continuity_required=False,
    )
    assert report["status"] == "failed"
    assert "DIFF_OUTSIDE_STRUCTURE" in {
        gate["code"] for gate in report["gates"]
    }


def test_high_frequency_failure_only_reduces_small_scale():
    params = build_candidate_params(
        get_target_profile("emission_nebula"),
        CandidateLevel.HIGH,
    )
    downgraded = downgrade_params(params, ["PROTECTED_HF_INCREASE"])
    assert downgraded.scale_weights["small"] < params.scale_weights["small"]
    assert downgraded.scale_weights["large"] == params.scale_weights["large"]


def test_color_failure_reduces_saturation_only():
    params = build_candidate_params(
        get_target_profile("emission_nebula"),
        CandidateLevel.HIGH,
    )
    downgraded = downgrade_params(params, ["CHANNEL_CLIP_INCREASE"])
    assert downgraded.saturation_gain < params.saturation_gain
    assert downgraded.star_strength == params.star_strength
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_quality.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement baseline-relative metrics and gate records**

Define `evaluate_candidate(baseline_starless, candidate_starless,
baseline_final, candidate_final, stars_input, masks, level,
continuity_required) -> dict` and `downgrade_params(params,
failed_codes) -> CandidateParams`. Use the exact thresholds and downgrade
mapping below.

Use gate objects:

```python
{
    "code": "NEAR_BLACK_INCREASE",
    "status": "failed",
    "value": 0.031,
    "threshold": "<=0.02 absolute increase",
}
```

Implement the design thresholds:

```python
HF_LIMITS = {
    CandidateLevel.LOW: 0.08,
    CandidateLevel.MEDIUM: 0.15,
    CandidateLevel.HIGH: 0.25,
}
MAX_NEAR_BLACK_INCREASE = 0.02
MAX_CHANNEL_CLIP_INCREASE = 0.005
MAX_CORNER_DEGRADATION = 0.20
MIN_STRUCTURE_DIFF_RATIO = 0.70
MAX_STAR_AREA_INCREASE = 0.10
MAX_CONTINUITY_LOSS = 0.10
```

Reuse `quality_metrics.calculate_metrics()` for baseline anchors, and add local helpers for:

- per-channel highlight clipping;
- protected-region high-frequency energy;
- difference energy inside `max(subject, dark_structure)`;
- connected edge continuity for targets that require it.

- [ ] **Step 4: Implement targeted downgrade with `dataclasses.replace`**

```python
def downgrade_params(params, failed_codes):
    scale_weights = dict(params.scale_weights)
    saturation_gain = params.saturation_gain
    tone_strength = params.tone_strength
    star_strength = params.star_strength
    star_softness = params.star_softness

    if "PROTECTED_HF_INCREASE" in failed_codes:
        scale_weights["small"] *= 0.55
    if "HALO_OR_DOUBLE_EDGE" in failed_codes:
        scale_weights["small"] *= 0.65
        scale_weights["medium"] *= 0.80
    if "NEAR_BLACK_INCREASE" in failed_codes:
        tone_strength *= 0.65
        scale_weights["large"] *= 0.80
    if "CHANNEL_CLIP_INCREASE" in failed_codes:
        saturation_gain *= 0.65
    if "STAR_AREA_INCREASE" in failed_codes:
        star_strength = min(star_strength + 0.08, 1.0)
        star_softness *= 0.60

    return replace(
        params,
        scale_weights=scale_weights,
        saturation_gain=saturation_gain,
        tone_strength=tone_strength,
        star_strength=star_strength,
        star_softness=star_softness,
    )
```

- [ ] **Step 5: Run quality tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_starless_quality.py -v
```

Expected: PASS.

- [ ] **Step 6: Verify existing quality metrics and agent gates**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_agent_protocol.py \
  tests/test_integration.py::IntegrationTests::test_quality_metrics_report_dynamic_range_and_channel_collapse \
  -q
```

Expected: PASS.

---

### Task 7: End-to-End CLI and Review Artifacts

**Files:**
- Create: `deep-sky-processor/scripts/enhance_starless.py`
- Create: `deep-sky-processor/tests/test_enhance_starless_cli.py`

- [ ] **Step 1: Write a failing CLI integration test**

```python
# deep-sky-processor/tests/test_enhance_starless_cli.py
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import tifffile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "enhance_starless.py"


def test_cli_writes_three_candidates_and_report(tmp_path):
    yy, xx = np.mgrid[:96, :128]
    shell = np.exp(
        -((np.sqrt((xx - 64) ** 2 + (yy - 48) ** 2) - 22) / 4) ** 2
    )
    starless = np.full((96, 128, 3), 0.025, dtype=np.float32)
    starless[..., 0] += shell * 0.35
    starless[..., 1] += shell * 0.08
    starless[..., 2] += shell * 0.05
    stars = np.zeros_like(starless)
    stars[20:23, 20:23] = 0.9
    stars[65:68, 100:103] = (0.7, 0.8, 1.0)

    starless_path = tmp_path / "starless.tif"
    stars_path = tmp_path / "stars.tif"
    output_dir = tmp_path / "output"
    tifffile.imwrite(starless_path, np.clip(starless, 0, 1))
    tifffile.imwrite(stars_path, stars)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(starless_path),
            str(stars_path),
            str(output_dir),
            "--target-type",
            "emission_nebula",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads((output_dir / "report.json").read_text())
    assert set(report["candidates"]) == {"low", "medium", "high"}
    assert (output_dir / "final_low.tif").exists()
    assert (output_dir / "final_medium.tif").exists()
    assert (output_dir / "review" / "subject_mask.tif").exists()
    assert report["status"] in {"success", "review_required"}


def test_cli_rejects_m45(tmp_path):
    image = np.zeros((32, 32, 3), dtype=np.float32)
    starless_path = tmp_path / "starless.tif"
    stars_path = tmp_path / "stars.tif"
    tifffile.imwrite(starless_path, image)
    tifffile.imwrite(stars_path, image)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(starless_path),
            str(stars_path),
            str(tmp_path / "output"),
            "--target-type",
            "reflection_nebula",
            "--target-name",
            "M45",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "star-dominant target" in completed.stderr
```

- [ ] **Step 2: Run the tests and verify the CLI is missing**

Run:

```bash
.venv/bin/python -m pytest tests/test_enhance_starless_cli.py -v
```

Expected: subprocess fails because `scripts/enhance_starless.py` does not exist.

- [ ] **Step 3: Implement CLI argument parsing and orchestration**

Use the public orchestration signature
`run_starless_enhancement(starless_path: str, stars_path: str,
output_dir: str, target_type: str, target_name: str | None = None,
write_jpg: bool = True) -> dict`.

CLI:

```python
parser = argparse.ArgumentParser(
    description="Enhance stretched starless deep-sky images and recompose stars"
)
parser.add_argument("starless")
parser.add_argument("stars")
parser.add_argument("output_dir")
parser.add_argument("--target-type", required=True)
parser.add_argument("--target-name", default=None)
parser.add_argument("--no-jpg", action="store_true")
parser.add_argument("--report", default=None)
```

Orchestration order:

```python
normalized_target = validate_starless_target(target_type, target_name)
profile = get_target_profile(normalized_target)

starless, starless_meta = read_image(starless_path)
stars, stars_meta = read_image(stars_path)
validate_stretched_input_meta(starless_meta)
validate_stretched_input_meta(stars_meta)
starless = ensure_rgb_float32(starless)
stars = ensure_rgb_float32(stars)
if starless.shape != stars.shape:
    raise ValueError("starless and stars inputs must have identical shapes")
stars_validation = validate_stars_layer(stars)

diagnostics, artifact_mask = diagnose_starless(starless)
masks, mask_report = build_starless_masks(
    starless,
    diagnostics,
    artifact_mask,
    profile,
)
```

For each level:

```python
params = build_candidate_params(profile, level)
candidate_starless, enhance_report, scale_artifacts = enhance_starless_candidate(
    starless,
    masks,
    params,
)
processed_stars, stars_report = process_stars_layer(stars, params)
baseline_final = recompose_additive(starless, stars)
candidate_final = recompose_additive(candidate_starless, processed_stars)
quality = evaluate_candidate(
    baseline_starless=starless,
    candidate_starless=candidate_starless,
    baseline_final=baseline_final,
    candidate_final=candidate_final,
    stars_input=stars,
    masks=masks,
    level=level,
    continuity_required=profile.continuity_required,
)
```

If quality status is `failed`, call `downgrade_params()` once, regenerate that candidate, and re-evaluate. Do not retry a second time.

The decomposition shapes must be identical for every candidate. Save the
`scale_artifacts` returned by the first level for the shared review files and
assert later artifact shapes match before discarding their duplicate arrays.

- [ ] **Step 4: Implement deterministic output naming**

Always write:

```text
review/subject_mask.tif
review/dark_structure_mask.tif
review/protection_mask.tif
review/detail_large.tif
review/detail_medium.tif
review/detail_small.tif
```

For each accepted or reviewable level:

```text
starless_<level>.tif
final_<level>.tif
final_<level>.jpg
review/<level>_difference.tif
```

Do not write `final_high.*` when HIGH remains hard-failed after its one retry. Record:

```python
report["candidates"]["high"]["outputs"] = []
```

Write the report atomically enough for this local tool by constructing the full dict in memory and calling:

```python
report_path.write_text(
    json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
```

- [ ] **Step 5: Run CLI integration tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_enhance_starless_cli.py -v
```

Expected: PASS.

- [ ] **Step 6: Run all new tests together**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_starless_profiles.py \
  tests/test_starless_diagnostics.py \
  tests/test_starless_masks.py \
  tests/test_starless_multiscale.py \
  tests/test_stellar_recompose.py \
  tests/test_starless_quality.py \
  tests/test_enhance_starless_cli.py \
  -q
```

Expected: PASS.

---

### Task 8: Documentation and Existing Workflow Integration

**Files:**
- Modify: `deep-sky-processor/SKILL.md:299-316`
- Modify: `deep-sky-processor/references/external_tools.md:89-114`
- Modify: `deep-sky-processor/references/case_ngc6888_rgb.md:72-80`

- [ ] **Step 1: Update the main skill workflow**

Replace the existing stretched-StarNet recommendation with:

```markdown
已拉伸的 StarNet starless TIFF/PNG 应使用独立的 stars 输出，不再默认通过
带星图减 starless 推导星点层：

```bash
python scripts/enhance_starless.py \
  starless.tif stars.tif output_dir \
  --target-type emission_nebula \
  --target-name NGC6888
```

该流程固定输出 LOW、MEDIUM、HIGH 三档候选，并按档位同步调整主体和星点。
HIGH 档允许更强视觉冲击，但不能绕过光晕、黑位、噪声、结构连续性和星点门禁。
球状星团、疏散星团、一般星团及 M45 会被拒绝，必须改用原带星流程。
```

- [ ] **Step 2: Update external-tool requirements**

Document:

- starless and stars must be independently exported by StarNet;
- identical dimensions, orientation, and crop are mandatory;
- stars must be positive signal on black background;
- TIFF/PNG only for this first version;
- no subtraction fallback;
- output file list and `report.json` statuses.

- [ ] **Step 3: Update the NGC6888 case**

Use:

```bash
python scripts/enhance_starless.py \
  starless_NGC6888.tif stars_NGC6888.tif NGC6888_starless_finish \
  --target-type emission_nebula \
  --target-name NGC6888
```

State that review should prioritize:

- shell continuity;
- internal filament authenticity;
- Hα/OIII color balance;
- starless dark pits or rings;
- whether HIGH remains natural enough to accept.

- [ ] **Step 4: Verify documentation commands reference real files and arguments**

Run:

```bash
rg -n "enhance_starless.py|--target-type|stars.tif|star-dominant" \
  SKILL.md references/external_tools.md references/case_ngc6888_rgb.md
.venv/bin/python scripts/enhance_starless.py --help
```

Expected:

- all three documents reference the new CLI;
- help exits with status 0 and lists `--target-type` and `--target-name`.

---

### Task 9: Full Regression and Real-Image Acceptance

**Files:**
- No source changes unless a failure reveals a defect.
- Add a local, ignored acceptance fixture directory only if real samples are available: `deep-sky-processor/local_acceptance/starless/`.

- [ ] **Step 1: Run the complete test suite**

Run:

```bash
cd /Users/mz/dev/skills/deep-sky-processor
.venv/bin/python -m pytest tests -q
```

Expected: all tests pass.

- [ ] **Step 2: Run a CLI smoke test with a synthetic emission-nebula image**

Run the CLI integration test directly:

```bash
.venv/bin/python -m pytest \
  tests/test_enhance_starless_cli.py::test_cli_writes_three_candidates_and_report \
  -v
```

Expected: PASS and temporary LOW/MEDIUM/HIGH report generation succeeds.

- [ ] **Step 3: Run real-image acceptance for each available target type**

For each available aligned pair:

```bash
.venv/bin/python scripts/enhance_starless.py \
  /absolute/path/to/starless.tif \
  /absolute/path/to/stars.tif \
  /absolute/path/to/output \
  --target-type emission_nebula \
  --target-name NGC6888
```

Required visual checks:

- LOW, MEDIUM, HIGH form a clear increasing enhancement sequence.
- Gas folds, dust lanes, spiral arms, dark clouds, or filaments come from existing input structure.
- Dark structures gain separation without becoming clipped black holes.
- No new halos, double edges, broken filaments, or granular background appear.
- Stars remain colored, natural, and progressively less dominant.
- A failed HIGH candidate is omitted or marked `review_required`; it is never reported as success.

- [ ] **Step 4: Inspect report invariants**

For every accepted output, verify:

```python
assert report["target_type"] == requested_target
assert set(report["candidates"]) == {"low", "medium", "high"}
assert report["candidates"]["low"]["params"]["star_strength"] > \
       report["candidates"]["medium"]["params"]["star_strength"] > \
       report["candidates"]["high"]["params"]["star_strength"]
assert report["candidates"]["high"]["retry_count"] <= 1
```

- [ ] **Step 5: Final verification checkpoint**

Run:

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m compileall -q scripts
```

Expected:

- all tests pass;
- Python compilation exits 0;
- no implementation or documentation placeholder remains:

```bash
rg -n "TBD|TODO|implement later|fill in details" \
  scripts/starless_*.py scripts/stellar_recompose.py scripts/enhance_starless.py \
  tests/test_starless_*.py tests/test_stellar_recompose.py \
  SKILL.md references/external_tools.md references/case_ngc6888_rgb.md
```

Expected: no matches.
