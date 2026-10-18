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

`quality_state` binds pending findings to an artifact SHA-256 and parent branch.
A later successful step does not clear earlier findings. `accept` validates
session membership, file identity and unresolved findings; rollback restores
the selected artifact's quality state. Legacy histories are recovered where
possible; missing evidence remains pending.

An acceptance review resolving findings includes `artifact`, `artifact_sha256`
and `resolutions`. Each resolution requires `issue_id`, `outcome` (`fixed`,
`false_positive` or `inherited`), `reason` and existing `evidence` file paths.
`fixed` requires an artifact-bound JSON report with a passing gate of the same
code. Reclassifying a failed finding requires artifact-bound JSON evidence
identifying the issue and outcome. Clean artifacts retain direct acceptance.
A review with a non-accept verdict cannot execute an accept action.

The default step preset is `adaptive`. Each candidate analyzes its actual input
domain while retaining source capture metadata. Review `effective_params` are
the resolved pipeline configuration, including safety adjustments.

Artifact state carries `star_layers`: `source`, `source_sha256`, `starless`,
`stars`, `processed_stars`, `domain`, `stars_is_linear`, `subject_mask`,
`linear_star_metrics`, and `stellar_analysis`. Paths must retain matching geometry
and refer to the current branch. Acceptance, variants, and rollback select the
corresponding artifact state. Roll back before separation to rerun upstream steps.
Inspect stellar processing with the actual layer previews; field background
gates do not apply to a black-background stellar layer.

Galaxy separation requires a reviewed `subject_mask` file. The mask protects
subject signal in internal, StarNet, and external starless paths. It is not a
catalog identification. Automatic memory skips return `partial_success`.

API callers can supply `capture_metadata`, `star_layers`, and `subject_mask`.
CLI callers use `--capture-metadata` / `--star-layers` JSON files and
`--subject-mask`. Existing call signatures remain valid; galaxy separation now
requires the reviewed mask. TIFF metadata sidecars preserve
capture provenance and the actual domain; keep them with their image.

## Stretch policy and display evidence

New sessions record `stretch_policy_version: 3`; existing versioned sessions
retain their version, and missing-version sessions use policy 1.
Explicit legacy `ghs` / `masked_ghs` retain their existing mathematics.
The pipeline CLI, API and Agent also accept `ghs_standard`,
`masked_ghs_standard` and `pi_mtf`. Standard parameters use independent
`ghs_standard_log_d`, `ghs_standard_b`, `ghs_standard_sp/lp/hp` keys in `params`.
No conversion from legacy strengths is implied.

Policy 3 selects `background_compressed` for every valid linear starless layer
in auto mode. Same-call, external and resumed layers share the same curve.
Explicit CLI methods precede methods in `params`; explicit methods are never
replaced by the starless auto policy. With-stars target policy is unchanged.

Use `params.stretch_subject_midtones` for the subject display target (`0.25` by
default, `0.20` for reflection/dark nebulae, `null` to disable anchoring). Require
`target_bg < M < (1-stretch_headroom)/3`. `subject_visibility=increase_slightly`
adds target `0.27`; `increase` adds `0.30`. Explicit params override these values.
The legacy stretch-factor mappings remain available to older algorithms.
Unmeasurable anchors and missed display targets remain unresolved quality issues
after fallback; later processing or acceptance cannot silently clear them.

`stretch_diagnostics` identifies the applied algorithm, background target and
actual value, curve parameters, RGB highlight shoulder, unused parameters,
rejected candidates and recovery. The fixed MTF reference carries a source-pixel
SHA-256, geometry and curve. Review creation verifies its source and geometry.
`display_reference.jpg` and `display_difference.jpg` supplement the original
stage previews; they never become processing inputs. Stellar widths compared
against this reference belong to the display domain. `linear_star_measurement`
records a separate linear PSF estimate and reports insufficient samples without
inventing a width. Display-curve peak amplitudes do not establish stellar signal
loss, so `STAR_LAYER_LOSS` is skipped for this comparison. Display width, ringing
and hole checks remain active. Standard `log_d` is limited to [0,20], `b` to
[-5,15], and all explicit curve parameters must be finite.

The plateau diagnostic preserves inherited flat cores and escalates new float
plateaus, including levels below 0.995. Unreliable background models continue to
require review. Failed stretch recovery restores its input and layer domain,
records skipped subsequent steps and returns `review_required`.

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

## 可追溯运行与阶段约束

解释器取当前调用进程的 `sys.executable`（`runtime` 中的 `python_executable` 字段即由此
记录），并从当前调用的 Skill 根目录解析脚本绝对路径；不要混用仓库和旧安装副本。
`runtime` 记录源码指纹、源提交、未提交状态、实际脚本路径、Python/依赖版本、平台、
锁文件指纹及拉伸策略。

`color` 仅接受线性输入，在像素变换前拒绝非线性校色。Agent 已记录的阶段及文件
sidecar 不能被参数静默覆盖；无 sidecar 的线性 TIFF 可使用 `--input-linear`。
旧会话缺少策略字段时继续使用策略 1，并输出兼容警告；验证新策略必须从原始输入新建会话。

`requested_config` 是请求，`effective_config` 是解析后的配置；不能把全部配置当作
已经执行。`execution` 区分实际执行步骤、操作消费的参数、未消费参数及跳过原因。
`emission` 不消费 GHS 保护参数；未运行 `enhance` 时 `hdr` 保持 `null`。
拉伸回退后的诊断与参数描述实际保留的结果，拒绝候选留在 `rejected_initial`。

独立检查支持 `quality_metrics.py input.tif --stage linear`；默认使用当前文件 sidecar、
匹配产物的 manifest 和格式推断，忽略其他产物的 manifest。

`quality_state` 随当前分支传递未解决问题、来源阶段和产物 SHA-256。后续步骤成功
不会清除早期问题；`accept` 拒绝未解决问题、外部产物和已变化的文件。逐项复核需
绑定当前产物及 SHA-256，并附原因和证据；已证实缺陷需通过复查。旧会话从历史
记录恢复质量状态，证据缺失保持待审；回退恢复目标产物的质量状态。

本轮复现环境为 macOS arm64、Python 3.12.13、NumPy 2.5.3，依赖固定在
`requirements.lock`。其他平台应独立验证，不宣称像素级一致。

### M42 固定区域验收

```bash
python3 scripts/m42_regression.py original.fits work/m42_regression \
  --emission-control C50.fits --cluster-control M45.fits --second-cluster-control M45_other.fits
```

脚本创建当前策略会话，从同一线性校色母版比较 emission、安全 MTF 和八组标准保护
参数（b=4/8，保护强度=0.35/0.5/0.7/0.85）；每组固定后续色彩，仅调整背景目标
匹配背景。参数搜索不修改生产默认值。输出原尺寸对比、核心与暗环 1:1 裁切、
逐通道原尺寸暗环剖面、掩膜/混合曲线/高光肩部诊断、固定区域统计、
三入口一致性及真实控制样本。5% 是工程比较容差，不是物理 SNR。数值通过仍等待
视觉审查；未确认自然基准收益时不接受候选、不生成增强版。

## 星点处理依赖规则与链路细节

星点处理依赖规则：

- 没有图层时，`star_process` / `star_combine` 自动补全完整星点链路；已有 `star_layers`
  时复用真实无星层和星点层，只补齐缺失的拉伸或星点处理，不重复去星。
- 独立去星返回真实无星主产物。Agent 在接受、候选分支和回退时保存对应图层；星点处理
  审查实际星点层，整场背景门禁留在合成图上执行。
- 星系默认保留星点；显式去星必须提供经过视觉审查的 `subject_mask`（保护核心及紧凑主体，
  与输入几何一致、数值有限且范围为 `[0,1]`）。
- 8MP 内存保护跳过请求步骤时返回 `partial_success`；提供现有外部无星层可继续完整链路，
  不降低成片分辨率。
- 球状星团、疏散星团和 M45 的安全规则会移除 `star_remove`、`star_process`、
  `star_combine`，不得强制补回。

星点链路（`star_process` 内部顺序）：
`S1 星点拉伸(arcsinh) → S2 去紫(SCNR) → S2b 保亮度饱和补偿 → S3 曲线微调 + 缩星`

- **S2b 是必需的**：arcsinh 压缩色度、SCNR 又把 Lab a 推向 0，两者叠加是最大
  损耗源，而 S3 的曲线用亮度增益乘回 RGB（保比例、不改饱和）、缩星只做腐蚀，
  都不会撤销它。外部经验要求「饱和必须在拉伸之后、且要足够激进」。
  由 `star_saturation` 控制（默认 `1.08`，`1.0` = 关闭）。公式保亮度，
  只在色度放大后越界裁切时才会轻微改变亮度（仅影响近零通道的极饱和星核）。
  产物 `05b2_star_saturation.tif`。
- **S1 的 `star_stretch_factor` 必须与主拉伸因子同量纲**。预设把两者成对给出
  （`light` 25→88、`medium` 45→132、`strong` 80→198、`adaptive`/`emission` 120→264/198），
  比值 1.65~3.52。历史上该值曾被设成 8/12/24/18 一档，导致星点层 p99 只有 0.0067、
  暗星整体沉进背景（见 `CHANGELOG.md` 的「提升 11 倍」条目）。
  **分析报告这条路径曾漏改**：`analyze.py` 写过 `stretch_factor * 0.25`，给出 9.375，
  而预设同档是 88 —— 星点层亮度只有预设的 1/4.7。现已改为按预设配对插值
  （`_star_stretch_factor_for`），并夹到 `[88, 264]`。**改这条链时两处都要看。**
- 星点即主体的目标（星团 / M45）已被安全规则移除 `star_process`，不进入该链路。
