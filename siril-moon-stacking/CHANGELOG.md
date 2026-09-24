# Changelog

本文件记录 `siril-moon-stacking` 的独立版本变更。

## [Unreleased]

- 引入亚像素阶跃边缘下冲暗环抑制（Subpixel Anti-Ringing Damping, `--anti-ringing`，默认 `auto`）：
  - 通过局部对数梯度与动态基准分析精确定位明暗阶跃断崖（晨昏线坑壁、月轮边缘）的阴影侧负下冲带；
  - 实施非对称弹性阻尼平复（默认强度 0.45~0.65），彻底根除 Gibbs 振铃与小波负瓣导致的人工暗环/甜甜圈黑圈，同时 100% 保持高光山脊正向极限锐度与解析力；
  - 支持 `--anti-ringing {auto,off,mild,aggressive}` 与 `--damping-factor` 手动覆盖；
  - 同步更新 32 位浮点明度母版、16 位 TIFF 母版及高质量自然版与矿物月成片。
- 在 `verify` 命令中集成边缘下冲暗环指数（Dark Halo Ratio, DHR）量化审查指标。
- 新增单元测试 `test_anti_ringing_damping_math` 与 `test_dark_halo_ratio_metric`，通过全套回归测试。
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
