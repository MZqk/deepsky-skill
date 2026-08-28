# Releasing siril-mosaic

`siril-mosaic` 只能从 `release-files.txt` 的精确白名单构建发布包；不得直接发布整个 Skill 目录。

从仓库根目录执行：

```bash
python3.12 -m venv siril-mosaic/.venv
siril-mosaic/.venv/bin/python -m pip install -r siril-mosaic/requirements-dev.txt
siril-mosaic/.venv/bin/python scripts/validate_repository.py --skill siril-mosaic
siril-mosaic/.venv/bin/python -B -m pytest -p no:cacheprovider siril-mosaic/tests
siril-mosaic/.venv/bin/python siril-mosaic/scripts/package_release.py --check
RELEASE_DIR="$(siril-mosaic/.venv/bin/python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="siril-mosaic-release-")).resolve())')"
siril-mosaic/.venv/bin/python siril-mosaic/scripts/package_release.py \
  --output "$RELEASE_DIR/siril-mosaic-0.1.0.zip"
```

发布前同步 `SKILL.md`、`CHANGELOG.md`、打包器版本和发布白名单，并对最终 ZIP 执行 SkillHub dry-run 预检。经明确批准后使用 `siril-mosaic/vX.Y.Z` 创建带注释标签和同名 GitHub Release；不得使用全局 `vX.Y.Z` 标签。`CHANGELOG.md`、`RELEASING.md`、测试和开发依赖不得进入运行发布包。
