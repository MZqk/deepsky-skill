# Siril 1.4 马赛克工作流

仅在生成或调试 `.ssf`、选择 plate solver，或解释运行失败时读取本文件。

## 正式命令链

Siril 1.4 的完整画布由两处共同保证：

```siril
requires 1.4.0
setext fit
set32bits

cd "/isolated/staging"
link mosaic "-out=/isolated/process"
cd "/isolated/process"

seqplatesolve mosaic_ -order=3 -force -nocache
seqapplyreg mosaic_ -framing=max -interp=la
stack r_mosaic_ rej none -norm=addscale -overlap_norm -feather=64 -maximize -32b "-out=/isolated/outputs/mosaic_linear"
```

- `seqplatesolve` 为每个 panel 写入 WCS 和 astrometric registration 信息。
- 在线星表时 `-nocache` 为不同中心的每张图分别取星表；不能用一个中心的缓存覆盖整幅宽场。
- `-order=3` 固定 Cubic SIP，不依赖用户 GUI 偏好，并允许配准时处理畸变。
- `-framing=max` 计算所有 panel 的 bounding box；`min` 会裁成交集，`cog` 也不是完整并集。
- `stack -maximize` 创建真正包含所有 panel 的最终画布。它与 `-framing=max` 缺一不可。
- 少量已堆栈 panel 使用 mean/no rejection、additive with scaling、无权重；`-overlap_norm` 在重叠区估计亮度差，`-feather` 软化边界。
- 不对线性母版使用 `-output_norm`，也不默认使用 `-rgb_equal` 改变窄带 RGB 比例。

跨平台参数只在完整参数外加引号，例如 `"-out=/path with spaces/process"`。不要拼接 shell 命令；通过固定 argv 启动：

```text
siril-cli --initfile /isolated/run/siril-init.ini --directory /isolated/run --script /isolated/run/mosaic.ssf
```

入口先保存一份不可变的 `siril-init-source.ini`，再复制为运行时 `siril-init.ini`。Siril 只可改写后者，不能把本次工作目录、输出扩展名或位深偏好写回用户的全局配置。

## 输入导入

- 纯 FITS/FIT/FTS 用 `link`，它不会误收 JPEG 预览。
- XISF/TIFF 或混合格式用 `convert`，但 staging 中只能包含本次明确选择的 panel；当前入口不解析其嵌入式指向，因此必须显式配置本地 Astrometry.net 盲解算。
- 不使用 FITSEQ；整条 FITSEQ 不能执行该流程所需的逐帧 astrometry。
- 即使 `link` 通常创建符号链接，也必须先复制到隔离 staging。`seqplatesolve` 会更新 FITS 头，不能把用户源文件置于写入链上。

## 解算选择

默认在线：

```siril
seqplatesolve mosaic_ -order=3 -force -nocache
```

Siril 下载星表切片，不上传源图像。若所有 panel 已有可靠 WCS，可在离线模式省略 `-force`，让 Siril 从现有解算建立序列注册信息。

本机已安装 Gaia astrometric catalog 时可使用：

```siril
seqplatesolve mosaic_ -order=3 -force -catalog=localgaia
```

缺少 WCS 和近似坐标时，只在本地 `solve-field` 及覆盖实际视场的 index 已安装时使用：

```siril
seqplatesolve mosaic_ -order=3 -force -localasnet -blindpos -blindres
```

普通星点注册仅在每个 panel 都与同一参考 panel 有充分重叠时才是可证明的替代方案。真正的多面板宽场解算失败时禁止自动回退到 `register -2pass`，因为它可能静默丢掉外围 panel。

## 显示派生图

线性 FITS 保存后，重新加载并只对内存图像做 linked autostretch：

```siril
load "/isolated/outputs/mosaic_linear.fit"
autostretch -linked -2.8 0.20
savejpg "/isolated/outputs/mosaic_preview" 95
close
```

预览只用于视觉审查；不可反馈为下一次科学处理输入。

## 官方依据

- [Siril Mosaics tutorial](https://siril.org/tutorials/mosaics/)
- [Siril 1.4.4 Commands](https://siril.readthedocs.io/en/stable/Commands.html)
- [Siril plate solving](https://siril.readthedocs.io/en/stable/astrometry/platesolving.html)
- [Siril registration](https://siril.readthedocs.io/en/stable/preprocessing/registration.html)
- [Siril stacking](https://siril.readthedocs.io/en/stable/preprocessing/stacking.html)
- [Siril headless mode](https://siril.readthedocs.io/en/stable/Headless.html)

早期开发分支出现过 `seqapplyastrometry`，但 1.4.4 的正式命令是 `seqapplyreg`；不存在 `framing=union`。
