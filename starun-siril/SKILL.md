---
name: starun-siril
description: >
  使用 Siril 1.4 CLI 处理单个已完成校准、配准和堆栈的深空 master，或按需生成、执行和审查
  单项 Siril .ssf 脚本，并从版本锁定的离线 Siril 1.4.4 手册查询功能、命令和参数。用于背景扣除、
  颜色校准与窄带映射、降噪、反卷积、StarNet 分星、拉伸、合星、最终调色和 JPEG 交付；
  不用于原始帧堆栈、GUI 自动化或普通摄影修图。
license: Proprietary
metadata:
  slug: starun-siril
  version: "0.1.0"
  displayName: Starun-siril
  summary: 以独立、可审计的 Siril CLI 会话处理已堆栈深空 master，并由真实像素审查控制正式交付。
  tags: [astronomy, siril, deep-sky, image-processing]
  homepage: https://github.com/MZqk/deepsky-skill
---

# Starun-siril

## 合同

使用 standalone contract `1` 和 `siril-cli >=1.4.4,<1.5`。只处理一个已堆栈 master；永不覆盖输入。

Agent 负责根据证据选择协议、编写编号 `.ssf` 及其来源 sidecar、逐个运行并审查真实产物。Python 只负责 CLI 执行、
路径隔离、哈希、日志、容器验证、产物验证和最终提交，不增强像素。Siril 默认离线；只有非 offline session
中 `color.calibrate` 明确使用远程 Gaia catalog 时才联网。本 Skill 不下载或安装工具。

SSF 的知识链分五层：当前 session、用户目标与实际像素证据决定是否适用及参数；
[默认阶段顺序](references/stage-sequence.md) 规定完整后期任务的宏观处理域；对应 protocol reference
提供科学边界、参数化骨架、预期产物和审查要求；冻结的 Siril 1.4.4 手册提供命令语法与语义；
`command-policy.json` 单独提供执行授权。默认顺序不是 Recipe，协议骨架也不是固定参数流水线，Python
不生成、选择或拼装 `.ssf`。手册完整随包分发，但只在功能相关、骨架未展开、语法有歧义或参数需要
确认时读取原文。

## 开始

1. 读取 [CLI 合同](references/cli-contract.md)、[默认阶段顺序](references/stage-sequence.md) 和
   [协议索引](references/protocol-index.md)。
2. 执行任务由 `init` 自动完整验证并在 session 冻结一次离线手册 Bundle，不要在此前手动重复验证。
   只有纯知识查询、没有 session 的任务才显式执行：

   ```bash
   python3 -B /abs/starun-siril/scripts/query_siril_manual.py --verify-bundle
   ```

3. 从本文件所在目录解析 `scripts/deep_sky_siril.py`，始终使用绝对路径。
4. 探测运行时：

   ```bash
   python3 /abs/starun-siril/scripts/deep_sky_siril.py probe
   ```

5. 创建空 session；此步自动验证并冻结 Bundle。已知线性状态必须附具体证据；扩展名或直方图外观不是证据：

   ```bash
   python3 /abs/starun-siril/scripts/deep_sky_siril.py init /abs/master.fit \
     --session /abs/session --input-state linear \
     --state-evidence "user supplied a linear stacked master" \
     --channel-mode unknown --target-name M81 --target-type galaxy \
     --container-validation siril
   ```

`--container-validation siril` 是默认模式：每个 FITS/XISF 科学产物都必须由新的 Siril 进程重新打开并
取得有限统计。高保证场景使用 `strict`，在 Siril 重开之外再执行标准库全容器检查并核对格式与几何。
该模式由 `init` 冻结，后续 `run` 不可覆盖。

`init` 将 fail-closed 日志诊断与默认离线网络策略冻结进 session。`--offline` 进一步禁止显式远程 Gaia；
不传该参数也不会让其他协议联网。只有用户明确要求保留阶段内容时传 `--keep-intermediates`。

## Agent 循环

1. 按 [默认阶段顺序](references/stage-sequence.md) 从 Stage 1 开始评估。使用 `input.inspect` 生成输入
   direct/autostretch 预览，并实际打开预览。输入状态或通道角色没有可靠来源时保持 `unknown`，不得从
   文件名、色调或设备型号补推；unknown 在本阶段后停止，获得可靠状态证据后创建新 session。
2. 按 Stage 2 → 10 依次评估当前阶段；没有图像证据、用户目标或运行能力支持时明确跳过。一个阶段含
   多个协议时也必须逐个执行，不能把它们合并进同一 SSF，也不能在审查前进入下一协议。
3. 打开协议原文，从参数化骨架生成一份 `scripts/NNN-name.ssf`。session 已冻结 Bundle 验证且逐字采用
   完整骨架时，不机械地逐命令重复查询；骨架未展开的变体、语法或参数歧义、运行结果与预期矛盾时，按
   [手册查询协议](references/manual-query.md) 查询并保存原始 command/read envelope。
4. 为每份交给 `run`（包括 `--validate-only`）的 SSF 创建同目录、同 stem 的
   `scripts/NNN-name.provenance.json`，遵循
   [SSF provenance Schema](references/ssf-provenance.schema.json)。它必须绑定脚本哈希、唯一 primary protocol
   reference、policy 哈希、实际手册查询证据和参数选择理由；没有查询时明确写 `manual_lookup.status=not_needed`
   及原因。不同协议不得合并到同一脚本。
5. 调用 `run`，显式传入协议、脚本、当前父源和每个预期产物：

   ```bash
   python3 /abs/starun-siril/scripts/deep_sky_siril.py run \
     --session /abs/session --protocol restoration.denoise \
     --script /abs/session/scripts/055-denoise.ssf \
     --source /abs/session/artifacts/050-deconvolve.fit \
     --expect /abs/session/artifacts/055-denoise.fit \
     --expect /abs/session/previews/055-denoise.jpg
   ```

   run receipt 的 `log_diagnostics` 为 failed 时不得审查或采用其产物；保留上一成功且已接受的父源，并记录
   该阶段限制。退出码 0 和可解码输出不能覆盖未知 Siril error/exception。

6. 打开协议要求的候选、父源预览、差分或报告。按 [质量协议](references/quality.md) 写
   `reviews/NNN-name.json`；内容符合 [review Schema](references/review.schema.json)，并绑定 run receipt
   与被检查材料的 SHA-256。
7. 只有 `verdict=accept` 且全部适用门为 `pass` 时，才把候选作为同阶段下一协议或下一阶段的父源。
   否则保留父源；不得因为后续阶段需要输入而自动接受当前候选。
8. 默认只生成一个保守候选；观察到具体、可修正问题时才生成修订候选。

材料打不开、哈希漂移、证据冲突或观察不确定时安全停止，不猜测、不无限重试。

## 交付

读取 [交付协议](references/delivery.md)，用独立 `delivery.render` 脚本生成最终候选，完成最终视觉审查，
再写 `final-selection.json`：

```bash
python3 /abs/starun-siril/scripts/deep_sky_siril.py finalize \
  --session /abs/session --selection /abs/session/final-selection.json
```

只有 `success|partial_success` 会创建正式的 `outputs/reference.jpg` 和 `outputs/final.jpg`。状态为
`review_required` 时必须保留并验证候选及审计证据，但不得创建正式输出；`failed` 只记录结构化错误。
成功提交后默认清理阶段图像；`review_required|failed` 或 `--keep-intermediates` 保留现场。

## 定向模式

用户只要求某项操作时，仅运行对应协议。用户只要求可执行脚本时，仍先 `probe/init` 冻结真实路径，
生成 `.ssf` 和同 stem provenance 后调用 `run --validate-only`；静态验证不代表 Siril 已执行或图像已验收。用户只询问 Siril
功能、命令或参数时，使用离线手册检索并读取命中原文，不创建 session、不执行命令。

## 按需读取

- 所有运行先读 [CLI 合同](references/cli-contract.md)、[默认阶段顺序](references/stage-sequence.md) 与
  [session 合同](references/session-contract.md)。
- 选择处理步骤时从 [协议索引](references/protocol-index.md) 打开对应协议原文。
- 审查候选或最终图时读 [质量协议](references/quality.md)。
- 生成最终交付时读 [交付协议](references/delivery.md)。
- 调试命令、路径、网络或适配器时读 [Siril 安全协议](references/siril-safety.md)。
- 查询功能、参数或编写不熟悉的 `.ssf` 时读 [手册查询协议](references/manual-query.md)。

机器合同为 [命令安全策略](references/command-policy.json)、
[背景样点 Schema](references/background-sample-contract.schema.json)、
[SSF provenance Schema](references/ssf-provenance.schema.json)、
[review Schema](references/review.schema.json) 和
[final-selection Schema](references/final-selection.schema.json)。

## 最终回复

若状态为 `success|partial_success`，展示 `outputs/final.jpg` 的绝对路径。否则明确说明没有正式交付。
始终报告状态、实际采用的协议、星点状态、限制和中间图像保留情况；只陈述 session、receipts 和产物能
证明的事实。
