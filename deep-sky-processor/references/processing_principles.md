# 深空天文后期处理原理参考

本文档阐述深空图像后期处理各步骤的数学和物理原理，供处理决策时参考。

---

## 0. 流程顺序选择

### 两种标准后处理工作流

| 流程 | 顺序 | 代表 | 适用场景 |
|------|------|------|---------|
| **A** | 拉伸 → 星点分离 | AstroBackyard, 经典 PixInsight | AI 星点分离 (StarNet 在非线性数据上更准) |
| **B** | 星点分离 → 分别拉伸 | Seti Astro | 星云大胆拉伸不炸星, Stars-only 独立拉伸 |

### 本 Skill 选择：流程 B（线性星点分离 → 分别拉伸）

**原因**：
1. 线性阶段星点尚未膨胀，适合先分离并让星云、星点分别拉伸
2. **降噪前置到拉伸前**（关键改进）：避免「拉伸放大噪声→再降噪」的恶性循环
3. PNG/JPG 输入已非真正线性数据，拉伸因子需保守

### 关键改进：降噪从拉伸后移到拉伸前

旧管线：`DBE → Color → Stretch → Denoise → Star Separate`  
问题：拉伸是**非线性映射**，暗部噪声被指数级放大；降噪面对已被扭曲的噪声，效果差。

新管线：`DBE → Color → **Pre-Denoise** → Star Separate → Stretch`  
改进：在线性数据上降噪，噪声统计特性稳定；拉伸时数据已纯净。

---

## 1. 校准帧原理

### 暗场减法
暗场记录了传感器热噪点和固定模式噪声。在相同温度和曝光时间下，这些噪声是系统性的。
```
校准后亮场 = 亮场 - 主暗场
```
黑暗电流在每个像素上叠加，减法后移除系统噪声，只留下天空信号。

### 平场除法
平场记录了光学系统渐晕、灰尘阴影和像素灵敏度差异。
```
最终图像 = (亮场 - 暗场) / (归一化平场 - 偏置场)
```
除法校正了每个像素的「接收效率」。若某像素因灰尘只接收了正常光量的 90%，除以 0.9 即可恢复。

### 叠加信噪比
叠加 N 帧，信噪比提升 √N 倍。10 帧 → 3.2×，100 帧 → 10×。

---

## 2. 背景提取 (DBE) 原理

光害和月光在图像上产生空间缓变的亮度梯度。DBE 通过以下方式建模背景：

- **多项式拟合**：`bg(x,y) = Σ a_ij * x^i * y^j`，低阶多项式（degree ≤ 3）适合平滑光害梯度
- **RBF 插值**：径向基函数（如 thin-plate spline）对不规则采样点做平滑插值
- **大尺度中值滤波**：滤除小尺度天体，保留大尺度背景

核心约束：背景变化必须是空间**缓变**的。背景的空间频率远低于天体。

---

## 3. 颜色校准原理

### 背景中性化
采样纯天空背景区域，调整 RGB 各通道增益使背景达到 R=G=B（中性灰）。

### 测光法校准 (PCC)
将图像中的恒星颜色与已知星表（如 Gaia, APASS）中该恒星的测光数据比对，求解出 RGB 三通道的**色彩转换矩阵**。这是最精确的颜色校准方法。

### 白平衡（灰度世界假设）
在深空图像中，大量暗弱恒星的平均颜色接近白色。通过使图像整体平均色趋向中性灰来近似白平衡。

---

## 4. 直方图拉伸原理

### Arcsinh 拉伸
```
stretched = arcsinh(x × factor) / arcsinh(factor)
```
反双曲正弦函数在原点附近接近线性（保护暗部层次），远离原点时趋近对数（压缩高光）。factor 控制拐点位置。

### MTF (Midtone Transfer Function)
```
MTF(x) = (m-1)x / ((2m-1)x - m)
```
单参数 m 控制中间调。当 m=0.5 时压缩亮部、拉伸暗部，m<0.3 时激进提亮暗部。

### 蒙版拉伸 (MaskedStretch)
先生成亮星蒙版，在蒙版保护下对暗部做激进的 arcsinh 拉伸。亮星区域不变，暗部大幅提亮。

---

## 5. 降噪原理

### 双边滤波
权重 = 空间邻近度 × 像素值相似度。在星点边缘处相邻像素值差异大 → 权重低 → 边缘保留。

### Non-Local Means
在整张图中搜索相似图像块，加权平均。利用星场和星云的纹理自相似性。

### 亮度/色彩分离
在 YCbCr 或 Lab 色彩空间中，对色彩通道施加 2-3 倍强度的降噪（人眼对色彩噪点更敏感）。

---

## 6. 反卷积与锐化原理

### 图像退化模型
```
观测图像 = 真实图像 ⊗ PSF + 噪声
```
PSF (Point Spread Function) 由大气视宁度、望远镜衍射、对焦误差共同决定。

### 维纳滤波反卷积
```
F_corrected = F_blurred × conj(H) / (|H|² + K)
```
在频域中逆向求解。K 是正则化参数，抑制噪声放大。

### 反锐化掩膜 (USM)
```
sharpened = image + amount × (image - blur(image))
```
(image - blur) 提取高频边缘信息，叠加回原图。

---

## 7. 星点处理原理

### 形态学星点检测
White Top-hat = 原图 - 开运算(原图)。开运算（先腐蚀后膨胀）移除小于结构元素的亮结构 → 星点。

### 星点修复
用周围像素插值填充星点区域。中值滤波天然抑制孤立亮点，适合移除星点。

### 缩星
形态学腐蚀缩小星点区域。多次轻度腐蚀比单次强腐蚀更自然。

---

## 8. 细节增强原理

### HDR 多尺度压缩
将图像分解为多个空间频率层，对高频层施加强度压缩，保留低频层。明亮核心 → 压缩，暗弱外围 → 保留。

### CLAHE
在局部窗口内做对比度受限的直方图均衡化。clip_limit 防止噪声被放大。

---

## 处理约束 (Core Constraint)

所有处理必须**基于原图真实数据**：
- 不凭空添加不存在的结构
- 不捏造未记录的色彩
- 拉伸和锐化需在合理物理范围内
- 降噪不可抹去真实天体细节

---

## 9. AI 决策框架 (AI Decision Framework)

### 9.1 AI 在每个处理步骤中的判断逻辑

#### DBE 步骤 — AI 决策树 (方法+阶数)

**第一步：选择方法** — 不总是 polynomial。根据梯度模式选择：

```
gradient_pattern == 'none'              → skip DBE 或 degree=1
gradient_pattern == 'linear'            → method=polynomial, degree=2
  (线性光害梯度 — polynomial 最精确建模，计算快)
gradient_pattern == 'vignetting_dominant' → method=polynomial, degree=3
  (渐晕主导 — 高阶 polynomial 可同时建模径向衰减和线性梯度)
gradient_pattern == 'complex'           → method=rbf
  (复杂非线性 — RBF thin-plate spline 适合不规则光害模式)
gradient_pattern == 'irregular'         → R²<0.5 且 severe → rbf; 否则 median
  (噪声主导的不规则背景 — median filter 无需模型假设)
```

**三种方法对比**:

| 方法 | 适合场景 | 优势 | 劣势 |
|------|---------|------|------|
| **polynomial** | 线性/均匀光害梯度，渐晕 | 精确建模平滑梯度，计算快 | 无法处理非线性复杂梯度 |
| **rbf** | 多光源、不规则光害、复杂背景 | 灵活适应任意空间模式 | 计算慢，容易过拟合 |
| **median** | 噪声主导、无清晰梯度结构 | 无需模型假设，稳健 | 无法精确建模方向性梯度 |

**第二步：选择阶数** (仅 polynomial):

```
degree = 1: 无显著梯度，只做微调
degree = 2: 线性光害梯度 (大多数城市光害)
degree = 3: 渐晕 + 线性梯度，或强梯度
```

**DBE 后检查**:
  ✓ 四角平均亮度差异 < 5%     → 通过
  ✗ 角落明显暗于中心         → 过减！降低 degree 或切换方法
  ✗ 出现黑色"坑"             → 背景采样包含天体 → 切换为 median
  ✗ RBF 产生条纹/wavy pattern → smoothing 参数太小 → 增大 smoothing
  ✗ median 残留方向性梯度    → 线性光害应切回 polynomial

#### 降噪步骤 — AI 决策树

```
noise_level == 'very_low'  → lum=0.005, chroma=0.015
noise_level == 'low'       → lum=0.012, chroma=0.035
noise_level == 'moderate'  → lum=0.025, chroma=0.07
noise_level == 'high'      → lum=0.04,  chroma=0.12
noise_level == 'very_high' → lum=0.06,  chroma=0.18

降噪后检查:
  ✓ 暗部干净但星点边缘清晰      → 通过
  ✗ 星云纹理变得模糊/塑料感     → 降噪过强！降低 lum 强度
  ✗ 暗部仍有明显颗粒            → 可适当增强，但提醒用户"原始数据噪声偏高"
```

#### 拉伸步骤 — AI 决策树

```
stretch_factor = 30 × (1.5 if is_linear else 1.0) × darkness_multiplier

darkness_multiplier:
  extreme_dark → 2.7   (median < 0.001)
  very_dark    → 2.0   (median < 0.01)
  dark         → 1.5   (median < 0.05)
  moderate     → 0.8   (median < 0.15)
  bright       → 0.4   (median ≥ 0.15)

拉伸后检查:
  ✓ 背景中位数落在 0.05-0.15  → 通过
  ✓ 亮星核心未溢出 (>0.98)    → 通过
  ✗ 整体偏暗 (<0.03)          → 拉伸不足，增大 factor 30%
  ✗ 亮部过曝/全白             → 拉伸过度，降低 factor 30% 或改用 masked_stretch
  ✗ 背景发灰 (中位数>0.2)     → 调整 black_point
```

#### 星点处理 — AI 决策树

```
star_density == 'sparse'      → threshold=0.90, reduction=0.15
star_density == 'moderate'    → threshold=0.85, reduction=0.30
star_density == 'dense'       → threshold=0.82, reduction=0.35
star_density == 'very_dense'  → threshold=0.78, reduction=0.40

去星后检查:
  ✓ 星云结构完整，无明显挖空      → 通过
  ✓ 残留星点 < 5%                 → 通过
  ✗ 星云细丝被误判为星点移除     → 提高 threshold
  ✗ 大面积残留星点               → 降低 threshold

合成后检查:
  ✓ 星点色彩自然 (蓝白→橙黄)     → 通过
  ✓ 星点与星云过渡自然           → 通过
  ✗ 星点有"贴上去"的感觉         → 降低 star_combine_strength
```

#### 增强步骤 — AI 决策树

```
dynamic_range_ratio > 10  → hdr_strength=0.6
dynamic_range_ratio > 5   → hdr_strength=0.4
dynamic_range_ratio > 2   → hdr_strength=0.25
dynamic_range_ratio ≤ 2   → hdr_strength=0.15

增强后检查:
  ✓ 星云暗部和亮部细节同时可见    → 通过
  ✗ 出现明暗边缘光晕 (halo)       → HDR 过强，降低 strength
  ✗ 暗部出现噪点放大              → 需后续降噪补救
```

#### 锐化步骤 — AI 决策树

```
sharpness_level == 'very_blurry' → amount=2.5
sharpness_level == 'blurry'      → amount=1.8
sharpness_level == 'moderate'    → amount=1.2
sharpness_level == 'sharp'       → amount=0.6
sharpness_level == 'very_sharp'  → amount=0.3

锐化后检查:
  ✓ 星云纤维纹理清晰，无 ring    → 通过
  ✗ 星点出现黑环/白环 (ringing)  → 降低 amount
  ✗ 暗区出现噪点放大              → 锐化前先降噪，或降低 amount
```

#### 色彩步骤 — AI 决策树

```
color_health == 'good'           → sat=1.2, skip background_neutralize
color_health == 'mild_cast'      → sat=1.3, background_neutralize + white_balance
color_health == 'moderate_cast'  → sat=1.5, background_neutralize + white_balance + SCNR
color_health == 'severe_cast'    → sat=1.4, 全流程校准但饱和保守 (先矫正再增强)

色彩检查:
  ✓ Hα区域呈深红(非品红/橙红)          → 通过
  ✓ OIII区域呈青蓝(非电蓝/紫蓝)        → 通过
  ✓ 背景区域 R≈G≈B (中性灰黑)          → 通过
  ✗ 绿色通道偏高                          → 运行 SCNR
  ✗ 整图偏暖/偏冷                        → 检查背景中性化是否生效
```

### 9.2 "真实美化" 的尺度把控

AI 必须在 "让图像更好看" 和 "保持真实" 之间找到平衡。以下是具体尺度：

| 操作 | 合理范围 (真实美化) | 越界行为 (虚假美化) |
|------|-------------------|-------------------|
| **拉伸** | 揭示暗部记录到的真实信号 | 把噪声当信号拉伸，无中生有 |
| **降噪** | 抑制统计噪声，保护星云结构 | 磨平星云纹理，制造塑料感 |
| **锐化** | 恢复被大气模糊的真实细节 | 制造不存在的"伪细节"，振铃伪影 |
| **饱和** | 增强已记录的颜色对比度 | 改变色相，Hα 变品红，OIII 变电蓝 |
| **HDR** | 压缩极端亮度差让结构可见 | 制造光晕，改变结构边界 |
| **缩星** | 减小星点视觉权重突出星云 | 完全消除星点，或制造不规则形状 |
| **增强** | 让已有纹理更清晰 | 凭空添加纤维结构 (AI 纹理) |

**核心原则**：处理是 "揭示" (reveal) 而非 "创造" (create)。好的处理让观众惊叹于 "原来望远镜真的拍到了这些"，而非 "AI 真会画"。

### 9.3 处理失败模式及应对

| 失败表现 | 根因 | 应对 |
|---------|------|------|
| 背景出现条纹/banding | DBE 阶数过高，过拟合 | 降低 degree，或切换为中值滤波 |
| 星点出现黑环 | 锐化 amount 过大 | 降低 amount 50%，改用多尺度锐化 |
| 星云像"塑料" | 降噪过度 | 降低降噪强度，优先降色彩噪声 |
| 整图偏灰无对比 | 拉伸不足 + 无曲线调整 | 增大 stretch_factor，加 S 曲线 |
| 核心过曝成白色 | 拉伸因子过大 | 降低 30%，或用 masked_stretch |
| 星云边缘出现亮边 | HDR 强度过高 | 降低 hdr_strength |
| 色彩不自然 (品红/电蓝) | 饱和度过度增强 | 降低 sat factor，检查背景中性化
