处理 deep-sky-capture-advisor 的 4 个结构不规范项（保留 agents/openai.yaml 于闭包与发布包，仅修 $ 语法；不重建 dist 中的 zip，改动只落仓库）。

## 1. 删除陈旧产物
`git rm deep-sky-capture-advisor/tests/trace-results-0.1.0.json`（0.1.0 时代的运行残留，schema_version 1，无任何代码引用）。

## 2. 统一 $ 语法 → 自然语言/裸技能名
- `SKILL.md:37`：「文件实测诊断应交给 `$deep-sky-advisor`，实际像素处理应交给 `$deep-sky-processor`」→「…应交给 deep-sky-advisor 技能，…应交给 deep-sky-processor 技能」。
- `scripts/query_knowledge.py:763,765`：`recommended_route` 返回值 `"$deep-sky-processor"`→`"deep-sky-processor"`、`"$deep-sky-advisor"`→`"deep-sky-advisor"`。
- `tests/test_query_knowledge.py:282`：断言同步改为 `== "deep-sky-advisor"`。
- `agents/openai.yaml:4`：default_prompt 去掉 `$` 前缀（`使用 deep-sky-capture-advisor 根据…`）。

## 3. release-authorization.json 移出运行时闭包与发布白名单
- `scripts/knowledge_common.py:41-43`：`OPTIONAL_RUNTIME_FILES` 收窄为 `("NOTICE.md",)`，新增 `EXCLUDED_RUNTIME_FILES = ("release-authorization.json",)`。
- `expected_runtime_file_hashes()`（knowledge_common.py:499）：跳过 EXCLUDED 文件。
- `scripts/package_release.py:40-47`：`STATIC_RUNTIME_FILES` 移除 `"release-authorization.json"`。
- 文件本身保留在仓库供治理/审计；`_validate_authorization` 仍从磁盘读取并与 LOCKED_RELEASE 比对，授权链不受影响（LOCKED_RELEASE 只钉 source_commit/catalog/knowledge 哈希，均不变）。
- 已确认相关测试仍成立：授权漂移测试改走 "Authorization lock mismatch for future_changes_automatically_authorized" 错误路径（正则第一分支命中）；extracted-zip 自包含测试的闭包集合一致。

## 4. 同步 references/manifest.json 的 runtime_files
- 删除 `"release-authorization.json"` 条目。
- 重算并更新本次被修改文件的 SHA-256：`SKILL.md`、`scripts/query_knowledge.py`、`scripts/knowledge_common.py`、`agents/openai.yaml`（保持 json 缩进/键序格式）。

## 5. CHANGELOG.md
在未提交的 1.0.3 条目追加 bullets：结构规范修正内容，并明确注明 dist 中的 1.0.3 zip 未随之重建（旧产物仍含 release-authorization.json）。不改 metadata.version、LOCKED_RELEASE、release-authorization.json 内容。

## 6. 验证
- `.venv/bin/pytest tests/ -q` 全量回归。
- `python3 -B scripts/query_knowledge.py --verify-bundle` → ok:true（校验闭包声明与磁盘一致）。
- 跑一次越界路由 query，确认 `recommended_route == "deep-sky-advisor"`。
- 打包链路冒烟：`python3 -B scripts/package_release.py --output /tmp/dsca-verify.zip` 应成功且产物文件列表不含 release-authorization.json，验证后删除临时 zip（不触碰 dist/）。