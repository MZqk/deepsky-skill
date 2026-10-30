# Changelog

本文件记录 `pws-stacking` 的独立版本变更。

## [1.0.4] - 2026-10-28

- Keep stdout JSON available when JSON archival fails, preserve the original error or stack result, and report `json_out_error` with exit code 1 without a traceback.
- Cover missing archive parents, directory destinations, permission errors, disk-full errors, and successful or failed archival after a completed stack.

## [1.0.3] - 2026-10-28

- Resolve command examples and the dependency manifest from the loaded skill directory, preserving the user's working directory and relative data paths.

## [1.0.2] - 2026-10-28

- Emit JSON errors for runner validation failures, including non-default B, an empty frames directory, a missing photos directory, and an empty output directory; preserve stderr diagnostics and exit code 20.
- Add regression coverage for stdout JSON, matching `--json-out` files, and the existing argparse exit code 2 contract; clarify the documented output boundary.

## [1.0.1] - 2026-10-28

- Change `metadata.tags` from a YAML list to a string to comply with the Agent Skills specification.

## [1.0.0] - 2026-10-05

- 首次纳入本仓库治理：`skills.manifest.json` 登记、README 四处标记区同步，补齐 `requirements-dev.txt`、`CHANGELOG.md` 与 `RELEASING.md`。
- 明确双重许可边界：封装层按专有许可发布并新增 `LICENSE.md`，`SKILL.md` 的 `license` 改为 `Proprietary`；随包的上游引擎源码仍保持 MIT（`scripts/vendor/LICENSE`），D.Cikey 署名与方法出处不变。
- 运行时依赖清单由 `scripts/requirements.txt` 上移至 Skill 根 `requirements.txt`，与仓库内其他 Skill 的约定一致；`scripts/vendor/requirements.txt` 保持上游原样未动。
- 修复 `precheck.py` 在无匹配帧目录（空目录或仅含非帧文件）时抛 `IndexError` 的缺陷：现按预检契约输出纯 JSON 与退出码 20，不再向用户抛栈。
- 修复首帧损坏时探针读帧异常直接外泄的问题，现归入 `input` FAIL 阻断项。
- 强制拦截 `--out` 与 `--photos` 同目录：成品 `stack_<tag>.xisf` 会被下一轮 `glob('*.xisf')` 当成帧读入，形状一致故不报错却静默产出废图。此前该风险仅写在文档里、代码无任何防护（引擎的报错文案虽提及但只检查了空串）。`precheck.py` 新增 `--out` 参数独立校验，`pws_run.py` 在调用预检前先行拦截，二者均以 exit 20 拒绝。
- 文档订正：`--tag` 留空回落到 `stack`，**不存在**上游参数表所述的「随素材目录名自动跟随」；`VENDOR.json` 的 `src` 为构建者本机绝对路径，在他人机器上必然失效。
- 文档补全 `precheck.py` 专有开关（`--sample` / `--all-frames` / `--out` / `--pretty`）并标注与 `pws_run.py` 侧的名称差异；至此两个入口的全部 30 个开关均在 `SKILL.md` 有据可查。
- 新增 `tests/test_precheck.py`，锁定上述失败路径与 CLI 的 JSON/退出码契约。

## [0.1.0] - 2026-10-05

- 建立 PWS 分区加权叠加封装的首个治理版本基线：无界面运行器、fail-fast 预检、锁定 B=2 与强制显式 `--frames-dir`。
