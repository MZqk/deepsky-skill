# background.subtract

## 适用条件

只用于 linear 父源中可与目标分离的大尺度渐变。目标铺满画面、背景样点不足或尘埃/IFN 容易被误当
背景时跳过。

## 样点协议

- 实际查看父源预览，由 Agent 根据画面与渐变选择足够且有代表性的真实背景样点；
- 样点必须位于图像范围内，并避开目标、恒星晕、发射结构或尘埃；
- 创建 `reports/NNN-background/background-sample-contract.json`，使用
  [background sample contract Schema](../background-sample-contract.schema.json)，绑定父源绝对路径、
  SHA-256 与实际尺寸；
- 样点 ID 和坐标不可重复，坐标必须是有限数且位于图像边界内。

目标、星晕和尘埃的识别是 Agent 的视觉判断，不另外写入机器合同。候选执行后仍按通用
review receipt 审查父源与结果，背景样点合同不包含独立 review。

## 模型

- `polynomial-1`：`subsky 1 -existing`，默认；
- `polynomial-2`：`subsky 2 -existing`，仅复杂平滑梯度；
- `rbf`：`subsky -rbf -existing -smooth=0.5`，仅多轴非多项式渐变。

## SSF 知识关系

适用性、模型和样点坐标来自当前父源、实际预览与 session 证据；本页是 SSF 的 primary protocol
reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `pyscript/subsky` 的语法语义；
`command-policy.json` 独立决定是否可执行。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定流水线。
逐字采用下方完整变体时可记录 manual lookup `not_needed`；改用未展开选项或遇到歧义时必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
pyscript "/abs/starun-siril/scripts/siril_background_samples.py" --contract "/abs/session/reports/030-background/background-sample-contract.json" --contract-sha256 HASH --expected-source "/abs/current-parent.fit" --receipt "/abs/session/reports/030-background/sample-injection-receipt.json"
subsky 1 -existing
stat main
save "/abs/session/artifacts/030-background" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/030-background" 95
close
```

把 FITS、JPEG 和 injection receipt 都列为 `--expect`。

## 审查与回退

确认 injection receipt 中的请求坐标与 Siril 实际保留坐标一致，并确认渐变改善且无坑洞、误减、
条纹、颜色关系破坏或微弱结构丢失；任何异常或不确定都 reject。
