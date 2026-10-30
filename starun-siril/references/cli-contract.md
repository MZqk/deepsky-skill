# Standalone Contract v1 CLI

薄 CLI 只执行确定性基础设施，不选择处理流程。公开命令固定为 `probe`、`init`、`run`、`review-check`、`confirm-input`、`finalize` 和只读 `status`。
它不下载工具，也不依赖 Starun 仓库或宿主结果格式。多文件按[入口串行规则](../SKILL.md#多文件串行处理)逐份建立 session。

本文件维护参数、输入输出、错误和执行边界。科学算法、选参、专属审查及历史语义按下方链接读取阶段或共用正文；
[协议映射](protocol-index.md)决定唯一 primary，手册原文查询及证据格式见[查询协议](manual-query.md)。

## 解释器与入口

辅助工具（CLI、手册查询、指标测量、自动样点、Header 同步和发布维护）使用已安装的普通 Python。
Agent 在启动任何辅助工具前，先只读解析候选可执行文件及符号链接；使用 venv 时还检查其
`pyvenv.cfg` 的底层解释器。不得选择 Siril app 内 Python，或底层解释器指向该 app 的 venv。
缺少可用普通解释器时报告缺失，不自动安装或切换环境。

把确认后的绝对路径设置为 `STARUN_PYTHON`。本机示例使用已验证的 Homebrew Python，不固定小版本；
其他主机替换为其已验证的普通解释器，不盲目照抄该路径：

```bash
STARUN_PYTHON=/opt/homebrew/bin/python3
"$STARUN_PYTHON" -B -c 'import sys; from pathlib import Path; print(Path(sys.executable).resolve()); print(sys.base_prefix)'
```

`STARUN_PYTHON` 只是示例中的 shell 变量，CLI 不读取它，也不据此选择 Siril 的 venv。
工具每次调用创建新 shell 时，重复绑定同一已验证路径，再执行对应命令；不依赖变量跨调用保留。

需要 Siril 连接上下文的 `siril_starnet.py`、`siril_starnet_linear.py`、`siril_deepsnr.py`、`siril_background_samples.py`、`siril_background_samples_scientific.py` 与 `siril_psf_samples.py` 仍由现有
`siril-cli → .ssf → pyscript` 调用，不能用 `"$STARUN_PYTHON" adapter.py` 直接启动。
Siril 自行选择其 Python 环境；probe 中的解释器路径、哈希和依赖记录只用于能力验证和运行时绑定，
不是外部执行或安装入口。Siril Python 缺依赖、连接失败或启动被拒绝时，保留日志并报告对应能力缺失，
不自动更换解释器、安装依赖或回退原生命令。macOS 启动约束排查见 [安全协议](siril-safety.md#macos-启动约束)。

## probe

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril.py probe [--offline] [--output /abs/probe.json]
```

输出 Siril、StarNet2、StarNet 模型、DeepSNR 及其 v2 模型与能力指纹、可选本地 Gaia、原生 SPCC 响应名单、JPEG 解码能力和协议级 SirilPy 状态。可选能力缺失只在
需要它的协议上阻塞。探测不会安装、下载或修改运行时。

## init

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril.py init INPUT --session SESSION \
  [context options] [--container-validation siril|strict]
```

`SESSION` 必须不存在或为空。命令创建 v1 `session.json`、`manifest.json`、初始 tool probe 和固定目录；
自动验证离线手册 Bundle 和 command policy，冻结 evidence、输入、用户约束、保留策略、容器验证模式和 `execution_policy`，不生成处理计划。
执行任务无需在 `init` 前另跑一次 `--verify-bundle`。冻结字段与历史执行权限见 [session 合同](session-contract.md#不变量)，
审查要求见 [quality](quality.md#review-receipt)。

### 初始化前的输入证据

先按 [输入前置边界](../SKILL.md) 确认当前输入已经完成暗场/平场校准与对齐叠加。复用当前用户确认、同图可信采集/处理记录或明确的 Header 证据；Master FITS、XISF/TIFF 扩展名及文件名本身不能证明这些操作已完成。前置处理未完成或无法确认时，停止依赖它的脚本生成和图像处理，说明缺项并要求先由设备机内算法或 Siril 预处理脚本完成。

校准、叠加完成情况与下方 `input_state/channel_mode` 证据分别核对；确认前置处理已完成不自动确定 `linear/nonlinear`，状态未知时仍遵循既有 input.inspect 边界。

先使用当前对话中已确认的用户声明，不因新任务没有重复声明就把同一 master 重置为 unknown。缺少会改变处理路径的状态或通道来源时，在初始化前提出一个简短问题，并继续只读检查；缺哪个字段只保留该字段为 unknown，不连带清空其他已确认字段。

下方 auto 路由不能识别的旧自然语言证据由 Agent 只读核对：检查输入目录下一层的既有 `*/session.json`，核对当前输入的完整 SHA-256 与大小，再用 `deep_sky_siril_session.load_session` 验证冻结绑定。仅恢复有具体用户或采集来源的 `context.state_evidence` 及它明确支持的 `input_state/channel_mode/channel_map`；若存在 manifest 绑定的确认记录，使用 `deep_sky_siril_session.effective_session_payload` 取得有效上下文。新 init 的 `--state-evidence` 保留原声明、来源 session 绝对路径与 `session_sha256`，以及适用的确认记录路径和 SHA-256。同名文件、相同设备或预览外观不是依据。

旧记录的 unknown 只表示缺少证据，不推翻已验证的明确声明；明确声明相互冲突或与当前用户信息冲突时先澄清。不继承旧执行策略、校色参数或处理产物，不改写旧 session；新 session 重新冻结本次策略与能力。

已知 `--input-state linear|nonlinear` 至少需要一个以 `user confirmed `、`acquisition record: ` 或 `FITS header: ` 开头且明确声明对应 `input_state=` 的 `--state-evidence`，与 confirm-input 使用相同来源规则。`auto|unknown` 不得附状态证据。
`--channel-map` 只声明单个三通道 master 的来源角色。通道类型或角色没有可靠用户、采集或文件内证据时
使用 `--channel-mode unknown`，不得从文件名、色调或设备型号推断。

`--input-state auto` 会检查输入目录下一层的 `*/session.json`，仅恢复完整 SHA-256、大小及
`load_session` 冻结绑定验证通过的同图声明。可自动恢复的证据以 `user confirmed `、
`acquisition record: ` 或 `FITS header: ` 开头，并明确包含 `input_state=linear|nonlinear`；
通道类型必须另外声明 `channel_mode=dualband-osc` 等实际枚举值，例如：
`user confirmed input_state=linear; channel_mode=dualband-osc; stacked, unstretched master`。
恢复成功后把原声明、来源 session 绝对路径和 `session_sha256` 写入新 session 的证据；已确认的 session 同时保留确认记录路径和 SHA-256；未指定的
通道类型随已确认状态恢复，显式通道选项保留。没有可靠状态声明时保持 unknown；冲突返回
`input_evidence_conflict`，旧策略、处理产物和通道角色不自动继承。Master Light、设备、文件名、直方图或旧版自动生成的 `verified unstretched linear master` 不证明线性，也不从旧 session 恢复该推断。XISF Image 0 的明确拉伸历史可确认为 `nonlinear`；与线性声明冲突时停止。显式 `--input-state unknown`
禁用自动恢复与推断。

FITS/XISF 初始化同时读取 `FILTER / FILTERID / FILTNAME`，将原字段、滤镜类别、明确谱线及设备映射的
厂商链接冻结到 `context.filter_profile`。支持识别 Seestar 型号（包括 S30 Pro、S30P、S30、S50 及其在 PixInsight 导出格式中的设备标识）与 DWARF 型号（DWARF 3、DWARF mini、DWARF II）；在 Header 包含受支持的 Seestar 型号来源和 `LP`
且没有 wide-camera 冲突时，按已记录的厂商定义识别 Hα/OIII；其他设备的 `LP` 保持 unknown。
泛称 `dual/duo` 单独不补猜谱线。未指定的 `channel_mode` 可依据滤镜证据和实际通道数填写；三通道
双窄带为 `dualband-osc`，通道角色 `channel_map` 不自动填写。明确声明与 Header 已识别类别冲突时
返回 `filter_evidence_conflict`。显式声明不可与 Header 滤镜证据冲突。

`--container-validation siril` 为默认值。对 FITS/XISF 科学产物，它启动新的 Siril 进程执行
`load → stat → close`，并从日志取得格式、宽、高、通道和有限统计。`strict` 先用标准库遍历 FITS 或
检查 monolithic XISF，再执行相同的 Siril 重开，并要求两种观察的格式与几何一致。该模式写入
`session.context.container_validation`；`run` 没有覆盖参数。

`init --style vivid|natural|balanced|artistic` 默认 vivid，仅改变新会话的显示风格偏好。已有 session 的 context.style 不改写。鲜艳选参辅助工具及其参数报告合同见 [共用调色协议](shared-protocols/color-finish.md#vivid-color-curve)；顶层 CLI 和正式协议名不增加。

`init --dualband auto|off` 默认 off，冻结 context.dualband；Python `init_session()` 同样默认 off。
用户显式选择 OSC HOO 后才传 auto；新建同源会话不继承开启状态，已冻结为 off 后改选须创建新会话。
完整来源与输出合同见 [HOO](stage7-hoo.md#dualband-hoo-stretch)。

## confirm-input

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril.py confirm-input \
  --session SESSION --run-id 010-inspect \
  --input-state linear --channel-mode broadband \
  --state-evidence 'user confirmed input_state=linear; channel_mode=broadband; stacked, unstretched master'
```

当前执行策略下，成功读取 immutable `@input` 的 `input.inspect` 经真实像素审查且 verdict=accept 后，
可提交可靠来源声明，在原 session 继续。必须指定 `--session`、`--run-id` 和至少一个
`--state-evidence`；`--input-state linear|nonlinear` 与 `--channel-mode mono|broadband|narrowband|dualband-osc`
只补未知字段，已知字段可省略或重复相同值，冲突拒绝。提交后状态与通道必须全部已知；不能只提交一半。

每个待确认字段必须由以 `user confirmed `、`acquisition record: ` 或 `FITS header: ` 开头的声明
明确支持，例如 `input_state=linear` 和 `channel_mode=broadband`。Agent 必须记录真实用户或文件来源，
不能把像素外观、中位数、设备型号、文件名或单独的堆栈 HISTORY 改写成可靠声明。
现有科学像素指标只作诊断，不生成 `linear_probable`，不自动解锁处理。`channel_map` 不自动补齐。

CLI 写入独占的 `reports/input-confirmation.json`（`starun-siril.input-confirmation.v1`），绑定
原 session 哈希、输入 SHA-256/大小、Stage 1 receipt/review 哈希、补齐字段与来源声明，再原子提交
`manifest.input_confirmation` fingerprint。不改写 session、历史 receipt 或 review。确认后的静态验证、
run receipt 与最终审计绑定确认 fingerprint；输入、确认文件或审查证据缺失/漂移/symlink 均阻断。

返回 `status`、有效 `context`、`confirmation` fingerprint 和 `reused`。相同请求幂等；不同请求
不得覆盖。确认文件已持久化而 manifest 未提交时，它不生效；相同请求重试可完成提交。
仅执行过 `input.inspect` 的未结束 session 可首次确认；确认后的重复请求可以在后续 run 后验证复用。
已 finalize 或更旧的只读执行策略拒绝确认。支持当前策略下升级前的检查记录：仅检查回放可使用
登记的修改前文档精确指纹，新 SSF 仍须绑定当前文档。历史 v9 确认记录保持可验证，并可支持同图证据恢复；旧策略首次确认或新增执行仍被拒绝。每个 session 串行调用 CLI。

## run

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril.py run \
  --session SESSION --protocol PROTOCOL_ID --script SESSION/scripts/NNN-name.ssf \
  --source /abs/current-parent.fit --expect SESSION/artifacts/result.fit \
  --expect SESSION/previews/result.jpg [--timeout 1800] [--validate-only]
```

规则：

- 一次只执行一份脚本和一个协议。
- 脚本必须位于 session 的 `scripts/`，名称符合 `NNN-name.ssf`。
- 脚本必须有自动按同 stem 发现的 `scripts/NNN-name.provenance.json`；CLI 不提供绕过或另传 sidecar 的
  参数。sidecar 必须遵循 `ssf-provenance.schema.json`，脚本、canonical primary body、required supporting references、policy 与实际
  manual command/read evidence 的路径和 SHA-256 均须匹配。
- `--source` 传当前科学父源的真实文件路径，默认使用绝对路径，并与 SSF 的父源 `load` 一致；父源必须是原始输入或成功 run receipt 中已验证的产物。
  相对路径按调用进程的工作目录解析，不按 session 目录解析；`@input` 是回执中的内部标记，CLI 不解析该别名。
- `input.inspect` 从已验证 session 的 `input.path` 取得原始 master 的绝对路径，同时用于 `--source` 和 SSF `load`；回执将该来源记录为 `@input`。
  当 session 的有效 `input_state=unknown` 或 `channel_mode=unknown` 时，执行期机器门只允许
  `input.inspect`；审查后停止，不得把 direct/autostretch 诊断预览作为 `delivery.render` 或科学处理父源。
  获得可靠来源证据后使用 `confirm-input`；初始 session 上下文不可改写，确认记录独立绑定到 manifest。
- `--expect` 可重复，每项声明一个实际输出文件，必须覆盖脚本的全部写入，包括中间科学层、
  预览和报告：`save`、`savejpg`、`savetif32`、`split` 的每个通道、`rgbcomp -out`、
  `findstar -out`、`makepsf -savepsf` 及固定 Python 适配器的 receipt。即使只在脚本内部使用、
  不作为候选的文件也要声明；`--primary-output` 只选科学主产物，不隐含任何 `--expect`。
  SSF 省略扩展名时，声明补齐后的文件名（例如 `save .../result` 对应 `result.fit`，
  `savejpg .../result` 对应 `result.jpg`）。漏声明在静态验证时报 `unsafe_siril_script`，
  消息为 `Script writes undeclared outputs: ...`，并附 `Add to the run command: --expect ...`：
  从同一次静态解析结果生成全部缺失路径，补齐扩展名并进行 shell 引用，可复制到原命令。
  修复建议不自动执行、不改变输出合同；实际执行后，每个声明文件都必须存在并通过验证。
- [080 局部对比骨架](stages/stage-08-starless-enhance.md#structure-local-contrast) 共需 13 个 `--expect`：
  `artifacts/` 下的 `080-r.fit`、`080-g.fit`、`080-b.fit`、`080-l.fit`、
  `080-l-enhanced.fit`、`080-mask.fit`、`080-gain.fit`、`080-r-enhanced.fit`、
  `080-g-enhanced.fit`、`080-b-enhanced.fit`、`080-local-contrast.fit`，以及 `previews/` 下的
  `080-mask.jpg`、`080-local-contrast.jpg`；主产物为 `artifacts/080-local-contrast.fit`。
- `--primary-output` 必须显式选择多科学产物中的主产物；只有一个科学产物时可自动选择。PSF、报告和诊断预览不能成为候选。
  receipt 记录 `primary_output`、`image_domain`、有序 `transfer_chain`、`branch_binding` 与 processing checks；图像域来自已验证回执。
- `stars.recompose` 的 `--source` 是当前已接受的非线性无星科学父源；线性源返回 `requires a nonlinear scientific parent`。
  此协议首个 SSF load 特例读取 separation receipt 的 `source_path`（内部 `@input` 解析为原输入），不读取 `--source` 或 `060-full-source.fit` 副本；
  最终 PixelMath 使用 `--source` 作为 A_prime。还须提供 `--separation-run NNN-name --stretch-run NNN-name`，
  绑定已接受且谱系、几何与链匹配的分星、拉伸及主体父源；产物声明见 [Stage 9](stages/stage-09-stars-recompose.md#stars-recompose)。
- 预期产物必须位于 `artifacts/`、`previews/` 或 `reports/`，且运行前不存在；不得重复声明同一路径。
- 同一脚本不可覆盖重跑；修订时创建新的编号脚本。
- 退出码为零但产物缺失、空文件、不可解码、容器门禁失败、父源漂移或日志出现未分类
  `error/exception/traceback`（包括 `log:` 包装）、脚本失败终态（包括 `progress:` 终态）或星表 HTTP 4xx/5xx 仍算失败；验证成功的输出仍记录为失败 run 的证据，但不能成为父源。
- 缺少 EXIF 的 JPEG 只有在同一日志随后成功读取该文件、取得完整通道统计且显示解码器通过时记为 warning；
  BrokenPipe 只有位于成功结束之后、退出码与全部产物均通过时记为 warning。其他上下文全部失败关闭。
- NL-Bayes 的 `error: no suitable data in src fits` 只有在邻近 denoise 命令、后续 NL-Bayes 日志、成功终态、退出码和全部产物验证均通过时记为 warning。
- 历史日志分类沿用冻结策略，只允许已有 receipt 回放与 finalize；见[版本兼容](session-contract.md#文档指导版本)。
- `channel_map=null` 不等于 `channel_mode=unknown`，已知通道类型不因缺少逐通道角色而触发 unknown 门。
- PCC 的 `imprecise solution` 独立记录为 `pcc_imprecise_solution` warning；它不覆盖真实运行错误或产物门禁，
  也不自动拒绝候选。是否采用由实际图像与科学测量审查决定，并保留拟合精度限制。
- JPEG 等显示产物必须由实际图像解码器完整解码像素；只有 SOI/EOI、尺寸等容器标记不算通过。当前运行时
  没有可用解码器或解码扫描数据失败时，该产物失败关闭，probe 只报告能力而不安装依赖。
- 每个 run receipt 绑定协议、脚本、SSF provenance、父源、运行时、原始日志、结构化 `log_diagnostics`、
  实际网络模式、预期产物和验证摘要。
- CLI 把每个 SSF 命令映射到 session 冻结的 Siril 1.4.4 command index，核对 scriptable 和 policy，并将
  `command_knowledge` 写入静态报告或 run receipt；该机器映射不冒充 Agent 的 manual command/read 查询。
- `background.subtract` 与 `stars.separate` 分别要求冻结且未漂移的背景样点与 StarNet 适配器环境；
  其他协议不因这些可选能力缺失而阻塞。

`--validate-only` 也必须先验证同 stem provenance；之后验证 session 路由适用性、policy、路径、声明写入、网络分类和冻结运行时绑定，
写静态报告但不启动 Siril、不创建
run receipt 或候选。之后仍可用同一脚本正常执行一次。

新 session 默认在线，Siril 不带 `--offline` 启动。`init --offline` 的 session 每个 run 都带 `--offline`，且其 SSF 不得声明
`-catalog=gaia`；`localgaia`、neutral 与远程 Gaia 三者各自仍需显式 `-catalog=`。旧 v1 session 没有冻结
`execution_policy` 时只允许已有成功 receipt replay 和 finalize，不允许创建新的静态验证或实际 run。

## Auxiliary metric report failures and recovery

New sessions freeze `reporting=isolated_metrics_v1`. After a successful receipt and
manifest entry are committed, automatic `metrics.json` generation is advisory.
A reporting exception returns the successful receipt with response-only
`report_warnings` and exit code 0. The warning preserves the exception and recovery
hint; `reports/<run-id>/metrics-error.json` records the receipt SHA and failure time
when writable. Failure to save this diagnostic is also reported without changing
the execution result. Receipt, manifest and execution log remain immutable.
Scientific validation, required previews and required protocol reports retain their
existing failure behavior. Interrupts and process-exit exceptions are not swallowed.

Explicitly recover auxiliary metrics with the ordinary Python interpreter:

```bash
"$STARUN_PYTHON" -B /abs/starun-siril/scripts/deep_sky_siril_metrics.py \
  --session /abs/session --run-id 010-input
```

Session mode forbids manual measurement arguments (`--protocol`, `--candidate`,
`--parent`, `--stars-tsv`, `--regions`, `--star-layer`, `--output`). It derives inputs
from a verified successful run, rechecks bindings before and after measurement,
and creates `reports/<run-id>/metrics-recovery.json` without overwriting evidence
or executing Siril. JSON stdout returns the report fingerprint and `reused` flag.
An existing valid recovery is returned unchanged; corrupt or conflicting evidence,
missing/pruned inputs and drift fail explicitly. Recovery errors return exit code 2
for contract failures or 3 for unexpected reporting failures. Direct measurement
mode remains available with its existing arguments.

Replay remains read-only: it reports missing/unreadable auxiliary metrics and any
valid supplemental report location, but never generates or replaces reports. Old
execution-policy sessions permit only existing replay/finalize, not recovery.

## status

```bash
python3 /abs/skill/scripts/deep_sky_siril.py status --session /abs/session
python3 /abs/skill/scripts/deep_sky_siril.py status --session /abs/session \
  --regions reports/030-background/regions.json
python3 /abs/skill/scripts/deep_sky_siril.py status --session /abs/session \
  --parameters reports/072-stretch-profile/stretch-parameters.json
```

The read-only `starun-siril.recovery-summary.v1` response reports `last_accepted_parent`, `next_stage`, `next_stage_basis`, `blocking_reasons`, `stretch_attempts` and `continuation`. The summary verifies recorded receipt/review/file bindings, labels them as such and does not replay every scientific audit. It cannot certify execution eligibility; native execution and explicit prefix reuse still use their existing full validators. Frozen Siril availability is checked without launching it. Failed-run summaries retain the receipt pointer and up to three recorded fatal/timeout details. A missing or rejected review never promotes its candidate. Zero trial slots restrict additional scientific trials, not inspection of existing evidence. Unknown input context, stale bindings, ambiguous reviews and historical science restrictions remain visible; a missing continuation record is reported without guessing why the Agent stopped. The default next-stage value is an evaluation hint, not a route or recipe. Finalized sessions return their verified committed result without resuming work.

New sessions freeze `recovery_summary=explicit_stage_decisions_v1`. A successful `review-check --review` also returns the continuation location and template. The Agent writes `reports/<run-id>/decision.json` separately with schema `starun-siril.stage-decision.v1`, exact returned `run_receipt` and `review` bindings, actual checkpoint `stage` (1..10, including stages assessed without a new run), `action=continue|stop`, `next_stage` (integer 1..10 for continue, null for stop) and a concrete `reason` of at most 800 characters. Recorded decisions return `next_path` and `next_template` for consecutive `decision-0001.json`, `decision-0002.json`, etc.; each revision binds its unchanged predecessor through `supersedes`. The latest validated decision supplies intent and an actual stop location; earlier records are not overwritten. The record describes intent only. Stale hashes, revision gaps, unknown fields, empty stop reasons and invalid stage values are rejected. Historical evidence is never backfilled or rewritten.

`--regions` returns a proposed regions.v1 contract without saving it. The old contract must have been inspected in an accepted same-lineage review; every intervening primary must be accepted and preserve coordinates. Crop/reorientation or unrelated equal-size images do not qualify. IDs, roles and rectangles remain exact, geometry is checked against scientific containers and only the reference hash is rebound. The proposal explicitly requires new-parent measurements and visual review; old medians, noise and color statistics are not reusable measurements. Save a new contract with a fresh name before calling the existing measurement/protocol tools.

`--parameters` verifies an existing solved report against the accepted parent and returns bound actual prefix FITS/JPEG and the frozen transfer chain. Reuse is conditional on an exact new-request prefix/source/ROI/runtime match; the images remain diagnostics, not scientific parents. This query starts no native trial, performs no parameter search and changes no attempt count.

## review-check

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril.py review-check \
  --session SESSION --run-id RUN_ID [--review SESSION/reviews/RUN_ID.json]
```

Read-only helper for the review receipt. Without `--review` it prints the skeleton a
review must fill: the run ID and receipt fingerprint, the protocol, every candidate,
parent preview and report the run actually produced, and five empty gates. The Agent
still opens every image and writes its own `verdict` and `notes`; this command never
writes, signs or decides a review. With `--review` it validates an existing receipt
against the same rules `finalize` applies, so a schema or binding mistake surfaces before
delivery instead of at the end. It adds no acceptance gate and no numerical threshold,
and it cannot change a session, receipt or delivered result.

## finalize

```bash
"$STARUN_PYTHON" scripts/deep_sky_siril.py finalize \
  --session SESSION --selection SESSION/final-selection.json [--keep-intermediates]
```

`final-selection.json` 必须符合 v1 schema。finalizer 验证输入未变、候选来源、selected runs、review
receipts、星点政策和限制，并提交 `reports/final-audit.json`、`reports/final-result.json` 与 v1
`manifest.finalization`。

- `success|partial_success`：验证参考图和最终候选，原子创建 `outputs/reference.jpg`、
  `outputs/final.jpg` 与 `outputs/final.fit`，再记录两者 fingerprint。
- `review_required`：验证并记录 `candidate_image` fingerprint，但不得创建正式输出。
- `failed`：要求结构化 `error`，不得声明候选或创建正式输出。

有效 `input_state` 或 `channel_mode` 任一为 `unknown` 的 session 不允许非 failed finalization；它停在 Stage 1 诊断状态，可靠来源证据经 `confirm-input` 正式确认后可在同会话继续。若仍未确认而调用 finalize，只能提交不带候选的
结构化 `failed` selection，或在获得可靠状态证据后创建新 session 继续完整处理。

相同 selection 的重复调用只验证已提交内容，并恢复尚未完成的允许清理；不同 selection 与已有提交
冲突。只有 `success|partial_success` 会触发默认阶段图像清理。

## 辅助接口

辅助工具沿用上方已验证的普通 Python 与绝对路径。下列选项不新增公共协议或主 CLI 命令；执行仍通过 `run`，
候选必须满足对应正文的前置与审查。只读报告、准备资产和静态验证都不代表原生执行成功或自动采用。

### 背景采样与科学格式

`siril_auto_samples.py` 默认 `--samples-per-line 20`，密度范围 4..64；`--samples 12..256` 指定目标总数，二者互斥。
`--report` 将诊断写到样点合同之外；`--exclude-regions` 绑定独立验证区并保护完整样点足迹，不能用于中性化 `--regions` 模式。
`--background-layout standard|limited_plane_v1` 默认 standard；受限一阶模式须显式 layout 与真实 `layout_reason`，最低 6 个目标样点。
输入不可读、非有限、来源/预览漂移或覆盖不足时不生成新的可执行合同，不放宽保护。

`--regions` 模式输出原有 regions.v1，用科学 RGB 生成中性化区域合同：`--samples 12..64` 默认 36，
`--region-size 8..64` 像素默认 16；拒绝 `--preview` 和 `--samples-per-line`。
样点选择、坐标转换、覆盖门、`# background-validation:` 的 regions/SHA-256 与可选 layout 声明，
以及区域、样点、注入 receipt 的同 run 目录要求，见 [Stage 3](stages/stage-03-background.md#background-subtract)
和[受限一阶布局](stages/stage-03-background.md#limited-plane-layout)。

Stage 3–5 的科学读取支持各自正文声明的 FITS/XISF；XISF 使用 Image 0，非单位 float bounds 拒绝。
科学输出保持 float32 FITS，不能把 JPEG 预览回退为科学来源。来源选择、单位、方向、恒等检查与原生骨架分别见
[Stage 3](stages/stage-03-background.md)、[Stage 4](stages/stage-04-astrometry-color.md)、[Stage 5](stages/stage-05-linear-restoration.md)。

### Stage 7 请求、预检与生成

`deep_sky_siril_stretch.py` 的现有入口：

| 入口 | 参数、输入与输出边界 |
|---|---|
| `morphology-context` | `--session --source --regions --preview --run-id`；冻结实际父源的上下文 manifest 与带框 PNG，仅独占写 reports，不启动 Siril、不占额度。 |
| `preflight` | 与 `init-request` 使用同组参数；只读返回完整约束表。`ready` 退出 0，`needs_revision` 退出 1；未知前缀可 pending，不创建请求、原生进程或试跑记录。 |
| `init-request` | `--mode profile\|exception` 默认 profile；普通 RGB、单色及 HDR 默认分步，用 goals.v4/strategy.v3/request.v5。显式 `--batch` 用 goals.v3/strategy.v2/request.v4；HOO 专用整链自动识别。 |
| `suggest-mtf` | `--session --source --regions --anchor-region --target-median`；读取已接受线性源与匹配 ROI，返回只读因子提案或 `no_solution`，不执行 Siril、不占额度。 |
| `solve / generate` | v4 显式 factors 与 v2 parameters；`generate --comparison` 可重复，分别绑定形态比较和末段比较，`--run-id` 可独立指定正式 run，原报告不改写。 |
| `compare-tail` | `--session --mtf-parameters --luminance-parameters --run-id`；输出 stretch-tail-comparison.v2，只比较已验证材料，不启动试跑。至少一项 solved；合格负对照仅供诊断，正式生成只接受 solved 项。 |

profile 的顺序由[形态绑定合同](stages/stage-07-stretch.md#stretch-routing-v29)冻结；首次短链、Agent 按形态选择的首次 HDR 和后续修订分别提供该正文要求的证据。
未绑定请求、裸拉伸 SSF、`--legacy-mtf`、`--search` 和自动 HDR 生成不授权新会话执行。
多节 RGB MTF 收尾须按[末段比较](stages/stage-07-stretch.md#rgb-tail-comparison)绑定对照，各 solve 共享[三次试跑额度](stages/stage-07-stretch.md#v4-attempt-accounting)。

solve 保留的前缀 FITS/JPEG、full 控制和 ROI 面板位于 `artifacts/stage7-trials/<run-id>/`，参数报告或 HDR receipt 绑定 `trial_artifacts`；
失败项仍标为诊断，不能成为父源。finalize 清理时保留这些档案。具体清单见[试跑产物](stages/stage-07-stretch.md#stage7-trial-artifacts)。

HDR 可由 Agent 根据五类形态首次选择，无需先证明单链冲突；整链用 `init-request --batch --mode exception` 冻结显式 hdr 双链与蒙版，SSF 绑定返回的 `# stretch-strategy:` 及最终保护请求，不执行普通 solve。沿用 `run --protocol stretch`，由唯一 `# hdr-stretch:` 声明选择变体；须声明 `--primary-output` 与全部 `--expect`。
完整 RGB 骨架有九份科学 FITS/四份 JPEG，mono 有四份 FITS/四份 JPEG；主产物和中间层见 [HDR](stage7-hdr.md)。
HOO 使用 dualband-request.v4；来源、三链和 OIII 输入准备见 [HOO](stage7-hoo.md)。
预检曲线诊断、因子与区域保护、HDR 最终混合及真实像素审查均由对应正文维护。

### 合星准备、分析与生成

`deep_sky_star_reduction.py prepare` 要求 `--session --source --run-id`；相对 source 按 session 解析，
必须显式选择 accepted 060-stars / 070-stretch 谱系中的当前非线性无星 primary，不扫描 Stage 8 文件名或隐式回退。
可选 `--star-request` 绑定原 prepared baseline、当前 source/ROI 和显式原生星曲线；`analyze --session --prepared --regions` 只输出 stdout。
需要保留分析时使用[成功后保存示例](stages/stage-09-stars-recompose.md#save-recomposition-analysis)。

`generate --session --request --run-id` 对应 Python `generate_recompose_assets(session, *, request, run_id)`。
返回 `source`、`primary_output`、`expected_outputs`、`script_path`、`provenance_path`、`mode`、`strength`、`separation_run` 和 `stretch_run`，
运行时使用这些精确材料。只对 active current-policy session 生成；预检失败不发布文件，不覆盖既有资产。
中断后未提交的脚本/sidecar 对不得执行或覆盖。旧 source/strength 调用返回迁移说明，compute 不授权新执行。
固定星表、星色、强度、残差与 HDR/HOO 重放和审查见 [Stage 9](stages/stage-09-stars-recompose.md#agent-recomposition-proposal)。

### 颜色、蒙版与 tone

`deep_sky_siril_color.py` 接受显式 `--target-gain --strength --goal --parameter-reason`。
`region_goals / --region-goals` 与 `--target-gain` 互斥；`--mask-mode none` 生成一份原生诊断对照。
零目标跳过且不要求执行参数；非零目标须显式 `--saturation-protect-start` / `--saturation-protect-end`，缺参数不补默认或扫描网格。
`--mask-mode luminance` 的首次蒙版比较须有 `--previous --observation`，保持同源、ROI 和 D；cyan 仍为显式蒙版变体。
`--selector all|cyan` 默认 all，cyan 须显式 target-gain，零目标不试跑。

`--curve ght|invght` 默认 ght，Python `solve` 接受 `curve='ght', sp=None, hp=None`。
非零 inverse 需要显式有限 D/SP/HP：`0<D<=10`、`0<=SP<=HP<=1`；B=0、LP=0、全通道、`-sat` 和 rgbblend 固定。
例：`--curve invght --strength 1.2 --sp 0.5 --hp 0.7`，不是默认值。forward 拒绝 SP/HP，保留原声明与命令顺序。
每次一份提案，修订基于同父源的实际观察。逐区域目标、保护、报告与正式生成见
[color.finish](shared-protocols/color-finish.md#unmasked-first-color)和[inverse 变体](shared-protocols/color-finish.md#inverse-saturation-candidate)。

`deep_sky_siril_subject_mask.py prepare --session --request --run-id [--timeout]` 返回报告路径与精确 binding。
局部对比 request.v4 接受 subject_mask；颜色 helper 的 `--subject-mask --high-start --high-end` 只用于 starless GHS、mask-mode none、selector all。
字段、羽化和空间支持见 [Stage 8 主体蒙版](stages/stage-08-starless-enhance.md#subject-spatial-mask)及[共用调色协议](shared-protocols/color-finish.md)。

`tone.adjust` 要求 accepted nonlinear 科学父源；非线性原始输入先接受唯一 input.inspect。
声明 FITS 与直接父源/候选 JPEG 三份产物，并选 FITS 为 primary；receipt 记录 tone_chain 并继承原 transfer_chain 与 separation binding。
`# tone-adjust:` 提案和 source-bound regions 为必需输入；缺失/漂移时 run 和 validate-only 拒绝，实际执行写 `reports/<run-id>/tone-diagnostics.json`。
红色局部亮度仅接受 local-tone 的 red、midtones、regions。所有选参、原生骨架、修订和审查见
[tone.adjust](shared-protocols/tone-adjust.md#remaining-goal-tone)。

### 降噪、PSF 与 StarNet 适配器

`deep_sky_siril_denoise.py analyze/generate` 使用显式请求，analyze 只报告，generate 返回一份 SSF、provenance 和 expected outputs；
缺 modulation、cosmetic 或参数理由拒绝，modulation=0 或 decision=skip 不生成科学候选。
Stage 5 使用 linear-denoise-request.v2 选择 backend，HOO/OIII 保留 v1；完整参数、准备、诊断输出与回退证据见
[Stage 5](stages/stage-05-linear-restoration.md#deepsnr-primary)和[HOO](stage7-hoo.md)。
`deep_sky_siril_chroma.py solve/generate` 的输入、输出与报告见[非线性降噪](shared-protocols/denoise-nonlinear.md#pre-color-chroma-denoise-chroma_bilateral_v1)。
reports 下的 pre/post-color probes 不成为科学父源；延续与交付使用已接受的 denoise FITS 及其直接预览。

`siril_psf_samples.py` 仅由固定 pyscript 入口调用，沿用 `--contract --contract-sha256 --expected-source --receipt`；
原有 metrics `--stars-tsv` 不变。六项预期产物和原生 PSF 合同见[反卷积](stages/stage-05-linear-restoration.md#restoration-deconvolve)。
StarNet 2.6.2 使用 `siril_starnet_linear.py` 的 contract/receipt v2；受支持旧版本使用 `siril_starnet.py` v1，失败不切换适配器。
合同绑定实际 full-source input_fits、显式有限 shadows_clipping<0 和 0<target_background<1；
临时交接留存至科学验证及 receipt 提交成功。流程、精度、分支与原生范围审查见[分星](stages/stage-06-stars-separation.md#stars-separate)。

Stage 6 精度不符为 `native_input_precision_invalid`，bridge 缺失/无效为 `starnet_bridge_unverified`，其他执行失败为 `stars_separation_failed`。
失败 receipt 保留 `failure={reason_code,reason,log_path}`、`rejected_outputs=[{original_path,path,sha256,size}]`、`quarantine_errors=[{path,reason}]`，
`outputs=[]`、`primary_output=null`；声明的科学/预览文件隔离到 `reports/<run-id>/rejected/`，日志、合同与运行工作区保留，CLI 返回非零。

## 环境与协议回执

| 能力 | 配置、输出与失败边界 |
|---|---|
| Gaia | `DEEP_SKY_SIRIL_GAIA_ASTRO_FILE` 为 astrometric 文件，独立于 photometric `DEEP_SKY_SIRIL_GAIA_DIR`。仅 local Gaia 绑定 core.catalogue_gaia_astro；remote 不需要本地文件且不得设置该项。offline 拒绝 remote；缺本地文件返回 `local_gaia_astro_missing`，缺坐标/采样来源同样阻塞。完整 WCS 保存并记录 `preserved_existing_solution`。 |
| Stage 4 | `probe.tools.spcc_profiles` 提供原生响应名及指纹；receipt 的 color_calibration 含 method、claim、source_sha256、catalogue_mode、fallback_reason，以及可选的 route_history（阶梯记录）与 fit_quality（从绑定日志解析的拟合质量，`degenerate` 禁止 accept），SPCC/neutral 的附加字段见 [Stage 4](stages/stage-04-astrometry-color.md#color-calibrate)。`reports/stage-04-preflight.md` 为 Agent 记录，不是新 CLI 参数或执行凭证。 |
| DeepSNR | `DEEP_SKY_SIRIL_DEEPSNR_BIN` / `DEEP_SKY_SIRIL_DEEPSNR_MODEL` 指向已安装 CLI 与 model 2 `.onnx` / `.mlpackage`；否则从 PATH/标准目录发现。只支持 1.3.2，固定 `--linear --model 2 --stride 480`，不下载、安装或检查更新。缺失/不匹配只阻塞本路线；原适配器位置仅在适配器、可执行文件和模型指纹仍匹配时支持只读 receipt 回放，新执行使用当前安装路径，不代装或替换运行时。 |
| XISF metrics | `--candidate` / `--parent` 接受 XISF，不读其 JPEG 代替科学源。解码失败通过 `report_warnings` 报 `metrics_measurement_unavailable` 及路径/原因，不改已提交 receipt 或执行状态；非有限数仍保留且 uncertain。旧输入另用手动参数与新 `--output` 写补充诊断，不替换旧报告；`--session` recovery 仍限完整 current-policy session。 |
| 星层 metrics | `--star-layer` 仅与 `--protocol stars.separate --parent --regions` 共用，绑定星层哈希且不得覆盖输入/区域合同；不会增加公共 run 参数。详见 [ROI 诊断](stages/stage-06-stars-separation.md#stars-separate-可选主体-roi-诊断)。 |
| 合星/交付 | processing_checks.closure 保留有符号指标、warnings 和最终组成/端点证据；delivery_export.histogram_gate 的 `global_contrast_span_low` 只告警，不自动接受或拒绝。缺正式审查为 review_required，硬失败非零。所有适用门及实际材料仍由 [Stage 9](stages/stage-09-stars-recompose.md)和[交付协议](delivery.md)核对。 |
| delivery.render | `--expect` 声明一份 float32 FITS 与重开后的 JPEG，`--primary-output` 选 FITS，final-selection 仍选 JPEG。receipt 绑定 delivery_export / processing_checks.delivery_export；audit/result 记录 final_fits，manifest 记录 final_fits_sha256，缺失/漂移阻断成功。review_required/failed 不创建三份正式文件；旧两 JPEG 交付不回填。骨架见 [Stage 10](stages/stage-10-delivery-finishing.md#delivery-render)。 |

## 阶段规则与历史入口

阶段特有算法、数值门、review 字段和历史语义由以下正文维护；没有新增 CLI 参数的版本更新不在本文件重复展开。
历史 session 保留其冻结策略及原证据，允许的回放/finalize 和派生诊断见 [session 文档指导版本](session-contract.md#文档指导版本)；
旧批处理 CLI 与专用 decision JSON 已停用。当前 SSF 仍须绑定当前正文的完整 SHA-256，未知或混搭指纹拒绝。

<a id="protected-background-sampling-policy"></a>
<a id="scientific-stage-3-sources"></a>
<a id="背景独立证据"></a>

[Stage 3正文](stages/stage-03-background.md)。

<a id="batch-stage-4-color-decisions"></a>
<a id="native-neutral-scientific-policy"></a>
<a id="neutral-held-out-benefit-and-paired-safety"></a>
<a id="stage-4-audit-fields"></a>
<a id="stage-4-scientific-container-validation"></a>

[Stage 4正文](stages/stage-04-astrometry-color.md)。

<a id="agent-proposed-linear-denoise"></a>
<a id="batch-stage-5-restoration-decisions"></a>
<a id="stage-5-deepsnr-backend"></a>
<a id="stage-5-scientific-restoration-validation"></a>

[Stage 5正文](stages/stage-05-linear-restoration.md)。

<a id="030-runtime-and-diagnostics"></a>
<a id="可选星层-roi-测量"></a>

[Stage 6 分星与 ROI 诊断](stages/stage-06-stars-separation.md#stars-separate)；PSF 适配器见[Stage 5](stages/stage-05-linear-restoration.md#restoration-deconvolve)。

<a id="morphology-guided-stretch-requests-v23"></a>

[Stage 7正文](stages/stage-07-stretch.md)。 `artifact-focus` 从初始父源、参数回执或正式运行生成只读审查材料；声明和追加见[伪影审查接口](stage7-artifact-focus.md)。 `preflight/init-request --stepwise`、逐步 `solve`、显式 `finish` 与新版 schema 见[分步接口](stage7-stepwise.md)；默认逐步反馈，显式 --batch 才整链，`status` 只读显示分支、步数和剩余额度。

<a id="ai-形态观察与现有参数接口"></a>

[Stage 1 观察模板](stages/stage-01-input-inspect.md#input-inspect-ai-morphology)。

<a id="multiscale-local-contrast-v21"></a>
<a id="stage-8-protected-masks-and-display-selectors"></a>
<a id="stage-8-冻结纹理门"></a>

[Stage 8正文](stages/stage-08-starless-enhance.md)。

<a id="explicit-stage-9-generation"></a>
<a id="fixed-stage-9-review-materials"></a>
<a id="native-capabilities-in-020"></a>
<a id="recomposition-and-delivery-warnings"></a>
<a id="合星显式提案与审查"></a>

[Stage 9正文](stages/stage-09-stars-recompose.md)。

<a id="paired-fitsjpeg-delivery"></a>

[Stage 10正文](stages/stage-10-delivery-finishing.md)。

<a id="color-helper-unmasked-first-evaluation"></a>
<a id="preservation-extensions"></a>

[共用调色正文](shared-protocols/color-finish.md)。

<a id="nonlinear-tone-adjustment"></a>
<a id="remaining-goal-tone-binding"></a>

[共用亮度正文](shared-protocols/tone-adjust.md)。

<a id="chroma-denoise-v1"></a>

[共用降噪正文](shared-protocols/denoise-nonlinear.md)。

<a id="benefit-first-adoption-and-native-range-advisory"></a>
<a id="reviewed-batch-continuation"></a>
<a id="xisf-full-image-metrics"></a>
<a id="共用协议正文绑定"></a>

[输入与版本兼容正文](session-contract.md#文档指导版本)。

<a id="explicit-dwarf-filter-metadata"></a>

[滤镜元数据合同](session-contract.md#explicit-filter-metadata-policy)。

## Stage 9 contour assets

The existing recomposition generator accepts `starun-siril.recomposition-request.v2` with optional `contour={amount,radius_px}`. Generation writes only the SSF/provenance and hash-bound auxiliary TIFF/PNG/report; Siril writes every scientific FITS. Use every returned expected output, including weights, processed stars and the same-contribution control. Omitted/zero contour retains the original recipe; contribution strength remains independent.

## Morphology multistage body routes

New Stage 7 body candidates require at least two executed transforms. One-step finish, standalone baselines, compare-mode single-step controls and legacy MTF generation are rejected. `--stepwise` and `--batch` are mutually exclusive; omitted execution selects stepwise for ordinary RGB/mono/HDR and batch for HOO. API `stepwise=None/True/False` has the same meaning. Existing schemas are retained without implicit conversion. Stage 9 Fs, HDR internal branches and HOO RGB references keep their existing semantics; read-only `suggest-mtf` cannot authorize a standalone body candidate.
