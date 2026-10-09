# 03 · 配准与链路选择

> **定位**：整个流水线技术风险最高的一环。选错链路会产出拉丝的重影或被裁掉的 mosaic。
> **加载时机**：理解两条 CFA 链路为何互斥、drizzle 何时必要、阈值来源、变换数值怎么读。
> **对应代码**：`scripts/param_derive.py`（`decide_chain` / `derive_registration` / `derive_apply`）、`scripts/fits_probe.py`（`read_seq_regdata` / `summarize_transforms`）

## 3.1 两条链路严格互斥

```
CFA (NAXIS=2 + BAYERPAT)
  ├─ DRIZZLE 链路：calibrate -cfa（不加 -debayer）
  │    register -2pass
  │    seqapplyreg -drizzle -pixfrac=1.0 -kernel=square -framing=max -filter-*
  │    stack rej w 4 3 -maximize -32b
  │
  └─ DEBAYER 链路：calibrate -cfa -debayer [-equalize_cfa]
       register -2pass
       seqapplyreg -framing=current -filter-*
       stack rej w 4 3 -32b
```

**不能混用的原因**（两条二进制消息，非文档）：

```
Cannot use drizzle on non-bayer sensors, aborting.
Applying interpolation on a sequence opened as CFA is a bad idea.
```

- drizzle 只吃 CFA。先 debayer 就失去 drizzle 能力。
- CFA 数据用普通插值会破坏拜耳结构。所以走 drizzle 就不能用普通 seqapplyreg。

`NAXIS=3` → RGB 路径，drizzle 不适用（非 Bayer 传感器）。

## 3.2 判定阈值

```python
ROTATION_THRESHOLD_DEG = 5.0
SCALE_DEV_THRESHOLD = 0.05
MOSAIC_SPAN_FACTOR = 1.2
```

三个常量都住在 `scripts/param_derive.py`，是链路的唯一决策源。自动选择的完成后复核也调用同一 `decide_chain`，读取变换前最终数据格式的注册摘要；不使用已经消除旋转/尺度的变换后矩阵。强制链路保留用户选择，不输出决策矛盾提示。`MOSAIC_SPAN_FACTOR` 由 `astra_stack` 显式作为 `mosaic_factor=` 参数传入 `fits_probe.summarize_transforms()`，后者不硬编码字面量——底层解析器不依赖上层策略常量。

任一命中即走 drizzle：

| 判据 | 阈值 | 依据 |
|---|---|---|
| 位移跨度 > 1.2 × FOV | mosaic | 超过视场即跨帧拼接 |
| 逐帧旋转 > 5° | AltAz 场旋转 | 超过此值全局模型产生拉丝 |
| 尺度偏差 > 5% | 变焦/画幅变化 | 全局模型无法表达 |

AltAz 智能望远镜（如 Seestar S50/S30）在赤道仪附近也可能累积可观旋转。赤道仪（尤其 meridian flip 之外）一般不触发。

### 旋转为什么触发 drizzle —— 一个被实测纠正的认知

早期文档称大旋转下"全局模型会产生拖线"。**实测否证了这个说法。**

M8 数据实测：81 帧 S30 Pro，旋转从 -1.39° 单调增长到 +33.19°（27 分钟内约 1.28°/分钟，标准 AltAz 场旋转）。强制走 debayer 链路（纯 homography）后：

- 中心区域星点锐利。
- **角落星点依然是圆的，无拉伸。**
- 画布保持 2160×3840，背景更暗更均匀。

原因是**单应矩阵可以精确表示纯旋转**，星点不会被拉成条纹。debayer 版本的唯一劣势是画布外的空白区域（drizzle 版本因 `framing=max` + `maximize` 把画布扩到 3982×4552，2.19 倍面积，多出大量无数据黑边）。

大旋转下全局模型的真实代价是：**帧边缘附近必须从源帧之外插值**，导致数据损失与边缘伪影，而不是拖线。drizzle 能最优地利用可用数据，因此仍然是更安全的选择——但理由不是"避免拖线"。

结论：阈值行为保留（drizzle 更安全），**理由的表述已修正**。若在意画布紧凑性，可用 `--force-chain debayer`。

## 3.3 span 为何参考帧无关

```python
span_x = max(dx) - min(dx)
span_y = max(dy) - min(dy)
span   = max(span_x, span_y)
```

**不用** `max(|dx|, |dy|)`。原因：`register -2pass` 会把质量最好的帧选为参考帧，它在序列中的位置是任意的。若参考帧在 mosaic 中间，两端帧相对它各偏一半，`max(|dx|)` 能触发；若参考帧恰好在角落，`max(|dx|)` 可能漏判。`span` 取极差，与参考帧选择无关。

## 3.4 变换读回：`.seq` 而非 sirilpy

### 为什么不用 sirilpy

官方 API 是 `SirilInterface.get_seq_regdata(frame, channel)`，但需要 Python 与 Siril 之间的 socket。**Siril 1.4.4 CLI 模式下该 socket 并不总是建立**：

```
log: Traceback (most recent call last):
sirilpy.exceptions.SirilConnectionError: Error in _send_command():
  'NoneType' object has no attribute 'sendall'
```

`register` 写出的 `.seq` 是行式文本，每次运行都存在，解析它不受 socket 影响。

### `.seq` 格式

```
#Siril sequence file. Contains list of images, selection, registration data and statistics
S 'c_astra_ts' 0 4 4 3 3 6 0 0 0
L 1
I 0 1
I 1 1
R0 4.92156 4.92156 0.991622 0 35153.4 7 H 0.999482 0.000554504 -9.00557 ...
```

`S` 行列位置（**已实测核对**）：

| 列 | 字段 |
|---|---|
| 1 | `S` |
| 2 | 序列名（含引号） |
| 3 | start_index |
| 4 | nb_images |
| 5 | nb_selected |
| 6 | fixed_len |
| 7 | **reference_image** |
| 8 | version |
| 9 | variable_size |
| 10 | fz_flag |
| 11 | drizzle |

`R<channel>` 行：`R<ch> fwhm wfwhm roundness quality background nbstars H h00..h22 pair_matched inliers`

`I <frame> <sel>` 行：帧序号 + 选中标志。

### 帧序号不在 R 行里

`R<channel>` 中的数字是**通道号**（R0/R1/R2 对应 RGB 层），不是帧号。帧序必须从 `I` 行读取并按位置对应到 `R` 行。

### 单应矩阵映射

```
[x']   [h00 h01 h02] [x]
[y'] = [h10 h11 h12]·[y]
[w']   [h20 h21 h22] [1]
然后 x_new = x'/w', y_new = y'/w'
```

## 3.5 符号约定（实测标定，勿硬编码）

场景按 `(+dx, +dy)` 平移，读出：

| 帧 | 真值 dx, dy | h02 | h12 |
|---|---|---|---|
| 0 | 0, 0 | 0 | 0 |
| 1 | +3, −2 | +3.01769 | +2.01224 |
| 2 | +6, −4 | +6.01660 | +4.01090 |
| 3 | +9, −6 | +9.01816 | +5.99981 |

**结论：`dx = h02`、`dy = h12`，同号无翻转。**

```python
rotation = atan2(h10, h00)              # 主旋转（度）
scale    = sqrt(det([[h00,h01],[h10,h11]]))
is_pure_translation = (|h00-1|<1e-3 and |h11-1|<1e-3
                       and |h01|<1e-4 and |h10|<1e-4)
```

`selftest.py` 用合成数据强制校验这些关系，**任何改动都必须重跑**。

### 位移相对参考帧

每个单应矩阵把**自己的帧映射到参考帧**。实测中参考帧是第 3 帧（`h02=0`），其他帧的位移为负：

```
idx 0 → dx=-9.006 dy=-6.008
idx 3 → dx= 0.000 dy= 0.000  ← REFERENCE
```

因为场景本身在向负方向推进。**符号不参与 mosaic 判定，只有 span 有意义。**

## 3.6 `-2pass` 不可省

实测对比（同一序列）：

| 方式 | 输出 `.seq` 的 H 系数 | 后续 `seqapplyreg` |
|---|---|---|
| `register`（无 -2pass） | `1 0 0 0 1 0 0 0 1`（**全单位阵**） | `Existing registration data is a set of identity matrices, no transformation would be applied, aborting` |
| `register -2pass` | 真实变换值 | 正常输出变换后图像 |

所以流水线里**不再跑第二次普通 register**，只跑 `-2pass`，再由 `seqapplyreg` 消费。

## 3.7 探测必须在校准之后

`calibrate` 会写出新序列并**重置注册数据**。若先探测再校准，探测得到的变换会被丢弃，随后 `seqapplyreg` 报 identity matrices。

当前顺序按数据状态决定：

- 自动 CFA 且无平场：`calibrate -cfa` → `register -2pass` → 读取并验证 `.seq`。选择 drizzle 时直接 `seqapplyreg -drizzle`，不重复校准或配准。
- 上述输入选择 debayer：对 `c_<stem>` 仅执行 `calibrate c_<stem> -prefix=d_ -cfa -debayer`，不再应用任何校准母帧或坏点校正；在新 RGB 序列 `d_c_<stem>` 上重新配准，然后输出 `ap_d_c_<stem>`。
- 明确指定链路或输入为 RGB：先生成目标格式的校准序列，探测后复用注册数据。
- 自动 CFA 且有平场：保留原有 RGB 探测校准及 `-equalize_cfa` 语义。仍选择 debayer 时复用；切换 drizzle 时从源帧重新生成 CFA 并配准。

`.seq` 中有效矩阵可在同次运行的不同 Siril 进程之间复用，小样本已验证。去拜耳会清除输出序列的注册数据，因此不手工把 CFA 矩阵移植到 RGB，保留必要的重新配准。探测文件缺少矩阵、包含不可解析或非有限矩阵时退出 5，`failed_stage=probe-reg`，不得把它当成可复用结果。

这不等于断点续跑：`--resume` 仍重建工作目录；`--dry-run` 没有真实注册数据，生成包含必要校准和配准的脚本，不声明复用。

## 3.8 drizzle 相关的额外约束

### `-framing=max` 依赖 drizzle 权重

```
Drizzle stacking cannot be performed because drizzle weights are missing.
Drizzle file %s not found in ./drizztmp folder, aborting
```

`framing=max` 是 drizzle 的产物，不是独立插值选项。生成器强制校验：`seqapplyreg -framing=max` 必须伴随 `-drizzle`。

### 必须配对 `-maximize`

`-framing=max` 的官方说明明确说："The resulting sequence can then be stacked using option `-maximize` of STACK command"。生成器强制两者配对。

### 禁 median

```
Cannot upscale or maximize framing with median stacking. Disabling
```

**只记 `Disabling`，不报错。** median + maximize 会静默失效，mosaic 无声丢失。`siril_script_gen` 在生成期硬禁。

### 输出不能是序列格式

```
Framing method "max" cannot export to FITSEQ or SER format, aborting
```

母版必须是单文件 FITS（本工具用 `-out=<path>`）。

### 无 flat 的处理

智能望远镜无平场。**不传 `-flat=`**——Siril 会在无权重情况下处理，但质量依赖自身平场函数。报告记录 `flat_used: "none"` 并在 `degradations` 加 `drizzle_without_flat_weights`。

**不合成平场**：用背景渐晕反推平场会引入伪影，且 `-equalize_cfa` 是给 master flat 用的、误用会偏色。

## 3.9 drizzle 链路的实测验证（M8，81 帧）

drizzle 链路此前只有合成数据验证。**M8 数据首次触发并验证了该路径。**

输入：81 帧 S30 Pro，`TELESCOP='S30 Pro_90f61d23'`，2026-07-15 傍晚拍摄，`CCD-TEMP=34.19°C`。

probe 实测：

| 指标 | 值 |
|---|---|
| 旋转范围 | -1.39° → +33.19°（单调，参考帧 = 第 7 帧） |
| dx 跨度 | 1264.4 px（视场的 0.33 倍） |
| dy 跨度 | 286.5 px |
| 尺度偏差 | 0.00098 |
| 决策 | **drizzle**（旋转 > 5°） |

结果：

- 87 秒完成，69/81 帧进入叠加（85.2%）。
- 画布 3982×4552（2.19 倍面积），呈 AltAz 旋转矩形并集特有的锯齿边界。
- M8 星云与 NGC 6530 星团清晰，星点锐利。
- 报告记录 `degradations: ["drizzle_without_flat_weights"]`。

### 背景梯度偏重

`background_lvl` 中位数 0.106，是 SH2-296（0.012）的近 9 倍——7 月傍晚拍摄的天空背景本身就亮。预览因此偏灰，这是数据特性而非拉伸失误。

同时该数据集 `weighted_fwhm` 有 15 帧超出 1.5× 中位数、`sigma_ratio=0.399`，进一步印证 k-sigma 形式在这类数据上不可用。

### 高天空背景下的 MTF

背景中位数 0.142 → 推导 `mid = 0.284`。这远高于 SH2-296 的 0.0226，因为公式是背景的固定倍数。预览看起来"偏亮"是正确反映，不是 bug。

### 对照实验

`--force-chain debayer` 强制走简单链路，输出见上。两者星点质量相当，差别在画布紧凑性与背景均匀度。