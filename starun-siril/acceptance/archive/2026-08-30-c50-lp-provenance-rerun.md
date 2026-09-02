# C50 LP standalone v1 provenance 复验（2026-08-30）

## 1. 结论

本轮已在新的 immutable session 中完成 provenance 合同复验。两份 Agent 编写的 SSF 均有同 stem
`deep-sky-siril.ssf-provenance.v1` sidecar，且在静态校验、真实 Siril 执行、replay 和 finalize 时持续绑定：

- 唯一 primary protocol reference；
- 实际使用的 supporting references；
- 原始 `command-policy.json` SHA-256；
- `init` 冻结的 Siril 1.4.4 离线手册身份；
- 原子创建的 `--command` 手册查询证据；
- Agent 对适用性和参数选择的理由。

所有知识链、路径、脚本、输入、运行时和输出哈希门均通过。真实像素最终仍未通过 background/color 两门，
因此正式状态是 `review_required`，现场全部保留，且没有生成：

- `outputs/reference.jpg`
- `outputs/final.jpg`

这不是正式交付。候选仅是被拒绝但可审计的显示产物。

## 2. 范围与不可变边界

- 修改范围仅为 `/Users/mz/dev/skills/deep-sky-siril`。
- 未修改 `/Users/mz/dev/starun/deep-sky-siril`。
- 未修改旧验收 session；旧报告只保留此前追加的审计勘误。
- 本轮输入：`/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit`
- 输入大小：`99,538,560` bytes。
- 输入 SHA-256：`982cae9e3c196bd290cc32ec85791e48bc98e61d3947dcd9e5a72da5c7f174f3`。
- 新 session：`/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830`。
- 冻结上下文：`input_state=unknown`、`channel_mode=unknown`、`container_validation=siril`、
  `offline=true`、`keep_intermediates=true`、`stars=preserve`。

文件中三通道、`FILTER=LP`、`.fit` 扩展名和显示外观均没有被解释为 broadband 证据。

## 3. 本轮修复的实现边界

### 3.1 SSF 来源合同

新增 `references/ssf-provenance.schema.json`，并由手写 validator 实现相同的严格合同。`run` 不新增公共参数，
而是自动查找 `scripts/<run-id>.provenance.json`。Python 只验证声明、字节、SHA、路径、手册覆盖、scriptable
标记和 policy 授权；它不生成 SSF、参数、Recipe 或流程。

执行器在运行前一次捕获 SSF、provenance、policy、references、manual evidence 和 session knowledge，执行后再次
复核。`script_unchanged` 与 `knowledge_bindings_unchanged` 分开记录；任一漂移都会使 run 失败，输出不能成为父源。
replay、父源 lineage 和 finalize 同样复核整条知识链。

### 3.2 手册基座

`init` 自动完整验证一次 Bundle，并原子写入
`reports/manual-evidence/bundle-verification.json`。`session.json.knowledge` 冻结：

| 项 | 值 |
|---|---|
| Siril 手册版本 | `1.4.4` |
| commit | `1550a31d325276124fe961368477c90d49df804b` |
| Bundle fingerprint | `ebc095fe19a19787660443677c3b2a43216874a28c0ff56616dd2ad514abc8d2` |
| manifest SHA | `5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d` |
| files SHA | `fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce` |
| tree SHA | `475f37da07acd98e9dbf406cc60ff0f0643d839d363dfa914e5b3717336be9a9` |
| Bundle evidence SHA | `d423c3a3e4b5442f78c7cab981cdc7ec0578e3987596a821f8f8852c9c11dc7a` |
| raw command policy SHA | `e5568d281e183be3e4c879c25cbd30fff8e8a912511a959d069cacdf9b817776` |

`query_siril_manual.py --output ABS_PATH` 现在原子创建 JSON 证据、拒绝覆盖和 symlink，同时 stdout 保持同一 JSON。
普通 search 不可作为最终手册证据；`performed` 必须绑定 `--command` 或 `--read` 结果。

### 3.3 其他问题修复

- JPEG `stat` 按最近一次 `Reading JPG/JPEG` 逐 sample 绑定；8-bit 原生值乘 `257` 得到
  `adu_16_equivalent`，normalized 等于原生值除以 `255`。FITS/XISF 仍使用 16-bit-equivalent。
- 每个统计 sample 记录 `source_format` 和 `native_denominator`；混合日志不会把格式跨 sample 串用。
- `probe` 区分 missing、incompatible、version-unparseable、execution-failed、timeout 和 environment-unavailable；
  临时目录故障不再伪报 Siril 缺失，并保留已发现二进制 fingerprint。
- unknown 路由统一为 `input.inspect -> accepted display-only preview -> delivery.render`；中间禁止科学处理，
  状态上限为 `partial_success`，但最终五门失败仍必须 `review_required`。
- 12 个 protocol reference 都声明四层知识关系：当前图像证据决定协议；protocol 文档提供适用条件和参数化骨架；
  离线手册提供语法/参数语义；policy 单独授权执行。骨架不是固定流水线。
- 删除了“按安装协议安装 StarNet”的残留表述；工具缺失只报告来源与影响。
- provenance Schema 已进入发布白名单与 release closure；法律门仍 fail-closed。

## 4. 真实验收执行过程与命令

以下命令均以 `/Users/mz/dev/skills/deep-sky-siril` 为工作目录。JSON stdout 没有用重定向冒充证据；需要持久化的
手册证据由查询器自身的 `--output` 原子创建。

### 4.1 工具发现

```bash
python3 -B scripts/deep_sky_siril.py probe --offline
```

结果：Siril CLI 为 `/Applications/Siril.app/Contents/MacOS/siril-cli`，版本 `1.4.4`，二进制 SHA-256
`9c2bde40e5747e340b827afadeeb863a882b826da6ab6600a5616c146c5281cb`。StarNet 已发现但本轮不使用；
local Gaia 不存在但不阻塞 unknown 路由；SirilPy 标为实际进入 `background.subtract` 时再检查，本轮未触发。

### 4.2 初始化

```bash
python3 -B scripts/deep_sky_siril.py init \
  /Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit \
  --session /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830 \
  --input-state unknown \
  --channel-mode unknown \
  --target-name "C 50" \
  --target-type unknown \
  --style natural \
  --stars preserve \
  --offline \
  --keep-intermediates \
  --container-validation siril
```

作用：创建新 session，冻结输入、上下文、工具 fingerprint、容器模式、原始 policy SHA 和完整离线手册身份；
不生成处理计划或 SSF。

### 4.3 精确手册查询

```bash
python3 -B scripts/query_siril_manual.py \
  --command autostretch \
  --output /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/reports/manual-evidence/autostretch.command.json

python3 -B scripts/query_siril_manual.py \
  --command savejpg \
  --output /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/reports/manual-evidence/savejpg.command.json
```

`autostretch` 原文确认：`-linked` 使用联动通道，`shadowclip` 以主直方图峰的 sigma 为单位、默认 `-2.8`，
`targetbg` 范围 `[0,1]`、默认 `0.25`，且非 linked 会改变白平衡。`savejpg` 原文确认：用法为
`savejpg filename [quality]`，`100` 为最佳和默认质量，较低值增加压缩。

两份 evidence 的 mode 均为 `command`，不是 search；其文档源均为 `doc/Commands.rst`，源 SHA-256 为
`0b35d170e9f8ab1b21630af94acc867a3eb15173f510fa842c2017937ca94d97`。

### 4.4 `010-input-inspect.ssf`

Agent 依据当前 unknown 图像证据和 `input-inspect.md` 参数化骨架编写：

```ssf
requires 1.4.4 1.5.0
set32bits
load "/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit"
stat main
savejpg "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/previews/010-input-direct" 95
close
load "/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit"
autostretch -linked -2.8 0.22
savejpg "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/previews/010-input-autostretch" 95
close
```

作用：分别生成 direct 和 linked-autostretch 诊断显示；`stat main` 取得原 FITS 统计。autostretch 只用于可读预览，
不创建科学父源。

同名 `010-input-inspect.provenance.json` 的来源绑定：

| 角色 | 路径 | SHA-256 | 实际作用 |
|---|---|---|---|
| primary | `references/protocols/input-inspect.md` | `0088024c413f85d1c55dfb56bb7913080328a853ab2278f943512bec0f97c6d9` | 适用条件、双预览骨架和阶段审查边界 |
| supporting | `references/protocol-index.md` | `0269a9bf8632be0296a56bef97c6af5e2fd570551ac5ee3c04885f93b2c97382` | unknown 路由与禁止插入科学协议 |
| supporting | `references/quality.md` | `05efda0b228164816449ccd09361e4ec0fe473e967ad93feba7e40cd3ceaf3d1` | 诊断 review 与最终五门的区别 |
| policy | `references/command-policy.json` | `e5568d281e183be3e4c879c25cbd30fff8e8a912511a959d069cacdf9b817776` | 每条实际 Siril 命令的协议授权 |
| manual evidence | `reports/manual-evidence/autostretch.command.json` | `0bbec25e00ec3a6299384cf9c4d2cb0c99b6921004ebeca3db4e1420f183c411` | `-linked -2.8 0.22` 语义 |
| manual evidence | `reports/manual-evidence/savejpg.command.json` | `2a036b068991e9355ece64a974870afc0acd1242ac2bfa20bc1ed95910d4bcff` | JPEG 质量参数语义 |

脚本 SHA-256 为 `470decf6c594c84bdc012108c7c0ecbbd56dcecb32d45f8c358f62c487d64d14`；sidecar SHA-256
为 `6cb6c6fd4248eef7dd857383cdce94c99af54f6ccbbfaf260bf5c2fde15eb57f`。

先执行静态校验：

```bash
python3 -B scripts/deep_sky_siril.py run \
  --session /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830 \
  --protocol input.inspect \
  --script /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/scripts/010-input-inspect.ssf \
  --source /Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit \
  --expect /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/previews/010-input-direct.jpg \
  --expect /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/previews/010-input-autostretch.jpg \
  --validate-only
```

结果：`deep-sky-siril.static-validation.v1`、`status=success`、`executed=false`；7 个唯一命令均在冻结手册中、
均为 scriptable 且获 `input.inspect` 授权；`script_unchanged=true`、`knowledge_bindings_unchanged=true`。

随后移除 `--validate-only` 执行同一命令。结果：Siril exit `0`，run `success`，输入、运行时、脚本和知识链全部
unchanged；direct 与 autostretch 均由 Pillow 实际解码为 `2160x3840 RGB JPEG`。

实际全图打开两张图：direct 近黑但星点可见；autostretch 可辨认 C50 环状星云、内部暗区和密集星场，未见
近黑堆栈边或几何破坏，但显示出明显青绿色背景和大尺度不均匀。`reviews/010-input-inspect.json` 因本阶段只做
诊断而 `accept`：structure/stars/geometry 为 pass，background/color 为 not_applicable；其 notes 明确保留
`channel_mode=unknown`。选择 autostretch 仅作为 display-only 父源。

### 4.5 协议取舍

由于 `input_state=unknown`，本轮没有执行：

- `geometry.crop-near-black`：全图未见近黑堆栈边；
- `background.subtract`：unknown 路由禁止科学线性处理，因此未调用 SirilPy；
- `color.calibrate` / `color.map`：unknown 路由禁止，且不得把三通道或 LP 字样当成 broadband；
- `restoration.denoise` / `restoration.deconvolve` / `stretch` / `color.finish`：unknown 路由禁止；
- `stars.separate` / `stars.recompose`：无适用授权需要，未调用 StarNet。

### 4.6 `120-delivery.ssf`

Agent 依据已接受的 display-only 父预览和 `delivery-render.md` 参数化骨架编写：

```ssf
requires 1.4.4 1.5.0
set32bits
load "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/previews/010-input-autostretch.jpg"
stat main
savejpg "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/artifacts/120-final-candidate" 95
close
load "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/artifacts/120-final-candidate.jpg"
stat main
close
```

作用：只把已审查父 JPEG 保存为独立候选，并在同一 Siril 脚本中重新 load/stat；不增加背景、颜色、恢复、拉伸
或分星处理，也不直接写正式 outputs。

同名 `120-delivery.provenance.json` 的来源绑定：

| 角色 | 路径 | SHA-256 | 实际作用 |
|---|---|---|---|
| primary | `references/protocols/delivery-render.md` | `f5e32bd89091979be3467571dce63ad68665f647c7da0929ec4dd4642b5b0e80` | delivery 适用条件、候选重开骨架和 unknown 上限 |
| supporting | `references/protocol-index.md` | `0269a9bf8632be0296a56bef97c6af5e2fd570551ac5ee3c04885f93b2c97382` | unknown 直接路由约束 |
| supporting | `references/delivery.md` | `0bba5d7afb93b0f16014e5af7ee95c1d50f76de4219a231ed4b2d8edf7d94836` | reference/final 语义与失败关闭 |
| supporting | `references/quality.md` | `05efda0b228164816449ccd09361e4ec0fe473e967ad93feba7e40cd3ceaf3d1` | 最终五门要求 |
| policy | `references/command-policy.json` | `e5568d281e183be3e4c879c25cbd30fff8e8a912511a959d069cacdf9b817776` | 实际命令的 delivery.render 授权 |
| manual evidence | `reports/manual-evidence/savejpg.command.json` | `2a036b068991e9355ece64a974870afc0acd1242ac2bfa20bc1ed95910d4bcff` | JPEG 质量参数语义 |

脚本 SHA-256 为 `56cf8762eff569ef2f7661c57297951c0417355a16065ea2f987b02523fed28b`；sidecar SHA-256
为 `23ee23dfd2c8d4fadd97918866ed9067b17e197ec311c343d3e275a55a056cd2`。

先以与 010 相同结构的命令执行 `--validate-only`，再移除该参数实际执行。结果：静态知识链通过；真实 Siril
exit `0`；候选由 Pillow 解码为 `2160x3840 RGB JPEG`；候选 SHA-256 为
`814b8ce8c144c38bd4ff97b01f8c52f9a8b7c7836c8d2cdff0d2f5af2d6ace96`；所有 unchanged 门为 true。

本 run 同时证明 JPEG 统计修复生效。父 JPEG 和候选 JPEG 两个 sample 都记录：

- `source_format=JPEG`
- `native_denominator=255.0`
- 例如候选蓝通道原生 median `107` 被记录为 `adu_16_equivalent=27499`，即 `107*257`；
- 同一 median 的 normalized 为 `0.4196078431`，即 `107/255`；
- 候选红通道原生 median `3` 被记录为 `771` 和 `0.01176470588`。

这不再把 JPEG 的 8-bit Siril 日志误当成原生 16-bit 数据。

### 4.7 最终像素审查

实际同时打开候选和其父预览。五门结果：

| 门 | 结果 | 观察 |
|---|---|---|
| structure | pass | C50 环状结构、内部暗区和外围云气连贯；未见新增振铃或伪结构 |
| background | fail | 全幅明显青绿色底色，并有可见的大尺度亮度不均匀 |
| color | fail | RGB 严重失衡，红通道暗部大面积裁剪 |
| stars | pass | 星点保留；未见重复星、分星残留或几何撕裂 |
| geometry | pass | 父预览和候选均为 2160x3840，方向与构图不变 |

Pillow 只读辅助统计：median RGB `[2,92,107]`，mean RGB 约 `[10.05,94.43,109.36]`；红通道值为 0
的比例约 `35.96%`，小于等于 1 的比例约 `47.95%`。统计只支持观察，不替代全图视觉审查。

`reviews/120-delivery.json` 因 background/color 失败写为 `verdict=reject`；delivery 五门不允许用 limitation
豁免。`final-selection.json` 因而使用 `status=review_required`。

### 4.8 finalize、replay 与幂等恢复

```bash
python3 -B scripts/deep_sky_siril.py finalize \
  --session /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830 \
  --selection /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-provenance-20260830/final-selection.json \
  --keep-intermediates
```

结果：`reports/final-result.json.status=review_required`、`retention_policy=preserve`、
`intermediates_preserved=true`。候选被记录但未复制到正式输出。

随后以原参数分别再次调用两个 `run`。最终加固后的 replay 会重新执行 unknown 路由检查、父 preview 的已接受
review 绑定、完整知识链核对和 Pillow 实际 JPEG 解码；stdout 都包含 `"replayed": true`。两个 Siril 日志在
replay 前后完全一致：

| 日志 | mtime 前后 | size | SHA-256 前后 |
|---|---:|---:|---|
| `logs/010-input-inspect.log` | `1788023224` | `3116` | `8e52d2228493ca718fb169f7967a1342619d13e1756a645ea229fb5aa04b3f6d` |
| `logs/120-delivery.log` | `1788023412` | `3133` | `bba204762e7de4b9c6fc776e9c41dd492cc6084933ad62fe026f5814a3b2e3d9` |

这证明 replay 没有重启 Siril 或重写日志。相同 selection 再次执行 finalize，返回同一 audit SHA、candidate SHA、
selection SHA 和 `review_required` 结果。`outputs/` 在两次 finalize 后仍为空。

## 5. 本次使用的 Skill 文件与作用

### 5.1 运行和知识实现

| 文件 | 本轮作用 |
|---|---|
| `SKILL.md` | 公共路由、四层知识关系、所有 SSF 的 provenance 要求、unknown/channel 边界 |
| `references/protocol-index.md` | 根据当前证据选择协议；约束 unknown 直接路由 |
| `references/protocols/input-inspect.md` | 010 的 primary 协议、参数化骨架和诊断审查要求 |
| `references/protocols/delivery-render.md` | 120 的 primary 协议、候选重开和交付门禁 |
| `references/quality.md` | review 五门及阶段适用性 |
| `references/delivery.md` | reference/final 输出含义和非交付状态 |
| `references/command-policy.json` | 独立于手册知识的执行授权边界 |
| `references/ssf-provenance.schema.json` | 新 sidecar 的公开 Schema |
| `references/review.schema.json` | 两份视觉 review 的严格字段和状态条件 |
| `references/final-selection.schema.json` | 最终 selection 的状态与字段条件 |
| `references/siril-manual/**` | 完整冻结的 Siril 1.4.4 离线原文、索引、许可和完整性材料 |
| `scripts/deep_sky_siril.py` | 仅四个公共命令的 CLI 分派 |
| `scripts/deep_sky_siril_contract.py` | v1 常量、原子 JSON、路径/哈希和 provenance/knowledge validator |
| `scripts/deep_sky_siril_session.py` | init、Bundle 自动验证、session knowledge 冻结和 lineage |
| `scripts/deep_sky_siril_validation.py` | SSF allowlist、source/expect、实际命令与冻结手册/policy 映射 |
| `scripts/deep_sky_siril_core.py` | `run_script`、replay、Siril 启动、receipt、review/finalize 和知识链复核 |
| `scripts/deep_sky_siril_artifacts.py` | JPEG 实际解码和 scientific/display 产物验证 |
| `scripts/deep_sky_siril_tooling.py` | 本地只读工具发现、fingerprint 与细分 probe 错误分类 |
| `scripts/query_siril_manual.py` | 精确 command/read 查询和原子 evidence 输出 |
| `scripts/siril_manual_bundle.py` | 完整 Bundle/组件闭包/哈希验证和命令条目读取 |
| `scripts/package_release.py` | 精确发布白名单、Schema 哈希、许可门和确定性 ZIP |

这些 Python 文件没有生成本轮两份 SSF 或 provenance rationale；它们只执行确定性、安全和证据工作。

### 5.2 新增/扩展的测试

| 测试文件 | 覆盖重点 |
|---|---|
| `tests/test_knowledge_contract.py` | provenance Schema/validator 一致性、primary/路径/SHA/manual evidence、协议闭包和知识漂移 |
| `tests/test_siril_manual_query.py` | `--output` 原子创建、stdout 一致、拒绝覆盖和 symlink |
| `tests/test_runtime_diagnostics.py` | JPEG/FITS 混合统计、255 到 65535 等价值和 probe 六类失败 |
| `tests/test_deep_sky_siril.py` | session knowledge、validate/run/replay/finalize 漂移、unknown route 和真实 Siril strict 小 FITS |
| `tests/test_release_bundle.py` / `tests/test_package_release.py` | 新 Schema 发布闭包、许可门和确定性包 |

## 6. 新 session 全部文件、SHA-256 与作用

以下哈希均在真实执行、两次 replay 和幂等 finalize 后重新计算。

| 相对路径 | SHA-256 | 作用 |
|---|---|---|
| `session.json` | `86d8cf74ffca2bffe9c09efa0ca69272d5547d073190155064ef8e3c33dcac1e` | immutable 输入、unknown 上下文、工具和 knowledge binding |
| `manifest.json` | `ef293eef612d53ca9b132fc8d49403ce9fe8b8091d93df4d3b574024cf2d43e1` | run lineage 与 committed finalization |
| `reports/tool-probe.json` | `72dae87c4f4839d1779c3d295e477046f3168230e760556a598a1497ea0c9abe` | 初始化时冻结的本地工具发现 |
| `reports/manual-evidence/bundle-verification.json` | `d423c3a3e4b5442f78c7cab981cdc7ec0578e3987596a821f8f8852c9c11dc7a` | init 自动完整 Bundle 验证 |
| `reports/manual-evidence/autostretch.command.json` | `0bbec25e00ec3a6299384cf9c4d2cb0c99b6921004ebeca3db4e1420f183c411` | autostretch 精确手册证据 |
| `reports/manual-evidence/savejpg.command.json` | `2a036b068991e9355ece64a974870afc0acd1242ac2bfa20bc1ed95910d4bcff` | savejpg 精确手册证据 |
| `scripts/010-input-inspect.ssf` | `470decf6c594c84bdc012108c7c0ecbbd56dcecb32d45f8c358f62c487d64d14` | 生成 direct/autostretch 诊断预览 |
| `scripts/010-input-inspect.provenance.json` | `6cb6c6fd4248eef7dd857383cdce94c99af54f6ccbbfaf260bf5c2fde15eb57f` | 010 的协议/reference/manual/policy/参数来源 |
| `reports/010-input-inspect-static-validation.json` | `9378b61ae467007649da30a931d1ad32755265cac9d7f06434e536cd40f3145c` | 010 未启动 Siril 的预执行回执 |
| `runtime/siril-configs/010-input-inspect.ini` | `75939ea33ca12f1a6378c4854b4e638974a78c118fcb220f2813de93dd9407c7` | 010 隔离 Siril 配置 |
| `logs/010-input-inspect.log` | `8e52d2228493ca718fb169f7967a1342619d13e1756a645ea229fb5aa04b3f6d` | 010 load/stat/autostretch/Siril stdout-stderr |
| `previews/010-input-direct.jpg` | `39d1b2a411fa1189651fc6f125f8b7884711be9462c45ba1379ce9a3a1aa0282` | direct 输入显示 |
| `previews/010-input-autostretch.jpg` | `aace30bddeb674dbf4528896cefb673298d41be456c26f55440b01f92c8cca6f` | 被接受的 display-only 父预览 |
| `runs/010-input-inspect.json` | `7d146c1b6583998574ac6c86ced389f8c5a15c0d64dc95ce7ee4e0f1482cec20` | 010 run receipt、命令到手册条目映射 |
| `reviews/010-input-inspect.json` | `608b85ce2a4e3b8555ebe1809aafa122d4c2471450363305a0655dd2096772af` | 010 实际视觉审查 |
| `scripts/120-delivery.ssf` | `56cf8762eff569ef2f7661c57297951c0417355a16065ea2f987b02523fed28b` | 从 display-only 父源保存、重开候选 |
| `scripts/120-delivery.provenance.json` | `23ee23dfd2c8d4fadd97918866ed9067b17e197ec311c343d3e275a55a056cd2` | 120 的协议/reference/manual/policy/参数来源 |
| `reports/120-delivery-static-validation.json` | `3a8a9bf61f0bc0a867e0afe454bdf71152ac7146f4fa98e217790639704759dc` | 120 未启动 Siril 的预执行回执 |
| `runtime/siril-configs/120-delivery.ini` | `75939ea33ca12f1a6378c4854b4e638974a78c118fcb220f2813de93dd9407c7` | 120 隔离 Siril 配置 |
| `logs/120-delivery.log` | `bba204762e7de4b9c6fc776e9c41dd492cc6084933ad62fe026f5814a3b2e3d9` | 两个 JPEG load/stat 与 savejpg 日志 |
| `artifacts/120-final-candidate.jpg` | `814b8ce8c144c38bd4ff97b01f8c52f9a8b7c7836c8d2cdff0d2f5af2d6ace96` | 被拒绝但保留的候选 |
| `runs/120-delivery.json` | `0ffb89546be5113ac342e59c6a4b3769fcd13df1b898f43d326b6b958c2d488f` | 120 run receipt、JPEG 统计和知识映射 |
| `reviews/120-delivery.json` | `bf140dab831168446ffb27aedc277e3e7fc5301dd80506c475501a9ccc535c13` | 最终五门 review，background/color fail |
| `final-selection.json` | `63894927c4669a02379d68c508dd54b56d0276ab04f2c185387d174e5dfe943f` | review_required 最终选择与限制 |
| `reports/final-audit.json` | `9634436cdcea3e4609dc9a8d1e18ecf2d90f817ed2c5352e096bddaa92d033e1` | 输入、lineage、review、知识链和保留策略审计 |
| `reports/final-result.json` | `14166b630bb324ffb99dd9ff44bc522ebc74360a10fadddfb7813a1e5a875d10` | standalone v1 最终非交付结果 |

`outputs/` 没有文件，符合失败关闭合同。

## 7. 最终验证

### 7.1 完整测试

```bash
python3 -m pytest -q tests
```

结果：`181 passed, 2 skipped, 26 subtests passed in 22.26s`。相对计划给出的基线
`117 passed, 2 skipped, 24 subtests passed`，增加 `64` 个 passed 和 `2` 个 subtests，skipped 不变；未设测试数量门。

真实 strict 小 FITS 用例也单独通过：测试创建一个非零 `12x8` mono FITS，先做 strict 完整容器解析，再由全新
Siril 1.4.4 离线进程 load/stat，取得有限且通道一致的统计。它提供 scientific strict 门证据；本 C50 JPEG-only
session 的 `container_validation=siril` 不冒充该覆盖。

### 7.2 Bundle、结构与发布候选

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  /Users/mz/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/mz/dev/skills/deep-sky-siril

PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/query_siril_manual.py --verify-bundle

PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/package_release.py --check
```

结果：

- Skill 结构：`Skill is valid!`
- Bundle：`verified`，577 个组件文件、536 个 RST、994 个 sections、199 个 commands。
- release check：`valid_candidate`，617 文件，content hash
  `372df63b9549973326b5d8f2da23495f6bb7849c1101688b1ae543acdc8361a1`。
- `publishable=false`，`legal_review.status=missing`，source gate closed。

### 7.3 两次确定性 ZIP

首次把输出放在 `/tmp` 时，发布器因 macOS `/tmp` 是 symlink 而正确拒绝：
`refusing symbolic-link output path component: /tmp`。随后使用 `mktemp -d` 在真实 `/private/tmp` 目录构建两次：

```bash
release_tmp=$(mktemp -d /private/tmp/deep-sky-siril-determinism.XXXXXX)
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/package_release.py --output "$release_tmp/first.zip"
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/package_release.py --output "$release_tmp/second.zip"
shasum -a 256 "$release_tmp/first.zip" "$release_tmp/second.zip"
cmp -s "$release_tmp/first.zip" "$release_tmp/second.zip"
```

两份 ZIP 均为 `15,291,336` bytes，SHA-256 均为
`24f5237d955d9176a3d46f29d144981555643f2f62730fdd8c56d21a0745b061`，`cmp` exit `0`，字节完全一致。
临时目录随后清理；Skill 目录没有 ZIP 或 release receipt。没有打标签、上传或发布。

父仓库当前把整个 `deep-sky-siril/` 显示为 untracked，因此没有可信的 Git 前态可计算代码行数差；本报告不拿
Starun 旧副本伪造基线。当前选定 Python、测试、references、SKILL/发布文档合计 `18,541` 行，其中 scripts
`10,914` 行、tests `6,091` 行；这些只是当前快照，不是压缩目标或质量门。

## 8. 最终判定

本轮证明修复后的 Skill 已把“SSF 从哪里来”变成可审计、可重放、失败关闭的机器合同，同时仍保持 Agent 负责
协议选择、参数推理和视觉判断，Python 不越界成为 Recipe/SSF 生成器。

C50 候选本身没有达到正式交付质量。除非用户提供可靠来源确认并在全新 session 中允许相应科学处理，当前
`review_required` 结果不得被升级或复制为 `outputs/final.jpg`。
