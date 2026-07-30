# 窄带合成详细流程

本文适用于独立的 Hα、OIII、SII 单通道母版。目标是先完成可靠的线性通道合成，再把合成后的 RGB 图交给 `pipeline.py` 做拉伸、颜色保护、HDR、锐化和星点处理。

## 能力边界

- 当前 `pipeline.py` 处理已经合成的单色或 RGB 图像。
- 当前版本没有 `--ha/--oiii/--sii/--narrowband-mode` 原生合成参数。
- 通道校准、星点配准和重采样应先在 Siril、PixInsight 或其他确定性工具中完成。
- 缺少独立通道时不能伪造该通道。双窄带 OSC 数据不能声称恢复了独立 SII。
- 合成只允许线性组合、归一化和色调映射，不生成不存在的结构。

## 输入要求

每个通道应满足：

1. 完成 bias/dark/flat 校准。
2. 使用同一参考帧完成星点配准。
3. 裁切到完全相同的宽高和视场。
4. 保持线性数据，优先使用 32-bit FITS/TIFF。
5. 分别检查梯度、噪声、星点 FWHM 和饱和区域。

如果通道形状、旋转、像素尺度或裁切范围不同，停止合成并重新配准。

## 通道预处理

### 背景与梯度

- 每个通道独立做保守背景建模。
- 不要强制三个通道背景均值完全相同。
- 大面积 Hα/OIII 弥散结构可能被误判为梯度，宽场发射星云优先人工审查背景模型差分。

### 线性归一化

使用不包含亮星核心和强星云结构的背景/中弱信号区域估计尺度。推荐：

```text
channel_normalized = channel / robust_P99.5
```

仅用于合成尺度统一，不对每个通道独立铺满动态范围。弱通道增益应有限制，通常不超过 `2×`，否则会放大噪声并伪造综合色彩。

### 通道质量降级

如果某通道：

- SNR 明显更低：降低其综合色彩权重。
- FWHM 明显更大：不要用强锐化伪装分辨率。
- 有严重梯度或云层：优先重处理或放弃该通道。
- 大面积饱和：不能从其他通道补造其内部结构。

## SHO 经典哈勃调色板

基础映射：

```text
R = SII
G = Ha
B = OIII
```

Hα 通常最强，直接等权合成容易偏绿。推荐起点：

```text
R = 1.00 * SII
G = 0.75-0.90 * Ha
B = 1.00 * OIII
```

处理顺序：

1. 合成线性 RGB。
2. 检查背景和通道裁切。
3. 用亮度域拉伸，不对 RGB 独立归一化。
4. 通过曲线做金色/青色分离。
5. 保护 OIII 区域，避免强 SCNR 抹掉真实信号。
6. 星点颜色单独处理，避免绿色或紫色星核。

## HOO 双窄带

适用于独立 Hα 与 OIII：

```text
R = Ha
G = 0.15 * Ha + 0.85 * OIII
B = OIII
```

OIII 较弱时：

```text
G = 0.25-0.35 * Ha + 0.65-0.75 * OIII
B = 0.8-1.0 * OIII
```

不要通过固定执行 `B=max(B,G*k)` 制造不存在的蓝色。只有确认 G/B 均来自 OIII 映射时，才允许有限重构。

## Foraxx 变体

推荐参数化公式：

```text
R = a * SII + (1-a) * Ha
G = b * Ha + (1-b) * OIII
B = OIII
```

起点：

```text
a = 0.65
b = 0.35
```

- SII 噪声高：降低 `a`。
- OIII 噪声高：提高 `b`，但不要强行制造青色。
- Hα 压制其他通道：先降低 Hα 权重，再做综合色调，不要直接剪除 Hα。

## HαRGB

Hα 仅增强 RGB 中已有的发射结构：

```text
R' = (1-w) * R + w * Ha
```

建议 `w=0.15-0.35`。应使用星云蒙版限制注入，并在恒星区域降低权重。禁止把全图背景和星点染红。

## 合成后进入管线

```bash
python scripts/analyze.py combined.tif --output combined.analysis.json

python scripts/pipeline.py combined.tif final.jpg \
  --strength adaptive \
  --analysis-report combined.analysis.json \
  --target-type emission_nebula \
  --color-mode emission \
  --style dramatic_nebula \
  --keep-all
```

如果合成图极暗，诊断会推荐 `very_dark` 拉伸。不要在合成前量化为 8-bit。

## 质量审查

- 通道配准：亮星不能出现红/绿/蓝错位边。
- 背景：无单通道黑坑、色带或硬剪切。
- Hα：深红或金色，不应无条件变成品红。
- OIII：青蓝但不应成为电蓝色块。
- SII：弱时保持克制，不通过强增益伪造结构。
- 星点：颜色和大小自然。
- 输出报告：检查 `channel_signal_ratios`、`collapsed_channels`、P99 和高光裁切率。

## 失败回退

- 通道错位：重新配准，不做颜色修补。
- 弱通道噪声爆炸：降低综合色彩权重。
- 背景偏色：重新检查每通道背景模型。
- 星点颜色异常：分离星点层后独立校色。
- 合成后过暗：使用 `very_dark` 或 `emission` 拉伸，不重新独立归一化各通道。
