# color.calibrate

## 适用条件

只用于 linear broadband 或 dualband-OSC 三通道父源。PCC/SPCC 必须有有效 WCS。默认 offline，优先
已配置并由 probe 冻结的本地 Gaia；只有非 offline session 显式 `-catalog=gaia` 才允许远程查询。

## 方法

- `neutral`：不增加光度真实性声明；没有可靠 WCS/设备证据时使用或跳过。
- `pcc`：有有效 WCS 时必须显式使用 `pcc -catalog=gaia|localgaia`。
- `spcc`：同样必须显式指定 `-catalog=gaia|localgaia`；传感器、滤镜和白参考必须来自用户或采集证据，
  不从设备型号猜测。

## SSF 知识关系

适用性、校准方法和设备参数来自 WCS、用户/采集来源与当前父源证据；本页是 SSF 的 primary protocol
reference，提供边界和参数化骨架；冻结 Siril 1.4.4 手册提供命令语法语义；`command-policy.json` 独立决定
执行授权。Agent 生成单协议 SSF 及同 stem provenance，不把本页串成固定流水线。下方 local/online PCC
完整变体可记录 manual lookup `not_needed`；neutral、SPCC 或未展开选项必须先查询原文并保留 evidence。

## 参数化 SSF 骨架

```ssf
requires 1.4.4 1.5.0
set32bits
set "core.catalogue_gaia_photo=/abs/local-gaia"
load "/abs/current-parent.fit"
pcc -catalog=localgaia
stat main
save "/abs/session/artifacts/040-color" -chksum
autostretch -linked -2.8 0.22
savejpg "/abs/session/previews/040-color" 95
close
```

online Gaia 删除 `set` 并使用 `-catalog=gaia`；这是本 Skill 唯一允许的在线 Siril 路径。SPCC 用官方
1.4.4 `spcc` 选项并显式指定 catalog，所有字符串严格引用；
不得添加协议未说明的 profile 字段。

## 审查与回退

检查背景中性、星色多样性、颜色连续性和通道裁剪。PCC/SPCC 失败、色彩断层或偏色恶化时 reject；
不得把显示调色描述成光度校准。
