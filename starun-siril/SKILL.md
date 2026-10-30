---

name: starun-siril
description: |
  Process calibrated, stacked deep-sky masters with Siril 1.4.4 CLI: background extraction, astrometry/color calibration, denoising/deconvolution, StarNet, stretching, star reduction and delivery. Use for processing, session-bound SSF generation or offline manual queries. Excludes RAW calibration/registration/stacking, GUI automation and general retouching.
license: Proprietary
metadata:
  slug: starun-siril
  version: "0.3.3"
  displayName: Starun-siril
  summary: 以独立、可审计的 Siril CLI 会话处理已完成暗场/平场校准与对齐叠加的深空 master，支持自适应缩星合星，并由真实像素审查控制正式交付。
  tags: "astronomy, siril, deep-sky, image-processing, star-reduction"
  homepage: https://github.com/MZqk/deepsky-skill
---

# Starun-siril

## 输入前置边界（Design Scope）

只处理已完成暗场、平场校准及配准叠加的单个深空 master；支持 FITS 与现有 XISF/TIFF 兼容。不处理原始 Light 帧的校准、配准、叠加、GUI 自动化或通用修图。

进入依赖处理前分别确认暗场、平场及配准叠加来源，复用当前用户确认、同图可信记录或明确 Header 证据；文件名、设备名和预览不能证明前置完成。缺项时停止依赖处理。校准叠加与线性状态分别判断，不从外观推断未知状态或通道来源。

## 合同

使用 standalone contract `1`、`siril-cli >=1.4.4,<1.5`。保留原输入与既有脚本、审查及产物，不覆盖证据。Agent 选协议与参数、编写 SSF 并实际审查；Python 执行和验证确定性基础设施，科学像素由 Siril 写入，不自动接受候选。

不下载或安装工具、模型、插件或完整星表。默认在线，`init --offline` 显式收紧网络；`platesolve/pcc/spcc` 须显式且恰好指定一个 `-catalog=gaia|localgaia`。冻结手册提供语法语义，[command policy](references/command-policy.json) 决定命令授权。

## 定向模式

按模式读取材料；分别报告静态验证、执行和像素审查。

| 模式 | 边界与入口 |
|---|---|
| 纯知识查询 | 读[手册查询协议](references/manual-query.md)，检索并读取命中原文；不 probe、不建 session、不检查或处理图像。 |
| session 绑定脚本生成 | 按[开始](#开始)冻结真实运行时、输入与路径，已知协议读下表正文，未定时查索引，生成 SSF/provenance 并 `run --validate-only`；不执行 input.inspect 或目标脚本，不承诺便携脚本。 |
| 定向执行 | 按[开始](#开始)初始化或验证 session，执行并审查 input.inspect（已接受同图检查可复用），随后只执行用户指定且前置满足的协议。 |
| 完整处理 | 按[开始](#开始)初始化，再依[阶段顺序](references/stage-sequence.md)逐阶段评估；进入阶段时才读对应导引，不一次加载全部阶段。 |

必要父源与上游证据必须真实存在；缺失时说明限制，不虚构执行、审查或擅自补做处理。

## 开始

实际执行与绑定脚本生成先读[解释器与入口](references/cli-contract.md#解释器与入口)、[init](references/cli-contract.md#init)及[session 不变量](references/session-contract.md#不变量)。资源相对本技能目录解析，辅助脚本使用已验证普通 Python 与绝对路径。

核对输入前置证据后 probe/init；init 已验证并冻结 Bundle，不重复手动验证。已有 session 先验证冻结绑定。实际执行中，有效 `input_state` 或 `channel_mode` 为 unknown 时只完成 input.inspect 与审查，停止依赖处理；取得可信声明后按 [confirm-input](references/cli-contract.md#confirm-input)一次补齐未知项，不改初始字段。`channel_map=null` 不等于类型 unknown。

新会话默认 `style=vivid`、`dualband=off`；窄带配色须用户显式选择。用户选择 OSC HOO 时，初始化前先读[配色路线](references/stages/stage-01-input-inspect.md#palette-route-selection)。

`run --source` 传当前已接受科学父源的绝对路径，Stage 1 使用 session 的 `input.path`；SSF 的 load 使用同一路径。`@input` 仅为内部回执标记，不能传作 CLI 路径。

## Agent 循环

实际执行逐协议完成以下循环；脚本生成只写脚本与静态验证，不接受候选。

1. 已知协议直读正文，未定时查索引，按用户目标、冻结上下文与真实科学测量选参，不以摘要代替正文。默认一份保守候选，有具体观察才修订；阶段特例按原文。完整处理逐阶段评估，不以预计收益小省略要求的测量或候选。
2. 用新编号写单协议 `scripts/NNN-name.ssf` 与同 stem provenance；按协议映射绑定唯一 primary 正文及完整 SHA-256。完整骨架可参数化；语法、变体或结果存疑时按手册查询协议保存真实 command/read evidence。
3. 按 [run 合同](references/cli-contract.md#run)声明父源、primary output 及全部预期产物。失败产物仅作诊断，不作为科学父源。
4. 打开要求的父源、候选及局部材料，按[质量协议](references/quality.md#review-receipt)取 review 骨架并记录观察；仅 accept 且适用门全 pass 才采用。数值、摘要与退出码不替代实际像素审查。
5. 采用后继续已授权工作；拒绝或不确定时保留已接受父源，继续可独立步骤。材料打不开、哈希漂移或来源冲突时停止依赖执行；修订另建编号，不改旧证据。换父源或几何后重新核对 ROI 并测量。

恢复或上下文变化时用[只读 status](references/cli-contract.md#status)查父源、阻塞及剩余额度。review-check 成功后直接使用返回的 continuation，按其模板记录继续或实际停止理由；不重复全量扫描，也不以承诺下一步结束完整处理。

## 多文件串行处理

按用户文件顺序逐个处理，每份独立 session；当前文件完成、失败或待审后再处理下一份。逐份核对状态、约束、父源与 ROI，不跨文件套用参数或审查结论。待审保留证据，不自动 accept；最终逐文件报告状态、路径和限制。

## 按需读取

下表直达唯一 primary 正文；未定时读[索引](references/protocol-index.md)。Stage 7 修订、换链及对照共享[三次额度](references/stages/stage-07-stretch.md#v4-attempt-accounting)。

| 协议 | 正文 |
|---|---|
| `input.inspect` | [正文](references/stages/stage-01-input-inspect.md) |
| `geometry.crop-near-black` | [正文](references/stages/stage-02-geometry.md) |
| `background.subtract` | [正文](references/stages/stage-03-background.md) |
| `astrometry.solve` / `color.calibrate` | [正文](references/stages/stage-04-astrometry-color.md) |
| `restoration.denoise` / `restoration.deconvolve` | [正文](references/stages/stage-05-linear-restoration.md) |
| `stars.separate` | [正文](references/stages/stage-06-stars-separation.md) |
| `stretch`（形态多阶段，默认逐步反馈） | [正文](references/stages/stage-07-stretch.md) |
| `structure.local-contrast` / `color.map` | [正文](references/stages/stage-08-starless-enhance.md) |
| `color.finish` | [正文](references/shared-protocols/color-finish.md) |
| `stars.recompose`（贡献与轮廓独立评估） | [正文](references/stages/stage-09-stars-recompose.md) |
| `restoration.denoise-nonlinear` | [正文](references/shared-protocols/denoise-nonlinear.md) |
| `tone.adjust` | [正文](references/shared-protocols/tone-adjust.md) |
| `delivery.render` | [正文](references/stages/stage-10-delivery-finishing.md) |

| 当前需要 | 材料 |
|---|---|
| 形态与退化 | [目标形态参考](references/target-morphology-review.md)、[排查参考](references/troubleshooting.md)，定位首次出现的阶段 |
| 缺 WCS 或源头 Header | [源头保留协议](references/astrometry-source-preservation.md)，写新文件后新建 session |
| 路径、工具或解释器故障 | [安全协议](references/siril-safety.md) |
| 测量与审查恢复 | [质量协议](references/quality.md)的相应小节 |

## 交付

先读[交付协议](references/delivery.md)，独立执行并审查 delivery.render，再按 [finalize](references/cli-contract.md#finalize)提交 final-selection。仅 success/partial_success 创建正式 reference/final；审查不确定用 review_required。

## 最终回复

成功时展示 `outputs/final.jpg`、`outputs/final.fit` 绝对路径；否则说明没有正式交付。报告真实状态、采用协议、星点状态、限制与保留情况，依据真实记录提炼说明。

## 历史入口与兼容

<a id="scientific-stage-3-sources"></a>
<a id="native-stage-4-xisf-inputs"></a>
<a id="fixed-recomposition-review-materials"></a>
<a id="dwarf-filter-provenance"></a>
<a id="protected-background-samples"></a>
<a id="native-dualband-background-neutralization"></a>
<a id="assessed-stage-5-restoration"></a>
<a id="explicit-stage-9-source"></a>

旧入口按上方路由读取，历史 session 按[文档指导版本](references/session-contract.md#文档指导版本)复核冻结证据；回放/finalize 及明确获准的派生诊断沿用原边界，不新增科学处理、不迁移或改写旧证据。
