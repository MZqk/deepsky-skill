---
name: deep-sky-processor
description: |
  AI 主导的深空天文后期助手。用于把深空天文原始叠加后的 FITS、XISF、
  TIFF、PNG/JPG 图像，在真实性约束下处理成更美观的 JPG/TIFF 成片。
  AI 负责视觉判断、天体类型策略、参数选择、风格取舍和质量审查；本地脚本
  只作为诊断、可控图像变换和量化审查工具。适用于发射星云、反射星云、
  星系、星团、行星状星云、暗星云、宽场星野的真实风格美化、拉伸、
  去光害、校色、降噪、缩星、细节增强和最终 JPG 输出。
---

# Deep Sky Processor

## 定位

这是 **AI 主导、代码辅助、真实约束** 的深空后期 skill，不是 PixInsight/Siril 的替代品。

## 触发条件

当用户提到以下任意场景时自动调用本 Skill：

**关键词**：深空后期、天文后期、处理深空照片、处理 FITS、处理 XISF、
拉伸星云、去光害、缩星、降噪星云、星云美化、深空图像处理、
NGC6888 后期、M42 后期、IC 2177 后期、星系后期处理。

**文件特征**：用户提及 `.fits` / `.xisf` / `.fit` / `.fts` 文件和深空天体名称，
或直接要求对深空天体图像做美化、拉伸、降噪或校色处理。

**不适用场景**：普通风景照、人像、日常摄影后期。这些应使用通用图像处理工具。

AI 的职责：
- 看图或根据用户描述判断天体类型、问题和审美方向。
- 运行诊断脚本，先表达视觉意图，再选择最小必要步骤和参数。
- 每执行一个高风险阶段就查看前后图、差异图和质量门禁。
- 决定接受、重试、生成候选版本、回退或请求人工审查。
- 输出真实但更好看的 `jpg`，必要时同时保留 `tif` 母版。

脚本的职责：
- 读取 FITS/XISF/TIFF/PNG/JPG。
- 提供亮度、噪声、梯度、星点、锐度、色彩等诊断。
- 执行可控的非生成式变换：拉伸、启发式校色、背景均衡、降噪、缩星、局部对比、饱和度调整。
- 输出中间图和质量指标，供 AI 审查。

## Agent-in-the-loop 模式

默认优先使用阶段式闭环，不要只制定一次计划后运行完整管线。

```text
诊断与目标确认
→ 执行一个阶段或生成 2-3 个候选
→ 查看 before/after/difference/review.json
→ 输出结构化 verdict 和 actions
→ 接受、微调或回退
→ 进入下一阶段
```

初始化会话：

```bash
python scripts/agent_workflow.py init input.fits work/session \
  --target-type emission_nebula \
  --target-name NGC6888
```

FITS/XISF 输入必须读取 `capture_metadata` 和 `physical_priors`。若本机安装
Astrometry.net，可增加 `--plate-solve`，并用 `--catalog-json` 将星表目标投影到
画面坐标。物理解释和限制见 `references/physical_metadata.md`。

提交单阶段动作：

```bash
python scripts/agent_workflow.py apply work/session action.json
```

读取 `work/session/session.json`、本轮 `review/review.json` 以及四张审查图。
动作和评审 JSON 必须符合 `references/agent_protocol.md`。

高风险阶段必须逐步审查：
- `dbe`
- `stretch`
- `star_remove` / `star_reduce`
- `star_process` / `star_combine`；请求其中任一步时自动执行完整星点链路
- `enhance` / `sharpen`
- `final_color` / `style`
- 外部 AI 降噪或无星图接入
- 任何 `masked_adjustment`，必须同时审查 `mask.jpg` 和覆盖率

低风险、确定性步骤可以合并执行，但仍需在最终阶段进行真实性审查。
模型描述“背景稍暗、核心强保护、轻微缩星”等语义意图；代码负责把意图映射到有边界的参数。模型不得直接生成或修补像素。

星点处理依赖规则：
- `star_process` 自动补全 `star_remove → stretch → star_process → star_combine`。
- `star_combine` 自动补全同一完整链路，确保存在无星层和已处理星点层。
- 球状星团、疏散星团和 M45 的安全规则会移除
  `star_remove`、`star_process`、`star_combine`，不得强制补回。

需要对 Hα、OIII、亮部、暗部或星点做精细局部控制时，使用
`scripts/mask_tools.py` 或 Agent 的 `masked_adjustment` 动作。只描述亮度范围、
色相范围、星点保护和组合逻辑，不直接绘制蒙版。详细协议见
`references/mask_workflow.md`。

## 真实性红线

必须遵守：
- 不生成图中不存在的星云纹理、尘埃、星点或颜色。
- 不使用 AI 超分辨率、AI 着色、AI 纹理生成、参考图重绘。
- 可以使用外部 AI 降噪和去星工具，但只作为统计建模/分类工具，结果必须审查。
- 单张 RGB/LP 数据不能伪造 SHO/HOO 窄带色彩。
- “美化”只能来自拉伸、黑位、曲线、局部对比、饱和度、降噪和星点比例的调整。

允许的风格词：
- `natural`：真实自然，低饱和，保守细节。
- `enhanced`：星云更明显，适度提高局部对比和饱和。
- `high_contrast`：更强主体结构，背景更深，风险更高。
- `soft`：柔和、低锐化、低噪声，适合反射星云。
- `emission`：保护 Hα 深红和 OIII 青蓝，不做全图灰度世界白平衡。

内置非生成式专业定调 profile：
- `auto`：根据目标类型自动选择。
- `natural`：保守自然，适合作为真实基准版本。
- `deep_clean`：深黑背景、干净现代。
- `dramatic_nebula`：发射星云主体突出，色彩有冲击但受控。
- `soft_dust`：反射星云/暗尘埃的柔和胶片感。
- `galaxy_core`：星系黄核、蓝臂、尘埃带层次。
- `widefield_punch`：宽场星野的深背景和星云可见度。

这些 profile 只做色调曲线、背景压暗、背景去饱和、局部对比、轻微色彩分离和高光 rolloff，不生成新结构。

默认输出两版更稳妥：
- `*_natural.jpg`
- `*_enhanced.jpg`

## 必须优先做的三轮流程

### Round 1: 诊断和预览

识别固定遵循：

```text
Header/WCS → 原始数值诊断 → 零裁切安全预览
→ AI视觉判断 → 本地CV辅助验证
```

1. 优先读取 FITS/XISF Header 和已有 WCS。可靠的目标标识不能被视觉分类静默覆盖。
2. 直接读取原文件运行数值诊断：

```bash
python scripts/analyze.py input.fits --format readable
```

3. 为线性 FITS/XISF 生成安全视觉审查包：

```bash
python scripts/recognize.py input.fits \
  --output recognition.json \
  --stage input \
  --workflow-dir recognition_workflow
```

安全预览固定 `shadow_pctl=0.0`，不会通过低端百分位裁切微弱正信号。
工作流输出全幅、主体、RGB 通道预览、浮点 TIFF、视觉审查请求和完整证据 JSON。
预览只用于观察，不能作为后续处理输入。

AI 必须实际查看预览后回填视觉判断。若模型没有视觉能力，状态保持
`awaiting_ai_visual_review`，不得把本地 CV 结果冒充 AI 视觉结论。

4. 本地 CV 只验证 AI 判断与星场、颜色、主体区域等启发式特征是否一致。
   冲突时优先级为 Header/WCS、高置信 AI 视觉判断、本地 CV。
5. 读取 `references/target_awareness.md` 中对应天体类型的策略。
6. **推荐方式**：使用 `--strength adaptive` + `--target-type` + `--target-name`，让管线自动基于诊断数据选择参数：

```bash
python scripts/pipeline.py input.fits preview.jpg \
  --strength adaptive \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --steps color,stretch,final_color \
  --keep-all
```

若使用 Agent-in-the-loop 模式，本轮只运行首个必要步骤或候选预览，不要直接执行所有步骤。

`adaptive` 预设会自动运行 `analyze.py` 并基于诊断报告的 recommendations 生成配置，覆盖 medium 基底的对应参数。如果已有诊断报告，可显式传入 `--analysis-report report.json` 避免重复分析。

**adaptive 的行为**：以 `medium` 为安全基底，用诊断数据逐项覆盖：DBE 方法/阶数、降噪强度、拉伸因子、星点阈值、HDR、锐化、饱和度。同时自动应用天体类型安全规则（如球状星团禁用去星）。

如果不是发射星云，或需要保守基准，用 `--strength light` 或 `medium`。

### Round 2: 针对问题增强

只加入必要步骤。使用 `adaptive` 预设时，大部分参数已自动优化，AI 只需审查并微调：

| 问题 | 加入/调整 |
|---|---|
| 明显光害/渐晕 | 加 `dbe`，优先低强度；宽场 Hα 背景要谨慎 |
| 极暗但低噪 | 增强 `stretch_factor` 或使用 emission/deep stretch |
| 噪声明显 | 加 `pre_denoise` 或 `final_denoise`，强度保守 |
| 星点压目标 | 加 `star_reduce`；密集星场优先建议外部 StarNet++ |
| 星云太平 | 加 `local_enhance` 或 `enhance`，避免 HDR/CLAHE 光晕 |
| 偏品红/电蓝 | 降低 `saturation`，发射星云保护 Hα/OIII 比例 |
| 核心过曝 | 降低拉伸、提高 HDR、输出更保守版本 |
| 图片扁平、缺少个人风格 | 加 `style`，使用 `--style auto` 或指定 profile |

**优先使用 `--strength adaptive`**，仅在需要覆盖特定参数时使用 `--override-params`：

```bash
python scripts/pipeline.py input.fits output_enhanced.jpg \
  --strength adaptive \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --style auto \
  --style-strength 1.0 \
  --steps color,stretch,final_color,local_enhance,star_reduce,style \
  --override-params '{"stretch_factor":72,"saturation":1.55,"star_reduction":0.25}' \
  --keep-all
```

AI 自动风格选择规则（`--style auto` 时）：
- 发射星云或 `color-mode emission` → `dramatic_nebula`
- 反射星云/暗星云 → `soft_dust`
- 星系 → `galaxy_core`
- 宽场 → `widefield_punch`
- 球状/疏散星团 → `star_cluster`（解析密集恒星，低饱和）
- 未知目标 → `deep_clean`

如果用户明确要求某种风格，使用 `--style <profile>` 覆盖 `auto`。
`--style-strength` 通常保持 `0.8-1.1`，超过 `1.2` 要特别检查颜色和光晕。

### 天体类型安全规则（已自动落实）

管线代码已根据 `target_type` 和 `target_name` 自动执行以下规则，AI 不需要手动禁用步骤：

| 目标类型 | 自动规则 |
|---|---|
| 球状星团 / 疏散星团 | 自动禁用 `star_remove`、`star_reduce`、`star_process`；降噪限制为保守级别 |
| M45 昴星团 | 同上（星点即主体，禁止去星/缩星） |
| M42 猎户座大星云 | 拉伸 ×0.75、HDR +0.15、target_bg ≥0.10，防止核心过曝 |
| 发射星云 | 拉伸 ×0.85、锐化 ×0.7、HDR 若过低则 +0.1 |
| 反射星云 | 拉伸 +20%、HDR -0.1、降噪 +15% |
| 星系 | HDR +0.15、锐化增强 |
| 行星状星云 | HDR +0.15、锐化增强 |
| 暗星云 | 拉伸 +15% |
| 发射星云 + 无梯度 | 自动跳过 DBE（诊断报告驱动） |

### Round 3: 审查和定稿

运行：

```bash
python scripts/quality_metrics.py output_enhanced.jpg
```

若有中间图，重点查看：
- `01_dbe.tif`：是否过减、黑坑、残留梯度。
- `05_stretched_starless.tif` 或拉伸后图：暗部是否浮现，核心是否死白。
- `08_color.tif`：颜色是否真实，背景是否中性。
- 最终 JPG：星点、噪声、结构、伪影。

质量门禁按阶段解释：
- 线性阶段允许存在轻微负背景，重点检查负值像素比例；不要用最终图的
  `median >= 0.015` 规则误判线性数据。
- 最终非线性图通过 `p1`、非正像素比例和背景中位数共同判断是否贴黑。
- 发射星云最终背景偏低只作为审查警告；是否保留真实 Hα/暗尘仍需看图。

密集星场处理：
- `dense/very_dense` 默认优先建议 StarNet++ 外部无星层，并降低星点重混合强度。
- 内置去星质量不达标时保持安全回退，不降低质量阈值强行接受。
- 发射星云去星回退后，自动改用 `masked_ghs` 保护星核和背景。
- 带星图局部增强与最终缩星复用线性星点蒙版，避免强化星核或误缩亮壳层。
- 发射星云颜色阶段保护青色 OIII 候选区域，避免 SCNR 与品红修正误伤。

极暗数据 `masked_ghs`：
- 先按稳健高光分位数归一化，再运行 GHS；默认 `shadow_pctl=0.0`，
  不从低端减黑位。
- `stretch_gamma` 会参与 masked GHS 的暗部预拉伸，推荐 `0.4-0.5`。
- 通用 `stretch_factor` 会映射到安全的 GHS `b=4-12`；若需直接控制，
  使用 `ghs_b`，不要把 `stretch_factor=100` 直接解释成 `b=100`。
- 可通过 `--override-params '{"dbe_method":"skip"}'` 显式跳过 DBE。

若视觉不可用，输出“需人工视觉审查”的清单，不要声称视觉通过。

## 参数闭环

### 参考图定调

用户提供参考成片时，只匹配全局亮度曲线、综合色调、饱和度和有限局部对比，
不得复制参考图结构、纹理、星点或颜色通道。先完成安全基础处理，再运行：

```bash
python scripts/reference_grade.py processed.jpg reference.png final.jpg \
  --strength 0.85 \
  --max-color-gain 1.25 \
  --max-saturation 1.45 \
  --local-contrast 0.10 \
  --match-orientation \
  --report final.reference-grade.json
```

参考图与输入不是同一视场或曝光深度时，只把参考图作为审美约束；不得为了
“看起来一样”制造输入数据中不存在的暗尘、Hα/OIII 结构或高频细节。

极暗线性数据不要直接量化为 16-bit 后交给 StarNet2，否则弱星云与背景会
产生分层和色块。先安全拉伸，再运行 StarNet2。对已拉伸 TIFF/PNG，应保留
StarNet 独立输出的 starless 和 stars 图层，再运行：

```bash
python scripts/enhance_starless.py \
  starless.tif stars.tif output_dir \
  --target-type emission_nebula \
  --target-name NGC6888
```

流程输出 LOW、MEDIUM、HIGH 三档 starless 和最终带星候选，并同步调整星点
主导程度。两个图层必须尺寸、方向和裁切一致；stars 必须是黑背景正向星点层，
不使用“带星图减 starless”的回退。球状星团、疏散星团、一般星团及 M45 会
被拒绝。HIGH 档不能绕过黑位、噪声、光晕、结构连续性和星点门禁。

### 推荐工作流：诊断 → adaptive → 审查 → 微调

```bash
# 1. 诊断（或让 adaptive 自动运行）
python scripts/analyze.py input.fits --output report.json --format readable

# 2. 使用 adaptive 预设，传入诊断报告和目标信息
python scripts/pipeline.py input.fits output.jpg \
  --strength adaptive \
  --analysis-report report.json \
  --target-type emission_nebula \
  --target-name NGC6888 \
  --color-mode emission \
  --style auto \
  --keep-all
```

### 关键 CLI 参数

| 参数 | 作用 |
|---|---|
| `--strength adaptive` | **推荐**。以 medium 为基底，自动读取 analyze.py 诊断报告生成配置。未提供 `--analysis-report` 时会自动运行 analyze.py。 |
| `--analysis-report` | 显式传入 analyze.py 的 JSON 报告路径，避免重复分析。 |
| `--target-type` | 天体类型，触发自动安全规则（禁用危险步骤、参数修正）。 |
| `--target-name` | 天体名称（如 M42、M45），用于激活特定规则（M42 核心保护、M45 禁用去星）。FITS 文件会自动从 OBJECT header 读取。 |
| `--override-params` | JSON 对象，覆盖 adaptive 或预设的任何参数。仅在诊断结果不满意时使用。 |

### `--override-params` 常用键

```json
{
  "dbe_method": "polynomial",
  "dbe_degree": 2,
  "pre_denoise_lum": 0.012,
  "pre_denoise_chroma": 0.035,
  "stretch_factor": 54,
  "target_bg": 0.08,
  "star_threshold": 0.85,
  "star_reduction": 0.3,
  "star_stretch_factor": 12,
  "star_scnr_strength": 0.15,
  "star_combine_strength": 0.9,
  "hdr_strength": 0.35,
  "sharpen_amount": 0.7,
  "saturation": 1.4,
  "style_strength": 1.0,
  "final_denoise_lum": 0.008,
  "final_denoise_chroma": 0.024,
  "shadow_pctl": 0.5,
  "highlight_pctl": 99.9,
  "stretch_gamma": 0.4
}
```

经验初值（`adaptive` 已自动处理大部分）：
- 极暗线性 FITS/XISF：`stretch_factor` 80-140，`stretch_gamma` 0.33-0.45。
- 发射星云 RGB/LP：`color-mode emission`，`saturation` 1.3-1.8，避免全图白平衡。
- 星系：`saturation` 1.1-1.35，`hdr_strength` 0.35-0.6，锐化可略强。
- 反射星云：拉伸可强，饱和和锐化要柔和。
- 球状星团/M45：管线已自动禁用 `star_remove`/`star_reduce`，无需手动操作。

## 星点处理引擎 v2

`scripts/star_tools.py` 已升级为多尺度检测引擎：

### 多尺度检测
- 自动从图像估计 FWHM（2D 高斯拟合亮星候选，MAD 剔除异常值）
- 基于 FWHM 生成 4 个尺度的结构元素：小星(0.4x)、中星(0.7x)、大星(1.1x)、星芒(1.8x)
- 各尺度独立 White Top-hat 检测，合并响应

### 连通域特征过滤
对每个检测到的亮结构计算：
- 面积、等效直径、圆度(4πA/P²)
- 峰值亮度、长宽比、包围盒填充率

自动过滤：
- **热像素**：面积 < π(0.3·FWHM)²
- **星云亮核**：面积 > π(4·FWHM)² 且圆度 < 0.25
- **噪声**：峰值 < 0.3·阈值
- **星云细丝**：长宽比 > 5 且圆度 < 0.3

### 梯度感知阈值
- 计算 Sobel 梯度幅值
- 在星云高梯度区域（亮丝、边缘）自动降低检测响应，避免误检

### 修复方法
- **默认**：OpenCV Telea 快速行进法（小/中星点）
- **大区域/星芒**：自动降级为 Navier-Stokes 流体动力学修复
- **OpenCV 不可用时**：回退到高斯模糊修复

### 置信度系统
`detect_stars_multiscale()` 返回 `confidence`（0-1）：
- 基于保留比例、圆度一致性、尺寸一致性、密度合理性、热像素比例综合评分
- `separate_stars()` 默认 `min_confidence=0.3`
- 置信度 < 0.3 时输出警告，建议改用外部 StarNet++

### CLI 使用
```bash
# 检测并输出掩膜 + 详情
python scripts/star_tools.py detect input.jpg mask.tif --details

# 分离星点（使用 Telea 修复）
python scripts/star_tools.py separate input.jpg starless.tif --method telea

# 使用旧版单尺度检测
python scripts/star_tools.py separate input.jpg starless.tif --legacy
```

## FITS I/O 精度保护（v2）

`scripts/fits_io.py` 已修复两个严重的精度丢失问题：

### 输入：不再使用百分位裁剪
旧版使用 `p0.1-p99.9` 百分位裁剪归一化，导致：
- p0.1 以下的暗部信号被截断为 0
- p99.9 以上的亮部信号（亮星核心、星云饱和区）被压缩到 1
- 原始线性范围和测光信息丢失

**新版行为**：
- 应用 BSCALE/BZERO 标定后，使用绝对值最大值归一化：`data = data / max(|min|, |max|)`
- 保留负值（噪声可以有负值）
- 完整动态范围进入管线，不做任何截断
- 归一化因子 `data_scale` 记录在 meta 中，供输出时恢复

### 输出：float32 替代 uint16
旧版将 `[0,1]` float 乘以 65535 转 uint16，导致：
- 32-bit 浮点精度丢失（仅 16-bit 整数精度）
- 拉伸后的非线性数据被硬编码为 16-bit，语义不匹配
- FITS header 中的 BSCALE/BZERO 不再正确描述数据

**新版行为**：
- 输出 `BITPIX=-32`（IEEE float32）
- `BSCALE=1.0, BZERO=0.0`，header 语义一致
- 添加 `HISTORY` 记录处理信息
- 可选恢复原始 `data_scale`（处理前线性数据时有用）

### 对管线的意义
- 中间 `.tif` 文件继续使用 float32，不受影响
- 最终 FITS 输出现在保留完整的浮点精度，可作为高质量母版
- JPG/PNG 输出行为不变（仍然基于 [0,1] 范围）

## 质量审查标准

数值审查必须运行 `quality_metrics.py`。关键字段：

| 字段 | 用法 |
|---|---|
| `median` | JPG 背景/整体亮度参考，通常 0.03-0.15 |
| `corner_uniformity_ratio` | 角落均匀度；>3 通常说明 DBE/裁切/黑边有问题 |
| `uniform_5x5_dark_patch_ratio` | 暗部过度涂抹风险；过高说明塑料感 |
| `high_frequency_energy_ratio` | 高频变化参考；不能单独判断真实细节 |
| `star_area_ratio` | 星点占比；星云通常应低于星团 |

严肃处理还应按 `references/quality_assessment.md` 补充：
- 在线性母版上做背景、暗弱目标、中亮结构和亮核的分区 SNR。
- 用未饱和孤立恒星统计 FWHM、长短轴、圆度/偏心率及全场空间趋势。
- 有 WCS 和测光星表时评估恒星颜色残差；否则只能标注为启发式色彩审查。
- 结合 WCS 像素尺度、FWHM、口径和波长评估实际分辨率与采样，不以输出像素数或锐化后高频能量代替真实分辨率。

视觉审查优先级：
1. 背景：不能有明显光害带、黑坑、拼接缝。
2. 目标：结构应来自原图，不能像生成纹理。
3. 星点：大小自然，不能有黑洞、紫边、硬边。
4. 色彩：Hα 深红不品红，OIII 青蓝不电蓝，星系自然黄核蓝臂。
5. 动态范围：核心不过曝，外围不断层。

失败时必须重跑，不要硬宣布完成：
- DBE 失败：降低 degree、换 `median`、跳过 DBE 或只裁黑边。
- 拉伸过强：降低 `stretch_factor` 或提高 `stretch_gamma`。
- 噪声塑料感：降低降噪，输出保守版。
- 颜色失真：降低 `saturation`，发射星云使用 `emission` 模式。
- 风格过重：降低 `--style-strength`，或改用 `natural`/`deep_clean`。
- 星点处理伪影：检查 `04_starless_linear.tif`，若残留星多或星云误伤严重，使用 `--external-starless` 传入 StarNet++ 无星图。也可运行 `star_tools.py detect` 查看检测置信度。
- 去星置信度过低（<0.3）：检测输出会提示原因（热像素过多/过检/全部拒绝），此时应优先使用外部 StarNet++。

## 输出格式

默认输出：
- 最终 JPG：用户要看的成片。
- 如用户没有拒绝，保留 `--keep-all` 的工作目录，里面有中间 TIFF 和 `manifest.json`。
- 对严肃处理，额外输出 16-bit/float TIFF 母版。
- 自动化调用使用 `--result-json result.json` 获取统一状态、有效配置、质量门禁和警告。

状态含义：
- `success`：数值门禁未发现明确风险，仍需完成视觉审查。
- `partial_success`：主图已生成，但识别等可选阶段失败。
- `review_required`：产物保留，必须由视觉 Critic 检查或调整后重跑。
- `failed`：输入、配置或执行失败。

处理完成后，按以下格式输出最终结果：

```markdown
## ✅ NGC6888 后期处理完成

| 项目 | 详情 |
|------|------|
| 输入 | FITS (SeeStar S50, 300s×45, Gain 120) |
| 目标类型 | 发射星云 (NGC6888, Crescent Nebula) |
| 风格 | enhanced / dramatic_nebula (style-strength: 1.0) |
| 预设 | adaptive (stretch_factor=76, saturation=1.52) |

**关键步骤**：analyze → adaptive pipeline (dbe + color + stretch +
final_color + star_reduce + local_enhance + style)

**输出文件**：
- 主图：`NGC6888_enhanced.jpg` (2048×1365, 982 KB)
- 自然基准：`NGC6888_natural.jpg` (2048×1365, 856 KB)

**质量审查**：
- median: 0.087 ✅
- corner_uniformity: 1.83 ✅
- star_area: 0.0024 ✅
- 视觉审查：背景均匀 ✅ | 星点自然 ✅ | Hα 深红保真 ✅ |
  核心无过曝 ✅ | 无伪影 ✅
- 状态：success
```

## 支持格式

| 格式 | 输入 | 输出 | 说明 |
|---|---|---|---|
| FITS `.fit/.fits/.fts` | 是 | 是 | 线性叠加图；脚本会归一化显示处理 |
| XISF `.xisf` | 是 | 否 | PixInsight 格式；需安装 `xisf` |
| TIFF `.tif/.tiff` | 是 | 是 | 推荐作为母版 |
| PNG/JPG | 是 | 是 | 已非线性/压缩，处理要保守 |

## 何时读取 references

- 阶段动作、候选比较和 Critic 返回协议：`references/agent_protocol.md`
- FITS/XISF 拍摄元数据、物理先验和天区解析：`references/physical_metadata.md`
- 多尺度亮度/色相/星点蒙版与局部算子：`references/mask_workflow.md`
- 目标类型策略：`references/target_awareness.md`
- 坏点修复、局部归一化、梯度诊断与宽场 DBE：`references/linear_stage_processing.md`
- 窄带合成：`references/narrowband_synthesis.md`
- LRGB 合成：`references/lrgb_synthesis.md`
- 多曝光 HDR 合成：`references/hdr_composition.md`
- 彗星、行星、月面、宽场星野和超新星残骸：`references/special_targets.md`
- 分区 SNR、星点圆度、星表色差与分辨率评估：`references/quality_assessment.md`
- Siril、PixInsight、StarNet++、外部降噪等集成：`references/external_tools.md`
- AI 工具边界：`references/ai_hybrid_workflow.md`
- 常见误判：`references/ai_common_pitfalls.md`
- 真实案例：`references/case_ngc6888_rgb.md`

## 环境

首次安装：

```bash
/opt/homebrew/bin/python3 -m venv ~/.workbuddy/binaries/python/envs/sys-default
~/.workbuddy/binaries/python/envs/sys-default/bin/pip install -r requirements.txt
```

运行脚本时优先使用已安装依赖的 Python；macOS managed Python 若有签名问题，使用 `/opt/homebrew/bin/python3`。
