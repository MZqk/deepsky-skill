# AI 混合工作流最佳实践

本文档阐述传统天文后期管线与外部 AI 工具（NoiseXTerminator、StarNet++、Topaz 等）混合使用的架构、最佳实践与风险管控。

---

## 1. 核心架构原则

**AI 仅用于统计建模，绝不用于生成推断。**

| 任务类型 | 本质 | 允许 AI | 原因 |
|---------|------|--------|------|
| 降噪 | 统计估计：从含噪观测中估计真实信号 | 允许 (L1) | AI 从噪声中估计信号，不创造新像素 |
| 星点分离 | 分类任务：判断每个像素是星还是非星 | 允许 (L1) | 分类边界可通过残差验证 |
| 反卷积 | 逆问题求解：估计 PSF 并去卷积 | 谨慎 (L2) | PSF 估计可能不准，引入振铃伪影 |
| 超分辨率 | 生成推断：凭空生成亚像素细节 | 禁止 (L3) | 生成不存在的结构 = 数据造假 |
| 着色 | 生成推断：凭空赋予色彩 | 禁止 (L3) | 破坏发射线物理约束 |

---

## 2. 推荐混合工作流

### 2.1 降噪：NoiseXTerminator / Topaz Denoise AI

**接入方式**: 在 PixInsight/Siril 中使用 AI 降噪工具处理线性数据，输出 32-bit TIFF，通过 `--external-denoised` 接入管线。

```
原始 FITS → DBE → 色彩校准 → [NoiseXTerminator] → 保存为 TIFF
                                                    ↓
pipeline.py --external-denoised denoised.tif ... 其余步骤
```

**质量验证**: 运行 `denoise.py` 的 `validate_external_denoise()` 函数：
- 背景区域 RGB 均值偏移 < 5%
- 高频能量比 > 0.5（低于此值 = 过度涂抹）

**适用场景**: 噪声水平为 `high` 或 `very_high` 时推荐，`moderate` 及以下无需使用。

### 2.2 星点分离：StarNet++ v2

**接入方式**: 使用 StarNet++ CLI 生成无星图，通过 `--external-starless` 接入管线。

```bash
# StarNet++ CLI 示例
starnet++ input.tif starless.tif --stride 256

# 接入管线
pipeline.py input.tif output.jpg \
  --external-starless starless.tif \
  --external-detail-strength 2.0
```

**质量验证**: 使用 `star_tools.py` 的 `estimate_star_removal_quality()` 函数：
- 残留星比例 < 5% → 良好
- 星云误伤率 < 15% → 可接受
- 任一指标超标 → 形态学方法可能更可靠，或调整 StarNet++ 参数

**适用场景**: 星场密度为 `dense` 或 `very_dense` 时推荐，`sparse` 时形态学方法足够。

### 2.3 反卷积：BlurXTerminator

**接入方式**: 在 PixInsight 中使用 BlurXTerminator 处理，保存后接入管线后续步骤。

**注意事项** (L2 级别):
- BlurXTerminator 的 PSF 估计可能不准确，特别是在视宁度不均匀时
- 处理后必须做 CP4 审查：检查锐化后有无振铃伪影
- 如果出现振铃，回退到管线的多尺度锐化模块

---

## 3. 禁止的操作

### 3.1 AI 超分辨率 (ESRGAN, Real-ESRGAN, Topaz Gigapixel)

**为什么禁止**:
- AI 超分辨率在噪声基底上"幻觉"出亚像素结构
- 这些结构在原始数据中不存在
- 在天文摄影中，引入不存在的细节等同于数据造假
- 即使看起来"更清晰"，也无法区分真实细节和 AI 生成内容

**替代方案**: 增加实际曝光时间和叠加帧数来提升真实分辨率。

### 3.2 AI 着色

**为什么禁止**:
- 天文色彩来自发射线物理：Hα (656nm) → 红色、OIII (500nm) → 青蓝、SII (672nm) → 深红
- AI 着色模型不理解这些物理约束
- 必然引入色彩失真：Hα 可能变为品红，OIII 可能变为电蓝
- 破坏测光校准的精确性

**替代方案**: 使用 PCC 测光法校准或灰度世界白平衡，结合 `emission` 模式。

### 3.3 AI 纹理生成

**为什么禁止**:
- 生成式模型会在噪声区域"补全"不存在的纤维结构
- 观众无法区分真实星云结构和 AI 幻觉
- 违反天文摄影"基于真实数据"的根本原则

**替代方案**: 优化拉伸和增强参数来揭示已记录但不够明显的结构。

---

## 4. AI 介入后的质量审查检查清单

当使用任何外部 AI 工具时，Phase E 审查必须增加以下检查：

1. **高频能量比对**: 运行 `quality_metrics.py` 比对处理前后高频能量
2. **色彩偏移检测**: 比对 AI 处理前后同一背景区域的 RGB 均值
3. **星云边界完整性**: 检查星云-背景交界处是否有 AI 涂抹或硬边
4. **暗区纹理真实性**: 检查极暗区是否出现不自然的均匀斑块

任何一项不通过，必须回退到管线内置的传统算法。
