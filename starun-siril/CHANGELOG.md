# Changelog

本文件记录 `starun-siril` 的独立版本变更。

## [0.1.0] - 2026-08-29

- 建立 standalone contract v1：公开命令收敛为 `probe`、`init`、`run`、`finalize`。
- 增加由 session 冻结的 `siril|strict` 容器验证模式；默认以真实 Siril 前向重开为准，strict 先做标准库完整容器检查再核对 Siril 观察。
- 离线 Siril 1.4.4 手册完整随包分发，但改为命令或参数不确定时按需查询。
- 明确 `review_required` 只保留候选与审计证据，不产生正式 reference/final；仅 `success|partial_success` 可正式交付。
- 保留精确白名单、确定性 ZIP、第三方组件完整性与不可公开发布许可门禁，同时移除运行时工具安装接口。
