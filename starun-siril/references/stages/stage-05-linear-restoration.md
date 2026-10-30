# Stage 5 · 线性域降噪与亮度细节恢复

完整处理默认分别评估线性降噪和反卷积；评估不等于执行。新会话另冻结 `linear_denoise_backend=deepsnr_primary_v1`，有需求且条件满足时优先试跑 DeepSNR 1.3.2/model 2，NL-Bayes 为有证据的同源回退；继续共用 `linear_denoise=agent_proposal_v1`：Agent 依据实际父源判断降噪需求、选择可信天空与保护区域、提出显式参数，脚本只读取科学像素、生成命令和绑定诊断。单候选经审查同时证明降噪收益与保护结构保留才采用；无收益、纹理损失或无法判断时保留父源。背景更平滑不能抵消细丝、暗带、外壳或星核损失。

RL 仍独立评估：确认恢复需求与 PSF 适用性后运行一个保守候选，技术通过且实际审查未见实质退化时可采用，包括微小或不可辨收益及轻微局部代价。缺失材料、来源冲突或无法判断损害程度仍为 uncertain。两项都采用时先完成降噪审查，再从其实际已接受输出重新检测星点和生成 PSF。每项使用独立 SSF、run 与 review；跳过记录真实原因。

本阶段集中维护协议正文、参数、SSF 骨架及专属审查。provenance 按协议到阶段映射，以首份阶段文件为唯一 primary、其余为 supporting，绑定完整文件 SHA-256。通用质量门见 [quality](../quality.md)。

<a id="stage5-decision-evidence"></a>
## 独立判断需求与证据

降噪与 RL 分别记录当前实际父源、具体 ROI、查看的同曲线原分辨率材料、剩余目标和执行或跳过理由；沿用现有提案与评估记录，不新增 schema。`analyze` 的 `display_mtf` 只记录传入的显示参数，不生成预览，也不证明 Agent 已查看图像。不能从近黑的线性直出或未经核对的显示曲线认定噪声不可见。

- **降噪需求**：观察预期显示下的天空颗粒、色噪及其对弱结构表达的影响；有具体改善目标且条件满足时评估一份 DeepSNR 候选，不要求损害明显或预计收益大。长曝光、较小 MAD/天空亮度比、较高主体幅度都不能单独证明无需降噪。
- **幅度含义**：sigma=1/2/4/8 的幅度是亮度与对应高斯平滑之差的平均绝对值，包含结构、噪声及星点；主体/天空幅度比不是经标定的真实信噪比，MAD/天空亮度比也不是执行阈值。逐尺度报告实际值与可测性，不将一个尺度的结论推广到全部尺度，不据此自动选择强度或跳过。
- **RL 需求**：先判断细丝、暗带、边缘等主体是否还有细节恢复目标，再确认当前父源的原生星点与 PSF 适用性。星点看起来圆不能证明主体没有恢复空间；尚未测量时记录“PSF 尚未确认”，不能写成“已确认无缺陷”。没有恢复目标时可以跳过，不强制找星或反卷积；有目标但 PSF 不可靠时保留父源并说明缺项。

| 记录状态 | 所需依据 |
|---|---|
| 无剩余目标，跳过 | 具体 ROI 与实际显示观察；不以幅度比或曝光时长代替观察。 |
| 条件不适用，跳过 | 实际不满足的线性状态、运行时、可信区域或 PSF 条件。 |
| 证据不足，保留父源 | 明确尚未确认的噪声/纹理、显示或 PSF；不称为已证实无收益。 |
| 候选失败或被拒绝 | 绑定实际 run、日志或 review，说明技术失败、无收益、损失或无法判断；失败产物不采用。 |

预期的细丝损失、振铃或光晕风险用于选择保护区域、参数与审查重点，不能冒充已经观察到的退化，也不能单独证明候选无收益。降噪仍须证明噪声收益与保护纹理保留；RL 仍沿用技术通过且无实质退化的采用规则，不把两项的接受条件互换。`stretch_noise=diagnostic_only_v1` 只规定 Stage 7 的噪声诊断语义，不决定本阶段是否有降噪需求。

## 形态保护

按 [目标形态参考](../target-morphology-review.md) 选择代表区域。

- **富星野与致密星团 (如 M8 银心天区、M13 球状星团)**：
  - 核对原子协议所需的星点数量、空间分布与 PSF；排除饱和、重叠和形态异常星点，降噪后重新检测，不复用旧 PSF。
- **微弱气流与外围尘埃 (如暗星云、反射星云)**：
  - 按当前父源选择调制并检查真实细丝与纹理，不能以更平滑替代改善证据。
- **紧致亮核与锐利边缘**：
  - 独立检查亮核、边缘光晕和细节连续性，不按目标类型预先保证反卷积有收益。

---

## 故障定位

按 [排查参考](../troubleshooting.md) 定位首次出现的阶段，从同一已接受父源进行有依据的修订。

| 故障现象 | 潜在原因 | 排查与处置对策 |
|---|---|---|
| **星点周围出现黑色光环** | PSF 失配、迭代或正则设置不适合当前父源 | 拒绝候选，核对 PSF 与 TV；仅在协议范围内评估减少迭代或更强正则，不添加不存在的防振铃参数。 |
| **背景纹理异常或噪点放大** | 噪声、PSF 或参数不匹配 | 拒绝候选，检查线性降噪是否适用；若修订正则，明确较低 alpha 对应更强正则，并重新检查细节损失。 |
| **反卷积后无明确收益** | 参数作用有限，尚未证明细节收益 | 技术通过且未见实质退化时接受并记录近似不变；不凭差分小自动增大迭代、步长或切换算法。 |
| **微弱云气结构被抹去** | 降噪或正则过强 | 拒绝候选，按对应协议边界及实际细节修订，或显式跳过；不规定固定百分比的参数减幅。 |

<a id="restoration-denoise"></a>
## restoration.denoise


<a id="restoration-denoise-适用条件"></a>
### 适用条件

只用于 linear 父源。按[需求与证据](#stage5-decision-evidence)检查当前父源在预期显示下的天空和弱结构，有具体降噪目标且条件满足时评估单候选；默认在反卷积之前执行，因为反卷积会改变 NL-Bayes 所依赖的噪声分布。
线性和叠加不自动证明噪声为白高斯；去拜耳、重采样及目标亮度可改变噪声相关性或方差。降噪不是必做步骤；候选必须先通过审查，才可作为反卷积父源。细丝、尘埃带、微弱星云和星核是保护对象。

<a id="restoration-denoise-参数"></a>
### 参数

以下为 NL-Bayes 回退与 HOO/OIII v1 的参数语义；新 Stage 5 首选见 [DeepSNR](#deepsnr-primary)。使用原生 NL-Bayes；modulation 满足 `out=m*d+(1-m)*in`，只受原生范围 `[0,1]` 约束，不能解释为噪声标准差。没有固定起步强度或强度网格。Agent 必须显式提供 modulation、cosmetic 布尔开关和参数理由；缺参拒绝，不补默认值。`0` 表示跳过，不产生恒等 run。cosmetic=true 显式选择原生附带的盐椒修正；false 生成 `-nocosmetic`，不能隐式附带执行。其他选项不在共用变体中，须先扩展可复核提案与验证，不能绕过声明。

<a id="restoration-denoise-ssf-知识关系"></a>
### SSF 知识关系

规则正文由本文件维护；provenance 绑定本文件为 primary、冻结手册为语法语义依据，command-policy 独立授权。脚本不识别真实纹理，也不从 MAD 或多尺度幅度反推强度。

<a id="linear-denoise-agent-proposal"></a>
### Agent 提案、诊断与单候选

1. 打开当前科学父源及其同曲线显示，选择 `regions.v1` 的可信 background 与 faint/core/stars 保护 ROI，绑定实际科学来源 SHA 和几何。没有可信天空或无法区分颗粒与纹理时记录跳过。
2. 用 `analyze` 读取亮度、MAD 和既有 sigma=1/2/4/8 的多尺度纹理幅度，按[逐尺度示例](#stage5-diagnostic-summary)展示实际值。缺少真实 4σ 上下文的尺度标记不可测；不填充像素、不补造数值。
3. 新 Stage 5 使用 `starun-siril.linear-denoise-request.v2`，按 [DeepSNR 优先策略](#deepsnr-primary) 显式声明 backend、参数及观察依据；HOO/OIII 保持 v1。`generate` 只生成一个 SSF、provenance 及科学输出/预览清单，仍由现有 `run` 执行 Siril。
4. 审查同 MTF 父源／候选预览、所有保护区域的原分辨率对照和有符号移除残差。脚本报告背景 MAD、主体多尺度幅度、亮度/逐通道颜色及端点变化，只写诊断 JSON/PNG，不由 Python 写科学 FITS。
5. 修订使用新 run ID、同一未降噪可信父源，并通过 `revision` 绑定前一提案、实际 run、其材料与观察理由；禁止累积降噪。

```sh
python3 scripts/deep_sky_siril_denoise.py analyze --session /abs/session --source @input --regions reports/denoise-regions.json --display-mtf 0 0.15 1
python3 scripts/deep_sky_siril_denoise.py generate --session /abs/session --request reports/denoise-request.json --run-id 050-denoise
```

<a id="stage5-diagnostic-summary"></a>
### 逐尺度读取诊断，不生成决策

将本次 `analyze` 成功且可解析的原始 stdout 保存到新的 `reports/050-denoise-analysis.json`，拒绝覆盖；可复用[成功后独占保存](stage-09-stars-recompose.md#save-recomposition-analysis)的方式，将命令替换为上面的降噪 analyze。下面在技能 scripts 目录位于 PYTHONPATH 时运行，只消费现有 JSON、打印各尺度的天空幅度中位数与每个主体区的诊断比值，不调用 Siril、不写科学像素、不推荐参数或 trial/skip：

```python
from deep_sky_siril_contract import load_json
from pathlib import Path
from statistics import median
import json
import math

session = Path("/abs/session")
analysis = load_json(session / "reports/050-denoise-analysis.json")
regions = analysis["measurements"]["regions"]
rows = {(row["id"], row["sigma"]): row for row in analysis["multiscale"]}

def amplitude(region, sigma):
    row = rows.get((region["id"], sigma))
    if row is None:
        return None, "Missing scale measurement"
    if row.get("status") != "measured":
        return None, row.get("reason") or "Scale is not measurable"
    value = row.get("amplitude")
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None, "Invalid amplitude"
    return value, None

scales = []
for sigma in (1, 2, 4, 8):
    sky = []
    unavailable_sky = []
    for region in regions:
        if region["role"] != "background":
            continue
        value, reason = amplitude(region, sigma)
        if reason is None:
            sky.append(value)
        else:
            unavailable_sky.append({"id": region["id"], "reason": reason})
    sky_amplitude = median(sky) if sky else None
    sky_reason = "No measurable background regions" if not sky else (
        "Zero background amplitude" if sky_amplitude == 0 else None)
    subjects = []
    for region in regions:
        if region["role"] not in {"core", "faint"}:
            continue
        value, reason = amplitude(region, sigma)
        ratio_reason = reason or sky_reason
        subjects.append({"id": region["id"], "amplitude": value,
            "amplitude_reason": reason,
            "diagnostic_ratio": value / sky_amplitude if ratio_reason is None else None,
            "ratio_reason": ratio_reason})
    scales.append({"sigma": sigma, "sky_amplitude_median": sky_amplitude,
        "sky_count": len(sky), "sky_reason": sky_reason,
        "unavailable_sky": unavailable_sky, "subjects": subjects})
print(json.dumps({"source": analysis["source"], "regions": analysis["regions"],
    "display_mtf": analysis["display_mtf"],
    "interpretation": "Diagnostic amplitude ratios only; not calibrated SNR or an execution decision",
    "scales": scales}, indent=2, allow_nan=False))
```

比值分母只使用当前尺度的有效天空区域，不跨尺度合并；部分天空不可测时保留逐区原因和有效数。无有效天空或分母为零时比值为 null，主体自身不可测时保留原原因；这些状态不自动接受或拒绝任何候选。来源、区域、显示参数须与实际审查材料一致，保存的分析不替代正式 run diagnostics 或 review。

下例 v1 仅供 HOO/OIII 与历史回放；新 Stage 5 的 NL-Bayes 回退使用 v2，额外声明 `backend=nlbayes` 和真实 `fallback` 证据。显示 MTF 和下例参数仅解释格式，必须换成当前父源的观察提案，不是默认值或建议起点。

```json
{
  "schema": "starun-siril.linear-denoise-request.v1",
  "source": {"path": "@input", "sha256": "<actual-source-sha256>"},
  "regions": {"path": "reports/denoise-regions.json", "sha256": "<actual-regions-sha256>"},
  "display_mtf": [0, 0.15, 1],
  "decision": "trial",
  "reason": "Observed background noise interferes with the intended stretch",
  "parameters": {"modulation": 0.12, "cosmetic": false},
  "parameter_reason": "One proposal based on actual noise and protected filament observations",
  "manual_lookup": {"status": "not_needed", "reason": "Documented common variant", "evidence": []}
}
```

`decision=skip` 可省略 parameters/parameter_reason；仍绑定来源、ROI、显示和理由。修订额外字段为 `revision={"request":<prior-request-binding>,"run_id":"050-denoise","evidence":[<actual-material-binding>],"reason":"Actual observed change requiring revision"}`。

<a id="restoration-denoise-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

生成器展开原生 `load → fmul 1 → denoise → save`，随后分别 reload 父源与候选、应用相同显式 MTF 并保存 JPEG。手写 SSF 同样必须有一条 `# linear-denoise: {"path":"reports/denoise-request.json","sha256":"<actual-sha256>","size":123}` 声明，绑定实际提案；命令、参数、来源与显示不一致在执行前拒绝。不要照抄示例 size。

以下展开上面显式提案的格式；数值和指纹必须来自实际请求。

```ssf
requires 1.4.4 1.5.0
set32bits
# linear-denoise: {"path":"reports/denoise-request.json","sha256":"<actual-request-sha256>","size":123}
load "/abs/current-parent.fit"
fmul 1
denoise -nocosmetic -mod=0.12
stat main
save "/abs/session/artifacts/050-denoise" -chksum
close
load "/abs/current-parent.fit"
mtf 0 0.15 1
savejpg "/abs/session/previews/050-denoise-parent" 95
close
load "/abs/session/artifacts/050-denoise.fit"
mtf 0 0.15 1
savejpg "/abs/session/previews/050-denoise-candidate" 95
close
```

<a id="restoration-denoise-审查与回退"></a>
### 审查与回退

新候选 review 必须填写 `denoise_assessment`，绑定实际 diagnostics，明确 noise_benefit、noise_observation，逐保护区域填写 retention/observation 和 adoption_reason。`review-check` 仅生成空结论骨架，不能自动填写接受。接受需要可复核噪声收益以及每个保护区域 `retained`，structure/background 门都为 pass；无收益、`loss` 或 `uncertain` 均保留父源。天空 MAD 降低、多尺度幅度变化和执行成功都不能独立证明纹理保留，也没有统一纹理损失阈值。

新候选同时写入逐区域审查材料（`denoise-review-materials.v1`），不再要求靠整张 `denoise-regions.png` 缩放查看：

| 材料 | 内容 |
|---|---|
| `denoise-index.png` | 全幅父源（同一显式 MTF，超 1024 px 按整数降采样）定位全部区域；黄框为区域，洋红框为 ≥1 样本包围框 |
| `denoise-region-<id>.png` | 该区域同一位置的父图、候选图与带符号残差；小 ROI 按整数倍最近邻放大（目标短边 300 px，上限 8×），大 ROI 保持 1:1 |
| `denoise-region-<id>-slice-<r>-<c>.png` | 原生边长超过 1024 px 的大 ROI 追加 512 px 1:1 局部切片，逐片记录 rect |
| `denoise-highlight-<c>.png` | 通道内每个 ≥1 样本的星核前后对照（父图、候选、残差），逐格标注 x/y、样本数、父/候选峰值 |
| `denoise-review-materials.json` | 以上全部文件的 path/sha256/size 清单，接受为 review 的单一材料绑定 |

父图与候选使用同一显示传递；残差灰色为零，红为 `parent-candidate>0`（被移除），蓝为负（被增加），并标明共同尺度与各区域独立尺度。`review_materials.regions[].zoom`、`native`、`rect`、`residual_scales` 与 `slices[]` 记录原始坐标、放大倍数和尺度，`review_materials.highlights[]` 除总数与包围框外给出逐像素 `x/y/parent/candidate`（超出 1024 条时 `pixels_truncated`）及瓦片数（超出 256 格时 `tiles_truncated`）；坐标与 region `rect` 一致，均为自上而下显示坐标，星核对照按 3× 最近邻放大。`denoise_assessment.structures[]` 逐区域绑定该区 `view`、`slices`、`residual_scales` 和实际非零 `highlights` 记录，review 必须检查这些文件后才能填写结论。

review 仍受 32 份材料上限约束，因此 `review_paths` 只新增材料清单与索引图两项并替代旧面板，逐区域 PNG 的哈希经清单间接绑定；数值统计仍只作辅助证据，纹理、噪声与星核是否保留由实际查看这些原生图像决定。

旧 Stage 5/HOO v1–v3 只按其原有冻结证据回放与 finalize，不追加提案、诊断或 review 字段，不改写原结果。Stage 10 非线性降噪、RL 参数和配色路线不变。

<a id="deepsnr-primary"></a>
### DeepSNR 首选与原生回退

Stage 5 新执行使用 `starun-siril.linear-denoise-request.v2`，保留上述 source、regions、display_mtf、decision、reason、parameter_reason、manual_lookup 和 revision 绑定，额外显式声明 `backend=deepsnr|nlbayes`。旧 v1 不用于新 Stage 5，继续用于既有 HOO/OIII；Stage 10 不变。

1. 从当前未降噪可信父源评估噪声、实际通道来源和保护区域；适用且有需求时先生成 DeepSNR 单候选。官方更适合非相关高频噪声，普通去拜耳 OSC 及 walking noise 须具体评估，不凭设备名或“AI”保证收益。
2. probe 必须绑定已安装 DeepSNR 1.3.2、model 2、适配器、能力和资源指纹；不安装或下载。固定 `--linear --model 2 --stride 480`，model 2 支持单色及 RGB；宽高至少 512，输入须有限且 XISF bounds 为 0:1。
3. DeepSNR parameters 为显式 modulation 与 linear_stretch，不允许 cosmetic。modulation 在 `[0,1]` 内且无默认值；0 跳过。MTF 初值使用原生 shadows_clipping=-2.8、target_background=0.25，显式写入提案；修订仍绑定真实观察，范围分别为有限负数与 `(0,1)`。
4. 受审 `siril_deepsnr.py` 经 `load → fmul 1 → pyscript → stat → save` 运行。桥接用 Siril 导出、验证 float32 FITS，运行原生线性 MTF/逆变换，再用 Siril PixelMath 按 `m*denoised+(1-m)*parent` 混合。Python 不写科学像素；负值原样传递，高于 1 由原生裁剪并保留数量、峰值、显示坐标的受影响包围框和日志，不能暗中归一化或裁剪。官方行为见 [CLI 文档](https://starnetastro.com/documentation/deepsnr/command-line-tool/)。
5. 使用同曲线预览、原分辨率保护 ROI 和有符号残差审查；须同时有噪声收益与结构保留，不能由数值自动采用。裁剪告警须实际检查对应亮核／星核和高光损失。采用后重建 RL 的星表与 PSF，不复用降噪前测量。

下面仅展示格式；source/regions 指纹、modulation、显示和理由必须来自实际父源。0.4 不是默认强度。

```json
{
  "schema": "starun-siril.linear-denoise-request.v2",
  "backend": "deepsnr",
  "source": {"path": "@input", "sha256": "<actual-source-sha256>"},
  "regions": {"path": "reports/denoise-regions.json", "sha256": "<actual-regions-sha256>", "size": 123},
  "display_mtf": [0, 0.15, 1],
  "decision": "trial",
  "reason": "Observed random grain interferes with the intended faint-shell stretch",
  "parameters": {
    "modulation": 0.4,
    "linear_stretch": {"shadows_clipping": -2.8, "target_background": 0.25}
  },
  "parameter_reason": "One explicit proposal protecting the observed filament and bright core",
  "manual_lookup": {"status": "not_needed", "reason": "Documented audited bridge variant", "evidence": []}
}
```

DeepSNR 不可用、不适用或经实际审查拒绝／无法判断时，NL-Bayes 以 v2 `backend=nlbayes`、原 modulation/cosmetic 参数和必填 `fallback={"reason": "Actual reason", "evidence": [<actual-file-binding>]}` 另建提案/run。证据必须包含 session 冻结的不可用 probe、同源适用性评估或对应 DeepSNR reject/uncertain review。
适用性评估为 `starun-siril.deepsnr-applicability.v1`，包含同一 source 绑定、`decision=inapplicable` 与具体 reason；运行不可用时引用其实际日志与失败证据，不把来源、哈希、精度或合同错误解释为算法不适用。原生回退仍从同一未降噪父源开始，禁止在失败 run 中静默换算法或累积降噪。

每次候选使用独立 run ID；失败产物隔离且不能作为父源。桥接合同、回执、原生日志与浮点输入／输出保留用于核对实际调制和不可变回放。旧会话只按冻结策略与指纹回放/finalize，不补新字段或迁移证据。

<a id="restoration-deconvolve"></a>
## restoration.deconvolve


<a id="restoration-deconvolve-适用条件"></a>
### 适用条件

只用于 linear 父源。按[独立需求判断](#stage5-decision-evidence)说明主体恢复目标，不能以圆星或未经标定的幅度比直接否定。必须有可靠、未饱和的星点支持可用 PSF；推荐约 20 个具代表性的建核样本，数量不构成普适质量门。少于 20 保留告警，在 review 中说明覆盖、实际细节、噪声与采用依据；无可用 PSF 或无法判断适用性时保留父源。目标本身不是星点替代物。若采用线性降噪，应先完成并审查；原生找星与 `makepsf` 都从当前实际父源重新运行，
不复用降噪前或其他候选的星表与 PSF。

<a id="restoration-deconvolve-参数"></a>
### 参数

- `setfindstar` 使用 Moffat、保守 roundness 与振幅门；
- 原生找星后最多采用 500 星；桥接保留原生返回顺序的前 500 个模型，再排除 `has_saturated=true`；
- `makepsf` kernel 31 为起点；沿用合同的可变奇数尺寸（至少 3、不超过图像短边），结合 FWHM、Moffat 翼部和空间适用性选择，不仅凭固定尺寸保证充分覆盖；
- Richardson-Lucy 推荐从 5–10 次迭代开始，保持 TV；次数不是普适上限，其他值依据冻结手册、当前残差和 provenance，不能自动递增；
- `alpha` 采用 Siril CLI 默认 3000 作为骨架起点；1–10000 仅是原有参考区间，不是普适质量边界。其他值按原生合法性、冻结手册和实际正则效果选择。值越低，TV 正则越强、
  结果越平滑，不代表反卷积越强；值越高，正则越弱，细节与噪声都可能增加。
- 默认使用梯度下降，`gdstep` 默认 0.0005；少量迭代可能改善不明显。增大步长可能不收敛，
  不根据输出差分小就自动提高步长、迭代数或切换算法。

<a id="restoration-deconvolve-ssf-知识关系"></a>
### SSF 知识关系

适用性、星点层、PSF 和迭代参数来自当前 linear 父源、检测报告和实际像素证据；本协议对应的阶段文件是 SSF 的 primary
protocol reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供相关命令语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定
流水线。逐字采用本节已展开参数可记录 manual lookup `not_needed`；其他 PSF/RL 选项必须查询原文。

<a id="restoration-deconvolve-内部样本合同与原生桥接"></a>
### 内部样本合同与原生桥接

新 session 冻结 `psf_provenance=native_star_samples_v2`。兼容的 SirilPy 1.0.25 运行时使用固定
`scripts/siril_psf_samples.py`，参数布局与其他桥接一致。合同位于 `reports/<run-id>/`，例如：

```json
{
  "schema": "starun-siril.psf-samples-contract.v1",
  "source": {
    "path": "/abs/current-parent.fit",
    "sha256": "<actual-parent-sha256>",
    "width": 384,
    "height": 384,
    "channels": 3
  },
  "detection": {
    "setfindstar": ["reset", "-moffat", "-sigma=1.00", "-roundness=0.35", "-minA=0.01", "-maxA=0.95"],
    "layer": 1,
    "maxstars": 500
  },
  "stars_tsv": "/abs/session/reports/055-deconvolve/before.tsv",
  "psf": {
    "path": "/abs/session/reports/055-deconvolve/psf.fit",
    "kernel_size": 31,
    "symmetric": false
  }
}
```

几何和哈希必须来自实际父源；mono 使用 layer 0，RGB 通常使用绿色层 1。当前 makepsf 生成单通道核；配 RGB 时 Siril 转入 Lab，仅反卷积 L，再与原 a/b 合成并转回 RGB。这是亮度细节恢复，不是逐个线性 RGB 通道的物理恢复，不声称通量无偏或窄带线比保真；定量测量保留校准 master。检测参数必须与 SSF 完全一致，
显式 `reset`，捕获上限 maxstars 在 20–500 内，这不是实际检测数或实际建核数的下限。输出留在合同目录，提前声明且不得已存在。公开 contract/schema 版本不变。

固定桥接在当前实际父源和冻结检测设置下读取一次原生 `get_image_stars`，记录全部检测数量、原生顺序的有限模型和饱和排除原因，
从非饱和模型派生参数并调用 Siril `makepsf manual`：先把每星 FWHM 规范化为长轴 a、短轴 b，
交换轴时角度同时旋转 90°，方向折返到 [-90°, 90°)。长短轴和 Moffat beta 均值沿用原生 float32 算法；
ratio 为平均长轴/平均短轴，`symmetric=true` 时为 1。FWHM 始终是像素。

报告与建核共用周期方向统计：e=(a-b)/(a+b)，用 `math.fsum` 汇总 C=Σe·cos(2θ)、S=Σe·sin(2θ)、W=Σe；
mean_angle=wrap(atan2(S,C)/2)，concentration=hypot(C,S)/W。圆星自然降权；全圆或显式对称核角度为 0，
报告方向为不适用。非圆核方向合量 ≤64×math.ulp(1.0)×W 时属于数值退化：保存原星表和失败桥接证据，
停止本次 RL，不使用旧角度均值或原生 stars 回退绕过。此判据只处理不可确定参数，不是空间质量阈值。
Python 只写样本证据和计算标量，不生成、归一化或修改 PSF/科学图像像素。
RL、TV 和核尺寸按下方骨架与参数边界执行；样本捕获不可用时按本协议的 uncertain 与原生回退路径处理。

桥接前紧接父源 `load`、`fmul 1` 和冻结的 `setfindstar`；桥接后紧接加载同一 PSF 的 `rl`。
不得换源、修改图像、清星表、重新检测或替换 PSF。前测 TSV 是完整精度的原生模型导出，
不另跑 `findstar` 冒充同一批样本。桥接 receipt 记录坐标、检测层、模型、FWHM、振幅、beta、角度、
`has_saturated`、参与/排除计数、原生命令和 PSF 哈希；执行器核对合同、TSV、receipt、PSF 与日志。

<a id="restoration-deconvolve-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
fmul 1
setfindstar reset -moffat -sigma=1.00 -roundness=0.35 -minA=0.01 -maxA=0.95
pyscript "/abs/starun-siril/scripts/siril_psf_samples.py" --contract "/abs/session/reports/055-deconvolve/contract.json" --contract-sha256 <actual-contract-sha256> --expected-source "/abs/current-parent.fit" --receipt "/abs/session/reports/055-deconvolve/bridge.json"
rl "-loadpsf=/abs/session/reports/055-deconvolve/psf.fit" -iters=6 -tv -alpha=3000.0
stat main
save "/abs/session/artifacts/055-deconvolve" -chksum
close
load "/abs/session/artifacts/055-deconvolve.fit"
setfindstar reset -moffat -sigma=1.00 -roundness=0.35 -minA=0.01 -maxA=0.95
findstar -layer=1 -maxstars=500 "-out=/abs/session/reports/055-deconvolve/after.tsv"
close
load "/abs/current-parent.fit"
mtf 0 0.15 1
savejpg "/abs/session/previews/055-deconvolve-parent" 95
close
load "/abs/session/artifacts/055-deconvolve.fit"
mtf 0 0.15 1
savejpg "/abs/session/previews/055-deconvolve-candidate" 95
close
```

将 `before.tsv`、`after.tsv`、`bridge.json`、`psf.fit`、科学候选和两张预览七项逐一列为 `--expect`。
合同是已有输入证据，不列为新产物。保存科学图并重开后，再用完全相同的设置、层和最大星数检测，最后制作显示预览。

运行时缺少桥接能力时，显式采用现有原生命令路径：在父源的 `setfindstar` 后调用 `findstar -layer=1
-maxstars=500 "-out=.../before.tsv"`、`makepsf stars -ks=31 "-savepsf=.../psf.fit"`，再执行相同 RL 和后测/预览。
这一路径不输出 `bridge.json`；按实际产物声明。不得安装依赖、静默重试或调整找星参数。
桥接捕获不可用、没有非饱和样本或平均参数超出原生 manual 可表示范围时，也显式记录 `uncertain` 并使用
原生 `makepsf stars`；不裁剪平均值。日志中的自动重检测星数可记录，但具体样本不可追溯。

<a id="restoration-deconvolve-匹配测量与证据限制"></a>
### 匹配测量与证据限制

`processing_checks.deconvolution` 保留材料哈希、原始日志行号、PSF 样本链和前后配对。
严格解析保留原生坐标、TSV 行号、像素单位与检测设置；通道、几何、单位、设置不可比时记录原因，
不计算混合样本的改善比例。双方非饱和、坐标/FWHM 有效且无重复的星点，按距离不超过 1.0 像素、
双向唯一匹配；不转换坐标或强配拥挤歧义。每对记录 X/Y/平均 FWHM、roundness、差值和比例，
汇总匹配/未匹配、重复、饱和、无效/歧义计数及配对变化的分位数。没有有效配对时为 `uncertain`。

诊断随 receipt 保留，审查通过现有 `inspected_materials` 绑定相关报告、PSF、候选和预览。
缺少证明、匹配少或不可比只记录诊断，不添加数值质量拒绝阈值；仍按下方既有协议条件和五项视觉门判断。
v2 正式采用前必须检查两份绑定空间报告及实际父源／候选像素，材料缺失保持待审查。
合同漂移、错误父源、越界路径、未声明产物和篡改沿用完整性拒绝。失败 receipt 保留已取得诊断，
失败产物仍不能进入审查、后续父源或交付。星数一致不能证明同一批样本；自动重检测数量不能补充缺失的样本来源，FWHM 缩小、星数下降或溯源成功均不能自动证明恢复收益。

<a id="restoration-deconvolve-空间覆盖与分区验收"></a>
### 空间覆盖与分区验收

执行器生成 `reports/<run-id>/psf-spatial.json` 和 `psf-spatial.md`；它们由执行器写入，不加入 SSF 的
`--expect`。报告绑定父源、候选、前后星表、PSF、桥接 receipt 与已有合同的 SHA/大小；run receipt
只保存报告路径、SHA/大小及简短摘要，逐星与逐配对证据由原星表复现。

按父源实际宽高划分固定 3×3 网格，使用原生 Siril 星表 X/Y 像素坐标，左上边缘为原点 (0,0)，X 向右、Y 向下，
区间为半开 [start,end)，不转换为预览坐标。各层和 Gaussian/Moffat 分开汇总：
捕获、有效、饱和、重复、无效、未匹配数，以及 FWHMx/y、长短轴比、Moffat beta 的 p05/中位数/p95，
Gaussian 不报告 beta。有效样本排除饱和与所有重复；饱和和重复计数可重叠。无法定位的无效行计入 unassigned。

报告披露捕获上限、实际捕获数、可证明的原生检测总数及截断状态；只有桥接证据 verified 才给出实际建核数，
原生回退保持未知。前后配对复用 ≤1 像素双向唯一集合，按父源位置分区，记录 FWHM、roundness、
180° 周期方向差与跨区移动；圆星不提供方向变化结论，不重新匹配、不强配歧义。
空区、缺几何、不可比表和无有效配对为 uncertain；小样本只报告可测结果和数量，不宣称区域适用。
局部恶化与低覆盖需要实际审查，不自动拒绝；本报告不能证明整幅 PSF 恒定，也没有独立留出星像验证。

报告生成失败保留科学结果和告警，不逆转已完成的科学执行；缺必需报告不能正式采用。
回放只重算核验，不创建或改写证据。冻结 v1 会话保留原选样、均值、诊断和紧凑／内联 receipt 的
只读回放与 finalize，不补写空间报告、不执行新变体。

<a id="restoration-deconvolve-审查与回退"></a>
### 审查与回退

检查振铃、噪声放大、星心、光晕和真实目标细节。v2 审查通过 inspected_materials 绑定两份报告、科学父源
（输入 master 用 `@input`）与候选，并打开已有预览。notes 必须说明区域差异、稀疏／未覆盖区限制及接受依据；
指标成功不能自动接受。无法生成可用 PSF 或出现明显振铃、噪声与细节退化时 reject；证据不足时 uncertain。少于建议样本数只保留告警，不据此自动拒绝。实际审查没有实质退化时接受，包括收益微小或无法辨出收益的候选，并记录判断与代价。

候选无明确恢复收益不再构成拒绝理由。参数采用默认值、算术差分可测或命令成功仍不能代替实际细节、噪声与振铃审查；审查确认没有实质退化才接受。不为“变差过多”杜撰通用百分比，不从其他数据的单次测量导出当前候选阈值；科学完整性门不变。

`-mul` 是乘法算法变体，`gdstep` 对它无效；采用前查询冻结手册
[rl 命令](../siril-manual/source/doc/commands/rl.rst) 与
[反卷积原文](../siril-manual/source/doc/processing/deconvolution.rst)，保留查询证据，
从同一已接受父源生成独立候选。它不是本协议的默认骨架；明显差分不能代替噪声、振铃与真实细节门。


<a id="assessed-scientific-restoration-policy"></a>
## Assessed scientific restoration policy

New sessions freeze `restoration=assessed_scientific_flow_v1`, retaining contract 1,
guidance and `psf_provenance=native_star_samples_v2`. The default RL example remains
6 iterations, TV, alpha=3000 and the native gradient-descent step 0.0005; do not
increase iterations or switch algorithms automatically when improvement is unclear.
Kernel 31 and about 20 representative samples are recommendations, not quality gates.

Stage 5 reads FITS/XISF scientific inputs. XISF binds Image 0, declared units and
canonical FITS bottom-origin pixels; float bounds must be 0:1. Native `set32bits`
alone does not promote integer inputs: immediately after parent load, exactly one
`fmul 1` is required before denoise or PSF detection/RL. No other multiplication or
scientific transform is permitted. The sample bridge still reads the actual source
and frozen detection settings; promotion does not authorize changing the source.
Scientific output is float32 FITS with finite pixels, matching channels/geometry and
preserved complete WCS. Missing WCS does not prevent restoration. PSF spatial
reports read actual Image 0 geometry and retain native star coordinates unchanged.

Parent and candidate previews must use the same explicit MTF; 0/0.15/1 in the
examples is illustrative and must be chosen from the actual parent. Declare both
JPEGs and every scientific/report output with --expect. Accept reviews must inspect
both previews and the run receipt; deconvolution additionally retains its existing
PSF, scientific pixel and spatial-report requirements. Check faint detail, rings,
noise, star cores and halos across representative regions. A smaller FWHM cannot
prove recovery benefit or a constant PSF across the frame. RGB mono-PSF restoration
uses Lab luminance and does not preserve physical flux or emission-line ratios.

After Stage 4, the Agent independently assesses denoise and deconvolve, records
actual adoption or skip reasons and continues from the accepted current parent.
When denoise is adopted, rebuild the PSF from its output before deconvolution.
Old sessions retain their original computations, reports and immutable replay/finalize
permissions; no precision/display fields are backfilled and no new execution is granted.
