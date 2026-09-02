# color.map

## 适用条件

在完整处理的默认顺序中，只处理 Stage 7 已接受的单个三通道 narrowband/dualband starless 非线性父源。
`source_roles` 必须来自冻结的用户或采集证据；不得读取或组合多个通道文件。没有可信 starless 分支或
通道角色时跳过 Stage 8；定向模式仍须显式证明父源域和角色适用。

## 映射

- HOO：`R=Ha,G=OIII,B=OIII`；
- SHO：`R=SII,G=Ha,B=OIII`；
- modified-SHO：`R=(1-a)SII+aHa`，`0<=a<=0.5`；
  `G=(1-b)Ha+bOIII`，`0<=b<=0.7`；`B=OIII`；
- identity：无需运行协议。

## SSF 知识关系

适用性、source roles 和映射参数来自冻结的用户/采集来源及当前三通道父源证据；本页是 SSF 的 primary
protocol reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `split/rgbcomp/pm` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。下方 HOO 完整示例可按实际角色参数化并记录 manual lookup `not_needed`；modified-SHO PixelMath
或其他未展开映射必须先查询原文并保留 evidence。

## 参数化 SSF 骨架

以下是 `source_roles R=Ha,G=OIII,B=OIII` 的 HOO 示例；其他角色映射必须替换实际 split 通道，不能照抄
文件名：

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
split "/abs/session/artifacts/080-red" "/abs/session/artifacts/080-green" "/abs/session/artifacts/080-blue"
close
rgbcomp "/abs/session/artifacts/080-red.fit" "/abs/session/artifacts/080-green.fit" "/abs/session/artifacts/080-green.fit" "-out=/abs/session/artifacts/080-hoo.fit" -nosum
load "/abs/session/artifacts/080-hoo.fit"
stat main
savejpg "/abs/session/previews/080-hoo" 95
close
```

把 `split` 实际生成的 R/G/B 文件按 `source_roles` 代入 `rgbcomp`。modified-SHO 先用 `pm` 生成混合
通道。Stage 7 已经完成拉伸，因此不得再次调用 `autostretch`。所有临时文件保持 session-local，并列入
预期产物或后续显式验证。

## 审查与回退

检查发射线结构连续性、亮部层次、背景、星色和通道裁剪。映射是有来源的显示表达，不得宣称自然
色。结构断裂、颜色块或角色证据不完整时 reject。
