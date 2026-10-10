# PixInsight 天文后期处理工作流程

> **扩展阅读，不是权威来源。**
> 本文件只描述 PixInsight 的**进程名、菜单路径、脚本接口与软件特有事项**。
> 操作内容（工具、步骤、参数依据、蒙版策略）以 `scripts/software_guidance.py` 为唯一真相源；
> 目的 / 起始策略 / 调整原则 / 阶段验收 / 回退条件以 `scripts/generate_advice.py` 为唯一真相源；
> 阶段定义见 `references/pipeline_stages.md`。**修改本文件不会改变生成结果。**

## 简介

PixInsight 是专业级天文图像处理软件，工具链最完整，适合作为主轨。

## 阶段与操作的对应关系

| 阶段 | 报告中的操作 | PixInsight 进程 / 脚本 |
|---|---|---|
| linear | 校准、选帧与叠加 | Scripts → Batch Processing → WeightedBatchPreprocessing；SubframeSelector；ImageIntegration |
| linear | 裁切无效边缘 | DynamicCrop |
| linear | 背景与梯度处理 | DynamicBackgroundExtraction（DBE）/ AutomaticBackgroundExtractor（ABE） |
| linear | 宽带色彩校准 | ImageSolver → SpectrophotometricColorCalibration（SPCC） |
| linear | 窄带通道映射 | PixelMath / ChannelCombination / NarrowbandNormalization |
| linear | 线性阶段降噪 | MultiscaleLinearTransform（MLT）/ TGVDenoise / NoiseXTerminator |
| linear | 星点形态诊断 | FWHMEccentricity / SubframeSelector / AberrationInspector / DynamicPSF |
| nonlinear | 受控非线性拉伸 | ScreenTransferFunction（仅预览）→ HistogramTransformation / GeneralizedHyperbolicStretch / MaskedStretch |
| nonlinear | 亮核与高光保护 | RangeSelection / HDRMultiscaleTransform / LocalHistogramEqualization |
| nonlinear | 星点处理 | StarNet / StarXTerminator / MorphologicalTransformation / PixelMath |
| finishing | 色彩精修 | ColorSaturation / CurvesTransformation（色度曲线） |
| export | 母版保存与最终导出 | 32-bit XISF 母版；ICCProfileTransformation；Resample |

每个操作的具体步骤、参数选择依据与验收条件不在本文件，见文首说明的来源。

## PixInsight 特有的注意事项

### STF 只是预览

ScreenTransferFunction 不改变像素数据。把它当作观察工具：确认之后再把参数转移到
HistogramTransformation，或改用 GHS 做更细的中间调与高光控制。

### 背景提取：减法还是除法

加性天空辉光用 Subtraction；疑似**乘性**渐晕应先回查平场与校准流程，
不要用 Division 掩盖配准或平场问题。ABE 只适合简单场景，且同样必须检查生成的背景模型。

### 拒绝算法取决于帧数

拒绝算法由有效帧数和异常值分布决定，不要固定套用同一组 sigma。
帧数较多时可以从 Winsorized Sigma Clipping 起步；帧数较少时应检查 Percentile、
Averaged Sigma 等适合小样本的方案。必须查看 rejection low/high 图。

### 去马赛克的位置

OSC / CFA 数据应在**校准之后、配准之前**按正确的 Bayer Pattern 去马赛克，
顺序颠倒会破坏校准精度。

### 外部 AI 工具

NoiseXTerminator / StarXTerminator 等需要单独安装。使用它们时必须与**未处理的**版本对比，
确认暗弱小星、细丝和尘埃边缘没有被抹除，也没有被伪造成不存在的结构。

### 蒙版构建入口

- 星点蒙版：StarMask 脚本；或手工用 RangeSelection + MorphologicalTransformation 膨胀
- 星云蒙版：RangeSelection 取中亮区域，排除星点与背景

### 已知限制

- SPCC 需要可靠 WCS、正确的滤镜/相机响应和线性数据；求解或拟合失败时应先修正
  WCS、焦距与像元尺寸，不要靠强制白平衡掩盖。
- 窄带 / 双窄带数据不能用宽带星表校色的结果解释为「星云的真实 RGB 色彩」。

## 自动化脚本（PJSR）

PixInsight 支持 JavaScript（PJSR）脚本做批量处理：

```javascript
#include <pjsr/ProcessInstance.jsh>

function batchDBE(imageList) {
  for (var i = 0; i < imageList.length; i++) {
    var image = ImageWindow.windowById(imageList[i]);
    var dbe = new ProcessInstance("DynamicBackgroundExtraction");
    dbe.loadParameters("dbe_params.xpsm");
    dbe.executeOn(image.mainView);
  }
}
```

## 性能与内存

- 彩色相机在 WeightedBatchPreprocessing 中启用 CFA 模式，提升色彩准确性。
- 并行处理：Process → Global Preferences → Parallel Processing，线程数通常取 CPU 核心数。
- 大图关闭不需要的图像窗口，并考虑启用 Swap File。

## 与 Siril / Photoshop 的衔接

- 在 PixInsight 完成处理后保存 **32-bit XISF** 母版；需要二次加工时导出 **16-bit TIFF**。
- 报告默认给出 Siril 与 PixInsight **两条并列主轨**，任选其一执行，不要串联叠加。
- 交给 Photoshop 的位深、色彩空间与不可逆项要求，见报告的「交接给 Photoshop 的契约」一节。

## 学习资源

- PixInsight 官方文档：https://pixinsight.com/doc/
- LightVortex Astronomy Guides
- YouTube: IP4AP（Image Processing for Astrophotographers）
