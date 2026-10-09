# 02 · 校准

> **定位**：两类设备在校准上的差异是流水线里最本质的分支。
> **加载时机**：需要理解暗场/平场如何应用、如何匹配母帧、为什么智能望远镜不做平场校正时。
> **对应代码**：`scripts/device_signatures.py`（`resolve_calibration_plan`）、`scripts/param_derive.py`（`derive_calibration`）

## 2.1 两类设备的根本差异

| | 智能望远镜（Seestar 系列） | 传统设备 |
|---|---|---|
| 暗场 | **设备内部拍摄并已应用**到导出的每一帧 | 用户拍摄 master dark |
| 平场 | **不拍**（ZWO 官方共识） | 用户拍摄 master flat |
| 偏置 | 不可见 | 可选 master bias 或用 OFFSET 表达式 |
| 已知暗角 | 存在且无法校正 | 用平场校正 |

Seestar 不拍平场的理由：50mm f/5 + 小口径传感器下平场收益极小；且 mosaic 模式下视场平移会把残余伪影摊开，负面影响大于收益。

Seestar 的暗场在**每次开机初始化时**拍摄并应用到导出帧。若中途环境变化较大，重启设备会重新初始化并拍新暗场。

## 2.2 校准计划解析

`resolve_calibration_plan(light_header, device, calibration_dir)` 返回：

```python
{
  "bias": None | "/path/master_bias.fit",
  "dark": None | "/path/master_dark.fit",
  "flat": None | "/path/master_flat.fit",
  "dark_embedded": True | False,
  "flat_missing_reason": None | "smart_scope_no_flat",
  "cc": {"enabled": True, "siglo": None, "sighi": 3},
  "warnings": [...],
}
```

### 智能望远镜分支

- `dark_embedded = True`，`dark = None`（不应用 master dark——暗场已在数据里）。
- `flat = None`，`flat_missing_reason = "smart_scope_no_flat"`。
- 产生两条 warning，明确记录后果。

### 传统设备分支

- master dark 缺失 → **抛 `CalibrationMissing`，退出码 3**。不降级、不继续：未校准的 data 产出看起来正常但背景偏置错误的结果。
- master flat 缺失 → 仅 warning，继续运行（平场比暗场可选性强）。
- master bias 始终可选。

## 2.3 母帧匹配算法

```python
tolerance = {"CCD-TEMP": 2.0, "GAIN": 3.0, "EXPTIME": 0.5}
```

匹配要求三个关键字**都存在**且都在容差内。评分 = 归一化偏差之和，最紧的母帧胜出。

任何关键字缺失或超容差即判不匹配——不做"只按温度匹配"这类降级。

### 目录约定

`_pick_master()` 按文件名包含关键字筛选：

| 类型 | 接受的文件名片段 |
|---|---|
| dark | `dark`、`masterdark` |
| flat | `flat`、`masterflat` |
| bias | `bias`、`masterbias` |

大小写不敏感，只扫 `.fit` / `.fits` / `.fts`。文件名不含对应片段的会被跳过，因此同一目录可以混放三类母帧。

### 放宽容差

```python
resolve_calibration_plan(header, device, dir,
    tolerance={"CCD-TEMP": 3.0, "GAIN": 5.0, "EXPTIME": 1.0})
```

温度容差在夏季可能需要放宽；曝光容差对增益切换（high gain / low gain）的设备需要按 gain 容差处理。

## 2.4 calibrate 命令参数生成

```python
derive_calibration(calibration_plan, structure, chain)
```

生成规则：

| 条件 | 追加参数 |
|---|---|
| 有 bias | `-bias=<path>` |
| 有 dark | `-dark=<path>` |
| 有 flat | `-flat=<path>` |
| 有 dark **且** `cc.enabled` | `-cc=dark <sighi>` 或 `-cc=dark <siglo> <sighi>` |
| `structure.is_cfa` | `-cfa` |
| CFA **且** chain == debayer | `-debayer` |
| CFA **且** debayer **且** 有 flat | `-equalize_cfa` |

### 三个易错点

**① `-cc=dark` 需要 master dark。** 所有单/双阈值形式统一要求已选 master dark 且 `cc.enabled` 开启；`siglo` 非空（包括 0）也不能绕过检查。无 master 时不生成该参数。

`cc.enabled` 保留计划开关含义。实际执行状态在顶层 `report.notes`，不写入新字段：计划关闭、无 dark 不适用、脚本已生成但未执行、含校正参数的校准命令成功完成、失败或完成未确认。每条记录注明脚本及输入序列；dry-run、仅去拜耳和参考帧回退不宣称校正完成。组合脚本失败不能证明其中校准已完成，但此前独立校准成功的记录保留。命令完成不代表已经测量坏点消除效果。

**② `-equalize_cfa` 只在有 flat 时加。** 它均衡的是 **master flat 的 RGB 层均值**，不是帧的通道。不加 flat 时它无意义；且它**不能**替代可选的 `stack -rgb_equal` 背景归一化（见 05）。

**③ `-debayer` 决定下游还能不能用 drizzle。** 加了 `-debayer` 就变成 RGB 数据，drizzle 对非 Bayer 数据会 `abort`。这是 03 的链路选择必须在校准**之后**做探测的原因。

### 引号规则

若母帧路径含空格，整个 token 必须加引号：

```
calibrate sub "-dark=/masters/my dark.fit" -cc=dark 3
```

而不是 `-dark="/masters/my dark.fit"`。生成器在 `siril_script_gen.quote_argument()` 中处理。

## 2.5 偏置的表达式形式

Siril 接受表达式代替偏置母帧：

```
calibrate sub "-bias==256"
calibrate sub "-bias==64*$OFFSET"
```

当已知相机 OFFSET 而没有偏置母帧时可用。注意等号需要写两次，因为 `-bias=` 后面紧跟的值本身以 `=` 开头。

## 2.6 报告中的校准记录

```json
"calibration": {
  "bias": null,
  "dark": null,
  "flat": null,
  "dark_embedded": true,
  "flat_missing_reason": "smart_scope_no_flat",
  "cc": {"enabled": true, "siglo": null, "sighi": 3},
  "warnings": [
    "smart-scope profile: darks are captured internally at session start and already subtracted from these frames; no master dark applied",
    "smart-scope profile: no flat frames are produced by this device; residual vignetting will remain in the stacked master"
  ]
}
```

上述 `cc.enabled=true` 仅表示计划允许。对应的顶层 notes 应说明：

```text
dark-based cosmetic correction [astra_calibrate.sir / calibrate Ms]: not applicable: no master dark; Siril dark-based cosmetic correction was not requested; device-side correction is not verified
```

不能据此断言设备内部没有处理坏点。

两条 warning 是**设计预期**，不是错误。它们明确告知用户：残余暗角未被校正。这是智能望远镜的物理限制，不是处理缺陷。