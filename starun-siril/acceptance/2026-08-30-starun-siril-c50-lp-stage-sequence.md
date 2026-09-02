# Starun-siril C 50 LP 重命名与 Stage 1–10 真实验收

日期：2026-08-30  
Skill：`/Users/mz/dev/skills/starun-siril`  
最终 session：`/Users/mz/SeeStar/Starun/starun-siril-acceptance-C50-LP-20260830-v2`  
结论：`success`，正式含星 JPEG 已生成；中间现场按验收要求保留。

## 1. 重命名范围与身份

- 目录从 `/Users/mz/dev/skills/deep-sky-siril` 改为 `/Users/mz/dev/skills/starun-siril`。
- 技能的用户可见名为 `Starun-siril`；技术 slug、Schema 前缀、release identity 为 `starun-siril`。
- `SKILL.md`、`agents/openai.yaml`、公共 runtime Schema、测试和发布器已同步。
- 内部 Python 模块文件名 `deep_sky_siril*.py` 保留；它们不是公共技能身份，避免为改名做无价值模块重构。
- 冻结手册组件内部的 `deep-sky-siril.siril-manual-*` Schema 保留，因为它们属于已内容寻址的离线 Bundle；改写会破坏手册完整性哈希。runtime/query 的公共输出仍为 `starun-siril.*`。
- 未修改 `/Users/mz/dev/starun/deep-sky-siril` 的旧副本，也未发布、上传或打标签。

## 2. 输入与冻结上下文

输入：`/Users/mz/SeeStar/Starun/SeestarS30Pro-C50-30s346_LP.fit`

- SHA-256：`982cae9e3c196bd290cc32ec85791e48bc98e61d3947dcd9e5a72da5c7f174f3`
- 大小：`99,538,560` bytes
- FITS：32-bit float、2160×3840、3 通道
- 文件内证据：`PROGRAM=Siril 1.4.0`、`STACKCNT=346`，HISTORY 记录 mean stacking、winsorized sigma clipping 与 normalized output。
- session 冻结：`input_state=linear`、`channel_mode=unknown`、`target_name=C 50`、`style=natural`、`stars=adaptive`、`container_validation=strict`、`keep_intermediates=true`。
- `FILTER=LP`、三通道与视觉颜色不足以证明 broadband/dualband，因此没有擅自做 PCC/SPCC 或窄带 palette。

`probe` 冻结的外部执行环境：

| 工具 | 路径/版本 | 作用 |
|---|---|---|
| Siril CLI | `/Applications/Siril.app/Contents/MacOS/siril-cli`，1.4.4，SHA `9c2bde40…` | 执行全部 SSF、保存科学 FITS/JPEG、重新打开并统计产物 |
| StarNet2 | `/usr/local/bin/starnet2`，SHA `b9307b1c…` | Stage 6 星点分离候选 |
| StarNet2 model | `/usr/local/lib/starnet2/StarNet2_weights.mlpackage`，SHA `611e1dcc…` | StarNet2 模型，未随 Skill 分发 |
| SirilPy | 实际 Stage 3 校验 `==1.0.25` | 向运行中的 Siril 注入哈希绑定背景样点 |
| Pillow | compatible | JPEG 实际解码验证，不处理科学像素 |

## 3. 知识基座与 SSF 来源

每份 SSF 都有同 stem `*.provenance.json`。知识层级为：当前真实像素决定是否适用；对应 protocol reference 提供参数化骨架；冻结 Siril 1.4.4 手册提供命令语法与参数语义；`command-policy.json` 独立授权命令。

本次所有 SSF 都完整实例化 protocol 已展开的骨架，没有新增命令、未展开变体或参数语义疑问，因此 provenance 的 `manual_lookup.status` 均为 `not_needed`，并写明理由。运行器仍自动把每个实际命令映射到冻结手册条目，逐项确认 `scriptable=true` 与 protocol policy 授权；这不是绕过手册，而是“骨架已由 references/手册建立，运行时全命令闭包复核”。

Bundle 证据：

- 版本 1.4.4，commit `1550a31d325276124fe961368477c90d49df804b`
- fingerprint `ebc095fe19a19787660443677c3b2a43216874a28c0ff56616dd2ad514abc8d2`
- manifest SHA `5208e09b9779ec1945bfba96d3345a74ae3b50cac57a5b914cc0edae516e356d`
- files SHA `fc50a4eabf1579e931a16dac5adbd860096505d4f04e8f36e5b3083abbd39bce`
- tree SHA `475f37da07acd98e9dbf406cc60ff0f0643d839d363dfa914e5b3717336be9a9`
- session 证据：`reports/manual-evidence/bundle-verification.json`

## 4. 首次 session 暴露并修复的问题

首次现场：`/Users/mz/SeeStar/Starun/starun-siril-acceptance-C50-LP-20260830`。

原 `stars.separate` 骨架只生成 `060-starless.jpg`，但审查协议同时要求检查 starless 与 star layer，无法形成真实视觉闭环。修复为在同一 SSF 内也对星层执行 linked autostretch，并输出 `previews/060-star-layer.jpg`；预期产物说明同步改为“两份对应预览”。

修复后尝试在原 session 重跑时，runtime 返回：

```text
manifest_invalid: Run receipt is invalid: 060-stars-separate.json
```

这是正确的 fail-closed：原 run 绑定修改前的 protocol SHA，修改 reference 后知识链发生漂移。没有改旧 receipt 或绕过门禁，而是保留现场并创建 v2 session，从 Stage 1 全量重跑。补丁先通过 `48 passed` 的知识/合同定向测试和 skill-creator `Skill is valid!`。

## 5. Stage 1–10 实际顺序

| Stage | 实际动作 | 结论与父源 |
|---|---|---|
| 1 输入准备 | `init` 冻结输入/工具/手册；`010-input-inspect` 生成 direct 与 linked autostretch | accept；原始 FITS 继续作为科学父源 |
| 2 边界校正 | 查看 Stage 1 预览；没有近黑堆栈边、旋转覆盖或无效边界 | 明确跳过，不凭空裁切 |
| 3 背景处理 | 32 个视觉选择样点，SirilPy 注入；`subsky 1 -existing` | accept；`030-background.fit` |
| 4 图像解析/色彩校准 | `channel_mode=unknown`，且没有可用于真实性声明的通道语义 | 明确跳过 PCC/SPCC/neutral run |
| 5 线性反卷积 | 500 个分布星点、31×31 PSF、6 次 TV RL | accept；`050-deconvolve.fit` |
| 5 线性降噪 | `denoise -mod=0.35` 单一保守候选 | accept；`055-denoise.fit` |
| 6 去星/星层 | StarNet2 stride 256；同时输出 starless 与 star-layer 预览 | execution success，但 review reject：星层含大面积 C 50 星云结构；不得成为父源 |
| 7 主体拉伸 | 回到已接受的含星 Stage 5 父源；linked `-4.50 0.120` | accept；`070-stretch.fit` |
| 8 Starless 增强 | 没有可信 starless lineage，且 channel mode 未解析 | 明确跳过 |
| 9 星点合成 | Stage 6 配对被拒绝 | 明确跳过，保留可信含星基线 |
| 10 最终调色 | 单一 `satu 0.10 1 6` 候选 | accept；`100-color-finish.fit` |
| 10 导出 | 独立 `delivery.render`，JPEG 95，重新 load/stat | 五门 accept；正式提交 |

## 6. 实际 SSF、provenance 与产物

所有路径以下均相对于 v2 session。

| Run | SSF SHA | Primary reference | 实际 Siril 命令/作用 | 主要产物 | Review |
|---|---|---|---|---|---|
| `010-input-inspect` | `3046bb7a…` | `references/protocols/input-inspect.md` | `load/stat/savejpg/autostretch`；建立不改变科学父源的双预览 | direct/autostretch JPEG | accept |
| `030-background` | `37b287a6…` | `references/protocols/background-subtract.md` | `pyscript/subsky 1 -existing`；注入并拟合背景样点 | 背景 FITS、JPEG、注入 receipt | accept |
| `050-deconvolve` | `6f67090e…` | `references/protocols/restoration-deconvolve.md` | `setfindstar/findstar/makepsf/rl`；构造 PSF 并保守反卷积 | stars.tsv、PSF、FITS、JPEG | accept |
| `055-denoise` | `abddc833…` | `references/protocols/restoration-denoise.md` | `denoise -mod=0.35`；降低后续拉伸会放大的线性噪声 | FITS、JPEG | accept |
| `060-stars-separate` | `1a0c6597…` | `references/protocols/stars-separate.md` | `starnet/pm`；保存 full/starless/star-layer 并分别预览 | 三份 FITS、两份 JPEG | **reject** |
| `070-stretch` | `e03e4b2c…` | `references/protocols/stretch.md` | linked `autostretch`；含星线性图转非线性 | FITS、JPEG | accept |
| `100-color-finish` | `4fa4ec9f…` | `references/protocols/color-finish.md` | `satu 0.10`；轻量显示调色，不声明光度真实性 | FITS、JPEG | accept |
| `110-delivery` | `c2a42118…` | `references/protocols/delivery-render.md` | `savejpg` 后重新 `load/stat`；验证实际 JPEG | final candidate JPEG | 五门 accept |

每个 `runs/*.json` 还记录：完整 Siril invocation、工具 fingerprint、脚本/provenance/reference/policy SHA、执行前后不变性、命令→手册映射、统计样本、strict FITS 解析与全新 Siril 重开结果。

关键辅助文件及作用：

| 文件/脚本 | 作用 |
|---|---|
| `scripts/deep_sky_siril.py` | 四命令公共 CLI：probe/init/run/finalize |
| `scripts/deep_sky_siril_core.py` | Siril 执行、receipt、finalize 与幂等提交 |
| `scripts/deep_sky_siril_validation.py` | SSF、provenance、lineage、路径、policy 与背景合同验证 |
| `scripts/deep_sky_siril_artifacts.py` | FITS/JPEG 产物验证、Siril 重开统计与 strict 容器解析 |
| `scripts/deep_sky_siril_session.py` | standalone v1 session/manifest/knowledge 冻结 |
| `scripts/deep_sky_siril_tooling.py` | 只探测本地 Siril、StarNet、模型、Pillow、Gaia，不安装工具 |
| `scripts/siril_background_samples.py` | 在实际 SirilPy 1.0.25 中注入并回读背景样点 |
| `scripts/query_siril_manual.py` | 验证/查询完整离线 Siril 1.4.4 手册；本次用于 Bundle 总体验证 |
| `reports/030-background/background-sample-contract.json` | 绑定输入路径/SHA/2160×3840 几何与 32 个样点 |
| `reports/030-background/sample-injection-receipt.json` | 证明 32 个请求坐标与 32 个 Siril 实际保留坐标完全一致 |
| `reviews/*.json` | 绑定 run receipt 和实际检查图像 SHA 的逐阶段视觉结论 |
| `final-selection.json` | 只选择接受的 010/030/050/055/070/100/110；排除 060 |
| `reports/final-audit.json` | 最终 lineage、review、知识链、星点与候选审计 |
| `reports/final-result.json` | 正式 standalone v1 结果 |

## 7. 视觉结论与正式交付

最终五门：structure/background/color/stars/geometry 均 pass。

- `outputs/reference.jpg`：SHA `aace30bddeb674dbf4528896cefb673298d41be456c26f55440b01f92c8cca6f`
- `outputs/final.jpg`：SHA `cea4cdb8305a3d72ef6906faafd697b06e7740ac9e3d0e6ffd814ee8f291f3ef`，2,298,961 bytes，2160×3840 RGB JPEG
- `final-selection.json`：SHA `0b09fd99ae30896e38c00b45c7fe4c8228b1dcd7d292cc1bf6a78da15222b4bb`
- `reports/final-audit.json`：SHA `9f7b2f73c01316f7d2ad0c47ee44c0b2268ec20d4971bd2f146df31d77ad5004`
- `reports/final-result.json`：SHA `26468c2a7fb0eb53a37940bddf3bbc005d2d470c9a527d4580bb8e1936f04ce9`

两次相同 finalize 返回完全相同的 selection、audit、reference 与 final SHA；没有重启处理流水线或改写正式像素，幂等 replay 通过。

透明限制：

1. `channel_mode_unresolved`：不宣称 PCC/SPCC 或窄带 palette 真实性。
2. `starnet_pair_rejected_preserve_stars_baseline`：Stage 6 星层泄漏目标结构，最终采用可信含星父源，不使用该配对。

## 8. 仓库与发布验收

- 全量测试：`178 passed, 2 skipped, 26 subtests passed`；相对计划基线 `117 passed, 2 skipped, 24 subtests passed` 为 `+61 passed`、`+2 subtests`，不设数量硬门。
- skill-creator quick validate：`Skill is valid!`
- repository validate：`validated: starun-siril`
- Bundle：577 component files、536 RST、994 sections、199 commands、24 selected images，`verified`
- `package_release.py --check`：`valid_candidate`，精确白名单闭包通过
- 两次确定性 ZIP：均为 15,290,720 bytes，SHA 均为 `d97f60dc660fbcd3adcc254f9752cbded7360457d6ee1061509b6f957330294a`
- 法律门保持关闭：`publishable=false`、`legal_review.status=missing`；没有上传、发布或打标签。

