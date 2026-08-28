---
name: siril-mosaic
description: 使用 Siril 1.4+ 将同一目标的多个已校准、已堆栈 FITS 面板自动拼接为完整天文马赛克，并以输入哈希、天体测量、最大画布配准、重叠归一化、线性 FITS 和视觉审查完成验收。不用于 RAW 灯光帧校准、去拜耳或普通全景照片。
metadata:
  slug: siril-mosaic
  version: "0.1.0"
  displayName: Siril Mosaic
  summary: 使用 Siril 自动解算、配准并拼接已堆栈天文面板，交付可审计的线性 FITS 马赛克和显示预览。
  tags: [astronomy, siril, mosaic, fits]
---

# Siril Mosaic

把 Agent 作为流程控制者，把 Siril 作为唯一的配准和像素拼接引擎。始终保留源文件，最终同时交付线性 32-bit FITS 和独立的显示预览。

## 适用边界

- 输入应是同一目标、同一滤镜或同一颜色层的多个已校准/已堆栈 panel。
- FITS/FIT/FTS 是可自动读取 WCS 与近似指向的正式输入；XISF/TIFF 只有在本机盲解算 Astrometry.net 已配置时才进入执行链。
- 不因 `BAYERPAT` 仍留在 FITS 头中就重复去拜耳；三通道堆栈图仍按成品 panel 处理。
- RAW lights、暗场/平场校准、单 panel 后期和普通照片拼接不属于本 Skill。
- 不把普通 `register -2pass` 当作宽幅马赛克的静默回退。外围 panel 可能不与同一参考帧重叠，必须使用逐图天体测量。

## 开始

从本文件所在目录解析 `scripts/siril_mosaic.py` 的绝对路径，然后依次执行：

```bash
python3 /abs/siril-mosaic/scripts/siril_mosaic.py probe

python3 /abs/siril-mosaic/scripts/siril_mosaic.py inspect \
  '/abs/path/to/panels'
```

`inspect` 是只读操作。先检查 JSON 中：

- `ready=true`、`panel_count>=2`；
- 滤镜和通道数没有冲突；
- 每个缺少 WCS 的 panel 至少有近似 RA/DEC；
- 输入确实是 stacked panels，而不是尚未校准的子帧。

用户文本中的 `\~/...` 会被入口安全展开为用户主目录。不得把反斜杠或未展开的 `~` 原样交给 Siril。

## 执行

每次使用一个全新的、位于源目录之外的 run 目录：

```bash
python3 /abs/siril-mosaic/scripts/siril_mosaic.py run \
  '/abs/path/to/panels' \
  --output-dir '/abs/path/to/new-run'
```

入口会复制并校验每个输入，再在隔离副本上运行：

```text
link/convert → seqplatesolve → seqapplyreg -framing=max
→ stack -maximize -overlap_norm -feather → linear FITS
→ linked autostretch display preview
```

默认在线 plate solve 只下载每个 panel 对应的星表，不上传图像。所有 panel 已有可靠 WCS 时可用 `--offline`；缺少坐标时，只有本机已配置 Astrometry.net 和匹配索引，才使用 `--local-astrometry-net --blind-position`。

常用受控参数：

- `--feather 0|32|64|128`：接缝羽化宽度；省略时取 panel 短边约 4%。
- `--scale 0.5`：预计画布单边超过 32768 px 或内存不足时缩小配准输出。
- `--focal-mm`、`--pixel-size-um`：只在可靠元数据缺失时覆盖。
- `--keep-work`：仅在调试或用户要求保留中间文件时使用。

命令语义、联网/本地解算分支和禁止的回退见 [workflow.md](references/workflow.md)。

## 必须完成视觉闭环

进程退出 0 只表示命令结束，不是成功交付。读取 `result.json`，确认：

- `execution.status=succeeded`；
- 解算数、配准数均等于输入 panel 数；
- `union_canvas_expanded=true`；
- 线性 FITS 和 JPEG 的 SHA-256 已记录。

随后实际打开 `outputs/mosaic_preview.jpg`，按 [quality.md](references/quality.md) 检查目标完整性、重复星、接缝、内部黑洞和源结构。只有看过预览后才能记录 review：

```bash
python3 /abs/siril-mosaic/scripts/siril_mosaic.py review '/abs/path/to/new-run' \
  --verdict accept \
  --target-complete pass \
  --alignment pass \
  --seams pass \
  --black-gaps pass \
  --source-structure pass \
  --notes '实际查看完整分辨率预览后的简要证据'
```

只有最终 `result.json` 为 `status=success` 才能报告成功。任何检查为 `fail/unknown` 时使用 `--verdict review_required`。

若只是接缝轻微，可在新的 run 目录中最多再试一个相邻 feather 值；重复星、panel 缺失、内部黑洞或解算失败不能靠 feather 掩盖，应停止并报告证据。

## 交付

最终回复展示绝对路径并区分：

- `outputs/mosaic_linear.fit`：Siril 生成的线性科学/后期母版；
- `outputs/mosaic_preview.jpg`：linked autostretch 显示派生图；
- `manifest.json`、`siril.log`、`result.json`、`review.json`：输入、执行和视觉审查证据。

报告 `success|review_required|failed`、Siril 版本、panel/solve/register 数、最终尺寸、scale、feather、已知限制和是否保留中间文件。不得把预览称作线性母版，也不得把目标名或单个 `OBJECT` 头当作“完整目标已显示”的证据。
