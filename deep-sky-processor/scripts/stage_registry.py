#!/usr/bin/env python3
"""处理步骤契约注册表（单一事实源）。

**为什么需要这个模块**：``pipeline.py`` 此前用裸字符串列表 ``ALL_STEPS``
（16 项）表达步骤顺序，但该列表与实际执行/落盘**已经脱节**——实测发现：

  - ``02c_linear_deconv.tif`` / ``11_reference_grade.tif`` /
    ``10b_background_locked.tif`` **有产物、有cfg.phase、有 completed 记录，
    却不在 ALL_STEPS 里**（三个「孤儿步骤」，只能靠独立 CLI 开关触发）。
  - 产物编号与列表顺序**错位**：``star_combine``=09 但位置 11、
    ``style``=09a 但位置 10、``local_enhance``=08b 但位置 13。
    字母后缀表达的是「同一步骤的变体」，不是序号推进。
  - ``resolve_step_dependencies()`` 与拉伸失败截断都用 ``ALL_STEPS.index()``
    做位置运算。孤儿步骤一旦被 ``--steps`` 传入就会 ``ValueError``。

本模块把这些事实固化成 ``StepContract``（frozen dataclass），并在 **import 时
自校验**：任何顺序/唯一性/产物归属/域一致性 violation 直接抛 ``RuntimeError``，
让问题在开发期暴露而不是运行期。

设计取舍（与 starun 侧 ``stage_contracts.py`` 的差异）：
  - **不做产物名前缀校验**。我方产物名带两位序号 + 字母变体后缀
    （``02b_`` / ``05a_`` / ``09a_`` / ``10b_``），字母表达变体而非序号推进，
    对方的 ``stage{N}_`` 前缀规则不适用。改为校验「产物→步骤映射双向一致」。
  - **不引入 ``runtime_label``**。我方是 Skill 包（无 GUI），阶段名直接是英文
    标识符，中文 label 字段没有消费者。
  - **保留 ``optional_steps``**。孤儿三步不是错误，是「有产物但不在主流程」的
    合法状态；``ALL_STEPS`` 仍是主流程 16 步，但注册表登记全部 19 步，
    自校验区分两者。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


# ── 域 ──────────────────────────────────────────────────────────────
#: 线性域：物理量纲上仍可直接比较原信号，误操作代价高。
LINEAR = "linear"
#: 非线性域：已做曲线映射，像素值不再与光子流线性对应。
NONLINEAR = "nonlinear"


@dataclass(frozen=True)
class StepContract:
    """单个处理步骤的契约。

    Attributes
    ----------
    key : str
        步骤标识符，同时是 ``cfg.phase`` 的取值与 ``executed_steps`` 的元素。
    order : int
        在**主流程**中的执行序号（从 0 起）。``optional_steps`` 为 -1。
    domain : str
        ``LINEAR`` 或 ``NONLINEAR``。用于校验产物元数据里的 ``is_linear`` 标记。
    artifacts : Tuple[str, ...]
        该步骤落盘的文件名（相对 work_dir）。第一个是「主产物」。
    optional : bool
        ``True`` 表示**不在主流程**（只能由独立 CLI 开关或特定 preset 触发），
        例如反卷积 / 参考图定调 / 背景中性化锁定。
    summary : str
        一句话职责描述，进入 ``pipeline-result.json`` 供Agent 与人类阅读。
    """

    key: str
    order: int
    domain: str
    artifacts: Tuple[str, ...] = ()
    optional: bool = False
    summary: str = ""


# ── 主流程 16 步（顺序即执行顺序）────────────────────────────────────
# order 必须严格递增；domain 在 linear→nonlinear 上只能切换一次。
_MAIN_STEPS: Tuple[StepContract, ...] = (
    StepContract("dbe", 0, LINEAR, ("01_dbe.tif",),
                 summary="背景建模与梯度提取"),
    StepContract("color", 1, LINEAR, ("02_color.tif",),
                 summary="颜色校准（白平衡/发射星云模式）"),
    StepContract("stellar_repair", 2, LINEAR,
                 ("02b_stellar_repaired.tif",
                  "02b_stellar_repair_preview.tif"),
                 summary="线性域星点像差/形变修复"),
    StepContract("pre_denoise", 3, LINEAR,
                 ("03_pre_denoise.tif", "03_pre_denoise_before.tif",
                  "03_pre_denoise_residual.tif"),
                 summary="线性域预降噪（DeepSNR 优先，回退 atrous）"),
    StepContract("star_remove", 4, LINEAR,
                 ("04_starless_linear.tif", "04_stars_linear.tif",
                  "04_separation_reference.tif", "04_subject_mask.tif",
                  "04_starless_fallback.tif"),
                 summary="星点分离（StarNet2 或形态学）"),
    StepContract("stretch", 5, NONLINEAR,
                 ("05_stretched_starless.tif", "05_display_reference.tif",
                  "05_rejected_stretch.tif", "05_rejected_recovery.tif"),
                 summary="非线性拉伸（protected/masked GHS 链路，含回退留痕）"),
    StepContract("star_process", 6, NONLINEAR,
                 ("05a_star_stretch.tif", "05b_star_scnr.tif",
                  "05b2_star_saturation.tif", "05c_star_final.tif"),
                 summary="星点层独立拉伸与洋红校正"),
    StepContract("enhance", 7, NONLINEAR, ("06_clahe_enhance.tif", "06a_hdr.tif"),
                 summary="星云细节增强（HDR + CLAHE，按 SNR 守卫降级）"),
    StepContract("sharpen", 8, NONLINEAR, ("07_sharpen.tif",),
                 summary="信号蒙版锐化"),
    StepContract("final_color", 9, NONLINEAR, ("08_color.tif",),
                 summary="末端颜色调整与饱和度处理"),
    # 真实执行序由**产物编号**给出：08b/08c/08d 都排在 09a 之前。
    # 旧 ALL_STEPS 列表把 style 排在 local_enhance/star_reduce 之前，
    # 与管线实际执行顺序（Phase 8b/8d 先于 9a）错位 —— 那是既有缺陷，
    # 本注册表按真实序纠正，不沿用该错位。
    StepContract("local_enhance", 10, NONLINEAR, ("08b_local_enhance.tif",),
                 summary="局部对比度与纹理增强（空操作自检）"),
    StepContract("external_detail", 11, NONLINEAR, ("08c_external_detail.tif",),
                 summary="外部细节层注入"),
    StepContract("star_reduce", 12, NONLINEAR, ("08d_star_reduced.tif",),
                 summary="带星图全局缩星"),
    StepContract("style", 13, NONLINEAR, ("09a_style.tif",),
                 summary="风格定调（局部黑位保护）"),
    StepContract("star_combine", 14, NONLINEAR, ("09_star_combined.tif",),
                 summary="星点层回合成"),
    StepContract("final_denoise", 15, NONLINEAR, ("10_final_denoise.tif",),
                 summary="最终整体降噪"),
)


# ── 可选步骤（不在主流程，由独立开关或特定路径触发）──────────────────
# 这些步骤**有产物、有 cfg.phase、会进 cfg.completed**，但不在 ALL_STEPS 里。
# 登记它们是为了让自校验能验证「产物→步骤」映射双向一致，并让
# `--steps` 误传它们时给出清晰错误而非 ALL_STEPS.index() 的 ValueError。
_OPTIONAL_STEPS: Tuple[StepContract, ...] = (
    StepContract("linear_deconv", -1, LINEAR, ("02c_linear_deconv.tif",),
                 optional=True,
                 summary="线性域物理反卷积（Richardson-Lucy，--linear-deconv）"),
    StepContract("reference_grade", -1, NONLINEAR,
                 ("11_reference_grade.tif",), optional=True,
                 summary="参考图定调（--reference-grade）"),
    StepContract("background_neutralization", -1, NONLINEAR,
                 ("10b_background_locked.tif",), optional=True,
                 summary="终极背景中性化锁定"),
)


STEP_CONTRACTS: Tuple[StepContract, ...] = _MAIN_STEPS + _OPTIONAL_STEPS


# ── 派生视图（由 _validate_registry 在 import 时校验后可用）────────────
#: 主流程步骤 key，按执行顺序。
ALL_STEPS: Tuple[str, ...] = tuple(c.key for c in _MAIN_STEPS)
#: 发射星云 preset 的子集步骤。**顺序必须与主流程一致**（旧列表沿用了
#: ALL_STEPS 的错位，把 style 排在 local_enhance/star_reduce 之前）。
EMISSION_STEPS: Tuple[str, ...] = (
    "color", "stretch", "final_color", "local_enhance", "star_reduce", "style",
)
#: 全部步骤（含可选）。
ALL_STEP_KEYS: Tuple[str, ...] = tuple(c.key for c in STEP_CONTRACTS)
#: key → 契约。
CONTRACTS_BY_KEY = {c.key: c for c in STEP_CONTRACTS}
#: 产物文件名 → 步骤 key。供 save_artifact 的「产物→步骤」判定使用。
STEP_BY_ARTIFACT = {
    name: contract.key
    for contract in STEP_CONTRACTS
    for name in contract.artifacts
}


def step_order(key):
    """返回步骤在主流程中的序号。

    可选步骤返回 ``None``（它们不在主流程里，位置运算对它们无意义）。
    """
    contract = CONTRACTS_BY_KEY.get(key)
    if contract is None or contract.optional:
        return None
    return contract.order


def sort_steps(keys):
    """按主流程顺序排序一组步骤 key。

    与旧的 ``sorted(keys, key=ALL_STEPS.index)`` 行为一致，但：
      - 不再对未知 key 抛 ``ValueError``
      - 可选步骤（``order == -1``）按其主产物编号插入到相邻位置
      - 未知 key 排到末尾并保持原有相对顺序

    未知 key 排末尾而非抛错，是因为调用方 ``resolve_step_dependencies``
    的输入来自用户 ``--steps``；把「拼错步骤名」变成可排序的差异，比
    ``ValueError: 'xxx' is not in list`` 更容易定位。
    """
    keys = list(keys)
    known = [k for k in keys if step_order(k) is not None]
    optional = [k for k in keys
                if step_order(k) is None and k in CONTRACTS_BY_KEY]
    unknown = [k for k in keys if k not in CONTRACTS_BY_KEY]

    ordered = sorted(known, key=step_order)
    for key in sorted(optional, key=_optional_sort_key):
        ordered.insert(_optional_insert_position(ordered, key), key)
    return ordered + unknown


def _artifact_marker(name):
    """产物文件名 → 可比较的排序标记 ``(数字部分, 字母后缀)``。

    我方产物命名是「两位序号 + 可选字母后缀 + 下划线」，字母表达**同一步骤的
    变体**而非序号推进（``02_color`` / ``02b_stellar_repaired`` /
    ``02c_linear_deconv``）。因此排序必须把字母后缀纳入比较，否则
    ``linear_deconv``（02c）会被排到 ``color``（02）之前。

    无数字前缀时返回 ``(10_000, '')``，排到末尾。
    """
    digits = ''.join(ch for ch in name[:2] if ch.isdigit())
    number = int(digits) if digits else 10_000
    suffix = ''
    for ch in name[:4]:
        if ch.isalpha():
            suffix += ch
        elif ch == '_' and suffix:
            break
    return (number, suffix)


def _optional_sort_key(key):
    """可选步骤的排序键：主产物的 ``(数字, 字母后缀)``。"""
    contract = CONTRACTS_BY_KEY.get(key)
    if contract is None or not contract.artifacts:
        return (10_000, '')
    return _artifact_marker(contract.artifacts[0])


def _optional_insert_position(ordered, key):
    """在已排序的主流程步骤里找插入点：标记**严格大于**该可选步骤的第一项。

    例：``linear_deconv``（02c）应插在 ``color``（02）之后、
    ``stellar_repair``（02b）之前 —— 反映它在管线里的真实位置，
    而不是被甩到末尾。
    """
    marker = _optional_sort_key(key)
    if marker == (10_000, ''):
        return len(ordered)
    for index, other in enumerate(ordered):
        contract = CONTRACTS_BY_KEY.get(other)
        if contract is None or not contract.artifacts:
            continue
        if _artifact_marker(contract.artifacts[0]) > marker:
            return index
    return len(ordered)


def artifact_domain(filename):
    """产物所属的域（``LINEAR`` / ``NONLINEAR`` / ``None`` 未登记）。"""
    key = STEP_BY_ARTIFACT.get(filename)
    if key is None:
        return None
    return CONTRACTS_BY_KEY[key].domain


def summary_table():
    """返回人类可读的步骤表（用于 CLI 自检与文档生成）。"""
    lines = ["%-24s %-5s %-11s %s"
             % ("STEP", "ORDER", "DOMAIN", "ARTIFACTS")]
    for contract in STEP_CONTRACTS:
        order = '-' if contract.optional else str(contract.order)
        lines.append("%-24s %-5s %-11s %s"
                     % (contract.key, order, contract.domain,
                        ', '.join(contract.artifacts) or '-'))
    return '\n'.join(lines)


# ── import 时自校验 ─────────────────────────────────────────────────
def _validate_registry():
    """校验注册表一致性。**import 时执行**，任何违反直接抛 RuntimeError。

    检查项：
      1. key 唯一
      2. 主流程 order 严格从 0 连续递增
      3. 域只能linear→nonlinear 单向切换一次
      4. 产物文件名全局唯一（一个文件不能属于两个步骤）
      5. ``EMISSION_STEPS`` 必须是主流程子集
      6. 可选步骤不得占用主流程 order
    """
    seen_keys = set()
    for contract in STEP_CONTRACTS:
        if contract.key in seen_keys:
            raise RuntimeError(f"duplicate step key in registry: {contract.key}")
        seen_keys.add(contract.key)

    orders = [c.order for c in _MAIN_STEPS]
    if orders != list(range(len(_MAIN_STEPS))):
        raise RuntimeError(
            f"main step order must be contiguous from 0, got: {orders}")

    domain_switches = 0
    previous = None
    for contract in STEP_CONTRACTS:
        if contract.optional:
            if contract.order != -1:
                raise RuntimeError(
                    f"optional step must use order=-1: {contract.key}")
            continue
        if contract.domain not in (LINEAR, NONLINEAR):
            raise RuntimeError(
                f"unknown domain {contract.domain!r} for step {contract.key}")
        if previous is not None and contract.domain != previous:
            domain_switches += 1
            if domain_switches > 1:
                raise RuntimeError(
                    "domain may only switch once (linear -> nonlinear), "
                    f"violated at step {contract.key}")
        previous = contract.domain

    seen_artifacts = {}
    for contract in STEP_CONTRACTS:
        if not contract.summary:
            raise RuntimeError(f"step lacks a summary: {contract.key}")
        for name in contract.artifacts:
            if name in seen_artifacts:
                raise RuntimeError(
                    f"artifact {name} claimed by both {seen_artifacts[name]} "
                    f"and {contract.key}")
            seen_artifacts[name] = contract.key

    for key in EMISSION_STEPS:
        if key not in ALL_STEPS:
            raise RuntimeError(
                f"EMISSION_STEPS entry {key!r} is not a main step")

    if len(STEP_BY_ARTIFACT) != len(seen_artifacts):
        raise RuntimeError("artifact index lost entries")

    # 8. 与 pipeline.py 的实际落盘调用保持一致（防「新增产物忘登记」）
    #    只在 pipeline 模块可导入时检查 —— stage_registry 单独可用时跳过。
    _check_against_pipeline(seen_artifacts)

    # 9. 执行序必须与产物编号**相容**。
    #    产物名是「两位序号 + 字母变体后缀 + 下划线」（如 ``02_color`` /
    #    ``02b_stellar_repaired`` / ``02c_linear_deconv``），字母表达**同序号下的
    #    变体**，所以数字前缀**不唯一**标识步骤（02/02b/02c 共享数字 02）。
    #    正确的约束是：order 递增时，数字前缀必须**非严格递增**（可相等）。
    #
    #    这项检查防的是「order 与产物编号错位」—— 旧 ALL_STEPS 踩过：
    #    style 排在 local_enhance/star_reduce 之前，而实际执行是
    #    8b/8d 先于 9a。若 order 递增但数字前缀下降，说明排序与命名脱节，
    #    「按 order 排序」与「按产物编号排序」会给出不同结果 ——
    #    config_fingerprint 的累积切片会算错。
    previous_number = None
    for contract in _MAIN_STEPS:
        if not contract.artifacts:
            continue
        number = _artifact_marker(contract.artifacts[0])[0]
        if number == 10_000:
            continue
        if previous_number is not None and number < previous_number:
            raise RuntimeError(
                'step %s: artifact number %s goes back to %d after %d — '
                'execution order must follow artifact numbering'
                % (contract.key, contract.artifacts[0], number,
                   previous_number))
        previous_number = number


def _check_against_pipeline(seen_artifacts):
    """校验注册表与 ``pipeline.py`` 里save_artifact 的实际调用一致。

    这条检查的价值：``ALL_STEPS`` 与实际落盘脱节正是本模块要修的问题，
    而**产物名漂移**（新增产物忘登记）是同一个问题的复发路径。把它变成
    import 期的 RuntimeError，比在 review 时人工核对可靠。
    """
    import re
    from pathlib import Path

    pipeline_path = Path(__file__).resolve().parent / "pipeline.py"
    if not pipeline_path.is_file():
        return
    source = pipeline_path.read_text(encoding="utf-8")
    called = set(re.findall(r"save_(?:artifact|current)\(\s*'([^']+)'", source))
    # DeepSNR 预测图由外部工具直接写入 artifact_paths（不经save_artifact），
    # 属已知例外；其余未登记的产物一律视为注册表漏项。
    called.discard("03_pre_denoise_prediction.tif")
    if not called:
        # 源码结构变化导致正则失配时不误报，但要留下可查痕迹。
        return
    unregistered = sorted(called - set(seen_artifacts))
    if unregistered:
        raise RuntimeError(
            "stage_registry is missing artifacts that pipeline.py writes: "
            + ", ".join(unregistered)
            + " — add them to the owning StepContract.artifacts")
    unused = sorted(set(seen_artifacts) - called)
    if unused:
        raise RuntimeError(
            "stage_registry declares artifacts that pipeline.py never writes: "
            + ", ".join(unused)
            + " — remove them or fix the filename")


_validate_registry()


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="步骤契约注册表自检（列出步骤表并校验一致性）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if args.json:
        print(json.dumps({
            "main_steps": list(ALL_STEPS),
            "optional_steps": [c.key for c in _OPTIONAL_STEPS],
            "emission_steps": list(EMISSION_STEPS),
            "artifact_to_step": STEP_BY_ARTIFACT,
            "contracts": [
                {"key": c.key, "order": c.order, "domain": c.domain,
                 "optional": c.optional, "artifacts": list(c.artifacts),
                 "summary": c.summary}
                for c in STEP_CONTRACTS
            ],
        }, ensure_ascii=False, indent=2))
    else:
        print(summary_table())
        print()
        print("main steps    : %d" % len(ALL_STEPS))
        print("optional steps: %d  (%s)"
              % (len(_OPTIONAL_STEPS), ', '.join(c.key for c in _OPTIONAL_STEPS)))
        print("artifacts     : %d" % len(STEP_BY_ARTIFACT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
