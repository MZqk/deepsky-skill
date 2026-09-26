# Changelog

本文件记录 `deep-sky-processor` 的独立版本变更。

## [0.1.9] - 2026-09-26

对比同设备他人 M31 成品后定位并修复的两项星点链路缺陷。出发点是「亮斑数量 2698 vs 483、平均尺寸 3.9px vs 21.4px、星点密度 4426 vs 1440 /Mpx」。

- **星点检测阈值全部改为噪声锚定**（`star_tools.detect_stars_multiscale`）。旧实现有三层「相对画面内容」的阈值，全都锚定到画面里最亮的星：
  - per-scale：`max(median + 4·MAD of positive, min(p97 of positive, 0.85·factor·max))` —— `p97 of positive` 在稀疏星场里几乎等于最大值，`0.85·factor·max` 直接锚定最亮星
  - 第二道：`star_threshold · combined_response.max() · 0.12` —— M31 上算出 **0.0985**，把 3.12% 的候选集压到 0.044%
  - 峰值下限：`bg + (p99.9 − bg) · star_threshold · 0.18` —— 算出 **0.0114**，而全图 **p99 只有 0.0111**，等于只放行最亮 ~1% 的像素

  改为：per-scale 阈值 = 背景区 tophat 的稳健噪声 × 7 × 尺度系数；峰值下限 = `background + 5σ`；删除第二道阈值。**检出 115 → 1,891 个星点**（局部极大法在同一张图上的参照约 1,985），去星质量评分 0.722 → **0.901**。
  - 关键实现细节：`white_tophat` 恒非负且对纯噪声**大多数像素恰好为 0**，因此必须只取**正值部分**估噪声；对含零的整段取 `median + 4·MAD` 会塌成 0，阈值归零后整幅图连成一个连通域被当成星云亮核拒掉。
- **星点层拉伸因子提升 11 倍**（`pipeline.py` 各预设 `star_stretch_factor` 8/12/24/18 → 88/132/264/198）。旧值下星点层 p99 只有 0.0067，而合成图背景在 0.09 量级 —— 暗星整体沉进背景里看不见。新值下星点层峰值 p50 = 0.082、96.8% 的星点高于可见阈值。

**效果（M31 实测）**：星点层星数 115 → **2,029**；纯星场区星点密度 1440 → **1870 /Mpx**（参照作品 4426）；检测到的星点像素 3,485 → 82,225。新增 5 个测试（`test_star_detection_thresholds.py`），合计 256 passed / 119 subtests。

**已知未完成**：星点密度仍只有参照作品的 42%。剩余限制不在星点链路 —— 星点层本身 96.8% 已高于可见阈值，但星系区域的背景噪声 σ=0.025 把可见门限抬到 0.29，把暗星盖住了。下一步应处理**色调曲线过平**（p90/p50 为 1.76，参照 5.2）与**背景噪声**。

## [0.1.8] - 2026-09-26

修复风格定调（Phase 9a）的同类缺陷 —— 这是「只改 Lab 的 L 通道」模式第三次出现。

- **`apply_professional_style` 改为比例保持**（`style_tools.py`）。旧实现把图像转到 Lab、替换 L 通道、再把 a/b 乘 `1+color_separation`。L 被 `black_floor` 压暗后，绝对色度不变等于相对放大 —— 实测暗背景的 B/G 由 1.29 **一步跳到 3.20**，整片背景发蓝（`BACKGROUND_COLOR_CAST` 门持续触发）。改为用色调曲线得到的亮度增益作用于 RGB，`color_separation` 改为以亮度为轴的保比例饱和度提升。
- **`black_floor` 增加边界**（`_tone_curve`）。该参数是 L/100 单位的绝对量，而曝光充分的图上背景 L/100 只有 ~0.03，直接加 0.018 会把整片背景裁成纯 0（p1 掉到 2e-06，重新触发 `BACKGROUND_CRUSHED`）。限制黑位最多吃掉背景亮度的一半。

**效果（M31 实测）**：暗区 B/G 3.20 → **1.13**；`background_color_cast` 幅值 2.18 → **0.19**（低于 0.08 阈值才不触发，此处已接近）；风格输出 p1 由 2e-06 回到 **5.1e-03**。

新增 6 个测试（`test_style_color_preservation.py`），合计 251 passed / 119 subtests。

## [0.1.7] - 2026-09-26

继续修复归一化改动暴露出的下游缺陷。这一版把 M31 端到端结果从「品红/灰调」拉到与参考基准基本一致，管线状态由 `review_required` 变为 `success`。

- **拉伸改为逐像素保留 RGB 比例**（`stretch.apply_luminance_stretch`）。旧实现把图像转到 Lab、只替换 L 通道再转回，而 Lab 的 a/b 是**绝对**色度 —— 亮度抬高后色度不变等于稀释饱和度。实测核心像素 `(0.295, 0.260, 0.242)` 的 L 从 28.9 拉到 86.5、a/b 原样保留后 R/G 由 1.137 掉到 1.055；整幅 M31 的核心 R/G 从 **1.49 塌到 1.02**，画面变成灰调。改为用亮度增益作用于 RGB 后，核心 R/G 与 B/G 逐像素保持不变。
- **修复 OpenCV inpainting 的 8 位量化**（`star_tools.inpaint_telea` / `inpaint_ns`）。旧实现先把图像按自身峰值缩放到 [0,1] 再取整成 uint8，而线性深空数据的背景（0.0017）相对峰值（0.97）只占 **0.45/255**，四舍五入后整片背景归零 —— 实测修复后 **93.7%** 的像素变成纯 0，去星质量评分 0.001、每次必然回退。改用 32F 后背景完整保留，去星评分 **0.719（good）**、误伤比 0.802 → **0.0**。
  附带发现：OpenCV 的 `INPAINT_TELEA` 在 32F 下数值不稳定（同样一张图输出 `[-1.41, 1.37]` 越界值），而 `INPAINT_NS` 能精确插值回背景电平，因此 float 通路统一走 NS。

**效果（M31 实测，对比参考基准）**：

| 指标 | v0.1.4 | v0.1.7 | 参考基准 |
|---|---|---|---|
| 管线状态 | review_required | **success** | — |
| `star_area_ratio` | 0.04646 | **0.00168** | 0.00237 |
| 核心 R/G | 1.003 | **1.468** | 1.446 |
| 核心 B/G | 0.980 | **0.788** | 0.708 |
| 背景 p1 | 3.0e-06 | **5.9e-03** | — |
| CP1 四角均匀度 | 2.49 | **1.68** | — |
| 去星评分 | 0.045 | **0.719（good，不再回退）** | — |

新增 14 个测试（`test_stretch_color_preservation.py`、`test_inpaint_precision.py`），合计 245 passed / 110 subtests。

## [0.1.6] - 2026-09-26

修复 DBE 归一化改动暴露出的三处下游缺陷 —— 它们此前被「整幅图被放大 37 倍」掩盖，归一化修正后才显现。

- **`color_conv` 的 Lab 转换精度**（影响 8 个模块）。旧实现用 `cv2.cvtColor(..., COLOR_RGB2Lab)`，而 OpenCV 对 float32 输入会把 sRGB→线性 一步按 8 位量化：实测 `RGB=0.001` 时 `L` 直接为 **0**，一幅线性深空图的 Lab 往返会让 **23.6% 的像素变成纯 0**。深空线性数据的背景常落在 1e-3 量级，这个损失是致命的。改为纯 NumPy 实现（sRGB→线性→XYZ→Lab，D65），往返误差 ~1e-8，与 skimage 一致。受影响的模块：`stretch`、`enhance`、`style_tools`、`sharpen`、`star_tools`、`starless_multiscale`、`analyze`、`color_tools`。
- **`background_neutralize` 不再重复扣黑**：改为只扣除**通道之间**的背景差异，保留共同黑位。DBE 阶段已经留了 pedestal，旧实现再把各通道中值整体减掉等于重复扣黑 —— 实测把 **90.2%** 的画面压成纯 0。
- **`remove_green_noise` 按亮度加权**：Lab 的 a/b 在近零亮度处不稳定，对线性阶段的暗背景施加色度修正会放大误差。现在按 `L` 渐入，SCNR 只在中高亮区生效。
- **`is_very_dark` 增加「峰值必须低」条件**：原判据只看中位数与 p99，会把高动态范围天体误判成欠曝数据。线性数据归一化到峰值后，亮核很小的星系（M31 的 p99≈0.011）在分位上确实很低，但峰值接近 1.0，说明曝光充分。

**效果（M31 实测）**：`star_area_ratio` 0.04646 → **0.00168**（原始线性图上的参照值 0.00237，已对齐）；CP1 四角均匀度 2.49 → **2.13**；DBE 与校色阶段的零值像素 28~44% → **0%**；高光裁切 1.26% → **0.02%**。新增 10 个测试（`test_color_conv_precision.py`），合计 231 passed / 107 subtests。

**已知未完成**：归一化修正后，`galaxy_core` 风格的 `black_floor=0.018`（L/100 单位的绝对常数）与拉伸的 `target_bg` 需要重新标定 —— 实测最终图中位数 0.0085，低于 `BACKGROUND_LOW` 门限 0.025；核心/盘面色比 R/G≈1.03 明显低于参考基准的 1.45（此项 v0.1.4 已存在，非本次引入）。属参数标定，待单独处理。

## [0.1.5] - 2026-09-26

修复 DBE 归一化 —— 它是复查清单第 2、3 项的共同根因，也是上一版「去星真实误伤 37%」的成因。

- **白点改为锚定真实峰值**（`gradient_removal.normalize_background_subtracted`）。旧实现用 `p99.7` 当白点，但星系/星云的亮度跨数量级，该分位落在很暗的外缘 —— 实测 M31 的 p99.7 比峰值低 **36.6 倍**。用它当白点等于把整幅图放大 ~37 倍，噪声随之被放大到与星点可比：星点检测于是把噪声当成星，`star_area_ratio` 从 0.24% 虚高到 **4.5%**（置信度还报 0.97），同时把明亮星点与核心裁到 1.0、摧毁检测所依赖的动态范围。
- **黑点改为背景的稳健中心**。旧实现先 `np.clip(current, 0, None)` 再减去「正值像素的 p0.3」，等于连续两次抬高黑位，把噪声底整个裁掉（实测 28~44% 的画面变成纯 0），暗弱外晕随之丢失。
- **新增 pedestal**（默认 3σ，上限 0.05），让背景噪声完整落在 [0,1] 内而不是被裁到 0。这同时让下游所有「背景亮度分位」类判据重新有意义。
- 新增 `gradient_removal.estimate_background_noise()`（稳健噪声尺度，**排除恰好为 0 的像素**，否则 DBE 后 MAD 会退化成 0）。
- **修正 `is_very_dark` 误判**（`pipeline.py`）：原判据只看中位数与 p99，会把高动态范围天体当成欠曝数据。线性数据归一化到峰值后，亮核很小的星系（M31 的 p99≈0.011）在分位上确实很低，但峰值接近 1.0，说明曝光充分。新增「峰值必须低」这一必要条件，避免误选 `very_dark` 拉伸导致整幅图过曝。

**效果（M31 实测）**：`star_area_ratio` 0.04646 → **0.00249**（原始线性图上的参照值为 0.00237，已基本对齐）；检测到的星点像素 96,329 → **5,156**；CP1 四角均匀度 2.49 → **1.11**；背景 p1 从 3e-06 回到 **3.22e-02**，`BACKGROUND_CRUSHED` 门不再触发；高光裁切 1.26% → **0.00%**。

新增 11 个测试（`test_dbe_normalization.py`），合计 221 passed / 99 subtests。

## [0.1.4] - 2026-09-26

修复去星质量评估（`star_tools.estimate_star_removal_quality`）—— 三个子指标此前都退化成了常数，导致去星链路处于「静默失效」状态：无论去星做得好坏，分数都恒为 0.5、`needs_starnet_plus` 恒为 True，每次必然回退。

- **`nebula_damage_ratio` 不再除以噪声底**：旧实现除以最暗 30% 像素的均值，而该值等于背景噪声底（实测约 1e-4），任何微小扰动都会让比值冲到 ≈1.0（实测报 0.972），与真实误伤程度无关。改为在**重平滑（sigma=8）后的延展结构**上比较，并扣除去星本身移除星点光通量带来的 ~10% 系统性下降。
  同时修正掩膜口径：旧掩膜按原始亮度取"最亮 25%"，而星点正是最亮的部分，于是"把星去掉"这件事本身被算成误伤（实测报 0.17）。
- **`residual_star_fraction` 改为「亮且紧凑」判据**：旧实现用 `max(阈值, 0.1)` 这个绝对亮度下限，任何亮于 0.1 的像素都算残留星，星系/星云本体被计入（恒等操作也能报出残留）。改为高通响应 + 噪声相对阈值，只识别点状结构。`needs_starnet_plus` 的残留阈值由 5% 收紧到 2%（星点掩膜通常只占 1~5%，旧阈值下恒等操作都能蒙混过关）。
- **`high_gradient_ratio` 改为「去星新引入的硬边」**：旧判据 `梯度 > p95(梯度) * 0.5` 用图像自身分布定阈值，对任何图像都必然命中 10~20% 的像素，等于恒定扣满该项惩罚。改为比较去星前后同一位置的梯度（原图在星点处本就有大梯度，好的修复只会让它变小），并用绝对噪声尺度锚定。
- 新增 `_robust_noise_scale()`：稳健噪声尺度，**排除恰好为 0 的像素** —— DBE 之后大片背景被 clip 到 0，直接取 MAD 会退化成 0，使所有"相对噪声"阈值失效。
- 评分权重按「可接受上限」重新标定：残留 2.5%、误伤 22%、新硬边 6.7% 分别扣满该项。

**效果**：合成场景上「完美去星」由 0.643 提升到 **0.996（good, 无需 StarNet）**；「恒等（完全没去星）」正确判为残留并标记 `needs_starnet_plus`；「压暗 40%」「过度平滑」均判为 poor。真实 M31 上分数由 0.5 变为 **0.045**，且回退理由从「指标失效」变成「真实误伤 37%、星系核心细节仅保留 20%」—— 指标现在说的是实话。

新增 12 个测试（`test_star_removal_quality.py`），合计 210 passed / 99 subtests。

## [0.1.3] - 2026-09-26

以真实样本（DWARF mini / M31 堆栈 FITS）做端到端测试后定位并修复的色彩管线缺陷。原输出为「品红核心 + 黄绿盘面 + 红褐背景」，现恢复为暖黄核心 + 中性暗背景。

- **FITS BZERO 不再被应用两次**（`fits_io._read_fits`）：astropy 在 `fits.open()` 阶段已按标准应用 BSCALE/BZERO，旧代码又手动 `data * bscale + bzero` 一次，给整幅图加上 50% 量程的亮底座，归一化背景从 0.1700 被抬到 0.4468。header 原值仍记录在 `scale_info`/`meta` 中供溯源。**影响本机 `~/SeeStar/` 下 77%（351/453）的 FITS 文件。**
- **DBE 改为逐通道独立估计背景**（`gradient_removal.remove_gradient`）：旧实现用 `np.mean(image, axis=2)` 得到一个亮度背景面并复制到 3 个通道，只要背景有轻微色偏就会把较暗通道减成负值并 clip 到 0（实测 B 通道 99.44% 变负，背景 R/G 从 1.01 飙到 245）。灰度（2D）路径保持逐点等价。
- **背景中性化改为加性逐通道黑点**（`color_tools.background_neutralize`）：旧的乘性增益 `bg_mean / max(bg_ch, 0.001)` 在近零通道上会算出约 96× 增益并全局乘到天体本体上，把暖核染成冷核（核心 B/G 0.75 → 2.50）。加性平移不可能放大任何通道。
- **修复背景中性化静默跳过**：掩膜由严格 `<` 改为 `<=`，并在命中不足时按亮度取前 k 个兜底。此前当大量像素恰好等于分位阈值（DBE 之后很常见）时掩膜为空集，整步被静默跳过。
- **白平衡改为真实星采样且保守施加**（`color_tools.white_balance_from_stars`）：旧版名为 `from_stars` 实际用全图均值做 gray-world，并带 R×0.9 / B×1.1 的硬编码偏置。新版用 `_reference_star_mask` 采样高局部对比、未饱和、且不在延展主体上的像素，增益取「几何平均 / 通道值」并限幅到 [1/1.25, 1.25]，样本不足时优雅跳过。
  新增 `strength` 参数，**默认 0.35（保守）而非 1.0**：把参考星强行拉成中性只在「星应当是白的」这一前提成立时才对。低银纬视场（M31 受银河尘埃红化）或相机光谱响应偏离 CIE 时该前提不成立 —— 实测把 1141 颗场星拉中性会把星系盘从 R/G≈1.55 压到 ≈1.01，抹掉天体本身的暖色。默认只施加 35% 校正量，既能去掉明显的仪器色偏，又不改写天体固有颜色。
- **去星回退时保留实测 FWHM**（`star_tools.separate_stars` / `_safe_star_removal_fallback`）：回退报告此前不含 `estimated_fwhm`，导致 `pipeline` 退回硬编码的 4.0px（实测约 7.5px），星点检测尺度错位使 `star_area_ratio` 从 0.0014 虚高到 0.15。`pipeline` 侧调用形式不变。
- **星点检测阈值改为噪声相对**（`analyze._analyze_starfield`、`recognize.analyze_starfield`）：绝对下限 0.01/0.02 隐含「背景接近 0」的假设，改为 `median + 3·MAD`。
- **新增拉伸塌缩检测与保守回退**（`stretch.is_stretch_collapsed`）：`masked_ghs` 黑点估计失准时会输出被压平的图（实测 span 0.145 → 0.014），此前无任何检查，直接交给下一阶段的 CLAHE 兜底。现在检测到塌缩会退回保守 `masked` 拉伸，结果记入 `result["stretch_recovery"]`；仍失败则追加 `STRETCH_COLLAPSE` 警告。注意 `core_ratio` 判据仅在参考图 p50 > 0.01 时启用（背景被 clip 到 0 时该比值会被 epsilon 主导而误报）。
- **空星点层显式跳过合成**：去星回退会产出全零的 `04_stars_linear.tif`，此前 `star_combine` 静默空操作且无任何记录。现在跳过合成并在 `result["star_combine_skipped"]` 与 `STAR_COMBINE_SKIPPED` 警告中说明原因。新增 `stellar_recompose.is_stars_layer_empty()`（未改动 `validate_stars_layer` 的既有行为）。
- **新增背景色偏质量门**（`agent_protocol.evaluate_quality_gates` + `quality_metrics.calculate_metrics`）：此前 7 道门无一检查每通道背景色偏，一幅整体全绿的 M31 也能拿到 `status: success`。新增 `BACKGROUND_COLOR_CAST`（阈值 `max(|R/G-1|,|B/G-1|) > 0.08`），并引入 `escalate` 标志使其**只记录不升级 status**。指标在「三通道都有信号」的背景像素上测量，避免背景被压死后报出 1e5 量级的假数值。
- 行为变更提示：`result` / `manifest.json` 新增 `stretch_recovery`、`star_combine_skipped` 字段；`quality_gates` 每项新增 `escalate` 布尔字段；`metrics` 在可测量时新增 `background_channel_medians` / `background_color_cast` / `background_color_cast_magnitude`。下游若做精确字段匹配需同步。
- 新增 44 个测试（`test_fits_io_bzero`、`test_gradient_removal_channels`、`test_color_calibration`、`test_star_fwhm_reporting`、`test_star_combine_empty_layer`、`test_quality_color_gate`、`test_stretch_collapse`），合计 198 passed / 99 subtests。

## [0.1.2] - 2026-09-26

- 修复 M42 拉伸被双重缩放：新增 `scripts/target_rules.py` 作为目标规则的单一事实源，拉伸因子改为"单一归属"（优先级 M42 > 发射星云 > 反射星云 > 暗星云），M42 不再叠加发射星云的 ×0.85；锐化 ×0.7 等非拉伸修正仍生效。
- 目标名称与类型判定收敛到 `fits_io.LOCAL_CELESTIAL_DB`：删除 `pipeline.py` 与 `starless_profiles.py` 中散落的硬编码名称清单；`normalize_target_name()` 新增由库标准名自动派生的反向同义词表，并补录 `PLEIADES CLUSTER`、`GREAT ORION NEBULA`、`ROSETTE NEBULA`、`WITCH HEAD NEBULA`。
- 名称匹配由子串改为精确匹配：修复 `M8` 误匹配 `M81` 导致星系被当作发射星云的问题。
- 修复带分隔符写法（`M 42`、`NGC 6888`、`ngc-6888`）无法激活安全规则的缺陷；`masked_ghs` 的 M42 核心保护改用同一判据。
- 星团安全规则补全：新增 `pre_denoise_chroma` ≤0.03、`final_denoise_chroma` ≤0.015 与 `saturation` ≤1.25 上限；星团按名称识别时也能正确移除星点链路。
- 只给 `--target-name` 时，星系/行星状星云/暗星云规则现在也会生效（此前仅 `--target-type` 可用）。
- 修复 Phase 8b 日志硬编码"中央眉月星云"，改为输出实际目标；修正 Phase 8d 注释编号与 `enhance.py` docstring。
- 注意：`safety_log` / `manifest.json` 的 `safety_rules_applied` 新增两条星团日志行（降噪保守、饱和度上限），下游若做精确字符串匹配需同步。

## [0.1.1] - 2026-09-23

- 例行补丁升级与元数据规范维护

## [0.1.0] - 2026-08-28

- 为现有真实性约束、分阶段审查和图像处理能力建立首个治理版本基线。
- 登记独立许可、开发环境和发布流程。
- 将 OpenCV 约束在兼容 `numpy<2.0` 的 4.11 系列及更早版本，确保 Python 3.12 环境可解析。
