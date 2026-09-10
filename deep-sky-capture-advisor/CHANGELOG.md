# Changelog

本文件记录 `deep-sky-capture-advisor` 的独立版本变更。

## [1.0.2] - 2026-09-10（小红书 RED skill 发布授权）

- 新发布授权 `xiaohongshu-red-skill:deep-sky-capture-advisor@1.0.2`：覆盖 StarunWiki 源提交 `e86c8f9`、catalog `035acb94…`、knowledge `d41164a6…` 的精确快照；授权依据为 2026-09-10 用户显式指令，仅覆盖小红书 RED skill 渠道，不覆盖 SkillHub 与未来变更。
- `02-器材百科/常见智能望远镜参数规格对照与使用边界.md`：Starun 规格表快照更新至 2026-09-10，覆盖 7 机型（新增 DWARFLAB Draco 预售）；记录 S50 Pro 官方规格二次复核（OS08B10 + IMX586）；新增“新机型未公开电性能字段不回填”停止条件；`stale_after` 延至 2026-12-10。
- 新增 `02-器材百科/智能望远镜传感器对比：IMX585与OS08B10证据分级.md`：由用户提供的 IMX585 × OS08B10 对比分析蒸馏，按官方/实测/推断三级证据组织；推断区间保留标注，主观评分未收录。
- `天文相机选型.md`、`智能望远镜：产品边界、文件导出与桌面后期前提.md`：关联知识补充上述新页链接。
- `SKILL.md` 版本升至 1.0.2，summary 与 manifest `distribution_notice` 改为渠道中性表述；`NOTICE.md` 渠道更新为小红书 RED skill。
- 构建注记：本机 macOS 26.6.2 的 sandbox-exec 已失效（`sandbox_apply: Operation not permitted`），本次构建以同一受 pins 约束的本地 Git 直跑替代 OS 隔离；后续对外发布仍建议按 RELEASING 在 Linux CI（Bubblewrap）重验。

## [1.0.1] - 2026-08-30

- 将当前受控运行时闭包作为 SkillHub 补丁版本发布，并保持来源提交、目录与知识树哈希不变。
- 更新公开主页并绑定独立 Skill 图标；继续标记为非权威测试版。

## [1.0.0] - 2026-08-28

- 首次发布到 SkillHub。

## [0.1.0] - 2026-08-28

- 建立内置可追溯知识快照、范围路由和精确发布授权基线。
- 登记独立开发环境和版本化发布流程；本记录不扩大现有发布授权。
- 加固维护构建器：强制精确来源 remote、commit 与正式页树哈希，增加总预算、Git 超时和输出/内容体积硬上限，使用最小环境并要求 macOS/Linux OS 级隔离；兼容且严格区分历史/当前源布局，补齐首次安装事务恢复。
