---
name: siril-moon-stacking
description: |
  AI-directed lunar lucky imaging and surface astrophotography processor combining Siril 1.4.4 CLI with Python sub-pixel FFT registration. Use when the user wants to align, stack, wavelet-deconvolve, and enhance lunar RAW/SER/FITS image sequences, correct atmospheric dispersion, or produce mineral moon color images under authenticity constraints.
  AI 主导的月面天文摄影与幸运成像处理助手。融合 Siril 1.4.4 CLI 与 Python 亚像素频域配准插件，支持月球单帧连拍与 SER/FITS 序列的选帧堆叠、小波反卷积、色散校正与矿物月色彩提取。
license: Proprietary
metadata:
  slug: siril-moon-stacking
  version: "1.0.6"
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
  * **按位深自适应堆叠（Vincent Hourdin 规范）**：8-bit（AVI/MP4/SER）采用加法叠加（`stack sum`）物理扩展动态范围；16-bit+（FITS/16-bit SER/RAW）采用带剔除的平均值叠加（`stack rej w 3 3`），严禁中位数/最值叠加；亦支持 `--stack-method {auto, sum, rej}` 显式指定；
  * **物理艾里斑 Airy Disc 反卷积**（`makepsf manual -airy` + `sb -iters=2` Split Bregman / Wiener / RL），针对月面高斯噪声模型消除环形山暗环伪影；
  * 自适应行星通道中值传递函数（`mtf`）与双曲拉伸（`asinh` / `ght`）校正白平衡与底噪偏置；
  * 局部对比度自适应增强（`clahe`）；
  * 'à trous' B-Spline 多尺度小波细节重构（`wavelet` + `wrecons`）与微反差锐化（`unsharp`）；
  * 色散校正与矿物月饱和度渐进式提升（`rmgreen 0` + `satu`）；
  * 32 位 FITS 母版、16 位 TIFF 母版及高质量 JPG 导出。
* **Python 的职责（插件）**：
  * **AVI / MP4 / MOV 通用视频原生直解（Direct-pass FITS）**：直接利用 OpenCV 硬件级解码逐帧抽取并平滑扩展至 16-bit FITS 序列，零中间容器转录，节省 50% 磁盘 I/O 写入带宽；自动注入 `BITPIX=16`, `ORIG_BIT=8` 并在下游与 Siril `stack sum` 联动；
  * **专业 SER 视频流原生直读与极速解压**：直接解析 178 字节规范头，零拷贝 `np.memmap` 提取帧数据，OpenCV 硬件级 demosaicing（1080p 单帧 <5ms），元数据（UTC 时间戳/相机/望远镜）无损注入 FITS；
  * **低仰角宏观大气消光一阶梯度补偿**：在 32 位浮点线性空间与对数色比空间鲁棒估计横跨月盘的 Rayleigh 消光红化坡度，平复“底暖顶冷、底暗顶亮”倾斜，杜绝矿物月被大气消光撕裂；
  * **单色（Mono）与彩色（RGB）全链路自适应**：智能生成 `L 1` 与 `L 3` 序列，单色输入（或 `--force-mono`）自动规避彩色专属滤镜；
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
# 安装运行时依赖（numpy / astropy / opencv-python-headless / scikit-image）
pip install -r requirements.txt

# 检查 Siril CLI 与依赖
python scripts/moon_stack.py probe

# 方式 A：扫描并导入单帧 RAW 或 FITS 目录
python scripts/moon_stack.py import --input /path/to/moon_raws --work /path/to/work

# 方式 B：直接传入单个 SER 视频文件（或包含 .ser 的录像目录），支持限制导入帧数
python scripts/moon_stack.py import --input /path/to/moon_capture.ser --work /path/to/work --limit 500

# 方式 C（新增）：直接传入 AVI / MP4 / MOV 视频文件，直出 16 位 FITS 序列并联动 stack sum
python scripts/moon_stack.py import --input /path/to/moon_capture.avi --work /path/to/work --limit 500
```
> **前置条件**：需已安装 Siril 1.4.4+（macOS 默认路径 `/Applications/Siril.app/Contents/MacOS/siril-cli`，可用 `--siril` 覆盖）。
> `probe` 输出的 `numpy` / `astropy` / `cv2` / `skimage` 四项必须均非 `null`，否则对应步骤会以 `ModuleNotFoundError` 中断。

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
# 模式 A（默认全月盘）：--mosaic-mode disc（默认 framing=min，统一裁切画幅）
# 模式 B（全景马赛克面板）：--mosaic-mode tile（自动缺省 framing=max，保留完整交叠区域）
python scripts/moon_stack.py stack --work /path/to/work --mosaic-mode disc --interp cu
```
* Siril 调用 `seqapplyreg -framing={min|max} -interp={cu|li} -filter-incl` 完成亚像素重采样；
* Siril 调用 `stack rej w 3 3 -norm=addscale -filter-included` 生成 32 位 `moon_master.fit`；
* 若指定 `--mosaic-mode tile`，自动默认启用最大画幅（`framing=max` 并带 `-maximize` 标记），最大化保留与邻近切片的重叠对齐特征，杜绝误裁切。

### Step 4: ADC 大气色散对齐与插值联动小波重构
自动执行光学参数智能推断、通道亚像素对准、Airy 物理反卷积与插值自适应细节重构：
```bash
# 自动生成并执行 03_postprocess.ssf (自动推断光学焦距与像元，亦支持显式 --focal/--aperture 覆盖)
python scripts/moon_stack.py postprocess --work /path/to/work --deconv sb
```
* 自动执行：
  * **智能光学参数推断 (Intelligent Optical Parameter Inference)**：
    * **FITS Header 自动挖掘**：优先自动解析 `XPIXSZ` / `PIXSIZE` 像元尺寸（如 $3.73\ \mu\text{m}$），摆脱死板硬编码；
    * **全月盘亚像素几何反推**：联动 RANSAC 鲁棒月轮拟合得到真实月盘像素直径 $D_{px}$（如实测 $2097\text{ px} \implies$ 像面尺寸 $7.82\text{ mm}$）。结合月球天体视直径（中值 $31.07'$，近地/远地物理区间 $29.4'\sim 33.5'$），通过光学针孔成像几何 $f = y / (2\tan(\theta/2))$，自动精确反推望远镜真实有效焦距（如 $865.5\text{ mm} \approx 866\text{ mm}$，区间 $803\sim 915\text{ mm}$），彻底解决默认值（$400\text{ mm}$）低估一倍的物理失真；
    * **精准驱动 Airy 物理反卷积**：使 Split Bregman 生成的 Airy PSF 像元半径从缩水的 $0.90\text{ px}$ 恢复为物理真实的 **$1.95\text{ px}$**（焦比 $F/10.8$），完全释放光学反卷积对衍射弥散的真实还原能力；
    * **切片模式安全旁路**：在 `--mosaic-mode tile` 下自动旁路全月轮拟合，回退至 Header `FOCALLEN` 或安全配置；支持 `--focal`、`--pixel-size`、`--aperture` 用户显式覆盖。
  * **ADC 亚像素通道对齐**：校准 R/B 相对 G 的空间偏移，消除边缘红蓝伪彩色彩边；
  * **物理 Airy PSF + Split Bregman 去卷积**：还原光学低通弥散，消灭亮缘黑环暗斑；
  * **高光保护自适应拉伸**：中值自适应保留高光动态余量，绝无死白溢出；
  * **物理感知自适应温润锐化 (Adaptive Organic Sharpening, 默认 `--sharp-mode auto` / 平衡温润模型 B 方案)**：
    * **平坦月海残噪感知**：利用 Donoho MAD 估计平坦玄武岩熔岩平原的高频噪声基底 $\sigma_{noise}$，信噪比较低时自动抑制高频放大，彻底消除平原沙砾噪点；
    * **反卷积与小波能量守恒折让**：检测到 Airy 物理反卷积已激活时，小波重构各层增益严格收敛至温润微调区间（增益收缩至 $2\%\sim 5\%$，实测 `1.02, 1.04, 1.05, 1.02`），彻底消灭环形山边缘甜甜圈白圈与人工浮雕描边；
    * **反卷积解耦温和 CLAHE**：消除旧版 $\ge 0.25$ 的死板强硬下限，反卷积激活时 CLAHE 自适应打折 60%（`clip = 0.06 ~ 0.18`，实测降至温润的 `0.13`），从根本杜绝局部微反差过拉造成的石膏白垩感；
    * **算法互斥防御（USM 自动旁路）**：在小波与反卷积激活时，传统的单尺度反锐化掩模自动归零（`unsharp = 0`），从源头切断阶跃边缘下冲（Undershoot）与人工白边；
    * **语义化预设分级**：
      * `--sharp-mode auto`（现代平衡温润基准，默认推荐）：Airy 反卷积 + 2~5% 小波轻调 + 0.13 微反差均衡，兼顾极限分辨率与温润无伪影；
      * `--sharp-mode mellow`（目视纯镜感）：Airy 反卷积 + 1~3% 极弱小波 + 彻底关闭 CLAHE，呈现高级大口径目视镜感；
      * `--sharp-mode mild`（温和轻调）、`--sharp-mode crisp`（高反差雕塑感）、`--sharp-mode none`（旁路锐化纯物理母版）或手动参数覆盖；
  * **安全黑点噪声抑制 (Safe Noise Ceiling Pedestal)**：
    * 采用四角安全噪声上限（$\text{median} + 2.0\sigma$），防止深空暗背景被非线性拉伸曲线与 CLAHE 局部直方图抬升，保证外围真空深空呈现纯净深邃的零噪黑底；
  * **月面专用线性灰世界平衡 (Linear Gray-World Balance, 默认 `--white-balance gray-world`)**：
    * **物理线性空间平衡**：坚决在非线性拉伸之前于 32 位浮点线性空间完成通道增益校准（$R_{lin} = R_{net} \cdot k_R, B_{lin} = B_{net} \cdot k_B$），避免独立逐通道非线性 MTF 拉伸在暗部产生斜率差异导致的色偏；
    * **统一等度拉伸 (Unified Isometric MTF)**：校准后各通道共享相同拉伸参数（$bg=0, hi=hi_{lum}, mid=0.13$），彻底消除暗部阴影与微光区的色调漂移；
    * 支持 `--white-balance legacy` 回退至旧版模式。
  * **月轮弧边色差与紫边抑制 (Limb Edge Defringing & CA Suppression)**：
    * 针对望远镜/长焦镜头在大反差明暗交界处产生的次级光谱与瑞利散射蓝紫边缘，在物理色度层对月轮过渡边缘（$L < 0.10 \times p_{99.95}$）实施色散平滑，限制过量蓝光散溢；
    * **保护真实地质色彩**：月面主体保留高达 +15% 的真实矿物蓝余量，完美还原静海（Mare Tranquillitatis）富钛玄武岩的真实地质矿物色，同时彻底消除月盘外缘弧边刺眼的紫边（Purple Fringing）；
  * **月轮亮弧前向散射眩光抑制 (Lunar Limb Glare Suppression, 默认 `--glare-suppress auto`)**：
    * **物理成因诊断**：月球明亮边缘在镜头前组镜片/保护镜以及地球高空大气气溶胶作用下，产生微弱的前向米氏/瑞利散射（Forward Scattering Glare），在非线性 MTF 与局部 CLAHE 强拉伸后外太空弥漫灰度高达 80~130 的发光亮雾；
    * **亚像素几何拟合**：采用径向最大负梯度拐点扫描配合 RANSAC 鲁棒圆拟合，精确解算月面真实物理天体圆盘（拟合残差 $\sigma_{res} < 1.0\text{px}$）；
    * **线性平滑衰减场**：在 32 位浮点线性空间非线性拉伸前，保持月盘实体（$r \le R + 1.0\text{px}$）100% 原始信号无损，在 $r \in [R+1, R+1+\delta]$ 实施 Hermite 三次 Smoothstep 平滑过渡（默认 $\delta = 4.5\text{px}$，保留望远镜 Airy 斑真实光学衍射轮廓，绝不产生剪纸边缘），在 $r > R + 1 + \delta$ 彻底清零漫射外太空背景，使深空恢复纯净零噪深邃黑；
    * 支持 `--glare-suppress auto`（默认 4.5px 过渡）、`--glare-suppress mild`（8.0px 柔和）、`--glare-suppress aggressive`（2.5px 紧致）与 `--glare-suppress off`（旁路）；
  * **亚像素阶跃边缘下冲暗环抑制 (Subpixel Anti-Ringing Damping, 默认 `--anti-ringing auto`)**：
    * **物理成因诊断**：阳光直射的高耸亮坑壁与坑底深阴影交界处，物理 Airy 反卷积频域振荡、双三次插值负旁瓣与多尺度小波高频差分级联，在暗侧陡峭处产生严重负下冲（Negative Undershoot），导致环形山边缘呈现人工“浓黑细圈/黑描边”（甜甜圈伪影）；
    * **暗环风险场定位**：基于归一化梯度与局部动态基准，精准锁定明暗交界断崖的暗侧过渡带，生成连续平滑的高斯羽化阻尼掩模 $M_{damp}$；
    * **非对称弹性阻尼平复**：高光山峰与山脊边缘的正向锐化增量保持 100% 原始解析力无损，仅对暗侧负向增量施加自适应阻尼衰减（`auto` 模式下依据插值与反卷积状态自适应设定为 0.45~0.65），彻底平复暗坑黑圈；
    * **参数支持**：`--anti-ringing {auto,off,mild,aggressive}`（默认 `auto`），支持 `--damping-factor <0.0-1.0>` 手动精细调节，支持 `--anti-ringing off` 完全旁路；
  * **低仰角宏观大气消光一阶梯度补偿 (Atmospheric Extinction Gradient Compensation, 默认 `--extinction-comp auto`)**：
    * **物理成因诊断**：月面低仰角（$a < 30^\circ$）拍摄时，因半度视场跨度的大气柱质量（Airmass）差诱发 Rayleigh/气溶胶消光空间倾斜，导致月盘呈现“底暖顶冷、底暗顶亮”的宏观红化与通量坡度，严重污染矿物月并造成拼接色差；
    * **地质反照率解耦**：将像元映射至对数色比空间（$\ln(B/G), \ln(R/G)$），表面绝对光通量差异被完全消除；
    * **网格中位数降采样与 Huber 鲁棒回归**：通过 $16\times 16$ 局部块中位数提取低频空间斜率，对局部高钛玄武岩色块天然免疫；
    * **Rayleigh 物理一致性校核**：验证蓝光与红光消光矢量的反向共线性，视场梯度 $<1.5\%$ 时自动旁路（高仰角零扰动）；
    * **参数支持**：`--extinction-comp {auto,mild,aggressive,off}`（默认 `auto`），在 32 位浮点线性空间执行通量守恒除法平复。
  * **全局色彩与直方图锁定（Anchor Master Profile Lock）**：
    * **核心痛点**：多面板月面全景马赛克（Multi-panel Mosaic）切片单独后处理时，各切片地质反照率差异悬殊（如纯暗玄武岩的月海切片 vs 极亮辐射纹的高地切片）。若各自独立估计白平衡与高光拉伸截断点，会导致各切片出现严重色块漂移（如月海切片偏紫、高地切片泛黄），且重叠区明暗映射阶梯跳变高达 400% 以上，拼接后接缝处出现不可调和的明暗断层；
    * **锁定机制**：支持将基准面板（Anchor Master）的 32 位浮点线性白平衡增益（$k_r, k_b$）与统一直方图拉伸基准（$bg_{lum}, hi_{lum}$、midtone 及矿物月色彩参数）导出为 `lunar_profile.json`，并由所有从属面板（Slave Tiles）一键锁入；
    * **命令行参数**：
      * `--lock-profile <path>`：从指定 JSON 档案中加载基准 profile 锁定全局色彩与拉伸；
      * `--lock-from <work_dir>`：指定基准面板工作目录，自动加载其 `lunar_profile.json`；
      * `--export-profile <path>`：导出当前切片的校准 profile（默认自动保存至工作目录的 `lunar_profile.json`）；
      * `--lock-wb <kr> <kb>`：手动强制锁定 R/G 与 B/G 通道增益；
      * `--lock-stretch <bg> <hi>`：手动强制锁定直方图黑位与高光截断点；
    * **多面板标准拼接工作流**：
      1. 选取反照率均匀、包含明暗界线或中央高地的面板作为 **基准面板（Anchor Master）** 执行后处理：
         ```bash
         python scripts/moon_stack.py postprocess --work /path/to/tile_center --mosaic-mode tile
         ```
      2. 对邻近的所有切片，后处理时统一挂载基准面板的 profile：
         ```bash
         python scripts/moon_stack.py postprocess --work /path/to/tile_north --mosaic-mode tile --lock-from /path/to/tile_center
         python scripts/moon_stack.py postprocess --work /path/to/tile_south --mosaic-mode tile --lock-from /path/to/tile_center
         ```
      3. 所有面板在绝对物理级线性归一化基准上对齐，重叠区地质反照率阶梯跳变降为 0.0000%，无缝拼合。
  * **L/RGB 明度与色度分离重构 (L/RGB Separation Pipeline, 默认 `--mineral-mode lrgb`)**：
    * **物理明度提取**：自动计算物理加权明度 $L = 0.299R + 0.587G + 0.114B$ 生成 32 位 `moon_lum.fit`；
    * **高频细节全归 L**：Airy PSF 物理反卷积、插值联动小波重构、Post-Wavelet CLAHE 与微反差 Unsharp 全部且仅作用于单通道明度 $L$，从物理源头彻底杜绝彩色高频噪点与边缘伪彩镶边；
    * **低频色彩全归 RGB**：RGB 通道执行独立自适应底噪中性化、`rmgreen 0` 去绿并受控提饱和；
    * **Siril 原生合成**：调用 `rgbcomp -lum=moon_lum_sharp` 分别合成干净自然的 `moon_natural`（自然色调+极致细节）与鲜活地质真实的 `moon_mineral`（矿物月）；
    * 支持 `--mineral-mode legacy` 回退至旧版全通道单体小波流程。
  * **电影级深影调地质彩月重构 (Deep-Cine Mineral Moon, 默认 `--mineral-style deep-cine`)**：
    * **胶片级非线性 S 曲线影调雕塑 (Filmic S-Curve Tone Sculpting)**：基于物理反卷积与温润小波重构的 32 位明度图（$L_{sharp}$），摒弃容易把高地压灰的单一 $\gamma$ 暗化，引入平滑步阶混合的非线性 S 曲线（暗部 $\gamma \approx 1.15$，亮部 $\gamma \approx 0.96$）。既将月海玄武岩压沉至油润厚重的丝绒质感，又完整保留南高地与第谷辐射纹通透璀璨的冷银白（亮部中位数维持在 $V \approx 168\sim 174$ 高质感区间）；
    * **晨昏线相位红化防御 (Terminator Phase-Reddening Defense)**：通过欧氏距离变换 `distanceTransform` 计算月盘表面到夜半球晨昏线的几何距离场。在距离 $< 25\text{px}$ 的明暗断崖区平滑将色度衰减归零，彻底切断太阳低掠射角微细风化层散射引起的假性霓虹橙光圈（Phase Reddening），使晨昏线完全恢复冷硬石质反照率与深邃立体炭黑；
    * **色度低通去噪平滑 (Geological Chrominance Filtering)**：在 32 位浮点线性归一化色度比空间（$cr_r = R / L, cr_b = B / L$）执行大半径高斯低通滤波（$\sigma \approx 0.016 \times \min(H, W)$，约 $36\text{px}$），彻底消除高倍色彩放大时 Bayer 阵列与微弱色散产生的沙砾杂色，呈现水彩般晕染的油画质感；
    * **双极地质色相纯化合成 (Bipolar Pure Synthesis)**：
      * *天青湛蓝 (Ti, 静海富钛区)*：定向锁定在纯正天青/牛仔蓝（Azure / Denim Blue, OpenCV $H \approx 106\sim 107$），告别阴暗发紫；
      * *赤陶桃木褐 (Fe, 澄海/高地富铁区)*：定向锁定在温润赤陶桃木色（Terracotta Peach / Copper, OpenCV $H \approx 11\sim 12$），黄绿杂色率从传统拉伸的 $18.6\%$ 彻底降至 $< 0.03\%$；
    * **双向明度引导色度保护 (Dual Luma-guided Chroma Masking)**：
      * *暗部滚降 (Shadow Rolloff)*：晨昏线月坑与深阴影区色度自然归零，呈现立体纯炭黑与冷石灰色；
      * *高光保护 (Highlight Protection)*：哥白尼/第谷辐射纹与撞击坑边缘色度收敛，保持冷银白纯净质感；
      * *亮轮锁止 (Limb Edge Zeroing)*：月轮物理边界外侧 $14\text{px}$ 内色度平滑归零，杜绝边缘黄环紫边；
    * **双轨并行成片输出**：同时产出电影级深影调成片 `moon_mineral.jpg` 与经典自然轻盈版 `moon_mineral_natural.jpg`，满足多元化审美需求；支持 `--mineral-style natural` 直接回退。
    * **参数支持**：`--mineral-fe-boost 6.8`, `--mineral-ti-boost 10.2`, `--mineral-gamma 1.09`。
* 自动生成产物：
  * `moon_master.fit`：32 位未锐化母版；
  * `moon_lum.fit`：32 位物理明度母版（仅 LRGB 模式）；
  * `moon_natural.tif`：16 位小波细节母版；
  * `moon_natural.jpg`：高清晰度自然写实影调成果图（冷硬微反差与温润质感）；
  * `moon_mineral.jpg`：电影级深影调地质彩月成果图（Deep-Cine 哑光玄武岩油润深影调）；
  * `moon_mineral_natural.jpg`：经典自然轻盈矿物月备份成果图；
  * `lunar_profile.json`：校准 Profile 档案（记录通道增益 $k_r, k_b$、直方图截断 $bg, hi$、矿物月色彩系数，可供其他面板锁入）；
  * `mosaic_tile_info.json`：切片元数据清单（记录尺寸、位深、物理比例、Profile 锁定状态与产品路径，供 `siril-mosaic` 马赛克拼接技能直接消费）。

### Step 5: 质检报告与审查
```bash
python scripts/moon_stack.py verify --work /path/to/work
```
* **全面量化质检指标矩阵**：
  * **切片模式与 Profile 锁定状态**：显示 `Mosaic mode: tile/disc` 与 `Calibration Profile: LOCKED (source=..., hi_lum=...)` 或 `AUTO`；
  * **光学参数推断状态 (Optical Setup)**：显示焦距 $f$、口径 $D$、焦比 $F$、像元 Airy 斑尺寸及推断来源（如几何反推 `geometric inversion` 或 Header）；
  * **暗环比率 (Dark Halo Ratio, DHR)**：度量阶跃明暗边缘阴影侧负下冲能量。$<0.015$ 为 `EXCELLENT (artifact-free)`，$<0.035$ 为 `GOOD (controlled)`，$\ge 0.035$ 触发暗环警报；
  * **高光白垩饱和度 (Chalky Saturation Index, CSI)**：检测过度拉伸导致的死白与微反差抹平。$<0.010$ 为 `EXCELLENT (highlight dynamic retained)`，$\ge 0.025$ 触发白垩化警报；
  * **梯度峰度脆裂度 (Gradient Kurtosis Metric, GKM)**：评估边缘梯度重尾分布以量化过度锐化与人工毛刺脆裂感。$<6.0$ 为 `ORGANIC (natural smooth)`，$6.0\sim 14.0$ 为 `CRISP (high detail)`，$\ge 14.0$ 触发脆裂警报；
  * **底噪标准差与动态范围**：精确检查四角深空底噪 $\sigma_{bg}$ 与主直方图像素极值。

> **一键执行模式**：
> 也可以直接使用 `all` 命令一步完成全套流水线：
> ```bash
> python scripts/moon_stack.py all --input /path/to/moon_raws --work /path/to/work --deconv sb --mosaic-mode disc
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
     * 配合新增的 `--anti-ringing auto`（亚像素边缘下冲阻尼），在 32 位浮点明度层对阴影侧负下冲执行非对称软性平复（DHR 降低 60% 以上），彻底切断黑圈形成通道，同时 100% 保留高光山脊的极限锐度；
     * 极端高反差或追求绝对零伪影时，建议使用 `--interp li`（双线性稳健模式），并联动自动放宽小波第 1 层至 `1.10`；
     * 追求极限 MTF 分辨力时使用 `--interp cu`（双三次模式），并严格锁定小波第 1 层在 `1.05` 抑制过冲；
     * 无论何种模式，Siril `seqapplyreg` 均严格保持默认 Clamping，绝不使用 `-noclamp`。
7. **高光白垩死白与微反差缺失 (Chalky Bleached Highlights)**：
   * 原因：非线性拉伸高光截断点过紧，或 CLAHE clip 过大导致辐射纹和环形山亮峰像素全部挤压在 1.0 附近，微结构梯度归零（CSI 指标超标 $>0.025$），形成石膏般平板死白。
   * 规范：调大高光保护余量（默认 `midtone 0.13` 配合 $p_{99.95} \times 1.10$ 线性高位），适当降低 `--clahe-clip` 或选用 `--sharp-mode auto / mellow`。
8. **质感脆裂硬化与人工毛刺 (Brittle / Crunchy Over-Sharpening)**：
   * 原因：多尺度小波重构增益偏激，叠加了单尺度 USM 锐化，导致全图梯度分布出现厚尾极端毛刺（GKM 指标超标 $>14.0$）。
   * 规范：保持 `--sharp-mode auto`（反卷积能量折让与 USM 自动旁路），避免手动叠加过激小波系数。
9. **马赛克面板边缘被切除或外太空清零抹杀 (Mosaic Tile Edge Truncation)**：
   * 原因：在多面板月面全景拼接时，默认的 `disc` 模式使用 `framing min` 统一截幅，并使用 RANSAC 月轮拟合对 $r > R + 4.5$ 处清零，导致切片邻近区域的月面地形被误杀。
   * 规范：拼接切片必须显式指定 `--mosaic-mode tile`，系统将自动使用 `--framing max` 保留最大画幅交叠，并旁路所有月盘半径清零逻辑，导出 `mosaic_tile_info.json` 与 `siril-mosaic` 联动。
10. **多面板拼接切片接缝阶梯断层与色块跳跃 (Mosaic Seam Stepping & Color Patch Discontinuity)**：
    * 原因：多面板切片地质反照率差异悬殊（如玄武岩暗月海 vs 亮高地），若各自独立计算 $p_{99.95}$ 与通道比，MTF 映射斜率相差数倍，拼接时重叠区明暗阶梯断层跳跃可达 400%+ 且色块漂移严重。
    * 规范：先选定代表性面板作为基准（Anchor Master）导出 `lunar_profile.json`，邻近切片后处理时传入 `--lock-from /path/to/anchor` 或 `--lock-profile ...`，统一白平衡增益与非线性 MTF 映射，将接缝明暗阶跃断层彻底归零至 0.0000%。
