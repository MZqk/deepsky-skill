# PixInsight 天文后期处理工作流程

## 简介
PixInsight 是专业级天文图像处理软件，提供最完整的天文图像处理工具集。

## 核心处理流程

### 1. 批量预处理（WeightedBatchPreprocessing）

#### 1.1 设置项目
```
Process → ImageCalibration → WeightedBatchPreprocessing
或使用脚本：
Scripts → Batch Processing → WeightedBatchPreprocessing
```

#### 1.2 配置原则
```
输入文件：
- Light frames: 选择亮场文件夹
- Dark frames: 暗场文件夹
- Flat frames: 平场文件夹
- Bias/Offset: 偏置帧文件夹

校准选项：
- 只在相机、暗场和平场条件支持时启用对应校准项
- CosmeticCorrection 需要基于坏点图或经过验证的参数，不能无条件启用
- Dark optimization 不适用于所有 CMOS 数据；存在暗电流不稳定、辉光或匹配暗场时应谨慎
- OSC/CFA 数据在校准后、配准前按正确 Bayer pattern 去马赛克

加权与拒绝：
- 使用 SubframeSelector/WBPP 的质量指标检查 FWHM、偏心率、SNRWeight 和异常帧
- 拒绝算法由帧数和异常值分布决定，不使用固定 sigma 作为通用答案
- 样本较多时可从 Winsorized Sigma Clipping 开始；样本较少时检查 Percentile、
  Averaged Sigma 或其他适合小样本的方案
- 必须查看 rejection low/high 图，确认卫星、热像素等被拒绝，而真实星核和目标结构未被删除

输出格式：
- Format: XISF (推荐) 或 FITS
- Bit depth: 32-bit floating point
```

### 2. 背景提取（DynamicBackgroundExtraction）

#### 2.1 手动放置背景点
```
Process → BackgroundModelization → DynamicBackgroundExtraction

操作步骤：
1. 点击"Generate"生成初始背景点
2. 手动调整点位置（避免星点和目标）
3. 样本数量和间距按画面尺度决定，避免把目标、暗尘埃、IFN、星系外晕或亮星光晕当作背景
4. 调整参数：
   - 加性天空梯度通常使用 Subtraction
   - 乘性渐晕优先追查平场；只有确认模型适合时才考虑 Division
   - 从低复杂度模型开始，只有残差证明确有必要时才提高 Function Degree
```

#### 2.2 执行背景提取
```
点击"Apply"或"Apply Global"
输出：
- result_dbe_: 背景模型
- result: 背景校正后的图像
```

### 3. 色彩校准（SPCC / PCC）

#### 3.1 设置参考星表
```
优先使用当前版本提供的 SpectrophotometricColorCalibration（SPCC）。
旧项目或特定流程可使用 PhotometricColorCalibration（PCC）。

参数设置：
- 先确认图像有可靠 WCS、正确滤镜/相机响应和线性数据
- 宽带 RGB/OSC：根据实际滤镜和传感器选择响应曲线；不要随意套用近似器材
- 窄带/双窄带：不能把宽带星表校色直接解释为星云的“真实 RGB 色彩”
- 执行后检查未饱和恒星颜色、背景色度梯度和亮星通道裁切
- 求解或星表拟合失败时，先修正 WCS、焦距、像元尺寸和检测条件，不要靠强制白平衡掩盖问题
```

### 4. 非线性拉伸（HistogramTransformation / MaskedStretch）

#### 4.1 直方图变换
```
Process → IntensityTransformations → HistogramTransformation

推荐流程：
1. 在线性图上使用 STF 预览判断目标和高光，但不要把 STF 预览误认为已经改变数据
2. 将经过检查的 STF 参数转移到 HistogramTransformation，或手动调整 shadows、
   midtones 和 highlights
3. 黑点不要切入有效数据；逐步调整中间调并监控星核、亮核和暗弱外围
4. 每轮拉伸后检查通道裁切、背景颗粒和颜色是否被高光压成白色
```

#### 4.2 蒙版拉伸（推荐）
```
Process → IntensityTransformations → MaskedStretch

参数：
- Target background 按目标、显示环境和后续处理空间选择，不固定为 0.25
- 迭代次数影响收敛平滑度，不代表处理强度
- MaskedStretch 有利于控制星点，但可能压低星色和局部对比；执行后必须检查星核和背景
```

### 5. 去噪（MultiscaleLinearTransform / TGVDenoise）

#### 5.1 多尺度变换去噪
```
Process → MultiscaleProcessing → MultiscaleLinearTransform

设置原则：
- 在线性阶段、使用合适的亮度/色度蒙版执行
- 层数按图像尺度和噪声结构决定
- Threshold 以噪声估计和实时预览为依据；Amount 从保守值开始
- 小尺度降噪后检查暗弱恒星和细丝是否被抹除
```

#### 5.2 TGVDenoise（高级去噪）
```
Process → MultiscaleProcessing → TGVDenoise

使用原则：
- TGVDenoise 用于受控的结构保持降噪，不是背景梯度修复工具
- 在线性或非线性阶段按工作流选择合适蒙版
- strength、edge protection 和 iterations 应通过预览确定
- 若背景出现蜡感、块状结构或弱信号消失，应降低强度或回退
```

### 6. 锐化（MultiscaleMedianTransform / UnsharpMask）

#### 6.1 多尺度锐化
```
Process → MultiscaleProcessing → MultiscaleMedianTransform

设置：
- Layers: 4 - 5 层
- Sharpening:
  - Layer 1-2: Amount = 0.1 - 0.3
  - Layer 3-4: Amount = 0.05 - 0.15
  - Layer 5+: (不锐化)
- 使用蒙版保护背景
```

#### 6.2 非锐化掩膜
```
Process → Convolution → UnsharpMask

参数：
- Std deviation: 2 - 5 (根据星点大小)
- Amount: 0.3 - 0.8
- 使用星点蒙版保护星点
```

### 7. 星点增强（StarNet / StarShrink）

#### 7.1 星点缩小
```
使用 StarShrink (第三方脚本)
或使用 MorphologicalTransformation

Process → Morphology → MorphologicalTransformation
- 结构元素尺度应相对星点 FWHM 选择，不固定为 1-2 像素
- 使用 Selection/Amount 混合原图，避免直接全强度腐蚀
- 用星点蒙版限制作用，并检查黑圈、方形星和微弱星消失
```

#### 7.2 分离星点和星云（StarNet++）
```
Scripts → StarNet → StarNet++ (需要单独安装)

输出：
- Starless: 无星点版本
- Stars: 仅星点版本
- 可分别处理后合并
```

### 8. 曲线和色彩调整（CurvesTransformation / ColorSaturation）

#### 8.1 曲线调整
```
Process → IntensityTransformations → CurvesTransformation

推荐调整：
- RGB/K: 整体亮度曲线（S 形）
- R/G/B: 分别调整色彩平衡
- 使用多个控制点精细调整
```

#### 8.2 饱和度调整
```
Process → ColorCalibration → ColorSaturation

设置：
- 饱和度按目标和星色逐步增加，不使用全目标通用数值
- 使用亮度或色度蒙版保护背景噪声和接近饱和的亮星
- 检查红星、蓝星和星云是否出现通道裁切或不自然的单色块
```

## 常用蒙版制作

### 星点蒙版
```
1. StarMask 脚本 (推荐)
   Scripts → StarMask → StarMask
   - Star intensity: 调整检测阈值
   - Fuzziness: 0.5 - 1.0

2. 或手动制作：
   - 复制图像
   - RangeSelection (星点亮度范围)
   - MorphologicalTransformation (膨胀)
```

### 星云蒙版
```
1. 使用 RangeSelection
   - 选择中等亮度区域
   - 排除星点和背景

2. 使用 MultiscaleLinearTransform
   - 仅保留大尺度结构
```

## 输出和保存

### 保存项目
```
File → Save Project
推荐格式：.xisf (保留所有处理历史)
```

### 导出最终图像
```
File → Save As
- Format: XISF (16-bit 或 32-bit)
- 或导出为 TIFF (16-bit) 用于 PS 处理
- 或导出为 JPEG/PNG (8-bit) 用于分享
```

## PixInsight 与 Siril/PS 的衔接

### 从 Siril 导入
```
1. Siril 保存为 32-bit FITS 或 XISF
2. PixInsight 直接打开
3. 建议：在 Siril 完成预处理，PixInsight 做后期
```

### 导出到 Photoshop
```
1. 保存为 16-bit TIFF
2. 色彩空间：sRGB 或 Adobe RGB
3. 分层保存（如果使用 StarNet）
```

## 自动化脚本

PixInsight 支持 JavaScript (PJSR) 脚本自动化：

```javascript
// 示例：批量背景提取
#include <pjsr/ProcessInstance.jsh>

function batchDBE(imageList) {
  for (var i = 0; i < imageList.length; i++) {
    var image = ImageWindow.windowById(imageList[i]);
    var dbe = new ProcessInstance("DynamicBackgroundExtraction");
    dbe.loadParameters("dbe_params.xpsm"); // 加载预设
    dbe.executeOn(image.mainView);
  }
}
```

## 性能优化建议

1. **使用 CFA 模式**（彩色相机）：
   - 在 WeightedBatchPreprocessing 中启用
   - 提升色彩准确性

2. **并行处理**：
   - Process → Global Preferences → Parallel Processing
   - 设置线程数（通常 = CPU 核心数）

3. **内存管理**：
   - 关闭不需要的图像窗口
   - 使用"Swap File"选项（大图像）

## 推荐学习资源

- PixInsight 官方文档：https://pixinsight.com/doc/
- LightVortex Astronomy Guides (详细教程)
- YouTube: IP4AP (Image Processing for Astrophotographers)
