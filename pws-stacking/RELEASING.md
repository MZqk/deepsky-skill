# Releasing pws-stacking

`pws-stacking` 采用「整目录自包含」打包：Skill 必须连同 `scripts/vendor/` 一并分发，因为引擎源码（`pws.py` 及其闭包）以顶层模块方式导入 `pws_params` / `DWT_DetailCore` / `xisf_io`，缺失即不可运行。

从仓库根目录执行：

```bash
python3.12 -m venv pws-stacking/.venv
pws-stacking/.venv/bin/python -m pip install -r pws-stacking/requirements-dev.txt
pws-stacking/.venv/bin/python scripts/validate_repository.py --skill pws-stacking
pws-stacking/.venv/bin/python -B -m pytest -p no:cacheprovider pws-stacking/tests
```

发布包应包含以下内容：

```text
SKILL.md
LICENSE.md
requirements.txt
references/pws-method.md
scripts/pws_run.py
scripts/precheck.py
scripts/extract.py
scripts/VENDOR.json
scripts/vendor/           # 上游引擎源码、MIT LICENSE、第三方声明（须逐位一致）
```

`CHANGELOG.md`、`RELEASING.md`、`requirements-dev.txt` 与 `tests/` 属开发与治理辅助文件，不得进入运行发布包。

## 许可边界（发布物须同时满足两条）

1. **封装层专有**：根 `LICENSE.md` 声明 `Copyright 2026 MZqk. All rights reserved.`，对应 `SKILL.md` 的 `license: Proprietary`。
2. **上游引擎保持 MIT**：`scripts/vendor/LICENSE` 及 D.Cikey 署名**不得删除或改写**。封装层的专有许可不覆盖、不改变第三方源码的 MIT 授权。

## 发布前必查

1. **vendor 完整性**：`pws-stacking/.venv/bin/python pws-stacking/scripts/extract.py --check` 必须通过，确保 `scripts/vendor/` 与 `scripts/VENDOR.json` 登记的 SHA256 一致、`vendor/LICENSE` 与 D.Cikey 署名随包保留。
2. **无 GUI 依赖**：`requirements.txt` 不得引入 PySide6；`vendor/requirements.txt` 虽列有 PySide6 属上游原貌，刻意保留、不作修改。
3. **失败契约**：`pws-stacking/tests` 全绿，确认空目录/损坏帧等错误输入仍以纯 JSON + 退出码表达。

发布前同步 `SKILL.md`、`CHANGELOG.md` 与打包内容，并对最终包执行 SkillHub dry-run 预检。经明确批准后使用 `pws-stacking/vX.Y.Z` 创建带注释标签和同名 GitHub Release；不得使用全局 `vX.Y.Z` 标签。

## 方法署名（发布物须保留）

引擎与方法由 **D.Cikey** 提出，软件 MIT、方法论文 CC BY 4.0。任何对外交付（README、发布说明、生成图注）均须保留 `SKILL.md` 第九节给出的署名句与原文链接。