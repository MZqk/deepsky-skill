"""目标感知规则的单一事实源 (single source of truth)。

名称 -> 目标类型的解析统一复用 `fits_io.normalize_target_name` 与
`fits_io.LOCAL_CELESTIAL_DB`，本模块只保留两类内容：

1. 无法从类型推导的"特殊规则表"（如 M42 核心保护、M45 星点即主体）；
2. 供 pipeline 与 starless_profiles 共享的谓词。

所有匹配均为**精确匹配**（星表编号相等或查表命中），不再使用子串匹配，
因此 `'M8' in 'M81'` 这类误判不会发生。
"""

from __future__ import annotations

from fits_io import LOCAL_CELESTIAL_DB, normalize_target_name

# 星点即主体的类型：去星/缩星会破坏目标本身。
STAR_DOMINANT_TYPES = frozenset({
    "globular_cluster",
    "open_cluster",
    "star_cluster",
})

# 极小的特殊规则表。只收录无法从 LOCAL_CELESTIAL_DB 类型推导的例外：
# - M42 类型上是发射星云，但核心极易过曝，需要独立的拉伸保护。
# - M45 在库中已是 open_cluster（已被 STAR_DOMINANT_TYPES 覆盖），此处显式化，
#   以防库中类型被改动后丢失"星点即主体"的语义；同时它是反射星云性质的
#   疏散星团（target_awareness.md 中列于反射星云一节的 "M45例外"），
#   因此额外标记 reflection_like，使其仍享受反射星云的拉伸/HDR 调校。
SPECIAL_TARGET_RULES = {
    "M42": {"core_protection": True},
    "M45": {"star_is_subject": True, "reflection_like": True},
}

# 已知目标类型集合。用于判定显式传入的 target_type 是否可信。
_KNOWN_TYPES = frozenset({
    "emission_nebula",
    "reflection_nebula",
    "dark_nebula",
    "galaxy",
    "planetary_nebula",
    "globular_cluster",
    "open_cluster",
    "star_cluster",
    "supernova_remnant",
    "wide_field",
})


def resolve_catalog_id(target_name) -> str:
    """把任意写法解析为规范星表编号（'M 42'->'M42'，'ngc-6888'->'NGC6888'）。

    未识别的名称返回去空格后的大写串；空名称返回 ''。
    """
    if not target_name:
        return ""
    return normalize_target_name(target_name)


def resolve_target_type(target_type, target_name) -> str:
    """解析目标类型。

    显式 `target_type` 优先（用户/AI 的声明权威）；否则用规范化名称精确查
    `LOCAL_CELESTIAL_DB`。两者都没有结果时返回规范化后的显式类型（可能为 ''）。
    """
    explicit = str(target_type or "").strip().lower().replace(" ", "_")
    if explicit in _KNOWN_TYPES:
        return explicit
    catalog_id = resolve_catalog_id(target_name)
    if catalog_id:
        entry = LOCAL_CELESTIAL_DB.get(catalog_id)
        if entry:
            return entry[0]
    return explicit


def get_special_rule(target_name) -> dict:
    """返回该天体的特殊规则字典；无特殊规则时返回空字典。"""
    return SPECIAL_TARGET_RULES.get(resolve_catalog_id(target_name), {})


def is_star_poi_target(target_type, target_name) -> bool:
    """星点即主体（球状/疏散星团、M45）——必须禁用去星与缩星。"""
    if resolve_target_type(target_type, target_name) in STAR_DOMINANT_TYPES:
        return True
    return bool(get_special_rule(target_name).get("star_is_subject"))


def is_emission_nebula_target(target_type, target_name) -> bool:
    return resolve_target_type(target_type, target_name) == "emission_nebula"


def is_reflection_nebula_target(target_type, target_name) -> bool:
    """反射星云。M45 这类"反射星云性质的疏散星团"由特殊规则表标记。"""
    if resolve_target_type(target_type, target_name) == "reflection_nebula":
        return True
    return bool(get_special_rule(target_name).get("reflection_like"))


def is_m42_target(target_name) -> bool:
    """M42 猎户座大星云——需要核心保护。"""
    return resolve_catalog_id(target_name) == "M42"
