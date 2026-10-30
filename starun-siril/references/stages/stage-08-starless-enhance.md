# Stage 8 · 无星主体增强与窄带映射

## 保色配方

主体调色的保色与蒙版合同见[共用增色正文](../shared-protocols/color-finish.md#unmasked-first-color)，逆向曲线见[可选逆向候选](../shared-protocols/color-finish.md#inverse-saturation-candidate)；红色局部亮度见[共用亮度正文](../shared-protocols/tone-adjust.md#display-red-local-tone)。局部对比的排除与权重仍由本页[暗部排除](#protected-local-contrast)维护。

仅在已接受的非线性 starless 主体上评估多尺度/CLAHE 局部对比和窄带映射，顺序为 structure.local-contrast → color.map → [tone.adjust](../shared-protocols/tone-adjust.md#tone-adjust) → restoration.denoise-nonlinear 色度变体 → color.finish 局部饱和度/鲜艳曲线；各项可有依据地跳过。没有可信无星分支时跳过局部对比、映射与局部增色，含星图的全局曲线独立评估。宽带 RGB 默认跳过窄带映射。合星前的调整只改变 A′，保留原星层和 stretch chain。

在现有阶段决定或交付审查中分别记录局部对比、颜色与亮度的当前父源、目标、局部依据及采用或跳过原因。天空中性不证明主体无需增色；全局亮度分布重叠不自动证明局部对比无收益。无目标、无可信材料或无收益时可跳过，不强制试跑。

主体亮度整形及适用的色度降噪后评估 color.finish；目标为零、已完成、无可信材料或无收益时跳过。候选、保护、修订与审查按[共用增色合同](../shared-protocols/color-finish.md#unmasked-first-color)执行。

本文件维护 structure.local-contrast、color.map 及主体空间蒙版；共用操作按下方正文入口读取，provenance 绑定各协议唯一 primary 的完整 SHA-256。通用质量门见 [quality](../quality.md)。

tone.adjust 在局部对比／映射后、色度降噪与增色前按[剩余目标](../shared-protocols/tone-adjust.md#remaining-goal-tone)评估；主体已定调且无新问题时跳过，不为进入调色而追加。合星前仅作用于 A′，冻结星层不变。

## 形态保护

按 [目标形态参考](../target-morphology-review.md) 选择区域，局部对比仅使用原子协议已验证的亮度蒙版。

- **三裂星云与多重暗尘埃带 (如 M20, 蛇夫座 $\rho$ 暗云)**：
  - 暗裂缝是星际分子尘埃遮挡星光的真实物理现象。CLAHE 的对比度提升应聚焦于暗裂缝边缘的锐度，但不可将暗带过度压黑截断；
- **微弱外壳 (如 NGC 6888 眉月星云、NGC 7635 气泡星云)**：
  - 微弱 OIII 外部气泡包层极为暗淡。局部对比度增强可让微弱外壳凸显，但需配合背景保护掩膜，防止背景噪点被一同误增强；
- **星系旋臂与尘埃通道 (如 M51, M82)**：
  - 增强星系盘面内恒星形成区与暗尘埃带的交错立体感，星系核球区域应予以掩膜保护，避免核心过热。

---

## 故障定位

通用定位方法参见 [排查参考](../troubleshooting.md)，修订仍须符合各原子协议的算子与参数边界。

| 故障现象 | 潜在原因 | 排查与处置对策 |
|---|---|---|
| **星云周围产生刺眼的亮光晕 (Halo)** | CLAHE 的网格块（Tile Grid）过小或 Clip Limit 过大 | 核对实际块边界和亮度保护蒙版；v2 提案先降低混合 strength，cliplimit/grid 保持固定。旧手写协议才按其允许边界评估 Clip Limit。 |
| **画面出现像浮雕一样的粗糙噪点块** | 局部对比度可能放大背景噪声 | 拒绝候选，核对原子协议既有亮度蒙版的淡入/淡出与实际预览，并在其参数范围内修订；不另造未经支持的信号或 SNR 掩膜。 |
| **宽带彩色数据被做成了古怪的绿金色彩** | 误对普通 RGB 数据执行了 SHO 窄带映射 | 撤销 `color.map`，宽带彩色数据严禁套用窄带色板，必须退回物理真实 RGB 色彩。 |
| **调色后暗部出现不自然色偏** | 曲线调整未锁定暗部锚点，暗黑背景发生通道偏离 | 使用 `tone.adjust` 时确保曲线在黑点处汇聚于 $(0, 0)$，若偏色源自线性校色，退回对应阶段排查，不在 Stage 10 强制对齐通道。 |

<a id="structure-local-contrast"></a>
## structure.local-contrast


Stage 8，只处理已接受分星分支中、审查通过的 nonlinear starless 主父源；没有可信分支或无明确
局部对比改善目标时跳过。按 [目标形态审查参考](../target-morphology-review.md) 选择亮核、目标
边缘及微弱外缘，以实际当前亮度选择蒙版边界和参数，不把默认值当通用配方。

<a id="multiscale-competition"></a>
### v21 多尺度与 CLAHE 同父源择优

新会话冻结 `local_contrast=wavelet_clahe_competition_v2`。宽带 RGB 和已接受的 HOO 主体默认从同一可信非线性无星父源评估小波与 CLAHE，保留原 RGB 星点链和已接受 palette。没有可信天空、有效纹理测量、分星谱系或合格候选时保留父源；低纹理比值本身不阻止新会话试跑。新会话另冻结 `local_contrast_hdr=wavelet_only_v1`：HDR 谱系允许评估小波，CLAHE 仍在 Siril 启动前拒绝；未带该标签的历史会话保持 HDR 与两种方法互斥。当前谱系已有任一局部增强时拒绝再次处理，其他中间协议不能解除这些限制。

使用原生 `wavelet 6 2`（五个带符号细节层加残差）和 `wrecons`。Agent 默认用 v3 请求（字段沿用 v2）显式提议有限幅度 `0 < amplitude <= 1.5` 与布尔值 `fine_detail`。开启时六个系数为 `(1+0.02t, 1+0.04t, 1+0.12t, 1+0.08t, 1, 1)`；关闭时前两项为 1，尺度 3–4 仍按同一 t 增强。Agent 根据当前局部预览、纹理和高频证据决定是否保留细尺度，不把更强反差自动视为更优。第 5 层与残差不直接增益，不开放独立系数或实现 MMT。Siril 1.4.4 七平面重建会记录 out-of-bounds 但可能返回 0，故拒绝七平面，必须同时检查日志、单位重建和实际加权像素。旧 v1 请求保留 t=0.5/1/1.5 的三档试跑。

显式方法在 `# local-contrast:` 中声明 `method=wavelet|clahe`，无 method 的旧声明仍为原 CLAHE。新变体使用 RGB 加权亮度和同一正增益；高光淡出依据 `max(R,G,B)`，保护已接受的 HOO 亮色结构，平滑尺度固定 sigma=2。小波混合强度为 1，幅度已体现在系数中；v2/v3 CLAHE 由 Agent 显式提议有限混合强度 `0 < strength <= 0.5`，cliplimit=1.5/grid=8 固定，v1 仍使用 strength=0.20。禁止裁零、峰值归一化、各颜色通道独立锐化或重新 HOO 映射。

现有辅助模块提供轻量 `solve/generate`；Agent 声明真实 ROI 与四个蒙版边界，不从全图亮度猜天空。输入 request：

```json
{"schema":"starun-siril.local-contrast-request.v3","source":"artifacts/075-starless.fit","regions":"reports/080-regions.json","mask":{"low_start":0.03,"low_end":0.08,"high_start":0.65,"high_end":0.9,"sigma":2},"wavelet":{"amplitude":0.4,"fine_detail":false},"clahe":{"strength":0.15},"manual_lookup":{"status":"not_needed","reason":"Complete native recipe from this protocol","evidence":[]}}
```

以上幅度、细尺度开关、混合强度和边界仅为格式示例，必须按当前科学父源实际天空、主体、亮部与局部预览选择；在现有 provenance 参数理由及 review.notes 中记录依据。v2/v3 不扫描幅度或自动重试，每种方法只执行一个明确提案。regions 沿用现有 v1 合同，至少三个不重叠天空框和一个 faint 框；全分辨率 sigma=16 大尺度诊断另要求每个天空/faint 框周围有真实 64 像素上下文。新会话以未经舍入的 texture_snr 记录纹理风险，低于 1.7 仍可进入有限候选比较。系数、蒙版、ROI、父源、运行时、脚本与试跑日志均冻结；完整试跑保留在 reports，不作为已接受科学父源。

```bash
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_local_contrast.py solve \
  --session /abs/session --request /abs/session/reports/080-request.json --run-id 080-compare
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_local_contrast.py generate \
  --session /abs/session --parameters /abs/session/reports/080-compare/parameters.json --candidate wavelet
```

v2/v3 普通分支候选键为 wavelet/clahe；新标签的 HDR 分支仅为 wavelet。v1 保留原三档小波，HDR 下同样只排除 CLAHE。请求字段和全部参数仍完整校验，不为被排除的 CLAHE 执行试跑或生成科学产物。报告 method_eligibility 绑定 HDR 祖先与各方法资格，加载时重新计算并核对实际候选集合；缺失、追加或伪造资格均拒绝。generate 为选定候选生成正式 SSF/provenance 和 expect 清单，不自动接受。正式声明另用 `# local-contrast-parameters: {"path":"reports/080-compare/parameters.json","candidate":"wavelet"}` 绑定实际试跑。SSF 的科学命令由模块的同一原生骨架生成并逐字验证：父源→split→L→六平面/单位重建/带符号层图→加权重建→峰值保护平滑蒙版→共同正增益→RGB 合成→重载 primary→直接预览。Python 只生成脚本、测量和诊断布局，科学图像始终由 Siril 写入。

v2/v3 可在初次提案中关闭细尺度；若初次开启后因高频背景问题被实际拒绝，只允许一次同源同 ROI/蒙版修订：solve 使用新 run-id，并传 `--fallback-from 原parameters.json --reason 实际高频拒绝依据`，保留原幅度、蒙版及 CLAHE 提案，只关闭前两层增强；仅试跑 fine-off，不重跑 CLAHE。初次已关闭则不提供再次修订。v1 保留三档小波全部实际拒绝后才允许一次 `(1,1,1.12,1.08,1,1)` 修订的原行为。不可用此入口反复试参或掩盖结构损失。

实际打开同显示父源、候选、蒙版与所有有限试跑的固定局部，分别审查细丝/暗带、弱外缘、天空、亮部与残星。以可复核的主体、细丝和弱外缘增强为首要，在通过科学完整性与结构保护后允许轻微且已记录的颗粒增加；外壳损失、明显噪声/色噪、振铃或光晕不能被更大反差抵消。仍无可靠区别时优先较温和的小波。每个算法的正式候选独立审查，仅选一个继续 Stage 8–10，不叠加两者。HDR 后仍须证明新增主体收益，重点核对壳层、细丝、亮部与过渡；方法资格、闭合或高频数值不能替代实际审查。

receipt 保存参数绑定、实际小波/CLAHE 对照、单位重建/层闭合/加权公式/common RGB gain 与试跑等价检查，以及 sigma=1/2/4/8 高频和 sigma=16 大尺度诊断。第 5 层与残差系数为 1 不证明最终大尺度完全不变，蒙版混合仍可能移动尺度能量。参数、区域、运行时、试跑、材料缺失或漂移不能通过审查/回放/finalize。材料包括三张直接预览、科学层与同父源比较局部，保持 32 项上限；报告递归绑定全部试跑证据。旧报告保留固定候选语义和原指纹，仅认可登记的精确旧 helper 或完整 Stage 7 实现集合，未知及混搭指纹仍拒绝。历史 v20 及更早策略维持原判定和命令权限。

<a id="clahe-shell-comparison"></a>
### CLAHE 0.10／0.15 与小波的同源比较

本流程用于已明确要求壳层细丝更清晰、天空无可辨粗化的普通分支；只沿用 v3 请求与现有 solve/generate，不改变通用参数默认值或质量门。当前已接受的无星科学父源为 `artifacts/070-stretched.fit`；名称不同时填写其真实路径。必须核对分星、拉伸与当前接受状态，HDR 谱系仍禁止 CLAHE，不能以本比较流程绕过资格限制。

**试跑前**固定同一份 regions.v1：指定一个角色为 faint 的壳层主 ROI，另保留其他壳层、暗带过渡、亮核及至少三个独立天空框。逐框说明真实内容，沿用区域资格、真实上下文及 sigma=16 的 64 像素上下文要求。先将主 ROI ID、来源与 ROI 指纹写入比较计划；不能看完结果再换主 ROI、合并天空或删掉退化区域。

从已填写且经实际材料支持的 v3 请求复制两份，仅令 `clahe.strength` 分别为 0.10、0.15；source、regions、四个蒙版边界、sigma、wavelet amplitude/fine_detail 和 manual_lookup 完全相同，cliplimit=1.5/grid=8 继续由现有配方固定。以下示例不会补齐未提供的参数，`shell-main` 必须替换为试跑前选定的主 ROI ID。使用当前会话冻结的同一运行时，不覆盖已有文件：

```python
from deep_sky_siril_local_contrast import request_candidates
from deep_sky_siril_contract import fingerprint
from pathlib import Path
import copy
import json

session = Path("/abs/session")
base = json.loads((session / "reports/080-request.json").read_text())
assert base["schema"] == "starun-siril.local-contrast-request.v3"
primary_id = "shell-main"
regions = json.loads((session / base["regions"]).read_text())
assert any(r["id"] == primary_id and r["role"] == "faint" for r in regions["regions"])
for suffix, strength in (("010", 0.10), ("015", 0.15)):
    request = copy.deepcopy(base)
    request["clahe"]["strength"] = strength
    request_candidates(request)
    with (session / f"reports/080-clahe-{suffix}-request.json").open("x") as stream:
        json.dump(request, stream, indent=2, allow_nan=False)
plan = {"primary_shell_roi": primary_id, **{
    key: {"path": base[key], **fingerprint(session / base[key], include_path=False)}
    for key in ("source", "regions")}}
with (session / "reports/080-comparison-plan.json").open("x") as stream:
    json.dump(plan, stream, indent=2, allow_nan=False)
```

```bash
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_local_contrast.py solve \
  --session /abs/session --request /abs/session/reports/080-clahe-010-request.json --run-id 080-clahe-010
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_local_contrast.py solve \
  --session /abs/session --request /abs/session/reports/080-clahe-015-request.json --run-id 080-clahe-015
```

每次 solve 都生成 wavelet 和 CLAHE 两份试跑，四个输出都是同一父源的 sibling。两份小波参数及科学配方相同，但各有独立运行、日志和绑定；**保留重复小波及全部证据**，不得删候选或拼接 parameters.json。试跑仅供比较，不接受为科学父源。已有 `local.png` 会缩略且只选部分 ROI，不能单独作为完整的 1:1 验收材料；面板另附整图显示尺度行，仅作整体可读性对照，不替代 1:1 判据。

下面示例在技能 scripts 目录位于 PYTHONPATH 时运行，重用报告验证、科学读取器与现有 E₂ 测量；仅读取科学像素，写一张 PNG 并输出比较记录草稿，不写 FITS。五列依次为父源、两次小波、CLAHE 0.10、CLAHE 0.15；顶部先加一行五列的显示尺度整图（长边 1024，LANCZOS 缩放，与 ROI 行同一 `[0,1]` 显示传递），随后每个已声明 ROI 占一行，裁片按原像素尺寸粘贴，不 resize/thumbnail 或逐图自动拉伸。显示统一为已拉伸科学值的 `[0,1]` 直接映射，超界只在 PNG 显示时截断；科学端点、负值和颜色审查仍用原材料。

```python
from deep_sky_siril_local_contrast import load_parameters, energies, ENERGY_FLOOR
from deep_sky_siril_artifacts import read_scientific_pixels
from deep_sky_siril_contract import fingerprint
from pathlib import Path
from PIL import Image, ImageDraw
import copy
import json
import numpy as np

session = Path("/abs/session")
report_paths = [f"reports/080-clahe-{suffix}/parameters.json" for suffix in ("010", "015")]
reports = [load_parameters(session, path) for path in report_paths]
first, second = reports
for key in ("source", "regions", "runtime"):
    assert first["bindings"][key] == second["bindings"][key]
requests = [json.loads((session / r["bindings"]["request"]["path"]).read_text()) for r in reports]
assert requests[0]["clahe"]["strength"] == 0.10
expected = copy.deepcopy(requests[0]); expected["clahe"]["strength"] = 0.15
assert expected == requests[1]
plan_path = "reports/080-comparison-plan.json"
plan = json.loads((session / plan_path).read_text())
for key in ("source", "regions"):
    assert plan[key] == first["bindings"][key]
regions = first["region_document"]["regions"]
primary_id = plan["primary_shell_roi"]
assert any(r["id"] == primary_id and r["role"] == "faint" for r in regions)
columns = [("parent", first["bindings"]["source"]["path"]),
           ("wavelet-010", first["candidates"]["wavelet"]["output"]["path"]),
           ("wavelet-015", second["candidates"]["wavelet"]["output"]["path"]),
           ("clahe-010", first["candidates"]["clahe"]["output"]["path"]),
           ("clahe-015", second["candidates"]["clahe"]["output"]["path"])]
arrays = [read_scientific_pixels(session / path)[0] for _, path in columns]
assert all(a.shape == arrays[0].shape and np.isfinite(a).all() for a in arrays)
height = arrays[0].shape[1]
pad, header = 8, 24
cell_width = max(r["rect"][2] - r["rect"][0] for r in regions)
display_edge = 1024
source_height, source_width = arrays[0].shape[1:]
scale = min(1.0, display_edge / max(source_width, source_height))
tw, th = max(1, round(source_width * scale)), max(1, round(source_height * scale))
column_width = max(cell_width, tw)
panel = Image.new("RGB", (5 * (column_width + pad) + pad,
    th + header + pad + sum(r["rect"][3] - r["rect"][1] + header + pad for r in regions) + pad), "#181818")
draw = ImageDraw.Draw(panel)
for column, ((label, _), values) in enumerate(zip(columns, arrays)):
    full = values[:, ::-1]
    rgb = np.moveaxis(np.repeat(full, 3, axis=0) if len(full) == 1 else full, 0, -1)
    image = Image.fromarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)).resize((tw, th), Image.LANCZOS)
    left = pad + column * (column_width + pad)
    draw.text((left, pad), f"full-frame display / {label}", fill="white")
    panel.paste(image, (left, pad + header))
top = pad + th + header + pad
for region in regions:
    x0, y0, x1, y1 = region["rect"]
    for column, ((label, _), values) in enumerate(zip(columns, arrays)):
        crop = values[:, height-y1:height-y0, x0:x1][:, ::-1]
        rgb = np.moveaxis(np.repeat(crop, 3, axis=0) if len(crop) == 1 else crop, 0, -1)
        image = Image.fromarray(np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        left = pad + column * (column_width + pad)
        draw.text((left, top), f"{region['id']} / {label}", fill="white")
        panel.paste(image, (left, top + header))
    top += y1 - y0 + header + pad
panel_path = "reports/080-comparison-1to1.png"
with (session / panel_path).open("xb") as stream:
    panel.save(stream, format="PNG")
amplitudes = {label: next(row["amplitude"] for row in energies(a, regions, scales=(2,))
    if row["id"] == primary_id) for (label, _), a in zip(columns, arrays)}
gains = {label: amplitude / amplitudes["parent"] if amplitudes["parent"] > ENERGY_FLOOR else None
         for label, amplitude in amplitudes.items() if label != "parent"}
record = {"primary_shell_roi": primary_id, "E2": amplitudes, "gains": gains,
    "bindings": {"source": first["bindings"]["source"], "regions": first["bindings"]["regions"],
        "parameters": [{"path": p, **fingerprint(session / p, include_path=False)} for p in report_paths],
        "plan": {"path": plan_path, **fingerprint(session / plan_path, include_path=False)},
        "panel": {"path": panel_path, **fingerprint(session / panel_path, include_path=False)}},
    "observations": {r["id"]: None for r in regions}, "selection": None, "reason": None}
print(json.dumps(record, indent=2, allow_nan=False))
```

将输出作为比较记录草稿，查看实际面板及必需材料后填写每个 ROI 的观察、选择及理由，以新文件保存为 `reports/080-comparison.json`；空值不能充当审查结论。记录同时绑定两份参数报告、比较计划、ROI、主 ROI 原始幅度及未经舍入的增益，不能只抄百分比或汇总天空结论。材料不能由脚本自动填写“可接受”。

主 ROI 使用现有 `E₂ = mean(abs(Y − Gaussian₂(Y)))`（RGB 加权亮度、真实上下文和原生像素，见下方定义）。本流程以未经舍入的 `E₂(CLAHE 0.10) / E₂(parent) > 1.05` 为工程幅度条件，等于 1.05 不通过；分母不大于既有 ENERGY_FLOOR、缺失上下文或其他不可测情况不算达到条件。该条件可能同时反映噪声变化，不能独立证明真实细丝收益，更不是物理信噪比。

选择顺序固定，其他区域的收益不能抵消退化：

1. CLAHE 0.10 严格满足上述幅度条件，实际壳层细丝更清晰，**全部天空框无可辨粗化**，其他壳层、暗带过渡、亮核等保护区域可接受，且现有质量门通过：选择 CLAHE 0.10。
2. 否则，小波有真实主体收益且全部保护审查合格：选择小波。本流程仍要求全部天空框无可辨粗化，不以通用规则允许的轻微颗粒增加放宽本次目标；从 0.10 请求的 wavelet 报告生成正式候选。
3. 两者均不可接受或无法判断：明确记录 `skip` 及逐区域原因，保留已接受的 `070-stretched`；不追加 amplitude、fine-off 或其他试参。执行或完整性失败仍按既有失败规则处理，不能伪装成无收益 skip。

CLAHE 0.15 **仅作诊断对照，不进入选择顺序**。选定后才从 `reports/080-clahe-010/parameters.json` 对应的 `clahe` 或 `wavelet` 执行 generate，再按其清单 run/review；技术通过不自动采用。只将一个正式接受的输出送入 Stage 9，两种方法不叠加；skip 时直接沿可信父源继续。

审查保留全部既有必需材料，并直接加入第二份 parameters.json、统一 1:1 PNG 和填写完成的比较记录；比较计划和两次完整试跑经比较记录／参数报告递归绑定，不逐个追加为 inspected_materials。当前 v3 wavelet 的原必需材料为 29 项，加上述 3 项恰为 32；CLAHE 为 22+3=25 项。以实际 review-check 骨架去重计数为准，仍遵守 32 项上限，不能删必需材料腾位置。正式 review.notes 指向比较记录，说明选择顺序、主 ROI 增益、逐天空与保护区观察；统计量和该文档指引均不替代 Agent 审查实际候选像素。

<a id="protected-local-contrast"></a>
### v3/v4 暗部排除与平滑偏好

v3 与 v2 使用相同字段和修订限制；普通分支有两个初次候选，新标签的 HDR 分支只评估小波。科学原始蒙版 M0 仍由当前父源的亮度淡入及 max(R,G,B) 高光淡出定义；新 `local_contrast_mask=minimum_source_support_v1` 会话的最终蒙版为 `min(M0, Gaussian_2(M0))`，与调色模块的最小值合成一致。保存 mask-base、mask-smoothed、mask 三层，正式声明为 `mask_support="source_exclusion_v2"`，由求参报告绑定，不能手工给旧报告更换标记。v4 保留空间主体权重相乘及高光保护，不增加亮度淡入。旧会话的 `source_exclusion_v1` 继续使用 `M0 * Gaussian_2(M0)`，v1/v2 请求保持原来的单次平滑配方。

复测原始蒙版、最小值合成、科学排除像素及与原生试跑等价；新回执以 `mask_minimum` 记录公式误差。对同一父源及边界，可直接以 mask-base 和 mask-smoothed 对照调色配方；在 `[0,1]` 内最小值不低于乘积，均匀软权重 w 保留为 w，避免弱结构增强被额外衰减成 w²。M0=0、空间权重为 0 或最终权重为 0 的像素逐像素保持父源值；乘法增益为 1、增强量为 0，不是把 RGB 乘以 0。v3 的边界仅指亮度/高光排除集，v4 另保留空间排除集；不能把其余低亮度像素自动当作天体外背景。

用户偏好纯净平滑时，可先提议 t=0.4、fine_detail=false，系数 `[1,1,1.048,1.032,1,1]`；这是例子，不是通用档位。关闭细尺度不保证零噪声，也不能仅凭尺度编号宣称增强了某种天体结构。比较局部细丝、暗带、过渡及高频代价后采用。

纹理诊断按主体和天空 ROI 评估，不是全图信噪比或物理信噪比。新会话记录背景纹理基准、全部主体比值及低纹理告警，不以最弱区域自动否决候选；结合 sigma=1/2/4/8 高频和 sigma=16 大尺度诊断检查实际主体收益。弱外壳同样保留在审查中，不删除弱结构或把主体改标天空。旧会话继续执行原 1.7 硬门。

选择性青色饱和度与红色局部亮度默认关闭。仅在用户目标与局部证据需要时，按共用 tone.adjust 的红色变体、再按 color.finish 的青色选择器分别执行和审查；已接受 HOO 不重新 color.map，颜色选择区不等同于真实谱线。

<a id="structure-local-contrast-参数与声明"></a>
### 参数与声明

四个蒙版边界必填，满足 `0 <= low_start < low_end <= high_start < high_end <= 1`：
`low_start` 至 `low_end` 让暗部平滑淡入，`high_start` 至 `high_end` 让高光平滑淡出。
边界与分母必须一起替换并写入 SSF 注释和 provenance；不能从全图 median 自动判断背景。

以下为无 method 的旧 CLAHE 声明，v2/v3 提案的 cliplimit/grid/sigma 仍固定。`# local-contrast:` 是单个严格 JSON 对象；新策略另须声明 `regions`，见下方纹理门；以下四项可独立省略，省略时使用原默认值。grid 必须是
JSON 整数，其他字段只接受有限 JSON 数值；拒绝布尔值、字符串、重复键、未知字段和重复声明。

| 字段 | 默认值 | 工程允许范围 | 选择依据 |
|---|---|---|---|
| cliplimit | 1.5 | 0.5–5 | CLAHE 对比限制；检查均匀区域的噪声放大，避免以更强局部反差伪造细节。 |
| grid | 8 | 整数 2–32 | 每轴分块网格，不是像素半径；按目标尺度和图像尺寸评估块状/边缘伪影。 |
| strength | 0.20 | 大于 0，且不超过 0.5 | 同一增益的混合强度；默认只生成一个保守候选，无收益则跳过。 |
| sigma | 2 | 0.5–16 | 蒙版 Gaussian 平滑尺度（像素）；检查目标边界过渡与保护区是否被过度扩散。 |

范围只定义执行允许边界，不保证任何组合适用于当前目标。改变声明时须同步更新 `clahe`、`gauss`
与增益表达式的对应系数；执行器核对实际 SSF 并将补齐默认值后的参数写入 `local_contrast` receipt。
旧的仅四边界声明保持原默认行为。

<a id="local-contrast-texture-and-hf-gates"></a>
### HDR 互斥、纹理诊断与高频审查

v14–v20 冻结 `local_contrast=texture_hf_review_v1`；v21 继承下述资格和高频审查。当前已接受科学父源的实际谱系包含
Stage 7 `hdr_transfer` 时，CLAHE 在 Siril 启动前被拒绝；新标签仅允许实际小波候选，手写 SSF 同样核对原生配方与试跑绑定，不能伪装方法绕过。历史会话仍拒绝两种方法；中间调色、曲线或降噪不能解除互斥。
其他分支或未采用的 HDR 不影响当前分支。阻断后保留父源，不自动改跑 HDR。

声明中的 `regions` 必须是 session 内 reports 下现有 `starun-siril.regions.v1` 文件，reference
绑定当前父源 SHA-256、width、height；坐标仍为预览左上起算、零基、半开矩形。至少三个互不
重叠的 background ROI 和一个代表待增强微弱纹理的 faint ROI，每区至少 64 个有限像素。
core/stars 用于明确排除，不能用亮核替代 faint 纹理评估。真实天空与目标角色须逐框审查，
不能把低亮度、分位数、四角或自动蒙版当作天空证明；没有可信天空时跳过。

测量保持原分辨率和科学单位，以 `L=0.2126R+0.7152G+0.0722B`、
`E_sigma(ROI)=mean(abs(L-G_sigma(L)))` 定义高频幅度。float64 可分离 Gaussian 核在 4 sigma
处截断，读取真实上下文，不缩小、补边或使用 JPEG。最大 sigma=8，因此测量 ROI 的 32 像素
上下文必须完整位于画幅；background 上下文不得碰 core/faint/stars，faint 本体不得碰 core/stars。
这不是 Stage 3 的布点合同，不要求六个天空 ROI 或背景拟合覆盖。

执行前固定 sigma=2 原图像素，与蒙版 sigma 独立；
`texture_snr=min_i(E_2(faint_i)/median_j(E_2(background_j)))`。新会话冻结
`subject_enhancement=benefit_first_v1`，比值低于 1.7 记录 `low_texture_ratio` 告警，
小波与 CLAHE 仍可按原有限提案试跑，不通过改标区域获得资格。低纹理时优先考虑关闭细尺度
和较小幅度，以实际主体反差、弱外缘连续性及背景代价决定采用；不以更少像素变化排序。
分母不大于 1e-12、无效 ROI、非有限值或来源漂移仍不放行。没有新标签的旧会话保留
未舍入比值小于 1.7 的前置硬门。该比值是工程纹理诊断，不是物理信噪比。

执行后直接读取 Siril 科学候选，在相同 ROI 的 sigma=1/2/4/8 像素尺度报告
`hf_amplification=E_sigma(candidate)/E_sigma(parent)`，分别列出全部目标与天空区域。
任一区域/尺度大于 1.5x 记录告警并要求实际审查，不自动拒绝或接受；该阈值尚未在真实低信噪比
Siril 母版上标定。分母不大于 1e-12 或无法计算时证据为 uncertain，不能 accept。有限像素、
几何、既有完整 WCS 和既有科学裁剪门继续验证，不改变科学容差。

review-check 骨架和验证器共享材料解析：父源、候选、三个 direct 预览、全部科学衍生材料、
ROI 合同和 run receipt 必须绑定。查看相同显示传递和 1:1 局部，检查蜂窝、网格边界、周期
条纹、星核/星晕、真实细丝与噪声；notes 记录受影响 ROI、尺度及实际对照。structure 和
background 两门必须实际通过，可见退化 reject，证据不足 uncertain。验证器不解析自由文本，
骨架仍保持空 verdict/gates/notes；数值通过不证明纹理真实，也不保证完全消除假结构。

另需一张显示尺度对照（长边 1024 px）。用 `scripts/deep_sky_siril_display_scale.py prepare --session … --source <候选 JPEG> --reference <父源 JPEG> --run-id <新编号>` 从本 run 已提交的直接预览生成 `reports/<run-id>/display-scale.png`，父源与候选同尺度并排；仅该 PNG 计入 `inspected_materials`，同目录 `display-scale.json` 落盘但不计材料。它继承两份直接预览的显示传递，不另做拉伸、不读科学像素；只判断典型观看尺寸下主体是否仍成立、是否只剩色斑或灰面。该图不产生 ROI 坐标，不得用于选择科学 ROI，不替代 1:1 局部与同尺度面板，也不重复 Stage 10 的全图跨度告警。CLAHE 对照流程（32 项已满）不新增文件，改用 `080-comparison-1to1.png` 顶部那行；该行与同面板 1:1 裁片使用同一显示传递，ROI 行仍按原像素不缩放。

静态验证绑定来源、区域、谱系、纹理测量和预览角色，receipt 保存候选高频报告；回放、审查、
finalize 按冻结算法重算。无新标签的历史记录沿用原规则并只读回放/交付，不授予新增执行权限。
后续协议沿当前谱系核验 CLAHE 的 accepted review，不能通过映射、调色或交付间接采用未接受候选。

<a id="ssf-knowledge-and-skeleton"></a>
<a id="structure-local-contrast-ssf-knowledge-and-skeleton"></a>
### SSF 知识关系与示例

本节定义已验证算术；构造变体前查询冻结 `split/pm/clahe/gauss/rgbcomp` 语法。每份生成 FITS 列为 `--expect`，RGB 合成为 `--primary-output`。旧 CLAHE 骨架由 Agent 编写；Python 验证操作顺序。v21 助手可生成上述严格变体的 SSF，均不修改科学像素。示例边界与蒙版分母须按实际证据一并替换；可用等价数字格式，操作顺序及公式必须匹配。

```ssf
requires 1.4.4 1.5.0
set32bits
# local-contrast: {"low_start": 0.03, "low_end": 0.08, "high_start": 0.65, "high_end": 0.90, "cliplimit": 1.5, "grid": 8, "strength": 0.20, "sigma": 2, "regions": "reports/080-local-contrast-regions.json"}
load "/abs/current-starless.fit"
savejpg "/abs/session/previews/080-parent" 95
split "/abs/session/artifacts/080-r" "/abs/session/artifacts/080-g" "/abs/session/artifacts/080-b"
pm "0.2126 * $artifacts/080-r$ + 0.7152 * $artifacts/080-g$ + 0.0722 * $artifacts/080-b$"
save "/abs/session/artifacts/080-l" -chksum
clahe 1.5 8
save "/abs/session/artifacts/080-l-enhanced" -chksum
pm "(min(1, max(0, ($artifacts/080-l$ - 0.03) / 0.05))^2 * (3 - 2 * min(1, max(0, ($artifacts/080-l$ - 0.03) / 0.05)))) * (1 - min(1, max(0, ($artifacts/080-l$ - 0.65) / 0.25))^2 * (3 - 2 * min(1, max(0, ($artifacts/080-l$ - 0.65) / 0.25))))"
gauss 2
save "/abs/session/artifacts/080-mask" -chksum
savejpg "/abs/session/previews/080-mask" 95
pm "1 + 0.20 * $artifacts/080-mask$ * ($artifacts/080-l-enhanced$ - $artifacts/080-l$) / max($artifacts/080-l$, 0.000001)"
save "/abs/session/artifacts/080-gain" -chksum
pm "$artifacts/080-r$ * $artifacts/080-gain$"
save "/abs/session/artifacts/080-r-enhanced" -chksum
pm "$artifacts/080-g$ * $artifacts/080-gain$"
save "/abs/session/artifacts/080-g-enhanced" -chksum
pm "$artifacts/080-b$ * $artifacts/080-gain$"
save "/abs/session/artifacts/080-b-enhanced" -chksum
rgbcomp "/abs/session/artifacts/080-r-enhanced.fit" "/abs/session/artifacts/080-g-enhanced.fit" "/abs/session/artifacts/080-b-enhanced.fit" -out=/abs/session/artifacts/080-local-contrast
load "/abs/session/artifacts/080-local-contrast.fit"
stat main
savejpg "/abs/session/previews/080-local-contrast" 95
close
```

仅亮度执行 CLAHE。smoothstep 暗部淡入与高光淡出乘积经模糊后，对三通道施加同一增益；禁止独立 RGB 均衡、裁剪或 HDR。检查噪声放大、细丝、宽光晕、亮核与颜色；无明确改善 reject，保留诊断层与已接受 starless 父源。

Siril `rgbcomp -out` 写出合成图但不加载；统计或预览前重载 primary，确保审查材料对应科学候选。

<a id="structure-local-contrast-审查与回退"></a>
### 审查与回退

将 `previews/080-mask.jpg` 和最终预览连同全部科学 FITS 列入 `--expect`，实际打开并在 review
材料中绑定 SHA-256；蒙版 JPEG 是直接显示，不作 autostretch，也不能证明科学像素数值边界。
核对原亮度、增强亮度和蒙版来源，保留全部诊断层。Python 可生成严格变体脚本并测量，不生成或增强科学像素。

- 对照已接受父源与候选的相同位置，检查亮核层次、细丝、尘埃带及微弱外晕；
- 打开蒙版检查暗部淡入、高光淡出、目标边缘平滑过渡及实际保护覆盖，不能仅凭最终图声称保护生效；
- 检查背景噪声放大、块状边界、宽光晕、塑料感、颜色过渡及通道裁剪，不因局部反差更大就接受；
- 蒙版不可可靠查看或目标细节无法确认时写 uncertain；新增伪影或无明确改善时 reject，均保留已接受父源。

若有具体可修正原因，可从同一父源生成新编号修订，记录改变的边界/参数及理由；不在拒绝的
候选上叠加第二遍增强，不另外加入 gamma、HDR 或独立 RGB 均衡。审查通过仍须在后续合星后
复核星点和目标结构；诊断与目标保护参考实际影响参数选择时记录为 supporting references。

<a id="color-map"></a>
## color.map


<a id="color-map-适用条件"></a>
### 适用条件

承接 [Stage 1 配色路线](stage-01-input-inspect.md#palette-route-selection)，只在用户显式选择窄带配色后评估，未选择时保留输入配色。只处理已接受的单个三通道 starless 非线性父源。执行窄带映射时，父源须为 narrowband/dualband，
`source_roles` 必须来自冻结的用户或采集证据；不读取多个外部通道文件拼接父源。没有可靠角色时
跳过窄带映射，其他 Stage 8 协议仍按各自前置条件评估；没有可信 starless 分支时跳过本协议。
宽带自然 RGB 路线跳过 `color.map`，沿用 Stage 4 已接受的校色状态；下方 identity 仅保留显式透传兼容入口，不为阶段编号创建运行。定向模式同样遵循图像域、分支和角色边界。

双窄带 OSC 去拜耳后的 R/G/B 不自动等于纯 Ha/OIII 通道。滤镜名称或画面色调不足以建立
`source_roles`；须有实际通带与提取/组合来源证据。未提取的混合通道不能直接套用下方 HOO
骨架；没有 SII 来源不能宣称 SHO。若目标是自然宽带颜色，本协议不能恢复未采集的光谱信息。

当前父源已是所选目标映射时跳过，不重复配色。真实 SII/Ha/OIII 可按用户目标选择 SHO，或由 Agent 根据实际颜色分区、金色/青蓝结构与层次选择下方受限 modified-SHO 参数；不固定套混合系数、不默认去绿。需要比较时从同一个角色清楚的已接受父源生成候选，只采用一个目标配色，不串接 SHO 再次映射。原生 OSC 的 Stage 7 HOO 主输出已完成显示配方，继续保留执行器禁止后续 `color.map` 的限制。

SHO 将 SII→红、Ha→绿、OIII→蓝，是显示颜色的指定映射，不是肉眼自然色，见 [NASA 配色说明](https://www.nasa.gov/image-article/orion-of-different-color/)。Stage 8 真实通道 HOO 与 Stage 7 OSC 的 `G=0.8O+0.2H` 估计配方分别保留，不混称为同一来源或纯谱线结果。

<a id="color-map-映射"></a>
### 映射

- HOO：`R=Ha,G=OIII,B=OIII`；
- SHO：`R=SII,G=Ha,B=OIII`；
- modified-SHO：`R=(1-a)SII+aHa`，`0<=a<=0.5`；
  `G=(1-b)Ha+bOIII`，`0<=b<=0.7`；`B=OIII`；
- identity：对于自然宽带颜色目标（`channel_mode: broadband`），默认跳过并由下游引用当前父源；有明确记录需求时可 `load` 当前父源、直接 `save` 科学副本及预览，不改变像素或宣称窄带调色。无需为阶段编号连续而执行无收益步骤。

<a id="color-map-ssf-知识关系"></a>
### SSF 知识关系

适用性、source roles 和映射参数来自冻结的用户/采集来源及当前三通道父源证据；本协议对应的阶段文件是 SSF 的 primary
协议正文，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `split/rgbcomp/pm` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定
流水线。下方 HOO 完整示例可按实际角色参数化并记录 manual lookup `not_needed`；modified-SHO PixelMath
或其他未展开映射必须先查询原文并保留 evidence。

<a id="color-map-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

以下是 `source_roles R=Ha,G=OIII,B=OIII` 的 HOO 示例；其他角色映射必须替换实际 split 通道，不能照抄
文件名：

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
split "/abs/session/artifacts/080-red" "/abs/session/artifacts/080-green" "/abs/session/artifacts/080-blue"
close
rgbcomp "/abs/session/artifacts/080-red.fit" "/abs/session/artifacts/080-green.fit" "/abs/session/artifacts/080-green.fit" "-out=/abs/session/artifacts/080-hoo.fit" -nosum
load "/abs/session/artifacts/080-hoo.fit"
stat main
savejpg "/abs/session/previews/080-hoo" 95
close
```

把 `split` 实际生成的 R/G/B 文件按 `source_roles` 代入 `rgbcomp`。modified-SHO 先用 `pm` 生成混合
通道。Stage 7 已经完成拉伸，因此不得再次调用 `autostretch`。所有临时文件保持 session-local，并列入
预期产物或后续显式验证。

<a id="color-map-审查与回退"></a>
### 审查与回退

检查发射线结构连续性、亮部层次、背景、星色和通道裁剪。窄带映射是有来源的显示表达，不得宣称自然
色；identity 仅记录宽带父源的透传，不新增颜色校准结论。双窄带候选另检查洋红星点、青色背景及绿色/黄色结构被过度压制；Stage 8 的 starless
审查不证明星色通过，星点须在 Stage 9 合星后复核。结构断裂、颜色块或角色证据不完整时 reject。

<a id="colorfinish"></a>
<a id="color-finish"></a>
<a id="local-saturation-on-a-trusted-starless-branch"></a>
<a id="color-finish-local-saturation-on-a-trusted-starless-branch"></a>
<a id="local-ssf-skeleton"></a>
<a id="color-finish-local-ssf-skeleton"></a>
## 局部饱和度（color.finish）

在已接受的无星主体完成亮度整形和适用色度降噪后评估；目标为零或已完成时跳过。完整合同见 [color.finish 共用正文](../shared-protocols/color-finish.md#color-finish)。



v20 双窄带 stretch 的主输出已经具有 HOO 显示配方，其估计图不升级为纯谱线 source_roles；不再执行 color.map 或自动去绿。继续评估主体亮度及 vivid 调色，保留已接受配色；原 RGB 星点链冻结。

Stage 7 v22 的 ROI 保护不替代本阶段纹理门。显示分离度提高不授权局部增强；tone、vivid 和局部结构只改变已接受主体，冻结传递链不变。HDR 谱系按冻结策略判断：新标签允许小波评估并禁止 CLAHE，历史会话保持两种方法均互斥。

v23 拉伸按形态比较后，仅从实际已接受的输出继续本阶段；比较报告不自动选择父源。高阶链已冻结，tone/vivid/局部增强不重选或追加 Stage 7 拉伸；HDR 互斥不变。


## 增色前色度降噪

新会话在无星主体完成主要亮度整形后、color.finish 前评估[仅色度降噪](../shared-protocols/denoise-nonlinear.md#pre-color-chroma-denoise-chroma_bilateral_v1)。有可信红绿斑、蓝紫斑等证据才试跑；无收益、结构退化或无法判断时保留父源。此变体区别于整体 NL-Bayes，原星层及拉伸链保持冻结，合星后仍须复核实际颜色。
<a id="subject-spatial-mask"></a>
## 可选主体空间蒙版

新会话冻结 `subject_mask=reviewed_polygons_v1`。仅用于已接受的 nonlinear RGB starless 父源上的小波／CLAHE 和 GHS 饱和度增强。Agent 实际查看父源与原分辨率局部后提出包含／排除轮廓；程序只做确定性栅格化和羽化，不识别天体、不自动分割或接受结果。ROI 继续用于完整区域测量，不是增强范围。没有明确空间需求时不提供蒙版，沿用原配方。

将几何请求保存为 session 的 `reports/079-mask-request.json`，字段恰为 `schema="starun-siril.subject-mask-request.v1"`、`source`（当前 artifacts 父源的 session 相对路径）、`reference={sha256,width,height}`、`include`、`exclude`、`feather_px`。include/exclude 是多边形列表，每个多边形是 `[[x,y],...]`，至少三个不重复顶点，不重复闭合首点。坐标为预览左上角、原分辨率图像边界坐标，范围 `[0,width] × [0,height]`，程序在像素中心采样。Agent 必须将缩略图坐标还原，不能把显示拉伸当科学阈值。每类最多 64 个多边形，每个最多 256 个顶点；非有限、越界、退化、自交或重叠边拒绝。

`feather_px` 必须显式为有限正数，以原图像素计；按结构厚度选择，不继承既有 sigma=2。包含多边形向内部羽化，排除多边形向外部的允许区域羽化，权重为 `smoothstep(clamp(d/feather_px,0,1))`，d 是像素中心到最近边的距离，smoothstep(t)=t²(3−2t)。包含取 max，排除约束取 min，两者再取 min；选区外、孔洞与边界保持零。重叠轮廓不累加增强量；薄壳宽度不足时最大权重可能小于 1，必须审查实际覆盖。

运行 `"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_subject_mask.py prepare --session /abs/session --request reports/079-mask-request.json --run-id 079-mask`。返回 `subject_mask` 报告路径及 `binding={path,sha256,size}`。工具将 float32 辅助 TIFF 经 Siril 转为单通道 float32 FITS，保存全部转换 SSF／日志／配置、mask.png、overlay.png 和并排的 mask-review.png。报告和所有证据不可覆盖；prepare 成功只表示需要审查，空选区记录 skipped。科学父源、候选和图像处理仍由原协议控制，辅助蒙版永不能成为科学父源。

局部对比使用 `starun-siril.local-contrast-request.v4`：沿用 source、regions、wavelet、clahe 和 manual_lookup，新增 `subject_mask`（prepare 返回的精确 binding）；`mask` 恰为 `high_start,high_end,sigma=2`，不声明 low_start/low_end。高光边界满足 `0<=high_start<high_end<=1`。空间选区替代低亮度门，P0 为原峰值高光淡出，P=P0×Gaussian_2(P0)，最终 M=Mspace×P，随后沿用共同正 RGB 增益。不再平滑 Mspace，保证选区外不变。HDR 仍只允许小波，有限比较、单次局部增强及 fine-off 修订限制不变；同源两种算法必须使用同一蒙版。

GHS 增色按共用 color.finish 的空间变体执行。两条链都绑定直接父源和报告指纹，父源改变须重新准备，不按相同尺寸隐式重绑。零有效覆盖不生成科学候选；修订使用新 run ID 和实际观察，不覆盖已有证据。

审查必须实际查看 mask-review.png（左侧空间权重，右侧父源叠加）、最终有效权重、父源／候选及固定 ROI 局部。报告区分空间覆盖与保护后的有效权重，并核对排除像素严格不变。继续审查天空误选、弱外缘漏选、薄壳衰减、过渡、亮部与实际主体收益。并排图仅合并显示材料，独立灰度图、叠加图和全部转换证据仍递归绑定；保持 32 项审查材料上限。准备或算术成功不自动接受候选。
