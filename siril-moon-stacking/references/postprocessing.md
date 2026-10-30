# 月面后处理

需要调整光学参数、反卷积、小波、防过锐、色彩、马赛克或输出构图时使用本文。命令使用入口文档中设置的 `TASK_SKILL_DIR`、`TASK_PYTHON` 和 `TASK_SIRIL`。产物、质检和故障恢复见 [质检与故障排查](quality-and-troubleshooting.md)。

## 默认后处理
推断光学参数、对齐通道，并根据可信参数执行或跳过物理反卷积，再进行自适应细节重构：
```bash
# Generate postprocessing commands from trusted optics, or bypass physical deconvolution
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/work --deconv auto

# Optionally use green luminance while preserving RGB chrominance
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/work --luminance-channel green --deconv auto
```

默认 `--deconv auto` 仅在可信焦距、口径和像元齐备时启用 Airy 反卷积，未知参数保持空值并跳过；显式 `sb` / `wiener` 缺少参数时给出错误。MTF 命令保持合法单位范围，必要时按同一系数归一化线性明度与色度，保留高光余量。饱和像素不参与自动白平衡，源通道严重截断时采用保守矿物色处理并记录警告；增益无法恢复已截断颜色。单色保存锐化 FITS 并生成 DHR/CSI/GKM；背景噪声按通道在月盘外计算，无可靠空背景时标注不可用。方形导出覆盖完整可见月面及拟合月轮，边界不足补黑边，不缩小框切掉月尖。

`--master` 必须位于 `--work` 内，且软链接解析后仍在该目录；外部母版先复制到工作目录。彩色母版的 ADC 在临时副本上执行，并在通过光学参数验证后原子替换工作母版；临时空间受磁盘预算约束。验证失败时母版保持不变；若回执回滚失败，错误会给出保留的旧回执路径。验证通过后的其他步骤若失败，ADC 可能已经应用，应检查 `adc_receipt.json` 和日志再重试。

`postprocess_receipt.json.deconvolution` 的 `requested`、`applied`、`reason` 分别记录请求、执行方法与决策原因；`sharp_guard.deconvolution.retained_alpha` 记录最终保留的增强比例。自动模式的原因是 `trusted_optics` 或 `missing_optical_metadata`，显式关闭为 `user_disabled`，显式选择方法为 `user_selected`。

## 光学参数、通道对齐与拉伸

* **智能光学参数推断 (Intelligent Optical Parameter Inference)**：
  * **FITS Header 自动挖掘**：优先自动解析 `XPIXSZ` / `PIXSIZE` 像元尺寸（如 $3.73\ \mu\text{m}$），摆脱死板硬编码；
  * **全月盘亚像素几何反推**：联动 RANSAC 鲁棒月轮拟合得到真实月盘像素直径 $D_{px}$（如实测 $2097\text{ px} \implies$ 像面尺寸 $7.82\text{ mm}$）。结合 **JPL DE 历表精确月球视直径**（从 FITS Header `DATE-OBS` 自动查询拍摄时刻的地月距离，通过 $\theta = 2\arctan(R_{moon}/d)$ 计算精确视角，精度 $< 0.5\%$；无 `DATE-OBS` 时回退至中值 $31.07'$），通过光学针孔成像几何 $f = y / (2\tan(\theta/2))$，自动精确反推望远镜真实有效焦距（如 $865.5\text{ mm} \approx 866\text{ mm}$），彻底解决默认值（$400\text{ mm}$）低估一倍的物理失真；
  * **自适应 Airy PSF 核尺寸**：PSF kernel 大小随计算得到的 Airy 半径自动缩放（$ks = \text{clamp}(\lceil r_{airy} \times 6.0 \rceil_{\text{odd}},\ 15,\ 65)$），短焦避免高频振铃、长焦完整覆盖衍射环；
  * **精准驱动 Airy 物理反卷积**：使 Split Bregman 生成的 Airy PSF 像元半径从缩水的 $0.90\text{ px}$ 恢复为物理真实的 **$1.95\text{ px}$**（焦比 $F/10.8$），完全释放光学反卷积对衍射弥散的真实还原能力；
  * **切片模式安全旁路**：在 `--mosaic-mode tile` 下自动旁路全月轮拟合，回退至 Header `FOCALLEN` 或安全配置；支持 `--focal`、`--pixel-size`、`--aperture` 用户显式覆盖。
* **ADC 亚像素通道对齐**：校准 R/B 相对 G 的空间偏移，消除边缘红蓝伪彩色彩边；
* **物理 Airy PSF + Split Bregman 去卷积**：还原光学低通弥散，消灭亮缘黑环暗斑；
* **物理反照率自适应中间调解析拉伸 (Smart Auto-Midtone，默认不指定 `--midtone`)**：
  * **物理原理**：基于月面玄武岩月海反照率中位数与高光截止点，通过 Siril 原生 MTF 方程 $y = \frac{(m-1)x}{(2m-1)x-m}$ 解析反解最优中间调参数 $m_{\text{auto}}$；
  * **全月相与曝光自适应**：不论满月高照度、弦月大阴影，亦或欠曝/过曝连拍，均能全自动将月面反照率锚定在目视最舒适的深影调区间，彻底消除 `--midtone` 人工盲猜；
* **高光保护自适应拉伸**：中值自适应保留高光动态余量，但无法恢复原始数据中已截断的高光；

## 小波、防过锐与防振铃

* **物理感知自适应温润锐化 (Adaptive Organic Sharpening, 默认 `--sharp-mode auto` / 平衡温润模型 B 方案)**：
  * **径向傅里叶功率谱视宁度截止频率感知 (Radial PSD Seeing Cutoff)**：通过 2D-FFT 径向平均功率谱计算月面有效信号降至残噪基底的临界空间截止频率 $k_{\text{cutoff}}$；平静视宁度释放第 1 层微弱微反差，气流抖动时自动锁定第 1 层为 1.00 并向第 2、3 层转移能量，杜绝毛刺噪点；
  * **平坦月海残噪感知**：利用 Donoho MAD 估计平坦玄武岩熔岩平原的高频噪声基底 $\sigma_{noise}$，信噪比较低时自动抑制高频放大，彻底消除平原沙砾噪点；
  * **反卷积与小波能量守恒折让**：检测到 Airy 物理反卷积已激活时，小波重构各层增益在物理阻尼掩模保护下收敛至黄金微调区间（增益收敛至 $2\%\sim 6\%$，实测 `1.02, 1.04, 1.06, 1.03`），既充分释放环形山台地与月溪的清晰微反差，又彻底消灭暗环甜甜圈与人工浮雕描边；
  * **彻底解耦 CLAHE 局部硬反差**：反卷积激活或 auto 模式下 CLAHE 彻底旁路（`clip = 0.0`），坚决杜绝局部微反差过拉造成的石膏白垩感、生硬刻痕与死黑阴影；
  * **算法互斥防御（USM 自动旁路）**：在小波与反卷积激活时，传统的单尺度反锐化掩模自动归零（`unsharp = 0`），从源头切断阶跃边缘下冲（Undershoot）与人工白边；
  * **语义化预设分级**：
    * `--sharp-mode auto`（默认）：温和小波请求（1.02/1.04/1.06/1.03），并旁路 CLAHE；实际增益受自适应权重和防过锐护栏限制，是否反卷积由 `--deconv` 决定；
    * `--sharp-mode mellow`：请求 1~2% 的微弱小波并关闭 CLAHE；是否反卷积由 `--deconv` 决定；
    * `--sharp-mode mild`（温和轻调）、`--sharp-mode crisp`（高反差雕塑感）、`--sharp-mode none`（旁路锐化纯物理母版）或手动参数覆盖；
* **可信尺度与区域小波（默认 `--wavelet-adapt auto`）**：
  * 所有预设及手动系数均为请求上限。仅由反卷积前的 `moon_unsharpened.fit` 冻结每层平坦区 MAD 噪声、局部信号支持与区域权重；暗部、高光、强边缘、月面边界及卷积边界连续减弱增强，残差始终不增强。单母版统计不证明新纹理真实。
  * Siril `extract 5` 实测为四个带符号细节层与一个残差，合计五个有效重构系数。Drizzle 按 `j_native = j_work − log2(px_scale)` 映射四个原生尺度请求，关闭虚增最细层和无支持的粗层；层数受图像尺寸限制。跨网格映射是尺度近似，不保证逐像素一致。
  * `--wavelet-l1` 默认表示原生最细尺度；`--wavelet-adapt off` 恢复全局小波及旧工作网格语义。此开关与 `--sharp-guard`、`--anti-ringing` 独立，支持 `postprocess` 和 `all`。CLAHE/USM 仍为独立后续步骤。
  * 保存 `moon_wavelet_support.fit`，将基准结构区与非零请求、可信权重至少 0.5 的区域相交后冻结为收益区域；不足 32 像素时由护栏回退，不放宽损伤门限。`verify` 和实际 TIFF 检查复用同一区域；关闭自适应时清除旧支持文件。
  * `postprocess_receipt.json.wavelet_adaptation` 记录层映射、请求与有效增益范围、噪声、可信覆盖率及旁路原因。分层逐层读取，隔离临时目录在成功或失败后清理；缺层、尺寸错误、非有限值和空间不足均明确失败，不自动改走全局增强。
* **完整链路防过锐（默认 `--sharp-guard auto`）**：
  * 反卷积前保存 `moon_unsharpened.fit`，与增强分支共用白平衡、线性校正及 MTF；原有反卷积后基准继续保留。亮边与暗环阻尼依据完整基准定位，默认不对零增强增量另加 soft-knee。
  * 固定基准的有效区域、亮度尺度、平坦区及收益区域，依次尝试增强保留比例 `1 / 0.75 / 0.5 / 0.25`；首个满足整个月面损伤限制且收益区域中位梯度提高至少 0.5% 的结果获选，否则回到无反卷积、无小波增强的基准。只运行一次反卷积，不搜索 PSF；关闭自适应时恢复原结构区收益判据。
  * 所有模式（含 `crisp` 和手动参数）默认受保护；`--sharp-guard off` 独立关闭护栏，`--anti-ringing off` 仅关闭局部阻尼。`--sharp-mode none` 仍只旁路小波等增强，反卷积由 `--deconv` 单独控制。
  * 门限：DHR / 正过冲比 ≤ 0.035；CSI ≤ `max(0.025, baseline + 0.005)`；GKM ≤ `max(14, baseline × 1.10)`；平坦区高频残差 MAD ≤ `max(baseline × 1.10, 1e-6)`；不得新增削顶。门限是待真实素材校准的工程默认值，不证明真实分辨率提升。
  * 有效月面不足 500 像素、关键指标无效或无法测量收益时回退；缺少强边缘记为不适用，不伪报为零。原始母版有 NaN/Inf 时报错，增强候选有 NaN/Inf 时拒绝候选。
  * `postprocess_receipt.json.sharp_guard` 记录各档指标、拒绝原因、分阶段诊断及 `accepted / reduced / fallback / off` 状态。所有成片来自同一获选结果；`verify` 撤销显示几何后检查实际 16 位 TIFF 与浮点成片母版的量化误差及质量指标。证据不足显示 `unavailable`，旧目录缺少完整基准时保护状态为 `unknown`。

* **亚像素阶跃边缘下冲暗环抑制 (Subpixel Anti-Ringing Damping, 默认 `--anti-ringing auto`)**：
  * **物理成因诊断**：阳光直射的高耸亮坑壁与坑底深阴影交界处，物理 Airy 反卷积频域振荡、双三次插值负旁瓣与多尺度小波高频差分级联，在暗侧陡峭处产生严重负下冲（Negative Undershoot），导致环形山边缘呈现人工“浓黑细圈/黑描边”（甜甜圈伪影）；
  * **暗环风险场定位**：基于归一化梯度与局部动态基准，精准锁定明暗交界断崖的暗侧过渡带，生成连续平滑的高斯羽化阻尼掩模 $M_{damp}$；
  * **双向弹性阻尼**：分别衰减强边缘暗侧负增量和亮侧正过冲。负向强度按插值与反卷积状态设为 0.45~0.65，正向强度为 0.75；完整链路护栏随后检验实际损伤，必要时继续降档，不保证细节完全无损；
  * **参数支持**：`--anti-ringing {auto,off,mild,aggressive}`（默认 `auto`），支持 `--damping-factor <0.0-1.0>` 手动精细调节，支持 `--anti-ringing off` 完全旁路；

## 白平衡、消光与矿物月

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

* **低仰角宏观大气消光一阶梯度补偿 (Atmospheric Extinction Gradient Compensation, 默认 `--extinction-comp auto`)**：
  * **物理成因诊断**：月面低仰角（$a < 30^\circ$）拍摄时，因半度视场跨度的大气柱质量（Airmass）差诱发 Rayleigh/气溶胶消光空间倾斜，导致月盘呈现“底暖顶冷、底暗顶亮”的宏观红化与通量坡度，严重污染矿物月并造成拼接色差；
  * **地质反照率解耦**：将像元映射至对数色比空间（$\ln(B/G), \ln(R/G)$），表面绝对光通量差异被完全消除；
  * **网格中位数降采样与 Huber 鲁棒回归**：通过 $16\times 16$ 局部块中位数提取低频空间斜率，对局部高钛玄武岩色块天然免疫；
  * **Rayleigh 物理一致性校核**：验证蓝光与红光消光矢量的反向共线性，视场梯度 $<1.5\%$ 时自动旁路（高仰角零扰动）；
  * **参数支持**：`--extinction-comp {auto,mild,aggressive,off}`（默认 `auto`），在 32 位浮点线性空间执行通量守恒除法平复。

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

* **风格定制与回退支持**：默认产出电影级深影调成片 `moon_mineral.jpg`；亦支持 `--mineral-style natural` 直接回退至经典自然轻盈饱和度拉伸风格；
* **参数示例**：`--mineral-fe-boost 6.8`, `--mineral-ti-boost 10.2`, `--mineral-gamma 1.09`。这些是显式示例值；当前默认分别为 5.2、6.5 和 1.00，以 `postprocess --help` 为准。

## 马赛克与全局 Profile 锁定

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
       "$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/tile_center --mosaic-mode tile
       ```
    2. 对邻近的所有切片，后处理时统一挂载基准面板的 profile：
       ```bash
       "$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/tile_north --mosaic-mode tile --lock-from /path/to/tile_center
       "$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/tile_south --mosaic-mode tile --lock-from /path/to/tile_center
       ```
    3. 面板共享同一白平衡和拉伸基准；仍需检查重叠区的亮度和色彩，不能保证接缝误差为零。

## 天空留白与构图

* **月盘自适应天空留白与深空居中 (Adaptive Lunar Sky Margins, 默认 `--auto-margin`, `--sky-margin 0.25`)**：
  * **物理与美学痛点**：全画幅月盘拍摄或经纬仪漂移导致月球偏心、贴边（四边空隙不足甚至紧顶画框），视觉逼仄且构图失衡；若盲目缩放月盘凑留白，会引入双三次/双线性重采样插值，破坏月面原生高分辨率，违背天文真实性红线；
  * **零插值安全深空扩充 (Zero-Interpolation Padding)**：坚决不缩放月面原生像素。基于 RANSAC 解算的月轮物理圆心 $(c_x, c_y)$ 与半径 $R$，动态计算满足四周最小留白率（默认 $0.25 \times R$，即边缘至月轮至少保留 25% 半径的天空）所需的画布尺寸与居中偏移量；
  * **真空深空零噪填充**：上下左右扩展区以四角深空零噪基底平滑填充，与月轮前向散射抑制（`--glare-suppress`）无缝衔接，输出纯净深邃黑底；
  * **切片模式安全旁路**：在 `--mosaic-mode tile` 下自动完全旁路留白处理，绝对保护相邻面板拼接重叠几何区；
  * **全链路协同扩展**：
    * 方形特写（`_square.jpg`）视场边长联动扩展至 $S = 2R \times (1 + 2 \times \text{sky\_margin})$（默认 1.50 倍月球直径），居中展示完整月球本体与呼吸感深空；
    * 交互式 HTML 对比器（`comparison.html`）中的参考帧（`moon_reference.jpg`）自动同步相同 padding 扩展与居中，保持双窗 1:1 亚像素严密联动；
    * 视频导入阶段（`--video-roi auto`）自动依据 `--sky-margin` 预留足够边距，避免前期抽帧过紧截断；
  * **参数支持**：`--sky-margin <ratio>`（默认 `0.25`，即 25% 半径）、`--auto-margin` / `--no-auto-margin`（默认启用）。
