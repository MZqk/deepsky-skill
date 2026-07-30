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

可使用 `denoise.py` 中的 `validate_external_denoise()` 做数值辅助，但不能替代视觉审查。

## BlurXTerminator / 外部反卷积

推荐在线性阶段、去星前执行，并保持保守参数。导出后从后续阶段进入管线，降低或跳过内置锐化：

```bash
python scripts/pipeline.py deconvolved.tif output.jpg \
  --steps stretch,enhance,final_color,final_denoise \
  --override-params '{"sharpen_amount":0.0}'
```

检查星点黑环、双边和星云高频伪影。

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
