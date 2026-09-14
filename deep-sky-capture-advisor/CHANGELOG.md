# Changelog

本文件记录 `deep-sky-capture-advisor` 的独立版本变更。

## [1.0.3] - 2026-09-10（小红书 RED skill 发布授权，范围路由修复）

- 修复范围路由误判：`scripts/query_knowledge.py` 的 `INTENT_ALIASES` 是字面子串匹配，领域词条没有动宾倒装形式（"拍深空"/"拍摄深空"），也没有独立的 "器材/设备/装备" 意图，采购意图只收 "购买"。实测 `第一次拍深空需要什么器材`、`拍深空需要什么器材`、`器材推荐`、`需要什么设备`、`我要拍深空，买什么` 此前一律被判 `out_of_scope` 并要求退出本技能，与 SKILL.md 声明的器材规划适用范围直接矛盾。
- 新增 "器材与装备" 意图，其覆盖由器材页自身元数据证明；"新手入门" 补 "第一次拍"；"预算采购" 补 "买""选购"。
- `SKILL.md` 第 1 步增加护栏：范围判定属字面匹配，明显落在适用边界内却被判 `out_of_scope` 且没有 `recommended_route` 的请求不得据此直接拒答，应按网络核验路径继续。
- `package_release.py` 的授权 scope 断言改为从 `LOCKED_RELEASE` 派生，避免版本号在多处硬编码漂移。
- 新增回归测试 `test_bare_gear_and_colloquial_first_contact_queries_stay_in_scope`；`manifest.runtime_files` 随本次运行时闭包变更刷新。
- 新发布授权 `xiaohongshu-red-skill:deep-sky-capture-advisor@1.0.3`：源提交 `e86c8f9`、catalog `035acb94…`、knowledge `d41164a6…` 不变；授权依据为 2026-09-10 用户显式指令，仅覆盖小红书 RED skill 渠道，不覆盖 SkillHub 与未来变更。
- 结构规范修正：`release-authorization.json` 移出运行时闭包（`knowledge_common.py` 新增 `EXCLUDED_RUNTIME_FILES`）与 `package_release.py` 发布白名单，文件保留在仓库供治理审计，授权校验仍从磁盘读取；SKILL.md、`query_knowledge.py` 路由值与 `agents/openai.yaml` 中的 `$skill` 伪语法统一改为自然语言/裸技能名；删除陈旧测试产物 `tests/trace-results-0.1.0.json`；`manifest.runtime_files` 相应刷新。
- 注意：`dist/` 中的 `deep-sky-capture-advisor-1.0.3.zip` 构建于上述修正之前，未随之重建；下次重建发布包时以上变更才会进入产物。

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
