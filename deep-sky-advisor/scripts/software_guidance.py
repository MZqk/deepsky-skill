"""Authoritative per-software implementation content for generated advice.

This module is the SINGLE SOURCE OF TRUTH for how each application performs each
operation (tools, ordered steps, parameter-selection logic, mask strategy).

Where the same content lives elsewhere:

- `references/*.md`  — extended reading. Conceptual explanation and menu paths only.
                       It is NOT authoritative for the generated report.
- `report_zh.py`     — label translations (evidence paths, enum values) and report
                       layout only. It must not carry operational content.

If you change an implementation here, nothing else needs to change. Do not
re-introduce a second copy of this content in another module.

Content is written in Chinese because the generated report is Chinese-only. The
compiled `*_advice.json` therefore carries Chinese prose; `validate_advice()` only
checks that the fields are present and non-empty.
"""


GENERIC = {
    "calibrate_integrate": {
        "tools": ["校准", "注册", "单帧质量评估", "叠加"],
        "steps": [
            "核对校准帧与亮场的相机模式、增益、偏置、温度、合并方式和光路。",
            "评估单帧焦点、跟踪、云层、背景和构图。",
            "叠加后检查高低拒绝图，确认被拒绝的是异常值而不是真实信号。",
        ],
        "parameter_logic": [
            "根据有效帧数和异常值分布选择拒绝算法。",
            "没有采集模型依据时，不要擅自优化暗场或归一化通道。",
        ],
        "mask_strategy": ["叠加前不使用图像蒙版，以质量图和拒绝图作为主要验收依据。"],
    },
    "crop_edges": {
        "tools": ["裁切"],
        "steps": ["用强预览拉伸检查四边。", "只裁去注册楔形、空白拼接边缘和无效像素。"],
        "parameter_logic": ["使用清除无效数据所需的最小裁切范围。"],
        "mask_strategy": ["无需蒙版；保留有效暗空和目标外围。"],
    },
    "background_review": {
        "tools": ["背景样本", "低频模型", "差分/模型预览"],
        "steps": [
            "只在确认的空背景位置放置样本。",
            "生成最简单的可行模型，但先不应用。",
            "检查模型图和差分图是否出现目标轮廓。",
            "确认模型不含天体信号后再执行校正。",
        ],
        "parameter_logic": [
            "从低复杂度模型开始。",
            "仅在残差仍连贯且模型始终不含目标结构时提高复杂度。",
        ],
        "mask_strategy": ["排除主体、星晕、暗尘埃、IFN、星系外晕、拼接缝和亮星反射。"],
    },
    "color_calibration": {
        "tools": ["天体解算（Plate Solving）", "星表约束色彩校准"],
        "steps": [
            "确认 WCS 和采集元数据。",
            "选择真实或最接近且有依据的相机/滤镜响应。",
            "使用未饱和孤立恒星完成校色。",
            "分别检查恒星颜色与空间色度梯度。",
        ],
        "parameter_logic": ["只为获得干净的未饱和孤立恒星样本而调整检测参数。"],
        "mask_strategy": ["排除饱和星、拥挤核心、强星云背景和光学光晕。"],
    },
    "narrowband_mapping": {
        "tools": ["通道检查", "Pixel Math / 通道映射", "可选独立星点层"],
        "steps": [
            "记录每个源通道对应的发射线或滤镜。",
            "在归一化前分别检查各通道噪声和结构。",
            "选择并记录显示配色。",
            "需要时单独处理宽带或窄带恒星层。",
        ],
        "parameter_logic": ["按实测信号质量分配权重，不为强行形成配色而等权弱通道。"],
        "mask_strategy": ["只有源信号确实存在时才使用发射线蒙版或星点蒙版。"],
    },
    "linear_denoise": {
        "tools": ["线性降噪", "亮度/范围保护蒙版"],
        "steps": [
            "在线性数据副本上处理。",
            "使用蒙版保护高信噪结构和恒星。",
            "保守降低小尺度亮度及色度噪声。",
            "用 100% 视图和强预览拉伸对比前后。",
        ],
        "parameter_logic": ["只有背景方差下降速度快于真实细节损失时才增加强度。"],
        "mask_strategy": ["保护亮结构，不把连贯细丝、暗尘埃和星系外围当作噪声。"],
    },
    "star_shape_review": {
        "tools": ["FWHM/偏心率测量", "星点空间分布图", "单帧对比"],
        "steps": [
            "比较中心、四角和边缘。",
            "比较叠加图与代表性单帧。",
            "判断异常是全局、径向、切向、单侧还是通道相关。",
        ],
        "parameter_logic": ["FWHM 仅作为相对检查尺度，不能直接当作通用反卷积参数。"],
        "mask_strategy": ["排除饱和星、混叠星、衍射芒、星云亮结和拥挤星团核心。"],
    },
    "controlled_stretch": {
        "tools": ["屏幕预览拉伸", "Histogram / GHS / Asinh 拉伸"],
        "steps": [
            "用非破坏预览确定目标效果。",
            "执行多次小幅永久拉伸。",
            "每次检查背景、亮核、星点尺寸和颜色。",
        ],
        "parameter_logic": ["当拉伸主要放大噪声或压平亮部时停止。"],
        "mask_strategy": ["仅在确认存在高动态范围亮核时使用柔和核心/高光蒙版。"],
    },
    "highlight_protection": {
        "tools": ["范围/亮核蒙版", "HDR 压缩", "受保护拉伸"],
        "steps": [
            "确认亮端占比对应真实高光风险。",
            "围绕亮核或亮星建立柔和蒙版。",
            "执行保守压缩或受保护拉伸。",
        ],
        "parameter_logic": ["使用能够恢复内部层次且不产生灰平台的最低强度。"],
        "mask_strategy": ["充分羽化过渡，并排除无关中间调。"],
    },
    "star_treatment": {
        "tools": ["星点蒙版", "可选星点分离", "形态学或曲线缩星"],
        "steps": [
            "完成主体拉伸后再判断星点是否压制目标。",
            "按测得星点尺寸建立蒙版。",
            "测试低强度缩星或星点层处理。",
            "在 100% 视图检查小星、亮星核心、颜色和目标亮结。",
        ],
        "parameter_logic": ["先降低处理强度，再考虑增大蒙版半径；必须保留星点层级和小星群体。"],
        "mask_strategy": ["紧凑星云亮结或星团成员可能被误判为星点时，必须排除主体。"],
    },
    "color_refinement": {
        "tools": ["饱和度/色彩平衡工具", "亮度或色度蒙版", "分通道余量检查"],
        "steps": [
            "在主体拉伸和星点处理完成之后再动色彩，避免前序步骤改变色彩平衡。",
            "先按通道检查亮端余量；某个通道已经压到上限时，不要再用饱和度或色彩平衡推高它。",
            "从低强度开始，每次只改一项（饱和度或色彩平衡），改完立即比较。",
            "用亮度或色度蒙版把平滑背景排除在饱和度提升之外。",
            "在 100% 视图检查噪声是否被染成彩色、恒星颜色是否仍合理、通道是否出现裁切。",
        ],
        "parameter_logic": [
            "只处理已测得的残余偏差，不追求统一的「好看」配色。",
            "先降低强度，再考虑分通道调整；不要为了强化单一颜色而牺牲其余通道。",
        ],
        "mask_strategy": [
            "用亮度蒙版保护平滑背景，避免把背景噪声提升为彩色斑块。",
            "排除已饱和的亮星和亮核，避免保护区域产生色偏。",
        ],
    },
    "final_export": {
        "tools": ["高位深母版", "色彩空间转换", "输出缩放/锐化", "展示图导出"],
        "steps": [
            "保存全分辨率高位深母版。",
            "复制并转换到目标色彩空间。",
            "先缩放，再做输出锐化。",
            "嵌入配置文件并在色彩管理查看器中检查。",
        ],
        "parameter_logic": ["根据最终像素尺寸和观看介质决定输出锐化。"],
        "mask_strategy": ["保护平滑背景，避免输出锐化放大噪声。"],
    },
}


OVERRIDES = {
    "siril": {
        "calibrate_integrate": {
            "tools": ["转换/序列", "预处理", "注册", "序列图（Plot）", "叠加", "拒绝图"],
            "steps": [
                "在校准前保持 CFA 数据未去马赛克；校准后使用正确 Bayer Pattern 去马赛克。",
                "使用序列图评估 FWHM、圆度、背景和注册质量，再叠加入选帧。",
            ],
        },
        "background_review": {
            "tools": ["Background Extraction（背景提取）", "RBF 背景提取", "背景模型视图"],
            "steps": [
                "先裁掉无效边缘，再使用 Background Extraction 或 RBF。",
                "加性天空辉光优先采用减法，并检查生成的背景模型。",
            ],
        },
        "color_calibration": {
            "tools": ["Plate Solving（天体解算）", "Photometric Color Calibration（光度校色）"],
            "steps": [
                "使用正确焦距、像元尺寸和中心坐标完成 Plate Solving。",
                "在线性宽带数据上运行 Photometric Color Calibration。",
            ],
        },
        "narrowband_mapping": {
            "tools": ["Channel Extraction（通道提取）", "Pixel Math", "RGB Composition", "星点重组"],
            "steps": [
                "通过 Channel Extraction、Pixel Math 或 RGB Composition 组合有记录的通道。",
                "需要自然星色时重组单独校准的恒星层。",
            ],
        },
        "linear_denoise": {
            "tools": ["Wavelets/Multiscale 小波去噪", "星点蒙版", "范围蒙版"],
            "steps": ["在永久拉伸前使用 Wavelets/Multiscale，并用 Range/Star Mask 保护高信号区域。"],
        },
        "star_shape_review": {
            "tools": ["序列图（FWHM/圆度）", "注册结果", "中心/四角对比"],
            "steps": [
                "用序列图和叠加结果比较中心、四角与代表性单帧的星形。",
                "判断异常来自跟踪、光学边场、倾斜、色差还是注册，不要靠提高 rejection 阈值掩盖采集问题。",
            ],
            "parameter_logic": ["序列图用于帧间相对质量排序，不能当作绝对视宁度测量。"],
            "mask_strategy": ["无需蒙版；诊断依据是序列图与逐帧星形。"],
        },
        "controlled_stretch": {
            "tools": ["Generalised Hyperbolic Stretch", "Asinh Transformation", "Histogram Transformation"],
            "steps": ["使用 GHS 控制中间调和高光，Asinh 侧重保护星色，Histogram 直接控制黑位和中间调。"],
        },
        "highlight_protection": {
            "tools": ["GHS 高光保护", "范围蒙版", "Pixel Math 融合"],
            "steps": ["使用 GHS 保护参数、Range Mask 或 Pixel Math 融合更保守的拉伸版本。"],
        },
        "star_treatment": {
            "tools": ["StarNet（已安装时）", "星点蒙版", "形态学/曲线调整", "星点重组"],
            "steps": ["仅在目标允许时使用 StarNet 生成星点/无星层，并检查残留孔洞和光晕后再重组。"],
        },
        "color_refinement": {
            "tools": ["Color Saturation（色彩饱和度）", "Color Balance（色彩平衡）", "Range/Star Mask"],
            "steps": [
                "在非线性阶段、完成拉伸和星点处理之后使用 Color Saturation 做小幅调整。",
                "先用 Range Mask 或 Star Mask 限制作用范围，避免背景噪声被染成彩色。",
                "需要分通道微调时改用 Color Balance，并同时检查恒星颜色是否仍然合理。",
            ],
        },
        "final_export": {
            "tools": ["保存 32-bit FITS", "导出 16-bit TIFF", "色彩管理 PNG/JPEG 导出"],
            "steps": ["保存 32-bit FITS 母版；需要精修时导出 16-bit TIFF；展示图嵌入目标配置文件。"],
        },
    },
    "pixinsight": {
        "calibrate_integrate": {
            "tools": ["WeightedBatchPreprocessing", "SubframeSelector", "LocalNormalization", "ImageIntegration", "拒绝图"],
            "steps": [
                "使用 WBPP 配置匹配的校准帧和 CFA Pattern。",
                "通过 SubframeSelector 评估单帧；仅在背景变化确有需要时使用 LocalNormalization。",
                "检查 ImageIntegration 高低拒绝图。",
            ],
        },
        "background_review": {
            "tools": ["DynamicCrop", "DynamicBackgroundExtraction", "AutomaticBackgroundExtractor", "生成的背景模型"],
            "steps": [
                "先使用 DynamicCrop 清除无效边缘。",
                "复杂画面优先 DBE 手工采样；ABE 只用于简单场景且同样必须检查模型。",
                "加性天空辉光使用 Subtraction；疑似渐晕先回查平场，不直接使用 Division。",
            ],
            "parameter_logic": [
                "加性天空辉光使用 Subtraction。",
                "疑似乘性渐晕应作为平场/校准问题先行追查，再考虑 Division。",
            ],
        },
        "color_calibration": {
            "tools": ["ImageSolver", "SpectrophotometricColorCalibration", "BackgroundNeutralization（仅在有依据时）"],
            "steps": [
                "使用 ImageSolver 确认 WCS。",
                "在 SPCC 中选择实际或有依据的相机/滤镜响应并检查拟合恒星。",
            ],
        },
        "narrowband_mapping": {
            "tools": ["ChannelCombination", "PixelMath", "NarrowbandNormalization", "StarNet"],
            "steps": [
                "使用统一 STF 参考检查 Hα/OIII/SII。",
                "通过 PixelMath、ChannelCombination 或 NarrowbandNormalization 建立并记录配色。",
                "需要自然星色时使用 RGB 或单独校准的星点层。",
            ],
        },
        "linear_denoise": {
            "tools": ["MultiscaleLinearTransform", "TGVDenoise", "NoiseXTerminator（已安装时）", "RangeSelection 蒙版"],
            "steps": [
                "使用 RangeSelection 建立保护蒙版。",
                "MLT 优先处理已测得的小尺度噪声；TGV 注意边缘保护。",
                "使用 NoiseXTerminator 等外部工具时必须与未处理线性母版对比。",
            ],
        },
        "star_shape_review": {
            "tools": ["FWHMEccentricity", "SubframeSelector", "AberrationInspector", "DynamicPSF"],
            "steps": [
                "使用 FWHMEccentricity/SubframeSelector 测量中心和四角。",
                "使用 AberrationInspector 比较边场几何；需要 PSF 时仅对未饱和孤立星使用 DynamicPSF。",
            ],
        },
        "controlled_stretch": {
            "tools": ["ScreenTransferFunction", "HistogramTransformation", "GeneralizedHyperbolicStretch", "MaskedStretch"],
            "steps": [
                "STF 仅作预览，检查 linked/unlinked 行为。",
                "将经过检查的 STF 转入 HistogramTransformation，或使用 GHS 精细控制中间调和高光。",
                "MaskedStretch 仅在其星点尺寸和对比度取舍适合目标时使用。",
            ],
        },
        "highlight_protection": {
            "tools": ["RangeSelection", "HDRMultiscaleTransform", "LocalHistogramEqualization", "GHS 高光保护"],
            "steps": [
                "使用 RangeSelection 建立柔和亮核蒙版。",
                "HDRMultiscaleTransform 的尺度必须匹配目标结构；LHE 应通过保护蒙版局部使用。",
            ],
        },
        "star_treatment": {
            "tools": ["StarNet", "StarXTerminator（已安装时）", "MorphologicalTransformation", "PixelMath 重组"],
            "steps": [
                "先检查 StarNet/StarXTerminator 星点层和无星层残留。",
                "MorphologicalTransformation 使用 Selection/Amount 混合，结构元素尺度参考 FWHM。",
                "PixelMath 重组时检查黑圈、核心裁切和星色。",
            ],
        },
        "color_refinement": {
            "tools": ["ColorSaturation", "CurvesTransformation（色度）", "RangeSelection 蒙版"],
            "steps": [
                "使用 ColorSaturation 在受保护蒙版下小幅提升饱和度，先只动 Saturation 一项。",
                "需要色彩平衡微调时使用 CurvesTransformation 的色度曲线，不要直接改通道直方图。",
                "用 RangeSelection 保护背景与已饱和亮核，并检查是否出现色度裁切。",
            ],
        },
        "final_export": {
            "tools": ["32-bit XISF 母版", "ICCProfileTransformation", "Resample", "UnsharpMask/MultiscaleLinearTransform", "16-bit TIFF/JPEG 导出"],
            "steps": [
                "保存带处理历史的 32-bit XISF 母版。",
                "使用 ICCProfileTransformation 转换展示副本，Resample 后再输出锐化并嵌入配置文件。",
            ],
        },
    },
    "photoshop": {
        "calibrate_integrate": {
            "tools": ["需要外部天文专用软件"],
            "steps": ["Photoshop 不用于校准、注册和叠加；先在 Siril 或 PixInsight 中准备校准、叠加并尽量完成校色的 16-bit TIFF。"],
        },
        "background_review": {
            "tools": ["返回 Siril/PixInsight 处理", "Curves 调整图层（仅处理轻微残差）", "大范围柔和亮度蒙版"],
            "steps": [
                "主要背景建模返回线性天文软件完成。",
                "仅对已确认的轻微残差使用 Curves 调整图层和大范围柔和亮度蒙版。",
                "禁止使用仿制图章、修复、内容识别或生成式填充修改天区。",
            ],
            "parameter_logic": ["禁止对天区使用仿制图章、修复画笔、内容识别填充或生成式填充。"],
        },
        "color_calibration": {
            "tools": ["外部测光色彩校准", "Curves", "Selective Color", "Color Balance 调整图层"],
            "steps": [
                "先在外部完成测光校色。",
                "Photoshop 中仅使用低不透明度 Curves、Selective Color 或 Color Balance 调整已确认的残余偏差。",
            ],
        },
        "narrowband_mapping": {
            "tools": ["预先配准合成的 16-bit 通道图像", "Apply Image", "Channel Mixer/Curves", "图层蒙版"],
            "steps": [
                "导入已注册的源通道或有记录的合成图。",
                "使用 Apply Image、Channel Mixer/Curves 和图层蒙版建立明确配色，不能补造缺失通道。",
            ],
        },
        "linear_denoise": {
            "tools": ["优先在外部完成线性降噪", "Camera Raw 滤镜", "Smart Object", "亮度蒙版"],
            "steps": [
                "优先在导入 Photoshop 前完成线性降噪。",
                "残余噪声可在 Smart Object 上轻量使用 Camera Raw，并通过亮度蒙版排除星点、细丝和尘埃边缘。",
            ],
        },
        "star_shape_review": {
            "tools": ["100% 视图检查", "外部 FWHM/偏心率测量工具"],
            "steps": ["跟踪、倾斜和场曲诊断应在外部工具完成；不要默认使用变形、绘画或液化把星点强行修圆。"],
        },
        "controlled_stretch": {
            "tools": ["16-bit 文档", "Curves 调整图层", "High Pass + 柔光混合", "Selective Color / Color Balance 调整图层", "亮度蒙版"],
            "steps": [
                "只做收尾级调整：主拉伸应已在主轨完成，这里只补局部对比与色彩。",
                "使用多层 Curves 调整图层而非破坏性 Image Adjustments，锚定黑位后逐步提升中间调。",
                "用亮度蒙版分离暗弱主体、中间调和亮核，分别处理。",
                "需要局部对比时：复制图层 → High Pass（半径按输出尺寸）→ 柔光混合 → 用蒙版限制在目标结构。",
                "需要色彩精修时：用 Selective Color 或 Color Balance 在低不透明度下调整，并同时监控恒星颜色。",
                "每层在 100% 视图检查，并开关图层确认确有改善。",
            ],
            "parameter_logic": [
                "先降低图层不透明度，再考虑改动曲线形状。",
                "局部对比半径必须依据最终输出尺寸，而不是当前工作分辨率。",
            ],
            "mask_strategy": [
                "用亮度蒙版把平滑背景和星点排除在对比增强之外。",
                "局部对比蒙版必须充分羽化，避免出现可见边界。",
            ],
        },
        "highlight_protection": {
            "tools": ["亮度蒙版", "Curves", "Camera Raw 高光控制", "图层不透明度"],
            "steps": [
                "建立亮核或亮星的亮度选区并充分羽化。",
                "通过 Curves 或 Camera Raw Highlights 轻量压制高光。",
                "用图层不透明度逐步降低强度，直到过渡痕迹消失。",
                "确认未饱和星色与亮核内部结构在压制后仍然保留。",
            ],
            "parameter_logic": ["使用能够恢复亮核层次且不产生灰平台的最低强度。"],
            "mask_strategy": ["充分羽化过渡，并把无关中间调排除在蒙版之外。"],
        },
        "star_treatment": {
            "tools": ["外部准备的星点层/蒙版", "Minimum 滤镜（Preserve Roundness）", "Curves", "图层不透明度"],
            "steps": [
                "优先使用外部生成的星点层或准确星点蒙版；主轨未做星点分离时，应向主轨索取星点层而不是在此自行构建。",
                "如使用 Minimum，仅作用于星点层并采用最小有效半径。",
                "通过图层不透明度混合，不要直接接受滤镜全强度结果。",
                "在 100% 视图检查小星、黑圈和核心裁切。",
            ],
            "parameter_logic": [
                "先降低图层不透明度，再考虑增大 Minimum 半径。",
                "绝不要对整个合成图使用 Minimum，它会侵蚀星云亮结和尘埃边缘。",
            ],
            "mask_strategy": [
                "所有调整只作用于星点层或准确的星点蒙版。",
                "排除可能被误判为星点的紧凑星云亮结和星团成员。",
            ],
        },
        "color_refinement": {
            "tools": ["Vibrance 调整图层", "Selective Color 调整图层", "Color Balance 调整图层", "亮度蒙版"],
            "steps": [
                "只做收尾级精修：主要色彩处理应已在主轨完成，这里只补残余偏差。",
                "优先使用 Vibrance 而非整体 Saturation，避免把已经饱和的区域进一步推高。",
                "需要定向调整时使用 Selective Color 或 Color Balance 调整图层，从低不透明度开始。",
                "通过亮度蒙版把平滑背景和已饱和亮星排除在调整之外。",
                "在 100% 视图检查背景是否出现彩色噪声斑块、恒星颜色是否仍合理。",
            ],
            "parameter_logic": [
                "先降低图层不透明度，再考虑扩大作用的颜色范围。",
                "只处理已测得的残余偏差，不追求统一的「好看」配色。",
            ],
            "mask_strategy": [
                "用亮度蒙版保护平滑背景，避免把背景噪声提升为彩色斑块。",
                "排除已饱和的亮星与亮核。",
            ],
        },
        "final_export": {
            "tools": ["分层 16-bit PSD/TIFF", "Convert to Profile", "Image Size 缩放", "通过蒙版使用 Smart Sharpen/High Pass", "导出"],
            "steps": [
                "在缩放和锐化之前先保留分层 16-bit 母版。",
                "复制并用 Convert to Profile 转换到交付色彩空间，不要用 Assign 指定。",
                "先缩放，再做受控的输出锐化。",
                "输出锐化只在最终展示尺寸执行，并通过蒙版排除平滑背景。",
                "导出时嵌入配置文件并检查色带。",
            ],
            "parameter_logic": [
                "根据最终像素尺寸和观看介质决定输出锐化。",
                "交付副本与母版分离；绝不对母版做输出锐化。",
            ],
            "mask_strategy": ["保护平滑背景、暗弱细丝和星点光晕，避免被输出锐化放大。"],
        },
    },
}


# The tool that best names each operation in a cross-software comparison table.
# `tools[0]` is the first step, not necessarily the signature tool (for example
# PixInsight lists DynamicCrop before DynamicBackgroundExtraction), so the
# comparison table uses this explicit map and falls back to `tools[0]`.
PRIMARY_TOOL = {
    ("generic", "background_review"): "Background model preview",
    ("generic", "color_calibration"): "Catalog-constrained color calibration",
    ("generic", "narrowband_mapping"): "Channel mapping",
    ("siril", "calibrate_integrate"): "Pre-processing / Stacking",
    ("siril", "color_calibration"): "Photometric Color Calibration",
    ("siril", "narrowband_mapping"): "Pixel Math",
    ("siril", "star_treatment"): "StarNet",
    ("siril", "color_refinement"): "Color Saturation",
    ("pixinsight", "background_review"): "DynamicBackgroundExtraction",
    ("pixinsight", "color_calibration"): "SpectrophotometricColorCalibration",
    ("pixinsight", "narrowband_mapping"): "PixelMath",
    ("pixinsight", "controlled_stretch"): "HistogramTransformation",
    ("pixinsight", "color_refinement"): "ColorSaturation",
}


def get_software_guidance(software, operation_id):
    """Merged implementation content for one operation in one application."""
    base = dict(GENERIC[operation_id])
    override = OVERRIDES.get(software, {}).get(operation_id, {})
    for key, value in override.items():
        base[key] = value
    base["checkpoints"] = []
    base["failure_signs"] = []
    return base


def get_primary_tool(software, operation_id):
    if (software, operation_id) in PRIMARY_TOOL:
        return PRIMARY_TOOL[(software, operation_id)]
    tools = get_software_guidance(software, operation_id)["tools"]
    return tools[0] if tools else ""
