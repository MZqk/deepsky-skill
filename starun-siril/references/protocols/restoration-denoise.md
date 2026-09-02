# restoration.denoise

## 适用条件

只用于 linear 父源。背景噪声必须明显影响后续拉伸；细丝、尘埃带、微弱星云和星核是保护对象。

## 参数

使用 Siril `denoise -mod=STRENGTH`，`0.20<=STRENGTH<=0.66`。默认从 0.30–0.40 的单一保守候选
开始，不因背景更平滑而提高强度。

## SSF 知识关系

是否降噪和强度来自当前 linear 父源、用户目标与实际像素证据；本页是 SSF 的 primary protocol
reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `denoise` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用本页已展开变体可记录 manual lookup `not_needed`；其他模式或选项必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
denoise -mod=0.35
stat main
save "/abs/session/artifacts/055-denoise" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/055-denoise" 95
close
```

## 审查与回退

放大比较真实细节、尘埃边缘、星核和背景纹理。任何结构变软、塑料感、色噪斑块或改善不明确都
reject。
