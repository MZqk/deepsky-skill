# 04 · 叠加与筛帧

> **定位**：决定最终信噪比与画面质量的一步。筛帧策略必须与设备特性匹配。
> **加载时机**：理解筛帧 flag 从哪来、为何不能用 `-filter-quality`、为什么禁用 median。
> **对应代码**：`scripts/param_derive.py`（`derive_stacking`）

## 4.1 筛帧数据源不是 seqstat

**`seqstat` 无法筛帧。** 它只是像素统计量导出器：

```
seqstat sequencename output_file [basic|main|full] [-cfa]
```

| 档位 | 内容 |
|---|---|
| basic | mean, median, sigma, bgnoise, min, max |
| main | basic + avgDev, MAD, √BWMV |
| full | main + location, scale |

**没有 FWHM、没有星数、完全不能排除帧、没有写回能力。**

真实数据源是 `register` 写入 `.seq` 的 regdata：

| flag | 数据字段 | 适用范围 |
|---|---|---|
| `-filter-fwhm=` | `fwhm` | 仅星点注册 |
| `-filter-wfwhm=` | `weighted_fwhm` | 仅星点注册，**推荐** |
| `-filter-round=` | `roundness` | 仅星点注册 |
| `-filter-bkg=` | `background_lvl` | 仅星点注册 |
| `-filter-nbstars=` | `number_of_stars` | 仅星点注册 |
| `-filter-quality=` | `quality` | **仅行星 DFT/Kombat** |
| `-filter-incl[uded]` | 手动选中状态 | 布尔 |

三种取值形式：绝对值 / 百分比（`%` 后缀）/ k-sigma（`k` 后缀）。

## 4.2 为什么智能分支不用 `-filter-quality`

`-filter-quality` 只在行星 DFT 或 Kombat 注册时填充。本流水线走的是星点对齐（deep-sky），`quality` 字段恒为 0——传 `-filter-quality=...` 是**静默空操作**。

代码中明确不生成该 flag，并在 `notes` 里记录原因。

## 4.3 两类设备的筛帧配置

```python
SMART_FILTERS = ["-filter-wfwhm=90%", "-filter-round=0.30",
                 "-filter-nbstars=85%"]
TRADITIONAL_FILTERS = ["-filter-wfwhm=90%"]
```

### 参数形式的两个陷阱（均已实测）

**① `Nk` 是 k-sigma 形式，在离群值存在时完全失效。**

k-sigma 阈值由**均值和标准差**算出，而离群值恰好会抬高标准差——正是这些 filter 想剔除的帧把阈值撑大，导致什么都剔不掉。

实测（真实 134 帧 S30 Pro 拍摄 SH2-296）：

```
weighted_fwhm 中位数 = 4.12，但有 6 帧落在 7.24 – 11.13
→ sigma 从约 0.35 被抬到 1.11
→ -filter-wfwhm=0.8k 的阈值 = 4.12 + 0.8×1.11 = 5.01
→ 134 帧全部保留，零剔除
```

改用 `-filter-wfwhm=90%` 后正常剔除尾部。**真实数据的分布信息报告在 `report.frame_quality`，其中 `weighted_fwhm_sigma_ratio` 超过 0.15 即为警告信号。**

**② `N%` 是"保留最好的 N%"，不是"剔除最差的 N%"。**

数字**越大保留越多**，保守筛选应该用**大**数字。这一点与直觉相反。

隔离验证（同一份注册序列）：

| flag | 保留帧数 |
|---|---|
| `-filter-nbstars=25%` | 35 / 134 |
| `-filter-nbstars=10%` | 16 / 134 |
| `-filter-nbstars=5%` | 8 / 134 |
| `-filter-nbstars=2%` | 4 / 134 |

我最初误以为"越小越保守"，把参数调到 `10%`，结果只叠了 16 帧——把 44.7 分钟的积分时间丢掉 88%。改为 `85%` 后保留 115 帧（38.3 分钟），符合真实数据质量分布。

### 智能望远镜（三项）

| flag | 作用 |
|---|---|
| `-filter-wfwhm=90%` | 加权 FWHM 保留最好的 90%。`wfwhm` 用星数加权，比裸 FWHM 更抗伪星 |
| `-filter-round=0.30` | **绝对值**阈值（roundness 是 0–1 量纲，不适用百分位）。放宽以避免过度剔除 |
| `-filter-nbstars=85%` | 剔除星数异常偏低的帧，通常对应云动、遮挡或严重抖动 |

`-filter-round=0.30` 的依据：ZWO 官方 `Seestar_Preprocessing` 脚本的文档说明它明确要求用户"if they find too many images are discarded before stacking, they should increase the value after `-filter-round=`"。这是官方承认的调优点，不是本工具的臆测。

实测该数据集的 roundness 范围是 0.58–0.87，因此这项 filter **从不生效**，只是防止星点被拉长（如跟踪异常）的保护。

### 传统设备（单项）

只用 `-filter-wfwhm=90%`。传统设备数据质量稳定，星数与圆度通常一致，激进筛帧反而丢信息。

### 原生筛选与实际计数

筛选交给 Siril 原生实现，不在 Python 重算百分位。默认把这些条件放到 `seqapplyreg`，只导出保留帧；输出 `.seq` 的 `I` 记录保留源文件编号，可以有间隙，作为后续叠加的固定名单。`stack` 不再附带质量筛选条件。

`number of filtered-in images` 包含各项独立计数，最后一行不一定是交集。真实 12 帧夹具中，最后一项打印 12，而实际叠加只有 10。报告优先读取母版 `STACKCNT`，与明确的 `N images have been stacked.` 完成消息核对；正常前移分支还必须与输出名单长度一致。质量分布和注册数量读取变换前的最终注册序列。

## 4.4 叠加参数

```python
args = ["rej", "w", "4", "3"]
args += ["-norm=addscale"]
args += ["-weight=wfwhm"]
if maximize: args.append("-maximize")
args += ["-rejmap", "-32b"]
# Quality filters are emitted on seqapplyreg, not stack.
```

| 参数 | 取值 | 理由 |
|---|---|---|
| 方法 | `rej` | 拒绝法，能剔除飞机轨迹、云动等离群帧 |
| 拒绝算法 | `w`（Winsorized） | 默认值。对极端离群不如 MAD 激进，但对渐变背景更稳 |
| sigma | `4 3` | 下限宽松（截尾而非硬拒），上限较紧（拒高值） |
| 归一化 | `-norm=addscale` | 加性 + 尺度，智能望远镜帧间增益有微小差异时更准 |
| 权重 | `-weight=wfwhm` | 用注册阶段算出的星数加权 FWHM，伪星抗性更好 |
| 剔除图 | `-rejmap` | 排查哪些像素被拒 |
| 位深 | `-32b` | 母版必须是 32bit 浮点 |

**帧级权重的天花板（PWS 视角）。** `-weight=wfwhm` 的权重在帧级是标量：同一张糊帧在大面积背景上仍携带真实信号，帧级权重却只能整体压低它；反之硬筛帧（`-filter-*`）直接丢帧等于丢弃该帧所有区域的曝光。分区权重叠加（PWS）把这一取舍下放到逐像素——细节区吃清晰度、背景区吃逆方差信噪比。本 skill 通过 `--stack-engine=pws` 提供该引擎（含实测可行性闸门），见 `references/08-pws-engine.md`；其余场景下，帧级 wfwhm 权重 + 百分位筛帧仍是与 Siril 能力边界匹配的最优组合。

**Winsorized `4 3` 与"分歧是信号"的佐证。** PWS 论文 3.6 节论证：细节区（星核）各帧的核值分歧是**信号**（锐帧与糊帧对同一星核的期望值本就不同），激进排异会把星核排稀。这为 `4 3` 的不对称设置提供了独立佐证：下限 4σ 宽松（糊帧的低位分歧被截尾而非硬拒），上限 3σ 较紧（卫星/宇宙线等真伪迹的绝对偏差远超真实分歧上界）。

**固定星表的检出偏差（wfwhm 的已知局限）。** PWS 论文 3.5 节指出：逐帧独立检星会让糊帧"检出星少 + 阈值相对高"，使质量度量混入星点强度偏差。Siril 的 register 正是逐帧独立检星，`wfwhm` 带同样的系统性偏差；Siril 无固定星表选项，此偏差无法在框架内修复，只能记录。影响方向：糊帧的 wfwhm 被高估得比真实 PSF 更宽，百分位筛帧与权重都会因此略微加重对糊帧的惩罚——保守方向，可接受。

## 4.5 固定名单与参考帧例外

不能把 `calibrate` 的默认选中规则外推为所有命令的行为，也不能假定质量筛选会把源序列永久写为 excluded。默认流程直接使用 `seqapplyreg -filter-*` 输出的子序列作为固定名单，叠加其全部帧。筛选条件必须在注册数据可用后使用；生成器同时支持经调用方验证的同一序列注册数据。

`framing=current` 要求原参考帧属于保留名单。Siril 1.4.4 若因这一明确错误拒绝执行，本工具仅回退一次：复用实际校准序列和矩阵，变换全部帧，再在叠加阶段按原条件筛选。两次尝试的脚本和日志都保留，不重复校准、去拜耳或注册；其他错误不触发此回退。

Drizzle 的原生最大画布受保留帧影响，提前筛选可能改变尺寸与边界像素；参考帧被排除时 Siril 也可能改选参考帧。此变化记录在回归比较中。PWS 保持全部有效帧参与自身权重计算。

## 4.6 其它拒绝算法（备选）

| 缩写 | 算法 |
|---|---|
| `n` | none，不拒绝 |
| `p` | Percentile |
| `s` | sigma clipping |
| `m` | Median |
| `w` | Winsorized（**默认，本工具使用**） |
| `l` | Linear Fit |
| `g` | Generalized GESD |
| `a` / `m` | k-MAD clipping |

sigma 参数对 `s` 类是**强制**的（除非选 `none`）。选 `g`（GESD）能更激进剔除卫星轨迹，但需要更多帧才稳定。

## 4.7 median 被硬禁的原因

```
Cannot upscale or maximize framing with median stacking. Disabling
Cannot upscale or maximize framing with median stacking. Disabling
```

**只记日志，不报错，不中断。** median + `-maximize` 时 Siril 静默禁用画幅放大，mosaic 拼接无声丢失——用户看到的是"成功"的报告和一张被裁掉大半的图。

因此 `derive_stacking` 在 `maximize` 为真时完全不生成 median 选项，生成器另有独立校验兜底：

```python
if method in MAXIMIZE_INCOMPATIBLE_METHODS and "-maximize" in args:
    raise ScriptValidationError(...)
```

注意 `stack` 语法是 `stack <seqname> <method> ...`，method 在 `args[1]` 而非 `args[0]`——早期版本读错位置导致校验漏判，现已修正并有自检覆盖。

## 4.8 `-overlap_norm` 与其它静默降级

```
Cannot compute overlap statistics if -maximize is not enabled. Disabling
```

`-overlap_norm` 只在 `-maximize` 时有效。本工具未使用该 flag，但记录在此以备扩展——若将来加入，必须同时传 `-maximize`。

## 4.9 完整生成的命令示例

### 智能望远镜 · debayer 链路

```text
seqapplyreg c_astra_ts -prefix=ap_ -framing=current -filter-wfwhm=90% -filter-round=0.30 -filter-nbstars=85%
stack ap_c_astra_ts rej w 4 3 -norm=addscale -weight=wfwhm -rejmap -32b -out=/path/M42_stacked_32bit.fits
```

### 智能望远镜 · drizzle 链路

```text
seqapplyreg c_astra_ts -prefix=ap_ -drizzle -pixfrac=1.0 -kernel=square -framing=max -filter-wfwhm=90% -filter-round=0.30 -filter-nbstars=85%
stack ap_c_astra_ts rej w 4 3 -norm=addscale -weight=wfwhm -maximize -rejmap -32b -out=/path/M42_stacked_32bit.fits
```

`-maximize` 与 `seqapplyreg -framing=max` 配对出现，缺一即被生成器拒绝。自动 CFA 转为 debayer 时上述序列名相应变为 `d_c_<stem>` 和 `ap_d_c_<stem>`。
