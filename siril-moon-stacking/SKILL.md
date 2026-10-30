---
name: siril-moon-stacking
description: >
  Align, stack and enhance lunar RAW/FITS/SER/AVI/MP4 captures with Siril and Python.
  Use for lunar lucky imaging, wavelet sharpening, Drizzle, atmospheric dispersion correction
  or mineral moon processing, including Seestar, planetary camera and telephoto captures.
  用于月球、月面的幸运成像选帧堆叠、配准、小波反卷积、色散校正与矿物月处理。
license: Proprietary
metadata:
  slug: siril-moon-stacking
  version: "1.2.1"
  displayName: Siril Moon Stacking
  summary: AI 主导的月面天文摄影与幸运成像处理助手，融合 Siril 1.4.4 CLI 与亚像素频域配准。
  tags: "astronomy, lunar, siril, lucky-imaging"
  homepage: https://github.com/MZqk/deepsky-skill
---
# Siril Moon Stacking

使用 Siril CLI 和 Python 处理月面 RAW/FITS 帧序列、SER 录像及 AVI/MP4/MOV/MKV/M4V 视频。按用户要求运行完整流程或指定阶段；缺少输入路径或独立工作目录时再询问。

## 真实性与数据安全

- 细节恢复仅限对光学和视宁度低通特性的数学还原，如小波重构或物理 PSF 反卷积；禁止用生成式模型或其他方法凭空添加月坑、裂谷和纹理。
- 矿物月色彩必须基于传感器捕获的色差，不得任意着色或伪造月海边界。
- 不得在原始输入目录中创建、修改或覆盖数据；`--work` 必须指向独立工作目录。已有工作目录与输入或处理参数不兼容时使用新目录。

## 环境与基本流程

将下面三个变量设为实际绝对路径，并在同一 shell 中使用；换用新 shell 时重新设置。依赖安装和脚本执行必须使用同一个 Python 解释器。需要 Siril 1.4.4+、numpy、astropy、OpenCV 和 scikit-image；兼容版本的 `opencv-python` 也可替代 `opencv-python-headless`。

```bash
TASK_SKILL_DIR="/absolute/path/to/siril-moon-stacking"
TASK_PYTHON="/absolute/path/to/venv/bin/python"
TASK_SIRIL="/absolute/path/to/siril-cli"

# Inspect Siril and Python dependencies
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" probe

# Process and verify a complete sequence
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" all --input /path/to/input --work /path/to/work --deconv auto
```

`probe` 始终输出 JSON：`ready: true` 表示基础依赖和版本检查通过；缺失、不兼容或无法验证时返回非零退出码，并在 `errors` 中说明。标准版 OpenCV 可用但会在 `warnings` 中提示。探测不会安装依赖；需要安装时，在用户选定的环境中执行 `"$TASK_PYTHON" -m pip install -r "$TASK_SKILL_DIR/requirements.txt"`。

macOS 默认 Siril 路径为 `/Applications/Siril.app/Contents/MacOS/siril-cli`；`--siril` 可指定其他绝对路径或 `PATH` 中的命令名，且必须放在子命令之前。`all` 已包含质检。也可在同一工作目录中执行所需阶段：

```bash
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" import --input /path/to/input --work /path/to/work
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" register --work /path/to/work
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" stack --work /path/to/work
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" postprocess --work /path/to/work --deconv auto
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" verify --work /path/to/work
```

- 默认使用剔除均值堆叠与自动 Drizzle 判断；缺少可信焦距、口径或像元参数时，`--deconv auto` 跳过物理反卷积。防过锐与自适应小波默认启用，不要为了让处理通过而关闭保护。
- 后处理的 `--master` 必须位于工作目录内，且软链接解析后仍在目录内；外部母版请先复制到工作目录。
- SER 的正数 `--limit` 截取前 N 帧；普通视频默认扫描并按质量选择，`--limit 0` 仍会筛选。涉及选帧或空间预算时先读输入参考。
- 多面板拼接使用 `--mosaic-mode tile`，并保持各面板尺度和 profile 一致；默认 `disc` 面向完整月盘。
- 工作目录的典型产物包括 `moon_master.fit`、`moon_natural.tif`、`moon_natural.jpg`、`verify_report.json` 和 `comparison.html`；彩色输入可生成 `moon_mineral.jpg`，单色输入不生成矿物月成片。
- 检查质检报告、相关回执与实际导出文件；报告失败或证据不足，不要宣称细节已被证实。

## 按需读取

只读取与当前任务有关的参考文档；需要具体选项时运行 `"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" <command> --help`。

| 场景 | 参考文档 |
| :--- | :--- |
| RAW/FITS、SER 或视频导入、选帧、空间预算、缓存和配准 | [输入、选帧与配准](references/input-and-registration.md) |
| 堆叠、Drizzle、Siril CLI 或配准矩阵 | [Siril CLI 参考](references/siril-144-cli.md) |
| 智能望远镜设备识别、光学先验和来源 | [设备规格表](references/device_specs.md) |
| 反卷积、小波、防过锐、色彩或马赛克 profile | [后处理](references/postprocessing.md) |
| 输出、质检、对比器或故障恢复 | [产物、质检与故障恢复](references/quality-and-troubleshooting.md) |
| 需要解释月面处理原理或真实性边界 | [月面处理指南](references/lunar_processing_guide.md) |
