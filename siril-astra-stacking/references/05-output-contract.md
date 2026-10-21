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
| NAXIS | `3`（RGB），即使输入是 CFA；**mono 链路为 `2`**，见下 |
| 尺寸 | 与变换后画幅一致 |
| 线性 | 未拉伸，像素值保留线性关系 |

**三种单通道来源**，母版均为 `NAXIS=2 / BITPIX=-32`，文件名、发布时机与原子性契约完全不变：

| 来源 | 触发 | 母版头 |
|---|---|---|
| **mono 链路** | profile 含 `mono_sensor_capable` 且几何未触发 drizzle | `NAXIS=2`；输入带 `BAYERPAT`（单反机身）时保留，纯单色传感器（无 `BAYERPAT`）时无该键 |
| **PWS 引擎** | `--stack-engine=pws` | `NAXIS=2`，无 `BAYERPAT`（引擎输出灰度即亮度成品） |
| **默认链路** | 其余情况 | `NAXIS=3` |

`report.stacking.output_naxis` 显式声明（`2` 或 `3`），消费方无需打开 FITS 文件即可判断。`STACKMODE` 引擎参数记录在 XISF 关键字与 `report.stacking` 中（FITS 头 8 字符键长截断为 `STACKMOD`，见 08 §8.4）。

**mono 与 PWS 互斥**：mono 母版是单通道 CFA，而 PWS 引擎的灰度转换与分区权重只在去拜耳后的 RGB 上验证过，`--stack-engine=pws` + mono 会在执行前被拒（退出码 5）。

实测产物验证：

```
BITPIX = -32
NAXIS  = 3 / NAXIS1 = 200 NAXIS2 = 150 NAXIS3 = 3     ← 默认链路
OBJECT  = 'M 42'
TELESCOP= 'S30 Pro_10d57b1d'    ← 设备元数据透传

BITPIX = -32
NAXIS  = 2 / NAXIS1 = 4948 NAXIS2 = 3280
BAYERPAT= 'RGGB'                ← mono 链路，BAYERPAT 保留
FILTER  = 'S'                   ← 窄带桶
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

该开关只在 Siril 的最终 `stack` 上增加 `-rgb_equal`，在归一化中均衡 RGB 背景；不新增 numpy 依赖，不等于 PCC/SPCC，也不保证整图中位数完全一致。current 参考帧回退保留同一颜色开关。PWS 成品为灰度，显式组合 `--stack-engine=pws --rgb-equal` 在执行前报参数错误。mono 母版同理：显式组合 `--force-chain mono --rgb-equal` 退出码 5，因为单通道母版没有 RGB 背景可均衡。

schema 1.3 的 `channel_stats`：

- `medians_after`：实测叠加后的母版中位数。默认链路为 RGB 三键（`r`/`g`/`b`）；**mono 母版为单键 `{"m": 值}`**；dry-run 或无法采样时为 null。
- `medians_before`：本流程没有测量颜色处理前的母版，保留旧键并置 null。
- `gains_applied`：未执行统一的母版 RGB 增益，置 null；原生归一化可能包含逐帧/逐通道偏移和尺度，不能伪装成三个统一增益。
- `background_equalization`：记录方法、是否显式请求、是否实际执行；dry-run 只标 requested，不标 applied。
- `notes`：记录统计含义和预览后备数据来源；mono 母版的 note 写明「单通道，RGB 统计不适用」。

`--channel-medians` 仍接受 `{"r":0.02,"g":0.03,"b":0.04}` 形式的 JSON，只在没有实测 RGB 数据时用于预览参数；不会被报告成实测 `medians_after`，不会启用背景均衡。已有 RGB 母版统计无效时失败并保留旧产物，不能用 JSON 掩盖无效母版。

旧报告的三通道近似相等值及 gains_applied 不代表实际颜色处理；不修改历史原始记录，后续用正确的实测报告替代这些结论。

## 5.5 report.json schema

```json
{
  "schema_version": "1.3",
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
    "mono_requested": false,
    "observed_after_run": {"span": 0.0, "frame_count": 4}
  },

  "stacking": {
    "method": "rej", "rejection": "winsorized",
    "sigma_low": 4.0, "sigma_high": 3.0, "norm": "addscale",
    "weight": "wfwhm", "maximize": false, "rejmap": true,
    "output_naxis": 3,
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
  "report_path": "/out/M42_report.json",
  "work_dir": "/out/M42.work",
  "warnings": [], "notes": ["chain 'debayer' chosen from measured transforms: span 9.0 px ...",
    "dark-based cosmetic correction [astra_calibrate.sir / calibrate Ms]: not applicable: no master dark; Siril dark-based cosmetic correction was not requested; device-side correction is not verified"],
  "timings": {"total_s": 3.21}
}
```

`work_dir` 只在两种情况下出现：成功且传了 `--keep-work`，或 `--dry-run`（后者总是保留，路径即该行所示）。`timings.total_s` 的口径是「commit 发起时刻」的耗时。

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
| `report_path` | 报告自身的绝对路径，下游脚本不必再拼 `<out>/<target>_report.json` |
| `work_dir` | 保留的 scratch 目录位置；`--dry-run` 下据此找到生成的 `.sir` |
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

报告在 rename **之前**就已写成终态（`artifacts` 由 `_published_paths` 预计算），随三件套一起发布；commit 之后对同一文件再做一次内容完全相同的重写。因此进程在 commit 窗口内被杀死时，已发布的报告仍是完整终态。rename 逐项执行、中途失败不回滚——详见 `references/07-failure-and-resume.md` §7.3。

含义：**上一份完好的母版要么完整保留，要么被新版本完整替换，绝不出现半写状态。**

报告文件本身用「临时文件 + `os.replace`」写入，序列化中途崩溃不会留下截断的非法 JSON。

## 5.7 `savepng` 的后缀陷阱

```
savepng out.png   →   实际写入 out.png.png
```

Siril 自己追加扩展名。实现中传不带后缀的路径，生成后再改名回期望的文件名。若不处理，产物会是 `M42.png.png` 且存在性检查失败。
筛帧报告语义：`stacking.filters` 记录质量筛选策略，默认在 `seqapplyreg` 阶段执行；`frames_registered` 和 `frame_quality` 来自变换前最终注册序列，`frames_stacked` 来自已核对的实际母版数量。正常前移分支中 `notes` 记录原始文件编号的保留/排除名单。`registration.observed_after_run.frame_count` 描述输出变换序列，可以小于注册数量。参考帧回退在 `notes` 中说明，不改报告 schema。

## 5.8 合成输入契约

`scripts/prepare_composition.py RUN_DIR --out OUTPUT_DIR --monochrome` 从 `RUN_DIR` 收集 2–8 个成功并完成 commit 的叠加报告（自适应支持 `RUN_DIR/*_report.json` 扁平 Astra 输出与 `RUN_DIR/*/*_report.json` 历史分滤镜子目录结构），用 `artifacts.master` 定位原母版（若绝对路径因目录迁移失效，自动回退查找报告同级目录同名母版）。要求同一 OBJECT、唯一且可安全命名的 FILTER、Float32 mono/RGB、有效曝光及 STACKCNT 与报告一致。`--monochrome` 是用户明确确认，不能由相机型号或图像尺寸替代。`--siril` 可指定 CLI，`--timeout` 默认每个 Siril 调用 3600 秒。

单通道转换使用 Siril split/pm -nosum，RGB 同滤镜平面等权平均，不把其当作三次曝光。已有单通道另存副本。仅删除副本的 BAYERPAT；FILTER、OBJECT、STACKCNT、EXPTIME、LIVETIME 均核对原值。转换、配准和裁切不修改源母版或源报告，运行前后 SHA-256 必须一致。

共同配准由 register -2pass 选择参考，并逐一验证矩阵及滤镜序号。seqapplyreg -framing=min 导出共同区域，不含筛帧或叠加。全为单位矩阵时按公共尺寸直接复制/裁切。最终 2–8 个文件轴数均为 2、BITPIX=-32、尺寸相同，完整像素必须有限；输出标签与源 FILTER 一一对应。

输出：
- `<FILTER>.fit`：已配准、共同裁切、未拉伸的单通道图像。
- `composition_manifest.json`：schema 1.0；status、reference_filter、output_size、framing，以及 layers 的源路径/哈希、conversion、header、source_pixels、mono_pixels、output_pixels、transform、output、format_ready、quality_status、quality_warnings。
- `previews/`：自动归集的各通道 MTF 预览图 `<FILTER>_preview.png`（从各滤镜报告的 `artifacts.preview` 中提取）。
- `diagnostics/`：各次 Siril 的 .sir、完整 .log 与 .seq。

`format_ready=true` 只表示结构检查与共同配准完成。中位数不大于零、入栈少于三帧及源校准警告都会保留；quality_status 为 warning 或 not_assessed，均不宣称科学质量合格。中位数为整图采样，零值比例为全像素统计，含原有空边；不自动将零值都归因于校准错误。

在输出父目录的临时工作目录完成全部处理和验证，随后将整个目录 rename 发布；已有输出目录拒绝覆盖。执行失败退出非零并保留工作目录、日志和 failure.json。原单桶三件套和报告 schema 不变。准备阶段不自动生成最终 RGB 或改变 H/S 配色。

## 5.9 多滤镜极简交付契约（`--bundle`）

为了彻底解决单滤镜独立叠加产物（可能含 3 图层与非统一大画幅）直接拖入 Siril「多图层图像合成」导致格式错误的问题，且避免多级目录嵌套与冗余大母版造成的困扰，`prepare_composition.py` 提供了 `--bundle` 精简打包模式。

```bash
python3 scripts/prepare_composition.py RUN_DIR --out DELIVERY_DIR --monochrome --bundle
```

交付包目录拓扑仅保留四项核心资产：

```text
DELIVERY_DIR/
├── <FILTER>.fit            ← 🌟【叠加图】32-bit 单通道 (NAXIS=2) 共同裁切尺寸 (如 4826×3101)，根目录直选即用
├── previews/               ← 👁️【预览图】各通道 MTF 预览图 (<FILTER>_preview.png)
├── diagnostics/            ← 🛠️【诊断信息】包含 composition_manifest.json 与 Siril 对齐执行脚本/日志
└── README.md               ← 📄【使用说明】极简使用与参数说明
```

契约规则：
1. **根目录直选**：对齐好的各滤镜 `.fit` 直接位于交付根目录，在 Siril 图像合成窗口中一次性全部选中，无需再钻入子目录。
2. **零冗余负担**：不保留容易引发 3 图层和画幅冲突的第一阶段中间母版，节约磁盘空间。
3. **审计透明**：对齐单应矩阵、溯源 SHA-256 与质量警告收纳至 `diagnostics/composition_manifest.json`。


