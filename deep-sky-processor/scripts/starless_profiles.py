"""Target-aware profiles for stretched starless enhancement."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from target_rules import (
    STAR_DOMINANT_TYPES,
    get_special_rule,
    resolve_target_type,
)


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
    "dark_cloud": "dark_nebula",
    "milky_way": "wide_field",
}


def _profile(name, weights, dark, tone, saturation, max_saturation,
             max_multiplier, background, highlight, continuity):
    return TargetProfile(
        name=name,
        scale_weights=weights,
        dark_structure_weight=dark,
        tone_strength=tone,
        saturation_gain=saturation,
        max_saturation_gain=max_saturation,
        max_structure_multiplier=max_multiplier,
        background_protection=background,
        highlight_protection=highlight,
        continuity_required=continuity,
    )


PROFILES = {
    "emission_nebula": _profile(
        "emission_nebula", {"large": .75, "medium": 1., "small": .55},
        .35, .16, .16, .28, 1.35, .85, .75, True),
    "reflection_nebula": _profile(
        "reflection_nebula", {"large": 1., "medium": .75, "small": .25},
        .30, .12, .08, .16, 1.20, .90, .80, False),
    "dark_nebula": _profile(
        "dark_nebula", {"large": .90, "medium": .80, "small": .35},
        1., .10, .06, .14, 1.25, .88, .70, True),
    "galaxy": _profile(
        "galaxy", {"large": .70, "medium": 1., "small": .65},
        .85, .16, .10, .20, 1.30, .90, .90, True),
    "planetary_nebula": _profile(
        "planetary_nebula", {"large": .40, "medium": .95, "small": 1.},
        .25, .18, .12, .22, 1.35, .95, .92, True),
    "supernova_remnant": _profile(
        "supernova_remnant", {"large": .55, "medium": .90, "small": .90},
        .20, .14, .10, .18, 1.25, .95, .75, True),
    "wide_field": _profile(
        "wide_field", {"large": 1., "medium": .60, "small": .15},
        .70, .12, .08, .16, 1.20, .95, .75, True),
    "generic": _profile(
        "generic", {"large": .55, "medium": .45, "small": 0.},
        .20, .08, .04, .10, 1., .95, .90, False),
}

_LEVELS = {
    CandidateLevel.LOW: (.55, .90, .96, .18),
    CandidateLevel.MEDIUM: (1., .78, .92, .35),
    CandidateLevel.HIGH: (1.35, .66, .88, .50),
}


def normalize_target_type(target_type: str) -> str:
    value = str(target_type or "").strip().lower().replace(" ", "_")
    return _ALIASES.get(value, value)


def validate_starless_target(target_type: str, target_name: str | None = None):
    normalized = normalize_target_type(target_type)
    # 名称也参与判定：显式类型可能标错（例如把 M45 标成 reflection_nebula），
    # 此时按规范化名称解析出的类型仍能拦住"星点即主体"的目标。
    name_type = resolve_target_type(None, target_name)
    if (
        normalized in STAR_DOMINANT_TYPES
        or name_type in STAR_DOMINANT_TYPES
        or get_special_rule(target_name).get("star_is_subject")
    ):
        raise RejectedStarlessTarget(
            f"starless workflow is unsafe for star-dominant target: "
            f"{target_name or target_type}"
        )
    if not normalized:
        raise ValueError("target_type is required")
    return normalized


def get_target_profile(target_type: str):
    return PROFILES.get(validate_starless_target(target_type), PROFILES["generic"])


def build_candidate_params(profile: TargetProfile, level: CandidateLevel):
    multiplier, star_strength, star_saturation, star_softness = _LEVELS[level]
    multiplier = min(multiplier, profile.max_structure_multiplier)
    return CandidateParams(
        level=level,
        structure_multiplier=multiplier,
        scale_weights={
            key: value * multiplier
            for key, value in profile.scale_weights.items()
        },
        dark_structure_weight=profile.dark_structure_weight * multiplier,
        tone_strength=profile.tone_strength * multiplier,
        saturation_gain=min(
            profile.saturation_gain * multiplier,
            profile.max_saturation_gain,
        ),
        star_strength=star_strength,
        star_saturation=star_saturation,
        star_softness=star_softness,
    )
