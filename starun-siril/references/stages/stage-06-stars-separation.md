# Stage 6 · 线性去星与星点层冻结

原生 StarNet 2.6.2 在 `--linear` 下接收高于 1 的有限浮点值，但会在 MTF 前裁剪并告警；峰值 ≤1 不是启动硬条件。保留原始 float32 交接并执行分星候选，不因少量越界提前跳过，不隐式缩放、裁剪或归一化。适配器记录越界样本数与峰值，原生日志绑定实际告警；采用前检查同位置的主体、星核与星层，披露原生高光损失。不能把原始 full 减去 starless 的算术闭合解释为 StarNet 已恢复被裁掉的信号。宽高均至少 512 仍是关闭 upsample 时的官方限制；非有限像素、解码失败、来源漂移及证据或运行时错误继续阻断。来源：[StarNet 2.6.2 CLI 官方输入范围](https://starnetastro.com/documentation/starnet/command-line-tool/)。


完整处理先评估 Stage 3–5；有依据地跳过背景扣除、校色或反卷积不额外阻断分星。分星通过审查后进入 Stage 7 的 starless 拉伸；失败或拒绝时保留已接受的含星父源，跳过需要可信无星分支的 Stage 8 增强和 Stage 9 合星。最终保留星点允许中间分星。

本阶段集中维护协议正文、参数、SSF 骨架及专属审查。provenance 按协议到阶段映射，以首份阶段文件为唯一 primary、其余为 supporting，绑定完整文件 SHA-256。通用质量门见 [quality](../quality.md)。

<a id="stars-separate"></a>
## stars.separate


<a id="stars-separate-适用条件"></a>
### 适用条件

只在线性父源上创建可审查 starless 分支。必须有受控 StarNet2 executable 和匹配模型；二者路径与
哈希来自本次 `probe`。Skill 不下载、解包或安装工具；缺失时报告来源和影响，由用户在 Skill 外安装，
本 session 跳过并保留完整含星父源。

<a id="stars-separate-执行触发agent-决策"></a>
### 执行触发（Agent 决策）

触发决定是否执行候选，实际审查决定是否采用。以下规则由 Agent 按当前任务、可靠目标类型和
真实预览应用；Python 不自动选择分支、参数或生成 SSF。用户明确选择的处理方式优先于默认分支。

| 当前任务与主体 | 前置条件满足时的执行选择 |
|---|---|
| 用户明确要求不分星或全程含星处理 | 跳过，保留含星父源。 |
| 用户明确要求 starless 或独立主体/星点处理 | 执行一份分星候选，包括用户明确要求处理的星团；记录其保护对象。 |
| 完整处理的星系或以云气/尘埃为主体的星云 | 默认执行一份分星候选，为 Stage 7–8 的主体拉伸与增强准备分支；不要求用户另说 starless。 |
| 纯星团或以恒星为主体的密集星场 | 默认优先含星路径；只有用户目标或具体图像收益支持时执行候选。 |
| 目标类型 unknown/general，输入状态与通道模式已确认 | 根据真实预览中的非恒星主体及独立处理收益决定；记录可见形态，不猜测目录类型或改写冻结上下文。 |

`stars=preserve` 与 `stars=adaptive` 都允许中间分星，默认最终仍合星；只有用户明确选择
`standalone-starless` 才交付无星终图。星云内嵌星团、星系紧致亮结或稠密前景星是保护区域，
不是仅凭类型跳过的理由。按 [目标形态审查参考](../target-morphology-review.md) 检查星系的
紧致亮结、小成员及有可靠来源的 HII 区/OB 星协，以及星云的云气、暗带和星团周边结构。

完整处理到本阶段前须依次评估 Stage 3–5，但不要求每个可选操作都已执行。SPCC/PCC 不可用
或被质量拒绝、背景已中性而跳过中性化、没有反卷积收益，均不单独阻止分星；使用上一份
已接受的 linear 科学父源，不使用被拒绝的校色候选。已确认线性与通道模式时，缺少逐通道
光谱通带也不单独阻止分星；不得据此补推谱线角色。

默认触发的任务若跳过，须在后续 review notes 或最终 limitations 中记录具体依据，例如用户
选择、缺失的受控工具/模型、不可用的 linear 父源、目标不可辨或没有可分离星点。不能只写
“没有明确 starless 需求”“最终保留星点”或“有中央星团”。执行候选时，在 provenance 的
applicability 中记录触发来源与保护区域；拒绝后回退须引用本次真实 review，不能把未执行写成失败。

<a id="stars-separate-ai-morphology"></a>
### AI 观察与分星选择

Stage 1 的 [形态观察](../target-morphology-review.md#ai-morphology-and-scientific-measurement) 仅提供保护与检查方向。到本阶段时实际查看当前已接受线性父源，按上方原触发规则及用户目标判断恒星是否主体、是否存在可分离的非恒星结构，并共同保护内嵌星团、紧致亮结和微弱外缘。与声明冲突时记录影响，不改写冻结 target_name/target_type；unknown/general 可按实际可见结构作候选决策，不猜目录身份。

不由名称改变冻结模型、stride 或原生预拉伸参数。Agent 仍按当前像素证据和原合同明确选择参数并记录 provenance；分星前后实际对照 full/starless/star layer。裁切、降噪或换父源后重新核对 ROI 坐标、来源哈希及像素域，不照搬 Stage 1 统计。无明确收益、结构误移或证据不足按原审查规则保留父源；AI 观察不新增自动接受或失败门。

<a id="stars-separate-参数"></a>
### 参数

probe 确认的 StarNet2 `2.6.2` 使用受审 `scripts/siril_starnet_linear.py` 与
[原生来源锁](../starnet-linear-source.json)，直接执行 CLI `--linear`；`2.5.0–2.6.1`
使用字节不变的 `scripts/siril_starnet.py` 与 [旧来源锁](../starnet-source.json)。由冻结版本确定路径，
执行失败不切换路径。Siril 仍限定 `>=1.4.4,<1.5`；禁止 upsample，高光保护开启，stride
只允许 128、256、512，默认 256。模型不替换，不自动搜索参数。

原生路径的 `linear_stretch` 必须显式填写 `shadows_clipping` 与 `target_background`，
默认 `-2.8/0.25`。前者允许有限负数，后者严格处于 `(0,1)`，布尔、未知字段与重复键拒绝。
参数依据当前图像和原生手册：紧致主体被误识别时，温和预拉伸可作为有依据的新候选，不能保证解决泄漏，
也不能将 M31 的候选值设为通用默认。receipt 记录参数、实际 argv、原生日志及输入/输出指纹，
不推算原生日志未提供的 MTF 系数。

输入复用 SSF 声明并由 Siril 保存的 full-source FITS，要求规范化 float32、有限像素、
尺寸至少 512×512。负暗部和高于 1 的值原样交接；不在桥接中裁剪、缩放或上采样。高于 1 的值由原生 `--linear` 按官方规则裁剪并告警，不额外设置阻断峰值或比例阈值；来源单位仍须正确，不能把浮点 ADU 当作归一化数据。
逐像素检查其与当前父源及已加载 Siril 图像一致。原生输出保持 mono/RGB、几何及 float32。
旧桥接仍使用上游 16 位 TIFF；其负值裁剪、峰值缩放和量化限制保留在旧 receipt，不追溯改写。
星层由 Siril `pm` 计算带符号的 `full - starless`，不用 StarNet 的裁剪 mask、Screen 或正残差变体。

<a id="stars-separate-固定运行合同"></a>
### 固定运行合同

先读取 session 的 `reports/tool-probe.json`；`tools.starnet.compatible` 必须为 true。Agent 在
`reports/060-stars/contract.json` 创建以下合同，路径使用绝对值：

```json
{
  "schema": "starun-siril.starnet-contract.v2",
  "source": {"path": "/abs/current-parent.fit", "sha256": "<source-sha256>"},
  "runtime": "<replace with the complete tools.starnet object from the frozen probe>",
  "stride": 256,
  "work_dir": "/abs/session/runtime/starnet/060-stars",
  "input_fits": "/abs/session/artifacts/060-full-source.fit",
  "linear_stretch": {"shadows_clipping": -2.8, "target_background": 0.25}
}
```

上例用于原生 2.6.2；较早受支持版本使用 v1 合同，删除 `input_fits` 和 `linear_stretch`，并选择旧桥接。
`runtime` 实际必须是完整 JSON 对象，不能使用上例占位字符串。合同目录名与 SSF run ID 相同。
计算合同 SHA-256；receipt 与合同同目录，且执行前不得存在。不得修改冻结的 CLI、模型、适配器、
来源锁或 Python 依赖记录；缺失依赖只报告，不在 Skill 执行时安装。

<a id="stars-separate-ssf-知识关系"></a>
### SSF 知识关系

适用性与 stride 来自当前 linear 父源、probe 与用户目标证据；本阶段文件为 primary protocol reference。
冻结手册提供 `pyscript/pm` 语法，两份 StarNet 来源锁固定对应适配器及旧验证器依赖，
`command-policy.json` 独立决定执行授权。Agent 编写 SSF 和同 stem provenance；改变骨架或 PM
表达式必须先查询手册并记录 evidence。

<a id="stars-separate-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
fmul 1
save "/abs/session/artifacts/060-full-source" -chksum
load "/abs/current-parent.fit"
fmul 1
pyscript "/abs/starun-siril/scripts/siril_starnet_linear.py" --contract "/abs/session/reports/060-stars/contract.json" --contract-sha256 CONTRACT_SHA256 --expected-source "/abs/current-parent.fit" --receipt "/abs/session/reports/060-stars/bridge.json"
stat main
save "/abs/session/artifacts/060-starless" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/060-starless" 95
close
pm "$artifacts/060-full-source$ - $artifacts/060-starless$"
save "/abs/session/artifacts/060-star-layer" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/060-star-layer" 95
close
```

把 full-source、starless、星层、两份预览及 bridge.json 全部列为 `--expect`，starless 为
`--primary-output`。SSF 中替换合同 SHA-256 占位符。保存 full-source 后重新加载原父源，保证
适配器的 loaded filename 与 source 合同一致。原生 v2 路径在两次 load 后分别执行
fmul 1：set32bits 不会将已加载的整数 FITS 自动提升为 float32，第二次 load 也会恢复原整数类型。
两次恒等乘法均受位置与倍率校验；旧 v1 桥接保持原骨架，不添加 fmul。转换遵循 Siril 的
FITS 缩放解释，不恢复已丢失精度，不裁剪或按图像最大值重新归一化。科学输出验证和 run receipt 提交成功后由执行器清理临时交接 FITS（旧路径为 TIFF）；失败时保留交接文件和原始日志供诊断，
不允许失败结果进入后续分支。

<a id="stars-separate-审查与回退"></a>
### 审查与回退

保留实际主体 autostretch 的 SSF、预览及已接受审查，供 Stage 7 作为跨阶段可见性参照。日志最后一组 autostretch MTF 可能属于星层，不直接当作主体参数；Stage 7 依据主体脚本重放参照，独立计算安全黑点并验收外缘、细丝、暗带和亮核。

科学验证要求四份源/层数据同形状且有限，full-source 与父源的逐像素绝对差不超过 `1e-7`，
`starless + star layer - full-source` 最大绝对闭合误差不超过 `1e-5`。这是同源数值验证，
不能证明恒星与主体被物理分离；实际结构审查仍必需。

分星适用性允许尝试，并不等于采用。紧致星系核（例如 M32）、伴星系外廓及尘埃结构必须
在固定同曲线 full/starless、核心/外缘 ROI 与星层对应位置实际对照；闭合和全图通道统计不能替代审查。

实际打开父源的已验证预览、starless 与星层预览，并核对同一分支 full-source/starless/star layer
的来源和几何。按相同位置检查亮星、密集星区、目标边缘及紧致目标；诊断 autostretch 可能各自
随统计变化，不能仅凭预览亮度差判定泄漏，也不得把诊断预览作为科学父源。

The run's `processing_checks.star_separation.channel_integrity` compares aligned
linear scientific planes in the same units on one parent-only p55–p99.2 mask.
It records per-channel background-subtracted p95 amplitudes, absolute retention
and raw nonpositive pixel fractions. Channels below 5% of the parent's strongest
amplitude are not flagged solely for being absent; retention below one half or
a raw-zero increase of at least 25 percentage points flags possible loss, including
loss shared by all channels. The mask is a heuristic, not proof of target isolation.
Inspect fixed target regions and the star layer to distinguish stellar removal
from nebula loss. Insufficient signal is `uncertain`; measurements never accept a
candidate. Confirmed loss requires reject and the containing-star fallback.
Do not use gain matching, clipping or synthetic line planes to conceal lost signal.

- 残星：starless 仍有明确对应的恒星残留或晕环；
- 黑洞/格纹：starless 新增孔洞、块状边界或规则纹理；
- 目标泄漏：starless 缺少父源中的目标结构，且星层在对应位置包含该结构；星晕本身不等于泄漏；
- 几何异常：尺寸、方向或同一目标位置不一致，不能靠后续合星掩盖。

实际异常写 reject；无法可靠确认时按质量协议写 uncertain。两者均不得采用候选，继续保留完整
含星父源；不能为了后续 starless 增强而接受破损分支。默认最终图仍必须含星。只在具体可修正
问题支持时生成新编号修订，原因和参数依据写入 provenance；不盲目轮换 stride/模型。定向排查见
[伪影参考](../troubleshooting.md)，合星仍遵循 [stars.recompose](../stages/stage-09-stars-recompose.md#stars-recompose)。

缺失 StarNet 且当前任务不触发分星时，只保留 probe warning，仍可 `success`。完整处理按上述
规则默认触发，或用户明确要求 starless/独立主体与星点处理，却因缺失而跳过时，记录 limitation code
`starnet_unavailable_preserve_stars_baseline` 并使用 `partial_success`；非交互环境不得下载。

<a id="stars-separate-可选主体-roi-诊断"></a>
### 可选主体 ROI 诊断

ROI 是可选证据，不增加分星接受门。复用 [区域合同](../quality.md#区域级科学测量)，用原始坐标选择亮核、微弱外缘或代表星区，绑定分星科学父源的哈希和几何。角色是声明，不是检测证明。

独立 autostretch 的亮度差不可比。需要同曲线对照时，可以在三份科学层均保存后，将以下显示片段追加到完整分星 SSF。两图使用根据父源选择的同一显式 MTF；下面的 0/0.1/1 仅是参数示例。声明两个新增 JPEG 为 --expect，实际打开两图，在同一组 ROI 对照，并检查星层对应位置。显式 MTF 仅允许该成对、同参数、load/mtf/savejpg 布局，不得回写科学层或放在 StarNet 前。

```ssf
load "/abs/session/artifacts/060-full-source.fit"
mtf 0 0.1 1
savejpg "/abs/session/previews/060-full-fixed" 95
close
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.1 1
savejpg "/abs/session/previews/060-starless-fixed" 95
close
```

run 的 star_separation.comparison_display 记录两份显示路径和显式传递；它们仍不是科学父源。原有两份自动诊断预览可以保留，不用其独立传递做量化比较。

```bash
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_metrics.py \
  --run-id 060-stars --protocol stars.separate \
  --parent /abs/current-parent.fit \
  --candidate /abs/session/artifacts/060-starless.fit \
  --star-layer /abs/session/artifacts/060-star-layer.fit \
  --regions /abs/session/reports/060-stars/regions.json \
  --output /abs/session/reports/060-stars/subject-metrics.json
```

报告绑定三图与区域合同哈希。每个 ROI/通道分别记录父源、starless 和星层的分位数及 raw_nonpositive_rate，并给出 parent - starless 的正/负计数、正/负质量、absolute_mass、最大绝对差位置和 max_abs_closure。量值来自原始科学像素，未归一化匹配增益或截断；星层天然可以非正，其比例不等于科学主体截断。位置采用预览左上角坐标。

中位差接近零可同时存在局部损失或正负抵消；闭合仅证明算术一致；恒星移除也会产生差值。报告不自动推断主体泄漏、不自动 accept，也不替代全图通道启发式。几何变化、非有限数据或不支持格式明确记录 uncertain/unsupported；只支持未压缩 FITS。将报告与实际检查的原生对照图加入现有 inspected_materials，说明 ROI 代表性；没有 ROI 时沿用现有流程。


### 执行失败与产物隔离

本节维护当前精度验证与失败隔离合同，执行依据会话冻结的 `session.execution_policy`；新会话默认值见 [`EXECUTION_POLICY`](../../scripts/deep_sky_siril_contract.py)。
执行器无论 Siril 退出码和日志终态，
均验证正式 bridge receipt；桥接缺失或无效时明确返回 failed，统计摘要、零星层或算术闭合
不能代替桥接证据，也不新增“星层必须非零”的硬门限。

执行失败时，在提交 run receipt 前，将该 run 声明并实际生成的 artifacts/ 和 previews/
文件移至 reports/<run-id>/rejected/，保留原相对目录、SHA-256 和大小。failed receipt 的
outputs 为空、primary_output 为 null；failure 记录 reason_code、reason、log_path，
rejected_outputs 记录 original_path、path、sha256、size，quarantine_errors 记录不能隔离
的 path 与 reason。隔离异常仍失败关闭，不覆盖目标、不删除证据、不回搬已经隔离的文件。
合同、桥接证据、日志和原生工作目录保留原位。

隔离仅针对执行失败；成功待审和正式 review reject 的材料完整保留。异常终止时尚未收尾的
文件仍是未提交材料，不能靠文件存在复用，也不得自动清理后复用同一 run ID。历史会话保持
字节不变，修正后重新 init。格式转换成功不意味着分星主体保真通过。

标准 unsigned 16-bit FITS（BZERO=32768、BSCALE=1）的提升须保持原解码像素。其他整数编码或缩放若被 Siril 重解释或量化，仍由既有父源/full-source 一致性门拒绝并隔离；不扩大容差或修改缩放语义以迁就转换。


接受原始线性 RGB 分星后，可信 Ha/OIII 双窄带默认评估 [独立主体拉伸](../stage7-hoo.md#dualband-hoo-stretch)。不得通过合成谱线图掩盖分星的通道损失；先在同位置核对原始 RGB 外壳、无星图和星层，再进入新分支。

#### 历史说明（仅回放或查询时读取）

显式精度提升与失败隔离在 [v6](../session-legacy.md#stage-owned-protocol-guidance) 引入；`guidance=stage_background_xisf_v9` 是[历史 v9](../session-legacy.md#scientific-stage-3-sources)的冻结标签，不代表当前默认。既有证据按原策略回放／finalize，不改写；继续执行权限见 [session 兼容合同](../session-contract.md#文档指导版本)。
