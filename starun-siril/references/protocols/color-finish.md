# color.finish

## 适用条件

只用于 nonlinear 三通道父源的轻量显示调色。它不增加光度真实性；无明确改善时跳过。

## 参数

- identity：跳过，不创建 run；
- saturation：`satu AMOUNT 1 6`，`0<AMOUNT<=0.20`；
- rmgreen：`rmgreen 3 AMOUNT`，`0<AMOUNT<=0.20`。

默认只选一个操作，不把 saturation 与 rmgreen 叠加到同一协议运行。

## SSF 知识关系

是否调色及单一操作参数来自当前 nonlinear 父源、用户目标和实际像素证据；本页是 SSF 的 primary
protocol reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `satu/rmgreen` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用本页已展开变体可记录 manual lookup `not_needed`；其他选项必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
satu 0.10 1 6
stat main
save "/abs/session/artifacts/100-color-finish" -chksum
savejpg "/abs/session/previews/100-color-finish" 95
close
```

## 审查与回退

检查颜色过渡、背景中性、通道裁剪、星色和色噪。过饱和、绿色结构误删或改善不明确时 reject。
