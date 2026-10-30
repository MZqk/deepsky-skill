# Session Contract v1

本文件维护当前 session 的不变量、文件权限、恢复、提交和保留合同。当前执行策略以
[`EXECUTION_POLICY`](../scripts/deep_sky_siril_contract.py)为准，由 init 一次冻结完整字典；
不能从各次版本说明拼装策略、补字段或覆盖已冻结值。处理算法及专属审查直接读取[阶段合同](#阶段专属合同)。

[版本沿革与兼容说明](session-legacy.md)仅在恢复旧证据或明确查询历史时读取，不作为新执行的默认材料。
其中“新会话”指当次引入该条目的版本，不代表当前默认。

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
- Agent 可以创建 `scripts/*.ssf`、对应 `scripts/*.provenance.json`、`reports/manual-evidence/*.json`、协议明确要求的 `reports/<run-id>/*.json` 合同与区域声明、
  `reviews/*.json` 和 `final-selection.json`。
- `runtime/` 下的重开脚本和 initfile 由 CLI 独占。
- `session.context.container_validation` 由 `init` 冻结，之后不可更改。
- `session.execution_policy` 冻结 fail-closed 日志诊断与网络策略；新 session 使用 `online_default_gaia_catalog_explicit_v3`（默认在线，`init --offline` 为显式收紧），历史 v1/v2 receipt 按各自冻结的离线优先规则重算，旧 session 仅回放与 finalize。新 receipt 必须绑定完整原始日志、
  由该日志重算的 diagnostics 和实际 `--offline` 调用状态；日志、字段或网络分类漂移均失败关闭。
- `session.context.input_state` 与 `channel_mode` 的初始值同样冻结。任一有效值为 `unknown` 时只完成 `input.inspect` 后停止，且仅允许无候选的 failed finalize。当前执行策略允许已接受的 `input.inspect` 后用 `confirm-input` 一次补齐所有未知字段；CLI 独占写入 `reports/input-confirmation.json` 并在 manifest 保存 fingerprint，`load_session` 仍返回初始上下文。协议、父源与交付使用经验证的有效上下文，不修改旧 session，也不把诊断预览升级为处理或交付父源。`channel_map=null` 不等于通道类型 unknown。
  已提交的历史 finalization 保持原有校验与回放，不重新分类或改写其结果。
- 新 session 的 `context.dualband` 默认 off，只有用户显式选择原生 OSC HOO 后在 init 传 auto；Python 默认与 CLI 一致。该值冻结，新建同源会话不恢复旧开启状态，confirm-input 不修改它。既有 auto/off 会话保持原值。
- 配色路线沿用实际 `channel_mode/channel_map` 和审查 notes、SSF provenance rationale；不新增路线字段或解析器。宽带自然 RGB 与窄带显式 SHO/HOO 独立于 style，未选择窄带映射时保留输入配色。已提交 receipt 可回放本次登记的精确旧知识引用，旧引用不能用于新运行；未知哈希、大小或绑定漂移继续拒绝。
- 新 session 的 `context.filter_profile` 冻结 Header 滤镜字段、类别、明确谱线和适用设备映射来源。
  它可以支持通道类型，不能证明线性状态或光谱通道角色；旧 session 不补写此字段。
- `init` 自动完整验证冻结手册 Bundle 和 command policy，将组件 fingerprint、policy capture 与
  `reports/manual-evidence/bundle-verification.json` 绑定进 session；该文件只证明 Bundle 闭包，不算某条
  命令的 manual lookup evidence。
- 每个交给 `run` 或 `--validate-only` 的 Agent SSF 必须有同 stem provenance sidecar；它绑定脚本、唯一
  canonical primary body、required supporting references、policy、实际 manual command/read 证据和参数理由。缺失、哈希漂移、路径逃逸、
  symlink 或协议不匹配都在执行前失败关闭。
- 每个 run receipt 绑定协议、脚本、SSF provenance、父源、工具、日志及其诊断、实际网络模式、预期产物和输出验证。
- Stage 7 参数求解可创建 `reports/<run-id>/request.json`、`stretch-parameters.json` 及 `parameter-trials/` 下的诊断 SSF/日志；临时 FITS 不进入谱系。生成的 SSF 绑定参数报告，receipt 追加 `adaptive_parameters`，执行与回放验证绑定和最终科学约束，不自动接受候选。
- 没有 `execution_policy` 或采用 0.2.0 执行策略的早期 v1 session 保持可读：已有 receipt 可 replay/finalize，且历史 receipt 可缺少
  diagnostics；此类 session 不得创建新的静态验证或实际 run，继续处理必须重新 `init`。

## 恢复与继续处理

[status](cli-contract.md#status)只读验证已记录的 receipt/review/文件绑定，报告最后已接受父源、阻塞、剩余试跑额度和 continuation；
它不重放全部科学检查，不替代执行资格验证。缺少意图或 review 时保持未知，不猜测停止原因或提升候选。
已 finalize 的 session 返回已验证提交，不恢复科学工作。

review-check 验证成功后，按返回模板独立记录 stage-decision.v1；decision 描述意图，不授予执行权限，
不把 session 改造成工作流状态机。记录的 run_receipt、review、supersedes 及各自哈希必须匹配；修订使用新路径，
不补填或覆盖旧决定。完整字段与输入输出见 [CLI status](cli-contract.md#status)。

从最近已接受的科学父源创建新编号脚本；不得复用失败 run、隔离材料或未提交残留。
ROI 建议只保留已审查同谱系的坐标，不复用旧统计；父源变化后重新测量，裁剪或重定向后重新核对区域。
前缀复用须满足完整来源、ROI、运行时及请求前缀匹配，返回图仍为诊断，不成为科学父源。
已接受当前策略的 input.inspect 可由 [confirm-input](cli-contract.md#confirm-input)补齐未知声明；
既有确认和同图恢复证据按原冻结绑定核验，不改初始字段或继承旧处理参数。

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

New run receipts record an explicit primary scientific output, propagated image domain, replayable transfer chain and branch/review bindings. Only primary scientific outputs and retained full-stars baselines can serve as scientific parents. This metadata is additive to receipt v1; old records remain immutable. Hash drift still rejects reuse.

## Paired scientific delivery

For delivery=paired_fits_jpeg_v1 or paired_fits_jpeg_v2, inspect and bind the scientific FITS, its JPEG and the verified parent
preview; retain all five visual gates. The FITS is the final nonlinear float32 master, not the linear input.
Scientific checks bind finite pixels, geometry, float32 storage and the JPEG pair; FITS parents additionally
retain normalized pixel values, endpoints, range and existing WCS. Formal success requires all three files,
including outputs/final.fit, before commit/cleanup. Historical sessions retain their original two-JPEG finalization.

## Auxiliary reporting policy

For `execution_policy.reporting=isolated_metrics_v1`, a failure in
post-commit auxiliary metrics does not change the successful run receipt. Returned
`report_warnings` are invocation diagnostics, not receipt fields. A best-effort
`reports/<run-id>/metrics-error.json` binds the original receipt SHA and failure
time; it never rewrites the execution log or manifest.

Explicit recovery creates only `reports/<run-id>/metrics-recovery.json`. Its
`recovery_binding` records the original receipt SHA, input fingerprints, actual
generation time and a digest of the metric report content. This is supplemental
evidence, not an output retroactively attributed to the original execution. The
content digest detects corruption; it is not a signature or proof of correctness.
Existing recovery evidence is verified and reused, never replaced. Old sessions
retain their exact reference fingerprints and replay/finalize-only permissions;
no history, policy or receipt is migrated. Required protocol evidence remains
required regardless of auxiliary reporting state.

## 文档指导版本

init 冻结执行策略、command policy 和手册 Bundle 的真实证据；文档说明不改变这些绑定。
完整协议骨架与必要 supporting 正文按[协议映射](protocol-index.md)绑定唯一 primary、角色及完整文件 SHA-256；
语法存疑时使用[手册 command/read envelope](manual-query.md)记录真实查询，不把摘要、Bundle 验证或数值结果当作接受证明。

已提交 provenance、receipt、review、参数报告、确认与 finalization 保持原字节和原语义。
精确登记的修改前文档只用于已提交证据的验证/回放；新 SSF 仍须绑定当前文档。
历史完整策略、来源、实现、配置与文档指纹共同决定兼容资格，不能混搭不同版本的链、蒙版、助手或报告字段。
未知指纹、路径、大小、符号链接、读取漂移和部分删除继续拒绝；退役的 references/protocols 目录必须整体不存在。

仅文档精简不改变 session 原有执行资格；已退役执行策略的 session 仅允许原证据回放/finalize，
继续科学处理须从同一 master 新建当前策略 session；
不得通过改写 execution_policy、旧记录或实际停止理由获取权限。已登记的派生诊断仅按明确授权范围创建，
不得启动原生科学进程、增加试跑或回填历史审查。已有 confirmation.v1、恢复报告及同图证据按原权限验证，
不因旧策略可读就允许首次确认、新静态验证或新科学运行。

阶段、共同策略及各次引入时的完整语义保留在[历史说明](session-legacy.md)；该材料不是新执行授权。

## 阶段专属合同

当前字段、科学门和 review 块由对应正文维护，不在 session 合同重复展开。

| 领域 | 当前正文 |
|---|---|
| 输入、通道与滤镜来源 | [Stage 1](stages/stage-01-input-inspect.md)、上方[不变量](#不变量)；filter_profile 不证明线性、通道角色或响应来源 |
| 背景与独立保护区 | [Stage 3](stages/stage-03-background.md#background-subtract) |
| 解算、校色与 neutral | [Stage 4](stages/stage-04-astrometry-color.md#color-calibrate) |
| 线性降噪、DeepSNR、PSF/RL | [Stage 5](stages/stage-05-linear-restoration.md) |
| 分星、原生桥接与失败隔离 | [Stage 6](stages/stage-06-stars-separation.md#stars-separate) |
| 拉伸策略、ROI、参照、额度与保留档案 | [Stage 7](stages/stage-07-stretch.md)；仅对应分支读取 [HDR](stage7-hdr.md)、[HOO](stage7-hoo.md) |
| 局部增强、HDR 谱系与空间蒙版 | [Stage 8](stages/stage-08-starless-enhance.md) |
| 合星提案、星色、固定星表与闭合 | [Stage 9](stages/stage-09-stars-recompose.md) |
| tone、调色、非线性降噪 | [tone.adjust](shared-protocols/tone-adjust.md)、[color.finish](shared-protocols/color-finish.md)、[denoise-nonlinear](shared-protocols/denoise-nonlinear.md) |
| 正式交付 | [Stage 10](stages/stage-10-delivery-finishing.md)、上方[最终提交与恢复](#最终提交与恢复) |

## 旧标题入口

以下入口保留旧链接。当前处理按上表正文，只有版本解释或旧证据排查才读取历史说明。

<a id="agent-proposed-linear-denoise"></a>

[历史说明](session-legacy.md#agent-proposed-linear-denoise)。

<a id="assessed-stage-5-restoration"></a>

[历史说明](session-legacy.md#assessed-stage-5-restoration)。

<a id="batch-color-decision-binding"></a>

[历史说明](session-legacy.md#batch-color-decision-binding)。

<a id="benefit-first-guidance-v18"></a>

[历史说明](session-legacy.md#benefit-first-guidance-v18)。

<a id="chroma-denoise-v1"></a>

[历史说明](session-legacy.md#chroma-denoise-v1)。

<a id="deepsnr-first-stage-5"></a>

[历史说明](session-legacy.md#deepsnr-first-stage-5)。

<a id="explicit-filter-metadata-policy"></a>

[当前滤镜字段与输入来源合同](#阶段专属合同)。

<a id="explicit-stage-9-guidance"></a>

[历史说明](session-legacy.md#explicit-stage-9-guidance)。

<a id="fixed-recomposition-review-policy"></a>

[历史说明](session-legacy.md#fixed-recomposition-review-policy)。

<a id="hdr-lineage-and-stage-8-methods"></a>

[历史说明](session-legacy.md#hdr-lineage-and-stage-8-methods)。

<a id="immutable-preservation-rollout"></a>

[历史说明](session-legacy.md#immutable-preservation-rollout)。

<a id="local_contrasttexture_hf_review_v1"></a>

[历史说明](session-legacy.md#local_contrasttexture_hf_review_v1)。

<a id="masked-dual-stretch-policy"></a>

[历史说明](session-legacy.md#masked-dual-stretch-policy)。

<a id="multiscale-local-contrast-v21"></a>

[历史说明](session-legacy.md#multiscale-local-contrast-v21)。

<a id="native-neutral-scientific-policy"></a>

[历史说明](session-legacy.md#native-neutral-scientific-policy)。

<a id="native-stage-4-scientific-inputs"></a>

[历史说明](session-legacy.md#native-stage-4-scientific-inputs)。

<a id="neutral-held-out-benefit-and-paired-safety"></a>

[历史说明](session-legacy.md#neutral-held-out-benefit-and-paired-safety)。

<a id="profile-guided-stretch-v23-history"></a>

[历史说明](session-legacy.md#profile-guided-stretch-v23-history)。

<a id="protected-background-sampling"></a>

[历史说明](session-legacy.md#protected-background-sampling)。

<a id="protected-stage-8-finishing"></a>

[历史说明](session-legacy.md#protected-stage-8-finishing)。

<a id="recomposition-and-delivery-review-v7"></a>

[历史说明](session-legacy.md#recomposition-and-delivery-review-v7)。

<a id="remaining-goal-tone-compatibility"></a>

[历史说明](session-legacy.md#remaining-goal-tone-compatibility)。

<a id="roi-stretch-v22-history"></a>

[历史说明](session-legacy.md#roi-stretch-v22-history)。

<a id="scientific-stage-3-sources"></a>

[历史说明](session-legacy.md#scientific-stage-3-sources)。

<a id="stage-7-参照与审查"></a>

[历史说明](session-legacy.md#stage-7-参照与审查)。

<a id="stage-owned-protocol-guidance"></a>

[当前正文绑定与历史引用规则](#文档指导版本)。

<a id="starless-subject-scope-v24-history"></a>

[历史说明](session-legacy.md#starless-subject-scope-v24-history)。

<a id="xisf-input-metrics-v8"></a>

[历史说明](session-legacy.md#xisf-input-metrics-v8)。

<a id="主体增强收益优先"></a>

[历史说明](session-legacy.md#主体增强收益优先)。

<a id="合星显式提案与审查"></a>

[历史说明](session-legacy.md#合星显式提案与审查)。

<a id="无掩膜优先的颜色评估"></a>

[历史说明](session-legacy.md#无掩膜优先的颜色评估)。

<a id="星层诊断版本"></a>

[历史说明](session-legacy.md#星层诊断版本)。

<a id="背景策略版本"></a>

[历史说明](session-legacy.md#背景策略版本)。

<a id="鲜艳调色-v19"></a>

[历史说明](session-legacy.md#鲜艳调色-v19)。

## Independent stellar contour preservation

New sessions freeze `recomposition_contour=local_peak_mtf_v1`. Contribution and optional contour processing retain separate controls, source-bound auxiliary weights and same-contribution comparisons. The outgoing exact policy, documents, command bytes, helper and complete implementation are retained in `star-contour-history.json` for immutable replay/finalize only; old evidence is never migrated.

## Morphology multistage defaults

New sessions freeze `stretch_routing=morphology_multistage_v1` and `stretch_execution=stepwise_default_v2`. The complete outgoing policy, documents, profiles and implementation are frozen in `stretch-multistage-history.json`; historical single-step, comparison and prefix receipts replay/finalize without migration. Ordinary body candidates require two executed steps; HOO retains its dedicated batch flow and Stage 9 Fs remains independent.
