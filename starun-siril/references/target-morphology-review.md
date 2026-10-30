# Target Morphology Review Reference

Stage 7 new sessions use [declared morphology](stages/stage-07-stretch.md#declared-morphology): independent parent/ROI context precedes profile selection, and structured primary/companion observations cover all solving ROIs. The program checks bindings and declared processing-goal/profile support, not whether image recognition is correct. Stage 1 retains its existing notes-only observation contract below; no image classifier, catalogue mapping, confidence score or spectral inference is added.

用于选择处理步骤、解释参数理由及审查候选时，按当前目标的保护对象选择检查区域。本页补充
[质量协议](quality.md) 的观察重点；分星触发遵循 [stars.separate](stages/stage-06-stars-separation.md#stars-separate-执行触发agent-决策)，
不提供固定配方、不改变 Stage 顺序或增加质量门。

## 适用依据与检查区域

- 已知目标类型来自用户或可靠目标资料；像素中可见的盘面、亮核、外晕等形态可用于选择检查区域，
  但不能推断线性状态、通道角色、滤镜或光度真实性。目标类型不确定时描述可见形态，不改写冻结的
  session.context.target_type；输入状态或通道模式仍为 unknown 时只完成 input.inspect。
- 按实际画面选少量代表区域：亮核/密集区、微弱外缘、目标与背景过渡、代表星区及有证据的空白
  背景。并非每张图都有空白背景；目标铺满画面时不强行设背景样点。
- 父源与候选在相同显示传递、缩放及对应位置比较；几何改变时先核对坐标对应。整图预览检查大尺度
  结构，局部放大检查细节与伪影，不以单一局部改善接受整体退化。
- 将实际检查材料、区域位置和观察写入现有 review notes。没有打开的区域或无法辨认的细节不能写为
  已通过；全图 median/std 不是背景统计，也不能证明微弱结构保留。

<a id="ai-morphology-and-scientific-measurement"></a>
## AI 形态判断与科学测量

Stage 1 按 [观察模板](stages/stage-01-input-inspect.md#input-inspect-ai-morphology) 在现有 review.notes 中记录可辨观察、疑似判断、无法判断及后续检查方向，不填写未经标定的置信百分比。实际材料沿用 SHA-256 与 receipt 绑定，notes 上限为 2000 字符；不新增形态 schema 或机器解析。验证器只检查现有绑定与审查合同，不验证 AI 识别正确性，也不自动填写 accept。

形态判断是当前显示下的观察，不是目录身份、真实谱线或科学参数证明。保持 target_name/target_type 冻结；声明冲突记录影响，不静默覆盖。不得补齐线性、滤镜、通道角色、谱线来源、纯天空或 PSF。形态不确定不新增失败门，必要审查不可完成按原规则待审；unknown 输入门不变。

| 阶段 | 观察如何进入现有接口 |
|---|---|
| [Stage 6 分星](stages/stage-06-stars-separation.md#stars-separate-ai-morphology) | 根据当前可见主体和用户目标应用原触发规则，合并保护内嵌星团、紧致亮结及微弱结构；不按名称改变模型、stride 或预拉伸参数。 |
| [Stage 7 拉伸](stages/stage-07-stretch.md#agent-led-enhancement) | 按实际形态提议完整多节链，以 Stage 6 autostretch 为可见性参照并用真实首步前缀检查后续收益；亮核与弱外缘跨度大时优先参考 Asinh → GHS，不按天体名称固定。单候选不是单个算子，达到目标即停止后续修订；baseline 仅为可选的首步 MTF 初值。subject_contrast 仅为参考推荐。Agent 分别确认外缘、细丝、暗带和亮核，区分求参保护 ROI 与额外成对诊断 ROI；先检查阻塞约束，再调整当前域锚点、因子及显示区间。复用现有统计，在 Agent 侧计算方向明确且口径固定的局部差值，在既有 `purpose/constraints` 与 reason/review.notes 中关联证据，并据此说明下一轮调整或停止。因子修订保持当前算子与链长；实测证明现链不适用时允许新提案换链，从同一线性父源重跑。换链与独立对照共同计入 [三次总额度](stages/stage-07-stretch.md#v4-attempt-accounting)，同源基线/高阶候选使用可比目标；首次短链与后续路线修订按[例外证据规则](stages/stage-07-stretch.md#stretch-routing-v29)分别绑定依据；新计划继承首次分类，不能重置次数。由连续结构、颜色与亮核的实际收益决定采用，形态表和代码推荐分只作参考；细节与记录模板见本阶段指南。 |
| [Stage 9 回混](stages/stage-09-stars-recompose.md#stars-recompose-ai-morphology) | 使用已接受分支和显式 strength，在该阶段 review notes 记录选择理由与实际效果；类型基准与统计仅供候选参考，权重不表示星径缩小比例。 |

初始观察只提供检查方向。裁切、降噪或换父源后，ROI 必须重新核对坐标、来源哈希与实际像素域，不能直接沿用旧亮度统计。参数及适用性理由仍按原 SSF/provenance 合同记录，实际报告与候选必须重新审查；无明确收益、出现退化或证据不足时保留父源。

该流程只增强 Agent 引导的观察与决策。Python 不读取 notes 推断分支，不自动消费 AI 判断；Agent 沿用已有参数接口并完成实际审查。

## 按形态审查

| 目标形态 | 保护对象与检查区域 | 处理选择与拒绝依据 |
|---|---|---|
| 侧视星系 | 尘埃带、薄盘边缘、核部到盘面的连续亮度过渡及微弱外晕 | 拉伸与降噪后检查尘埃带是否变软或成为硬黑线；局部对比不能把盘面压成均匀亮条。没有明确改善时跳过增强，新增硬边、振铃或外晕丢失时拒绝候选。 |
| 正面旋涡星系 | 核部、旋臂尘埃、紧致亮结及外臂；有可靠来源时特别检查 HII 区/OB 星协 | 分星前后对照这些位置，确认目标亮结没有被移入星层或在 starless 中形成孔洞。泄漏是待验证风险，不能仅因目标是星系就认定 StarNet 失败；实际泄漏则拒绝分星并保留含星路径。 |
| 星系群/星系团 | 代表性的明亮与微弱成员、小型紧致目标、外晕及已有证据支持的潮汐结构 | 比较相同位置的成员轮廓和低表面亮度结构，检查分星是否误移小成员、降噪是否抹掉外缘。不能为了更黑背景或更低噪声接受成员消失，也不凭颜色把紧致目标判作恒星。 |
| 恒星团与密集星场 | 明亮星核、密集中心的可辨分离、星色差异及稀疏边缘星 | 恒星本身是主体，默认优先保留含星路径；只有明确收益或用户目标才分星。检查降噪/反卷积是否融合或删除弱星，拉伸是否扩大星径、裁剪星核；不得默认使用 starless 局部增强。 |
| 发射星云与亮核星云 | 亮核层次、细丝、暗尘埃、微弱发射外缘及其背景过渡 | 背景扣除前确认样点不落在微弱目标；拉伸时同时检查核部与外缘。降噪不能抹掉细丝，局部增强不能制造硬轮廓；窄带色彩按有来源的通道与 palette 审查，不把绿色一概当偏色。 |
| 反射星云、暗星云及弥散尘埃/IFN | 微弱尘埃纹理、暗带、宽尺度亮度变化及亮星附近结构 | 先区分可能的真实结构与梯度，单凭预览不能确诊 IFN。完整处理须先完成 Stage 3 控制区提议、局部审查及允许的一次修订，仍无法建立可信背景区域时保留父源；不得以全图更平滑、更中性或更暗证明改善，尘埃被误减或纹理消失时拒绝。 |

完整处理中的星系与云气/尘埃主体星云，在分星前置满足时默认执行候选，实际审查后决定采用。
多种形态同框时按主体处理需求选择分支并合并保护对象；星云内嵌星团不套用纯星团的默认含星
选择，星团中心及周边云气须共同检查。不为了主目标效果忽略伴随目标。无法确定形态时仍检查亮核、微弱
外缘、星区和背景过渡，不机械套用任一行的处理选择。

## 亮核、蒙版与分星的检查边界

1. 拉伸前后分别检查科学像素的裁剪证据及亮核真实层次。显示预览纯白不单独证明源已裁剪；科学
   数据已丢失的层次不能靠后续对比或锐化恢复。新增裁剪或均匀色块时回到已接受父源，按
   [stretch](stages/stage-07-stretch.md#stretch) 选择可修正候选。
2. 对 [structure.local-contrast](stages/stage-08-starless-enhance.md#structure-local-contrast) 实际生成的蒙版检查暗部与高光
   淡入/淡出、目标边缘的平滑过渡及微弱结构覆盖；不能只审查增强图。无法可靠查看蒙版时不宣称
   保护生效，不另加 gamma/HDR 配方绕过当前协议。没有可信 starless 分支时跳过该协议。
3. 按 [stars.separate](stages/stage-06-stars-separation.md#stars-separate) 检查同一分支的 full/starless/star layer；保留目标
   结构比得到更干净的 starless 更优先。合星按 [stars.recompose](stages/stage-09-stars-recompose.md#stars-recompose) 复核
   星径、星色和目标纹理，数值闭合不能代替形态审查。具体伪影验证见 [排查参考](troubleshooting.md)。

本页实际用于 SSF 适用性或参数选择时，作为 supporting reference 写入既有 provenance；具体协议
仍是唯一 primary reference。观察不支持改善时拒绝候选，证据不充分时记录 uncertain，沿用现有
质量与回退合同，不靠新增参数表或自动评分决定接受。

形态参考可以同时描述亮核、外晕、尘埃或星团等混合目标；选择主要拉伸目的并记录次要保护对象。Stage 7 默认参考 [五类多节起始链](stages/stage-07-stretch.md#morphology-starting-chains)，由当前科学 ROI 提议因子并对照 autostretch 和真实首步前缀；不强制独立单链比较。配方不提供固定强度，也不能替代实际区域证据或作为低质量数据必须完成三级拉伸的理由。
