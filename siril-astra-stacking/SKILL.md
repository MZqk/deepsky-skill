---
name: siril-astra-stacking
description: >-
  Headless Siril 1.4 astrophotography stacking pipeline. Identifies capture devices (ZWO Seestar series and traditional setups) from FITS headers to derive calibration, registration, and stacking parameters. Publishes 32-bit linear master FITS, MTF preview PNG, and JSON report. Supports Siril rejection stacking and Partition-Weighted Stacking (--stack-engine=pws). Use when stacking raw FITS sequences, calibrating and registering deep-sky frames, or processing Seestar captures. Do NOT use for planetary/lunar imaging (see siril-moon-stacking) or mosaic stitching (see siril-mosaic).
  基于 Siril 1.4 CLI 的天文图像自动化无头叠加流水线。依据 FITS 头元数据识别设备（ZWO Seestar 与传统望远镜）并自动推导校准、配准与堆栈参数，交付 32-bit 线性母版 FITS、MTF 预览 PNG 与可审计 JSON 报告。支持原生拒绝法与 PWS 分区权重叠加（--stack-engine=pws）。当需要叠图、堆栈、校准对齐原始 FITS 序列或处理 Seestar 数据时触发。不适用于行星/月面幸运成像（见 siril-moon-stacking）或马赛克拼接（见 siril-mosaic）。
license: Proprietary
compatibility: "Requires siril-cli 1.4.x and Python 3.9+. Optional PWS mode requires sibling pws-stacking skill."
metadata:
  slug: siril-astra-stacking
  version: "1.4.1"
  displayName: Siril Astra Stacking
  summary: 使用 Siril 1.4 CLI 按设备元数据推导校准、配准与堆栈参数，无头叠加 FITS 序列，交付 32bit 线性母版、MTF 预览与可审计报告。
  tags: "astronomy, siril, stacking, fits, seestar"
  homepage: https://github.com/MZqk/deepsky-skill
---

# siril-astra-stacking

Siril 1.4 无头叠加流水线。按设备元数据推导参数，而非按图像尺寸猜测。

## 四条铁律

1. **只信 FITS 头。** 设备识别仅使用 `TELESCOP` / `INSTRUME` / `CREATOR` / `PRODUCER` 签名表与显式配置覆盖。**禁止**用宽高比、位深、像素数推断设备——误判会静默套用错误的校准与叠加参数，而拒绝只是让用户改一行配置。
2. **拒绝优于猜测。** 视频、SER、8bit 预览图、未列入签名表的设备、缺失的校准帧，一律明确报错退出。绝不降级、绝不伪造。
3. **失败不污染产物。** 所有中间文件在 `<target>.work/`，三件套全部成功后才原子改名发布。中断或失败时上一份完好的母版保持不变；崩溃兜底报告也不会覆盖已发布的成功报告。
4. **一次运行一个滤镜。** probe 阶段逐帧读取 FILTER 并分组，目录内混放多种滤镜直接拒绝（退出码 6），并按滤镜给出帧数分桶清单——混叠会把第一帧的平场套给所有帧，且不可分通道回收。

## 环境要求

- `siril-cli` **1.4.x**。macOS 常不在 PATH，脚本按顺序探测：`--siril` 显式路径 → PATH → `/Applications/Siril.app/Contents/MacOS/siril-cli` 等常见安装位置。
- Python 3.9+，**仅标准库**。无numpy / astropy / sirilpy 依赖。
- `--stack-engine=pws` 是唯一例外：届时通过子进程调用相邻 `pws-stacking` skill 的运行器（其引擎需要 numpy/scipy/astropy/photutils）。依赖不可导入时报错拒绝，本 skill 自身代码路径仍零第三方导入。

## 用法

背景均衡默认关闭。明确需要 RGB 背景归一化时使用 `--rgb-equal`；需要后续 PCC/SPCC 的工作流可保持默认。`--channel-medians` 只提供预览的后备统计，不改变母版颜色，也不作为实测通道数据。

```bash
# 基本用法
python3 scripts/astra_stack.py /data/M42 --out ./results

# 只推导参数并把 .sir 脚本写到 stdout，不调用 Siril
python3 scripts/astra_stack.py /data/M42 --out ./results --dry-run > plan.sir

# 传统设备：指定校准帧目录
python3 scripts/astra_stack.py /data/M31 --out ./results --calibration-dir ./masters

# 强制指定 CFA 链路（默认按实测变换自动判定；mono 仅单色传感器可用）
python3 scripts/astra_stack.py /data/NGC7000 --out ./results --force-chain drizzle
python3 scripts/astra_stack.py /data/NGC6946 --out ./results --force-chain mono

# 成功后保留中间目录，用于检查注册数据与筛帧效果
python3 scripts/astra_stack.py /data/NGC7000 --out ./results --keep-work

# 只要结论行，不要阶段进度
python3 scripts/astra_stack.py /data/M42 --out ./results --quiet

# PWS 叠加引擎：注册后的帧交给分区权重叠加引擎（需相邻 pws-stacking skill）
python3 scripts/astra_stack.py /data/M31 --out ./results --stack-engine pws
```

## 多滤镜母版的合成准备

已确认黑白传感器的多滤镜任务，按 FILTER 分桶独立叠加后，必须调用 [prepare_composition.py](scripts/prepare_composition.py) 生成 Siril RGB 合成输入。复用成功报告中的母版，不重新校准、叠加或修改原三件套；普通彩色相机不得传入黑白确认参数。

```bash
# 基本合成输入准备（输出到指定目录）
python3 scripts/prepare_composition.py /data/NGC6946/run \
  --out /data/NGC6946/run/composition_inputs --monochrome

# 推荐：精简交付包（仅保留叠加图、预览图、diagnostics 和 README）
python3 scripts/prepare_composition.py /data/NGC6946/run \
  --out /data/NGC6946/delivery --monochrome --bundle
```

输入为各滤镜报告目录的共同父目录，报告位于其直接子目录。输出目录必须不存在。RGB drizzle 母版取三个同滤镜平面的等权平均；已有单通道另存副本；全局两遍配准后按最小公共区域导出 32-bit 单通道 FITS。输出仅删除副本的 BAYERPAT，不改变曝光记录或进行拉伸/亮度归一化。

**精简交付包（`--bundle`）**：仅保留四项核心资产：根目录 `<FILTER>.fit`（Siril 图像合成直选即用）、`previews/`（各通道快速肉眼质检图）、`diagnostics/`（对齐清单与执行日志）及根目录 `README.md`。详细契约见 [05-output-contract.md §5.8 与 §5.9](references/05-output-contract.md)。

**产物**：`<target>_stacked_32bit.fits`、`<target>.png`、`<target>_report.json`。

**运行输出**：stderr 每阶段一行进度，末行给出结论（含退出码与报告路径）；`--dry-run` 额外把 `.sir` 脚本正文写到 stdout。`--quiet` 只抑制进度行，结论行始终输出。**脚本层请读 `<target>_report.json`，不要解析 stderr。**详见 `references/07-failure-and-resume.md` §7.6。

## 渐进式加载

本文件只承担路由与铁律。**按需加载 references，不要全量读取。**

| 遇到的问题 | 加载文档 |
|---|---|
| 设备识别失败、要加新设备或改签名表 | [01-device-signatures.md](references/01-device-signatures.md) |
| 校准帧匹配、暗场内嵌、平场缺失 | [02-calibration.md](references/02-calibration.md) |
| 配准链路选择、drizzle 与 mosaic、场旋转阈值 | [03-registration.md](references/03-registration.md) |
| 筛帧 flag、拒绝算法、median 禁令 | [04-stacking.md](references/04-stacking.md) |
| PWS 叠加引擎、闸门、XISF 转换 | [08-pws-engine.md](references/08-pws-engine.md) |
| 三件套契约、MTF 拉伸、report schema | [05-output-contract.md](references/05-output-contract.md) |
| Siril 1.4 能力边界、实测踩坑、禁用清单 | [06-siril-1.4-constraints.md](references/06-siril-1.4-constraints.md) |
| 退出码、work 目录、原子提交、输出契约、resume | [07-failure-and-resume.md](references/07-failure-and-resume.md) |

## 流水线七阶段

1. **probe** — 收集 FITS 帧，解析头部，判别 CFA(`NAXIS=2`) / RGB(`NAXIS=3`)。
2. **identify** — 签名表匹配设备；未命中则拒绝并列出线索。
3. **plan** — 匹配校准母帧（暗场按温度/增益/曝光就近）。
4. **calibrate** — 应用校准。自动 CFA 且无平场时保留 CFA，待探测后再决定是否去拜耳；明确指定链路、RGB 输入或有平场时按相应配置生成探测序列。智能望远镜走「暗场已内嵌」分支。
5. **probe-reg** — 在**校准后**的序列上跑 `register -2pass`，读回并验证 `.seq` 中的单应矩阵。数据状态不变时直接复用；CFA 去拜耳后或校准参数改变时才重新配准。详见 [03-registration.md §3.7](references/03-registration.md)。
6. **stack** — 按实测值选定链路后生成并执行 `.sir`。`--stack-engine=pws` 时此阶段改为：闸门评估（帧数 + 实测清晰度离散度）→ Siril 前端跑完 `seqapplyreg`（+ `seqsubsky`）→ 注册后帧交给 PWS 引擎。
7. **preview → commit** — MTF 拉伸出 PNG，三件套原子发布。

`calibration.cc.enabled` 是计划开关，不代表已执行。实际 Siril 坏点校正状态记录在 `report.notes`，按具体脚本/校准命令区分计划、跳过、成功完成与完成未确认；无 master dark 时不生成任何 `-cc=dark`，设备内部处理状态不由本流程推断。

链路复核仅针对自动选择，使用变换前最终注册序列，并复用旋转、尺度及 mosaic 的同一判据。`registration_probe` 保留初始决策依据；`registration.observed_after_run` 描述变换后的几何，不作为原始链路是否合理的依据。显式指定链路不产生决策矛盾提示。

## 叠加引擎（siril 默认 / pws 可选）

- **`siril`（默认）**：`stack rej w 4 3 -norm=addscale -weight=wfwhm` 帧级加权拒绝叠加，两条 CFA 链路都支持。
- **`pws`**：`--stack-engine=pws` 把 Siril 的 `stack` 一步替换为 [分区权重叠加法](references/08-pws-engine.md)（Partition-Weighted Stacking，D.Cikey）：帧级清晰度权重 `C_i` 与信噪比权重 `S_i` 经逐像素分区场 `R` 在指数上插值，细节区吃解析力、背景区吃逆方差信噪比。入口有**实测闸门**（≥20 帧 且 wfwhm p90/p10 > 1.4；drizzle 链路结构性拒绝），不达标即报错退出，绝不静默回落。需要相邻 `pws-stacking` skill 的引擎（numpy/scipy/astropy/photutils），缺失时干净拒绝——本 skill 自身仍保持仅标准库。

## 三条 CFA 链路（严格互斥）

```
NAXIS=2 + BAYERPAT
  ├─ 实测 span > 1.2×FOV 或 旋转 > 5° 或 尺度偏差 > 5%
  │    → DRIZZLE 链路：calibrate 不加 -debayer，drizzle 阶段自行去拜耳
  │      seqapplyreg -drizzle -framing=max -filter-*
  │      stack rej w 4 3 -maximize
  │
  ├─ 设备 profile 标记 mono_sensor_capable（如 ASIAIR + 单反）
  │    → MONO 链路：全程不去拜耳
  │      calibrate -cfa
  │      seqapplyreg -framing=current -filter-*
  │      stack rej w 4 3
  │
  └─ 否则 → DEBAYER 链路
       calibrate -cfa -debayer [-equalize_cfa]
       seqapplyreg -framing=current -filter-*
       stack rej w 4 3

NAXIS=3 → RGB 路径，drizzle 不适用（非 Bayer 传感器）
```

**默认两条链路（drizzle / debayer）的输出都是三通道 RGB**（1.4.4 实测）：debayer 链路在 `calibrate -debayer` 转换，drizzle 链路在 `seqapplyreg -drizzle` 内部转换——**drizzle 要求输入仍是 CFA，遇到已去拜耳的 RGB 序列会报 `This sequence is not mono / CFA, cannot drizzle.`**。所以「保持 CFA」指的是**转换发生的时机**，不是最终产物的格式；`BAYERPAT` 不会出现在母版里。转换点详见 [03-registration.md §3.1](references/03-registration.md)。

**mono 链路是例外**：母版为 `NAXIS=2` 且 `BAYERPAT` 保留。**Siril 1.4.4 没有 `-mono` 参数**（已对二进制核验），mono 就是「没有 `-debayer`」而已。它只对 profile 标记单色传感器的设备生效——彩色传感器上拜耳阵列的每个位置记录的是真实颜色，去掉去拜耳等于丢色。

三条链路不可混用：Siril 对非 Bayer 数据拒绝 drizzle（`Cannot use drizzle on non-bayer sensors, aborting.` / `This sequence is not mono / CFA, cannot drizzle.`），对 CFA 数据用普通插值会破坏拜耳结构（`Applying interpolation on a sequence opened as CFA is a bad idea.`）。

**大旋转触发 drizzle 的真实理由**：单应矩阵能精确表示纯旋转，**星点不会拖线**（M8 实测：33° 旋转下角落星点依然圆）。代价是帧边缘附近必须从源帧外插值，损失数据并产生边缘伪影。drizzle 能最优利用可用数据，因此更安全。若在意画布紧凑性，`--force-chain debayer` 可强制简单链路——实测输出星点质量相当，只是画布外多出无数据黑边。

**单色传感器需要单通道时**：几何在阈值内会自动走 mono；若 span/旋转触发了 drizzle，mono 不可达（drizzle 内部会去拜耳），报告会给出原因并建议 `--force-chain mono`。该开关会接受 `-framing=current` 在边缘损失数据的代价。

## 必须知道的 Siril 1.4 行为

这些是实测发现的，官方文档未载，全部已在代码中处理：

- **`-2pass` 不可省。** 普通 `register` 写入输出序列的单应矩阵**全为单位阵**（九个系数读作 `1 0 0 0 1 0 0 0 1`），随后的 `seqapplyreg` 报 `Existing registration data is a set of identity matrices` 并中止。`-2pass` 才把实测变换留在源序列上。
- **`-cc=dark` 的 sigma 不可脚本化。** 手册写 `-cc=dark [siglo] sighi`，实测 1.4.4 CLI：`-cc=dark 3` 报 `Unknown parameter 3`（"sigma 3.00 for hot pixels" 日志是内置默认值，不是读到了参数），`-cc=dark=3` 与整体加引号也被拒。只有裸 `-cc=dark` 可用（=热 sigma 3、冷关，即计划默认值）。生成器只输出裸形式，自定义 sigma 直接拒绝。
- **`-framing=max` 依赖 drizzle 权重。** 它不是独立的插值选项，脱离 drizzle 使用会撞 `Drizzle stacking cannot be performed because drizzle weights are missing.`。因此画幅判定必须绑在 drizzle 链路上。
- **median 叠加会静默禁用 `-maximize`**（只记 `Disabling` 不报错），拼接会无声丢失。参数层硬禁 median。
- **序列名不能以数字结尾。** 帧命名为 `<stem>NNNN.fit` 时，Siril 把尾部数字并入序号：目标 `astra_t1` 生成的 `astra_t10000.fit` 被解析为 stem `astra_t` + index `10000`，导致命令报 `invalid input sequence`。`sequence_stem()` 会剔除末尾数字。
- **序列名必须相对 CWD。** 绝对路径被拒。Siril 用 `-d <dir>` 指定工作目录；且当目录中存在多个序列时，它会忽略请求的名字、只取自己发现的第一个——所以 work 目录必须保持单一 stem。
- **各级前缀会累积。** `calibrate -prefix=c_` 产出 `c_<stem>`，随后 `seqapplyreg -prefix=ap_` 产出 `ap_c_<stem>`。名称需逐级推导。
- **`savepng` 会自己追加 `.png`**，传入 `out.png` 会得到 `out.png.png`。
- **`calibrate` 会重置注册数据**，因此 transform probe 必须跑在校准之后。
- **CWD 会被强制改写**到 `/tmp/s1d`（除非用 `-d`），`.sir` 路径需绝对。
- **`-minpairs` 默认 8**，小视场智能望远镜数据常不足，本工具按设备 profile 用 4。
- **`setmag` / `seqsetmag` / `load_seq` 在 1.4 不可脚本化。**
- **禁用清单**：`mpp` / `register_mpp` / `stack_mpp`、`-debayer=`、`-extref=`、`starnet`、`seqwcsbg`、`unload`、`pjp`、`seqsetreg`。生成器会在生成期拒绝。

详见 [06-siril-1.4-constraints.md](references/06-siril-1.4-constraints.md)。

## 变换读回：为什么用 `.seq` 而非 sirilpy

官方读回方式是 `sirilpy.SirilInterface.get_seq_regdata(frame, channel)`，但它需要 Python 进程与 Siril 进程之间的 socket。**Siril 1.4.4 CLI 模式下该 socket 并不总是建立**，`pyscript` 会失败于 `SirilConnectionError: 'NoneType' object has no attribute 'sendall'`。

因此本工具解析 `register` 写出的 `.seq` 文本文件。`R<channel>` 行格式：

```
R0 <fwhm> <wfwhm> <roundness> <quality> <background> <nbstars> H <h00..h22> <pair_matched> <inliers>
```

**符号约定（已实测标定）**：场景按 `(+dx, +dy)` 平移时读出 `h02=+dx`、`h12=+dy`，**同号无翻转**。旋转取 `atan2(h10, h00)`，尺度取 `sqrt(det(M))`。合成序列（真值 `dx=3i, dy=-2i`）实测 `h02=+3.018/+6.017/+9.018`、`h12=+2.012/+4.011/+6.000`，误差 < 0.02 px。

注意：`R<channel>` 中的数字是**通道号而非帧号**，帧序要从 `I` 行读取；`register -2pass` 会把参考帧换成质量最好的那一帧，因此位移相对参考帧、符号可能与场景推进方向相反——**只有跨度 span 有意义，符号不参与 mosaic 判定**。

## 帧筛选的参数形式（两个实测陷阱）

**① `Nk` 是 k-sigma，离群值会让它完全失效。** k-sigma 阈值由均值和标准差算出，而离群值恰好抬高标准差——正是这些 filter 想剔除的帧把阈值撑大。实测真实 134 帧数据：`weighted_fwhm` 中位数 4.12 但有 6 帧在 7.24–11.13，sigma 从 0.35 被抬到 1.11，`0.8k` 阈值算成 5.01，**134 帧零剔除**。改用百分位后正常。报告的 `frame_quality.weighted_fwhm_sigma_ratio` 超 0.15 即为警告。

**② `N%` 是"保留最好的 N%"，不是"剔除最差的 N%"。数字越大保留越多。** 隔离实测同一序列：`25%`→35 帧、`10%`→16 帧、`5%`→8 帧、`2%`→4 帧。保守筛选要用**大**数字。

默认 Siril 链路在 `seqapplyreg` 中按原生质量条件筛选，只导出保留帧；`stack` 复用输出 `.seq`，不再次按百分位筛选。多个 filter 的日志包含各项独立计数，最后一行不保证是交集。实际数量以母版 `STACKCNT` 为主，与叠加完成日志及输出名单核对；`frame_quality` 和注册数量仍覆盖变换前的最终注册序列。

## 自检

```bash
python3 scripts/selftest.py           # 第一层，无需 Siril
python3 scripts/selftest.py --real    # 加真实 Siril 端到端
python3 scripts/test_prepare_composition.py --real # 合成准备的真实 Siril 夹具
```

## 已知局限

- **CFA 两条链路均已在真实数据上端到端验证**：
  - *debayer*：134 帧 S30 Pro SH2-296（2026-02-22，`CCD-TEMP=25.88°C`），111 秒，115/134 帧（38.3/44.7 分钟）。
  - *drizzle*：81 帧 S30 Pro M8（2026-07-15，`CCD-TEMP=34.19°C`），87 秒，69/81 帧，旋转 33.19° 自动触发，画布扩至 3982×4552。
  - *单反路径*：145 帧 Nikon D5100 NGC 6946 分桶六通道（2025-08-17～20，L/R/G/B/Hα/SII），L 桶 62/69 帧、196 秒。
- **默认两条 CFA 链路（drizzle / debayer）的产物都是三通道 RGB**（1.4.4 逐阶段实测）：debayer 链路在 `calibrate -debayer` 转换，drizzle 链路在 `seqapplyreg -drizzle` 内部转换（`BAYERPAT` 不保留）。drizzle 拒绝已去拜耳的序列（`This sequence is not mono / CFA, cannot drizzle.`），故不能在 `calibrate` 阶段预先去拜耳。mono 链路是例外，产物为 `NAXIS=2`。详见 [03-registration.md §3.1.1](references/03-registration.md) 与 [06-siril-1.4-constraints.md §6.9.1](references/06-siril-1.4-constraints.md)。
- **mono 链路（1.4.1 起）**：profile 含 `mono_sensor_capable: true` 的设备（当前仅 `zwo_asiair_dslr`）在几何未触发 drizzle 时自动走单通道，母版 `NAXIS=2` / `BAYERPAT` 保留，`report.stacking.output_naxis == 2`、`report.registration.mono_requested == true`。**行为变更**：ASIAIR + 单反的既有用户会发现原本三通道的窄带桶变成单通道；用 `--device-profile`（JSON 内写 `{"zwo_asiair_dslr": {"mono_sensor_capable": false}}`）可退回。`--stack-engine=pws` 与 mono 互斥（退出码 5），`--rgb-equal` 与 mono 互斥。
- **RGB 路径已用 Siril 去拜耳生成的 Float32 序列完成端到端验证**，原始设备直接采集的 RGB 序列仍未实测。合成器直接写出的 `NAXIS=3` / `BITPIX=16` 数据此前无法检出星点，当前真实自检使用 Siril 生成的 RGB 样本。
- **RGB 统计与颜色处理**：Float32 FITS 按 R/G/B 平面读取；旧交错读取导致“通道几乎相等”的结论无效。默认不均衡母版颜色；只有显式 `--rgb-equal` 才在 Siril stack 中启用原生背景归一化，作用不等同 PCC/SPCC。PWS 灰度链路不支持该开关。schema 1.3 用 `medians_after` 记录实测叠后统计（旧 `medians_before` 与未执行的 `gains_applied` 置空）；RGB 无有效有限采样时不发布母版；mono 母版的 `medians_after` 为单键 `{"m": 值}`。
- **智能望远镜无平场，背景梯度无法校正**：M8 数据（高天空背景 + 33° 旋转 + 1264 px 漂移）下渐晕与光污染梯度在预览中明显可见。这不是处理缺陷，而是设备物理限制。
- **中间文件占用大量磁盘**：81 帧 2160×3840 需约 8 GiB，134 帧需约 12 GiB。空间不足时报 `Not enough free disk space` 并中止。磁盘紧张时用 `--dry-run` 先看参数，或分批处理。
- `--resume` 尚未实现跨运行检查点复用，仍重建 work 目录，仅用于失败后重试；有效 `.seq` 可在同次运行的多个 Siril 调用之间复用。
- 签名表收录 ZWO Seestar 系列与 ZWO ASIAIR + PTP 单反（如 Nikon D5100）。其它设备需扩展 `scripts/device_signatures.py` 的 `SIGNATURES`，或用 `--device-profile` 提供 JSON 覆盖（覆盖只能改 profile，不能让未列入表的设备通过识别）。
- **单反（ASIAIR + DSLR）路径已在真实数据上端到端验证**：145 帧 Nikon D5100 NGC 6946（2025-08-17，B 滤镜，300s/帧，EQMod），130/145 帧（10.8 小时）drizzle 链路出片。单反头部无 `CCD-TEMP`/`GAIN`，母帧匹配按 profile 的 `calibration_optional_keys` 双侧缺失即跳过；flat 匹配不比 `EXPTIME`、要求 `FILTER` 一致。工具只消费母帧：bias/flat 需先用 Siril 合成 master，PixInsight 的 XISF master dark 需先转 FITS（其头常缺 `EXPTIME`，需人工确认曝光后注入）。
- `--stack-engine=pws` 仅覆盖 debayer/RGB 链路：drizzle 链路写出的每帧画布尺寸互不相同（实测 218×176 / 205×156 / 200×150 混存），引擎要求统一 shape，故闸门结构性拒绝并在 report 说明理由。PWS 成品 FITS 头的 `STACKMODE` 因 FITS 8 字符键长截断为 `STACKMOD`，完整参数以 XISF 关键字与 `report.stacking` 为准。
- **`seqapplyreg -drizzle` 的执行路径未被自检覆盖**：合成 CFA 夹具（3 帧 64×48）上报 `invalid arguments`，真实数据可用——drizzle 对序列另有隐含要求。当前 drizzle 的**脚本生成**有断言覆盖（含去拜耳时机不变量），**实际执行**只有真实数据验证过。
筛帧兼容边界：drizzle 按保留帧计算原生画布，尺寸及边界像素可能变化；`framing=current` 的参考帧被筛掉时，保留失败日志并仅回退一次到全帧变换、叠加阶段筛选，复用现有校准和注册数据。PWS 不套用 Siril 质量筛选。无效输出名单或实际数量矛盾在发布前失败，原成品保持不变。
