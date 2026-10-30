# 共用协议 · 非线性亮度调整

## 红色选区保色

新 `vivid_preservation_v1` 会话的红色局部亮度蒙版使用归一化的 `R-max(G,B)` 和 `min(M0,Gaussian_2(M0))`。亮度操作声明不变，由冻结会话策略选择配方，报告 ROI 有效覆盖率和权重分位数；不改变局部对比蒙版，也不允许独立 RGB 增益。历史会话保留原选择器分子与乘积蒙版。

正文与完整 SSF 只维护在本文件；Stage 8/10 仅说明执行位置。新 provenance 绑定本文件完整 SHA-256 为唯一 `primary`，无需绑定 Stage 10 primary 或 Stage 8 supporting。其他确实用于决策的材料可按原规则记录为 supporting；旧会话保持原冻结路径、角色和指纹，见 [兼容说明](../session-contract.md#文档指导版本)。

<a id="tone-adjust"></a>
## tone.adjust

<a id="remaining-goal-tone"></a>
### 已定调父源与剩余目标

新会话冻结 `tone_adjustment=remaining_goal_v1`。先读取已采用的 stretch 链、既有 tone 及实际审查，再说明当前父源仍未满足的区域与目标。两步链或 HDR/HOO 已达到目标时直接跳过；链长不决定是否允许调整。非恒等曲线继续复合作用于现有像素，独立 `tone_chain` 记录不能消除累积变化。Stage 8 与 Stage 10 不按阶段自动各追加一次；合星后只有实际剩余问题才进入新候选，并重新审查星核、颜色与光晕。

每份新 tone SSF（包括下方红色局部变体）必须添加 `# tone-adjust:` JSON 提案。字段为 `schema="starun-siril.tone-proposal.v1"`、当前父源的 `regions.v1` 文件指纹 `regions={path,sha256,size}`，以及非空 `goal`、`upstream_assessment`、`remaining_issue`、`parameter_reason`。Agent 选择真实 ROI 和显式参数；不能用全图变亮、压暗噪声或合法非恒等操作替代剩余目标。没有改善目标时记录跳过，不创建 identity run。沿用现有 run/provenance、手册查询及参数合法范围，不新增自动强度、步数上限或固定收益阈值。

执行器自动绑定实际来源、相关上游 receipt/accept review、真实 tone 链与 ROI，在 `reports/<run-id>/tone-diagnostics.json` 保存上游链和原审查、逐区域科学测量、亮度变化及端点变化。脚本不判断上游目标是否已满足，也不从统计量自动选择参数或接受候选。Python 只读科学像素并写诊断 JSON，科学 FITS 仍由 Siril 写入；直接父源／候选预览沿用同一显示条件。

同一父源已有候选后，修订提案增加 `revision={"run_id":"085-tone","evidence":[<actual-prior-material-path-sha256-size-binding>],"reason":"Observed remaining issue"}`。使用新 run ID，从同一可信父源生成完整候选，不能把未采用候选作为下一轮输入。已采用的 tone 历史继续显示在上游材料中，Agent 必须解释新的剩余目标，不能默认重复同目的调整。

新候选 review 增加 `tone_assessment`，绑定 diagnostics，填写 `goal_status`、`additional_benefit`、`benefit_observation`、逐 ROI 的 `assessment/observation` 及 `adoption_reason`。只有目标达到、有可复核新增收益、全部保护区域可接受且既有适用质量门通过才采用。无收益、退化或无法判断时保留父源；端点通过不单独证明收益。骨架不填写结论。新字段仅对新标签候选强制，旧会话按修改前策略、文档和实现指纹只读回放／finalize，不补字段或改写结果。


<a id="tone-adjust-适用条件与位置"></a>
### 适用条件与位置

只用于已经审查接受的 nonlinear 科学父源，支持 mono/RGB。linear、unknown、诊断预览和辅助层
不得进入本协议。已知 nonlinear 原始输入必须先完成且接受唯一成功的 `input.inspect`；
其他父源必须有生成它的成功 run 与 accept review。没有明确亮度改善目标时跳过。

完整处理可在两个位置选用同一协议：Stage 8 的局部对比、颜色映射之后，或 Stage 10 的非线性
降噪之后、最终调色之前。两个位置不是必做组合；每次都独立生成 SSF、运行并审查。
定向模式只需满足父源与审查前置条件。

合星前只调整当前 starless 主体 A′；原始 stretch 的 F 与星层 `S_d = F(I) - F(A)` 保持不变。
合星仍使用原 `--separation-run`、`--stretch-run` 和冻结链，并以调整后的主产物作为
`--source`/A′。合星后的调整作用于完整图，须重新检查星色、星径与光晕。
用户明确要求的 `standalone-starless` 终图也可在 Stage 10 使用本协议。

<a id="tone-adjust-参数与选择依据"></a>
### 参数与选择依据

- MTF：`mtf LOW MID HIGH`，沿用 `0 <= LOW < HIGH <= 1`、`0 < MID < 1`。
  固定端点 0/1 时，MID > 0.5 压低中间调，MID < 0.5 抬高中间调；单独改变 MID 不新增端点裁剪。
  LOW/HIGH 的移动可能截断科学信号，必须以科学 FITS 测量验收，不以 JPEG 黑色像素判断。
- GHS：D/B/LP/SP/HP 全部显式声明，沿用 stretch 的参数边界。
  使用 `-human -clipmode=rgbblend`，不允许独立通道、饱和度或指定单通道操作；mono 忽略色彩权重。
- identity（MTF 0/0.5/1 或 GHS D=0）直接跳过，不创建没有有效操作的 run。
- 默认选择单一算子。有具体证据说明单步不能同时满足背景、微弱结构和亮核要求时，
  可在同一加载中显式组合 MTF/GHS；不自动增加步骤或选择参数。

使用已确认的背景、微弱目标和亮核固定 ROI，读取它们在**当前 nonlinear 父源**中的分位数、
median/MAD 与实际像素。SP 对准需要增加对比的亮度区间，LP/HP 分别保护暗部与亮部；
保护设置不能保证不裁剪。复用 [区域测量](../quality.md#区域级科学测量) 的 `--regions`，
不把全图 median 当背景，也不规定通用背景亮度目标。

压低背景与抬高星云中间调应分别验证；不能仅以更暗或更亮宣称改善。
背景与微弱信号高度重叠时，全局单调变换无法可靠分离二者；无收益时保留父源。
不默认追加固定 MID 的压暗 MTF。MID > 0.5 仍是合法选择，但须证明其实际收益并检查主体是否被同时压回背景。
继承 Stage 7 收益优先的父源时，固定检查已经显露的外壳、暗带与细丝；不能仅为隐藏颗粒噪声而降低其可见性。背景更暗或更平滑不单独构成收尾收益。
需要集中增加目标亮度区间的对比时，依据当前域 ROI 选择显式 GHS 的 SP 与 D/B，
再实测 LP/HP 对暗部、核与星点的效果；保护参数和某张图的成功数值不能成为通用配方。
以下数值只展示语法，不是通用参数，也不要求运行两个候选。

<a id="tone-adjust-ssf-知识关系与骨架"></a>
### SSF 知识关系与骨架

本共用文档 `references/shared-protocols/tone-adjust.md` 是 SSF 唯一的 primary reference；冻结 Siril 1.4.4 `mtf/ght` 手册提供语法与语义，
command policy 单独决定授权。逐字实例化完整骨架可记录 manual lookup `not_needed`；
未展开的组合或选项有歧义时查询原文并保存 evidence。provenance 必须解释适用性与参数选择。

每份 SSF 只加载当前父源一次，先保存直接父源预览，再完成全部变换，保存科学主产物一次，
最后保存直接候选预览。普通变体不使用 autostretch、Asinh、PixelMath、裁剪或另一个输入；下方 local-tone 仅授权其严格匹配的原生蒙版骨架。
两份 JPEG 路径必须不同，不能在主产物保存后继续变换。审查时必须绑定两份 JPEG 的 SHA-256。

MTF 独立候选：

```ssf
requires 1.4.4 1.5.0
set32bits
# tone-adjust: {"schema":"starun-siril.tone-proposal.v1","regions":{"path":"reports/tone-regions.json","sha256":"<actual-sha256>","size":123},"goal":"Resolve the observed remaining tonal issue","upstream_assessment":"Describe what the accepted chain already achieved","remaining_issue":"Identify the unresolved current-source ROI","parameter_reason":"Explain the explicit candidate parameters"}
load "/abs/current-nonlinear-parent.fit"
icc_assign sRGB
stat main
savejpg "/abs/session/previews/085-tone-mtf-parent" 95
mtf 0 0.55 1
stat main
save "/abs/session/artifacts/085-tone-mtf" -chksum
savejpg "/abs/session/previews/085-tone-mtf-candidate" 95
close
```

GHS 独立候选：

```ssf
requires 1.4.4 1.5.0
set32bits
# tone-adjust: {"schema":"starun-siril.tone-proposal.v1","regions":{"path":"reports/tone-regions.json","sha256":"<actual-sha256>","size":123},"goal":"Resolve the observed remaining tonal issue","upstream_assessment":"Describe what the accepted chain already achieved","remaining_issue":"Identify the unresolved current-source ROI","parameter_reason":"Explain the explicit candidate parameters"}
load "/abs/current-nonlinear-parent.fit"
icc_assign sRGB
stat main
savejpg "/abs/session/previews/086-tone-ghs-parent" 95
ght -D=0.2 -B=0 -LP=0.02 -SP=0.15 -HP=0.90 -human -clipmode=rgbblend
stat main
save "/abs/session/artifacts/086-tone-ghs" -chksum
savejpg "/abs/session/previews/086-tone-ghs-candidate" 95
close
```

编号按当前 session 的实际顺序选择；Stage 10 也用相同骨架，不能覆盖已有编号。
`--expect` 分别声明科学 FITS、直接父源 JPEG 与直接候选 JPEG。
`--primary-output` 选择科学 FITS，不能选择预览。receipt 的 `tone_chain` 记录本次操作，
继承的 `transfer_chain` 仍是原始 stretch 链；回放核对绑定 SSF 与 `tone_chain`。

<a id="tone-adjust-审查与回退"></a>
### 审查与回退

实际打开相同显示条件下的两份直接预览，并对科学 FITS 使用同一组固定 ROI 测量：
背景是否降低且不截断、微弱结构是否仍可辨认、亮核层次是否保留、噪声及颜色是否退化；
含星图额外检查星色、星径与光晕。JPEG 量化不能证明科学信号截断。

沿用 [质量协议](../quality.md)：科学像素 ≤0 的比例达到 0.01% 时建议 reject；
near-black 仅作诊断，不以固定背景亮度替代审查，也不降低合星闭合 1e-5 容差或带符号残差 −0.10 硬门。
适用的结构、背景、颜色、星点、几何门必须通过才能 accept；
缺失测量或材料、改善不明确、可见伪影或 uncertain 时保留已接受父源。
数值检查不自动产生视觉 accept，失败候选不进入 selected lineage。
按 [主体显著性审查](../quality.md#主体显著性与收尾收益) 在 notes 记录固定目标—天空中位差、
细节可辨性及噪声代价。主体变弱或收尾没有明确收益时保留父源，不为完成阶段强制压暗。

非线性预览前必须使用原生 `icc_assign sRGB` 标记显示约定；只赋予 profile，不转换科学像素。
父源和候选使用同一 TRC，保存的 FITS 保留该标记，避免重开后重复 Gamma。已有其他明确色域证据时停止并核对，不能强行改标。


<a id="display-red-local-tone"></a>
### 可选的红色局部亮度

只用于可信、已接受的 nonlinear RGB starless 分支，默认关闭。严格声明为 `# local-tone: {"selector":"red","midtones":0.48,"regions":"reports/red-regions.json"}`，字段恰好这三项；midtones 为有限数且 `0<MID<0.5`，拒绝布尔值、未知字段、重复声明及混搭调色声明。示例值不构成通用提亮幅度，不扫描参数；没有改善目标就跳过。

regions.v1 绑定当前父源和专属红色 faint ROI，天空/目标/高光边界复用颜色 helper 的 derive_mask，无法分离时保留父源。固定 `S=smoothstep(clamp((R-max(G,B))/max(R+G+B,1e-6),0,1))`；该显示选区不是 Ha 提取。原始蒙版 M0=亮度淡入×最大通道高光淡出×S，最终蒙版 `M=min(M0,Gaussian_2(M0))`。

原生 split 后测加权亮度 L，执行 `mtf 0 MID 1` 得 L′，共同 RGB 增益 `g=1+(L′-L)/max(L,1e-6)`；先形成增强 RGB，再以同一个 M 对全部通道混合。科学结果等价于 `parent*(1+M*(g-1))`，不单独增加 R，不裁剪或峰值归一化。中间增强图不是可接受科学父源。

`deep_sky_siril_color.local_tone_script(session, source, primary, declaration)` 返回完整 SSF、expected outputs 和绑定，不写入科学像素；Agent 检查生成内容、保存新 SSF/provenance 后用现有 run 执行。唯一 primary 仍为本文件；完整展开骨架可记录 manual lookup not_needed。必须精确匹配 split、亮度/峰值、三个蒙版、原生 MTF、共同增益、RGB 混合及直接预览；普通 tone.adjust 不能借新命令权限执行任意 PixelMath。

receipt.local_tone 绑定参数、来源、regions 和所有图层；processing_checks.local_tone 核对有限值、几何/WCS/曝光、MTF、蒙版、正共同增益、通道比例、排除像素及成对端点。新增阴影/高光跨界拒绝，未变化的原有端点分别记录。review 实际打开父源、蒙版、候选及必要同位置局部，绑定 receipt 和 ROI，仍遵守 32 项上限。反差过硬、亮壳失层次或无收益时保留父源；已采用该变体的谱系禁止重复。Stage 9 沿用原冻结星层。
