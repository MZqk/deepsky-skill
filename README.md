# Skills

一组面向 Codex 的可复用 Skills，覆盖深空摄影知识问答、天文图像诊断与处理，以及基于真实职业经历的定制简历生成。

## Skills 一览

| Skill | 用途 | 主要输出 |
| --- | --- | --- |
| [`deep-sky-capture-advisor`](deep-sky-capture-advisor/) | 使用内置可追溯知识快照回答深空摄影器材、拍摄、后期与排障问题 | 带适用条件、审核状态与来源路径的建议 |
| [`deep-sky-advisor`](deep-sky-advisor/) | 分析 FITS、XISF、TIFF、PNG 或 JPEG 深空图像，并给出有证据支持的后期建议 | 诊断数据、预览图、处理建议报告 |
| [`deep-sky-processor`](deep-sky-processor/) | 在真实性约束下，通过分阶段审查完成深空图像后期 | 自然版和增强版 JPG，可选 TIFF 母版 |
| [`siril-mosaic`](siril-mosaic/) | 使用 Siril 自动解算、配准并拼接已堆栈天文面板 | 线性 32-bit FITS、显示预览和审计记录 |
| [`tailor-resume`](tailor-resume/) | 根据已验证的职业经历和职位描述生成 ATS 友好的定制简历 | Markdown、DOCX、PDF 简历及匹配报告 |

每个 Skill 的完整行为、约束和工作流均定义在对应目录的 `SKILL.md` 中。

## 目录结构

```text
.
├── deep-sky-capture-advisor/ # 自包含的深空摄影知识顾问
├── deep-sky-advisor/    # 深空图像诊断与后期建议
├── deep-sky-processor/  # AI 主导的深空图像处理工作流
├── siril-mosaic/        # Siril 天文马赛克拼接与视觉验收
└── tailor-resume/       # 职业事实管理与定制简历生成
```

各目录通常包含：

- `SKILL.md`：Skill 说明和执行规则
- `scripts/`：分析、处理或渲染脚本
- `references/`：工作流所需的参考资料
- `assets/`：模板等静态资源
- `tests/`：自动化测试（如有）
- `agents/openai.yaml`：Codex 展示信息（如有）

## 安装

将需要的 Skill 目录复制或软链接到 Codex Skills 目录。例如：

```bash
mkdir -p ~/.codex/skills
ln -s "$(pwd)/deep-sky-capture-advisor" ~/.codex/skills/deep-sky-capture-advisor
ln -s "$(pwd)/deep-sky-advisor" ~/.codex/skills/deep-sky-advisor
ln -s "$(pwd)/deep-sky-processor" ~/.codex/skills/deep-sky-processor
ln -s "$(pwd)/siril-mosaic" ~/.codex/skills/siril-mosaic
ln -s "$(pwd)/tailor-resume" ~/.codex/skills/tailor-resume
```

需要运行深空图像脚本时，请分别安装对应依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r deep-sky-advisor/requirements.txt
python -m pip install -r deep-sky-processor/requirements.txt
```

`tailor-resume` 生成 DOCX 和 PDF 时还需要 `python-docx`、`pypdf`、LibreOffice 和 Poppler。具体要求以各 Skill 的 `SKILL.md` 为准。

## 使用

在 Codex 中直接描述任务，或明确指定 Skill。例如：

```text
使用 $deep-sky-capture-advisor 根据我的器材和观测条件制定今晚的拍摄方案。

使用 $deep-sky-advisor 分析这张 FITS，并给出 PixInsight 后期建议。

使用 $deep-sky-processor 将这张星云图处理为自然版和增强版 JPG。

使用 $siril-mosaic 将这个目录中的已堆栈面板拼成完整天文马赛克。

使用 $tailor-resume 根据 career-master.md 和这份 JD 生成定制简历。
```

也可以独立运行部分脚本。以下命令展示各 Skill 的基本入口：

```bash
# 检索自包含知识快照
python3 -B deep-sky-capture-advisor/scripts/query_knowledge.py "城市阳台第一次拍摄" --format text

# 分析深空图像
bash deep-sky-advisor/scripts/run_analysis.sh /path/to/input.fits /path/to/output

# 查看深空处理管线参数
python deep-sky-processor/scripts/pipeline.py --help

# 检查天文马赛克输入目录
python siril-mosaic/scripts/siril_mosaic.py inspect /path/to/panels

# 查看简历渲染器参数
python tailor-resume/scripts/render_resume.py --help
```

## 开发与验证

修改 Skill 后，应至少确认 `SKILL.md` 中引用的脚本和资料仍然存在。带测试的 Skill 可使用 pytest 验证：

```bash
python -m pytest deep-sky-advisor/tests
python -m pytest deep-sky-processor/tests
python3 -B -m pytest -p no:cacheprovider deep-sky-capture-advisor/tests
python -m pytest siril-mosaic/tests
```

图像处理和文档渲染的结果还应按照对应 `SKILL.md` 的要求进行视觉检查。

## Siril Mosaic 发布

禁止直接运行 `skillhub publish siril-mosaic/`。SkillHub 的目录采集不会读取 `.gitignore`，直接发布会把 `dev-runs/` 中的 FITS、日志和本机配置带入候选包。只能发布由精确白名单生成的 ZIP：

```bash
python3 siril-mosaic/scripts/package_release.py --check
python3 siril-mosaic/scripts/package_release.py \
  --output dist/siril-mosaic-0.1.0.zip \
  --skillhub-preflight
```

`--skillhub-preflight` 只允许执行以下 dry-run；不得在预检阶段省略 `--dry-run`：

```bash
skillhub publish dist/siril-mosaic-0.1.0.zip --dry-run --json
```
