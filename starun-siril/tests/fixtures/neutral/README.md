# S01、S02 与最小 D02 验收记录

记录日期：2026-10-05。实际执行：Siril 1.4.4，offline、原生 SSF、32-bit float 科学输出。S01/S02 已实现，两个真实 master 已完成独立运行、成对测量与实际图像审查。两个候选均为 `uncertain`，保留父源；这份记录不建立收益或新增越界阈值。

## 实现与证据口径

- 保留 `starun-siril.regions.v1`；校色声明单独绑定 `background_partition`。fit、validation 各至少三个天空区域，完整且不重复，天空矩形不得覆盖其他天空、主体或星区。采样器仅凭归一化中心生成确定性最远点分组建议，Agent 查看父图后确认。
- 系数只读取 fit 区。validation 按固定 ROI 比较 RGB 中位数范围，报告逐区配对差值、median、p90、数值改善/恶化比例、各通道空间变化和覆盖范围。范围下降是取样天空中性证据，不代表物理色彩真实性。
- 科学数据按通道保留越界前后总数、新增、持续、恢复、8×8 新增位置计数与新增最大幅度。恰好达到 0/1 计入越界；浮点越界不自动证明硬裁剪。core、faint、stars 扣除归一化中心距离最近的固定留出天空，记录 RGB 信号与 `candidate_signal - gains * parent_signal`；近零或非正信号的比例标记不可测。
- `starun-siril.neutral-validation.v1` 写入不可变 receipt，绑定父源、候选、区域合同、分组及系数；metrics 和恢复报告引用同一证据。新执行策略只对完整绑定的 neutral 避免使用候选绝对暗部总量拒绝，既有完整性检查继续执行；旧策略仍可 replay/finalize。
- accept review 必须引用 receipt、区域合同及实际父源/主候选图像。合成测试覆盖加性、明确来源的宽带白参考和双带单位增益；双带白参考仍禁止。本轮没有真实白参考验收。

## 外部材料与来源

科学 FITS、运行日志、receipt、审查与 JPEG 保留在 `/Users/mz/SeeStar/Starun/neutral-corpus-20261005/{m31,ngc6888}`。仓库只保存清单、固定 ROI、Agent 编写的原生 SSF 模板和本判读记录；模板只替换外部文件与工作目录路径。

| 案例 | 外部源文件 | 几何 / 通道 | fit / validation |
|---|---|---|---|
| M31 | `DWARF3-M31-30s401_Astro.fits` | 3856×2180 RGB / broadband | 24 / 12 |
| NGC6888 | `SeestarS30-NGC6888-60s114_LP.fit` | 1080×1920 RGB / dualband-osc | 19 / 10 |

源根目录为 `/Users/mz/SeeStar/Starun`。两例线性状态和通道证据沿用先前审计 session 中的用户确认与 FITS 头记录；原 session 路径与 SHA 一并登记在 [corpus.json](corpus.json)，未凭文件名推断线性状态或 RGB 谱线角色，也未在本轮重新验证旧记录中的厂商链接。

| 案例 | 源 SHA-256 |
|---|---|
| M31 | `a4c2b7e8b02050ecf663c342bc28a3770a6c3480f08418103ed307b8a8c40030` |
| NGC6888 | `52f46aa69ee3bfde21d372750a04ba08fdd436a0a22f6bb8e1ee34674c470aa3` |

清单还登记原始 receipt、区域、显示绑定、review 的 SHA/size。每例 `regions.json` 固定天空 ROI 及主体、暗弱和星区，坐标使用预览左上角、半开矩形。M31 主体/暗弱/星区分别为 `[1900,1060,1980,1140]`、`[1720,1260,1820,1360]`、`[1936,552,1984,600]`；NGC6888 分别为 `[610,930,650,990]`、`[438,1030,478,1070]`、`[138,994,168,1024]`。

## 实际测量与图像判读

以下为科学数据归一化单位，非 JPEG 指标。两例增益均为 `[1,1,1]`。完整背景系数、目标值与分组保存在清单和 SSF 中。

| 测量 | M31 | NGC6888 |
|---|---|---|
| 留出区 RGB 范围 median，前 → 后 | 0.0126726177 → 0.0003204495 | 0.0002021820 → 0.0001945524 |
| 留出区 RGB 范围 p90，前 → 后 | 0.0129549096 → 0.0005134583 | 0.0002983139 → 0.0002922097 |
| 数值改善的留出区比例 | 100% | 50% |
| 新增暗部越界 R/G/B | 0 / 0 / 0 | 0 / 0 / 0 |
| 新增亮部越界 R/G/B | 0 / 129 / 143 | 0 / 0 / 0 |
| 公式闭合最大绝对误差 | 7.84e-8 | 2.96e-8 |
| 同一显示传递 `mtf low mid high` | `0.245 0.04 1` | `0.02 0.015 1` |
| 审查与采用 | uncertain，未采用 | uncertain，未采用 |

Agent 已实际打开各自相同 MTF 的父源/候选预览。M31 红色天空明显减弱，星系主体与暗弱延伸仍可见；同一显示下背景变亮，既有空间梯度仍在。新增 G/B 亮部越界最大幅度约 0.0054971 / 0.0129359，保留为风险；不能据此宣称科学图像已裁剪或颜色更真实。

NGC6888 云气与星场视觉变化很小。留出区 median 改善约为 uint16 归一化半个步长，仅一半区域改善，天空样本主要覆盖上部；没有建立可见收益或全幅保证。作为受控语料运行保留候选证据，默认不采用。

`matched-preview.ssf` 只读科学图像并保存 JPEG，不写科学 FITS；执行前后源及候选 SHA 保持一致。`matched-preview-binding.json` 记录输入、工具、脚本、配置、显示传递及 JPEG 指纹。这些比较预览是审查材料，不进入已选择的科学 lineage。

实际材料：

- M31：[receipt](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/m31/runs/040-neutral.json)、[review](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/m31/reviews/040-neutral.json)、[父源预览](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/m31/previews/040-neutral-matched-parent.jpg)、[候选预览](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/m31/previews/040-neutral-matched-candidate.jpg)。
- NGC6888：[receipt](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/ngc6888/runs/040-neutral.json)、[review](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/ngc6888/reviews/040-neutral.json)、[父源预览](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/ngc6888/previews/040-neutral-matched-parent.jpg)、[候选预览](/Users/mz/SeeStar/Starun/neutral-corpus-20261005/ngc6888/previews/040-neutral-matched-candidate.jpg)。

## 复现与反例

从仓库根目录运行，真实材料和 Siril 缺失时测试明确 skip，不计入真实验收；存在材料但 SHA 不同则失败。

```sh
DEEP_SKY_SIRIL_NEUTRAL_CORPUS_ROOT=/Users/mz/SeeStar/Starun \
  python3 -B -m pytest -q -p no:cacheprovider \
  starun-siril/tests/test_neutral_corpus.py \
  starun-siril/tests/test_neutral_validation.py
```

上述命令本轮实际通过 20 项，包括两个真实样本。反例覆盖已中性与均匀偏色、fit/all 系数相同而留出区改善、空间偏色与尾部恶化、天空不足与云气误选、恢复越界抵消新增越界、边界等号、非有限值及材料哈希漂移。原生集成测试另外覆盖白参考来源/单位增益约束、报告恢复与隔离、receipt/区域/脚本漂移、实际审查材料和 lineage；协议与发布清单测试覆盖新增模块及旧策略回放。

最终全量检查：配置上述真实语料环境变量后，`python3 -B -m pytest -q -p no:cacheprovider starun-siril/tests` 实际得到 **771 passed、7 skipped、26 subtests passed**。7 个跳过项为需要 `imagecodecs` 的 4 项、未开启的真实 StarNet 推理 1 项及未配置 decoder 环境变量的 2 项。撤销语料环境变量后单跑语料测试得到 **2 skipped**。两份外部真实 receipt、区域合同、review、显示绑定与仓库清单指纹复核一致，receipt 当前完整绑定验证通过；本轮修改的 Python 语法与 diff 空白检查通过。

未完成的真实验收：宽带显式白参考。收益阈值、新增越界拒绝阈值和物理色彩真实性仍未标定；本轮只提供可绑定、可恢复的独立收益与成对安全证据。
