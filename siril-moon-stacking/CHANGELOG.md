# Changelog

本文件记录 `siril-moon-stacking` 的独立版本变更。

## [1.1.0] - 2026-10-02

- Add fixed tracked lunar video crops and conservative peak disk budgets; reduce selected candidates when space is insufficient without rescaling source pixels.
- Cache full-video multi-region, exposure/noise-aware scores and presentation timestamps; reuse generated FITS and registration with source and reference identity checks.
- Select video quality once by default; add optional measured candidate doubling with full Siril rejection stacks, a fixed comparison region and sky mask, and best-round restoration.
- Preserve relative transforms while avoiding Siril 1.4.4 zero-sum rejection; reconcile selected, exported and actually stacked frame counts.
- Bound normalized MTF parameters, retain mono sharp FITS and QC, measure channel-wise sky noise, and handle normalized-float saturation.
- Require trusted physical optics for Airy deconvolution, offer green luminance, restrain clipped-source mineral colors and pad square exports to preserve lunar cusps.
- Add a runnable regression covering both video containers, pixel fidelity, budget failure, cache invalidation, incremental registration, native Siril transforms and two-round feedback.

## [1.0.19] - 2026-10-02

- Use adaptive two-pass sequential video import for MP4 and AVI: score every frame by default, reuse quality selection, and write only selected FITS frames. Preserve explicit top-K and full-import modes; track ROI drift and verify source-frame fidelity with both containers.

## [1.0.18] - 2026-10-01

- **移除 `scripts/mp4_to_ser.py`（Remove the bundled video→SER converter）**：
  - **背景**：该文件在 v1.0.13 被写入 CHANGELOG 并被 SKILL.md 三处引用，但**从未 `git add`**（同目录另两个脚本均已跟踪，且它不在 `.gitignore` 中）。后果是任何人 clone 仓库后，SKILL.md 会让其运行一个不存在的文件——文档与仓库实际内容不一致。
  - **决策依据（已实测）**：它唯一不可替代的能力是**产出 SER 交给外部工具**（`moon_stack.py import` 只产出 FITS，技能内没有其它 SER 输出路径）。但对本技能自身的流水线，SKILL.md 早已写明「mp4/avi 直解路径功能更全，转 SER 属负收益」——直解含 sidecar `DATE-OBS` 注入、Bayer 图案探测与设备识别，且 `import` 的 SER 分支不具备 `--sample-mode` 内容驱动抽帧能力（`--limit` 退化为顺序截断）。该能力被判定为超出本技能职责范围。
  - **同时纠正一处站不住的文档理由**：原文把「Siril GUI 互通」列为转 SER 的动机之一。实测 Siril 1.4.4 CLI **连它自己写出的 SER 都读不了**（`load f` 对 Siril 自产的 `f.ser` 报 `file not found or not supported`），属 CLI 读取限制而非文件缺陷。真正需要 SER 的是 AutoStakkert / PIPP / SER Player 等外部工具。
  - **附带消除一处维护隐患**：该文件内的 `probe_bayer_pattern` 与暖月 R/B 镜像消歧逻辑（原 168-215 行）与 `unpack_video_to_fits` 中的同类实现是**两份独立拷贝**，会各自漂移；移除后不再有此重复。
  - **文档同步**：删除 SKILL.md 中「方式 D」整段用法示例、`mp4_to_ser.py --limit` 说明、「方式 D 位深说明」段（`--depth`/`--color` 均为该工具专有参数，`moon_stack.py` 无此选项），以及故障排查第 11 条的「SER 出口」小节。§5 第 11 条改写为保留经实测确认的事实——**Siril 自身不能做视频→SER 转换**（`convert` 只扫描静态图像，`load` 读不了 SER），需要时请用 ffmpeg 或 PIPP 自行转换。
  - **CHANGELOG 历史不改写**：v1.0.13 / v1.0.15 中关于该文件的条目保留为历史记录，本条即为移除说明。
  - **未移除的能力**：`import` 对**外部工具产出的** SER 的导入支持完全保留（`--format ser`、`unpack_ser_to_fits`、SER 头解析、Bayer ColorID 处理均不受影响）。
- **移除后校验**：`validate_repository.py` → `validated`；全套 76 项测试（技能 57 + 仓库 19）保持全绿；自跑脚本退出码 0。移除前已确认该文件**未被任何代码 import、无任何测试覆盖**（CHANGELOG v1.0.13 所称的「已实测四条分支」为手工验证，非回归测试）。

## [1.0.17] - 2026-10-01

- **新增智能望远镜设备规格表（Built-in Smart-Telescope Specification Table）**：
  - **动机**：智能望远镜的视频/SER 直出往往不写 `FOCALLEN`/`APERTURE`，导致 `--drizzle auto` 无判据可用、postprocess 光学推断退回 3.73µm / 80mm / 400mm 这类可能差数倍的硬编码默认值。
  - **新增 `scripts/device_specs.py`**：**19 款智能望远镜**的规格表——ZWO Seestar（S50 / S50 Pro / S30 / S30 Pro）、DWARFLAB（DWARF 2 / 3 / mini / DRACO）、Vaonis（Stellina / Vespera / Vespera Pro / Vespera II / Vespera Pro 2）、Unistellar（eVscope 2 / eQuinox 2 / Odyssey / Odyssey Pro）、Celestron（Origin / Origin Mark II）。每项含焦距、口径、焦比、传感器、像元尺寸、**来源等级**与备注。
  - **收录范围明确限定为智能望远镜**：不收行星相机机身、不收「望远镜 + 减焦 + 相机」套装、不收单反长焦组合——这些组合空间无穷，查表不可靠，只能靠月轮几何反推。
  - **诚实标注存疑项**：Unistellar eVscope 2 / eQuinox 2 传感器厂商写 IMX224、第三方拆解认定 IMX347（2.9µm），标为 `disputed` 且**禁止参与传感器反推**；Seestar S50 Pro 与 Odyssey 的传感器厂商未公布，标为 `unpublished`；Vaonis Vespera 已停产，来源降级为 `dealer+third_party`；DRACO 同时记录原生 1.197µm 与 2×2 合并 2.394µm（写入头用合并值）。**Vaonis Hestia 被排除**——无内置传感器、经手机成像，像元尺度不是固定设备属性。
  - **识别算法**：信号优先级 `--device` 覆盖 > FITS 头 `TELESCOP`/`INSTRUME`（`high`）> 采集侧车（`medium`）> 文件名（`medium`）> 传感器回退（`low`）。**取最长匹配 token**，因此 `Seestar S30 Pro` 不会退化成 `seestar-s30`，`Vespera Pro 2` / `Pro` / `II` / `base`、`Odyssey Pro` / `Odyssey`、`Origin Mark II` / `Origin` 同理；仅命中品牌（如只写 `DWARFLAB`）时**不注入任何先验**。
  - **传感器歧义规则**：仅当共用该传感器的机型规格一致时采纳整组先验（IMX662 → Seestar S30 与 DWARF mini 均为 150/30/f5/2.9µm）；规格冲突但像元一致时只回退像元尺寸并标 `ambiguous`（IMX585 → S30 Pro 160/30 vs Vespera II 250/50）；品牌线索可收窄到唯一机型时采纳整组（IMX585 + `Seestar` → S30 Pro）；否则拒绝（IMX224/IMX347 因存疑直接不参与）。
  - **溯源标记（关键设计）**：查表值写入 FITS 头时附带 `DEVICE`（设备 ID）、`DEVSRC`（匹配信号）、`DEVCONF`（置信度）、`OPTPRIOR`（**精确列出实际被填的键**）、`OPTISRC`（`device_table` / `sensor_pixel_only`）。`_infer_optical_parameters` 与 `_decide_drizzle` 的来源字符串随之显示为 `FITS Header (FOCALLEN) [device_table: seestar-s50]`，**先验绝不会被报告成实测**。`OPTPRIOR` 精确到键，所以侧车已给 `FOCALLEN` 时该键不会出现在其中，来源标注也保持普通头部来源。
  - **侧车与实测永远优先**：设备表**只填缺失键**，且注入点位于侧车透传循环**之后**，实测视频侧车 `FOCALLEN=160` 胜过表值 250（已端到端验证）。
  - **FITS 输入路径只识别不注入**：FITS 帧是用户原文件的软链接，技能红线禁止改写用户原始目录。识别结果只写入 `import_receipt.json` 的 `device` 字段（含 `injected` / `target`）并打日志；测试断言源文件 md5 不变。
  - **新增 CLI**：`import` 与 `all` 的 `--device <id>`（未知 id 直接报错并提示 `devices` 命令）、以及 `devices` 子命令输出整表 JSON（含来源等级与排除清单）供 AI 直接查询规格。
  - **交叉校验（advisory）**：当焦距同时来自设备表与几何反推时比较两者，偏差 > 15% 打告警并写入 `postprocess_receipt` 的 `focal_crosscheck`——可暴露机型误判或像元尺寸错误（反推结果与像元尺寸成正比）。**只告警不覆盖**，因为局部月盘的反推可能不可靠。
  - **端到端实测**：侧车仅给 `TELESCOP=Seestar S50`（无任何光学参数）→ 识别为 `seestar-s50`（`source=sidecar, confidence=medium`），头写入 `FOCALLEN=250 / APERTURE=50 / XPIXSZ=2.9` 与全部溯源键；侧车另给 `FOCALLEN=160` 时该键由侧车胜出、`OPTPRIOR=APERTURE,XPIXSZ,YPIXSZ` 正确排除它；FITS 输入识别为 `dwarf-3` 且 `injected=False`、源文件 md5 未变。
- **新增 `references/device_specs.md`**：来源等级图例、19 行主表、存疑与未公布说明、DRACO 双像元说明、未收录机型（Hestia）及原因、识别信号优先级、传感器歧义规则、溯源标记定义、各输入路径行为对照表。**SKILL.md 首次建立 `## 6. 参考文档` 章节**链接三份参考文档，解除此前 `references/` 完全未被引用的孤儿状态。
- **修复 `CHANGELOG.md` 重复版本段**：v1.0.16 存在两个 `## [1.0.16]` 段（bump 脚本生成的占位条目 + 详细条目），已删除占位段。
- **修复 `test_registration_math.py` 陈旧断言**：`test_adaptive_sharpening_math` 期望 `deconv_discount == 0.65`，但 `ee87a75`（v1.0.12「auto 锐化基准调整」）已将其改为 **0.80**，测试未同步更新，导致该测试长期失败。经 `git log -S` 核实为陈旧断言而非代码回归，已修正为 0.80。
- **修复自跑测试注册列表**：`main()` 的 `tests=[...]` 只注册了 32 项，而文件中有 56 个 `def test_*`——v1.0.15/1.0.16 新增的全部 drizzle/sidecar 测试都不在自跑路径内（pytest 能收集，`python test_registration_math.py` 不能）。已补齐为全部 56 项，**自跑脚本首次返回 0**。
- **修复 `cmd_register` 覆盖序列层数导致单色序列无法配准（Critical: mono sequences could not be registered）**：
  - `cmd_register` 重写 `.seq` 时把 `L` 行**硬编码为 `L 3`**，覆盖了 `cmd_import` 按实际通道数写入的 `L 1`。单色来源（`--force-mono` 视频、单色 SER、单色 FITS）因此层数不符，Siril 报 `No registration data exists for this sequence` 且 `seqapplyreg` 直接中止——**单色采集走不完流水线**。
  - 已改为从原 `.seq` 解析并保留真实层数。端到端实测：同一份单色 AVI 在修复前 `stack` 必然失败，修复后 `L 1` 被正确保留、`seqapplyreg` 立即成功。
- **新增测试 11 项**：`test_device_specs_table_integrity`（字段完整性 + 来源等级合法性 + 焦比与焦距口径自洽 + Draco 双像元 + Hestia 必须缺席）、`test_identify_device_model_specificity`（17 组变体特异性，含 `moon_origin_2026.avi` 不得误匹配）、`test_identify_device_sensor_ambiguity_rejected`（IMX662 采纳 / IMX585 仅像元 / IMX178 仅像元 / 品牌收窄 / 存疑传感器拒绝）、`test_identify_device_cli_override_and_brand_only`、`test_device_priors_header_provenance`（含未知 `--device` 报错）、`test_sidecar_overrides_device_table`、`test_ser_device_priors_injection`、`test_fits_import_identify_only_no_injection`（断言源文件字节不变）、`test_optical_source_annotation_device_table`、`test_register_preserves_sequence_layer_count`。全套 57 项测试 **57 通过**，pytest 与自跑脚本双绿。
- **说明（非目标）**：本次**不新增 `tests/` 目录**。CI（`.github/workflows/skills-ci.yml`）只执行技能下的 `tests/`，本技能没有该目录，故其测试目前不进 CI；引入 `tests/` 需先确认自跑路径长期稳定，作为后续单独事项。

## [1.0.16] - 2026-10-01

- **`--drizzle auto` 判据扩展：零光学元数据也能自动判定（Metadata-Free Drizzle Decision）**：
  - **背景**：v1.0.15 的 `auto` 只读 FITS 头 + CLI 覆盖，对没有 `FOCALLEN`/`APERTURE` 的采集源（Seestar 等视频直出）一律返回 `insufficient_metadata` 并关闭，形同虚设。
  - **判据①衍射极限**（`--drizzle-airy-threshold`，默认 1.5px）：需要焦距 + 口径 + 像元三者齐备。**口径在几何上无法从图像反推**，所以这条判据只在口径已知时可用。
  - **判据②实测 PSF 宽度**（`--drizzle-psf-threshold`，默认 2.0px，新增）：任一元数据缺失时启用。两步都不需要设备信息：
    1. **焦距几何反推**——复用 `_infer_optical_parameters` 在参考帧上做月轮 RANSAC 拟合，配合像元尺寸与 JPL 历表月球视直径反解 `f = D_px·px·1e-3 / (2·tan(θ/2))`。实测合成数据反推 192.3mm（理论 192.27mm）。**若头部没有像元尺寸则拒绝反推**——默认 3.73µm 会让焦距等比失真，宁可不用；
    2. **月轮 ESF 实测 PSF 宽度**——月轮对黑空是近乎完美的阶跃边缘，其径向强度剖面就是整条光学+视宁度+采样链的边缘扩散函数。对高斯 PSF，10–90% 宽度 = 2.563σ，故 `FWHM = 0.919 × w10-90`。**对解析高斯模糊圆盘验证：FWHM 0.9–7.1px 全程误差 < 1.5%**。
  - **为什么用实测 PSF 而不是衍射极限**：决定「更细网格能否恢复细节」的是**有效 PSF 宽度**（含视宁度），衍射极限只是下界。视宁度通常主导，所以实测判据在两者分歧时更可靠，且它完全不需要任何元数据。
  - **绝不用默认值冒充真实值**：两条判据都不成立时关闭 drizzle 并给出可操作提示，`focal_len_mm`/`aperture_mm` 在回执中保持 `null`，绝不填 80mm / 400mm / 3.73µm 这类默认值。
  - **回执全量溯源**：`stack_receipt.json` 的 `drizzle` 记录 `criterion`（`airy` / `psf_fwhm` / `forced` / `none`）、`psf_fwhm_px`、`psf_esf_width_px`、`limb_radius_px`、`limb_fit_residual_std`、`psf_possible_saturation`、`focal_source`、`pixel_size_source`、`aperture_source`、`probe_frame`。
  - **已知边界（已写入文档）**：几何反推与 ESF 测量都要求**完整月盘在画面内**，月面局部特写 / 马赛克切片会失败并安全关闭；月轮过曝时 ESF 顶部被削平会低估 FWHM（偏向启用，安全方向），此时回执标注 `psf_possible_saturation`。
  - **被否掉的方案（记录以免重走）**：曾考虑用径向功率谱截止频率 `k_cutoff` 作经验判据，实测发现它**非单调**——k_cutoff 在 PSF FWHM≈2.0px 处达到峰值（0.762），两侧同时下降（FWHM 0.71px → 0.525，FWHM 5.89px → 0.364），因为它同时受内容带宽与噪声水平影响，无法判别采样充分度，故弃用。
  - **端到端实测**（4 帧 512×256，头部**只有** `XPIXSZ=2.9`）：`criterion=psf_fwhm`、实测 `FWHM 0.604px`、`limb_radius_px=89.39`（真值 90）、`limb_fit_residual_std=0.074`、`focal_len_mm=null`、`aperture_mm=null` → 自动启用 2× drizzle，母版 1022×512、`XPIXSZ` 1.45、`DRZSCALE` 2.0。另测「只有 `XPIXSZ` 但圆盘角直径反推焦距仅 58mm」的合成帧：几何反推被 100mm 下限正确拒绝，自动降级到 ESF 判据并仍给出正确结论。
- **新增测试 4 项**（`scripts/test_registration_math.py`）：`test_limb_psf_fwhm_recovers_gaussian`（解析高斯圆盘，σ 0.5–2.0 误差 < 5% + 单调性）、`test_drizzle_psf_criterion_without_aperture`（无口径走 ESF 判据、阈值两侧翻转、显式 `--aperture` 恢复衍射判据、**断言默认 80mm 口径绝不被静默使用**）、`test_drizzle_geometric_inversion_recovers_focal`（反推焦距落在 180–205mm、来源含 `geometric inversion`、无像元尺寸时拒绝反推）、`test_probe_frame_prefers_register_reference`（优先用 `ranking.json` 选出的最锐帧）；`test_drizzle_auto_decision` 同步更新为新 reason（`insufficient_evidence` / `user_forced`）。全套 47 项测试 **46 通过**（`test_adaptive_sharpening_math` 为改动前即存在的既有失败，已用 `git show HEAD` 版本复现确认与本版无关）。
- **文档同步**：`SKILL.md` §1 与 Step 3 改写为「两条判据 + 各自前提 + 已知边界」，并补充 `--drizzle-airy-threshold` / `--drizzle-psf-threshold` 用法。

## [1.0.15] - 2026-10-01

- **统一剔除均值堆叠 + 噪声加权（Unified Rejection-Mean Stacking with Noise Weighting）**：
  - **背景**：用合成序列对 Siril 1.4.4 的 `stack sum` 做了行为实测，发现两个此前未被记录的缺陷——① **按全图最大值归一化**（两次实测输出峰值恒为 `1.000000`；两区域 100/200 的输出为 `0.4926 / 0.9852`，即 `sum / max(sum)`）；② **不做任何像素剔除**，也不支持 `-norm=` 与 `-weight=`。后果是单帧一个热噪点、宇宙线或视频压缩坏块即可让**整幅信号塌缩**：注入一个 60000 的像素后输出从 `0.4926 / 0.9852` 变为 `0.004984 / 0.009967`（约 **100×**）。
  - **实测对照**：`rej w 3 3 -norm=addscale` 的相对对比度与 `sum` 完全一致（**2.0 vs 1.97**），二者经后处理归一化后等价，而 `rej` 额外提供像素剔除、帧间归一化与加权能力。故原「8-bit 用 sum 扩展动态范围」的论据不成立。
  - **改动**：所有位深统一走 `stack rej w 3 3 -norm=addscale -filter-included -weight=noise`；`--stack-method sum` 保留为显式回退（此时忽略 `--weight`）。新增 `--weight {auto,none,noise}`（默认 `auto`）。
  - **实测确认 `-weight=noise` 真实生效**：日志打印 `Computing weights based on noise...` → `Image weighting ........... from noise`，输出与无权重版本确有差异；非法取值会报 `Unknown argument to -weight=bogus, aborting.`。
  - **失败自愈**：`-weight=noise` 因背景统计不可用（`MAD is null` / `Statistics cannot be computed.`）失败时，自动识别日志并去掉 `-weight=` 重跑一次，记入 `stack_receipt.json` 的 `weight_fallback`。
- **HST Drizzle 超采样（HST Drizzle Supersampling，`--drizzle {auto,off,2,3}`，默认 `auto`）**：
  - **CLI 语义已实测定案**：`seqapplydrizzle` 独立命令在 1.4.4 **不存在**（`Error: command seqapplydrizzle is not available`，上游手册属更新版本）；只能用 `seqapplyreg -scale=N -drizzle -pixfrac= -kernel=`。合成 3 帧 1024×512 序列实测：scale=2 输出 **2046×1024**，日志 `Drizzling parameters: scale: 2.000000`。注意**单独 `-scale=2`（无 `-drizzle`）也会放大到同样尺寸**，因此判断 drizzle 是否生效必须看日志而非尺寸。
  - **Lanczos 限制是建议而非硬约束**：`-kernel=lanczos3 -scale=2` 实测**不报错**，故由技能主动回退为 `square`（手册规定 Lanczos 仅允许 `scale == pixfrac == 1.0`）。
  - **auto 决策**：读取首帧 FITS 头的 `FOCALLEN`/`XPIXSZ`/`APERTURE`（可用 `--focal`/`--pixel-size`/`--aperture` 覆盖），计算理论艾里斑半径 `1.22·λ·F#/pixel`，**< 1.5px（欠采样光路）时自动启用 2×**。元数据缺失时 `auto` 关闭并给出可操作的提示；显式 `--drizzle 2` 仍可强制。`--mosaic-mode tile` 下 `auto` 默认关闭（保持各面板采样尺度一致，避免与 `siril-mosaic` 自有的 `-scale` 冲突）。
  - **母版像素尺度校验**：实测 Siril 在 drizzle 时**会自动**把 `XPIXSZ`/`YPIXSZ` 除以缩放倍率（2.9µm → 1.45µm），且 `stack` 出的母版继承该值，`FOCALLEN`/`APERTURE` 原样透传。技能随后校验（`_verify_master_pixel_scale`），仅在未修正时代为改写，并写入 `DRIZZLE`/`DRZSCALE`/`DRZPIXFR`/`DRZKRENL`/`ORIGXPIX` 与 `HISTORY`。因 `FOCALLEN` 保持物理正确，下游 `siril-mosaic` 的 `seqplatesolve -focal= -pixelsize=` 自动获得正确尺度（此前若只改 XPIXSZ 而不改 FOCALLEN，plate solve 会差 2×）。
  - **postprocess 像素常数缩放**：新增 `_px()`/`_odd()` 与 `_resolve_px_scale()`，把 drizzle 倍率从 `stack_receipt.json`（回退母版 `DRZSCALE`）贯通到后处理。缩放对象：月轮 RANSAC 拟合的射线步长与内点/残差阈值（不缩放会把内点判定静默收紧到等效 1.5 原生 px 导致拟合失败）、眩光抑制过渡带宽与月盘保护带、抗振铃阻尼的局部基线/膨胀/柔化核、深影调矿物月的月轮去色与清零宽度、以及 `_infer_optical_parameters` 的 user/default 像元尺寸分支（header 分支已由 Siril 修正，不重复缩放）。色度低通 σ 因已按图像尺寸表达而**不缩放**。
  - **端到端实测**（4 帧 512×256，XPIXSZ=2.9µm / FOCALLEN=160mm / APERTURE=30mm）：auto 判定 Airy r=1.234px < 1.5px → 启用 2×；母版输出 1022×512，`XPIXSZ` 1.45µm、`FOCALLEN` 160.0 保持不变；postprocess 日志确认「working grid is 2x finer」、`px=1.45um`、`Airy radius = 2.47px`（物理上正确地翻倍）；verify 新增 Drizzle 状态行；`mosaic_tile_info.json` 新增 `px_scale`/`drizzle` 字段。
  - **收尾**：新增清理 Siril 的 `drizztmp/` 中间目录。
  - **运行失败自愈**：drizzle 堆叠失败且非强制时自动回退为插值路径重跑，记入 `stack_receipt.json` 的 `drizzle.fallback`。
- **采集侧车光学元数据透传（Capture Sidecar Optical Metadata Passthrough）**：
  - 视频导入原先只把侧车 `.txt` 里的 `SENSOR`/`DATE-OBS`/`SITELONG`/`SITELAT`/`OBJECT` 写进 FITS 头，`FOCALLEN`/`APERTURE` 等被解析后即丢弃。由于视频路径从不写 `FOCALLEN`，`--drizzle auto` 在最常见的采集源上会因缺元数据而永远关闭。
  - 现将侧车的 `FOCALLEN`/`FOCAL`/`APERTURE`/`APTURE`/`XPIXSZ`/`PIXSIZE` 一并透传（仅取正值，已由传感器查表设定的键不被覆盖，设 `XPIXSZ` 时同步补 `YPIXSZ`），使 `auto` 决策与 postprocess 光学推断可用真实数值而非默认值。
- **新增测试 11 项**（`scripts/test_registration_math.py`）：`test_video_8bit_stack_policy`（改为断言 rej + `-weight=noise`）、`test_rej_weight_noise_default_for_16bit`、`test_sum_method_ignores_weight`、`test_weight_fallback_on_failure`、`test_stack_all_attempts_fail_reports_count`、`test_drizzle_auto_decision`、`test_drizzle_script_lines_and_receipt`、`test_drizzle_runtime_fallback`、`test_video_sidecar_optical_metadata_passthrough`、`test_master_header_pixel_scale_repair_and_idempotency`、`test_px_scale_threading_scales_pixel_constants`、`test_resolve_px_scale_prefers_receipt_then_header`；全套 43 项测试 **42 通过**（`test_adaptive_sharpening_math` 为改动前即存在的既有失败，已用 `git show HEAD` 版本复现确认与本版无关）。
- **文档同步**：`SKILL.md`（§1 堆叠原则、Step 3 重采样/堆叠/自愈/母版尺度、方式 C/D 描述）、`references/siril-144-cli.md`（新增 §3.1 Drizzle 实测表与 §4.1~4.3 堆叠分支/推荐组合/`sum` 缺陷）、`scripts/mp4_to_ser.py` 头部注释（位深不再是堆叠策略分叉）。

## [1.0.14] - 2026-10-01

- 补充视频抽帧策略文档：明确 --sample-mode（smart-top/smart-cluster/window/head）、--probe-stride、--start-frame 语义，澄清默认全帧导入与内容驱动选帧、非固定间隔抽帧
- 补充 SER 路径 `--limit` 语义警示：`import` 的 SER 分支没有 `--sample-mode` 智能扫描，`--limit N` 退化为顺序截断（取前 N 帧，等价 `head` 模式），与视频路径的"全片最锐 N 帧"语义不同；转 SER 不免除选帧环节，但会削弱 import 阶段的内容驱动抽帧能力，推荐转 SER 后用 `--limit 0` 全帧导入交由 `register` 选帧

## [1.0.13] - 2026-09-30

- **修复 SER 读取小端序反转缺陷（Critical: all real-world SER files were byte-swapped）**：
  - `parse_ser_header` 原先按字段名字面语义解释 `LittleEndian` 标志（`"<" if little_endian else ">"`），而 SER 生态的**事实标准与字段名相反**——`0 = 小端、1 = 大端`（SER Player / PIPP / Siril / GoQat 均如此，Siril 官方 wiki 明确记录此为规范遗留问题）。
  - 后果：所有主流工具（Siril、FireCapture、SharpCap、PIPP）产出的 SER 被整体字节翻转。以 Siril 1.4.4 亲笔写出的 16 位 SER（实测 R=1000 / G=2000 / B=4000）为例，修复前被读成 59395 / 53255 / 40975 的乱码。
  - 现已改为 `dt_endian = ">" if little_endian else "<"`，实测可正确解出 Siril 产出的 SER；单色/Bayer SER（单平面）不受影响，此前缺陷仅在 RGB/BGR 彩色 SER 上显性暴露。
  - 单元测试辅助函数 `_make_synthetic_ser` 的 `little_endian` 默认值同步由 `1` 改为 `0`，使测试覆盖真实生态约定而非自洽的私有约定。
- **新增 `scripts/mp4_to_ser.py`：MP4/MOV/MKV/AVI 直转 SER 容器**：
  - 背景：Siril 的 `convert` 只扫描静态图像，实测**拒绝一切视频输入**（`convert moon -ser` 对现存的 `moon.mp4` / `moon.avi` 均报 "No files were found for conversion"，`load moon.mp4` 报 "file not found or not supported"）；PIPP 需 GUI 安装，ffmpeg 常缺失。
  - 采用 OpenCV + NumPy 直转，零中间容器：写出 178 字节规范 SER 头（小端、`LittleEndian=0`、RGB 帧为**交织序** R,G,B,...，均以 Siril 1.4.4 实际产出为基准校准）。
  - `--depth {8,16}`：8 位为解码结果忠实透传；16 位走 `(v << 8) | v` 无损扩展。注意二者会让下游走不同堆叠分支（8 位 → `stack sum`，16 位 → `stack rej w 3 3`）。
  - `--color {auto,mono,rgb,bayer-*}`：`auto` 复刻 `unpack_video_to_fits` 的检测逻辑，对未解拜耳的 RAW 视频载体探测四种 Bayer 图案并做暖月 R/B 镜像消歧，以正确的 SER `ColorID` 保留 CFA 平面；`--verify` 可回读校验头部与载荷自洽。
  - 已实测：彩色/单色/显式 Bayer/auto 探测四条分支、8 位与 16 位两种深度均通过；mp4 → SER → `unpack_ser_to_fits` 全 12 帧逐像素一致；合成 RGGB 马赛克被 auto 正确识别为 RGGB 而非镜像 BGGR。

## [1.0.12] - 2026-09-26

- **超稳视宁度散布感知帧筛选（Spread-Aware）**：`otsu` 模式内置相对散布判定，当全序列极差 < 6% 时自动扩容保留至 75% 优质帧，兼顾极致信噪比与抗抖动。
- **auto 锐化基准调整**：Airy 反卷积激活时小波重构各层增益收敛至黄金微调区间（实测 `1.02, 1.04, 1.06, 1.03`）；反卷积激活或 auto 模式下**彻底旁路 CLAHE**（`clip = 0.0`），杜绝局部微反差过拉造成的石膏白垩感、生硬刻痕与死黑阴影。
- **`--sharp-mode` 语义更新**：`auto` 为 Airy 反卷积 + 黄金小波微调保底 + 彻底旁路 CLAHE；`mellow` 为 1~2% 极弱小波 + 关闭 CLAHE。
- **矿物月默认饱和度提升**：fe-boost 5.2、ti-boost 6.5、sat-base 0.35，追求自然水彩质感。

## [1.0.11] - 2026-09-25

- **亮度和锐化参数全智能化自适应系统（Smart Adaptive Brightness & Sharpening System）**：
  - **物理反照率自适应中间调解析求解（Smart Auto-Midtone, 默认 `--midtone auto`）**：
    - 废除一刀切的硬编码 `--midtone 0.42`，建立基于月面玄武岩月海反照率中位数的解析反求模型（$y = \frac{(m-1)x}{(2m-1)x-m} \implies m_{\text{auto}} = \frac{x(1-y)}{x(1-2y)+y}$）；
    - 全月相（满月、弦月、残月）与全曝光范围自适应，将月海与高光始终稳定锚定在黄金影调区间，彻底消除人工反复测试调参；仍保留显式数值通道以保持 100% 后向兼容。
  - **低动态线性 RAW 月轮几何拟合与真实光学焦距反推修复**：
    - 修复 `_fit_lunar_limb_circle` 底噪未解耦与硬编码绝对梯度阈值（`grad < -0.005`）导致的静默拟合失败；
    - 改用自适应相对负梯度阈值（`grad < -0.015 * p999`），在 32 位浮点线性 RAW 堆叠上达到 100% 成功识别；实测准确反推 Moon3 真实望远镜焦距 $f = 915.0\text{ mm}$、焦比 $F/11.4$ 与真实 Airy 艾里斑半径 $r = 2.06\text{ px}$（此前被低估为 0.90px），充分释放 Split Bregman 消除光学衍射弥散的真实能力。
  - **二维傅里叶径向功率谱视宁度截止频率感知（Radial PSD Seeing Cutoff）**：
    - 引入 2D-FFT 径向平均功率谱，测定月面信号降至残噪基底的空间截止频率 $k_{\text{cutoff}}$；
    - 动态根据 $k_{\text{cutoff}}$ 与月海高频噪声分配小波层级权重：视宁度良好释放第 1 层微弱反差，视宁度差自动锁定第 1 层为 1.00 并向第 2、3 层转移增益，杜绝高频毛刺与沙砾感。
  - **GKM 质检指标天球边缘解耦与误报消除**：
    - 修复 5px 浅层腐蚀未能完全剔除月盘外缘阶跃梯度导致的峰度虚高问题；通过自适应深层腐蚀（25-50px）纯化月表地貌采样，GKM 真实值从虚高误报的 48.42 自然回落至健康的 5.30（ORGANIC 质感评级）。
  - **精简成片交付体系与剥离冗余备份**：
    - 移除默认流程中冗余的 Siril 线性饱和度拉伸备份 `moon_mineral_natural.jpg`，消除心智模型负担；
    - 规范交付体系为「写实自然月 (`moon_natural.jpg`)」与「地质彩月 (`moon_mineral.jpg`)」二元输出，并对称导出 1:1 方形社交特写切图（`moon_natural_square.jpg`, `moon_mineral_square.jpg`）；
    - 仍保留 `--mineral-style natural` 显式参数回退开关。
  - 新增 3 项核心单元测试，全套 32 项单元测试 100% 绿色通过。

## [1.0.10] - 2026-09-25

- **增强非标准视频解码错误提示与自愈引导（Enhanced Non-Standard Video Diagnostics & Recovery Guidance）**：
  - **智能文件头与 OpenCV 状态诊断矩阵 (`format_video_decode_error`)**：当 OpenCV `VideoCapture` 打开或抽取帧失败时，不再抛出无语义的简陋 `ValueError`，而是深入透视文件头魔数与尾部数据，生成结构化诊断报告（包含目标路径、字节大小、容器类型推断、FourCC 编码识别、OpenCV 后端状态与确凿根因）；
  - **高频损坏场景精准定位与自愈命令注入**：
    - *未闭合 MP4 识别*：自动检测拍摄中途断电/App闪退造成的 `moov atom missing`，并给出即拷即用的 `ffmpeg -err_detect ignore_err -i input.mp4 -c copy fixed.mp4` 修复指令；
    - *缺失编解码器支持*：识别 H.265/HEVC、AV1、Apple ProRes 等 OpenCV 缺少解码后端的格式，给出无损 rawvideo/mjpeg AVI 转码指令；
    - *天文相机特有 FourCC*：识别 `Y800`、`GREY`、`DIB `、`ZWO ` 等未压缩调色板，提供 PIPP / AutoStakkert / ffmpeg 适配建议；
    - *扩展名伪装识别*：自动识别被误命名为 `.mp4/.avi` 的真实 SER 天文流或 FITS 图像，引导用户直接使用 `--format ser` 或 `--format fits` 导入；
    - *异常格式前置拦截*：针对 `.webm`, `.ts`, `.flv` 等视频扩展名在 `import` 入口处给出友好的标准转码指引。
  - 全套 29 项单元测试（含新增 `test_nonstandard_video_diagnostics`）100% 保持绿色通过。

## [1.0.9] - 2026-09-25

- **修复视频 Bayer 解码两处色彩正确性缺陷（Color Fidelity Fixes）**：
  - **补齐去马赛克分支缺失的 `BGR→RGB` 转换**：OpenCV `cv2.demosaicing()` 的输出为 **BGR** 通道序（以同目录 `.mp4` 彩色预览为 ground truth 做相关性验证：dem 通道1→G=0.975、通道2→R=0.911、通道0→B），但该分支此前直接 `transpose` 写入 FITS，导致 R/B 通道倒置；RGB 直通分支已有转换而 Bayer 分支遗漏。现已补上 `rgb = cv2.cvtColor(rgb, cv2.COLOR_BGR2RGB)`，与全链路「FITS RGB 序」契约一致。
  - **修复自动 Bayer 图案选型会锁定 R/B 镜像错图案**：原逻辑仅凭「网格伪影最小」metric 选型，但一个 Bayer 图案与其 R↔B 镜像（如 `GRBG` vs `GBRG`）网格 metric 完全相同（实测均 7.93），会静默选错致 R/B 颠倒。现改为：① 在最亮 128×128 月面块上测量（原用画面中心，可能为空天而丧失 CFA 信息）；② 增加物理 R/B 消歧——月球整体偏暖（R/G > B/G），若解出蓝主导则切换至镜像图案。Seestar S30 真实样本实测由误判的 `GBRG` 正确翻转为 `GRBG`（ground truth R/G=1.439）。
  - 全套 28 项单元测试 100% 通过。

## [1.0.8] - 2026-09-25

- **月面物理日光反照率影调重塑与锐化过头（Crunchy Texture）彻底根治**：
  - **纠正深空星云拉伸预设，重塑日光物理影调（Midtone 0.18 -> 0.42）**：
    - 确凿定位“全局过曝”根源为错误套用暗弱深空天体（DSO）的拉伸参数（`midtone=0.18`）——导致输入仅 0.116 的玄武岩暗海被强拉至 128 灰阶，全月面 88.8% 的像素被严重挤压在 $[153, 235]$ 极亮暴晒区（中位数高达 192.5）；
    - 将 `--midtone` 默认值调整为月球日光反射率基准 **`0.42`**：月表整体中位数回归至柔正中灰 **123.0**，最暗玄武岩暗海从 147.5 回落至深沉沉郁的 **74.0**，有效动态范围从 77 灰阶扩宽至 **102 灰阶**（地质层次扩展 32.5%），高光暴晒比例从 50.6% 骤降至 **1.68%**，从根本上彻底消灭过曝泛白。
  - **消除非线性阶梯陡化，彻底解决生硬描边（Crunchy / Embossed Texture）**：
    - 消除因中间调塌陷导致仅剩高反差黑白勾线的浮雕感错觉；非线性微积分导数 $dy/dx$ 从 2.48 自然平复至 1.30，环形山明暗边缘阶跃硬度自然减半，恢复圆润、深邃的碗状盆地地貌反差。
  - **前置软膝盖高光压缩启动阈值（`knee 0.85 -> 0.75`）**：
    - 从约 190 灰阶开始平滑双曲正切下压最高反照率辐射纹峰值，自然黑白与彩色矿物双管线同步受控，确保高反照率微细节晶莹立体、通透不刺眼。
  - 全套 27 项单元测试全部保持 100% 绿色通过，真实 Seestar S50 样本各项量化指标完美达标。

## [1.0.7] - 2026-09-25

- **平坦暗海椒盐噪点与月盘边缘镶边伪影彻底根治（Shot Noise Amplification & Limb Chromatic Aberration Root-Cause Cure）**：
  - **彻底清除暗海椒盐噪点与环形山削顶白点（CLAHE Bypass & Soft Sharp Balance）**：
    - 确凿定位平坦暗海满屏椒盐颗粒真凶为 Siril 脚本中的小半径局部直方图均衡（`clahe 0.12 32`）——在缺乏反差的平坦月海中将高频散粒噪声强行放大 13.5 倍（拉普拉斯方差曾暴增至 0.107）；
    - 规则化重构自适应锐化算法：在 `auto` 模式下，当物理反卷积激活（`has_deconv=True`）或底噪可检出时，严格将 CLAHE clip 设为 0.0（完全 bypass），高频清晰度纯粹由光学 Airy 反卷积及小波中层恢复，平坦月海方差回归至平滑自然的 0.000085（降噪比逾 134 倍），环形山与辐射纹削顶白点彻底归零（0 像素）。
  - **彻底消除月缘品红/紫边与下边缘绿边（Sobel ADC Gradient Alignment + 360° Limb Circle Fit + 18px Defringe Zone）**：
    - **Sobel 边缘梯度亚像素 ADC**：将 `align_rgb_channels` 升级为基于 Sobel 物理边缘梯度图的相位相关对齐，彻底排除地表矿物反射率偏差干扰，使 R/G/B 通道几何边界对齐精度达到极致；
    - **修复月轮圆拟合大端字节序与全周向采样**：修复 FITS `>f4` 大端字节序导致 OpenCV C++ 函数解析错误返回 None 的隐蔽缺陷，并将径向搜索由局部单向扩展至全周向 360° 采样，RANSAC 拟合残差标准差仅 0.67 像素，全面赋能渐盈月、弯月及任意倾角月相；
    - **18 像素平滑月肢中性保护带（Limb Defringe & Desaturation Zone）**：月轮外沿 4px 设为绝对中性死区，向内 4~18px 采用三次 Hermite 平滑多项式过渡，将折射镜次级光谱与横向色散（$R+B > G$ 品红与下缘绿色渗出）彻底归零，边缘品红像素减少 100%（从 590 降至 0），纯净空间截断彻底消弭背景光晕与色散渗出。
  - 全套 27 项单元测试全部保持 100% 通过，真实 Seestar S50 视频测试样本完美通过科学指标与视觉双重验收。

## [1.0.6] - 2026-09-25

- **月面图像后期画质全链路重构与四大视觉缺陷根治（Image Quality & Geological Fidelity Overhaul）**：
  - **色彩通道与矿物色重构（彻底解决色彩失效与孤立假蓝斑）**：
    - 彻底废除 `_render_deep_cine_mineral` 中的二值硬阈值分类器（`ti_m`, `fe_m`）与过激的夜侧/暗海截断掩模；
    - 采用标准**物理连续地质色度空间拉伸（Continuous Geological Chrominance Stretch）**：先经大半径双边滤波（Bilateral Filter）剥离传感器散粒彩色噪点，再按真实光谱偏差平滑连续放大，雨海的深邃钛蓝与静海、澄海的铁红暖橙实现天然、大面积连续地质色彩过渡（有效色彩覆盖率从 0.70% 跃升至 18.13%，Ti/Fe 比例达 50.1% vs 43.4%）；
    - 收紧宏观大气消光补偿门槛（要求严苛反向共线性与高置信度 $\ge 0.70$），彻底消除将月球原生反照率误拟合为倾斜平面的整盘色偏；
    - 移除 Siril 脚本中破坏天然青蓝光谱的 `rmgreen 0`，保留纯净真实地质波段响应。
  - **自适应去噪、高光余量预留与软膝盖压缩（彻底解决过“crunch”感与小白点削顶）**：
    - 在白平衡与拉伸时提供 $+25\%$ 的充足高光余量（Highlight Headroom），使底图峰值保持在 $\approx 0.80$，避免反卷积与小波能量在 1.0 发生硬截断（Hard Clipping）；
    - 优化小波多尺度权重：当存在反卷积或可探测底噪时，小波第 1 层增益严格保持在 1.00，消除粗糙椒盐状散粒噪点放大，锐化能量集中于 2~6 像素尺度的真实环形山轮廓；
    - 引入可微分**软膝盖高光压缩（Soft-Knee Highlight Compression, $L > 0.85$）**，环形山 rims、中心峰与高反照率辐射纹削顶率（CSI）从过曝削顶降至 0.002%（完全保留高光微反照率反光梯度）。
  - **双向边缘过冲阻尼与月肢色度中性化（彻底解决月缘亮白圈与边缘色彩错位）**：
    - 升级抗振铃阻尼机制：不仅抑制阴影侧负过冲暗晕（DHR 降低 45%），同时精确捕获月盘物理边缘（Limb）内侧小波正向过冲（$\Delta > 0$）并施加 75% 吸收阻尼，彻底消灭月缘死白发光白边；
    - 引入月肢过渡带色度平滑淡出（Limb Desaturation，最外侧 5 像素转换为纯中性灰过渡到太空深黑），彻底根除折射镜横向色差及拜尔边缘插值产生的红绿蓝色边。
  - **宽动态范围影调平复（彻底解决暗海死黑与阶调断层）**：
    - 废除暗部下凹幂函数（$lum^{1.155}$），平复中灰影调曲线，默认 MTF 中间调调整为更自然的 0.18，玄武岩暗海（静海/澄海/风暴洋）暗调细节丰富饱满，彻底杜绝死黑。
  - 全套 27 项单元测试全部保持 100% 通过，真实 Seestar S50 视频测试样本完美通过科学指标与视觉双重验收。

## [1.0.5] - 2026-09-25

- **引入 AVI / MP4 / MOV 等通用视频容器原生直通支持（Direct-pass FITS Architecture）**：
  - 支持直接传入单文件（如 `--input /path/to/moon.avi`）或包含视频文件的目录，涵盖 `.avi`、`.mp4`、`.mov`、`.mkv`、`.m4v` 等主流封装；
  - 彻底规避“AVI $\to$ SER $\to$ FITS”的冗余二次转录，利用 OpenCV 硬件级解码逐帧抽取并直写 16-bit FITS 序列，节省 50% 磁盘 I/O 写入带宽；
  - 内存级精确映射 BGR 到 FITS RGB `(channels, H, W)`，平滑执行 8-bit 到 16-bit 线性高动态扩展，彻底杜绝跨格式转录导致的红蓝对调与相位错位；
  - 自动检测单色与彩色流，新增 `--force-mono` 参数支持彩色相机配 IR-pass 滤镜拍摄的高反差单色模式；
  - 截断与坏帧弹性容错：遇视频尾部截断或坏帧平滑捕获并记录有效帧数，绝不因容器格式异常中断；
  - **自适应绑定 Siril `stack sum` 物理高动态扩展（Vincent Hourdin 规范）**：
    - 自动识别 8-bit 视频源，在下游 Siril 堆叠脚本中自动启用加法叠加（`stack sum -filter-included`），在 32 位浮点累加器中将 8 位动态范围物理扩展到 14~16 位以上；
    - 新增 `--stack-method {auto, sum, rej}` 允许显式覆盖；
  - 新增 `test_video_unpack_synthetic_avi`、`test_video_cmd_import_single_file_and_dir`、`test_video_8bit_stack_policy` 3 项高覆盖度单元测试，全套 27 项测试 100% 保持通过。

## [1.0.4] - 2026-09-24

- **引入低仰角宏观大气消光一阶梯度补偿（Atmospheric Extinction Gradient Compensation, `--extinction-comp {auto,mild,aggressive,off}`）**：
  - 针对月出/月落低仰角（$a < 30^\circ$）拍摄时，因月盘半度视场跨度引起的大气质量（Airmass）差诱发的宏观 Rayleigh 消光倾斜（“底暖顶冷、底暗顶亮”坡度），在 32 位浮点线性空间实施通量守恒除法平复；
  - 创新采用对数色比空间映射（$s_B = \ln(B/G), s_R = \ln(R/G)$），表面反照率（月海 vs 高地）与月相明暗在此空间完全被除法抵消；
  - 集成多尺度 $16\times 16$ 网格中位数降采样与 Huber 鲁棒平面回归（IRLS），天然免疫局部高钛玄武岩色块干扰；
  - 引入 Rayleigh 物理反向共线性校验与自适应门限（全盘色偏 $<1.5\%$ 自动旁路，确保高仰角零扰动）；
  - 彻底根除深空矿物彩月被宏观消光污染导致的“半边黄泥、半边紫蓝”的伪地质色带；
  - 导出 `extinction_receipt.json` 并集成到 `verify` 质检审计报告；
  - 新增 `test_extinction_gradient_synthetic`、`test_extinction_gradient_flat_bypass`、`test_extinction_geological_immunity` 3 项高强度单元测试。

## [1.0.3] - 2026-09-24

- **引入行星与月面 SER 视频流原生直接支持（Native SER Video Stream Support）**：
  - 核心遵循 Lucam Recorder 178 字节二进制标准协议，支持直接传入单文件（`--input /path/to/capture.ser`）或包含 `.ser` 的目录；
  - 采用零拷贝 `np.memmap` 瞬时读取，上百 GB 大视频零内存膨胀；
  - 原生支持 Bayer CFA（RGGB/GRBG/GBRG/BGGR）与 RGB/BGR 彩色格式，集成 OpenCV C++ 硬件级多线程 demosaicing（1080p 单帧解拜耳仅需 4.9ms），直接生成 16-bit RGB FITS；
  - 提取 SER 纳秒级 UTC 科学时间戳，自动换算为标准 ISO-8601 字符串注入 FITS `DATE-OBS`；相机（`INSTRUME`）与望远镜（`TELESCOP`）设备元数据全量无损透传；
  - **首度建立单色（Mono）与彩色（RGB）全链路自适应**：Mono 数据原生生成 `L 1` 序列并在后处理优雅绕过彩色专属滤镜，为单色冷冻相机配 IR-pass 窄带滤镜的高阶月面摄影铺平道路；
  - 支持 `--limit <N>` 限制帧数，避免超长视频冗余 I/O；
  - 新增 `test_ser_header_parser`、`test_ser_unpack_mono`、`test_ser_unpack_bayer`、`test_ser_cmd_import_single_file_and_dir` 4 项单元测试。
- 引入智能光学参数推断（Intelligent Optical Parameter Inference，Header 元数据挖掘 + 亚像素月盘几何反推）：
  - 自动从 FITS Header 挖掘 `XPIXSZ` / `PIXSIZE` 像元尺寸（如 $3.73\ \mu\text{m}$），解耦命令行硬编码；
  - 联动亚像素 RANSAC 月轮圆拟合，由实测像素直径 $D_{px} = 2097\text{ px}$ 结合天体测量学月球视直径（$31.1'$，区间 $29.4'\sim 33.5'$），通过针孔成像几何精确反推有效焦距 $f \approx 865.5\text{ mm} \approx 866\text{ mm}$（物理区间 $803\sim 915\text{ mm}$）；
  - 彻底纠正旧版硬编码默认值（$400\text{ mm}$）低估一倍的物理失真，使 Split Bregman Airy 反卷积 PSF 像元半径从 $0.90\text{ px}$ 恢复为物理真实的 $1.95\text{ px}$（焦比 $F/10.8$），完全释放光学反卷积效能；
  - `--focal`、`--pixel-size`、`--aperture` 默认设为 `None`，未指定时自适应推断，用户显式指定时 100% 优先；切片模式自动安全旁路；
  - `verify` 命令集成光学推断参数、焦比与 Airy 斑尺寸输出；
  - 新增数学单元测试 `test_infer_optical_parameters_math`。
- 引入全局色彩与直方图锁定机制（Anchor Master Profile Lock，`--lock-profile` / `--lock-from` / `--export-profile` / `--lock-wb` / `--lock-stretch`）：
  - 彻底根除多面板全景拼接时，切片间由于局部反照率差异（暗月海 vs 亮高地）独立拉伸导致的色块漂移与接缝明暗阶梯断层；
  - 实测将多面板公共重叠区明暗跳跃从未锁定的 409.2% 直接降至 0.0000%；
  - 支持从基准面板（Anchor Master）一键继承通道平衡增益（$k_r, k_b$）与直方图非线性 MTF 映射基准（$bg_{lum}, hi_{lum}$、midtone、矿物色彩参数）；
  - `mosaic_tile_info.json` 自动记录锁定状态、来源路径与校验参数；
  - `verify` 命令集成 Profile 锁定状态审查输出；
  - 新增数学与物理管道单元测试 `test_histogram_color_lock_pipeline`。
- 引入亚像素阶跃边缘下冲暗环抑制（Subpixel Anti-Ringing Damping, `--anti-ringing`，默认 `auto`）：
  - 通过局部对数梯度与动态基准分析精确定位明暗阶跃断崖（晨昏线坑壁、月轮边缘）的阴影侧负下冲带；
  - 实施非对称弹性阻尼平复（默认强度 0.45~0.65），彻底根除 Gibbs 振铃与小波负瓣导致的人工暗环/甜甜圈黑圈，同时 100% 保持高光山脊正向极限锐度与解析力；
  - 支持 `--anti-ringing {auto,off,mild,aggressive}` 与 `--damping-factor` 手动覆盖；
  - 同步更新 32 位浮点明度母版、16 位 TIFF 母版及高质量自然版与矿物月成片。
- 引入防脆裂质检指标矩阵与高光白垩化诊断（Anti-Brittleness & Chalky Saturation Quality Matrix）：
  - **高光白垩饱和度指数 (CSI, Chalky Saturation Index)**：精确定量过度拉伸/CLAHE 引起的撞击坑辐射纹死白与微反差抹平（$<0.010$ 为优，$>0.025$ 报警）；
  - **梯度峰度脆裂度指标 (GKM, Gradient Kurtosis Metric)**：提取月面内部 Sobel 梯度的皮尔逊四阶峰度，精准量化过度小波/USM 产生的人工尖锐毛刺脆裂感（$<6.0$ 为温润，$>14.0$ 报警）；
  - 在 `verify` 命令与报告中集成 DHR、CSI、GKM 联合质检输出。
- 引入月面全景马赛克拼接切片模式（`--mosaic-mode {disc|tile}`，默认 `disc`）：
  - 切片模式下强行旁路全月盘月轮 RANSAC 拟合与外太空清零，彻底根除相邻切片重叠月面地形被误杀的问题；
  - `stack` 与 `all` 子命令下切片模式自动缺省使用 `--framing max`（携带 `-maximize`），最大化保留重叠特征；
  - 后处理结束自动导出 `mosaic_tile_info.json` 切片元数据清单，与下游 `siril-mosaic` 技能零摩擦对接。
- 新增单元测试 `test_anti_ringing_damping_math`、`test_dark_halo_ratio_metric`、`test_anti_brittle_metrics_math` 与 `test_mosaic_tile_mode_pipeline`，全套 15 项测试 100% 通过。
- 新增 `requirements.txt` 声明运行时依赖（numpy / astropy / opencv-python-headless / scikit-image）。
  此前 `requirements-dev.txt` 仅含 pytest 与 PyYAML，全新环境执行 `register` 会因缺少
  `skimage` 抛出 `ModuleNotFoundError`，`import`/`postprocess` 亦会分别缺少 `astropy`/`cv2`。
- `SKILL.md` Step 1 补充依赖安装命令、Siril 1.4.4+ 前置条件与 `probe` 四项依赖校验说明。
- `RELEASING.md` 增加 `requirements.txt` 安装步骤。

## [1.0.2] - 2026-09-23

- 例行补丁升级与元数据规范维护

## [1.0.1] - 2026-09-23

- 建立治理基线：规范 frontmatter metadata 与独立发布契约。
- 引入 MTF-SNR 联合效用模型进行选帧，提供亚像素频域配准与矿物月色彩提取。
