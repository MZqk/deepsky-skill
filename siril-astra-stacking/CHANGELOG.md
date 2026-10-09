# Changelog

本文件记录 `siril-astra-stacking` 的独立版本变更。

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
