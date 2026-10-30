# Default Stage Sequence

本页只规定完整后期处理的顺序、前置依赖、跳过与停止条件；执行前读取对应正文。
Agent 根据当前图像证据选择协议、参数与采用结果，Python 只在显式声明后生成 SSF。
定向任务按[入口定向模式](../SKILL.md#定向模式)读取必要前置；多个 master 按[串行规则](../SKILL.md#多文件串行处理)分别完成本页顺序。

## 顺序规则

- 从 Stage 1 开始，依次评估 Stage 1 → 10。unknown 停在输入门；nonlinear 不强行进入线性背景、校色或恢复处理。
- 每份 SSF 只实现一个协议，逐份绑定 provenance、运行、打开真实产物并写 review；只有被接受的科学产物可作下一父源。诊断、失败、拒绝及未提交候选不进入后续谱系。
- 背景空间模型 → 独立背景中性化 → 线性降噪／RL，各自从当前已接受父源评估；前一步跳过或拒绝不自动否决下一步。ROI 随实际父源重新绑定，像素变化后重测；备用路线仅在记录的触发条件出现后准备。
- 每次审查后执行下一授权步骤，或保存绑定 receipt/review 的 stage decision 和具体停止原因。恢复时读取 [status](cli-contract.md#status)确认父源及额度，默认阶段只作评估提示；坐标与精确前缀的复用按[恢复合同](session-contract.md#恢复与继续处理)。
- 完整交付前分别评估 tone.adjust 和 color.finish，在最终 delivery review notes 记录采用或跳过依据；无剩余收益可跳过，不要求合星前后重复执行。

## Stage 1–10 映射

协议箭头表示评估顺序，适用性及科学硬门由正文维护，不构成固定配方。

| Stage | 前置依赖与处置 | 阶段专属导引 (Stage Dossier) | 本 Skill 的协议顺序 |
|---|---|---|---|
| 1 · 输入准备 | 只读已堆栈 master，冻结来源与上下文；检查输入门和显式配色意图 | [Stage 1](stages/stage-01-input-inspect.md) | `init` → [`input.inspect`](stages/stage-01-input-inspect.md#input-inspect) |
| 2 · 边界校正 | 仅实测近黑边、堆栈边或旋转覆盖时保守裁剪；不在此解算 | [Stage 2](stages/stage-02-geometry.md) | [`geometry.crop-near-black`](stages/stage-02-geometry.md#geometry-crop-near-black) |
| 3 · 背景处理 | 每份线性输入必做实测评估；有充分证据才跳过候选，否则条件合格须试跑 | [Stage 3](stages/stage-03-background.md) | [`background.subtract`](stages/stage-03-background.md#background-subtract) |
| 4 · 图像解析与色彩校准 | 先定路线；测光须先接受解算，再冻结校色直接父源；双窄带默认先评估 neutral，方法不叠加 | [Stage 4](stages/stage-04-astrometry-color.md#stage4-preflight) | [`color.calibrate`](stages/stage-04-astrometry-color.md#color-calibrate)；测光前先 [`astrometry.solve`](stages/stage-04-astrometry-color.md#astrometry-solve) |
| 5 · 线性降噪与反卷积 | 分别评估降噪与 RL；采用降噪后重新检测 PSF，再决定反卷积 | [Stage 5](stages/stage-05-linear-restoration.md) | [`restoration.denoise`](stages/stage-05-linear-restoration.md#restoration-denoise) → [`restoration.deconvolve`](stages/stage-05-linear-restoration.md#restoration-deconvolve) |
| 6 · 线性分星 | 星系和云气／尘埃主体星云在前置满足时默认试跑分星；纯星团优先含星。审查并冻结 full/starless 源对 | [Stage 6](stages/stage-06-stars-separation.md) | [`stars.separate`](stages/stage-06-stars-separation.md#stars-separate) |
| 7 · 主体拉伸 | 同一可信线性父源，优先 starless，否则含星保护；按形态选择至少两步主体链，默认逐步反馈；HOO 专用整链，共享三次总额度 | [Stage 7](stages/stage-07-stretch.md) | [`stretch`](stages/stage-07-stretch.md#stretch) |
| 8 · 无星主体增强 | 已接受的 nonlinear starless 父源；依次评估局部对比、已选 palette、剩余亮度目标、色度降噪与增色 | [Stage 8](stages/stage-08-starless-enhance.md) | [`structure.local-contrast`](stages/stage-08-starless-enhance.md#structure-local-contrast) → [`color.map`](stages/stage-08-starless-enhance.md#color-map) → [`tone.adjust`](shared-protocols/tone-adjust.md#tone-adjust) → [`restoration.denoise-nonlinear`](shared-protocols/denoise-nonlinear.md#restoration-denoise-nonlinear) → [`color.finish`](shared-protocols/color-finish.md#color-finish) |
| 9 · 合星 | 当前已接受 nonlinear starless 主体、Stage 6 同源 full/starless 对及冻结星点传递；贡献与轮廓分别按实图需求提案；轮廓候选生成同贡献对照后审查 | [Stage 9](stages/stage-09-stars-recompose.md) | [`stars.recompose`](stages/stage-09-stars-recompose.md#stars-recompose) |
| 10 · 最终整理与导出 | 最终含星图，或初始化时已明确请求的无星非线性终图；可选整理后独立生成交付候选 | [Stage 10](stages/stage-10-delivery-finishing.md) | [`restoration.denoise-nonlinear`](shared-protocols/denoise-nonlinear.md#restoration-denoise-nonlinear) → [`tone.adjust`](shared-protocols/tone-adjust.md#tone-adjust) → [`color.finish`](shared-protocols/color-finish.md#color-finish) → [`delivery.render`](stages/stage-10-delivery-finishing.md#delivery-render) |

## 采用、跳过与待审

Stage 3 保存 `reports/stage-03-assessment.md`，完成[必做评估](stages/stage-03-background.md#stage3-required-assessment)。
只有[实测证据充分](stages/stage-03-background.md#measured-evidence-skip)才说明无需候选；否则条件合格须试跑。
天空不足时完成允许的一次人工修订，仍不足则记录具体原因；无收益、拒绝或不确定保留父源继续。
来源或证据错误阻断依赖处理，不能用文字决定替代实测。

Stage 4 主动取证，可解算时先解算；可信天空下非恒等 neutral 须实际试跑，不以“已近中性”跳过。
Stage 3 控制区不足不代替 Stage 4 的独立天空评估。Stage 5 线性降噪须有噪声收益且保护纹理保留，
无收益、纹理损失或无法判断时保留父源；RL 无实质退化可采用，按[各自验收规则](stages/stage-05-linear-restoration.md)记录依据。

Stage 7 普通 RGB、单色及 HDR 默认使用[分步前缀反馈](stage7-stepwise.md)，查看真实前缀后再决定下一步参数；显式 --batch 才整链，HOO 保留专用整链流程。普通主体候选实际执行至少两步后才可完成。同一分步候选逐步推进共占一次，修订、对照与原生失败重试另计。Stage 7 达到主体充分显露且高光、过渡、颜色自然时停止；修订、换链和独立对照共享[三次总额度](stages/stage-07-stretch.md#v4-attempt-accounting)。
首次短链、Agent 按实际形态选择的首次双分支 HDR、后续修订与停止的完整条件见 [Stage 7](stages/stage-07-stretch.md)；HDR 无需先证明单链冲突，不预备无依据的回退。
Stage 8 无资格或无合格候选保留父源，已采用的局部增强不叠加。
Stage 9 无收益、退化或无法判断保留可信恢复点；普通含星会话合星不合格不改为无星成功。
报告与告警不自动采用候选，来源、像素完整性及科学硬门仍须通过。

## Preservation checkpoints

保护与对照按 [Stage 3 颜色保护](stages/stage-03-background.md)、[Stage 4 父源重绑](stages/stage-04-astrometry-color.md)、
[Stage 7 末段比较](stages/stage-07-stretch.md#rgb-tail-comparison)、[Stage 8 保护规则](stages/stage-08-starless-enhance.md)及
[Stage 9 星点曲线对照](stages/stage-09-stars-recompose.md#stellar-curve-selection)执行。采用必须有实际 ROI 收益及保护证据，数值目标达成只作诊断。

## 能力边界

只接收已校准、配准并堆栈的 master，不处理 Light 序列预处理、去拜耳或堆栈。
工具缺失按[工具边界](../SKILL.md#合同)报告来源和影响，不下载或安装。
星点最终保留不妨碍中间分星／合星，内嵌星团保持保护；测光缺失或拒绝不阻断其他条件合格的线性处理。

## 与 SSF provenance 的关系

已知协议直接读表中正文；不确定时才用[协议索引](protocol-index.md)定位。
按运行时映射绑定唯一 primary、必要 supporting 与完整文件 SHA-256，不使用锚点作指纹。
只有实际用于排序时本页才作 supporting，定向任务未使用则不虚构绑定。
手册语法与 command policy 授权沿用[文档指导合同](session-contract.md#文档指导版本)。

## 按需细节

<a id="multiscale-local-contrast-v21"></a>

- 局部增强方法及 HDR 排除条件：[Stage 8 多尺度择优](stages/stage-08-starless-enhance.md#multiscale-competition)。

<a id="合星显式提案与审查"></a>

- 合星参数与来源：[显式提案](stages/stage-09-stars-recompose.md#agent-recomposition-proposal)、[已接受主体](stages/stage-09-stars-recompose.md#explicit-recomposition-sources)；诊断见[固定星表](stages/stage-09-stars-recompose.md#fixed-catalog-geometry)。

<a id="已定调后的-tone-评估"></a>

- 已定调后先检查[剩余亮度目标](shared-protocols/tone-adjust.md#remaining-goal-tone)，满足即跳过；合星前后作用域见正文。

<a id="增色前色度降噪"></a>

- 主要亮度整形后、增色前按证据评估[仅色度降噪](shared-protocols/denoise-nonlinear.md#pre-color-chroma-denoise-chroma_bilateral_v1)。
- 配色选择与 HOO 前置：[Stage 1 配色路线](stages/stage-01-input-inspect.md#palette-route-selection)、[Stage 7 HOO](stage7-hoo.md)；Stage 8 不重复已完成的映射，未显式选择或无可靠通道角色时跳过 palette。
- 调色与保护区域：[color.finish](shared-protocols/color-finish.md#color-finish)。主体已采用同目的增色时不重复执行，仍评估最终颜色。

## 历史兼容

仅恢复或查询历史时读取 [session 兼容合同](session-contract.md#文档指导版本)及[旧版本说明](session-legacy.md#鲜艳调色-v19)。
旧 primary/supporting 路径、角色和指纹按原冻结证据验证，不替换为共用文件；精简文档不改变原执行资格。
