# Releasing starun-siril

`starun-siril` 只从 `release-files.txt` 的固定核心白名单和唯一
`@component references/siril-manual/manifest.json` 组件闭包构建。不得直接打包整个 Skill 目录，也不得把
测试、缓存、session、维护构建器、源 archive 或 transaction journal 放入 ZIP。

## 本地候选验收

从仓库根目录执行：

```bash
python3.12 -m venv starun-siril/.venv
starun-siril/.venv/bin/python -m pip install -r starun-siril/requirements-dev.txt
starun-siril/.venv/bin/python scripts/validate_repository.py --skill starun-siril
starun-siril/.venv/bin/python -B -m pytest -p no:cacheprovider starun-siril/tests
starun-siril/.venv/bin/python starun-siril/scripts/query_siril_manual.py --verify-bundle
starun-siril/.venv/bin/python starun-siril/scripts/package_release.py --check
RELEASE_DIR="$(starun-siril/.venv/bin/python -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="starun-siril-release-")).resolve())')"
starun-siril/.venv/bin/python starun-siril/scripts/package_release.py \
  --output "$RELEASE_DIR/starun-siril-0.1.0.zip"
```

停止条件：任一测试或验证失败、白名单漂移、组件闭包/哈希/许可证漂移、ZIP 不可复现、便携解包后的
`probe/init/run --validate-only` 失败，或真实 Siril 前向验收失败。不要用单元测试替代真实 FITS/XISF
重开与最终 JPEG 视觉检查。

## 发布边界

`--check` 和普通构建只产生本地 `valid_candidate`；receipt 必须为 `publishable: false`。发布 receipt 绑定
展开后的文件清单、Siril 手册 manifest/files/tree、组件许可证和 notices 的 SHA-256。

当前手册组件许可结论仍为 `NOASSERTION`，MuniPack 派生段落的准确 GNU FDL 版本与平台对混合或路径级
许可的支持尚未确认。因此不得公开上传，不得创建发布标签，也不得把自行编写的授权 JSON 当作许可
批准。`package_release.py` 中的 SkillHub preflight 代码作为维护兼容功能保留，但源码门固定关闭；当前
版本的发布验收不执行这个不可达分支。

未来若独立完成法律审查、平台能力确认和可验证的人工授权机制，应在新版本中重新设计并测试公开发布
流程；不能仅切换一个布尔常量。若平台不支持混合许可，应把手册拆成独立许可包。

经明确批准后，标签必须为 `starun-siril/vX.Y.Z`，不得使用全局 `vX.Y.Z`。正式发布前重新确认
slug/version 尚未存在，且只上传经过上述验收的精确 ZIP。
