---
type: "Method"
title: "Siril 新手首图工作流"
description: "面向单次或多夜 OSC/单反原始数据，以“分组、校准、配准、筛选、整合、线性主图、基础处理、可复现导出”为验收节点完成第一张图。"
category: "04-后期处理"
tags: ["后期", "Siril", "首图", "OSC", "单反", "校准", "叠加", "线性处理"]
difficulty: "新手"
audience: "已保留 Lights 和匹配校准帧，希望用 Siril 完成首张基础成图、但不想靠不透明的一键脚本猜测结果的拍摄者"
status: stable
created: "2026-07-30"
updated: "2026-08-27"
stale_after: "2026-11-27"
generated:
  by: process:official-source-raw-capture
  at: "2026-08-27T10:13:26+08:00"
review:
  state: needs-human-review
  owner: knowledge-base-maintainer
applies_to:
  系统: ["Siril 当前稳定版本可读取的深空原始子帧及匹配校准数据"]
  条件: ["先确认文件类型、相机模式、目录、校准关系和磁盘空间"]
  不适用: ["只剩 JPEG 或厂商成图", "未知通道映射、混合数据或设备专用流程"]
sources:
  - id: raw-p0-user-scenarios
    resource: "/raw/2026-07-30-P0用户场景与首图工作流来源.md"
    title: "P0：用户场景、首图工作流与诊断页面来源记录"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-siril-docs
    resource: "https://siril.readthedocs.io/"
    title: "Siril 官方文档"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-siril-calibration
    resource: "https://siril.readthedocs.io/en/latest/preprocessing/calibration.html"
    title: "Siril 官方文档：Calibration"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-siril-stacking
    resource: "https://siril.readthedocs.io/en/latest/preprocessing/stacking.html"
    title: "Siril 官方文档：Stacking"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-siril-scripts
    resource: "https://siril.readthedocs.io/en/latest/scripts/Script-files.html"
    title: "Siril 官方文档：Script Files"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-siril-seestar
    resource: "https://siril.org/tutorials/seestar/"
    title: "Siril 官方：Processing ZWO Seestar images"
    rights: unknown
    accessed_at: "2026-08-26"
    usage: link-only
    evidence_level: primary
  - id: raw-smart-telescope-official-ledger
    resource: "/raw/2026-08-26-智能望远镜与深空后期官方资料台账.md"
    title: "智能望远镜与深空后期官方资料台账"
    rights: unknown
    accessed_at: "2026-08-26"
    usage: metadata-only
    evidence_level: internal-ledger
  - id: raw-smart-telescope-official-captures
    resource: "/raw/受限原始文本捕获说明.md"
    title: "智能望远镜与深空后期官网受限原始文本捕获说明"
    rights: unknown
    accessed_at: "2026-08-27"
    usage: "local-evidence-capture; public-link-only"
    evidence_level: internal-ledger

---

# Siril 新手首图工作流

**摘要**：首图工作流不是“点一次脚本就结束”，而是从可分组的原始数据开始，依次完成校准、配准、质量筛选、整合、线性主图检查和基础拉伸。每一步都保留可回退的文件和通过条件；出现问题时回到最早未通过的关口，而不是继续叠加或用强后期遮盖。

## 适用范围与不适用范围

适用：已保存 Lights，以及按[校准帧规范](/03-拍摄SOP/校准帧规范.md)确认过状态的 Dark、Flat 和 Bias 或 Dark Flat；相机可为 OSC 或单反/无反，数据可由当前安装的 Siril 版本和相应工作流处理。

不适用或须先确认：只剩 JPEG、智能镜仅有厂商成图、单色多通道的复杂工程、来源不明的混合数据、未确认导出格式的设备。智能镜或相机的机内堆栈和原始子帧导出能力是产品/版本特定的；先读厂商当前说明，再决定是否使用此流程。已确认的 Seestar 多张光帧不应直接套用本页的通用校准关口：Siril 的专用教程说明该设备专用路径只处理 Lights、不能在其中校准，应改走[智能望远镜导出数据的 Siril 工作流](/04-后期处理/智能望远镜导出数据的Siril工作流.md)。[^src-siril-seestar]

## 开始前：建立不可覆盖的输入

1. 保留原始 Lights 与校准帧，不在原目录覆盖、去拜耳、裁切或批量改名。
2. 为本次项目建立工作副本与会话记录，记录目标、日期、相机、滤镜、曝光、温度、增益/ISO、offset、读出模式、binning/ROI、相机姿态和校准关系。
3. 把不同滤镜、曝光、相机状态或明显不同夜晚条件分开。文件名相似不等于可合并。
4. 选择当前 Siril 版本中与数据类型相符的官方脚本或 GUI 流程。脚本的目录要求、支持格式和输出名称会变化，应以已安装版本的官方说明为准。

推荐的项目结构与会话记录见[数据管理、命名、备份与可复现归档](/03-拍摄SOP/数据管理、命名、备份与可复现归档.md)。

## 七个验收关口

| 关口 | 你要做什么 | 通过时应能回答什么 | 不通过时回到哪里 |
|---|---|---|---|
| 1. 输入分组 | 分开 Light、Dark、Flat、Bias/Dark Flat，并按相机状态分组。 | 每一组 Light 对应什么校准数据？ | 会话记录和[校准帧现场速查卡](/03-拍摄SOP/校准帧现场速查卡.md)。 |
| 2. 校准路线 | 为 Flat 选择符合相机与软件要求的 Bias 或 Dark Flat 路线，并为 Light 找到匹配 Dark。 | 没有把不同匹配规则混成“全都同曝光”。 | 校准帧规范和当前软件文档。 |
| 3. 预处理 | 使用匹配的脚本或 GUI 执行校准；OSC 数据按当前流程去拜耳。 | 输出不是空序列，日志没有忽略输入或明显错配。 | 输入目录、脚本说明、文件格式。 |
| 4. 配准与质检 | 将同一组帧对齐，并查看连续原片和质量指标。 | 星点、构图、背景和帧数没有明显异常；坏帧可被说明。 | [单张检查与翻车诊断](/08-FAQ/单张检查与翻车诊断.md)。 |
| 5. 整合 | 以适合帧数和数据质量的平均/拒绝策略整合，并保留拒绝图或等效检查输出。 | 哪些帧被用到、哪些被排除、为何排除？ | 质量筛选与 Siril Stacking 文档。 |
| 6. 线性主图 | 保存未经强拉伸的整合结果及其输入/设置。 | 原始信号、背景和校准残差能被检查。 | 校准、配准、整合中的最早失败关口。 |
| 7. 基础处理与导出 | 先做背景与色彩的基础检查，再进行适度拉伸，保存母版和导出图。 | 发布图能回溯到线性主图和处理配方。 | 线性主图与会话记录。 |

Siril 的整合文档说明，叠加通过对齐并组合多张图像改善信噪比；平均整合的归一化、拒绝方法和权重应与数据情况相匹配，拒绝图可帮助检查被排除的像素。不要把任何一种拒绝算法或默认参数写成少量帧到大量帧都适用的万能预设。[Siril Stacking](https://siril.readthedocs.io/en/latest/preprocessing/stacking.html)

## 按顺序完成第一张图

### 1. 先看 Light，不先跑脚本

抽看开始、中段、末段的原始 Light：星点是否逐步变软、是否开始拖线、背景是否突变、构图是否漂移、是否有雾/云/直射灯/断连迹象。把明显不合格的候选帧标出来，但保留原文件和排除原因。

### 2. 校准与预处理

按当前版本官方文档选择流程，明确输入目录和所用主帧。关键不是“脚本运行完成”，而是确认它读到了正确的帧组、没有把不同曝光或相机状态混用。Flat 的校正路线只能按实际软件校准图选择；Dark、Bias 与 Dark Flat 的作用和匹配关系见[校准帧规范](/03-拍摄SOP/校准帧规范.md)。

### 3. 配准后再决定哪些帧值得整合

配准输出用于检查共同星场是否对齐。将质量筛选视为一项可解释的决定：记录因失焦、严重拖线、异常背景、云、飞机/卫星影响或断连而排除的帧，不要只依赖单一分数阈值。

### 4. 整合并保存线性主图

对通过的帧进行整合，保存线性主图、整合设置、拒绝图或等效诊断图，并记录实际纳入的帧数。线性图在屏幕上看起来暗、灰或颜色不鲜艳并不自动说明失败；不要仅为“看起来亮”而覆盖线性母版。

### 5. 基础处理和导出

在线性阶段先检查并处理可确认的背景不均、色彩基础和剩余校准问题，再做保守拉伸。每次会改变图像意义的处理都保存项目、脚本或参数记录；导出一份便于分享的图，同时保留可编辑母版。复杂的 LRGB、SHO 或 Photoshop 精修应在基础首图通过后再加入。

## 三种常见“像失败但未必失败”的情况

| 现象 | 先检查 | 不要马上做什么 |
|---|---|---|
| 整合后的线性图很暗/灰 | 是否确实是线性数据、直方图和背景是否正常、星点是否已配准。 | 直接把源文件覆盖成强拉伸版本。 |
| 脚本完成但结果看起来不对 | 日志是否读取了预期目录、帧数是否合理、校准帧匹配是否正确。 | 连续重复运行不同脚本并覆盖中间结果。 |
| 背景、暗角或尘斑在处理后更明显 | Flat 的光路/姿态/亮度、Dark/Dark Flat/Bias 路线和帧分组。 | 先用重度裁切、降噪或饱和度把残差遮掉。 |

## 首图交付物清单

- 未覆盖的原始 Light 与校准帧；
- 会话记录与输入分组说明；
- 已通过/已排除的帧列表及原因；
- 线性主图、整合设置和拒绝图或等效检查输出；
- 可编辑处理项目、脚本或关键参数；
- 一张导出图，以及能回到母版的位置。

## 权威问答口径

- 本页可承担的回答范围：Siril 当前稳定版本可读取的深空原始子帧及匹配校准数据；成立条件：先确认文件类型、相机模式、目录、校准关系和磁盘空间。
- 遇到以下情况必须拒绝确定结论或转入专项页/实测：只剩 JPEG 或厂商成图；未知通道映射、混合数据或设备专用流程。
- 具体数值、兼容性、软件行为或安全结论只使用正文就近绑定的来源，并同时受 `stale_after` 与人工 `verified.scope` 限制。


## 关联知识

- [校准帧现场速查卡](/03-拍摄SOP/校准帧现场速查卡.md)
- [校准帧规范](/03-拍摄SOP/校准帧规范.md)
- [校准与叠加](/04-后期处理/校准与叠加.md)
- [智能望远镜导出数据的 Siril 工作流](/04-后期处理/智能望远镜导出数据的Siril工作流.md)
- [后期软件对比](/07-软件工具/后期软件对比.md)
- [LRGB处理与调色](/04-后期处理/LRGB处理与调色.md)
- [窄带SHO映射](/04-后期处理/窄带SHO映射.md)

## 维护记录

| 日期 | 修改人 | 变更说明 |
|---|---|---|
| 2026-08-26 | process:official-smart-telescope-ingest | 补充 Seestar 专用分流：不把通用校准关口错误套用到其仅含 Lights 的官方 Siril 路径。 |
| 2026-08-27 | process:official-source-raw-capture | 将 Seestar 与 Siril 官网文本先补入受限捕获集，并更新智能镜分流的来源链。 |
| 2026-07-30 | Codex | 第二批 P0 新增：把 Siril 首图拆成可验收、可回退的七个关口，不把一键脚本当作不可解释的终点。 |
