# Siril 1.4.4 CLI 事实与指令清单

本文件记录**本机实测与官方命令集核对**得到的语法与核心约束，是 `siril-moon-stacking` 调用 Siril CLI 的权威参考依据。

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
Siril 的序列文件 `.seq` 原生支持存储图像单应性变换矩阵（Homography）。在序列条目后写入：
```text
R0 1.0 1.0 1.0 0 0.0 1 H 1 0 -dx 0 1 -dy 0 0 1
```
* `R0`：表示应用于第 0 图层（RGB 图像默认继承）；
* `H 1 0 -dx 0 1 -dy 0 0 1`：为 3×3 变换矩阵，其中平移分量 $H_{13} = -\Delta x, H_{23} = -\Delta y$；
* 随后调用 Siril 原生 `seqapplyreg`：
```bash
seqapplyreg sequencename -framing=min -interp=cu
```
* `-framing=min`：**自动取全序列所有帧的公共相交区域（Common Area）**，自动丢弃黑边；
* `-interp=cu`：调用 Siril 原生多线程**双三次（Bicubic）亚像素重采样**，彻底杜绝整像素取整模糊；
* 输出带有 `r_` 前缀的对齐序列（如 `r_moon_00001.fit` 及 `r_moon_.seq`）。

---

## 4. 序列堆叠 (`stack`)

```bash
stack seqfilename rej [rejection_type] [sigma_low sigma_high] [-norm=norm_type] [-filter-included] [-out=filename]
```
* **推荐参数组合（月面幸运成像）**：
  ```bash
  stack r_moon_ rej w 3 3 -norm=addscale -filter-included -out=moon_master.fit
  ```
  * `rej w 3 3`：Winsorized Sigma Clipping 像素剔除算法（标准推荐）；
  * `-norm=addscale`：加法缩放归一化，平衡因视宁度或薄云引起的曝光浮动；
  * `-filter-included`：仅堆叠在 `.seq` 文件中标记为选中（`I <index> 1`）的优质帧；
  * 输出 `moon_master.fit`：为 32 位浮点高动态、高信噪比母版。

---

## 5. 后处理与细节重构

### 小波变换与重构 (`wavelet` / `wrecons`)
月面幸运成像叠加后的原片因大气点扩散函数（PSF）平滑，视觉上偏软，**必须依靠小波反卷积恢复高频细节**：
```bash
wavelet nbr_layers type
wrecons c1 c2 c3 ...
```
* `wavelet 5 2`：使用 B-Spline（`type=2`）算法进行 5 层 'à trous' 小波分解；
* `wrecons 1.5 1.4 1.3 1.1 1.0 1.0`：根据尺度赋予各层权重，大幅提升第 1~3 层（微小月坑、环形山边缘锐度），并适度保护底层平滑；
* 可结合 `unsharp 1.5 0.5` 进一步增强微对比度。

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

### 成果导出 (`savejpg` / `savetif`)
```bash
savejpg moon_natural.jpg 95
savetif moon_natural.tif -astro
savejpg moon_mineral.jpg 95
```
* 直接利用 Siril 原生 ICC 颜色管理与高质量图像编码器输出成片。
