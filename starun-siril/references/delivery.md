# Delivery Protocol v1

For `stretch_optimization=highlight_budget_mtf_v2`, disclose Stage 7 new-highlight counts and per-channel rates, affected structures and the actual review of retained location overlays. A permitted endpoint crossing can affect subject detail or stellar color; it does not imply those details were preserved.

For `stretch_morphology=roi_declared_v1`, include every verified morphology context manifest, boxed panel and referenced native parent preview in the evidence inventory and preserve them during intermediate cleanup. Committed replay verifies retained bindings without requiring pruned scientific parent FITS. Context images are diagnostic evidence and do not add canonical outputs or authorize scientific reuse.

For new sessions with `stretch_trial_artifacts=retained_artifacts_v1`, retain the verified `artifacts/stage7-trials/` archives even when pruning intermediates. Include each index and copied image in the final evidence inventory and validate their hashes during replay. The canonical `outputs/` deliverables remain unchanged; trial copies never inherit scientific-parent eligibility. Do not backfill or alter historical sessions.

Stage 9 selected Fs and body transfer remain separate. Delivery must use the accepted actual final composition and its matching preview, after stellar core/color/halo/endpoint checks and the existing gates; full-contribution diagnostics are not scientific parents. Historical delivery/replay retains its frozen policy.

## 正式交付边界

只有 `success|partial_success` 才正式发布 `outputs/reference.jpg`、`outputs/final.jpg` 与 `outputs/final.fit`。reference 是从已
验证的 `input.inspect` 结果固化的处理前显示预览；final 是被接受的 `delivery.render` JPEG。final.fit 是同一 delivery.render 的 32 位浮点 nonlinear 科学母版，供二次加工。
默认不复制 TIFF、其他父源预览、额外候选图或阶段报告到 outputs/。

`review_required` 是有候选但未达到正式发布条件的审计状态。finalizer 必须验证并记录候选 fingerprint，
但不得创建 reference.jpg、final.jpg 或 final.fit 三份正式输出。`failed` 只有结构化错误，不得声明候选。

有效 `input_state` 或 `channel_mode` 任一为 `unknown` 的输入只执行 Stage 1 的 `input.inspect` 并停止，不进入交付协议。获得可靠来源证据并通过 `confirm-input` 一次补齐未知字段后，可在同一 session 继续；`channel_map=null` 不等于 `channel_mode=unknown`。
诊断预览既不是科学父源，也不能直接成为正式 JPEG。limitations 只披露已知边界，不会把 unknown 变成
可交付状态。

## 最终选择

按 `final-selection.schema.json` 创建 `final-selection.json`：

- `status` 使用 `success|partial_success|review_required|failed`；
- 非 `failed` 必须提供 `candidate_image`；
- `failed` 必须提供 `error`；
- `selected_runs` 和 `review_receipts` 只列实际采用、哈希可验证的记录；
- 明确 `stars_required` 和 `output_contains_stars`；
- 用结构化 `limitations` 记录输入状态、WCS、颜色真实性、可选工具或其他限制；
- `partial_success` 至少说明一个实际限制。

缺少未使用的可选工具只是 probe warning。若用户要求的能力因此无法完成，应保留已验证候选并选择
`partial_success` 或 `review_required`，取决于候选是否仍满足正式交付门禁；不得伪造完成。

## 清理

只有 `success|partial_success` 且 finalizer 完成 v1 提交后，才按默认策略清理阶段图像。
`review_required|failed` 保留现场；`--keep-intermediates` 让成功分支也保留。清理不删除脚本、日志、
运行时重开记录、JSON receipts、final audit 或 final result。

## 验收证据

测试、静态校验、Siril 退出码或 JSON 状态都不能替代真实图像验收。正式交付至少需要：

- Siril 对候选容器的前向重开证据；
- 与 run receipt 绑定的候选 fingerprint；
- 对实际 JPEG 像素完成结构、背景、颜色、星点和几何审查；
- selection、audit、final-result 与正式输出的 SHA-256 链路一致；
- 每个 selected run 的 SSF 都有经验证且与 receipt 绑定的同 stem provenance sidecar。

## 形态判断与选参说明

采用 [AI 形态判断与科学测量](target-morphology-review.md#ai-morphology-and-scientific-measurement) 时，从真实 Stage 1 与相关阶段 review notes 提炼观察、保护对象、参数理由及实际效果；区分可辨观察、疑似判断、无法判断和来源声明。候选名称不作为已确认目录身份，不宣称 AI 自报置信度或数值通过证明科学保真。交付审查与 final-selection 合同保持不变。

## 最终回复

若状态为 success|partial_success，展示 outputs/final.jpg 和 outputs/final.fit 的绝对路径。否则明确说明没有正式交付。
始终报告状态、实际采用的协议、星点状态、限制和中间图像保留情况；只陈述 session、receipts 和产物能
证明的事实。

从已有 session、tool probe、SSF/provenance、run/review receipts 和 final audit 提炼处理说明：输入证据
来源、关键参数及理由、实际采用或跳过的阶段及原因、适用门的测量值/单位与结果。引用实际记录，
不凭最终图倒推处理历史；保留失败或拒绝尝试的记录，但不得写入 selected lineage。报告 Siril 实测
版本与已使用工具/模型的可用 fingerprint；没有版本证据就不补猜。区分静态验证、真实 Siril 执行
和实际像素审查的覆盖范围，不把一次数据验收推广为其他平台或数据均已验证。不另建重复调用日志。


## Paired scientific delivery compatibility

New sessions freeze delivery=paired_fits_jpeg_v2. Receipts bind the FITS/JPEG roles under delivery_export
and scientific checks under processing_checks.delivery_export. Audit/result add final_fits; the committed
manifest adds final_fits_sha256. Three formal files are verified before the commit and intermediate cleanup.
The formal FITS survives default pruning. Interrupted copies retain evidence and permit an identical retry;
conflicting targets are never overwritten. Older frozen sessions retain two-JPEG replay/finalize without backfill.

## Global contrast warning review

Under paired_fits_jpeg_v2, global JPEG p99-p01 below 15 is a mandatory-review warning.
The 15-100 median and 5% pure-white boundaries remain; FITS/JPEG identity, WCS, range and native reopening remain enforced.
Inspect the direct display and scientific FITS, bind the run receipt, and judge actual target/core/outer detail against independent sky.
Small target occupancy can produce a low global span; a visible subject must still be demonstrated by actual pixels.
Warning acceptance requires all five gates to pass. No automatic accept or historical failure promotion is allowed.


## Stage 7 visibility disclosure

Report the actual Stage 7 route and executed operator chain, the adopted result or retained parent, and the verified references and comparisons used in review. Disclose any visible outer-structure regression retained for core protection, its affected ROIs and the evidence for the tradeoff. Separation ranking, chain completion and passing endpoints alone do not establish visibility, preserved highlight detail or natural transitions.

## 配色路线披露

从实际选中谱系与审查记录说明：目标配色及用户选择、实际通道来源、是否沿用已有映射、Stage 4 采用的 SPCC/PCC/neutral 或跳过原因、以及星层来源。宽带自然 RGB 是配色目标；只有中性化或未校色时明确披露未完成测光校准。SHO/modified-SHO 属于指定谱线的显示表达，Ha/OIII HOO 不能宣称有 SII；OSC 估计与真实谱线组合分别说明。

保留已接受配色并披露最终回混贡献、饱和度和亮度调整。原窄带 RGB 星层不能称为宽带真彩星色；“SHO 主体＋宽带真彩星点”需要额外宽带输入、来源和配准支持，当前单 master 合同不提供。没有显式窄带选择、角色证据不足或已是目标映射时记录保留或跳过依据，不以预览补齐来源。

## Native neutral disclosure

For dualband OSC, report the actual Stage 4 native-neutral adoption or skip reason and its
fit/validation sky evidence. Report each file independently; accepted calibration runs
and reviews belong in the selected lineage. Describe preservation only as background-subtracted
RGB signals and original channel gains during neutralization. Neither neutralization nor later
stretching establishes physical H-alpha/OIII line ratios or a final nonlinear line-intensity ratio.
Insufficient sky and any retained precision/endpoint warnings remain disclosed limitations.


## Stage 5 restoration disclosure

Disclose denoising and RL as independently assessed, adopted or skipped with actual
reasons. Include adopted run/review records in selected lineage; retain rejected or
uncertain attempts as evidence. Bind the current accepted
parent chain. Describe RGB mono-PSF RL as Lab luminance-detail restoration, without
claims of unbiased photometry or physical emission-line ratio preservation. Formal
image delivery still requires the existing actual pixel review and finalization gates.

Under benefit_first_flow_v18, report Stage 4 evidence acquisition and even tiny
measured improvements; distinguish an executed neutral candidate from a visual-only
assessment. Stage 5 may be adopted after review finds no material degradation even
without visible benefit; disclose near-unchanged results and minor costs without
claiming restored detail. Native StarNet --linear accepts above-one samples with
clipping warnings: disclose the actual native behavior and inspect the affected
subject/stellar locations rather than calling the range advisory a startup failure.


## Stage 9 source disclosure

Describe recomposition as the accepted nonlinear starless parent plus a scaled signed display-star
residual F(I)-F(A). Stage 6 linear_stars is not added directly or independently Asinh-stretched.
Bind the adopted source and complete Stage 7 transform/HDR mask through the existing run/review
lineage. Arithmetic closure does not prove stellar color, halo quality or absence of target leakage.

## 局部对比候选的交付依据

采用新策略 CLAHE 候选时，交付链须保留有效的完整材料审查与冻结纹理/高频证据；uncertain 测量及科学裁剪失败不能 accept。大于 1.5x 的高频告警须披露实际 ROI 对照依据，不能以技术通过证明真实细节。HDR 谱系不允许接入 CLAHE，规则见 [Stage 8](stages/stage-08-starless-enhance.md#local-contrast-texture-and-hf-gates)。


v20 HOO 交付说明注明 Ha/OIII 为近似显示估计、所用配方和是否追加 OIII 降噪；星点保留原双窄带 RGB 显示颜色。记录外壳、细丝、背景、星色的实际观察及回退原因；不宣称纯谱线、真实宽带星色或物理线比。


## Multiscale local contrast v21

记录 Stage 8 的小波/CLAHE 同源对照、采用理由、幅度、细尺度增强开关、实际系数与 CLAHE 混合强度，披露局部预览和纹理证据、固定局部、高频/大尺度代价及未采用原因；明确 v2 两组提案、v1 三档或一次 fine-off 修订的实际路线。合星后复核外壳、细丝、星色与端点。保留完整试跑与历史结果，不宣称恢复未采集的细节。

v22 交付根据绑定证据说明实际 ROI 保护选择及天空纹理代价，或明确披露 legacy/回退。保留请求、参数报告、原生脚本日志、选中/full 对照与固定区域材料及指纹；不能用合成测试宣称真实天体收益。

v23 交付说明实际形态、单链与高阶链、相同最终区域约束、比较结果和像素采用理由；失败候选、显式 v22-compatible、未绑定 `strategy` 的 v4 提案（含 custom）、HDR/HOO 路线与保留父源按实际披露。不把数值推荐或级联步数写成实际画面收益。比较材料随已有审查谱系保留。

v24 另披露主体采用的强度、带星重放诊断及最终 strength；星区警告不再压制无星主体求参，但必须说明最终星核、光晕、颜色与端点的实际审查结果。不得将减弱星层贡献写成恢复已裁剪星核或保证缩小星径。

v25 披露 Agent 一次因子提案或显式旧搜索路线、实际每步调用数、资格失败与保留父源的原因。形态两路线各评估一次不等于整条流程只调用一次 Siril。一次提案不保证最优或色彩准确，不能省略原生复测、对照和实际审查。

Stage 7 delivery disclosure must cite the structured assessment for marked runs: identify structures that met the Stage 6 reference, gains and costs relative to the actual base control, any regression and whether core conflict was demonstrated. Explain adoption and any actual route revision through the existing rationale/notes. Do not label an ordinary morphology baseline as a reference-derived proposal.

Stage 8 使用选择性处理时，说明目标颜色属于显示选区、实际幅度/细尺度取舍、青色 target_gain 与 achieved_gain、红色 midtones、ROI 与局部比较依据，披露未达标或退化。暗部零改动仅指绑定的亮度排除集，不宣称天体外全部像素不变或零噪声。Stage 9 披露绑定提案的实际 strength、目标、观察依据及星色来源，不把降低星层贡献写成指定星径。


新会话 `subject_enhancement=benefit_first_v1` 不以低纹理比值或天空/主体分位重叠自动跳过 Stage 8。交付说明实际主体增强收益、所用蒙版回退及背景/颗粒代价；低比值不是物理信噪比证明，中位亮度蒙版也不保证天空完全排除。旧会话继续按冻结规则回放。
