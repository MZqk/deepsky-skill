# Changelog

本文件记录 `deep-sky-advisor` 的独立版本变更。

## [0.2.0] - 2026-09-10

- 新增 `references/smart_telescope_devices.md`：DWARF 3 / DWARF mini / Draco 与 Seestar
  S30 / S30 Pro / S50 / S50 Pro 的光学、传感器、像元尺度、内置滤镜与采集特性先验，
  全部按 official / chip spec / inferred 标注证据等级。
- `analyze_file.py` 新增 `classification.device`：基于 TELESCOP/INSTRUME 头与文件名的
  智能望远镜识别（含 brand-only 降级），schema 升级至 2.1。
- `generate_advice.py`：设备先验中的双窄带滤镜可触发 narrowband_mapping 复核；
  报告头部新增拍摄设备字段。

## [0.1.0] - 2026-08-28

- 为现有图像诊断与后期建议能力建立首个治理版本基线。
- 登记独立许可、开发环境和发布流程。
