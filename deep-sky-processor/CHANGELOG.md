# Changelog

本文件记录 `deep-sky-processor` 的独立版本变更。

## Unreleased

- Share explicit DWARF mini/II/3 selected-filter mappings and manufacturer main-passband provenance with starun-siril; keep unknown selections, pixel geometry and spectral-role limits.

- Keep anchored starless subject gains over a conservative MTF background with a smooth signal-confidence blend. Protect residual point-source footprints locally, exclude them from diffuse subject statistics, and preserve compact planetary nebulae. Retain existing acceptance thresholds and recovery paths.
- Default new pipeline calls and Agent sessions to stretch policy 3. Every valid linear starless source uses the same auto dispatch; explicit methods remain authoritative and old sessions retain their recorded policy.
- Add `stretch_subject_midtones` (0.25 by default; 0.20 for reflection/dark nebulae; null disables anchoring), continuous background/subject anchoring and bounded native-size residual gains. Unavailable or missed anchors propagate review issues; rejected candidates retry once with a reduced subject target before safe MTF recovery.
- Replace starless luminance and global color hard ceilings with a pixel-local, hue-preserving rational highlight shoulder. Preserve extra channels, zero pixels and native image geometry; expose actual anchors, targets, shoulder parameters and parameter consumption.
- Add curve, color, fallback, version, fingerprint and API/CLI/Agent regression coverage. Visual acceptance still requires real-image review, including amplified star-removal rings and footprints.

- Galaxies now run star separation by default under a scaled core-protection radius instead of being blocked outright; globular/open clusters and double stars still skip separation. This releases the highlight headroom that sparse bright stars otherwise consume.
- Add `background_compressed` stretch, used only when the stretch source is a starless layer. The with-stars `target_bg` calibration is relative to the full-frame range, so it does not transfer to a starless frame where the background sits much higher; on the M33 master this raises subject/background from 1.28 to 3.70 while keeping 0% of samples at or above 0.995.
- Fix galaxy target localisation: galaxies now use star-suppressed diffuse detection instead of the compact high-pass detector, which locked onto the brightest star in dense fields (M33 master returned a star at peak/sky = 472x instead of the galaxy at frame centre). The error propagated to the DBE exclusion zone, the saturation mask and Phase 8b local enhancement, all of which ran on empty sky.
- Share starless gain solving across callers; require final convergence and reject new clipping instead of silently clipping repaired pixels.
- Distinguish raw nonpositive pixels from background-relative zero fractions and reject nonfinite channel inputs.

## [0.1.41] - 2026-10-07

- Compress `SKILL.md` from 1151 to 576 lines by moving implementation rationale, calibration evidence and gate definitions into the references they belong to. No behavioral, parameter or threshold change: every load-bearing token, numeric value and gate code is preserved either in `SKILL.md` or in a reference.
- Relocate the FITS I/O precision narrative, star-detection engine internals, StarNet payload-domain/stride detail, GHS anchor semantics, traceability rules, CLAHE guards and the named/fidelity/config-fingerprint/failure-policy gate definitions to `quality_assessment.md`, `agent_protocol.md`, `cv_guardrails.md`, `linear_stage_processing.md`, `external_tools.md` and `fidelity_gate_calibration.md`.
- Add a `##`-level table of contents; keep the command-level instructions (phase loop, gate-code remediation table, CLI parameters, authenticity red lines) in `SKILL.md`.
- Record the saturation channel-ratio measurements in `ai_common_pitfalls.md` and the C50 per-image parameters in `case_ngc6888_rgb.md`, so the compressed prose does not lose its evidence trail.

## [0.1.40] - 2026-10-07

- Add reference-integrity validation (dangling paths, unreachable references), correct the historical 0.20 damage gate, index the star-damage calibration record, and fix the stale B/G saturation measurement.

## [0.1.39] - 2026-10-07

- Fix bare `python` to `python3` in 49 documented commands, declare Pillow/threadpoolctl, warn on threadpoolctl fallback, correct runtime provenance docs, add allowed-tools.

## [0.1.38] - 2026-10-04

全部档 `damage_limit = None`，去星误伤门禁走恒 `uncalibrated` 路线。
**误伤读数照常计算并记录，但不参与判定。**

### 依据

v0.1.37 的 V5 实测：`healthy_ceiling = 0.5371 > degraded_floor = 0.0789`
⇒ **夹逼区间为空**，`emission_nebula` 档 `not_lineable`。

进一步核查确认这不是「再调参数」的问题，而是**判据的绝对刻度不适用于
跨样本比较**：

- 健康基线在样本间跨 **10 倍**（0.0357~0.3580）；
- 而「最轻劣化」相对各自健康态只抬高约 **0.033**（中位）；
- ⇒ 健康组内部方差（0.3223）比「健康到劣化的间隔」（0.0271）**大 12 倍**。

### 为什么是「全部档」而不是只改 emission_nebula

| target_type | 标定样本 |
|---|---|
| `emission_nebula` | 8 份（5 可测）⇒ 不可定线 |
| `galaxy` / `planetary_nebula` / `reflection_nebula` / `dark_nebula` / `supernova_remnant` / `wide_field` | **零样本** |

零样本档同样持有 `damage_limit=0.20` 并**照常判定** ——
按同一逻辑它们也是「未标定」。只改一档等于把同一个问题留在别处。

**诚实的后果**：`galaxy` 等档从此不再拦误伤。接受它是因为
「用一个没有依据的 0.20 拦住健康数据」比「不拦」更坏 ——
v0.1.33 实测 NGC6888 被单指标误拒（damage 0.516）正是这种危害。

### P0：`None` 会抛 TypeError，不是静默降级

`float <= None` 在 Python 是 `TypeError`。5 个消费点逐个处理
（**顺序必须是先消费点、后默认值**，反了就是全线崩）：

| 位置 | 处理 |
|---|---|
| `star_tools._star_removal_gate` | 新增第四态 `uncalibrated` |
| `star_tools._record_gate_state` | 第四态文案；`hard_failed` 也补文案（否则失败原因被吞） |
| `star_tools.starnet_reject_note` | 「接纳线未标定」而非「接纳线 None」 |
| `calibrate_star_damage_gate.replay_gate_states` | 跳过硬线计算并明说原因 |
| `star_removal_profiles.strictest` / `apply_profile` | `None` 不可被具体值覆盖 |

### 新增第四态 `uncalibrated`

| 状态 | 含义 | 排查方向 |
|---|---|---|
| `unavailable` | 这**张图**测不了（`separation < 1.30`） | 换数据 |
| `uncalibrated` | 判据**能测**，但阈值没定 | 补标定 |

⚠ **不复用 `advisory`**（已被 `measurable=False` 占用）——
混用会让报告里「阈值没定」与「读数不可用」无法区分。

**质量分仍参与判定**：`repair_quality_score` 有实测依据
（残留 2.5% / 误伤 22% / 硬边 6.7% 三档扣满），
应继续拦住「去星完全没生效」这类硬失败。

### 棘轮语义：`None` 是「无阈值」，不是「无限松」

`apply_profile(profile, 0.25, 'damage_limit')` 原会静默把 `None` 变成
0.25 —— 那等于把「没有阈值」悄悄换成「一个来源不明的阈值」，
比标不出来更危险。现在保持 `None` 且记 `bound=True`，
让「试图定值」在报告里可见。

### 报告显式化

`star_removal_profile` 新增 `damage_limit_calibrated: false` 与
`uncalibrated: ["damage_limit"]` —— 否则 `applied` 里的 `null`
会被读成「阈值是 0」或「阈值极宽松」，方向完全相反。

### Verification

- 全量测试 **1068 passed / 0 failed**。
- `validate_repository.py` exit=0。
- **端到端复跑 NGC2237**（真实 StarNet2 产物）：

```
source=starnet  accepted=true  quality=0.542  residual=0.0
nebula_damage_ratio = 0.2983          ← 仍被记录
gate_state = uncalibrated
gate_note: 误伤 0.2983 已记录但**不参与判定**：该目标类型的 damage
          阈值尚未标定（跨样本口径跨度达 10 倍，夹逼无解）
star_removal_profile.damage_limit_calibrated = false
star_removal_profile.uncalibrated = ['damage_limit']
```

### 不变的东西

`min_subject_separation`（1.30）、damage闭式、`q` 定义、E 型红线、
`coverage ≡ 0.25` —— 这些都已被独立验证，**不受阈值缺失影响**，
一起改会让那些验证全部失效。

### 一个操作事故（记录以防重踩）

批量改 `star_removal_profiles.py` 时，前 3 处改动因第 4 处
`assert` 失败而**整体回滚**（Python 脚本只在最后 `write_text`）。
随后用 `sed` 修一个不存在的「拼写错误」，把第 256 行
`TIGHTER_IS_HIGHER` 改成了 `TIGHTER_IS_HIGTER` ⇒ `NameError`。

教训：**批量文本改动要么逐处校验、要么整体幂等重写**；
「先改几处、验证后再改下一处」的写法在中途失败时会留下
「以为改了其实没改」的状态。

## [0.1.37] - 2026-10-04

V5 定线：改相对倍率网格 + 修正双向夹逼方向。
**结论：`emission_nebula` 档不可定线**（结构性，非数据噪声）。**仍未放宽任何阈值。**

### 阻塞发现：劣化组与健康组重叠

A型网格当时是**绝对 q 阶梯**（使 `q'` 落在 0.90/0.855/…/0.45，
**与样本无关**），而真实健康样本的 `q` 本身就在 0.54~0.86 ——
**去星必然带走主体光通量**，真实 StarNet2 产物的 `q` 天生 < 1。

⇒ 两者交错，**4/5 份样本的「劣化」比「健康」还轻**：

| 样本 | q_healthy | 绝对阶梯最轻档 | 健康态 damage |
|---|---|---|---|
| ngc6888 | 0.6597 | 0.0450 | 0.2403 |
| M8 | 0.5420 | 0.0450 | 0.3580 |
| NGC2237 | 0.6017 | 0.0450 | 0.2983 |
| IC434-S50 | 0.8643 | 0.0450 | 0.0357 |
| C50-S50 | 0.7149 | 0.0450 | 0.1851 |

### 顺带发现：v0.1.36 的 §4.4 公式方向是反的

```python
damage_limit = min(劣化组最轻档, 健康组上界 × relax)   # ← 错
```

`min()` **恒取劣化侧**（0.0450），而它比健康态（0.185~0.358）还低
⇒ 定出的线会**拒绝几乎所有健康数据**。

正确方向：`damage_limit` 是**接纳线**（`damage <= limit` 即接受），
两条约束方向相反 ⇒

```
healthy_ceiling = max(健康读数) × relax    ← 线下界
degraded_floor  = min(最轻劣化档)          ← 线上界
可行区间 = [healthy_ceiling, degraded_floor]
区间为空 ⇒ not_lineable（报越界，不硬凑）
```

### 修法：网格改相对倍率

`q' = q_healthy × f`，`f ∈ (1.00, 0.95, 0.90, …, 0.50)`，
`f=1.00` 即**健康态本身**（构造的零参照点）。B★ 同步改
`α = 1 + β`，`β=0` 即健康态。

实测 5 份样本的 `f=1.00` 档**逐位复现**真实 StarNet2 读数
（0.2403/0.3580/0.2983/0.0357/0.1851），`overlap` 全部
**+0.027~0.043**（劣化档严格高于健康态）。

旧的绝对网格保留为 `grid_values_absolute()` —— 它的读数已被
V1/V2/V3 验证过，不能删；但它**不能用于定线**。

### V5 实测结论：`not_lineable`

```
healthy_ceiling = 0.3580 × 1.5 = 0.5371
degraded_floor  = 0.0789
⇒ 区间为空，越界 0.4581
```

根因**结构性**：健康基线跨 **10 倍**（0.0357~0.3580），
而「最轻劣化」相对各自健康态只抬高约 **0.033**（中位）
⇒ **单一绝对 damage 阈值无法区分健康与劣化**。

**正确处置**（已写入报告 `remedy` 字段）：
① 判据改**相对基线**（如以 `q_healthy` 归一化的 damage 增量）；
② 或按形态/子类型再分档（IC434-S50 的 0.0357 与 M8 的 0.3580
显然不是同一类对象）；③ **不得**为凑出线而放宽任一侧。

### 实施中的 4 个真实 bug（均由新测试抓出）

1. **V5 块插在 `healthy_ranges` 定义之前** ⇒ `UnboundLocalError`。
2. **V4 不可用时的 `continue` 吞掉 V5 输入** —— V4 缺的是 `spread`
   （抖动可信度），V5 要的是构造读数，两者输入不同。
3. **抖动告警用 `degraded_floor − max(healthy)`** ⇒ 忽略 `relax`
   系数，把间隔算大（0.190 vs 正确的 0.135）⇒ **漏报**告警。
4. `excluded` 记 reason 字符串而非**样本名** ⇒ 无法定位是哪几份被排除。

### 测试夹具的两处自我教训

- 用「集中健康组」夹具写「旧公式会越界」的断言 —— 实测**不越界**，
  证明不了任何东西。必须用**真实语料**（健康跨 10 倍）才能暴露方向错误。
- 夹具的档位必须**自己算一遍**：`LINE_RELAX_FACTOR=1.5` 会把
  健康 0.105 抬到 0.1575，劣化档填 0.155 恰好被跨过 ⇒ 报
  `not_lineable` 却查不出原因。

### Verification

- 全量测试 **1056 passed / 0 failed**（V5 新增
  `tests/test_star_damage_line_selection.py` 25 项；
  标定相关 6 个文件共 130 passed）。
- `validate_repository.py` exit=0。
- V1/V3 结论**未被改网格破坏**：5 份可测样本的闭式残差
  仍 ≤4.8e-05，E 型红线仍全部通过。

## [0.1.36] - 2026-10-04

补齐 `emission_nebula` 档标定样本 + 母版可测性筛查工具。**仍未放宽任何门禁阈值。**

起因是 v0.1.35 的结论：数据抖动修好后，该档 4 份样本里**只有 M8 一份
「抖动已测且通过」**，不达 `CALIBRATION_CORPUS_SIZE = 3`。

### 发现：NGC7000 早已在产物里，但不可用

`/tmp/calib2/` 实际已有 **4 份**产物（v0.1.34 批次跑过 NGC7000，
此前漏检）。实测其读数 `separation=1.019`、`measurable=False`
—— 与 M17（1.017）同命，**全区域不可测**。

### 关键效率发现：raw sep == post sep

跑一次管线约 8 分钟/份，但 `04_separation_reference.tif`（star_remove
的输入）上的 `subject_separation` 与**原始 XISF 母版逐位相同**
（4 份样本 Δ 全部 = 0.000000）。

机制：`separation = p75/p25` of `gaussian_filter(gray, sigma=8)`，
是**分位之比**；DBE 减掉的是大尺度背景梯度，对该比值几乎无影响。

⇒ **26 份母版 27 秒筛完**，且工具每次运行都用已有标定样本**重新验证
这个等价性** —— 它是经验观察不是保证，失效时筛查会变成**系统性错误**
且全程不报错。

### 新增 `scripts/screen_calibration_corpus.py`

输出每份母版的 `separation` / 9 子窗可测率 / 分辨率 /
`catalog_hit` / `type_is_guessed`。

**`type_is_guessed` 是必需的**：实测 **C50（疏散星团）不在本地星表**
⇒ `resolve_target_type` 兜底猜成 `emission_nebula`
⇒ `is_star_poi_target` 返回 `False` ⇒ 管线的星团禁令**不触发**
⇒ 会在「星点即主体」的数据上跑完整去星链，`damage` 语义不明。
这类目标**不作首选**。

筛查结果揭示：`separation` 判据在本机数据上**天然偏严** ——
26 份里可测率 ≥0.5 的只有 5 份。

### 筛查结果

```
母版                      sep       可测率   9子窗范围
M8                        1.5702   1.00    [1.454,1.651]  ← 已标定
C50(S50)                  1.4126   1.00    [1.318,1.479]  ⚠ 类型兜底猜测
IC434(S50)                1.3837   0.67    [1.262,1.720]
NGC2237                   1.3330   1.00    [1.346,1.507]  ★
NGC6888                   1.3079   0.44    [1.271,1.345]  ← 已标定
IC434(S30)                1.2684   0.44    [1.195,1.491]
M42 / NGC7000 / M17       ~1.02-1.08  0.00
其余 15 份                 ~1.02-1.12  0.00
SeestarS30Pro-IC434       READ FAILED（文件损坏）
```

⚠ **跨机型不可直接比 sep**：`separation` 是分位之比，sigma=8 的高斯核
在 2MP 小图（子窗 1151×647）上占比更大 ⇒ 平滑更强 ⇒ p25 被拉高。
实测 S50-IC434 的子窗 sep 范围 [1.262,1.720] 比 NGC2237 的
[1.346,1.507] 离散得多。

### 四份新样本实测：全部成功

`source=starnet` + `accepted=true` + `fallback_applied=false`，
产物 SHA-256 与 `04_separation_reference.tif` **全部不同**。
且 **raw 筛查预测的 sep 与管线实测逐位吻合**（1.333/1.384/1.413/1.268），
再次验证 §0 的等价性。

| 样本 | sep | damage | measurable | V4 可测层 n/spread | 不可测占比 |
|---|---|---|---|---|---|
| NGC2237 (8MP) | 1.333 | 0.2983 | True | 9 / 0.0854 | 0% |
| IC434-S50 (2MP) | 1.384 | 0.0357 | True | 6 / 0.0991 | 33% |
| C50-S50 (2MP) | 1.413 | 0.1851 | True | 9 / 0.0648 | 0% |
| IC434-S30 (2MP) | 1.268 | 0.0000 | False | 4 / 0.0453 | 56% |

**四份的 V4 全部 `measured`**（抖动已测），比预期好 ——
IC434-S30 母版级 sep 1.268 不可测，但子窗层仍可测 4 个。

### 全量标定结论（8 份样本，可测 5 份）

`emission_nebula` 档可测样本 **5 份 ≥ 3** ⇒ 样本量门槛已过。
档内 `damage` 全距 **0.3223**（0.0357~0.3580），跨度接近一个数量级。

**抖动项不再阻塞**：6 份样本的组内 spread 为档内全距的 3%~31%
（最大 IC434-S50 31%），`jitter_exceeds_damage_range` 为空。
⇒ v0.1.35 的「抖动未知」现变为「抖动已知且可接受」。

剩余两条阻塞**都只指向那 3 份不可测样本**（M17 / NGC7000 / IC434-S30），
不是档位本身的问题。**下一步是跑 V5 劣化组** —— §4.4 的双向夹逼
需要劣化组最轻档的下界才能定线。

### Verification

- 全量测试 **1031 passed / 0 failed**（新增
  `tests/test_screen_calibration_corpus.py` 15 项；
  标定相关 5 个文件共 105 passed）。
- `validate_repository.py` exit=0。

### 顺带修的真实缺陷

- `synthetic_nebula(contrast=1.0)` 触发 `ZeroDivisionError`
  （pedestal 公式 `(p75 - c·p25)/(c-1)` 除零）⇒ 夹具自己没兜住，
  等于「能产出 NaN 的数据」。已加 `contrast > 1` 前置校验。
- 筛查工具的坏文件处理：`SeestarS30Pro-IC434` 是损坏文件
  （`not well-formed: line 6`），单份读失败只记 `read_failed`，
  不中断整轮筛查。

### 明确不做

**不改 `min_subject_separation` / `damage_limit`。** 本轮只补数据 + 加工具。
26 份里可测率 ≥0.5 的只有 5 份 —— 判据在本机数据上**天然偏严**。
若后续找不到可测样本，回到「标记不可标定」路线
（`damage_limit=None` ⇒ 恒 `uncalibrated`），而不是改判据 ——
判据一动，原本 62.5% 不可测的区域会突然变成可测，
产出**看似确定**的 damage，比不可测危险得多。

## [0.1.35] - 2026-10-04

修V4 数据抖动的配对缺陷 + 2D 网格 + 按可测性分层。**仍然不放宽任何门禁阈值。**

起因是「把 V4 子窗从 5 提到 9+ 并按 separation 分层」这个需求。调查发现
**V4 有个更严重的缺陷必须先修** —— 否则「提到 9+」只是给一个测不出东西的
函数加样本量。

### 缺陷一：V4 把原图当无星图（结构性，无法测抖动）

`data_jitter(image, ...)` 的签名里**没有 `starless`**，函数体
`collect_sample(sub, construct_brightness(sub, k))` 于是把原图自己
当无星图。配对变成 `(orig, orig×k)` ⇒ `q = 1/k` 是**代数后果**，
与像素内容无关。实测 9 子窗 `q` 读数极差 **0.000000**。

修法：加 `starless` 形参，配对改为 `(sub_orig, brightness(sub_sl, k))`
—— 与 `run_constructs` 同源。实测 `q_spread` 从 0.000000 变成
0.0635（NGC6888）/ 0.0724（M8）。

### 缺陷二：子窗沿对角线滑动，四角一个都采不到

`frac = (index + 0.5) / n_windows` 同时用在 y 和 x 上 ⇒ 全部子窗沿
**一条对角线**排列（实测 n=5 距画幅中心 (-615,-346)→(+614,+345)）。
角区/边区/中心的形态差异正是「位置」这个自变量的全部内容。

修法：新增 `window_positions()`，2D 网格 `ceil(sqrt(n))` 布置，
`np.unique` 折叠 `linspace` 在单轴上的重复值，并**如实上报**实际
位置数（不静默截断）。默认 9（3×3）；16（4×4）更密但每子窗降到
~0.55MP，接近 `separation` 判据敏感区，故只作可选档。

### 缺陷三：0/0.3 双峰是可测性翻转，不是读数噪声

`separation` 在 1.25~1.35 之间**横跨判据下限 1.30** ⇒ 部分子窗
`measurable=False`、`raw_change` 被强制为 0.0、`damage` 读 0。
混池化算 spread 得到的是「可测子窗占比」，不是读数稳定性 ——
v0.1.34 报的 `spread=0.30` 就是这么来的。

修法：按 `measurable` 分层，各自独立报 `n` / p50 / p95 / spread /
`separation` 区间。不可测子窗**不参与抖动统计**，但计入
`unmeasurable_fraction`；>50% 时给出「代表性有限」警告。

未分层的 `spread` 保留但改名 `pooled_spread_misleading` 并附
「不可用于定线」—— 留着是为了让旧报告能看出问题，不是因为它有用。

### 缺陷四：`summarize()` 根本不读 V4

这是缺陷一能存活整�� v0.1.34 的直接原因：V4 跑出什么都影响不了
`verdict`，于是「没有反馈」被当成「没问题」。

修法：`summarize()` 新增两条 `blocking_reasons`：

1. 可测层 `n < 3` ⇒ 「数据抖动**未测** —— 放宽的前置条件是
   『抖动已知且可接受』，**『未知』不等于『可接受』**」
2. 可测层 `spread >` 档内 damage 全距 ⇒ 「单点定的线无意义」

并在 `verdict` 里增报 `jitter_strata` / `pooled_damage_range`，验收者
不必回翻样本。

### 实测结果（v0.1.34 的表格整体作废，见下）

| 母版 | q_spread | 可测层 n / spread | 不可测占比 | V4 判定 |
|---|---|---|---|---|
| NGC6888 | 0.0635 | 4 / **0.0112** | **55.6%** | measured |
| M8 | 0.0724 | 9 / **0.0724** | 0% | measured |
| M17 | 0.0056 | **0** | **100%** | no_measurable_window |

档内 damage 全距 0.1177 ⇒ M8 的 spread 0.0724 在其内（抖动可接受），
NGC6888 的 0.0112 远小于它。

### 更正 v0.1.34 的两处结论

1. **标定文档 §4.2 第 3 条**（「NGC6888 数据抖动 spread=0.30 是比样本数
   不足更本质的阻塞」）**作废**。该数字来自 `[0,0,0,0,0.3]` 的双峰，
   衡量的是可测子窗占比。**数据抖动在 v0.1.34 中从未被测量过** ——
   不是「很大」，是「未知」。
2. **CHANGELOG v0.1.34「未放宽任何数值 — 理由」第 2 条**撤销，替换为
   「数据抖动尚未测量」。**放宽决定本身不变**（仍不放宽），但理由从
   「抖动过大」更正为「抖动未知」—— 后者更弱，也更诚实。

### 教训（已写入标定文档 §3.10）

**「报告里有个看起来合理的数字」不等于「它在测那件事」。**
判断一个诊断量是否真的在测目标，要找一个「若它没在测就必然为常数」
的量，直接验它是否随自变量变化。

V4 的 bug 能存活三个原因，都值得当回归网：
①零测试覆盖；② `verdict` 不读它的字段 ⇒ 无反馈被当成没问题；
③ 输出数字看起来合理（0.0/0.3 都像真实读数），不像 `NaN`。

### Verification

- 全量测试：995 passed → **1015 passed / 1 failed**（长期既有 flaky：
  `test_emission_fallback_switches_to_masked_ghs`，单独跑 1 passed）。
- 新增 `tests/test_star_jitter_stratification.py`（18 项），其中
  `test_q_varies_across_windows` 是缺陷一的回归网 ——
  V4 一旦退回用原图当无星图（`q` 极差为 0）立刻失败。

## [0.1.34] - 2026-10-04

去星门禁按 `target_type` 分档：机制 + 棘轮 + 产物语义修复 + 标定器。
**本版本不放宽任何数值** —— 实测证明放宽必须等标定数据（见末节）。

### 产物语义修复（P0，标定的前提）

- **回退时不再把恒等图写进无星图路径**。`_safe_star_removal_fallback()`
  返回 `image.copy()`，而落盘是无条件的 ⇒ 逐字节 SHA-256 确认 9 组产物
  全部 `04_separation_reference.tif == 04_starless_linear.tif`。
  后果：文件名承诺「无星图」实为原图，人工审查与后续流程都会被误导；
  离线标定更是拿不到健康态对照（`q ≡ 1`、构造全落死区、**静默失效**）。
  回退时改落 `04_starless_fallback.tif`，并在 `result.json` 新增
  `artifact_meta` 标 `is_fallback_copy: true`；回退时不再落全零的
  `04_stars_linear.tif`（落零文件同样是噪音）。
- **`target_type` 进入 `cfg` 与配置指纹**。它是门禁阈值的选择依据，
  不进 cfg 就等于「指纹相同但门禁结果不同」—— 正是
  `config_fingerprint` 声明要防的不可区分产物。实测
  `--target-type galaxy` vs `emission_nebula` 的 `config_fingerprint`
  现已不同。
- **星系静默剥离补日志**。无 `subject_mask` 时会移除 4 个星点步骤，
  此前无任何输出 —— 门禁分档后「无 `star_removal` 报告」会被读成
  「门禁通过」，而真实原因是**根本没走到门禁**。

### 分档机制

- 新增 `scripts/star_removal_profiles.py`：`RemovalProfile` 冻结 dataclass
  （19 键）+ 一处冻结 `DEFAULTS` + 一处表。**默认值刻意等于 v0.1.33 的
  全局常量** ⇒ 分档上线当天全类型行为逐位不变；放宽只能显式改表，
  并被单向棘轮记成可审计的 `ratchet_bound`。
- **逐字段声明棘轮方向**，不做全局 `max`。两处方向与直觉相反：
  `repair_cap_*`（上限）与 `repair_weight_*`（权重）**反向**；
  `baseline_change` 调**小**才是更严（它是从 raw_change 里减掉的基线）；
  可测性阈值调**大**才是更严。
- 星团 3 类标 `reachable=False`（`pipeline.py:959-968` 会删掉 4 个星点
  步骤，结构上到不了门禁）；`supernova_remnant` / `wide_field` 建空档
  + PROVISIONAL。键空间自校验：表里出现未知类型即 import 期报错
  （拼错一个键 = 该类型永远落默认档且**不报错**）。
- 报告新增 `star_removal.star_removal_profile`（纯增量，既有 12 键零改动）：
  生效阈值、被棘轮挡住的字段、`deprecated` 列表、标定文档引用。

### 观测性修补

- `_record_gate_state` 改用 profile 传值。此前硬编码模块常量 ——
  分档后会打印**错误的**阈值数字，而验收者正是靠这些数字判断口径。
- `external_starless` 路径标 `gate_evaluated: False`。此前硬编码
  `'accepted': True` 且无 `gate_state`，会被读成「默认档通过」——
  那是「没判」与「判过且通过」两种完全不同的结论。
- `starnet_reject_note` 补上档位名与实际接纳线。

### 弃用：`nebula_mask_coverage`

主体掩膜由 `orig_smooth >= percentile(orig_smooth, 75)` 圈定 ——
**相对**分位 ⇒ 掩膜占比**数学上恒等于 0.25**（4 份母版实测全部
`0.250000`）⇒ `> 0.60` 的判据**从不触发**。

0.1.33 CHANGELOG 声称的「掩膜退化为全图」失败形态因此**不存在**。
处置：字段保留（schema 兼容 + 观测量），标 `DEPRECATED_FIELDS`、
不参与分档与棘轮、在报告里显式列出；可测性回归 `subject_separation`
**单判据**（实测 M17 正是被它拦下：`separation=1.017 < 1.30`）。

### 新增：劣化对照标定器

`scripts/calibrate_star_damage_gate.py` + `references/star_damage_gate_calibration.md`。
V1 闭式 → V2 单调 → V3 隔离 → V4 抖动 → 门禁口径回放，输出独立 JSON
（**不写回 `result.json`**）。

标定器自身的开发过程中被实测推翻 9 条设计假设，全部记录在
标定文档 §3，其中三条影响面最大：

- **标定口径必须全分辨率**，不能照抄保真度门禁的 1400px（那条降采样
  只适用于显式过 `downsample_for_gates` 的调用点，去星门禁不过）。
  实测同一对 NGC6888 图：全分辨率 `separation=1.308 / damage=0.2403 /
  measurable=True`，1400px 读 `1.287 / 0.0000 / measurable=False` ——
  **两个相反结论**，且 NGC6888 紧贴判据下限 1.30，跨口径必然翻转。
- **A 型亮度缩放不是「零混淆」构造**。`high_grad_ratio` 的判据含**绝对**
  噪声地板 `6σ_sl`，`sl` 缩放时它与 `grad_sl` 同比变化而 `grad_orig` 不变
  ⇒ 三段归因法判为 `confounded`。**没有任何构造是 `pure_damage`**，
  定线只能给「量级正确的候选值」，`check_isolation()` 会在
  `limitation` 字段自陈。
- **E 型阴性对照的红线在数学上不可能成立（除非腐蚀）**。`damage` 定义在
  `gaussian_filter(sigma=8)` 上而高斯核支撑无限，掩膜外紧邻边界的亮度
  会卷进掩膜内（实测 damage 0.2495 → 0.2627）。腐蚀 3σ=24px 后逐位不变。
  配套：阈值必须对齐 4 位小数的舍入粒度，取 1e-9 会让**任何进位都判失败**
  （M8 实测 0.3580 → 0.3581 被误报为「damage 不是纯主体局部量」）。

### 端到端验收（NGC6888，`--use-starnet`）

**StarNet2 首次被接受**（此前 v0.1.33 被拒）：

```
✓ 去星质量=0.535  method=starnet（accepted, fallback_applied=false）
residual 0.0003 / damage 0.2403 / separation 1.308 / coverage 0.2500
gate_state=advisory
gate_note: 误伤 0.2403 落在接纳线 0.20 与硬线 0.40 之间，已带警告接受
```

产物侧 `04_separation_reference.tif` 与 `04_starless_linear.tif`
SHA-256 **不同**（v0.1.33 及更早为相同），证明落盘的确实是去星结果。

### 未放宽任何数值 —— 理由

`verdict.status = blocked`。三条阻塞：

1. M17 `separation=1.017` 落在可测性判据之下 ⇒ `emission_nebula` 档
   只有 2 份**可测**样本 < 3 ⇒ PROVISIONAL。
2. **NGC6888 的 V4 数据抖动 spread=0.30**（5 个子窗），比档内
   `damage` 全距（0.24~0.36）还大 ⇒ 读数强烈依赖取样位置，
   单点定的线没有意义。这是比样本数不足更本质的阻塞。
3. 硬线 0.40 > score 分支饱和点 0.225 ⇒ `damage > 0.225` 后由
   score 分支实际把关，damage 分支不可判定。

实测三份同为发射星云的母版，`damage` 从 0（M17 不可测）到 0.358（M8），
**跨度覆盖了整个门禁区间**。分档机制已就位，放宽须待抖动收敛后按
`star_damage_gate_calibration.md` §4.3 的双向夹逼规则定线。

### Verification

- 全量测试 **940 passed / 0 failed**（§2 P0 改动后复测）。
- 新增 `tests/test_star_removal_profiles.py`（21 项）+
  `tests/test_star_damage_calibration.py`（30 项），
  `tests/test_star_tools_fallback.py` 补 7 项分档接线用例。

## [0.1.33] - 2026-10-03

去星门禁优化：两级门禁 + 误伤可测性判据 + 重试非回归 + 验收可见性。
参照 aiseestart 的 `docs/stage6_star_separation.md`（advisory ×2 → hard 分级、
「真实低覆盖拒绝 / 测量不可用保留」区分、`stage7_starless_repair_max_score_growth=0.00`）。

- **两级门禁取代一刀切**：`_star_removal_gate()` 统一判定，取代
  StarNet 侧 `score >= X and damage <= 0.20`（与）与形态学侧
  `score >= X and not needs_starnet_plus`（或）两套矛盾口径。
  - `ok`：误伤 ≤ 0.20 且分数达标
  - `advisory`：误伤 ∈ (0.20, 0.40] ⇒**带警告接受**，不再全盘回退
  - `hard_failed`：误伤 > 0.40 或分数不达标
- **误伤可测性判据**：`estimate_star_removal_quality` 新增
  `nebula_damage_measurable` / `nebula_mask_coverage` / `subject_separation`。
  主体掩膜占比过高（掩膜退化为全图）或主体/背景亮度比≈1（无星云主体）
  ⇒ 报 `unmeasurable`，**不据此拒绝已完成的推理**。
- **重试非回归零容忍**：`_STAR_REMOVAL_MAX_SCORE_GROWTH = 0.00`，重试分数
  下降即丢弃（此前只查「达标没达标」，更差的结果也能被接受）。新增
  `star_removal_retry_regressed` 原因码。
- **验收可见性**：回退时把被拒候选的指标提升到顶层
  （`starnet_candidate_*`）+ `starnet_reject_note` 明写「谁被拒、为什么、
  回退后劣化多少」。此前顶层字段被形态学结果覆盖，只看顶层会误以为形态学是
  唯一候选 —— NGC6888 实测 StarNet2 残留 0.0003 vs 形态学 0.0057（**19 倍**）
  在产物里完全不可见。
- **同模块两处对 damage 的容忍度统一**（0.20 vs 0.15 的矛盾）。

### 端到端验收（NGC6888 --use-starnet）

StarNet2 仍被拒（`damage 0.5161 > 硬线 0.40`），但这次**可解释且可对比**：

```
StarNet2 推理成功但被质量门拒：质量分 0.537、残留星 0.0003、误伤 0.5161；
门禁状态 hard_failed。回退形态学后残留星劣化至 0.0057。
```

未放宽 0.20 的理由：实测 NGC6888 的主体掩膜 coverage 0.25（未退化）、
`g>3×背景底` 占比 0.164，说明 damage 指标在该数据上**可测**——
之前的「掩膜退化导致不可信」假设不成立。是否放宽需先补 M17/M42/NGC7000
的 damage 分布确认 0.516 是离群还是常态。

### Verification

- 全量 **940 passed / 0 failed**（新增 `StarRemovalGateTest` 10 项门禁用例）。
  `test_star_fwhm_reporting` 与 `test_star_step_dependencies` 两个失败为
  长期既有 flaky（单独跑全过）。

## [0.1.32] - 2026-10-03

累计配置指纹（`scripts/config_fingerprint.py`），补上质量继承链缺失的「配置维度」
证据。阶段化对标的 P0/P1/P2 三项至此全部落地。

- **要补的证据缺口**：`quality_state` 绑定的是**产物 SHA-256** —— 能证明
  「这个文件没被改过」，**证明不了「产生它的配置没被改过」**。于是有一条静默
  失效路径：配置 A 跑出产物 P → 改配置 B → 重跑 → `parent_state` 把
  **配置 A 的 issues 继承到配置 B 的产物上**。该路径在 Agent-in-the-loop 下
  尤其易触发：`agent_workflow` 的 `artifact_state` 跨 action 累积、
  `parent_state` 链一路继承，而 `initialize_session` 允许新 session 复用
  同一个 `session_dir`。
- **累计指纹**（借鉴 starun 侧 `task_plan.py:208-229`）：
  ``fingerprint(N) = hash(Stage 1..N 的全部配置 + 输入 SHA)``。
  「改 Stage 2 参数 ⇒ Stage 5 指纹自动失效」是这条性质的直接后果。
- **指纹不同即切断父issue 继承**（`build_quality_state` 的 ``config_changed``）；
  **父 state 无指纹（历史产物）时保守继承** —— 不静默丢弃已有 finding。
  当前阶段的门禁照常记录，配置变更只影响「父的」那部分。
- `result.json` 新增 `config_fingerprint`（20 位）与 `config_used`
  （按阶段分组的实际参数清单）—— 报告能自证「我是用哪套参数判的」。
- 参数归阶段：82 条精确映射 + 16 条前缀规则。**未识别的键进 `_unassigned`
  桶并照样参与哈希** —— 漏算会让「改了某参数却算出同样指纹」，那等于没做。
  追踪容器（`used`/`completed`/`phase`）被排除，它们不是配置。
- **与 `runtime_info.source_fingerprint()` 正交**：source 管「跑的是哪版代码」
  （全部 `scripts/*.py` + `references/**`），config 管「用的是哪套参数」。
  改代码只动前者、改参数只动后者 —— 这是两个不同维度，不制造「两份指纹
  并存」的认知负担。
- **补齐 Agent 会话路径的传递**：`build_quality_state` 的 6 处调用中4 处已接入
  指纹（`run_step` / `create_variants` / `masked_adjustment` /
  `request_human_review`），2 处**刻意不传** —— `initialize_session`（无父状态、
  无配置概念）与 `artifact_quality` 的兜底分支（无法定位历史 `pipeline_result`，
  构造 `LEGACY_*` 记录；此时指纹为 None，按「父无指纹 ⇒ 保守继承」处理）。
  `masked_adjustment` 的指纹取自**父状态**而非 `pipeline_result` —— 该路径产物
  由 `execute_masked_adjustment` 生成、**不经管线**，作用域内没有
  `pipeline_result` 变量（曾误传导致 `NameError`，已补两项回归锁）。掩码调整
  不改配置，沿用父指纹是正确语义。

- **验收中发现并修复：步骤顺序与产物编号错位**。NGC6888 端到端跑出
  ``executed_steps = [... local_enhance(13), star_reduce(15), style(10) ...]``
  —— 实际执行序是 Phase 8b/8d 先于 9a（产物编号 ``08b``/``08d`` <
  ``09a``），而旧 ``ALL_STEPS`` 把 ``style`` 排在 ``local_enhance``/
  ``star_reduce`` 之前。这是**既有缺陷**（HEAD 基线的 ALL_STEPS 即如此），
  但注册表若照抄会把它固化，并让 `config_fingerprint` 的累积切片算错。

  修法：注册表按**产物编号**重排（``local_enhance``→10、
  ``external_detail``→11、``star_reduce``→12、``style``→13），
  ``EMISSION_STEPS` 同步纠正；并新增**第 9 项自校验**「order 递增时产物
  编号不得回退」防复发。产物编号的数字前缀**不唯一**标识步骤
  （``02``/``02b``/``02c`` 共享数字 02），所以约束是「非严格递增」而非相等。

### Verification

- 全量 **930 passed / 0 failed**（新增 `test_config_fingerprint.py` 38 项、
  `test_mask_tools.py` 2 项指纹回归锁、`test_stage_registry.py` 4 项
  顺序一致性；888 → 930）。
- 顺序修正**零行为变化**：`status` / `quality_gates`(6) / `warnings`(7) 与
  修正前逐项一致；两次独立运行的 `config_fingerprint` 完全相同
  （`de98551a54cd286d7562`），证明指纹不受顺序修正影响。
- 累计语义逐条验证：改上游（`dbe_degree`）**必**影响下游（style）指纹；
  改下游（`saturation`）**不影响**上游（stretch）指纹；换输入必变指纹。
- 质量继承三场景：同配置继承父 issue / 改配置切断继承 / 父 state 无指纹时
  保守继承。
- 端到端 NGC6888：`status` / `quality_gates`(6) / `warnings`(7) / `degraded_stages`
  与 0.1.31 **逐项一致**；`config_fingerprint` 与 `quality_state` 中记录一致，
  `config_changed=False`（单次运行无父状态）。

## [0.1.31] - 2026-10-03

失败处置策略三态化（`scripts/failure_policy.py`），并补上此前缺失的
`degraded` 管线级状态。

- **要解决的问题**：拉伸链全部候选被拒后，`pipeline.py` 做的是**无条件截断**
  —— 拉伸之后的步骤全丢、且不出图。线性域的 dbe / color / pre_denoise /
  star_remove 已经算完且 TIFF 已落盘，却因最后一步失败而被全部丢弃。
  用户「拉伸失败但想看前面校色后的结果」的需求无法满足，只能重跑 ——
  而重跑会再失败一次。
- 三个处置动作（`stretch_failure_action` 或 `failure_<stage>_action` cfg 键）：
  - `auto_fallback`（默认，保持现状语义）：回退保守输入并继续。
  - `preserve_review`（新增中间态）：同样继续，但**保留全部中间产物**、
    把`stretch_diagnostics["preserve_review"]` 写明保留的步骤与原因。
  - `stop`：**不出图**，状态 `failed`，但线性域中间产物仍保留供审查。
- **`stop_is_stricter`**：任一阶段选 `stop` 时，其他阶段的
  `auto_fallback` / `preserve_review` 一律失效。混合语义（「前面自动回退、
  后面突然终止」）的结果最难解释。
- 非法动作值**报错不回落**（`FailurePolicyError`）—— 处置策略是
  「宁可不交付也不出错图」的最后一道闸，回落会把显式意图变成别的行为。
  与 `target_rules` 的既有纪律一致。
- **`degraded` 成为管线级status**（`failure_policy.PIPELINE_STATUSES`），
  优先级 `failed` > `review_required` > `degraded` > `success`
  （`quality_state.derive_status`）。**有未解决 issue 时不被 degraded 掩盖**。
  `degraded` **不阻断 `accept`**（判据是 issues 非空，degraded 不产生 issue）。
  降级阶段记入 `quality_state.degraded_stages` 并**向后继承** —— 本阶段
  干净不代表整条链路干净。
- **为什么需要 degraded 这一态**：此前 status 只有二值
  （`review_required` / `success`），而拉伸有 4 级回退链 —— 「第 2 级
  `masked` 成功」与「一次成功的 `pi_mtf`」**完全无法区分**，调用方无从
  判断结果是否经过降级、是否该人工介入。`degraded` 补上了这个信息。
  它与 `gate_authority` 里门禁级的 `degraded` 是同一概念的两种载体：
  **都可见但不升级**。
- import 时自校验：`DEFAULT_FAILURE_ACTIONS` 的动作必须合法、
  `ACTION_TO_STATUS` 的 status 必须已声明、`FAILURE_ACTIONS` 每个动作
  必须在 `ACTION_PRESERVES_ARTIFACTS` 有条目、`OVERRIDABLE_STAGES` 不得
  声明未登记阶段。

### Verification

- 全量 **888 passed / 0 failed**（新增 `test_failure_policy.py` 32 项；856 → 888）。
- 端到端 NGC6888：`status` / `quality_gates`（6条）/ `warnings`（7 条）与
  0.1.30 **逐项一致** —— 正常路径未被 degraded 逻辑污染。
  `degraded_stages = ["star_remove"]` 正确检出该数据确实走了形态学去星回退，
  `status` 因存在 2 条 issue 仍为 `review_required`（优先级正确）；
  清空 issue 后按`derive_status` 回落为 `degraded`（单测锁定）。

## [0.1.30] - 2026-10-03

阶段化设计对标 starun 侧的 `stage_contracts.py` / `stage8_handoff.py`，落地 P0两项：
步骤契约化与门禁授权字段分离。**无行为变更** —— 正常路径的门禁判定结果与
0.1.29 逐条一致，本版只是把此前「无机制会报错」的三类漂移变成 import 期
RuntimeError。

### P0-1 步骤契约化（`scripts/stage_registry.py`，新建）

- `StepContract` frozen dataclass + 19 步契约（16 主步 + 3 可选步）+ 32 个产物，
  **`ALL_STEPS` / `EMISSION_STEPS` / 产物→步骤映射全部从注册表派生**，
  `pipeline.py` 不再维护自己的列表。
- **import 时自校验**（8 项）：key 唯一、主流程 order 从 0 连续、域只能
  linear→nonlinear 单向切换一次、产物名全局唯一、`EMISSION_STEPS` 是主流程
  子集、可选步骤 order 必须为-1、**注册表与 `pipeline.py` 实际落盘调用双向
  一致**（缺一即 RuntimeError，错误信息指名产物）。
- 修复三处**实测发现**的既有脱节：
  - `02c_linear_deconv.tif` / `11_reference_grade.tif` /
    `10b_background_locked.tif` 有产物、有 `cfg.phase`、会进 `cfg.completed`，
    却在 `ALL_STEPS` 之外（旧 `ALL_STEPS.index()` 遇它们会 `ValueError`）。
    现登记为 `optional_steps`，`sort_steps` 按主产物 `(数字, 字母后缀)` 标记
    插到相邻位置（`02c_linear_deconv` 排在 `02_color` 之后）。
  - 产物编号与步骤顺序错位（`style`=09a 但位置 10、`local_enhance`=08b 但
    位置 13）—— 注册表显式区分「order（执行序）」与「artifacts（命名）」，
    不再让命名隐式承载顺序。
  - 拉伸失败截断（`pipeline.py` 原 `ALL_STEPS.index()` 位置运算）改走
    `stage_registry.step_order()`，可选步骤返回 `None` 而非抛错。
- 刻意**不照抄** starun 的 `artifact_prefix` 校验：我方产物名带两位序号 +
  字母变体后缀（`02b_`/`05a_`/`09a_`/`10b_`），字母表达变体而非序号推进，
  对方的 `stage{N}_` 前缀规则不适用。也**不引入 `runtime_label`**（无 GUI，
  是死字段）。
- `sort_steps` 对未知步骤不再抛 `ValueError`，排末尾并保持相对顺序 ——
  调用方输入来自用户 `--steps`，把「拼错步骤名」变成可排序的差异更易定位。

### P0-2 门禁授权字段与诊断字段分离（`scripts/gate_authority.py`，新建）

- **要修的循环依赖**：`collect_quality_gates` / `build_quality_state` 用
  `gate.get('status') in ('warning','failed')` 与 `gate.get('escalate')` 决定
  升级与阻断，而这两个字段**由门禁模块自己填**。任一环节改错，整条质量
  继承链**静默失效**且无任何机制发现。
- **授权字段**（进入 digest）：`code` / `status` / `escalate` / `value` /
  `threshold`。**诊断字段**（不进入 digest，允许自由改写）：`message` /
  `evidence` / `summary` / `context` / `attribution`。划分标准借鉴 starun 侧
  `stage8_handoff.py:20` 的 `_UNBOUND_DIAGNOSTIC_FIELDS`。
- 三个门禁构造器（`agent_protocol.add` / `artifact_gates._gate` /
  `fidelity_gates._gate`）在**创建时封印**授权字段；聚合点复验。
  `seal_gate` 对已封印门禁**会复验**而非无条件返回 —— 否则「篡改后重新流经
  聚合点」会被放行，机制形同虚设。
- `status` 三分类：`warning`/`failed` 唯一授权；`degraded`/`unavailable`/
  `uncalibrated`/`skipped`/`passed`/`error` 为诊断态**按定义不授权**（可见
  但不升级）；**未知状态默认不授权**（宁可漏报不误放行）。
- `verify_quality_state` 逐条复验 issue 携带的门禁封印 ⇒ 落盘后编辑
  `status` 会被检出并硬失败。
- **保护范围（诚实声明）**：能检出「构造后被改写」与「跨 run 被编辑」；
  **不能**检出「首次构造即填错」—— 那需要「按合同阈值重算判定」的阈值注册表，
  当前阈值分散在 `fidelity_gates.THRESHOLDS` 与 `artifact_gates` 模块常量，
  强行集中会引入「两份阈值」的新耦合，留待后续。

### Verification

- 全量 **856 passed / 0 failed**（新增 `test_stage_registry.py` 35 项与
  `test_gate_authority.py` 36 项；820 → 856）。
- 自校验有效性用「改源码 → 真实子进程 import → 期望 RuntimeError」验证，
  8 类漂移（漏登记产物 / 多余登记 / 重复产物 / order断裂 / 重复 key /
  可选步骤 order≠-1 / EMISSION 含非主流程 / 缺summary）逐条命中，并设
  「未改动源码必须能import」的对照组排除环境因素。
- 端到端 NGC6888：`status` 与 `quality_gates` 与 0.1.29 一致；
  `executed_steps` 正确包含可选步骤 `background_neutralization`。

## [0.1.29] - 2026-10-03

新增保真度门禁体系（`scripts/fidelity_gates.py`），补齐噪声增益与色度保真维度，并按实测证据修正两处被验证不可用的设计。

- 五道具名保真度门禁：`BACKGROUND_NOISE_BOUNDED`（绝对 σ，只记录）、
  `BACKGROUND_RELATIVE_NOISE_BOUNDED`（相对噪声增益，唯一可升级）、
  `HUE_PRESERVED`（逐像素 chromaticity 漂移 p95，L∞）、
  `SUBJECT_CHROMA_VISIBILITY_PRESERVED`（主体色度 SNR 保留比）、
  `BACKGROUND_CHROMA_NOISE_BOUNDED`（背景色度噪声增益）。参照图为同一份拉伸
  输入以固定 `pi_mtf` 保守曲线渲染的结果，因此度量的是「当前曲线相对保守基线
  的损伤」。全部度量为显示域比较性量，`evidence["physical_snr"]` 恒为 False。
- 三态语义：健康不append；`degraded` 进 warnings 但不升级、不产生 quality_state
  issue；`unavailable` / `uncalibrated` 均记录且**不等于健康**。`quality_state.py`
  与 `agent_protocol` 的 status 判定零改动。
- 阈值按 4 份母版 / 3 种目标类型（NGC6888、M31、M45、M17）在降采样 1400px 口径
  实测标定，记录于 `references/fidelity_gate_calibration.md`，附离线复现命令。
- **实测否决一**：绝对 σ 跨目标相差约 150 倍（M31 6.0e-05 vs NGC6888 9.2e-03），
  统一阈值必然要么全静默要么全误报 ⇒ 该门降级为只记录，噪声判定交给相对量门。
- **实测否决二**：「压主体」不是主体色度保留比的有效劣化对照（分子分母同步骤降，
  比值反升）⇒ 改用保守值 0.95 / 0.85 并在 v1 不升级。
- 标定过程发现绝对噪声注入会被分母median 抵消（注入后σ/median 仅 0.342→0.530），
  改为相对注入后门禁才开始正确区分健康与劣化。
- 门禁挂载点为拉伸链之后（`pipeline.py` 的 `stretch_diagnostics['fidelity_gates']`），
  不塞进 `stretch_checks()` —— 后者返回 4 元组且有 4 个解包点，改元数会波及整条
  拉伸回退链；链后单次评估的语义也更正确（评估最终采纳的结果而非每个被拒候选）。
  `stretch_recovery == 'failed'` 时不产出跨域配对门。
- 修正 `--target-type` 的 CLI 取值清单：此前硬编码 8 项，漏了
  `star_cluster` 与 `supernova_remnant`，导致 CLI 能接受的值少于 API。现改为从
  `target_rules._KNOWN_TYPES` 派生，两处不再各写一份。
- **抽取统一外部工具探测器**（`scripts/external_tools.py`）：StarNet2 / DeepSNR /
  solve-field 三处探测逻辑统一为薄封装（各 8~13 行），平台安装目录收敛到一张
  `INSTALL_DIRS` 表，新增 **Windows 完整路径表**（`C:\Program Files\<Product>\`、
  `AppData\Local`、astrometry 的 `bin\` 子目录），并提供 `probe_environment()`
  逐级诊断与 CLI（`python scripts/external_tools.py [tool]`）—— 「工具找不到」
  靠猜很难定位，现在能一眼看出断在显式/环境变量/PATH/安装目录/脚本目录哪一级。
  修掉一个**真实跨平台缺陷**：`_normalize()` 此前用 `os.path.abspath`，
  而它是**平台相关**的 —— 在 POSIX 上会给 `C:\Program Files\x` 拼上当前工作目录，
  变成永不命中且**静默** miss 的路径；现改为只展开 `~`，盘符/UNC/绝对路径原样保留。
  Windows 上 `os.access(X_OK)` 语义不同，存在性判定只判「是文件」。
  迁移到 Windows 的行为保证：显式 `--starnet-path` / `DEEPSNR_PATH` /
  `SOLVE_FIELD_PATH` 仍是最强优先级，环境变量可指向目录或文件，PATH 缺失时靠
  安装目录表兜底 —— 与 macOS/Linux 行为一致，不会重现「StarNet 能发现、
  DeepSNR 报未找到」的不对称失败。
- **C50 验收修复**：`evaluate_fidelity_gates` 在参考图与候选几何不一致时原本
  返回**空 gates**，使「配对门全部没跑」与「跑了但全部健康」无法区分 ——
  典型静默失效。现改为：① 差 1px（降采样浮点取整所致）时对齐到公共尺寸恢复
  配对能力；② 无法对齐时产出 `FIDELITY_GEOMETRY_MISMATCH` 记录门并在
  `context.geometry_alignment` 标注 `failed`，明确这是几何问题不是数据问题；
  ③ 超差（> 1px，即两侧本不是同一份数据）不对齐，避免掩盖真问题。
- **修复 DeepSNR 探测的路径不对称**：`denoise.run_deepsnr` 原来只用
  `shutil.which('deepsnr')`，**没有默认安装路径兜底**；而
  `star_tools.find_starnet_executable` 有 `/usr/local/bin/starnet2` 兜底。
  在 GUI / IDE / 宿主应用启动的进程里（实测 WorkBuddy 的 PATH **不含**
  `/usr/local/bin`）`shutil.which` 对两者**都返回 None** ⇒ StarNet 能被发现、
  DeepSNR 被报「executable not found」并**静默降级到 atrous**。这是代码缺陷
  而非环境问题：二进制明明装着（`/usr/local/bin/deepsnr` 1.3.2、
  `/usr/local/bin/starnet2` 2.6.2，均 arm64/x86_64 可执行）。
  修法：新增 `denoise.find_deepsnr_executable()`，优先级与 StarNet 侧**完全
  对等** —— 显式配置 → `DEEPSNR_PATH` 环境变量 → `shutil.which` →
  默认扫描路径（`/usr/local/bin`、`/opt/homebrew/bin`、用户 Applications）。
  显式指定但不存在的路径**返回 None 不静默回退**（否则用户以为指定了 A 工具、
  实际跑的是 B 工具）。同时给 `plate_solve.find_solve_field` 补上同样的默认
  路径兜底（`solve-field` 本机确实未安装，属真实缺失，但模式一致才不易再踩）。
  C50 实测收益：`pre_denoise` 从 `fallback`（atrous）变为 `accepted`
  （DeepSNR 1.3.2 / CoreML），`noise_ratio` 0.529→0.653，
  `detail_retention` 0.999→1.002，Phase 10 由「再次降噪」变为
  `Skipped: AI denoise already accepted`，`PRE_DENOISE_FALLBACK` warning 消失。
- Verification: 785 tests passed（新增 `test_external_tools.py` 33 项：四级优先链、
  三平台路径表完整性、Windows `.exe` 后缀、盘符/UNC 规范化守卫、Program Files
  端到端模拟、逐级诊断；`test_deepsnr.py` +2项；`test_starnet_bridge.py` 改 mock
  目标），0 failed。真实语料复核：4 份健康
  母版跑门禁均 `success` 无 escalate；注入 0.3× 背景中位的噪声后 M31 打
  `failed` 升级、其余三份打 `degraded`；饱和度 ×1.45 被 `HUE_PRESERVED` 捕获。
  C50（LP 双窄带，**不在标定 corpus 内**）验收：管线 `status` 与既有标量门
  零变化；修复 DeepSNR 探测后重跑，warnings 由 6 条降至 3 条、
  `quality_gates` 由 4 条降至 2 条（两个色度门因参考图色度支撑改善而转为可测
  并落在健康区，不再报 unavailable）。

## [0.1.28] - 2026-10-01

- Preserve artifact-bound quality findings across stages, variants and rollback; share acceptance guards and require identified resolution evidence. Recover legacy histories conservatively when artifact identity or quality evidence is missing.
- Lock the validated Python 3.12.13 / NumPy 2.5.3 environment; record lock and platform provenance. Use p99.9−p50 for sparse stellar subjects and retain existing star gates; independent display curves require native channel evidence for dark-ring attribution.
- Verification: 623 skill tests, 13 repository contract tests and 245 subtests passed in an independent locked environment; pip check and key Float32 replay passed. Eight real M42 profiles retain weaker core structure than the safe MTF reference and remain rejected. Keep production defaults, linear rollback and C50/M45 routes; no enhanced candidate was generated.

## [0.1.27] - 2026-10-01

- Enforce linear color calibration and saved stage state; audit runtime provenance, applied parameters and skipped operations; add fixed-region M42 comparison diagnostics.

- Verification: 607 skill tests, 13 repository contract tests and 245 subtests passed. Retain the real M42 emission comparison as rejected evidence; matched-background standard GHS removes channel plateaus but does not pass natural-image acceptance. Keep emission/cluster policy selection separate from quality recovery.

## [0.1.26] - 2026-10-01

- Add normalized standard GHS and masked GHS with tangent protection, shared RGB gain and highlight headroom; retain legacy sinh curves and independent parameter semantics. Fit background strength by bracketing the first target crossing before at most 24 bisections.
- Share target policy and dispatch across API, CLI and Agent; new sessions use policy 2 and missing-version sessions retain policy 1. Report effective curves, unused parameters, source hashes and display references.
- Separate physical linear PSF estimates from fixed-MTF display comparisons; preserve RGB clipping and background checks, add inherited/new float core plateau diagnostics, and restore input pixels, domains and layers when recovery fails.
- Verification: 597 tests and 245 subtests passed. Three unchanged original masters produced 18 native float32 comparisons with zero RGB clipping; both historical clipped controls remain rejected. Reviewed C50 workflows retain the emission baseline. M31 background uncertainty keeps overall image acceptance partial; the visually compressed DWARF3 standard-GHS natural candidate was preserved and replaced once from its original stage input with conservative MTF.

## [0.1.25] - 2026-09-30

- 统一 API、CLI、Agent 的自适应分析、真实图像域及源采集元数据传递；TIFF sidecar 保留源信息，累计积分不再触发单帧长曝光先验，记录最终生效参数。
- 增加逐通道裁切比例和区域定位；拉伸及两次受限回退均检查塌缩和 RGB 裁切，末端校色、风格同域检查并记录引入阶段，保留振铃背景结构判据。
- 分步去星返回真实无星层；图层随接受、候选与回退续接，禁止失效复用；审查实际星点层，内存跳步返回 `partial_success`，保留 8MP 保护及外部桥接。
- 低噪声 `auto` 跳过整个预降噪；DeepSNR 1.3.2 与 Starlet 共用实际结果检查，过降噪时减半重验，拒绝时保留输入及复核标记；保存预测、应用验证和残差证据。
- 星系默认保留星点；显式去星要求经过审查的主体掩膜，内置、StarNet、外部无星层均恢复保护信号并排除星点层。
- 回归验证：553 项测试、245 项子测试通过。三份原始母版重跑自然版、增强版，原尺寸 float32 输出 RGB 裁切均为零；旧 C50 13.14% 红通道裁切、M31 核心 43.84% 裁切均被拦截。实际图像验收保留跨域星点门禁的复核边界。

## [0.1.24] - 2026-09-30

- 修复线性中间阶段误用成片背景锁定与最终质量指标；绝对暗部平滑度仅用于非线性审查，保留外部 AI 降噪结构/漂移门禁。
- 修复外部线性无星层 sidecar 覆盖源图元数据导致输出异常；补充明确线性输入的端到端回归。
- 识别 DWARF `DET-TEMP` 温度字段，记录累计曝光歧义及 C50 双窄带实测拉伸/调色约束，避免把单图参数作为通用默认值。

## [0.1.23] - 2026-09-30

- 振铃与暗坑门禁改用屏蔽邻星的局部背景平面和同域配对欠冲增量，避免将 Hα 纤维场结构归因于本阶段处理。
- 已有缺口保留为不升级状态的诊断；新增缺口、背景不可靠或参照不可比时继续触发复核，统计使用实际有效采样数。
- 门禁星宽采用半高核心尺度，避免纤维污染矩估计导致采样环带漏过真实暗环；该尺度不代表物理分辨率。
- Agent 传递输入/输出线性状态，主管线报告注明合成后至末端的归因范围；补充纤维、梯度、新增/已有伪影和参照边界回归测试。

## [0.1.22] - 2026-09-30

- 适配本机 DeepSNR 1.3.2 CoreML：实测 FITS RGB 顺序正常，省去新版每次处理前的额外色块推理；旧版继续校准 RGB/BGR。
- 保留 float32 FITS + 零黑位裁切 MTF：新版原生 `--linear` 默认裁切阴影且不还原源样本，因此不自动启用。
- 报告增加原生线性模式使用标记和通道顺序来源；补充新版单次推理与暗弱信号保留回归测试。

## [0.1.21] - 2026-09-30

- 接入 DeepSNR CLI 预降噪：自动/强制/禁用模式、路径、超时及线性混合强度。
- 零黑位裁切可逆 MTF + float32 FITS，兼容本机 1.2.3 CoreML；固定色块校准 RGB/BGR 平面顺序。
- 外部结果经几何、有限值、值域、亮度/通道漂移、背景噪声和结构保留门禁，拒绝或运行失败时回退保守 Starlet。
- 接受 AI 降噪后默认抑制末端重复降噪；Agent 传递 TIFF 中间图的线性域与已降噪状态。
- 保留阶段前后图、残差及报告，新增 CLI 边界、回退与阶段集成回归测试。

## [0.1.20] - 2026-09-28

具名星点伪影门禁与外部工具规则补完（Named Star Artifact Gates & External Tool Rules）：

- **具名星点伪影门禁模块**（`scripts/artifact_gates.py`）：
  - 新建 `check_ringing`：亮星周围暗环检测，环形剖面 + 局部背景中值比对，定位到具体星坐标和环半径。
  - 新建 `check_star_bloat`：星点胀大检测，直接复用 `star_tools.measure_paired_star_profiles` 的成对 FWHM 比。
  - 新建 `check_star_layer_integrity`：星点层完整性（保留率 + 暗坑），步骤感知跳过（star_remove/star_process 时自动 skipped）。
  - 新建 `check_core_burning`：核心死白连通域检测，区分"输入自带饱和"与"处理引入烧毁"。
  - 聚合器 `evaluate_star_artifact_gates`：与标量门禁并存，failed/warning 升级 review_required，异常降级为 error 记录不阻断。
- **Review bundle 集成**（`scripts/agent_protocol.py`）：
  - `create_review_bundle` 自动运行星点门禁，结果写入 `review.json` 的 `star_artifact_gates` 字段。
  - 星点门禁触发时自动把 bundle status 升级为 `review_required`。
- **星系缩星保守化**（`scripts/pipeline.py`）：
  - `apply_target_aware_safety_rules` 规则 5（星系）：`star_reduction > 0.25` 时自动压回 0.25，记录 safety log。
  - 不照搬 aescaffre "星系场禁用缩星"——本地为 Telea/NS 修复 + 线性星点蒙版，与形态学 threshold 法的环状伪影机理不同；改为限制强度上限，连通域过滤已保护星云亮核。
- **外部工具规则补完**（`references/external_tools.md`）：
  - StarNet2 CLI `-m/--mask` vs `-n/--unscreen` 语义对照表：线性用 mask（可加性），非线性用 unscreen（Screen 混合），GHS/arcsinh 预拉伸会导致误判。
  - BXT 两趟法：Pass1 `correctOnly=true` 在校色前；Pass2 正常锐化在校色后；外部 BXT `adjustStarHalos` 必须 0.00。
  - NXT 多次轻量优于一次重手：四阶段各 denoise=0.25/detail=0.15，过降噪症状自查表。
- **星点症状速查与禁止清单**（`references/ai_common_pitfalls.md`）：
  - 9 条症状→原因→本地对策速查表，每条指回具体参数与 `artifact_gates` 门禁码。
  - 6 条星点禁止动作清单，含 SXT/StarNet 线性数据禁用 unscreen、NXT 禁止一次重手。
- **天体策略与 SKILL 文档同步**：
  - `target_awareness.md` 星系节补缩星保守化说明与 HII 区保护机制。
  - `SKILL.md` 安全规则表补星系缩星上限，质量审查标准补具名门禁说明，版本号升至 0.1.20。
- **全量测试**：新增 `tests/test_artifact_gates.py`（23 项测试），全量 351 passed，零失败，零回归。

## [0.1.19] - 2026-09-28

外部神经网络去星桥接推荐体系（External Neural Network Star Removal Bridge & Recommendation Engine）：

- **外部神经网络环境智能嗅探**（`scripts/neural_star_bridge.py`）：
  - 新增 `detect_neural_starnet_environment`：智能嗅探本地系统是否已部署 StarNet2 / StarNet++ / StarXTerminator 等 AI 去星二进制；
  - 自动识别版本（如 `2.5.4`）、计算硬件后端（如 Apple Silicon CoreML 硬件加速、ONNX、DirectML、CPU）及命令参数兼容特性（如 `-n/--unscreen` 星点剥离、`-s/--stride` 步长分块）；
  - 严格支持 `--starnet-path` 显式路径优先与不存在安全拦截。
- **多维度天体物理去星推荐决策树**（`scripts/neural_star_bridge.py`、`scripts/analyze.py`）：
  - 新增 `evaluate_neural_bridge_recommendation`：
    1. **星团绝对红线（Celestial Redline）**：球状星团/疏散星团（`globular_cluster`、`open_cluster`、`star_cluster`）恒星即主体目标，严格执行 `DISALLOWED` 禁令，绝对禁止去星；
    2. **去星质量与残星受损响应**：当内置形态学方法评分受限或微弱星云受损时，给出 `STRONGLY_RECOMMENDED`；
    3. **星场密度自适应感知**：当处于密集/极繁星场（`dense`、`very_dense`，覆盖率高）时，自动推荐使用 StarNet 神经网络消除微星干扰；
    4. **本地硬件就绪联动**：检测到本机已就绪 CoreML 加速二进制时，智能提升推荐优先级并输出命令行直通指导。
- **黄金标准 16-bit 线性载荷导出与无损通信**（`scripts/neural_star_bridge.py`、`scripts/pipeline.py`）：
  - 新增 `export_starless_payload`：在线性去噪后阶段（Phase 4）无损导出 16-bit uint16 RGB TIFF（`starnet_payload_linear.tif`），严格剥离 Alpha 通道并精准映射到 $[0, 65535]$，提供外部去星工具的最佳线性动态范围输入；
  - 命令行新增 `--export-starnet-payload` 与 `--starnet-recommendation {auto, always, never}`。
- **外部执行与管线回流一键指令生成**（`scripts/neural_star_bridge.py`、`scripts/pipeline.py`）：
  - 新增 `build_bridge_run_instructions` 与 `format_bridge_recommendation_card`：自动生成开箱即用的外部 StarNet2 执行命令（包含步长与 unscreen 路径）和无缝回流管线的恢复命令（`--external-starless`）；
  - 在控制台输出精美易读的桥接诊断卡片，并把桥接元数据完整持久化至 `manifest.json` 与 `result` 的 `neural_star_removal_bridge` 节点中。
- **外部无星图真实性校验门禁**（`scripts/neural_star_bridge.py`、`scripts/pipeline.py`）：
  - 新增 `verify_external_starless`：对通过 `--external-starless` 接入的外部图层进行几何尺寸一致性、三通道结构与星点能量提取健康度审查，防范尺寸错位与空星层。
- **全量测试与实测全景验证**：
  - 新增测试模块 `tests/test_neural_star_bridge.py`（9 项专属测试 100% 通过）；
  - 全量单元测试 **319 passed**（零失败，零回归，零额外第三方依赖）；
  - 在 DWARFmini-C50 双窄带 FITS 上实测验证，成功导出 16-bit uint16 标准载荷 `starnet_payload_linear.tif`，自动识别为 `STRONGLY_RECOMMENDED` 并精准生成 Apple Silicon CoreML 加速执行命令。

## [0.1.18] - 2026-09-28

质检角部均匀度天体感知（Celestial-Aware Corner Uniformity Assessment）：

- **天体物理 $3\sigma$ 背景与信号解耦引擎**（`scripts/quality_metrics.py`）：
  - 新增核心函数 `analyze_corner_uniformity`：稳健测量全图天空背景基线 $B_{\text{sky}}$ 与弥散度 $\sigma_{\text{sky}}$，计算 $3\sigma$ 天体检出门限；
  - 像素级统计四角区域（TL, TR, BL, BR）的天体结构覆盖率与局部纯背景底电平；
  - **深空天体延伸准则（Celestial Extension Criterion）**：确立画面主体天体核心与局部角区的能量对比约束（`inner_peak >= patch_mean * 1.20` 且 `core_contrast >= 3.5`），严密区分“真实深空天体结构延伸”与“单角孤立光害倾斜/漏光缺陷”。
- **天体主导角智能感知与质检门禁豁免**（`scripts/agent_protocol.py`、`scripts/pipeline.py`）：
  - 升级 `evaluate_quality_gates` 中 `CORNER_NONUNIFORM` 门禁判定：当检测到真实天体结构显著延伸至角区（如 C50 西南角覆盖率高达 94.1% 的 OIII 氧辐射云），且纯背景角平整度达标时，自动标记为 `passed`，彻底根除假阳性阻断；
  - Phase 1 DBE 后的 CP1 检查点同步接入天体感知分析器，消除控制台误导性警告；
  - `metrics` 与 `manifest.json` 丰富四角诊断字典 `corner_analysis`，完整记录天体覆盖率与豁免审计理由。
- **全量测试与实测飞跃**：
  - 新增测试模块 `tests/test_corner_uniformity_celestial_awareness.py`（5 项专属测试全部通过）；
  - 全量单元测试 **310 passed**（100% 通过，零额外第三方依赖，零回归）；
  - 在 DWARFmini-C50 双窄带 FITS 上实测：
    - 西南角 94.1% OIII 氧辐射云被自动识别为天体延伸；
    - 纯背景角均匀度比值精准测定为 **1.19x**（远优于门禁阈值 3.0x）；
    - `CORNER_NONUNIFORM` 门禁由 `failed` 转变为 **`passed`**，彻底解除错误阻断！

## [0.1.17] - 2026-09-28

背景中性化锁定闭环迭代与饱和度暗部防压死守护（Background Neutralization Lock & Saturation Floor Guard）：

- **终极背景中性化锁定引擎自适应闭环迭代**（`scripts/color_tools.py`、`scripts/pipeline.py`）：
  - 核心模块 `lock_background_neutrality` 升级：引入自适应闭环对齐机制（`max_iters=2, cast_tolerance=0.05`）；
  - 单次平滑加性平移后，立即按照 `quality_metrics` 严格评估标准检验各通道中位数与色偏量级；若因唤醒单通道贴零像素导致分布偏移，自动启动第二轮高精度微调闭环；
  - 在全流程成片阶段输出中间件 `10b_background_locked.tif`，并在 manifest 与审计报告中精准记录闭环对齐指标；
  - 彻底根治背景通道失衡与色偏漂移，使 C50 实测背景色偏量级从 **0.8186 锐减至 0.0211（下降 97.4%）**，彻底消除 `BACKGROUND_COLOR_CAST` 质量门禁警告！
- **色彩饱和度暗部防压死与通道截断安全守护**（`scripts/color_tools.py`）：
  - 升级 `enhance_saturation`：引入基于三次 Hermite（smoothstep）的动态平滑背景保护蒙版，彻底终结暗部硬阶跃；
  - 设定饱和度上限 $S \le 0.98$，杜绝 $S=1.0$ 导致的 RGB 最小通道塌缩至绝对 0；
  - 引入底电平安全守护（Pedestal Floor Guard），保证非零暗部像素的最小弱通道不被饱和度增益剪切至零，从源头消灭单通道塌缩病态像素。
- **全量测试与实测验证**：
  - 新增测试模块 `tests/test_background_neutralize_lock.py`（7 项专项测试全部通过）；
  - 全量单元测试 **305 passed**（100% 通过，零额外安装第三方依赖，零回归）；
  - 在 DWARFmini-C50 双窄带 FITS 上重新全流程出片验证：
    - `c50_v4_locked_sho.jpg` 背景三通道中位数精准收敛：R=0.0404, G=0.0399, B=0.0408；
    - 背景色偏量级仅 0.0211（远低于门禁阈值 0.08），星云金黄主体与星点自然光谱完全保留！

## [0.1.16] - 2026-09-28

解耦双窄带假彩色调色板与自然恒星光谱真彩色（Decouple Palette and Natural Stars）：

- **天体物理标准屏幕混合模式（Screen Blending）**（`scripts/star_tools.py`、`scripts/pipeline.py`）：
  - 升级 `combine_starless_stars`：增加 `blend_mode: str = "screen"`，采用天体物理无损屏幕混合公式 $1 - (1 - A) \times (1 - B)$；
  - 彻底终结简单加法（$A + B$）导致的恒星边缘被底层金色假彩色星云暴力泛黄、恒星核心硬切削顶溢出截断的问题；
  - 命令行与管线支持 `--star-blend-mode {screen, add}` 参数，并在 manifest / effective_config 中全面记录跟踪。
- **假彩色映射独立解耦与单图星点保护**（`scripts/pipeline.py`、`scripts/palette_tools.py`）：
  - 在多图层流程中，严格将哈勃假彩色置换限制在纯净无星底图（`04_starless_linear` $\to$ `08_palette_sho`）；
  - 在单图流程中，自动挂载软羽化 `star_mask` 传入 `apply_dualband_palette`，软锁定恒星天然色度向量，免遭假彩色置换。
- **深空暗背景中性灰锁定（Background Neutralization Anchor）**（`scripts/palette_tools.py`）：
  - 新增 `neutralize_dark_background`：在假彩色置换后自动计算深空低信号背景 R/G/B 中位数并对齐，彻底根除哈勃色板映射可能引入的背景底电平色偏，消除 `BACKGROUND_COLOR_CAST` 警告。
- **风格定调星点掩膜注入与防染色保护**（`scripts/style_tools.py`、`scripts/pipeline.py`）：
  - `apply_professional_style` 新增 `star_mask` 参数，动态豁免恒星像素接受暖色微调（`warmth`）与背景去饱和，防止成片阶段恒星被暖金风格滤镜二次染黄。
- **全量测试与实测画质飞跃**：
  - 新增测试模块 `tests/test_decouple_palette_stars.py`（9 项专项测试全部通过）；
  - 全量单元测试 **298 passed**（100% 通过，零额外第三方依赖，零回归）；
  - 在 DWARFmini-C50 双窄带 FITS 样本上实测生成 `c50_v4_decoupled_sho.jpg`：
    - 星点 $B/R$ 蓝红能量比从 **0.646 飙升至 0.934（激增 +44.4%）**；
    - 星点 $B/G$ 蓝绿能量比从 **0.750 提升至 0.963**；
    - 完美实现“星云呈现哈勃金黄（SHO）与深邃电离空腔，恒星 100% 保留自然蓝白与暖金光谱真彩”。

## [0.1.15] - 2026-09-28

参照 Starun 三大梯队系统演进（双窄带哈勃假彩色、线性反卷积与物理守卫全链路落地）：

- **第一梯队（P1 调色板）AstroBOH 经典双窄带转哈勃假彩色算法**（`scripts/palette_tools.py`、`tests/test_tier1_palette.py`）：
  - 100% 纯 Python / NumPy 实现 Carlo Mollicone 经典双窄带调色板；
  - 物理通道解离与合成硫二（Synthetic SII = $(H+O)/2$），支持 `SHO`、`HSO`、`HOO`、`OSH`、`OHS`、`HOS` 假彩色映射；
  - **保亮度色域重映射（Luminance-Preserving Gamut Mapping）**：在重映射为金黄与深蓝哈勃色板的同时，100% 严格保全底图 Rec.709 感官亮度与对比度结构不变。
- **第二梯队（P1 物理反卷积）线性域正则化 Richardson-Lucy 反卷积**（`scripts/deconvolution.py`、`tests/test_tier2_deconv.py`）：
  - 线性阶段（Phase 2c）执行物理反卷积与点扩散函数 (PSF) 还原，使星点实测 FWHM 从 3.88px 锐减至 2.70px（收缩 30%）；
  - 信号自适应掩膜门禁保护暗部信噪比，极端高光门禁杜绝吉布斯振铃（Gibbs ringing）黑边环。
- **第三梯队（P2 物理守卫）抖动叠边裁切与星晕平整守卫**（`scripts/border_tools.py`、`scripts/star_tools.py`、`tests/test_tier3_border_halo.py`）：
  - **叠边裁切（`apply_stacking_border_trim`）**：自动按行/列扫描通道统计一致性，切除场旋与抖动带来的低信噪比边缘条纹，避免带偏背景提取与颜色校准；
  - **亮星星晕守卫（`apply_star_halo_guard`）**：在去星后的大亮星周围 2~4 倍 FWHM 区域平滑色度，彻底消除强色差折射镜导致的假彩色晕环。
- **全量测试与实测产物**：
  - 新增 `tests/test_tier1_palette.py`、`tests/test_tier2_deconv.py`、`tests/test_tier3_border_halo.py`、`tests/test_tier_pipeline_integration.py`；
  - 全量单元测试套件全部通过（**289 passed**，零外部依赖安装）；
  - 实测输出 `c50_test_output_v4/C50_v4_sho.jpg` 和 `c50_test_output_v4/C50_v4_full_sho.jpg`。

## [0.1.14] - 2026-09-28

Caldwell 50 / 玫瑰星云双窄带深层缺陷根治与星云反差/纯黑背景画质跃升 (v3)：

- **Phase 1 DBE 大尺度弥散发射星云主体保护与 degree=1 线性平移约束**（`scripts/pipeline.py`、`scripts/gradient_removal.py`）：
  - 自动提取大尺度低频弥散星云主体排除区（`dbe_exclusion_mask`），避免把星云自身拟合成穹顶扣除；
  - 对占画幅 $>30\%$ 的超大视场天体自动约束为线性光害倾斜校正（`degree=1`），并引入中心采样点有效性守卫。彻底根治多项式拟合导致的中央挖坑与四角被动飙红，使星云对四角背景的反差从 **7.6 暴增至 26.3（提升 3.5 倍）**！
- **Phase 1 DBE 逐通道黑点中位对齐**（`scripts/gradient_removal.py`）：
  - `normalize_background_subtracted` 针对多通道输入分别计算各通道背景中值并分别对齐至 0，杜绝单标量导致的通道间底电平微弱脱节，确保各通道干净对齐至 `pedestal`。
- **Phase 6 CLAHE 暗区平滑滚降门禁（shadow_rolloff）**（`scripts/enhance.py`）：
  - 在 `apply_clahe` 中引入平滑暗区滚降（$L < 0.16$ 衰减至 1.0），确保 CLAHE 只专注于中高动态范围的星云细丝、涟漪与暗尘埃结构，彻底切断暗角残余噪声被均衡化强行拉升的放大链路。
- **Phase 9a 风格定调背景阈值上限与空腔 OIII 豁免保护**（`scripts/style_tools.py`）：
  - 设定背景判定上限（$\le 0.25$），并将双窄带中央 OIII 电离区（$(G+B)/(2R) \ge 0.42$）明确从暗部去饱和掩膜中豁免，让中央清澈的青蓝电离空腔与外围粉红花瓣层次分明；同时引入边界衰减保护，杜绝四角背景加温染色。
- **Phase 2 / 5c NGC 2244 疏散星团星色通透感恢复**（`scripts/color_tools.py`、`scripts/pipeline.py`）：
  - 双窄带下将星点白平衡强度由 1.0 调和至 0.45，保留大质量 OB 恒星在 OIII 连续谱的高能量，恢复疏散星团自然微冷的璀璨蓝白星光。
- **全量测试与实测产物**：
  - 新增 `tests/test_c50_v3_refinements.py`，全量测试套件增至 **278 passed**。
  - 在 DWARFmini C50 样本上实测生成 `c50_test_output_v3`，成片四角飙红彻底归零（$242 \to 1 \sim 12$），星云对角反差由负变正（$-179.7 \to +206.8$）。

## [0.1.13] - 2026-09-28

Caldwell 50 / 玫瑰星云双窄带全链路重构与星点雪花白斑/死红根治：

- **星点连通域面积自适应与明亮星点保护（根治雪花白斑/麻子）**（`scripts/star_tools.py`、`scripts/stretch.py`）：
  - 彻底修复 `_analyze_connected_components` 中 `area > max_area`（673 像素）对明亮恒星的一刀切误杀机制。引入紧凑明亮恒星判定（峰值显著性 $\ge 2.0$、长宽比 $< 2.5$ 且面积在画幅 $5\%$ 与 $1.5 \times \text{max\_area}$ 范围内），使密集星团核心亮星被精准保留在去星掩膜中。
  - 扩大去星掩膜膨胀半径（从 `fwhm * 0.25` 扩至 `fwhm * 0.6`），完整覆盖恒星 PSF 边缘扩散翼，彻底终结残存恒星光晕在 `04_starless_linear` 中被 `emission_stretch` 放大 700 倍打爆为雪花状白斑与黑边噪环的历史（`05_stretched_starless` 中 $>0.85$ 饱和白点从 125 处彻底归零，最高亮度从 0.9998 恢复为 0.5771）。
  - 在 `emission_stretch` 中引入高光双曲滚降（arcsinh rolloff），杜绝极端残余亮点失控爆炸。
- **Phase 2 发射星云黑点基线对齐（根治背景假性红偏）**（`scripts/color_tools.py`）：
  - `emission_nebula_calibrate` 针对极暗背景底电平（$<0.005$）引入各通道扣黑差值硬约束（$\le 0.00015$），防止因极端低分位噪声不对称波动导致 G/B 被过度扣除，将背景底电平 $R/G$ 从 $2.42$ 精准拉回至健康平衡的 **$1.16$**。
- **Phase 8 双窄带 OIII 青蓝光电离结构恢复与阴影保全**（`scripts/pipeline.py`）：
  - 摒弃荒谬的 $G > 1.03 \cdot R$ 判据，依据双窄带真实的 OIII/Hα 辐射比例（$(G+B)/(2R) \ge 0.42$ 且 $G \approx B$）精准提取中央空腔与边缘电离区的 OIII 蒙版（空腔检出率从 0.0% 提升至 41.1%），免除粗暴去绿（SCNR）与压蓝。
  - 调和发射星云阴影曲线（`shadows = 0.92`），彻底解除对 0.10~0.25 区域微弱 OIII 青蓝光与星云微细云气的暴力腰斩，使中央空腔 $R/G$ 从 $2.44$ 恢复至 **$1.43$**，真实呈现青蓝与品红交织的电离结构。
- **Caldwell 星表识别与双窄带物理先验自动直通**（`scripts/fits_io.py`、`scripts/analyze.py`）：
  - 扩展 `normalize_target_name` 正则全面支持 `C` / `CALDWELL` 编号，并在俗名表中收录全部常用 Caldwell 映射（`C 50` $\rightarrow$ `NGC2237`, `emission_nebula`）。
  - 在 `analyze.py` 中建立双窄带（Duo-Band / Narrowband）物理先验直通，自动推断发射星云并锁定对应策略。

## [0.1.12] - 2026-09-28

拉伸后链路 P1 级落地优化（时序理顺、背景纯净中性化与宽带星系色彩激活）：

- **Phase 8d 缩星时序与无星层保护门禁**（`scripts/pipeline.py`）：
  - 当流水线拥有独立无星层时，显式跳过 Phase 8d 在无星图上的盲目形态学腐蚀，彻底避免对星坑周围星云微细结构的无效侵蚀，并与星点层的独立缩星正确解耦。
- **Phase 9a 风格色温背景保护**（`scripts/style_tools.py`、`tests/test_post_stretch_refinements.py`）：
  - 引入边缘背景中值锚定的背景阈值估计，并将 `warmth` 加温严格绑定到天体有效结构信号蒙版，彻底阻断对深空暗背景的无差别加温染红，深空背景维持严格的 RGB 中性平衡。
- **Phase 8 宽带星系空间自适应色彩饱和度提升**（`scripts/pipeline.py`、`tests/test_post_stretch_refinements.py`）：
  - 终结非发射星云直接进入单一曲线压暗的贫弱历史，引入基于星系空间范围的自适应饱和度提升（旋臂平均饱和度提升 33.5%：$0.321 \rightarrow 0.429$），同时避开窄带去绿（SCNR）对宽带绿色成分的削减，完整展现出核球老年星族暖金与外盘年轻恒星群冷蓝的双温区分离。
- **全量测试与实测产物**：
  - 新增 `tests/test_post_stretch_refinements.py`，全量测试套件增至 274 passed。
  - 在 DWARFmini M31 样本上实测生成 `m31_test_output_v5`，成片背景纯净，星系色彩层次分明。

## [0.1.11] - 2026-09-28

根除无星层拉伸后出现的橙红圆斑伪影与星系核球“甜甜圈”反转凹陷两大视觉缺陷：

- **星坑色度保护（Inpaint Chroma Guard）**（`scripts/star_tools.py`、`tests/test_starless_artifacts_guard.py`）：
  - **根因定位**：折射式消色差望远镜二次光谱导致红色 PSF 扩散翼显著大于绿蓝光。去星掩膜虽能覆盖核心，但在外围边缘处残存极高红比（实测 $R/G = 2.7 \sim 8.2$）；OpenCV Navier-Stokes 修复从外向内插值，将红光填满整个星坑。在 Phase 5 非线性高增益拉伸时被急剧放大，形成刺眼的红橙色圆盘伪影。
  - **色度平滑约束**：在保持 Inpaint 亮度不变的前提下，计算全图低频平滑背景色彩场，将星坑内部色彩通道比例软约束至周围背景（羽化蒙版过渡）。实测拉伸后星坑 $R/G$ 从 $1.75 \sim 2.68$（刺眼红斑）精准收敛至 **$1.000$**（完美与平滑背景中性融合），彻底消灭全图刺眼红橙圆斑。
- **星系核球自然连通域组件排除（Natural Component Exclusion）**（`scripts/star_tools.py`、`scripts/pipeline.py`）：
  - **根因定位**：v3 版本使用人工几何硬圆盘掩膜（`core_exclusion_mask`），在核球边界处形成阶跃式硬切断层（圆盘内未去星、圆盘外去星插值），产生边缘台阶。
  - **拓扑特征自然判别**：在 `detect_stars_multiscale` 连通域组件分析层引入 `galaxy_center`，直接基于质心距离与连通域面积判别核球单体组件，标记为 `reject_reason = "galaxy_nuclear_core"` 直接排除，从源头上保留核球自然边缘与真实椭圆拓扑。
- **高动态核心拉伸防反转保护（HDR Stretch Anti-Inversion Guard）**（`scripts/stretch.py`、`scripts/pipeline.py`）：
  - **根因定位**：在 `masked_ghs_stretch` 中，归一化采用固定高光百分位（`highlight_pctl = 99.9`，约 0.023），而 M31 等高动态天体核心亮度高达 0.295。硬截断 `np.clip(..., 0, 1)` 将核球中心整整数十像素大面积饱和为 1.0；在后续感官亮度增益除法 $gain = L_{stretched} / L$ 下，中心越亮增益越小，直接把高耸的山峰反转削成了火山口凹陷（甜甜圈凹坑）。
  - **高光无截断自适应动态范围**：为星系、行星状星云及高动态天体配置 `highlight_pctl = 100.0`，并在 `masked_ghs_stretch` 内增加峰值保护（当峰值显著高出分位数时不截断饱和）。实测核球中心径向亮度剖面从凹陷恢复为从峰值（0.809）向外单调连续平滑递减，彻底抹平甜甜圈凹坑。
- **测试覆盖**：
  - 新增 `tests/test_starless_artifacts_guard.py`，全量回归测试增至 272 passed。
  - 在 DWARFmini M31 样本上实测生成 `m31_test_output_v4` 并通过视觉审查与像素级检验。

## [0.1.10] - 2026-09-28

借鉴 Starun 核心引擎的工业级护栏设计，加固图像处理安全契约并精简废弃模块：

- **引入星体光学缺陷与几何微修复系统**（`scripts/stellar_repair.py`、`scripts/stellar_shape_repair.py` 与 `references/stellar_repair.md`）：
  - **RGB 亚像素通道平移对齐**：自动在 $\le 1.5\text{px}$ 范围内修正大气折射与光学色散导致的 RGB 彩边。
  - **亮星洋红/紫晕受控抑制**：仅作用于恒星衰减翼（stellar wings），采用亮度保持型色度压制，绝不损伤星云发射区的正常颜色。
  - **椭圆 Moffat PSF 空间场建模与形变修复**：支持全局及平滑二次空间 PSF 场拟合，改善边缘彗差与星点圆度，具备严格的通量、质心与轴比回滚硬门禁。
  - **流水线 Phase 2b 深度集成**：在 `pipeline.py` 中作为 Phase 2b 可选步骤，支持 `--stellar-repair`、`--stellar-repair-mode`、`--stellar-repair-strength`，输出诊断报告 `02b_stellar_repair_report.json` 与局部比对 `02b_stellar_repair_preview.tif`。
  - 在 `star_tools.py` 中补全配对星点轮廓测量（`measure_paired_star_profiles` 与 `_profile_shape_at`）。
  - 新增 15 个测试（`tests/test_stellar_repair.py`），全量回归测试增至 270 passed。
- **引入计算机视觉护栏契约**（`references/cv_guardrails.md`）：
  - 明确严格的图像状态不变量声明（`array_dtype`、`value_range`、`data_domain`、`bit_depth_source`），禁止将线性浮点数据隐式转换为 `uint8` 或拉伸显示数组做处理。
  - 规范 OpenCV/scikit-image 边界准则与坐标后缀命名（`_rc` vs `_xy` vs `_bbox_xywh`）。
  - 为修补（Inpaint）与局部对比度（CLAHE）建立硬性上限阈值，防止合成不存在的天体结构。
  - 补充各大天体类型（发射星云、星系、星团、反射星云等）的物理安全约束规范。
- **端到端处理链路细节优化（M31 实测验证通过）**：
  - **Header 天体先验前置**（`scripts/analyze.py`）：在图像特征推断前优先调用 `resolve_celestial_target` 解析 FITS/XISF Header 中的 `OBJECT`/`TARGNAME`，消除线性未拉伸大星系因致密核球被纯几何算法误判为球状星团的隐患。
  - **拉伸方法与参数传递修复**（`scripts/analyze.py`、`scripts/stretch.py`、`scripts/pipeline.py`）：统一推荐方法命名为 `'arcsinh'`，对齐 `'luminance_arcsinh'` 别名并确保 `factor` 参数完整传递，同时高动态天体优先升级为 `masked_ghs`。
  - **星系盘面局部增强半径保底**（`scripts/pipeline.py` Phase 8b）：当目标为星系时，自动增强半径保底保证 $\ge \min(H, W) \times 0.38$（约 410px），防止仅覆盖致密小核球，确保整个星系盘面与尘埃带得到充分的局部对比度增强。
  - **星系致密核球防去星保护蒙版（Core Exclusion Mask）**（`scripts/star_tools.py`、`scripts/pipeline.py`）：在 Phase 4 为星系核心构建软边缘防去星保护蒙版，彻底终结核球峰值被小尺度 Top-hat 剥离（原先峰值从 0.35 削平至 0.11，削掉 70%）并在合成层被拉爆为 `(1.0, 1.0, 1.0)` 纯白死心的缺陷，成片核心成功呈现出自然的暖黄老年星族色调（`RGB=0.924, 0.842, 0.666`，`R/G=1.098`）与细腻平滑的高光层次。
  - **保比例感官亮度增益乘法与 CLAHE 高光滚降**（`scripts/enhance.py`）：彻底重构 `apply_clahe` 与 `apply_curves`，摒弃 CIELAB L 单通道操作旧模式，转为保比例的感官亮度增益乘法，消除阴影压暗时背景色偏急剧放大的顽疾（压暗阴影时背景 B/G 从旧版的 1.368 严格锁死在 1.130 并平滑收敛至 1.089），同时引入高光软滚降衰减防止亮核被顶爆。
  - **DBE 目标排除区保护**（`scripts/gradient_removal.py`、`scripts/pipeline.py`）：平场拟合采样点支持目标排除蒙版，自动避开大星系盘面，防止大视场望远镜数据中微弱的星系外盘被当成光害坡度扣除。
  - **星系风格黑位安全保护**（`scripts/style_tools.py`）：将 `galaxy_core` 预设的 `black_floor` 从 0.018 调至 0.008，防止弱信号星系外盘被截断，彻底消除 `BACKGROUND_LOW` 门禁告警。
  - **图像保存目录自动创建**（`scripts/fits_io.py`）：`write_image` 写入前自动确保父级目录存在，避免因目标输出文件夹未提前创建而引发 `FileNotFoundError`。
- **清理过时废弃脚本**：
  - 移除废弃的 `scripts/compose_starnet_layers.py` 及其测试 `tests/test_compose_starnet_layers.py`，无星层处理与重组统一收敛至 `enhance_starless.py` 与 `stellar_recompose.py`。

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
