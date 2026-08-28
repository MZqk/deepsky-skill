---
type: "Method"
title: "智能望远镜导出数据的 Siril 工作流"
description: "以数据状态为门禁，说明 Seestar 专用 Siril 教程的适用范围、目录与后续处理边界，避免把通用校准流程错误套用于设备专用导出数据。"
category: "04-后期处理"
tags: ["Siril", "Seestar", "智能望远镜", "数据导出", "预处理", "校准边界", "可回退"]
difficulty: "新手"
audience: "已经从智能望远镜导出文件，想判断是否以及如何把其中的 Seestar 数据交给 Siril 的用户"
status: stable
created: "2026-08-26"
updated: "2026-08-27"
stale_after: "2026-11-27"
generated:
  by: process:official-source-raw-capture
  at: "2026-08-27T10:13:26+08:00"
review:
  state: needs-human-review
  owner: knowledge-base-maintainer
applies_to:
  系统: ["Seestar 导出的多张光帧，以及仅使用本页数据状态门禁的其他智能望远镜数据"]
  条件: ["Siril 版本、设备导出说明和实际文件状态均与页面来源一致，并使用独立工作副本"]
  设备:
    - "Seestar 导出的多张光帧：以 Siril 的 Seestar 专用教程和设备当前导出说明均能对应为前提"
    - "其他智能望远镜：仅可使用本页的数据状态门禁，不自动继承 Seestar 脚本或校准结论"
  软件与版本:
    - "Siril 1.4.0 或更高版本：专用教程在访问日如此说明；以已安装版本实际可见的脚本和文档为准"
  数据:
    - "未混入 JPEG、截图或二次导出的独立工作副本"
  不适用:
    - "厂商成图、已堆栈结果、格式/处理状态不明文件，或设备说明已要求其他专用流程的数据"
sources:
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
  - id: src-siril-seestar
    resource: "https://siril.org/tutorials/seestar/"
    title: "Siril 官方：Processing ZWO Seestar images"
    rights: unknown
    accessed_at: "2026-08-26"
    usage: link-only
    version: "教程写明脚本要求 Siril 1.4.0 或更高版本"
    evidence_level: primary
  - id: src-siril-script-files
    resource: "https://siril.readthedocs.io/en/latest/scripts/Script-files.html"
    title: "Siril 官方文档：Script Files"
    rights: unknown
    accessed_at: "2026-08-26"
    usage: link-only
    evidence_level: primary
  - id: src-seestar-file-transfer
    resource: "https://h5.seestar.com/course/295008?locale=zh-CN"
    title: "Seestar 学堂：使用 Wi-Fi 传输文件"
    rights: unknown
    accessed_at: "2026-08-26"
    usage: link-only
    evidence_level: primary

---

# 智能望远镜导出数据的 Siril 工作流

**摘要**：先判断导出物是厂商成图、已堆栈结果，还是一组可由 Seestar 专用教程处理的光帧。只有最后一种才进入本页脚本路线；该专用教程明确说明 Seestar 只提供光帧，不能把通用暗场/平场/偏置校准流程硬套进去。

> 证据层：Siril Seestar 教程与相关设备导出说明的可见文本已先保存在受限 raw 捕获集；公开版只保留本页的数据状态门禁、适用条件和官网链接。

## 第一道门：不要按文件名猜流程

| 数据状态 | 是否进入 Seestar 专用脚本 | 正确的下一步 | 禁止的假设 |
|---|---|---|---|
| 厂商成图、分享图或导出的展示图 | 否。 | 保留原件，在副本上做与现有状态相符的查看或非破坏性处理。 | 它可以回到逐张 Lights，或应重新跑预处理。 |
| 已堆栈/已处理结果 | 否。 | 留下处理状态证据，再决定是否只做后续图像处理。 | 再把它当作多张输入做校准和叠加会得到更科学的结果。 |
| 多张 Seestar 光帧，且设备导出说明与 Siril 专用教程能对应 | 是，先在工作副本中验证。 | 按本页的专用目录与脚本步骤执行。 | 所有型号、版本与导出文件都完全相同。 |
| 格式、数量、厂商处理状态或来源不明 | 否，暂停。 | 回到[智能望远镜：产品边界、文件导出与桌面后期前提](/02-器材百科/智能望远镜：产品边界、文件导出与桌面后期前提.md)补齐导出证据。 | 先转换格式、批量改名或塞进任何脚本再观察结果。 |

Siril 的[Seestar 专用教程](https://siril.org/tutorials/seestar/)说明其脚本处理 Seestar 的多张图像，并写明 Seestar 只给出 light images、因此不能在该教程的流程中进行校准；这是一项设备专用数据边界，不是“任何相机都不需要校准”的通用结论。[^src-siril-seestar]

## 第二道门：建立专用工作副本

1. 不在设备、手机或原始导出目录内运行脚本。先复制一份工作副本，并保存原始文件清单和会话记录。
2. 在项目根目录创建 lights 目录，只放经确认的 Seestar 光帧；不要把 JPEG、截图、已处理结果或不明文件混入。Siril 的专用教程要求以项目根目录和 lights 输入组织该路径。[^src-siril-seestar]
3. 选中项目根目录，不是 lights 目录。然后在已安装的 Siril 中确认当前版本是否提供教程所述的 Seestar_Preprocessing 脚本；教程将该脚本列为 Siril 1.4.0 及以上版本的入口。[^src-siril-seestar]
4. 运行前保存版本号、脚本名称、输入数量和开始时间。脚本完成不等于结果已正确；继续查看实际输出目录、输出文件和日志。

从 Seestar 到电脑的传输方式、文件位置与设备适用范围仍应以当前设备教程为准，例如[使用 Wi-Fi 传输文件](https://h5.seestar.com/course/295008?locale=zh-CN)。不要因 Siril 能打开一个文件，就反推该文件必然是专用教程所需的输入。[^src-seestar-file-transfer]

## 第三道门：后续处理只在结果状态清楚时进行

Siril 专用教程把裁切、背景处理、板解/色彩校准、拉伸和导出列在专用预处理之后；其中自动拉伸用于查看，不应覆盖线性数据或被当成最终处理。每个步骤仍以当前 Siril 界面、设备数据状态和保留的中间产物为准。[^src-siril-seestar]

建议的可回退顺序：

1. 保存脚本输出及其日志，先检查边缘、背景、星点和是否存在处理伪影。
2. 需要裁切或背景处理时，保存新的项目/文件，不覆盖脚本输出。
3. 做板解或色彩校准前，记录软件版本、目标/设备信息和采用的参数来源。
4. 拉伸只在可回到线性母版的副本上做；导出分享图与可编辑母版分开保存。

## 绝不从本页推出的结论

- Seestar 的专用“只用光帧、不可校准”边界，不适用于传统相机、其他智能设备或厂商明示提供不同校准路线的数据。
- 厂商文件可被 Siril 打开，不证明它未经设备处理、不证明可重新叠加，也不证明适合通用 OSC/DSO 脚本。
- 当前教程中出现的菜单、输出位置或脚本名称，不保证在任何未来 Siril 版本或任何产品导出中保持不变。

如果你手中是 DWARF 或其他智能设备的文件，先核对当前厂商导出说明；不要因为它同样叫“RAW”就使用 Seestar 脚本。

## 本次处理应交付什么

- 未改动的原始导出副本与文件清单；
- 项目根目录结构与 Lights 数量记录；
- Siril 版本、脚本名称、日志与脚本输出；
- 每一步的可编辑中间结果和关键参数；
- 线性母版（如有）与单独的分享版；
- 未确认问题、设备/App/固件版本和对应官方 URL。

## 权威问答口径

- 本页可承担的回答范围：Seestar 导出的多张光帧，以及仅使用本页数据状态门禁的其他智能望远镜数据；成立条件：Siril 版本、设备导出说明和实际文件状态均与页面来源一致，并使用独立工作副本。
- 遇到以下情况必须拒绝确定结论或转入专项页/实测：厂商成图、已堆栈结果、格式/处理状态不明文件，或设备说明已要求其他专用流程的数据。
- 具体数值、兼容性、软件行为或安全结论只使用正文就近绑定的来源，并同时受 `stale_after` 与人工 `verified.scope` 限制。


## 关联知识

- [Siril 新手首图工作流](/04-后期处理/Siril新手首图工作流.md)
- [校准与叠加](/04-后期处理/校准与叠加.md)
- [智能望远镜：产品边界、文件导出与桌面后期前提](/02-器材百科/智能望远镜：产品边界、文件导出与桌面后期前提.md)
- [数据管理、命名、备份与可复现归档](/03-拍摄SOP/数据管理、命名、备份与可复现归档.md)

## 维护记录

| 日期 | 修改人 | 变更说明 |
|---|---|---|
| 2026-08-26 | process:official-smart-telescope-ingest | 新建 Seestar 专用 Siril 分流页；明确不把该教程的校准边界扩展为通用相机流程。 |
| 2026-08-27 | process:official-source-raw-capture | 将支撑该页的 Siril 与设备官网文本先补入受限捕获集并更新来源链；专用校准边界维持待人工核验。 |
