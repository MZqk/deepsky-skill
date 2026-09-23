# Releasing siril-moon-stacking

`siril-moon-stacking` 是独立的源码发布单元，采用独立语义化版本。

从仓库根目录执行：

```bash
python3.12 -m venv siril-moon-stacking/.venv
siril-moon-stacking/.venv/bin/python -m pip install -r siril-moon-stacking/requirements-dev.txt
siril-moon-stacking/.venv/bin/python scripts/validate_repository.py --skill siril-moon-stacking
```

发布前必须同步 `SKILL.md` 中的版本和 `CHANGELOG.md`，确认工作树中的目标变更完整且测试通过。经明确批准后，使用 `siril-moon-stacking/vX.Y.Z`（或 `skill/siril-moon-stacking/vX.Y.Z`）创建带注释标签和同名 GitHub Release；不得使用全局 `vX.Y.Z` 标签。
