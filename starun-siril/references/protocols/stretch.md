# stretch

## 适用条件

linear 父源变为非线性显示图时必需。nonlinear 和 unknown 不使用该协议。若没有可接受 stretch，
停止而不是把线性图伪装成成片。

## 参数

- 默认 `autostretch -linked SHADOW TARGET`；`-8.0<=SHADOW<=-2.8`，
  `0.08<=TARGET<=0.18`；
- `asinh -human STRENGTH -clipmode=rgbblend` 只在亮核与微弱结构动态范围需要时使用，
  `20<=STRENGTH<=55`，之后仍执行 linked autostretch；
- 有 StarNet 分支时必须使用可从日志记录 MTF 的 linked 路径，供星层同传递。

## SSF 知识关系

是否拉伸、方法和参数来自当前 linear 父源、目标动态范围与实际像素证据；本页是 SSF 的 primary protocol
reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `autostretch/asinh` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。下方 linked autostretch 完整变体可记录 manual lookup `not_needed`；asinh 分支或其他未展开组合
必须先查询原文并保留 evidence。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
autostretch -linked -4.50 0.120
stat main
save "/abs/session/artifacts/070-stretch" -chksum
savejpg "/abs/session/previews/070-stretch" 95
close
```

## 审查与回退

检查黑位、亮核、微弱结构、噪声、星色与通道裁剪。星云被压平、核溢出、背景截断或噪声失控时
reject；根据具体观察最多修订一次。
