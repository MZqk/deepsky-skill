---
type: "Method"
title: "LRGB处理与调色"
description: "LRGB 把高分辨的 L（亮度）通道与 R/G/B 彩色通道合成，兼顾细节与色彩。"
category: "04-后期处理"
tags: ["后期", "进阶", "LRGB", "调色", "PixInsight", "SCNR", "拉伸"]
difficulty: "进阶"
audience: "已掌握叠加、想要做宽带彩色（星系/星云）并正确合成亮度与色彩通道的拍摄者。"
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
  系统: ["单色相机获得且已正确校准、配准的 L/R/G/B 数据"]
  条件: ["通道对应关系、线性状态、采样、背景和色彩校准输入已确认"]
  不适用: ["把审美性调色宣称为唯一物理真色", "来源或通道映射不明的数据"]
sources:
  - id: raw-research
    resource: "/raw/素材调研报告.md"
    title: "深空天文拍摄知识库素材调研报告"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-effe6809
    resource: "https://chaoticnebula.com/pixinsight-lrgb-workflow/"
    title: "ChaoticNebula：Broadband LRGB 完整流程"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-0d4ce7ff
    resource: "https://www.theastrogeek.com/dark_sky_journal/pixinsight-lrgb-combine-tutorial"
    title: "The Astro Geek：PixInsight LRGB 合成分步教程"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-674f4c45
    resource: "https://chaoticnebula.com/pixinsight-scnr/"
    title: "ChaoticNebula：SCNR 与色彩平衡说明"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-333fed7b
    resource: "https://chaoticnebula.com/color-balancing-with-pixinsight-spectrophotometric-color-calibration/"
    title: "ChaoticNebula：Photometric/Spectrophotometric 色彩校准"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"

---
# LRGB处理与调色

**摘要**：LRGB 把高分辨的 L（亮度）通道与 R/G/B 彩色通道合成，兼顾细节与色彩。PixInsight 中流程为：线性阶段做色彩校准（ColorCalibration / PhotometricColorCalibration）、各通道 LinearFit 对齐背景；非线性后用 LRGBCombination 合成、SCNR 去绿、曲线拉伸与局部增强。关键在于先校准色彩再拉伸，并把 L 的细节「借」给彩色图。

## 背景 / 适用场景
需要：已分别叠加好的 L、R、G、B 主图（也可用合成亮度 Synthetic Luminance 代替真实 L）。软件：PixInsight（LRGBCombination、ColorCalibration、SCNR、CurveTransformation、HistogramTransformation、ChannelCombination、LinearFit）。若素材为 OSC 单色相机，可用 ChannelExtraction 抽取合成 L。

## 核心知识点
- 色彩校准（线性阶段）：用 ColorCalibration 或 PhotometricColorCalibration（基于 Gaia 星表，Spectrophotometric 模式）做初始白平衡，消除滤镜/大气带来的色偏。
- 通道亮度对齐：用 LinearFit 把 R/G/B 的背景与强度统一到同一参考通道，避免某通道过强导致偏色。
- 合成亮度 L：优先使用真实 L 通道（可透过光害滤镜拍更长曝光获得更多信号）；无 L 时用 ChannelExtraction 抽取 R/G/B 合成 Synthetic Luminance。
- LRGBCombination（非线性阶段）：将 L 的亮度信息赋予彩色图。典型做法——先拉伸 L 与 RGB，再把 L 作为 Luminance、RGB 作为 Chrominance 合成；可用 Luminance 滑块控制 L 贡献（常 0.7–1.0），并可对彩色图做轻微降噪（Chrominance 降噪）。
- SCNR（Subtractive Chromatic Noise Reduction，去绿）：Ha 发射星云常导致绿色溢出，用 SCNR 选 Green、Method=Average Neutral 把绿色中和；若想保留哈勃 palette 的绿则跳过。
- 曲线拉伸：线性阶段用 ScreenTransferFunction（STF）仅用于预览；正式拉伸用 HistogramTransformation（拖动中灰/黑点）或 CurveTransformation（Arcsinh/自定义曲线）把暗部提亮，避免一次性过曝。
- 通道对齐/色度噪声：合成后若仍有色噪，用 ACDNR（Chrominance 模式）针对颜色噪声做最终降噪；星点可用 StarNet/StarXTerminator 分离后再分别处理。
- 局部增强：用 CurvesTransformation + 星点/结构蒙版（Star Mask / Range Mask）提升星云细节与对比，避免整体提亮导致背景发灰。

## 权威问答口径

- 本页可承担的回答范围：单色相机获得且已正确校准、配准的 L/R/G/B 数据；成立条件：通道对应关系、线性状态、采样、背景和色彩校准输入已确认。
- 遇到以下情况必须拒绝确定结论或转入专项页/实测：把审美性调色宣称为唯一物理真色；来源或通道映射不明的数据。
- 具体数值、兼容性、软件行为或安全结论只使用正文就近绑定的来源，并同时受 `stale_after` 与人工 `verified.scope` 限制。


## 注意事项
- 务必先在线性阶段完成色彩校准与 LinearFit，再拉伸；先拉伸后校准会导致色偏难以修正。
- LRGBCombination 前 L 与 RGB 需都已拉伸到相近显示范围，否则合成后细节错位或偏色。
- 不要过度 SCNR，会把本该有的绿色（如 Ha 区域）全部抹掉，丢失科学信息。
- 拉伸（STF）只是预览，不会写入像素；正式出图必须用 HistogramTransformation/CurveTransformation。
- L 通道与 RGB 的配准/采样必须一致（建议同像素尺度），否则合成后星点重影。
- OSC 相机直接出彩色帧时无需 LRGBCombination 合成，但仍需做色彩校准与拉伸。

## 示例
- PixInsight LRGB 流程：叠加 L/R/G/B → 线性阶段 ColorCalibration + 各通道 LinearFit → BlurXTerminator/NoiseXTerminator 降噪与反卷积 → 拉伸 L 与 RGB（CurveTransformation）→ LRGBCombination（L 为亮度、RGB 为色度）→ 视情况 SCNR Green → 局部 Curves 增强 → ACDNR 收色噪。
- 无真实 L：ChannelExtraction 从 RGB 抽 L，再用 LuminanceWorkflow 提升对比后并入彩色图。
- Ha 强星云去绿：Process > SCNR，Parameters 选 Green、Method=Average Neutral、Amount 通常 0.7–1.0，配合星点蒙版避免影响星色。

## 相关资源
- [PixInsight LRGBCombination 文档](https://www.pixinsight.com/)
- [ChaoticNebula：PixInsight LRGB 工作流](https://chaoticnebula.com/pixinsight-lrgb-workflow/)
- [ChaoticNebula：SCNR 去绿教程](https://chaoticnebula.com/pixinsight-scnr/)
- [ChaoticNebula：色彩校准（Spectrophotometric）](https://chaoticnebula.com/color-balancing-with-pixinsight-spectrophotometric-color-calibration/)

## 信息来源
- [ChaoticNebula：Broadband LRGB 完整流程](https://chaoticnebula.com/pixinsight-lrgb-workflow/)
- [The Astro Geek：PixInsight LRGB 合成分步教程](https://www.theastrogeek.com/dark_sky_journal/pixinsight-lrgb-combine-tutorial)
- [ChaoticNebula：SCNR 与色彩平衡说明](https://chaoticnebula.com/pixinsight-scnr/)
- [ChaoticNebula：Photometric/Spectrophotometric 色彩校准](https://chaoticnebula.com/color-balancing-with-pixinsight-spectrophotometric-color-calibration/)


## 关联知识

- [校准与叠加](/04-后期处理/校准与叠加.md)
- [后期软件对比](/07-软件工具/后期软件对比.md)

## 维护记录
| 日期 | 修改人 | 变更说明 |
|------|--------|----------|
| 2025-07-10 | 知识库助手 | 基于互联网调研初稿 |
