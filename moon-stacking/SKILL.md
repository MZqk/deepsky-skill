---
name: moon-stacking
description: |
  AI 主导的月面天文摄影与幸运成像处理助手。专为月球单帧连拍、短曝光序列及月面全盘摄影设计。
  充分融合 Siril 1.4.4 CLI 官方原生能力（RAW 解码与去马赛克、序列管理、Winsorized 堆叠、
  多尺度 B-Spline 小波反卷积细节重构、矿物月色彩提取）与轻量级 Python 亚像素频域配准插件。
  支持将输入的相机 RAW（.ORF, .CR2, .NEF, .ARW 等）或 FITS/SER 序列处理为真实高解析力的
  自然细节版 JPG/TIFF 及地质信息丰富的矿物月色彩成片。
---

# Moon Stacking

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

### Step 2: 智能选帧与亚像素配准
自动识别月面反差特征，计算浮点亚像素偏移并评估帧质量：
```bash
# 挑选前 30% 最清晰帧（可选参数 --roi 1024 --keep-percent 30）
python scripts/moon_stack.py register --work /path/to/work --keep-percent 30
```
* Python 会自动生成包含 `R0 ... H 1 0 -dx 0 1 dy 0 0 1` 单应性矩阵（遵循 FITS 图像原点在左下角的坐标系约定）及 `I <index> 1/0` 选帧标记的 Siril 标准 `.seq` 文件。

### Step 3: Siril 原生插值重采样与堆叠
由 Siril CLI 原生执行多线程重采样与高动态堆叠：
```bash
# 自动生成并执行 02_align_stack.ssf
python scripts/moon_stack.py stack --work /path/to/work
```
* Siril 调用 `seqapplyreg -framing=min -interp=cu` 完成亚像素重采样并统一裁切；
* Siril 调用 `stack rej w 3 3 -norm=addscale -filter-included` 生成 32 位 `moon_master.fit`。

### Step 4: Siril 原生小波锐化与双版本交付
自动恢复视宁度模糊并提取矿物地质色彩：
```bash
# 自动生成并执行 03_postprocess.ssf
python scripts/moon_stack.py postprocess --work /path/to/work
```
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
> python scripts/moon_stack.py all --input /path/to/moon_raws --work /path/to/work --keep-percent 30
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
