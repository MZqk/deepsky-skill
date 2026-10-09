# 05 · 输出契约

> **定位**：三件套的定义、每个字段的来源、以及为什么预览不用 autostretch。
> **加载时机**：需要知道产物是什么、report 怎么读、如何复现同一张预览。
> **对应代码**：`scripts/astra_stack.py`（`atomic_publish` / `_make_preview`）、`scripts/param_derive.py`（`derive_preview_mtf`）

## 5.1 三件套

| 角色 | 文件名 | 内容 |
|---|---|---|
| master | `<target>_stacked_32bit.fits` | 线性 32bit 浮点母版，**未拉伸** |
| preview | `<target>.png` | MTF 拉伸后的 16bit RGB 预览 |
| report | `<target>_report.json` | 全部参数与质量指标，机器可读 |

**不输出非线性拉伸 FITS。** 拉伸是显示偏好，不是数据；把它写进母版会污染后续处理。预览的定位是"快速目测"，不承担成品职责。

## 5.2 母版规格

| 项 | 值 |
|---|---|
| BITPIX | `-32`（强制 `-32b`） |
| NAXIS | `3`（RGB），即使输入是 CFA |
| 尺寸 | 与变换后画幅一致 |
| 线性 | 未拉伸，像素值保留线性关系 |

**`--stack-engine=pws` 的例外**：PWS 引擎输出单通道灰度，转换后的母版是 `NAXIS=2 / BITPIX=-32`（灰度即亮度成品）。三件套的文件名、发布时机与原子性契约完全不变；`STACKMODE` 引擎参数记录在 XISF 关键字与 `report.stacking` 中（FITS 头 8 字符键长截断为 `STACKMOD`，见 08 §8.4）。

实测产物验证：

```
BITPIX = -32
NAXIS  = 3 / NAXIS1 = 200 NAXIS2 = 150 NAXIS3 = 3
OBJECT  = 'M 42'
TELESCOP= 'S30 Pro_10d57b1d'    ← 设备元数据透传
```

## 5.3 预览：用 mtf，不用 autostretch

### 为什么不用 autostretch

```
autostretch [-linked] [shadowsclip [targetbg]] [-mask]
```

`autostretch` **没有任何输出参数**，无法回读它计算出的 midtones。结果是：同一份输入重跑可能得到不同 PNG，而用户无法验证"这次和上次是否一致"。这与"标准化输出"的要求直接冲突。

另有一条相关警告：

> `Do not use the unlinked version after color calibration, it will alter the white balance`

`mtf low mid high [channels] [-mask]` 接受显式参数，把它们写进 report 即可保证重跑像素级一致。

### MTF 中点必须高于背景

`mid` 是被映射到中灰的输入值。**若 `mid` 低于背景电平，背景会被映射到中灰以上，预览泛白。**

旧 SH2-296 预览的经验对照（当时读取器给出的 0.0113 不再视为有效的 RGB 背景测量；本例仅说明 MTF 参数效果）：

| mid | 结果 |
|---|---|
| 0.0057（背景 × 0.5） | **背景接近纯白，完全不可用** |
| 0.0113（= 背景） | 背景中灰，仍偏亮 |
| 0.0226（背景 × 2.0） | **暗背景、星点锐利、微弱星云可见** |
| 0.0452（背景 × 4.0） | 偏暗，星云仍不可见 |

因此推导公式是：

```python
mid = 背景中位数 × 2.0，clamp 到 [0.01, 0.40]
```

我最初写的是 `中位数 × 0.5`，方向完全相反——预览背景泛白。`selftest.py` 中 `test_preview_stretch_direction` 专门断言 `mid > background`，防止回归。

### 中位数从母版直接采样

`derive_preview_mtf` 的输入来自 `fits_probe.estimate_channel_medians()`——直接采样叠后母版，不依赖 sirilpy（1.4 CLI 下 socket 不可用，见 06）。

实现要点：

- 只处理 `BITPIX=-32` 的母版。
- **按通道平面同位置采样**：样本总数不超过预算；只保存有限样本，不把整幅图像装入 Python 数值数组。读取必要行仍可能访问大部分文件，不承诺固定采样耗时。
- **FITS 数据是大端**（`>` 而非 `<`），包括 IEEE float。字节序写错会读出天文数字般的中位数。
- 数据起点 = `END` 卡片位置向上取整到 2880 字节块。

`derived_from` 字段会记录实际推导依据，例如：

```
"derived_from": "minimum whole-image channel median 0.011317 x 2.0"
```

无中位数时退化为默认值 `0.028`（Siril 自身 GHS 的默认值），并在 `derived_from` 中标注 `default`。

## 5.4 通道统计与可选背景均衡

Float32 FITS 的主图像按 R、G、B 平面存储：x 最快，y 次之，通道轴最慢。读取器在三个平面的同一组空间位置采样，遵守总采样预算并应用 BSCALE/BZERO；跳过非有限值，拒绝截断数据。统计包含空边和天体信号，是整图中位数，不能直接解释为纯天空背景或光度颜色。

默认只测量，不改变线性母版颜色。明确需要原生背景均衡时：

```bash
python3 scripts/astra_stack.py /data/M42 --out ./results --rgb-equal
```

该开关只在 Siril 的最终 `stack` 上增加 `-rgb_equal`，在归一化中均衡 RGB 背景；不新增 numpy 依赖，不等于 PCC/SPCC，也不保证整图中位数完全一致。current 参考帧回退保留同一颜色开关。PWS 成品为灰度，显式组合 `--stack-engine=pws --rgb-equal` 在执行前报参数错误。

schema 1.2 的 `channel_stats`：

- `medians_after`：实测叠加后的母版 RGB 中位数。dry-run 或灰度输出为 null。
- `medians_before`：本流程没有测量颜色处理前的母版，保留旧键并置 null。
- `gains_applied`：未执行统一的母版 RGB 增益，置 null；原生归一化可能包含逐帧/逐通道偏移和尺度，不能伪装成三个统一增益。
- `background_equalization`：记录方法、是否显式请求、是否实际执行；dry-run 只标 requested，不标 applied。
- `notes`：记录统计含义和预览后备数据来源。

`--channel-medians` 仍接受 `{"r":0.02,"g":0.03,"b":0.04}` 形式的 JSON，只在没有实测 RGB 数据时用于预览参数；不会被报告成实测 `medians_after`，不会启用背景均衡。已有 RGB 母版统计无效时失败并保留旧产物，不能用 JSON 掩盖无效母版。

旧报告的三通道近似相等值及 gains_applied 不代表实际颜色处理；不修改历史原始记录，后续用正确的实测报告替代这些结论。

## 5.5 report.json schema

```json
{
  "schema_version": "1.2",
  "status": "ok",
  "failed_stage": null,
  "completed_stages": ["probe","identify","plan","calibrate","probe-reg","stack","preview","commit"],
  "target": "M42",
  "input_dir": "/data/M42",
  "siril": {"path": "/Applications/Siril.app/.../siril-cli", "version": "siril 1.4.4"},

  "input": {
    "frame_count": 4, "naxis": 2, "bitpix": 16, "bayerpat": "GRBG",
    "width": 200, "height": 150, "object": "M 42", "exptime": 60.0,
    "warnings": []
  },

  "device": {
    "identified": true, "id": "zwo_seestar_s30pro", "class": "smart",
    "description": "ZWO Seestar S30 Pro (IMX585)",
    "matched_by": "TELESCOP", "confidence": "signature_table",
    "profile": { "flats_available": false, "dark_embedded": true, "min_pairs": 4 }
  },

  "calibration": {
    "bias": null, "dark": null, "flat": null,
    "dark_embedded": true,
    "flat_missing_reason": "smart_scope_no_flat",
    "cc": {"enabled": true, "siglo": null, "sighi": 3},
    "warnings": ["smart-scope profile: ..."]
  },

  "registration_probe": {
    "span_x": 9.006, "span_y": 6.008, "span": 9.006,
    "rotation_max_deg": 0.0098, "scale_dev_max": 0.00002,
    "median_scale": 0.9997, "mosaic": false, "frame_count": 4
  },

  "registration": {
    "chain": "debayer", "transform_model": "homography",
    "reasons": ["span 9.0 px, rotation 0.00 deg ... the simpler debayer chain is sufficient"],
    "mosaic": false, "rotation_max_deg": 0.0, "scale_dev_max": 0.0,
    "span_px": 9.0, "degradations": [], "apply_args": ["-framing=current"],
    "observed_after_run": {"span": 0.0, "frame_count": 4}
  },

  "stacking": {
    "method": "rej", "rejection": "winsorized",
    "sigma_low": 4.0, "sigma_high": 3.0, "norm": "addscale",
    "weight": "wfwhm", "maximize": false, "rejmap": true,
    "filters": ["-filter-wfwhm=90%", "-filter-round=0.30", "-filter-nbstars=85%"],
    "frames_used": 4,
    "notes": ["-filter-quality is omitted on purpose: ..."]
  },

  "preview": {
    "method": "mtf", "low": 0.0, "mid": 0.028, "high": 1.0,
    "derived_from": "per-channel median of the stacked master",
    "produced": true
  },

  "channel_stats": {
    "medians_before": null,
    "medians_after": {"r": 0.02, "g": 0.03, "b": 0.02},
    "gains_applied": null,
    "background_equalization": {"method": null, "requested": false, "applied": false},
    "notes": ["whole-image statistics; not sky-only or photometric calibration"]
  },

  "script_text": "requires 1.4.0\ncalibrate ...",
  "artifacts": {"master": "...", "preview": "...", "report": "..."},
  "warnings": [], "notes": ["chain 'debayer' chosen from measured transforms: span 9.0 px ...",
    "dark-based cosmetic correction [astra_calibrate.sir / calibrate Ms]: not applicable: no master dark; Siril dark-based cosmetic correction was not requested; device-side correction is not verified"],
  "timings": {"total_s": 3.21}
}
```

### 关键字段的读法

| 字段 | 用途 |
|---|---|
| `calibration.cc.enabled` | 校准计划开关，不是执行结果；实际命令状态由顶层 `notes` 说明 |
| `registration_probe` | 链路决策的**实测依据**，不是猜测 |
| `registration.reasons` | 人可读的判定理由，含具体数值 |
| `registration.degradations` | 降级标记，如 `drizzle_without_flat_weights` |
| `registration.observed_after_run` | 变换后输出序列的几何与数量，不用于判断原始链路 |
| `script_text` | 完整的 `.sir` 内容，可复现、可审计 |
| `stacking.notes` | 参数选择的理由，含刻意排除某 flag 的原因 |
| `frame_quality.weighted_fwhm_p90_over_p10` | 复用 `metrics.weighted_fwhm` 已排序的 p90/p10，保留四位小数；不受采集顺序影响 |

分散度提示使用未舍入比值与 1.4 比较。表内 p10 为零时比值为 null、不给出分散度提示；原有可用值不足两条时不生成该汇总字段。sigma 统计规则与 PWS 准入闸门保持原样。

坏点校正 notes 按脚本和输入序列记录。无 dark 时明确不适用/未请求，并说明设备内部处理未确认；dry-run 只描述计划。成功只表示包含 `-cc=dark` 的校准命令及现有产物检查通过，不证明效果。失败/超时可能已有部分处理，记为完成未确认，保留之前确认成功的记录。仅去拜耳、复用或参考帧回退不新增校正完成记录。

### 链路矛盾自检

仅对自动选择进行复核：使用变换前、最终数据格式的已验证注册摘要，调用同一 `decide_chain`，完整沿用 mosaic、旋转及尺度判据。若最终推荐与已选链路不同，`notes` 记录所选链路、最终推荐及具体实测依据；一致时不追加提示。

`registration_probe` 保留最初选择时的探测依据。数据格式转换后的必要重配准可能改变最终推荐，因此不能省略真实差异的复核。`registration.observed_after_run` 仅描述变换后几何；旋转归零是应用矩阵后的正常结果，不能据此推断 drizzle 没必要。显式指定 drizzle/debayer 时保留强制理由，跳过矛盾提示。

## 5.6 原子发布

```python
published = atomic_publish(work_dir, out_dir, target_name, artifacts)
```

在 `out_dir` 内部用 `os.rename`，因此是同一文件系统上的原子操作。三件套全部成功后才执行，任一失败则不发布。

含义：**上一份完好的母版要么完整保留，要么被新版本完整替换，绝不出现半写状态。**

## 5.7 `savepng` 的后缀陷阱

```
savepng out.png   →   实际写入 out.png.png
```

Siril 自己追加扩展名。实现中传不带后缀的路径，生成后再改名回期望的文件名。若不处理，产物会是 `M42.png.png` 且存在性检查失败。
筛帧报告语义：`stacking.filters` 记录质量筛选策略，默认在 `seqapplyreg` 阶段执行；`frames_registered` 和 `frame_quality` 来自变换前最终注册序列，`frames_stacked` 来自已核对的实际母版数量。正常前移分支中 `notes` 记录原始文件编号的保留/排除名单。`registration.observed_after_run.frame_count` 描述输出变换序列，可以小于注册数量。参考帧回退在 `notes` 中说明，不改报告 schema。
