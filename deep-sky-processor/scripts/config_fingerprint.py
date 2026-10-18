#!/usr/bin/env python3
"""配置指纹：判断「这份产物是用哪套配置产出的」。

## 要解决的问题

`quality_state` 绑定的是**产物 SHA-256** —— 能证明「这个文件没被改过」，但
**不能证明「产生它的配置没被改过」**。于是有一条静默失效路径：

    1. 用配置 A 跑出产物 P，`P.meta.json` 里记了 issues
    2. 改配置 A → 配置 B（只改一个拉伸参数）
    3. 重跑，产物路径不变（或复用旧 sidecar）
    4. `build_quality_state(..., parent_state=旧state)` 继承���**配置 A 的issues**
       ⇒ 配置 B 的产物背上配置 A 的历史问题

这条路径在 Agent-in-the-loop 里尤其容易触发：`agent_workflow` 的
``artifact_state`` 跨 action 累积、``parent_state`` 链一路继承，而
`initialize_session` 允许新 session 复用同一个 ``session_dir``。

## 累计指纹

借鉴 starun 侧 ``task_plan.build_resume_fingerprints``（`task_plan.py:208-229`）：
**Stage N 的指纹 = hash(Stage 1..N 的全部配置)**。

    fingerprint(N) = sha256(canonical({
        "schema":       CONFIG_FINGERPRINT_SCHEMA,
        "input":        <输入指纹>,
        "stages":       {1: {...}, 2: {...}, ..., N: {...}},
    }))

「改 Stage 2 参数 ⇒ Stage 5 的断点自动失效」就是这条性质的直接后果。
不��要断点机制，它本身就是**质量继承的合法性前提**。

## 两种粒度

- **阶段级**（:func:`stage_fingerprint`）：按 ``step_order`` 把参数归到
  各阶段，累加到该阶段为止。用于「这个产物链的上游配置有没有变过」。
- **单阶段**（:func:`step_fingerprint`）：只覆盖该阶段自己的参数。用于
  「重跑这一步时配置变没变」的轻量判断。

## 边界与诚实声明

- **不覆盖运行结果**。指纹只管配置；产物内容仍由 ``artifact_sha256`` 负责。
  两者是**正交**的证据，缺一不可。
- **不覆盖源码**。代码版本由 ``runtime_info.source_fingerprint()``（对全部
  ``scripts/*.py`` + ``references/**`` 算整体指纹）负责。本模块**不**重复
  那个职责 —— 两个指纹语义不同：source 管「跑的是哪版代码」，config 管
  「用的是哪套参数」。改代码会让 source 变、config 不变，这是正确行为。
- **阶段归属是启发式的**。参数按前缀与已知清单归阶段，未识别的键归入
  ``_unassigned`` 桶并**照样参与哈希** —— 宁可多算不可漏算，漏算会让
  改了某参数却算��指纹不变。
"""

from __future__ import annotations

import hashlib
import json

from stage_registry import ALL_STEP_KEYS, step_order


CONFIG_FINGERPRINT_SCHEMA = "deep-sky-processor.config-fingerprint/v1"

#: 不参与指纹的键 —— 运行时状态或与产物内容无关的开关。
#: - ``completed``/``used``：ExecutionConfig 的追踪容器
#: - ``phase``：当前阶段标记
EXCLUDED_KEYS = frozenset({
    'completed', 'used', 'phase',
})

#: 参数 → 阶段的显式映射（按已知前缀/精确键）。未命中的键进 ``_unassigned``。
STEP_KEY_MAP = {
    # dbe
    'dbe_degree': 'dbe', 'dbe_method': 'dbe', 'dbe_pctl_low': 'dbe',
    'dbe_pctl_high': 'dbe',
    # color
    'star_balance_strength': 'color', 'oiii_blue_injection': 'color',
    # stellar_repair
    'stellar_repair': 'stellar_repair', 'stellar_repair_mode': 'stellar_repair',
    'stellar_repair_strength': 'stellar_repair',
    # linear_deconv
    'linear_deconv': 'linear_deconv', 'deconv_iterations': 'linear_deconv',
    # pre_denoise
    'pre_denoise_lum': 'pre_denoise', 'pre_denoise_chroma': 'pre_denoise',
    'deepsnr': 'pre_denoise', 'deepsnr_path': 'pre_denoise',
    'deepsnr_timeout': 'pre_denoise', 'deepsnr_strength': 'pre_denoise',
    'skip_final_denoise': 'pre_denoise',
    # star_remove
    'star_threshold': 'star_remove', 'use_starnet': 'star_remove',
    'starnet_path': 'star_remove', 'starnet_stride': 'star_remove',
    'starnet_domain': 'star_remove', 'starnet_timeout': 'star_remove',
    'external_starless': 'star_remove',
    'external_starless_domain': 'star_remove',
    'external_starless_midtones': 'star_remove',
    'external_denoised': 'star_remove',
    'starnet_recommendation': 'star_remove',
    'export_starnet_payload': 'star_remove',
    'export_starnet_payload_domain': 'star_remove',
    'starnet_failure_action': 'star_remove',
    # target_type 决定去星门禁用哪一档阈值（见 star_removal_profiles），
    # 不归阶段就落进 _unassigned 桶 —— 虽仍参与哈希，但不会出现在
    # describe() 的 star_remove 行里，验收时看不出「门禁换档了」。
    'target_type': 'star_remove',
    # stretch
    'stretch_method': 'stretch', 'stretch_factor': 'stretch',
    'stretch_gamma': 'stretch', 'stretch_target_bg': 'stretch',
    'stretch_headroom': 'stretch', 'stretch_subject_midtones': 'stretch',
    'stretch_highlight_pctl': 'stretch',
    'stretch_shadow_pctl': 'stretch', 'ghs_sp': 'stretch',
    'ghs_b': 'stretch', 'ghs_hp': 'stretch', 'ghs_lp': 'stretch',
    'ghs_c': 'stretch', 'ghs_gamma': 'stretch', 'ghs_preserve_lp': 'stretch',
    'ghs_preserve_hp': 'stretch', 'ghs_protect_strength': 'stretch',
    'ghs_protect_lp': 'stretch', 'ghs_protect_hp': 'stretch',
    'stretch_policy_version': 'stretch',
    # 跨阶段的全局参数：归到 dbe（累积起点）以保证它进入所有下游指纹。
    # target_bg 同时被拉伸与回退路径使用；input_is_linear 决定整个线性链
    # 是否适用 —— 两者改动都应使下游指纹失效。
    'target_bg': 'dbe', 'input_is_linear': 'dbe',
    # star_process / star_reduce / star_combine
    'star_stretch_factor': 'star_process',
    'star_curves_midtones': 'star_process',
    'star_curves_shadow': 'star_process', 'star_curves_highlight': 'star_process',
    'star_scnr_strength': 'star_process', 'star_saturation': 'star_process',
    'star_reduction': 'star_reduce', 'star_blend_mode': 'star_reduce',
    'halo_guard': 'star_reduce', 'star_combine_strength': 'star_combine',
    # enhance
    'enhance_mode': 'enhance', 'hdr_strength': 'enhance',
    'clahe_clip': 'enhance', 'clahe_kernel': 'enhance',
    'clahe_mask_gamma': 'enhance', 'hdr_failure_action': 'enhance',
    # sharpen
    'sharpen_amount': 'sharpen', 'sharpen_radius': 'sharpen',
    # final_color
    'saturation': 'final_color', 'color_mode': 'final_color',
    # style
    'style': 'style', 'style_strength': 'style',
    # local_enhance
    'local_center': 'local_enhance', 'local_radius': 'local_enhance',
    'local_strength': 'local_enhance',
    # external_detail
    'external_detail_strength': 'external_detail',
    # final_denoise
    'final_denoise_lum': 'final_denoise', 'final_denoise_chroma': 'final_denoise',
    # background_neutralization
    'background_neutralize_lock': 'background_neutralization',
}

#: 走前缀匹配的键（精确映射里没有的）。
STEP_KEY_PREFIXES = (
    ('dbe_', 'dbe'),
    ('ghs_', 'stretch'),
    ('star_', 'star_process'),
    ('deepsnr_', 'pre_denoise'),
    ('starnet_', 'star_remove'),
    ('hdr_', 'enhance'),
    ('clahe_', 'enhance'),
    ('deconv_', 'linear_deconv'),
    ('sharpen_', 'sharpen'),
    ('style_', 'style'),
    ('local_', 'local_enhance'),
    ('external_detail_', 'external_detail'),
    ('final_denoise_', 'final_denoise'),
    ('stellar_repair_', 'stellar_repair'),
    ('background_neutralize_', 'background_neutralization'),
    ('plate_solve', 'dbe'),
)

UNASSIGNED = '_unassigned'


def canonical_payload(value):
    """稳定序列化：排序键、无空格、保留中文、容忍不可序列化对象。"""
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, default=str)


def payload_hash(value):
    """配置载荷 → 20 位短摘要（与 issue_id 的长度一致，便于并排看）。"""
    return hashlib.sha256(canonical_payload(value).encode()).hexdigest()[:20]


def input_fingerprint(input_path):
    """输入产物指纹。与配置正交 —— 换输入必须让全部断点失效。"""
    from quality_state import artifact_digest
    try:
        return artifact_digest(input_path)
    except OSError:
        return None


def assign_step(key):
    """参数键 → 所属阶段。未识别归 :data:`UNASSIGNED`。

    未识别的键**不丢弃而是进 ``_unassigned`` 桶**，因为该桶照样参与哈希 ——
    漏算会让「改了某参数却算出同样指纹」。
    """
    if key in EXCLUDED_KEYS:
        return None
    mapped = STEP_KEY_MAP.get(key)
    if mapped:
        return mapped
    for prefix, step in STEP_KEY_PREFIXES:
        if key.startswith(prefix):
            return step
    return UNASSIGNED


def split_config(cfg):
    """把配置 dict 拆成 ``{阶段: {参数: 值}}``。

    ``ExecutionConfig`` 是 dict 子类，直接迭代即可；``used``/``completed``/
    ``phase`` 三个追踪容器被排除。
    """
    buckets = {}
    for key, value in dict(cfg or {}).items():
        step = assign_step(key)
        if step is None:
            continue
        buckets.setdefault(step, {})[key] = value
    return buckets


def cumulative_config(buckets, upto_order):
    """累积到指定阶段（含）的配置切片。"""
    cumulative = {}
    for step in ALL_STEP_KEYS:
        order = step_order(step)
        if order is None or order > upto_order:
            continue
        if step in buckets:
            cumulative[str(order)] = buckets[step]
    return cumulative


def stage_fingerprint(cfg, input_path=None, upto_step='stretch'):
    """累计配置指纹：Stage N = hash(Stage 1..N 的全部配置)。

    ``upto_step`` 给出累计终点（用主流程步骤名）。指纹相同的两个产物，
    其「上游配置 + 输入」必然一致 —— 这是质量继承与断点复用的合法性前提。
    """
    order = step_order(upto_step)
    if order is None:
        raise ValueError(
            'cumulative fingerprint needs a main step, got %r' % (upto_step,))
    buckets = split_config(cfg)
    payload = {
        'schema': CONFIG_FINGERPRINT_SCHEMA,
        'input': input_fingerprint(input_path) if input_path else None,
        'upto_step': upto_step,
        'stages': cumulative_config(buckets, order),
        'unassigned': buckets.get(UNASSIGNED, {}),
    }
    return payload_hash(payload)


def step_fingerprint(cfg, step):
    """单阶段指纹：只看该阶段自己的参数。"""
    buckets = split_config(cfg)
    return payload_hash({
        'schema': CONFIG_FINGERPRINT_SCHEMA,
        'step': step,
        'params': buckets.get(step, {}),
    })


def describe(cfg, upto_step='stretch'):
    """人类可读的累积配置清单（进报告，便于人工核对「用的是哪套参数」）。"""
    buckets = split_config(cfg)
    order = step_order(upto_step)
    rows = []
    for step in ALL_STEP_KEYS:
        step_order_value = step_order(step)
        if step_order_value is None or step_order_value > order:
            continue
        params = buckets.get(step)
        if params:
            rows.append({'step': step, 'order': step_order_value,
                         'params': dict(sorted(params.items()))})
    if buckets.get(UNASSIGNED):
        rows.append({'step': UNASSIGNED, 'order': None,
                     'params': dict(sorted(buckets[UNASSIGNED].items()))})
    return rows


def matches(expected, actual):
    """两个指纹是否一致（任一为 None 视为不匹配 —— 无指纹不等于匹配）。"""
    return bool(expected) and bool(actual) and expected == actual
