# Stage 2 · 边界校正与几何修整

边界检测仅提供当前科学父源的候选建议。Agent 实际确认构图与无效边界后选择是否执行裁切；采用须绑定同源成功运行和已接受审查，跳过须记录实际理由。检测建议不授权裁切，既有检测与裁切限制不变。


在输入检查之后评估几何修正；没有实际无效边时跳过。保留输入图像域，不在本阶段求解 WCS；裁切候选的 WCS 异常必须在本阶段拒绝，不能留给 Stage 4 修复。

本阶段集中维护协议正文、参数、SSF 骨架及专属审查。provenance 按协议到阶段映射，以首份阶段文件为唯一 primary、其余为 supporting，绑定完整文件 SHA-256。通用质量门见 [quality](../quality.md)。

## 形态保护

保护区域参见 [目标形态参考](../target-morphology-review.md)，裁切坐标仍由实际像素决定。

- **大尺度广角星野与星云复合体 (如 M8/M20 广角同框、北美洲星云)**：
  - 星云边缘经常自然蔓延至画幅边界，此时必须极度保守，宁可保留轻微不齐边缘，也严禁将弥散氢发射气体当成边缘切除；
- **倾斜大星系 (如 M31 仙女座星系, M51 涡状星系)**：
  - 检查外围旋臂及微弱伴星系位置，严禁因追求画面对称而切断旋臂外缘暗尘埃带；
- **球状星团与致密行星状星云 (如 M13, M57)**：
  - 检查实际位置及外围恒星；即使主体居中，也只在原子协议边界内裁掉有证据的无效边。

---

## 故障定位

通用定位方法参见 [排查参考](../troubleshooting.md)。

| 故障现象 | 潜在原因 | 排查与处置对策 |
|---|---|---|
| **裁切后 WCS 坐标失效** | 裁切或坐标更新异常 | 按原子协议拒绝候选并保留父源；不得接受异常候选再依赖 Stage 4 修复。 |
| **画面依然存在斜向黑角** | 旋转或配准覆盖留下无效边 | 在原子协议允许范围内依据实际边界修订坐标；无法兼顾主体与边界时保留父源。 |
| **过度裁切导致主体局促** | 误将星云外部暗尘埃判定为无效边缘 | 回退裁切操作，放宽裁切包围盒，确保天体构图舒展。 |

<a id="geometry-crop-near-black"></a>
## geometry.crop-near-black


<a id="geometry-crop-near-black-适用条件"></a>
### 适用条件

仅在 direct/autostretch 预览显示近黑堆栈边、配准空边或明显无效边界时使用。目标贴边、裁切证据不清
或 WCS 无法验证时跳过。

<a id="geometry-crop-near-black-参数协议"></a>
### 参数协议

- 每侧最多裁 3.5%；优先 0.5% 递增；
- 保留面积至少 70%；
- 输出宽高使用偶数；
- 坐标与尺寸来自实际预览和输入几何，不凭目标类型猜测。

<a id="geometry-crop-near-black-ssf-知识关系"></a>
### SSF 知识关系

是否裁切及坐标尺寸来自当前父源几何、WCS 和实际预览证据；本协议对应的阶段文件是 SSF 的 primary protocol reference，
提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供 `crop` 语法语义；`command-policy.json` 独立决定执行
授权。Agent 生成单协议 SSF 及同 stem provenance，不把本节串成固定流水线。逐字采用下方完整变体可记录
manual lookup `not_needed`；其他 crop 选项或语法不确定时必须查询原文。

<a id="geometry-crop-near-black-参数化-ssf-骨架"></a>
### 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
load "/abs/current-parent.fit"
crop X Y WIDTH HEIGHT
stat main
save "/abs/session/artifacts/020-crop" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/020-crop" 95
close
```

nonlinear 父源删除 `autostretch`。预期产物为 FITS 与 JPEG。

<a id="geometry-crop-near-black-审查与回退"></a>
### 审查与回退

比较父源和候选，确认目标、星晕、构图未受损，黑边确实减少，尺寸变化与预期一致。任何裁错、WCS
异常或改善不明确都 reject，保留父源。
