# Delivery Protocol v1

## 正式交付边界

只有 `success|partial_success` 才正式发布 `outputs/reference.jpg` 与 `outputs/final.jpg`。reference 是从已
验证的 `input.inspect` 结果固化的处理前显示预览；final 是被接受的 `delivery.render` JPEG。默认不复制
FITS/TIFF、其他父源预览、候选图或阶段报告到 `outputs/`。

`review_required` 是有候选但未达到正式发布条件的审计状态。finalizer 必须验证并记录候选 fingerprint，
但不得创建 `outputs/reference.jpg` 或 `outputs/final.jpg`。`failed` 只有结构化错误，不得声明候选。

unknown 输入只执行 Stage 1 的 `input.inspect` 并停止，不进入交付协议。获得可靠状态证据后创建新 session；
诊断预览既不是科学父源，也不能直接成为正式 JPEG。limitations 只披露已知边界，不会把 unknown 变成
可交付状态。

## 最终选择

按 `final-selection.schema.json` 创建 `final-selection.json`：

- `status` 使用 `success|partial_success|review_required|failed`；
- 非 `failed` 必须提供 `candidate_image`；
- `failed` 必须提供 `error`；
- `selected_runs` 和 `review_receipts` 只列实际采用、哈希可验证的记录；
- 明确 `stars_required` 和 `output_contains_stars`；
- 用结构化 `limitations` 记录输入状态、WCS、颜色真实性、可选工具或其他限制；
- `partial_success` 至少说明一个实际限制。

缺少未使用的可选工具只是 probe warning。若用户要求的能力因此无法完成，应保留已验证候选并选择
`partial_success` 或 `review_required`，取决于候选是否仍满足正式交付门禁；不得伪造完成。

## 清理

只有 `success|partial_success` 且 finalizer 完成 v1 提交后，才按默认策略清理阶段图像。
`review_required|failed` 保留现场；`--keep-intermediates` 让成功分支也保留。清理不删除脚本、日志、
运行时重开记录、JSON receipts、final audit 或 final result。

## 验收证据

测试、静态校验、Siril 退出码或 JSON 状态都不能替代真实图像验收。正式交付至少需要：

- Siril 对候选容器的前向重开证据；
- 与 run receipt 绑定的候选 fingerprint；
- 对实际 JPEG 像素完成结构、背景、颜色、星点和几何审查；
- selection、audit、final-result 与正式输出的 SHA-256 链路一致；
- 每个 selected run 的 SSF 都有经验证且与 receipt 绑定的同 stem provenance sidecar。
