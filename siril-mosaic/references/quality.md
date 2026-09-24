# 马赛克验收与质量门禁

仅在查看最终预览、决定是否调整 feather/解算参数，或解释 `review_required` 时读取本文件。

## 预处理防线（Pre-stitching Hygiene）

马赛克接缝与双星的根源往往在单 panel 预处理阶段。输入 panel 必须遵循以下红线：

1. **绝对禁止单 Panel 背景过度拟合（Avoid Overfitting Backgrounds）**：
   - 各 panel 在拼接前若做平场/光害梯度扣除，**严禁使用密集采样点或高阶曲面拟合（如 RBF、高阶样条）**。
   - 过度拟合会将重叠区边缘真实的暗星云或微弱连续渐变当成光害削平，在画幅边缘产生上翘或下凹的曲率失真。Siril 的 `-overlap_norm` 在重叠区进行线性增益与偏移匹配时，两侧相悖的边缘斜率无法数学对齐，拼合后在拼缝处**必然产生死黑拼缝或阶梯反差断层**。
   - **正确做法**：拼接前单图若有大光害梯度，仅允许 1~2 阶超低阶多项式平面扣除；或完全不做单图背景提取，直接保留原始线性背景进行拼接，将背景平场留待拼接出全幅马赛克后再做统一提取。
2. **底噪与信噪比对齐（Match Noise & SNR across Panels）**：
   - 各 panel 应尽量具备接近的有效积时和信噪比。若某一 panel 积时过短或透光条件差，即使 `-overlap_norm` 拉齐了平均亮度，重叠线两侧仍会出现“一边细腻平滑、一边粗糙爆裂”的**噪声接缝（Noise Seams）**。
3. **几何与光学一致性**：
   - 确认输入 panel 属于同一光学系统（无混合焦距、无因严重温漂引起的像元尺度漂移）。

## 机器门禁

`result.json` 的下列项必须全部通过：

- Siril 返回 0 且未超时；
- sequence、plate-solved、registered 以及 Siril 日志中的实际 stacked 数量均等于输入 panel 数；
- 线性 FITS 可读，JPEG 有合法文件头；
- 对不同指向的 panel，输出画布面积大于单幅 panel；
- 输入、脚本、日志和交付物都有 SHA-256。

任一失败都不能用视觉观感覆盖。

## 视觉门禁

在适合观察接缝的缩放和 100% 星点缩放下都查看预览：

1. `target_complete`
   - 用户要求的天体主要结构均在画布内。
   - 不能只凭 `OBJECT`、文件名或一个中心坐标判定。
2. `alignment_no_duplicate_stars`
   - 四条重叠边中的星点为单一中心；无双星（Double stars）、弧形拖影或局部错层。
   - 边缘星点若有轻微彗差必须保持径向一致对称，不得在重叠交界处出现多重分离星点。
3. `seams_and_background`
   - 重叠区没有明显矩形边、亮度阶跃、死黑接缝、噪声断层或颜色突变。
   - **排查隔离法则（Verify in Linear Master First）**：若在 `mosaic_preview.jpg` 中发现暗缝或跳阶，必须首先在 FITS 查看器中检查 `outputs/mosaic_linear.fit` 的真实数值和适度拉伸。激进的 `autostretch -linked -2.8` 极易将边缘千分之一 ADU 的微小过渡放大为视觉上的“严重接缝”；必须确认伪影是否真实存在于底层线性数据中。
   - 星云本身的真实渐变不应被误判为接缝并强行抹平。
4. `no_internal_black_gaps`
   - 外轮廓的无覆盖黑角可以保留供后续裁切；panel 网格内部不能有黑洞或漏块。
5. `source_structure_preserved`
   - 细丝、恒星和窄带颜色来自源 panel；无插值振铃、重复纹理或显示拉伸导致的高光大片截断。
   - 双窄带（如 OSC Duo-band）拼接时，注意各 panel 的 OIII / Ha 比例是否漂移，避免重叠区出现异常品红/青斑。

## 有界重试与故障诊断决策树

- **接缝轻微且配准正确**：在新 run 中试一个相邻 feather 值（常见 32、64、128）。
- **重叠区出现死黑暗缝或明显背景阶跃**：
  - 检查单 panel 是否在拼接前接受了高阶/密集 RBF 背景提取（过度拟合背景）。
  - 若已过度拟合：停止加大 feather；回退至仅做 1~2 阶多项式平面提取或未经背景扣除的线性原始 panel 重拼。
- **重叠区出现双星、放射拖影或错层（Registration Double Stars）**：
  - 属于几何配准失败，**严禁尝试通过增大 `feather` 掩盖双星**（feather 只会将错位星点虚化为毛刺重影）。
  - 排查步骤：
    1. 检查各 panel 头文件中的 `FOCALLEN`、`XPIXSZ` 是否存在微小漂移（Mixed Plate Scale）；
    2. 检查视场边缘光学场曲与差分几何畸变（Differential Distortion）。若边缘畸变严重，可尝试调整解算畸变阶数 `--distortion-order 2` 或 `--distortion-order 3` 对比残差；
    3. 检查是否有严重未加平场的周边像场或跟焦拉线引起的假星点解算偏离。
- **内部黑洞或 panel 数不足**：直接 `review_required`，检查输入覆盖和解算日志。
- **目标超出画布**：确认同时存在 `-framing=max` 与 `-maximize`，以及 scale 没有被误当裁切。

每次重试必须使用新目录。最多一个参数化重试；仍不满足时保留失败证据并停止。
