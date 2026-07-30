# Deep Sky AI 图像生成提示词工程手册

> 图像提示词工程师 × deep-sky-processor 协同设计
> 将专业深空后期处理知识转化为 AI 图像生成的精确语言

---

## 目录

1. [核心哲学：AI 生成与真实后期的协同](#1-核心哲学ai-生成与真实后期的协同)
2. [处理阶段 → 视觉特征 → 提示词映射](#2-处理阶段--视觉特征--提示词映射)
3. [AI 平台适配策略](#3-ai-平台适配策略)
4. [目标天体提示词库](#4-目标天体提示词库)
5. [风格预设系统](#5-风格预设系统)
6. [协同工作流实战](#6-协同工作流实战)
7. [负面提示词与质量守卫](#7-负面提示词与质量守卫)

---

## 1. 核心哲学：AI 生成与真实后期的协同

### 两条路径，一个目标

| 维度 | AI 图像生成 | deep-sky-processor | 协同价值 |
|------|------------|-------------------|---------|
| **数据来源** | 文本 → 合成像素 | 真实 FITS/PNG → 处理 | AI 生成概念参考，真实数据做科学记录 |
| **控制精度** | 语义级（"星云红蓝交织"） | 像素级（arcsinh 拉伸因子 3.2） | 提示词定义美学方向，processor 实现精确控制 |
| **可重复性** | 每张独特 | 确定性处理 | AI 探索风格可能性，processor 固化最佳参数 |
| **物理真实性** | 近似，可能幻觉 | 基于实测光子计数 | processor 验证 AI 参考图的物理合理性 |

### 核心原则

```
AI 生成的角色：美学探索器、构图参考、风格灵感
deep-sky-processor 的角色：科学保真处理器、真数据增强引擎
协同：AI 生成图 → 定义期望的视觉效果 → processor 调参对标 → 真实数据输出
```

---

## 2. 处理阶段 → 视觉特征 → 提示词映射

这是本手册的核心创新：将 deep-sky-processor 每个处理阶段的视觉效果目标，精确转化为 AI 可理解的摄影描述语言。

### 2.1 DBE（背景梯度去除）→ 纯净深邃的太空背景

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| 去除光害梯度 | 均匀黑色/深灰背景 | `pitch black space background` `uniform dark field` |
| 背景空间缓变去除 | 无色彩偏色 | `neutral dark sky background, no light pollution gradient` |
| 天空背景中性化 | R=G=B 的中性暗灰 | `color-neutral deep space background` |

**提示词片段模板**：
```
Pristine deep space background with perfectly uniform dark field,
no vignetting, no light pollution gradients, neutral gray-black sky tone
```

### 2.2 颜色校准 → 科学准确的色彩再现

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| 背景中性化 | 暗区无偏色 | `neutral black background, no color cast in shadows` |
| 白平衡矫正 | 恒星呈自然白/蓝白/橙黄 | `stars with natural spectral colors: blue-white hot stars, golden cool stars` |
| SCNR 绿噪去除 | 无人工绿色 | `no green noise, no artificial green tint` |
| 色彩饱和控制 | 星云色彩浓郁但不溢出 | `rich but natural nebula colors, saturated without clipping` |

**提示词片段模板**：
```
Accurate astronomical color calibration, hydrogen-alpha regions glowing deep red,
oxygen-III regions electric teal-blue, reflection nebulae cool blue,
stars with natural spectral temperatures from blue-white to warm gold
```

### 2.3 初步降噪（线性阶段）→ 丝滑的暗部

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| 双边滤波保边 | 星点边缘清晰，暗区干净 | `crisp star edges against smooth dark background` |
| 亮度/色彩分离 | 色彩噪点优先抑制 | `clean chroma noise, smooth luminance gradients` |
| 线性数据降噪 | 不扭曲信号统计 | `pristine signal quality, no denoising artifacts` |

**提示词片段模板**：
```
Exceptionally low noise, smooth dark regions free of grain,
stars perfectly crisp at edges, clean transition between nebula and space
```

### 2.4 直方图拉伸 → 暗弱细节的揭示

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| Arcsinh 拉伸 | 暗部层次保留，高光压缩 | `extended dynamic range revealing faint outer nebulosity, preserved core details` |
| 百分位自动拉伸 | 自适应数据范围 | `perfectly balanced exposure across entire image` |
| 蒙版拉伸 | 亮星不炸，暗部提亮 | `bright stars without blooming, faint details visible` |

**提示词片段模板**：
```
Revealing extremely faint outer nebulosity extending far from the core,
delicate structures visible in what would be dark regions,
bright core well-controlled without overexposure
```

### 2.5 星点分离与处理 → 精致星场

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| 形态学检测 | 星点vs星云正确分离 | `distinct separation between stars and nebula structures` |
| 缩星 | 星点精细不喧宾夺主 | `fine pinpoint stars, subtle stellar presence` |
| SCNR 去紫 | 无形色异常 | `stars without purple fringing, natural stellar colors` |
| 星点合成 | 自然融合 | `stars naturally integrated into nebula, no hard compositing edges` |

**提示词片段模板**：
```
Delicate pinpoint stars of varying magnitudes across the field,
natural stellar density without overcrowding,
stars appearing naturally embedded in nebulosity rather than painted on top
```

### 2.6 细节增强 → 星云结构与纹理

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| HDR 多尺度压缩 | 明亮核心细节+暗弱外围 | `intricate core details visible alongside faint outer tendrils` |
| CLAHE | 局部对比度提升 | `local contrast revealing delicate filaments and wisps` |
| S 曲线 | 中间调冲力 | `punchy midtone contrast in nebula structures` |

**提示词片段模板**：
```
Intricate filamentary structures within the nebula,
delicate tendrils of gas and dust creating three-dimensional depth,
pillars and knots of dense material silhouetted against glowing background
```

### 2.7 反卷积锐化 → 刀锋般清晰

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| 维纳反卷积 | 去除大气模糊 | `tack-sharp resolution, space-telescope clarity` |
| USM | 高频边缘增强 | `crisp structural edges, fine detail definition` |
| 多尺度锐化 | 分层控制，无振铃 | `natural sharpness without halos or ringing artifacts` |

**提示词片段模板**：
```
Telescope-grade optical sharpness with diffraction-limited resolution,
every filament and dust lane rendered in exquisite detail,
no oversharpening halos, natural clarity as if viewed through perfect optics
```

### 2.8 最终降噪 → 干净成片

| processor 目标 | 视觉特征 | 提示词映射 |
|---------------|---------|-----------|
| 最终全局降噪 | 全图统一干净 | `pristine final image quality, noise-free across entire field` |
| 边缘保护 | 细节无涂抹 | `no smoothing of fine structures, preserved micro-detail` |

**提示词片段模板**：
```
Gallery-quality final image, noise-free yet retaining all fine structural detail,
ready for large-format printing at exhibition standard
```

---

## 3. AI 平台适配策略

### 3.1 平台能力对比

| 能力维度 | Midjourney | DALL-E 3 | Flux | Stable Diffusion |
|---------|-----------|----------|------|-----------------|
| 天文细节 | ★★★★★ | ★★★☆☆ | ★★★★☆ | ★★★☆☆ |
| 颜色准确性 | ★★★★☆ | ★★★★☆ | ★★★★☆ | ★★★☆☆ |
| 构图自由度 | ★★★☆☆ | ★★★★★ | ★★★★☆ | ★★★★★ |
| 星点质感 | ★★★★★ | ★★★☆☆ | ★★★★☆ | ★★★☆☆ |
| 暗部层次 | ★★★★☆ | ★★★☆☆ | ★★★★☆ | ★★★☆☆ |
| 推荐用途 | 旗舰级深空渲染 | 概念探索/构图试验 | 摄影级写实 | LoRA 定制训练 |

### 3.2 Midjourney 深度适配（推荐主力）

Midjourney 在天文摄影细节和星点质感上表现最佳。以下是针对 MJ 优化的提示词结构：

#### MJ 标准结构

```
[摄影类型] of [天体名称], [构图视角]
[核心视觉特征1], [核心视觉特征2], [核心视觉特征3]
[色彩描述 - 发射线映射]
[技术规格 - 焦段/光圈/传感器]
[后期处理风格参考]
[质量词]
--ar [比例] --v 6.1 --style raw --s [风格化]
```

#### 实战示例：M42 猎户座大星云

```
Astrophotography of the Great Orion Nebula Messier 42, wide field view
revealing the complete sword of Orion region

Intricate HDR detail from the bright Trapezium core to the faint outer
ionization fronts, delicate tendrils of hydrogen gas forming the iconic
bat-wing structure, dark dust lanes silhouetted against glowing emission

Spectral color mapping: deep crimson hydrogen-alpha regions blending into
pink H-beta emission, electric teal oxygen-III core around the Trapezium
cluster, dark brown molecular cloud complex, bright blue reflection
nebulosity in the Running Man nebula above

Shot on a cooled monochrome astronomy camera through narrowband filters,
equivalent to 400mm f/4 telescope, 10-hour total integration,
pristine sub-arcsecond seeing conditions at a dark sky observatory

Post-processed in PixInsight with masked stretch, HDR multiscale transform,
and natural color calibration --no border, --no text, --no watermark
--ar 16:9 --v 6.1 --style raw --s 250
```

### 3.3 DALL-E 3 适配

DALL-E 对自然语言理解最强，但对天文细节渲染精度不及 MJ。适合概念探索和快速原型。

```
A professional astronomical photograph of the Orion Nebula (M42),
captured through a high-end telescope in narrowband wavelengths:

The nebula displays the classic "bat wing" structure with vivid crimson
hydrogen emission and teal oxygen regions near the bright Trapezium core.
Dark brown dust lanes cut through the glowing gas clouds, while the blue
Running Man reflection nebula sits above.

Technical quality: deep space astrophotography, pristine dark sky conditions,
tack-sharp star resolution, extended dynamic range revealing both bright
core and faint outer nebulosity, scientific color accuracy, no light
pollution, professional observatory quality
```

### 3.4 Flux 适配

Flux 在写实感上极强，适合生成"看起来像真实望远镜拍摄"的图像。

```
Professional deep-sky astrophotograph: Orion Nebula M42, shot through
a Takahashi FSQ-106EDX4 530mm f/5 refractor telescope with ZWO ASI2600MM
cooled camera, narrowband SHO palette mapped to RGB.

Brilliant crimson hydrogen-alpha emission forming the iconic wings,
cyan oxygen-III core, dark brown Bok globules and dust lanes.
Perfect polar alignment, autoguided tracking, 120 x 300s subs integrated.
PixInsight post-processing: DBE, SpectrophotometricColorCalibration,
BlurXTerminator, NoiseXTerminator, GeneralizedHyperbolicStretch,
StarXTerminator separated processing, CurvesTransformation.

Ultra-high resolution, diffraction-limited optics, pristine dark site,
no atmospheric distortion, flawless stars, gallery-grade print quality
```

### 3.5 Stable Diffusion 适配

SD 需要更结构化的提示词，配合 LoRA 模型效果更佳。

```
(masterpiece, best quality:1.4), (astrophotography:1.3),
(deep sky object:1.3), Orion Nebula, M42,

(intricate nebula details:1.3), (hydrogen emission:1.2), crimson and teal colors,
dark dust lanes, (pinpoint stars:1.2), (telescopic optics:1.2),

no light pollution, (uniform dark background:1.1), professional observatory,
(PixInsight post-processing:1.1), (narrowband imaging:1.1),
high dynamic range, sharp focus, 8k resolution

Negative: illustration, cartoon, CGI, overexposed, lens flare, atmosphere,
clouds, trees, landscape, foreground, person, text, watermark
```

---

## 4. 目标天体提示词库

### 4.1 发射星云 (Emission Nebulae)

#### NGC 7000 — 北美星云

```
Astrophotography of the North America Nebula NGC 7000 in Cygnus,
mosaic view revealing the full continent-shaped hydrogen cloud

Deep crimson hydrogen-alpha emission forming the recognizable Gulf of Mexico
coastline, the bright Cygnus Wall ridge with intricate ionization front details,
delicate tendrils of gas extending into the dark surrounding space,
the Pelican Nebula IC 5070 visible at the right edge

Narrowband color palette: hydrogen-alpha mapped to gold-orange,
oxygen-III mapped to cyan-blue, sulfur-II mapped to deep red

400mm focal length wide-field astrograph, cooled monochrome sensor,
total integration 20+ hours from Bortle 2 dark sky site,
processed with meticulous attention to faint outer nebulosity

--ar 3:2 --v 6.1 --style raw --s 300
```

#### NGC 2237 — 玫瑰星云

```
Astrophotography of the Rosette Nebula NGC 2237 in Monoceros,
face-on view of the spectacular circular emission region

Perfect symmetrical rose-like structure with the central NGC 2244 open
star cluster carving out the hollow core, concentric shells of ionized
hydrogen in crimson and pink, dark Bok globules forming elephant trunk
structures pointing inward, delicate lace-like filaments at the outer edges

Rich narrowband color mapping emphasizing the three-dimensional structure,
oxygen-III teal accents tracing the shock fronts and wind-blown bubbles

800mm focal length, pristine seeing, 15+ hour narrowband integration,
exceptional depth revealing the extended outer halo

--ar 1:1 --v 6.1 --style raw --s 280
```

#### IC 434 — 马头星云

```
Astrophotography of the Horsehead Nebula Barnard 33 against IC 434 in Orion

The iconic dark equine silhouette formed by thick molecular dust,
backlit by the glowing crimson hydrogen-alpha emission of IC 434 behind it,
delicate wisps of gas curling around the horsehead's edges,
the bright star Alnitak with its distinctive diffraction spikes at left,
reflection nebulosity NGC 2023 glowing blue at the lower edge,
the Flame Nebula NGC 2024 burning orange in the corner

Exceptional contrast between the pitch-dark dust and the illuminated background,
revealing subtle internal structure within the horsehead itself

1500mm focal length at f/7, excellent seeing, captured from a high-altitude
observatory above the inversion layer, 12-hour LRGBHa integration

--ar 4:3 --v 6.1 --style raw --s 220
```

### 4.2 反射星云 (Reflection Nebulae)

#### M45 — 昴星团

```
Astrophotography of the Pleiades star cluster M45 in Taurus,
wide field capturing the complete reflection nebulosity

Brilliant blue-white hot B-type stars of the Seven Sisters dominating the field,
the intricate blue reflection nebulosity NGC 1435 (Merope Nebula) wrapping
around the brightest stars in sweeping arcs, delicate filamentary structures
in the dust illuminated by starlight, the diffraction spikes creating a
natural asterism pattern, surrounding darker regions emphasizing the
ethereal glow of the nebula

Rich star colors from deep blue-white to subtle golden,
bright stars perfectly controlled showing Airy disk structure,
faint background galaxies visible through the translucent dust

400mm f/2.8 wide-field astrograph, LRGB imaging from a dark sky site,
meticulous processing to reveal the full extent of the Merope Nebula
without saturating the bright cluster stars

--ar 16:9 --v 6.1 --style raw --s 240
```

#### 鸢尾花星云 NGC 7023

```
Astrophotography of the Iris Nebula NGC 7023 in Cepheus,
detailed view of the bright reflection nebula

The brilliant central star HD 200775 illuminating the surrounding dust cloud
in ethereal sapphire blue, delicate petals of reflected light extending
in a flower-like pattern, the dark obscuring dust cloud LDN 1167 surrounding
the nebula creating dramatic contrast, subtle pink emission at the interface
between reflection and dark nebulae, intricate dust structures visible in
the transition zones

Gentle gradients from the bright core to the dark surrounding molecular cloud,
the three-dimensional depth of the dust sculpted by radiation pressure

1200mm focal length at f/5, excellent transparency,
LRGB with extended luminance for depth

--ar 3:2 --v 6.1 --style raw --s 260
```

### 4.3 星系 (Galaxies)

#### M31 — 仙女座大星系

```
Astrophotography of the Andromeda Galaxy Messier 31,
mosaic capturing the full galactic disk and satellite galaxies

The magnificent spiral structure spanning the field, bright galactic core
dominating the center with yellow-orange old stellar population,
dust lanes tracing the inner spiral arms in brown-black silhouettes,
the outer disk extending with blue spiral arms rich in young hot stars,
star-forming HII regions appearing as pink knots throughout the arms,
the two satellite galaxies M32 (compact elliptical at left) and M110
(elongated elliptical at lower right) completing the composition

Exceptional depth reaching magnitude 24+ revealing globular clusters
and stellar streams in the outer halo, three-dimensional dust structure,
foreground Milky Way stars scattered across the field providing depth

400mm f/3 wide-field astrograph, LRGBHa integration 20+ hours,
captured under exceptional transparency at a remote dark sky observatory

--ar 3:2 --v 6.1 --style raw --s 200
```

#### M51 — 漩涡星系

```
Astrophotography of the Whirlpool Galaxy Messier 51 in Canes Venatici,
showing the grand-design spiral interacting with NGC 5195

Stunning face-on spiral structure with clearly defined arms wrapping around
the bright yellow core, pink HII regions dotting the spiral arms like jewels,
dark dust lanes tracing the inner edge of each arm, the gravitational
interaction with the smaller companion NGC 5195 creating a dramatic
tidal bridge of stars and gas, the companion's distorted shape showing
the power of galactic collision

Blue spiral arms rich in young stellar populations contrasting with
the golden core of older stars, background galaxies visible through
the deep integration

2000mm focal length at f/8, excellent seeing with sub-arcsecond resolution,
LRGBHa with 15+ hours from a premier dark sky observatory

--ar 4:3 --v 6.1 --style raw --s 230
```

#### M101 — 风车星系

```
Astrophotography of the Pinwheel Galaxy Messier 101 in Ursa Major,
face-on grand design spiral galaxy

Magnificent symmetric spiral structure with multiple arm segments,
enormous HII regions appearing as bright pink emission knots tracing
the spiral arms larger than any in our Milky Way, the blue spiral arms
rich with newly formed hot stars extending far from the yellow core,
the galaxy's asymmetric outer disk showing the effects of past
gravitational interactions, dozens of background galaxies visible
through the face-on disk

Massive dynamic range from the compact bright core to the extremely
faint outer spiral extensions, exceptional star formation detail

1000mm focal length at f/5, LRGBHa with extremely deep integration,
processing revealing the full extent of the low surface brightness disk

--ar 16:9 --v 6.1 --style raw --s 210
```

### 4.4 星团 (Star Clusters)

#### M13 — 武仙座球状星团

```
Astrophotography of the Great Hercules Globular Cluster Messier 13,
resolving the dense stellar core to individual stars

Hundreds of thousands of ancient golden stars packed into a spherical swarm,
the bright dense core gradually resolving into individual pinpoint stars,
subtle color variations from blue horizontal branch stars to red giants,
a faint propeller-like dark lane structure visible near the core,
the surrounding field dotted with distant background galaxies

Perfect stellar resolution to the cluster center, no star blending,
the three-dimensional spherical nature clearly visible through
photometric depth

2000mm focal length at f/8, exceptional seeing conditions,
LRGB imaging resolving stars to magnitude 22+

--ar 1:1 --v 6.1 --style raw --s 190
```

### 4.5 行星状星云 (Planetary Nebulae)

#### M57 — 环状星云

```
Astrophotography of the Ring Nebula Messier 57 in Lyra,
high-resolution view of the iconic planetary nebula

The perfect smoke-ring structure of ionized gas expanding from the central
dying white dwarf star, the ring itself showing intricate braided filaments
and knots of denser material, the inner cavity glowing with soft teal
oxygen-III emission, the outer shell extending with faint red hydrogen-alpha
lobes, the central white dwarf visible as a tiny blue point

The three-dimensional torus structure clearly apparent through the gas
density variations, nearby galaxy IC 1296 visible through the translucent
outer shell

2500mm focal length at f/10, exceptional seeing at high altitude,
narrowband HaOIII with RGB stars, Hubble-palette inspired natural color

--ar 4:3 --v 6.1 --style raw --s 200
```

---

## 5. 风格预设系统

### 5.1 自然纪实风格 (Documentary Natural)

对应 deep-sky-processor 的 **默认 medium 强度** 处理。强调真实感与科学性。

```
Style directive to append to base prompt:

Style: Documentary astrophotography, natural color rendering faithful
to visual observation through a large telescope, subtle processing
respecting the data, no exaggerated saturation, true to the photon
count, scientific accuracy prioritized over dramatic effect, the feeling
of looking through a research-grade telescope eyepiece at a dark sky site
```

### 5.2 极致震撼风格 (Premium Impact)

对应 deep-sky-processor 的 **strong 强度** 处理。最大化视觉冲击力，但仍基于物理可能。

```
Style directive to append to base prompt:

Style: Premium exhibition-grade astrophotography, maximum visual impact
while maintaining physical plausibility, vivid narrowband color palette
emphasizing emission line contrast, striking dynamic range from deep
shadows to bright highlights, dramatic three-dimensional depth through
light and shadow, the definitive image of this celestial object,
APOD (Astronomy Picture of the Day) quality, breathtaking cosmic vista
that inspires awe while remaining scientifically grounded

Post-processing references: HDRMultiscaleTransform, LocalHistogramEqualization,
CurvesTransformation with saturation enhancement, DarkStructureEnhance
```

### 5.3 科学展示风格 (Scientific Presentation)

强调最大信息量和结构清晰度。

```
Style directive to append to base prompt:

Style: Scientific presentation astrophotography, maximum structural clarity
and information density, all morphological features clearly separated and
identifiable, neutral color balance optimized for structure discrimination
rather than aesthetic appeal, the image serves as a research-quality
reference showing every filament, knot, and shell at the limits of
detection, reminiscent of professional observatory survey images from
the Hubble Space Telescope or ESO VLT, monochromatic-inspired contrast
with subtle color coding of physical conditions
```

### 5.4 艺术渲染风格 (Artistic Interpretation)

在真实结构基础上允许艺术表现。

```
Style directive to append to base prompt:

Style: Fine art astrophotography, an artistic interpretation of cosmic beauty
grounded in real astronomical structures, painterly rendering of nebula
textures reminiscent of the Hubble palette but pushed toward art,
dramatic chiaroscuro lighting across the cosmic landscape,
the image feels like a masterpiece that could hang in a gallery,
evoking the romantic tradition of astronomical illustration while
using actual nebula morphology as the foundation,
inspired by the aesthetic of Ted Argo and Robert Gendler's
composite astrophotography
```

---

## 6. 协同工作流实战

### 工作流 A：AI 先行探索 → processor 对标处理

```
步骤 1 ─── 用 AI 生成期望的视觉效果
         │  使用本手册提示词库生成目标天体的参考图像
         │  输出：视觉参考图
         ↓
步骤 2 ─── 分析参考图的视觉特征
         │  - 暗部层次：背景黑到什么程度？
         │  - 星云亮度：核心/外围的对比度如何？
         │  - 色彩饱和度：Hα/OIII 的强度比例？
         │  - 星点大小：星点与星云的视觉权重比？
         │  - 细节锐度：纤维结构的刻画程度？
         ↓
步骤 3 ─── 映射到 processor 参数
         │  视觉特征 → 参数调整：
         │  - 背景黑度 → DBE degree 参数
         │  - 星云对比度 → stretch factor
         │  - 色彩饱和度 → color_tools saturation 强度
         │  - 星点大小 → star_tools reduction factor
         │  - 细节锐度 → sharpen amount
         ↓
步骤 4 ─── deep-sky-processor 处理真实 FITS 数据
         │  python pipeline.py M42.fit output.jpg --strength strong
         │  或在各独立步骤中微调参数对标参考图
         ↓
步骤 5 ─── A/B 对比迭代
         │  真实处理结果 vs AI 参考图
         │  → 调整参数 → 重新处理 → 直到满意
         │  → 记录最终参数作为该目标的「配方」
```

### 工作流 B：processor 出品 → AI 风格迁移探索

```
步骤 1 ─── deep-sky-processor 处理真实数据
         │  产出一张高质量的基础天文照片
         ↓
步骤 2 ─── 用 AI 对该照片做「风格变体」生成
         │  使用 img2img 或 reference image 功能
         │  应用不同的风格预设（自然/震撼/科学/艺术）
         │  探索同一数据的多种美学可能
         ↓
步骤 3 ─── 挑选最佳风格方向
         │  记录触发该风格的提示词和参数
         ↓
步骤 4 ─── 反馈到 processor 参数
         │  将风格方向转化为 processor 可执行的参数
         │  在真实数据上复现该风格
```

### 工作流 C：AI 补充真实数据无法捕捉的细节

```
场景：你用 SeeStar S30 Pro 拍摄了 M42，但由于口径和曝光限制，
      某些极暗的外围结构没有记录到

步骤 1 ─── processor 处理你的真实数据到最佳状态
步骤 2 ─── 用 AI 生成一张包含所有已知结构的参考图
步骤 3 ─── 用参考图指导你的「二次拍摄计划」：
         - 需要多少张 sub？什么曝光时间？
         - 哪些区域需要额外 focus？
         - 是否有必要拍摄 OIII 或 SII 通道？
步骤 4 ─── 补充拍摄 → 叠加到已有数据 → 逼近参考图质量
```

---

## 7. 负面提示词与质量守卫

### 7.1 通用负面提示词（适配所有平台）

```
通用负面语义清单（按平台语法自行转换）：
- illustration, cartoon, painting, drawing, digital art
- CGI, 3D render, artificial, computer-generated look
- clouds, atmosphere, sky, landscape, trees, ground, horizon
- foreground, person, human, figure, silhouette, tent, tripod
- text, label, watermark, signature, logo, copyright
- overexposed, blown highlights, clipped shadows
- lens flare, ghosting, internal reflection
- star trails, motion blur, tracking error
- light pollution, skyglow, gradient, vignetting
- artificial colors, neon, rainbow, fantasy colors
- low resolution, pixelated, blurry, out of focus
- noise, grain, compression artifacts, JPEG artifacts
- frame, border, vignette, lens distortion
```

### 7.2 平台专属语法

| 平台 | 负面语法 | 示例 |
|------|---------|------|
| **Midjourney** | `--no 关键词,关键词` | `--no illustration, CGI, text, landscape, clouds, atmosphere` |
| **DALL-E 3** | 自然语言描述排除 | "There should be no landscape, no ground, no clouds, no text" |
| **Flux** | 自然语言 + 强调 | "Absolutely no landscape elements, clouds, or terrestrial features" |
| **Stable Diffusion** | `(negative:1.3)` | 见上文 SD 示例中的 Negative 部分 |

### 7.3 天文特化负面提示词

针对深空摄影特有的常见问题：

```
天文专场负面词：
- "no atmospheric dispersion" — 无大气色散（星点无红蓝分离）
- "no diffraction spikes on small stars" — 小星点无十字衍射星芒
- "no false color, no chromatic aberration" — 无色差
- "no dust motes, no sensor artifacts" — 无灰尘斑点
- "no satellite trails, no cosmic ray hits" — 无卫星轨迹和宇宙线
- "no amp glow, no hot pixels" — 无传感器辉光/热像素
- "no coma, no field curvature" — 无彗差/场曲
- "no walking noise, no fixed pattern noise" — 无步行噪点/固定模式噪点
- "no misaligned color channels" — 无RGB通道错位
- "no clipping in core" — 核心不裁剪溢出
```

---

## 附录 A：快速参考卡片

### A.1 最常用的三个「万能」提示词模板

#### 模板 1：发射星云通用（Midjourney）

```
Professional astrophotography of [目标名称] [NGC/IC编号],
[焦距]mm focal length wide-field view,

Spectacular [结构描述] structure showing [核心特征1], [核心特征2],
[核心特征3], rich narrowband color palette with hydrogen-alpha in
crimson red, oxygen-III in electric teal, sulfur-II in warm gold

Captured through premium [望远镜型号] telescope with cooled monochrome
astronomy camera, [N] hours total integration from Bortle 1 dark sky
observatory, sub-arcsecond seeing, diffraction-limited optics,
processed in PixInsight with meticulous HDR and sharpening

Pristine dark space background, pinpoint stars --no illustration,
CGI, text, landscape --ar 16:9 --v 6.1 --style raw --s 250
```

#### 模板 2：星系通用（Midjourney）

```
Deep-sky astrophotography of [星系名称],
[焦距]mm focal length revealing [视角描述],

[核心] bright core with old yellow stellar population,
[旋臂] blue spiral arms rich in young stars and pink HII regions,
[尘埃] dark dust lanes tracing the spiral structure in sharp relief,
[特殊] [伴星系/潮汐尾/相互作用特征],

Imaged through [望远镜型号] under pristine seeing conditions,
LRGB + H-alpha integration totaling [N] hours, processed with
careful dynamic range compression and structural sharpening

Background galaxies visible through the deep integration --no text,
watermark, landscape --ar 16:9 --v 6.1 --style raw --s 200
```

#### 模板 3：星云细节特写（通用）

```
Extreme close-up astrophotography of [目标名称],
[焦距]mm at [光圈] revealing the finest structural details,

Intricate [特征1] forming [形态描述], delicate [特征2] creating
three-dimensional depth and texture, [特征3] providing dramatic contrast,
every filament and knot resolved at the diffraction limit

[颜色描述] with scientifically accurate emission line mapping,
perfect dark sky background, stars like diamond dust on black velvet

Prime focus through [望远镜], [N] hours narrowband, exceptional seeing
at high-altitude observatory --no landscape, illustration, text
--ar 3:2 --v 6.1 --style raw --s 220
```

### A.2 参数速查

| 视觉效果目标 | MJ 参数 | processor 对应参数 |
|-------------|---------|-------------------|
| 更写实 | `--s 100-200` | `--strength light` |
| 更具冲击力 | `--s 250-400` | `--strength strong` |
| 更暗更神秘 | `--s 150 + dark mood` | DBE 更强 + stretch 更保守 |
| 更亮更绚丽 | `--s 300 + vivid` | stretch 更激进 + saturation 增强 |
| 宽画幅 | `--ar 16:9` or `--ar 2:1` | 无需对应（raw 数据画幅固定） |
| 方形构图 | `--ar 1:1` | 裁切到 1:1 |

### A.3 常用滤镜/色彩映射参考

| 发射线 | 波长 | 传统色 | Hubble 色 | 推荐提示词描述 |
|--------|------|--------|-----------|--------------|
| Hα | 656nm | 红 | 绿 | `deep crimson`, `ruby red`, `warm hydrogen glow` |
| OIII | 500nm | 青 | 蓝 | `electric teal`, `cyan-blue`, `cool oxygen emission` |
| SII | 672nm | 红 | 红 | `warm gold`, `amber`, `deep orange-red` |
| Hβ | 486nm | 蓝 | — | `soft blue`, `azure`, `indigo highlights` |
| NII | 658nm | 红 | — | `magenta hints`, `rose undertones` |
| 反射星云 | — | 蓝 | 蓝 | `ethereal sapphire`, `ice blue`, `cool blue reflection` |
| 暗星云 | — | 黑 | 黑 | `dark brown`, `opaque dust`, `silhouetted molecular cloud` |

---

## 附录 B：与 deep-sky-processor 的参数对标表

| AI 提示词中的视觉效果 | processor 参数调整 | 具体命令示例 |
|---------------------|-------------------|------------|
| "pristine uniform dark background" | DBE degree 提高到 3 | `gradient_removal.py input.tif output.tif --degree 3` |
| "extended range revealing faint outer nebulosity" | stretch 使用 auto 模式 | `stretch.py input.tif output.tif --method auto` |
| "vivid crimson hydrogen and teal oxygen" | color_tools saturation 增强 | `color_tools.py input.tif output.tif --method saturation` |
| "delicate pinpoint stars, subtle stellar presence" | star_tools reduce | `star_tools.py reduce input.tif output.tif --reduction 0.6` |
| "crisp filament details" | sharpen amount 提高 | `sharpen.py input.tif output.tif --method multiscale_sharpen` |
| "no noise in dark areas, clean gradients" | denoise luminance_chroma | `denoise.py input.tif output.tif --method luminance_chroma` |
| "intricate three-dimensional dust structures" | enhance hdr | `enhance.py input.tif output.tif --method hdr` |

---

> **使用建议**：将本手册保存到 `~/.workbuddy/skills/deep-sky-processor/references/ai-prompt-engineering.md`，处理真实数据时参考「附录 B」对标表调参，AI 创作时直接使用「第 4 章」天体提示词库。
