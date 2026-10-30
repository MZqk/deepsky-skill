# Stage 7 · 伪影重点区审查

新会话的 `artifact_review=structured_focus_v1` 要求普通 RGB／单色、分步、HDR 和 HOO 都明确记录伪影重点区。[Stage 7 正文](stages/stage-07-stretch.md)仍为 primary。本合同只增加审查记录，不增加拉伸、降噪、Screen 合成或自动伪影检测。

## 选区与初始声明

在已接受线性父源的实际原生预览上选区，给出包含问题边界及邻域的真实矩形。优先检查实际相关的位置：

- 星周残星、分离补洞、光晕和假色圈。
- 亮核边缘的平顶、暗环、振铃和不连续过渡。
- 暗云核心的灰雾、阴影损失或非恒星结构误删。
- 背景纹理突变、补丁与条带。
- HDR 蒙版作用范围及融合过渡。

清单使用独立的 `starun-siril.review-focus.v1`，由 goals.v3 或 goals.v4 的 `artifact_focus` 文件绑定引用；strategy 因而冻结初始清单。它不是科学 regions.v1，不增加 `artifact` ROI role，不用作 GHS 锚点或科学验收约束。重点区可以与科学 ROI 重叠。

```json
{
  "schema": "starun-siril.review-focus.v1",
  "source": {"path": "@input", "sha256": "ACTUAL_LINEAR_PARENT_SHA256"},
  "geometry": {"width": 512, "height": 512},
  "previous": null,
  "areas": [{
    "id": "bright-star-boundary",
    "rect": [90, 90, 130, 130],
    "risks": ["stellar_residual", "halo"],
    "reason": "Inspect the observed bright-star boundary and surrounding texture."
  }],
  "none_reason": null,
  "evidence": [{"path": "previews/060-starless-autostretch.jpg", "sha256": "ACTUAL_NATIVE_PREVIEW_SHA256", "size": 12345}]
}
```

示例哈希、尺寸、坐标及依据必须替换为实际材料。坐标沿用原图左上角零基、右／下边界不包含。风险标识为 `stellar_residual, separation_patch, halo, ringing, flat_core, color_fringe, gray_haze, structure_loss, texture_break, hdr_transition`。最多 32 个实际重点区；风险类型是检查问题，不表示检测到伪影。

没有适用重点区时，填写 `areas=[]`、具体 `none_reason` 和实际整图原生预览证据。不要为了通过字段校验制造选区；空声明也不代表工具证明图像无伪影。

## 材料生成与追加

`artifact-focus` 读取已有原生预览，生成带框上下文及 100% 原像素局部。整图定位可以缩略，局部不缩放、不另做 autostretch。已有主体面板保留在合并面板中；可用的 previous-prefix、full 对照及 HDR gentle/faint/mask 一同显示，帧名称区分实际来源。没有对应 full 参照时，不虚构分离前后的比较。

```sh
python scripts/deep_sky_siril_stretch.py artifact-focus --session SESSION --focus reports/artifact-focus.json --source @input --run-id 069-artifact-context
python scripts/deep_sky_siril_stretch.py artifact-focus --session SESSION --focus reports/artifact-focus.json --parameters reports/071-prefix/stretch-parameters.json --run-id 071-artifact-review
python scripts/deep_sky_siril_stretch.py artifact-focus --session SESSION --focus reports/artifact-focus.json --run 076-hdr --run-id 076-artifact-review
```

三种目标互斥；`--run-id` 是新材料的 ID。`--parameters` 支持已有选中输出的普通参数回执和分步回执；HOO 用正式 Stage 7 运行回执审查最终 RGB 呈现。材料保存在 `artifacts/stage7-reviews/<run-id>/`，绑定原生图像、目标回执、选区、策略及实现指纹，均为 `scientific_parent=false`。

初始 source 材料用于确认坐标。针对参数回执或正式运行生成材料时，将清单登记到对应策略。追加必须新建清单文件，以 `previous` 绑定当前版本，原有 ID、坐标、风险和依据保持不变，只增加区域。工具复用候选锁串行登记，并发追加冲突时合并已有区域后重提；不能以旧版本省略已登记区域。初始清单不改写，科学 ROI、变换、候选身份和三次额度不变。已提交有效正式审查后，该策略的清单冻结，不回写旧记录。

## 分步反馈与正式结论

每份后续 request.v5 的 `feedback.artifacts`，以及正式 `stretch_assessment.artifacts`，使用相同结构：

- `focus`：当前清单的文件绑定。
- `report`：针对实际上一前缀／正式运行生成的 artifact-focus.v1 材料绑定。
- `areas`：完整逐区结论，不能省略之前登记的区域。
- `none_reason`：与清单一致；有区域时为 null。

每条结论包含 `id, risks, baseline, change, observation, evidence, decision_reason`。`baseline` 为 `present/absent/uncertain`，说明参照中是否已有问题；`change` 为 `not_seen/improved/unchanged/new_or_worse/uncertain`。描述实际看见的形态与位置，区分既有缺陷被拉伸显露和本阶段新增损害；证据必须包含对应的局部比较面板。新增、加重或无法判断时，具体说明保留、调整或回退的依据，不以“后续会修复”代替判断。

有重点区时，分步 `feedback.materials` 还必须包含该材料的面板。继续下一步、结束分支和 `finish` 都复查当前目标；HDR 两支继承同一清单，最终融合再次核对蒙版过渡。正式运行后先生成对应材料，再用既有 `check-review` 取得骨架；骨架只填绑定，视觉结论保持空白。

没有重点区时，`report` 可为 null、`areas=[]`，沿用已有整图预览与局部面板，并保留明确理由；不要求额外生成空材料包。

验证器拒绝缺区、空观察或理由、越界坐标、错源／错目标、遗漏新区域及哈希漂移。伪影结论不自动改变 `verdict`，允许 Agent 说明视觉代价后采用；原有五项质量门及科学保护保持有效，记录不能豁免已有 fail/uncertain 或数值硬约束。工具不能证明 Agent 看过图，也不能从统计证明无伪影。

## 恢复、归档和兼容

`status` 显示当前重点区、待填区域与材料是否就绪，不启动 Siril。材料生成与追加同样不启动 Siril、不另占科学试跑次数。正式面板合并保留原比较内容，不逐框扩充顶层附件，继续遵守 32 项材料上限。

剪枝归档保留清单版本、材料 JSON 和面板，并验证后续完整性。完整修改前策略、文档、配置与实现指纹存于 `references/artifact-focus-history.json`；旧会话按原合同回放／finalize，不回填重点区或改写审查。
