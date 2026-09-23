# deepsky-skill

[![skills.sh](https://skills.sh/b/MZqk/deepsky-skill)](https://skills.sh/MZqk/deepsky-skill)

面向 Codex 与各类 AI Coding Agent 的深空摄影 Skill 集合，覆盖拍摄知识、图像诊断、真实性约束下的后期处理，以及 Siril 天文马赛克拼接。

本仓库采用“领域单仓、独立发布单元”的组织方式：每个顶层 Skill 独立维护依赖、虚拟环境、测试、版本、许可和发布记录；仓库根目录只维护索引、结构校验、CI 和轻量路由契约。

## Skills 一览

<!-- skills-index:start -->
| Skill | 用途 | 主要输出 |
| --- | --- | --- |
| [`deep-sky-capture-advisor`](deep-sky-capture-advisor/) | 使用内置可追溯知识快照回答深空摄影器材、拍摄、后期与排障问题 | 带适用条件、审核状态与来源路径的建议 |
| [`deep-sky-advisor`](deep-sky-advisor/) | 分析 FITS、XISF、TIFF、PNG 或 JPEG 深空图像，并给出有证据支持的后期建议 | 诊断数据、预览图、处理建议报告 |
| [`deep-sky-processor`](deep-sky-processor/) | 在真实性约束下，通过分阶段审查完成深空图像后期 | 自然版和增强版 JPG，可选 TIFF 母版 |
| [`siril-moon-stacking`](siril-moon-stacking/) | 融合 Siril 1.4.4 CLI 与亚像素频域配准，完成月面幸运成像堆叠与矿物月处理 | 32 位 FITS、TIFF 与高质量 JPG 成片 |
| [`siril-mosaic`](siril-mosaic/) | 使用 Siril 自动解算、配准并拼接已堆栈天文面板 | 线性 32-bit FITS、显示预览和审计记录 |
| [`starun-siril`](starun-siril/) | 以独立、可审计的 Siril CLI 会话处理已堆栈深空 master | 可审计的 .ssf 脚本与真实像素审查产物 |
<!-- skills-index:end -->

每个 Skill 的完整行为、约束和工作流均定义在对应目录的 `SKILL.md` 中。

## 目录结构

<!-- skills-tree:start -->
```text
.
├── deep-sky-capture-advisor/ # 自包含的深空摄影知识顾问
├── deep-sky-advisor/         # 深空图像诊断与后期建议
├── deep-sky-processor/       # AI 主导的深空图像处理工作流
├── siril-moon-stacking/      # 月面幸运成像堆叠与矿物月处理
├── siril-mosaic/             # Siril 天文马赛克拼接与视觉验收
└── starun-siril/             # 独立可审计的 Siril CLI 会话处理深空 master
```
<!-- skills-tree:end -->

各 Skill 可按需要包含 `scripts/`、`references/`、`assets/`、`tests/` 和 `agents/openai.yaml`。每个 Skill 还维护自己的 `CHANGELOG.md`、许可文件、`RELEASING.md` 和 `requirements-dev.txt`。

## 安装

### 方式一：使用 Skills CLI（推荐，支持主流 Agent 并触发 skills.sh 遥测收录）

本项目支持通过开放标准 Skills CLI 一键安装到 Claude Code、Cursor、Windsurf、Codex 等主流 Coding Agent。通过 `npx skills add` 安装会自动向 [skills.sh](https://skills.sh) 触发安装遥测，完成本仓库 Skill 的登记收录与热度榜单更新：

```bash
# 安装整个仓库的所有 Skill 到当前项目 Agent
npx skills add MZqk/deepsky-skill

# 或指定安装特定 Skill（例如 deep-sky-processor）
npx skills add MZqk/deepsky-skill -s deep-sky-processor

# 全局安装到所有受支持的 Agent
npx skills add MZqk/deepsky-skill -g
```

> **遥测说明**：`npx skills add` 命令会向 `skills.sh` 遥测接口发送匿名安装事件（仅含 Skill 标识与时间戳，不包含任何隐私数据），使 Skill 自动进入 [skills.sh](https://skills.sh/MZqk/deepsky-skill) 目录与热度排行。若需关闭遥测，可设置环境变量 `DISABLE_TELEMETRY=1`。

### 方式二：手动软链接到 Codex Skills 目录

将需要的 Skill 目录软链接到 Codex Skills 目录：

<!-- skills-install:start -->
```bash
CODEX_SKILLS_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$CODEX_SKILLS_DIR"
ln -s "$(pwd)/deep-sky-capture-advisor" "$CODEX_SKILLS_DIR/deep-sky-capture-advisor"
ln -s "$(pwd)/deep-sky-advisor" "$CODEX_SKILLS_DIR/deep-sky-advisor"
ln -s "$(pwd)/deep-sky-processor" "$CODEX_SKILLS_DIR/deep-sky-processor"
ln -s "$(pwd)/siril-moon-stacking" "$CODEX_SKILLS_DIR/siril-moon-stacking"
ln -s "$(pwd)/siril-mosaic" "$CODEX_SKILLS_DIR/siril-mosaic"
ln -s "$(pwd)/starun-siril" "$CODEX_SKILLS_DIR/starun-siril"
```
<!-- skills-install:end -->

六个 Skill 使用彼此独立的 Python 虚拟环境。

## 使用

在 Codex 中直接描述任务，或明确指定 Skill：

<!-- skills-usage:start -->
```text
使用 $deep-sky-capture-advisor 根据我的器材和观测条件制定今晚的拍摄方案。

使用 $deep-sky-advisor 分析这张 FITS，并给出 PixInsight 后期建议。

使用 $deep-sky-processor 将这张星云图处理为自然版和增强版 JPG。

使用 $siril-moon-stacking 对这组月面 RAW/SER 序列进行亚像素对齐与矿物月增强。

使用 $siril-mosaic 将这个目录中的已堆栈面板拼成完整天文马赛克。

使用 $starun-siril 在独立 Siril 会话中对已堆栈 deep-sky master 进行背景扣除与拉伸。
```
<!-- skills-usage:end -->

也可以从仓库根目录独立运行脚本：

```bash
deep-sky-capture-advisor/.venv/bin/python -B \
  deep-sky-capture-advisor/scripts/query_knowledge.py \
  "城市阳台第一次拍摄" --format text

bash deep-sky-advisor/scripts/run_analysis.sh \
  /path/to/input.fits /path/to/output

deep-sky-processor/.venv/bin/python \
  deep-sky-processor/scripts/pipeline.py --help

siril-mosaic/.venv/bin/python \
  siril-mosaic/scripts/siril_mosaic.py inspect /path/to/panels
```

## 开发与验证

先为目标 Skill 创建上述独立环境，再执行它自己的测试：

```bash
deep-sky-capture-advisor/.venv/bin/python -B -m pytest -p no:cacheprovider deep-sky-capture-advisor/tests
deep-sky-advisor/.venv/bin/python -B -m pytest -p no:cacheprovider deep-sky-advisor/tests
deep-sky-processor/.venv/bin/python -B -m pytest -p no:cacheprovider deep-sky-processor/tests
siril-mosaic/.venv/bin/python -B -m pytest -p no:cacheprovider siril-mosaic/tests
```

根级结构、README 和跨 Skill 路由契约使用轻量开发环境执行：

```bash
REPO_CHECK_ENV="$(mktemp -d)/repository-contracts"
python3.12 -m venv "$REPO_CHECK_ENV"
"$REPO_CHECK_ENV/bin/python" -m pip install "pytest>=8,<10" "PyYAML>=6,<7"
"$REPO_CHECK_ENV/bin/python" scripts/validate_repository.py
"$REPO_CHECK_ENV/bin/python" -B -m pytest -p no:cacheprovider tests
```

图像处理结果仍须按照各 Skill 的视觉与真实性门禁检查；测试通过不能替代真实图像验收。

## 独立发布

每个 Skill 的发布步骤见其 `RELEASING.md`，变更记录见其 `CHANGELOG.md`。标签必须使用 `<skill>/v<version>`，例如 `siril-mosaic/v1.0.1`；不要创建含义不明确的全局 `v1.0.1` 标签。

Capture Advisor 和 Siril Mosaic 具有精确发布打包器。发布检查只构建本地候选包，不执行上传：


```bash
RELEASE_DIR="$(python3.12 -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="deepsky-release-")).resolve())')"

deep-sky-capture-advisor/.venv/bin/python \
  deep-sky-capture-advisor/scripts/package_release.py \
  --output "$RELEASE_DIR/deep-sky-capture-advisor-1.0.1.zip"

siril-mosaic/.venv/bin/python \
  siril-mosaic/scripts/package_release.py --check
siril-mosaic/.venv/bin/python \
  siril-mosaic/scripts/package_release.py \
  --output "$RELEASE_DIR/siril-mosaic-1.0.1.zip"
```

禁止直接运行 `skillhub publish siril-mosaic/`。SkillHub 的目录采集不会读取 `.gitignore`，正式发布只能使用精确白名单生成的 ZIP，并在发布前按 `RELEASING.md` 完成显式授权和 dry-run 预检。
