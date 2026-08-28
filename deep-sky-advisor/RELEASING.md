# Releasing deep-sky-advisor

`deep-sky-advisor` 是独立的源码发布单元，目前不生成 ZIP 发布包。

从仓库根目录执行：

```bash
python3.12 -m venv deep-sky-advisor/.venv
deep-sky-advisor/.venv/bin/python -m pip install -r deep-sky-advisor/requirements-dev.txt
deep-sky-advisor/.venv/bin/python scripts/validate_repository.py --skill deep-sky-advisor
deep-sky-advisor/.venv/bin/python -B -m pytest -p no:cacheprovider deep-sky-advisor/tests
```

发布前必须同步 `SKILL.md` 中的版本和 `CHANGELOG.md`，确认工作树中的目标变更完整且测试通过。经明确批准后，使用 `deep-sky-advisor/vX.Y.Z` 创建带注释标签和同名 GitHub Release；不得使用全局 `vX.Y.Z` 标签。
