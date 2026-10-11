# 06 · Siril 1.4 能力边界

> **定位**：本文件记录**实测发现**的 Siril 1.4 行为，官方文档未载或与文档不符。全部已在代码中处理或明确禁用。
> **加载时机**：怀疑"按文档写的代码为何不工作时"、扩展功能前评估可行性、排查失败时。
> **对应代码**：`scripts/siril_script_gen.py`（禁用清单与静态校验）、`scripts/astra_stack.py`（序列命名）

## 6.1 环境

```
siril-cli 路径:  /Applications/Siril.app/Contents/MacOS/siril-cli
版本:            siril 1.4.4 (aarch64)
sirilpy:         /Applications/Siril.app/Contents/Resources/share/siril/python_module/sirilpy
sirilpy 版本:    1.0.25（对应 Siril 1.4.4）
```

**Siril 通常不在 PATH。** macOS 从 Homebrew 或 DMG 安装都不会创建 `siril-cli` 的 PATH 链接。探测顺序：`--siril` 显式路径 → `which siril-cli` → 常见安装位置。

## 6.2 CWD 被强制改写

```
log: Setting CWD (Current Working Directory) to '/tmp/s1d'
```

Siril 启动时把工作目录改写到自己的临时目录，**不跟随 shell 的 `cd`**。

后果与对策：

| 事项 | 对策 |
|---|---|
| `.sir` 脚本路径 | 必须绝对路径，否则 `File [x.sir] does not exist (from CWD, use absolute path?)` |
| 序列查找位置 | 用 `-d <workdir>` 显式指定 |
| 输出路径 | `-out=` 用绝对路径 |

## 6.3 序列命名的三个约束

### ① 序列名不能以数字结尾

Siril 把文件名尾部的数字并入帧序号。实测：

```
帧文件:  astra_t10000.fit  astra_t10001.fit  astra_t10002.fit  astra_t10003.fit
请求:    register astra_t1
Siril:   Sequence found: astra_t 10000->10003      ← stem 被截成 astra_t
结果:    Error in line 6 ('register'): invalid input sequence
```

`sequence_stem()` 的规则：非 ASCII 字母数字替换为 `_`，长度 ≤ 8，**末尾数字替换为字母**。

```python
astra_t1  → astra_ts
M42       → M4s
NGC7000   → NGC700s
M 31      → M_3s
targetXY  → targetXY
```

注意"先加后缀再截断"是错的：`astra_t1` + `s` → `astra_t1s` → 截断 8 → `astra_t1`，末尾又是数字。必须**替换**而非追加。

### ② 序列名必须相对 CWD

绝对路径被拒（`invalid input sequence`）。生成的 `.sir` 中所有序列名都是裸 stem。

### ③ 目录中有多个序列时，请求的名字被忽略

实测：目录里同时有 `s1NNNN`、`M42NNNN`、`verylongstemNNNN` 三组帧，请求 `register s1` 时 Siril 报 `Sequence found: verylongstem 0->2`——它只取自己发现的第一个序列。

**后果**：work 目录必须保持单一 stem。任何残留的 `.seq` 或旧帧文件都可能让 Siril 选中错误的序列。因此 `stage_workdir` 每次都重建 work 目录。

### ④ 各级前缀累积

| 阶段 | 命令 | 产出 |
|---|---|---|
| 校准 | `calibrate <stem> -prefix=c_` | `c_<stem>` |
| 注册 | `register c_<stem> -prefix=r_` | `r_c_<stem>` |
| 变换 | `seqapplyreg c_<stem> -prefix=ap_` | `ap_c_<stem>` |

前缀**前置**到上一阶段的输出名上。推导链：`calibrated = "c_" + stem`，`applied = "ap_" + calibrated`。

用单字母前缀是为了控制总长度。

## 6.4 `-2pass` 不可省（严重）

实测同一序列：

| 方式 | 输出 `.seq` 的 H 系数 | 后续 `seqapplyreg` |
|---|---|---|
| `register`（无 -2pass） | `1 0 0 0 1 0 0 0 1`（**全单位阵**） | `Existing registration data is a set of identity matrices, no transformation would be applied, aborting` |
| `register -2pass` | 真实变换 | 正常 |

两者的 `background_lvl` 也不同（单位阵版是 `0.536407`，2pass 版是 `35153.4`）。

**结论**：普通 `register` 的输出序列里没有可用变换。流水线只跑 `-2pass`，不跑第二次普通 register。

官方说明与此一致：

> `The -2pass option will only compute the transforms but not generate the transformed images, -2pass adds a preliminary pass to the algorithm to find a good reference image before computing the transformations, based on image quality and framing. To generate transformed images after this pass, use SEQAPPLYREG.`

## 6.5 `calibrate` 重置注册数据

`calibrate` 写出新序列，注册数据不保留。因此 **transform probe 必须跑在校准之后**，否则探测结果被丢弃，后续 `seqapplyreg` 报 identity matrices。

正确顺序：`calibrate` → `register -2pass`（探测）→ 读并验证 `.seq` 判链路 → 复用同一数据状态的注册信息 → `seqapplyreg -filter-*` → `stack`。格式转换后保留必要的重新注册。

## 6.6 `-cc=dark` 的 sigma 不可脚本化（实测 1.4.4）

手册（`commands/calibrate.rst`）写 `-cc=dark` 后可跟 `siglo`/`sighi`，**CLI 实际不接受**：

| 写法 | 实测结果 |
|---|---|
| `-cc=dark 3` | `Unknown parameter 3, aborting.` —— 解析器不消费后续数字；日志里 "using sigma 3.00 for hot pixels" 是内置默认值的输出，不是读到了参数 |
| `-cc=dark=3` | `Unknown argument -cc=dark=3, aborting.` |
| `"-cc=dark 3"`（整体加引号） | `Unknown argument -cc=dark 3, aborting.` —— 引号使它成为单个 token |
| `-cc=dark`（裸写） | ✅ 热像元 sigma 3、冷像元关闭（即默认值） |

**生成器行为**：只输出裸 `-cc=dark`（等价于计划默认 `siglo=None, sighi=3`）；自定义 sigma 直接拒绝（`ValueError`），绝不静默降级成默认值。

## 6.7 `-framing=max` 依赖 drizzle 权重

```
Drizzle stacking cannot be performed because drizzle weights are missing.
Drizzle file %s not found in ./drizztmp folder, aborting
Framing: Shift from reference origin: %d, %d
```

`framing=max` 的实现建立在 drizzle 权重之上，不是独立的插值选项。脱离 drizzle 使用会失败。

**生成期强制**：`seqapplyreg -framing=max` 必须伴随 `-drizzle`。

其它 framing 相关消息：

```
Framing method "max" cannot export to FITSEQ or SER format, aborting
Framing method "cog" requires all images to be of same size, aborting
Cannot compute overlap statistics if -maximize is not enabled. Disabling
```

## 6.8 静默降级清单（最危险）

这些**只记日志、不报错、不中断**：

| 消息 | 后果 |
|---|---|
| `Cannot upscale or maximize framing with median stacking. Disabling` | median + maximize 时拼接失效，图被裁剪，无任何提示 |
| `Cannot compute overlap statistics if -maximize is not enabled. Disabling` | `-overlap_norm` 静默失效 |
| `-filter-quality` 在星点注册下 | `quality` 恒为 0，filter 是空操作 |

对策：生成期静态校验拦截前两项；第三项在参数层直接不生成该 flag。

## 6.9 drizzle 的约束

```
Cannot use drizzle on non-bayer sensors, aborting.
This sequence is not mono / CFA, cannot drizzle.
Cannot drizzle sequences with images of different sizes, aborting.
Applying interpolation on a sequence opened as CFA is a bad idea.
Drizzle: CFA pattern mismatch between reference image and image #%d, using reference pattern.
```

**drizzle 与普通插值互斥**，这就是两条 CFA 链路必须二选一的原因。

官方补充：

> `Note: when using -drizzle on images taken with a color camera, the input images must not be debayered. In that case, star detection will always occur on the green pixels`

### 6.9.1 drizzle 会自行去拜耳（1.4.4 实测，文档未载）

对已经 `-debayer` 的 RGB 序列使用 drizzle，被拒绝的信息是上面第二条：

```
This sequence is not mono / CFA, cannot drizzle.
```

由此可确认：**drizzle 要求输入仍是 CFA，并在内部完成去拜耳**。逐阶段实测（NGC 6946 S 桶，读 FITS 头）：

```
calibrate -cfa                      → NAXIS=2  BAYERPAT='RGGB'
seqapplyreg -drizzle                → NAXIS=3  BAYERPAT=None    ← 转换发生在这里
对照：seqapplyreg -framing=current  → NAXIS=2  BAYERPAT='RGGB'
```

因此**默认两条链路**（drizzle / debayer）的**最终产物都是三通道 RGB**，`BAYERPAT` 不保留；差别只在转换时机（debayer 链路在 `calibrate`，drizzle 链路在 `seqapplyreg`）。这条也是「drizzle 链路不能在 `calibrate` 阶段预先 `-debayer`」的技术原因，代码里的 `-debayer` 只挂在 `chain == "debayer"` 分支上，`selftest.py::test_chain_demosaic_placement` 守住这个不变量。

### 6.9.2 `stack` 没有 `-mono`（1.4.4 实测，文档未载）

`stack` 的完整用法串（从二进制核验）：

```
stack seqfilename { rej | mean } [rejection type] [sigma_low sigma_high] [-rejmap[s]]
  [-nonorm, -norm=] [-fastnorm] [-overlap_norm] [-weight={noise|wfwhm|nbstars|nbstack}]
  [-feather=] [-rgb_equal] [-output_norm] [-out=filename] [-maximize] [-upscale] [-32b]
```

**没有 `-mono`**。`strings` 里唯一的 `-mono*` 是 `-monosensor=`（SPCC 参数，无关）。所以单通道母版不可能靠叠加上加一个开关实现，只能靠**全程不去拜耳**：`calibrate -cfa`（无 `-debayer`）→ `register -2pass` → `seqapplyreg -framing=current` → `stack`，实测得到 `NAXIS=2` / `BAYERPAT='RGGB'`，100% 像素有效、动态范围 0→1.0。

这条与 6.9.1 合起来说明 mono 与 drizzle **互斥**：drizzle 无论 `calibrate` 阶段如何都会内部去拜耳，所以 mono 只能从 debayer 分支进入。

**覆盖缺口**：`seqapplyreg -drizzle` 在自检的合成 CFA 夹具（3 帧 64×48，`BAYERPAT=GRBG`）上报 `Error in line 3 ('seqapplyreg'): invalid arguments.`，而真实数据可用——drizzle 对序列另有隐含要求（帧数/尺寸/模式），合成夹具覆盖不到该路径。因此 drizzle 的**脚本生成**有断言覆盖，**实际执行**只有真实数据验证过。

mono 链路走 `-framing=current`，与 debayer 同路径，**不受这条限制**；但它的实际执行同样只有真实数据验证过。

### 6.9.4 极窄带母版的中位数可能是 0.0

实测 NGC 6946 的 Hα 与 SII 窄带桶走 mono 后，`estimate_channel_medians` 返回 `{"m": 0.0}`：这两个滤镜极窄，扣掉天空背景后整幅图 **74% 的像素精确为 0**（对比 G 桶为 0%）。这不是采样失败——`>0` 的像素占 25.56%、99 分位 0.00044，数据本身有效。

后果是预览中点：`0.0 × 2.0` 被裁剪到下限 `0.01`，而非无数据时的默认 `0.028`。**该行为与 RGB 路径完全一致**（`{'r':0,'g':0,'b':0}` 同样得 `mid=0.01`），不是 mono 引入的偏差。

顺带发现一个**既有**问题（RGB 路径同样存在，本版未改）：`report.preview.derived_from` 写 `... median 0.000000 x 2.0`，而实际 `mid` 是裁剪后的 `0.01`，**文案与数值不符**。

## 6.10 `-minpairs` 默认 8

```
log: Not enough star pairs (7): Image 1 skipped
log: There are not enough stars in reference image to perform alignment
```

Siril 默认要求 8 对星。小视场智能望远镜数据常只有 6–7 颗星，导致**全部帧被静默丢弃**，最终报 `No image was registered to the reference`。

本工具按设备 profile 用 `min_pairs=4`。

星点检测在**绿通道**（`Findstar: processing for channel 1`）——合成数据若绿通道信号弱会检测不到星。

## 6.11 脚本文件的限制

| 限制 | 说明 |
|---|---|
| 无循环、无条件分支、无函数 | 复杂逻辑必须在 Python 侧 |
| 变量 | 仅 FITS 头变量插值，且只对 `calibrate` / `stack` / save 命令生效 |
| 错误处理 | 任一命令失败即整体停止，无 try/catch |
| 注释 | `#` 开头 |
| 换行 | 一条命令独占一行 |
| 参数分隔 | 空格 |

**引号规则**（极易错）：参数含空格时必须引号包住**整个** `key=value`：

```
正确:  calibrate sub "-prefix=r seq"
错误:  calibrate sub -prefix="r seq"
```

官方原文：

> `If you want to provide an argument that includes a string with spaces, for example a filename, you need to quote the entire argument not just the string.`

## 6.12 不可脚本化的命令（scriptable=0）

```
setmag  seqsetmag  sequnsetmag  unsetmag
load_seq  ls  dir  clear  show  visu  tilt
```

`load_seq` 不可用意味着序列名**不能用 `.` 替代**——必须显式写序列名。

## 6.13 sirilpy 在 CLI 下不可用

```
pyscript xxx.py
→ log: Python module is up-to-date
→ log: Traceback (most recent call last):
→ sirilpy.exceptions.SirilConnectionError: Error in _send_command():
  'NoneType' object has no attribute 'sendall'
```

`SirilInterface` 需要与 Siril 进程的 socket，CLI 模式下未建立。

**影响**：

- 无法用 `get_seq_regdata` 读变换 → 改解析 `.seq` 文本（见 03）
- RGB 背景均衡可显式使用原生 `stack -rgb_equal`；统计由标准库读取 FITS 平面，外部中位数仅作为预览后备，不执行通道增益。

## 6.14 禁用清单

### 命令

```
mpp  register_mpp  stack_mpp          # 1.5 新引擎
starnet  seqstarnet                  # 1.4 有，1.5 移除；本工具不需要
seqwcsbg  seqwcs                      # 从未存在
unload                                 # 应为 close
pjp                                    # PixInsight 命令，Siril 无
seqsetreg                              # ≤0.9 才有
stack_rl  stack_rbf                   # 不存在
atrous  seqatrous  rgbalign  ssr  detect_streaks
eqcrop  seqeqcrop  gps  seqgps  catmag  healpix  clear_mask
```

### Flag

```
-extref=                              # 1.5 新增
-debayer=                             # 1.5 有算法选择；1.4 只有裸 -debayer
-avi-bayer=  -engine=  -ap-step=  -shift-smooth=   # 1.5 mpp 专用
-async                                # pyscript，1.4 无
```

### seqsubsky 的 1.4 能力

```
seqsubsky sequencename { -rbf | degree } [-nodither] [-samples=20] [-tolerance=1.0] [-smooth=0.5] [-prefix=]
```

**没有** 1.5 的 `-auto` / `-border=` / `-random` / `-gradient` / `-mode=` / `-simplified` / `-downsample=`。序列背景提取能力显著弱于单图 `subsky`。本工具未使用背景提取。

### 转场（filter）能力边界

`-filter-quality` 只对行星 DFT / Kombat 注册有效；`-transf=` 只有 `shift` / `similarity` / `affine` / `homography`，**没有 `wcs`**。

需要 WCS 时走 `seqplatesolve` + `seqapplyreg`（1.3 引入的 astrometric registration），不是本工具的范围。

## 6.15 其它实测细节

### `savepng` 自加后缀

```
savepng /tmp/out.png   →   实际写入 /tmp/out.png.png
```

### `get -A` 可用于能力探测

列出全部可设置项，用于运行时探测。

### 退出码

Siril 未定义结构化退出码语义。任一命令失败 → 脚本停止 → 退出码非 0（实测 1）。**无法区分是哪一步失败**，因此必须抓 `Error in line N ('cmdname')` 日志行来定位。

### `failed to create new file (already exists?)` 不等于磁盘满

```
log: FITS error: failed to create new file (already exists?):
log: FITS error: c_<stem>_NNNN.fit
log: Sequence processing failed. Check the log.
```

这句容易被读成空间不足。**实测在磁盘空闲 65 GiB 时同样出现**，而目标文件往往已存在且大小正常——真正的含义是 Siril 无法创建输出文件（同名已存在、或写入被宿主策略/沙箱拦截），与磁盘余量无关。

排查顺序：①`df -h` 确认余量（排除真空间不足）；②`ls -la` 看目标文件是否已存在且非空；③看是否运行在受限宿主下（沙箱/文件策略会拦截写入）。**不要只看这一句就断定是磁盘问题**——真实案例中一次是空间耗尽、另一次是策略拦截，症状完全一样。

### 多帧序列的中途失败会留下半成品

上述错误发生在序列处理中途时，work 目录里已写出一批 `c_<stem>NNNN.fit`（本例 69 帧全部存在、大小正常），但整批仍是失败的。**不能因为文件都在就认为处理成功**——以 Siril 的 `Script execution failed.` 与本工具报告的 `failed_stage` 为准。重跑前必须清掉 work 目录，否则残留文件会让下一次 `stage_workdir` 之外的路径判断产生混淆。

### 配置文件写入失败（macOS 沙箱）

```
log: Could not save the settings in .../config.1.4.ini: Failed to rename file ...:
  g_rename() failed: Operation not permitted
```

在受限环境下属正常现象，不影响处理结果。

## 6.16 文档版本差异

`siril.readthedocs.io` 当前默认是 **1.5.0**，但 1.4 的命令在 1.5 中基本保留。**以本文件为准**，因为内容基于 1.4.4 实测。