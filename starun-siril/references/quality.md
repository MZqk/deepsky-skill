# Quality and Review Protocol

本文件维护通用 review、证据绑定、ROI 坐标及可比性规则。背景、校色、PSF、拉伸、合星和调色的专属合同由[对应正文](#阶段专属合同)维护，执行或审查前按需读取；不从本页拼装阶段策略。
新会话默认策略由 [`EXECUTION_POLICY`](../scripts/deep_sky_siril_contract.py) 定义，已有会话使用自身冻结值；历史权限见 [session 合同](session-contract.md#文档指导版本)。

实际打开协议要求的每个图像或报告。文件名、摘要、统计数字和退出码不能替代视觉检查。

根据 [目标形态审查参考](target-morphology-review.md) 选择代表区域与保护对象，将具体观察写入现有 review notes；目标类型不确定时只描述可见形态，不推断输入状态或通道来源。
出现伪影、偏色或退化时按[排查参考](troubleshooting.md)区分输入已有问题与本阶段新增问题，定位首次出现处并选择最小验证；Stage 7 的重点区结论按[伪影专属合同](stage7-artifact-focus.md)结构化记录，其余说明写入 notes，不替代质量门。

## Review receipt

先按 [review-check](cli-contract.md#review-check) 调用只读命令取得当前 run 的骨架，由它绑定 receipt、产物与 SHA-256。实际打开要求的图像和报告后，填写 `verdict`、五项 `gates` 与 `notes`，保存 review；提交前可用 `--review` 复核。该命令不代替视觉审查，也不自动写入或接受 review。

每个采用的 run 写入 `reviews/<run-id>.json`，遵循 `review.schema.json`：

- 原样复制 run ID、receipt SHA-256、协议 ID；
- 列出每个实际打开材料的 session 相对路径和 SHA-256；
- 对 `structure`、`background`、`color`、`stars`、`geometry` 写
  `pass|fail|not_applicable|uncertain`；
- `verdict=accept` 要求所有适用门均为 `pass`，不得包含 `fail|uncertain`；
- 说明具体观察，不写无法从材料证明的推断。

数值证据保存在实际生成的 metrics/区域报告或 run receipt 的 processing checks 中；review 的五项
`gates` 保存判断，`notes` 解释证据与观察。Schema 没有独立数值字段不妨碍据真实测量记 fail，
但不允许伪造未实现的度量或扩展 gate 名称。测量报告也不能自动作出视觉 accept。

review.notes 上限 2000 字符，不自行扩展 schema、gate 名称或数值接受阈值；结构化 assessment 的字段、必需材料与采用条件按[专属合同](#阶段专属合同)填写。骨架只预填绑定，不预填视觉结论；自由文本不解析为质量分数。

## 通用门

- `structure`：真实细节保留，无抹除、伪结构、振铃、泄漏或塑料感。
- `background`：无本步新增坑洞、误减、条纹或色块；背景前后比较使用固定且已确认的天空区域。
- `color`：过渡连续，无通道裁剪或无来源的真实性声明。
- `stars`：检查星核、星径、星色和光晕，无重复星或明显残留；FWHM 与 roundness 只作辅助。
- `geometry`：构图、裁边、尺寸和 WCS 变化符合协议预期。

区分输入已有缺陷与本步新增损害，不要求单步消除原图所有噪声、既有饱和或残余渐变。
微小但可复核的收益同样有效，不要求肉眼显著；具体收益要求、技术门与例外由专属正文维护。
实际观察无法判断时不采用，可从仍有效的已接受父源继续独立步骤；来源或父源证据无法验证时停止依赖执行。
不能靠“后续会修复”填写 pass；测量、技术成功及数值排名均不自动产生视觉 accept。

成功执行后生成的 metrics.json 可支持审查。辅助报告失败只记录 warning，不逆转科学 receipt，也不代表视觉通过。
需要补充报告时用[显式恢复入口](cli-contract.md#auxiliary-metric-report-failures-and-recovery)，把绑定的 metrics-recovery.json 路径和 SHA 加入 inspected_materials。
恢复证据不替代协议强制预览或科学报告；缺必需材料阻断采用与正式交付，报告成功也不消除测量 uncertain。

## 可比证据与阈值

- 父源与候选使用相同几何、缩放、固定检查区域和同一显式显示传递。非线性 JPEG 使用同一明确的 sRGB TRC，不能将自动 ICC 猜测造成的 Gamma 差异当作曲线收益。相同 autostretch 目标值不代表
  相同传递；各自重新计算的预览只能辅助观察，不能量化前后改善。
- 背景判断覆盖分散且已确认无目标/星晕/尘埃的同一组区域；有实际测量时逐通道记录 median/MAD
  及区域坐标、单位和来源。现有 metrics.json 的全图统计不提供这些区域测量；未测量不得补写，
  没有可信天空区域时不宣称背景变平或中性，并按 background.subtract 判断是否跳过。
- FWHM 比较需同一图像域、采样尺度和检测设置，并检查可比星样本；星数减少或 FWHM 下降本身
  不能证明改善。亮核、目标外缘与星晕仍需独立视觉检查。
- 只报告本合同或对应协议实际采用的门槛，写清数值、单位、测量域与结果；外部流程的求解残差、
  背景变化或显示截断阈值不能直接移植。校验缺失明确为未验证，不能记 pass。
- 数值检查分别回答对应问题：全图暗部截断率不能排除局部坑洞，FWHM 不能替代振铃审查，合星
  算术闭合不能证明没有目标泄漏。既有阈值是本技能的执行或审查策略，不代表通用于所有目标的物理定律。

## Unknown 输入

只审查可读性，不从 direct/autostretch、色调或形态推断线性状态与通道来源；完成输入审查后停止依赖处理。
取得可靠来源证据后按 [Stage 1](stages/stage-01-input-inspect.md#input-inspect)正式确认；是否可在同会话继续由冻结策略决定。

## 最终审查

实际打开并绑定最终科学 FITS、对应最终 JPEG 和最终父源预览；父源预览必须来自生成当前父源的已验证 run，原输入则使用已验证的 input.inspect 预览，不临时伪造材料。
当前正式三产物与历史交付合同见 [Stage 10](stages/stage-10-delivery-finishing.md#delivery-render)。默认输出含星，仅已明确选择 standalone-starless 的会话可无星；unknown 不进入最终审查。

最终五项门全部为 pass 才能接受，不使用 not_applicable；任何 uncertain 使用 review_required。
limitations 不豁免五门，明显强偏色背景、通道裁剪、违规直方图、梯度、结构伪影或星点异常必须 fail。
收尾采用或跳过 tone.adjust、color.finish 的理由写入最终 delivery review notes。

## 主体显著性与收尾收益

在 stretch、tone.adjust 与最终交付的五项既有门中审查主体呈现，不新增质量门或自动指标。
按用户目标与噪声取舍，在 notes 说明外缘／微弱结构与背景的分离、尘埃边界、代表细节、核层次、星色／光晕及背景纹理代价。
固定 ROI 的逐通道目标—天空中位差可支持判断；亮度统计须逐像素加权后测量，不能将加权通道中位数当作亮度中位数。
差值增加、span 增加、颗粒增多或全幅增亮均不证明信噪比、细节保真或视觉接受。

按 [Stage 7 逐结构审查](stages/stage-07-stretch.md#structured-stretch-assessment)对照目标、固定局部与真实控制图。
线性转显示首份候选须展示真实主体；亮度收尾须说明相对父源的新增收益与代价。
单纯背景变暗、全幅变亮、技术通过或没有明显损伤不足以采用亮度收尾；外缘回落到背景或细节变难辨认则保留父源。
用户允许细颗粒时，不把更平滑或更暗当作隐含优先级。RL 等其他协议沿用其专属采用规则。

可将已验证 linked-autostretch 诊断图作为可见性参照，结合核、星点及噪声差异；不要求逐点等亮，不复制黑位，也不用不同显示传递量化收益。
优先复用已有参照。需要当前 nonlinear 科学图的参照时，在独立诊断 session 将其声明为已知 nonlinear，通过 input.inspect 生成 direct/autostretch 双预览。
诊断图不成为科学或正式交付父源，不扩大 tone.adjust/delivery.render 授权；最终图须在直接显示下满足目标。
新增高光端像素须结合原始值及核／星区局部复查；全图占比小不豁免已确认的层次或星色损失，证据不足仍 uncertain。

## Metric report v2

`starun-siril.metric-report.v2` 记录截断前有限 FITS/XISF 科学值：shadow_clip_rate 统计 ≤0，highlight_sat_rate 统计 ≥1，near_black_rate 统计 (0, 0.0001]。
比例为小数，0.0001 即 0.01%。全图 image_median/image_mad/image_std 不是背景统计，不能推断图像域或背景改善。

科学暗部比例达到 0.01% 时默认建议 reject；input.inspect 仅报告，经验证的独立[背景](stages/stage-03-background.md#background-subtract)和 [neutral](stages/stage-04-astrometry-color.md#neutral-held-out-evidence-policy)证据按各自成对端点合同判读，其他协议不继承例外。
near-black 正值比例和 JPEG 黑色像素均不能当作科学截断；JPEG 的 measurement_domain=display_quantized 只描述量化显示。
缺失、不可读或非有限测量保持 uncertain；自动报告不能建议 accept 或声称视觉门通过。

## XISF full-image measurement

XISF global candidate/parent metrics use the first Image only, preserving its channel order, negative
and above-range values, and nonfinite counts. Unsigned samples use their type's full range; Float32/64
use the explicitly declared bounds as black/white measurement units. No observed-maximum scaling or
NaN replacement is permitted. Reports identify Image 0 and auxiliary-image count. Missing bounds,
unsupported encoding and invalid payloads remain uncertain with an explicit reason; they never use
display previews to fill scientific fields. Supported blocks are attached/embedded, uncompressed or
single-stream zlib with optional byte shuffle, with checksum and declared-size verification.
This extends full-image metrics only; the existing ROI contract still requires uncompressed FITS.


## 区域级科学测量

Agent 根据实际预览显式选择无目标/尽量无星的背景、亮核、微弱外缘或代表星区，不自动选区。
预览必须保持科学图的原始尺寸和方向；缩略、翻转或裁剪后的显示坐标不能直接沿用。
在 `reports/<run-id>/regions.json` 声明科学父源哈希和原始宽高：

```json
{
  "schema": "starun-siril.regions.v1",
  "reference": {"sha256": "<parent-scientific-sha256>", "width": 1024, "height": 768},
  "regions": [{"id": "background-a", "role": "background", "rect": [20, 30, 100, 110]}]
}
```

`rect=[x0,y0,x1,y1]` 为预览左上角零基、右/下边界不包含；FITS 原生数组取
`[:, height-y1:height-y0, x0:x1]`。ID 唯一，role 为 `background/core/faint/stars`。
参考哈希绑定 `--parent`，未提供 parent 时绑定 candidate；几何或通道数改变不能比较，裁剪后重新选区。

测量工具使用 [CLI 合同](cli-contract.md#解释器与入口) 中已验证的 `STARUN_PYTHON`。

```bash
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_metrics.py \
  --run-id 070-stretch --protocol stretch \
  --parent /abs/current-parent.fit --candidate /abs/session/artifacts/070-stretch.fit \
  --regions /abs/session/reports/070-stretch/regions.json \
  --output /abs/session/reports/070-stretch/region-metrics.json
```

运行后生成独立报告，不覆盖原有 metrics.json。逐通道提供有限/非有限像素数、median、MAD、mean、
std、p01/p50/p99、近黑比例及科学裁剪比例，并绑定原图、候选和合同哈希。非有限或无法读取时
`uncertain`；不支持的格式为 `unsupported`，首版不将 JPEG、XISF 或压缩 FITS 回退为科学测量。
MAD 含真实结构、星点和梯度，不能直接叫噪声或 SNR；跨线性/非线性域不自动计算改善率。
把报告路径及 SHA-256 作为 review 的 inspected material，说明区域代表性，仍实际打开像素审查。
区域角色仅为 Agent 声明，不是检测证明；任何数值均不自动使质量门通过。

## 阶段专属合同

下列入口保留旧标题锚点，直接进入正文；这里只定位，不复制策略标签、阈值、配方或历史默认值。

| 审查内容 | 读取正文 |
|---|---|
| <a id="ai-观察的审查边界"></a>输入观察与来源边界 | [Stage 1](stages/stage-01-input-inspect.md#input-inspect-ai-morphology) |
| <a id="背景候选强制验收"></a><a id="scientific-stage-3-sources"></a>背景来源、独立收益、端点及颜色保护 | [Stage 3](stages/stage-03-background.md#background-subtract)；neutral 容器见 [Stage 4](stages/stage-04-astrometry-color.md#scientific-container-inputs) |
| <a id="neutral-held-out-benefit-and-paired-safety"></a>校色、neutral 与 PCC 告警 | [独立验证](stages/stage-04-astrometry-color.md#neutral-held-out-evidence-policy)、[审查与回退](stages/stage-04-astrometry-color.md#color-calibrate-审查与回退) |
| <a id="psf-spatial-evidence-v2"></a><a id="stage-5-scientific-acceptance"></a>PSF、RL 与线性降噪 | [PSF 空间证据](stages/stage-05-linear-restoration.md#restoration-deconvolve-空间覆盖与分区验收)、[RL 采用](stages/stage-05-linear-restoration.md#restoration-deconvolve-审查与回退)、[降噪采用](stages/stage-05-linear-restoration.md#restoration-denoise-审查与回退) |
| <a id="stage-35-evidence-limits"></a>算法前提、推荐参数与技术验收的界限 | [背景](stages/stage-03-background.md#background-subtract)、[校色](stages/stage-04-astrometry-color.md#color-calibrate)、[恢复](stages/stage-05-linear-restoration.md#assessed-scientific-restoration-policy) |
| <a id="可选分星主体证据"></a>分星主体 ROI 与固定显示对照 | [Stage 6](stages/stage-06-stars-separation.md#stars-separate-可选主体-roi-诊断) |
| <a id="protected-stretch-v22-evidence"></a><a id="profile-chain-comparison-v23-evidence"></a>拉伸参数、对照、试跑与结构化审查 | [Stage 7](stages/stage-07-stretch.md#structured-stretch-assessment)；仅历史查询读[旧合同](stage7-legacy.md) |
| <a id="stage-8-纹理与高频证据"></a><a id="multiscale-local-contrast-v21"></a>局部增强、HDR 资格与多尺度证据 | [纹理／高频](stages/stage-08-starless-enhance.md#local-contrast-texture-and-hf-gates)、[方法比较](stages/stage-08-starless-enhance.md#multiscale-competition)；HOO 另读[变体合同](stage7-hoo.md) |
| <a id="stage-9-display-domain-materials"></a><a id="合星显式提案与审查"></a>合星来源、显式提案与星点诊断 | [显示域来源](stages/stage-09-stars-recompose.md#explicit-recomposition-sources)、[提案审查](stages/stage-09-stars-recompose.md#agent-recomposition-proposal)、[固定星表](stages/stage-09-stars-recompose.md#fixed-catalog-geometry) |
| <a id="signed-residual-and-global-contrast-warning-review"></a>合星残差与交付全图告警 | [合星审查](stages/stage-09-stars-recompose.md#stars-recompose-review-and-fallback)、[交付直方图](stages/stage-10-delivery-finishing.md#delivery-histogram-review-v2) |
| <a id="非线性亮度调整"></a><a id="remaining-goal-tone-review"></a>剩余亮度目标与收尾审查 | [tone.adjust](shared-protocols/tone-adjust.md#remaining-goal-tone) |
| <a id="非线性轻量调色"></a><a id="local-saturation-evidence"></a><a id="无掩膜优先的增色审查"></a>增色、局部饱和度与区域目标 | [color.finish](shared-protocols/color-finish.md#color-finish)、[无掩膜与蒙版](shared-protocols/color-finish.md#unmasked-first-color) |
| <a id="paired-scientific-delivery"></a>正式配对交付与历史格式 | [Stage 10](stages/stage-10-delivery-finishing.md#delivery-render-最终审查) |
| <a id="color-preservation-evidence"></a>跨阶段颜色保护与空间蒙版 | [背景](stages/stage-03-background.md#background-subtract)、[校色](stages/stage-04-astrometry-color.md#color-calibrate)、[末段比较](stages/stage-07-stretch.md#rgb-tail-comparison)、[主体蒙版](stages/stage-08-starless-enhance.md#subject-spatial-mask)、[星点曲线](stages/stage-09-stars-recompose.md#stellar-curve-selection) |

历史默认值与恢复权限见 [session 兼容合同](session-contract.md#文档指导版本)及[版本沿革](session-legacy.md)，不据旧说明改写当前策略或既有证据。
