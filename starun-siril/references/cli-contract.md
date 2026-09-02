# Standalone Contract v1 CLI

薄 CLI 只执行确定性基础设施，不选择处理流程。公开命令固定为 `probe`、`init`、`run` 和 `finalize`。
它不下载工具，也不依赖 Starun 仓库或宿主结果格式。

## probe

```bash
python3 scripts/deep_sky_siril.py probe [--offline] [--output /abs/probe.json]
```

输出 Siril、StarNet2、StarNet 模型、本地 Gaia、JPEG 解码能力和协议级 SirilPy 状态。可选能力缺失只在
需要它的协议上阻塞。探测不会安装、下载或修改运行时。

## init

```bash
python3 scripts/deep_sky_siril.py init INPUT --session SESSION \
  [context options] [--container-validation siril|strict]
```

`SESSION` 必须不存在或为空。命令创建 v1 `session.json`、`manifest.json`、初始 tool probe 和固定目录；
它自动完整验证离线手册 Bundle 和 command policy，并冻结其 evidence、输入、用户约束、保留策略、容器
验证模式和 `execution_policy`，不生成处理计划。执行策略固定为 `log_diagnostics=fail_closed_v1` 与
`network=offline_default_explicit_gaia_v1`。执行任务无需在 `init` 前另跑一次 `--verify-bundle`。

已知 `--input-state linear|nonlinear` 至少需要一个 `--state-evidence`。`auto|unknown` 不得附状态证据。
`--channel-map` 只声明单个三通道 master 的来源角色。通道类型或角色没有可靠用户、采集或文件内证据时
使用 `--channel-mode unknown`，不得从文件名、色调或设备型号推断。

`--container-validation siril` 为默认值。对 FITS/XISF 科学产物，它启动新的 Siril 进程执行
`load → stat → close`，并从日志取得格式、宽、高、通道和有限统计。`strict` 先用标准库遍历 FITS 或
检查 monolithic XISF，再执行相同的 Siril 重开，并要求两种观察的格式与几何一致。该模式写入
`session.context.container_validation`；`run` 没有覆盖参数。

## run

```bash
python3 scripts/deep_sky_siril.py run \
  --session SESSION --protocol PROTOCOL_ID --script SESSION/scripts/NNN-name.ssf \
  --source CURRENT_PARENT --expect SESSION/artifacts/result.fit \
  --expect SESSION/previews/result.jpg [--timeout 1800] [--validate-only]
```

规则：

- 一次只执行一份脚本和一个协议。
- 脚本必须位于 session 的 `scripts/`，名称符合 `NNN-name.ssf`。
- 脚本必须有自动按同 stem 发现的 `scripts/NNN-name.provenance.json`；CLI 不提供绕过或另传 sidecar 的
  参数。sidecar 必须遵循 `ssf-provenance.schema.json`，脚本、primary protocol reference、policy 与实际
  manual command/read evidence 的路径和 SHA-256 均须匹配。
- 父源必须是原始输入或成功 run receipt 中已验证的产物。
- `input.inspect` 必须直接读取 immutable `@input`。当 session 的 `input_state=unknown` 时，执行期机器门只允许
  `input.inspect`；审查后停止，不得把 direct/autostretch 诊断预览作为 `delivery.render` 或科学处理父源。
  获得可靠状态证据后以该证据创建新 session，因为 session 上下文不可改写。
- 预期产物必须位于 `artifacts/`、`previews/` 或 `reports/`，且运行前不存在。
- 同一脚本不可覆盖重跑；修订时创建新的编号脚本。
- 退出码为零但产物缺失、空文件、不可解码、容器门禁失败、父源漂移或日志出现未分类
  `error/exception/traceback` 仍算失败；验证成功的输出仍记录为失败 run 的证据，但不能成为父源。
- 缺少 EXIF 的 JPEG 只有在同一日志随后成功读取该文件、取得完整通道统计且显示解码器通过时记为 warning；
  BrokenPipe 只有位于成功结束之后、退出码与全部产物均通过时记为 warning。其他上下文全部失败关闭。
- JPEG 等显示产物必须由实际图像解码器完整解码像素；只有 SOI/EOI、尺寸等容器标记不算通过。当前运行时
  没有可用解码器或解码扫描数据失败时，该产物失败关闭，probe 只报告能力而不安装依赖。
- 每个 run receipt 绑定协议、脚本、SSF provenance、父源、运行时、原始日志、结构化 `log_diagnostics`、
  实际网络模式、预期产物和验证摘要。
- CLI 把每个 SSF 命令映射到 session 冻结的 Siril 1.4.4 command index，核对 scriptable 和 policy，并将
  `command_knowledge` 写入静态报告或 run receipt；该机器映射不冒充 Agent 的 manual command/read 查询。
- `background.subtract` 才要求冻结且未漂移的 SirilPy 适配器；其他协议不因其缺失而阻塞。

`--validate-only` 也必须先验证同 stem provenance；之后验证 session 路由适用性、policy、路径、声明写入、网络分类和冻结运行时绑定，
写静态报告但不启动 Siril、不创建
run receipt 或候选。之后仍可用同一脚本正常执行一次。

新 session 中所有协议默认用 `--offline` 启动 Siril。唯一例外是非 offline session 的 `color.calibrate` SSF
显式声明 `pcc/spcc -catalog=gaia`；`localgaia`、neutral 和其他协议仍离线。旧 v1 session 没有冻结
`execution_policy` 时只允许已有成功 receipt replay 和 finalize，不允许创建新的静态验证或实际 run。

## finalize

```bash
python3 scripts/deep_sky_siril.py finalize \
  --session SESSION --selection SESSION/final-selection.json [--keep-intermediates]
```

`final-selection.json` 必须符合 v1 schema。finalizer 验证输入未变、候选来源、selected runs、review
receipts、星点政策和限制，并提交 `reports/final-audit.json`、`reports/final-result.json` 与 v1
`manifest.finalization`。

- `success|partial_success`：验证参考图和最终候选，原子创建 `outputs/reference.jpg` 与
  `outputs/final.jpg`，再记录两者 fingerprint。
- `review_required`：验证并记录 `candidate_image` fingerprint，但不得创建正式输出。
- `failed`：要求结构化 `error`，不得声明候选或创建正式输出。

unknown session 不允许非 failed finalization；它停在 Stage 1 诊断状态。若调用 finalize，只能提交不带候选的
结构化 `failed` selection，或在获得可靠状态证据后创建新 session 继续完整处理。

相同 selection 的重复调用只验证已提交内容，并恢复尚未完成的允许清理；不同 selection 与已有提交
冲突。只有 `success|partial_success` 会触发默认阶段图像清理。
