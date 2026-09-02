# stars.separate

## 适用条件

只在线性父源上创建可审查 starless 分支。必须有受控 StarNet2 executable 和匹配模型；二者路径与
哈希来自本次 `probe`。Skill 不下载、解包或安装工具；缺失时报告来源和影响，由用户在 Skill 外安装，
本 session 跳过并保留完整含星父源。

## 参数

使用 Siril 1.4.4 原生 `starnet -stretch`，让预拉伸、逆变换和像素写回都留在 Siril/StarNet 内。
stride 只允许 128、256 或 512，默认 256；禁用 upsample。使用 `-nostarmask` 避免不可预测的隐式文件名，
再用 Siril PixelMath 从同一冻结父源计算星层。

## SSF 知识关系

适用性、StarNet 路径/哈希和 stride 来自当前 linear 父源、probe 与用户目标证据；本页是 SSF 的 primary
protocol reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `starnet/pm` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用本页已展开变体可记录 manual lookup `not_needed`；其他 StarNet/PixelMath 选项必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
set "core.starnet_exe=/abs/starnet2"
set "core.starnet_weights=/abs/StarNet2_weights"
load "/abs/current-parent.fit"
save "/abs/session/artifacts/060-full-source" -chksum
starnet -stretch -stride=256 -nostarmask
stat main
save "/abs/session/artifacts/060-starless" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/060-starless" 95
close
pm "$artifacts/060-full-source$ - $artifacts/060-starless$"
save "/abs/session/artifacts/060-star-layer" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/060-star-layer" 95
close
```

把 full-source、starless、星层及两份对应预览全部列为预期产物。`core.starnet_*` 的值必须逐字取自 probe，
不得读取用户全局 Siril 偏好或用搜索结果路径替代。

## 审查与回退

检查 starless 中的目标泄漏、星层中的星云/星系结构、格纹、星残留和几何。任何异常或不确定都
reject，继续使用完整含星父源。默认最终图仍必须含星。

缺失 StarNet 但本协议并非用户目标或证据所需时，只保留 probe warning，仍可 `success`。若用户明确
要求 starless/星点控制却因缺失而跳过，记录 limitation code
`starnet_unavailable_preserve_stars_baseline` 并使用 `partial_success`；非交互环境不得下载。
