---
type: "Software Guide"
title: "PHD2 导星软件"
description: "PHD2（Open PHD Guiding）是免费开源的导星软件，负责锁定一颗（或多颗）导星并向赤道仪发送修正指令以消除跟踪误差。"
category: "07-软件工具"
tags: ["软件", "PHD2", "导星", "赤道仪", "周期误差", "NINA"]
difficulty: "进阶"
audience: "使用导星相机+导星镜/主镜离轴导星、需要提高跟踪精度（压低 RA/Dec 漂移与周期误差）的深空摄影者；通常与 N.I.N.A、SGP、APT 等拍摄软件联动。"
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
  系统: ["PHD2 当前稳定版连接兼容导星相机和赤道仪的导星系统"]
  条件: ["配置向导、标定、Guiding Assistant 和日志分析均针对当前设备与天空完成"]
  不适用: ["跨设备固定算法、曝光或 RMS 目标", "仅凭截图替代日志和主相机验收"]
sources:
  - id: raw-research
    resource: "/raw/素材调研报告.md"
    title: "深空天文拍摄知识库素材调研报告"
    evidence_level: internal-ledger
    rights: unknown
    usage: metadata-only
    accessed_at: "2026-08-27"
  - id: src-56468e00
    resource: "https://openphdguiding.org/"
    title: "Open PHD Guiding 官网"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-63fdabf7
    resource: "https://openphdguiding.org/PHD2_User_Guide.pdf"
    title: "PHD2 用户指南(含 Multi-star 说明)"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-96cc784c
    resource: "https://openphdguiding.org/man-dev/Guide_algorithms.htm"
    title: "PHD2 Guide Algorithms 官方文档"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-91b0ee48
    resource: "https://adgsoftware.com/phd2/archive/man-2.5.0dev8/Trouble_shooting.htm"
    title: "PHD2 故障排查文档(calibration step-size)"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-48cec40f
    resource: "https://nighttime-imaging.eu/docs/develop/site/advanced/guiding/"
    title: "N.I.N.A 导星(PHD2)官方集成文档"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-1eb81c43
    resource: "https://www.astrobin.com/forum/post/166538/"
    title: "AstroBin 论坛 Star Lost 实际案例"
    evidence_level: experience
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-phd2-basic-use
    resource: "https://openphdguiding.org/man/Basic_use.htm"
    title: "PHD2 官方文档：Basic Use"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"
  - id: src-phd2-advanced-settings
    resource: "https://openphdguiding.org/manual/"
    title: "PHD2 官方用户手册：Advanced Settings"
    evidence_level: primary
    rights: unknown
    usage: link-only
    accessed_at: "2026-08-27"

---
# PHD2 导星软件

**摘要**：PHD2（Open PHD Guiding）是免费开源的导星软件，负责锁定一颗（或多颗）导星并向赤道仪发送修正指令以减小跟踪误差。它的关键在于正确的设备 Profile、有效校准、合适的导星星与可解释的日志；算法、曝光和回差设置须由设备行为验证，不存在只靠“开 PPEC”或固定 RMS 即可解决的通用配方。

## 背景 / 适用场景
适用场景：任何需要导星的深空长曝光；支持 ASCOM（Windows）与 INDI（Linux/树莓派）设备。前置条件：导星相机（如 ASI 120MM 系列等）+ 导星镜或主镜 OAG；赤道仪已做极轴对齐与基础平衡；导星软件需与拍摄软件处于同一台电脑或通过网络（例如 N.I.N.A 用 External Guider/PHD2 的 ASCOM 服务器）连接。版本：稳定版约 2.6.x（开发版可见 2.6.13dev 系列）。

## 核心知识点
- 导星算法（Guide Algorithms）：RA 与 Dec 都有多种算法和参数选择；从 Profile Wizard、Guiding Assistant 和稳定运行日志的建议开始。Predictive PEC、Hysteresis、Resist Switch 与 Lowpass 各有适用条件，不应为所有赤道仪预设同一算法。
- 星点搜索/匹配：Multi-star（多星）算法利用辅助星点细化导星星移动、降低抖动，官方称‘几乎不会使导星变差’；另有 Auto、Static、Dynamic 等模式。
- 校准（Calibration）：PHD2 会在 RA/Dec 两个方向移动导星星，以测量尺度、正交性与回差。首次建立 Profile、导星相机旋转、焦距/硬件/连接方式改变或校准图形异常时应重校；若通过 ASCOM/INDI/Aux Mount 获得可靠指向信息，可复用并自动变换校准数据，换目标或翻转不必当然重校。
- PPEC / Periodic Error Correction：PPEC 是可选的导星算法/策略之一，应在 Guiding Assistant 和长时日志显示周期性误差时评估；它不替代机械状态、极轴、线缆与校准检查。
- 关键状态指标：RMS（总导星误差，角秒）、RA/Dec 误差曲线、曝光时间、SNR 和校准图。RMS 用来观察趋势和定位问题；最终应以主相机星点、成像采样、视宁度和拒片率判断是否达标。
- 与 N.I.N.A 联动：N.I.N.A 的 Guiding 设 PHD2（External Guider/Phd2 服务器），N.I.N.A 可在序列中自动 Start/Stop 导星、读取 RMS 并在掉星时告警；亦可让 N.I.N.A 做居中后把导星交给 PHD2。
- 曝光与信噪比：从能稳定检测导星星的几秒曝光起步，再结合 SNR、视宁度和误差曲线调整；弱星环境可加长曝光、改善对焦或换更亮导星。

## 权威问答口径

- PHD2 应为不同设备组合建立独立配置，并按官方 Basic Use 完成连接、选星、校准和导星；不能仅凭一个 RMS 数字判断整套主相机曝光是否合格。[^src-phd2-basic-use][^src-63fdabf7]
- 高级参数要在明确故障证据后调整；焦距、像元、binning 和校准条件错误会影响下游参数与角秒报告，优先使用新配置向导和官方说明。[^src-phd2-advanced-settings]

## 注意事项
- Star Lost – Low SNR：多因导星太暗/曝光过短/焦距太长导致星点 SNR 不足；解决——选更亮导星、加长曝光、检查导星镜/主镜焦点与跟踪是否稳定。
- 校准失败/走形：常因校准步长(calibration step-size)不匹配或大风吹动；可在 Advanced Settings > Guiding 调整 calibration step-size，或重选画面中心附近的导星重新校准。
- 周期误差（Periodic Error）过大：先检查机械、极轴、平衡和线缆，再基于长时日志评估 PPEC 或其他算法；不要仅靠切换算法硬扛异常周期误差。
- Dec 轴选错算法：给 Dec 用 RA 型低阻尼算法易产生‘锯齿/过冲’，应改用 Resist Switch/Lowpass 类。
- 导星与拍摄软件连接管理：避免在 N.I.N.A 与 PHD2 中重复 Start Guiding，统一由拍摄序列控制启停。
- 更换焦距/减速比/导星镜后必须重新校准，否则修正方向/标度错误会越导越偏。

## 示例
- 起始设置：使用 Setup Wizard 建 Profile，启用 Multi-star；运行 Calibration Assistant 和 Guiding Assistant，按建议生成起始参数；之后以主相机测试帧和日志决定是否调整算法、min move、回差或曝光。
- 掉星排查流程：PHD2 报 Star Lost → 看 SNR 与曲线 → 若 SNR 低则加曝光/换亮星 → 若校准老失败则重选中心导星并校准 → 确认极轴与平衡无问题后续拍。
- N.I.N.A 联动：N.I.N.A 的 Guiding 选择 PHD2，序列里放‘Start Guiding’；居中(Center After Slew)完成后 PHD2 接管导星，N.I.N.A 实时显示 RMS 并在异常时暂停。

## 相关资源
- [Open PHD Guiding 官网](https://openphdguiding.org/)
- [PHD2 用户指南(PDF)](https://openphdguiding.org/PHD2_User_Guide.pdf)
- [PHD2 导星算法(Guide Algorithms)文档](https://openphdguiding.org/man-dev/Guide_algorithms.htm)
- [PHD2 GitHub Releases](https://github.com/OpenPHDGuiding/phd2/releases)
- [PHD2 故障排查(归档文档)](https://adgsoftware.com/phd2/archive/man-2.5.0dev8/Trouble_shooting.htm)
- [AstroBin PHD2 导星讨论区](https://www.astrobin.com/forum/c/equipment-forums/open-phd-guiding-project-phd2/)
- [PHD2 官方：Basic Use](https://openphdguiding.org/man/Basic_use.htm)
- [PHD2 官方：Advanced Settings](https://openphdguiding.org/man/Advanced_settings.htm)

## 信息来源
- [Open PHD Guiding 官网](https://openphdguiding.org/)
- [PHD2 用户指南(含 Multi-star 说明)](https://openphdguiding.org/PHD2_User_Guide.pdf)
- [PHD2 Guide Algorithms 官方文档](https://openphdguiding.org/man-dev/Guide_algorithms.htm)
- [PHD2 故障排查文档(calibration step-size)](https://adgsoftware.com/phd2/archive/man-2.5.0dev8/Trouble_shooting.htm)
- [N.I.N.A 导星(PHD2)官方集成文档](https://nighttime-imaging.eu/docs/develop/site/advanced/guiding/)
- [AstroBin 论坛 Star Lost 实际案例](https://www.astrobin.com/forum/post/166538/)
- [PHD2 官方文档：Basic Use](https://openphdguiding.org/man/Basic_use.htm)
- [PHD2 官方文档：Advanced Settings](https://openphdguiding.org/man/Advanced_settings.htm)


## 关联知识

- [导星](/03-拍摄SOP/导星.md)
- [N.I.N.A 使用要点](/07-软件工具/N.I.N.A%20使用要点.md)

## 维护记录
| 日期 | 修改人 | 变更说明 |
|------|--------|----------|
| 2026-07-30 | Codex | 修正校准复用、RMS 与算法的固定化表述，改为依 Profile、连接方式、日志和主相机结果验收。 |
| 2025-07-10 | 知识库助手 | 基于互联网调研初稿 |
