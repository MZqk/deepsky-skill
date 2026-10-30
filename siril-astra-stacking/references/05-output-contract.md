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

`work_dir` 在成功且传了 `--keep-work`、`--dry-run` 或发布后清理失败时出现。清理失败只告警，保持 `status="ok"` 和退出码 0；`notes` 记录异常类型、原因及路径，工作文件可能仅部分残留。若告警报告写回也失败，已发布的完整成功报告保持原样，诊断仍见 stderr。`timings.total_s` 的口径是「commit 发起时刻」的耗时。

### 关键字段的读法

| 字段 | 用途 |
|---|---|
| `calibration.cc.enabled` | 校准计划开关，不是执行结果；实际命令状态由顶层 `notes` 说明 |
| `registration_probe` | 链路决策的**实测依据**，不是猜测 |
| `registration.reasons` | 人可读的判定理由，含具体数值 |
| `registration.degradations` | 降级标记，如 `drizzle_without_flat_weights` |
| `registration.flat_used` / `registration.flat_note` | 仅 drizzle 链路记录；`"none"` 表示未启用 `seqapplyreg -flat=` 权重，不声明设备能力或校准平场是否选用，dry-run 与实际运行含义一致 |
| `calibration.flat` | 校准母版的选用路径；非空时用于 `calibrate -flat=`，dry-run 只表示计划，与 drizzle 平场权重独立 |
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

## 5.10 RGB／SHO／HOO 通道合成

`scripts/compose_channels.py INPUT --mode rgb|sho|hoo --out OUTPUT` 消费 §5.8／§5.9 的准备目录。`--siril` 可指定 Siril CLI，`--timeout` 为每次调用上限（默认 3600 秒，必须为正数）。

### 输入与映射

- 清单必须恰好位于 `INPUT/composition_manifest.json` 或 `INPUT/diagnostics/composition_manifest.json` 之一；两者并存或均缺失时拒绝。要求内部叠加准备 schema `1.0`，或 §5.14 的显式外部母版 schema `1.1`；`status=ok`、`monochrome_confirmed=true`，所需 layer 的 `format_ready=true`。
- 从输入根目录读取清单标签对应的 `<FILTER>.fit`，不沿用清单中的旧绝对路径，故普通包与 bundle 均可搬迁，原始母版和叠加报告不必在本机。所选文件必须位于准备目录内。
- 滤镜别名忽略大小写：`H/HA/HALPHA/H-ALPHA/H_ALPHA` → Hα，`S/SII` → SII，`O/OIII` → OIII，`R/G/B` 直接对应。所需通道缺失或出现多个等价别名时拒绝；额外通道不参与。
- 文件必须为无 `BAYERPAT` 的 Float32 单通道，目标与尺寸一致，曝光元数据与清单一致且有效，全部像素有限。内部来源要求完整曝光计数；外部来源允许未知值，见 §5.14。要求保留准备时的线性数据；格式检查不能证明图像未被其他软件拉伸或修改。

| 模式 | 输出 R | 输出 G | 输出 B |
|---|---|---|---|
| `rgb` | R | G | B |
| `sho` | SII | Hα | OIII |
| `hoo` | Hα | OIII | OIII |

科学像素由 Siril `rgbcomp ... -nosum` 生成，保持输入数值，包括有限负值及大于 1 的值。不重新配准、强度匹配、背景均衡或拉伸母版；没有真实 SII 不生成 SHO。SHO/HOO 是指定显示通道映射，不代表自然色或经过校准的物理线比。

### 输出与溯源

| 文件 | 内容 |
|---|---|
| `composed_<mode>_32bit.fits` | 线性 Float32 RGB，`NAXIS=3`、`NAXIS3=3`，默认尺寸与准备文件相同，显式 common 裁切除外 |
| `composed_<mode>.png` | 采样母版中位数后生成的显式 MTF 显示预览，不改变 FITS |
| `composition_report.json` | 独立 schema `1.4`，合成状态、映射、各独立输入、源清单哈希、输出统计、MTF 参数、警告和产物路径 |
| `diagnostics/` | `compose.sir`、`compose.log`、`preview.sir`、`preview.log` |

`-nosum` 仍继承第一通道的曝光计数，因此合成 FITS 删除 `BAYERPAT`、`EXPTIME`、`LIVETIME`、`STACKCNT`，避免将单滤镜值误读为整图总量。报告的 `sources` 按独立通道记录当前准备文件路径、SHA-256、FITS 头、像素检查和源质量警告；HOO 的 OIII 只有一条 source，由 `mapping.g` 与 `mapping.b` 共用。曝光和帧数读取各 source 的 `header`，不产生合成总量。

报告包含 `input_manifest.path/sha256`、`mode`、`mapping`、`sources`、`output_state="linear"`、`normalization="none"`、`output_pixels`、`preview`、`warnings` 和 `artifacts`。schema `1.4` 保留 `input_origin` 及来源的 `origin`、`exposure`、`external_metadata`，明确外部导入与未知曝光；新增覆盖和质量字段见 §5.15，Hα 混合说明继续使用 `ha_blend`。本节三种线性映射的 `pre_stretch` 与 `ha_blend` 均为 null，默认 legacy 策略的原像素和 MTF 预览行为保持不变；可选策略见 §5.15。`status=ok` 表示格式、执行与发布成功，不构成科学质量保证；源质量警告原样保留。

### 失败与发布

输出目录必须不存在；在输出父目录的 `.compose-*` 临时目录执行并验证全部产物，输入文件和清单在运行前后核对 SHA-256，最后整目录重命名发布。任何执行、超时、校验或发布失败返回非零，保留工作目录和 `failure.json`，已有输入和目标产物不被覆盖。创建工作目录前发生的参数或文件系统错误只输出 stderr。

## 5.11 LRGB 自动预拉伸与融合

`scripts/compose_channels.py INPUT --mode lrgb --out OUTPUT` 沿用 §5.10 的准备清单、格式校验、搬迁寻址和发布机制，但要求四个单色通道 L/R/G/B。L 标签忽略大小写，缺失或重复 L 时拒绝；输入必须保持线性且已对齐。首版不接收独立 L＋三通道 RGB，不新增手动拉伸、颜色空间、校色或重新配准选项。

### 处理与线性状态

1. `rgbcomp R.fit G.fit B.fit -nosum` 生成临时线性 RGB，直接采样其通道中位数。
2. 复用 `derive_preview_mtf` 分别求参：L 使用单通道中位数，RGB 使用三通道最小中位数；中点为中位数绝对值乘 2 并限制在 `[0.01, 0.4]`，低点 0、高点 1。完整参数与推导依据分别记录在 `pre_stretch.l` 和 `pre_stretch.rgb`。
3. 在临时副本上执行 L 的 MTF 和 RGB 的共用 MTF，再通过 Siril 1.4 的 `rgbcomp -lum=l_stretched.fit rgb_stretched.fit -nosum` 融合，使用原生默认方式。
4. 验证融合结果后保存 Float32 FITS，直接导出 PNG，**不再对 PNG 施加第二次 MTF**。

LRGB 输出为**非线性**，不能作为未经拉伸的线性母版。MTF 的原生端点截断只作用于临时副本，原输入不变；自动 MTF 不保证最终亮度、星色或科学线比，已有质量警告继续保留。

### 产物与报告

输出为 `composed_lrgb_32bit.fits`、`composed_lrgb.png`、`composition_report.json` 和 `diagnostics/`。FITS 仍要求 `BITPIX=-32`、三通道、原公共尺寸及有限像素，沿用拜耳和曝光计数清理规则，并写入 COMMENT：

```text
NONLINEAR LRGB: MTF pre-stretch applied to L and RGB
```

报告使用 schema `1.4`，`output_state="nonlinear"`、`mapping={"r":"R","g":"G","b":"B","l":"L"}`，`sources` 为四份独立输入；`normalization="none"` 表示未进行强度匹配，不代表未执行拉伸。`pre_stretch` 保存两个 MTF 参数对象，`ha_blend=null`，`preview={"method":"direct"}` 明确预览不再求参。曝光与帧数只记录各 source，外部来源的未知值为 null。

诊断目录保留 `compose_rgb`、`pre_stretch`、`compose`、`preview` 四阶段的 `.sir` 和 `.log`。临时线性 RGB、预拉伸副本和源文件链接在成功时移除，不进入正式交付；失败保留诊断和工作目录，`failed_stage` 标识失败阶段。内部准备清单使用 schema `1.0`，外部准备清单使用 schema `1.1`，与合成报告独立。

## 5.12 HaRGB／HaLRGB 显式 Hα 混合

两种模式沿用 §5.10 的准备清单、源数据校验、目录搬迁和整目录发布机制。`hargb` 要求真实 Hα、R、G、B，`halrgb` 另外要求 L；Hα 使用已有别名。所需源通道与临时计算通道分开处理，`R_HA` 不是输入滤镜或额外曝光。

```bash
python3 scripts/compose_channels.py /data/prepared --mode hargb \
  --ha-weight 0.25 --ha-scale 1.0 --out /data/composed_hargb
```

### 参数与公式

`--ha-weight`、`--ha-scale` 均无默认值且必须显式提供。权重为有限的 `[0,1]` 数值，标度为有限正数；NaN、Inf、越界值、缺项以及其他模式误传这两个参数均在创建工作目录前拒绝。原命令和不带新参数的内部调用保持可用。

```text
R_HA = (1 - weight) * R + weight * scale * HA
G_out = G
B_out = B
```

通过 Siril `pm ... -nosum` 生成临时 Float32 红通道，不使用 `-rescale` 或裁剪。生成后核对单通道、尺寸、目标与有限像素；溢出或格式异常失败，不发布。有限负值和大于 1 的值保留。权重 0 的像素结果应与对应 RGB／LRGB 一致，权重 1 的红通道为缩放后的 Hα。

### 两种输出路径

| 模式 | 流程 | FITS 状态 | PNG |
|---|---|---|---|
| `hargb` | `R_HA/G/B` 合成 | 线性 | 沿用 MTF 预览 |
| `halrgb` | 混合 RGB 实测求参 → L／RGB 各自 MTF → 原生亮度融合 | 非线性 | 直接导出 |

L 不加入 Hα，但仍在临时副本上执行既有 LRGB 预拉伸。HaLRGB 的 RGB MTF 必须来自混合后的图像，不能沿用混合前参数。原线性源文件保持不变；输入哈希核对包含 Hα，临时混合图成功后删除。

### 报告与诊断

输出遵循 `composed_<mode>_32bit.fits`、`composed_<mode>.png`、`composition_report.json` 和 `diagnostics/`。报告统一为 schema `1.4`，新模式的 `mapping.r="R_HA"`，HaLRGB 另含 `mapping.l="L"`；`normalization="explicit_ha_scale"` 表示使用用户给定标度，不是自动拟合。`ha_blend` 记录：

```json
{
  "formula": "(1 - weight) * R + weight * scale * HA",
  "weight": 0.25,
  "scale": 1.0,
  "continuum_subtraction": false
}
```

原模式的 `ha_blend=null`。HaRGB／HaLRGB 分别记录四／五份独立来源，不重复累计曝光；沿用拜耳及曝光计数清理规则，参数完整值以报告为准。FITS COMMENT 标注 `Explicit H-alpha/R mixture; no continuum subtraction`，HaLRGB 同时保留既有非线性说明。

诊断增加 `ha_blend.sir` 和 `ha_blend.log`，失败阶段为 `ha_blend`；其余阶段和失败发布契约沿用现有模式。

这是一种显式显示混合，不自动估计标度，不进行连续谱扣除、背景校正、星层保护或向 L 注入 Hα。恒星颜色可能变化，保持 L 不变也不保证 Hα 区域获得更强亮度结构；不宣称纯谱线或光度校准结果。

## 5.13 多滤镜批处理与整桶续跑

`scripts/batch_filters.py INPUT --out OUTPUT --monochrome` 调度单桶叠加，再调用 §5.8／§5.9 的准备流程。`--out`、单色传感器确认均必填；支持 `--calibration-dir`、`--device-profile`、`--siril`、`--allow-missing-dark`、`--timeout` 和 `--resume`。超时必须为正数，默认每次 Siril 调用 3600 秒，不是整桶或整批总用时上限。

### 输入与预检

- 仅扫描 INPUT 第一层 `.fit`／`.fits`／`.fts`，后缀忽略大小写；要求 2–8 个滤镜，每桶至少两帧。重复指向同一源路径的输入拒绝，子目录不参与。
- 逐帧通过设备 profile 和显式确认判定单色身份；要求 `NAXIS=2`。单凭几何或缺 BAYERPAT 不构成单色确认，沿用既有单色设备的元数据兼容规则。
- OBJECT、设备及仪器标识、望远镜标识、尺寸和 binning 在整批一致；FILTER 规范为去空白的大写标签，原始标签保存在来源记录。同一合成身份出现 H／HA 等不同标签桶时拒绝，不自动合并。
- 每桶 EXPTIME、GAIN、OFFSET、BITPIX、FITS 数值缩放和 BAYERPAT 一致；可选字段允许整桶缺失，不允许部分缺失。曝光必须正且有限，其他参与比较的数值必须有限。温度允许在校准容差内漂移，但存在性及最终选中的母帧组必须一致。
- 所有桶完成严格校准预检后才启动叠加。详细规则见 `02-calibration.md` §2.7。所选母帧通过独立符号链接目录提供给子进程，子进程不重新扫描共用母帧库。

首版使用原生 Siril 和原有自动链路，不新增 PWS、OSC 提取、多配置合并或配色选项。

### 输出与报告

| 路径 | 内容 |
|---|---|
| `stacks/<FILTER>/` | 现有 `<FILTER>_stacked_32bit.fits`、`<FILTER>.png`、`<FILTER>_report.json` |
| `composition_inputs/` | 全部桶成功后的精简准备包，继续使用准备清单 schema 1.0 |
| `diagnostics/<FILTER>.log`、`prepare.log` | 子进程完整输出 |
| `diagnostics/inputs/`、`diagnostics/calibration/` | 仅供读取的源帧及所选母帧链接，保留子报告的路径可查性 |
| `diagnostics/attempts/` | 续跑前归档的未完成桶、未确认准备包及对应日志 |
| `batch_report.json` | 独立 schema 1.0，既是批处理报告也是整桶检查点 |

报告的 `kind="batch_filters"`、`output_dir` 标识所属输出目录。`config` 保存处理选项、实现版本、Siril 路径和版本及设备配置文件哈希；`inputs` 保存每桶原始帧路径、原始 FILTER、SHA-256、设备、配置、所选母帧及警告。`fingerprint` 对这些内容做确定性哈希。

`buckets` 与 `preparation` 分别记录 `status`、`exit_code`、`reused`、日志路径及产物路径／SHA-256。阶段状态为 `pending`／`running`／`ok`／`failed`；成功桶必须经过子报告 commit、母版格式和曝光元数据、实际校准选择及三件套哈希校验。准备阶段核对清单、各通道格式、公共尺寸、源母版哈希，并记录包内全部文件的哈希。

整体 `status=ok` 仅在所有桶及准备阶段成功、最终输入核对通过后写入，`failed_stage=null`。运行期间记录 `stack:<FILTER>`、`prepare` 或 `verify` 等阶段，失败附 `error`。报告使用现有原子 JSON 写入方式。

### 失败与续跑

普通运行拒绝已有输出；任何桶失败立即停止，已完成桶保留，尚未开始的桶维持 pending，不进入合成准备。输入和选中母帧在当前桶前后、准备前及最终完成前核对哈希；设备配置同样参与核对。最终核对失败时，将本次准备包移入诊断归档，不保留为成功交付入口。

`--resume` 仅接管合法批处理报告所属的原输出目录。先重新预检并比较整个指纹，再校验所有成功产物，全部通过后才更新检查点。原始帧、选中母帧、设备配置、处理参数、Siril 路径／版本发生变化，或成功产物缺失、内容变化时，拒绝续跑并要求新目录；不重写原检查点。

仅 status=ok 且校验通过的桶复用。未写入成功检查点的桶即使已有三件套也重新执行；此前目录和日志先移入 `diagnostics/attempts/`，不调用单桶入口的 `--resume`。准备失败时可复用全部成功桶，归档未确认准备包和日志后单独重试准备。整批已成功时，复验后直接成功返回，不重跑、不重写产物。

原始文件及校准母帧不被修改。批处理允许保留部分完成的母版，因此消费方必须检查整体报告状态。参数错误退出 2，执行或续跑校验失败退出 1，成功退出 0；创建输出目录前的错误只输出 stderr。准备流程自己的失败工作目录仍按原契约保留在输出父目录中。

## 5.14 显式外部母版导入

`prepare_composition.py INPUT --out OUTPUT --monochrome --external-masters --linear --target M51 [--bundle]` 导入其他软件已经叠加的 Float32 单通道 FITS／XISF 母版。必须明确确认线性和单色，并指定目标；单独传 `--linear`／`--target` 不会改变内部叠加准备行为。缺少确认时在创建工作目录前拒绝。

只扫描第一层 2–8 个支持的文件。FILTER 读取文件元数据，不能用文件名补猜；缺失 OBJECT 时写入明确的 target，并记录赋值事实；已有目标与 target 冲突时拒绝。XISF 读取 XML 来源信息，所有像素解码和 FITS 保存均由原生 Siril 完成。源文件通过 ASCII 链接名加载，支持中文和含空格的输入路径。

导入后仍执行单通道检查、有限像素检查、全局配准及公共裁切；不会重新校准、叠加或自动强度匹配。原文件在开始和结束核对 SHA-256。成功仍整目录发布，失败保留诊断；不生成伪装成 Astra 叠加成功的报告。

外部准备清单使用 schema `1.1`、`origin="external_masters"`、`linear_confirmed=true`。每个 layer 保存原始文件与哈希，以及 `external_metadata` 的格式、OBJECT 是否赋值、上游处理类、帧数及其 HISTORY 证据。原有内部准备继续输出 schema `1.0`，必需曝光计数的检查不放宽。

外部文件的 EXPTIME、STACKCNT、LIVETIME 若存在，必须有效并在处理中保持一致；缺失允许保持未知，不添加假计数。XISF 中唯一且为正的 `DrizzleIntegration.numberOfImages`／`ImageIntegration.numberOfImages` 可作为报告帧数证据，不注入 FITS STACKCNT；证据冲突时帧数未知。LIVETIME 缺失时总曝光保持未知，不将名义单帧曝光乘帧数当作已验证总量，也不把 DATE-OBS／DATE-END 时间跨度当作曝光。

合成报告 schema `1.4` 的 `input_origin` 区分 `astra_stacks` 与 `external_masters`。来源 `exposure` 保存 `nominal_seconds`、`stacked_frames`、`total_seconds` 和 `frames_evidence`；未知值为 null。`external_metadata` 对内部来源为 null。新 schema 1.1 清单只有同时明确外部来源及线性确认时才可被合成消费；所有模式仍检查格式、目标、公共尺寸、有限像素、源哈希及曝光元数据一致性。

## 5.15 覆盖诊断、可选裁切与背景拉伸

六种模式支持 `--stretch legacy|background`（默认 legacy）、`--crop none|common`（默认 none）。`--target-bg` 仅 background 可用，默认 0.15，必须为 `(0,1)` 内有限数。参数错误在创建工作目录前拒绝；旧内部调用缺少新字段时使用默认值。单桶叠加 MTF 与批处理实现指纹保持不变。

v1.10.0 同时修复共享 FITS 数据偏移的块边界错误：数据起点必须越过完整的 END 卡片再对齐至 2880 字节块。此前 END 位于块起点的文件会把该块头部误当成像素，影响统计并漏检尾部。这类文件的统计、预览及统计驱动的 LRGB 预拉伸存在正确性修正例外；legacy 参数公式和 Siril 原生线性像素处理不变。

准备清单各层增加可选 `coverage` 字段，内部 schema 1.0、外部 schema 1.1 不变。覆盖算法仅移除每行首尾连续的精确零值，内部零值、负值及大于 1 的数值保留；该候选不能证明真实曝光覆盖，也不能识别内部孔洞或非零填充值。合成重新读取实际通道，不要求旧清单包含覆盖字段；仅参与计算的独立通道限制共同区域。HOO 的 OIII 去重，Hα 权重 0 不用 HA 限制区域，权重 1 不用 R 限制区域；未贡献来源仍需通过全部输入校验。

逐行共同区间内求最大面积轴对齐矩形，同面积按左上角 y、x 升序，再按宽度降序确定。报告坐标以 Siril 左上角为原点，FITS 行号通过 `stored_y=height-1-y` 转换。默认只报告；common 用原生 `crop` 裁切临时副本，验证尺寸、目标及曝光元数据，再执行原混合与合成。没有共同候选时 legacy/none 告警继续；显式裁切或 background 失败且不发布。`coverage` 记录方法、各贡献通道、输入尺寸、候选矩形、损失比例、实际裁切与输出尺寸。

background 在共同候选矩形中按固定空间步长采样，每幅图总预算 200,000，各平面位置一致；记录样本数、中位数、MAD 和 `sigma=1.4826*MAD`。L 单独计算，RGB 共用参数，以最小通道中位数为参考：

```text
low = max(0, min(median_i - 2.8 * sigma_i))
high = 1
x = (reference_median - low) / (1 - low)
mid = x * (1 - target_bg) / (x + target_bg - 2 * x * target_bg)
```

MAD 为零导致 low 无法低于参考中位数时改用 low=0 并告警；无样本、无有效归一化中位数或无有效中点时失败，不回退到 legacy。完整参数保留浮点精度，像素由原生 Siril `mtf` 处理。RGB/SHO/HOO/HaRGB 只作用于 PNG；LRGB/HaLRGB 作用于临时 L/RGB 预拉伸，HaLRGB 使用混合后 RGB 求参，PNG 保持 direct。裁切未启用时仍在候选矩形统计，输出画幅保留。目标仅约束预拉伸参考值，不保证最终融合背景、自然色或物理线比。

schema 1.4 保留原字段，增加 `stretch`、`coverage`、`quality.stretch_clipping`；background 的 MTF 对象增加 `strategy`、`target_bg` 及完整 `statistics`。像素检查的 `range_counts` 精确记录整幅图小于 0、大于 1、等于 0 和等于 1 的数量；`stretch_clipping` 精确记录候选统计区域内严格低于 low 或高于 high 的数量，属于显示截断风险，不是传感器饱和判定。legacy 预览对象和参数公式不变。

`ha_blend.diagnostics` 记录同一区域混合前后统计、中位数比例、变化百分比及混合图范围；原 R 中位数为 0 时比例和百分比为 null，下降时提示但不自动更改系数。缺少共同候选时不记录该诊断。`status=ok` 只表示处理和发布成功，质量结论仍需检查；所有统计不会触发自动强度匹配、背景中和或校色。

诊断增加可选 `crop.sir/crop.log`，成功后裁切、混合与预拉伸临时 FITS 均清理；失败保留阶段及日志，整目录发布与原始来源哈希核对不变。

外部来源的上游校准、拒绝和真实线性状态未由本次流程重验，质量警告据实保留。外部母版可以验证准备和合成链，但不能用作原始帧分桶、校准匹配或整桶续跑的验收。


