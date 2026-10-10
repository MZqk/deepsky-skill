# Changelog

本文件记录 `deep-sky-advisor` 的独立版本变更。

## [0.4.0] - 2026-10-03

新增 `color_refinement`（色彩精修），这是第一个真正使用 `finishing` 阶段的操作。

### 为什么加它

`references/` 收敛后新增操作的成本降到 4 处必改 + 3 处可选。在剩余的两个候选
（`local_contrast` / `color_refinement`）中，只有后者有可引用的**已测量**证据：
`color.background_ratios_to_mean`、`color.channel_p99_normalized`、`color.channel_correlation`、
`color.collapsed_channels`、`clipping.per_channel.*`、`background.channel_planes.*`。
`local_contrast` 可引用 0 条——分析器不测量局部对比 / 结构 / 锐度，
因此不新增（详见 `reports/2026-10-03-deep-sky-advisor-剩余两项价值分析.md`）。

### 门禁逻辑

`_color_state()` 取背景通道比例相对 1.0 的**最大偏差**作为残余色偏强度，并单独读出塌陷通道。

| 条件 | 判定 | 置信度 |
|---|---|---|
| 窄带 / 双窄带数据 | `skip` — 配色由 `narrowband_mapping` 决定并已记录 | 高 |
| 存在信号塌陷的通道 | `skip` — 色彩精修无法恢复该通道，须回查采集或校准 | 高 |
| 残余色偏 ≥ `COLOR_REFINEMENT_THRESHOLD`（0.10） | `review` | 中 |
| 未测得残余色偏 | `skip` — 此时提升饱和度属纯审美选择 | 低 |

`color_refinement` 始终是 `review` 或 `skip`，从不 `recommend`。

### 与既有操作的边界

- `color_calibration`（linear）管**准确性**；`color_refinement`（finishing）管拉伸后的**残余偏差**，
  两者是顺序关系而非替代。
- 窄带数据的配色由 `narrowband_mapping` 决定，`color_refinement` 让位。
- 分通道饱和时自动追加风险提示：「X 通道已接近或压到上限；提升饱和度会进一步推高该通道」——
  复用 0.3.2 修好的 `_highlight_pressure()`。

### 其它

- `software_guidance.py`：新增 `GENERIC["color_refinement"]` 与 siril / pixinsight / photoshop
  三个 override，以及 `PRIMARY_TOOL` 条目（`Color Saturation` / `ColorSaturation`）。
- `report_zh.py`：新增 `color.collapsed_channels`、`color.channel_correlation` 的中文标签。
- `SKILL.md`：决策顺序中把「nonlinear contrast and color refinement」拆开，
  新增独立的 finishing 色彩精修步骤。
- `references/diagnostic_metrics.md`：补齐报告实际引用的字段名
  （`color.background_ratios_to_mean`、`color.channel_p99_normalized`、`color.collapsed_channels`、
  `color.channel_correlation`、`background.corner_median_range`、`background.channel_planes.*`、
  `noise.block_count`、`file.format`）。该文档的用途是「读 `*_analysis.json` 时查这个」，
  此前只有描述性说明、没有可检索的字段名。
- `references/recommendation_policy.md`：`finishing` 阶段不再声称包含尚未实现的 local contrast
  与输出级降噪锐化，改为注明当前实现为 `color_refinement`。
- 测试增至 61 项，新增 `ColorRefinementTests`（10 项）。

### `references/` 结构收敛（设计文档 §6.4 的剩余部分）

0.3.1 只给三份软件文档加了「扩展阅读」声明，没有真正减重。本次完成结构收敛。

**新增 `references/pipeline_stages.md`**：软件无关的阶段定义（`linear` / `nonlinear` /
`finishing` / `export`），每个阶段给出含义、包含的操作、进入条件、阶段级完成判据与回退迹象、
交接点，以及「为什么边界重要」。它只讲阶段，不重复任何操作内容。

**三份软件文档只保留软件特有知识**，并修掉三类实质问题：

| 问题 | 处理前 | 处理后 |
|---|---|---|
| 固定数值预设（直接违反 SKILL.md 的「禁止 `exact` 参数」策略） | 13 处，如 `Layers: 4-5 层`、`Amount = 0.1-0.3`、`不透明度 30%-70%`、`羽化 200-500px`、`饱和度 +10%~+30%` | **0 处** |
| 与阶段归属冲突的章节 | `photoshop_workflow.md` 的「背景均匀化」「HDR 合成」「多窄带合成」把 linear 工作写成 PS 职责 | **已移除**，改为指向主轨 |
| 术语错误 | `siril_workflow.md` 的 `去拜耳模式：根据相机选择（OLEDCF/Mono）` | 已改写为正确的 CFA/Bayer 处理顺序说明 |

保留并强化的部分：菜单路径与进程名、脚本自动化（Siril `.ssf` / PixInsight PJSR）、
文件命名约定、软件特有的坑（STF 只是预览、减法 vs 除法、拒绝算法随帧数变化、
去马赛克位置、PS 不要用像素级修补改天区、Convert 而非 Assign）。

体量：三份文档合计 859 → 304 行；`references/` 合计 1306 → 912 行（含新增的
`pipeline_stages.md` 135 行）。每份文档新增「阶段与操作的对应关系」表，把阶段、
报告中的操作、以及本软件的入口三者对齐。

`SKILL.md` 第 7 步的参考列表加入 `pipeline_stages.md`，并明确三份软件文档
**刻意不含参数预设**。

## [0.3.2] - 2026-10-03

缺陷修复：`highlight_protection` 的门禁只用整图亮度，会漏掉单通道饱和。

### 问题

门禁为 `clipping.highlight_ratio_ge_0_999 >= 0.002`，而该值是**整图亮度**的占比
（`analyze_file.py` 中 `gray = _luminance(normalized)`）。亮度是 R/G/B 的加权和，因此
**当红色饱和而绿蓝未饱和时，亮度仍达不到阈值，整图指标看不到红通道已经爆了。**

实测 NGC6888 Ha 合成数据：

| 指标 | 值 |
|---|---|
| 整图亮度 | 0.0 |
| 红通道 | 0.0502（5.02%） |
| → `highlight_protection` | **被静默跳过** |

红通道饱和正是 Ha / HOO 数据最常见的情况（亮核死白、星色丢失），而报告对此一言不发。

### 修复

- 新增 `_highlight_pressure()`：取「整图亮度」与「各通道占比」的**最大值**作为有效亮端压力，
  并返回驱动该结论的证据路径与通道名。门禁阈值不变（0.002，且该操作始终是 `review`，
  灵敏度代价低）。
- 分通道驱动时：置信度从 `low` 升为 `medium`；证据中同时列出整图路径与**最严重通道**的路径；
  起始策略改为提示「整图亮度尚未压到上限，但 X 通道已经饱和」；风险提示新增
  「X 通道已被单独压到上限；后续提升饱和度或色彩时不要进一步推高该通道」——
  这也是后续 `color_refinement` 的安全前提。
- `report_zh.py` 新增 `clipping.per_channel.{r,g,b,mono}.highlight_ratio_ge_0_999` 的中文标签。
- 兼容性：`clipping.per_channel` 缺失、非字典、或通道值为非数值时均安全跳过；
  整图驱动的行为与 0.3.1 完全一致。
- `references/diagnostic_metrics.md` 与 `SKILL.md` 补充该语义说明。
- 测试增至 51 项，新增 `HighlightPressureTests`（8 项）。

## [0.3.1] - 2026-10-03

内容收敛：把分散在多处、互相漂移的操作内容合并为单一真相源，并修复由此导致的
三处「死内容」缺陷。报告结构不变。

### 收敛结果（每类内容现在只有一个位置）

| 内容 | 唯一真相源 |
|---|---|
| 每软件的工具 / 步骤 / 参数依据 / 蒙版策略 | `scripts/software_guidance.py` |
| 每操作的目的 / 起始 / 调整 / 验收 / 回退 / 告警 | `scripts/generate_advice.py`（各 `_operation()` 调用处内联） |
| 标签与版式 | `scripts/report_zh.py`（纯标签层） |
| 概念与菜单说明 | `references/*.md`（扩展阅读，非权威） |

### 修复的缺陷

- **`parameter_logic` / `mask_strategy` 从不渲染。** 渲染器一直取 `report_zh.py` 的中文
  通用版，导致 `software_guidance.py` 里的软件专属规则从未出现在报告中——
  例如 PixInsight 的「加性天空辉光使用 Subtraction；疑似渐晕先回查平场，不直接使用 Division」
  和 Photoshop 收尾操作的参数规则。现已按轨渲染。
- **`operation["cautions"]` 的真实内容被丢弃。** 渲染器只输出一句固定套话，
  「该目标/滤镜可能包含与梯度相似的真实大尺度信号」等针对性告警从未出现。现按操作渲染。
- **`SOFTWARE_MAP`（软件专属一行摘要）零引用**，是纯死代码，已删除；
  报告中原本标为「软件处理方向」的一行实际是软件无关文本，属标签错误，该行已移除。

### 其它变更

- `report_zh.py` 从 345 行降至 178 行：删除 `OPERATION_TEXT`、`GENERIC_GUIDANCE`、
  `SOFTWARE_STEPS`、`CHECKPOINTS`、`FAILURES`、`TOOL_LABELS`、`REQUIRED_INFO`。
- `software_guidance.py` 内容改为中文（报告是中文单语，编译产物 `*_advice.json` 随之变为
  中文；`validate_advice()` 只校验字段存在且非空）。
- `validate_advice()` 新增 `purpose` / `starting_point` / `how_to_adjust` 与
  `implementations.*.parameter_logic` / `mask_strategy` 的存在性校验。
- `references/*.md` 顶部加权威性声明，明确「修改这些文件不会改变生成结果」。
- 测试增至 43 项，新增 `ContentOwnershipTests`：结构性守卫（`report_zh` 不得再出现内容表）、
  英文散文泄漏回归、per-software 参数依据与蒙版策略确实渲染、告警内容确实渲染。

## [0.3.0] - 2026-10-03

行为变更：报告从「单一软件」改为「共用诊断 + 双主轨 + 下游二次加工」。

- `generate_advice.py` 数据模型重构：操作对象新增 `phase`（`linear` / `nonlinear` /
  `finishing` / `export`）与 `implementations`（每软件实现映射），取代原先单一
  `software_instruction`。`evidence`、`purpose`、验收与回退条件保持软件无关，两轨共用。
- 新增 `TRACK_CAPABILITY`：规定各软件可拥有的阶段。**Photoshop 不再拥有 `linear` 阶段**——
  校准、注册、叠加、裁切、背景建模、色彩校准、窄带映射、线性降噪一律归主轨。
- `--software` 改为接受逗号分隔多值，默认 `siril,pixinsight`；单值退化为单轨报告（与 0.2.1
  行为一致，作为回归基线）。新增 `--no-finishing` 关闭下游章节。`generic` 不允许与具体软件混用。
- 报告结构改为：共用诊断区（一次）→ 轨 A Siril → 轨 B PixInsight → 双轨对应关系表 →
  交接给 Photoshop 的契约 → 二次加工（Photoshop）→ 仍需补充的信息。
- 新增**双轨对应关系表**：自动从 `implementations` 生成，按操作配对两轨的主入口工具。
  为此在 `software_guidance.py` 新增 `PRIMARY_TOOL` / `get_primary_tool()`——`tools[0]` 是
  第一步而非标志性工具（例如 PixInsight 在 DBE 之前先列 DynamicCrop）。
- 新增**交接契约**（`handoff_contract`）：从主轨 `linear` 操作的 decision 状态推导前置清单，
  并记录位深/格式、色彩空间、校色状态、窄带映射、星点分离与不可逆项。
- Photoshop 章节改为三段式：上游前置清单、可执行的收尾操作、不在 Photoshop 处理的操作表。
  此前 PS 条目几乎全是「回上游做」，收尾阶段缺少可执行内容，本次补齐局部对比、色彩精修、
  输出锐化等步骤。
- `validate_advice` 多轨化：按轨校验 `implementations`，新增 `phase` 合法性与
  `TRACK_CAPABILITY` 归属一致性校验（能捕获「某轨缺失实现」「阶段不匹配」「非法阶段归属」）。
- `references/recommendation_policy.md`：删除「只展开用户选定软件、不得在一份报告里重复多套
  流程」的旧策略，改为 `Phase ownership` / `Dual-track output` / `Downstream finishing stage`
  三节。
- `report_zh.py`：新增 `TRACK_LABELS`、`PHASE_LABELS`、`MASTER_FORMATS` 与交接契约文案；
  补 `SOFTWARE_STEPS["siril"]["star_shape_review"]`（此前缺项，回落到通用文案）；
  `localized_guidance()` 改为接收 implementation 参数。
- `software_guidance.py`：补 `OVERRIDES["siril"]["star_shape_review"]`。
- 分析 JSON 的 schema 不变（`analyze_file.py` 未改动），建议 JSON 的 `schema_version` 升为 2.0。
- 测试：现有断言按轨改写，新增双轨渲染、PS 阶段边界、交接契约推导、多轨校验等 20 项用例
  （35 项全部通过）。

## [0.2.1] - 2026-09-23

- 例行补丁升级与元数据规范维护

## [0.2.0] - 2026-09-10

- 新增 `references/smart_telescope_devices.md`：DWARF 3 / DWARF mini / Draco 与 Seestar
  S30 / S30 Pro / S50 / S50 Pro 的光学、传感器、像元尺度、内置滤镜与采集特性先验，
  全部按 official / chip spec / inferred 标注证据等级。
- `analyze_file.py` 新增 `classification.device`：基于 TELESCOP/INSTRUME 头与文件名的
  智能望远镜识别（含 brand-only 降级），schema 升级至 2.1。
- `generate_advice.py`：设备先验中的双窄带滤镜可触发 narrowband_mapping 复核；
  报告头部新增拍摄设备字段。

## [0.1.0] - 2026-08-28

- 为现有图像诊断与后期建议能力建立首个治理版本基线。
- 登记独立许可、开发环境和发布流程。
