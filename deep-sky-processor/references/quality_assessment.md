# 扩展质量审查

本文补充四类定量审查：分区 SNR、星点圆度、星表色彩真实性和实际分辨率。指标用于发现采集或处理问题，不能替代对原图、中间图和最终成片的视觉审查。

## 能力边界

- `quality_metrics.py` 当前直接输出亮度分位数、裁切比例、角落均匀度、高频能量、星点覆盖率和线性 FWHM。
- `star_tools.py` 已计算候选星点的圆度、长宽比和 FWHM，但主管质量报告尚未汇总完整空间分布。
- `plate_solve.py` 可获得 WCS 和像素尺度，也可投影用户提供的星表；它不会自动下载测光星表。
- SNR 应优先在线性、未拉伸、未强降噪的数据上测量。
- 色彩真实性评估需要 plate solving、测光星表、滤镜/相机响应和未饱和恒星。仅凭最终 JPG 不能完成严格测光判断。
- 理论分辨率需要像元尺寸、焦距、口径、波长和实际视宁度。缺少其中关键参数时只能报告像素 FWHM。

## 推荐审查顺序

```text
线性母版
→ 分区 SNR
→ 星点 FWHM、圆度和空间趋势
→ WCS / 像素尺度
→ 星表色差
→ 理论与实际分辨率比较
→ 最终图动态范围和伪影审查
```

先运行现有基础审查：

```bash
python3 scripts/quality_metrics.py output.tif \
  --manifest intermediates/manifest.json \
  --output quality.json
```

## 1. 分区 SNR 评估

### 为什么必须分区

单个全图 SNR 会混合背景、星云、亮核和星点，没有明确物理意义。至少分别评估：

- 纯背景区；
- 暗弱目标区；
- 中等亮度结构区；
- 亮核或高光区；
- 图像中心与四角。

区域应记录像素坐标、天空坐标或蒙版来源，以便不同处理版本复测。

### 线性数据估计

对一个目标区域和邻近背景区域，可使用稳健估计：

```text
background = median(background_pixels)
noise_sigma = 1.4826 × MAD(background_pixels)
signal = median(target_pixels) - background
SNR_per_pixel = signal / noise_sigma
```

其中：

```text
MAD(x) = median(|x - median(x)|)
```

对于总通量测量：

```text
SNR_integrated ≈
    sum(target - background) /
    (noise_sigma × sqrt(N_effective))
```

`N_effective` 不能简单等于像素数量。配准插值、降噪、卷积和 drizzle 会产生相关噪声，应通过多个同尺寸空白孔径的通量离散度估计。

### 背景 SNR

纯背景理论信号接近零，不报告“背景 SNR 越高越好”。背景区应报告：

- `median`；
- `MAD sigma`；
- 负值比例；
- 大尺度背景残差；
- 不同区域噪声比。

中心与四角噪声差异明显，通常提示平场、渐晕校正、堆栈覆盖或局部归一化问题。

### 非线性成片

最终 JPG/TIFF 的曲线、黑位、局部对比和降噪改变了噪声统计，只能用于版本间相对比较：

- 同一区域局部均值；
- 同一区域局部标准差；
- 结构对比与背景颗粒；
- 降噪前后细节保持率。

不能把拉伸后测得的 SNR 当作传感器或积分数据的物理 SNR。

### 参考判定

不设置所有目标通用的固定通过线。建议记录：

| 区域 | 重点判断 |
|---|---|
| 背景 | 噪声是否均匀，是否有色块或相关纹理 |
| 暗弱结构 | 信号是否稳定高于多个邻近背景孔径 |
| 中亮结构 | 拉伸后是否保留连续层次 |
| 亮核 | 是否饱和，HDR 是否产生硬边 |
| 四角 | SNR 是否因覆盖不足或渐晕明显下降 |

若所谓细节只在单个处理版本出现，且原始线性数据的局部 SNR 不支持，应视为伪影风险。

## 2. 星点圆度评估

### 核心指标

对未饱和、孤立、信噪足够的恒星拟合二维 PSF：

```text
FWHM_major = 长轴半高全宽
FWHM_minor = 短轴半高全宽
axis_ratio = FWHM_minor / FWHM_major
ellipticity = 1 - axis_ratio
```

连通域圆度可作为辅助指标：

```text
circularity = 4π × area / perimeter²
```

圆度接近 1 表示更接近圆形，但它对阈值、采样不足和饱和星很敏感。跟踪精度优先使用 PSF 长短轴和方向角，不应只看连通域圆度。

### 样本筛选

排除：

- 饱和星和大光晕星；
- 双星、星团核心和星云亮核；
- 图像边缘被裁切的星；
- 信噪过低的微弱候选；
- 明显受衍射芒影响的亮星。

至少分别统计中心、四角和边缘区域，并报告中位数、P90 和样本数量。

### 空间模式诊断

| 圆度模式 | 可能原因 |
|---|---|
| 全场同方向拉长 | 赤道仪跟踪、周期误差、风振或配准问题 |
| 只在单帧方向变化 | 阵风、机械抖动或导星丢失 |
| 四角沿径向拉长 | 场曲、彗差、后焦距问题 |
| 四角沿切向拉长 | 光学倾斜或像散 |
| 一侧明显更差 | 传感器倾斜、镜组偏心 |
| RGB 拉长方向不同 | 通道错位、大气色散或色差 |

### 建议报告字段

```json
{
  "star_shape": {
    "n_stars": 236,
    "fwhm_major_median_px": 3.2,
    "fwhm_minor_median_px": 2.8,
    "axis_ratio_median": 0.875,
    "ellipticity_median": 0.125,
    "ellipticity_p90": 0.22,
    "position_angle_deg": 87.0,
    "center_vs_corner_fwhm_ratio": 1.28
  }
}
```

### 解释边界

- 圆度差不必然等于跟踪差，必须结合全场方向分布。
- 欠采样图像中的单个星点只有 1–2 像素时，圆度结果不稳定。
- 去卷积、缩星和锐化会改变星形，采集质量应在线性、处理前母版上评估。
- 最终成片只检查处理是否新增黑圈、硬边或方向性变形。

## 3. 色彩真实性评估

### 前提

需要：

1. 对图像做 plate solving，得到 WCS。
2. 获取包含恒星坐标和测光数据的星表。
3. 将星表坐标投影到图像。
4. 对未饱和、孤立恒星做孔径测光。
5. 拟合仪器颜色到标准颜色的转换关系。

可使用 Gaia、APASS 或其他适合波段的测光星表。Gaia `BP-RP` 不是相机 RGB 的直接真值，必须使用颜色转换模型；APASS 的 B/V/g′/r′/i′ 也需要结合滤镜响应。

### 恒星筛选

排除：

- 饱和或接近饱和的恒星；
- 低 SNR 恒星；
- 双星和拥挤区域；
- 星云背景强烈变化区域；
- 有明显光晕、衍射芒或传感器溢出的恒星；
- 变星和星表质量标记异常的对象。

### 色差模型

先计算仪器颜色：

```text
g-r = -2.5 log10(Fg / Fr)
r-b = -2.5 log10(Fr / Fb)
```

再拟合标准星表颜色，例如：

```text
catalog_color ≈ a + b × instrumental_color
```

评估：

- 颜色零点偏差；
- 颜色斜率偏差；
- 残差 RMS；
- 残差随画面位置、星等和颜色指数的趋势；
- 背景中性与恒星颜色是否同时成立。

严格测光可以使用 CIE Lab 的 `ΔE` 作为显示色差辅助指标，但只有在相机 RGB 已通过可靠颜色模型映射到标准颜色空间后才有意义。

### 常见模式

| 残差模式 | 可能原因 |
|---|---|
| 所有星统一偏红/偏蓝 | 白平衡或颜色零点错误 |
| 蓝星正确、红星偏差大 | 颜色变换斜率或滤镜响应不匹配 |
| 画面一侧颜色不同 | 色度梯度、平场或月光污染 |
| 亮星偏白 | 通道饱和或高光压缩 |
| 暗星颜色散布很大 | 测光 SNR 不足 |

### 发射星云和窄带边界

- 星表校色主要约束恒星颜色和连续谱背景。
- SHO/HOO 等伪色窄带不存在“自然 RGB 真色”目标，应评估通道真实性和色调一致性，而不是与星表 RGB 强行匹配。
- 窄带星点可用 RGB 星点层替换或单独校色，但必须记录合成方式。
- 不能为了中性背景破坏真实 Hα/OIII 大尺度比例。

### 当前工具接入

`plate_solve.py` 可提供 WCS 和投影用户星表：

```bash
python3 scripts/plate_solve.py linear_rgb.fits \
  --output-dir plate_solve_work \
  --output-json plate_solve.json \
  --catalog-json photometric_catalog.json
```

当前项目尚未实现完整的星表下载、孔径测光和颜色残差报告。因此在没有外部 SPCC/PCC 或测光流程时，只能报告“启发式颜色审查”，不能声称已通过星表真实性验证。

## 4. 分辨率评估

### 像素尺度

若已知像元尺寸和焦距：

```text
pixel_scale_arcsec =
    206.265 × pixel_size_um / focal_length_mm
```

若图像已 plate solve，应优先使用 WCS 实测像素尺度，因为裁切、重采样和 drizzle 可能改变输出尺度。

### 实际角分辨率

使用线性母版中未饱和恒星的中位 FWHM：

```text
measured_resolution_arcsec =
    median_FWHM_px × pixel_scale_arcsec
```

同时报告中心和四角 FWHM，避免全图单值掩盖场曲或倾斜。

### 理论衍射极限

圆孔 Airy 斑第一暗环角半径近似：

```text
diffraction_limit_arcsec =
    0.252 × wavelength_nm / aperture_mm
```

例如可见光评估通常选择 550 nm；窄带应使用实际中心波长。该值只是光学理想上限，不是地面长曝光图像必然达到的结果。

### 实际限制因素

实际 FWHM 通常由多项因素共同决定：

```text
FWHM_actual² ≈
    seeing² +
    tracking² +
    focus² +
    optical_aberration² +
    sampling²
```

这是诊断近似，不应把各项当作严格独立高斯过程。

### 采样评估

- `FWHM < 2 px`：明显欠采样，圆度和细节估计不稳定。
- `FWHM ≈ 2–3 px`：通常接近合理采样。
- `FWHM > 4–5 px`：可能过采样，也可能是视宁度、跟踪或对焦较差。

是否过采样不能仅靠像素 FWHM判断，还要结合角秒/像素和当地视宁度。

Drizzle 可以改善有足够子像素位移的欠采样数据表达，但不会突破原始光学与信噪限制。

### 达成率

不要简单用“理论极限 / 实测 FWHM”作为唯一评分。建议并列报告：

```json
{
  "resolution": {
    "pixel_scale_arcsec": 1.24,
    "fwhm_median_px": 2.9,
    "measured_fwhm_arcsec": 3.6,
    "diffraction_limit_arcsec_550nm": 1.1,
    "estimated_seeing_arcsec": 2.5,
    "sampling": "adequate",
    "limiting_factor": "seeing_or_tracking"
  }
}
```

若没有独立视宁度数据，只能说“实测分辨率高于衍射极限，可能受视宁度、跟踪、对焦共同限制”，不能断言具体原因。

### 分辨率审查不能做什么

- 高频能量高不等于真实分辨率高；噪声和锐化振铃同样会提高高频能量。
- 反卷积后 FWHM 变小不代表采集分辨率提高。
- 输出像素更多不代表光学分辨率提高。
- AI 超分辨率结果禁止用于理论分辨率达成判断。

## 统一报告模板

```json
{
  "snr_regions": [
    {
      "name": "background",
      "median": 0.00042,
      "mad_sigma": 0.00008
    },
    {
      "name": "faint_nebula",
      "signal_above_background": 0.00031,
      "snr_per_pixel": 3.88
    }
  ],
  "star_shape": {
    "n_stars": 236,
    "fwhm_median_px": 2.9,
    "axis_ratio_median": 0.91,
    "ellipticity_p90": 0.18
  },
  "color_fidelity": {
    "method": "catalog_photometry",
    "catalog": "Gaia/APASS",
    "n_reference_stars": 84,
    "color_residual_rms_mag": 0.06
  },
  "resolution": {
    "pixel_scale_arcsec": 1.24,
    "measured_fwhm_arcsec": 3.6,
    "sampling": "adequate"
  },
  "limitations": [
    "seeing was not independently measured"
  ]
}
```

未实际计算的字段必须写为 `null` 或从报告中省略，不能填入经验猜测值。

## 通过与回退

| 发现 | 处理建议 |
|---|---|
| 暗弱结构 SNR 不稳定 | 降低拉伸/锐化，增加积分时间 |
| 全场星点同向拉长 | 检查导星、周期误差、风振和配准 |
| 四角 FWHM 显著变差 | 检查后焦距、倾斜、场曲和裁切 |
| 星表颜色残差有位置趋势 | 检查色度梯度和平场 |
| 亮星颜色统一变白 | 检查通道饱和和高光压缩 |
| 实测分辨率远差于采样能力 | 分离检查视宁度、对焦、跟踪和光学 |
| 锐化后高频升高但星点振铃 | 回退锐化，不视为分辨率改善 |

## 最小审查结论

每次严肃处理至少明确：

- SNR 是在线性还是非线性数据上测量；
- 星形样本数量、FWHM 和圆度/偏心率；
- 色彩是启发式审查还是星表测光验证；
- 像素尺度来源是 WCS 还是设备参数；
- 分辨率结论是否包含独立视宁度依据；
- 哪些指标因缺少元数据或外部星表未能计算。

## 具名星点伪影门禁（`scripts/artifact_gates.py`）

**两条路径都会运行**：Agent-in-the-loop 的 review bundle（`review.json` 的
`star_artifact_gates`），以及 `pipeline.py` 主管线（`result.json` 的同名顶层键）。
主管线在 Phase 9 星点合成后快照一张**同域**参照图，末端与成品成对比较，因此
`STAR_BLOAT`/`STAR_LAYER_LOSS`/`STAR_HOLES` 都能跑（此前它们只在会话路径生效，
导致星点胀大在主管线里无人报警）。

- **STAR_RINGING**：固定参照图星位，以屏蔽邻星的局部背景平面检测暗环；同域配对
  比较欠冲增量，已有场结构只保留诊断，不因本阶段再次升级状态。
- **STAR_BLOAT**：星点整体胀大，复用 `measure_paired_star_profiles` 的成对 FWHM 比。
- **STAR_LAYER_LOSS / STAR_HOLES**：星点层保留率不足或新增/加深的中心暗坑，定位到具体星坐标。
  振铃与暗坑的背景估计不足、配对残差不可靠或参照域不可比时保留审查，不自动判通过。
  `warning` 且 `escalate=False` 表示已有结构或已证明未变化的背景诊断。
  门禁半高核心尺度是处理诊断，不代表物理分辨率；主管线参照阶段为
  `post_star_combine`，只能归因合成后的末端润色，不能据此豁免更早阶段的损伤。
- **CORE_BURNING**：处理引入的死白连通域，区分"输入自带饱和"与"处理烧毁"。
- **CHANNEL_CLIPPING**：逐通道检查 `>=0.995` 的裁切区域，定位新增或扩大区域；复用 25/80 像素阈值。同域处理比较阶段前后，跨域拉伸检查显示结果并保留归因边界。拉伸回退仍需重新验证，末端校色与风格记录首次损伤阶段。

这些门禁与标量门禁（BACKGROUND_CRUSHED / CORNER_NONUNIFORM 等）**并存**：数值门禁是
审查触发器，具名门禁提供定位证据。两者都不声称"视觉质量合格"——最终判断仍由
AI/human critic 完成。

主管线路径下，具名门禁触发是**加法式**升级：只在原本 `success` 时升级为
`review_required`，且只把 `escalate=True` 的 warning/failed 写进 `warnings`。

## 标量门禁 `INPAINT_FOOTPRINT`（去星修补足迹）

盯住「线性域 inpaint 补丁被拉伸放大成可见斑块」。判据是**联合**的：补丁缺纹理
（核心 std < 35% × 环带 std）**且**与邻域有亮度落差（|Δ| ≥ 0.020）。
单独任一条都会大量误报（实测只看落差命中 63%、只看纹理 36%；联合命中 ~10%）。

- 量的是**合成星点之后**的成品（斑块是星点层 screen 混合后才出现的），星位来自
  线性域星点蒙版，FWHM 必须用**线性域**实测值。
- `warning`（命中 ≥2%）只记录不升级；`failed`（≥8%）升级为 `review_required`。

## 保真度门禁（`scripts/fidelity_gates.py`）

与星点伪影门禁**并存**，补齐「处理有没有放大噪声、破坏色度」这一维度。
`BACKGROUND_COLOR_CAST` 只看**静态**每通道背景中位数比，严重偏色的输出
（例如背景 R/G≈3.3）能一路通过；本模块做**逐像素相对参照图的**漂移测量。

参照图是同一份拉伸输入以固定 `pi_mtf` 保守曲线渲染的结果（与成品的唯一差异
是曲线），因此度量的是「**这条曲线相对保守基线损伤了多少**」。

| 门禁码 | 含义 |
|---|---|
| `BACKGROUND_RELATIVE_NOISE_BOUNDED` | 相对背景噪声增益（σ/中位 之比）。**唯一具备升级能力的门** —— degraded 1.1407、hard 1.60（硬上限不可放松） |
| `BACKGROUND_NOISE_BOUNDED` | 绝对 σ。**只记录不判定**（`uncalibrated`）—— 实测健康组跨目标相差约 150 倍（M31 6.0e-05 vs NGC6888 9.2e-03），单一阈值必然要么全静默要么全误报 |
| `HUE_PRESERVED` | 逐像素三通道 chromaticity 漂移 p95（L∞，非 L2） |
| `SUBJECT_CHROMA_VISIBILITY_PRESERVED` | 主体色度 SNR 保留比 |
| `BACKGROUND_CHROMA_NOISE_BOUNDED` | 背景色度噪声增益 |

**三态语义**（承接「阈值按灾难形态标定会留静默通过区」的教训）：

- 健康 ⇒ **不 append**（完全静默，避免在 gates/warnings 里造噪音）
- `degraded` ⇒ 进 `warnings` **可见但不升级**，且**不产生 quality_state issue**
- `unavailable` / `uncalibrated` ⇒ 不可测量或未标定，**均不等于健康**。
  「测不到」绝不能报成 0.0 当健康值 —— 那是静默通过区的来源。

全部度量都是**显示域、比较性**的，不是目录测光校色，也不是物理 SNR 测量；
每条门禁的 `evidence["physical_snr"]` 恒为 `False`。阈值来源与复现命令见
`fidelity_gate_calibration.md`。该文档同时记录了两个被实测**否决**的设计假设
（绝对 σ 不可用；「压主体」不是主体色度保留比的有效劣化对照），改阈值前先读它。
阈值不暴露为 cfg —— 相对噪声的硬上限 1.60 连放宽接口都不给。

## 配置指纹（`scripts/config_fingerprint.py`）

证明「这份产物是用哪套配置产出的」。

- `artifact_sha256` 只能证明「**产物文件**没被改」，**证明不了「产生它的配置没被改」**。
  没有指纹时的静默失效路径：配置 A 跑出产物 P → 改配置 B → 重跑 →
  `parent_state` 把**配置 A 的 issues 继承到配置 B 的产物上**。
- **累计指纹**（借鉴 starun 侧 `task_plan.py:208-229`）：
  `fingerprint(N) = hash(Stage 1..N 的全部配置 + 输入 SHA)`。
  「改 Stage 2 参数 ⇒ Stage 5 指纹失效」是这条性质的直接后果。
- **指纹不同即切断父 issue 继承**（`quality_state.build_quality_state` 的
  `config_changed`）；父 state 无指纹（历史产物）时**保守继承**，不静默丢弃已有 finding。
- `result.json` 带 `config_fingerprint`（20 位）与 `config_used`（按阶段分组的
  实际参数清单）—— 报告能自证「我是用哪套参数判的」。
- 参数按 82 条精确映射 + 16 条前缀规则归阶段；**未识别的键进 `_unassigned`
  桶并照样参与哈希** —— 漏算会让「改了某参数却算出同样指纹」。
- **与 `runtime_info.source_fingerprint()` 正交**：source 管「跑的是哪版代码」
  （全部 `scripts/*.py` + `references/**` + `SKILL.md`），config 管「用的是哪套参数」。
  改代码只动前者，改参数只动后者。不要把两者混为一谈。

## 失败处置策略（`scripts/failure_policy.py`）

决定性失败时怎么走。三个动作（`--override-params '{"stretch_failure_action": "..."}'`
或 cfg 同名键）：

- `auto_fallback`（默认）：回退到该阶段的保守输入并**继续**后续步骤，能出图。
- `preserve_review`：同样继续，但**强制标记 `degraded`** 并把
  `stretch_diagnostics["preserve_review"]` 写明保留的步骤与原因。
  适用于「结果可能不是最终想要的，但先给我看」。
- `stop`：**不出图**，状态 `failed`，但线性域中间产物仍保留供审查。
  用于「宁可不交付也不要错图」。

**`stop_is_stricter`**：一旦任一阶段选了 `stop`，其他阶段的
`auto_fallback` / `preserve_review` 一律失效。混合语义（「前面自动回退、
后面突然终止」）产生的结果最难解释。

非法动作值**报错不回落** —— 处置策略是最后一道闸，回落会把显式意图变成别的行为。

**`degraded` 是 success 与 review_required 之间的中间态**：产物可用、
有降级记录、**不阻断 `accept`**，但调用方看得出「这不是一次干净的处理」。
`quality_state.degraded_stages` 列出走过回退的步骤（如 C50 实测的
`["star_remove"]`）。status 优先级：`failed` > `review_required` > `degraded`
> `success`（有未解决 issue 时不会被 degraded 掩盖）。
**此前 status 只有二值**，拉伸 4 级回退链中「第 2 级 masked 成功」与
「一次成功的 pi_mtf」完全无法区分 —— 补上这一态就是为了找回这个信息。

门禁级 `degraded`（某道门处于退化区）与管线级 `degraded`（某阶段走了回退）
是同一概念的两种载体：**都可见但不升级**。
