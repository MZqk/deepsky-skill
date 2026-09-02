# Session Contract v1

Session 是独立、可审计的工作区，不是 workflow 状态机，也不依赖外部仓库。

```text
session/
├── session.json
├── manifest.json
├── final-selection.json
├── scripts/
│   ├── <run-id>.ssf
│   └── <run-id>.provenance.json
├── runs/
├── reviews/
├── artifacts/
├── previews/
├── reports/
│   └── manual-evidence/
│       └── bundle-verification.json
├── logs/
├── runtime/decode-validation/
├── runtime/siril-configs/
└── outputs/
    ├── reference.jpg
    └── final.jpg
```

## 不变量

- manifest、session、final-result 和 finalization 都使用 standalone v1 schema。
- 原输入必须是普通文件，且 SHA-256 始终匹配 `session.json`。
- `.ssf`、运行输出、receipt 和正式输出不得是符号链接。
- Agent 不得编辑 `session.json`、`manifest.json` 或 `runs/*.json`。
- Agent 可以创建 `scripts/*.ssf`、对应 `scripts/*.provenance.json`、`reports/manual-evidence/*.json`、
  `reviews/*.json` 和 `final-selection.json`。
- `runtime/` 下的重开脚本和 initfile 由 CLI 独占。
- `session.context.container_validation` 由 `init` 冻结，之后不可更改。
- `session.execution_policy` 冻结 fail-closed 日志诊断与默认离线网络策略。新 receipt 必须绑定完整原始日志、
  由该日志重算的 diagnostics 和实际 `--offline` 调用状态；日志、字段或网络分类漂移均失败关闭。
- `session.context.input_state` 同样冻结。unknown session 只完成 `input.inspect` 后停止；获得可靠状态证据
  时创建新 session，不修改旧 session，也不把诊断预览升级为处理或交付父源。
- `init` 自动完整验证冻结手册 Bundle 和 command policy，将组件 fingerprint、policy capture 与
  `reports/manual-evidence/bundle-verification.json` 绑定进 session；该文件只证明 Bundle 闭包，不算某条
  命令的 manual lookup evidence。
- 每个交给 `run` 或 `--validate-only` 的 Agent SSF 必须有同 stem provenance sidecar；它绑定脚本、唯一
  primary protocol reference、policy、实际 manual command/read 证据和参数理由。缺失、哈希漂移、路径逃逸、
  symlink 或协议不匹配都在执行前失败关闭。
- 每个 run receipt 绑定协议、脚本、SSF provenance、父源、工具、日志及其诊断、实际网络模式、预期产物和输出验证。
- 没有 `execution_policy` 的早期 v1 session 保持可读：已有 receipt 可 replay/finalize，且历史 receipt 可缺少
  diagnostics；此类 session 不得创建新的静态验证或实际 run，继续处理必须重新 `init`。

## 最终提交与恢复

`manifest.finalization` 使用 `starun-siril.finalization.v1`，固定记录 `state`、`selection_sha256`、
`status`、`final_result_sha256`、`audit_sha256`、`retention_policy` 和 `cleanup_completed`。仅
`success|partial_success` 再记录 `reference_sha256` 与 `final_sha256`。

`reports/final-result.json` 使用 `starun-siril.final-result.v1`，状态与 selection、audit 互相绑定。
提交后中断时，相同 selection 的 `finalize` 只验证提交并完成待办清理；不同 selection 失败关闭，不改写
既有记录。

## 状态与保留

- `success|partial_success`：存在正式 reference/final，提交后默认清理阶段图像。
- `review_required`：保留已验证候选 fingerprint 和审计现场，但没有正式 reference/final。
- `failed`：保留结构化错误和现场，没有候选或正式图像。
- `--keep-intermediates`：禁止成功分支清理，不降低验证或审查要求。

保留脚本及 provenance、manual evidence、运行时重开脚本、initfile、JSON receipts、日志、tool probe、manifest、final result 和 final
audit。恢复处理时从最后一个成功且被接受的 run 继续创建新编号脚本，不重用旧编号。
