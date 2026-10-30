# Stage 7 · 默认分步前缀反馈

普通 RGB、单色及 HDR 默认读取，并作为 SSF supporting；[Stage 7 正文](stages/stage-07-stretch.md)仍是 primary。显式 `--batch` 才使用整链执行。新会话冻结 `stretch_execution=stepwise_default_v2`，不将历史会话迁移到分步模式。HOO 保留专用整链流程，Ha/OIII 主体链各至少两步；显式 --stepwise 仍拒绝。

新会话还须绑定[伪影重点区清单](stage7-artifact-focus.md)，在 `feedback.artifacts` 中复查上一前缀；追加审查区不改变科学 ROI、候选身份或额度。

## 请求与暂停

1. 使用当前已接受线性父源、实际预览、`morphology-context.v1` 和固定 ROI 编写 goals.v4。`preflight --stepwise` 只校验声明，不执行 Siril、不预测尚未给参的步骤；`init-request --stepwise` 创建 strategy.v3 和 `first-request.json`。
2. 用新 run ID 执行 `solve`。每次只从原线性父源重放冻结前缀并追加一个新步骤，保留原生 FITS、直接预览、同显示 ROI 面板、统计、SSF 和日志，成功返回 `awaiting_next_step`。首版不缓存前缀，计算量随链长增加。
3. Agent 查看真实前缀，创建新路径的 request.v5：绑定上一回执、实际查看的预览与 ROI 面板，对每个 ROI 写具体观察，再填当前步因子、阶段目标和锚点。已有变换按冻结数值重放，不重新求参；GHS 锚点数值来自 `anchor_domain` 对应的真实科学输入域。
4. 主体充分显露且普通链已实际执行至少两步时提交显式完成请求，`finish` 将候选冻结为 parameters.v3 的 `solved`；可以提前省略剩余步骤，但须说明图像依据。普通链仅一步时 finish 拒绝且不占后继、不写完成回执；仍可继续第二步。HDR 内部单步分支允许 finish_branch，两支都完成后才能融合。随后 `generate`、既有 `run --protocol stretch` 和图像审查。`solved` 只代表冻结与科学验收，不自动确认视觉收益或采用。

```sh
python scripts/deep_sky_siril_stretch.py preflight --stepwise --session SESSION --source SOURCE --regions REGIONS --goals GOALS --run-id 071-plan
python scripts/deep_sky_siril_stretch.py init-request --stepwise --session SESSION --source SOURCE --regions REGIONS --goals GOALS --run-id 071-plan
python scripts/deep_sky_siril_stretch.py solve --session SESSION --request reports/071-plan/first-request.json --run-id 071-prefix
python scripts/deep_sky_siril_stretch.py solve --session SESSION --request reports/072-next-request.json --run-id 072-prefix
python scripts/deep_sky_siril_stretch.py finish --session SESSION --request reports/073-finish-request.json --run-id 073-finished
python scripts/deep_sky_siril_stretch.py generate --session SESSION --parameters reports/073-finished/stretch-parameters.json --run-id 074-stretch
```

## goals.v4 与 strategy.v3

goals.v4 使用精确字段：`schema, profile, reason, evidence, targets, core_absence, protection, final_constraints, manual_lookup, morphology, branches, initial, revision`，新策略还必须包含 `artifact_focus` 文件绑定，可选 `tail_control`。其中证据、目标方向、无亮核说明、手册查阅和形态声明沿用 goals.v3 的内容与验证规则。

`branches` 为普通链的 `{"main": [...]}`，或 HDR 的 `{"gentle": [...], "faint": [...]}`。每个条目只有 `method` 和 `purpose`；方法限 MTF/Asinh/GHS 原生标识 `mtf/asinh/ght`。未来步骤不填写 `factors`、`landmarks` 或占位数值。普通 `--mode profile` 的方法顺序匹配形态参考链，显式例外用 `--mode exception`；HDR 可由 Agent 根据任一受支持形态首次选择。

`initial` 只描述当前第一步，字段为 `method, purpose, factors, constraints`；GHS 还须 `landmarks`。方法与目的必须匹配冻结顺序。因子名称和范围沿用 request.v4；GHS 的 LP/SP/HP 引用为 `{region, statistic}`。`protection` 冻结各 ROI 的科学保护，`constraints` 只增加当前阶段目标。`final_constraints` 单独冻结最终显示目标，与保护取交集，只在普通链完成或 HDR 最终融合时验收。不要把最终背景或弱结构亮度下限放进首步保护要求。暗端、高光、几何和有限性规则保留；背景 MAD/hf_sigma2 及 full 重放仍遵循既有诊断政策。

`revision` 首次为 `null`。已执行变换、源或 ROI 的更改不能通过续接请求实现，须新建候选。分步候选修订绑定 `{parameters, reason}`；绑定既有整链候选时使用原完整修订证据结构。新候选保留首次形态根和亮核／星点保护，独立计次。

strategy.v3 冻结源及 full 配对、形态、算子顺序、ROI、阶段／最终保护声明、Siril runtime、实现与配置指纹。任何漂移都阻断续接。

## request.v5 与回执

每份请求精确包含 `schema, strategy, previous, feedback, action, branch`。`strategy` 为 `{plan, variant}`，`variant` 使用初始化的 `profile/exception`；`previous` 是上一份 parameters.v3 文件绑定。首份请求的 `previous/feedback` 为 `null`，且只能使用初始化产生的不可覆盖文件。

| action | 额外字段 | 执行方式 |
|---|---|---|
| `step` | `step, anchor_domain` | `solve` 追加当前步；`step` 与 `initial` 同结构，阶段目标与因子由 Agent 新提案 |
| `finish_branch` | `reason` | `finish` 显式结束非空 HDR 分支，可提前结束，返回 `branch_complete` |
| `finish` | `reason, mask` | `finish` 冻结最终候选；普通链 `mask=null`，HDR 按下节声明 |

`feedback` 的新策略的精确结构为 `{materials, observations, reason, artifacts}`。`materials` 至少包含上一回执、该前缀 `selected[".jpg"]` 与 `local_panel` 的完整文件绑定，可追加同一前缀的 FITS 或 full 预览；`observations` 对每个实际 ROI 各填写一个 `{region, observation}`。文本须描述实际结构与参数选择依据；工具只验证来源与声明完整性，不能证明 Agent 实际看过图或判定视觉收益。

`anchor_domain` 首步为 strategy 的线性 `source` 绑定，后续为本分支最新 `selected[".fit"]` 的完整绑定。换分支时重新使用同一线性父源。回执 `proposal` 保留真实提交请求，`request` 是由已执行步骤推导并校验的科学配方视图；`branches` 保留原生链与逐步证据，普通链还提供 `steps/transfer_chain` 供既有生成、审查和比较使用。所有前缀及其档案均标记 `scientific_parent=false`。

## HDR 双分支

先逐步完成 gentle，Agent 显式 `finish_branch` 后，才可从同一线性父源开始 faint。faint 也须显式结束。分支审计保留诊断，不把最终混合图的亮核上界、亮度下限和端点预算误用为强分支的最终资格；分支有限性、几何、证据和原生失败仍为硬条件。

两支结束后，Agent 查看 gentle 的实际输出域及局部结构，再提交 `finish`：`mask={"domain": GENTLE_FITS_BINDING, "parameters": {"mask_low": ..., "mask_high": ..., "sigma": ...}}`。省略号表示此时由 Agent 根据实际输出填写的数值，不写入初始化目标。该完成请求的 `feedback.materials` 还须包含 gentle 最新预览和 ROI 面板。蒙版规则沿用 [HDR 合同](stage7-hdr.md)。正式 SSF 重放冻结双链并融合，full/starless 共用同一冻结蒙版；最终混合图承担原有 ROI、端点与 `1e-6` 融合误差验收。

## 额度、末段对照与恢复

候选第一次启动原生科学处理时占一次，后续尚未执行的步骤与正式重放沿用该额度；全局仍最多三次。修改已执行变换、父源或 ROI，独立对照，以及原生失败／超时后的重试，都另建候选计次。静态纠错、正常暂停续接、查询和已有 receipt 只读回放不加次。会话额度锁与候选锁保护记账，每个前缀的 `stepwise-successor.json` 只允许一个实际后继。请求、日志、回执和图像均用新路径，不能覆盖旧材料或用同一前缀并行试不同因子共享额度。

多步 RGB MTF 收尾仍须 GHS 对照。独立建立分步 GHS 候选，`revision` 指向已完成的 MTF 候选，`tail_control` 绑定它；重放相同冻结前缀，最后一步提议 GHS。两候选独立计次。`compare-tail` 接受显式完成的 parameters.v3，验证相同原线性父源、数值前缀、实际像素、ROI 与最终约束。仅未达到显示目标而科学保护合格的末段可作为负对照；它不能生成正式候选。GHS 对照正式采用也须比较与图像审查，不因避开 MTF 标签省略比较。

主 CLI 的 `status --session SESSION [--parameters PARAMETERS]` 显示候选、当前分支与步数、待办和剩余额度。按实际后继记录恢复，不按文件修改时间猜测下一步；查询、档案验证与恢复检查不启动 Siril。未完结候选不能生成正式 SSF、成为科学父源或进入后续阶段。历史会话只回放／finalize，不改写旧策略、证据或计数。本次默认模式与多阶段限制的修改前指纹在 `references/stretch-multistage-history.json`；更早的分步实现见 `references/stretch-stepwise-history.json`。

本变更只实现前缀反馈，不增加降噪或 Screen 合成。原生合成数据验证流程与数值一致性；真实 M42、M31 等目标的可见性收益仍须实际图像审查。
