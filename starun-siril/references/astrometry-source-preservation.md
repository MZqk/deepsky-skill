# astrometry-source-preservation

源头 FITS Header 保留与 WCS 同步的适用条件和证据边界。

## 1. 适用场景与前置证据

master 缺少 WCS 或采集先验时，先检查实际 Header、采集记录和配准参考帧。源头单帧可能携带可用字段，但设备型号、文件名或相同画幅不能证明这些字段存在、正确或适用于 master。

WCS 同步只适用于有来源的参考帧，且参考帧到 master 的像素坐标对应关系已经确认。保留或补充 WCS 后，仍须按 [astrometry.solve](stages/stage-04-astrometry-color.md#astrometry-solve) 完成原生 Siril 产物验证和审查。Stage 4 的 PCC/SPCC 另须满足 [color.calibrate](stages/stage-04-astrometry-color.md#color-calibrate) 的通道、目录和设备响应前置条件。

`sync_fits_header.py` 使用 Python 标准库，可离线运行；这个依赖范围只覆盖 Header 辅助工具。原生处理仍需 Siril，WCS/像素产物核验还需 NumPy，测光校色仍需所选 Gaia 目录。

## 2. 同步范围与几何条件

- 工具重写 Header，原样复制原 Header 之后的数据部分，不重采样、插值或生成科学图像。默认启用数据 SHA-256 核验，哈希相等支持这段字节保持不变；不证明 WCS 的天空对应、元数据真实性或校色正确性。核验不符时中止，不提交同步结果。
- 默认保留目标已有采集先验，缺失项可从有来源的参考补充；WCS 字段会由源替换。结构与像素解释字段不从源复制。单帧先验不能作为整个堆栈历史的证明。
- `geometry_match` 仅比较宽高。同尺寸不证明参考帧与 master 的旋转、翻转、缩放、裁切和像素坐标一致。
- `--center-align` 仅适用于已确认的中心裁切；`--offset-crpix dx,dy` 仅适用于已知平移，两者都不能修复未知旋转、缩放或其他重采样。
- `--allow-geometry-mismatch` 只绕过尺寸拦截，不验证坐标映射。映射关系不明时不复制 WCS；只同步可靠先验，再按 astrometry.solve 对 master 解析。

## 3. 工具使用

从技能目录解析辅助脚本绝对路径，使用 [CLI 合同](cli-contract.md#解释器与入口) 中已验证的 `STARUN_PYTHON`。

### 3.1 诊断与审查 (`inspect`)

检查 master 的 Header，并可与有来源的参考帧比较：

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/sync_fits_header.py inspect /abs/master_stacked.fit \
  --reference /abs/reference.fit \
  --target-size-arcmin 60.0
```

| 输出 | 实际检查范围 |
|---|---|
| `complete_wcs` | 必需 WCS 字段、数值范围、矩阵和声明的 SIP 项通过 Header 完整性检查；不测量天空匹配 |
| `wcs_round_trip.verified` | 采样点的像点与天球变换内部闭合；不证明恒星位置与真实天空对应 |
| `Sky Footprint / Framing` | 由 Header 和提供的目标角大小派生视场与构图诊断；不是独立天空验证 |
| `stage4_readiness / stage4_status` | 辅助工具的前置分类；不是已成功执行的 astrometry run receipt |
| `geometry_match` | 参考与目标宽高相同；不证明像素坐标映射 |

辅助分类 `preserved_existing_solution` 表示 Header 可供评估保留；`ready_for_targeted_solve` 表示先验字段存在，数值和来源仍须按协议验证；`missing_astrometric_evidence` 表示缺少相关证据。实际是否执行和采用由当前 session、协议验证与审查决定。

### 3.2 同步与注入 (`sync`)

确认映射和字段来源后写入新的 master 副本。此步在 init 前完成；已有 session 的输入不得改写，新文件须创建新 session：

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/sync_fits_header.py sync \
  --source /abs/reference_with_wcs.fit \
  --target /abs/master_stacked.fit \
  --output /abs/master_with_wcs.fit \
  --mode wcs-and-priors
```

本技能必须显式传入 `--output`，使用尚不存在且不同于 source/target 的路径，保留原 master。映射不明时使用 `--mode priors-only`，不复制 WCS；字段仍须有可靠来源。模拟检查可加 `--dry-run`，实际同步保留默认 SHA-256 核验。

### 3.3 Header 完整性检查 (`verify`)

```bash
"$STARUN_PYTHON" /abs/starun-siril/scripts/sync_fits_header.py verify /abs/master_with_wcs.fit --json
```

退出码 0 只表示 Header WCS 完整性检查通过；不完整时返回 1。round-trip 结果单独报告，不参与该退出码：即使其 verified 为 false，完整 Header 仍返回 0。不得把退出码、辅助分类或内部闭合描述为整个 Stage 4 通过或天空匹配已验证。

## 4. 采集与堆栈来源记录

对 Siril、Seestar、ASIAIR、NINA 等来源，检查实际文件中的坐标、采样、WCS、滤镜和历史字段，不按设备名称保证字段或投影存在。记录实际配准参考帧及旋转、缩放、裁切过程，保留源 Header，并检查最终 master 的 Header。

来自其他日期或任意单帧的 WCS 不因设备、目标或尺寸相同而可用；只有已确认坐标映射的参考才能作为 WCS 同步来源。

## 5. Stage 4 验证与采用

以新 master 初始化 session，再使用 [astrometry.solve](stages/stage-04-astrometry-color.md#astrometry-solve) 的保留或求解路径。成功 run 要求实际产物写出并通过协议的 WCS、几何和像素核验；审查还须结合来源证据和实际天空对应关系。仅当这些检查支持采用时，将候选作为后续父源。

run receipt 的 `preserved_existing_solution` 证明已有 WCS 按合同保留，不能补充原来源没有提供的天空匹配证据。校色仍按 Stage 4 的独立协议执行并审查；Header 同步或 astrometry 成功不自动放行 PCC/SPCC。
