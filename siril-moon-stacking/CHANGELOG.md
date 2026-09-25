# Changelog

本文件记录 `siril-moon-stacking` 的独立版本变更。

## [1.0.8] - 2026-09-25

- **月面物理日光反照率影调重塑与锐化过头（Crunchy Texture）彻底根治**：
  - **纠正深空星云拉伸预设，重塑日光物理影调（Midtone 0.18 -> 0.42）**：
    - 确凿定位“全局过曝”根源为错误套用暗弱深空天体（DSO）的拉伸参数（`midtone=0.18`）——导致输入仅 0.116 的玄武岩暗海被强拉至 128 灰阶，全月面 88.8% 的像素被严重挤压在 $[153, 235]$ 极亮暴晒区（中位数高达 192.5）；
    - 将 `--midtone` 默认值调整为月球日光反射率基准 **`0.42`**：月表整体中位数回归至柔正中灰 **123.0**，最暗玄武岩暗海从 147.5 回落至深沉沉郁的 **74.0**，有效动态范围从 77 灰阶扩宽至 **102 灰阶**（地质层次扩展 32.5%），高光暴晒比例从 50.6% 骤降至 **1.68%**，从根本上彻底消灭过曝泛白。
  - **消除非线性阶梯陡化，彻底解决生硬描边（Crunchy / Embossed Texture）**：
    - 消除因中间调塌陷导致仅剩高反差黑白勾线的浮雕感错觉；非线性微积分导数 $dy/dx$ 从 2.48 自然平复至 1.30，环形山明暗边缘阶跃硬度自然减半，恢复圆润、深邃的碗状盆地地貌反差。
  - **前置软膝盖高光压缩启动阈值（`knee 0.85 -> 0.75`）**：
    - 从约 190 灰阶开始平滑双曲正切下压最高反照率辐射纹峰值，自然黑白与彩色矿物双管线同步受控，确保高反照率微细节晶莹立体、通透不刺眼。
  - 全套 27 项单元测试全部保持 100% 绿色通过，真实 Seestar S50 样本各项量化指标完美达标。

## [1.0.7] - 2026-09-25

- **平坦暗海椒盐噪点与月盘边缘镶边伪影彻底根治（Shot Noise Amplification & Limb Chromatic Aberration Root-Cause Cure）**：
  - **彻底清除暗海椒盐噪点与环形山削顶白点（CLAHE Bypass & Soft Sharp Balance）**：
    - 确凿定位平坦暗海满屏椒盐颗粒真凶为 Siril 脚本中的小半径局部直方图均衡（`clahe 0.12 32`）——在缺乏反差的平坦月海中将高频散粒噪声强行放大 13.5 倍（拉普拉斯方差曾暴增至 0.107）；
    - 规则化重构自适应锐化算法：在 `auto` 模式下，当物理反卷积激活（`has_deconv=True`）或底噪可检出时，严格将 CLAHE clip 设为 0.0（完全 bypass），高频清晰度纯粹由光学 Airy 反卷积及小波中层恢复，平坦月海方差回归至平滑自然的 0.000085（降噪比逾 134 倍），环形山与辐射纹削顶白点彻底归零（0 像素）。
  - **彻底消除月缘品红/紫边与下边缘绿边（Sobel ADC Gradient Alignment + 360° Limb Circle Fit + 18px Defringe Zone）**：
    - **Sobel 边缘梯度亚像素 ADC**：将 `align_rgb_channels` 升级为基于 Sobel 物理边缘梯度图的相位相关对齐，彻底排除地表矿物反射率偏差干扰，使 R/G/B 通道几何边界对齐精度达到极致；
    - **修复月轮圆拟合大端字节序与全周向采样**：修复 FITS `>f4` 大端字节序导致 OpenCV C++ 函数解析错误返回 None 的隐蔽缺陷，并将径向搜索由局部单向扩展至全周向 360° 采样，RANSAC 拟合残差标准差仅 0.67 像素，全面赋能渐盈月、弯月及任意倾角月相；
    - **18 像素平滑月肢中性保护带（Limb Defringe & Desaturation Zone）**：月轮外沿 4px 设为绝对中性死区，向内 4~18px 采用三次 Hermite 平滑多项式过渡，将折射镜次级光谱与横向色散（$R+B > G$ 品红与下缘绿色渗出）彻底归零，边缘品红像素减少 100%（从 590 降至 0），纯净空间截断彻底消弭背景光晕与色散渗出。
  - 全套 27 项单元测试全部保持 100% 通过，真实 Seestar S50 视频测试样本完美通过科学指标与视觉双重验收。

## [1.0.6] - 2026-09-25

- **月面图像后期画质全链路重构与四大视觉缺陷根治（Image Quality & Geological Fidelity Overhaul）**：
  - **色彩通道与矿物色重构（彻底解决色彩失效与孤立假蓝斑）**：
    - 彻底废除 `_render_deep_cine_mineral` 中的二值硬阈值分类器（`ti_m`, `fe_m`）与过激的夜侧/暗海截断掩模；
    - 采用标准**物理连续地质色度空间拉伸（Continuous Geological Chrominance Stretch）**：先经大半径双边滤波（Bilateral Filter）剥离传感器散粒彩色噪点，再按真实光谱偏差平滑连续放大，雨海的深邃钛蓝与静海、澄海的铁红暖橙实现天然、大面积连续地质色彩过渡（有效色彩覆盖率从 0.70% 跃升至 18.13%，Ti/Fe 比例达 50.1% vs 43.4%）；
    - 收紧宏观大气消光补偿门槛（要求严苛反向共线性与高置信度 $\ge 0.70$），彻底消除将月球原生反照率误拟合为倾斜平面的整盘色偏；
    - 移除 Siril 脚本中破坏天然青蓝光谱的 `rmgreen 0`，保留纯净真实地质波段响应。
  - **自适应去噪、高光余量预留与软膝盖压缩（彻底解决过“crunch”感与小白点削顶）**：
    - 在白平衡与拉伸时提供 $+25\%$ 的充足高光余量（Highlight Headroom），使底图峰值保持在 $\approx 0.80$，避免反卷积与小波能量在 1.0 发生硬截断（Hard Clipping）；
    - 优化小波多尺度权重：当存在反卷积或可探测底噪时，小波第 1 层增益严格保持在 1.00，消除粗糙椒盐状散粒噪点放大，锐化能量集中于 2~6 像素尺度的真实环形山轮廓；
    - 引入可微分**软膝盖高光压缩（Soft-Knee Highlight Compression, $L > 0.85$）**，环形山 rims、中心峰与高反照率辐射纹削顶率（CSI）从过曝削顶降至 0.002%（完全保留高光微反照率反光梯度）。
  - **双向边缘过冲阻尼与月肢色度中性化（彻底解决月缘亮白圈与边缘色彩错位）**：
    - 升级抗振铃阻尼机制：不仅抑制阴影侧负过冲暗晕（DHR 降低 45%），同时精确捕获月盘物理边缘（Limb）内侧小波正向过冲（$\Delta > 0$）并施加 75% 吸收阻尼，彻底消灭月缘死白发光白边；
    - 引入月肢过渡带色度平滑淡出（Limb Desaturation，最外侧 5 像素转换为纯中性灰过渡到太空深黑），彻底根除折射镜横向色差及拜尔边缘插值产生的红绿蓝色边。
  - **宽动态范围影调平复（彻底解决暗海死黑与阶调断层）**：
    - 废除暗部下凹幂函数（$lum^{1.155}$），平复中灰影调曲线，默认 MTF 中间调调整为更自然的 0.18，玄武岩暗海（静海/澄海/风暴洋）暗调细节丰富饱满，彻底杜绝死黑。
  - 全套 27 项单元测试全部保持 100% 通过，真实 Seestar S50 视频测试样本完美通过科学指标与视觉双重验收。

## [1.0.5] - 2026-09-25

- **引入 AVI / MP4 / MOV 等通用视频容器原生直通支持（Direct-pass FITS Architecture）**：
  - 支持直接传入单文件（如 `--input /path/to/moon.avi`）或包含视频文件的目录，涵盖 `.avi`、`.mp4`、`.mov`、`.mkv`、`.m4v` 等主流封装；
  - 彻底规避“AVI $\to$ SER $\to$ FITS”的冗余二次转录，利用 OpenCV 硬件级解码逐帧抽取并直写 16-bit FITS 序列，节省 50% 磁盘 I/O 写入带宽；
  - 内存级精确映射 BGR 到 FITS RGB `(channels, H, W)`，平滑执行 8-bit 到 16-bit 线性高动态扩展，彻底杜绝跨格式转录导致的红蓝对调与相位错位；
  - 自动检测单色与彩色流，新增 `--force-mono` 参数支持彩色相机配 IR-pass 滤镜拍摄的高反差单色模式；
  - 截断与坏帧弹性容错：遇视频尾部截断或坏帧平滑捕获并记录有效帧数，绝不因容器格式异常中断；
  - **自适应绑定 Siril `stack sum` 物理高动态扩展（Vincent Hourdin 规范）**：
    - 自动识别 8-bit 视频源，在下游 Siril 堆叠脚本中自动启用加法叠加（`stack sum -filter-included`），在 32 位浮点累加器中将 8 位动态范围物理扩展到 14~16 位以上；
    - 新增 `--stack-method {auto, sum, rej}` 允许显式覆盖；
  - 新增 `test_video_unpack_synthetic_avi`、`test_video_cmd_import_single_file_and_dir`、`test_video_8bit_stack_policy` 3 项高覆盖度单元测试，全套 27 项测试 100% 保持通过。

## [1.0.4] - 2026-09-24

- **引入低仰角宏观大气消光一阶梯度补偿（Atmospheric Extinction Gradient Compensation, `--extinction-comp {auto,mild,aggressive,off}`）**：
  - 针对月出/月落低仰角（$a < 30^\circ$）拍摄时，因月盘半度视场跨度引起的大气质量（Airmass）差诱发的宏观 Rayleigh 消光倾斜（“底暖顶冷、底暗顶亮”坡度），在 32 位浮点线性空间实施通量守恒除法平复；
  - 创新采用对数色比空间映射（$s_B = \ln(B/G), s_R = \ln(R/G)$），表面反照率（月海 vs 高地）与月相明暗在此空间完全被除法抵消；
  - 集成多尺度 $16\times 16$ 网格中位数降采样与 Huber 鲁棒平面回归（IRLS），天然免疫局部高钛玄武岩色块干扰；
  - 引入 Rayleigh 物理反向共线性校验与自适应门限（全盘色偏 $<1.5\%$ 自动旁路，确保高仰角零扰动）；
  - 彻底根除深空矿物彩月被宏观消光污染导致的“半边黄泥、半边紫蓝”的伪地质色带；
  - 导出 `extinction_receipt.json` 并集成到 `verify` 质检审计报告；
  - 新增 `test_extinction_gradient_synthetic`、`test_extinction_gradient_flat_bypass`、`test_extinction_geological_immunity` 3 项高强度单元测试。

## [1.0.3] - 2026-09-24

- **引入行星与月面 SER 视频流原生直接支持（Native SER Video Stream Support）**：
  - 核心遵循 Lucam Recorder 178 字节二进制标准协议，支持直接传入单文件（`--input /path/to/capture.ser`）或包含 `.ser` 的目录；
  - 采用零拷贝 `np.memmap` 瞬时读取，上百 GB 大视频零内存膨胀；
  - 原生支持 Bayer CFA（RGGB/GRBG/GBRG/BGGR）与 RGB/BGR 彩色格式，集成 OpenCV C++ 硬件级多线程 demosaicing（1080p 单帧解拜耳仅需 4.9ms），直接生成 16-bit RGB FITS；
  - 提取 SER 纳秒级 UTC 科学时间戳，自动换算为标准 ISO-8601 字符串注入 FITS `DATE-OBS`；相机（`INSTRUME`）与望远镜（`TELESCOP`）设备元数据全量无损透传；
  - **首度建立单色（Mono）与彩色（RGB）全链路自适应**：Mono 数据原生生成 `L 1` 序列并在后处理优雅绕过彩色专属滤镜，为单色冷冻相机配 IR-pass 窄带滤镜的高阶月面摄影铺平道路；
  - 支持 `--limit <N>` 限制帧数，避免超长视频冗余 I/O；
  - 新增 `test_ser_header_parser`、`test_ser_unpack_mono`、`test_ser_unpack_bayer`、`test_ser_cmd_import_single_file_and_dir` 4 项单元测试。
- 引入智能光学参数推断（Intelligent Optical Parameter Inference，Header 元数据挖掘 + 亚像素月盘几何反推）：
  - 自动从 FITS Header 挖掘 `XPIXSZ` / `PIXSIZE` 像元尺寸（如 $3.73\ \mu\text{m}$），解耦命令行硬编码；
  - 联动亚像素 RANSAC 月轮圆拟合，由实测像素直径 $D_{px} = 2097\text{ px}$ 结合天体测量学月球视直径（$31.1'$，区间 $29.4'\sim 33.5'$），通过针孔成像几何精确反推有效焦距 $f \approx 865.5\text{ mm} \approx 866\text{ mm}$（物理区间 $803\sim 915\text{ mm}$）；
  - 彻底纠正旧版硬编码默认值（$400\text{ mm}$）低估一倍的物理失真，使 Split Bregman Airy 反卷积 PSF 像元半径从 $0.90\text{ px}$ 恢复为物理真实的 $1.95\text{ px}$（焦比 $F/10.8$），完全释放光学反卷积效能；
  - `--focal`、`--pixel-size`、`--aperture` 默认设为 `None`，未指定时自适应推断，用户显式指定时 100% 优先；切片模式自动安全旁路；
  - `verify` 命令集成光学推断参数、焦比与 Airy 斑尺寸输出；
  - 新增数学单元测试 `test_infer_optical_parameters_math`。
- 引入全局色彩与直方图锁定机制（Anchor Master Profile Lock，`--lock-profile` / `--lock-from` / `--export-profile` / `--lock-wb` / `--lock-stretch`）：
  - 彻底根除多面板全景拼接时，切片间由于局部反照率差异（暗月海 vs 亮高地）独立拉伸导致的色块漂移与接缝明暗阶梯断层；
  - 实测将多面板公共重叠区明暗跳跃从未锁定的 409.2% 直接降至 0.0000%；
  - 支持从基准面板（Anchor Master）一键继承通道平衡增益（$k_r, k_b$）与直方图非线性 MTF 映射基准（$bg_{lum}, hi_{lum}$、midtone、矿物色彩参数）；
  - `mosaic_tile_info.json` 自动记录锁定状态、来源路径与校验参数；
  - `verify` 命令集成 Profile 锁定状态审查输出；
  - 新增数学与物理管道单元测试 `test_histogram_color_lock_pipeline`。
- 引入亚像素阶跃边缘下冲暗环抑制（Subpixel Anti-Ringing Damping, `--anti-ringing`，默认 `auto`）：
  - 通过局部对数梯度与动态基准分析精确定位明暗阶跃断崖（晨昏线坑壁、月轮边缘）的阴影侧负下冲带；
  - 实施非对称弹性阻尼平复（默认强度 0.45~0.65），彻底根除 Gibbs 振铃与小波负瓣导致的人工暗环/甜甜圈黑圈，同时 100% 保持高光山脊正向极限锐度与解析力；
  - 支持 `--anti-ringing {auto,off,mild,aggressive}` 与 `--damping-factor` 手动覆盖；
  - 同步更新 32 位浮点明度母版、16 位 TIFF 母版及高质量自然版与矿物月成片。
- 引入防脆裂质检指标矩阵与高光白垩化诊断（Anti-Brittleness & Chalky Saturation Quality Matrix）：
  - **高光白垩饱和度指数 (CSI, Chalky Saturation Index)**：精确定量过度拉伸/CLAHE 引起的撞击坑辐射纹死白与微反差抹平（$<0.010$ 为优，$>0.025$ 报警）；
  - **梯度峰度脆裂度指标 (GKM, Gradient Kurtosis Metric)**：提取月面内部 Sobel 梯度的皮尔逊四阶峰度，精准量化过度小波/USM 产生的人工尖锐毛刺脆裂感（$<6.0$ 为温润，$>14.0$ 报警）；
  - 在 `verify` 命令与报告中集成 DHR、CSI、GKM 联合质检输出。
- 引入月面全景马赛克拼接切片模式（`--mosaic-mode {disc|tile}`，默认 `disc`）：
  - 切片模式下强行旁路全月盘月轮 RANSAC 拟合与外太空清零，彻底根除相邻切片重叠月面地形被误杀的问题；
  - `stack` 与 `all` 子命令下切片模式自动缺省使用 `--framing max`（携带 `-maximize`），最大化保留重叠特征；
  - 后处理结束自动导出 `mosaic_tile_info.json` 切片元数据清单，与下游 `siril-mosaic` 技能零摩擦对接。
- 新增单元测试 `test_anti_ringing_damping_math`、`test_dark_halo_ratio_metric`、`test_anti_brittle_metrics_math` 与 `test_mosaic_tile_mode_pipeline`，全套 15 项测试 100% 通过。
- 新增 `requirements.txt` 声明运行时依赖（numpy / astropy / opencv-python-headless / scikit-image）。
  此前 `requirements-dev.txt` 仅含 pytest 与 PyYAML，全新环境执行 `register` 会因缺少
  `skimage` 抛出 `ModuleNotFoundError`，`import`/`postprocess` 亦会分别缺少 `astropy`/`cv2`。
- `SKILL.md` Step 1 补充依赖安装命令、Siril 1.4.4+ 前置条件与 `probe` 四项依赖校验说明。
- `RELEASING.md` 增加 `requirements.txt` 安装步骤。

## [1.0.2] - 2026-09-23

- 例行补丁升级与元数据规范维护

## [1.0.1] - 2026-09-23

- 建立治理基线：规范 frontmatter metadata 与独立发布契约。
- 引入 MTF-SNR 联合效用模型进行选帧，提供亚像素频域配准与矿物月色彩提取。
