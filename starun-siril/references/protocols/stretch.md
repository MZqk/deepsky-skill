# stretch

## 适用条件

linear 父源变为非线性显示图时必需。nonlinear 和 unknown 不使用该协议。若没有可接受 stretch，
停止而不是把线性图伪装成成片。

## 参数

- 默认 `autostretch -linked SHADOW TARGET`；`-8.0<=SHADOW<=-2.8`，
  `0.08<=TARGET<=0.18`；
- `asinh -human STRENGTH OFFSET -clipmode=rgbblend` 只在亮核与微弱结构动态范围需要时使用，
  `20<=STRENGTH<=55`，之后仍执行 linked autostretch；
- 有 StarNet 分支时使用全部参数显式的 MTF/Asinh/GHS 链，并分别传递原始 full/starless。

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

## Explicit native transfer chains (0.2.0)

Keep the linked autostretch full-stars baseline. Starless branches require explicit ordered commands; the executor records parameters from the verified SSF and does not generate scripts or accept incomplete auto-derived chains. GHS bounds: 0≤D≤10, -5≤B≤15, 0≤LP≤SP≤HP≤1. Apply all RGB channels with `-human -clipmode=rgbblend`; independent/saturation/channel-only stretches are forbidden. All five numeric GHS parameters are explicit for a starless branch.

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-linear-parent.fit"
mtf 0 0.01 1
asinh -human 2 0 -clipmode=rgbblend
ght -D=0.2 -B=0 -LP=0 -SP=0.2 -HP=1 -human -clipmode=rgbblend
save "/abs/session/artifacts/070-explicit-stretch" -chksum
stat main
savejpg "/abs/session/previews/070-explicit-stretch" 95
close
```

These values illustrate syntax, not image-independent defaults. Consult the frozen `mtf/asinh/ght` manuals for this chain and record evidence and actual parameter reasons. Recomposition applies the same chain independently to the original full and starless sources.
