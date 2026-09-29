# 外部工具集成指南

本文说明如何把 Siril、PixInsight、StarNet++、NoiseXTerminator、GraXpert 等工具的确定性输出接入 deep-sky-processor。

## 通用交换规范

优先格式：

1. 32-bit float TIFF。
2. 32-bit FITS。
3. 16-bit TIFF，仅用于外部工具不支持浮点时。

避免：

- 8-bit JPG/PNG 作为中间母版。
- 自动色彩管理造成的 gamma 重复应用。
- 尺寸、裁切、旋转或通道数不一致。
- 外部工具输出覆盖原始母版。

每个外部结果都应保留：

- 工具名称与版本。
- 关键参数。
- 输入文件哈希或路径。
- 输出位深和色彩空间。
- 人工视觉审查结论。

## Siril

适合：

- 校准、去马赛克、配准、堆栈。
- 窄带/LRGB 通道合成。
- 背景提取和测光校色。
- 彗星配准。

建议在 Siril 中完成线性基础处理，导出 float32 FITS/TIFF，再运行：

```bash
python scripts/analyze.py siril_master.fit --output analysis.json

python scripts/pipeline.py siril_master.fit final.jpg \
  --strength adaptive \
  --analysis-report analysis.json \
  --keep-all
```

## PixInsight

适合：

- DBE/ABE、SPCC/PCC。
- ChannelCombination、LRGBCombination、HDRComposition。
- StarAlignment、CometAlignment。
- BlurXTerminator、NoiseXTerminator、StarXTerminator。

外部处理应保持步骤单一、可审计。例如只导出降噪结果，不要同时进行未知曲线、锐化和色彩变化。

## StarNet++ v2

### 管线直接调用

```bash
python scripts/pipeline.py input.tif output.jpg \
  --use-starnet \
  --starnet-path /absolute/path/to/starnet2 \
  --starnet-stride 256 \
  --starnet-timeout 900 \
  --steps star_remove,stretch,star_process,star_combine,final_color \
  --keep-all
```

也可以设置：

```bash
export STARNET_PATH=/absolute/path/to/starnet2
```

`STARNET_PATH` 也可指向包含 `starnet2`/`starnet++` 的目录。

集成功能：

- 自动兼容参数式和旧版位置式 CLI。
- 设置动态库搜索路径。
- 校验输出尺寸、数据类型和 NaN/Inf。
- 运行质量门禁，不合格时回退内置方法或保留原图。
- 在结果 JSON 的 `star_removal` 中记录执行和失败信息。

### 外部生成无星图

```bash
python scripts/pipeline.py input.tif output.jpg \
  --external-starless starless.tif \
  --steps stretch,external_detail,final_color,star_reduce \
  --external-detail-strength 0.75 \
  --keep-all
```

要求外部无星图与原图尺寸、裁切、方向完全相同。若有暗环或修复伪影，只使用 `external_detail` 提取正向结构，不直接覆盖底图。

### StarNet2 CLI 星点层语义（关键：不要混用）

StarNet2 CLI 提供两种星点输出，语义**相反**，混用会导致合成失败：

| 参数 | 输出语义 | 适用数据 | 重组方式 |
|---|---|---|---|
| `-m/--mask <file>` | **可加性星点层**（input − starless） | **线性数据** | 线性相加：`starless + mask` |
| `-n/--unscreen <file>` | **Screen 混合星点层**（供 Screen 混合） | **非线性/已拉伸** | Screen 混合：`1 − (1−starless)·(1−stars)` |

**规则**：
- **线性数据**使用 `-m/--mask`，**禁用 `-n/--unscreen`**。线性数据的星点层是减法关系，Screen 混合会错误压缩动态范围。
- **已拉伸数据**使用 `-n/--unscreen`，禁用 `-m/--mask`。非线性数据的星点层已适配显示域，需用 Screen 混合重建。
- 这与 SXT（StarXTerminator）的复选框方向**一致**：SXT 的 "Unscreen stars" 同样表示"生成供 Screen 混合的星点层"，且 RC-Astro 官方明确说明"线性数据不要选 Unscreen"。
- **GHS / 反正弦预拉伸会让 StarNet/SXT 误判星点为小星系**。若先做了 GHS 或 arcsinh 拉伸再去星，星点剖面会被压扁，神经网络可能将其识别为椭圆星系而漏除。去星应在线性阶段或标准 MTF 拉伸后执行。

**桥接默认走「MTF 拉伸」域**（`--export-starnet-payload-domain` 默认 `mtf`）：载荷先做标准 MTF 拉伸再量化为 16-bit，因此用 `-n/--unscreen`（已拉伸语义）。回流时管线读 `work-dir/starnet_payload_meta.json`（记录 `domain` 与 `midtones`）自动做逆 MTF，无需手传参数。

**为什么必须这样**：极暗线性数据直接量化时，弱色通道会被量化压成 0。实测某 Duo-Band 母版星云区 G 通道只有 23 counts（占 16-bit 满量程 0.035%、量化跨度仅 70 级），StarNet2 的输出在**所有低于 200 counts 的亮度档把 G 100% 归零**，而 R 存活 → 拉伸后整片星云变纯红。先做 MTF 拉伸（`midtones≈0.02`，底部增益 49×）后，同一命令下：

| 阶段 | R/G | B/G | G 归零 |
|---|---|---|---|
| 源（线性母版） | 3.272 | 0.830 | 0.08% |
| MTF 载荷 | 3.149 | 0.833 | 0.08% |
| StarNet2 输出（MTF 域） | 3.175 | 0.839 | 0.05% |
| 逆 MTF 回线性 | 3.294 | 0.837 | 0.05% |
| 线性对照（同 CLI） | ∞ | — | **99.4%** |

**回流硬校验**：外部无星层在星云信号区若大范围丢失某通道（相对强度掉到 <1/2，或归零比例上升 ≥25pp），管线 `raise ValueError` 并报出通道名与数值；原图该通道本来就近零（相对最强通道 <5%）的窄带图不判塌缩。此外还会检查无星层与原图的中位亮度比（应在 [0.1, 10] 内）以拦住未归一化的 FITS。

**stride**：内置 `--use-starnet` 路径与外部桥接命令**共用** `--starnet-stride`，默认 **128**。

**内置路径也走 MTF 域**（`--starnet-domain`，默认 `mtf`）：`run_starnet_cli` 在量化为
16-bit 前先做 MTF 拉伸、读回后做逆 MTF，与外部桥接一致。实测同一极暗母版：旧版
（线性域）输出 `nebula_damage_ratio = 0.5521` → 超过接受门 `≤ 0.20` → 回退形态学
（StarNet2 白跑一次）；改 MTF 域后 `damage = 0.0`、`score = 0.996`、`accepted = True`、
**不再回退**。**接受门（`score ≥ 0.45` 且 `damage ≤ 0.20`）没有被放宽**——是输出真的
变健康了，所以能自然通过。

### 已拉伸 StarNet 分层美化与重组

```bash
python scripts/enhance_starless.py \
  starless.tif stars.tif output_dir \
  --target-type emission_nebula \
  --target-name NGC6888
```

首版只接受已拉伸 TIFF/PNG。starless 和 stars 必须由 StarNet 独立导出，
尺寸、方向和裁切一致；stars 必须是黑背景正向星点层，不使用相减回退。
输出包含 LOW、MEDIUM、HIGH 三档、蒙版、尺度 detail、差异图和
`report.json`。星团及 M45 会被拒绝；HIGH 仍硬失败时不输出最终 HIGH 图。

## NoiseXTerminator / 外部降噪

在线性阶段导出外部降噪图：

```bash
python scripts/pipeline.py input.fit output.jpg \
  --external-denoised denoised.tif \
  --steps color,pre_denoise,stretch,final_color,sharpen \
  --keep-all
```

管线会用外部图替代内置初步降噪。必须检查：

- 暗弱细丝是否被涂抹。
- 星点是否变成塑料感。
- 背景是否出现重复纹理。
- 输出是否与原图形状一致。

**NXT 最佳实践：多次轻量优于一次重手**。不要在一次处理中把 denoise 推到 0.7。推荐四个应用点各用轻量参数：

| 阶段 | denoise | detail | 说明 |
|---|---|---|---|
| 线性 RGB 去星前 | 0.25 | 0.15 | 保护暗弱结构 |
| 拉伸后 L | 0.25 | 0.15 | 亮度通道轻量降噪 |
| 拉伸后 RGB | 0.25 | 0.15 | 色彩通道轻量降噪 |
| LHE-HDRMT 后收尾 | 0.25 | 0.15 | 最终平滑，不压细节 |

**过降噪症状自查**（出现任一即回退）：
- 塑料感/油画感：背景失去自然颗粒纹理。
- 暗星丢失：中等亮度星点被抹平为天空背景。
- 边缘模糊：星云亮丝边界发虚。
- 色彩涂抹：窄带区域（Hα/OIII）色块化。

可使用 `denoise.py` 中的 `validate_external_denoise()` 做数值辅助，但不能替代视觉审查。

## BlurXTerminator / 外部反卷积

推荐在线性阶段、去星前执行，并保持保守参数。导出后从后续阶段进入管线，降低或跳过内置锐化：

```bash
python scripts/pipeline.py deconvolved.tif output.jpg \
  --steps stretch,enhance,final_color,final_denoise \
  --override-params '{"sharpen_amount":0.0}'
```

检查星点黑环、双边和星云高频伪影。

**BXT 两趟法**（RC-Astro 官方推荐）：

1. **Pass 1 — `correctOnly=true`**：在**校色前**执行，只修正光学像差（彗差、像散、场曲），不锐化。此时色彩尚未校准，BXT 的像差修正不依赖色彩空间。
2. **Pass 2 — 正常锐化**：在**校色后**执行，恢复高频细节。

**外部 BXT 的 `adjustStarHalos` 必须设为 0.00**。本地管线已有 `--halo-guard` 等价物（去星后亮星周围 2~4×FWHM 色度平滑），外部工具的星晕调整会与之冲突，产生双重平滑或色偏。

## GraXpert

适合背景提取和可选降噪。使用背景提取时：

- 保存背景模型和校正结果。
- 对宽场 Hα/OIII 图像检查是否减掉真实弥散信号。
- 不要在 GraXpert 和管线中重复执行强 DBE。

如果外部已完成背景提取：

```bash
python scripts/pipeline.py corrected.tif output.jpg \
  --strength adaptive \
  --override-params '{"dbe_method":"skip"}'
```

## AstroPixelProcessor / DeepSkyStacker

适合校准、配准和堆栈。导出线性高位深母版后再处理。关闭自动拉伸或确保导出的是未拉伸数据。

## 外部参考图

只允许本地参考成片进行全局定调：

```bash
python scripts/pipeline.py input.tif output.jpg \
  --reference-image reference.jpg \
  --reference-auto-search \
  --reference-strength 0.85
```

该模式只匹配全局直方图、背景色度、饱和度和 HDR 参数，不复制结构。

## 失败处理

- 外部图尺寸不匹配：重新配准和裁切。
- StarNet++ 找不到：设置 `--starnet-path` 或 `STARNET_PATH`。
- StarNet++ 输出未通过门禁：保留原图，检查 `star_removal` 报告。
- 外部降噪过度：降低强度或回退内置降噪。
- 背景工具减掉星云：恢复原始线性母版，重新建模。
- 外部工具产生未知色彩变化：不要继续叠加校色，先确认色彩空间和处理历史。

## 安全清单

- 原始母版只读保留。
- 每个外部步骤单独输出。
- 不接受生成式纹理、补星、补尘埃或 AI 着色。
- 任何 AI 降噪/去星结果都需要中间图和差分审查。
- 最终运行 `quality_metrics.py` 并检查中间 TIFF。
