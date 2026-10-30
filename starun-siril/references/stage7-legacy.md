# Stage 7 · 历史策略与兼容说明

按需读取；当前科学规则和执行入口见[Stage 7 主文档](stages/stage-07-stretch.md)。

<a id="reference-driven-baseline"></a>

以下参照反求只解释历史提案；新会话使用上方显式因子与策略入口。
### 可选：参照反求 baseline 与独立单链比较

只有需要独立单链对照时，才使用 `init-request → solve → compare → generate --comparison`；两候选仍共用同一最终约束并保留完整绑定。`baseline` 是参照反求入口，不再是默认：需要从 Stage 6 autostretch 反求安全 MTF 初值，或当前结构未被形态链覆盖时使用。它只反求首个 MTF 的锚点亮度，不选择整链，不保证局部分离或颜色保留；baseline-request.json 保留请求中的后续算子，不自动补齐多节链，也不代表执行过形态比较。`proposed`、`eligible` 和 `solved` 均不能作为最终采用依据。历史请求、产物和决定不回填绑定、不重新命名。

1. 在现有 v4 custom 请求中声明真实 ROI、targets、亮核与端点保护，首步选择 MTF；初始模板的 midtones 只表示待替换的合法因子，不固定使用 0.015、0.006 或其他通用强度。选择代表欠显露结构的 faint ROI 作为 anchor-region，查看带框上下文并避开亮核、亮云及明显星晕；不能凭 ROI 名称认定它代表极暗外缘。混合大框可用于整体保护，不能直接作为弱外缘亮度锚点。
2. 用下方 baseline 入口绑定已接受 Stage 6 receipt/review、实际主体 autostretch 的脚本与预览。工具在原生 Siril 重放该主体参照并测量科学 ROI，独立计算当前线性父源的安全黑点，再由输入 ROI 与参照 ROI 的中位亮度反求 MTF 初值；不复制 autostretch 的黑点，不从 JPEG 量化亮度推定科学参数。原生试跑同时记录实际输出、保护诊断和 full 对照，初值不保证通过所有亮核或颜色保护。
3. baseline 输出新 baseline-request.json 与不可覆盖的提案证据；原请求不改写。Agent 按下方[预算与冲突复核](#baseline-budget-proposal)检查实际 audit，硬约束合格并查看试跑与参照后，才将输出请求交给既有 solve/generate。若保护目标冲突，按实测证据修订显示目标、锚点或请求并重新提案，不能更改白点或裁零修补。保留原黑点安全规则与合法范围。

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_siril_stretch.py baseline \
  --session /abs/session --request /abs/session/reports/070-template.json \
  --anchor-region shell --run-id 070-reference --timeout 1800
```

输入是首步 MTF 的既有 v4 custom 请求，目标、保护和来源仍由 Agent 提议；输出请求的可选 baseline_proposal 绑定只用于校验参照来源、首步算子和反求证据，不增加连续 D、独立白点或新的亮度指标。baseline 只生成诊断和提案，不自动接受，不直接创建科学父源；后续采用依赖完整原生求参、正式执行与审查。

<a id="baseline-budget-proposal"></a>
### Baseline 预算与冲突复核

试跑前，Agent 从当前可信科学父源测量并实际查看每个天空、主体和亮核 ROI，在现有 constraints 中填写输出预算，并在 operator.purpose 说明显示目标与依据。每个可信 background ROI 的 median 上下界是硬约束；不能只限制全图 median 或只写一个天空框。当前 `stretch_noise=diagnostic_only_v1` 下，天空 mad/hf_sigma2 仍须显式声明、测量和记录，但超预算只进入 noise_warnings，不据此淘汰候选。主体、亮核、科学端点及其他适用硬门保持不变。

例如 `{"region": "sky-a", "statistic": "median", "min": 0.02, "max": 0.12}` 只说明一条预算的字段结构，不是默认值或完整请求；实际 v4 请求仍需全部适用 ROI、MAD／高频诊断预算、目标和保护约束。范围来自当前材料和用户显示偏好，不能从“宽带”“亮星云”或目标名称查固定天空亮度。

baseline 反求的是 anchor-region 在 Stage 6 科学参照中的中位亮度，不会自动选择适合的天空亮度，也不扫描 seed 或按预算夹紧 midtones。返回 `status=proposed`、退出码 0 或得到一份 baseline-request.json 均不表示预算已满足。先读取 baseline-proposal.json 的 audit.eligible、constraints 和 reasons；下面示例在技能 scripts 目录位于 PYTHONPATH 时运行，只读报告并列出未经舍入的实际值与边界，不执行 solve、不填写采用结论：

```python
from deep_sky_siril_visibility import PROPOSAL
from deep_sky_siril_stretch import strict_json_bytes
from pathlib import Path
import json

session = Path("/abs/session")
path = session / "reports/070-reference/baseline-proposal.json"
proposal = strict_json_bytes(path.read_bytes(), document="baseline proposal")
assert proposal["schema"] == PROPOSAL
audit = proposal["audit"]
print(json.dumps({"anchor_region": proposal["anchor_region"],
    "reference_median": proposal["reference_median"], "eligible": audit["eligible"],
    "constraints": audit["constraints"], "reasons": audit["reasons"],
    "noise_warnings": audit.get("noise_warnings", [])}, indent=2, allow_nan=False))
if audit["eligible"] is not True:
    raise SystemExit("Baseline hard constraints failed; inspect evidence and create a new request/run ID before solve")
```

硬约束失败时停止衔接 solve，保留这份提案、原生试跑和失败原因；先判断是天空 median、主体区间、亮核层次还是科学端点冲突。天空 MAD／高频单独超预算不应误读为硬失败。Agent 结合实际参照决定是否修订显示目标、选择更有代表性的锚点，或按[路线修订规则](stages/stage-07-stretch.md#benefit-first-stretch)评估其他链；不能自动放宽预算、换白点或降低科学保护。修订使用新的原始请求和 run ID，从同一可信线性父源重新提案，并计入同一三次总额度；不回写旧报告或覆盖已绑定 baseline-request.json。单步 MTF 无法兼顾天空与主体时，可有依据地换链并保留同源比较，不把参照亮度照搬成最终显示目标。

M8-IRCUT-accept 的原请求将三个天空框 median 上限设为 0.30，baseline 的实际亮度 median 为约 0.274–0.287，均满足那份宽预算；这不是 helper 内置上限，也不是已绑定亮度口径下的 0.40。后续新请求改为上限 0.12，实际约 0.094–0.100。该案例说明应先明确预算，再检查提案与视觉效果；0.10–0.12 不成为其他宽带或亮星云的默认目标，数值合格仍不能代替参照可见性、颜色与层次审查。

单链对照已达到参照可见性且层次、过渡、颜色自然时，若已显式选择形态比较，仍须完成同源两链比较；高阶链没有可复核结构收益时可采用单链。默认多节路线不额外生成该对照，以真实首步前缀检查后续步骤。参照反求的 custom 单链仍有明确结构需求时，可在新请求追加有依据的 GHS：SP 读取基础拉伸后的实际 ROI，LP/HP 保护暗部与亮核；追加可复用 baseline_proposal，改变首步或基线条件则重新提案。链条完整、排名第一或微小百分比变化均不单独证明增强有效。

无可信无星分支时，仍按形态和实测曲线作用选链（可用有依据的短链）并执行含星保护；星团优先走含星路径。保留可信已接受线性父源的原生显示参照及真实首步前缀，不自动退回 autostretch → GHT 通用链。

同一传递链实测无法同时呈现外缘和亮核时，再显式使用既有共享蒙版 HDR：弱结构分支显露外缘，温和分支保留亮核，按最终混合图而非单独分支验收。HOO 的独立链与来源保持原合同，参照用于结构可见性，不把原 RGB 与 HOO 色彩差异当作校色失败。


<a id="stretch-参数"></a>
### 参数

无分星分支的含星基线可使用 linked autostretch。

- 无可信分星分支时默认 `autostretch -linked SHADOW TARGET → GHT`，首步抬入可测区间、后步使用实际当前域锚点；`-8.0<=SHADOW<=-2.8`，
  `0.08<=TARGET<=0.18`；
- `asinh -human STRENGTH OFFSET -clipmode=rgbblend` 只在亮核与微弱结构动态范围需要时使用，
  含星 Asinh + linked-autostretch 分支建议从 `20<=STRENGTH<=55` 评估，之后仍执行 linked autostretch；
- 有 StarNet 分支时使用全部参数显式的 MTF/Asinh/GHS 链，并分别传递原始 full/starless。

按 [目标形态审查参考](target-morphology-review.md) 同时选择亮核、微弱外缘与代表星区，不以
固定背景目标值代替观察。上述 Asinh 建议起点不是执行边界：显式链允许 `1<=STRENGTH<=1000`、
`0<=OFFSET<=1`，下方 strength=2 只展示合法语法，不要求每个目标串联三种拉伸。

<a id="stretch-方法选择"></a>
#### 方法选择

| 当前证据与目的 | 首个候选 | 审查重点 |
|---|---|---|
| 无可信分星分支、动态范围较温和 | linked autostretch → GHS，保留真实首步前缀 | 黑位、星色、亮核及背景噪声；不能把固定 TARGET 当质量目标。 |
| 微弱信号的颜色重要、亮核与外缘跨度较大 | Asinh，RGB 使用 `-human -clipmode=rgbblend` | 微弱色彩、核层次及星径；含星分支仍按所选形态链执行星点保护。 |
| 希望增加特定信号区间的对比 | 显式 GHS | 根据目标 ROI 的 median/分位数与直方图选择 SP，再结合背景、微弱外缘和亮核选择 LP/HP。 |
| 有可信无星分支、后续需要合星 | 全参数显式 MTF、Asinh 或 GHS | 记录每步真实输入域，完整冻结链；full/starless 复用同一链。按实际背景、微弱外缘和亮核审查，不设通用背景 median。只读 advisor 可提供诊断建议；参数层可求解 Agent 已声明的约束，不选择算子或替代科学裁剪与视觉验收。 |

根据当前形态与 ROI 目标评估相应高阶链，步骤多少不决定采用优先级。需要显式形态比较时按[形态链路比较](#profile-guided-chains-v23) 从同一父源生成单链基线和一个形态级联候选，
采用相同最终约束与显示条件比较；不要求先证明单链失败才允许组合。固定链不能表达实际目标时，按实测失败／保护冲突建立 exception 计划。固定链长不构成质量证据。
ROI 来自 [区域测量](quality.md#区域级科学测量)，不要将全图 median 改名为背景或目标区域统计。
若要依据链中间像素选下一步参数，保存并实测对应中间图；不把原始线性 ROI 数值直接套入已拉伸域。


<a id="stretch-ssf-知识关系"></a>
### SSF 知识关系

是否拉伸、方法和参数来自当前 linear 父源、目标动态范围与实际像素证据；本协议对应的阶段文件是 SSF 的 primary protocol
reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `autostretch/asinh` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定
流水线。下方 linked autostretch 完整变体可记录 manual lookup `not_needed`；asinh 分支或其他未展开组合
必须先查询原文并保留 evidence。

<a id="stretch-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
autostretch -linked -4.50 0.120
icc_assign sRGB
stat main
save "/abs/session/artifacts/070-stretch" -chksum
savejpg "/abs/session/previews/070-stretch" 95
close
```


<a id="stretch-compatibility-appendix"></a>
## 附录：历史策略与显式兼容入口

默认新任务按开头的[当前路线](stages/stage-07-stretch.md#morphology-default-chain)执行；仅在核对已有记录、显式搜索或双链比较时读取本附录。完整冻结策略、精确文档指纹及回放权限见 [session-contract](session-contract.md)。已有请求、命令原始字节、receipt、审查和交付不迁移、不改写；历史绑定只用于已提交记录的回放与 finalize，不能授权新执行。

| 历史规则 | 当前定位 |
|---|---|
| v22：`guidance=roi_guided_stretch_flow_v22`、`stretch=roi_protected_selection_v2` | 引入 ROI 保护与有限搜索；当前保护规则已归入[正文](stages/stage-07-stretch.md#current-roi-protection)，旧噪声排序与搜索规则保留在下方。 |
| v23：`guidance=profile_guided_stretch_flow_v23`、`stretch_strategy=morphology_chain_comparison_v1` | 显式形态双链比较；不是默认入口，实际启动的独立 solve 共享当前三次总额度。 |
| v24：`guidance=starless_subject_stretch_flow_v24` | 引入无星主体与 full 诊断分工；仍适用的[主体边界](stages/stage-07-stretch.md#starless-subject-scope-v24)在正文维护。 |
| v25：显式因子提案；v26：形态入口 | 当前因子格式与配方分别见[请求说明](stages/stage-07-stretch.md#agent-factor-proposals-v25)和[选链表](stages/stage-07-stretch.md#morphology-starting-chains)；历史策略只按原证据回放。 |
| v27：`guidance=subject_priority_stretch_flow_v27` | 历史主体优先标签；新任务使用 strategy.v2 绑定的 v4 单候选，按当前主体、保护、预算与审查规则执行。 |
| v28：`guidance=stellar_stretch_flow_v28` | 当前默认；Stage 7 主体规则沿用 v27，Stage 9 按实际星点问题选择 Fs，可显式使用 Modified Asinh；主体与星点分别冻结。 |

旧 HOO v1–v3 仅按原冻结证据回放与 finalize；历史 v1/v2 报告保留原每步最多 128 次原生评估及总 timeout。显式 v1 请求和 `--legacy-mtf` 保留旧温和排序，必须披露 legacy，不能宣称具有 v2 保护。显式 legacy 和历史分星策略按其冻结规则保护 full/starless，不追套当前主体诊断边界。

v1/v2/v3、goals.v1/v2、--search、参照反求 baseline、未绑定 strategy 的 v4 与 --legacy-mtf 仅解释历史证据，不授权新会话执行。v28 的完整策略、文档和实现指纹冻结于 references/stretch-routing-history.json；历史已有 receipt 的回放及 finalize 不迁移、不改写。

旧噪声策略为每份合规候选计算方向修正后的微弱目标与天空中位差，除以天空 MAD 中位数；多目标取最弱值。在最佳显示分离度的 95% 内，选择天空 hf_sigma2 中位幅度较低者，再按 gentle_key 和稳定参数顺序择优。天空 MAD 不大于 1e-12 时记录指标不可用，通过其他门后退回温和排序。新 `stretch_noise=diagnostic_only_v1` 使用上方 subject_contrast 排名，保留这些旧指标作诊断。均不得称为物理 SNR。显式搜索每步仍最多 128 次原生调用，包含 full 试算与前缀重放，并受总超时约束；搜索耗尽不证明数学上无解。

每步保留最多三份代表候选（选中、分离度最高、最温和）的原生科学图及同显示 JPEG、选中前缀、固定 ROI 对照与全部脚本日志。正式审查绑定参数报告、最终选中预览、固定局部与适用的 full 对照；之前各步与代表图通过参数报告完整绑定，保持原材料数量上限。实际检查亮核、外壳/暗带、背景纹理、残星、光晕和颜色；生成成功与数值通过都不自动接受。

<a id="stretch-单曲线自适应参数层"></a>
<a id="单曲线自适应参数层"></a>
### 单曲线自适应参数层（显式搜索兼容）

方法仍由目标形态、科学证据与用户取舍决定；参数求解不能把所有目标统一为 MTF。Agent 在
`reports/<run-id>/request.json` 声明 `starun-siril.stretch-request.v2`，包含 `source`、`regions`、
有序 `operators` 与真实 `manual_lookup`。source 使用 `@input` 或会话 artifacts 相对路径，
source/regions 都绑定 SHA-256。regions 沿用 regions.v1，必须有可信 background、faint；亮核存在时声明 core，无亮核时记录 core_absence 理由。
分星谱系另要求 stars；每步覆盖全部适用保护区域。全图 median/MAD 不能替代天空统计，MAD 不直接称为噪声。

每个 operator 声明 `method=mtf|asinh|ght|autostretch`、英文 `purpose` 与
`constraints=[{region, statistic, min, max}]`；统计量允许 `median/mad/p01/p99/span`，
`span=p99-p01`；v2 另支持 p999、max_rgb 和仅限天空的 hf_sigma2。RGB 亮度约束使用 human-weighted luminance，max_rgb 与端点逐通道保护。
Agent 用当前证据给出收益/保护区间，不按目标类别写死 target background。
GHS 另有 `landmarks={LP,SP,HP}`，每项为 `{region,statistic}`；由该步真实输入域测量，
次序不满足 `0<=LP<=SP<=HP<=1` 时停止，不排序或强行修正区域证据。

| 已选算子 | 动态参数 |
|---|---|
| MTF | LOW 取保守的逐通道暗部计数边界；HIGH=1 保留像素域端点。MID 由映射反解提供起点，再检查全部区域约束。 |
| Asinh | OFFSET 使用同一暗部安全依据，STRENGTH 在合法范围内求解；RGB 保持 `-human -clipmode=rgbblend`。 |
| GHS | LP/SP/HP 来自指定 ROI 的当前域统计；D/B 联合求解，五项数值均显式冻结。CLI 实测要求 D>0、B>=0；求解器在原生可执行范围内搜索，低 D 的数值退化同样通过实际截断检查排除。 |
| linked autostretch | 仅用于无分星分支，SHADOW/TARGET 遵守本协议范围并根据实际原生输出求解。 |

黑点不使用固定 2.8σ，不宣称硬件偏置已被扣除。采用保守 float32 参数边界，并由实际原生输出
确认逐通道科学暗部截断低于现有 0.01% 拒绝建议；源中已有非有限或越界问题独立记录。
分星分支同时检查同链 full-source 的截断及声明的 stars 约束。

`solve` 执行隔离的原生试算，每步最多 128 次原生评估（包含 full-source 与选中前缀重放），
受总 timeout 限制。v2 不在首次合规后停止：新噪声策略优先最大 subject_contrast，旧策略保留最佳分离度 95% 内天空高频代价最低者及温和参数排序。找不到时返回 `search_exhausted`，
不宣称数学上无解、不增加链长度、不换算子、不放宽约束。
显式组合逐步冻结并测量真实中间域，最后从原线性父源重放完整链；
末步约束声明整链最终收益及保护要求，前面步骤的约束约束各自的前缀输出。

试算 SSF、日志和测量保存在 `reports/<run-id>/parameter-trials/`；临时 FITS 只用于测量，
不进入成功 receipt 或科学父源谱系。结果写为 `stretch-parameters.json`，仅 `status=solved`
可用于 generate，且仍需视觉 review。SSF 的 `# adaptive-stretch:` 声明绑定该报告；
执行前检查链与绑定，执行后复测最终约束，receipt 回放继续检查报告、输入、运行时和试算证据漂移。

Agent 可实例化以下请求结构；区间及 GHS landmark 必须来自当前科学证据，不复制示例数值：

```json
{
  "schema": "starun-siril.stretch-request.v2",
  "profile": "custom",
  "targets": {"faint-a": "bright"},
  "core_absence": null,
  "source": {"path": "artifacts/060-starless.fit", "sha256": "<source-sha256>"},
  "regions": {"path": "reports/070-stretch/regions.json", "sha256": "<regions-sha256>"},
  "operators": [{
    "method": "ght",
    "purpose": "Reveal faint structure while retaining core hierarchy",
    "landmarks": {
      "LP": {"region": "background-a", "statistic": "p99"},
      "SP": {"region": "faint-a", "statistic": "median"},
      "HP": {"region": "core-a", "statistic": "p01"}
    },
    "constraints": [
      {"region": "background-a", "statistic": "median", "min": 0.05, "max": 0.12},
      {"region": "background-a", "statistic": "mad", "min": 0, "max": 0.01},
      {"region": "background-a", "statistic": "hf_sigma2", "min": 0, "max": 0.01},
      {"region": "faint-a", "statistic": "median", "min": 0.18, "max": 0.35},
      {"region": "core-a", "statistic": "p999", "min": 0, "max": 0.98},
      {"region": "core-a", "statistic": "span", "min": 0.05, "max": 0.5},
      {"region": "stars-a", "statistic": "p999", "min": 0, "max": 0.99},
      {"region": "stars-a", "statistic": "span", "min": 0.01, "max": 0.9}
    ]
  }],
  "manual_lookup": {
    "status": "performed",
    "reason": "Read the pinned GHS command semantics",
    "evidence": [{"path": "reports/manual-evidence/070-ght.command.json", "sha256": "<evidence-sha256>"}]
  }
}
```

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_siril_stretch.py solve \
  --session /abs/session --request /abs/session/reports/070-stretch/request.json \
  --run-id 070-stretch --timeout 1800
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_siril_stretch.py generate \
  --session /abs/session --parameters /abs/session/reports/070-stretch/stretch-parameters.json
```

generate 不执行 Siril；之后按原 run 合同声明全部产物、执行并审查。已有脚本、报告或候选不得覆盖；
修订使用新 run ID。Stage 9 重放同一冻结链，不重新选参。

只有调用方显式选择 `generate --legacy-mtf` 或 `calculate --legacy-mtf` 时保留旧 2.8σ 和
分类背景目标规则。旧模式仍校验父源、FITS 存储域和有限值，且不自动 accept。
缺少请求参数或实际审查时保留当前会话与证据，补齐后再继续依赖处理。




<a id="profile-guided-chains-v23"></a>
<a id="按形态评估高阶链路-v23"></a>
### 显式形态比较与搜索兼容

形态起始链统一见开头[选链表](stages/stage-07-stretch.md#morphology-starting-chains)。显式比较中的单链对照为 emission_nebula 使用 MTF，其他四类使用 Asinh；strategy 继续按冻结配方绑定，不能将默认单候选伪称为已完成双链比较。

<a id="请求生成与有限试跑"></a>
### 请求生成与有限试跑（v23 显式搜索兼容）

以下旧 goals.v1 搜索示例仅用于阅读历史材料，不用于新会话。新入口见[形态绑定合同](stages/stage-07-stretch.md#stretch-routing-v29)。

protection 是所有前缀共用的区域保护约束；phases 与高阶链逐步对应，只填写 constraints，GHS 步
另填 landmarks。每步同时覆盖所有适用区域，并具有天空 MAD/hf_sigma2、亮核/星点亮度与跨度保护。
phase 与共用保护中同一区域/统计量取区间交集，冲突拒绝，不静默放宽。前缀的弱结构目标可与末步
不同，避免第一步就被迫完成最终拉伸；末步与单链对照使用完全相同的最终约束。Asinh 初步分配亮度后，GHS 可能降低天空而提高主体反差，不强制各步背景递增。求解仍逐步冻结前缀，不是整链联合最优；后步搜索耗尽时用新编号审查并修订前缀目标，不能放宽最终保护来强行完成级联。

以下数字只演示字段，必须替换为当前科学父源的可信测量与显示目标，不是发射星云默认参数。

```json
{
  "schema": "starun-siril.stretch-goals.v1",
  "profile": "emission_nebula",
  "reason": "Observed diffuse emission with faint filaments and a compact bright region.",
  "targets": {"faint-a": "bright"},
  "core_absence": null,
  "protection": [
    {"region":"sky-a","statistic":"mad","min":0,"max":0.012},
    {"region":"sky-a","statistic":"hf_sigma2","min":0,"max":0.008},
    {"region":"core-a","statistic":"p999","min":0,"max":0.95},
    {"region":"core-a","statistic":"span","min":0.01,"max":0.8},
    {"region":"stars-a","statistic":"p999","min":0,"max":0.98},
    {"region":"stars-a","statistic":"span","min":0.005,"max":0.9}
  ],
  "phases": [
    {
      "constraints":[{"region":"faint-a","statistic":"median","min":0.025,"max":0.4}],
      "landmarks":{
        "LP":{"region":"sky-a","statistic":"p99"},
        "SP":{"region":"faint-a","statistic":"median"},
        "HP":{"region":"core-a","statistic":"p01"}
      }
    },
    {
      "constraints":[{"region":"faint-a","statistic":"median","min":0.05,"max":0.6}],
      "landmarks":{
        "LP":{"region":"sky-a","statistic":"p99"},
        "SP":{"region":"faint-a","statistic":"median"},
        "HP":{"region":"core-a","statistic":"p01"}
      }
    },
    {"constraints":[
      {"region":"faint-a","statistic":"median","min":0.1,"max":0.7},
      {"region":"sky-a","statistic":"median","min":0.04,"max":0.12}
    ]}
  ],
  "manual_lookup":{"status":"not_needed","reason":"Complete documented explicit operator skeletons; no command ambiguity.","evidence":[]}
}
```

```bash
"$STARUN_PYTHON" /abs/skill/scripts/deep_sky_siril_stretch.py init-request --search \
  --session /abs/session --source artifacts/060-starless.fit \
  --regions reports/stretch-regions.json --goals reports/stretch-goals.json --run-id 071-emission
"$STARUN_PYTHON" /abs/skill/scripts/deep_sky_siril_stretch.py solve \
  --session /abs/session --request reports/071-emission/baseline-request.json --run-id 071-emission-base
"$STARUN_PYTHON" /abs/skill/scripts/deep_sky_siril_stretch.py solve \
  --session /abs/session --request reports/071-emission/profile-request.json --run-id 071-emission-profile
"$STARUN_PYTHON" /abs/skill/scripts/deep_sky_siril_stretch.py compare \
  --session /abs/session --strategy reports/071-emission/strategy.json \
  --parameters reports/071-emission-base/stretch-parameters.json \
  --parameters reports/071-emission-profile/stretch-parameters.json
"$STARUN_PYTHON" /abs/skill/scripts/deep_sky_siril_stretch.py generate \
  --session /abs/session --parameters reports/071-emission-profile/stretch-parameters.json \
  --comparison reports/071-emission/comparison.json
```

当前任务的两次独立 solve 各占一次 Stage 7 总额度；每步原生调用与超时仍沿用原有资源上限，历史任务按冻结证据回放，不另建流程编排器。此显式搜索请求 schema 为 stretch-request.v3，只在 v2 上增加
冻结 strategy/variant 绑定，不增加原生命令或自动延长链。报告仍由现有 v2 求参核心生成。v3 的 GHS 粗搜同时覆盖低 D 与高 D，避免线性域均匀网格漏掉非线性前缀所需的微调区间；保持原每步调用上限。v3 将日志正常但科学像素含非有限值的试算记为不合格，保存参数、非有限计数和原生日志后继续有限搜索；不允许其成为代表或选中图。v1/v2 保留原失败行为，原生日志报错仍使本次求参失败。未完成
试算、日志报错、预算耗尽或无合规参数须保留真实尝试报告，不把失败说成数学上无解。

### 比较、采用与回放

compare 必须接收恰好两份对应尝试报告。已求解候选重新读取选中科学像素并检查相同最终约束；
原参数报告继续验证所有前缀、含星重放、运行时与参数绑定。失败候选仅作诊断，不成为科学父源。
新噪声策略按 subject_contrast 从高到低推荐，同分按稳定 run ID；天空 MAD 为零也不影响该指标。旧策略保留 95% 近优、较低天空高频和较小全图像素变化的排序。均不按步数惩罚高阶链，不把指标称为物理 SNR。
两者均失败时输出 retain_parent。当前 Agent 按[收益优先规则](stages/stage-07-stretch.md#benefit-first-stretch)选择实际主体收益最佳的合格候选，即使它不是 recommended_run_id。不得为迎合数值排名而牺牲弱结构可见性。

实际查看同显示条件的两候选、固定 ROI 面板及适用的 full 对照，逐项记录亮核层次、弱结构/暗带、
背景纹理、星径/光晕/星色与采用理由。优先保留可复核的主体增强收益，并记录暂未处理的颗粒噪声；超过预算的新增端点、外壳丢失、明显色块或颜色过渡破坏不能被更高分离度抵消。没有可信天空或无法判断时保留父源，不强行凑出候选。

正式 v3 SSF 必须同时绑定 adaptive-stretch 与 stretch-comparison。静态验证拒绝缺失比较、不同
父源/ROI/最终目标、错误候选、改动的请求/目标/运行时/代码/报告；正式输出继续独立复测。
receipt 的 adaptive_parameters.comparison 绑定比较报告，review-check 强制两候选预览、比较面板、
尝试报告和适用 full 材料；缺失或漂移拒绝，保持现有材料数量上限。科学负值、SHADOW_LIMIT、
-human/-clipmode=rgbblend、显式 RGB 星残差与 Stage 8 HDR 互斥不变。

历史 v22 完整策略、命令原始字节和修改前文档指纹已冻结。历史请求、receipt 与已提交交付按旧证据
回放/finalize，不迁移。显式 v1/legacy-mtf、v2 请求和未绑定 `strategy` 的 v4 提案保留原行为，
生成理由分别披露 legacy、v22-compatible 或 unbound，不把它们当作已经完成形态链间比较的证据。
