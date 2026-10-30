# Stage 7 · 按需诊断示例

按需读取；当前科学规则和执行入口见[Stage 7 主文档](stages/stage-07-stretch.md)。

<a id="structure-roi-context"></a>
#### 带框主体上下文：选区后、冻结前查看

New sessions use the independent `morphology-context` command above before choosing or freezing a profile. The following request-dependent example remains useful for additional diagnostic neighbours after a request exists; it is not the prerequisite for initial morphology selection.

在可信原生预览上提议少量框，查看整图位置及包含完整主体和邻域的上下文，再确认或用新材料修订。逐框记录真实可辨内容；命名为 south-arc 的框若主要落在弧外弱场，应按实际内容描述，不能以名称代替确认。弱结构选区避开亮核、亮云及明显星晕，避免大框混合亮边、内部细丝和天空；混合框可用于整体保护，但不能直接代表极暗外缘或替代独立弱结构锚点。邻域按同一局部云气或背景过渡选择，排除星核、星晕和其他结构。相邻关系由上下文和实际观察说明，不设通用框尺寸、距离或对比阈值。不可辨或没有可信邻域时记录不可测，不编造参照。

新证据策略的 preflight 在 `warnings` 显示 faint/core 交集坐标及占 faint 面积比例，不阻断或自动分类；在 morphology observation 中如实说明混合内容。换链理由引用实际失败约束或真实前后缀测量，一次参数失败不能证明整个算子不适用。

求参保护区仍使用原请求；另在 `reports/070-diagnostic-regions.json` 填写独立 regions.v1，reference 绑定同一线性父源哈希和原始几何。结构类型和配对关系只写入示例变量及现有 purpose/review.notes，不添加 ROI role 或请求字段，邻近弥漫区通常仍属于 faint。下面示例用于有可信 Stage 6 参照的默认多节路线；草案和诊断区由 Agent 实际填写，context_rect 必须替换为完整主体及邻域的真实坐标。无 Stage 6 分支时沿用当前父源已有可信原生预览的绑定，不伪造 Stage 6 来源。

```python
# Stage 7 diagnostic: boxed ROI context.
from pathlib import Path
from PIL import Image, ImageDraw
import json
from deep_sky_siril_session import load_session, effective_session_payload
from deep_sky_siril_stretch import read_request, bound_file, record
from deep_sky_siril_metrics import measure_regions
from deep_sky_siril_visibility import stage6_reference

session, payload, manifest = load_session(Path("/abs/session"))
payload = effective_session_payload(session, payload, manifest)
request, source, protected, _ = read_request(session, payload, session / "reports/071-morphology/profile-request.json")
origin = stage6_reference(session, payload, source)
if origin is None or origin["source"] != record(session, source):
    raise SystemExit("This example requires the actual accepted Stage 6 preview")
preview = bound_file(session, origin["preview"], ("previews",))
regions_path = session / "reports/070-diagnostic-regions.json"
if regions_path.resolve() == bound_file(session, request["regions"]).resolve():
    raise ValueError("Diagnostic regions must remain separate from solving regions")
measured = measure_regions(source, None, regions_path)
if measured["status"] != "measured":
    raise ValueError(measured.get("reason", "Unavailable scientific ROI measurements"))
regions = json.loads(regions_path.read_text())
with Image.open(preview) as original:
    image = original.convert("RGB")
    profile = original.info.get("icc_profile")
width, height = regions["reference"]["width"], regions["reference"]["height"]
if image.size != (width, height):
    raise ValueError("Use the original preview geometry and orientation")
context_rect = (100, 100, 600, 600)  # Replace with the actual subject and its surrounding field.
x0, y0, x1, y1 = context_rect
if not 0 <= x0 < x1 <= width or not 0 <= y0 < y1 <= height:
    raise ValueError("Subject context is outside the original geometry")
subject = [r for r in protected if r["role"] in {"faint", "core"}] + regions["regions"]
if any(not (x0 <= r["rect"][0] < r["rect"][2] <= x1 and y0 <= r["rect"][1] < r["rect"][3] <= y1) for r in subject):
    raise ValueError("The context must contain the subject ROIs and their diagnostic neighbours")
annotated = image.copy()
draw = ImageDraw.Draw(annotated)
for tag, colour, rows in (("solve", "#66d9c3", protected), ("diag", "#f6b26b", regions["regions"])):
    for row in rows:
        a, b, c, d = row["rect"]
        draw.rectangle((a, b, c - 1, d - 1), outline=colour)
        draw.text((a, max(0, b - 12)), tag + "/" + row["id"], fill=colour)
full = annotated.copy()
full.thumbnail((360, 360))
crop = annotated.crop(context_rect)
panel = Image.new("RGB", (full.width + crop.width + 24, max(full.height, crop.height) + 36), "#161616")
panel.paste(full, (8, 28)); panel.paste(crop, (full.width + 16, 28))
ImageDraw.Draw(panel).text((8, 8), "Full frame | subject context (native crop)", fill="white")
directory = session / "reports/070-v4-a1-roi-diagnostics"
directory.mkdir()
with (directory / "context.png").open("xb") as stream:
    panel.save(stream, format="PNG", icc_profile=profile)
context = {"context_rect": list(context_rect), "bindings": {"source": record(session, source),
    "origin": origin, "solve_regions": request["regions"], "diagnostic_regions": record(session, regions_path),
    "panel": record(session, directory / "context.png")}}
with (directory / "context.json").open("x") as stream:
    json.dump(context, stream, indent=2, allow_nan=False)
```

框按原图左上角零基、右/下边界不包含绘制，框线最后一像素为 x1−1/y1−1；只在副本标注，不翻转原预览。整图缩略只用于定位，主体裁片保持原像素；不能反用缩略显示坐标选科学框。查看该图并将逐框内容和配对理由写入现有 purpose/notes 后才冻结请求。ROI 或来源改变时新建诊断目录、重新核对，不覆盖旧图和记录；这里未启动原生科学试跑。

<a id="adjacent-structure-pairs"></a>
#### 邻近结构成对测量：真实首步前缀与最终候选

先在上下文中确认并固定下列配对；只保留实际存在且相关的类型，示例 ID 须替换为真实诊断 ROI ID。求参 targets/regions/constraints 全部保持原样，不将诊断框追加进去。

| 结构 | 正方向差值 C |
|---|---|
| 细丝 | median(filament) − median(adjacent_diffuse) |
| 暗带 | median(adjacent_bright) − median(dark_lane) |
| 弱外缘 | median(outer_edge) − median(adjacent_outside)，邻域须经上下文确认 |
| 亮边 | median(bright_rim) − median(adjacent_inner) |

下例在正式 run 成功后执行，读取其已绑定的真实 first_prefix 与最终候选，不调用内部 build 或重新求参。两次 measure_regions 均以同一线性父源绑定诊断区；用于 C 的是各自报告中 **candidate** 的逐通道 median，parent 仅用于来源和几何校验。科学值来自原生浮点 FITS 的同一标尺，不从 JPEG 量化值或加权 RGB 中位数计算。局部面板使用已绑定的统一 sRGB 原生预览，不对各框追加 autostretch。

```python
# Stage 7 diagnostic: adjacent structure pairs.
from pathlib import Path
from PIL import Image, ImageDraw
import json
from deep_sky_siril_core import _load_run
from deep_sky_siril_stretch import bound_file, record
from deep_sky_siril_metrics import measure_regions
from deep_sky_siril_visibility import review_paths

session = Path("/abs/session")
directory = session / "reports/070-v4-a1-roi-diagnostics"
context_path = directory / "context.json"
context = json.loads(context_path.read_text())["bindings"]
_, receipt = _load_run(session, "070-v4-a1")
if receipt["status"] != "success":
    raise ValueError("Use a successful formal run and its frozen visibility evidence")
review_paths(session, receipt["stretch_visibility"], receipt)
visibility_path = bound_file(session, receipt["stretch_visibility"])
view = json.loads(visibility_path.read_text())
controls = [row for row in view["controls"] if row["kind"] == "first_prefix"]
if len(controls) != 1 or context["source"] != view["source"] or context["solve_regions"] != view["regions"] or context["origin"] != view["origin"]:
    raise ValueError("The context and first prefix must belong to this exact source and ROI selection")
bound_file(session, context["panel"])
regions_path = bound_file(session, context["diagnostic_regions"])
regions = json.loads(regions_path.read_text())["regions"]
source = bound_file(session, view["source"], ("artifacts",))
control = controls[0]
paths = {"control": bound_file(session, control["input"]),
         "candidate": bound_file(session, view["candidate"], ("artifacts",))}
metrics = {name: measure_regions(path, source, regions_path) for name, path in paths.items()}
pairs = [("filament", "filament", "adjacent-diffuse"), ("dark-lane", "adjacent-bright", "dark-lane"),
         ("outer-edge", "outer-edge", "adjacent-outside"), ("bright-rim", "bright-rim", "adjacent-inner")]
rows = {name: {row["id"]: row["candidate"]["channels"] for row in report["regions"]} for name, report in metrics.items()}
results = []
for name, positive, negative in pairs:
    result = {"id": name, "positive": positive, "negative": negative}
    try:
        for label, report in metrics.items():
            if report["status"] != "measured":
                raise ValueError(label + ": " + report.get("reason", "Nonfinite or unavailable ROI measurements"))
        values = [rows[label][roi] for label in ("control", "candidate") for roi in (positive, negative)]
        channels = [row["channel"] for row in values[0]]
        if not channels or any([row["channel"] for row in value] != channels for value in values):
            raise ValueError("Channel identities differ")
        differences = [[a["median"] - b["median"] for a, b in zip(values[i], values[i + 1])] for i in (0, 2)]
        result.update(status="measured", channels=[{"channel": channel, "C_control": before,
            "C_candidate": after, "delta_C": after - before} for channel, before, after in zip(channels, *differences)])
    except (KeyError, ValueError) as error:
        result.update(status="uncertain", reason=str(error), channels=[])
    results.append(result)
previews = [bound_file(session, row, ("reports",)) for row in
            (control["baseline_view"]["preview"], view["candidate_view"]["preview"])]
images = []
for path in previews:
    with Image.open(path) as image:
        images.append(image.convert("RGB"))
if any(image.size != (metrics["control"]["reference"]["width"], metrics["control"]["reference"]["height"]) for image in images):
    raise ValueError("Use the original native preview geometry")
cell = max(row["rect"][2] - row["rect"][0] for row in regions)
panel = Image.new("RGB", (2 * (cell + 8) + 8, 8 + sum(r["rect"][3] - r["rect"][1] + 24 for r in regions)), "#161616")
draw, top = ImageDraw.Draw(panel), 8
for region in regions:
    draw.text((8, top), region["id"] + " / first prefix | final", fill="white")
    for column, image in enumerate(images):
        panel.paste(image.convert("RGB").crop(region["rect"]), (8 + column * (cell + 8), top + 24))
    top += region["rect"][3] - region["rect"][1] + 24
panel_path, report_path = directory / "pairs.png", directory / "pairs.json"
if any(path.exists() or path.is_symlink() for path in (panel_path, report_path)):
    raise FileExistsError("Keep existing diagnostic evidence; use a new directory")
with panel_path.open("xb") as stream:
    panel.save(stream, format="PNG", icc_profile=images[1].info.get("icc_profile"))
for image in images:
    image.close()
diagnostic = {"bindings": {"context": record(session, context_path), "visibility": receipt["stretch_visibility"],
    "regions": context["diagnostic_regions"], "panel": record(session, panel_path)},
    "metrics": metrics, "pairs": results, "requires_visual_review": True}
with report_path.open("x") as stream:
    json.dump(diagnostic, stream, indent=2, allow_nan=False)
```

RGB 按实际 channel=0/1/2 分别报告 C，单色只保留真实通道；不加权、不求总分、不取绝对值，也不除以天空 MAD 或计算改善率。缺失 ROI、非有限值、来源或几何不匹配时保留实际原因和不可测状态，不补零。复用已保存 metrics 时同样核对来源哈希、独立区域合同、几何、通道及科学标尺；不混用 JPEG、其他父源或不同统计口径。

实际打开上下文和同位置局部面板，将逐框观察与每个配对的 C_control/C_candidate/ΔC 写入既有 purpose/review.notes。背景与细丝一起变亮而差值不变，不能称为局部收益；暗带反转保留负方向；颗粒使 span 扩大不等于结构改善。正 ΔC 仍须确认连续结构、颜色、亮核和过渡自然，不触发自动采用。远处天空和既有 subject_contrast 继续仅说明主体—天空分离，不新增或替换排序分数。

上下文、局部面板及诊断记录通过现有 inspected_materials 绑定路径和 SHA-256；记录引用其来源和独立区域文件，保持既有 32 项上限，不新增材料合同。以上示例仅解释普通 first_prefix 对照；HDR/HOO 保持既有真实分支、单色通道和审查语义，不把映射色差当作细节收益。

#### 紧凑记录模板

沿用 reason/purpose 和现有 review.notes，自由文本保持 2000 字符上限；报告、局部预览及 SHA-256 通过现有 inspected_materials 绑定，notes 用 run/ROI ID 引用，避免重复长哈希。下表是记录内容，不是新增 schema 或机器解析字段。

| 记录内容 | 必须能复核的依据 |
|---|---|
| 目标与区域 | 带框整图/主体上下文和 Stage 6 参照的引用、逐框真实内容、边界与邻域；分别记录实际可辨的细丝/暗带/弱外缘/亮边、保护 ROI 和独立 pair ID，确认基础拉伸是否充分。 |
| 阻塞与目标 | ROI/统计量、实测值、旧→新区间及修订证据/取舍；区分 phase、共享 protection 和科学保护。 |
| 当前域提案 | 每步输入证据、LP/SP/HP 引用、实际输出天空检查、因子变化及预期结构收益。 |
| 成对反馈 | 固定口径的 C_control、C_candidate、ΔC；逐通道时列明通道，附暗带、云气轮廓、颜色和亮核过渡观察；若外缘退回背景，记录保护亮核的取舍。 |
| 决定与下一轮 | 采用/保留/拒绝及理由、相对推荐的差异；阶段停止原因、本轮结果、partial/未解决结构与取舍、下一轮调整；请求/run 引用及试跑账目。 |

#### 对照判读示例

以下仅解释差值与观察的关系，不是固定增强目标或数值验收阈值。

| 场景 | 反馈与行动 |
|---|---|
| 细丝和邻域都整体提亮，成对差值不变 | 不把全幅变亮单独计为细节收益；查看细丝连续性，必要时重定 SP/目标区间。 |
| 暗带成对差值由正变负 | 保留反转方向并核对角色、口径与实际像素；不用绝对值宣称增强。 |
| 外缘差值提高且边界、弱色彩连续 | 可作为本轮收益证据，同时检查亮核和 MTF 收尾是否压回外壳；不能仅凭正差值接受。 |
| 颗粒使 span 扩大，连续结构未改善 | span 增大不等于层次或细节收益；噪声仅诊断，不以数值扩大证明增强。 |
| 亮核失败或共享上限仍阻塞 | 按报告回查实测值及交集；保留科学保护，合理修订显示目标时新建请求并重跑；显式比较才同步重建两候选，不追认旧失败图。 |

这些指导复用现有接口，不改变代码的配方、数值推荐或资格判定。新增执行使用新请求，并在 provenance 中绑定实际读取的文档指纹；已有请求、报告和历史审查保持原样。

拉伸方法由 Agent 根据目标、当前科学父源和实际区域约束选择，沿用现有 request、求参与生成接口。天体名称不自动选择 HDR；含星回退须重新绑定含星父源。HDR 须显式选择并实际执行、验证与审查；缺参数或证据时保留恢复点。`--legacy-mtf` 仍是显式兼容入口，不默认启用。




只拉伸已接受的线性科学父源；有可信无星分支时优先主体，否则保留含星分支。冻结可重放的显式传递链，供 Stage 9 使用；已接受的非线性图后续明暗调整使用 [tone.adjust](shared-protocols/tone-adjust.md#tone-adjust)，不重新进入 stretch。

