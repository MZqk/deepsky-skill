# input.inspect

## 目的

建立可读输入证据，不解析未知线性状态。所有执行模式先运行一次。

## SSF 知识关系

输入路径、状态声明和通道证据来自 immutable session 与用户/采集来源；本页是 SSF 的 primary protocol
reference，提供双预览角色和参数化骨架；冻结 Siril 1.4.4 手册提供 `savejpg/autostretch/stat` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用下方完整骨架可记录 manual lookup `not_needed`；改变命令语义或选项时必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/master.fit"
stat main
savejpg "/abs/session/previews/010-input-direct" 95
close
load "/abs/master.fit"
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/010-input-autostretch" 95
close
```

把两个 JPEG 都作为 `--expect`，并保留 `*-input-direct.jpg` 与
`*-input-autostretch.jpg` 的角色后缀，finalizer 会据此选择处理前参考图。不要把 autostretch
预览当作新的科学父源或交付父源；known 路由后续科学父源仍是原 master。unknown 完成本协议的审查后
停止，获得可靠状态证据后创建新 session。

## 审查

known linear 通常使用 autostretch 观察结构，known nonlinear 通常使用 direct。unknown 只选择更能可靠
辨认结构与星点的显示；`background` 与 `color` 可记 `not_applicable`。通道来源无可靠证据时保持 channel
unknown，不从画面颜色推断。两者都不可读时停止。
