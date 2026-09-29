---
name: deep-sky-processor
description: |
  AI-directed deep-sky astrophotography post-processing assistant under strict authenticity constraints. Processes stacked FITS, XISF, or TIFF masters into natural and enhanced JPG/TIFF results. Use when the user asks to stretch, denoise, remove light pollution/gradients, calibrate color, reduce stars, enhance DSO details, or execute end-to-end post-processing for nebulae, galaxies, or star clusters. Do not use for camera RAW stacking or general non-astronomical photo retouching.
  AI 主导的深空天文后期助手。在严苛真实性约束下，将已叠加的深空 FITS/XISF/TIFF 母版通过拉伸、去光害、校色、降噪、缩星与细节增强处理为自然版和增强版 JPG/TIFF 成片。
license: Proprietary
metadata:
  slug: deep-sky-processor
  version: "0.1.20"
  displayName: Deep Sky Processor
  summary: 在真实性约束和分阶段审查下处理深空图像，交付自然版与增强版成片。
  tags: [astronomy, astrophotography, image-processing, fits]
  homepage: https://github.com/MZqk/deepsky-skill
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
- `stellar_repair`（RGB 亚像素对齐 + 亮星紫晕抑制 + PSF 形变修复，仅限线性阶段）
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

星点链路（`star_process` 内部顺序）：

```
S1 星点拉伸(arcsinh) → S2 去紫(SCNR) → S2b 保亮度饱和补偿 → S3 曲线微调 + 缩星
```

- **S2b 是必需的**：arcsinh 压缩色度、SCNR 又把 Lab a 推向 0，两者叠加是最大
  损耗源，而 S3 的曲线用亮度增益乘回 RGB（保比例、不改饱和）、缩星只做腐蚀，
  都不会撤销它。外部经验要求「饱和必须在拉伸之后、且要足够激进」。
  由 `star_saturation` 控制（默认 `1.08`，`1.0` = 关闭）。公式保亮度，
  只在色度放大后越界裁切时才会轻微改变亮度（仅影响近零通道的极饱和星核）。
- **S1 的 `star_stretch_factor` 必须与主拉伸因子同量纲**。预设把两者成对给出
  （`light` 25→88、`medium` 45→132、`strong` 80→198、`adaptive`/`emission` 120→264/198），
  比值 1.65~3.52。历史上该值曾被设成 8/12/24/18 一档，导致星点层 p99 只有 0.0067、
  暗星整体沉进背景（见 `CHANGELOG.md` 的「提升 11 倍」条目）。
  **分析报告这条路径曾漏改**：`analyze.py` 写过 `stretch_factor * 0.25`，给出 9.375，
  而预设同档是 88 —— 星点层亮度只有预设的 1/4.7。现已改为按预设配对插值
  （`_star_stretch_factor_for`），并夹到 `[88, 264]`。**改这条链时两处都要看。**
  产物 `05b2_star_saturation.tif`。
- 星点即主体的目标（星团 / M45）已被安全规则移除 `star_process`，不进入该链路。

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

这些 profile 只做色调曲线、背景压暗、**保比例饱和度提升**、背景去饱和、局部对比、轻微色彩分离和高光 rolloff，不生成新结构。

饱和度提升以**像素均值为轴**缩放色度（`new = mean + (x - mean) * k`，与 `color_separation` 同一公式），保亮度、保通道比例。**不要**改用 HSV 的 S 乘法——它保持 max 通道不变、把 `(max−min)` 拉开，当 G≠B 时两个非最大通道被**不等比例**压向 0：实测受控输入 `[0.25,0.10,0.06]` 经 ×1.32 后 B 直接归零（B/G 保留率 **0%**），对双窄带数据即系统性破坏 OIII。

**同一条规则适用于 Phase 8 `final_color` 的 `enhance_saturation`**（`color_tools.py`）。它此前也是 HSV 的 S 乘法且因子更高（1.45），破坏更重——实测受控输入 `[0.25,0.10,0.06]` 的 B/G 从 0.600 被压到 **0.088**。现已改为同一保比例公式，并用**每像素色域上限**（而非末尾"底电平守护"的均匀抬亮补丁）保证不越界、逐像素均值严格守恒。两处一起改后，真实母版成片 `B/G 0.48 → 0.72`、`S 0.944 → 0.810`、`R/G 2.22 → 1.77`。

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

### HDR 与 CLAHE 二选一（`enhance_mode`）

外部权威经验：「**HDRMT 与 LHE 同时使用太过头，二选一**」；「LHE amount > 0.5 开始假」；
「一趟 LHE 通常就够」；「dual LHE + HDRMT = 过度处理的'设计感'」。本地 Phase 6 因此
支持条件触发（`enhance_mode`，默认 `auto`）：

| 条件 | mode | 说明 |
|---|---|---|
| `highlight_clip ≥ 0.002` 或 `p99 ≥ 0.95`，且 `p50 < 0.12` | `both` | 高光过曝 + 暗背景，两者都要 |
| `highlight_clip ≥ 0.002` 或 `p99 ≥ 0.95`，且 `p50 ≥ 0.12` | `hdr_only` | 背景已亮，再叠 CLAHE 会放大噪声 |
| `p99 < 0.80` | `clahe_only` | 没有可压的高光，HDR 近似空操作 |
| `span = p99 − p50 < 0.22` | `clahe_only` | 全局层次不足，优先局部对比 |
| 其余（含信号缺失） | `both` | **保守兜底 = 历史行为** |

判定信号在 Phase 6 开头**现算**（不用 `post_health`——它只在 `stretch` 在 steps 里时存在）。
`enhance_mode` 可用 `--override-params` 强制。

**CLAHE 亮核保护**：`clahe_mask_gamma`（默认 2.0）在 `[p90, p99.9]` 区间内逐步降低
局部均衡权重，`p90` 以下权重恒为 1——只保护亮端，背景与主体不受影响（实测某星云占满
画幅的图 median 变化 +1.9%）。**不要**把掩版写成上升型幂版 `((L-lo)/(hi-lo))**g`：
那会在整个中低亮度区间给出接近 0 的权重，等于把 CLAHE 全图关掉（实测 median 掉 25%）。

### 噪声守卫：低 SNR 时不要跑 CLAHE

`decide_enhance_mode` 的 span 分支会为「层次不足」的图选 `clahe_only`。但**光看 span
分不出来**该不该跑 CLAHE —— 实测同一批数据里 `span = 0.2187` 的几张是有结构的（跑
CLAHE 无害），而 `span = 0.0814` 的才是**噪声主导**（跑 CLAHE 出蜂窝斑块）。

判据必须是 SNR。Phase 6 现算 `texture_snr = 星云区高通能量 / 背景区高通能量`
（背景区只有噪声，即噪声地板）：

| `texture_snr` | 含义 | 决策 |
|---|---|---|
| `< 1.7` | 星云区不比噪声多出结构 | **削掉 CLAHE**（`both`→`hdr_only`，`clahe_only`→`none`） |
| `≥ 1.7` | 有可揭示的纹理 | 保持原决策 |

实测分离度：出伪影的输入 **1.24~1.35**，干净的 **2.32~5.26**。触发时日志会打印
「星云区纹理/背景噪声 = X < 1.7，判定为噪声主导」并给出降级前后的模式。

### 第二条守卫：CLAHE 高频放大实测

`clip_limit` **不是**可用的强度旋钮 —— 在**窄直方图**上它会饱和。实测某极暗发射星云
（span=0.39）上 `clip_limit` 从 0.0003 到 0.003 给出**逐位相同**的输出（HF 放大恒为
1.85×、p75 恒为 0.440），只有到 0.006 才变化。**所以只能二选一，不能"调小一点"。**

因此 Phase 6 在下采样预览（≤640px）上**实测** CLAHE 会把星云区高频放大多少倍，
超过 `CLAHE_MAX_HF_AMPLIFICATION`（默认 **1.5**）就跳过，并把实测值写进
`hdr_report.clahe_hf_amplification` 供审计。

实测收益（真实母版）：跳过 CLAHE 后成片 **p75 由 0.381 降到 0.173（−55%）**、
**HF 比由 6.74 降到 3.62（−46%）**，大幅贴近参考成片。

> ⚠️ 该阈值目前只在**单一案例**上标定过（该例实测放大 1.92×）。它是保守取值，
> 随样本积累应复核。

**为什么必须跳而不是调参**：CLAHE 的局部直方图均衡会把噪声一起均衡掉 —— 实测把星云区
高通能量放大 **4.24×**，成片出现灰色蜂窝斑块（`NGC2237_fixed.jpg` 那种）。调小
`clip_limit` 或换 `kernel_size` 都只是缓解；噪声主导时它没有任何可揭示的结构。

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

### 双窄带哈勃假彩色、线性反卷积与物理守卫（三大梯队演进）

系统内置纯 Python / NumPy / SciPy 实现的进阶天体物理处理链，无需安装额外第三方工具：

1. **梯队 1：双窄带哈勃假彩色色板合成 (`--palette`)**：
   - 算法：基于 Carlo Mollicone (AstroBOH) 经典双窄带转哈勃假彩色算法；
   - 通道解离与合成硫二：从双窄带（Hα+OIII）提取 Hα 与 OIII，通过梯队分布合成 $S=(H+O)/2$；
   - 支持色板：`SHO`（经典哈勃金色星云+深蓝空腔）、`HSO`、`HOO`（自然双窄带基准）、`OSH`、`OHS`、`HOS`；
   - **保亮度色域重映射（Luminance-Preserving Gamut Mapping）**：在重映射色板时 100% 保持底图 Rec.709 感官亮度与对比度结构不变，仅平滑置换色彩。

2. **梯队 2：线性域物理反卷积 (`--linear-deconv`)**：
   - 原理：在非线性拉伸之前（Phase 2c）执行正则化 Richardson-Lucy 反卷积，还原大气视宁度和光学衍射丢失的高频极限；
   - 自适应 PSF 核：基于实测 FWHM 自动构建高斯/Moffat 卷积核；
   - 安全门禁：信号自适应权重掩膜（保护纯暗背景不发散）与高光防吉布斯振铃门禁。参数 `--deconv-iterations 8~12`。

3. **梯队 3：叠边自动裁切与星晕守卫 (`--stacking-crop`, `--halo-guard`)**：
   - **抖动叠边裁切 (`--stacking-crop auto`)**：扫描行/列统计一致性，自动切除场旋与抖动带来的低信噪比边缘色差断崖，避免带偏 DBE 和 PCC；
   - **星晕守卫 (`--halo-guard`)**：去星后在亮星周围 2~4 倍 FWHM 区域平滑色度，彻底平整强色差折射镜造成的假彩色星晕，保持背景亮度不变。

### 天体类型安全规则（已自动落实）

管线代码已根据 `target_type` 和 `target_name` 自动执行以下规则，AI 不需要手动禁用步骤：

| 目标类型 | 自动规则 |
|---|---|
| 球状星团 / 疏散星团 | 自动禁用 `star_remove`、`star_reduce`、`star_process`、`star_combine`；降噪限制为保守级别（亮度 ≤0.01/0.005，色彩 ≤0.03/0.015）；饱和度上限 1.25 |
| M45 昴星团 | 同上（星点即主体，禁止去星/缩星），另保留反射星云的拉伸/HDR 调校 |
| M42 猎户座大星云 | 拉伸 ×0.75（**独占**，不再叠加发射星云的 ×0.85）、HDR +0.15、target_bg ≥0.10；锐化 ×0.7 仍生效 |
| 发射星云（非 M42） | 拉伸 ×0.85、锐化 ×0.7、HDR 若过低则 +0.1 |
| 反射星云 | 拉伸 +20%、HDR -0.1、降噪 +15% |
| 星系 | HDR +0.15、锐化增强；**缩星上限 0.25**（HII 区/旋臂结保护） |
| 行星状星云 | HDR +0.15、锐化增强 |
| 暗星云 | 拉伸 +15% |
| 发射星云 + 无梯度 | 自动跳过 DBE（诊断报告驱动） |

拉伸因子采用**单一归属**：优先级为 M42 > 发射星云 > 反射星云 > 暗星云，只乘一次；非拉伸修正（锐化/HDR/降噪/饱和）仍按规则叠加。

名称判定统一走 `scripts/target_rules.py`，唯一事实源为 `fits_io.LOCAL_CELESTIAL_DB`，因此 `M 42`、`NGC 6888`、`ngc-6888`、`Crescent Nebula` 等价，且为精确匹配（`M8` 不会误匹配 `M81`）。只给 `--target-name` 也能激活星系/行星状/暗星云规则。

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

GHS 两端保护 `ghs_lp` / `ghs_hp`（默认 `0.0` / `1.0`，行为与不设置完全一致）：
- `lp`（阴影锚点）与 `hp`（高光锚点）定义曲线的**作用窗口**：窗口内走 GHS
  基准曲线，窗口外以边界处的切线线性延伸（只做等比缩放，不引入非线性扭曲），
  最后仿射归一化使 `f(0)=0`、`f(1)=1`。
- **`ghs_lp` 治「拉伸坍缩」**：极暗数据的真实动态范围常只占 `[0.002, 0.03]`，
  而裸 GHS 的归一化锚定在固定的 `[0,1]`，导致暗部增益极低（实测压缩 172 倍），
  整幅被压成一条窄带。把 `lp` 设为背景水平（如 `0.001`）即可重新锚定。
- **`ghs_hp` 治「核心过曝」**：它是**拉伸阶段的高光 headroom 旋钮**，因果上
  早于 HDR 阶段。设为真实最亮结构（如 `p99.9`）可为亮核留出余量，并让离群亮
  像素不再主导整条曲线。注意两条路径方向不同——裸 `ghs` 下 `hp` 越小核心落点
  越高，`masked_ghs` 下 `hp` 是归一化除数、越小核心落点越低。
- 建议成对使用：极暗数据 `{"ghs_lp": 0.001, "ghs_hp": 0.05, "ghs_sp": 0.005}`
  实测可把 `stretch_recovery` 从 `failed` 转为正常，暗部均匀斑块比从 0.50 降到 0。
- `ghs_hp <= ghs_lp` 视为退化窗口，管线会告警并回退为不启用。

### 内层 GHS 切线保护 `ghs_protect_lp` / `ghs_protect_hp` / `ghs_c`

`masked_ghs` 路径**另有**一套内层保护，与外层 `ghs_lp`/`ghs_hp` **语义不同**，
因此是独立键（默认 `None` = 不启用，输出与旧实现逐位一致）：

| 键 | 语义 | 空间 | 默认 |
|---|---|---|---|
| `ghs_lp` / `ghs_hp` | **重锚点**（`source=lp→0`、`source=hp→1`） | 绝对输入单位 | `None` |
| `ghs_protect_lp` / `ghs_protect_hp` | 内层 GHS 的**两端切线保护锚点** | 归一化 `[0,1]` | `None` |
| `ghs_c` | GHS 输出后的高光 rolloff（域无关） | 曲线输出 `[0,1]` | `0.0` |

- **为什么不能复用外层键**：外层 `lp/hp` 作用在归一化**之前**；内层 `ghs_stretch`
  看到的已经是 `normalized`。原样透传量纲不符；按 `((v-low)/(high-low))` 换算又会
  退化为 `(0,1)`（等于无保护）。故必须两个独立旋钮。
- 取值可为 `None` / `"auto"` / 浮点。`"auto"` 按归一化背景中值与 `p99.5` 自动推导
  （实测量级 `protect_lp≈0.02~0.08`、`protect_hp≈0.70~0.95`）。
- `ghs_protect_hp <= ghs_protect_lp` 时告警并禁用内层保护。
- 典型用法：极暗/发射星云需要「锁住已建立的暗部层次、同时给亮核留 headroom」时，
  `--override-params '{"ghs_protect_lp":"auto","ghs_protect_hp":"auto"}'`。

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

#### 信号区色比匹配（`--reference-color-match` / `--reference-color-only`）

全局定调的搜索空间是 stretch/gamma/target_bg/saturation/hdr —— **里面没有色比控制**，
所以它**修不了 Hα/OIII 比**（双窄带数据的核心色彩指标）。实测它会把源 R/G 从 1.77
冲到 **0.79**（目标 1.37）而 B/G 完全不动（0.73）。`_background_channel_gains` 只看
背景（luma ≤ p60），同样不解决星云主体。

补一条**闭式解**通道增益：

```
gR = (R/G)_ref / (R/G)_src ,  gG = 1 ,  gB = (B/G)_ref / (B/G)_src
```

色比取星云主体（luma > p55）的逐通道 p99。实测某极暗母版：源 R/G=1.769 / B/G=0.727，
参考 R/G=1.375 / B/G=0.993 → 解出 `[0.777, 1.0, 1.365]`，命中后 R/G 误差 **0.0%**、
B/G 误差 **0.1%**。是**确定性闭式解，无搜索、无生成**。

```bash
# 全局定调 + 色比匹配（推荐：全局定调同时改善 tone 与星点亮度）
python scripts/pipeline.py input.fits out.jpg \
  --reference-image 参考成片.jpg --reference-color-match

# 只做色比匹配，跳过全局亮度/色调/饱和度定调
python scripts/pipeline.py input.fits out.jpg \
  --reference-image 参考成片.jpg --reference-color-only
```

**两者的取舍（实测）**：`--reference-color-match` 在色彩与星点亮度上更接近参考
（R/G 1.28 / B/G 0.98 / S 0.602 / 亮星 0.9635，参考 1.38 / 0.99 / 0.643 / 0.9694），
但全局定调会把星核推到近饱和，可能新增 `CORE_BURNING` 告警 —— 注意**参考成片自身
往往就是近饱和的**（本例参考的亮星中位 0.9694）。`--reference-color-only` 只改色比，
不碰 tone，代价是星点亮度与 R/G 不如前者贴近参考。

报告（`reference_grade.signal_color_match`）记录源/参考/结果三方色比与原始解、限幅后、
按 strength 混合后的增益，可直接审计。

极暗线性数据不要直接量化为 16-bit 后交给 StarNet2，否则弱星云与背景会
产生分层和色块。先安全拉伸，再运行 StarNet2。

**「安全拉伸」必须是 MTF，不能用 GHS / 反正弦**（见 `references/external_tools.md`：
GHS/arcsinh 会让 StarNet/SXT 误判星点为小星系）。实测某极暗 Duo-Band 母版的
星云区 G 通道只有 23 counts（占 16-bit 满量程 0.035%），线性直接量化后 StarNet2
的输出在**所有低于 200 counts 的亮度档把 G 100% 归零**，拉伸后整片星云变纯红
（成片 R/G 从 1.70 涨到 9.1）。先做 MTF 拉伸后同一命令下 G 归零率 99.4% → 0.05%，
通道比值全程保持。

桥接命令已默认走这条路（`--export-starnet-payload-domain` 默认 `mtf`）：

```bash
# 1. 导出**已 MTF 拉伸**的 16-bit 载荷，并写 starnet_payload_meta.json 记录域与 midtones
python scripts/pipeline.py input.fits out.jpg --steps dbe,pre_denoise,star_remove,stretch \
  --export-starnet-payload --work-dir work --keep-all
#    打印出的 external_starnet_command 形如：
#    starnet2 -i .../starnet_payload_stretched.tif -o .../04_starless_external.tif \
#             -n .../stars_unscreen.tif -s 128
# 2. 原样执行该命令（外部桥接默认 stride=128；内置路径仍是 --starnet-stride 256）
# 3. 回流：work-dir 里的 sidecar 会自动识别 mtf 域并做逆 MTF，无需手传参数
python scripts/pipeline.py input.fits out.jpg --steps dbe,pre_denoise,star_remove,stretch \
  --external-starless work/04_starless_external.tif --work-dir work --keep-all
```

相关键：`--export-starnet-payload-domain {linear,mtf}`（默认 `mtf`）、
`--external-starless-domain {linear,mtf}`（默认 `linear`，兼容旧输入；有 sidecar
时自动识别）、`--external-starless-midtones`（缺省按背景推导，**必须与导出时一致**）。

**内置 `--use-starnet` 路径同样走 MTF 域**（`--starnet-domain`，默认 `mtf`），
读回后自动逆变换回线性。两条路径的 stride 默认值已统一为 **128**（`--starnet-stride`）。

> 实测对比（同一极暗母版）：旧版内置路径因弱色通道被量化归零，输出
> `nebula_damage_ratio = 0.5521` → 超过接受门 `≤ 0.20` → **回退形态学**（StarNet2 白跑）。
> 改走 MTF 域后 `damage = 0.0`、`score = 0.996`、`accepted = True`、**不再回退**。
> **接受门没有被放宽** —— 是输出真的变健康了。

**回流时会做通道完整性硬校验**：若外部无星层在星云信号区大范围丢失某个通道
（实测故障形态是 G 归零 99%），管线直接 `raise ValueError` 并给出通道名、相对
强度与归零比例，不会静默产出纯红成片。窄带图天然缺通道（原图该通道相对最强
通道 <5%）不判塌缩。

对已拉伸 TIFF/PNG，应保留 StarNet 独立输出的 starless 和 stars 图层，再运行：

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
  "star_stretch_factor": 149,
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
  "stretch_gamma": 0.4,
  "ghs_lp": 0.0,
  "ghs_hp": 1.0,
  "ghs_protect_lp": null,
  "ghs_protect_hp": null,
  "ghs_c": 0.0,
  "clahe_mask_gamma": 2.0,
  "clahe_mask_low_pctl": 90.0,
  "clahe_mask_high_pctl": 99.9,
  "enhance_mode": "auto",
  "star_saturation": 1.08
}
```

`ghs_lp` / `ghs_hp` 是 GHS 的阴影/高光锚点，默认 `0.0` / `1.0`（即不启用，
行为与不设置这两个键完全一致）。仅在需要修复极暗拉伸坍缩或为亮核留高光
余量时设置，取值参见上文「GHS 两端保护」一节。内层保护与 `ghs_c` 见
「内层 GHS 切线保护」一节。

`clahe_mask_gamma` / `clahe_mask_low_pctl` / `clahe_mask_high_pctl`：
CLAHE 的**自适应高光渐弱掩版**。在 `[p_low, p_high]` 分位区间内按 `taper**gamma`
逐步降低局部均衡权重，**p_low 以下权重恒为 1**（背景与星云主体不受影响）。
用于保护亮核不被局部均衡压平成无特征斑块。`clahe_mask_gamma=0` 一键回到历史行为。

`enhance_mode`：`auto` / `both` / `hdr_only` / `clahe_only` / `none`。
见下文「HDR 与 CLAHE 二选一」。

`star_saturation`：星点层保亮度饱和补偿（默认 `1.08`，`1.0` = 关闭）。
见下文「星点链路」。

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
- BSCALE/BZERO 由 astropy 在 `fits.open()` 阶段按 FITS 标准自动应用（含 `BZERO=2**(BITPIX-1)` 的伪无符号 uint16 约定），**管线不再手动叠加**；header 原值仅记录在 `scale_info`/`meta` 中供溯源
- 使用绝对值最大值归一化：`data = data / max(|min|, |max|)`
- 保留负值（噪声可以有负值）
- 完整动态范围进入管线，不做任何截断
- 归一化因子 `data_scale` 记录在 meta 中，供输出时恢复

### DBE 阶段的归一化（v0.1.5 起）

黑点取**背景稳健中心**（逐通道 DBE 后背景以 0 为中心、噪声对称），
白点**锚定真实峰值**，并保留一个 3σ 的 pedestal 让背景噪声完整通过。

- 旧的 `p99.7` 白点对「亮核很小、暗晕很大」的天体是灾难：该分位落在很暗的外缘
  （实测 M31 的 p99.7 比峰值低 36.6 倍），等于把整幅图放大 ~37 倍 ——
  噪声被放大到与星点可比，星点检测会把噪声当成星。
- 旧的黑点是「正值像素的 p0.3」，等于在 `clip(0)` 之后再抬一次黑位，
  会把噪声底整个裁掉（实测 28~44% 画面变纯 0）。
- 配置项 `dbe_pctl_low` 已不再作为黑点使用（保留仅为兼容既有配置）；
  `dbe_pctl_high` 仅作为白点的下界保留。

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

具名星点伪影门禁（`scripts/artifact_gates.py`）：
- **两条路径都会运行**：Agent-in-the-loop 的 review bundle（`review.json` 的
  `star_artifact_gates`），以及 `pipeline.py` 主管线（`result.json` 的同名顶层键）。
  主管线在 Phase 9 星点合成后快照一张**同域**参照图，末端与成品成对比较，因此
  `STAR_BLOAT`/`STAR_LAYER_LOSS`/`STAR_HOLES` 都能跑（此前它们只在会话路径生效，
  导致星点胀大在主管线里无人报警）。
- **STAR_RINGING**：亮星周围暗环（振铃/黑环），定位到具体星坐标和环半径。
- **STAR_BLOAT**：星点整体胀大，复用 `measure_paired_star_profiles` 的成对 FWHM 比。
- **STAR_LAYER_LOSS / STAR_HOLES**：星点层保留率不足或出现暗坑，定位到具体星坐标。
- **CORE_BURNING**：处理引入的死白连通域，区分"输入自带饱和"与"处理烧毁"。
- 这些门禁与标量门禁（BACKGROUND_CRUSHED / CORNER_NONUNIFORM 等）**并存**：数值门禁是审查触发器，具名门禁提供定位证据。两者都不声称"视觉质量合格"——最终判断仍由 AI/human critic 完成。
- 主管线路径下，具名门禁触发是**加法式**升级：只在原本 `success` 时升级为
  `review_required`，且只把 `escalate=True` 的 warning/failed 写进 `warnings`。

标量门禁 `INPAINT_FOOTPRINT`（去星修补足迹）：
- 盯住「线性域 inpaint 补丁被拉伸放大成可见斑块」。判据是**联合**的：补丁缺纹理
  （核心 std < 35% × 环带 std）**且**与邻域有亮度落差（|Δ| ≥ 0.020）。
  单独任一条都会大量误报（实测只看落差命中 63%、只看纹理 36%；联合命中 ~10%）。
- 量的是**合成星点之后**的成品（斑块是星点层 screen 混合后才出现的），星位来自
  线性域星点蒙版，FWHM 必须用**线性域**实测值。
- `warning`（命中 ≥2%）只记录不升级；`failed`（≥8%）升级为 `review_required`。

失败时必须重跑，不要硬宣布完成：
- DBE 失败：降低 degree、换 `median`、跳过 DBE 或只裁黑边。
- 拉伸过强：降低 `stretch_factor` 或提高 `stretch_gamma`。
- 噪声塑料感：降低降噪，输出保守版。
- 颜色失真：降低 `saturation`，发射星云使用 `emission` 模式。
- 风格过重：降低 `--style-strength`，或改用 `natural`/`deep_clean`。
- 星点处理伪影：检查 `04_starless_linear.tif`，若残留星多或星云误伤严重，使用 `--external-starless` 传入 StarNet++ 无星图。也可运行 `star_tools.py detect` 查看检测置信度。
- 去星置信度过低（<0.3）：检测输出会提示原因（热像素过多/过检/全部拒绝），此时应优先使用外部 StarNet++。
- **具名伪影门禁触发**：`review.json` 中 `star_artifact_gates` 出现 failed/warning 时，按门禁码定位具体问题：
  - `STAR_RINGING` → 检查 `--deconv-iterations` 和 `sharpen_amount`，考虑降低或跳过锐化。
  - `STAR_BLOAT` → 检查拉伸方法（是否裸 STF？），改用 `masked_ghs` 或加 `ghs_hp`。
  - `STAR_LAYER_LOSS` / `STAR_HOLES` → 检查去星/缩星强度，回落 `star_reduction` 或改用外部 StarNet++。
  - `CORE_BURNING` → 检查 `ghs_hp` 是否过低，或降低 `sharpen_amount` / HDR 强度。
  - `INPAINT_FOOTPRINT` → 去星修补足迹被拉伸放大。优先改用外部 StarNet++
    （`--use-starnet` / `--external-starless`），或调整内部 inpaint 半径与去星蒙版阈值。
    注意：该门禁的灵敏度受**星点蒙版质量**限制——若 `detect_stars` 的蒙版里混入
    大量星云大块，采样位置会偏离真实星点，命中率会被压低。

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
- CV 护栏、浮点图像状态契约与算子边界：`references/cv_guardrails.md`
- 线性星点像差、色散与形变修复：`references/stellar_repair.md`
- 常见误判：`references/ai_common_pitfalls.md`
- 真实案例：`references/case_ngc6888_rgb.md`

## 环境

首次安装：

```bash
python3.12 -m venv deep-sky-processor/.venv
deep-sky-processor/.venv/bin/python -m pip install \
  -r deep-sky-processor/requirements.txt
```

以上命令从仓库根目录执行；运行脚本时使用该 Skill 自己的 `.venv/bin/python`。
