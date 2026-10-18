#!/usr/bin/env python3
"""失败处置策略（failure policy）。

## 要解决的问题

`pipeline.py` 此前在拉伸链全拒绝后做的是**无条件截断**：

    stretch_diagnostics['skipped_after_failure'] = [拉伸之后的全部步骤]
    steps = [拉伸之前的步骤]

后果：**用户拿不到任何产物**。线性域的dbe / color / pre_denoise / star_remove
已经算完、TIFF 已落盘，却因为最后一步拉伸失败而全部丢弃；`star_combine` 之类的
后续步骤也被跳过。用户合理的需求「拉伸失败，但我想看看前面校色后的结果」无法
满足 —— 唯一的选择是重跑，而重跑会再失败一次。

## 三个处置动作

借鉴 starun 侧 `processing_parameters.py:223-240` 的 ``stageN_failure_action``
三选项（`auto_fallback` / `preserve_review` / `stop`）与其
``strictness="stop_is_stricter"`` 规则，但按我方实际情况重新定义：

``AUTO_FALLBACK``（默认，保持现状语义）
    回退到该阶段的保守输入并**继续**后续步骤。能出图，但��图可能不是用户
    想要的（拉伸是核心步骤，回退意味着色调不对）。

``PRESERVE_REVIEW``
    回退到保守输入，**保留全部已落盘的中间产物与诊断**，并把状态标记为
    ``degraded``（可见、可审、不阻断）。这是本模块新增的中间态 ——
    此前只有「出图」与「什么都不出」两种选择。

``STOP``
    硬失败，不出图，状态 ``failed``。用于「宁可不交付也不要错图」的场景。

## stop_is_stricter

一旦任一阶段选了 ``STOP``，其他阶段的 ``auto_fallback`` / ``preserve_review``
**一律失效**（降为 ``stop``）。理由：混合语义（「前面自动回退、后面突然终止」）
产生的结果最难解释 —— 用户既不知道哪些阶段过了，也不知道哪个是转折点。
``resolve_failure_actions`` 强制这一规则。

## degraded 状态

``DEGRADED`` 是 ``success`` 与 ``review_required`` 之间的中间态：
产物可用、有降级记录、**不阻断** ``accept``，但调用方能看出「这不是一次
干净的处理」。

**为什么要单独一态**：此前 ``quality_state.status`` 只有二值
（``review_required`` / ``success``），而拉伸有 4 级回退链 —— 第 2 级
``masked`` 成功与一次成功的 ``pi_mtf`` **完全无法区分**。调用方无法知道
结果经过降级，也就无从判断是否该人工介入。``degraded`` 补上了这个信息。

注意与 ``gate_authority.DIAGNOSTIC_STATUSES`` 里的 ``degraded`` 是**同一概念
的两种载体**：门禁级 degraded（某道门处于退化区）与管线级 degraded
（某阶段走了回退路径）。两者都「可见但不升级」，语义一致。
"""

from __future__ import annotations


# ── 处置动作 ────────────────────────────────────────────────────────
AUTO_FALLBACK = "auto_fallback"
PRESERVE_REVIEW = "preserve_review"
STOP = "stop"

FAILURE_ACTIONS = (AUTO_FALLBACK, PRESERVE_REVIEW, STOP)

#: 动作 → 管线级 status。
#: - AUTO_FALLBACK 继续跑完 ⇒ 成功出图（但记入 degraded_stages）
#: - PRESERVE_REVIEW 保留现场并要求复核 ⇒ degraded
#: - STOP 不交付 ⇒ failed
ACTION_TO_STATUS = {
    AUTO_FALLBACK: None,          # 由调用方按「是否真的回退过」决定
    PRESERVE_REVIEW: 'degraded',
    STOP: 'failed',
}

#: 动作 → 是否保留已落盘的中间产物。
ACTION_PRESERVES_ARTIFACTS = {
    AUTO_FALLBACK: True,
    PRESERVE_REVIEW: True,
    STOP: False,
}

#: 阶段 → 处置动作。**新增阶段时必须在此登记**，否则
#: ``resolve_failure_actions`` 的覆盖检查会在 import 期报错。
DEFAULT_FAILURE_ACTIONS = {
    'stretch': AUTO_FALLBACK,
}

#: 允许逐阶段覆盖的动作（与上表同一取值域，此处只声明「可被 cfg 覆盖」）。
OVERRIDABLE_STAGES = frozenset(DEFAULT_FAILURE_ACTIONS)


class FailurePolicyError(ValueError):
    """处置动作非法或覆盖不合法。"""


def normalize_action(value, stage=None):
    """把外部输入规整为合法动作。

    非法值**不静默回落**到默认 —— 与 ``target_rules`` 的处理一致：
    处置策略是「宁可不交付也不出错图」的最后一道闸，回落会把用户的
    显式意图变成别的行为。非法时抛 :class:`FailurePolicyError`。
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text not in FAILURE_ACTIONS:
        raise FailurePolicyError(
            'invalid failure action %r for stage %r; expected one of %s'
            % (value, stage, ', '.join(FAILURE_ACTIONS)))
    return text


def resolve_failure_actions(stage, requested=None, overrides=None):
    """解析某阶段实际生效的动作，并施加 ``stop_is_stricter``。

    参数
    ----
    stage : str
        步骤标识符。
    requested : str,可选
        本次运行的显式请求（通常来自 ``cfg``）。``None`` 表示不覆盖。
    overrides : dict,optional
        全局覆盖，形如 ``{'stretch': 'stop'}``。任一阶段为 ``stop``
        时触发 ``stop_is_stricter``。

    返回
    ----
    str
        实际生效的动作，必属于 :data:`FAILURE_ACTIONS`。
    """
    action = normalize_action(requested, stage) or DEFAULT_FAILURE_ACTIONS.get(
        stage, AUTO_FALLBACK)
    stop_requested = action == STOP or any(
        normalize_action(value, key) == STOP
        for key, value in (overrides or {}).items())
    if stop_requested:
        # stop_is_stricter：一旦全局要求停止，本阶段不能自动回退。
        return STOP
    return action


def preserves_artifacts(action):
    """该动作是否保留已落盘的中间产物。"""
    return ACTION_PRESERVES_ARTIFACTS.get(
        normalize_action(action), True)


def action_status(action, fallback_used=False):
    """动作 → 管线级 status。

    ``AUTO_FALLBACK`` 的 status 取决于是否真的回退过：回退过就是
    ``degraded``（信息不能丢），没回退就是 ``success``。
    """
    normalized = normalize_action(action)
    if normalized is None:
        return None
    if normalized == AUTO_FALLBACK:
        return 'degraded' if fallback_used else 'success'
    return ACTION_TO_STATUS[normalized]


def _validate_registry():
    """import 时自校验：动作表与阶段登记表必须自洽。"""
    for action in DEFAULT_FAILURE_ACTIONS.values():
        if action not in FAILURE_ACTIONS:
            raise RuntimeError(
                'failure policy default uses unknown action: %r' % (action,))
    for action, status in ACTION_TO_STATUS.items():
        if action not in FAILURE_ACTIONS:
            raise RuntimeError(
                'ACTION_TO_STATUS has non-action key: %r' % (action,))
        if status is not None and status not in PIPELINE_STATUSES:
            raise RuntimeError(
                'action %r maps to unknown pipeline status %r'
                % (action, status))
    for action in FAILURE_ACTIONS:
        if action not in ACTION_PRESERVES_ARTIFACTS:
            raise RuntimeError(
                'action %r is missing from ACTION_PRESERVES_ARTIFACTS'
                % (action,))
    unknown = OVERRIDABLE_STAGES - set(DEFAULT_FAILURE_ACTIONS)
    if unknown:
        raise RuntimeError(
            'OVERRIDABLE_STAGES declares unregistered stages: %s'
            % ', '.join(sorted(unknown)))


#: 管线级 status 的合法取值。与 ``gate_authority`` 的门禁级 status 是
#: **不同集合**（门禁还有 degraded/unavailable/uncalibrated）—— 不要混用。
PIPELINE_STATUSES = frozenset({
    'success', 'degraded', 'review_required', 'failed',
})

#: 不阻断 ``accept`` 的 status。``degraded`` 在此 —— 可见、可审、可接受。
NON_BLOCKING_STATUSES = frozenset({'success', 'degraded'})

_validate_registry()
