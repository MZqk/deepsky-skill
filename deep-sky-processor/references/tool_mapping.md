# 专业工具对标

| 处理步骤 | deep-sky-processor | Siril | PixInsight |
|---|---|---|---|
| FITS 读写/检查 | `fits_io.py` | FITS sequence / Image Information | FITS Format / ImageInspection |
| 背景梯度去除 | `gradient_removal.py` | Background Extraction | DBE / ABE |
| 颜色校准 | `color_tools.py` | Color Calibration / PCC | SPCC / PCC / BackgroundNeutralization |
| LRGB 合成 | `lrgb_combine.py` | RGB Composition | LRGBCombination / ChannelCombination |
| 窄带合成 | 参数化通道映射 | Pixel Math / RGB Composition | PixelMath / ChannelCombination |
| 线性降噪 | `denoise.py` | Wavelets / denoise tools | MLT / MMT / NoiseXTerminator |
| 拉伸 | `stretch.py` | Asinh / Histogram Transformation | GHS / HistogramTransformation / MaskedStretch |
| 星点分离 | `star_tools.py` | StarNet | StarNet2 / StarXTerminator |
| HDR/局部增强 | `enhance.py` | CLAHE / wavelets | HDRMultiscaleTransform / LocalHistogramEqualization |
| 反卷积/锐化 | `sharpen.py` | Deconvolution | BlurXTerminator / Deconvolution / MLT |
| 缩星与重组 | `star_tools.py` | Star Recomposition | MorphologicalTransformation / PixelMath |
| 数值诊断 | `analyze.py` | Statistics / Image Inspection | Statistics / SubframeSelector |
| 质量量化 | `quality_metrics.py` | Statistics | Statistics / FFT / FWHMEccentricity |

这些名称用于建立迁移语境，不表示算法与商业工具完全等价。
