---
name: siril-moon-stacking
description: |
  AI-directed lunar lucky imaging and surface astrophotography processor combining Siril 1.4.4 CLI with Python sub-pixel FFT registration. Use when the user wants to align, stack, wavelet-deconvolve, and enhance lunar RAW/SER/FITS image sequences, correct atmospheric dispersion, or produce mineral moon color images under authenticity constraints.
  AI 主导的月面天文摄影与幸运成像处理助手。融合 Siril 1.4.4 CLI 与 Python 亚像素频域配准插件，支持月球单帧连拍与 SER/FITS 序列的选帧堆叠、小波反卷积、色散校正与矿物月色彩提取。
license: Proprietary
metadata:
  slug: siril-moon-stacking
  version: "1.0.2"
  displayName: Siril Moon Stacking
  summary: AI 主导的月面天文摄影与幸运成像处理助手，融合 Siril 1.4.4 CLI 与亚像素频域配准。
  tags: [astronomy, lunar, siril, lucky-imaging]
  homepage: https://github.com/MZqk/deepsky-skill
---

# Siril Moon Stacking

## 1. 定位与设计原则

本 Skill 是 **“Siril 原生主干 + Python 极简配准插件”** 的专业月面后期解决方案。

* **Siril 的职责（主干）**：
  * 相机 RAW 原生多线程解码与去马赛克（`convert -debayer` / 支持 `split_cfa`, `seqsplit_cfa`）；
  * 序列管理与安全工作目录隔离；
  * 基于单应性矩阵的多线程双三次（Bicubic）亚像素重采样与构图自适应（`seqapplyreg -framing=min/max -interp=cu`）；
  * **按位深自适应堆叠**：8-bit（AVI/SER）采用加法叠加（`stack sum`）物理扩展动态范围；16-bit+（FITS/16-bit SER）采用带剔除的平均值叠加（`stack rej w 3 3`），严禁中位数/最值叠加；
  * **物理艾里斑 Airy Disc 反卷积**（`makepsf manual -airy` + `sb -iters=2` Split Bregman / Wiener / RL），针对月面高斯噪声模型消除环形山暗环伪影；
  * 自适应行星通道中值传递函数（`mtf`）与双曲拉伸（`asinh` / `ght`）校正白平衡与底噪偏置；
  * 局部对比度自适应增强（`clahe`）；
  * 'à trous' B-Spline 多尺度小波细节重构（`wavelet` + `wrecons`）与微反差锐化（`unsharp`）；
  * 色散校正与矿物月饱和度渐进式提升（`rmgreen 1 0.8` + `satu`）；
  * 32 位 FITS 母版、16 位 TIFF 母版及高质量 JPG 导出。
* **Python 的职责（插件）**：
  * 智能月相感知与高反差特征地貌定位（终结者明暗线/环形山密集区，抗月相干扰）；
  * 基于 Hann 加窗的 FFT 亚像素频域相位相关位移计算；
  * 视宁度与云雾置信度评分，帧数感知动态挑选（小样本自动扩充比例，保证高信噪比）；
  * 向 Siril 的 `.seq` 序列文件注入 `R0` 单应性变换矩阵与选帧状态；
  * **大气色散亚像素校正 (ADC)**：在母版后处理阶段自动对齐 R/B 通道至 G 通道，消除月面断崖与月轮外边缘的红蓝伪色彩边缘。

## 2. 真实性红线

1. **零生成式造假**：严禁使用 AI 扩散模型、超分辨率生成等算法无中生有地编造月坑、裂谷或月面纹理；所有细节恢复必须且只能来自小波反卷积对光学和视宁度低通特性的数学还原。
2. **矿物月真实性约束**：色彩饱和度拉伸必须基于传感器捕获到的微小光谱反照率差异，严禁随意人工着色或伪造不存在的月海边界。
3. **安全隔离**：绝对禁止在用户原始图像目录直接生成、修改或覆盖数据，必须使用独立的临时工作目录。

---

## 3. 核心处理流程（5 步闭环）

### Step 1: 环境探测与数据导入
运行诊断探测并导入原始序列：
```bash
# 检查 Siril CLI 与依赖
python scripts/moon_stack.py probe

# 扫描并导入数据（自动区分 RAW 与 FITS，并在工作目录生成 01_import.ssf）
python scripts/moon_stack.py import --input /path/to/moon_raws --work /path/to/work
```

### Step 2: 智能选帧与刚体亚像素配准
自动识别月面反差特征，通过双锚点互相关解算亚像素视场旋转角 $\theta$ 与平移 $(dx, dy)$：
```bash
# 智能选帧（默认基于参考帧质量运行 Otsu 双峰自动聚类；亦可选用 --select-mode utility/mtf-snr 联合效用模型）
python scripts/moon_stack.py register --work /path/to/work

# 可选：选用物理级 MTF-SNR 联合效用模型（平衡高频对比度与信噪比，支持 --utility-alpha / --utility-beta 权重调校）
python scripts/moon_stack.py register --work /path/to/work --select-mode utility --utility-alpha 2.0 --utility-beta 1.0
```
* **选帧模式一览**：
  * `otsu`（**默认推荐**）：Otsu 自适应双峰聚类，自动判定当晚平静视宁度临界断崖；
  * `utility` / `mtf-snr`：**MTF-SNR 联合效用模型**，求导最大化 $U(k) = \bar{Q}(k)^\alpha \cdot \sqrt{k/N}^\beta$，严格平衡清晰度衰减与降噪增益；
  * `relative`：按参考帧质量相对阈值筛选（`--quality-threshold 0.75`）；
  * `percent`：固定/经验分级百分比（`--keep-percent <val>`）。
* Python 会自动生成包含 `R0 ... H cosθ -sinθ h13 sinθ cosθ h23 0 0 1` 刚体单应性矩阵（遵循 FITS 图像原点在左下角的坐标系约定）及 `I <index> 1/0` 选帧标记的 Siril 标准 `.seq` 文件，彻底根除长间隔连拍带来的月盘外围视旋转模糊与劣质帧污染。

### Step 3: Siril 原生插值重采样与堆叠
由 Siril CLI 原生执行多线程重采样与高动态堆叠（严格保持 Clamping，绝不加 `-noclamp` 避免数值越界）：
```bash
# 自动生成并执行 02_align_stack.ssf
# 模式 A（默认高锐度）：--interp cu（双三次插值，追求光学极限 MTF，配合后续小波 L1 抑制）
# 模式 B（稳健防过冲）：--interp li（双线性插值，凸组合绝不过冲，彻底根除环形山边缘振铃暗环）
python scripts/moon_stack.py stack --work /path/to/work --framing min --interp cu
```
* Siril 调用 `seqapplyreg -framing=min -interp={cu|li} -filter-incl` 完成亚像素重采样并统一裁切；
* Siril 调用 `stack rej w 3 3 -norm=addscale -filter-included` 生成 32 位 `moon_master.fit`。

### Step 4: ADC 大气色散对齐与插值联动小波重构
自动执行通道亚像素对准、Airy 物理反卷积与插值自适应细节重构：
```bash
# 自动生成并执行 03_postprocess.ssf
python scripts/moon_stack.py postprocess --work /path/to/work --deconv sb --aperture 80 --focal 400
```
* 自动执行：
  * **ADC 亚像素通道对齐**：校准 R/B 相对 G 的空间偏移，消除边缘红蓝伪彩色彩边；
  * **物理 Airy PSF + Split Bregman 去卷积**：还原光学低通弥散，消灭亮缘黑环暗斑；
  * **高光保护自适应拉伸**：中值自适应保留高光动态余量，绝无死白溢出；
  * **插值感知联动小波重构 (Interp-Aware Wavelet Tuning)**：
    * **时序先于 CLAHE**：坚决在拉伸后直接执行小波分解，杜绝 CLAHE 预先放大平坦月海背景噪声并污染小波高频细节层；
    * 若前置使用 `--interp li`（双线性）：小波第 1 层自动放宽至 `1.10`（`wrecons 1.10 1.22 1.25 ...`），补偿双线性高频滚降，兼具极高清晰度与零振铃；
    * 若前置使用 `--interp cu`（双三次）：小波第 1 层自动锁定抑制在 `1.05`（`wrecons 1.05 1.20 1.25 ...`），过滤 Bicubic 负旁瓣引起的微过冲；
    * 支持通过 `--wavelet-l1 <val>` 显式微调第 1 层系数；
  * **轻量化局部自适应对比度 (Post-Wavelet CLAHE)**：
    * 移至小波重构之后执行，针对已确立的干净断崖地貌做宏观反差烘托；默认采用保守的 `--clahe-clip 1.0`（从激进的 1.5 调低，杜绝月海沙砾浮噪），支持传入 `<=0` 完全旁路禁用。
* 自动生成产物：
  * `moon_master.fit`：32 位未锐化母版；
  * `moon_natural.tif`：16 位小波细节母版；
  * `moon_natural.jpg`：高清晰度自然影调成果图；
  * `moon_mineral.jpg`：多彩矿物月地质成果图。

### Step 5: 质检报告与审查
```bash
python scripts/moon_stack.py verify --work /path/to/work
```
* 打印信噪比改善倍数、锐度提升倍数以及图像统计信息。

> **一键执行模式**：
> 也可以直接使用 `all` 命令一步完成全套流水线：
> ```bash
> python scripts/moon_stack.py all --input /path/to/moon_raws --work /path/to/work --deconv sb
> ```

---

## 4. 视宁度与月相自适应决策表

| 月相或拍摄条件 | 视宁度预估 | 建议保留率 (`--keep-percent`) | 锚点特征定位建议 | 适用后处理倾向 |
| :--- | :--- | :--- | :--- | :--- |
| **满月 (Full Moon)** | 普遍中等 | 30% ~ 40% | 自动定位第谷/哥白尼辐射纹高频区 | 极佳的矿物月制作时机，中度小波重构 |
| **上弦/下弦/凸月** | 良好 | 40% ~ 50% | 自动锁定明暗交界线（Terminator）断崖群 | 激进小波第 1~3 层重构，凸显立体月坑阴影 |
| **娥眉月 (Crescent)** | 视宁度常较差（低仰角） | 10% ~ 20% | 自动加窗于亮月牙中段，抑制大面积背景黑边 | 保守小波重构，配合中度 unsharp，防止颗粒噪声 |
| **薄云/经纬仪大漂移** | 气流不稳定 | 15% ~ 25% | 启用置信度强过滤，自动剔除漂移过大或云雾帧 | 标准自然细节版为主 |

---

## 5. 故障排查与恢复 (Troubleshooting)

1. **报错：`Cannot load a memory-mapped image: BZERO/BSCALE ...`**：
   * 已在此架构中强制统一使用 `memmap=False` 规避，保证与 Astropy 7.x 完美兼容。
2. **报错：`common area too small after alignment`**：
   * 说明序列中有个别帧发生了镜头严重抖动或出框。本插件已内置置信度阈值过滤，会自动丢弃此类异常帧；如仍出现，可降低 `--keep-percent`。
3. **Siril SSF 脚本独立调试**：
   * 所有步骤均在工作目录的 `logs/` 下保留了对应的 `01_import.ssf`、`02_align_stack.ssf`、`03_postprocess.ssf` 与日志。用户或智能体可直接使用 Siril GUI 载入单步排查。
4. **月面重影分层同心弧 (Concentric Ghosting Limb)**：
   * 原因：FITS 图像坐标系原点位于左下角，纵轴向上递增；而 NumPy 二维数组索引按行向下递增。若将向下位移（`+dy`）直接取反注入 Siril 的 `R0` 单应矩阵 $H_{23}$，会导致帧向相反方向错位，使经纬仪漂移误差翻倍（形成梯田状分层重影）。
   * 规范：Siril 单应矩阵垂直分量 $H_{23}$ 必须为 `+dy`。
5. **月面偏绿与过曝/欠拉伸 (Green Cast & Overexposure)**：
   * 原因：拜尔阵列（RGGB）绿光通道灵敏度偏高，且相机存在基底偏置（Black Pedestal）；深空常用的 `autostretch` 默认以 80% 黑背景为参考将其拉伸至 0.25 灰度，导致行星/月面高光剧烈溢出。
   * 规范：采用月面专用的通道独立 `mtf` 动态截取背景偏置并匹配高光，结合 `rmgreen` 消除色相偏色，再进行小波细节恢复。
6. **环形山边缘硬振铃/白边暗环防御 (Overshoot & Ringing Prevention)**：
   * 原因：双三次（Bicubic）插值核在阶跃边缘存在负旁瓣，对于阳光直射的亮坑壁与深邃阴影交界处易产生微过冲（白边）与下冲（黑圈）；若小波锐化过猛会被成倍放大。
   * 规范：
     * 极端高反差或追求绝对零伪影时，建议使用 `--interp li`（双线性稳健模式），并联动自动放宽小波第 1 层至 `1.10`；
     * 追求极限 MTF 分辨力时使用 `--interp cu`（双三次模式），并严格锁定小波第 1 层在 `1.05` 抑制过冲；
     * 无论何种模式，Siril `seqapplyreg` 均严格保持默认 Clamping，绝不使用 `-noclamp`。
