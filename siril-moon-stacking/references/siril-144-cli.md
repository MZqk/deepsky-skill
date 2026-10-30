# Siril 1.4.4 CLI 事实与指令清单

本文件记录**本机实测与官方命令集核对**得到的语法与核心约束，是 `siril-moon-stacking` 调用 Siril CLI 的权威参考依据。

Python 命令使用入口文档中设置的 `TASK_SKILL_DIR`、`TASK_PYTHON` 和 `TASK_SIRIL`。

---

## 1. 调用契约与环境基础

| 项 | 值 | 说明 |
| :--- | :--- | :--- |
| **二进制路径** | `/Applications/Siril.app/Contents/MacOS/siril-cli` | macOS 默认未加入 PATH，需使用绝对路径调用 |
| **脚本执行语法** | `siril-cli -s <script.ssf> -d <cwd>` | 也支持标准输入 `siril-cli -s - -d <cwd>` |
| **脚本首行声明** | `requires 1.4.4` | **必须作为脚本第一行**；若缺失会直接报错拒绝执行后续命令 |
| **日志语言** | `LANG=C` | 执行前置 `LANG=C` 环境变量，避免中文日志在控制台出现乱码 |
| **工作目录隔离** | 必须由 `-d` 显式指定独立工作目录 | 避免污染原始输入数据，且防止沿用 Siril GUI 缓存的旧目录 |
| **多线程并行** | 启动时自动探测 CPU 逻辑核数 | 默认开启全核并行（如 M 系列芯片并行处理） |

---

## 2. 图像序列导入与 RAW 解码

### `convert`
```bash
convert basename [-debayer] [-fitseq] [-ser] [-start=index] [-out=dir]
```
* **RAW 原生解码**：使用 `-debayer` 选项可直接将相机 RAW 原片（如 `.ORF`, `.CR2`, `.NEF`, `.ARW`）转换为去马赛克后的 16-bit 3 通道 RGB FITS，并自动并行处理。
* **生成序列**：自动将当前目录或输出目录中符合条件的文件归并为 `<basename>.seq` 序列文件。

### `link`
```bash
link basename [-date] [-start=index] [-out=dir]
```
* **FITS 快速安全建序**：针对已有的 FITS 文件，通过符号链接建立序列，避免占用双倍磁盘空间，并安全隔离输入目录。

---

## 3. 配准与序列重采样 (`seqapplyreg`)

### 关键约束：为什么不能使用 `register` 配准月面？
* `register` 官方说明第一句即指出：*“Using stars for registration, this algorithm only works with deep sky images.”* 其算法完全依赖星点三角形匹配，对月面/行星/太阳地貌完全失效。
* Siril GUI 中的 Image Pattern Alignment 与 KOMBAT（行星配准）为图形界面框选专用，**未开放 CLI 入口**。

### 突破口：利用 `.seq` 文件的 `R0` 单应性矩阵由 `seqapplyreg` 接管
Siril 的 `.seq` 使用 `R0` 保存第 0 图层的单应性矩阵，RGB 默认继承。技能按 FITS 坐标约定生成矩阵，纵轴原点在下、向上递增。无旋转时，注入前的平移为 $H_{13}=-dx$、$H_{23}=+dy$：

```text
R0 1.0 1.0 1.0 0 0.0 1 H 1 0 -dx 0 1 +dy 0 0 1
```

有旋转时不能沿用该简化式。对于脚本中的旋转角 `theta` 和中心 `(cx, cy)`，`H` 的前两行为：

```text
cos(theta)   sin(theta)  cx*(1-cos(theta)) - cy*sin(theta) - dx
-sin(theta)  cos(theta)  cy*(1-cos(theta)) + cx*sin(theta) + dy
```

技能可能为避免 Siril 1.4.4 的零和矩阵拒绝，给所有矩阵的 $H_{13}$ 增加相同的输出原点偏移；相对变换不变。
* 随后调用 Siril 原生 `seqapplyreg`：
```bash
seqapplyreg sequencename -framing=min -interp=cu
```
* `-framing=min`：**自动取全序列所有帧的公共相交区域（Common Area）**，自动丢弃黑边；
* `-interp=cu`：调用 Siril 原生多线程**双三次（Bicubic）亚像素重采样**，彻底杜绝整像素取整模糊；
* 输出带有 `r_` 前缀的对齐序列（如 `r_moon_00001.fit` 及 `r_moon_.seq`）。

### 3.1 Drizzle 超采样（`-drizzle`）

```
seqapplyreg sequencename [-prefix=] [-scale=] [-layer=] [-framing=]
seqapplyreg sequencename ... [-interp=] [-noclamp]
seqapplyreg sequencename ... [-drizzle [-pixfrac=] [-kernel=] [-flat=]]
seqapplyreg sequencename ... [-filter-*]
```

实测（Siril 1.4.4，合成 3 帧 1024×512 mono 序列）：

| 命令 | 结果 |
| :--- | :--- |
| `seqapplyreg smoke_ -framing=min -interp=cu -scale=2.0 -drizzle -pixfrac=0.5 -kernel=square` | 输出 **2046×1024**（≈2×），日志 `Drizzling parameters: scale: 2.000000` |
| `seqapplyreg smoke_ -framing=min -interp=cu -scale=2.0`（无 `-drizzle`） | 同样输出 2046×1024（纯插值放大）→ **判断 drizzle 是否生效不能只看尺寸，要看日志的 `Drizzling parameters`** |
| `... -drizzle -kernel=lanczos3 -scale=2.0` | **不报错**（仅 "Max drizzle weight" 不同）→ 手册的 Lanczos 限制是建议而非硬约束，**必须由调用方主动回退为 `square`** |

* `seqapplydrizzle` 独立命令在本版本**不存在**（`Error: command seqapplydrizzle is not available`），只能用 `seqapplyreg -drizzle`；
* `-scale=` 范围 0.1~3.0，与 `-drizzle` 组合即 drizzle 的输出网格倍率；手册推荐 `pixfrac ≈ 1/scale`；
* **drizzle 会自动修正头部 `XPIXSZ`/`YPIXSZ`**：实测 3.76µm → 1.88µm（scale=2），且 `stack` 出的母版继承该值；`FOCALLEN`/`APERTURE` 原样透传。调用方仍应校验并在缺失时补写；
* drizzle 过程会在工作目录生成 `drizztmp/` 中间目录（8-bit 中间帧），需在收尾时清理；
* 代价：scale=2 时像素量/体积/耗时约 ×4，且噪声更高（需更多帧补偿）；null pixel 会削弱剔除算法的可用样本。

---

## 4. 序列堆叠 (`stack`)

### 4.1 分支语法（Siril 1.4.4 实测）

```bash
stack seqfilename { sum | min | max } [-output_norm] [-out=filename] [-maximize] [-upscale] [-32b]
stack seqfilename { med | median } [-nonorm, -norm=] [-fastnorm] [-rgb_equal] [-output_norm] [-out=filename] [-32b]
stack seqfilename { rej | mean } [rejection type] [sigma_low sigma_high] [-rejmap[s]] [-nonorm, -norm=] [-fastnorm] [-overlap_norm] [-weight={noise|wfwhm|nbstars|nbstack}] [-feather=] [-rgb_equal] [-output_norm] [-out=filename] [-maximize] [-upscale] [-32b]
```

* **`-weight=` 只在 `rej`/`mean` 分支可用**（`sum` 分支完全不支持，也不支持 `-norm=`）。
  * `noise`：按背景噪声给帧加权（**月面唯一可用**）；`wfwhm`/`nbstars` 依赖星点配准数据，月面不可用；`nbstack` 供 live stacking 使用。
  * 非法取值会明确报错：`Unknown argument to -weight=bogus, aborting.`；合法时日志打印 `Computing weights based on noise...` → `Image weighting ........... from noise`。
  * 若背景统计不可用（如画面完全均匀）会报 `MAD is null. Statistics cannot be computed.` 并导致归一化失败。

### 4.2 推荐参数组合（月面幸运成像）

```bash
stack r_moon_ rej w 3 3 -norm=addscale -weight=noise -filter-included -out=moon_master.fit
```
* `rej w 3 3`：Winsorized Sigma Clipping 像素剔除算法（标准推荐）；
* `-norm=addscale`：加法缩放归一化，平衡因视宁度或薄云引起的曝光浮动；
* `-weight=noise`：按背景噪声加权，让低噪帧贡献更多；
* `-filter-included`：仅堆叠在 `.seq` 文件中标记为选中（`I <index> 1`）的优质帧（**实测对 `sum` 分支同样生效**，日志只读取被标记的帧）；
* 输出 `moon_master.fit`：为 32 位浮点高动态、高信噪比母版。

### 4.3 `sum` 分支的两个实测缺陷（勿用于月面）

1. **按全图最大值归一化**：实测两次输出峰值恒为 `1.000000`，两区域（100/200）输出 `0.4926 / 0.9852`，即 `sum / max(sum)`。因此**单个热噪点、宇宙线或视频压缩坏块会让整幅信号塌缩**——注入一个 60000 的像素后输出变为 `0.004984 / 0.009967`（约 100×）。
2. **不做任何像素剔除**，也不支持 `-norm=` 与 `-weight=`。

另实测：`rej w 3 3 -norm=addscale` 的相对对比度与 `sum` 一致（2.0 vs 1.97），二者经后处理归一化后等价，而 `rej` 额外提供剔除、帧间归一化与加权能力。故本技能统一走 `rej`，`sum` 仅作 `--stack-method sum` 的显式回退。

> 注意：3 帧时 Winsorized 3σ 无法剔除单点异常（日志 `Pixel rejection in channel #0: 0.000%`），剔除需要足够的帧数。

### 4.4 技能的重采样与堆叠决策
由 Siril CLI 原生执行多线程重采样与高动态堆叠（插值路径严格保持 Clamping，绝不加 `-noclamp` 避免数值越界）：
```bash
# Generate and execute 02_align_stack.ssf
# Full disc: framing=min by default
# Mosaic panels: use --mosaic-mode tile for framing=max
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" stack --work /path/to/work --mosaic-mode disc --interp cu

# Drizzle: auto, off, or force scale 2 or 3
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" stack --work /path/to/work --drizzle auto
# Supply trusted optics explicitly when metadata is missing
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" stack --work /path/to/work --drizzle auto --focal 160 --pixel-size 2.9 --aperture 30
# Override the measured PSF threshold if needed
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" stack --work /path/to/work --drizzle-psf-threshold 2.5
```
* **重采样两条互斥路径**：
  * 未启用 Drizzle 时使用插值：`seqapplyreg -framing={min|max} -interp={cu|li} -filter-incl`；
  * Drizzle（`--drizzle` 启用时）：`seqapplyreg -framing={min|max} -scale={2|3} -drizzle -pixfrac={1/scale} -kernel=square -filter-incl`，**自动去掉 `-interp=`**。scale=2 时输出网格线性尺寸翻倍（512×256 → 1022×512），代价是约 4× 像素量、体积与耗时，以及更高的噪声（需更多帧补偿）；
  * `--drizzle-kernel` 默认 `square`（保通量、任意 scale 稳健）；`turbo` 更快但易留 null pixel（Moiré 花纹）；**Lanczos 核仅允许 `scale == pixfrac == 1.0`，用于超采样时会被自动降级为 `square`**；
  * `--mosaic-mode tile` 下 `auto` 默认关闭 drizzle（保持各面板采样尺度一致，避免与 `siril-mosaic` 自有的 `-scale` 冲突），需显式 `--drizzle 2` 才启用；
* **`auto` 的两条判据（按序尝试，全部可溯源）**：
  1. **衍射极限**（`--drizzle-airy-threshold`，默认 1.5px）：需要焦距 + 口径 + 像元三者齐备（来自 CLI 或 FITS 头）。理论艾里斑半径 `r = 1.22·λ·F#/px` 低于阈值即启用。**口径无法从图像反推**，故这条判据只在口径已知时可用；
  2. **实测 PSF 宽度**（`--drizzle-psf-threshold`，默认 2.0px）：任一元数据缺失时启用。对参考帧（`ranking.json` 选出的最锐帧）以月轮边缘扩散函数（ESF）实测有效 PSF 的 FWHM（`FWHM = 0.919 × w10-90`），低于阈值即启用。几何反推焦距是独立步骤，并非此测量的前提；PSF 判据**不需要光学元数据**；
  * 两条判据都无法成立时关闭 drizzle，并给出可操作的提示，**绝不使用默认口径 80mm / 默认像元 3.73µm 冒充真实值**；
  * 几何反推焦距的边界：要求**完整月盘在画面内**（局部特写 / 马赛克切片会失败），且没有可信像元尺寸时拒绝采纳反推焦距；这不阻止独立的 PSF 测量。月轮过曝时 ESF 顶部被削平会低估 FWHM，此时回执标注 `psf_possible_saturation`；
* **堆叠策略**：统一 `stack rej w {sigma} -norm={norm} -filter-included [-weight=noise]` 生成 32 位 `moon_master.fit`。`--weight {auto,none,noise}` 默认 `auto`（rej 路径启用噪声加权）；`--stack-method sum` 保留为显式回退（`sum` 无剔除、无加权、按最大值归一化）；
* **失败自愈**：`-weight=noise` 因背景统计不可用（`MAD is null` 等）失败时，自动去掉 `-weight=` 重跑一次；drizzle 运行失败且非强制时自动回退为插值路径重跑，两者均记入 `stack_receipt.json`；
* **母版像素尺度**：Siril 在 drizzle 时会自动把 `XPIXSZ` 除以缩放倍率；技能随后校验，若未修正则代为改写，并写入 `DRIZZLE`/`DRZSCALE`/`DRZPIXFR`/`DRZKRENL`/`ORIGXPIX` 与 `HISTORY`。`FOCALLEN` 作为物理量始终不动，因此 `siril-mosaic` 的 `seqplatesolve -focal= -pixelsize=` 自动获得正确尺度；
* 若指定 `--mosaic-mode tile`，自动默认启用最大画幅（`framing=max` 并带 `-maximize` 标记），最大化保留与邻近切片的重叠对齐特征，杜绝误裁切；
* `stack_receipt.json` 记录 `px_scale`、`weight`、`drizzle` 决策明细与 `drizzle.header` 校验结果，供 postprocess 与 verify 消费。

---

## 5. 后处理与细节重构

### 小波变换与重构 (`wavelet` / `wrecons`)
月面幸运成像叠加后的原片因大气点扩散函数（PSF）平滑，视觉上偏软，**必须依靠小波反卷积恢复高频细节**：
```bash
wavelet nbr_layers type
wrecons c1 c2 c3 ...
```
* `wavelet 5 2`：使用 B-Spline（`type=2`）算法分解为四个细节层和一个低频残差；
* `wrecons 1.5 1.4 1.3 1.1 1.0`：四个细节层的请求增益及单位残差，共五个有效系数；第六个系数被 Siril 1.4.4 忽略，不应生成；
* `extract 5`：输出 `layer00.fit` 至 `layer04.fit`，细节包含负值，最后一层为残差；实测逐层相加重建输入，误差 ≤ `1e-6`。技能默认对基准与反卷积后图像分别提取；关闭反卷积时复用同次提取。层数按图像尺寸限制，临时目录隔离并自动清理。
* 可结合 `unsharp 1.5 0.5` 进一步增强微对比度；这些命令示例不构成安全参数推荐，技能默认仍旁路 USM，并对手动参数执行完整链路护栏。

技能在反卷积前以相同 MTF 生成 `moon_unsharpened.fit`，原有反卷积后基准继续用于阶段诊断。默认 `--wavelet-adapt auto` 在 Python 中按基准统计分配带符号细节增量，按 Drizzle 的对数尺度映射四个原生增益；残差不增强，CLAHE/USM 如有请求则在重构后单独运行。`--wavelet-adapt off` 恢复上述全局重构路径。反卷积只执行一次，Python 护栏选择通过检查的最高增量比例（`1 / 0.75 / 0.5 / 0.25`，否则 `0`），并用持久收益区域检查实际 TIFF。

```bash
# Full-chain protection also applies to explicit sharpening settings.
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/work --sharp-mode crisp --sharp-guard auto

# Disable local ringing damping while retaining full-chain protection.
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/work --anti-ringing off

# Explicitly bypass the full-chain guard; existing local damping settings still apply.
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/work --sharp-guard off
```

### 色彩增强与矿物月 (`satu` / `rmgreen` / `rgbcomp`)
```bash
rmgreen 0
satu 0.7 1.2
satu 0.4 1.0

# LRGB 明度/色度分离合成 (Siril 1.4.4 原生)
rgbcomp -lum=moon_lum_sharp moon_color_clean -out=moon_natural_master
rgbcomp -lum=moon_lum_sharp moon_color_sat -out=moon_mineral_master
```
* `rmgreen 0`：使用平均中性保护（type=0，默认保留明度）抑制由于 Bayer 阵列传感器感光特性产生的绿色偏色；
* `satu amount background_factor`：针对月海地质成分（富钛玄武岩的蓝灰区与贫钛富铁高地的橙黄区）进行饱和度渐进式提升，背景噪点自动阈值保护；
* `rgbcomp -lum=lum_img color_img -out=result`：将纯净高频明度层与平滑色度层合成，输出零色噪的高清成片。

上述为 Siril 原生命令示例。技能实际在护栏选定明度后统一生成成片：自然色沿用亮度比合成、soft-knee 与月缘处理；自然风格矿物月使用 Siril `rgbcomp`，深影调矿物月使用既有浮点渲染公式。对应 FITS 与显示文件共用同一获选结果，显示几何单独记录在 `mosaic_tile_info.json`。

### 成果导出 (`savejpg` / `savetif`)
```bash
savejpg moon_natural.jpg 95
savetif moon_natural.tif -astro
savejpg moon_mineral.jpg 95
```
* 直接利用 Siril 原生 ICC 颜色管理与高质量图像编码器输出成片。
