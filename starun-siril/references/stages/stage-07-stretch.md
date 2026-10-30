# Stage 7 · 非线性拉伸与审查

## 新会话：形态驱动的多阶段主体拉伸

新会话冻结 `stretch_routing=morphology_multistage_v1` 与 `stretch_execution=stepwise_default_v2`。普通 Stage 7 主体候选必须实际执行至少两个步骤，按下方五类实际形态选链；禁止独立单步候选、单步 exception、baseline 科学试跑和新建单步对照计划。不自动补恒等步骤凑数，也不在多阶段失败后回退单步主体。Stage 6 autostretch 仍只作参照。

普通 RGB、单色和 HDR 的 `preflight/init-request` 默认分步，使用 goals.v4、strategy.v3、request.v5 和 parameters.v3；`--stepwise` 可显式保留该选择。只有 `--batch` 才使用下文整链 goals.v3/request.v4 路径，两选项互斥。HOO 检测到 channels 计划后沿用专用整链流程，Ha/OIII 主体链各至少两步，显式 `--stepwise` 仍拒绝。

Stage 9 独立星点 Fs、HDR 内部单步分支、HOO 内部 RGB 星点／显示参照链，以及只读 `suggest-mtf` 保留；这些产物不能独立作为单步主体候选采用。`compare-tail` 比较的两份普通主体候选仍须多阶段，保留末段比较和三次总额度。旧会话只按冻结证据回放/finalize，不迁移；完整修改前指纹见 `stretch-multistage-history.json`。

## 按需材料

普通拉伸先读取本文件；新会话的伪影重点区审查还需读取对应合同。以下附文按条件读取，不作为默认流水线或自动换路线依据：

| 材料 | 何时读取 |
|---|---|
| [伪影重点区](../stage7-artifact-focus.md) | 新会话声明、追加重点区或填写分步／正式审查时读取；只组织已有图像材料 |
| [分步前缀反馈](../stage7-stepwise.md) | 普通 RGB／单色或 HDR 默认逐步审查；显式 `--batch` 才整链 |
| [HDR](../stage7-hdr.md) | Agent 根据实际形态选择双分支蒙版变体，可首次提案即使用，无需先证明单链冲突；本文件 primary，HDR 文档 supporting |
| [HOO](../stage7-hoo.md) | 显式选择且初始化开启 HOO，来源条件合格；本文件 primary，HOO 文档 supporting |
| [诊断示例](../stage7-diagnostics.md) | 需要带框 ROI、邻近结构成对测量或记录示例；实际使用时记录 supporting |
| [历史与兼容](../stage7-legacy.md) | 回放或明确历史查询；不用于授权新执行 |

新会话冻结 `stage7_references=split_variants_v1`，生成和手写的 HDR/HOO SSF 均须绑定对应附文；旧会话保留冻结策略与全部证据，只回放/finalize，新增科学执行须新建会话。

<a id="artifact-focus-review"></a>
## 伪影重点区结构化记录

`artifact_review=structured_focus_v1` 将星周补洞／光晕、亮核边缘、暗云核心、背景纹理和 HDR 过渡等实际风险位置独立列入审查。goals 的 `artifact_focus` 绑定 review-focus.v1；审查区不作为科学 ROI、锚点或约束。可以新版本追加，已登记区域持续复查，不改变冻结变换、候选身份或额度。

`artifact-focus` 只使用已有原生预览生成带框上下文与原像素局部，不调用 Siril。后续分步 `feedback.artifacts` 和正式 `stretch_assessment.artifacts` 必须逐区填写参照中是否已有、变化、观察、证据和处置理由；无适用区域时明确说明并绑定整图预览。不增加伪影一票否决，也不豁免现有质量门与科学保护。完整接口、追加与归档规则见[专属合同](../stage7-artifact-focus.md)。

<a id="stepwise-prefix-feedback"></a>
<a id="按需分步执行"></a>
## 默认分步执行

新会话冻结 `stretch_execution=stepwise_default_v2`。`preflight/init-request --stepwise` 使用 goals.v4、strategy.v3、request.v5 和 parameters.v3；只给当前步因子，未来因子省略。每次 `solve` 从同一线性父源重放已冻结前缀并追加一步，成功停在 `awaiting_next_step`；Agent 查看真实预览、ROI 面板和测量后提交新请求，锚点绑定该实际前缀域。阶段目标与最终显示目标分开，保留科学保护；主体已充分显露且实际执行至少两步时可说明依据并提前 `finish`。未完成前缀不能生成正式候选或成为科学父源。

HDR 先逐步完成温和分支，再完成强分支；分别显式结束后，依据温和输出域冻结蒙版，正式重放双链并共用 full/starless 蒙版融合。分支为诊断，最终融合继续承担既有验收。一个候选的连续新步骤共占一次额度；独立对照、已执行变换修订和原生失败重试各另计一次，全局仍三次。后继独占、末段 GHS 对照、正式图像审查和只读恢复要求见[分步合同](../stage7-stepwise.md)。显式 `--batch` 保留整链 schema，HOO 不启用分步。

<a id="stage7-adaptive-budget"></a>
## 新会话：高光预算与 MTF 反求

`stretch_optimization=highlight_budget_mtf_v2` 对每个通道将本步或完整前缀新增 `before < 1 && after >= 1` 的数量除以全图像素数；不超过 0.01% 可以继续，超出拒绝。已有端点单独记录。各 ROI 上界与跨度、暗部严格小于 0.01%、有限像素和几何保护不变。每个有新增高光的原生试跑保留标注位置的 PNG 并绑定；逐步及累计端点分别检查。正式输出重新测量；查看标注和同显示原生局部，记录亮核、星色及主体代价，无法判断或不可接受时保留父源。比例合格不证明没有裁剪，也不自动通过视觉审查。HOO 最终调色和 HDR 主体同样执行该预算；full 重放仍按既有诊断合同。

新策略的 MTF/Asinh 黑点使用各通道剩余暗端数量预算的 10%，再取各通道共同安全黑点。原生每步、预检和重放使用一致算法，已有暗端不视作新预算。旧会话保留原黑点及零新增高光规则。

`suggest-mtf --session SESSION --source SOURCE --regions REGIONS --anchor-region ID --target-median VALUE` 只读取已接受线性父源、匹配 ROI 和显式目标，对每个通道先映射再测亮度中位数，输出 32 次以内二分所得因子和预测值；不可达返回 `no_solution`。目标由实际结构及显示依据选定，不从 JPEG 量化值反推科学像素。提议不启动 Siril、不占试跑额度，也不确认参数质量或最终资格；将因子填入现有请求后仍须预检、原生执行和实际视觉审查。Asinh/GHS 根据实际父源、固定 ROI 和先前试跑修订，不能将单次参数失败概括为整个算子的限制。三次独立试跑及对照记账不变。

<a id="morphology-default-chain"></a>
<a id="subject-priority-stretch-v27"></a>
<a id="主体优先的多节拉伸与参照可见性"></a>
## 当前默认路线

默认顺序为：**按实际形态选择多节链 → 试跑当前一步 → 查看真实前缀与局部结构 → 提议下一步 → 完成候选及正式审查**。主体突出指微弱外缘、细丝、暗带的可见性与局部分离，同时保留亮核层次、颜色和连续过渡；不以整图更亮、分离度最高或 status=solved 作为完成依据。先对照 Stage 6 已接受无星图 autostretch 中显露的结构；它只提供可见性参照，不要求候选每个 ROI 都更亮，其科学裁剪和黑点不作为正式候选的安全保证。正式候选始终从可信线性 FITS 生成。

新会话冻结 `guidance=evidence_bound_stretch_flow_v29`、`stretch_routing=morphology_multistage_v1`。默认 `init-request --mode profile` 冻结分步 strategy.v3；Agent 在 goals.v4 中填写实际形态依据、父源预览、ROI、算子顺序、当前步因子和保护。显式 `--batch` 才按 goals.v3 冻结 strategy.v2/request.v4；不生成独立单步对照。多节 RGB MTF 收尾仍须完成同前缀 GHS 末段比较；分步模式只填写当前步因子，完成 MTF 候选后独立建立分步 GHS 对照，整链模式才在 goals.v3 提供 tail 因子。主提案失败则停止同前缀对照，进入有证据的修订。无计划请求、裸拉伸 SSF 和 --legacy-mtf 不能生成新正式候选。

单候选指一份形态计划及其实际执行的多阶段链。分步模式只填写当前步因子，不能预填未来参数；profile 的初始顺序、算子及计划链长匹配下表，实际执行至少两步后才可依据实图提前结束；不按文件名、天体名称或 target_type 自动分类。混合目标明确主要对象并保护伴随结构。首次短链、首次 HDR 与后续路线修订分别按[例外证据规则](#stretch-routing-v29)使用 --mode exception，新 run ID 或新计划不能重置首次分类。一次失败只证明这份提案失败，不证明整类算子不适用。

<a id="stage7-trial-artifacts"></a>
### Trial images retained in artifacts

New sessions freeze `stretch_trial_artifacts=retained_artifacts_v1`. Every parameter solve automatically copies its retained trial-prefix FITS, JPEG, optional full-stars control and ROI panel to `artifacts/stage7-trials/<run-id>/`. HOO channel and tail trials follow the same rule. Native HDR run outputs and its visibility panel are archived there as well. No extra Siril trial is launched and the three-attempt budget is unchanged.

Each `index.json` records the original evidence path, native parameters, source, complete/incomplete trial status, per-trial eligibility and SHA-256 bindings. Failed or incomplete prefixes remain labeled as such; a single failed step does not represent a completed chain. Technical attempts without retained images produce an empty image inventory rather than a fabricated preview.

The parameter report or HDR receipt binds the archive index. Finalization preserves the archive even when other intermediates are pruned and includes it in the evidence inventory. Files in this directory do not become accepted candidates or scientific parents. Existing sessions retain their original evidence and receive no backfilled copies. The exact outgoing v29 bindings are stored in `references/stretch-trial-artifacts-history.json`.

<a id="morphology-starting-chains"></a>
### 按实际形态选择起始链

| 当前主要形态 | 默认多节起始链 | 每步的具体目标 |
| --- | --- | --- |
| emission_nebula：弥漫发射星云/遗迹 | GHS → GHS → MTF | 广域发射初显；在实际中间域分离弱细丝与暗部过渡；完成最终显示亮度。 |
| high_dynamic_range：亮核与弱外缘跨度很大 | Asinh → GHS | 分配初始动态范围；保护亮核层次并分离包层。可由 Agent 首次选择双分支蒙版 HDR。 |
| galaxy_halo：星系外晕/旋臂/尘埃带 | Asinh → GHS → MTF | 显示盘面颜色；分离外晕与暗带；在外缘保护内完成显示亮度。 |
| dust_reflection：反射/暗星云/尘埃 | Asinh → GHS | 显露反射光与尘埃渐变；拉开目标亮度区间并保留连续过渡，新噪声策略只记录背景纹理代价。 |
| star_cluster：球状/疏散星团 | Asinh → MTF | 含星路线显示星色与密集中心；保留稀疏弱星并完成显示亮度。 |

新会话的 `stretch_hdr=morphology_selected_v1` 允许 Agent 为上表任一形态首次选择[双分支蒙版 HDR](../stage7-hdr.md)：温和分支保护明亮结构，强拉伸分支显露弱结构，再用共享平滑蒙版融合。保留真实形态、ROI 与选路理由，不按 M42/M31 等名称自动分类；无需先证明单链冲突。使用 --mode exception，默认逐步完成双分支并依据温和输出选择蒙版；显式 --batch 才预先冻结双链、蒙版与最终保护并按 HDR 原生骨架执行，不先 solve 普通请求。

这些配方是形态起始参考；profile 的参考顺序由分步 strategy.v3 或整链 strategy.v2 锁定，首次有父源证据时可用 exception 明确选择两步或双分支 HDR，不强制先完成长链或独立对照。形态名称不能替代图像证据、科学参数或采用理由。GHS 的 LP/SP/HP 每步读取该步真实输入域。后续更换路线须绑定上一提案及实测依据，不能用 custom 标签绕过。

<a id="default-stretch-sequence"></a>
### 默认执行顺序

1. **核对父源与区域。** 只使用已接受的线性科学父源；有可信 Stage 6 无星分支时优先主体，否则仍按同一形态链执行含星保护。先查看形态准备所得的真实带框主体上下文；需要额外画框代码时再读[诊断示例](../stage7-diagnostics.md#structure-roi-context)，区分天空、弱外缘、亮云、亮核与星晕；混合大框只用于整体保护，不能代表极暗外缘。
2. **冻结顺序，提议当前步。** 按实际形态选链，在[当前请求](#agent-factor-proposals-v25)中填写算子顺序、当前步 purpose、factors、约束与适用 landmarks；前缀与最终显示目标分别设置。后续 GHS 锚点来自实际前缀域，不沿用线性域数值。首次可基于父源测量、预览、ROI 与曲线作用依据选择 两步，不要求先试跑长链；已提交的顺序和因子仍不可变。
3. **预留额度并试跑。** 按下方[三次总额度](#v4-attempt-accounting)记账，再用[分步合同](../stage7-stepwise.md)独占冻结当前请求并 solve；整链示例须显式 `--batch`。多节 RGB MTF 收尾先预留同前缀 GHS 对照，两条 solve 各计一次；完成[末段比较](#rgb-tail-comparison)后才 generate。
4. **按实际结构决定。** 对照 Stage 6 autostretch、真实首步前缀及同位置局部面板，检查外缘、细丝、暗带、颜色和亮核过渡；结合邻近结构的实际成对差值与天空抬升判断收益；需要计算脚本时再读[诊断示例](../stage7-diagnostics.md#adjacent-structure-pairs)。中位亮度匹配、solved、推荐分或链长都不能代替可见性；科学保护通过后仍须填写[逐结构采用依据](#structured-stretch-assessment)。
5. **修订或结束。** 因子修订保持当前链；实测证明选链不适用时，可用新提案换算子、顺序或链长，均从同一线性父源重跑并共享剩余额度。目标达到、收益停滞、退化、无法判断或额度耗尽时停止新增试跑；保留实际收益最佳的已接受候选或可信恢复点，再完成正式 run/review，不把预算耗尽改写成目标达到。

按需读取：[因子与当前域](#agent-factor-proposals-v25)、[ROI 与科学保护](#current-roi-protection)、[GHS 参数尺度](#stretch-mtf-是-ghs-族的-b1-特例已实测)、[跨阶段验收](#reference-visibility-review)。仅使用相应入口时读取[参照反求](../stage7-legacy.md#reference-driven-baseline)、[蒙版 HDR](../stage7-hdr.md#masked-dual-hdr)、[HOO](../stage7-hoo.md#dualband-hoo-stretch)或[兼容附录](../stage7-legacy.md#stretch-compatibility-appendix)。

<a id="v4-attempt-accounting"></a>
<a id="默认-v4-试跑账目"></a>
### 三次独立试跑总额度

同一 Stage 7 任务的独立科学试跑总数最多为三次，不是三轮主提案再免费追加对照。普通链每个 solve 首次启动原生参数试算即占一次，首步不合格或中途失败仍占用；同次内部各步、full/前缀重放及配套正式 run 不重复计数。重新启动 solve 的技术恢复仍占一次。静态校验、冻结参数的诊断回放、比较已有报告和首次原生试算前的纠错不计；退出码本身不能证明是否已启动，依据实际试算日志、报告和回执核对。总数达到三次即停止新增试跑，仍可比较、审查或正式执行已有合格提案；保留实际收益最佳的已接受候选或可信恢复点，不因分类、同因子重跑、新 run ID、改 profile 或换路线追加额度。

额外求参同样记账：MTF/GHS 末段比较的两个多阶段候选各占一次；`compare-tail` 读取已有报告生成诊断材料，不另占次数，完整匹配的既有候选可复用。新会话不执行 baseline 或单步形态对照。完整 HDR 候选首次运行原生分支/融合时占一次；完整 HOO 求参也占一次，同份提案内的派生子链不拆分计数。内部单步分支或 RGB 参照链不能独立采用为主体；其他独立多阶段候选仍各自计次，不能改标为内部重放免除。切换 HDR/HOO 仍须满足原适用性与来源前置；不为额度不足自动切换。

程序在首次原生科学试跑前独占写入 reports/<run-id>/scientific-attempt.json，并以会话级锁核对三次总额；HOO 子链共享一份顶层账目，HDR 在首次执行分支前记账。原生启动前的超时或文件准备失败不计次。比较既有材料不增加次数。Agent 仍在 reason、review.notes 和下方模板解释请求/run、原因、实际状态、累计次数与剩余额度；账目不授权放宽科学保护。

| 原因分类 | 判断依据与计数 |
|---|---|
| 因子修订 | 根据上一轮证据有意调整因子、锚点或显示目标；启动新 solve 的原生试算即占一次。 |
| 路线修订 | 实测证明现链不适用后更换算子、顺序或链长，使用新提案从同一线性父源完整重跑；占一次，不重置总额。 |
| 独立对照 | MTF 及同前缀 GHS 多阶段候选各占一次；读取匹配的已有报告及 compare-tail 不计。 |
| 提案构造错误 | 目标放错步骤、漏项或错误绑定等；执行前纠正不占，已经启动后即占一次，修正后再次试跑另占一次。 |
| 技术恢复 | 因执行或材料发布错误恢复，科学父源、ROI、因子、锚点和约束保持原提案；只读回放不占，重新启动 solve 的原生试算即占一次。 |

例如：初始试跑已启动，累计 1/剩余 2；第二次因首步目标错放而提前停止，累计 2/剩余 1；纠正后第三次已启动，累计 3/剩余 0。此后即使是相同因子的技术恢复也不再启动科学试跑。反之，首次试跑前由可信前缀发现同一错误并纠正，累计仍为 0。分类用于解释实际开销，不授权自动重试或放宽科学保护。

末段比较例：MTF 链 solve 用第 1 次，同前缀 GHS 链 solve 用第 2 次，compare-tail 不计；剩余 1 次可修订其中一条末段并复用另一份完全匹配的报告。若改了共同前缀，旧对照失效，剩余 1 次不足以重建两条新链，不能超额求参或省略比较采用 MTF。换链例：原链实测不适用用第 1 次，新 MTF 收尾链用第 2 次，其同前缀 GHS 对照用第 3 次；后续只能对已有提案执行比较、正式重放与审查。

<a id="agent-factor-proposals-v25"></a>
<a id="agent-显式因子提案-v25"></a>
## 当前请求与因子提案

当前执行使用 `stretch_parameters=agent_proposal_v1` 和 `stretch_noise=diagnostic_only_v1`。默认由当前 Agent 根据科学测量、可信 ROI、实际预览与显示目标明确给定每步因子，Python 不调用外部模型、不猜缺失数值、不调整因子或自动扫描。每份请求的每步仍只有一组提案；硬约束不合格即 `ineligible` 并保留父源，天空 MAD/hf_sigma2 超预算仅记录诊断。Agent 按下方收益优先规则，用新请求与 run 有限增强或修订；不得把有限提案宣传为全局最优或自动采用。

使用既有 metrics/区域测量读取当前科学父源和各通道统计，实际查看天空、弱结构、亮核和代表星区。不能只凭 JPEG 猜科学黑点或把目标名称映射成固定 D/B。现有亮度评分不包含自动色彩保真门；Agent 仍须查看色相、饱和度、背景色噪与 MTF 收尾的颜色压缩。不得只追最大饱和度。

`starun-siril.stretch-request.v4` 保留 v2 的 source、regions、profile、targets、core_absence、operators、manual_lookup；每个 operator 在原字段上增加且必须增加 `factors`：

| method | factors | 自动计算项 |
|---|---|---|
| ght | D、B | 依该步实际域 ROI 计算 LP/SP/HP |
| asinh | stretch | 安全黑点 |
| mtf | midtones | 安全黑点，white=1 |
| autostretch，仅普通含星路线 | shadowsclip、targetbg | 原生 linked 计算；分星分支仍禁止 |

所有因子必须是有限 JSON 数值并符合原生命令范围；当前锁定的 Siril 1.4.4 CLI 实测要求 GHS B=0..15，因子路径按此范围前置检查，不增加命令授权。请求中的因子不经 log/logit 往返替换。后续步骤读取真实前缀输出重新计算锚点，不能把线性域 SP 直接套到非线性域。整链若未满足当前域目标，停止并留存原因，不隐式搜索。

每步至多评估一个候选，保留一次完整前缀重放：无 full 为两次原生调用，有 full 为四次；full 重放仍是诊断，不属于第二套因子。报告继续使用受保护参数 schema，增加 `generation=agent_proposal_v1`，绑定请求、因子、当前域锚点、来源、ROI、运行时及辅助代码。资格失败仍保存科学图、预览和原生日志作诊断，不能作为科学父源；非有限候选不生成误导的局部面板。正式 SSF 与输出继续独立复测并完成实际像素审查。

默认分步使用 goals.v4。显式 `--batch` 使用 goals.v3，--mode profile 为形态多阶段候选；新会话拒绝 --mode compare 和 baseline_factors，不生成独立单步对照。--mode exception 以 operators 代替 phases，按[例外证据规则](#stretch-routing-v29)区分首次短链、首次 HDR 与后续修订的绑定。goals.v1/v2 与搜索仅保留历史含义，不授权新会话执行。

以下为字段结构示例，数值须依据当前图像填写，不是默认配方：

```json
{"method":"ght","purpose":"Reveal the measured faint shell","factors":{"D":1.8,"B":1.0},"landmarks":{"LP":{"region":"sky","statistic":"p99"},"SP":{"region":"faint","statistic":"median"},"HP":{"region":"core","statistic":"p01"}},"constraints":[{"region":"faint","statistic":"median","min":0.1,"max":0.4}]}
```

完整请求须覆盖全部适用保护区；上面的 operator 示例不能单独通过验证。默认由 goals.v3 和 init-request 生成带不可变计划的 request.v4；不手写无 strategy 请求。没有无星分支仍执行同一形态策略并保护星核、星色和光晕。

HOO 默认使用 `starun-siril.dualband-request.v4`，沿用受保护 v2 请求字段，但 rgb/ha/oiii 的每步都给出 factors，分别生成 v4 子请求。RGB 星点链、单色估计与配色公式不变；已有 OIII 降噪变体仍须独立的适用证据，不为追求拉伸强度默认开启。可信无星主体与带星诊断沿用[当前主体边界](#starless-subject-scope-v24)，Stage 8–10 不增加步骤或自动采用。

<a id="benefit-first-stretch"></a>
## 达到参照目标后的按需增强与有限修订

默认多节链先使主体和弱结构达到 Stage 6 参照可见性，再以实际首步前缀检查后续局部层次与过渡收益；达到目标后停止，不以更强曲线、最大分离度或固定链条完成度作为目标。拉伸显露的颗粒噪声先记录，本阶段不处理，也不因更平滑、更暗或参数更温和而优先较弱候选。结构丢失、伪结构、明显色块、科学端点失败与证据不完整仍不能采用；不承诺后续降噪能够修复这些损害。

试跑前仍声明真实天空 MAD/hf_sigma2 参考预算和主体/亮核目标，保留完整测量。`stretch_noise=diagnostic_only_v1` 只将 role=background 的 mad/hf_sigma2 条目改作诊断；超预算保留 `satisfied=false` 并写入 `noise_warnings`，不导致资格失败。其他区域约束、科学端点、来源、几何、有限值及原生错误检查仍为硬门。不得删除测量、伪造通过或修改既有报告。

1. 明确尚需显露的主体区域，完成初始提案与实际像素对照。候选已合格但仍有明确欠显露结构且保护存在余量时，在剩余额度内继续修订，不能因 solved 提前停止；先区分因子问题与选链问题，不额外生成无依据的单链来决定是否继续。
2. 同一 Stage 7 任务最多三次独立科学试跑，初始候选、修订、换链及额外对照共享额度，按下方[试跑账目](#v4-attempt-accounting)计数，提前失败和技术恢复不豁免。**因子修订**保持该链的算子与链长，只调整有依据的因子、当前域锚点或显示目标；**路线修订**允许在实测显示现链不适用时改变算子、顺序或链长；Agent 根据实际形态选择 HDR 无需先证明单链冲突。须记录受影响 ROI、现链不足、换链理由及剩余额度；真实 GHS 前后证据可支持缩短链，不要求先失败或制造保护冲突。两类修订均用新 request 路径及 run ID 独占创建，从同一已接受线性父源重跑完整链，保持可比 ROI、显示条件及最终目标，不在失败候选上累积拉伸，也不降低科学保护。冻结的是每份已提交提案，不是整个阶段的初选路线。带 strategy 的请求仍受原 plan 约束，换链使用继承原始分类的 exception 计划，不改旧 plan 或混绑报告。HOO/HDR 保留各自来源、派生链与保护合同；换路线不重置次数，也不增加隐式搜索。
3. 主体与弱结构实际收益优先，不以噪声较低打破平局。新策略的 `subject_contrast` 为各目标 ROI 相对天空中位数的方向修正差值，取最弱目标；原生搜索与链间工具仍以该值给出参考推荐，同分按稳定顺序；该排序不决定实际采用，不除以天空 MAD、不使用 95% 近优降档，也不惩罚全图像素变化。它仍不是细节保真或物理 SNR；Agent 应按真实收益选择任一合格候选并记录理由。
4. 按下方[反馈与停止节点](#agent-led-enhancement)决定修订或停止；每份正式候选仍独立生成、执行和审查。

随机颗粒显露或预算内的背景抬升本身不单独导致视觉 reject；必须仍能可靠审查主体、颜色与亮核，并在 review.notes 记录暂未处理的噪声区域。MTF 收尾不得为了压暗背景把已显露的外壳或细丝压回去。采用不代表噪声已消除或最终交付合格，Stage 8/10 保持各自真实资格与审查要求。

<a id="agent-led-enhancement"></a>
### Agent 主导增强目标、提案与采用

Agent 负责定义要显露的结构、选择现有路线和算子、给出因子及 ROI 目标，并根据实际像素决定修订与采用。示例数值、形态名称和代码推荐分不构成显示目标；`solved` 只证明当前提案满足执行约束。沿用现有 `reason/purpose/factors/landmarks/constraints` 和 review.notes 记录判断，不新增参数字段、自动评分或固定增强档位。

| 决策内容 | Agent 的依据与权限 | 现有执行边界 |
|---|---|---|
| 增强目标与 ROI | 根据当前外缘、细丝、暗带、亮核及用户偏好确定优先顺序，声明可辨收益和保护区间。 | ROI、来源、当前像素域与统计必须真实绑定；不能把目标区改标为天空。 |
| 曲线与强度 | 显式提议算子顺序、D/B、stretch、midtones 及 GHS 锚点所引用的区域/统计量。 | Python 计算安全黑点和当前域锚点，校验合法范围与科学端点；不直接覆盖这些派生值。 |
| 路线与候选 | 判断现有形态链是否适用，必要时使用已有显式 custom 路线；选择实际收益最佳的合格候选。 | 默认 profile 由 strategy 锁定配方；例外须绑定对应图像证据，不获得未执行的比较证据。代码推荐不自动采用。 |

每次按以下顺序形成提案：因子修订保持当前链结构；有实测依据的路线修订可在同一剩余额度内换链。每份新提案独立冻结，不同时把所有因子推向边界：

1. **先细化目标与局部参照。** 选框时先看整图定位和实际带框主体上下文；需要补充画框时读[诊断示例](../stage7-diagnostics.md#structure-roi-context)，逐框说明真实内容、边界与邻域；将实际存在的细丝、暗带、微弱外缘和亮边分别记录，亮边不能代表全部弱结构，也不强行凑齐不可辨类型。保护 ROI 继续进入求参的 regions/targets/constraints；额外成对诊断 ROI 使用独立的 regions.v1 和已有 [区域测量](../quality.md#区域级科学测量)，绑定同一父源、几何和候选，不为测量方便加入求参 targets。后者会改变所有 faint ROI 的最弱目标评分及保护要求。参照区按真实角色声明，邻近弥散目标不改标为 background；没有可信天空时沿用原前置门。`targets.dark` 仍表示低于天空，不能代替暗带相对亮云的局部对比。
2. **先读阻塞约束，再决定修订。** 从当前报告的 constraints/reasons 定位区域 ID、statistic、value、min/max，回查对应 phase 与共享 protection，区分自行声明的显示目标和不可绕过的保护。天空 median、主体区间、亮核上界和 span 仍能阻止候选；仅天空 MAD/hf_sigma2 按当前策略作诊断。每轮显示预算有变时，在现有 purpose/review.notes 记录 ROI、统计量、旧→新区间、对应实测值及局部结构、亮核和过渡证据，解释原预算为何不适用与修订取舍；用新 goals/request 修订，不改旧报告，也不只为通过约束放宽预算。phase 与共享 protection 取交集，只改 phase 不会放宽共享上限；显式比较中的同轮基线和高阶候选同步使用相同最终约束。科学端点、来源、有限值、几何及必要亮核保护继续保留，`span=p99-p01` 只保护层次，不按越大越好排序。
3. **对准当前域，再分配强度。** 检查每步 input_statistics 和真实前缀预览，先调整 SP 所引用的目标 ROI/统计量，复核 LP/HP 对暗部与亮核的保护，再提议 D、B、Asinh stretch 或 MTF midtones。LP 以下保持该步线性变换，不等于背景亮度锁定；天空亮度仍按实际输出和已声明预算检查。Python 继续从该步实际输入派生锚点和安全黑点，不直接覆盖数值；后续步骤不复用链首线性域统计。弱外缘仍暗且亮核有余量时可提高 D 或 Asinh stretch；细丝与邻域一起变亮而层次变平时，先修订 SP 引用及曲线形状。MTF 提亮通常降低 midtones；B 改变形状，不按大小判断收益。每次调整都说明待改善结构及前一轮证据，不固定增强幅度或扩大参数范围。
4. **计算成对差值并用于反馈。** 复用已有 ROI median；需要脚本时按[邻近结构配对示例](../stage7-diagnostics.md#adjacent-structure-pairs)计算；细丝、暗带、外缘和亮边都使用上下文中确认的邻近参照，远处天空差值仅解释主体—天空显示分离。优先读取求参报告中已声明 ROI 的亮度统计；额外诊断沿用独立 metrics 的逐通道统计，分别记录 C_R/C_G/C_B，不把 RGB 中位数加权冒充亮度中位数，也不混用两种口径。同次比较固定 ROI、通道、科学单位和显示条件，记录 `ΔC=C_candidate-C_control`，control 默认使用真实首步前缀，也可补充同条件下已接受的前轮候选；显式比较时优先为同轮独立单链。不同对照分别记录，默认路线披露未执行独立单链；与线性父源的差只说明显示变换，不称为物理信号增益或改善率。保留差值方向，不用绝对值掩盖反转，不合成总分或更改推荐排序；正 ΔC 也须实际查看连续结构、颜色和亮核，不能把随机颗粒显露算作细节收益。这些计算在 Agent 侧指导下一轮提案与采用，当前代码不新增评分。
5. **由反馈决定修订、采用与停止。** 遵守[三次试跑预算](#v4-attempt-accounting)，每轮用新请求从同一可信线性父源重跑完整链。ROI 或最终目标变化后，重建参照、前缀与候选的同条件对照，不直接排名不同条件下的旧分数；显式双链路线同步重建两候选。可采用推荐分较低但实际结构更好的合格候选，并用其参数报告 generate；显式比较才绑定原 comparison，不改 recommended_run_id。在 purpose/review.notes 分开记录阶段停止原因与本轮结果：“目标达到”须有主体结构证据；“预算耗尽”仅表示不能继续试跑；“本次提案失败”须保留具体失败依据，第三次失败同时记录后两者。收益停滞、仅增加颗粒或全幅亮度、实质退化或无法判断时如实停止。保留 partial、未解决结构及取舍，不因 solved、预算耗尽或采用候选改为 met；较暗天空不能代替主体收益。保留实际收益最佳的已接受候选或可信恢复点，未通过审查的图不能成为父源。

默认通过 goals.v4 与 init-request --mode profile 冻结形态顺序和当前步参数；显式 --batch 才使用 goals.v3 的整链参数。不额外生成独立单步候选。例外按[统一例外证据规则](#stretch-routing-v29)执行。局部成对反馈不授权修改配方表、命令范围或科学保护，也不替代 full 诊断与 Stage 9 冻结链。

<a id="whole-chain-constraint-preflight"></a>
<a id="declared-morphology"></a>
### Declare morphology before choosing a profile

New sessions freeze `stretch_morphology=roi_declared_v1`. Prepare independent material before creating the first strategy:

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril_stretch.py morphology-context \
  --session /abs/session --source artifacts/060-starless.fit \
  --regions reports/regions.json --preview previews/060-starless.jpg \
  --run-id 069-morphology-context
```

The command validates the accepted linear parent, its committed native preview and regions.v1, then exclusively writes `reports/<run-id>/morphology-context.json` and original-size `roi-context.png`. Labels contain ROI ID and existing role only. Display pixels outside annotations, geometry and ICC remain unchanged; ROI coordinates follow the preview top-left convention used by scientific measurements. It does not invoke Siril, claim an attempt, choose a profile or freeze the first classification. Existing files, symlinks, foreign previews and incompatible geometry are rejected.

Inspect the actual preview and boxed panel. Goals v3 now requires `morphology` containing `context` (returned manifest file binding), `primary_subject` (the primary object or complex), `processing_goal` and `regions`. Each region entry has `region` (existing ROI ID), `relation`, `features` and a concrete nonempty `observation`. Every solving ROI must appear exactly once. Use `goals.reason` to explain why the declared primary structures support the chosen profile; names, colour, global maxima and prose are not parsed into a route.

| processing_goal | profile | Required primary features |
|---|---|---|
| emission_structure | emission_nebula | diffuse_nebulosity or filament |
| core_envelope_balance | high_dynamic_range | bright_core in a core ROI and faint_envelope in a separate faint ROI |
| galaxy_structure | galaxy_halo | galaxy_disk, spiral_arm, galaxy_halo or galaxy_dust_lane |
| dust_reflection_structure | dust_reflection | reflection_nebulosity, dust_nebulosity or dark_lane |
| stellar_structure | star_cluster | cluster_center, cluster_members or cluster_outskirts |

Relations are `primary`, `companion`, `background`, `field_star`. Features are the table vocabulary plus `background`, `field_stars`, `stellar_residual`, `unresolved`; these are processing observations, not proof of spectral origin or catalogue identity. Background ROIs must declare background only. `field_star`, field_stars and stellar_residual require stars ROIs; stars ROIs may contain stellar/cluster features or unresolved content. bright_core and faint_envelope require core/faint roles respectively. Other nonbackground ROIs retain primary/companion identity and their existing scientific protection. Unresolved content must be described and cannot satisfy profile support. Companion cores, field stars and residuals never substitute for the primary HDR core/envelope pair.

Run `preflight` after filling all declarations and intermediate-constraint bases. It aggregates morphology and constraint issues and remains read-only. `init-request`, request loading and native launch verify context bindings again. Revisions disclose morphology differences; changes to the primary subject, processing goal or ROI declarations must bind the previous parameter report and identify affected ROIs. Initial short routes use the bound exception mode; subsequent route changes bind their actual previous evidence and affected ROIs. HOO, HDR, compare and tail routes share these declarations without acquiring alternative classification authority.

Finalization inventories and preserves the context manifest, boxed panel and its native parent preview even when pruning other intermediates. They remain diagnostic evidence, outside canonical outputs and scientific-parent authority. Scientific parent FITS retain their existing cleanup policy; committed replay verifies preserved evidence without reconstructing pruned science. Historical sessions are not migrated. The Agent remains responsible for reading images and honest observations; declaration consistency is not visual acceptance.

### Whole-chain constraint preflight

Preflight v3 adds `curve_effects` and `curve_amplification`. For a verified actual input, MTF uses the same safe blackpoint and explicit MID/HIGH as solving, transforms each scientific channel, then measures ROI median, p01/p99/p999, maximum RGB and span. Mapping deviations beyond `32 * float32.eps` (twice for span) block freezing; roundoff-boundary cases require native verification with the original strict constraints. Noise remains diagnostic. Never substitute MTF of a luminance median for luminance measured after per-channel MTF. Unknown prefixes remain pending without another native trial.

GHS effects come only from verified actual input/output prefixes. `effect_diagnostics` records absolute and relative brightness/span changes and signed subject-minus-sky contrast; a zero baseline has null relative change. Reuse the retained scientific images, JPEGs and local panels, inspect the same display mapping, and record concrete ROI observations. Small positive changes, legal parameters and final MTF brightness do not establish structural benefit. These diagnostics add no stopping or adoption gate and change no attempt count.

`curve_amplification` is diagnostic only. For a known MTF input it reports the analytic derivative of the same tone curve at each ROI's per-channel input median together with the predicted p99-p01 span gain; a clamped input reports a zero slope and a flag. For a measured GHS prefix it reports the measured span gain and leaves the slope unavailable. `subject_over_background` compares the subject and background aggregates. A zero span baseline, a missing baseline or an unmeasurable denominator is recorded as null with a reason. These numbers add no threshold, warning, stopping or adoption gate and change no attempt count; the ratio is a curve and data diagnostic, not a physical SNR, a noise measurement or a substitute for reviewing actual pixels.

Use `--mode exception` with explicit `operators` for an initial evidence-bound two-step route. Keep the declared morphology, actual parent preview, ROI, per-step purpose, factors, shared protection and route reason. After a trial, a shorter route may bind a verified GHS input/output pair and affected target ROIs instead of a failure or reviewed core conflict. Morphology-selected HDR may also be the initial proposal for any of the five profiles without a prior single-chain conflict; bind its explicit native branches, mask and final protection. Other route exceptions retain their measured-failure/conflict rules. Multi-step RGB MTF tails still require their same-prefix comparison. Changing the chain creates a fresh bound proposal from the accepted linear parent; it never edits a plan or promotes a prefix to a scientific parent.

New sessions freeze `stretch_constraint_preflight=curve_effect_review_v3`. Before the first native trial, inspect every variant and step together. Separate intermediate output goals from final display goals: correcting a median lower bound does not correct a remaining p99 lower bound in the same or a later step. Shared protection still intersects every phase constraint.

`preflight` accepts the same arguments as `init-request` and only prints JSON. It does not create a plan, invoke Siril or consume a scientific attempt. Status `ready` exits 0; `needs_revision` exits 1 and lists all missing bases and detected constraint conflicts together. `init-request` performs the same checks, saves `constraint-preflight.json` beside a successful strategy and binds it into the immutable plan. Request loading and native-attempt claiming verify the binding again.

The report lists variant, one-based step, ROI, statistic, effective min/max, shared protection, input reference, available input value and basis. The first RGB step uses actual linear-parent statistics. Later inputs are unknown unless an explicitly bound previous report supplies a verified same-source, same-ROI, identical scientific prefix. HOO channel inputs remain unknown until their native preparation is available; RGB statistics are never substituted for Ha/OIII measurements. Unknown prefixes and GHS outputs are not predicted. An input value outside an output interval is diagnostic; a known MTF input is separately mapped to check actual output reachability.

Goals v3 adds required `constraint_review` under this policy. For every nonfinal step with a positive lower bound on `median`, `p01`, `p99`, `p999` or `max_rgb`, supply an entry with exact `variant`, `step`, `region`, `statistic`, `reason` and nonempty `evidence` file bindings. Explain why this output requirement belongs at that step, rather than only after final display preparation. Evidence must identify a committed preview of the actual parent or a verified same-source native prefix/panel. Use `[]` when there are no such bounds. Identical scientific prefixes with identical bounds share a basis across main/tail controls; invalid locations, duplicate entries, missing bases and changed evidence block native execution. The program checks coverage and bindings, not the scientific plausibility of prose.

```json
{"constraint_review":[{"variant":"profile","step":1,"region":"core","statistic":"p99","reason":"This phase must retain the measured core range before local separation; this is an intermediate output requirement, not the final display level.","evidence":[{"path":"previews/060-starless.jpg","sha256":"<actual-file-sha256>"}]}]}
```

Use actual IDs, measured bounds and evidence; the example is only a field structure. Run `scripts/deep_sky_siril_stretch.py preflight --batch --session /abs/session --source artifacts/060-starless.fit --regions reports/regions.json --goals reports/stretch-goals.json --run-id 071-morphology --mode profile`, inspect the complete report and correct all relevant steps before freezing the request. Do not lower necessary scientific protection to obtain `ready`; native eligibility and visual review remain separate.

The read-only IC434 review demonstrates two independent issues: step-one core median `0.003792 < 0.005` and p99 `0.008387 < 0.01`, followed by a revision that changed the median bound but left p99 unchanged. These are output-goal placement errors, not evidence that GHS is unsuitable. Existing session reports remain unchanged.

<a id="default-v4-flow"></a>
<a id="默认-v4-最短衔接"></a>
<a id="stretch-routing-v29"></a>
## 形态绑定、修订证据与执行入口

`init-request --mode profile|exception` 默认 profile；以下 goals.v3 结构仅用于显式 `--batch`。`starun-siril.stretch-goals.v3` 必填 schema、profile、reason、evidence、targets、core_absence、protection、manual_lookup、revision；profile 用 phases（按配方顺序，每步 constraints、factors，GHS 另含 landmarks）；exception 用明确的 operators。可选 tail 包含 purpose、factors、landmarks；channels 包含 rgb/ha/oiii 的完整 operators；hdr 包含 gentle/faint 的明确原生命令链及 mask（mask_low、mask_high、sigma）。蒙版参数同样冻结，改动必须绑定上一提案的修订依据。参数全部来自当前科学像素，不填入默认强度，不扫描。

首次 revision=null，evidence 至少绑定实际父源的已提交预览；仅 FITS 哈希、文件名、星层或其他候选预览不构成形态依据。无星与含星均保留形态绑定，profile 使用形态配方顺序；高动态跨度策略须有实际 core ROI，含星路径须有 stars 保护。strategy.json、goals、请求、报告和面板均使用新路径，不能覆盖已有证据。程序按线性父源哈希绑定首次分类；仅换 run ID、改 profile 或重建计划不能重新声明首次提案。

新修订的 revision 包含 parameters（上一份原生参数报告绑定）、reason（具体失败／换链理由）、affected_regions（真实 ROI ID）、evidence（该报告保留的科学图或局部面板）及 target_change。没有显示目标变化时 target_change=null；变化时填 kind=display_correction 或 science_protection、reason、tradeoff 和实际 evidence。程序自动列出因子、形态、算子路线、逐步约束及 ROI 差异；同一路线的中间步骤显示区间变化也须单独说明依据。不能删除、移动已有亮核／星区来规避保护，也不能降低必要高光上界或核跨度保护；只收紧末步不能掩盖共享保护的放宽。既定科学端点不变。

已获准 exception 的同路线因子修订沿用原例外授权，仍须绑定上一提案和实际证据；改变 profile、算子或链长仍须绑定对应路线依据；HDR 依据实际形态选择，不要求先证明单链冲突。历史 v1 保留原判定。

当前 curve_effect_review_v3 的 exception 按以下情形绑定证据；新会话另冻结 stretch_hdr=morphology_selected_v1，历史会话保留原判定：

1. **首次短链**：revision=null，显式 operators 由所属形态允许的算子组成，共两步，且不包含 HDR。绑定真实父源预览、形态声明、ROI、每步用途与参数、共享保护及选链理由；不要求上一份原生报告，也不要求先试跑长链或制造失败。
2. **后续缩短**：revision 绑定上一份原生参数报告；新链比原链更短，evidence 同时绑定该报告中经验证的某一步 GHS 实际输入与输出，affected_regions 至少包含一个当前目标 ROI。程序复核保留像素、原生日志与参数；无需额外制造失败或保护冲突。
3. **双分支 HDR**：五类形态均可由 Agent 根据实际父源、ROI、动态范围及目标首次选择，revision=null，无需上一份单链报告、失败或保护冲突。在现有 hdr 中冻结 gentle/faint 原生双链和 mask，绑定匹配计划的最终 stretch-protection；后续修订可用 revision.parameters 绑定上一 HDR 的真实运行回执。完整执行及保护见 [HDR](../stage7-hdr.md)。
4. **其他路线例外**：仍须可复测原生科学失败或已审查的同源保护冲突。已 solved 的提案以保护冲突授权时，另绑定 conflict_review：它必须审查同一参数报告，相关结构的 regression.core_conflict=demonstrated，并引用真实同源比较。

技术错误、非有限错误、单独噪声诊断和“参数合法／保护亮核”的泛泛理由不授权例外。独立基线、尾部或 RGB 星链的失败不能单独授权主体换链。单次失败不能证明整类算子不适用。所有例外继续受三次共享试跑额度、多步 RGB MTF 末段比较、科学保护及实际像素审查约束。

相同父源、相同科学变换仅修改显示阈值时，报告明确标记 new_image_benefit_from_revision=false，即“相对上一提案没有新增图像收益”。合理的目标纠错仍可独立成立，但不能冒充新增收益；与真实首步前缀的增强比较继续单独完成。已知前缀可用于执行前发现目标错置，不能倒改旧报告。

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril_stretch.py init-request --batch \
  --session /abs/session --source artifacts/linear-starless.fit \
  --regions reports/regions.json --goals reports/stretch-goals.json \
  --run-id 071-morphology --mode profile
"$STARUN_PYTHON" scripts/deep_sky_siril_stretch.py solve \
  --session /abs/session --request reports/071-morphology/profile-request.json \
  --run-id 071-morphology-profile
"$STARUN_PYTHON" scripts/deep_sky_siril_stretch.py generate \
  --session /abs/session --parameters reports/071-morphology-profile/stretch-parameters.json
```

RGB MTF 收尾须在主提案完成后求解同前缀 GHS 多阶段对照；分步模式独立建立绑定 tail_control 的分步候选，整链模式在 goals.v3 提供 tail 后求解 tail-request.json。主提案失败不继续已知无效前缀。compare-tail 完成后，generate 绑定返回的实际比较报告，例如 `--comparison reports/074-tail/comparison.json`。不生成独立单步形态对照；首次主链、修订和尾部科学试跑共享三次额度，比较已有材料不计次。

正式 run ID 可通过 generate --run-id 显式指定；参数报告仍绑定原求参 run ID、原线性父源、策略和完整科学变换，不复制或改写报告。HOO request.v4 的 strategy 直接绑定本计划，Ha/OIII 主体链与形态顺序一致，RGB 星链保留原合同；同份 HOO 的提取与子链共用一次账目。HDR 必须在 exception 中冻结明确双链，并声明匹配计划的 stretch-protection；旧自动 HDR 生成器不用于新会话。

<a id="reference-driven-baseline"></a>
<a id="baseline-budget-proposal"></a>

历史参照反求；仅回放或明确查询历史时读取。 见[对应材料](../stage7-legacy.md#reference-driven-baseline)。

<a id="reference-visibility-review"></a>
### 跨阶段材料与实际验收

新的 Stage 7 无星候选必须绑定 Stage 6 主体 autostretch 原预览、对应 receipt/review/脚本、正式候选、统一 sRGB 重放图及相同位置和缩放的局部面板。执行器发布 reports/<run-id>/visibility/visibility.json，审查 receipt 绑定完整报告，必看材料增加一张整图加局部面板，报告继续绑定原预览及独立原生整图；缺项、来源或哈希漂移不能通过。参照和候选不各自追加 autostretch；面板用相同缩放并排显示统一 sRGB 的整图及每个 ROI；统一管理色彩、原始几何和局部缩放，原 Stage 6 图保留为跨阶段来源证据。

受保护 MTF/GHS、HDR 和 HOO 从已有参数/保护报告取得 ROI；手写无星 SSF 必须用 `# stretch-visibility-regions:` 绑定一份既有 regions.v1 文件的 path/sha256/size。该绑定引用当前线性父源及原始尺寸；没有可信 Stage 6 分支的含星路线保留原前置与审查，不伪造跨阶段材料。

另需一张显示尺度对照（长边 1024 px）。用 `scripts/deep_sky_siril_display_scale.py prepare --session … --source <候选 JPEG> --reference <父源 JPEG> --run-id <新编号>` 从本 run 已提交的直接预览生成 `reports/<run-id>/display-scale.png`，父源与候选同尺度并排。仅该 PNG 计入 `inspected_materials`，同目录的 `display-scale.json` 回执落盘但不计入材料。它继承两份直接预览的显示传递，不另做拉伸、不读科学像素；只用于判断典型观看尺寸下主体是否仍可辨、是否塌成色块或灰面。该图不产生 ROI 坐标，不得用于选择科学 ROI（见[区域测量](../quality.md#区域级科学测量)），也不替代本节的同尺度整图加局部面板或 1:1 裁片。

| 保护对象 | 验收依据 |
|---|---|
| 科学像素 | 逐通道新增高光跨界、阴影裁剪、有限值与几何保护；不移动白点或裁零补救。 |
| 亮核层次 | 实际查看核心与周围过渡，结合 p99/p999/max_rgb/span；小于 1 不等于视觉上仍有层次。 |
| 颜色 | 检查单通道先饱和、亮部褪色、色块及弱结构原有色彩。 |
| 星点 | 保留同链 full 重放诊断，Stage 9 审查实际星残差与回混。 |
| 局部过渡 | 暗带不变成硬黑线，云气无生硬轮廓，亮核到外缘连续；成对差值只解释变化，span 只保护层次。 |

采用顺序为：**科学保护通过 → 弱结构达到参照可见性 → 需要增强的局部层次确有改善 → 过渡与颜色自然**。整链已足够时不要求追加修订或额外局部增强；实际执行至少两步后才允许完成普通主体候选。中位亮度匹配或超过参照不能单独证明可见性：同时检查天空抬升、主体与邻近参照的分离、颜色和核心过渡；主体与天空一起变亮不能称为局部收益，不以压暗天空换取弱结构损失。已有结构明显退回背景时必须修订，或在 review.notes 明确记录亮核保护所需的实际取舍及受影响 ROI；仅参数合格、分离度最大或推荐第一均不能替代参照审查。subject_contrast 降为显示分离的参考推荐，数值不作为最终目标或自动通过条件。达到目标就停止后续修订，噪声继续只记录，伪结构、色块与真实层次损失仍不能接受。

<a id="structured-stretch-assessment"></a>
### 逐结构采用依据

新会话参照报告标记 review_contract=roi_reference_assessment_v2，review.v1 的 stretch_assessment 必须逐结构引用实际 panel 和 detail_controls。reference_visibility 仅对照 Stage 6／已接受线性参照；detail_effect 仅对照列明的增强控制图，末段保色对照不能替代主体增强对照。骨架只预填绑定，不自动填写结论。历史 v1 或无标记报告继续按原合同审查，不回填。

审查块包含 `visibility`、`structures` 和 `adoption_reason`。每个 structure 的 `region` 使用 `{regions: 文件绑定, id: ROI ID}`，覆盖参照报告全部 faint/core ROI；独立诊断 regions.v1 可增加真实局部参照，不加入求参 targets，也不改变最低 faint 分数。每个条目必须回答：

| 字段 | 结论与依据 |
|---|---|
| reference_visibility | `met / partial / not_met / uncertain`；逐个说明外缘、细丝、暗带和亮核的参照可见性，亮核同时检查层次而非只看亮度。 |
| detail_effect | `improved / unchanged / regressed / not_applicable / uncertain`；相对实际基础对照说明额外增强的收益与代价。未追加增强时填 not_applicable，说明基础已充分或当前选择原因，不强求正收益。 |
| observation | 同位置的实际结构、颜色和过渡观察；HOO 按真实 Ha/OIII 子链判断增强，映射本身和 RGB/HOO 的色差不算细节收益。 |
| regression | 参照仅部分达到、未达到或额外增强造成退化时，记录 loss、core_conflict、core_region、evidence、tradeoff_reason；其他情况为 null。 |

`core_conflict` 使用 `demonstrated / not_demonstrated / uncertain`。声称已证实时，core_region 必须指向真实 core ROI，evidence 必须引用本 run 已绑定的同源对照图或报告；不得用“保护亮核”套话代替实际对照。未证实或无法判断时明确记录，不能伪称存在冲突。采用理由必须解释保留该代价的原因，拒绝或不确定时说明为何保留父源。

普通多步链始终保留真实首步原生前缀；MTF/GHS 末段面板可补充，但不能替代前缀；独立单步对照仅保留历史回放。HDR 使用温和分支，HOO 使用对应 Ha/OIII 单通道前缀。所有对照采用相同显示条件、几何和 ROI 尺度；显示尺度图只作整体可读性复核，不改变同尺度面板与 1:1 局部判据，也不重复 Stage 10 的全图跨度告警。报告自动展示实际参数、参照／候选与控制图统计、天空分离、亮核跨度及修订差异。亮度或分离下降是复核诊断，不设置统一参照亮度比例通过线。

验证器检查字段、ROI、来源、哈希和可确定的矛盾：与声明控制图的 ROI 科学像素相同不能填写 improved。accept 不得保留主要 ROI 的 not_met 或 uncertain。partial 可以基于风格或其他明确目标采用，但须绑定实际对照、列出未完成结构和具体取舍；风格取舍允许 core_conflict=not_demonstrated，不强制虚构亮核冲突。预算耗尽不能改写成 met。Stage 10 可以处理真实剩余问题，不能追认 Stage 7 已充分显露；合星后仍审查星核、星色、光晕与弱结构。

<a id="structure-roi-context"></a>
<a id="adjacent-structure-pairs"></a>

需要带框上下文、邻近结构测量或记录示例时，读取诊断材料；本节要求的实际 ROI 审查不省略。 见[对应材料](../stage7-diagnostics.md#structure-roi-context)。

<a id="starless-subject-scope-v24"></a>
<a id="无星主体与带星诊断的边界-v24"></a>
## 无星主体与带星诊断的边界

当前 `starless_stretch=subject_only_full_diagnostic_v1` 下，可信无星分支以无星科学本体的弱结构、亮核、科学端点及适用区域约束决定合规；新噪声策略另将天空 MAD/高频改作诊断。星区仍须声明以保留诊断，但不强制每步填写星区亮度/跨度预算；已填写的星区约束仅在同链带星重放上诊断，不套在无星本体上，也不淘汰主体候选。普通含星直接拉伸继续保护星区；显式 legacy 和历史策略按[兼容附录](../stage7-legacy.md#stretch-compatibility-appendix)保留原保护。

GHS 强度由当前域 ROI 和真实前缀输出决定，按下方 [D 参数尺度](#stretch-mtf-是-ghs-族的-b1-特例已实测)理解对数参数化，不设推荐起始区间。无星本体按当前会话的端点预算检查，亮核层次丢失或其他硬约束失败仍拒绝。保留同源链路比较，每份请求一次提案、Agent 可有限递进，显式兼容模式才由代码搜索，不为追求 D 数值强行增亮。

每份候选仍实际执行完全相同的 full 重放，绑定脚本、日志、科学图、预览、星区测量及既有/新增端点；报告写入 `full_replay_role=diagnostic`。带星非有限像素、几何变化、日志报错或来源漂移仍失败；星区数值不合规和有限值端点仅作完整披露。共享蒙版 HDR 对最终无星混合图执行主体硬门，full 同蒙版重放同样作诊断。HOO 保持三条冻结链及原 RGB 星层，不改变配色或残差公式。

Stage 7 接受只表示主体可用。Stage 9 再根据实际星残差与最终混合结果选择原允许范围内的 strength，检查星径、光晕、星色、平顶、负值和端点。strength 是贡献权重，不能恢复已裁剪的星核或保证消除肥大；不可接受时保留可信恢复点；可按 [Stage 9 独立星点曲线](stage-09-stars-recompose.md#stellar-curve-selection)从原线性 full/starless 对显式提议 Fs，或修订主体拉伸候选并重新审查。不直接拉伸差值星层，不自动降低贡献直到通过，不改用 Screen。

本阶段维护专属协议正文、参数、SSF 与审查。按 [协议索引](../protocol-index.md) 绑定本文件为唯一 primary 和完整 SHA-256；实际参与决策的其他材料按原规则记录为 supporting。通用质量门见 [quality](../quality.md)。

<a id="current-roi-protection"></a>
<a id="roi-protected-stretch-v22"></a>
<a id="roi-保护与主体分离度-v22"></a>
## ROI 约束与科学保护

形态 profile 从 [stretch-profiles.json](../stretch-profiles.json) 的五类策略中选择；custom 仅为绑定实测例外后的请求标签。新执行必须绑定 strategy.v2，程序逐字校验实际链与冻结计划。targets 将每个 faint ID 映射为 bright/dark；有 core 时 core_absence=null，没有时说明真实观察。每步覆盖天空、主体、亮核和适用星区；含星路径要求星点保护，可信无星的同链 full 重放保持诊断合同。

每个天空区域必须声明输出 MAD 和 hf_sigma2 参考上限；当前噪声策略只记录超限；旧策略的硬门见兼容附录。后者是原分辨率亮度减 Gaussian sigma=2 的平均绝对幅度，四周须有八像素真实过滤上下文，不做合成填充。天空不能与其他天空或主体、星区重叠。这是工程纹理诊断，不等于物理噪声或 SNR。每个 core 区域以及普通含星路线的 stars 区域须同时有亮度上界（p99、p999 或 max_rgb）与 span 硬约束。数值依据当前已接受科学源与显示目标，不使用按名称固定的参数表。

端点检查逐通道分别报告既有与新增端点，并保留每个 ROI 的局部计数。暗部仍执行原 0.01% 严格门；新增高光按当前会话策略检查，历史策略仍拒绝任意新增高光。已有饱和只披露，不宣称恢复。Asinh 与 RGB blend 不保证色相、星径或核心层次完全不变。

每步保留实际试跑的原生科学图及同显示 JPEG、选中前缀、固定 ROI 对照与全部脚本日志；显式搜索模式的代表候选见兼容附录。正式审查绑定参数报告、最终选中预览、固定局部与适用的 full 对照；之前各步与代表图通过参数报告完整绑定，保持原材料数量上限。实际检查亮核、外壳/暗带、背景纹理、残星、光晕和颜色；生成成功与数值通过都不自动接受。

双窄带的 RGB、Ha 和 OIII 独立声明预算与弱结构要求。OIII 不必比 Ha 背景更暗，也不因较弱而自动加强降噪；继续固定曲线的降噪前后对照。HOO 配方与 RGB 星层不变，正式输出重新测量每条 v2 链及最终 HOO 端点。

<a id="stretch"></a>
## stretch


<a id="stretch-适用条件"></a>
### 适用条件

linear 父源变为非线性显示图时必需。nonlinear 和 unknown 不使用该协议。若没有可接受 stretch，
停止而不是把线性图伪装成成片。

<a id="stretch-参数"></a>
<a id="stretch-方法选择"></a>

旧默认参数和方法表见历史说明；新执行按上方形态绑定策略提出显式因子，不从旧配方选择默认值。 见[对应材料](../stage7-legacy.md#stretch-参数)。

<a id="stretch-ai-morphology"></a>
#### AI 观察与当前域约束

Stage 1 的 [形态观察](../target-morphology-review.md#ai-morphology-and-scientific-measurement) 提议亮核、微弱外缘、恒星与过渡区的检查重点，不预先决定本阶段数值。Agent 在当前已接受线性科学父源上确认可信 ROI，选择已有算子，在现有 request 的 `purpose/constraints` 说明收益及保护目标；数值依据当前科学测量与实际观察，沿用原求参与审查规则，不由名称或类型套用固定背景目标。

裁切、降噪或换父源后重新核对 ROI 坐标、来源哈希及实际像素域，链内后续步骤使用其真实输入域，不能直接沿用 Stage 1 或链首亮度统计。没有可信天空时不把目标改标成 background；无法满足原请求前置或收益不明确时保留父源。Agent 可在现有协议内根据证据显式选择路线、修订提案并完成采用审查；Python 不根据形态标签自动选择 HDR、增加算子、放宽约束或接受候选。

<a id="stretch-ghs-选择依据"></a>
#### GHS 选择依据

SP identifies the strongest local stretch; it is not a fixed point satisfying `output(SP)=SP`. HP begins a linear highlight-protection segment, not an identity segment, and does not have to approach 1. Select LP/SP/HP from each actual input domain and inspect the resulting curve and ROI changes. Neither setting SP on a subject nor setting HP on a core percentile guarantees a specific brightness change; do not derive fixed anchors or strengths from those interpretations. See the pinned [GHS command semantics](../siril-manual/source/doc/commands/ght.rst)。

| 参数 | 作用与选择依据 |
|---|---|
| D | CLI 参数采用对数尺度，内部强度为 `expm1(D)`；Siril 1.4.4 原生命令要求 `0<D<=10`，恒等需求跳过操作。根据当前主体区间提议并有限递进，以实际结构收益决定强度，不因颗粒噪声单独压低 D，也不按线性强度理解 D 的增量。 |
| B | 控制 SP 附近拉伸形状；当前 v4 因子提案遵守锁定 CLI 的 0–15 范围，不能把手册中的更宽范围当作当前执行权限。结合实际直方图与目标区域说明理由，不按目标名称固定，也不以较大 B 代表较强收益。 |
| SP | 最大拉伸作用的对称点；选择希望增加对比的信号区间。全图 median 可作线索，但不是背景统计，也不是必选 SP。 |
| LP | LP 以下采用线性段保护暗部细节；检查固定背景与微弱外缘，不能以黑位裁剪换取更暗背景。 |
| HP | HP 以上采用线性段保护高光；检查亮核和星径。保护设置不保证不裁剪，也不能恢复源中已丢失层次。 |

参数满足 `0<=LP<=SP<=HP<=1`。每一步以它实际接收的像素域和已有证据选择参数，后续步骤不能
机械沿用第一步统计。完整有序链在 SSF 中显式冻结，由 receipt 记录；不在合星时重新计算 SP，
Agent 选择算子、顺序及因子，Python 根据已绑定证据计算锚点并验证；显式旧请求才确定性搜索，不能自行选择方法、增加步骤或接受候选。

<a id="stretch-mtf-是-ghs-族的-b1-特例已实测"></a>
<a id="mtf-是-ghs-族的-b1-特例已实测"></a>
#### GHS 的 D 尺度与特定配置下的 MTF 等价

用 `d` 表示传给 `ght -D=` 的参数，用 `k` 表示内部强度。Siril 1.4.4 的
[命令解析源码](https://gitlab.com/free-astro/siril/-/blob/1.4.4/src/core/command.c#L2306)
执行 `params->D = (float) expm1(D)`，因此 `k=expm1(d)=exp(d)-1`。请求 factors.D 和 SSF 的
`-D=` 保留 d 本身，Python 不再做指数换算，否则会重复转换。

当 `B=1, LP=0, SP=0, HP=1` 时，标量曲线为 `T(u)=(1+k)*u/(1+k*u)`，与黑点 0、白点 1 的
MTF 在相同通道处理方式下等价。其 MID 换算为

```
m = 1 / (exp(d) + 1)
```

反向关系为 `d=log((1-m)/m)`，仅用于上述配置的尺度理解。下表是换算示例，不是起始配方：

| CLI d | 内部 k=expm1(d) | 等价 MTF m |
|---|---|---|
| 1 | 1.718282 | 0.268941 |
| 1.5 | 3.481689 | 0.182426 |
| 3 | 19.085537 | 0.047426 |

`k+1=exp(d)`，所以 d 每增加 1，k+1 乘以 e；图像亮度、局部反差及逐像素变化不按固定倍数增长，
还取决于输入域、B 和 LP/SP/HP。MID 的绝对变化缩小也不能解释为拉伸增量变弱。理论极限 d=0
对应 m=0.5，但 1.4.4 CLI 拒绝 `ght -D=0`，恒等需求应跳过操作。

1. **等价条件必须完整。** 改变 LP、SP、HP、B 或黑白点后，不再保证上述等价关系；使用真实 ROI
   锚点的 GHS 链不能按这张表直接替换成 MTF。
2. **标量等价不代表 RGB 输出相同。** 单色或相同的逐通道处理可比较该曲线；本协议的 RGB GHS
   使用 `-human -clipmode=rgbblend`，与逐通道 MTF 不同，单色梯度不能证明两者的保色效果相同。
3. **公式推导与原生验证分开。** 原生 float32 梯度回归以最大绝对误差 `1e-6` 检查上述等价配置，
   并验证分段参数与 RGB 亮度模式的反例；float64 公式自检不能宣称为 Siril 原生输出精度。

Agent 从实际前缀域提议 d、B 和保护锚点，修订时记录旧→新参数与结构依据；不把 d 当线性强度倍数，
也不因这一特例默认缩短多节链。每步单提案、含换链与对照的三次总额度及实际像素审查保持有效。

<a id="stretch-ssf-知识关系"></a>
<a id="stretch-参数化-ssf-骨架"></a>

本文件仍是唯一 primary；新执行使用绑定 strategy 的生成结果。HDR/HOO 同时绑定实际变体附文为 supporting；旧裸 SSF 示例只解释历史语法，不授权新执行。 见[对应材料](../stage7-legacy.md#stretch-ssf-知识关系)。

<a id="explicit-native-transfer-chains-020"></a>
<a id="stretch-explicit-native-transfer-chains-020"></a>
### 显式原生传递链

有 starless 分支时禁止自动推导的 autostretch
链，必须显式记录 MTF/Asinh/GHS 的参数与顺序；GHS 五个数值参数全部给出。Asinh/GHS 对全部
RGB 通道使用 `-human -clipmode=rgbblend`，禁止独立通道、饱和度或指定单通道拉伸。Stage 9
将同一冻结链分别作用于分星时的原始 full/starless，再计算显示域星残差；不直接拉伸线性星层。

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-linear-parent.fit"
mtf 0 0.01 1
asinh -human 2 0 -clipmode=rgbblend
ght -D=0.2 -B=0 -LP=0 -SP=0.2 -HP=1 -human -clipmode=rgbblend
icc_assign sRGB
save "/abs/session/artifacts/070-explicit-stretch" -chksum
stat main
savejpg "/abs/session/previews/070-explicit-stretch" 95
close
```

这些数值只展示语法，不是所有图像共用的默认值。查询冻结 `mtf/asinh/ght` 手册，记录证据与实际参数理由。合星时将同一链分别作用于原始 full/starless 来源。

完成拉伸后、保存科学产物与直接预览前，使用原生 `icc_assign sRGB` 标记本协议的非线性显示约定。
这只是 profile assignment，不做 Gamma 转换；不要让加载时的 ICC 自动猜测重新解释已拉伸数据。

<a id="masked-dual-hdr"></a>
<a id="stretch-masked-dual-hdr"></a>

Agent 可按五类实际形态在首次提案选择 HDR，无需先证明单链冲突；读取完整变体合同并绑定 supporting。 见[对应材料](../stage7-hdr.md#masked-dual-hdr)。

<a id="stretch-审查与回退"></a>
### 审查与回退

检查黑位、亮核、微弱结构、噪声、星色与通道裁剪，并按
[主体显著性审查](../quality.md#主体显著性与收尾收益) 证明直接显示下主体实际可辨。
在 notes 记录固定目标—天空区域的中位差、代表细节、核层次及噪声代价；整体变亮不等于结构收益。
星云被压平、核溢出、背景截断或出现伪结构、明显色块时 reject；材料或细节无法确认时写 uncertain，两者均不采用候选。仅颗粒噪声显露且主体仍可可靠辨认时，记录并暂不处理；不得把后续降噪当作已执行的收益。按上方有限递进规则，以新编号从同一原线性父源重跑完整链，不把已拉伸候选再送入本 linear-only 协议。停止时保留已评估的最佳合格候选；不得靠增加链长度、压黑背景或增强饱和度掩盖问题。

已接受的 nonlinear 图若还需亮度整形，使用 [tone.adjust](../shared-protocols/tone-adjust.md#tone-adjust)，不重新进入本 linear-only
协议。合星前该操作只修改主体 A′，不把它追加到本协议冻结的 F 或替换显示域星残差。

<a id="recomposition-aware-display-review"></a>
### 合星前的显示审查

依据主体可辨性及实测代价选择显式传递，不能为压低负能量比例而选曲线。Stage 9 保留带符号残差并验证最终算术，比例告警须实际审查目标和星点。全图对比度较低不能单独证明小目标不可辨；不以通用天空中位数替代检查。


HDR 后的 Stage 8 谱系限制随变体合同读取。 见[对应材料](../stage7-hdr.md#masked-dual-hdr)。

<a id="rgb-tail-comparison"></a>
<a id="新保色策略的-rgb-末段比较"></a>
## RGB MTF 收尾的末段比较

Before another trial, inspect the read-only recovery summary and verify reusable parameter reports with `status --parameters`. Reuse existing exact-prefix reports/panels where bindings match; do not rerun them just to reconstruct a narrative. Changed prefixes, sources, ROI or final constraints invalidate reuse. Scientific candidates still replay from the accepted linear parent; status and diagnostic prefix images do not bypass full-chain verification or the shared trial budget.

New sessions freeze `stretch_tail_comparison=verified_negative_control_v1`. `compare-tail` writes comparison v2 and still requires the same linear parent, exact prefix operators/parameters and actual prefix pixels, ROI, runtime and final constraints. Existing v1 reports retain their original requirement for two solved candidates.

At least one v2 candidate must be `solved`. The other may be `ineligible` only after its entire prefix passed and its single final native trial completed with retained FITS/JPEG, native logs and verified archive. Its sole rejection reason must be `regional_constraints`; independently remeasured shared `protection`, finite pixels, geometry, endpoints and target direction must pass under the frozen session policy. Native errors, timeouts, incomplete prefixes and changed evidence are rejected. Full-stars replay retains its original diagnostic role.

An ineligible row records the original audit/reason, shared protection audit, `diagnostic` FITS/JPEG and `requires_failure_review=true`, with no `selected` field. Panels and visibility controls label it as not meeting the goals. Such a control cannot establish a structural improvement, win selection or become a scientific parent; only a solved candidate may authorize formal generation, followed by actual visual review. `recommended_run_id` remains null. A failed control is evidence of failure, not a matched-brightness color winner.

Registered historical sessions may append an exclusive derived comparison JSON/PNG from verified existing trials without running Siril, claiming another attempt, resetting the ledger or rewriting any frozen evidence. This diagnostic exception does not permit new historical scientific processing. A C50 recovery can compare the existing `072-stretch-profile` and `072-stretch-tail` under a new `073-tail-recovery` report ID; existing IDs are never overwritten.

多节 RGB 链以 MTF 收尾时，额外提议一份同线性父源、同前缀算子／因子／锚点、同 ROI 和最终约束的候选，只把末段改为显式 `ght -human -clipmode=rgbblend`，不扫描参数。两份完整提案分别从同一线性父源求参，保留原生前缀重放。单色及 HOO 单通道拉伸不强制比较。

```bash
python3 scripts/deep_sky_siril_stretch.py compare-tail --session /abs/session \
  --mtf-parameters reports/071-mtf/stretch-parameters.json \
  --luminance-parameters reports/072-ghs/stretch-parameters.json --run-id 073-tail
python3 scripts/deep_sky_siril_stretch.py generate --session /abs/session \
  --parameters reports/072-ghs/stretch-parameters.json --comparison reports/073-tail/comparison.json
```

比较报告核对实际前缀像素与绑定一致，按相同 [0,1] 显示保存末段前／MTF 后／GHS 后对照，并逐 ROI 测量饱和度、圆周色相、亮度分位数、亮核跨度及通道端点。不自动选胜者；在共同最终亮度约束下审查颜色保留、亮核层次及弱结构连续性，更亮或更饱和不足以胜出。正式候选从原线性父源重跑完整链；停止、有观察依据的修订及拒绝规则不变。

两条链各自启动的 solve 分别计入 [Stage 7 三次总额度](#v4-attempt-accounting)，不是一轮免费配套比较；`compare-tail` 只读取已有科学图并生成比较材料，不另计科学试跑。启动 MTF 收尾前预留缺少的同前缀 GHS 对照额度。已有父源、ROI、前缀算子/因子/锚点及最终约束完全匹配且绑定有效的报告可复用，不重复求参；改动前缀或最终约束后不能复用旧对照。没有匹配对照且只剩一次额度时，不再启动需要两条新链的 MTF 方案；保留可信恢复点，或有依据地选择不要求该比较的 GHS 收尾新提案，不能省略必要比较后采用 MTF。

按 [Stage 1 配色路线](stage-01-input-inspect.md#palette-route-selection)保护真实通道角色、弱信号颜色、各通道裁剪与亮核层次。SHO/modified-SHO 的主体显示映射留给 Stage 8；不因 natural/vivid 风格改变谱线来源，也不把独立非线性拉伸后的颜色称为物理线比。只有 Ha/OIII 时沿用显式选择的 HOO，不能标为 SHO。

<a id="dualband-hoo-stretch"></a>

仅用户显式选择且初始化开启 HOO 后读取；完整来源、三链及 OIII 降噪合同需绑定 supporting。 见[对应材料](../stage7-hoo.md#dualband-hoo-stretch)。

<a id="stretch-compatibility-appendix"></a>
<a id="stretch-单曲线自适应参数层"></a>
<a id="单曲线自适应参数层"></a>
<a id="profile-guided-chains-v23"></a>
<a id="按形态评估高阶链路-v23"></a>
<a id="请求生成与有限试跑"></a>

历史策略、旧搜索及兼容示例仅用于已有证据回放或明确历史查询，不授权新科学执行。 见[对应材料](../stage7-legacy.md#stretch-compatibility-appendix)。


### 旧标题入口

<a id="hdr-最终混合保护"></a>
<a id="ssf-示例"></a>
<a id="stage-8-的-hdr-谱系互斥"></a>
<a id="参数与算子"></a>
<a id="双传递蒙版-hdr"></a>
<a id="适用条件与来源"></a>
<a id="验证审查与回退"></a>

这些旧标题对应[HDR](../stage7-hdr.md)。

<a id="双窄带-hoo-主体与原-rgb-星色"></a>

这些旧标题对应[HOO](../stage7-hoo.md)。

<a id="对照判读示例"></a>
<a id="带框主体上下文选区后冻结前查看"></a>
<a id="紧凑记录模板"></a>
<a id="邻近结构成对测量真实首步前缀与最终候选"></a>

这些旧标题对应[诊断示例](../stage7-diagnostics.md)。

<a id="baseline-预算与冲突复核"></a>
<a id="ssf-知识关系"></a>
<a id="单曲线自适应参数层显式搜索兼容"></a>
<a id="参数"></a>
<a id="参数化-ssf-骨架"></a>
<a id="可选参照反求-baseline-与独立单链比较"></a>
<a id="方法选择"></a>
<a id="显式形态比较与搜索兼容"></a>
<a id="比较采用与回放"></a>
<a id="请求生成与有限试跑v23-显式搜索兼容"></a>
<a id="附录历史策略与显式兼容入口"></a>

这些旧标题对应[历史与兼容](../stage7-legacy.md)。

