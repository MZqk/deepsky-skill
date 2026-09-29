# astrometry-source-preservation

源头 FITS Header 保留与天体测量 (WCS) 同步规范。

## 1. 适用场景与痛点

在深空天文后期处理中，Stage 4（天体测量校准）常因以下原因受阻：
1. **本地星表缺失**：离线环境下未配置数十 GB 的全天区 Gaia 星表，无法执行盲解（Blind Platesolve）。
2. **堆栈元数据丢失**：拍摄设备（如 Seestar S30/S50、ASIAIR、NINA、KStars）在原始单帧（subframes）或参考子帧中已写入了高精度的拍摄先验（`RA`, `DEC`, `FOCALLEN`, `XPIXSZ`）甚至机内解算好的完整 WCS（`CRVAL1/2`, `CRPIX1/2`, `CD1_1..2_2`, `CTYPE1/2 = 'RA---TAN-SIP'`）。但第三方或常规堆栈流程（如 Siril 默认堆栈、DSS、旧版自动化脚本）在输出 Master FITS 时，丢弃了 WCS 投影卡片，导致下游处理工具误判为“无解算信息”。

**核心方案**：在进入 Stage 4 之前，利用“源头 FITS Header 保留”机制，将源头单帧/参考帧中的 WCS 矩阵与天文先验无损同步至堆栈 Master FITS，实现 100% 离线、零外部依赖通过 Stage 4。

---

## 2. 核心技术防线与规范

1. **科学像素绝对不变性 (Bit-for-Bit Pixel Invariance)**：
   - 同步工具**绝不**重新采样、插值、压缩或修改 Data HDU。
   - 32-bit float 或 16-bit 像素数组在逐字节层面 100% 保持不变。
   - 注入前后对科学数据块进行 SHA256 强制哈希校验；任何哈希不一致将立即中止并回滚。
2. **智能字段优先级 (Smart Header Merging)**：
   - 堆栈图特有的累积元数据（如 `STACKCNT`, `LIVETIME`, `OBJECT`, `PROGRAM`, `HISTORY`）默认保留，不被单帧的 `STACKCNT=1` 覆盖。
   - 缺失的天体测量关键字段（WCS 坐标系、焦距、像元大小、赤经赤纬、滤镜、仪器型号等）从源头完整注入。
3. **几何防呆与参考点自适应 (Geometry Consistency)**：
   - 严格比对源与目标的画幅尺寸（`NAXIS1`, `NAXIS2`）。
   - 尺寸一致时（常规全画幅堆栈）：WCS 逐字映射。
   - 尺寸不一致时（如堆栈边缘裁切或局部装配）：默认安全拦截，需配合 `--center-align` 或 `--offset-crpix dx,dy` 重新修正 `CRPIX1/2` 投影中心。

---

## 3. 操作指令与工具使用 (`sync_fits_header.py`)

工具位置：`starun-siril/scripts/sync_fits_header.py`。纯 Python 实现，零额外依赖。

### 3.1 诊断与审查 (`inspect`)
检查目标 FITS 是否具备完整 WCS 或先验，并可与参考单帧比对：
```bash
python3 starun-siril/scripts/sync_fits_header.py inspect /path/to/master_stacked.fit \
  --reference /path/to/raw_subframe.fit
```
**输出判定**：
- `complete_wcs: YES` → Stage 4 将直接识别为 `preserved_existing_solution`。
- `complete_wcs: NO (ready_for_targeted_solve)` → 具备坐标与像元先验，支持窄视场极速靶向解算。
- `complete_wcs: NO (missing_astrometric_evidence)` → 缺失坐标先验。

### 3.2 同步与注入 (`sync`)
将源头单帧/参考帧的 WCS 和先验卡片注入到堆栈 Master 中：
```bash
python3 starun-siril/scripts/sync_fits_header.py sync \
  --source /path/to/raw_subframe_with_wcs.fit \
  --target /path/to/master_stacked.fit \
  --mode wcs-and-priors
```
- 默认在原地安全更新目标文件（自动生成 `.bak` 备份），并输出像素 SHA256 校验结果。
- 若需输出至新文件：添加 `--output /path/to/master_with_wcs.fit`。
- 若需模拟演练：添加 `--dry-run`。

### 3.3 验证 Stage 4 合规性 (`verify`)
```bash
python3 starun-siril/scripts/sync_fits_header.py verify /path/to/master_stacked.fit
```
若返回退出码 0，表示可直接送入 `starun-siril` Stage 4。

---

## 4. 堆栈期避免 WCS 丢失的最佳实践

在拍摄和堆栈源头阶段，可通过以下设置避免 WCS 丢失：

1. **Siril 堆栈工作流**：
   - 在 `register`（对齐）阶段，选择已经完成天体测量（Platesolve）的单帧作为**参考帧（Reference image）**。
   - 堆栈命令中使用 `-norm` 归一化时，确保保留参考帧的主 Header。
2. **Seestar S30 / S50 设备**：
   - Seestar 内部堆栈完成后的 FITS 文件（如 `SeestarS30-*.fit`）默认带有机内 Platesolve 写入的完整 `RA---TAN-SIP` 投影和 `CD` 矩阵。
   - 若将多天的单帧或多段视频使用外部电脑堆栈，可保留其中任意一张带有机内 WCS 的单帧作为 Header 同步母本。
3. **ASIAIR / NINA 设备**：
   - 确保采集中开启天体测量同步；单帧 Header 中必须包含 `OBJCTRA`/`RA`、`OBJCTDEC`/`DEC`、`FOCALLEN`、`XPIXSZ` 真实数值。

---

## 5. 验收标准与 Stage 4 联动

当堆栈 Master FITS 经由本方案完成 WCS 注入后，执行 `starun-siril` Stage 4 脚本：
```ssf
requires 1.4.4 1.5.0
set32bits
load "/path/to/master_with_wcs.fit"
save "/path/to/040-solved" -chksum
stat main
savejpg "/path/to/040-solved" 95
close
```
- `starun-siril` 核心验证逻辑会自动确认 `complete_wcs == True`。
- 脚本执行跳过冗长的网络请求与星表搜索，生成的 Receipt 记录：
  ```json
  "astrometry": {
    "status": "preserved_existing_solution"
  }
  ```
- 像素数据与坐标系统实现 100% 无损闭包，直接放行后续 Stage 5 PCC/SPCC 颜色校准。
