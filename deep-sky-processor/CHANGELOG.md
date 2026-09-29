# Changelog

本文件记录 `deep-sky-processor` 的独立版本变更。

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
