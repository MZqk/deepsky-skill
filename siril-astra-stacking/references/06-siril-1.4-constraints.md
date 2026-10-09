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

## 6.6 `-framing=max` 依赖 drizzle 权重

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

## 6.7 静默降级清单（最危险）

这些**只记日志、不报错、不中断**：

| 消息 | 后果 |
|---|---|
| `Cannot upscale or maximize framing with median stacking. Disabling` | median + maximize 时拼接失效，图被裁剪，无任何提示 |
| `Cannot compute overlap statistics if -maximize is not enabled. Disabling` | `-overlap_norm` 静默失效 |
| `-filter-quality` 在星点注册下 | `quality` 恒为 0，filter 是空操作 |

对策：生成期静态校验拦截前两项；第三项在参数层直接不生成该 flag。

## 6.8 drizzle 的约束

```
Cannot use drizzle on non-bayer sensors, aborting.
Cannot drizzle sequences with images of different sizes, aborting.
Applying interpolation on a sequence opened as CFA is a bad idea.
Drizzle: CFA pattern mismatch between reference image and image #%d, using reference pattern.
```

**drizzle 与普通插值互斥**，这就是两条 CFA 链路必须二选一的原因。

官方补充：

> `Note: when using -drizzle on images taken with a color camera, the input images must not be debayered. In that case, star detection will always occur on the green pixels`

## 6.9 `-minpairs` 默认 8

```
log: Not enough star pairs (7): Image 1 skipped
log: There are not enough stars in reference image to perform alignment
```

Siril 默认要求 8 对星。小视场智能望远镜数据常只有 6–7 颗星，导致**全部帧被静默丢弃**，最终报 `No image was registered to the reference`。

本工具按设备 profile 用 `min_pairs=4`。

星点检测在**绿通道**（`Findstar: processing for channel 1`）——合成数据若绿通道信号弱会检测不到星。

## 6.10 脚本文件的限制

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

## 6.11 不可脚本化的命令（scriptable=0）

```
setmag  seqsetmag  sequnsetmag  unsetmag
load_seq  ls  dir  clear  show  visu  tilt
```

`load_seq` 不可用意味着序列名**不能用 `.` 替代**——必须显式写序列名。

## 6.12 sirilpy 在 CLI 下不可用

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

## 6.13 禁用清单

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

## 6.14 其它实测细节

### `savepng` 自加后缀

```
savepng /tmp/out.png   →   实际写入 /tmp/out.png.png
```

### `get -A` 可用于能力探测

列出全部可设置项，用于运行时探测。

### 退出码

Siril 未定义结构化退出码语义。任一命令失败 → 脚本停止 → 退出码非 0（实测 1）。**无法区分是哪一步失败**，因此必须抓 `Error in line N ('cmdname')` 日志行来定位。

### 配置文件写入失败（macOS 沙箱）

```
log: Could not save the settings in .../config.1.4.ini: Failed to rename file ...:
  g_rename() failed: Operation not permitted
```

在受限环境下属正常现象，不影响处理结果。

## 6.15 文档版本差异

`siril.readthedocs.io` 当前默认是 **1.5.0**，但 1.4 的命令在 1.5 中基本保留。**以本文件为准**，因为内容基于 1.4.4 实测。