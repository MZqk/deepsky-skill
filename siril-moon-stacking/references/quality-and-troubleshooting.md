# 产物、质检与故障恢复

需要检查交付文件、质量指标、比较结果或排查失败时使用本文。命令使用入口文档中设置的 `TASK_SKILL_DIR`、`TASK_PYTHON` 和 `TASK_SIRIL`。

## 主要产物

实际生成的文件取决于输入通道、处理选项和执行的阶段。

* `moon_master.fit`：32 位未锐化母版；
* `moon_unsharpened.fit`：完成校准与同参数 MTF、但未反卷积或锐化的完整链路基准；
* `moon_natural_baseline.fit`：按自然色成片流程渲染并量化到 16 位网格的浮点质检基准；
* `moon_natural_master.fit` / `moon_mineral_master.fit`：显示几何调整前的浮点成片（单色不生成矿物月）；
* `moon_lum.fit`：32 位物理明度母版（仅 LRGB 模式）；
* `moon_natural.tif`：16 位小波细节母版（自适应居中留白保护）；
* `moon_natural.jpg`：高清晰度自然写实影调成果图（全月盘自适应居中与安全深空留白）；
* `moon_natural_square.jpg`：1:1 自然写实方形特写社交成果图（自适应 1.50× 直径居中留白）；
* `moon_mineral.jpg`：电影级深影调地质彩月成果图（Deep-Cine 哑光玄武岩油润深影调，自适应居中留白）；
* `moon_mineral_square.jpg`：1:1 地质彩月方形特写社交成果图（自适应 1.50× 直径居中留白）；
* `moon_raw_original.jpg`：相机原生未加工真实底片（原始原生画幅、未经仿射平移或扩边留白，保留真实传感器底噪与大气弥散辉光）；
* `moon_reference.jpg`：亚像素坐标对齐对比帧（同步自适应留白与公允拉伸，1:1 精确对齐成片网格，专用于微观细节比对）；
* `comparison.html`：**交互式 HTML 双窗同步对比器**（自包含零依赖单文件，双击即开）；
* `lunar_profile.json`：校准 Profile 档案（记录通道增益 $k_r, k_b$、直方图截断 $bg, hi$、矿物月色彩系数，可供其他面板锁入）；
* `mosaic_tile_info.json`：切片元数据清单（记录尺寸、位深、物理比例、Profile 锁定状态与产品路径，供 `siril-mosaic` 马赛克拼接技能直接消费）。

## 质检报告与交互式对比审验
```bash
"$TASK_PYTHON" "$TASK_SKILL_DIR/scripts/moon_stack.py" --siril "$TASK_SIRIL" verify --work /path/to/work

# Open the comparison viewer on macOS
open /path/to/work/comparison.html
```
* **交互式 HTML 双窗同步对比器 (comparison.html)**：
  * **参考图源双模切换**：
    * `🎯 对齐对比帧`：与成片严格重合，专用于微观地貌的 1:1 卷帘与闪烁微观细节比对；
    * `📷 原生真实 RAW`：相机原生拍摄画幅，无仿射平移、无扩边留白、无消光，真实还原单帧拍摄底片；
    * 快捷键 **`R`** 可在两种参考模式间实时瞬时切换；
  * **三模无缝切换**：
    * `Side-by-Side`（双窗并排联动）：左侧显示参考单帧，右侧显示堆叠增强成片，两窗完全 1:1 亚像素同步平移与缩放，带同步十字准星；
    * `Split Slider`（卷帘滑动对比）：单视口可拖拽分割条，实时查看微坑与噪点“擦玻璃”式过渡；
    * `Blink Toggle`（闪烁交替对比）：按空格键在单帧与成片间极速切换，敏锐发现细节增益；
  * **预设倍率与居中**：支持 `Fit` 适屏、`100%`（1:1 原始物理像素）、`200%` 高频检测、`Center`（一键居中月轮）；
  * **快捷键支持**：`Space` (Blink闪烁)、`R` (切换对齐/原生RAW)、`1` (100%)、`2` (200%)、`0/F` (适屏)、`C` (居中)、`Tab` (切换视图)、`M` (切换成片)、`H` (HUD面板唤出/折叠)；
  * **质检与光学 HUD 抽屉 (全中文，默认隐藏)**：默认隐藏保持画面纯净，按 `H` 或点击顶栏 `📊 质检 HUD` 随时唤出；全面呈现中文量化指标（拍摄设备、焦距/口径、堆叠帧数、DHR、CSI、GKM、深空残噪与地质矿物增益）。
* **全面量化质检指标矩阵**：
  * **防过锐状态**：HUD 显示保护状态、增强保留比例、自适应状态及可信覆盖率；`verify_report.json.sharp_guard.delivery` 区分实际 TIFF 通过、失败、证据不足和关闭。展示性阈值提示不能替代完整基准上的自动接受规则；
  * **切片模式与 Profile 锁定状态**：显示 `Mosaic mode: tile/disc` 与 `Calibration Profile: LOCKED (source=..., hi_lum=...)` 或 `AUTO`；
  * **光学参数推断状态 (Optical Setup)**：显示焦距 $f$、口径 $D$、焦比 $F$、像元 Airy 斑尺寸及推断来源（如几何反推 `geometric inversion` 或 Header）；
  * **暗环比率 (Dark Halo Ratio, DHR)**：度量阶跃明暗边缘阴影侧负下冲能量。$<0.015$ 为 `EXCELLENT (artifact-free)`，$<0.035$ 为 `GOOD (controlled)`，$\ge 0.035$ 触发暗环警报；
  * **高光白垩饱和度 (Chalky Saturation Index, CSI)**：检测过度拉伸导致的死白与微反差抹平。$<0.010$ 为 `EXCELLENT (highlight dynamic retained)`，$\ge 0.025$ 触发白垩化警报；
  * **梯度峰度脆裂度 (Gradient Kurtosis Metric, GKM)**：评估边缘梯度重尾分布以量化过度锐化与人工毛刺脆裂感。$<6.0$ 为 `ORGANIC (natural smooth)`，$6.0\sim 14.0$ 为 `CRISP (high detail)`，$\ge 14.0$ 触发脆裂警报；
  * **底噪标准差与动态范围**：精确检查四角深空底噪 $\sigma_{bg}$ 与主直方图像素极值。

## 故障排查与恢复

1. **报错：`Cannot load a memory-mapped image: BZERO/BSCALE ...`**：
   * 已在此架构中强制统一使用 `memmap=False` 规避，保证与 Astropy 7.x 完美兼容。
2. **报错：`common area too small after alignment`**：
   * 说明序列中有个别帧发生了镜头严重抖动或出框。本插件已内置置信度阈值过滤，会自动丢弃此类异常帧；如仍出现，可降低 `--keep-percent`。
3. **Siril SSF 脚本独立调试**：
   * 所有步骤均在工作目录的 `logs/` 下保留了对应的 `01_import.ssf`、`02_align_stack.ssf`、`03_postprocess.ssf` 与日志。用户或智能体可直接使用 Siril GUI 载入单步排查。
4. **月面重影分层同心弧 (Concentric Ghosting Limb)**：
   * 原因：FITS 图像坐标系原点位于左下角，纵轴向上递增；而 NumPy 二维数组索引按行向下递增。若将向下位移（`+dy`）直接取反注入 Siril 的 `R0` 单应矩阵 $H_{23}$，会导致帧向相反方向错位，使经纬仪漂移误差翻倍（形成梯田状分层重影）。
   * 规范：无旋转时 $H_{23}=+dy$；有旋转时还需计入旋转中心项。完整公式与 Siril 输出原点偏移见 [Siril CLI 参考](siril-144-cli.md)。
5. **月面偏绿与过曝/欠拉伸 (Green Cast & Overexposure)**：
   * 原因：拜尔阵列（RGGB）绿光通道灵敏度偏高，且相机存在基底偏置（Black Pedestal）；深空常用的 `autostretch` 默认以 80% 黑背景为参考将其拉伸至 0.25 灰度，导致行星/月面高光剧烈溢出。
   * 规范：采用月面专用的通道独立 `mtf` 动态截取背景偏置并匹配高光，结合 `rmgreen` 消除色相偏色，再进行小波细节恢复。
6. **环形山边缘硬振铃/白边暗环防御 (Overshoot & Ringing Prevention)**：
   * 原因：双三次（Bicubic）插值核在阶跃边缘存在负旁瓣，对于阳光直射的亮坑壁与深邃阴影交界处易产生微过冲（白边）与下冲（黑圈）；若小波锐化过猛会被成倍放大。
   * 规范：
     * 使用 `--anti-ringing auto` 衰减边缘负增量和正过冲，再由完整链路护栏检查损伤；效果依赖素材，不保证细节完全无损；
     * 极端高反差或追求绝对零伪影时，建议使用 `--interp li`（双线性稳健模式），并联动自动放宽小波第 1 层至 `1.10`；
     * 追求极限 MTF 分辨力时使用 `--interp cu`（双三次模式），并严格锁定小波第 1 层在 `1.05` 抑制过冲；
     * 无论何种模式，Siril `seqapplyreg` 均严格保持默认 Clamping，绝不使用 `-noclamp`。
7. **高光白垩死白与微反差缺失 (Chalky Bleached Highlights)**：
   * 原因：非线性拉伸高光截断点过紧，或 CLAHE clip 过大导致辐射纹和环形山亮峰像素全部挤压在 1.0 附近，微结构梯度归零（CSI 指标超标 $>0.025$），形成石膏般平板死白。
   * 规范：调大高光保护余量（默认 `midtone 0.13` 配合 $p_{99.95} \times 1.10$ 线性高位），适当降低 `--clahe-clip` 或选用 `--sharp-mode auto / mellow`。
8. **质感脆裂硬化与人工毛刺 (Brittle / Crunchy Over-Sharpening)**：
   * 原因：多尺度小波重构增益偏激，叠加了单尺度 USM 锐化，导致全图梯度分布出现厚尾极端毛刺（GKM 指标超标 $>14.0$）。
   * 规范：保持 `--sharp-mode auto`（反卷积能量折让与 USM 自动旁路），避免手动叠加过激小波系数。
9. **马赛克面板边缘被切除或外太空清零抹杀 (Mosaic Tile Edge Truncation)**：
   * 原因：在多面板月面全景拼接时，默认的 `disc` 模式使用 `framing min` 统一截幅，并使用 RANSAC 月轮拟合对 $r > R + 4.5$ 处清零，导致切片邻近区域的月面地形被误杀。
   * 规范：拼接切片必须显式指定 `--mosaic-mode tile`，系统将自动使用 `--framing max` 保留最大画幅交叠，并旁路所有月盘半径清零逻辑，导出 `mosaic_tile_info.json` 与 `siril-mosaic` 联动。
10. **多面板拼接切片接缝阶梯断层与色块跳跃 (Mosaic Seam Stepping & Color Patch Discontinuity)**：
    * 原因：多面板切片地质反照率差异悬殊（如玄武岩暗月海 vs 亮高地），若各自独立计算 $p_{99.95}$ 与通道比，MTF 映射斜率相差数倍，拼接时重叠区明暗阶梯断层跳跃可达 400%+ 且色块漂移严重。
    * 规范：先选定代表性面板作为基准（Anchor Master）导出 `lunar_profile.json`，邻近切片后处理时传入 `--lock-from /path/to/anchor` 或 `--lock-profile ...`，统一白平衡增益与非线性 MTF 映射，再检查实际重叠区；不能保证接缝误差为零。
11. **非标准视频解码失败与损坏容器自愈 (Non-Standard Video Codec & Corrupted Container Recovery)**：
    * 原因：拍摄中途断电/App崩溃致 MP4 缺失关键 `moov` 索引原子；视频采用 H.265/HEVC、AV1 或 Apple ProRes 高规格编码，而当前 Python OpenCV 缺少解码器后端；行星相机特殊未压缩调色板（FourCC 如 `Y800`, `GREY`, `DIB `）未被映射；天文专用 SER 文件或 FITS 图像被误命为 `.mp4/.avi` 扩展名。
    * 规范：脚本内置全套智能文件头与 OpenCV 状态诊断器（`format_video_decode_error`），在解码受阻时自动输出包含文件大小、魔数、FourCC、捕获状态及确凿根因的报告；针对未闭合 MP4 自动生成快速修复指令（`ffmpeg -err_detect ignore_err -i input.mp4 -c copy fixed.mp4`）；针对非标准编码自动生成无损转码指令（`ffmpeg -i input.mp4 -c:v rawvideo -pix_fmt bgr24 converted.avi`）；识别到真实 SER/FITS 头时自动引导使用 `--format ser` 或 `--format fits`。
    * **Siril 自身不能做「视频 → SER」转换**：`convert` 只扫描静态图像，实测对现存的 `.mp4`/`.avi` 一律报 "No files were found for conversion"；CLI 的 `load` 也读不了 SER（实测对 Siril **自己写出**的 `.ser` 同样报 "file not found or not supported"，属 CLI 读取限制而非文件缺陷）。若需把 MP4 交给 AutoStakkert / PIPP / SER Player 等外部工具，请用 ffmpeg 或 PIPP 自行转换——**本技能不内置视频转 SER 工具**（mp4/avi 直解路径功能更全，转换属负收益）。
