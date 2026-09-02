# stars.recompose

## 适用条件

只在已接受 `stars.separate` starless 与星层，且 `stretch` 记录了 matched MTF 时使用。不得把不同
父源、不同几何或不同 stretch 的星层混合。

## 参数

先用完全相同的 `mtf LOW MID HIGH` 拉伸冻结星层，再以 `0.70<=strength<=1.00` 加回 starless。

## SSF 知识关系

适用性、matched MTF 和合成强度来自同一分星 lineage、stretch receipt 与实际像素证据；本页是 SSF 的
primary protocol reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `mtf/pm` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用本页已展开变体可记录 manual lookup `not_needed`；其他表达式或选项必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/session/artifacts/060-star-layer.fit"
mtf LOW MID HIGH
save "/abs/session/artifacts/090-star-layer-stretched" -chksum
close
pm "min(1, $artifacts/080-hoo$ + 0.85 * $artifacts/090-star-layer-stretched$)"
stat main
save "/abs/session/artifacts/090-recomposed" -chksum
savejpg "/abs/session/previews/090-recomposed" 95
close
```

示例采用已执行 Stage 8 HOO 的 `080-hoo`。若 Stage 8 被明确跳过，把 PixelMath 中的 starless 项替换为
Stage 7 已接受的 `070-stretch`；不得同时引用两个分支或改用未审查候选。

## 审查与回退

检查自然星径、星色、光晕、重复目标结构、残留和闭合误差。未通过时回到 full-stars matched-transfer
基线；只有用户显式 `standalone-starless` 才允许无星交付。
