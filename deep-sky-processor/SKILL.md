---
name: deep-sky-processor
description: |
  AI-directed deep-sky astrophotography post-processing assistant under strict authenticity constraints. Processes stacked FITS, XISF, or TIFF masters into natural and enhanced JPG/TIFF results. Use when the user asks to stretch, denoise, remove light pollution/gradients, calibrate color, reduce stars, enhance DSO details, or execute end-to-end post-processing for nebulae, galaxies, or star clusters. Do not use for camera RAW stacking or general non-astronomical photo retouching.
  AI 主导的深空天文后期助手。在严苛真实性约束下，将已叠加的深空 FITS/XISF/TIFF 母版通过拉伸、去光害、校色、降噪、缩星与细节增强处理为自然版和增强版 JPG/TIFF 成片。
license: Proprietary
allowed-tools: Bash, Read, Write, Glob, Grep
metadata:
  slug: deep-sky-processor
  version: "0.1.41"
  displayName: Deep Sky Processor
  summary: 在真实性约束和分阶段审查下处理深空图像，交付自然版与增强版成片。
  tags: [astronomy, astrophotography, image-processing, fits]
  homepage: https://github.com/MZqk/deepsky-skill
---

# Deep Sky Processor

**AI 主导、代码辅助、真实约束** 的深空后期 skill，不是 PixInsight/Siril 的替代品。

## 目录

- [定位与职责](#定位与职责)
- [真实性红线](#真实性红线)
- [Agent-in-the-loop 模式](#agent-in-the-loop-模式)
- [三轮流程](#三轮流程)
- [阶段要点](#阶段要点)
- [参数闭环](#参数闭环)
- [质量审查标准](#质量审查标准)
- [输出格式](#输出格式)
- [支持格式](#支持格式)
- [何时读取 references](#何时读取-references)
- [环境](#环境)

## 定位与职责

AI 的职责：判断天体类型、问题和审美方向；先表达视觉意图，再选择最小必要步骤和参数；
每个高风险阶段后查看前后图、差异图和质量门禁；决定接受、重试、生成候选、回退或请求
人工审查；输出真实但更好看的 `jpg`，必要时同时保留 `tif` 母版。

脚本的职责：读取 FITS/XISF/TIFF/PNG/JPG；提供亮度、噪声、梯度、星点、锐度、色彩等诊断；
执行可控的非生成式变换（拉伸、启发式校色、背景均衡、降噪、缩星、局部对比、饱和度调整）；
输出中间图和质量指标供 AI 审查。

**触发**：深空后期、天文后期、处理深空照片、处理 FITS/XISF、拉伸星云、去光害、缩星、
降噪星云、星云美化、NGC6888 / M42 / IC 2177 / 星系后期处理；或用户提及
`.fits` / `.xisf` / `.fit` / `.fts` 与深空天体名称。
**不适用**：普通风景照、人像、日常摄影后期（应使用通用图像处理工具）。

## 真实性红线

必须遵守：
- 不生成图中不存在的星云纹理、尘埃、星点或颜色。
- 不使用 AI 超分辨率、AI 着色、AI 纹理生成、参考图重绘。
- 可以使用外部 AI 降噪和去星工具，但只作为统计建模/分类工具，结果必须审查。
- 单张 RGB/LP 数据不能伪造 SHO/HOO 窄带色彩。
- “美化”只能来自拉伸、黑位、曲线、局部对比、饱和度、降噪和星点比例的调整。

允许的风格词：`natural`（真实自然，低饱和，保守细节）、`enhanced`（星云更明显，
适度提高局部对比和饱和）、`high_contrast`（更强主体结构，背景更深，风险更高）、
`soft`（柔和、低锐化、低噪声，适合反射星云）、`emission`（保护 Hα 深红和 OIII 青蓝，
不做全图灰度世界白平衡）。

内置非生成式定调 profile：`auto`（按目标类型自动选择）、`natural`（保守自然基准）、
`deep_clean`（深黑背景、干净现代）、`dramatic_nebula`（发射星云主体突出，色彩有冲击但受控）、
`soft_dust`（反射星云/暗尘埃的柔和胶片感）、`galaxy_core`（星系黄核、蓝臂、尘埃带层次）、
`widefield_punch`（宽场星野的深背景和星云可见度）。这些 profile 只做色调曲线、背景压暗、
**保比例饱和度提升**、背景去饱和、局部对比、轻微色彩分离和高光 rolloff，不生成新结构。

**饱和度提升必须保通道比例**：以**像素均值为轴**缩放色度
（`new = mean + (x - mean) * k`，与 `color_separation` 同一公式），保亮度、保通道比例。
**不要**改用 HSV 的 S 乘法——它保持 max 通道不变、把 `(max−min)` 拉开，当 G≠B 时
两个非最大通道被**不等比例**压向 0：实测受控输入 `[0.25,0.10,0.06]` 经 ×1.32 后
B 直接归零（B/G 保留率 **0%**），对双窄带数据即系统性破坏 OIII。
**同一条规则适用于 Phase 8 `final_color` 的 `enhance_saturation`**（`color_tools.py`）。
默认输出两版更稳妥：`*_natural.jpg` 与 `*_enhanced.jpg`。

## Agent-in-the-loop 模式

默认优先使用阶段式闭环，不要只制定一次计划后运行完整管线：

```text
诊断与目标确认 → 执行一个阶段或生成 2-3 个候选
→ 查看 before/after/difference/review.json → 输出结构化 verdict 和 actions
→ 接受、微调或回退 → 进入下一阶段
```

```bash
# 初始化会话
python3 scripts/agent_workflow.py init input.fits work/session \
  --target-type emission_nebula --target-name NGC6888
# 提交单阶段动作
python3 scripts/agent_workflow.py apply work/session action.json
```

FITS/XISF 输入必须读取 `capture_metadata` 和 `physical_priors`。若本机安装
Astrometry.net，可增加 `--plate-solve`，并用 `--catalog-json` 将星表目标投影到
画面坐标（见 `references/physical_metadata.md`）。读取 `work/session/session.json`、
本轮 `review/review.json` 以及四张审查图；动作和评审 JSON 必须符合
`references/agent_protocol.md`。

**高风险阶段必须逐步审查**：`dbe`、`stellar_repair`（RGB 亚像素对齐 + 亮星紫晕抑制 +
PSF 形变修复，仅限线性阶段）、`stretch`、`star_remove` / `star_reduce`、
`star_process` / `star_combine`（已有图层时续接，只补齐缺失依赖）、`enhance` / `sharpen`、
`final_color` / `style`、外部 AI 降噪或无星图接入，以及任何 `masked_adjustment`
（必须同时审查 `mask.jpg` 和覆盖率）。

低风险、确定性步骤可以合并执行，但仍需在最终阶段进行真实性审查。
模型描述“背景稍暗、核心强保护、轻微缩星”等语义意图；代码负责把意图映射到有边界的参数。
模型不得直接生成或修补像素。

星点处理依赖规则（完整版见 `references/agent_protocol.md`）：没有图层时
`star_process` / `star_combine` 自动补全完整链路，已有 `star_layers` 时复用真实无星层
和星点层、不重复去星；星系默认保留星点，显式去星必须提供经视觉审查的 `subject_mask`
（保护核心及紧凑主体，与输入几何一致、数值有限且范围 `[0,1]`）；8MP 内存保护跳过步骤时
返回 `partial_success`；球状星团、疏散星团和 M45 会移除 `star_remove`、`star_process`、
`star_combine`，不得强制补回。

星点链路（`star_process` 内部顺序）：
`S1 星点拉伸(arcsinh) → S2 去紫(SCNR) → S2b 保亮度饱和补偿 → S3 曲线微调 + 缩星`

- **S2b 是必需的**（`star_saturation` 默认 `1.08`，`1.0` = 关闭）：arcsinh 压缩色度、
  SCNR 又把 Lab a 推向 0，两者叠加是最大损耗源，而 S3 的曲线与缩星都不会撤销它。
- **S1 的 `star_stretch_factor` 必须与主拉伸因子同量纲**：预设成对给出
  （`light` 25→88、`medium` 45→132、`strong` 80→198、`adaptive`/`emission` 120→264/198），
  比值 1.65~3.52，夹到 `[88, 264]`。**改这条链时 `pipeline.py` 预设与 `analyze.py`
  的 `_star_stretch_factor_for` 两处都要看。**
- 星点即主体的目标（星团 / M45）已被安全规则移除 `star_process`，不进入该链路。

需要对 Hα、OIII、亮部、暗部或星点做精细局部控制时，使用 `scripts/mask_tools.py`
或 Agent 的 `masked_adjustment` 动作。只描述亮度范围、色相范围、星点保护和组合逻辑，
不直接绘制蒙版。详细协议见 `references/mask_workflow.md`。

## 三轮流程

### Round 1: 诊断和预览

识别固定遵循 `Header/WCS → 原始数值诊断 → 零裁切安全预览 → AI视觉判断 → 本地CV辅助验证`：

1. 优先读取 FITS/XISF Header 和已有 WCS。可靠的目标标识不能被视觉分类静默覆盖。
2. 直接读取原文件运行数值诊断，并为线性 FITS/XISF 生成安全视觉审查包：

```bash
python3 scripts/analyze.py input.fits --format readable

python3 scripts/recognize.py input.fits \
  --output recognition.json --stage input --workflow-dir recognition_workflow
```

安全预览固定 `shadow_pctl=0.0`，不会通过低端百分位裁切微弱正信号。工作流输出全幅、
主体、RGB 通道预览、浮点 TIFF、视觉审查请求和完整证据 JSON。预览只用于观察，
不能作为后续处理输入。

3. AI 必须实际查看预览后回填视觉判断。若模型没有视觉能力，状态保持
   `awaiting_ai_visual_review`，不得把本地 CV 结果冒充 AI 视觉结论。
4. 本地 CV 只验证 AI 判断与星场、颜色、主体区域等启发式特征是否一致。
   冲突时优先级为 Header/WCS、高置信 AI 视觉判断、本地 CV。
5. 读取 `references/target_awareness.md` 中对应天体类型的策略。
6. **推荐方式**：使用 `--strength adaptive` + `--target-type` + `--target-name`，
   让管线自动基于诊断数据选择参数：

```bash
python3 scripts/pipeline.py input.fits preview.jpg \
  --strength adaptive --target-type emission_nebula --target-name NGC6888 \
  --color-mode emission --steps color,stretch,final_color --keep-all
```

若使用 Agent-in-the-loop 模式，本轮只运行首个必要步骤或候选预览，不要直接执行所有步骤。
`adaptive` 预设会自动运行 `analyze.py` 并基于诊断报告的 recommendations 生成配置，
覆盖 medium 基底的对应参数（DBE 方法/阶数、降噪强度、拉伸因子、星点阈值、HDR、锐化、
饱和度），并自动应用天体类型安全规则。已有诊断报告时可显式传入
`--analysis-report report.json` 避免重复分析。非发射星云或需保守基准时用
`--strength light` 或 `medium`。

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
python3 scripts/pipeline.py input.fits output_enhanced.jpg \
  --strength adaptive --target-type emission_nebula --target-name NGC6888 \
  --color-mode emission --style auto --style-strength 1.0 \
  --steps color,stretch,final_color,local_enhance,star_reduce,style \
  --override-params '{"stretch_factor":72,"saturation":1.55,"star_reduction":0.25}' \
  --keep-all
```

AI 自动风格选择规则（`--style auto` 时）：发射星云或 `color-mode emission` →
`dramatic_nebula`；反射星云/暗星云 → `soft_dust`；星系 → `galaxy_core`；
宽场 → `widefield_punch`；球状/疏散星团 → `star_cluster`（解析密集恒星，低饱和）；
未知目标 → `deep_clean`。用户明确要求某种风格时用 `--style <profile>` 覆盖 `auto`。
`--style-strength` 通常保持 `0.8-1.1`，超过 `1.2` 要特别检查颜色和光晕。

### Round 3: 审查和定稿

```bash
python3 scripts/quality_metrics.py output_enhanced.jpg
```

若有中间图，重点查看：`01_dbe.tif`（是否过减、黑坑、残留梯度）、
`05_stretched_starless.tif` 或拉伸后图（暗部是否浮现，核心是否死白）、
`08_color.tif`（颜色是否真实，背景是否中性）、最终 JPG（星点、噪声、结构、伪影）。

质量门禁按阶段解释：线性阶段允许存在轻微负背景，重点检查负值像素比例，不要用最终图的
`median >= 0.015` 规则误判线性数据；最终非线性图通过 `p1`、非正像素比例和背景中位数
共同判断是否贴黑；发射星云最终背景偏低只作为审查警告，是否保留真实 Hα/暗尘仍需看图。

密集星场处理：`dense/very_dense` 默认优先建议 StarNet++ 外部无星层，并降低星点重混合
强度；内置去星质量不达标时保持安全回退，不降低质量阈值强行接受；发射星云去星回退后
自动改用 `masked_ghs` 保护星核和背景；带星图局部增强与最终缩星复用线性星点蒙版；
发射星云颜色阶段保护青色 OIII 候选区域，避免 SCNR 与品红修正误伤。

极暗数据 `masked_ghs`：先按稳健高光分位数归一化，再运行 GHS，默认 `shadow_pctl=0.0`，
不从低端减黑位；`stretch_gamma` 会参与暗部预拉伸，推荐 `0.4-0.5`；通用
`stretch_factor` 会映射到安全的 GHS `b=4-12`，若需直接控制用 `ghs_b`，不要把
`stretch_factor=100` 直接解释成 `b=100`；可通过
`--override-params '{"dbe_method":"skip"}'` 显式跳过 DBE。GHS 两端保护 `ghs_lp` /
`ghs_hp` 与内层切线保护 `ghs_protect_lp` / `ghs_protect_hp` / `ghs_c` 的语义、取值与
用法见 `references/linear_stage_processing.md`。

若视觉不可用，输出“需人工视觉审查”的清单，不要声称视觉通过。

## 阶段要点

### 预降噪：DeepSNR CLI + Starlet 回退

`pre_denoise` 默认使用 `--deepsnr auto`：只在线性阶段、背景相对噪声不低于 5% 时尝试
本机 DeepSNR；星团/M45、低噪声、非线性输入和小于 512×512 的图像自动跳过 CLI，
低噪声跳过整个预降噪阶段；其他 CLI 跳过或失败路径保留经过验证的 Starlet 回退。
5% 是可审查的启发式触发条件，不是信噪比测量。

```bash
python3 scripts/pipeline.py input.fits output.tif \
  --steps pre_denoise,stretch --deepsnr auto --keep-all
```

- 推荐 CLI 1.3.2，兼容 1.2.3+；`--machine-info` 必须明确声明浮点 FITS 输入/输出支持。
  输入/输出使用 float32 FITS，复用零黑位裁切的可逆 MTF；1.3.2 的原生 `--linear`
  默认进行阴影裁切，当前真实性约束下不自动启用。
- 默认在线性域混合 `--deepsnr-strength 0.35`；可指定 `--deepsnr-path` 和
  `--deepsnr-timeout`（默认 900 秒）。`--deepsnr always` 强制尝试；`--deepsnr never`
  仅使用内置算法。线性 TIFF 输入须加 `--input-linear`。
- CLI 超时、模型/输出错误或数值门禁拒绝时，回退保守 Starlet 并标记 `review_required`。
  用户提供的 `--external-denoised` 优先，经过同一数值门禁。
- 使用 `--keep-all` 保留 `03_pre_denoise_before.tif`、`03_pre_denoise.tif`、
  `03_pre_denoise_residual.tif` 和 `03_pre_denoise_report.json`。必须查看同尺度前后图及残差，
  检查暗弱纤维、星点与 Hα/OIII 色彩；数值通过不能代替视觉审查。

### HDR 与 CLAHE 二选一（`enhance_mode`）

外部权威经验：「**HDRMT 与 LHE 同时使用太过头，二选一**」；「LHE amount > 0.5 开始假」。
Phase 6 因此支持条件触发（`enhance_mode`，默认 `auto`）：

| 条件 | mode |
|---|---|
| `highlight_clip ≥ 0.002` 或 `p99 ≥ 0.95`，且 `p50 < 0.12` | `both`（高光过曝 + 暗背景，两者都要） |
| `highlight_clip ≥ 0.002` 或 `p99 ≥ 0.95`，且 `p50 ≥ 0.12` | `hdr_only`（背景已亮，再叠 CLAHE 会放大噪声） |
| `p99 < 0.80` | `clahe_only`（没有可压的高光，HDR 近似空操作） |
| `span = p99 − p50 < 0.22` | `clahe_only`（全局层次不足，优先局部对比） |
| 其余（含信号缺失） | `both`（**保守兜底 = 历史行为**） |

判定信号在 Phase 6 开头**现算**（不用 `post_health`——它只在 `stretch` 在 steps 里时存在）。
`enhance_mode` 可用 `--override-params` 强制。

**CLAHE 亮核保护**：`clahe_mask_gamma`（默认 2.0）在 `[p90, p99.9]` 区间内逐步降低
局部均衡权重，`p90` 以下权重恒为 1——只保护亮端，背景与主体不受影响。**不要**把掩版
写成上升型幂版 `((L-lo)/(hi-lo))**g`：那会在整个中低亮度区间给出接近 0 的权重，
等于把 CLAHE 全图关掉（实测 median 掉 25%）。

### CLAHE 守卫：噪声主导时跳过

`span` 分不出该不该跑 CLAHE —— 实测 `span = 0.2187` 的几张有结构（跑 CLAHE 无害），
而 `span = 0.0814` 的才是**噪声主导**（跑 CLAHE 出蜂窝斑块）。判据必须是 SNR：
Phase 6 现算 `texture_snr = 星云区高通能量 / 背景区高通能量`（背景区即噪声地板）。
`< 1.7` 时**削掉 CLAHE**（`both`→`hdr_only`，`clahe_only`→`none`），`≥ 1.7` 保持原决策；
实测分离度：出伪影的输入 **1.24~1.35**，干净的 **2.32~5.26**。

`clip_limit` **不是**可用的强度旋钮——窄直方图上它会饱和（实测 0.0003 到 0.003 给出
**逐位相同**的输出），**所以只能二选一，不能“调小一点”**。Phase 6 另在下采样预览
（≤640px）上**实测** CLAHE 的高频放大倍数，超过 `CLAHE_MAX_HF_AMPLIFICATION`
（默认 **1.5**）就跳过，实测值写入 `hdr_report.clahe_hf_amplification` 供审计。

标定依据、实测收益与被否决的假设见 `references/fidelity_gate_calibration.md`。

### 双窄带哈勃假彩色、线性反卷积与物理守卫

系统内置纯 Python / NumPy / SciPy 实现的进阶天体物理处理链，无需安装额外第三方工具：

1. **双窄带哈勃假彩色色板合成 (`--palette`)**：基于 Carlo Mollicone (AstroBOH) 经典算法，
   从双窄带（Hα+OIII）提取通道并合成 $S=(H+O)/2$；支持色板 `SHO`、`HSO`、`HOO`、
   `OSH`、`OHS`、`HOS`；**保亮度色域重映射**在置换色板时 100% 保持底图 Rec.709
   感官亮度与对比度结构不变。
2. **线性域物理反卷积 (`--linear-deconv`)**：在非线性拉伸之前（Phase 2c）执行正则化
   Richardson-Lucy 反卷积，基于实测 FWHM 自适应构建高斯/Moffat PSF 核；信号自适应
   权重掩膜保护纯暗背景不发散，并有高光防吉布斯振铃门禁。参数 `--deconv-iterations 8~12`。
3. **叠边自动裁切与星晕守卫 (`--stacking-crop`, `--halo-guard`)**：抖动叠边裁切扫描
   行/列统计一致性，自动切除场旋与抖动带来的低信噪比边缘色差断崖，避免带偏 DBE 和 PCC；
   星晕守卫在去星后于亮星周围 2~4 倍 FWHM 区域平滑色度，平整强色差折射镜造成的假彩色
   星晕，保持背景亮度不变。

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

拉伸因子采用**单一归属**：优先级为 M42 > 发射星云 > 反射星云 > 暗星云，只乘一次；
非拉伸修正（锐化/HDR/降噪/饱和）仍按规则叠加。名称判定统一走 `scripts/target_rules.py`，
唯一事实源为 `fits_io.LOCAL_CELESTIAL_DB`，因此 `M 42`、`NGC 6888`、`ngc-6888`、
`Crescent Nebula` 等价，且为精确匹配（`M8` 不会误匹配 `M81`）。只给 `--target-name`
也能激活星系/行星状/暗星云规则。

## 参数闭环

### 参考图定调

用户提供参考成片时，只匹配全局亮度曲线、综合色调、饱和度和有限局部对比，不得复制
参考图结构、纹理、星点或颜色通道。先完成安全基础处理，再运行：

```bash
python3 scripts/reference_grade.py processed.jpg reference.png final.jpg \
  --strength 0.85 --max-color-gain 1.25 --max-saturation 1.45 \
  --local-contrast 0.10 --match-orientation --report final.reference-grade.json
```

参考图与输入不是同一视场或曝光深度时，只把参考图作为审美约束；不得为了“看起来一样”
制造输入数据中不存在的暗尘、Hα/OIII 结构或高频细节。

**信号区色比匹配**（`--reference-color-match` / `--reference-color-only`）：全局定调的
搜索空间**没有色比控制**，修不了 Hα/OIII 比。补一条**闭式解**通道增益
（`gR = (R/G)_ref / (R/G)_src`、`gG = 1`、`gB = (B/G)_ref / (B/G)_src`，色比取星云主体
luma > p55 的逐通道 p99），是确定性闭式解、无搜索、无生成。`--reference-color-match`
在色彩与星点亮度上更接近参考，但全局定调可能把星核推到近饱和并新增 `CORE_BURNING`
告警；`--reference-color-only` 只改色比、不碰 tone。公式推导、实测数值与报告字段见
`references/external_tools.md`。

### StarNet 外部去星

极暗线性数据不要直接量化为 16-bit 后交给 StarNet2。先做**安全 MTF 拉伸**（不能用
GHS / 反正弦），再运行 StarNet2；桥接命令已默认走这条路
（`--export-starnet-payload-domain` 默认 `mtf`），回流时 sidecar 自动识别域并做逆 MTF。

`--starnet-stride` 是**质量/速度旋钮，不是画质开关**：官方约束**偶数且 2~512**，
代价按推理块数 `(W/s)×(H/s)` 增长，**低端极慢**（实测 8.3MP 全幅 stride 2 时
2400 s 仍未跑完）。**默认 128 是有意的折中**——64 相对 128 只改变约 2.7 万像素
（集中在星核），却要付 4 倍推理时间。**除非有明确的星核质量要求，否则不要调低。**

回流时会做**通道完整性硬校验**：外部无星层在星云信号区大范围丢失某通道时直接
`raise ValueError`，不会静默产出纯红成片。对已拉伸 TIFF/PNG，保留 StarNet 独立输出的
starless 和 stars 图层，再运行 `scripts/enhance_starless.py` 输出 LOW/MEDIUM/HIGH
三档与最终带星候选；两个图层必须尺寸、方向和裁切一致，stars 必须是黑背景正向星点层，
球状星团、疏散星团、一般星团及 M45 会被拒绝。

载荷域、回流参数、stride 实测表、通道语义与失败路径见 `references/external_tools.md`。

### 关键 CLI 参数

| 参数 | 作用 |
|---|---|
| `--strength adaptive` | **推荐**。以 medium 为基底，自动读取 analyze.py 诊断报告生成配置。未提供 `--analysis-report` 时会自动运行 analyze.py。 |
| `--analysis-report` | 显式传入 analyze.py 的 JSON 报告路径，避免重复分析。 |
| `--target-type` | 天体类型，触发自动安全规则（禁用危险步骤、参数修正）。 |
| `--target-name` | 天体名称（如 M42、M45），用于激活特定规则。FITS 文件会自动从 OBJECT header 读取。 |
| `--override-params` | JSON 对象，覆盖 adaptive 或预设的任何参数。仅在诊断结果不满意时使用。 |

### 标准 GHS 与目标拉伸策略

含星自动策略：星系、行星状星云、M42 使用 `masked_ghs_standard`；星团及 M45 使用
`pi_mtf`；弥散发射目标保留 `emission`；反射/暗星云使用保色 `arcsinh`；未知目标和
宽场使用 `pi_mtf`。显式方法优先于覆盖配置中的方法，再采用目标规则。

新管线调用与 Agent 会话记录 `stretch_policy_version: 3`；既有会话保留策略 1 或 2，
缺失版本按策略 1。`stretch_subject_midtones` 控制无星主体的显示目标（默认 `0.25`，
反射/暗星云 `0.20`，`null` 则保留白锚点曲线），必须满足
`target_bg < stretch_subject_midtones < (1-stretch_headroom)/3`：

```json
{"stretch_subject_midtones": 0.25, "target_bg": 0.035, "stretch_headroom": 0.05}
```

标准参数使用独立键，旧 `ghs_b` 等不会自动换算（`ghs_standard_b` 8.0、
`ghs_standard_lp` 0.0、`ghs_standard_hp` 0.95、`ghs_protect_strength` 0.85、
`stretch_headroom` 0.05、`target_bg` 0.035）。保护锚点是归一化域参数，必须满足
`0 <= LP <= SP <= HP <= 1` 且 `HP > LP`；`log_d` 范围 `[0,20]`，`b` 范围 `[-5,15]`，
所有显式参数必须有限，无效参数直接拒绝。强度与旧 `stretch_factor`、`ghs_b` 无换算关系。
主体锚定行为、回退链、显示域证据边界与参数二分策略见 `references/linear_stage_processing.md`。

### `--override-params` 常用键

```json
{
  "dbe_method": "polynomial", "dbe_degree": 2,
  "pre_denoise_lum": 0.012, "pre_denoise_chroma": 0.035,
  "stretch_factor": 54, "target_bg": 0.08, "stretch_gamma": 0.4,
  "star_threshold": 0.85, "star_reduction": 0.3, "star_stretch_factor": 149,
  "star_scnr_strength": 0.15, "star_combine_strength": 0.9, "star_saturation": 1.08,
  "hdr_strength": 0.35, "sharpen_amount": 0.7, "saturation": 1.4,
  "style_strength": 1.0,
  "final_denoise_lum": 0.008, "final_denoise_chroma": 0.024,
  "shadow_pctl": 0.5, "highlight_pctl": 99.9,
  "ghs_lp": 0.0, "ghs_hp": 1.0,
  "ghs_protect_lp": null, "ghs_protect_hp": null, "ghs_c": 0.0,
  "clahe_mask_gamma": 2.0, "clahe_mask_low_pctl": 90.0,
  "clahe_mask_high_pctl": 99.9, "enhance_mode": "auto"
}
```

`clahe_mask_gamma=0` 一键回到历史行为。`enhance_mode` 见「HDR 与 CLAHE 二选一」，
`star_saturation` 见「星点链路」，GHS 各保护键见 `references/linear_stage_processing.md`。

经验初值（`adaptive` 已自动处理大部分）：极暗线性 FITS/XISF `stretch_factor` 80-140、
`stretch_gamma` 0.33-0.45；发射星云 RGB/LP 用 `color-mode emission`、`saturation` 1.3-1.8、
避免全图白平衡；星系 `saturation` 1.1-1.35、`hdr_strength` 0.35-0.6、锐化可略强；
反射星云拉伸可强、饱和和锐化要柔和；球状星团/M45 管线已自动禁用
`star_remove`/`star_reduce`，无需手动操作。

## 质量审查标准

数值审查必须运行 `quality_metrics.py`。关键字段：

| 字段 | 用法 |
|---|---|
| `median` | JPG 背景/整体亮度参考，通常 0.03-0.15 |
| `corner_uniformity_ratio` | 角落均匀度；>3 通常说明 DBE/裁切/黑边有问题 |
| `uniform_5x5_dark_patch_ratio` | 暗部过度涂抹风险；过高说明塑料感 |
| `high_frequency_energy_ratio` | 高频变化参考；不能单独判断真实细节 |
| `star_area_ratio` | 星点占比；星云通常应低于星团 |

严肃处理还应按 `references/quality_assessment.md` 补充：在线性母版上做背景、暗弱目标、
中亮结构和亮核的分区 SNR；用未饱和孤立恒星统计 FWHM、长短轴、圆度/偏心率及全场空间
趋势；有 WCS 和测光星表时评估恒星颜色残差（否则只能标注为启发式色彩审查）；结合 WCS
像素尺度、FWHM、口径和波长评估实际分辨率与采样，不以输出像素数或锐化后高频能量代替
真实分辨率。

视觉审查优先级：背景（不能有明显光害带、黑坑、拼接缝）；目标（结构应来自原图，
不能像生成纹理）；星点（大小自然，不能有黑洞、紫边、硬边）；色彩（Hα 深红不品红，
OIII 青蓝不电蓝，星系自然黄核蓝臂）；动态范围（核心不过曝，外围不断层）。

### 门禁触发时的处置

具名星点伪影门禁（`scripts/artifact_gates.py`）、标量门禁（`INPAINT_FOOTPRINT`）、
保真度门禁（`scripts/fidelity_gates.py`）、配置指纹（`scripts/config_fingerprint.py`）
与失败处置策略（`scripts/failure_policy.py`）**并存**：数值门禁是审查触发器，
具名门禁提供定位证据，两者都不声称“视觉质量合格”。

**关键语义（不得误读）**：
- 保真度门禁三态：健康 ⇒ 完全静默；`degraded` ⇒ 可见但**不升级**、不产生 issue；
  `unavailable` / `uncalibrated` ⇒ **均不等于健康**，「测不到」绝不能当通过。
- 管线级 `degraded` 是 success 与 review_required 之间的中间态：产物可用、
  **不阻断 `accept`**，但调用方看得出这不是一次干净的处理。status 优先级：
  `failed` > `review_required` > `degraded` > `success`。
- 配置指纹与 `runtime_info.source_fingerprint()` **正交**：前者管“用的哪套参数”，
  后者管“跑的哪版代码”。指纹不同即切断父 issue 继承。

失败时必须重跑，不要硬宣布完成：DBE 失败则降低 degree、换 `median`、跳过 DBE 或只裁
黑边；拉伸过强则降低 `stretch_factor` 或提高 `stretch_gamma`；噪声塑料感则降低降噪并
输出保守版；颜色失真则降低 `saturation`，发射星云使用 `emission` 模式；风格过重则降低
`--style-strength` 或改用 `natural`/`deep_clean`。星点处理伪影需检查
`04_starless_linear.tif`，若残留星多或星云误伤严重，使用 `--external-starless` 传入
StarNet++ 无星图；也可运行 `star_tools.py detect` 查看检测置信度，置信度 <0.3 时应优先
使用外部 StarNet++。

按门禁码定位：

| 门禁码 | 处置 |
|---|---|
| `STAR_RINGING` | 先看 `added_dip_abs`、`reference_dip_abs` 和背景可靠性；对已归因的新增伪影，检查锐化/反卷积对应阶段的参照与强度 |
| `STAR_BLOAT` | 检查拉伸方法（是否裸 STF？），改用 `masked_ghs` 或加 `ghs_hp` |
| `STAR_LAYER_LOSS` / `STAR_HOLES` | 检查去星/缩星强度，回落 `star_reduction` 或改用外部 StarNet++ |
| `CORE_BURNING` | 检查 `ghs_hp` 是否过低，或降低 `sharpen_amount` / HDR 强度 |
| `INPAINT_FOOTPRINT` | 去星修补足迹被拉伸放大。优先改用外部 StarNet++（`--use-starnet` / `--external-starless`），或调整内部 inpaint 半径与去星蒙版阈值。灵敏度受**星点蒙版质量**限制 |
| 保真度 `degraded` | 按 `evidence.remediation` 处理：噪声类退拉伸曲线到 `pi_mtf`；`HUE_PRESERVED` 降 `saturation` 并复核 `color_mode`；`SUBJECT_CHROMA_VISIBILITY_PRESERVED` 降 `style_strength`；`BACKGROUND_CHROMA_NOISE_BOUNDED` 提高 `final_denoise_chroma` |
| 保真度 `unavailable` | **不要当成通过**——先看 `evidence.chroma_measurement_status` 判断是 `insufficient_regions`（样本不足）还是 `insufficient_source_chroma_support`（参考图色度支撑不足） |

门禁码定义、三态语义、阈值标定来源与被实测否决的设计假设，见
`references/quality_assessment.md` 与 `references/fidelity_gate_calibration.md`。

## 输出格式

默认输出最终 JPG（用户要看的成片）；如用户没有拒绝，保留 `--keep-all` 的工作目录，
里面有中间 TIFF 和 `manifest.json`；对严肃处理额外输出 16-bit/float TIFF 母版；
自动化调用使用 `--result-json result.json` 获取统一状态、有效配置、质量门禁和警告。

状态含义：
- `success`：数值门禁未发现明确风险，仍需完成视觉审查。
- `partial_success`：主图已生成，但识别等可选阶段失败，或内存保护跳过了请求步骤。
- `review_required`：产物保留，必须由视觉 Critic 检查或调整后重跑。
- `failed`：输入、配置或执行失败。

处理完成后按以下格式输出最终结果（表格列：输入、目标类型、风格、预设）：

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
**输出文件**：`NGC6888_enhanced.jpg` (2048×1365, 982 KB)、
`NGC6888_natural.jpg` (2048×1365, 856 KB)
**质量审查**：median 0.087 ✅ | corner_uniformity 1.83 ✅ | star_area 0.0024 ✅
**视觉审查**：背景均匀 ✅ | 星点自然 ✅ | Hα 深红保真 ✅ | 核心无过曝 ✅ | 无伪影 ✅
**状态**：success
```

## 支持格式

| 格式 | 输入 | 输出 | 说明 |
|---|---|---|---|
| FITS `.fit/.fits/.fts` | 是 | 是 | 线性叠加图；脚本会归一化显示处理 |
| XISF `.xisf` | 是 | 否 | PixInsight 格式；需安装 `xisf` |
| TIFF `.tif/.tiff` | 是 | 是 | 推荐作为母版 |
| PNG/JPG | 是 | 是 | 已非线性/压缩，处理要保守 |

## 何时读取 references

**流程与协议**：`references/agent_protocol.md`（阶段动作、候选比较、Critic 返回协议）、
`references/physical_metadata.md`（拍摄元数据、物理先验、天区解析）、
`references/mask_workflow.md`（多尺度蒙版与局部算子）、
`references/target_awareness.md`（目标类型策略）、`references/case_ngc6888_rgb.md`（真实案例）。

**合成与目标**：`references/linear_stage_processing.md`（坏点修复、局部归一化、梯度诊断、
宽场 DBE、GHS 保护键与拉伸策略）、`references/narrowband_synthesis.md`（窄带合成）、
`references/lrgb_synthesis.md`（LRGB 合成）、`references/hdr_composition.md`（多曝光 HDR）、
`references/special_targets.md`（彗星、行星、月面、宽场星野、超新星残骸）。

**质量与门禁**：`references/quality_assessment.md`（分区 SNR、星点圆度、星表色差、
分辨率评估与门禁定义）、`references/fidelity_gate_calibration.md`（保真度门禁与 CLAHE
守卫的阈值来源、标定方法与被实测否决的设计假设）、
`references/star_damage_gate_calibration.md`（去星误伤门禁的标定记录与重新定线依据）。

**工具与护栏**：`references/external_tools.md`（Siril、PixInsight、StarNet++、外部降噪、
参考图定调）、`references/ai_hybrid_workflow.md`（AI 工具边界）、
`references/cv_guardrails.md`（CV 护栏、浮点图像状态契约、星点引擎细节）、
`references/stellar_repair.md`（线性星点像差与形变修复）、
`references/ai_common_pitfalls.md`（常见误判）。

## 环境

首次安装（从仓库根目录执行）：

```bash
python3.12 -m venv deep-sky-processor/.venv
deep-sky-processor/.venv/bin/python -m pip install \
  -r deep-sky-processor/requirements.txt
```

运行脚本时使用该 Skill 自己的 `.venv/bin/python`。
