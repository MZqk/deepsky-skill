#!/usr/bin/env python3
"""去星质量门禁的按目标类型分档表（单一事实源）。

**为什么需要这个模块**：v0.1.33 把去星门禁从一刀切改成两级
（``_star_removal_gate`` 的 ``ok`` / ``advisory`` / ``hard_failed``），
但阈值全部是 :mod:`star_tools` 里的模块级常量。那样做有一个实测暴露的
问题：**同一套阈值对不同目标类型的实际严格程度差一个数量级**。

- 强发射星云（NGC6888）的延展信号占满画面，``subject_separation`` 天然高，
  误伤读数天然大 —— StarNet2 的 damage 0.516 与形态学的 0.450 双双越过
  0.20 硬线，两者**都**被拒，门禁既没区分优劣也表达不出「测不准」。
- 暗弱反射星云的主体对比度低，同一套阈值又偏松。

所以阈值必须按 ``target_type`` 分档。但**分档不等于放宽**：档位只是把
「同一判据在不同形态上的口径」显式化，具体数值仍须由劣化对照标定
（见 ``references/star_damage_gate_calibration.md``）。本模块只提供
**机制**与**默认值**，默认值等于 v0.1.33 的全局常量 —— 也就是说
**分档上线当天行为零变化**，放宽只能通过显式改表发生，且被单向棘轮
（:func:`strictest`）记录。

设计取舍
--------
- **不引入新的判定逻辑**。所有阈值都已经是 ``star_tools`` 里的常量，
  这里只做「键 → 值」的映射与棘轮，不复制判据。
- **方向逐字段声明**（:data:`TIGHTER_IS_HIGHER`），不做全局 ``max``。
  ``repair_cap_*`` 与 ``repair_weight_*`` 的方向是**相反**的
  （权重调大更严、上限调小更严），全局 ``max`` 会把其中一族放松。
- **弃用字段显式化**。:data:`DEPRECATED_FIELDS` 里的字段保留在
  dataclass 里（schema 兼容、报告仍输出实测值），但不参与分档与棘轮。
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Optional

from target_rules import STAR_DOMINANT_TYPES, _KNOWN_TYPES


# ──弃用字段 ────────────────────────────────────────────────────────
#: 不参与分档与棘轮的字段。
#:
#: ``mask_max_coverage`` 被弃用的**实测依据**：主体掩膜由
#: ``orig_smooth >= percentile(orig_smooth, 75)`` 圈定，是**相对**分位，
#: 因此掩膜占比数学上恒等于 ``1 - 0.75 = 0.25``（4 份母版实测全部
#: ``0.250000``）⇒ ``> 0.60`` 的判据**从不触发**。保留字段是为了
#: schema 兼容与人工观测，绝不允许据此分档。
DEPRECATED_FIELDS = frozenset({'mask_max_coverage'})


@dataclass(frozen=True)
class RemovalProfile:
    """单个目标类型的去星门禁阈值档。"""

    name: str
    #: 该类型能否到达去星门禁。星团 3 类为 False —— ``pipeline.py`` 的
    #: ``apply_target_aware_safety_rules`` 会删掉 4 个星点步骤，它们
    #: **结构上**到不了门禁；建档位等于给一条不可达路径写规则。
    reachable: bool
    #: 该档是否因标定样本不足而禁止升级（``escalate``）。
    provisional: bool
    # ── damage 族：越小越严 ──
    #: 误伤接纳线。**``None`` = 尚未标定** ⇒ 门禁走 ``uncalibrated``
    #: （读数照记、不参与判定），见 :mod:`star_tools`。
    #:
    #: 为什么默认是 ``None`` 而不是 0.20：0.20 来自 v0.1.30 之前的年代，
    #: 从未被任何数据验证过。v0.1.37 的标定实测**证否了它可标定**——
    #: 5 份可测样本的健康 damage跨 0.0357~0.3580（10 倍），而「最轻劣化」
    #: 相对各自健康态只抬高 0.033 ⇒ 双向夹逼区间为空。见
    #: ``references/star_damage_gate_calibration.md`` §3.12。
    damage_limit: Optional[float]
    advisory_multiplier: float
    baseline_change: float
    needs_starnet_residual: float
    needs_starnet_damage: float
    # ── 星层归一化族（v0.1.39 新增）──
    #: 是否启用「扣除星层通量」的主体基准。True 时 damage 改用
    #: median(orig_smooth[M]) - median(stars_smooth[M]) 作分母。
    #: 依据见 references/star_damage_gate_calibration.md §6。
    star_flux_normalize: bool
    #: 归一化判据自己的死区。**与 baseline_change 独立**：旧死区 0.10
    #: 是在旧判据（健康组极差 0.3393）下调出来的；归一化后健康组极差
    #: 只有 0.0138，0.10 的死区会吃掉全部劣化信号（实测 beta=0.20 的
    #: 劣化下界 0.0867 仍低于健康上界 0.1138 ⇒ 区间为空）。
    star_flux_baseline_change: float
    #: 归一化后的主体基准下限。扣除星通量后 orig - stars 可能趋近 0
    #: （高星密度小目标），此时比值失去意义 ⇒ 判为不可测。
    min_star_free_subject_level: float
    #: **相对判据**的接纳线：同一张图相对**自己健康基线**的偏离上限。
    #: None = 尚未标定（与 damage_limit 同语义，走 uncalibrated）。
    #: 见 references/star_damage_gate_calibration.md §8。
    damage_delta_limit: Optional[float]
    # ── score 族：越大越严 ──
    min_quality_score: float
    repair_weight_residual: float
    repair_weight_damage: float
    repair_weight_gradient: float
    #: 与权重**反向**：上限调小更严。
    repair_cap_residual: float
    repair_cap_damage: float
    repair_cap_gradient: float
    quality_band_good: float
    quality_band_marginal: float
    # ── 可测性族：越大越严（反直觉：抬高下限才让更多数据可测）──
    mask_max_coverage: float
    min_subject_separation: float
    #: 非回归上限。0.00 = 重试不得让质量分变差。
    max_score_growth: float

    def as_applied(self) -> dict:
        """报告里展示的「实际生效阈值」（排除弃用字段与元信息）。"""
        skip = {'name', 'reachable', 'provisional'} | set(DEPRECATED_FIELDS)
        return {f.name: getattr(self, f.name) for f in fields(self)
                if f.name not in skip}

    @property
    def damage_limit_calibrated(self) -> bool:
        """误伤阈值是否已标定。``False`` ⇒ 门禁走 ``uncalibrated``。"""
        return self.damage_limit is not None

    @property
    def uncalibrated_fields(self) -> tuple:
        """未标定字段名（报告里显式列出，验收者不必读文档）。"""
        return ('damage_limit',) if not self.damage_limit_calibrated else ()


#: v0.1.33 的全局常量，逐字段落成默认档。
#:
#: **除 ``damage_limit`` 外，这份默认值刻意等于 v0.1.33 的全局常量**
#: —— 分档机制上线当天，全类型行为与 v0.1.33 逐位相同；放宽只能通过
#: :func:`apply_profile` 显式发生，并被 :func:`strictest` 的棘轮记录成
#: 可审计的 ``ratchet_bound``。
#:
#: ``damage_limit`` 是**唯一**改为 ``None`` 的字段（v0.1.38）：v0.1.37 的
#: 标定实测**证否了它可标定**（跨样本口径跨 10 倍，夹逼无解）。其余 6 档
#: 零样本却各持 0.20 并照常判定 —— 同属「未标定」，故一并置 ``None``。
#:
#: v0.1.39 新增星层归一化族 3 字段。它们**不改变判定行为**
#: （``star_flux_normalize=False`` 且 ``damage_limit`` 仍为 ``None``）——
#: 新判据只作为**观测量**并存出数，等劣化组分离点稳定后再翻默认值。
#: 这条「先出数、后翻开关」是刻意的：一次改两处会让读数无法归因。
DEFAULTS = RemovalProfile(
    name='default',
    reachable=True,
    provisional=True,
    damage_limit=None,
    advisory_multiplier=2.0,
    baseline_change=0.10,
    needs_starnet_residual=0.02,
    needs_starnet_damage=0.15,
    # v0.1.39：星层归一化**默认关闭**。新判据已实测更准（健康组极差
    # 0.3393 → 0.0138，24.6×），但它只改了「怎么算」，没有改「判不判」——
    # damage_limit 仍是 None ⇒ 门禁依旧走 uncalibrated。先并存出数，
    # 等劣化组分离点稳定后再翻默认值（见标定文档 §6.3）。
    star_flux_normalize=False,
    star_flux_baseline_change=0.02,
    min_star_free_subject_level=1e-8,
    # v0.1.44：相对判据阈值。
    #
    # 标定（6 份真实 StarNet2 产物，Rec.709 口径，合成劣化）：
    #   beta=0.02 → delta 下界 0.0071（已可检出）
    #   beta=0.05 → 0.0183   beta=0.10 → 0.0366   beta=0.20 → 0.0748
    # 健康态 delta 按构造为 0，故分离区间是 (0, 0.0071)。
    #
    # 取 0.005 = 分离上界的 **0.70x**，留 30% 余量而非贴着边界
    # （§6.7 已警告过「刚好可分」等于没标定）。该值可检出 beta>=0.02 的劣化。
    #
    # ⚠ delta 来自**合成**劣化构造，尚无真实误伤样本上界佐证。
    #   该值属工程标定，样本积累后应复核。
    damage_delta_limit=0.005,
    min_quality_score=0.45,
    repair_weight_residual=20.0,
    repair_weight_damage=2.0,
    repair_weight_gradient=3.0,
    repair_cap_residual=0.5,
    repair_cap_damage=0.45,
    repair_cap_gradient=0.2,
    quality_band_good=0.7,
    quality_band_marginal=0.4,
    mask_max_coverage=0.60,
    min_subject_separation=1.30,
    max_score_growth=0.0,
)


#: 逐字段的「更严」方向。``True`` = 数值越大越严。
#:
#: **不允许在调用点临时判断方向** —— 那正是 v0.1.33 把阈值散落成
#: 模块级常量时的隐患：改一处忘了另一处，棘轮就会放松某一族。
TIGHTER_IS_HIGHER = {
    'damage_limit': False,
    'advisory_multiplier': False,
    # baseline_change 是从 raw_change 里**减掉**的基线，
    # 调**小**才是「更严」（少减一点 = 判出的误伤更大）。
    'baseline_change': False,
    'needs_starnet_residual': False,
    'needs_starnet_damage': False,
    # 归一化族的棘轮方向：
    #   star_flux_normalize —— bool 不可比大小。False 是**旧口径**
    #   （已被实测证明跨样本不可比），True 是**新口径**，故 True 视为更严。
    'star_flux_normalize': True,
    # 死区调**小**才是更严（少减一点 = 判出的误伤更大），同 baseline_change。
    'star_flux_baseline_change': False,
    # 主体基准下限：抬**高**才是更严（更多数据落不可测）。
    'min_star_free_subject_level': True,
    'damage_delta_limit': False,
    'min_quality_score': True,
    'repair_weight_residual': True,
    'repair_weight_damage': True,
    'repair_weight_gradient': True,
    # 上限与权重反向：权重越大越严，上限越小越严。
    'repair_cap_residual': False,
    'repair_cap_damage': False,
    'repair_cap_gradient': False,
    'quality_band_good': True,
    'quality_band_marginal': True,
    'min_subject_separation': True,
    # max_score_growth 是「允许重试让质量分变差多少」。调**大**= 更松，
    # 所以更严方向是 min。默认已是 0.0（零容忍）⇒ 该字段的棘轮**饱和**：
    # 任何正值都被挡回 0.0，不产生 ratchet_bound 条目（值本来相同）。
    'max_score_growth': False,
}

assert set(TIGHTER_IS_HIGHER) | DEPRECATED_FIELDS == {
    f.name for f in fields(RemovalProfile)
    if f.name not in ('name', 'reachable', 'provisional')
}, 'TIGHTER_IS_HIGHER / DEPRECATED_FIELDS 与 RemovalProfile 字段不同步'


#: 目标类型 → 档位名。未列出的类型走 :data:`DEFAULTS`。
#:
#: 键空间 = ``target_rules._KNOWN_TYPES``（实测 10 类）。星团 3 类**不建档位**
#: （用户裁决）—— 它们进不了门禁，建档位是给不可达路径写规则。
#:
#: ⚠ **档位当前全等于 DEFAULTS**（只有 ``name`` 不同）：所有阈值都还没被任何
#: 数据标定过，``damage_limit`` 恒为 ``None`` ⇒ 误伤一律走 ``uncalibrated``。
#: 按类型分档的**机制**已就位，**数值**仍待标定；看到 ``emission_nebula``/
#: ``galaxy`` 等档名不要误以为该档已收紧。见 ``references/star_damage_gate_calibration.md``。
_TARGET_TYPE_PROFILES = {
    'emission_nebula': 'emission_nebula',
    'reflection_nebula': 'reflection_nebula',
    'dark_nebula': 'dark_nebula',
    'galaxy': 'galaxy',
    'planetary_nebula': 'planetary_nebula',
    # supernova_remnant / wide_field 建**空档**（全部沿用 DEFAULTS）
    # 但标 provisional：这两类无现成母版，长期不可标定。
    'supernova_remnant': 'provisional',
    'wide_field': 'provisional',
}

#: 星团 3 类显式登记为「不可达」，让 :func:`get_profile` 能给出
#: 明确的 ``reachable=False`` 而不是静默落回默认档。
UNREACHABLE_TYPES = frozenset(STAR_DOMINANT_TYPES)

PROFILES = {name: replace(DEFAULTS, name=name) for name in set(
    _TARGET_TYPE_PROFILES.values()) | {'default'}}


def normalize_target_type(target_type):
    return str(target_type or '').strip().lower().replace(' ', '_')


def get_profile(target_type=None, profile=None):
    """目标类型 → 阈值档。

    优先级：显式传入的 ``profile`` > 目标类型查表 > :data:`DEFAULTS`。
    ``target_type`` 为 None（未指定）时落默认档 —— CLI 不暴露该参数，
    默认即「按旧行为」。
    """
    if profile is not None:
        return profile
    if not target_type:
        return DEFAULTS
    normalized = normalize_target_type(target_type)
    if normalized in UNREACHABLE_TYPES:
        return replace(PROFILES['default'], name=normalized, reachable=False)
    return PROFILES.get(_TARGET_TYPE_PROFILES.get(normalized, 'default'),
                        DEFAULTS)


def strictest(base, override, tighter_is_higher):
    """取两者中更严的一个。任一为 ``None`` 时返回 ``base``。

    ``base is None`` 表示「该字段未标定」⇒ 保持未标定而非采用 ``override``。
    **「未标定」不等于「无限松」** —— 允许具体值覆盖它，等于把「没有阈值」
    悄悄变成「有了一个来源不明的阈值」，比标不出来更危险。

    棘轮方向由 :data:`TIGHTER_IS_HIGHER` 声明，**不允许调用点临时传
    布尔** —— 方向的错误是静默的（放松了却显示「已应用」）。
    """
    if override is None or base is None:
        return base
    return (max(base, override) if tighter_is_higher
            else min(base, override))


def apply_profile(base, override, field_name):
    """把 ``override`` 的单字段并入 ``base``，返回 ``(新档, 是否被棘轮挡住)``。

    弃用字段与未知字段一律不并入 —— 弃用字段并入会让人误以为
    「掩膜退化」场景已受控（该判据从不触发，见 :data:`DEPRECATED_FIELDS`）。
    """
    if field_name in DEPRECATED_FIELDS or field_name not in TIGHTER_IS_HIGHER:
        return base, False
    current = getattr(base, field_name)
    if current is None:
        # 未标定字段不可被具体值覆盖，且此处 ``bound=True`` ——
        # 让「试图给未标定字段定值」在报告里可见。否则棘轮会把 None
        # 静默变成一个数字，uncalibrated 状态随之消失。
        return base, True
    winner = strictest(current, override, TIGHTER_IS_HIGHER[field_name])
    bound = winner != override and override is not None
    if winner == current:
        return base, bound
    return replace(base, **{field_name: winner}), bound


def profile_report(profile, target_type=None, ratchet_bound=(),
                   profile_warnings=()):
    """构造进 ``result.json`` 的 ``star_removal`` 报告段（纯增量）。"""
    return {
        'target_type': target_type,
        'profile_name': profile.name,
        'reachable': profile.reachable,
        'provisional': profile.provisional,
        'applied': profile.as_applied(),
        # 显式告诉验收者「哪些数字不存在」—— 否则 applied 里的 null
        # 会被读成「阈值是 0」或「阈值极宽松」，方向完全相反。
        'damage_limit_calibrated': profile.damage_limit_calibrated,
        'uncalibrated': list(profile.uncalibrated_fields),
        'ratchet_bound': sorted(ratchet_bound),
        'profile_warnings': list(profile_warnings),
        'deprecated': sorted(DEPRECATED_FIELDS),
        'calibration_ref': 'star_damage_gate_calibration.md',
    }


__all__ = [
    'RemovalProfile', 'DEFAULTS', 'PROFILES', 'DEPRECATED_FIELDS',
    'TIGHTER_IS_HIGHER', 'UNREACHABLE_TYPES', 'PROFILE_TARGET_TYPES',
    'get_profile', 'strictest', 'apply_profile', 'profile_report',
    'normalize_target_type',
]

#: 公开的目标类型 → 档位映射（供测试与文档核对，不参与逻辑）。
PROFILE_TARGET_TYPES = dict(_TARGET_TYPE_PROFILES)

#: 键空间自校验：表里的每个 target_type 都必须是已知类型，否则分档
#: 永远不会生效（静默失效比报错更难查）。
_UNKNOWN_IN_TABLE = set(_TARGET_TYPE_PROFILES) - set(_KNOWN_TYPES)
if _UNKNOWN_IN_TABLE:
    raise RuntimeError(
        'star_removal_profiles references unknown target types: %s'
        % ', '.join(sorted(_UNKNOWN_IN_TABLE)))
