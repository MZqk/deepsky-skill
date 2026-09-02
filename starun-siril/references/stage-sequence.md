# Default Stage Sequence

本页规定完整后期处理任务的默认宏观顺序。它采用用户提供的 Stage 1–10 处理域作为设计基座，但不移植
原实现中的质量门、候选竞争、回退策略或产物命名。每个实际处理动作仍由 Agent 根据当前图像证据选择
协议、参数化 SSF，并在运行后完成视觉审查；Python 不生成流程或 SSF。

## 顺序规则

- 完整处理从 Stage 1 开始，按 Stage 1 → 10 评估；不适用的阶段或协议可以明确跳过，但不得把只适合
  后一图像域的操作提前。
- 每份 SSF 只实现一个协议。一个阶段含多个协议时，也必须逐份生成 SSF/provenance、运行、打开真实
  产物并写 review；只有被接受的产物才能成为下一份 SSF 的父源。
- 该顺序是默认编排，不是 Recipe。它不规定目标无关的固定参数、固定候选数量或必须执行的协议集合。
- 定向模式可以只执行用户指定的协议，但必须满足该协议的输入图像域和 lineage 前置条件，不得借定向
  模式把 nonlinear 产物送入 linear-only 协议。
- 若 Stage 1 后输入状态仍是 `unknown`，停止完整处理并请求可靠的用户、采集或文件内状态证据。由于
  session 上下文被冻结，获得证据后创建新 session；不得把诊断预览直接送往最终交付。

## Stage 1–10 映射

| Stage | 默认处理域 | 本 Skill 的协议顺序 |
|---|---|---|
| 1 · 输入准备 | 冻结已堆栈 master 的只读路径、大小、SHA 与上下文，建立 direct/autostretch 输入证据 | `init` → [`input.inspect`](protocols/input-inspect.md) |
| 2 · 边界校正 | 只在实际像素显示近黑边、堆栈边或旋转覆盖时做保守几何修正；不在此阶段 Plate Solve | [`geometry.crop-near-black`](protocols/geometry-crop-near-black.md) |
| 3 · 背景处理 | 在线性域判断大尺度梯度是否可与目标分离，再执行有样点来源绑定的背景扣除 | [`background.subtract`](protocols/background-subtract.md) |
| 4 · 图像解析与色彩校准 | 先核对通道语义与 WCS 证据，再选择 neutral、PCC 或 SPCC；无证据时跳过真实性校准 | [`color.calibrate`](protocols/color-calibrate.md) |
| 5 · 线性反卷积与降噪 | 两项都适用时先反卷积、后降噪；每项独立运行和审查 | [`restoration.deconvolve`](protocols/restoration-deconvolve.md) → [`restoration.denoise`](protocols/restoration-denoise.md) |
| 6 · 线性去星与星点层准备 | 需要 starless 分支且 StarNet2 与模型已安装时，在线性域冻结 full/starless/star layer | [`stars.separate`](protocols/stars-separate.md) |
| 7 · 主体拉伸 | 优先拉伸已接受的 starless 父源；没有可信分星结果时拉伸保留星点的线性父源 | [`stretch`](protocols/stretch.md) |
| 8 · Starless 增强 | 只对已接受的 starless 非线性父源做有来源的窄带 palette；没有可靠通道角色时跳过 | [`color.map`](protocols/color-map.md) |
| 9 · 星点处理与合成 | 使用 Stage 6 的配对星层和 Stage 7 记录的 matched MTF 回混星点 | [`stars.recompose`](protocols/stars-recompose.md) |
| 10 · 最终降噪与导出 | 对最终含星非线性父源做可选轻量调色，再以独立脚本生成交付候选 | [`color.finish`](protocols/color-finish.md) → [`delivery.render`](protocols/delivery-render.md) |

## 能力边界

- 本 Skill 只接收一个已经完成校准、配准和堆栈的 master。Stage 1 中 Light 序列预处理、去拜耳、配准和
  叠加不在当前合同内。
- 当前没有独立 Plate Solve 协议；Stage 4 只能消费已经存在且可验证的 WCS，不能把“准备执行 PCC/SPCC”
  当成成功解析。
- 当前没有专用的 starless 结构增强协议，也没有 nonlinear 最终降噪协议。Stage 8 与 Stage 10 的这些
  空位必须明确跳过，不能用 linear-only `restoration.denoise` 或未声明命令替代。
- 工具缺失只报告来源和影响；本 Skill 不下载或安装 StarNet、模型、Gaia 或插件。

## 与 SSF provenance 的关系

具体 protocol reference 始终是该 SSF 唯一的 `primary` reference。完整处理任务中，本页实际参与了阶段
排序时，可把 `references/stage-sequence.md` 作为 `supporting` reference 记录在 provenance；定向模式未使用
本页时不应虚构绑定。离线 Siril 手册仍只负责命令语法与参数语义，`command-policy.json` 仍只负责执行
授权。
