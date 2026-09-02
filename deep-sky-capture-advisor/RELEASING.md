# Releasing deep-sky-capture-advisor

`deep-sky-capture-advisor` 的发布授权只覆盖 `release-authorization.json` 和 `NOTICE.md` 指定的精确快照。治理文件、测试通过或本地构建成功都不会自动扩大该授权。

## 重建内置知识快照

以下命令假定已按下文创建 `deep-sky-capture-advisor/.venv`。维护构建器只接受精确绑定的干净独立 clone（不要使用 `.git` 指向仓库外部的 linked worktree）。源页可位于历史根级 `00–09` 或当前 `knowledge-packs/deep-sky/00–09`，但只能存在一种布局；双布局会失败关闭。先在只读检查模式生成候选提交、源布局和正式页树哈希：

```bash
deep-sky-capture-advisor/.venv/bin/python -B \
  deep-sky-capture-advisor/scripts/build_knowledge_bundle.py \
  --inspect-source \
  --source /path/to/StarunWiki \
  --expect-source-remote git@github.com:MZqk/StarunWiki.git
```

人工核对输出后，必须通过独立可信来源核对提交，再把同一 `source_commit`、`formal_page_sha256` 和精确 `origin` 作为 pins 传回 `--check`；缺少任一项都会在读取 Git 前失败：

```bash
deep-sky-capture-advisor/.venv/bin/python -B \
  deep-sky-capture-advisor/scripts/build_knowledge_bundle.py \
  --source /path/to/StarunWiki \
  --expect-source-remote git@github.com:MZqk/StarunWiki.git \
  --expect-source-commit <40-or-64-character-commit> \
  --expect-formal-sha256 <64-character-formal-tree-sha256> \
  --check
```

确认检查结果和新的发布授权后，才可把 `--check` 改为 `--replace`。构建器设置 120 秒来源操作总预算和每个 Git 子进程 30 秒上限；stdout/stderr、正式页数量、单页/总包字节数及工作树扫描项数都有硬上限，超限或超时会终止整个进程组。Git 使用最小环境，并要求 OS 隔离：macOS 使用 Apple Command Line Tools Git 与 `sandbox-exec`，Linux 使用 Bubblewrap。后端缺失或策略无法应用时失败，不会静默降级。本机验收只实测 macOS 后端；公开发布前还必须以 Linux CI 的真实 Bubblewrap 结果为准，不把静态命令测试描述成等价实证。`sandbox-exec` 已被 Apple 标记为 deprecated，因此这里只把它作为当前 macOS 的纵深防御；一旦系统移除或拒绝该策略，构建必须停止。`origin` 精确绑定只是本地 Git 配置断言，不等同于签名提交或远端所有权的密码学认证。

从仓库根目录执行技术检查：

```bash
python3.12 -B -m venv deep-sky-capture-advisor/.venv
deep-sky-capture-advisor/.venv/bin/python -B -m pip install -r deep-sky-capture-advisor/requirements-dev.txt
deep-sky-capture-advisor/.venv/bin/python -B scripts/validate_repository.py --skill deep-sky-capture-advisor
deep-sky-capture-advisor/.venv/bin/python -B -m pytest -p no:cacheprovider deep-sky-capture-advisor/tests
deep-sky-capture-advisor/.venv/bin/python -B deep-sky-capture-advisor/scripts/query_knowledge.py --verify-bundle
deep-sky-capture-advisor/.venv/bin/python -B deep-sky-capture-advisor/scripts/package_release.py \
  --output /tmp/deep-sky-capture-advisor-1.0.1.zip
```

只有新的显式发布授权覆盖最终运行时闭包时，才可创建 `deep-sky-capture-advisor/vX.Y.Z` 标签、GitHub Release 或执行外部发布。未来版本不得继承 `1.0.1` 的授权，也不得使用全局 `vX.Y.Z` 标签。
