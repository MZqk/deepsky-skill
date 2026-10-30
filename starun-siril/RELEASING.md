# Releasing starun-siril

## Preservation release checks

Stage 7 adaptive budget: verify per-channel boundaries, cumulative prefixes, HOO/HDR, overlay binding and drift, conservative blackpoints, native MTF equivalence, the unchanged attempt ledger and immutable outgoing-policy replay. Run the complete suite, manual bundle and package checks; retain real-image acceptance evidence in a new session without altering the old session.

Run the preservation regression tests plus the complete test suite, manual bundle verification and package release check. Verify the M31/NGC6888 corpus SHA-256 values before creating comparisons in a new directory. Retain identical display conditions and native/independent arithmetic checks. Real blue reflection-cloud coverage remains an explicit acceptance gap unless a genuine suitable source is supplied; synthetic blue tests alone do not close it.


DeepSNR Stage 5 acceptance: run backend discovery/drift, v2 proposal/fallback, precision/WCS,
failed-output isolation and immutable predecessor replay tests, then the full suite and release checks.
Exercise native Siril → DeepSNR 1.3.2/model 2 → Siril on controlled mono/RGB FITS and XISF.
Keep ONNX/CoreML models external to the release; synthetic success does not establish real-master
weak-structure retention. Version remains 0.3.3; no publishing or dependency installation.

Dualband v20 adds the auto/off session setting, strict native HOO stretch and independent curve trials. Verify signed estimates, optional OIII denoise, original RGB stellar replay, matched local materials, source/ROI/runtime drift, v19 immutable history and real NGC 6888 acceptance in a separate preserved session. Run the full suite and release checks; no publishing or version bump.

Vivid color flow v19 adds the vivid default, masked GHS saturation curves and bounded native parameter trials. Verify style overrides, source/ROI/runtime/trial bindings, neutral and hue preservation, endpoint/mask/metadata checks, paired review and immutable v18 replay. Include the new helper in the release allowlist. No automatic visual acceptance or version bump.

Benefit-first flow v18 updates adoption guidance and the native StarNet range advisory. Verify Stage 4 evidence acquisition, solvable WCS prerequisites, nonidentity neutral trials and tiny-benefit review; Stage 5 accepts no-material-degradation candidates without claiming visible gains. Test above-one handoff byte identity and warning retention, failed/nonfinite/geometry/source gates, current source-lock hashes and v17 replay-only history. Official StarNet 2.6.2 --linear clips above-one samples with a warning rather than rejecting startup; original full-source/residual and actual subject/stellar review remain required. No dependency installation, new CLI/receipt fields, fabricated responses or automatic visual acceptance. Version remains 0.3.3; no release.

Prepared Stage 4 flow v17 changes Agent guidance and copyable examples only: verify route selection, post-astrometry source binding, reviewed neutral fit/validation and subject protection, fixed display parameters, one path/argument list and per-attempt evidence. Check ordinary and space-containing paths, all nine neutral outputs, unchanged route-specific SSF blocks, native-neutral/XISF/PCC guards, release checks and complete v16 replay/finalize history. Walk through M31, insufficient diffuse-field sky, astrometry-first photometry and missing response evidence. No M31 rerun, new generator, CLI, schema or executor gate; documentation does not prove sky validity or faint-signal preservation. Version remains 0.3.3; no release.

Assessed Stage 3 flow v16 changes Agent guidance only: verify mandatory control proposals and eligible candidate execution, sparse reliable sampling, degree 1–4/RBF selection, bounded same-parent revisions and documented fallback. Check complete v15 replay/finalize history and unchanged scientific operators, thresholds, CLI, schemas and canonical SSF blocks. Walk through large-galaxy, diffuse-emission, compact-target and flat-sky cases; documentation checks do not prove Agent compliance or real-image quality. Version remains 0.3.3; no release.

Serial Agent flow v15 retires the independent batch CLI and its private diagnostic packets. Verify serial multi-master guidance, removal from the release allowlist, unchanged scientific examples/native adapters, and complete v14 policy/reference history. Historical sessions remain replay/finalize-only. Run the full tests and new-session real-image acceptance before publishing. Version remains 0.3.3; this change publishes no release.


`starun-siril` 只从 `release-files.txt` 的固定核心白名单和唯一
`@component references/siril-manual/manifest.json` 组件闭包构建。不得直接打包整个 Skill 目录，也不得把
测试、缓存、session、维护构建器、源 archive 或 transaction journal 放入 ZIP。

## 本地候选验收

以下是维护者在 Skill 执行之外准备开发环境的步骤，不是任务运行时的安装流程。先按
[CLI 合同](references/cli-contract.md#解释器与入口) 验证并设置 `STARUN_PYTHON`，再从仓库根目录执行：

```bash
"$STARUN_PYTHON" -m venv starun-siril/.venv
STARUN_PYTHON="$PWD/starun-siril/.venv/bin/python"
"$STARUN_PYTHON" -m pip install -r starun-siril/requirements-dev.txt
"$STARUN_PYTHON" scripts/validate_repository.py --skill starun-siril
"$STARUN_PYTHON" -B -m pytest -p no:cacheprovider starun-siril/tests
"$STARUN_PYTHON" starun-siril/scripts/query_siril_manual.py --verify-bundle
"$STARUN_PYTHON" starun-siril/scripts/package_release.py --check
RELEASE_DIR="$("$STARUN_PYTHON" -c 'import tempfile; from pathlib import Path; print(Path(tempfile.mkdtemp(prefix="starun-siril-release-")).resolve())')"
"$STARUN_PYTHON" starun-siril/scripts/package_release.py \
  --output "$RELEASE_DIR/starun-siril-0.3.3.zip"
```

停止条件：任一测试或验证失败、白名单漂移、组件闭包/哈希/许可证漂移、ZIP 不可复现、便携解包后的
`probe/init/run --validate-only` 失败，或真实 Siril 前向验收失败。不要用单元测试替代真实 FITS/XISF
重开与最终 JPEG 视觉检查。

Stage 4 guidance v10 accepts native XISF Image 0 for astrometry/PCC/SPCC and writes FITS outputs.
Run `tests/test_stage4_xisf.py` for metadata, source identity, WCS, history and real Siril load/save
coverage. Float bounds must be 0:1; non-unit bounds fail before execution. Gaia and photometry
request/result tests use deterministic substitutes; live service/star-field acceptance remains separate.
V9 sessions keep immutable replay/finalize and confirmed same-master recovery; new processing requires
a new session from the original master. Do not rewrite existing sessions or deliveries during upgrade.

## 引用语义回归

修改阶段导引、协议骨架或数值门限后，从仓库根目录运行：

```bash
"$STARUN_PYTHON" -B -m pytest -q -p no:cacheprovider \
  starun-siril/tests/test_knowledge_contract.py \
  starun-siril/tests/test_native_processing.py \
  starun-siril/tests/test_tone_adjust.py \
  -k "knowledge_contract or recompos or shadow_threshold or real_siril_mtf_asinh_ghs_closure"
```

自动检查阶段表的成员、顺序和导引链接，以及协议索引的完整且唯一覆盖，检查导引的发布覆盖。可执行示例只维护在
原子协议中，阶段导引通过链接引用，不另写 SSF/shell 骨架。反卷积骨架选项与冻结手册核对，并检查
PSF 生成/使用关系；合星直接把文档 SSF 交给运行时校验。数值用例读取原子协议阈值表，生成阈值内外的
受控 FITS，核对实际接受/拒绝行为；既有固定边界与非法算法回归同时保留，防止文档和代码一起意外漂移。

这些检查不理解任意自然语言。人工复核仍须确认：

- Stage 1 的线性状态来自可靠来源证据，预览不改写状态，也不成为科学父源。
- Stage 5 两项都适用时先降噪；RL 参数语义以冻结手册为准，较低 alpha 对应更强正则，不把算法收益写成保证。
- 背景、裁切与局部对比只使用原子协议支持的操作；新增指标或阈值须说明计算来源、单位、适用域和验证证据。
- Stage 9 不推荐 Screen、差值星层再传递、截断负残差或失败后自动交付无星图；数值闭合不等于没有目标泄漏。
- review 五项门记录判断，测量报告提供证据；全图截断率不能替代局部坑洞、振铃、星色及真实结构检查。

此命令用于聚焦回归，不替代上方完整发布验收；真实 Siril 用例被 skip 时须明确记录未完成该项验证。

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

## Background v2 acceptance

Check held-out luminance/chromatic benefit, warning review, no-benefit skip isolation, paired new-shadow crossings, and v1 immutable replay/finalize. Run Stage 1–3 in a separate C50 session with native first/second-order candidates and identical explicit MTF previews; report actual regional and visual quality, not just successful gates. Include the existing read-only background module in the release whitelist; exclude sessions and diagnostic test data.

Background v3 release checks include sparse-star recovery, source-only ROI proposals, native identity precision, and immutable v1/v2 replay. Runtime files remain in release-files.txt; generated sample/session evidence is never packaged.


PSF v2 release checks cover axial wraparound, swapped axes, circular/symmetric kernels, direction cancellation,
native-grid boundaries and regional matched diagnostics, report drift and I/O failure, and immutable compact/inline
v1 replay/finalize. Real Siril tests separately verify native model capture and kernels/RL driven by controlled axial
parameters. Report tests describe captured samples only; they do not calibrate spatial applicability thresholds or
validate a real master or independent held-out stars. No dependency, CLI or public protocol is added in 0.3.3.


Paired delivery release checks cover float32 FITS/JPEG source identity, WCS and range preservation,
required review materials, all three formal copies, interrupted-copy retry, default pruning and immutable
historical two-JPEG replay/finalize. Verify real Siril mono/RGB FITS and supported TIFF/XISF native conversion;
report skipped native coverage explicitly. No Python scientific pixel writer or new runtime dependency is added.

## Stage evidence limits v4

Verify sourced standard RGB PCC, explicit user display fallback and their immutable receipt bindings; policy_default cannot select display PCC. Serial multi-master handling must preserve pending materials and actual-review requirements for each session, reject changed parents and avoid automatic acceptance or downstream cleanup. Check outgoing v3 and earlier immutable replay/finalize, and an unpacked new v4 static session. PSF sample counts and restoration defaults remain engineering guidance, not physical confidence claims.


## Fixed Stage 9 review and PCC guidance

Run fixed-material, missing/report-recovery and historical-review tests in
`tests/test_recompose_review_v7.py`, plus confirm-input, HDR, serial guidance, CLI/finalize, knowledge and
release regressions. Warning and clean runs must derive the same material roles; HDR may add paths.
Capture exact outgoing knowledge fingerprints before edits. V10 policy history, v9/v10 confirmation
and old recovery-report verification must remain intact. Do not rewrite task 1-5 evidence/deliveries.
PCC remains a nonfatal fit warning requiring actual review and disclosure; guide tests must preserve
its linear/WCS/passband/clipping constraints and avoid an unsupported M31 causal conclusion.


## DWARF explicit filter mapping

Verify the identical stdlib metadata modules in both skills, strict model aliases, selected-filter
priority, missing/conflicting devices and wide/unknown lens exclusions. Test synthetic FITS/XISF
init geometry and frozen profiles, unchanged unknown linear state/channel_map, and PCC/SPCC evidence
gates. Retain exact outgoing fixed-review policy/reference history and v9/v10/fixed-review
confirmation, same-master recovery, reports, review/replay/finalize verification. Do not rewrite
task 4 files or sessions. Synthetic tests do not validate every DWARF firmware/header variant or
actual hardware filter selection; missing recorded selection must remain unknown.


## Protected manual background sampling

Run protected-background, background v3/v4, native Siril Order 1/2, CLI/session, confirm-input,
metric recovery, historical replay/finalize, serial guidance, knowledge and release checks. Cover full sample
footprints across core/faint/stars boundaries, overlapping sky controls, sparse valid manual layouts,
corner-only coverage failure, field-filling emission and FITS/XISF top-origin coordinates. Confirm
current sampling-policy bindings cannot be removed or altered and historical absence retains the
old behavior. Capture outgoing policy/reference fingerprints before edits. Do not rerun actual
NGC 6888 images or modify existing sessions/deliveries; synthetic tests do not prove real clean sky.


Native neutral freezes neutral=scientific_native_flow_v2 without changing contract, guidance or
other policy labels. Include scientific ROI, native fmul 1/PixelMath/rgbcomp, paired preview,
accepted-parent continuation and exact legacy command-policy/confirmation/replay tests. Real Siril
synthetic FITS/XISF verification checks units, orientation, formula, background-subtracted signals
and WCS; it does not prove pure sky or physical line ratios. Public ROI remains FITS-only.
After Stage 3, assess dualband native neutral and either adopt an accepted same-parent
calibration or record the actual reason for retaining that parent. Do not rerun existing tasks or rewrite their evidence.


Stage 5 adds restoration=assessed_scientific_flow_v1 while preserving contract,
guidance and PSF algorithms. Validate constrained float32 promotion, FITS/XISF Image
0 units/orientation, finite geometry/WCS, native PSF tables and matched displays.
Single-task tests must cover exact accepted-source chaining, drift,
review bindings and immutable finalized replay.
Run native synthetic denoise/PSF/RL and historical confirmation/report/replay/finalize
regressions plus knowledge/release checks. Do not rerun or rewrite existing tasks.
Synthetic technical verification does not establish true recovery benefit or PSF suitability.


Stage 9 guidance v11 requires explicit generator --source and accepted same-stretch starless
lineage. Check generation refusal without writes for wrong domain/branch/review, stale receipts,
unsafe paths, drift and existing assets. Validate generated single-chain/HDR SSFs and native Siril
MTF/Asinh/GHS/PixelMath closure with independent NumPy checks. Regress single-task replay/finalize,
fixed review materials, historical Stage 5/confirmation/recovery and complete policy fingerprints.
Do not rerun or rewrite existing tasks; mathematical closure cannot replace actual pixel review.


## 共用协议与中文正文发布检查

v12 冻结 guidance=stage_shared_protocol_docs_v12；完整 v11 策略、修改前知识指纹、锚点及示例清单已留存核对。发布显式包含 shared-protocols/tone-adjust.md 与 color-finish.md，不重建退役 protocols 目录。核对唯一正文/primary、Stage 8/10 旧锚点和相对导航，以及错误路径、角色、哈希拒绝；对照科学命令块确认无参数、公式、单位、方向或门限变动，command policy 字节保持不变。

回归共用协议、背景/neutral/HDR/交付、v11 历史回放/finalize、确认与同图恢复、知识绑定及发布清单；新 guidance 仍须覆盖 XISF/PCC 校验。真实 Siril 合成验证只证明执行与数学绑定，不证明真实图像处理收益，不重跑或改写任务 1–5。


## AI 形态观察与科学选参衔接

v13 冻结 guidance=stage_ai_morphology_measurement_v13；修改前完整 v12 策略、改动知识文档精确指纹和命令块清单已登记。首版复用 review.notes、SSF/provenance、现有拉伸 request 与显式 strength，不新增 CLI/schema、天体库、模型或外部识别 API，Python 不解析观察文本自动选参。

核对 Stage 1 观察模板、模式边界与 Stage 6/7/9 衔接；科学命令块、公式、默认参数、门限、协议归属及 command policy 字节不变。回归 CLI/session、确认/同图恢复、v12 历史回放/finalize、unknown 门、ROI 漂移、分星/拉伸/回混、XISF/PCC、多文件串行规则和发布检查；运行相关现有真实 Siril 合成用例。自动化只验证绑定与技术约束，不证明 AI 识别正确或实际处理收益；不重跑或改写任务 1–5。

## Stage 8 纹理策略兼容

新 session 冻结 local_contrast=texture_hf_review_v1，guidance 仍为 stage_ai_morphology_measurement_v13。完整旧 v13 策略、command policy 内容及改动前知识 SHA-256/大小登记为只读兼容记录。检查 HDR 谱系互斥、执行前 1.7 门、原生候选多尺度报告、告警实际审查、历史确认/回放/finalize、知识绑定及发布白名单。contract、CLI、review/provenance schema、CLAHE 科学参数与算术不变；不新增依赖，不发布或改写既有任务。


## Multiscale v21 acceptance

Validate the current guidance and local contrast strategy, six-plane native reconstruction and error-log handling, same-parent wavelet/CLAHE competition, positive RGB gain, source/ROI/runtime/report drift, fixed review materials, no repeated enhancement, HDR exclusion and exact v20 replay/finalize. Run the complete skill suite, repository/manual/package validation and independent M33/NGC 6888 sessions with all intermediate evidence retained. The existing runtime module remains in the release whitelist; no new orchestrator, dependency, version bump or release.

## ROI stretch v22 candidate verification

Verify v2 requests/reports, all-role protection, sky MAD/hf_sigma2 budgets, bright/dark weakest-target selection, near-best texture ordering, full-source stellar replay, HOO independent budgets, final HDR protection and immutable v21 history. Run native synthetic and complete contract tests plus repository/manual/package checks. Include deep_sky_siril_stretch_v2.py and stretch-profiles.json in the allowlist. No real master rerun, release or version bump in this change.

## Morphology chain v23 candidate verification

V25 verifies explicit one-proposal factors, exact current-domain anchors, bounded native call counts, retained failure evidence without retries, mode/factor/source/ROI/runtime drift rejection, morphology comparison, HOO v3 children and Stage 9 closure. Preserve exact v24 policy/helper/document bytes and readonly history. No new runtime file or native command is added; package inventory is unchanged and publication remains disabled.

V24 additionally separates trusted starless subject eligibility from full-replay stellar diagnostics. Verify strong GHS candidates, retained full evidence, ordinary full-image rejection, HDR subject/full scope, frozen RGB residual closure, report-role drift and exact v23 read-only replay. Preserve the pre-edit v23 policy/command bytes/helper and document fingerprints. No native command or package inventory is added; the release remains unpublished.

Verify five profile cascades and same-parent controls, explicit phase goals and shared final protection, current-domain GHS landmarks, frozen strategy/goals/ROI/runtime/code bindings, native two-attempt comparison, failure diagnostics, comparison-bound formal SSFs and mandatory review materials. Preserve v22 immutable replay/finalize, existing HOO/HDR/style behavior and original RGB stellar replay. Run related native synthetic tests, the complete skill suite, repository/manual and candidate-package checks. Include deep_sky_siril_stretch_profiles.py in the allowlist. No dependency, command authorization, real-master rerun, version bump or publication.


Chroma denoise v1: verify native RGB bilateral range units, luminance-null projection, shared endpoint weights, equal-brightness color boundaries, weak red/blue structures, paired protected color probes, formal/trial equality, evidence drift and pre-chroma replay/finalize. Run related regressions, the full suite and repository/manual/package checks. No real-master rerun, version bump or publication. Synthetic evidence does not establish true faint-color retention on all targets.
Stellar stretch guidance v28 adds Modified Asinh only to independent Stage 9 Fs. Verify strict options and protocol isolation, native RGB/mono/HDR/HOO paired-source replay, fixed catalog/float32 geometry, baseline/final closure and final-addition endpoints. Capture the outgoing policy, command bytes, documents/helpers and implementation before edits; test immutable pre-v28 replay/finalize and reject mixed bindings. Run stellar, knowledge, complete regression and package checks. Keep version 0.3.3; no publication, dependency installation or real-master rerun. Synthetic success does not establish equal-brightness visual superiority.


Stage 7 v29 freezes guidance=evidence_bound_stretch_flow_v29 and stretch_routing=profile_bound_v1. Package references/stretch-routing-history.json with the exact pre-edit v28 policy/documents/helpers/implementation; do not regenerate that snapshot from current files. Verify five initial chains, early rejection of unbound/custom/tampered routes, native exception evidence and target diffs, both comparison types and the three-attempt ledger, HOO/HDR isolation, ROI assessment v2 and immutable historical replay/finalize. Use explicit factors on small synthetic native inputs; do not rerun search grids or alter C50/NGC6888/M42 sessions. Actual morphology and visual conclusions still require image inspection. Keep version 0.3.3; no publication is authorized.

Stage 7 trial-archive acceptance: verify successful and failed prefixes, full-stars/HOO/HDR copies, byte/hash identity, no additional native trials, collision/symlink/drift rejection, retention through prune and idempotent finalize, and unchanged scientific-parent eligibility. Include the new archive helper and outgoing v29 history in the release allowlist. Replay/finalize historical evidence without copying into existing sessions. Version remains 0.3.3; no publication.

Whole-chain Stage 7 preflight: verify complete intermediate-bound basis coverage, aggregated conflicts, read-only CLI/exit status, truthful unknown input domains, native-prefix controls, HOO/tail sharing and prelaunch tamper rejection without attempt consumption. Test explicit native SP/HP semantics on small synthetic data. Freeze outgoing policy/documents/helpers/implementation before edits; migrate shared test factories with real control bindings, preserve historical replay/finalize and all existing sessions. No endpoint, budget, recipe or solver change, version bump or publication.

Stage 7 declared-morphology acceptance: verify independent native-parent context preparation, exact preview/ROI geometry and ICC retention, all five declared processing goals, complete ROI coverage, primary versus companion evidence, unresolved structures, tamper/collision/symlink rejection, aggregated preflight and native-claim gates, morphology revision diffs, HOO/HDR/tail reuse, prune retention and immutable historical replay/finalize. Freeze outgoing bindings before edits and migrate shared test factories with actual context evidence. No real-master rerun, scientific algorithm/endpoint/budget/recipe change, Stage 1 contract change, version bump or publication.

Tail comparison v2 acceptance: verify two-solved and safe negative controls on either side, exact shared prefix/source/ROI/runtime bindings, shared protection and native evidence remeasurement, failed formal selection rejection, labeled visibility/review materials, v1 replay and outgoing policy replay/finalize. Recover C50 only by adding a derived comparison from existing trials; preserve every original file and three-attempt ledger, with no Siril launch, installation, publication or version bump.

Recovery-summary acceptance: verify read-only status on pending, accepted, rejected, stopped, unknown-context, historical and finalized sessions; receipt/review/decision drift, exact trial counts, coordinate-preserving same-lineage ROI proposals and fresh measurements, immutable prefix reuse, outgoing snapshot replay/finalize and release inventory. No background orchestration, automatic scientific acceptance, real-master rerun, installed-skill update, publication or version bump.

Curve-effect acceptance: verify mono/nonuniform RGB MTF mapping, safe blackpoint/endpoints/roundoff and pre-freeze rejection without native execution; unknown and exact verified prefix handling; actual GHS input/output changes without new gates; initial one/two-step and measured shortening evidence, immutable plans, protected ROI and RGB tail requirements, shared attempts, v1 replay/finalize and exact release inventory. Use only synthetic native fixtures and read-only C50 retained evidence. Do not install, publish, migrate historical science or rerun C50/M31.

For source-root changes, verify historical DeepSNR replay against the original unchanged frozen adapter path, and reject the same path for new native execution or after adapter byte drift. This compatibility path must not launch DeepSNR or modify historical contracts.

Curve-amplification acceptance: verify the analytic MTF derivative against a float64 reference at interior points and its zero-slope/clamped behaviour, mono and non-uniform RGB luminance aggregation, predicted span gains against mapped statistics, null ratios for zero span baselines, measured GHS reuse without new native measurement, and identical status/issues/warnings/attempt counts with and without the block. Freeze the outgoing `curve_effect_review_v2` policy, documents, helpers and implementation in `references/stretch-curve-amplification-history.json` before edits; keep the report schema at `stretch-constraint-preflight.v2`; preserve v1/v2 preflight replay, `verify()` byte equality and every existing session. Add the new history file to both release allowlists in the same position. No scientific transform, endpoint, budget or recipe change, version bump or publication.

Display-scale acceptance: verify fit() downscales without upscaling, rejects scientific suffixes and renamed non-JPEG/PNG payloads, enforces identical dimensions and session-relative paths, and produces byte-identical PNGs on repeated runs; verify `unchanged` on an identical re-run, refusal to overwrite different bytes, and retention of the panel through finalize pruning. Confirm Stage 7/8 review requires the display-scale observation without adding thresholds or ROI coordinates, that the 32-material CLAHE comparison flow carries the display row inside the existing panel, and that the outgoing stage-07/stage-08 hashes are registered for historical replay. Add the new script to both release allowlists in the same position. No policy label, implementation fingerprint, scientific transform, version bump or publication.


## Independent stellar contour

Verify continuous zero-identity weights, signed-pixel preservation, common RGB gains and same-contribution geometry; exercise native mono/RGB/HDR/HOO and independent Fs. Check auxiliary tampering, source/ROI drift, exact recipe authorization, explicit output declarations, review materials and measurable adoption evidence. Preserve the exact outgoing policy/document/helper/implementation snapshot and immutable replay/finalize. Run related and complete skill regression plus repository/manual/package validation. Keep version 0.3.3; no new dependency, publication or real-master quality claim.

## Morphology multistage body routes

Verify all five profiles, default prefix feedback and explicit batch; reject standalone body transforms at request, finish, generation, execution and adoption boundaries without consuming a rejected finish successor. Exercise native RGB/mono/HDR/HOO, single-step internal branches, independent Stage 9 Fs and contour recomposition. Verify unchanged historical single-step/comparison/prefix replay and finalization, then run full skill/repository/manual/package validation. Include stretch-multistage-history.json in both allowlists; keep version 0.3.3. Synthetic success does not establish real-subject visibility.
