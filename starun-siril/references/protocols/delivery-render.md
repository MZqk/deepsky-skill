# delivery.render

## 适用条件

当前父源必须是按默认阶段顺序或合法定向模式形成、已经审查接受的 nonlinear 图。默认星点政策要求
含星。unknown session 和 `input.inspect` 的 direct/autostretch 诊断预览不得进入本协议。

## SSF 知识关系

父源资格、星点要求和 JPEG 参数来自 selected lineage、用户选择与实际像素证据；本页是 SSF 的 primary
protocol reference，提供交付边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `savejpg/load/stat` 语法语义；
`command-policy.json` 独立决定执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定
流水线。逐字采用下方完整骨架可记录 manual lookup `not_needed`；改变命令或选项时必须查询原文。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
stat main
savejpg "/abs/session/artifacts/110-final-candidate" 95
close
load "/abs/session/artifacts/110-final-candidate.jpg"
stat main
close
```

把最终候选 JPEG 作为预期产物。不要直接写 `outputs/final.jpg`；finalizer 在验证 review receipt 后复制
并固定该路径。

## 最终审查

实际打开最终候选与最终父源预览，五项质量门全部通过才 accept。父源预览必须是生成当前父源的成功
run 中已验证的显示图；已知 nonlinear 原输入作为父源时使用成功 `input.inspect` 的 direct 预览。unknown
不得进入本协议。任何 uncertain 都使用 `review_required`。
最终 review receipt 必须列出两份不同的 session 图像：最终候选和最终父源预览；`delivery.render` 的
五项门全部写 `pass`。强青/绿色背景、明显梯度、通道裁剪、伪结构或星点异常必须 fail，并以
`review_required` 结束。
