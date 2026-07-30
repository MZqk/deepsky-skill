# LRGB 合成流程

LRGB 合成使用高信噪比亮度通道 L 提供结构，RGB 提供颜色。核心原则是替换或增强亮度，而不是把 L 直接叠加到每个颜色通道。

## 当前能力边界

- 当前仓库没有原生 LRGB 配准/合成 CLI。
- L、R、G、B 的校准、配准和首次合成应在 Siril/PixInsight 等外部工具完成。
- 合成后的 LRGB RGB 图可以交给 `pipeline.py` 做后续处理。

## 输入准备

1. 分别校准 L/R/G/B。
2. 以 L 或质量最佳的 RGB 通道为参考完成配准。
3. 所有通道必须具有相同尺寸、裁切和像素尺度。
4. L 与 RGB 保持线性。
5. 分别检查 FWHM、背景梯度、饱和核心和噪声。

如果 L 的 FWHM 比 RGB 更差，直接注入会降低综合色彩图分辨率，应降低 L 权重或改用 RGB 自身亮度。

## RGB 基础合成

先生成可靠 RGB：

```text
RGB = stack(R, G, B)
```

执行背景中性化和受控色彩校准，但暂不做强饱和。RGB 的亮度：

```text
Yrgb = 0.2126*R + 0.7152*G + 0.0722*B
```

## L 与 RGB 匹配

在注入前匹配：

- 背景水平。
- P50/P90/P99 亮度分布。
- 黑点和高光范围。
- 星点尺寸。

不要通过独立通道全范围归一化实现匹配。推荐对 L 使用单调亮度曲线，使其综合色调接近 `Yrgb`。

## 亮度注入

安全公式：

```text
Ytarget = (1-w) * Yrgb + w * Lmatched
gain = Ytarget / max(Yrgb, epsilon)
LRGB = RGB * gain
```

推荐起点：

```text
w = 0.45-0.70
```

- L 明显更锐、更干净：可提高到 `0.7-0.8`。
- L 噪声更高或星点更肥：降低到 `0.3-0.5`。
- 发射星云窄带 L：只通过星云蒙版局部注入，避免改变星点和综合色调。

## 色彩恢复

L 注入会降低饱和度。只恢复已有颜色差异：

```text
result = Ytarget + saturation_factor * (LRGB - Ytarget)
```

通常 `saturation_factor=1.05-1.25`。背景区域应降低饱和增强，避免彩噪。

## 星点保护

- 使用星点蒙版降低 L 注入权重。
- 亮星核心不能因 L 通道而完全去色。
- L 与 RGB 星点 FWHM 差异大时，先匹配 PSF 或降低星点区域权重。
- 不要对 LRGB 成片全图做强反卷积。

## 推荐流程

```text
校准/配准
→ RGB 合成与校色
→ L 背景/亮度匹配
→ 亮度域注入
→ 饱和恢复
→ 输出 32-bit TIFF
→ pipeline.py 后续 HDR/锐化/风格
```

```bash
python scripts/pipeline.py lrgb_combined.tif final.jpg \
  --strength adaptive \
  --target-type galaxy \
  --style galaxy_core \
  --steps stretch,enhance,sharpen,final_color,style,final_denoise \
  --keep-all
```

## 质量审查

- L 注入前后星点位置完全一致。
- RGB 色相未因 L 注入改变。
- 背景噪声没有明显增加。
- 星系核或星云核心没有裁切。
- 细节来自 L 通道原始数据，没有锐化光晕。
- 饱和恢复没有让暗背景出现彩色斑块。

## 常见失败

- 综合色彩发灰：降低 L 权重或轻度恢复饱和。
- 星点白化：星点区域减少 L 注入。
- 背景颗粒增加：先处理 L 噪声，或降低暗部注入。
- 星点黑边：L/RGB PSF 不匹配或锐化过强。
- 结构错位：重新配准，不能通过模糊掩盖。
