---
type: "Method"
title: "窄带SHO映射"
description: "窄带映射是把 Ha、SII、OIII 三个单色通道按规则分配到 R/G/B，最经典是 SHO（SII→红、Ha→绿、OIII→蓝）即哈勃调色板。"
category: "04-后期处理"
tags: ["后期", "高手", "窄带", "SHO", "HaRGB", "PixelMath", "哈勃调色板", "PixInsight"]
difficulty: "高手"
audience: "使用 Ha/SII/OIII 窄带滤镜拍摄星云，希望把单色信号映射成科学/艺术化彩色（哈勃调色板）或把窄带融入宽带的进阶用户。"
status: stable
created: "2025-07-10"
updated: "2026-08-27"
stale_after: "2027-07-30"
generated:
  by: process:okf-migration
  at: "2026-07-30T12:00:00+08:00"
review:
  state: needs-human-review
  owner: knowledge-base-maintainer
applies_to:
  系统: ["已校准配准的 SII、Hα、OIII 单色窄带数据"]
  条件: ["通道身份、线性状态、信噪比和映射目的明确，并保留原始通道"]
  不适用: ["把 SHO 色彩称为肉眼真实颜色", "缺失通道或通道身份不明时伪造映射"]
sources:
  - id: raw-research
    resource: "/raw/素材调研报告.md"
    title: "深空天文拍摄知识库素材调研报告"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-da4c8fce
    resource: "https://thecoldestnights.com/2020/06/pixinsight-dynamic-narrowband-combinations-with-pixelmath/"
    title: "The Coldest Nights：Dynamic narrowband combinations with PixelMath"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-ed6a671f
    resource: "https://astroimagery.com/techniques/post-processing/hubble-palette-colours/"
    title: "AstroImagery：Hubble Palette 颜色映射（Ha/SII/OIII→RGB）"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-bdcdbfd5
    resource: "https://nightskypics.com/sho-vs-dynamic-narrowband-combinations/"
    title: "NightSkyPics：SHO 与动态窄带组合对比"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-e9e0e88b
    resource: "https://www.highpointscientific.com/astronomy-hub/post/astro-photography-guides/combining-narrowband-data-pixinsight"
    title: "High Point Scientific：PixInsight 合成窄带数据教程"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"

---
# 窄带SHO映射

**摘要**：窄带映射是把 Ha、SII、OIII 三个单色通道按规则分配到 R/G/B，最经典是 SHO（SII→红、Ha→绿、OIII→蓝）即哈勃调色板。PixInsight 可用 ChannelCombination 做静态映射，或用 PixelMath 写表达式做「动态映射」（每个像素比例不同）以获得更丰富的色彩。HaRGB 则是把 Ha 当作亮度/细节并入彩色 RGB 图。

## 背景 / 适用场景
需要：分别叠加好的 Ha、SII、OIII 单色主图（建议已配准、背景值对齐）。软件：PixInsight（ChannelCombination、PixelMath、CurveTransformation、LRGBCombination）；也可用 Siril 的 PixelMath 公式。HaRGB 还需 RGB 或 L 通道。

## 核心知识点
- 标准哈勃调色板（SHO）映射：SII→Red、Ha→Green、OIII→Blue。用 ChannelCombination（Combine 模式，分别指定 R=SII、G=Ha、B=OIII）即可完成静态映射。
- 替代调色板：HOO（Ha→红、OIII→蓝、绿常取 Ha 与 OIII 混合）、HSO 等可自由分配；窄带数据不必拘泥于 SII=红。
- 静态（static）映射：各通道系数为固定数字，且系数之和须=1 以避免通道截断（如 R=.6*SII+.4*Ha）。
- 动态（dynamic）映射（PixelMath）：系数不是常数而是随像素变化的「图像」。例：以 f = (OIII*Ha) 的拉伸副本作为动态因子，使 Ha/OIII 都强的区域偏向某色、弱区保持 HOO，色彩更自然。
- 通用 PixelMath 表达式（The Coldest Nights，Sii/Ha/Oiii）：R = (Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha；G = ((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii；B = Oiii。（~X 表示 1-X 反相；系数和=1）
- Ha/Oiii 双窄带通用式：R = Ha；G = ((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii；B = Oiii。
- 混合前准备：动态映射要求各窄带通道已拉伸且背景值近似一致；否则先用 CurvesTransformation 调整。
- HaRGB（窄带+宽带）：把 Ha 作为亮度/细节层用 LRGBCombination 并入 RGB 彩色图（Ha 作 L 或额外细节），突出恒星形成区；星系常加 Ha 强调 HII 区。
- 亮度层与调色：SHO 合成后建议另建 Luminance 层（如用 Ha 或 Ha+OIII）提升质感；再用 CurvesTransformation 的 Hue 微调星点颜色（紫色星点可对星蒙版做 SCNR Green 再反相）。

## 权威问答口径

- 本页可承担的回答范围：已校准配准的 SII、Hα、OIII 单色窄带数据；成立条件：通道身份、线性状态、信噪比和映射目的明确，并保留原始通道。
- 遇到以下情况必须拒绝确定结论或转入专项页/实测：把 SHO 色彩称为肉眼真实颜色；缺失通道或通道身份不明时伪造映射。
- 具体数值、兼容性、软件行为或安全结论只使用正文就近绑定的来源，并同时受 `stale_after` 与人工 `verified.scope` 限制。


## 注意事项
- PixelMath 表达式中各通道系数之和必须等于 1，否则会截断/溢出（PixelMath 输出归一化到 [0,1]）。
- 动态映射依赖拉伸后的数据；线性阶段直接做动态映射需改用拉伸副本，否则因子无意义。
- 各窄带通道背景值差异过大会导致映射后偏色，需先对齐背景（LinearFit / 背景归一化）。
- 双窄带（如 Ha+OIII duo-band）缺少真实 SII 时，只能用 PixelMath 合成「模拟硫」通道，非真实 SHO。
- 不要过度追求哈勃金蓝配色而牺牲科学真实性，必要时保留说明。
- 星点颜色异常（偏紫）通常是通道配准或缩放不一致所致，先检查对齐再调色。

## 示例
- 静态 SHO（PixInsight）：Process > ChannelCombination，Combine，R 选 SII、G 选 Ha、B 选 OIII → 应用。
- 动态 SHO（PixelMath）：R = (Oiii^~Oiii)*Sii + ~(Oiii^~Oiii)*Ha；G = ((Oiii*Ha)^~(Oiii*Ha))*Ha + ~((Oiii*Ha)^~(Oiii*Ha))*Oiii；B = Oiii。
- HaRGB：先把 RGB 彩色图做好，再用 LRGBCombination 把 Ha 作为 Luminance 并入，增强发射区细节。
- Siril PixelMath 模拟：Red=0.8*R+0.2*B、Green=R、Blue=0.5*B+0.5*G（依素材调比例，再进 Photoshop 微调）。

## 相关资源
- [The Coldest Nights：PixelMath 动态窄带合成](https://thecoldestnights.com/2020/06/pixinsight-dynamic-narrowband-combinations-with-pixelmath/)
- [High Point Scientific：PixInsight 窄带数据合成](https://www.highpointscientific.com/astronomy-hub/post/astro-photography-guides/combining-narrowband-data-pixinsight)
- [NightSkyPics：SHO vs 动态窄带组合](https://nightskypics.com/sho-vs-dynamic-narrowband-combinations/)
- [AstroImagery：哈勃调色板映射与 PixelMath 公式](https://astroimagery.com/techniques/post-processing/hubble-palette-colours/)

## 信息来源
- [The Coldest Nights：Dynamic narrowband combinations with PixelMath](https://thecoldestnights.com/2020/06/pixinsight-dynamic-narrowband-combinations-with-pixelmath/)
- [AstroImagery：Hubble Palette 颜色映射（Ha/SII/OIII→RGB）](https://astroimagery.com/techniques/post-processing/hubble-palette-colours/)
- [NightSkyPics：SHO 与动态窄带组合对比](https://nightskypics.com/sho-vs-dynamic-narrowband-combinations/)
- [High Point Scientific：PixInsight 合成窄带数据教程](https://www.highpointscientific.com/astronomy-hub/post/astro-photography-guides/combining-narrowband-data-pixinsight)


## 关联知识

- [滤镜系统](/02-器材百科/滤镜系统.md)
- [校准与叠加](/04-后期处理/校准与叠加.md)

## 维护记录
| 日期 | 修改人 | 变更说明 |
|------|--------|----------|
| 2025-07-10 | 知识库助手 | 基于互联网调研初稿 |
