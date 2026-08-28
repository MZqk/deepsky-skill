---
type: "Software Guide"
title: "N.I.N.A 使用要点"
description: "N.I.N.A（Nighttime Imaging 'N' Astronomy）是一款免费、开源、模块化的 Windows 深空拍摄控制软件，覆盖对星、构帧、对焦、居中、解析、导星与序列拍摄全流程。"
category: "07-软件工具"
tags: ["软件", "NINA", "ASCOM", "序列", "自动化", "深空拍摄"]
difficulty: "进阶"
audience: "已拥有赤道仪/相机/导星设备、希望用一台 Windows 电脑完成从对星到拍摄全流程自动化的深空摄影者；也适合从 APT、SGP 等迁移的进阶用户。"
status: stable
created: "2025-07-10"
updated: "2026-08-27"
stale_after: "2027-01-30"
generated:
  by: process:okf-migration
  at: "2026-07-30T12:00:00+08:00"
review:
  state: needs-human-review
  owner: knowledge-base-maintainer
applies_to:
  系统: ["Windows 上使用当前 N.I.N.A. 稳定版及兼容 ASCOM/原生驱动的采集系统"]
  条件: ["菜单、插件、设备支持与序列行为以当前官方文档和本机版本复核"]
  不适用: ["将 develop 文档直接视为稳定版行为", "未经白天干跑的无人值守自动化"]
sources:
  - id: raw-research
    resource: "/raw/素材调研报告.md"
    title: "深空天文拍摄知识库素材调研报告"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-cfce4141
    resource: "https://nighttime-imaging.eu/"
    title: "N.I.N.A 官网/下载与更新说明"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-e587bbb6
    resource: "https://nighttime-imaging.eu/docs/master/site/contributing/plugins/"
    title: "N.I.N.A 插件系统官方文档"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-b938f0f6
    resource: "https://nighttime-imaging.eu/docs/master/site/advanced/advancedsequence/"
    title: "N.I.N.A 高级序列官方文档"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-48cec40f
    resource: "https://nighttime-imaging.eu/docs/develop/site/advanced/guiding/"
    title: "N.I.N.A 导星官方文档(PHD2集成)"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-de0cdf97
    resource: "https://pi.bestxtech.com/nina/"
    title: "N.I.N.A 中文使用指南"
    evidence_level: secondary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-581bf38e
    resource: "https://nighttime-imaging.eu/news/"
    title: "N.I.N.A 3.0 版本更新日志示例"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"

---
# N.I.N.A 使用要点

**摘要**：N.I.N.A（Nighttime Imaging 'N' Astronomy）是一款免费、开源、模块化的 Windows 深空拍摄控制软件，覆盖对星、构帧、对焦、居中、解析、导星与序列拍摄全流程。它通过 ASCOM 平台连接几乎所有设备，并支持插件生态与高级序列（Advanced Sequencer）实现无人值守自动化；新手可用内置向导快速上手，进阶用户用触发器/条件实现复杂流程。

## 背景 / 适用场景
适用场景：DSO（深空天体）长曝光叠加拍摄、马赛克拼接、窄带/宽带多目标自动计划。前置条件：Windows 10/11 64 位；需先安装 ASCOM Platform（设备统一接口层）及对应硬件的 ASCOM 驱动（相机、赤道仪/镜塔、电调焦、导星相机、滤镜轮、天平/穹顶等）；导星与解析需配合 PHD2 与 ASTAP/PlateSolve2。当前主线版本为 3.x（如 3.0.x/3.2.x nightly），3.0 起自动对焦、序列器、解析、导星、构帧向导等均有重大增强。

## 核心知识点
- 设备连接统一走 ASCOM：Options > Equipment 下分别添加 Camera、Telescope(Mount)、Focuser、Guide、Filter Wheel、Rotator、Weather、Safety Monitor 等；相机若默认不支持需先装 ASCOM 平台与厂商驱动。
- 拍摄流程标签（Tabs）：Imaging（拍摄/取景）、Sequence（序列）、Equipment（设备状态）、Options（设置）等，构成一条龙工作流。
- Framing Wizard（构帧向导）：输入目标后可在画面中预览视场、旋转角、构图，并一键 Slew+Center 居中。
- 自动对焦（Auto-Focus）：基于星点 Half-Flux Radius / 对比度检测，3.0 起支持多种曲线拟合方法并新增对比度检测对焦（contrast detection AF）；含 backlash 补偿（分两种可选行为）。
- 解析对齐（Plate Solving）：内置 Platesolve 设置，可调用 ASTAP、PlateSolve2、本地/在线求解器做居中（Center After Slew）与 GoTo 校准。
- 导星（Guiding）：原生集成 PHD2（也可 MetaGuide），序列中自动 Start/Stop Guiding 并接收导星状态。
- 高级序列（Advanced Sequencer）：由 Sequence Start / Target / End 三段组成，可用触发器（Trigger）、条件（Container/Condition）、循环（Loop）编排复杂流程（如先拍亮场预热、到中天翻转 Meridian Flip 后继续、按温度/湿度切换计划），可存为 XML 模板复用。
- 插件生态：通过 Plugin Manifest Repository 在应用内插件页直接下载安装（如 Image Planner、Orbitrary、Safety Monitors、Dome 控制等），扩展官方未覆盖的专用功能。
- Planetarium 集成：可与 Stellarium、SkySafari 等星图联动发送/接收目标坐标，辅助规划与 GoTo。

## 权威问答口径

- N.I.N.A. 的功能、系统要求、插件和序列行为以当前官方文档与版本日志为准；插件存在不代表它适配所有设备或与当前版本兼容。[^src-cfce4141][^src-e587bbb6][^src-b938f0f6]
- 自动化序列必须经过本机连接、模拟/白天干跑和夜间安全收尾验证；文档支持某功能不等于用户系统已经可无人值守。[^src-b938f0f6]

## 注意事项
- 务必先装 ASCOM Platform 与对应驱动，再在 N.I.N.A 内添加设备；驱动版本不匹配会无法连接或掉线。
- Nightly（每夜构建）版本含新特性但可能不稳定，正式拍摄建议用稳定 Release 而非 early nightly。
- Meridian Flip（中天翻转）阈值与回中（Settle）时间要设置合理，否则翻转后可能丢失导星或撞击镜筒限位。
- 远程/无人值守前，务必配置 Safety Monitor 与断电/云量保护，避免设备受损。
- 插件来自社区仓库，安装前确认来源与兼容性，避免与当前版本冲突。

## 示例
- 推荐起步流程：Equipment 连好设备 → Framing Wizard 选目标并居中 → Auto-Focus 跑一次 → Sequence 建简单序列（如 L 各 30×300s、Gain/Offset 设定）→ 启动并开 PHD2 导星。
- 进阶自动化：Advanced Sequencer 中 Sequence Start 加‘唤醒设备/平场’触发器，Target 段放‘亮场循环+中天翻转条件’，Sequence End 加‘停导星/收镜/关机’；存为 XML 模板，多夜复用。
- GoTo 校准：在 Platesolve 中选 ASTAP，执行‘Center After Slew’，N.I.N.A 会解析当前画面并微调赤道仪指向，将目标拉到画面中心。

## 相关资源
- [N.I.N.A 官方网站](https://nighttime-imaging.eu/)
- [N.I.N.A 插件开发/插件仓库说明](https://nighttime-imaging.eu/docs/master/site/contributing/plugins/)
- [N.I.N.A 高级序列(Advanced Sequencer)文档](https://nighttime-imaging.eu/docs/master/site/advanced/advancedsequence/)
- [N.I.N.A Sequence 标签文档](https://nighttime-imaging.eu/docs/master/site/tabs/sequence/)
- [N.I.N.A 导星(PHD2/MetaGuide)文档](https://nighttime-imaging.eu/docs/develop/site/advanced/guiding/)
- [N.I.N.A 中文使用指南（星空π对）](https://pi.bestxtech.com/nina/)
- [ASCOM Platform 官网](https://www.ascom-standards.org/)

## 信息来源
- [N.I.N.A 官网/下载与更新说明](https://nighttime-imaging.eu/)
- [N.I.N.A 插件系统官方文档](https://nighttime-imaging.eu/docs/master/site/contributing/plugins/)
- [N.I.N.A 高级序列官方文档](https://nighttime-imaging.eu/docs/master/site/advanced/advancedsequence/)
- [N.I.N.A 导星官方文档(PHD2集成)](https://nighttime-imaging.eu/docs/develop/site/advanced/guiding/)
- [N.I.N.A 中文使用指南](https://pi.bestxtech.com/nina/)
- [N.I.N.A 3.0 版本更新日志示例](https://nighttime-imaging.eu/news/)


## 关联知识

- [拍摄序列计划](/03-拍摄SOP/拍摄序列计划.md)
- [PHD2 导星软件](/07-软件工具/PHD2%20导星软件.md)
- [采集控制平台选择与迁移](/07-软件工具/采集控制平台与迁移.md)
- [板解、翻转与任务恢复](/03-拍摄SOP/板解、翻转与任务恢复.md)

## 维护记录
| 日期 | 修改人 | 变更说明 |
|------|--------|----------|
| 2025-07-10 | 知识库助手 | 基于互联网调研初稿 |
