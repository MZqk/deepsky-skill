# Releasing siril-astra-stacking

`siril-astra-stacking` 采用「整目录自包含」打包：Skill 必须连同 `references/` 与 `scripts/` 一并分发，因为 `SKILL.md` 明确要求按需加载 7 篇参考文档，且 `scripts/astra_stack.py` 以顶层模块方式导入 `device_signatures` / `fits_probe` / `param_derive` / `siril_script_gen`，缺失任一即不可运行。

运行时**仅依赖 Python 3.9+ 标准库**，无 numpy / astropy / sirilpy，也无 vendor 目录，因此不存在第三方许可边界问题。唯一例外：`--stack-engine=pws` 通过**子进程**调用相邻 `pws-stacking` skill 的运行器，其第三方依赖（numpy/scipy/astropy/photutils）属于彼 skill，本 skill 的代码路径仍零第三方导入；`pws_bridge.py` 对引擎缺依赖的行为是报错拒绝而非降级。

从仓库根目录执行：

```bash
python3.12 -m venv siril-astra-stacking/.venv
siril-astra-stacking/.venv/bin/python -m pip install -r siril-astra-stacking/requirements-dev.txt
siril-astra-stacking/.venv/bin/python scripts/validate_repository.py --skill siril-astra-stacking
siril-astra-stacking/.venv/bin/python -B siril-astra-stacking/scripts/selftest.py
```

发布包应包含以下内容：

```text
SKILL.md
LICENSE.md
references/01-device-signatures.md
references/02-calibration.md
references/03-registration.md
references/04-stacking.md
references/05-output-contract.md
references/06-siril-1.4-constraints.md
references/07-failure-and-resume.md
references/08-pws-engine.md
scripts/astra_stack.py
scripts/device_signatures.py
scripts/fits_probe.py
scripts/param_derive.py
scripts/pws_bridge.py
scripts/siril_script_gen.py
scripts/synth_fits.py
scripts/selftest.py
```

`CHANGELOG.md`、`RELEASING.md` 与 `requirements-dev.txt` 属开发与治理辅助文件，不得进入运行发布包。

## 发布前必查

1. **自检全绿**：`selftest.py` 第一层（无需 Siril）必须全过。第二层 `--real` 需本机 siril-cli 1.4.x 与真实数据，属人工验收，不阻塞发布。
2. **纯标准库**：`scripts/` 下不得出现 `import numpy` / `import astropy` / `import sirilpy`。这是本 Skill 的核心卖点，`SKILL.md` 环境要求节对此有明确承诺。
3. **失败契约**：`selftest.py` 的「atomic commit」与「failure keeps the previous good result」两节必须通过，确认三件套原子发布、失败不污染既有产物。
4. **产物名**：`references/05-output-contract.md` 约定的 `<target>_stacked_32bit.fits` / `<target>.png` / `<target>_report.json` 不得漂移。

发布前同步 `SKILL.md`、`CHANGELOG.md` 与发布内容，并对最终包执行 SkillHub dry-run 预检。经明确批准后使用 `siril-astra-stacking/vX.Y.Z` 创建带注释标签和同名 GitHub Release；不得使用全局 `vX.Y.Z` 标签。

> **注意**：本 Skill 当前**没有** `package_release.py` 打包器，无法像 `siril-mosaic` 那样用白名单 ZIP 收敛发布包。直接发布整个目录会带上 `CHANGELOG.md` / `RELEASING.md` / `requirements-dev.txt`。在补上打包器之前，发布前须人工按上方清单核对包内容。
