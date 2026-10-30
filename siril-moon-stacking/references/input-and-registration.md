# 输入、选帧与配准

需要选择输入格式、视频采样、空间预算、设备先验或配准参数时使用本文。命令使用入口文档中设置的 `TASK_SKILL_DIR`、`TASK_PYTHON` 和 `TASK_SIRIL`。

- 静态相机 RAW 交给 Siril 解码；FITS 输入以符号链接建序。视频经 OpenCV 顺序解码并将入选帧写成 16-bit FITS，保留原视频；SER 直接读取规范头和帧数据，并尽可能将采集元数据写入 FITS。
- 单色输入或 `--force-mono` 使用 `L 1`，RGB 使用 `L 3`。月面高反差特征与 FFT 相位相关用于选帧和亚像素配准，结果写入 `.seq` 的 `R0` 与选帧状态。

## 环境探测与导入
运行诊断探测并导入原始序列：
```bash
# If needed, install dependencies in the selected Python environment
"$TASK_PYTHON" -m pip install -r "$TASK_SKILL_DIR/requirements.txt"

# Check Siril CLI and dependencies
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" probe

# Import a directory of RAW or FITS frames
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" import --input /path/to/moon_raws --work /path/to/work

# Import a SER recording; a positive limit takes the first frames
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" import --input /path/to/moon_capture.ser --work /path/to/work --limit 500

# Option C: Score every MP4 / AVI / MOV frame and import adaptive selections as 16-bit FITS
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" import --input /path/to/moon_capture.avi --work /path/to/work
```
> **视频两遍顺序读取（MP4 / AVI / MOV / MKV / M4V 共用同一路径）**：默认保留原始视频，先完整逐帧评分，再顺序读取并只将入选帧写成 FITS，最后交给 `register` / Siril 配准与堆叠。不会先把全片展开为 FITS，也不会为每张入选帧反复 seek。
> * `adaptive`（**默认**）：复用 Otsu 自适应选帧决定导入数量；`--limit 0`（默认）表示**无数量上限，仍按质量筛选**，正数则是入选帧上限。可用 `--select-mode utility` / `relative` / `percent` 或 `--keep-percent` 调整导入筛选；`all` 默认只在导入时做一次主质量筛选，后续配准仅剔除低置信度或失败帧；`--register-selection reselect` 可显式重新筛选。
> * `--probe-stride 1`（**默认**）：整段视频每帧都评分。显式设为更大值只减少评分次数，仍需逐帧解码，并可能漏掉好帧。评分使用绿色通道或单色平面，通过缩小图像的特征相关跟踪同一地貌 ROI，相关性不足时回退质心位移；月盘出框或无法可靠定位时会记录质量标记。
> * `smart-top`：显式 `--limit N` 时从整段视频的已评分候选中取全局 Top N；`smart-cluster` 则按 10 个时间窗分散选帧。两者都顺序提取；`smart-cluster` 使用真实呈现时间戳分窗，无法验证时间戳时明确报错。
> * **明确要求全帧落盘**：`--sample-mode head --limit 0` 跳过导入质量筛选；若还要求所有有效、可配准帧参与叠加，`all` 再加 `--keep-percent 100`。默认 `adaptive --keep-percent 100` 也会保留所有正清晰度候选，但解码失败、低信号或无有效结构帧不在其候选池中；空间不足仍会缩减落盘数量。
> * `window` / `head`：自 `--start-frame` 起顺序读取，正数 `--limit` 控制片段帧数；智能模式始终扫描整片，不使用该起始偏移。
> * `import_receipt.json` 的 `video_metadata.seeing_probe` 记录评分帧数、实际 stride、选择模式与入选数量；`adaptive` 另记录 `selection_meta`（阈值与决策原因）。这些是导入候选，`register` 默认复用主筛选并检查配准置信度，`stack` 只叠加 `I 1` 的帧，并从 Siril 日志和输出序列核对实际帧数。空间节省比例取决于实际入选数，配准输出与 Drizzle 仍需额外空间。
> **自动裁剪与空间预算（1.1.0）**：`--video-roi auto` 默认用固定框覆盖月盘、可见月尖和候选漂移范围，`--roi-margin 64` 留出原始像素边距；不会缩放数据。无法可靠定位时回退全画幅，`--video-roi off` 可显式保留全画幅。默认提前估算 FITS、浮点重采样、Drizzle 临时文件、自适应小波分层及成片，增加 25% 估算余量，并保留至少 10 GiB 或磁盘可用空间 10%（取较大值）。空间不足按质量缩减候选，无法容纳最低数量时中止；`--disk-budget-gb` 可进一步限制工作目录体积，单位 GiB，也可用于独立 `postprocess`。分层提取前再次检查新增空间，空间不足明确报错。
>
> **评分与复用**：在月面内部最多三个特征区域进行曝光归一化、局部噪声估计和饱和检查，以缩小图像的特征相关跟踪稳定 ROI，防止邻近亮点参与月面评分。`--min-signal-ratio 0.2` 默认排除月面信号低于片段第 90 百分位 20% 的帧，避免过暗 MP4 编码块被当成清晰细节；可按拍摄条件调整，0 关闭此额外门限。`video_scores.json` 缓存全片评分、PTS、位置及质量标记，并校验源 SHA-256、算法版本与参数。增加 `--limit` 只新增缺失 FITS，复用固定参考帧下的配准结果；源文件、裁剪或通道不兼容时使用新工作目录。
>
> **可选反馈扩容**：`all --candidate-mode feedback` 从最多 256 帧开始翻倍，使用每轮完整入选序列执行 Siril 全局剔除堆叠。固定公共区域与参考月盘之外的同一批天空像素，噪声至少降低 5%、细节下降不超过 3% 才接受新一轮，连续两轮无改善停止；候选、用户上限或空间预算也会终止扩容。`candidate_feedback.json` 保留各轮指标、实际帧数、获选轮与停止原因；默认仍为 `single`。背景或细节指标无法可靠测量时保留当前结果并明确停止，不宣称找到了最佳帧数。

```bash
# Reuse cached scores and grow only missing video candidates
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" all --input /path/to/moon.mp4 --work /path/to/work --candidate-mode feedback --limit 2048

# Preserve full dimensions
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" import --input /path/to/moon.avi --work /path/to/full --video-roi off
```

> **⚠️ SER 路径的 `--limit` 语义不同（易踩坑）**：`import` 的 SER 分支**不具备** `--sample-mode` 的智能扫描能力，`--limit N` 退化为**顺序截断（取前 N 帧）**，等价于视频路径的 `head` 模式。因此外部工具转出的 `SER → import --limit 500` 拿到的是**前 500 帧**，而不是 MP4 / AVI `adaptive` 路径的**全片自适应择优候选**或 `smart-top --limit 500` 的 Top 500。
> * 导入外部 SER 时若仍要保住 lucky imaging 的选帧收益，请用 `--limit 0` **全帧导入**，让 `register` 在完整候选池上做 Otsu 选帧——全片均有参与下游评估的机会，最终选帧仍由 `register` 决定；
> * **转 SER 不会免除选帧环节**（换容器不改变视宁度造成的帧间质量差异），但会**削弱 `import` 阶段的内容驱动抽帧能力**。仅在本技能流水线内处理时，mp4/avi 直解路径功能更全（含 sidecar `DATE-OBS` 注入、Bayer 图案探测与设备识别），转 SER 属负收益；仅在需要与 AutoStakkert / PIPP / SER Player 等外部工具互通时才转 SER——**本技能不内置视频转 SER 工具**，请用 ffmpeg 或 PIPP 自行转换。
> **前置条件**：需已安装 Siril 1.4.4+（macOS 默认路径 `/Applications/Siril.app/Contents/MacOS/siril-cli`，可用 `--siril` 覆盖）。
> `probe` 输出 JSON；检查 `ready`、`errors` 和 `warnings`，未就绪或无法验证时退出码非零。兼容版本的 `opencv-python` 可替代 `opencv-python-headless`，但会产生提示。探测不会自动安装依赖。

> **采集设备识别与光学先验（`--device`）**：智能望远镜的视频/SER 往往不写 `FOCALLEN`/`APERTURE`，此时内置规格表会兜底提供像元尺寸/焦距/口径。
> * 自动识别信号优先级：FITS 头 `TELESCOP`/`INSTRUME`（`high`）→ 采集侧车 `SENSOR`/`TELESCOP`（`medium`）→ 文件名（`medium`）→ 传感器型号回退（`low`，仅当共用该传感器的机型规格一致时才采纳整组，否则最多只回退像元尺寸）。仅命中品牌（如只写 `DWARFLAB`）时不注入任何先验。
> * `--device <id>` 强制指定（如 `--device seestar-s50`）；`--device none` 完全关闭。`"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" devices` 打印全部 19 个机型的 JSON 规格表（含来源等级与存疑标注）。
> * **先验永远不是实测**：由表写入的 FITS 头会带 `DEVICE`/`DEVSRC`/`DEVCONF`/`OPTPRIOR`/`OPTISRC` 溯源标记，来源标注显示为 `FITS Header (FOCALLEN) [device_table: seestar-s50]`。**侧车显式值与实测永远优先**——只有缺失的键才会被填，且 `OPTPRIOR` 精确列出被填的键。
> * **FITS 输入路径只识别不注入**：FITS 帧是用户原文件的软链接，技能红线禁止改写用户原始目录；识别结果只写入 `import_receipt.json` 的 `device` 字段并打日志。此时若需要这些先验，请显式传 `--focal`/`--pixel-size`/`--aperture`，或依赖 `--drizzle auto` 的月轮 ESF 实测判据（无需任何光学元数据）。
> * 完整规格表、来源等级、存疑说明与识别规则见 [`device_specs.md`](device_specs.md)。

## 智能选帧与刚体亚像素配准
自动识别月面反差特征，通过双锚点互相关解算亚像素视场旋转角 $\theta$ 与平移 $(dx, dy)$：
```bash
# Register using default adaptive frame selection
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" register --work /path/to/work

# Optionally select frames with the MTF-SNR utility model
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" register --work /path/to/work --select-mode utility --utility-alpha 2.0 --utility-beta 1.0
```
* **选帧模式一览**：
  * `otsu`（**默认推荐**）：Otsu 自适应双峰聚类，自动判定当晚平静视宁度临界断崖；内置**超稳视宁度相对散布感知（Spread-Aware）**，当全序列极差 $<6\%$ 时自动扩容保留至 75% 优质帧，兼顾极致信噪比与抗抖动；
  * `utility` / `mtf-snr`：**MTF-SNR 联合效用模型**，求导最大化 $U(k) = \bar{Q}(k)^\alpha \cdot \sqrt{k/N}^\beta$，严格平衡清晰度衰减与降噪增益；
  * `relative`：按参考帧质量相对阈值筛选（`--quality-threshold 0.75`）；
  * `percent`：固定/经验分级百分比（`--keep-percent <val>`）。
* Python 在 Siril `.seq` 中写入 `R0` 刚体单应性矩阵及 `I <index> 1/0` 选帧标记。矩阵使用 FITS 图像坐标约定；旋转和平移公式见 [Siril CLI 参考](siril-144-cli.md)。

## 月相与选帧参考

下表是经验参考，实际视宁度应结合帧质量测量，不能由月相单独确定。

| 月相或拍摄条件 | 视宁度预估 | 建议保留率 (`--keep-percent`) | 锚点特征定位建议 | 适用后处理倾向 |
| :--- | :--- | :--- | :--- | :--- |
| **满月 (Full Moon)** | 普遍中等 | 30% ~ 40% | 自动定位第谷/哥白尼辐射纹高频区 | 极佳的矿物月制作时机，中度小波重构 |
| **上弦/下弦/凸月** | 良好 | 40% ~ 50% | 自动锁定明暗交界线（Terminator）断崖群 | 激进小波第 1~3 层重构，凸显立体月坑阴影 |
| **娥眉月 (Crescent)** | 视宁度常较差（低仰角） | 10% ~ 20% | 自动加窗于亮月牙中段，抑制大面积背景黑边 | 保守小波重构，配合中度 unsharp，防止颗粒噪声 |
| **薄云/经纬仪大漂移** | 气流不稳定 | 15% ~ 25% | 启用置信度强过滤，自动剔除漂移过大或云雾帧 | 标准自然细节版为主 |
