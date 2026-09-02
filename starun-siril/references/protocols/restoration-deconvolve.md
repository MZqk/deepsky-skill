# restoration.deconvolve

## 适用条件

只用于 linear 父源。必须能检测至少 20 个分布合理、未饱和的星点并生成 PSF；目标本身不是星点
替代物。

## 参数

- `setfindstar` 使用 Moffat、保守 roundness 与振幅门；
- `findstar` 最多 500 星；
- `makepsf` kernel 31；
- Richardson-Lucy 迭代 5–10，必须开启 TV；
- `alpha` 只在 1–10000 内，并从保守值开始。

## SSF 知识关系

适用性、星点层、PSF 和迭代参数来自当前 linear 父源、检测报告和实际像素证据；本页是 SSF 的 primary
protocol reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供相关命令语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用本页已展开参数可记录 manual lookup `not_needed`；其他 PSF/RL 选项必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
setfindstar reset -moffat -sigma=1.00 -roundness=0.35 -minA=0.01 -maxA=0.95
findstar -layer=1 -maxstars=500 "-out=/abs/session/reports/050-deconvolve/stars.tsv"
makepsf stars -ks=31 "-savepsf=/abs/session/reports/050-deconvolve/psf.fit"
rl "-loadpsf=/abs/session/reports/050-deconvolve/psf.fit" -iters=6 -tv -alpha=100.0
stat main
save "/abs/session/artifacts/050-deconvolve" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/050-deconvolve" 95
close
```

mono 使用 layer 0；RGB 通常使用绿色层 1。把星表、PSF、候选与预览全部列为预期产物。

## 审查与回退

检查振铃、噪声放大、星心、光晕和真实目标细节。星点不足、PSF 异常、振铃或改善不明确都 reject。
