# Stage 9 · 星点处理与传递合成


## 独立贡献与轮廓控制

新会话冻结 `recomposition_contour=local_peak_mtf_v1`。先分别评估星点遮挡主体的贡献目标与星点过肥的轮廓目标；有真实观察才试跑轮廓候选，不强制每次执行。`strength` 继续只控制回混贡献，范围 `[0.30,1.00]`，不能解释为星径缩小比例。独立 Fs 仍只对原始 full/starless 源对回放，HOO 沿用原 RGB 星层。

新请求使用 `starun-siril.recomposition-request.v2`，保留原字段，可增加 `"contour":{"amount":0.4,"radius_px":5}`。数值只展示格式，不是默认强度或半径。amount 必须是 `[0,1]` 内的有限数；radius_px 为正整数，`2*radius_px+1` 不超过短边。根据实际显示域星点轮廓选择，不用线性 PSF 宽度直接代替显示星径。省略 contour 或 amount=0 时沿用原贡献路径，不生成权重、中间星层或恒等候选。原 v1 请求继续支持贡献控制，不获得轮廓权限。

设 `S=Fs(full)-Fs(starless)`。RGB 使用 Rec.709 共同亮度，单色直接使用通道值；在指定半径方窗内取亮度峰值 P。任何通道为负或亮度非正的像素权重为 1，其亮度仍参与邻域峰值计算。其余像素计算 `t=L/P`、`m=0.5+0.4*amount`、`q=(1-m)/(m-(2*m-1)*t)`。所有通道共用 q，得到 `S_contour=q*S`，最终 `C=A_prime+strength*S_contour`。零强度严格恒等且连续，局部峰顶 q=1，翼部更强衰减；拥挤星场中暗星可能受到邻近亮星影响，不承诺每颗星核不变或保持物理通量。

生成器只生成脚本及辅助证据，不执行 Siril。在 `reports/<run-id>/` 独占保存 `contour.json`、float32 `contour-weights.tif` 和 `contour-weights.png`；报告绑定请求、prepared、当前主体、ROI、原始星层、运行时和完整实现。SSF 增加唯一 `# star-contour: <report-path-sha256-size-binding>`，必须与 recomposition-proposal 指向同一请求。静态验证重算权重及方向，核验全部绑定后只授权该 TIFF；未知来源、漂移或缺失立即拒绝。

正式 SSF 原样回放并保留 full_baseline、original_starless、display_stars，随后由 Siril 导入单色权重；RGB 用 rgbcomp 复制共同权重，以 PixelMath 写入 contour_stars、同贡献未缩星对照 `A_prime+strength*S` 及最终候选。RGB 额外产物为 contour_weight_rgb，单色没有；所有科学中间层均须声明 --expect。新增 rgbcomp/update_key 仅允许完整匹配的轮廓配方，update_key 仅恢复同源 EXPTIME/LIVETIME/STACKCNT，不能借此改色、裁剪、缩放或换源；新增算式使用 -nosum；其仍可能平均 EXPTIME，故保存前用原生 update_key 恢复绑定父源已有的曝光关键字，不改科学像素。

原闭合 `Fs(starless)+S=Fs(full)` 与原负残差门保持；最终算术按实际 S_contour 验证，不能要求处理后的星层重建未经处理的 full。另核验权重、RGB 复制、乘法及同贡献对照（1e-6 容差）、float32/有限性/几何、负值像素严格保留及既有 WCS/曝光元数据。保留原始与轮廓星层的有符号残差统计，不裁零、归一化或隐藏原告警。

诊断增加 contribution_geometry（完整贡献→同贡献未缩星）和 contour_geometry（同贡献未缩星→候选）。三者使用同一固定目录、亚像素星心、口径、共同背景和显示参数，不重新找星。局部面板明确标注 subject、contribution only、contour candidate、raw signed stars，星色报告保留同贡献对照；prepared 的 full-contribution 仍须实际审查。

新审查独立填写 `contribution_assessment={assessment,observation}`；轮廓候选另填 `contour_assessment={benefit,regions,observation}`，regions 为实际观察且 FWHM 或 r90 收缩的固定 stars ROI ID。accept 须贡献可接受、benefit=observed、至少一处可测收缩与实际观察一致，并且全部主体及星核、星色、星晕、端点保护通过。没有统一缩小百分比；数字下降不自动采用。不可测、无收益或损害时保留已接受结果，不称为已完成缩星。审查必须绑定并打开轮廓报告、权重图和局部对照。

旧会话沿冻结策略、文档、helper 与完整实现只读回放/finalize，不补字段或升级。轮廓候选始终从同一可信 A_prime 与 prepared 重建；修订使用新请求、run ID 和真实观察证据，不累积处理。

## Stellar color evidence for new sessions

New sessions freeze `recomposition_review=stellar_color_assessment_v2`, retaining all fixed-material review requirements. The existing `prepare/analyze/generate`, requests v1 and `regions.v1` remain unchanged. Record the independently observed stellar core, color and presence goal in `goal/parameter_reason`; no default curve or automatic parameter grid is introduced.

`analyze` and formal diagnostics share `stellar_color` evidence on the frozen ROIs/catalog: subject and full contribution, original body-curve baseline when an independent Fs is proposed, and the actual final candidate after execution. Every stars ROI retains raw RGB and per-channel endpoints/new-crossing counts, bounding rectangles and the first 32 top-left pixel coordinates even when geometry is not measurable. Reliable isolated catalog positions and sufficient fixed context additionally provide core/halo zones. Crowded, saturated, edge or insufficient samples retain explicit reasons; no candidate redetection or substitute samples are used.

Core/halo diagnostics retain raw composite values and signed net RGB/flux after subtracting the common current-subject RGB median in the outer 20% radius. Background MAD and sample coverage are recorded. Hue uses nonnegative net RGB, a positive peak and chroma above 1e-12, weighted by chroma; achromatic or cancelling directions yield null with a reason. Background structure, noise and separation leakage can bias these display statistics; they are not physical stellar colors or automatic acceptance thresholds. Science pixels are unchanged.

For RGB stars ROI rows, `recomposition_assessment.regions[].stellar_checks` requires `core`, `color`, `halo` and `endpoints`, each with `assessment` and an actual `observation`. Acceptance requires every check and protected ROI acceptable, the explicit goal met, and all existing gates/materials. Monochrome color evidence is not applicable and retains the existing assessment. Missing geometry alone does not reject a candidate; inability to judge actual image quality requires uncertain. Compare original/new full contribution before evaluating final strength. A new Fs requires observed benefit; the ordinary route and strength 1 need no artificial reduction benefit.

Use the selected stellar transform Fs on both original linear sources: `B_s=Fs(starless)`, `S=Fs(full)-B_s`, final `C=A_prime+strength*S`. Verify `B_s+S=Fs(full)` and `C=A_prime+strength*S`; C need not equal either original full replay when the subject or contribution differs. Inspect final endpoints even if Fs(full) is unclipped. Curve-induced clipping can be avoided by replaying a gentler Fs from linear sources; acquired clipping cannot be recovered. Never independently stretch or recolor the signed residual. The source-bound contour variant below may multiply it by a shared, nonnegative weight; independent saturation and halo removal remain excluded.

The outgoing policy, helpers/implementation, documents and configuration are frozen in `references/star-color-history.json`. Historical sessions replay/finalize their original reports and reviews without new fields or recomputation. The earlier recipe sections below describe the inherited body-curve route unless an explicit star request selects Fs.


## Independent star curve — current preservation policy

First run ordinary `prepare` to retain the existing full-contribution baseline and frozen linear-full star catalog. Then write an explicit `starun-siril.star-curve-request.v1` with exactly `schema`, `source`, `baseline`, `regions`, `transfer_chain`, `goal`, `parameter_reason`. Source/baseline/regions use path/SHA-256/size bindings; baseline must be the original body-curve preparation. Select the same subject/star ROIs for the final recomposition proposal.

```json
{
  "schema": "starun-siril.star-curve-request.v1",
  "source": {"path": "artifacts/subject.fit", "sha256": "<actual sha256>", "size": 123},
  "baseline": {"path": "reports/089-base/recomposition-prepared.json", "sha256": "<actual sha256>", "size": 123},
  "regions": {"path": "reports/star-regions.json", "sha256": "<actual sha256>", "size": 123},
  "transfer_chain": [{"command": "asinh", "parameters": ["-human", "5", "0", "-clipmode=rgbblend"]}],
  "goal": "Retain stellar color and core gradation in the actual baseline",
  "parameter_reason": "Explicit curve proposed after inspecting the bound baseline and star regions"
}
```

```bash
python3 scripts/deep_sky_star_reduction.py prepare --session /abs/session \
  --source artifacts/subject.fit --run-id 089-star-curve --star-request reports/star-request.json
```

Fs permits explicit MTF, Asinh, GHS and, under `guidance=stellar_stretch_flow_v28`, Modified Asinh. Replay Fs on the same linear full/starless pair: `Sd=Fs(full)-Fs(starless)`; final remains `A_prime+strength*Sd`, strength in [0.30,1.00]. Never saturate or independently stretch the signed residual itself. Body and star transfers remain separately bound; HDR keeps its body mask/transfer, HOO stars remain from the original RGB branch.

Reuse the baseline catalog without redetection. Compare original baseline, new full contribution and final mix under the same display, including FWHM, r90, peak/flux and fixed-aperture core/halo RGB, saturation, hue and clipped channels. Both additive closures and formal/prepared pixel agreement remain mandatory. Accept only observed benefit with all protected regions acceptable; otherwise retain the original prepared route. Curves cannot recover already clipped stellar cores. The optional contour variant below is separate from Fs; no halo removal is added. Historical recipes below continue to replay under their frozen policy.

<a id="stellar-curve-selection"></a>
### Select the curve from the observed stellar problem

Keep the ordinary prepared route as the baseline. If its stellar color, core gradation, halo and presence already meet the goal, retain it. Otherwise propose one explicit Fs from the original linear sources, starting with the shortest curve that addresses the observation:

| Observed goal | Candidate to assess |
|---|---|
| General RGB color preservation with a gentle reveal | Single Asinh with `-human` and `-clipmode=rgbblend`. |
| Bright stellar cores need an explicit highlight protection point | Single Modified Asinh with measured LP/SP/HP and explicit D. |
| A particular brightness interval needs more contrast | Human-luminance GHS with explicit D/B/LP/SP/HP. |
| Existing MTF is satisfactory, or monochrome brightness shaping is the goal | Retain MTF; prefer black point 0 and white point 1. |

There is no default strength, fixed factor sequence, automatic ranking or required MTF finishing step. Add a step only when the actual single-step result leaves a specific problem. A shared nonlinear MTF on RGB channels can change their ratios without clipping; Asinh/GHS-family luminance modes and `rgbblend` also require actual color and endpoint review. Do not claim one family is universally superior.

Modified Asinh uses `command="modasinh"` in the existing `transfer_chain`; its parameters must contain exactly one each of `-D=`, `-LP=`, `-SP=`, `-HP=`, `-human` and `-clipmode=rgbblend`. Require finite `0<D<=10` and `0<=LP<=SP<=HP<=1`. No B, channel selector, saturation mode, alternate color model or clip mode is authorized. D is the native Modified Asinh parameter, not an Asinh stretch factor; LP/HP define linear protection segments rather than locking brightness or guaranteeing unclipped output. Syntax and semantics come from the frozen Siril 1.4.4 `modasinh` manual. This command is authorized only for Stage 9 stellar replay, not Stage 7 or HDR body chains.

Compare the original and proposed full contributions before selecting final strength. Use the same frozen catalog, ROI, aperture, display and scale; inspect peak/flux alongside core/halo color, faint-star presence, FWHM/r90 and per-channel endpoints. A darker candidate is not by itself evidence of better color or smaller stars. If stellar brightness or presence is not comparable, disclose that tradeoff rather than claiming equal-brightness superiority; do not normalize or rescale scientific layers to force a match. Then choose contribution weight and inspect the actual final addition: unclipped Fs(full) does not prevent A_prime + strength*Sd from crossing an endpoint. Reject or uncertain results retain the trustworthy route; lowering weight cannot recover clipped data.

The pre-v28 policy, command bytes, documents and implementation are frozen in `references/star-stretch-history.json`. Existing sessions replay/finalize their original evidence without new generation or backfilled fields; new requests cannot use an outgoing helper or implementation binding.


<a id="agent-recomposition-proposal"></a>
## Agent 提案、显示域诊断与实际回混验收

新会话冻结 `recomposition_parameters=agent_proposal_v1`。Agent 根据当前已接受主体、实际星残差和完整贡献对照选择 strength；脚本负责冻结材料、生成单候选和验证证据。范围保留 `[0.30,1.00]`，没有默认值、自动夹紧、固定强度网格或两位小数舍入。`1` 表示完整贡献，不要求产生缩星收益；`0` 不在本协议范围内。

1. `prepare --session --source --run-id` 验证实际 separation/stretch 谱系，使用冻结运行时原生重放 `F(I)`、`F(A)` 和有符号 `S_d=F(I)-F(A)`，保存完整贡献对照 `A′+S_d`、统一显示预览及脚本/日志/来源指纹。HDR 使用原冻结蒙版；HOO 使用原 RGB 星点链。准备图只用于诊断，不能成为通用科学父源。
2. Agent 查看实际材料，按下方[目录坐标选 ROI](#catalog-guided-rois)为当前 A′ 的 SHA/几何选择 `regions.v1`，至少包含主体 faint/core 与代表 stars 区。星核与外晕可分别选择 stars ROI；区域角色来自观察，不由脚本识别。
3. `analyze --session --prepared --regions` 测量主体、完整贡献对照和有符号残差的亮度、逐通道颜色、贡献、负值及端点。新会话还按下方固定星表规则评估显示轮廓，不推荐强度或推断物理星晕。CLI 只输出 JSON 到 stdout，需要保留时使用下方[成功后保存示例](#save-recomposition-analysis)。
4. Agent 写 `starun-siril.recomposition-request.v2`，绑定 source、prepared、regions、显式 strength、goal、parameter_reason 和 manual_lookup；`generate --session --request --run-id` 生成一份 SSF/provenance/输出清单，再通过既有 `run --protocol stars.recompose` 执行。缺参或证据漂移拒绝；旧 source/strength 参数不能单独授权生成。
5. 实际审查当前主体／完整贡献对照／候选的统一显示、原分辨率 ROI 和带符号残差。填写 `recomposition_assessment`：绑定 diagnostics，回答 goal_status、逐 ROI 的 assessment/observation 和 adoption_reason。目标达到、全部区域可接受且既有适用门通过才采用；退化、无法判断或目标未达均保留可信恢复点。不能以更低权重或算术闭合证明更好，也不把保留无星父源当作普通会话的无星交付授权。

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_star_reduction.py prepare --session /abs/session --source artifacts/080-reviewed-detail.fit --run-id 089-recomposition-input
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_star_reduction.py analyze --session /abs/session --prepared reports/089-recomposition-input/recomposition-prepared.json --regions reports/recomposition-regions.json
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_star_reduction.py generate --session /abs/session --request reports/recomposition-request.json --run-id 090-recompose
```

请求中的 source/prepared/regions 使用实际路径、SHA-256 与 size 绑定；source 和 prepared 取自 prepare 返回值。下例强度只说明精度格式，不是建议起点：

```json
{
  "schema": "starun-siril.recomposition-request.v2",
  "source": {"path": "artifacts/080-reviewed-detail.fit", "sha256": "<actual-sha256>", "size": 123},
  "prepared": {"path": "reports/089-recomposition-input/recomposition-prepared.json", "sha256": "<actual-sha256>", "size": 123},
  "regions": {"path": "reports/recomposition-regions.json", "sha256": "<actual-sha256>", "size": 123},
  "strength": 0.654321,
  "goal": "Retain stellar presence while keeping the protected subject visible",
  "parameter_reason": "Chosen from the actual prepared star and subject observations",
  "manual_lookup": {"status": "not_needed", "reason": "Documented frozen replay", "evidence": []}
}
```

修订增加 `revision={"request":<prior-request-binding>,"run_id":"090-recompose","evidence":[<actual-prior-material-binding>],"reason":"Actual observation requiring revision"}`，使用新请求和新 run ID，保持同一 A′ 和 prepared，不将上一合星结果再次合星。输出文件随 run ID 派生，已有资产不能覆盖。手写 SSF 同样必须添加 `# recomposition-proposal: <actual-request-path-sha256-size-binding>`；执行器核对 strength、父源、原链/蒙版与输出角色。

执行器在 `reports/<run-id>/` 保存 `recomposition-diagnostics.json` 和 `recomposition-regions.png`。Python 只读科学像素、写 JSON/PNG，不写科学 FITS。统计不能独立证明星核、光晕、星色或主体保护；不可测项明确披露。统一显示不另加 autostretch；残差面板是单独标注尺度的有符号诊断。完整贡献对照使用当前 A′，不是未经增强的 F(I)。

prepare 同时保存 `signed-stars.png`：Rec.709 亮度残差以灰色表示零、红色表示正值、蓝色表示负值；绝对尺度记录在 prepared 报告。该预览最多 1600 像素，候选审查的 ROI 面板保持原分辨率。区域残差统计同时列出原始残差与实际 strength 加权贡献，数值不能替代视觉审查。

<a id="fixed-catalog-geometry"></a>
### 固定星表与显示域几何诊断

新会话另冻结 `recomposition_geometry=fixed_catalog_profiles_v1`，必须形成几何评估。`prepare` 在冻结的线性 full 上执行一次 `setfindstar reset` 和 `findstar`，RGB 使用绿色通道、单色使用通道 0，最多 500 颗；不生成 makepsf 内核，也不在候选上重新检星。prepared 绑定原生目录、设置、来源、几何、运行时、脚本、日志及完整实现指纹。HOO 仍使用原 RGB 星层来源，不从 HOO 主体检星。

Agent 查看实际材料后，为孤立星点选择 stars ROI；每个几何测量 ROI 恰含一颗有效目录星。目录不足、拥挤、饱和、重复、上下文过小或图像边缘截断时保留该区域和不可测原因，不换样本、不填零。目录只覆盖原生检测到的样本，未检出的邻星和主体污染仍由 Agent 审查。native 坐标按左上起点像素中心解释，科学读取器使用底起点，转换为 `x_array=x_native; y_array=height-1-y_native`，保留亚像素位置。

原生 `FWHMx/FWHMy` 是线性 full 的 PSF 拟合量。显示域 FWHM 是同一固定星心下的经验圆环轮廓半高全宽，不作 Gaussian/Moffat 重拟合；不能把两种域的值直接相除。比较只使用当前完整贡献对照 `A′+S_d` 与实际候选，不能用 F(I) 替换增强后的 A′。starless 用于检查残留、共同背景和主体变化，不能把其未检出星点当成零星径。

测量圆是 ROI 内以目录星心为中心的最大内切圆；像素中心以整数表示，矩形物理边界为 `[x0-.5,x1-.5] × [y0-.5,y1-.5]`。外侧 20% 半径区间从当前 A′ 估计共同背景中位数及未缩放 MAD，内部 80% 为固定测量口径。亮度使用 Rec.709，单色直接读取。按 1 像素宽圆环报告中位轮廓及实际采样半径；半高交点仅在唯一且完整时线性插值，空环不补值。报告主体口径内亮度范围，背景统计不证明没有真实结构污染。

`r90` 是该有限口径内、扣除共同背景后的 **90% 正通量包围半径**，不是完整物理星晕半径。正通量只用于该诊断定义；原始负值、负通量、科学像素与既有负残差告警保留。峰值、正负通量、逐星 FWHM/r90 比值、可测性及覆盖计数写入现有 analysis/diagnostics；候选 ROI PNG 增加共同坐标下的轮廓图，科学局部仍为原分辨率。没有通用星径或光晕接受阈值。

`recomposition_assessment` 结构不变，星区 observation 必须明确实际几何结果、限制，以及亮度贡献与几何变化的区别。降低 strength 可只降低峰值和通量，FWHM/r90 保持不变；此时只能说明贡献降低。不可测时不得宣称缩星，仍可按贡献平衡目标审查；主体、星核、星色或光晕无法判断时保留可信恢复点。技术闭合、较小比值或完整目录均不自动采用。

原贡献路径与 CLI 保持；v2 的可选轮廓变体见上方独立控制；生成、手写 SSF、执行及回放通过既有 prepared/regions/diagnostics 绑定检查同一目录与配方。原生目录和轮廓面板属于固定审查材料，仍遵守 32 份上限。没有新标签的历史会话保持原报告和 review，不补几何字段、不重新检星，只读回放及 finalize 使用登记的精确旧文档、helper 与完整实现集合；未知或混搭指纹拒绝。

<a id="catalog-guided-rois"></a>
### 按目录坐标选择初始 ROI

顺序为 **prepare → 查看冻结目录与实际图像 → 选 ROI → analyze → 冻结提案**。先复用 prepared 绑定的目录和现有解析／排除规则列出有效星，而不是先在预览随意圈小框再期待目录命中。以下示例在技能 scripts 目录位于 PYTHONPATH 时运行，校验目录指纹，只列 ID、亚像素坐标及线性原生 FWHM；不选星、不推荐强度、不生成科学图像：

```python
from deep_sky_siril_psf import parse_native_stars, eligible_stars
from deep_sky_star_reduction import bound_file
from deep_sky_siril_contract import load_json
from pathlib import Path
import json

session = Path("/abs/session")
prepared = load_json(session / "reports/089-recomposition-input/recomposition-prepared.json")
table = parse_native_stars(bound_file(session, prepared["geometry_catalog"]["table"]), spatial=True)
assert table["status"] == "measured"
fields = ("id", "line_number", "xpos", "ypos", "fwhmx", "fwhmy")
print(json.dumps({"coverage": prepared["geometry_catalog"]["coverage"],
    "eligible_stars": [{key: row[key] for key in fields} for row in eligible_stars(table)]},
    indent=2, allow_nan=False))
```

Agent 根据这些坐标与同显示实际图像，选择有代表性且孤立的星点，并明确给出矩形；口径不按固定 FWHM 倍数或默认边长生成。检查星核、拟覆盖的外晕、邻星、饱和、图像边缘以及足够的背景上下文，保留原始亚像素中心。ROI 仍使用当前 A′ 的 SHA/几何和左上起点半开矩形；不能绑定线性 full 的 SHA，不能把 native 坐标与底起点数组坐标混用。

写好初始 regions.v1 后，可运行下面的只读预检查；每个几何 stars ROI 应对原始目录恰含一颗星，该星须通过既有有效性排除，且框内无已知无效目录行。示例只报告资格，不自动换样本、扩大 ROI 或填写接受结论；正式 analyze 继续负责科学、上下文和轮廓可测性检查：

```python
from deep_sky_siril_psf import parse_native_stars, eligible_stars
from deep_sky_star_reduction import bound_file
from deep_sky_siril_contract import load_json
from pathlib import Path
import json

session = Path("/abs/session")
prepared = load_json(session / "reports/089-recomposition-input/recomposition-prepared.json")
table = parse_native_stars(bound_file(session, prepared["geometry_catalog"]["table"]), spatial=True)
regions = load_json(session / "reports/recomposition-regions.json")
geometry = prepared["geometry_catalog"]["geometry"]
reference = {"sha256": prepared["bindings"]["source"]["sha256"],
             "width": geometry["width"], "height": geometry["height"]}
assert all(regions["reference"][key] == value for key, value in reference.items())
eligible_lines = {row["line_number"] for row in eligible_stars(table)}
checks = []
for region in (r for r in regions["regions"] if r["role"] == "stars"):
    x0, y0, x1, y1 = region["rect"]
    inside = lambda row: ("xpos" in row and "ypos" in row
        and x0 <= row["xpos"] < x1 and y0 <= row["ypos"] < y1)
    members = [row for row in table["rows"] if inside(row)]
    known_invalid = any(inside(row) for row in table["invalid_rows"])
    checks.append({"region": region["id"], "catalog_count": len(members),
        "catalog_qualified": table["status"] == "measured" and len(members) == 1
            and members[0]["line_number"] in eligible_lines and not known_invalid})
print(json.dumps(checks, indent=2, allow_nan=False))
```

目录资格不证明隔离、真实星晕或轮廓可测。analyze 后不可测的区域和原因保留，不自动换星、扩框或提高 500 星上限；候选之间使用同一批固定坐标与口径，不通过换样本制造几何收益。M8-IRCUT-accept 有 500 颗目录星、414 颗有效星，但两个 40×40 stars 框各自 catalog_count=0；这说明初始选框未命中目录，不足以证明目录只覆盖最亮星或应放宽检测。无法测量时仍按原合同披露限制，仅审查贡献平衡，不宣称缩星。

<a id="save-recomposition-analysis"></a>
### analyze 成功后保存诊断

单独运行 analyze 只打印 JSON，不自动落盘；generate/run 的正式 diagnostics 另行生成并绑定。需要保留提案前数值时，用下面的 Python 调用原 CLI：捕获 stdout/stderr，退出失败或 JSON 无法解析时不创建报告，成功后独占创建新文件并保存原始 stdout 字节。stderr 保留显示，已有文件拒绝覆盖；输出目录须已存在。示例不写科学 FITS、不生成候选、不接受任何结果：

```python
from deep_sky_siril_stretch import strict_json_bytes
from pathlib import Path
import subprocess
import sys

session = Path("/abs/session")
output = session / "reports/089-recomposition-analysis.json"
if output.exists() or output.is_symlink():
    raise FileExistsError(output)
result = subprocess.run([sys.executable, "-B", "/abs/starun-siril/scripts/deep_sky_star_reduction.py",
    "analyze", "--session", str(session),
    "--prepared", "reports/089-recomposition-input/recomposition-prepared.json",
    "--regions", "reports/recomposition-regions.json"], capture_output=True)
if result.stderr:
    sys.stderr.buffer.write(result.stderr)
if result.returncode != 0:
    raise SystemExit(result.returncode)
strict_json_bytes(result.stdout, document="recomposition analysis")
with output.open("xb") as stream:
    stream.write(result.stdout)
```

保存的 analysis 已包含本次 prepared/regions 绑定，须连同来源材料保留；它不是正式候选的 diagnostics 或 review，也不自动成为任何科学父源。先查看实际材料和可测性，再冻结 source/prepared/regions、strength 及理由。不可测不是可接受的自动结论，数值或退出成功也不证明星点与主体质量。

修改前策略、文档及实现指纹保留只读回放和 finalize。旧普通链、HDR/HOO 产物与 review 不补字段、不升级；现有闭合容差、负残差告警语义和 32 份审查材料上限保持。

Stage 7 v25 可由 Agent 一次给定因子生成冻结链；本阶段仍精确重放已接受的整链及原 RGB 显示残差，不能重新生成或优化星层因子。提案已通过主体资格不等于星核、光晕和颜色已验收，原回混、闭合及实际像素审查继续适用。

使用 Stage 6 的同源分星记录、Stage 7 的冻结传递链和当前已接受的非线性主体合星。增强与 tone.adjust 只改变 A′。失败时保留可信含星父源或基线；只有用户明确选择 standalone-starless 才能跳过合星并交付无星父源。

主体沿用已接受的自然 RGB、SHO/modified-SHO 或 HOO 配色，不在合星时重新选择映射。星层沿用原分星来源和冻结显示链；窄带原 RGB 星色不等于宽带真彩星色。若要求“SHO 主体＋宽带真彩星点”，必须另有宽带输入及来源、配准支持，当前单 master 合同不提供该扩展。按实际回混预览调整贡献并复核主体、星色和光晕。

本阶段集中维护协议正文、参数、SSF 骨架及专属审查。provenance 按协议到阶段映射，以首份阶段文件为唯一 primary、其余为 supporting，绑定完整文件 SHA-256。通用质量门见 [quality](../quality.md)。

## 形态保护

代表区域参见 [目标形态参考](../target-morphology-review.md)。

- **银河银心富星野天区 (如 M8, M20, 礁湖-三裂复合区)**：
  - 对照主体可辨性、嵌入星团与星点贡献；需要调整时仅在协议范围内修改回混强度，并重新审查同一批代表区域。
- **包含著名超大亮星的星场 (如 参宿七与巫头星云、天津四与北美洲星云)**：
  - 对照父源中已有星晕，检查新增硬边、凹陷和颜色断裂；不能仅凭外观判断光晕成因或抹去它。

---

## 故障定位

按 [排查参考](../troubleshooting.md) 定位来源；以下问题均不能用切换混合模式掩盖。

| 故障现象 | 潜在原因 | 排查与处置对策 |
|---|---|---|
| **星心发黑、硬门失败或负能量告警** | 分星质量、输入来源、传递链或显示选择需核对 | 硬门失败拒绝；比例告警须实际检查主体与星层。不能仅凭负像素数量认定损伤，也不裁负值或改用 Screen。 |
| **主体颜色发白或结构变淡** | 主体泄漏、当前父源或传递选择不适合 | 对照原始 starless、差值层与当前主体；无法证明结构保留时拒绝，不以星层归零或增益补偿修复。 |
| **星色丢失或光晕异常** | 原始输入、分星或冻结拉伸已产生问题 | 定位首次出现的阶段，从可信父源修订；不在合星脚本中插入独立星层调色或未实现的掩膜操作。 |

<a id="stars-recompose"></a>
## stars.recompose


Stage 9. Requires accepted separation, explicit stretch and current nonlinear starless reviews. Pass `--separation-run` and `--stretch-run`; every source must share lineage and geometry. Automatic stretch without a complete replayable chain cannot be recomposed.

An accepted masked dual stretch instead freezes two explicit chains and a shared spatial mask;
use the [HDR replay variant](../stages/stage-09-stars-recompose.md#stars-recompose-masked-dual-hdr-replay) below. The existing additive residual and gates
apply to that complete transform, not to either branch by itself.

<a id="explicit-recomposition-sources"></a>
### Explicit accepted starless source

Stage 6 `linear_stars` is a signed intensity residual, not a binary mask or a display-ready star layer.
Stage 9 never adds that linear residual to a nonlinear parent. Siril replays the selected complete frozen stellar transform Fs on
both original linear inputs and creates `display_stars = Fs(I) - Fs(A)` before addition to A_prime.
An independently stretched `F(I - A)` is generally different and is not an authorized substitution.
Asinh can still clip colored highlights; neither its name nor arithmetic closure proves fidelity.

The auxiliary generator requires an explicit accepted nonlinear starless primary:

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_star_reduction.py generate \
  --session /abs/session \
  --request reports/recomposition-request.json \
  --run-id 090-recompose
```

The request binds the actual current source, prepared control and Agent-selected contribution.

The prepare `--source` may be absolute or relative to the session. The generator no longer selects Stage 8 by
filename or falls back to another background. The exact parent must descend from the accepted
`070-stretch`, whose source belongs to accepted `060-stars`. The caller passes its already
selected current parent. The generator verifies session/runtime/knowledge, receipt/review bindings,
scientific lineage, geometry, full transfer and HDR mask before publishing new assets exclusively.
Missing, rejected, uncertain, duplicate, drifting or unsafe evidence blocks generation without
creating or overwriting the SSF/provenance. An existing asset is a conflict; the caller replays a bound
existing receipt before generation. Historical sessions remain replay/finalize-only.

Use the source and primary paths returned by the generator when executing the SSF. The fixed
working directory and explicit file variables avoid dependence on a current-image `$T` context.
NumPy reads the saved scientific products solely for independent closure verification; it does
not generate the restored star layer or final image. Adopt only after the existing complete review.

<a id="stars-recompose-ai-morphology"></a>
### AI 观察与显式回混强度

在 Agent 引导的 [形态判断与科学测量](../target-morphology-review.md#ai-morphology-and-scientific-measurement) 流程中，默认由 Agent 生成或调整最终 `strength`，通过绑定 request 的 strength 显式传入。实际查看当前已接受同分支主体及已验证的分星/显示域材料，结合用户偏好、星场对主体的遮挡、星晕与最终回混预览，决定应保留多少星点存在感。原参数范围和科学闭合容差不变；生成器必须绑定显式提案，不补默认强度。

`strength` 控制显示域星层回混贡献：`A_prime + strength * S_d`，数值越小，星层贡献越低；它不直接指定星径，不表示星径缩小比例，也不证明物理通量或谱线比保持。明确要求保留完整星层时使用 `1.00`；星团及嵌入星团按实际主体保护审查，不由类型标签替代判断。

对于主体优先的明确风格，按实际显示域星点遮挡及保护区域提案，不使用固定起步强度或邻近网格；较低贡献仍须实际星核、颜色、光晕与主体审查，也不保证星径变小。Stage 8 选择性处理后沿用原星层，不能为配合主体青红呈现独立重调星色。

保留 `compute` 作为旧启发式参考；`target_type` 基线、通量与星晕启发式及辅助诊断不决定最终值，也不是 AI 识别结论或已标定质量门。compute 明确标记为 legacy_heuristic_reference，不授权生成。新 generate 必须通过 request 显式传入选定值，返回 strength 为实际原值；不自动调用 compute。保持冻结 target_name/target_type，不用改写类型来间接选参数。

在现有 SSF/provenance 参数理由与本阶段 review.notes 中记录最终值、选择或调整理由、实际星点与目标效果、保护对象及限制；使用自动参考时说明与最终选择的关系。先生成候选并查看最终回混预览，再决定采用。需调整时保留原候选，从同一可信父源按下方骨架编写新 run ID 的 SSF/provenance，并重新执行和审查；生成器接受显式 run ID，每份候选独立保存，已有资产不得覆盖。

v24 无星求参允许带星重放仅作诊断。检查 Stage 7 参数报告的 `full_replay_role`、`full_audit` 和 `full_prefix_audit`；其中数值不合规不能被隐去，也不代表主体失败。实际查看同链 full、原无星基线、RGB 星残差、当前主体和回混结果，披露新增星核端点、平顶、星径、光晕与颜色。可在原 `[0.30,1.00]` 范围选择贡献权重；这不能恢复冻结链已裁掉的星核，也不能保证缩小星径。最终 clipping/closure/目标保护和像素审查仍有效；无法合格时保留可信恢复点，回到拉伸候选修订，不把主体已接受当成合星自动通过。

裁切、降噪或换父源后重新核对 ROI 坐标、来源哈希及实际像素域；采用 Stage 8 增强后尤其须重新观察主体与星点关系。无明确收益、退化或证据不足保留已接受父源，不由数值或形态标签自动 accept。Agent 沿用现有参数接口，根据实际证据选择显式 strength；不从 notes 自动生成强度。

<a id="stars-recompose-frozen-transfer-and-ssf-knowledge"></a>
### Frozen transfer and SSF knowledge

This protocol section defines display-domain subtraction. Replay the selected stellar chain Fs (the stretch receipt chain when no star request is supplied) on **both** separation full source I and original starless A. Build `S_d = F(I) - F(A)`; compose `A_prime + strength * S_d`. **Agent-guided recomposition binds an explicit strength proposal** within the existing protocol range 0.30–1.00, selected as described [above](#stars-recompose-ai-morphology). The legacy calculator remains reference-only; omission never authorizes default generation. Never stretch the linear residual directly. No `min`, clipping or rescaling may conceal negative residuals.

Screen blending is not compatible with this residual: with `B = F(A)`, `Screen(B, S_d) = F(I) - B * S_d`, which generally fails to reconstruct `F(I)`. Baseline reconstruction applies when `A_prime = B` and strength is 1.0; an enhanced parent or reduced star contribution is not expected to equal that baseline. Do not independently stretch or recolor the star layer.

Bind `--source` to the current accepted nonlinear starless scientific parent (here `/abs/session/artifacts/080-local-contrast.fit`, the `A_prime` used in the final PixelMath expression). Passing the linear separation input as `--source` fails with `requires a nonlinear scientific parent`. The first `load` instead reads the exact input recorded in the separation receipt's `source_path`: here `/abs/session/artifacts/055-deconvolved.fit`; resolve `@input` to the immutable session input. Do not substitute the separation output copy `060-full-source.fit`, even if its pixels match.

For contribution-only recomposition, declare all four scientific outputs and the JPEG with separate `--expect` arguments; set `--primary-output /abs/session/artifacts/090-recomposed.fit`. Paths for `load` and `save` are absolute. PixelMath paths are session-relative without extensions. The example uses a single MTF; substitute exactly the recorded ordered chain on both loads. Consult frozen command manuals for unexpanded chains and retain evidence.

```ssf
requires 1.4.4 1.5.0
set32bits
# recomposition-proposal: {"path":"reports/recomposition-request.json","sha256":"<actual-request-sha256>","size":123}
load "/abs/session/artifacts/055-deconvolved.fit"
mtf 0 0.01 1
save "/abs/session/artifacts/090-full-baseline" -chksum
close
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.01 1
save "/abs/session/artifacts/090-original-starless" -chksum
close
pm "$artifacts/090-full-baseline$ - $artifacts/090-original-starless$"
save "/abs/session/artifacts/090-display-stars" -chksum
close
pm "$artifacts/080-local-contrast$ + 1.0 * $artifacts/090-display-stars$"
save "/abs/session/artifacts/090-recomposed" -chksum
stat main
savejpg "/abs/session/previews/090-recomposed" 95
close
```

[tone.adjust](../shared-protocols/tone-adjust.md#tone-adjust) may adjust the accepted nonlinear starless A′ before this protocol.
Use that adjusted scientific primary as `--source`; replay only the original `--stretch-run` chain
on the separation sources. The separate `tone_chain` does not alter F, the display-star residual,
or the closure/negative-residual gates. Tone after recomposition instead applies to the whole image.

<a id="stars-recompose-review-and-fallback"></a>
### Review and fallback

The current runtime checks the decoded scientific layers per channel in `deep_sky_siril_processing.validate_processing_outputs` and records `processing_checks.closure`:

| Check | Accepted boundary | Receipt field |
|---|---|---|
| Unenhanced baseline closure | `max(abs(F(A) + S_d - F(I))) <= 1e-5` | `per_channel_max_abs` |
| Final candidate arithmetic | `max(abs(candidate - (A_prime + strength * S_d))) <= 1e-5` | `per_channel_final_max_abs` |
| Minimum signed residual | `min(S_d) >= -0.10` | `per_channel_min_residual` |
| Negative energy ratio review trigger | `sum(max(-S_d, 0)) / (sum(abs(S_d)) + 1e-12) <= 0.005`; `<= 0.010` for the frozen `galaxy` or `cluster` target type | `per_channel_neg_energy_ratio`, `max_allowed_neg_energy_ratio` |

The baseline closure limit and the minimum signed residual remain hard limits in scientific pixel-value units.
The negative energy reference ratios remain 0.005 (0.50%) and 0.010 (1.00%) for frozen galaxy/cluster targets.
Under subject_roi_residual_v2, exceeding a ratio records a warning requiring actual review on every branch,
including enhanced parents and reduced star strength; it does not reject solely on that statistic.
V1 historical receipts keep their original hard ratio gate. The ratio depends on the display transform
and is not a universal measure of target damage. Negative-pixel prevalence alone does not establish loss.

The runtime also measures the actual candidate against A_prime + strength * S_d per channel with tolerance 1e-5.
Missing layers, geometry changes, nonfinite values, failed baseline/final arithmetic or minimum residual below -0.10 reject.
Receipt evidence records raw shadow/highlight counts and new crossings relative to the current parent and full baseline;
existing scientific clipping recommendations remain. These technical tolerances do not prove physical fidelity.

Under `recomposition_review=fixed_materials_v1`, the review skeleton always binds the same roles,
independently of warnings: scientific inputs loaded for replay, full-stars baseline, original
stretched starless, signed display-star layer, current A_prime, candidate FITS and preview, the
immutable run receipt, and a valid metric report. A single-chain run normally has ten unique
materials; HDR retains all additional declared images. Ten is not a maximum or an exact-count gate.
The skeleton is sorted, deduplicated and hash-bound. Accepted reviews must inspect every required
role; additional valid materials remain allowed. Reject/uncertain reviews may retain partial observations.

The metric role prefers `reports/RUN_ID/metrics.json`, otherwise an existing valid
`metrics-recovery.json`. Schema, run/protocol and parent/candidate associations are checked;
recovery evidence additionally retains its receipt and input fingerprint bindings. An uncertain
measurement is valid diagnostic evidence, never an automatic acceptance. Missing or invalid reports
block skeleton generation and acceptance under the new policy, with the existing metric recovery
command in the error. `review-check` never creates or overwrites reports. Unsafe paths and read drift
fail closed. Historical reviews retain their frozen rules, without backfilling materials.

An accepted warning review must bind the immutable run receipt, full-stars display baseline, original stretched starless,
current A_prime, signed display-star layer and actual candidate. All five gates must pass.
Open matching direct/diagnostic comparisons and inspect the target core, outer structure, companions, independent sky,
stellar cores/halos and every flagged channel's minimum/negative-energy locations. Explain the observations in notes.
No review means review_required; warnings never automatically accept. Reject/uncertain retains the trustworthy parent
and evidence; no standalone-starless delivery without explicit session intent.

The positive-part residual `S_d_plus = max(F(I) - F(A), 0)` is not an authorized variant. It changes closure by `F(A) + S_d_plus - F(I) = max(-S_d, 0)`; disclosing negative mass does not restore the discarded signed residual or validate target leakage. Preserve the signed residual and apply the separate checks above; never clip it to make a candidate pass.

For an explicitly requested starless final image, initialize with `--stars standalone-starless`, skip recomposition, and render the accepted nonlinear starless primary via `delivery.render`. Set `stars_required=false` and `output_contains_stars=false` in final selection; all applicable scientific and visual reviews must still pass. The session intent is frozen: an existing `adaptive/preserve` session requires a new session with the requested starless intent. Failed recomposition artifacts cannot be adopted or included in selected lineage; use the accepted starless branch instead. See [delivery](../delivery.md).

<a id="stars-recompose-完整失败诊断"></a>
### 完整失败诊断

新会话冻结 star_diagnostics=subject_roi_residual_v2。先测量所有可测通道再统一判断；负能量比例超限进入
warnings，硬门原因进入 violations。保留原有三个 per_channel 数组、目标类型、参考限值、channels、
layer_shapes 与 read_errors；negative_energy_ratio_role=review_trigger 明确其用途。
新增 per_channel_final_max_abs、逐通道 max_abs_final_composition、endpoint_counts 与
new_endpoint_crossings，分别核对最终公式并定位端点变化。技术成功仍为 run success，正式采用依赖 accept。
失败证据保存在原 receipt，不补写或重新解释历史失败记录。

每个通道记录实际越界原因、非有限像素数量、negative_pixel_count、minimum_residual_position，以及固定 8×8 的 negative_energy_grid。分区 rect 使用预览左上角零基、右/下边界不包含的坐标；小图的空分区省略。negative_energy 是有限带符号残差的负部分绝对质量，不是该分区的质量阈值，也不代替全图负能量比例。

通道的 status=measured 表示可测，不代表通过质量门；硬门越界列于 reasons/violations，比例超限列于 warnings。几何或读取失败、非有限像素以 uncertain 和明确原因记录；可用部分的数值不能冒充完整测量，无法测量的值为 null。位置与分区帮助定位待检查星区/主体，不自动识别目标损伤，也不授权负值裁剪。

<a id="stars-recompose-masked-dual-hdr-replay"></a>
### Masked dual HDR replay

For `--stretch-run` carrying `hdr_transfer`, use
`F_M(X)=M*F_gentle(X)+(1-M)*F_faint(X)` on both separation sources. Read the **same** Stage 7 mask
from its recorded output path/fingerprint, including the RGB copy for RGB data. Never regenerate,
blur or recolor that mask in Stage 9. Execution verifies mask fingerprints before and after replay;
receipt revalidation also reparses both chains and the shared blend operations.

Declare all eight scientific FITS and the final JPEG with `--expect`; choose `090-final.fit` as
`--primary-output`. Four branch layers, two mixed baselines, signed display stars and the candidate
have distinct paths. The primary `--source` remains the current accepted nonlinear starless parent,
which may have Stage 8 enhancement or tone.adjust. The first two loads use the exact separation
input, and the next two original starless. Substitute **both** chains exactly from the stretch receipt.
This RGB illustration matches the preceding Stage 7 RGB skeleton; mono uses its frozen mono mask.

```ssf
requires 1.4.4 1.5.0
set32bits
# recomposition-proposal: {"path":"reports/recomposition-request.json","sha256":"<actual-request-sha256>","size":123}
# HDR replay uses the frozen Stage 7 RGB mask.
load "/abs/session/artifacts/055-deconvolved.fit"
mtf 0 0.2 1
save "/abs/session/artifacts/090-ig" -chksum
close
load "/abs/session/artifacts/055-deconvolved.fit"
mtf 0 0.06 1
save "/abs/session/artifacts/090-if" -chksum
close
pm "$artifacts/090-ig$ * $artifacts/070-mask-rgb$ + $artifacts/090-if$ * (1 - $artifacts/070-mask-rgb$)" -nosum
save "/abs/session/artifacts/090-full" -chksum
close
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.2 1
save "/abs/session/artifacts/090-ag" -chksum
close
load "/abs/session/artifacts/060-starless.fit"
mtf 0 0.06 1
save "/abs/session/artifacts/090-af" -chksum
close
pm "$artifacts/090-ag$ * $artifacts/070-mask-rgb$ + $artifacts/090-af$ * (1 - $artifacts/070-mask-rgb$)" -nosum
save "/abs/session/artifacts/090-base" -chksum
close
pm "$artifacts/090-full$ - $artifacts/090-base$" -nosum
save "/abs/session/artifacts/090-stars" -chksum
close
pm "$artifacts/080-local-contrast$ + 1.0 * $artifacts/090-stars$" -nosum
save "/abs/session/artifacts/090-final" -chksum
stat main
savejpg "/abs/session/previews/090-final" 95
close
```

Baseline/final arithmetic and minimum-residual hard gates remain; negative energy ratios trigger the same mandatory review as single-chain recomposition.
Closure is checked for the unenhanced mixed baseline, not an enhanced A_prime or reduced strength.
No Screen, positive-part residual, clipping or rescaling is permitted. A failed or uncertain candidate
retains the trustworthy parent; it does not authorize standalone-starless delivery.

### Generate the reviewed candidate

Use the prepare/analyze/request/generate flow above after observing the actual contribution.
For an optional automatic starting reference, run:

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/deep_sky_star_reduction.py compute --session /abs/path/to/session
```


<a id="dualband-rgb-stars"></a>
## HOO 主体保留原 RGB 星色

双窄带变体保留三条链；基线在 Stage 9 回放冻结 RGB 链。新保色策略若采用独立 Fs，则在同一原 RGB full/starless 分支分别回放 Fs，HOO 主体链保持冻结。星层为 `S_RGB=F_RGB(I)-F_RGB(A)`，合成为 `A_HOO_prime+strength*S_RGB`；不对星层执行 HOO 映射或独立拉伸。原 RGB 基线继续验证 `F_RGB(A)+S_RGB=F_RGB(I)`，混合成片另检查负值、端点、星核、色晕及主体接缝，不要求它等于原 RGB 全图。保留带符号残差，不裁零、不以归一化掩盖接缝。这里保留的是原双窄带 RGB 显示星色，不是未采集的宽带星色。

生成器沿当前已接受父源谱系选择实际 separation/stretch receipt，不依赖 060/070 固定编号；普通 run 仍显式声明实际 --separation-run 与 --stretch-run。receipt.dualband_origin 绑定 HOO stretch 和参数报告，原有星层审查与闭合门继续有效，强度由绑定提案显式选择。

v22 继续使用原冻结 RGB 传递构造有符号加法星残差。Stage 7 保存的受保护 full 对照不是独立星层拉伸；按当前实际分支审查固定星区、亮核和光晕，不重新选参或改用 Screen。

v23 采用的单链或高阶链完整作为 F 冻结并在 full/starless 上重放；比较中未采用链不能混入残差。仍使用有符号 RGB 显示残差相加，不增加独立星层拉伸或 Screen。
