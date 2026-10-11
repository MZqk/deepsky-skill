# Changelog

本文件记录 `siril-astra-stacking` 的独立版本变更。

## [1.4.2] - 2026-10-11

- **修复 `--calibration-dir` 相对路径支持**：此前 `--calibration-dir` 若传入相对路径（如文档示例中的 `--calibration-dir ./masters`），会直接将相对路径写入 Siril 脚本，而 Siril 在执行时工作目录切换至 `<target>.work`（`-d` 参数），导致其在 `work` 目录下查找母帧失败报错。现于 CLI 入口、`resolve_calibration_plan`、`_pick_master` 以及 `derive_calibration` 四层强制转换为绝对路径；`selftest.py` 自检新增 11 项断言覆盖相对路径解析与脚本生成，自检增至 825 项。

## [1.4.1] - 2026-10-11

- **多滤镜精简交付包（`--bundle`）与预览图自动归集**：`prepare_composition.py` 支持 `--bundle` 参数，将输出直接精炼为四项核心资产：根目录单通道叠加图（`<FILTER>.fit`，Siril 图像合成直选即用）、预览图（`previews/`）、诊断与对齐清单（`diagnostics/`）及使用说明（`README.md`）。彻底摒弃冗余的中间大母版归档，节约磁盘并杜绝 3 图层母版误拖入 Siril 导致的 `Invalid file type for compositing` 报错。
- `prepare_composition.py` 默认自动归集各滤镜报告中的 `artifacts.preview` 到输出目录的 `previews/`，方便合成前一站式肉眼审查全部通道画质。
- 新增 `test_collect_inputs_captures_preview` 与 `test_bundle_structure_publishing` 单元测试。
- Add a standard-library composition preparation entry point for published monochrome filter stacks, preserving the original masters and reports.
- Convert same-filter RGB drizzle planes to one plane, align filter masters to a common minimum canvas, and publish a separate validated bundle with provenance and quality warnings.
- Add focused unit and real Siril tests for conversion, metadata preservation, registration, identity transforms, and failure isolation.
- **新增 mono（单通道）链路**：profile 含新增布尔键 `mono_sensor_capable` 的设备，在几何未触发 drizzle 时走第三条链路——`calibrate -cfa`（无 `-debayer`）→ `register -2pass` → `seqapplyreg -framing=current` → `stack`，母版为 `NAXIS=2` 且 `BAYERPAT` 保留。**Siril 1.4.4 没有 `-mono` 参数**（已对二进制核验其 `stack` 用法串），mono 就是「不出现 `-debayer`」本身，因此与 drizzle 互斥（`seqapplyreg -drizzle` 内部自行去拜耳）。mono 仅从 debayer 分支可达。
- **⚠️ 行为变更**：当前只有 `zwo_asiair_dslr` 标记了该键。ASIAIR + 单反的既有用户会发现**原本三通道的窄带桶变为单通道**（NGC 6946 六桶中 G/H/S 几何安静，原走 debayer，现走 mono；L/R/B 因span/旋转触发 drizzle 不变）。退回方法：`--device-profile` 提供 JSON 覆盖 `{"zwo_asiair_dslr": {"mono_sensor_capable": false}}`。报告中`registration.mono_requested` 与 `registration.reasons` 必含机器可读的判定依据。
- 新增 `--force-chain mono`：几何触发 drizzle 时 mono 本不可达，报告会给出原因并建议此开关。**该开关完全绕过阈值判定**——否则在触发 drizzle 的数据上会变成静默无效的空操作，使那条建议变成假话。彩色传感器上强制 mono 会被拒（退回 debayer 并说明理由），因为去掉去拜耳等于丢弃唯一的颜色信息。
- **修 `decide_chain` 的 force 早返回**：原 reasons 文案硬编码 `"--drizzle"` / `"--no-drizzle"`，而这两个 flag 早已不存在（现为 `--force-chain`）；已改为按实际 flag 名输出。
- **修 mono 会触发的两处联动**（原实现只在 debayer 分支生效，mono 会静默分叉）：①`astra_stack.py` 的 `_reference_filter_fallback` 触发条件写死 `chain == "debayer"`，而 mono 同样用 `-framing=current`、会遇到完全相同的 `Reference image is not included in the filtered list` 失败——不改则 mono 丢失唯一一次重试兜底；②`_chain_disagreement_notes` 的早返回未含 mono，会在无 flat 时产出「automatic chain 'mono' differs from 'drizzle'」的假矛盾note（预探针把 `initial_chain` 设为 drizzle，最终却是 mono）。
- **mono 与 PWS、--rgb-equal 互斥**（退出码 5）：PWS 的灰度转换与分区权重只在去拜耳 RGB 上验证过；单通道母版没有 RGB 背景可均衡。PWS 拒绝在此显式插入而非交给 `evaluate_gate`——其 cfa 分支会把原因说成「drizzle 画布尺寸不一」，而 mono 用 `-framing=current`、画布一致，那是**报告撒谎**。
- schema `1.2` → **`1.3`**：新增 `stacking.output_naxis`（`2` 或 `3`，消费方无需打开 FITS 即知平面数）与 `registration.mono_requested`；`channel_stats.medians_after` 在 mono 下为单键 `{"m": 值}` 而非三个 RGB 键。
- `fits_probe.estimate_channel_medians` 新增 `NAXIS=2` 分支：此前对单通道母版返回空dict，导致预览中点退回硬编码 0.028。采样循环抽为 `_sample_planes`。同步给 `derive_preview_mtf` 加 `mono` 形参——单值走 `min([m])*2.0` 数值恰好正确，但 `derived_from` 文案写「minimum whole-image channel median」对单通道是误导。
- 修正 `references/06`：新增 §6.9.2 记录 `stack` 的完整用法串与「无 `-mono`」这一实测结论（文档此前未载）；原 §6.9.1 末尾的覆盖缺口说明拆分，注明 mono 走 `-framing=current` 因而不受合成夹具的 drizzle 限制。
- 同步订正文档中「两条 CFA 链路」与「两条链路输出都是三通道 RGB」的表述：SKILL.md 链路图与标题、`references/03` §3.1（表格加MONO 列，并指出 mono 让「drizzle 保持 CFA」这句话第一次字面成立）、`references/05` §5.2（`NAXIS` 行标注 mono 为 `2`；「PWS 的例外」改为「三种单通道来源」对照表）、`references/01` profile 字段表、`references/04` `-maximize` 章节、`references/07` 退出码表。
- 自检 757 → **814 项**（含 7 项合成准备用例）。新增 `test_mono_chain` / `test_mono_chain_conflicts` / `test_mono_report_contract` / `test_mono_pipeline_end_to_end`（用新增的 `synth_fits.DSLR_HEADER_FIELDS` 夹具走真实设备识别 + 真实 `build_script`，断言生成的 `.sir` 文本里没有 `-debayer`/`-equalize_cfa`/`-drizzle`/`-maximize`），并扩展 `test_chain_demosaic_placement`（三元组）、`test_channel_median_estimation`（NAXIS=2 采样）、`test_chain_review`。**全部先写红灯验证**：13 条断言在实现前精确失败。`test_pipeline_branches`、`test_preprocessing_reuse` 的 33 个既有 case 未受影响（Seestar profile 未加新键，这也是刻意不做的「图方便」）。

## [1.3.3] - 2026-10-10

- **修复 1.3.1 遗漏的缺陷：`report_path` 字段从未真正落盘**（v1.3.2 重跑 L桶真实验收时发现）。1.3.1 只在 `_emit_final` 里给 payload 加了该字段，但 commit 路径的快照由 `_commit_and_publish` 直接调`write_report` 写出、**绕过 `_emit_final`**，因此正常成功时靠末尾那次幂等重写"顺带"补上——一旦进程在 commit 期间被杀（正是 1.3.1 要解决的场景），快照里就没有这个字段。真实数据证据：L 桶（17:35重跑）`report_path` 为 `None`，而 15:25–15:37 那批六桶报告同样缺失。现于 `_commit_and_publish` 内直接写入发布路径（指向 out_dir 的目标文件，而非 work 目录里的临时副本）。
- 自检新增 19 项 `report_path` 断言（覆盖全部成功 case + dry-run + `commit-interrupted`）。**已验证红灯**：回退修复后 `commit-interrupted` 那条精确失败（值 `None`），其余 18 条仍绿——因为正常路径末尾的 `_emit_final` 会补上，**只有 commit 期间被杀时才暴露**，与缺陷的实际表现完全一致。自检 738 → 757 项。
- 真实验收（v1.3.2→1.3.3）：**六桶全部重跑并全部合格**（L 62/69 帧 drizzle 链路、R 16/18、B 17/19 drizzle 链路，G 16/18、H 16/18、S 2/3 debayer 链路），每份报告均 `status=ok` + 8 项 `completed_stages` + 3 条 `artifacts`（路径全部存在）+ `timings.total_s` + `report_path`。此前 15:25–15:37 那批六桶报告全部缺 `report_path`。
- **订正 `references/06`：`FITS error: failed to create new file (already exists?)` 不等于磁盘满**。实测在磁盘空闲 65 GiB 时同样出现，且目标文件已存在且大小正常——真实含义是Siril 无法创建输出文件（同名已存在或写入被宿主策略/沙箱拦截）。此前本项目把它误判为磁盘耗尽两次。文档补充了排查顺序（先 `df`、再看 `ls`、再看是否受限宿主），以及「中途失败会留下半成品序列，文件都在≠成功，须以 `failed_stage` 为准，重跑前必须清 work 目录」。
- **已知局限（受限宿主）**：沙箱/文件策略拦截 `os.replace` 时，`write_report` 的临时文件无法清理，每次失败在输出目录残留一个 `.<target>_report.json.*.tmp`（内容是完整合法 JSON，仅差 rename 一步）。`_emit_final` 会照常打印带异常类型名的 stderr 警告（v1.3.1 加固），报告本身保持上一份完好状态——数据不会损坏，但残留需手动清理。批量运行受限宿主时值得预先留意。

## [1.3.2] - 2026-10-10

- **订正文档与实现不符：drizzle 链路并非"保持 CFA"，两条链路的产物都是三通道 RGB**（1.4.4 逐阶段实测，现象先由NGC 6946 六通道实跑发现、再逐步加脚本定位）。原文档写「DRIZZLE 链路：保持 CFA」，实测：输入 `NAXIS=2 BAYERPAT=RGGB` → `calibrate -cfa` 后**仍是 `NAXIS=2`** → **`seqapplyreg -drizzle` 后变`NAXIS=3`、`BAYERPAT` 消失**；对照 `seqapplyreg -framing=current`（无 `-drizzle`）则保持单通道。即转换点是 `seqapplyreg -drizzle`，不是 `calibrate`。`references/05` 原有的 `NAXIS=3（RGB），即使输入是 CFA` 本就正确，未改。
- **新发现第三条二进制消息（文档此前未载）**：对已 `-debayer` 的 RGB 序列使用 drizzle，被拒信息为 `This sequence is not mono / CFA, cannot drizzle.`（此前只记录了 `Cannot use drizzle on non-bayer sensors, aborting.`）。这同时给出了「drizzle 链路不能在 `calibrate` 阶段预先 `-debayer`」的确切技术原因。已写入 `references/06` §6.9 与 `references/03` §3.1.1。
- 同步订正 `references/03-registration.md` §3.1（新增 §3.1.1 逐阶段通道对照表）、`scripts/param_derive.py` 模块说明、`scripts/astra_stack.py:1054` 行内注释（原文 "Keep CFA until the measured chain is known" 容易被读成最终产物格式）。
- 自检新增 `test_chain_demosaic_placement`（6 项断言）：守住「`-debayer` 只出现在 debayer 链路的校准步骤」这一不变量。**已按纪律验证红灯**——向 `param_derive.derive_calibration` 注入「drizzle 也提前去拜耳」的 bug 后，该测试 2 条断言精确失败且debayer 侧不受影响。自检 732 → 738 项。
- **记录覆盖缺口**：`seqapplyreg -drizzle` 在自检的合成 CFA 夹具（3 帧 64×48，`BAYERPAT=GRBG`）上报 `Error in line 3 ('seqapplyreg'): invalid arguments.`，真实数据可用——drizzle 对序列另有隐含要求（帧数/尺寸/模式）。故 drizzle 的**脚本生成**有断言覆盖，**实际执行**只有真实数据验证过。已写入 `references/06` §6.9.1 与 SKILL.md 已知局限。
- 补记真实数据基线：单反 drizzle 链路 145帧 NGC 6946 分桶六通道实跑（L 62/69 帧、196 秒），此前已知局限中只记了 130/145 的单滤镜历史基线。

## [1.3.1] - 2026-10-10

- 修复成功报告在进程于commit 窗口内终止时退化为残缺态的缺陷：报告改为在原子发布**之前**写成终态（`artifacts` 由新增的 `_published_paths` 预计算、`completed_stages` 含 `commit`、`timings.total_s` 一次算定），随三件套一起 rename 发布；发布后的 `_emit_final` 退化为同内容幂等重写。背景：NGC 6946 六通道实跑中 L/G/B/H 四份报告只有 7 项 `completed_stages`、`artifacts` 为空、`timings` 为空，而三件套其实已全部发布成功（进程在 scratch 清理被外部删守护拦截时提前终止）。Siril 与 PWS 两条commit 块此前是复制粘贴的同构实现，现统一为 `_commit_and_publish`，不再分叉。
- 新增输出契约：stderr 每阶段一行进度 + 末行结论（含退出码与报告路径），`--quiet` 只抑制进度行、结论与警告始终输出；`--dry-run` 按 help 承诺把 `.sir` 脚本正文写到 stdout（此前只塞进 report，从不打印）。此前全部 31 条退出路径的 stdout/stderr 均为 0 字节，调用方只能靠翻 JSON 判断原因。结论行由 `main()` 打印而非 `_emit_final`，因此任何新增分支都无法漏打。
- `write_report` 改为「临时文件 + `os.replace`」原子写，消除序列化中途崩溃留下截断非法 JSON 的窗口（commit 阶段两处调用此前无任何异常保护）。临时文件固定建在目标同目录，跨文件系统时 `os.replace` 会失败。不引入 fsync：本技能的持久化契约围绕 `os.rename` 发布产物，报告的掉电持久化不在契约内。
- `main` 的崩溃兜底报告改为复用 `write_report`，且当输出目录已存在 `status: "ok"` 的完整报告时改写 `<target>_report.internal.json`，不再用七字段的 internal 报告覆盖完整报告——此前直接违反「失败不污染产物」铁律。
- `--dry-run` 有意保留 work 目录（那份 `.sir` 是其唯一可执行产物），并在报告写入 `work_dir` 字段与说明 note——此前残留目录在报告中无任何指针，实测 `/tmp/astra-dry` 六个目录全部残留且不可发现。
- 统一产物命名规则为单一真源 `_artifact_final_name`，`atomic_publish` 与新增的 `_published_paths` 共用，消除「报告记录的路径与实际发布路径可能不一致」这一新风险面。
- 补 `report.report_path` 字段（此前在写盘**之后**才赋值，永不落盘）；报告写盘失败不再静默 `pass`，改为 stderr 警告。
- 订正文档与实现不符处：`references/07` §7.3 原写「任一环节抛错 → 已发布的文件保持不变」，实测 `atomic_publish` 是先 `os.remove` 再**逐项** rename、中途失败**不回滚**，本次下调该承诺的表述边界（不是新引入的缺陷，是原有描述过强）。
- 自检从 698 项增至 726 项。新增：mock 层覆盖全部 case 的报告完整性断言（`artifacts` 三条路径存在且归属输出目录、`completed_stages` 末位为 `commit`、`timings.total_s` 为 float）、模拟 commit 期间被杀仍须发布完整报告的 `commit-interrupted` case、dry-run 的 `work_dir` 指针与「只发布不写产物」语义、stdout/stderr 分工契约与 `--quiet` 语义、报告原子写（模拟序列化中途抛异常，断言目标文件未被破坏且无临时文件残留）、`_published_paths` 与 `atomic_publish` 一致性。此前 `test_pipeline_branches` 跑遍全部分支却从不断言 `artifacts`，正是缺陷逃逸的原因。
- 订正版本号不一致：本 skill 的 frontmatter 此前停留在 `1.2.4`，而 CHANGELOG 无 `[1.2.4]` 段落、`skills.manifest.json` 仍为 `1.2.3`，仓库校验因此长期报两条错（`validate_repository.py` 会因 manifest 与 SKILL.md 不一致、CHANGELOG 未记录当前版本而失败）。本次统一为 1.3.1，两条错误同时消除；不补空的 `[1.2.4]` 段——那会掩盖「1.2.4 到底改了什么」这个已不可考的问题。
- **真实验收发现并修复：策略型宿主拒绝文件操作时抛的不是 `OSError`**。`_emit_final` / `_write_internal_report` / `_report_is_complete` / `write_report` 的清理分支原先只捕 `OSError`，被沙箱或文件策略代理以自有异常类型拒绝时（实测 `Brokered host rename source refused by file policy`），异常会穿透并**掩盖运行本身的真实结局**——报告写不进去时调用方既拿不到报告也读不到原因。改为捕全部异常并降级为 stderr 警告，警告文本带上异常类型名。自检新增 6 项断言（模拟非 `OSError` 拒绝，验证三条路径均降级为警告且不静默）。
- 实测确认：NGC 6946 六通道在 1.3.1 下重跑，G/H/S/R/B 五桶报告均为 8 项 `completed_stages` + 3 条 `artifacts`（修复前 L/G/B/H 为 7 项 + 0 条），即commit 窗口缺陷已消除。
- 已知局限（本次不修）：仍无 SIGTERM/SIGINT 处理，SIGKILL 落在 commit 之前的阶段时，报告停留在上一阶段快照、work 目录保留（符合既有语义）。commit 预写已消除这一局限在产物层面的可见症状。全局 `try/finally` 重构会侵入全部退出路径，收益低于风险。
- 已知局限（本次不修）：`atomic_publish` 中途失败不回滚已 rename 的产物。需要二阶段发布（先 rename 到临时名、全部成功后再改名），与本次「报告必须先写好」的方向冲突，需单独设计。

## [1.3.0] - 2026-10-10

- 新增 `zwo_asiair_dslr` 设备签名（ASIAIR + PTP 单反，如 Nikon D5100，赤道仪）：`CCD-TEMP`/`GAIN` 双缺是单反结构性特征，经 profile `calibration_optional_keys` 声明，母帧匹配在 light 与母帧两侧均缺失时跳过该键，任一侧存在仍正常比较；跳过写入 warning。ASIAIR + ZWO 相机（有 GAIN/CCD-TEMP）不含在内，仍按拒绝处理直至扩展表。
- 校准匹配按母帧类型分键集：flat/bias 不再比较 `EXPTIME`（平场/偏置永远按自身曝光拍，与 light 曝光比较会拒绝所有正确母帧）；flat 增加 `FILTER` 一致性检查（双方都有 FILTER 时必须相等，母帧缺 FILTER 跳过检查）。dark 匹配保持全键不变；智能望远镜走 `dark_embedded` 分支不受影响。
- 修复 `-cc=dark` sigma 生成错误：手册声称 `-cc=dark [siglo] sighi`，实测 1.4.4 CLI `-cc=dark 3` 报 `Unknown parameter 3`（日志 "sigma 3.00" 是内置默认值输出）、`-cc=dark=3` 与整体加引号亦被拒，此前生成脚本在传统设备上必然失败。现只输出裸 `-cc=dark`（等价计划默认 hot sigma 3 / cold off）；自定义 sigma 生成期拒绝（`ValueError`），不静默降级。此路径此前从未被真实数据覆盖（智能望远镜无 master dark）。
- 修正 drizzle note 硬编码「smart scopes produce no flats」为设备无关措辞：`seqapplyreg` 不带 `-flat=` 与校准平场无关。
- 新增滤镜混放守卫：probe 阶段逐帧读取 FILTER 分组，输入目录出现多种滤镜时拒绝（复用退出码 6）并在报错中列出各滤镜帧数与分桶指引。背景：NGC 6946 数据实为 L/R/G/B/Hα/SII 六滤镜混放，此前静默混叠 145 帧且第一帧的 B 平场套用于全部帧。文档同步 07 退出码表与 SKILL.md 铁律（三条→四条）。
- 验收：635 项自检通过（新增混合滤镜拒绝用例：改写单帧 FILTER 后 dry-run 断言退出码、报错含两种滤镜名、无母版发布）；真实混放目录复验 EXIT=6 分组计数 L69/R18/G18/B19/H18/S3 与实际一致。
- 验收：631 项自检通过（含单反匹配分支、cc 形式与自定义 sigma 拒绝）。145 帧 Nikon D5100 NGC 6946（B 滤镜 300s，EQMod）端到端真实出片：bias 15 帧、B-flat 10 帧经 Siril 合成 master，PixInsight XISF master dark（头缺 `EXPTIME`，用户确认 300s 后注入并附 HISTORY）转 FITS，三母帧匹配成功，drizzle 链路 130/145 帧（10.8 小时）发布三件套。

## [1.2.3] - 2026-10-09

- 保留 `calibration.cc.enabled` 的计划语义，在既有 notes 中按实际脚本/校准序列记录跳过、计划、成功完成和失败/完成未确认；无 dark 时说明未请求 Siril 校正及设备内部处理未确认。不新增字段，schema 1.2 保持不变。
- 补齐所有 `-cc=dark` 形式的 master dark 守卫，冷像元阈值（包括 0）不能再绕过检查。合法参数和 M8 默认处理保持原样；未新增坏点处理算法。
- 验收：637 项自检通过，覆盖校准/重校准失败、超时、缺失产物、后续失败保留状态、dry-run、仅去拜耳、复用和参考帧回退。120 组合合法脚本和 M8 默认脚本逐字一致，24 组无 dark 冷阈值异常组合仅移除校正参数；M8 计划/脚本/日志回放说明本次未请求校正及设备内部处理未确认。未启动真实处理，详见[验证记录](../reports/2026-10-09-坏点校正计划与执行状态修复.md)。

## [1.2.2] - 2026-10-09

- 修正 weighted FWHM 分散度汇总从未排序列表读取百分位的缺陷，直接复用单项表已排序的 p10、p90，避免采集顺序改变比值及提示。
- 保留四位小数和未舍入的 1.4 提示阈值；sigma、样本纳入规则及零分母守卫不变。PWS 闸门本身已排序，无需修改；CLI、报告 schema 1.2 和叠加处理不变。
- 验收：472 项自检通过，覆盖顺序无关性、未舍入的 1.4 阈值、缺失值和零分母。M8 注册数据回放比值由 1.7286 修正为 2.2694，其余质量统计不变；48 组生成脚本及 M8 实际脚本逐字一致，PWS 闸门文件未改。未启动真实叠加，详见[验证记录](../reports/2026-10-09-weighted-FWHM分散度报告修复.md)。

## [1.2.1] - 2026-10-09

- 修正自动链路复核的数据来源：改用变换前、最终数据格式的已验证注册摘要，复用 `decide_chain` 的 mosaic、旋转和尺度判据，消除 drizzle 被变换后零旋转误判为没必要的提示。
- 显式指定链路不输出矛盾提示；自动选择与最终推荐不同时记录两者及实际依据，保留初始探测和变换后几何报告。CLI、报告 schema 1.2、脚本生成和像素处理不变。
- 验收：446 项自检通过，覆盖阈值边界、双向真实差异及主流程变换后归零场景；M8 注册数据回放不再误报，保留 81/69 帧语义。48 种生成脚本和 M8 实际脚本与修复前逐字一致；本轮未启动真实 Siril/PWS。详见[验证记录](../reports/2026-10-09-drizzle链路复核修复.md)。

## [1.2.0] - 2026-10-09

- 验收：463 项自检及真实 Siril/PWS 检查通过；受控 RGB 背景默认保持差异，仅显式开启后均衡。M8 默认关闭回归复用 69 帧，清除隔离副本统计缓存后与基准所有母版像素一致；正确采样 RGB 约 0.142962/0.140086/0.177374，旧近似等值结论撤销。旧母版未改，实测与预览后备数据不混淆。

- 修复 Float32 FITS RGB 按交错像素读取的缺陷，改为平面布局同位置采样，支持 BSCALE/BZERO、有限值和截断校验；修正 RGB 合成帧和统计夹具的存储布局。
- 删除未执行的通道增益推导与虚假 gains_applied。报告 schema 1.2 增加 medians_after 和 background_equalization，旧 medians_before/gains_applied 保留为 null；实测统计与预览 JSON 分离，RGB 统计无效时不发布母版。
- 新增显式 --rgb-equal，默认关闭，仅在 Siril stack 中启用原生 RGB 背景归一化；不替代 PCC/SPCC，不引入 numpy。PWS 灰度链路拒绝显式使用此开关；dry-run 和参考帧回退记录真实执行状态。

## [1.1.0] - 2026-10-08

- 默认 Siril 筛帧前移到 `seqapplyreg`，输出 `.seq` 固定名单，叠加不再二次百分位筛选；PWS 保持全帧输入。报告读取最终注册序列的完整质量分布，以母版 `STACKCNT` 和叠加完成日志核对实际数量，无效名单/计数矛盾阻止发布。`framing=current` 参考帧被排除时仅回退一次到原筛选流程，复用实际序列和矩阵，不重复校准或注册。纠正筛选日志计数、excluded 持久化及旧命令示例。
- 筛帧前移验收：366 项自检与真实 Siril/PWS 检查通过；M8 仅一次校准/配准，固定 69/81 帧，图像与权重各减少 12 份，少写 2.38 GiB；3982×4552 RGB Float32 母版与探测复用基准所有像素一致，原始输入未变。单次总耗时 77.960→56.718 秒，不作为多轮性能基准。

- 新增 `--stack-engine=pws` 叠加引擎分支：Siril 前端（calibrate → register -2pass → seqapplyreg → seqsubsky）产出的注册帧交给相邻 `pws-stacking` skill 的分区权重叠加引擎（PWS，D.Cikey），成品 XISF 由新增的 stdlib 转换器转回 32bit FITS 母版，preview/commit 契约不变。方法出处与署名见 `references/08-pws-engine.md`（新增）。
- 新增 `scripts/pws_bridge.py`（仅标准库）：实测闸门（帧数 ≥20、wfwhm p90/p10 >1.4、drizzle 链路结构性拒绝）、注册帧→灰度 FITS 转换（绕开引擎读取器的 channels-first 折叠缺陷，实测 200×150 RGB 帧会被错误折叠为 (3,150)）、XISF→FITS 母版转换（针对引擎写出子集的迷你读取器，小端 attachment → 大端 FITS，与真实引擎产物像素级往返 maxerr=0）。
- `--stack-engine=pws` 拒绝时 exit 5 并把理由写入 `report.stack_engine.reasons`，绝不静默回落 Siril stack；drizzle 链路实测 seqapplyreg 输出每帧画布尺寸互不相同（218×176 / 205×156 / 200×150 混存），引擎要求统一 shape，故结构性拒绝。
- report 契约扩展（schema_version 1.1）：新增 `stack_engine` / `stack_engine_run` / `stacking.method=="pws"` 分支字段；`frame_quality` 新增 `weighted_fwhm_p90_over_p10` 与 `dispersion_note`（对默认引擎同样有效，作为离散度驱动加权的可行性预演）。
- PWS 分支在 Siril 脚本中追加 `seqsubsky 1 -prefix=sk_`（实测验证 1.4 可用）：PWS 引擎假设帧间天空一致接近公共底，先减合成背景再加性平移才有正确着落。
- `selftest.py` 新增五节：闸门单元测试、PWS 分支脚本生成、黑角闸门（L 形黑区几何精确断言）、与真实引擎产物的 XISF 转换往返断言、真实端到端（Siril 前端 + 真实 PWS 引擎 + 20 帧双质量组）。全绿 176 项（含 `--real`）。
- 修复探测和正式处理的重复校准/配准：自动 CFA 无平场时保留 CFA 探测；数据状态相同则复用已验证的 `.seq`。选择 debayer 时仅对已校准 CFA 去拜耳，再注册新 RGB；有平场且切换链路时保留必要重建。删除含义反转的 `calibrate_ran` 控制，纠正此前“调用点已修复”的不实记录。PWS 支持实际注册序列名，原命名仍兼容；非法探测矩阵在 probe-reg 阶段失败。
- 修复 `pws_bridge.build_card` 本地复制缺 END 特判的缺陷：写出 `END     = `（含 `= `）使 `fits_probe.data_offset` 报 "no END card found"，黑角检测静默失效（`sampled=0`）。现直接委托 `fits_probe.build_card`，删除语义不实的"本地复制"注释。
- 新增 PWS 黑角闸门 `check_black_corners`（阈值 10%）：大旋转序列在固定画布上产生三角无数据区，引擎无 coverage 掩膜，黑角 ~20% 时其 precheck 把黑块中值 0 误判为"帧内背景结构 107.2%（阈值 12%）"并以"帧未校准"拒绝（理由误导，决定正确）。闸门在桥接层抽样首/中/尾帧测非正像素占比，超阈值时以准确理由拒绝。81 帧真实 M8 数据实测黑角 3.5%→23.5% 逐帧记录。
- 真实 81 帧 M8（33.19° 旋转、33 秒/帧升温至 45.5°C）回归记录与三处异常的完整分析见 `references/08-pws-engine.md` §8.8；默认 drizzle 链路结果与 1.0.0 已知局限记录一致（69/81 帧、span 1264px、rot 33.19°）。
- `synth_fits.py` 扩展：`blur_per_frame`（逐帧 PSF 宽度增量，制造真实帧间离散度）、`vignetting` 开关（模拟"已校准"输入）；STARS 表扩至 12 颗含远角星（满足引擎 precheck 的 ≥5 颗孤立星要求，26px 边界 + 25px 隔离）。原有调用默认值不变，地面真值测试不受影响。
- 已知局限（SKILL.md）：PWS 成品 FITS 头 `STACKMODE` 因 FITS 8 字符键长截断为 `STACKMOD`，完整参数以 XISF 关键字与 `report.stacking` 为准。

## [1.0.0] - 2026-10-07

- 首次纳入本仓库治理：`skills.manifest.json` 登记、README 四处标记区（索引 / 目录树 / 安装命令 / 使用示例）同步，补齐 `CHANGELOG.md`、`RELEASING.md`、`LICENSE.md` 与 `requirements-dev.txt`。
- 新增 `LICENSE.md` 声明专有许可；`SKILL.md` 的 `license` 改为 `Proprietary` 并补齐 `metadata` 六项（slug / version / displayName / summary / tags / homepage）；移除来路不明的 `agent_created` frontmatter 键。
- `description` 改写为中英双语 block scalar，在保留原有全部触发词的前提下补充三条反向边界（月面幸运成像 / 已堆栈面板拼接 / 成品非线性后期分别归属 `siril-moon-stacking`、`siril-mosaic`、`deep-sky-processor`），并明确预览 PNG 经 MTF 拉伸。
- 清理 `fits_probe.py` 中三个零调用函数 `read_sequence_info` / `pack_uint16_header_cards` / `unpack_uint16`；其中 `pack_uint16_header_cards` 的 docstring 曾谎称供 `selftest.py` 的合成器调用，而实际导入的是 `build_card` / `data_offset` / `estimate_channel_medians` / `pad_to_block`。
- 删除 `param_derive.py` 中零引用的 `SMART_ROUNDNESS`，`-filter-round=0.30` 的取值只保留在 `SMART_FILTERS` 一处。
- 消除 mosaic 阈值双写：`MOSAIC_SPAN_FACTOR = 1.2` 现由 `param_derive` 单点持有并经新增的 `mosaic_factor` 参数传入 `fits_probe.summarize_transforms`，后者不再硬编码字面量。
- 统一后缀拒绝表：`astra_stack.collect_frames` 内联的 7 项后缀改为复用 `fits_probe.REJECTED_SUFFIXES`，补齐此前漏掉的 `.mkv` 与 `.webp`；仅含这两种文件的目录不再报笼统的 `no FITS frames found`。
- 修正 `selftest.py` 中恒取右侧的 `identify_device(...) if False else {...}`：`rgb_device` 不再是永不执行的假调用。
- 删除 `astra_stack.py` 中被紧随其后的重复调用覆盖的 `derive_pipeline`（两处参数逐字相同，区间内零引用）。
- 订正 `estimate_channel_medians` 的 `max_samples` 语义为「整帧总采样预算，按通道数整除摊分」，并移除在当前唯一调用路径（RGB 母版 `NAXIS3=3`）下不可达的早退分支。
- 订正 `fits_probe` 模块 docstring：删除从未实现的 astropy 交叉校验承诺——全文没有任何 astropy 导入或 `ImportError` 探测，该表述与 `SKILL.md` 的「仅标准库」约束自相矛盾。
- 简化 `device_signatures.py` 中 `except (UnsupportedInputError, Exception)` 为 `except Exception`（前者是后者的子类，元组无意义）。
- 修复 `fits_probe.py` 文件末尾缺少换行符的问题。
