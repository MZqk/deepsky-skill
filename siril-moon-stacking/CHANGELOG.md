# Changelog

本文件记录 `siril-moon-stacking` 的独立版本变更。

## [Unreleased]

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
