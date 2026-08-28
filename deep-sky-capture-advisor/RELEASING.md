# Releasing deep-sky-capture-advisor

`deep-sky-capture-advisor` 的发布授权只覆盖 `release-authorization.json` 和 `NOTICE.md` 指定的精确快照。治理文件、测试通过或本地构建成功都不会自动扩大该授权。

从仓库根目录执行技术检查：

```bash
python3.12 -m venv deep-sky-capture-advisor/.venv
deep-sky-capture-advisor/.venv/bin/python -m pip install -r deep-sky-capture-advisor/requirements-dev.txt
deep-sky-capture-advisor/.venv/bin/python scripts/validate_repository.py --skill deep-sky-capture-advisor
deep-sky-capture-advisor/.venv/bin/python -B -m pytest -p no:cacheprovider deep-sky-capture-advisor/tests
deep-sky-capture-advisor/.venv/bin/python -B deep-sky-capture-advisor/scripts/query_knowledge.py --verify-bundle
deep-sky-capture-advisor/.venv/bin/python deep-sky-capture-advisor/scripts/package_release.py \
  --output /tmp/deep-sky-capture-advisor-0.1.0.zip
```

只有新的显式发布授权覆盖最终运行时闭包时，才可创建 `deep-sky-capture-advisor/vX.Y.Z` 标签、GitHub Release 或执行外部发布。未来版本不得继承 `0.1.0` 的授权，也不得使用全局 `vX.Y.Z` 标签。
