# geometry.crop-near-black

## 适用条件

仅在 direct/autostretch 预览显示近黑堆栈边、配准空边或明显无效边界时使用。目标贴边、裁切证据不清
或 WCS 无法验证时跳过。

## 参数协议

- 每侧最多裁 3.5%；优先 0.5% 递增；
- 保留面积至少 70%；
- 输出宽高使用偶数；
- 坐标与尺寸来自实际预览和输入几何，不凭目标类型猜测。

## SSF 知识关系

是否裁切及坐标尺寸来自当前父源几何、WCS 和实际预览证据；本页是 SSF 的 primary protocol reference，
提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `crop` 语法语义；`command-policy.json` 独立决定执行
授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定流水线。逐字采用下方完整变体可记录
manual lookup `not_needed`；其他 crop 选项或语法不确定时必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
crop X Y WIDTH HEIGHT
stat main
save "/abs/session/artifacts/020-crop" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/020-crop" 95
close
```

nonlinear 父源删除 `autostretch`。预期产物为 FITS 与 JPEG。

## 审查与回退

比较父源和候选，确认目标、星晕、构图未受损，黑边确实减少，尺寸变化与预期一致。任何裁错、WCS
异常或改善不明确都 reject，保留父源。
