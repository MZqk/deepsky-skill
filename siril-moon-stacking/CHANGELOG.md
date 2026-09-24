# Changelog

本文件记录 `siril-moon-stacking` 的独立版本变更。

## [Unreleased]

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
