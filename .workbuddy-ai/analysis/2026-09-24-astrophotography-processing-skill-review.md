# 外部技能评估：mickpletcher/AI-Skills · astrophotography-processing

评估日期：2026-09-24
评估对象：https://github.com/mickpletcher/AI-Skills/tree/main/claude/skills/astrophotography-processing
对照对象：本仓库 deepsky-skill（6 个 Skill）

---

## 一、结论

**整体借鉴价值：低—中。不建议引入该技能本体；建议吸收其中 3 项轻量交互模式与 1 项安全护栏。**

该技能在**广度**上与本项目高度重叠（同样覆盖深空 / 窄带 / 行星月面 / 银河 / 彗星 / 马赛克 / PixInsight / Siril），但在**深度**上整体低于本项目一个量级。它是一份**纯文档型 router 技能**：

- 全部内容约 40 KB，SKILL.md 仅 7 KB；
- `references/` 下 8 个文件，每个 0.9–2.1 KB，均为条目式清单，无参数化骨架；
- `scripts/` 仅 2 个约 3.5 KB 的只读小工具（FITS 头表、图像统计预览）；
- 无测试、无版本管理、无质量门禁、无真实性红线、无发布契约。

本项目同类能力已由 `deep-sky-processor`（20+ 脚本、adaptive 预设、星点引擎 v2、agent-in-the-loop 协议）、`starun-siril`（provenance/review/final-selection 三套 Schema）、`deep-sky-advisor`（measured/metadata/visual/assumed 四级证据分级）等以远高得多的严谨度覆盖。

因此：**能力层面不存在需要替换或升级的项，只存在少数值得移植的"表述与流程"层面的做法。**

---

## 二、逐项对照

| 能力项 | 外部技能 | 本项目现状 | 判断 |
|---|---|---|---|
| 深空后期流程编排 | 8 步文字序列，无参数 | `deep-sky-processor` 分阶段闭环 + 高风险阶段强制审查 | 已远超 |
| 校准帧匹配规则 | `calibration.md`，1.9 KB 清单 | `校准帧规范.md`、`physical_metadata.md`、`calibration` 决策流 | 已远超 |
| PixInsight / Siril 指引 | 各约 1.2–1.6 KB，通用顺序 | `pixinsight_workflow.md`、`siril_workflow.md`、`starun-siril` 手册冻结 Bundle | 已远超 |
| APP / DSS / Lightroom / GIMP / Affinity | `app-dss-photoshop.md` 2.4 KB，覆盖完整 | 分散在 `external_tools.md`、`后期软件对比.md`、`photoshop_workflow.md` | **本项目偏薄** |
| 彗星 / 银河 / 行星 | 3 个参考文件，各约 1 KB | `special_targets.md`（彗星双对齐、宽场、行星、月面、SNR） | 已远超 |
| 太阳观测安全护栏 | SKILL.md 有明确硬约束 | **全仓库无对应规则** | **真缺口** |
| 会话交互与输出结构 | 最少提问 + 模式标注 + 固定响应结构 | 各 Skill 独立表述，风格不统一 | **可借鉴** |
| 工程化 / 测试 / 审计 | 无 | semver + 契约校验 + 逐 Skill 测试 + 发布打包 | 本项目独有优势 |

---

## 三、值得吸收的 4 项（按价值排序）

### 1. 太阳观测安全护栏（唯一的能力型缺口）

外部技能在 SKILL.md 与 `planetary-lunar-solar.md` 中各有一处硬约束：

> 对太阳数据，不得给出暗示在没有前置太阳滤镜或专用太阳望远镜的情况下目视或拍摄太阳的拍摄建议。后期指导可以给出，但必须简短纠正不安全的拍摄假设。

本项目 `siril-moon-stacking` 覆盖月面，`special_targets.md` 覆盖行星与月面，但**没有任何一处涉及太阳拍摄的物理安全**。太阳是唯一会造成**不可逆人身伤害**（视网膜灼伤、设备烧毁）的天文题材，属于安全类硬约束，成本极低、收益明确。

建议落点：`deep-sky-capture-advisor/SKILL.md` 的排除清单 + 一张太阳专项知识页（若纳入范围）；至少在 `deep-sky-processor/references/special_targets.md` 增加一节安全前置说明。

### 2. "最少提问"策略（≤3 问 + 显式标注假设）

外部技能的 `Minimal Questions Policy` 规定：

- 关键上下文缺失时，**最多先问 3 个高价值问题**；
- 用户想要即时指导时，**带着合理假设继续，并显式标注假设**；
- 不要倾倒完整 intake 清单，除非用户主动要求。

本项目 `deep-sky-advisor` 已有"不因缺失可选信息而阻塞"的表述，但没有**提问数量上限**这一硬性约束。3 问上限能有效防止 agent 在启动阶段过度盘问。

建议落点：`deep-sky-advisor`、`deep-sky-capture-advisor`、`deep-sky-processor` 三处统一补一句上限约束。

### 3. 显式"操作模式标注"

外部技能要求每次响应先声明一个主模式：

`Diagnosis | Workflow | Recovery | Automation | Scientific preservation`

好处是让用户立刻知道本次回答的**性质**（是诊断、是流程、是补救、是自动化、还是科学保真优先）。本项目 `deep-sky-advisor` 有类似隐含区分（诊断 vs 建议），但未要求显式标注。

建议落点：作为可选输出头，加在 `deep-sky-advisor` 与 `starun-siril` 的最终回复结构中。注意与现有 `success/partial_success/review_required/failed` 状态体系**并存而不冲突**——状态描述执行结果，模式描述回答性质。

### 4. 响应结构中的"不要做什么"独立小节

外部技能的默认响应结构第 5 项固定为 **What not to do**，与"推荐路径"平级。

本项目 `deep-sky-advisor` 有 `Operations not currently recommended` 一节（已覆盖），但 `deep-sky-processor`、`starun-siril`、`siril-mosaic` 的最终回复结构中没有对应的反向清单。对后期类技能而言，"这一步不要做什么"往往比"要做什么"更能防止误操作。

建议落点：三个执行型 Skill 的输出模板各加一节。

---

## 四、不建议引入的部分

| 项 | 原因 |
|---|---|
| 技能本体 | 能力被现有 Skill 全面覆盖且更浅，引入会造成重复与路由歧义 |
| `when_to_use` frontmatter 字段 | 本仓库 `scripts/validate_repository.py` 的 `ALLOWED_FRONTMATTER_KEYS` 仅允许 `name/description/license/allowed-tools/metadata`，直接加入会导致契约校验失败 |
| `fits_audit.py` | 输出为扁平 TSV 表；`deep-sky-advisor` 的 `analyze_file.py` 已提供更丰富的统计与证据分级 |
| `image_stats_preview.py` | 依赖 Pillow 做逐通道统计与缩略图；本项目 `recognize.py` 的零裁切安全预览更严格 |
| `templates/processing-log.md` | 人工填写模板；本项目 `starun-siril` 的 provenance/review Schema 是机器可校验的更强形式 |
| `troubleshooting.md` | 17 条 artifact→cause 平铺清单；`ai_common_pitfalls.md` + `quality_assessment.md` 深度更高 |
| `upgrades.md` / `future-upgrades.md` | 其列出的候选改进（工具配方、窄带调色板示例、FITS 审计脚本、排障决策树、导出预设）在本项目中**已全部实现** |

---

## 五、一个可复用的观察

外部技能把"**广度覆盖**"当作技能的第一价值，本项目把"**深度 + 可审计性**"当作第一价值。两者是不同定位，不构成替代关系。

对本项目有参考意义的是它的**分发形态**：单文件 SKILL.md + 8 个小 reference，总 40 KB，可被任意 agent 直接读取。本项目的 `starun-siril` 达到 634 个文件（含 529 个 .rst 手册）。若未来需要面向轻量 agent 提供"入门级"入口，可以考虑从现有 Skill 中抽取一份**只读的薄 router**（复用现有 reference，不新增知识），作为分发层的补充，而非新的能力模块。

---

## 六、建议行动清单

| 优先级 | 行动 | 落点 | 工作量 |
|---|---|---|---|
| P1 | 补太阳观测安全护栏 | `special_targets.md` + capture-advisor 排除清单 | 小 |
| P2 | 统一"最多 3 问 + 标注假设" | 3 个顾问/执行 Skill 的 SKILL.md | 小 |
| P2 | 执行型 Skill 输出模板补"不要做什么" | processor / starun-siril / mosaic | 小 |
| P3 | 增补 APP / DSS / Lightroom / GIMP 覆盖深度 | 新建 `finishing_tools.md` 或扩写 `external_tools.md` | 中 |
| P3 | 评估是否需要薄 router 分发层 | 仓库根目录 | 中，需先确认分发需求 |

以上均为**增量补充**，不涉及对现有 Skill 的重构。
