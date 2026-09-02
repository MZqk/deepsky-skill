# C50 LP standalone v1 继续运行验收（2026-08-30）

## 1. 结论

本轮使用当前 `/Users/mz/dev/skills/deep-sky-siril`，对以下输入创建了全新的 standalone v1 session，
完整执行 `probe → init → input.inspect → review → delivery.render → final review → finalize → replay`：

- 输入：`/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit`
- 输入大小：`99,538,560` bytes
- 输入 SHA-256：`982cae9e3c196bd290cc32ec85791e48bc98e61d3947dcd9e5a72da5c7f174f3`
- session：`/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830`

最终状态为 `review_required`，不是正式交付。结构、星点和几何门通过；背景与颜色门失败。候选保留在
`artifacts/120-final-candidate.jpg`，但 `outputs/` 为空，没有 `outputs/reference.jpg` 或
`outputs/final.jpg`。

## 2. 冻结边界与工具

`init` 冻结：

- `input_state=unknown`
- `channel_mode=unknown`
- `container_validation=siril`
- `offline=false`
- `keep_intermediates=true`
- `stars=preserve`
- `style=balanced`

`target_name=C50` 和 `target_type=nebula` 只作为描述性标签，不用于推断线性状态或通道来源。文件名中的
`LP`、三通道、扩展名和显示色调均未被当成 broadband、narrowband、dualband 或 linear 的证据。

工具探测结果：

| 工具 | 结果 | 作用 |
|---|---|---|
| Siril CLI | 1.4.4，SHA `9c2bde40e5747e340b827afadeeb863a882b826da6ab6600a5616c146c5281cb` | 执行 SSF、读取 FITS/JPEG、输出 stat 日志 |
| Pillow | compatible | 对每个 JPEG 完整解码像素，不以容器标记替代验证 |
| StarNet2 | compatible | 本轮协议不适用，未执行 |
| SirilPy bridge | `runtime_check_required` | 只在 `background.subtract` 执行时验证；本轮未调用 |
| local Gaia | absent | unknown 路由不需要，未造成阻塞 |

Bundle 在 `init` 中自动完整验证并冻结：Siril 手册版本 1.4.4、commit
`1550a31d325276124fe961368477c90d49df804b`、Bundle fingerprint
`ebc095fe19a19787660443677c3b2a43216874a28c0ff56616dd2ad514abc8d2`；command policy 原始 SHA 为
`e5568d281e183be3e4c879c25cbd30fff8e8a912511a959d069cacdf9b817776`。

## 3. 使用的 Skill 文件与脚本

### 3.1 Agent 知识文件

| 文件 | 本轮作用 |
|---|---|
| `SKILL.md` | standalone v1 总合同、Agent 循环和 unknown 路由 |
| `references/cli-contract.md` | 四个公共命令、运行和 finalize 机器边界 |
| `references/session-contract.md` | immutable session、允许 Agent 写入的文件及恢复规则 |
| `references/protocol-index.md` | 根据输入证据选择协议；unknown 只允许 inspect→delivery |
| `references/protocols/input-inspect.md` | `010` 的 primary reference 和双预览参数化骨架 |
| `references/protocols/delivery-render.md` | `120` 的 primary reference 和候选重开骨架 |
| `references/manual-query.md` | 精确手册查询及原子 evidence 文件规则 |
| `references/quality.md` | input review 和最终五门视觉审查 |
| `references/delivery.md` | `review_required` 不生成正式输出的交付规则 |
| `references/command-policy.json` | 每条实际 Siril 命令的协议级执行授权 |
| `references/ssf-provenance.schema.json` | 两份 SSF provenance sidecar 的机器合同 |
| `references/review.schema.json` | 两份视觉 review receipt 的机器合同 |
| `references/final-selection.schema.json` | 最终 selection 的机器合同 |

### 3.2 实际运行脚本与模块

| 脚本或模块 | 本轮作用 |
|---|---|
| `scripts/deep_sky_siril.py` | `probe/init/run/finalize` 的薄 CLI 分派 |
| `scripts/deep_sky_siril_tooling.py` | 发现并冻结 Siril、StarNet、Pillow、Gaia 等本地能力 |
| `scripts/deep_sky_siril_session.py` | 创建 session、冻结输入、工具和知识 Bundle |
| `scripts/deep_sky_siril_validation.py` | 验证 SSF、provenance、手册证据、policy、路由和声明写入 |
| `scripts/deep_sky_siril_core.py` | 执行 Siril、写 receipt、replay、review/finalize 知识链核对 |
| `scripts/deep_sky_siril_artifacts.py` | 解析 Siril 统计并对 JPEG 做实际解码验证 |
| `scripts/deep_sky_siril_contract.py` | 路径、哈希、JSON 和原子写入基础合同 |
| `scripts/query_siril_manual.py` | 查询 `autostretch`、`savejpg` 并原子创建 evidence |
| `scripts/siril_manual_bundle.py` | 验证和读取冻结的 1.4.4 离线手册索引 |
| `/Applications/Siril.app/Contents/MacOS/siril-cli` | 真正读取像素并执行两份 Agent SSF |

`siril_background_samples.py`、StarNet、SirilPy、安装器、下载器和发布器均未参与本轮图像处理。

## 4. 完整命令过程

### 4.1 输入和工具

```bash
stat -f '%N|%z' /Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit
shasum -a 256 /Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit

python3 -B /Users/mz/dev/skills/deep-sky-siril/scripts/deep_sky_siril.py probe
```

### 4.2 创建 session

```bash
python3 -B /Users/mz/dev/skills/deep-sky-siril/scripts/deep_sky_siril.py init \
  /Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit \
  --session /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830 \
  --input-state unknown --channel-mode unknown \
  --target-name C50 --target-type nebula \
  --style balanced --stars preserve --keep-intermediates \
  --container-validation siril
```

### 4.3 精确离线手册证据

```bash
python3 -B /Users/mz/dev/skills/deep-sky-siril/scripts/query_siril_manual.py \
  --command autostretch \
  --output /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/reports/manual-evidence/010-autostretch.command.json

python3 -B /Users/mz/dev/skills/deep-sky-siril/scripts/query_siril_manual.py \
  --command savejpg \
  --output /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/reports/manual-evidence/010-savejpg.command.json
```

查询确认：`autostretch [-linked] [shadowsclip [targetbg]]`；`-2.8` 是阴影裁剪 sigma，`0.22` 是目标背景；
`savejpg filename [quality]` 中 `95` 是合法质量值。两条命令均是 scriptable，且获当前协议授权。

### 4.4 `010-input-inspect`

Agent 编写：

```ssf
requires 1.4.4 1.5.0
set32bits
load "/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit"
stat main
savejpg "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/previews/010-input-direct" 95
close
load "/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit"
autostretch -linked -2.8 0.22
savejpg "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/previews/010-input-autostretch" 95
close
```

作用：从 immutable `@input` 生成 direct 与 linked-autostretch 两个诊断预览，不生成科学 master。SSF SHA 为
`65845d02fe64afae1490b4f46f604b265a385369db5f34e5cd88aafd28bba8c9`；同 stem provenance SHA 为
`a36178ac5c0440ec0ee7af5c81d99dd5829b030c288189628a6bef5d8912029e`。provenance 的 primary reference 是
`input-inspect.md`，supporting references 是 `protocol-index.md`、`quality.md`，并绑定两份 command evidence。

先执行 `run ... --validate-only`，再用同一参数移除 `--validate-only` 真实执行。静态门和真实 run 均成功；
Siril exit 0；脚本、输入、运行时和知识链均 unchanged；两个 JPEG 都由 Pillow 完整解码为 `2160x3840 RGB`。

实际打开两张全图：direct 近黑，只能看到少量星点；autostretch 能可靠辨认中央偏下的环状星云和密集星场，
未见断裂、重复星、几何撕裂或近黑堆栈边。它有明显青绿色背景和大尺度不均匀，但 input.inspect 的
background/color 只用于诊断，记为 `not_applicable`。`010` review 为 `accept`，选择 autostretch 作为唯一
display-only 父源。

### 4.5 `120-delivery`

Agent 编写：

```ssf
requires 1.4.4 1.5.0
set32bits
load "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/previews/010-input-autostretch.jpg"
stat main
savejpg "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/artifacts/120-final-candidate" 95
close
load "/Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/artifacts/120-final-candidate.jpg"
stat main
close
```

作用：只把已接受的 display-only 父预览保存为独立候选并重新 load/stat；不插入背景、颜色、恢复、拉伸或
分星处理。SSF SHA 为 `30049836733103570a82a9e9e87d32e24345c0ce2b052f060dab09559f4b70d1`；同 stem
provenance SHA 为 `fae1d725f99d6c399abdf430aaa134e56b0165ffc8ad515b94c5a16d3b6c3f5e`。primary reference 是
`delivery-render.md`，supporting references 是 `protocol-index.md`、`delivery.md`、`quality.md`，并复用本
session 已创建的精确 `savejpg` evidence。

静态验证确认 accepted input review 实际检查了被选择的 autostretch 父源。真实 run exit 0，候选被 Pillow
完整解码为 `2160x3840 RGB JPEG`，SHA 为
`814b8ce8c144c38bd4ff97b01f8c52f9a8b7c7836c8d2cdff0d2f5af2d6ace96`。

## 5. 最终像素审查

实际并排打开父预览和候选：

| 门 | 结果 | 观察 |
|---|---|---|
| structure | pass | 环状主体、内部暗区和外围云气连续；未见新振铃或伪结构 |
| background | fail | 全幅强青绿色底色，大尺度亮度和色度不均匀仍明显 |
| color | fail | RGB 严重失衡；候选 median 为 R=3、G=92、B=107（8-bit 原生值） |
| stars | pass | 星点保留，无明显重复星、分星残留或几何破坏 |
| geometry | pass | 父源与候选均为 2160x3840，方向和构图不变 |

`reviews/120-delivery.json` 因 background/color 失败使用 `verdict=reject`。unknown/display-only 和 limitations
不豁免最终五门，因此 selection 使用 `review_required`，不是 `partial_success`。

## 6. Finalize、replay 与正式输出

```bash
python3 -B /Users/mz/dev/skills/deep-sky-siril/scripts/deep_sky_siril.py finalize \
  --session /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830 \
  --selection /Users/mz/SeeStar/Starun/deep-sky-siril-acceptance-LP-v1-continuation-20260830/final-selection.json \
  --keep-intermediates
```

结果：`status=review_required`、`retention_policy=preserve`、`intermediates_preserved=true`。candidate 被记录，
但没有复制为正式 output。

随后用原参数分别重放两个 `run`，均返回：

- `replayed=true`
- `script_unchanged=true`
- `knowledge_bindings_unchanged=true`
- `source_unchanged=true`

日志重放前后保持：

| 日志 | mtime | size | SHA-256 |
|---|---:|---:|---|
| `logs/010-input-inspect.log` | `1788056466` | 3095 | `486baadc3bc362acdeb9cd4a8cd131dfaa34fb04b1f64171a9dfca21f196a1ed` |
| `logs/120-delivery.log` | `1788056543` | 3110 | `6e611328ca645abaff22557ffec507028a207e09c313f53d062467a926f9d661` |

再次 finalize 返回相同 selection、audit、candidate 和 `review_required` 结果。`outputs/` 始终没有文件。

## 7. Session 文件清单

| 文件 | SHA-256 | 作用 |
|---|---|---|
| `session.json` | `1302afb415595a60dfe7226dc636e53b64f3b5c495029e803870fe16426d7c02` | immutable 输入、上下文、工具和知识冻结 |
| `manifest.json` | `2d64e5464a32d82d8fe43f1c3c21f1fbe06c7c1dcc2d85be99ece384f27a5847` | session/finalization manifest |
| `reports/tool-probe.json` | `3f2dfbb2611e59e515a4b89a9e91d002b37425a7a4b1055f4d4c1a2fbc39ed90` | init 时的工具探测 |
| `reports/manual-evidence/bundle-verification.json` | `d423c3a3e4b5442f78c7cab981cdc7ec0578e3987596a821f8f8852c9c11dc7a` | Bundle 闭包验证 |
| `reports/manual-evidence/010-autostretch.command.json` | `0bbec25e00ec3a6299384cf9c4d2cb0c99b6921004ebeca3db4e1420f183c411` | autostretch 精确手册证据 |
| `reports/manual-evidence/010-savejpg.command.json` | `2a036b068991e9355ece64a974870afc0acd1242ac2bfa20bc1ed95910d4bcff` | savejpg 精确手册证据 |
| `scripts/010-input-inspect.ssf` | `65845d02fe64afae1490b4f46f604b265a385369db5f34e5cd88aafd28bba8c9` | direct/autostretch 诊断预览 |
| `scripts/010-input-inspect.provenance.json` | `a36178ac5c0440ec0ee7af5c81d99dd5829b030c288189628a6bef5d8912029e` | `010` 知识来源和参数理由 |
| `reports/010-input-inspect-static-validation.json` | `26797e5f5ff0778ab2f63199386ad80a481b407aaf98aad592ef136df9852be9` | `010` 执行前静态回执 |
| `runtime/siril-configs/010-input-inspect.ini` | `acab82d04d6c889dc9bcd450aed4010c58b218ed901f271ac82ebdbc0503a695` | `010` 隔离 Siril 配置 |
| `logs/010-input-inspect.log` | `486baadc3bc362acdeb9cd4a8cd131dfaa34fb04b1f64171a9dfca21f196a1ed` | FITS load/stat/autostretch 日志 |
| `previews/010-input-direct.jpg` | `39d1b2a411fa1189651fc6f125f8b7884711be9462c45ba1379ce9a3a1aa0282` | direct 诊断显示 |
| `previews/010-input-autostretch.jpg` | `aace30bddeb674dbf4528896cefb673298d41be456c26f55440b01f92c8cca6f` | accepted display-only 父源 |
| `runs/010-input-inspect.json` | `5be6fcbfab2cfa1f097177e98753a7c7e78b375e17fc258c19a6097590d414a8` | `010` run receipt 和命令知识映射 |
| `reviews/010-input-inspect.json` | `222cf45df7474258fc7b0585135df0416995815b4fffd4c3e15c10454a76d960` | 两张输入预览的视觉审查 |
| `scripts/120-delivery.ssf` | `30049836733103570a82a9e9e87d32e24345c0ce2b052f060dab09559f4b70d1` | 候选保存和重开 |
| `scripts/120-delivery.provenance.json` | `fae1d725f99d6c399abdf430aaa134e56b0165ffc8ad515b94c5a16d3b6c3f5e` | `120` 知识来源和参数理由 |
| `reports/120-delivery-static-validation.json` | `c13cfc2681d732e99c7bbd9ce9e5f4e90cde330ecfa60625be7c088e224d583e` | `120` 执行前静态回执 |
| `runtime/siril-configs/120-delivery.ini` | `acab82d04d6c889dc9bcd450aed4010c58b218ed901f271ac82ebdbc0503a695` | `120` 隔离 Siril 配置 |
| `logs/120-delivery.log` | `6e611328ca645abaff22557ffec507028a207e09c313f53d062467a926f9d661` | JPEG load/stat/save/reopen 日志 |
| `artifacts/120-final-candidate.jpg` | `814b8ce8c144c38bd4ff97b01f8c52f9a8b7c7836c8d2cdff0d2f5af2d6ace96` | 被拒绝但保留的候选 |
| `runs/120-delivery.json` | `ad06b12b56333be5d63a2a1c74e9037fe0c09c37e27dd2df8e4455379bd2845c` | `120` run receipt、JPEG 统计和知识映射 |
| `reviews/120-delivery.json` | `974fa29672b706fb31e159ad56d5c24f3c8e8a26ec0a019243d4bcceb82ee9d0` | 最终五门审查，background/color fail |
| `final-selection.json` | `2685981fe73d18941ee5db926d346af9e546f7c460937df343599c4f5362cee1` | review_required selection |
| `reports/final-audit.json` | `ecd2b8f5d295f06efa58afa33325e7e23f053751848fda0f33c2fd3b8ca2289f` | 最终知识、lineage、review 审计 |
| `reports/final-result.json` | `a7a66fa5d28c1e3ed55b3d6a790280daae8c572a7b276e2712f537dd3031f89d` | standalone v1 最终结果 |

## 8. 实现健康检查与验收边界

```bash
python3 -m pytest -q tests
PYTHONDONTWRITEBYTECODE=1 python3 -B \
  /Users/mz/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  /Users/mz/dev/skills/deep-sky-siril
```

结果：`181 passed, 2 skipped, 26 subtests passed in 29.48s`；Skill 结构为 `Skill is valid!`。

这些测试和结构结果不替代真实像素审查。本轮真实候选没有通过背景与颜色门，因此没有正式输出。若要进行
背景扣除、颜色校准、降噪、反卷积或正式拉伸，必须由用户或可靠采集来源先确认输入线性状态及通道角色，
并在新的 session 中运行；不能修改本 session 把 unknown 结果升级。
