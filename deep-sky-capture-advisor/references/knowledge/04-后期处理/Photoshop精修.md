---
type: "Method"
title: "Photoshop精修"
description: "Photoshop 是窄带/宽带主图完成后的精修终端：用 16-bit 模式与曲线/色阶做最终拉伸，配合 RC Astro 的 NoiseXTerminator（AI 降噪）、StarXTerminator（星点分离）与 StarShrink（星点缩小）管理星点，再用蒙版做局部亮度/色彩增强。"
category: "04-后期处理"
tags: ["后期", "进阶", "Photoshop", "降噪", "星点", "局部增强", "NoiseXTerminator"]
difficulty: "进阶"
audience: "已在 PixInsight/Siril 完成主流程、需要进入 Photoshop（或 Affinity Photo）做最终拉伸、降噪、星点管理与局部润色的用户。"
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
  系统: ["已完成天文专用校准、叠加和基础拉伸的展示候选图像"]
  条件: ["保留线性母版和可回退副本，所有局部操作在全分辨率检查星点与背景"]
  不适用: ["替代校准、配准或科学测量流程", "用生成式内容冒充原始天文信号"]
sources:
  - id: raw-research
    resource: "/raw/素材调研报告.md"
    title: "深空天文拍摄知识库素材调研报告"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-0d0bff07
    resource: "https://www.rc-astro.com/software/photoshop-bundle/"
    title: "RC Astro：Photoshop Bundle 工具说明（降噪/去星/梯度/缩星）"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-e1ba7dc2
    resource: "https://www.rc-astro.com/software/nxt/"
    title: "RC Astro：NoiseXTerminator 官方页"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-493930e8
    resource: "https://macobservatory.com/ai-astrophotography-image-processing/"
    title: "MacObservatory：AI 天文图像处理（RC Astro/GraXpert/StarNet++）"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-6fe46cc1
    resource: "https://www.opticalmechanics.com/mastering-deep-sky-astrophotography-processing/"
    title: "Optical Mechanics：Mastering Deep-Sky 处理（降噪/星点/局部增强）"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"

---
# Photoshop精修

**摘要**：Photoshop 是窄带/宽带主图完成后的精修终端：用 16-bit 模式与曲线/色阶做最终拉伸，配合 RC Astro 的 NoiseXTerminator（AI 降噪）、StarXTerminator（星点分离）与 StarShrink（星点缩小）管理星点，再用蒙版做局部亮度/色彩增强。关键是用蒙版保护背景与星点，分层非破坏式编辑。

## 背景 / 适用场景
需要：已拉伸、可视觉化的主图（从 PixInsight 导出 16-bit TIFF，建议保留 LAB/RGB）。软件：Photoshop CC 或 Affinity Photo 2；RC Astro Photoshop Bundle（NoiseXTerminator、StarXTerminator、GradientXTerminator、StarShrink，付费，许可证也可用于 PixInsight/CLI）。

## 核心知识点
- 导入与位深：用 16-bit/channel 模式打开 TIFF，所有调整层（Curve/Levels/HP 滤镜）非破坏式操作，便于回退。
- 最终拉伸：用 Curves（曲线）或 Levels（色阶）做非线性拉伸；对星云暗部用 S 形曲线提对比，配合图层蒙版只作用于目标区域。
- AI 降噪 NoiseXTerminator（RC Astro，Photoshop 滤镜）：ML 降噪，保留暗弱结构与星点形状；参数 Strength 控制强度，通常在背景/暗部强、结构区弱（可加蒙版分区）。
- 星点分离 StarXTerminator：把星点与星云/星系分离为两个图层，便于分别对星点调色缩星、对星云单独提细节而不影响星点。
- 星点缩小 StarShrink：智能收紧过曝/膨胀的大星点（受大气与曝光影响），不伤周围结构，让深空目标更突出。
- 梯度校正 GradientXTerminator：消除光害/暗角/月光造成的大尺度渐变背景（也可在 PixInsight 的 DynamicBackgroundExtraction 先做）。
- 局部增强：用 Luminosity 蒙版（或自绘选区）对星云核心/暗云做 Curves 提亮或对比增强；用 Selective Color / Hue-Saturation 微调色相。
- 星点着色：分离后的星点层可整体调色（如中性灰偏暖），避免紫边；用蒙版防止背景被污染。
- 导出：最终存为 16-bit TIFF 母版，网络发布再转 8-bit sRGB JPEG/PNG，注意色彩空间转换。

## 权威问答口径

- 本页可承担的回答范围：已完成天文专用校准、叠加和基础拉伸的展示候选图像；成立条件：保留线性母版和可回退副本，所有局部操作在全分辨率检查星点与背景。
- 遇到以下情况必须拒绝确定结论或转入专项页/实测：替代校准、配准或科学测量流程；用生成式内容冒充原始天文信号。
- 具体数值、兼容性、软件行为或安全结论只使用正文就近绑定的来源，并同时受 `stale_after` 与人工 `verified.scope` 限制。


## 注意事项
- 不要用 8-bit 流程精修，会断阶（banding）；始终 16-bit 以上。
- NoiseXTerminator 强度过高会抹掉暗弱星云细节，建议分区域蒙版、适度。
- StarXTerminator 分离后若直接删除星点层会丢失星点，需保留或重新合成。
- Photoshop 中星点缩小过度会让画面显得「假」、失去星野氛围，适度即可。
- 不要在未对齐的图层上做局部增强，避免错位。
- 导出发布版务必先把 ProPhoto/AdobeRGB 转 sRGB，否则网页显示偏色。[不确定：部分工作流保留更广色域母版]

## 示例
- PS 精修流程：PixInsight 导出 16-bit TIFF → PS 用 Curves 做最终 S 形拉伸 → 副本层跑 NoiseXTerminator（Strength 中值）→ StarXTerminator 分离星点 → 对星云层做 Luminosity 蒙版局部增强 → StarShrink 收星点并调星色 → 合并 → 导出。
- 梯度处理：先在 PixInsight 用 DynamicBackgroundExtraction 去梯度，残留渐变再到 PS 用 GradientXTerminator 收尾。
- 局部增强示例：新建 Curves 调整层，用画笔在星云暗部蒙版内提亮、在背景区压暗，营造立体感。

## 相关资源
- [RC Astro Photoshop Bundle（NoiseX/StarX/GradientX/StarShrink）](https://www.rc-astro.com/software/photoshop-bundle/)
- [RC Astro NoiseXTerminator 详情](https://www.rc-astro.com/software/nxt/)
- [MacObservatory：AI 在天文后期中的应用](https://macobservatory.com/ai-astrophotography-image-processing/)
- [Optical Mechanics：深空精修全流程概述](https://www.opticalmechanics.com/mastering-deep-sky-astrophotography-processing/)

## 信息来源
- [RC Astro：Photoshop Bundle 工具说明（降噪/去星/梯度/缩星）](https://www.rc-astro.com/software/photoshop-bundle/)
- [RC Astro：NoiseXTerminator 官方页](https://www.rc-astro.com/software/nxt/)
- [MacObservatory：AI 天文图像处理（RC Astro/GraXpert/StarNet++）](https://macobservatory.com/ai-astrophotography-image-processing/)
- [Optical Mechanics：Mastering Deep-Sky 处理（降噪/星点/局部增强）](https://www.opticalmechanics.com/mastering-deep-sky-astrophotography-processing/)


## 关联知识

- [校准与叠加](/04-后期处理/校准与叠加.md)
- [LRGB处理与调色](/04-后期处理/LRGB处理与调色.md)

## 维护记录
| 日期 | 修改人 | 变更说明 |
|------|--------|----------|
| 2025-07-10 | 知识库助手 | 基于互联网调研初稿 |
