# 保真度门禁阈值标定记录

`fidelity_gates.py` 的 5 道门禁阈值来源。本文记录实测分布、标定方法与两个被
实测**否决**的设计假设 —— 后者是本文档最重要的部分。

复现命令：

```bash
# 1. 跑管线并保留中间件（--keep-all 保留 05_display_reference.tif）
.venv/bin/python scripts/pipeline.py <master> /tmp/calib/<name>/out.jpg \
    --target-name <name> --keep-all --save-intermediates \
    --low-memory-threshold-mpix 99.0 \
    --work-dir /tmp/calib/<name>/work --result-json /tmp/calib/<name>/result.json

# 2. 采集分布并定阈值
.venv/bin/python scripts/calibrate_fidelity_gates.py \
    --corpus ngc6888 M31 M45 M17 --calib-root /tmp/calib \
    --json /tmp/calib/calibration.json
```

## 标定口径

- **降采样 1400px**（`GATE_PREVIEW_MAX_SIDE`），与运行时同几何。
  `downsample_for_gates` 带 `anti_aliasing=True`，会平滑高频噪声，1400px 上的
  σ 系统性低于全分辨率。**标定与运行必须同口径**，否则门会过松。
- 参考图 = 同一份 `_pre_stretch_input` 以固定 `pi_mtf` 保守曲线渲染的
  `05_display_reference.tif`，候选 = 最终采纳的拉伸结果。两者唯一差异是曲线。
- 母版来自 `/Users/mz/SeeStar/Starun/`。

## Corpus（4 份母版 / 3 种目标类型）

| 母版 | 目标类型 | 拉伸方法 | 参考产物 | bg_px | sig_px |
|---|---|---|---|---|---|
| NGC6888 | emission_nebula | `masked_ghs_standard` | `05_display_reference.tif` | 275450 | 644597 |
| M31 | galaxy | 回退（`rejected_recovery`） | `05_rejected_recovery.tif` | 275451 | 149475 |
| M45 | open_cluster | `pi_mtf` | `05_display_reference.tif` | 275450 | 500195 |
| M17 | emission_nebula | `masked_ghs_standard` | `05_display_reference.tif` | 276850 | 616037 |

M31 走的是回退路径（`05_display_reference.tif` 未落盘），标定器改用
`05_rejected_recovery.tif` 作参考；此时管线自身**跳过**了保真度门禁（跨域不可比），
标定器补测了这对图。

## 健康组实测分布

| 母版 | `background_sigma` | `relative_noise_gain` | `hue_drift_p95` | `subject_chroma_ret` | `chroma_noise_gain` |
|---|---|---|---|---|---|
| NGC6888 | 0.009190 | 1.1510 | 0.001450 | 不可测 | 不可测 |
| M31 | 0.000060 | 1.0102 | 0.000130 | 不可测 | 不可测 |
| M45 | 0.004226 | 1.0000 | 0.000000 | 1.000000 | 1.000000 |
| M17 | 0.005550 | 1.1166 | 0.001233 | 1.015534 | 0.995055 |

几何统一为 1400×787（M17 为 1400×791）。

## 劣化对照组

注入幅度按**相对背景中位数**（不是绝对值，理由见下）。每档 4 个样本。

| 对照 | `relative_noise_gain` | `hue_drift_p95` |
|---|---|---|
| 注噪 0.30× | 1.273 / 1.600 / 1.562 / 1.503 | 0.060 / 0.156 / 0.111 / 0.081 |
| 注噪 0.60× | 1.399 / 1.600 / 1.600 / 1.600 | 0.112 / 0.331 / 0.197 / 0.148 |
| 饱和度 ×1.15 | 1.159 / 1.117 / 1.033 / 1.157 | 0.034 / 0.008 / 0.017 / 0.038 |
| 饱和度 ×1.45 | 1.189 / 1.345 / 1.117 / 1.264 | 0.103 / 0.024 / 0.051 / 0.114 |

健康组上界（`relative_noise_gain` 1.151、`hue_drift_p95` 0.00142）与劣化组下界
（1.033 / 0.008）**不重叠**，分离清晰。

## 最终阈值

| 门禁 | degraded | hard | 依据 |
|---|---|---|---|
| `BACKGROUND_NOISE_BOUNDED` | — | — | **实测否决**，见下 §1 |
| `BACKGROUND_RELATIVE_NOISE_BOUNDED` | 1.1407 | 1.60 | 健康 p90 = 1.1407；hard 取硬上限，注噪组全部 ≥1.27 |
| `HUE_PRESERVED` | 0.0040 | 0.037926 | 饱和度 ×1.15 劣化组最轻档 0.0081，取其下方留余量 |
| `SUBJECT_CHROMA_VISIBILITY_PRESERVED` | 0.95 | 0.85 | **无有效劣化对照**，见 §2 |
| `BACKGROUND_CHROMA_NOISE_BOUNDED` | 1.02 | 1.12 | 健康组上界 1.0000；饱和度 ×1.15 组 1.14–1.15 |

只有 `BACKGROUND_RELATIVE_NOISE_BOUNDED` 具备升级能力（`ESCALATING_CODES`），
且 `PROVISIONAL = False`（`CALIBRATION_CORPUS_SIZE = 3 ≥ 3`）。

---

## 被实测否决的两个设计假设

### 1. 绝对 σ 不可用统一阈值 → 降级为只记录

**原设计**：`BACKGROUND_NOISE_BOUNDED` 用绝对 σ（MAD），阈值取健康组 p90 /
劣化组下界。

**实测否决理由**：健康组跨目标相差约 **150 倍** —— M31 是 6.0e-05，NGC6888 是
9.2e-03。σ 随背景亮度量级漂移（星系目标背景压得极暗，绝对噪声自然极小），
单一阈值必然要么全静默、要么全误报。

**处置**：`degraded` / `hard` 均置 `None` ⇒ 该门恒为 `uncalibrated`，记录数值
但不判定。噪声判定全部交给相对量门 `BACKGROUND_RELATIVE_NOISE_BOUNDED`。

这是「门禁阈值按灾难形态标定会留静默通过区」教训的**正向应用**：不是把阈值
调到勉勉强强，而是实测证明该形态不成立，就改设计。

### 2.「压主体」不是主体色度保留比的有效劣化对照

**原设计**：对主体区做 5% / 12% 亮度压缩，观察
`subject_chroma_snr_retention` 下降，用它定 hard 线。

**实测否决理由**：压缩后 retention 反而**略升**（1.006 / 1.018）。原因是该度量
= `cand_snr / source_snr`，分子分母都按同一掩膜、同一主体区域计算，主体与背景
色度同比缩小 ⇒ 比值不变。

同理，**整体去饱和对该度量也是不变量**（实测 retention = 1.0000）—— 这是
SNR 保留比作为「相对色度保真」度量的定义，不是 bug。`tests/test_fidelity_gates.py`
里两个用例分别锁住「只压主体 ⇒ 下降」与「整体去饱和 ⇒ 不变」两条性质。

**处置**：无法从现有劣化手段定出数据驱动的 hard，改用保守值 0.95 / 0.85
（沿用 starun 侧专业基线 `HARD_FLOOR_CHROMA_SNR = 0.95`），只在主体色度严重
流失时才提示。该门在 v1 不升级。

---

## 标定过程中发现的第三个坑：绝对噪声注入被分母抵消

最初劣化对照用**绝对**噪声幅度（`+ N(0, 0.30)`）。实测 NGC6888：

| | median | σ | σ/median |
|---|---|---|---|
| 原始 | 0.0279 | 0.0095 | 0.342 |
| 绝对注入 0.30 | 0.1318 | 0.0699 | 0.530 |

注入把 median 也抬到近 5 倍，相对噪声只从 0.342 升到 0.530，**远够不到 1.60
的 hard 线**。改为**相对注入**（`N(0, 0.30 × 背景中位)`）后增益才达到
1.27–1.60，门禁开始正确区分健康与劣化。

教训：标定劣化对照时，注入方式必须与被测量的**量纲**匹配。测相对量就用相对
幅度注入，否则分母会被一起抬高，测出「劣化不劣化」的错误结论。

---

## 复核清单

标定后必须验证三件事（本轮已全部通过）：

| 检查 | 结果 |
|---|---|
| 4 份健康母版跑门禁 | 全部 `status=success`，无 escalate |
| 健康态 vs 注噪 0.3× | NGC6888 1.264→degraded、M17 1.502→degraded、M45 1.560→degraded、**M31 1.600→failed 升级** |
| 健康态 vs 饱和度 ×1.45 | `HUE_PRESERVED` 报 warning（0.103 / 0.114） |

## 已知局限

1. **corpus 只有 4 份 / 3 种目标类型**。`planetary_nebula`、`dark_nebula`、
   `reflection_nebula`、`globular_cluster`、`supernova_remnant` 未覆盖。
   随样本积累必须复核。
2. **3 份母版的色度门不可测**（`insufficient_source_chroma_support`）—— 背景与
   主体色度差异太小，色度 SNR ≈ 0。这不是门禁缺陷，而是如实记录「测不到」。
3. **M31 的绝对 σ 异常低**（6.0e-05）主导了 `background_sigma` 的分布形态，
   进一步支持了「绝对量不可用」的结论。
4. 色度三门在 v1 不升级。若要开启，需先按 `target_type` 分档建立色度阈值表
   （A 侧 10 类类型），当前样本量不足以支撑。

## 附：CLAHE 两道运行期守卫（Phase 6）

这两道守卫不是 `fidelity_gates.py` 的门禁，而是 Phase 6 决定是否运行 CLAHE 的
运行期判据，标定依据同源，故记录于此。

### 守卫一：噪声主导时削掉 CLAHE（`texture_snr`）

`decide_enhance_mode` 的 span 分支会为「层次不足」的图选 `clahe_only`。但**光看 span
分不出来**该不该跑 CLAHE —— 实测同一批数据里 `span = 0.2187` 的几张是有结构的
（跑 CLAHE 无害），而 `span = 0.0814` 的才是**噪声主导**（跑 CLAHE 出蜂窝斑块）。

判据必须是 SNR。Phase 6 现算 `texture_snr = 星云区高通能量 / 背景区高通能量`
（背景区只有噪声，即噪声地板）：

| `texture_snr` | 含义 | 决策 |
|---|---|---|
| `< 1.7` | 星云区不比噪声多出结构 | **削掉 CLAHE**（`both`→`hdr_only`，`clahe_only`→`none`） |
| `≥ 1.7` | 有可揭示的纹理 | 保持原决策 |

实测分离度：出伪影的输入 **1.24~1.35**，干净的 **2.32~5.26**。触发时日志会打印
「星云区纹理/背景噪声 = X < 1.7，判定为噪声主导」并给出降级前后的模式。

**为什么必须跳而不是调参**：CLAHE 的局部直方图均衡会把噪声一起均衡掉 —— 实测把
星云区高通能量放大 **4.24×**，成片出现灰色蜂窝斑块（`NGC2237_fixed.jpg` 那种）。
调小 `clip_limit` 或换 `kernel_size` 都只是缓解；噪声主导时它没有任何可揭示的结构。

### 守卫二：CLAHE 高频放大实测（`CLAHE_MAX_HF_AMPLIFICATION`）

`clip_limit` **不是**可用的强度旋钮 —— 在**窄直方图**上它会饱和。实测某极暗发射星云
（span=0.39）上 `clip_limit` 从 0.0003 到 0.003 给出**逐位相同**的输出（HF 放大恒为
1.85×、p75 恒为 0.440），只有到 0.006 才变化。**所以只能二选一，不能"调小一点"。**

因此 Phase 6 在下采样预览（≤640px）上**实测** CLAHE 会把星云区高频放大多少倍，
超过 `CLAHE_MAX_HF_AMPLIFICATION`（默认 **1.5**）就跳过，并把实测值写进
`hdr_report.clahe_hf_amplification` 供审计。

实测收益（真实母版）：跳过 CLAHE 后成片 **p75 由 0.381 降到 0.173（−55%）**、
**HF 比由 6.74 降到 3.62（−46%）**，大幅贴近参考成片。

> ⚠️ 该阈值目前只在**单一案例**上标定过（该例实测放大 1.92×）。它是保守取值，
> 随样本积累应复核。
