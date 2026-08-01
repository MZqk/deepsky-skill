# Skills

一组面向 Codex 的可复用 Skills，覆盖深空天文图像诊断与处理，以及基于真实职业经历的定制简历生成。

## Skills 一览

| Skill | 用途 | 主要输出 |
| --- | --- | --- |
| [`deep-sky-advisor`](deep-sky-advisor/) | 分析 FITS、XISF、TIFF、PNG 或 JPEG 深空图像，并给出有证据支持的后期建议 | 诊断数据、预览图、处理建议报告 |
| [`deep-sky-processor`](deep-sky-processor/) | 在真实性约束下，通过分阶段审查完成深空图像后期 | 自然版和增强版 JPG，可选 TIFF 母版 |
| [`tailor-resume`](tailor-resume/) | 根据已验证的职业经历和职位描述生成 ATS 友好的定制简历 | Markdown、DOCX、PDF 简历及匹配报告 |

每个 Skill 的完整行为、约束和工作流均定义在对应目录的 `SKILL.md` 中。

## 目录结构

```text
.
├── deep-sky-advisor/    # 深空图像诊断与后期建议
├── deep-sky-processor/  # AI 主导的深空图像处理工作流
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
ln -s "$(pwd)/deep-sky-advisor" ~/.codex/skills/deep-sky-advisor
ln -s "$(pwd)/deep-sky-processor" ~/.codex/skills/deep-sky-processor
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
使用 $deep-sky-advisor 分析这张 FITS，并给出 PixInsight 后期建议。

使用 $deep-sky-processor 将这张星云图处理为自然版和增强版 JPG。

使用 $tailor-resume 根据 career-master.md 和这份 JD 生成定制简历。
```

也可以独立运行部分脚本。以下命令展示各 Skill 的基本入口：

```bash
# 分析深空图像
bash deep-sky-advisor/scripts/run_analysis.sh /path/to/input.fits /path/to/output

# 查看深空处理管线参数
python deep-sky-processor/scripts/pipeline.py --help

# 查看简历渲染器参数
python tailor-resume/scripts/render_resume.py --help
```

## 开发与验证

修改 Skill 后，应至少确认 `SKILL.md` 中引用的脚本和资料仍然存在。带测试的 Skill 可使用 pytest 验证：

```bash
python -m pytest deep-sky-advisor/tests
python -m pytest deep-sky-processor/tests
```

图像处理和文档渲染的结果还应按照对应 `SKILL.md` 的要求进行视觉检查。
