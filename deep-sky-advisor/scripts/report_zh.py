"""Chinese localization for generated Markdown processing reports."""


OPERATION_TEXT = {
    "calibrate_integrate": {
        "purpose": "先建立可信的校准与叠加母版，避免根据单帧或校准帧制定成片后期方案。",
        "start": "核对采集条件和校准帧匹配关系，再进行选帧、注册和叠加。",
        "adjust": "只根据焦点、跟踪、云层、背景和注册质量等可验证问题剔除帧。",
        "software_action": "在天文专用软件中完成校准、质量评估、注册、异常值拒绝和叠加。",
    },
    "crop_edges": {
        "purpose": "在统计分析和背景建模前移除无效注册边缘。",
        "start": "高对比检查四边，只裁掉零值楔形、空白拼接边缘和明确无效像素。",
        "adjust": "采用能够清除无效数据的最小裁切，不因暗弱天空较暗而扩大裁切。",
        "software_action": "使用裁切工具删除无效边缘，同时保持目标构图和暗弱外围结构。",
    },
    "background_review": {
        "purpose": "确认画面中的低频变化是否属于可校正背景，同时避免减掉真实天体结构。",
        "start": "先创建低复杂度试验背景模型，不要立即应用。",
        "adjust": "只有残差仍呈连贯的仪器或天空梯度，且模型中没有目标结构时，才提高模型复杂度。",
        "software_action": "建立并审查背景模型；模型通过真实性检查后再决定减法、除法或跳过。",
    },
    "color_calibration": {
        "purpose": "使用恒星和器材响应约束宽带颜色，而不是仅凭视觉把背景强行调成中性。",
        "start": "确认或完成 WCS 求解，再使用实际或有依据的相机/滤镜响应进行星表校色。",
        "adjust": "空间色度梯度应与全局色彩校准分开处理，不用白平衡掩盖残余梯度。",
        "software_action": "使用天文测光校色流程约束未饱和恒星颜色，并独立检查背景色度残差。",
    },
    "narrowband_mapping": {
        "purpose": "根据真实采集通道建立可追溯的窄带配色，不把伪色映射表述成自然 RGB。",
        "start": "先确认每个源通道对应的发射线、滤镜和信噪质量，再决定颜色映射。",
        "adjust": "弱通道不应仅为凑配色而被强行拉到与强通道等权。",
        "software_action": "明确记录通道到显示颜色的映射，并在需要时单独处理恒星层。",
    },
    "linear_denoise": {
        "purpose": "在线性阶段降低有统计依据的背景噪声，同时保护暗弱真实信号。",
        "start": "在副本上使用保护蒙版做保守降噪，并以 100% 视图和强预览拉伸进行比较。",
        "adjust": "只有背景方差下降且小星、尘埃边缘和连贯细丝仍保留时才增加强度。",
        "software_action": "使用线性降噪工具配合亮度或范围蒙版，优先处理小尺度噪声。",
    },
    "star_shape_review": {
        "purpose": "在进行美容修正前，判断星点异常来自跟踪、光学边场、倾斜、色差还是注册。",
        "start": "分别检查中心、四角、边缘和代表性单帧的星点形态与方向。",
        "adjust": "区分全场同向拉长、径向/切向边场变化、单侧异常和通道相关异常。",
        "software_action": "使用星点 FWHM、偏心率和空间分布工具完成诊断，不直接把美容处理当成修复。",
    },
    "controlled_stretch": {
        "purpose": "在保留黑位、星色和亮核层次的前提下显现暗弱目标。",
        "start": "先用非破坏预览确定目标效果，再分多次进行小幅永久拉伸。",
        "adjust": "当新增拉伸带来的噪声增长超过真实结构增长，或亮部开始变平时停止。",
        "software_action": "根据目标动态范围选择 Histogram、GHS、Asinh 或 Curves，并逐步验收。",
    },
    "highlight_protection": {
        "purpose": "确认并保护亮星或目标亮核，避免拉伸后内部结构和星色丢失。",
        "start": "结合高光预览和原始数值范围确认风险，再建立柔和的范围或亮核蒙版。",
        "adjust": "只在已确认的亮部区域增加保护，使用能够恢复层次的最低强度。",
        "software_action": "通过范围蒙版、受保护拉伸或局部 HDR 压缩控制亮部。",
    },
    "star_treatment": {
        "purpose": "在目标安全的前提下控制星点视觉权重，同时保留星点层级、颜色和小星数量。",
        "start": "先完成主体拉伸并确认星点确实压制目标，再测试低强度星点处理。",
        "adjust": "蒙版尺度应参考测得的星点 FWHM；小星消失时先降低强度，不要先扩大半径。",
        "software_action": "使用星点蒙版、可选星点分离和保守形态学或曲线处理。",
    },
    "final_export": {
        "purpose": "保留高精度母版，并生成颜色可预测、没有新增裁切和光晕的展示版本。",
        "start": "先保存全分辨率高位深母版，再复制用于色彩空间转换、缩放和输出锐化。",
        "adjust": "输出锐化必须基于最终像素尺寸和观看介质，并保护平滑背景。",
        "software_action": "保存高位深母版，转换而不是错误指定色彩配置文件，并嵌入输出配置文件。",
    },
}


EVIDENCE_LABELS = {
    "classification.frame_role": "文件角色分类",
    "classification.processing_stage": "处理阶段分类",
    "classification.transfer_state": "线性/非线性状态判断",
    "classification.channel_model": "通道模型",
    "classification.filter": "FITS/XISF 滤镜元数据",
    "classification.object": "FITS/XISF 目标元数据",
    "file.header.WCSAXES": "WCS 信息",
    "file.format": "输入文件格式",
    "statistics.exact_min_ratio": "等于原始最小值的像素比例",
    "statistics.near_min_ratio": "接近原始最小值的像素比例",
    "statistics.exact_max_ratio": "等于原始最大值的像素比例",
    "background.plane.magnitude_across_frame": "低信号背景平面跨画面变化幅度",
    "background.plane.r_squared": "背景平面拟合解释度 R²",
    "background.corner_median_range": "四角背景中位数差异",
    "color.channel_p99_normalized": "各通道 P99 信号",
    "color.background_ratios_to_mean": "背景 RGB 相对比例",
    "noise.background_noise_sigma_normalized": "归一化高通 MAD 噪声估计",
    "noise.block_count": "参与噪声评估的背景分块数量",
    "stars.usable_star_count": "有效星点样本数量",
    "stars.fwhm_major_median_px": "星点长轴 FWHM 中位数",
    "stars.eccentricity_p90": "星点偏心率 P90",
    "stars.position_angle_median_deg": "星点方向角中位数",
    "stars.density_per_megapixel": "每百万像素有效星点密度",
    "clipping.shadow_ratio_le_0_001": "稳健映射暗端占比",
    "clipping.highlight_ratio_ge_0_999": "稳健映射亮端占比",
    "user_context.target_type": "用户提供的目标类型",
    "user_context.target_name": "用户提供的目标名称",
    "user_context.filter": "用户提供的滤镜/通道信息",
}


GENERIC_GUIDANCE = {
    "calibrate_integrate": {
        "steps": ["核对校准帧与亮场的相机模式、增益、偏置、温度、合并方式和光路。", "评估单帧焦点、跟踪、云层、背景和构图。", "叠加后检查高低拒绝图，确认被拒绝的是异常值而不是真实信号。"],
        "parameter_logic": ["根据有效帧数和异常值分布选择拒绝算法。", "没有采集模型依据时，不要擅自优化暗场或归一化通道。"],
        "mask_strategy": ["叠加前不使用图像蒙版，以质量图和拒绝图作为主要验收依据。"],
    },
    "crop_edges": {
        "steps": ["用强预览拉伸检查四边。", "只裁去注册楔形、空白拼接边缘和无效像素。"],
        "parameter_logic": ["使用清除无效数据所需的最小裁切范围。"],
        "mask_strategy": ["无需蒙版；保留有效暗空和目标外围。"],
    },
    "background_review": {
        "steps": ["只在确认的空背景位置放置样本。", "生成最简单的可行模型，但先不应用。", "检查模型图和差分图是否出现目标轮廓。", "确认模型不含天体信号后再执行校正。"],
        "parameter_logic": ["从低复杂度模型开始。", "仅在残差仍连贯且模型始终不含目标结构时提高复杂度。"],
        "mask_strategy": ["排除主体、星晕、暗尘埃、IFN、星系外晕、拼接缝和亮星反射。"],
    },
    "color_calibration": {
        "steps": ["确认 WCS 和采集元数据。", "选择真实或最接近且有依据的相机/滤镜响应。", "使用未饱和孤立恒星完成校色。", "分别检查恒星颜色与空间色度梯度。"],
        "parameter_logic": ["只为获得干净的未饱和孤立恒星样本而调整检测参数。"],
        "mask_strategy": ["排除饱和星、拥挤核心、强星云背景和光学光晕。"],
    },
    "narrowband_mapping": {
        "steps": ["记录每个源通道对应的发射线或滤镜。", "在归一化前分别检查各通道噪声和结构。", "选择并记录显示配色。", "需要时单独处理宽带或窄带恒星层。"],
        "parameter_logic": ["按实测信号质量分配权重，不为强行形成配色而等权弱通道。"],
        "mask_strategy": ["只有源信号确实存在时才使用发射线蒙版或星点蒙版。"],
    },
    "linear_denoise": {
        "steps": ["在线性数据副本上处理。", "使用蒙版保护高信噪结构和恒星。", "保守降低小尺度亮度及色度噪声。", "用 100% 视图和强预览拉伸对比前后。"],
        "parameter_logic": ["只有背景方差下降速度快于真实细节损失时才增加强度。"],
        "mask_strategy": ["保护亮结构，不把连贯细丝、暗尘埃和星系外围当作噪声。"],
    },
    "star_shape_review": {
        "steps": ["比较中心、四角和边缘。", "比较叠加图与代表性单帧。", "判断异常是全局、径向、切向、单侧还是通道相关。"],
        "parameter_logic": ["FWHM 仅作为相对检查尺度，不能直接当作通用反卷积参数。"],
        "mask_strategy": ["排除饱和星、混叠星、衍射芒、星云亮结和拥挤星团核心。"],
    },
    "controlled_stretch": {
        "steps": ["用非破坏预览确定目标效果。", "执行多次小幅永久拉伸。", "每次检查背景、亮核、星点尺寸和颜色。"],
        "parameter_logic": ["当拉伸主要放大噪声或压平亮部时停止。"],
        "mask_strategy": ["仅在确认存在高动态范围亮核时使用柔和核心/高光蒙版。"],
    },
    "highlight_protection": {
        "steps": ["确认亮端占比对应真实高光风险。", "围绕亮核或亮星建立柔和蒙版。", "执行保守压缩或受保护拉伸。"],
        "parameter_logic": ["使用能够恢复内部层次且不产生灰平台的最低强度。"],
        "mask_strategy": ["充分羽化过渡，并排除无关中间调。"],
    },
    "star_treatment": {
        "steps": ["完成主体拉伸后再判断星点是否压制目标。", "按测得星点尺寸建立蒙版。", "测试低强度缩星或星点层处理。", "在 100% 视图检查小星、亮星核心、颜色和目标亮结。"],
        "parameter_logic": ["先降低处理强度，再考虑增大蒙版半径；必须保留星点层级和小星群体。"],
        "mask_strategy": ["紧凑星云亮结或星团成员可能被误判为星点时，必须排除主体。"],
    },
    "final_export": {
        "steps": ["保存全分辨率高位深母版。", "复制并转换到目标色彩空间。", "先缩放，再做输出锐化。", "嵌入配置文件并在色彩管理查看器中检查。"],
        "parameter_logic": ["根据最终像素尺寸和观看介质决定输出锐化。"],
        "mask_strategy": ["保护平滑背景，避免输出锐化放大噪声。"],
    },
}


SOFTWARE_STEPS = {
    "siril": {
        "calibrate_integrate": ["在校准前保持 CFA 数据未去马赛克；校准后使用正确 Bayer Pattern 去马赛克。", "使用序列图评估 FWHM、圆度、背景和注册质量，再叠加入选帧。"],
        "background_review": ["先裁掉无效边缘，再使用 Background Extraction 或 RBF。", "加性天空辉光优先采用减法，并检查生成的背景模型。"],
        "color_calibration": ["使用正确焦距、像元尺寸和中心坐标完成 Plate Solving。", "在线性宽带数据上运行 Photometric Color Calibration。"],
        "narrowband_mapping": ["通过 Channel Extraction、Pixel Math 或 RGB Composition 组合有记录的通道。", "需要自然星色时重组单独校准的恒星层。"],
        "linear_denoise": ["在永久拉伸前使用 Wavelets/Multiscale，并用 Range/Star Mask 保护高信号区域。"],
        "controlled_stretch": ["使用 GHS 控制中间调和高光，Asinh 侧重保护星色，Histogram 直接控制黑位和中间调。"],
        "highlight_protection": ["使用 GHS 保护参数、Range Mask 或 Pixel Math 融合更保守的拉伸版本。"],
        "star_treatment": ["仅在目标允许时使用 StarNet 生成星点/无星层，并检查残留孔洞和光晕后再重组。"],
        "final_export": ["保存 32-bit FITS 母版；需要精修时导出 16-bit TIFF；展示图嵌入目标配置文件。"],
    },
    "pixinsight": {
        "calibrate_integrate": ["使用 WBPP 配置匹配的校准帧和 CFA Pattern。", "通过 SubframeSelector 评估单帧；仅在背景变化确有需要时使用 LocalNormalization。", "检查 ImageIntegration 高低拒绝图。"],
        "background_review": ["先使用 DynamicCrop 清除无效边缘。", "复杂画面优先 DBE 手工采样；ABE 只用于简单场景且同样必须检查模型。", "加性天空辉光使用 Subtraction；疑似渐晕先回查平场，不直接使用 Division。"],
        "color_calibration": ["使用 ImageSolver 确认 WCS。", "在 SPCC 中选择实际或有依据的相机/滤镜响应并检查拟合恒星。"],
        "narrowband_mapping": ["使用统一 STF 参考检查 Hα/OIII/SII。", "通过 PixelMath、ChannelCombination 或 NarrowbandNormalization 建立并记录配色。", "需要自然星色时使用 RGB 或单独校准的星点层。"],
        "linear_denoise": ["使用 RangeSelection 建立保护蒙版。", "MLT 优先处理已测得的小尺度噪声；TGV 注意边缘保护。", "使用 NoiseXTerminator 等外部工具时必须与未处理线性母版对比。"],
        "star_shape_review": ["使用 FWHMEccentricity/SubframeSelector 测量中心和四角。", "使用 AberrationInspector 比较边场几何；需要 PSF 时仅对未饱和孤立星使用 DynamicPSF。"],
        "controlled_stretch": ["STF 仅作预览，检查 linked/unlinked 行为。", "将经过检查的 STF 转入 HistogramTransformation，或使用 GHS 精细控制中间调和高光。", "MaskedStretch 仅在其星点尺寸和对比度取舍适合目标时使用。"],
        "highlight_protection": ["使用 RangeSelection 建立柔和亮核蒙版。", "HDRMultiscaleTransform 的尺度必须匹配目标结构；LHE 应通过保护蒙版局部使用。"],
        "star_treatment": ["先检查 StarNet/StarXTerminator 星点层和无星层残留。", "MorphologicalTransformation 使用 Selection/Amount 混合，结构元素尺度参考 FWHM。", "PixelMath 重组时检查黑圈、核心裁切和星色。"],
        "final_export": ["保存带处理历史的 32-bit XISF 母版。", "使用 ICCProfileTransformation 转换展示副本，Resample 后再输出锐化并嵌入配置文件。"],
    },
    "photoshop": {
        "calibrate_integrate": ["Photoshop 不用于校准、注册和叠加；先在 Siril 或 PixInsight 中准备校准、叠加并尽量完成校色的 16-bit TIFF。"],
        "background_review": ["主要背景建模返回线性天文软件完成。", "仅对已确认的轻微残差使用 Curves 调整图层和大范围柔和亮度蒙版。", "禁止使用仿制图章、修复、内容识别或生成式填充修改天区。"],
        "color_calibration": ["先在外部完成测光校色。", "Photoshop 中仅使用低不透明度 Curves、Selective Color 或 Color Balance 调整已确认的残余偏差。"],
        "narrowband_mapping": ["导入已注册的源通道或有记录的合成图。", "使用 Apply Image、Channel Mixer/Curves 和图层蒙版建立明确配色，不能补造缺失通道。"],
        "linear_denoise": ["优先在导入 Photoshop 前完成线性降噪。", "残余噪声可在 Smart Object 上轻量使用 Camera Raw，并通过亮度蒙版排除星点、细丝和尘埃边缘。"],
        "star_shape_review": ["跟踪、倾斜和场曲诊断应在外部工具完成；不要默认使用变形、绘画或液化把星点强行修圆。"],
        "controlled_stretch": ["在 16-bit 文档中使用多层 Curves 调整图层逐步拉伸。", "锚定黑位，配合亮度蒙版分别处理暗弱主体、中间调和亮核。"],
        "highlight_protection": ["建立亮度选区并羽化，通过 Curves 或 Camera Raw Highlights 轻量压制高光，再用图层不透明度消除过渡痕迹。"],
        "star_treatment": ["优先导入外部生成的准确星点层或蒙版。", "如使用 Minimum，仅作用于星点层并采用最小有效半径，再通过不透明度混合。"],
        "final_export": ["保留分层 16-bit PSD/TIFF 母版。", "使用 Convert to Profile 转换展示副本，缩放后再使用受蒙版保护的 Smart Sharpen/High Pass。"],
    },
}


REQUIRED_INFO = {
    "Confirm whether the file is a calibrated single frame or an integrated master.": "请确认文件是已校准单帧还是已经叠加的母版。",
    "Confirm whether the image is linear or already stretched.": "请确认图像仍为线性数据还是已经完成非线性拉伸。",
    "Provide the target type to activate target-specific safety rules.": "请提供目标类型，以启用对应的真实性和安全规则。",
    "Provide the filter or channel acquisition details.": "请提供滤镜或通道采集信息。",
}

VALUE_LABELS = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
    "qualitative": "定性参数",
    "evidence_bound": "证据约束参数",
    "measured": "已测量",
    "unavailable": "不可用",
    "likely_linear": "很可能为线性数据",
    "stacked_or_integrated": "已叠加/积分",
    "light": "亮场单帧",
    "dark": "暗场",
    "flat": "平场",
    "bias": "偏置场",
    "rgb": "RGB 三通道",
    "mono_or_cfa": "单通道或 CFA",
    "galaxy": "星系",
    "emission_nebula": "发射星云",
    "reflection_nebula": "反射星云",
    "dark_nebula": "暗星云",
    "planetary_nebula": "行星状星云",
    "supernova_remnant": "超新星遗迹",
    "globular_cluster": "球状星团",
    "open_cluster": "疏散星团",
    "wide_field": "宽场星野",
    "generic": "通用流程",
    "siril": "Siril",
    "pixinsight": "PixInsight",
    "photoshop": "Photoshop",
}


TOOL_LABELS = {
    "Background Extraction": "Background Extraction（背景提取）",
    "RBF background extraction": "RBF 背景提取",
    "Background model view": "背景模型视图",
    "StarNet integration when installed": "StarNet（已安装时）",
    "32-bit FITS save": "保存 32-bit FITS",
    "16-bit TIFF export": "导出 16-bit TIFF",
    "Color-managed PNG/JPEG export": "色彩管理 PNG/JPEG 导出",
    "Generated background model": "生成的背景模型",
    "BackgroundNeutralization only when justified": "BackgroundNeutralization（仅在有依据时）",
    "NoiseXTerminator when available": "NoiseXTerminator（已安装时）",
    "RangeSelection mask": "RangeSelection 蒙版",
    "GHS protection": "GHS 高光保护",
    "StarXTerminator when available": "StarXTerminator（已安装时）",
    "PixelMath recomposition": "PixelMath 重组",
    "32-bit XISF master": "32-bit XISF 母版",
    "16-bit TIFF/JPEG export": "16-bit TIFF/JPEG 导出",
    "External astronomy software required": "需要外部天文专用软件",
    "Return to Siril/PixInsight": "返回 Siril/PixInsight 处理",
    "Curves adjustment layer for minor residual only": "Curves 调整图层（仅处理轻微残差）",
    "Large soft luminosity mask": "大范围柔和亮度蒙版",
    "External photometric calibration": "外部测光色彩校准",
    "Color Balance adjustment layers": "Color Balance 调整图层",
    "Precomposed 16-bit channel images": "预先配准合成的 16-bit 通道图像",
    "Channel mixer/Curves": "Channel Mixer/Curves",
    "Layer masks": "图层蒙版",
    "External linear denoise preferred": "优先在外部完成线性降噪",
    "Luminosity mask": "亮度蒙版",
    "100% inspection": "100% 视图检查",
    "External FWHM/eccentricity tools": "外部 FWHM/偏心率测量工具",
    "16-bit document": "16-bit 文档",
    "Levels for diagnostic inspection": "Levels（用于诊断检查）",
    "Luminosity masks": "亮度蒙版",
    "Camera Raw Highlights": "Camera Raw 高光控制",
    "Layer opacity": "图层不透明度",
    "Star layer/mask prepared externally": "外部准备的星点层/蒙版",
    "Minimum filter with Preserve Roundness": "Minimum 滤镜（Preserve Roundness）",
    "Layered 16-bit PSD/TIFF": "分层 16-bit PSD/TIFF",
    "Image Size": "Image Size 缩放",
    "Smart Sharpen/High Pass through mask": "通过蒙版使用 Smart Sharpen/High Pass",
}


def localized_operation(operation):
    operation_id = operation["id"]
    return OPERATION_TEXT[operation_id]


def localized_evidence(evidence):
    path = evidence["path"]
    return EVIDENCE_LABELS.get(path, path)


def localized_guidance(software, operation):
    operation_id = operation["id"]
    source = operation["software_guidance"]
    generic = GENERIC_GUIDANCE[operation_id]
    specific = SOFTWARE_STEPS.get(software, {}).get(operation_id, [])
    return {
        "tools": [TOOL_LABELS.get(item, item) for item in source["tools"]],
        "steps": specific or generic["steps"],
        "parameter_logic": generic["parameter_logic"],
        "mask_strategy": generic["mask_strategy"],
        "checkpoints": CHECKPOINTS[operation_id],
        "failure_signs": FAILURES[operation_id],
    }


def localized_value(value):
    if isinstance(value, str):
        return VALUE_LABELS.get(value, value)
    return value


CHECKPOINTS = {
    "calibrate_integrate": ["叠加母版的背景噪声下降且星形没有恶化。", "拒绝图中没有成片真实目标结构。"],
    "crop_edges": ["无效楔形或空白边缘已清除。", "目标构图和暗弱外围仍完整。"],
    "background_review": ["背景模型只包含平滑的非目标低频成分。", "四角差异改善且没有黑坑、断层或目标边缘损失。"],
    "color_calibration": ["未饱和恒星颜色合理。", "背景色度梯度减弱，同时真实发射颜色未被中和。"],
    "narrowband_mapping": ["通道映射已有明确记录。", "弱通道噪声没有被提升为伪结构，恒星层处理方式明确。"],
    "linear_denoise": ["背景颗粒下降但没有塑料感。", "小星、细丝和尘埃边缘仍保留，未出现分块或色斑。"],
    "star_shape_review": ["星点异常原因得到空间分布或单帧证据支持。", "后续处理没有破坏恒星轮廓和颜色。"],
    "controlled_stretch": ["背景与纯黑分离且没有硬截断。", "亮核保留内部层次，星色仍可见。"],
    "highlight_protection": ["亮核结构可见，蒙版过渡不可见，未饱和星色保留。"],
    "star_treatment": ["主体可读性提高，同时星点层级、颜色和小星数量保持自然。", "没有黑圈、硬边或核心裁切。"],
    "final_export": ["高位深母版完整保留。", "展示版本嵌入配置文件，且没有新增色带、裁切或锐化光晕。"],
}


FAILURES = {
    "calibrate_integrate": ["校准或拒绝后渐晕、辉光、灰尘或固定图样噪声加重。", "拒绝图出现真实星点或目标结构。"],
    "crop_edges": ["裁切损失目标结构、拼接有效区域或真实暗空。"],
    "background_review": ["背景模型出现弧线、尘埃带、星系外晕、IFN 或星云细丝。", "校正后出现黑坑、色彩断层或角落过减。"],
    "color_calibration": ["求解失败、通道被裁切、彩色恒星被洗白或目标预期颜色被破坏。"],
    "narrowband_mapping": ["结果暗示不存在的通道、出现荧光色块，或把噪声变成疑似发射结构。"],
    "linear_denoise": ["小星消失、细丝断裂、尘埃变成塑料感，或出现相关分块。"],
    "star_shape_review": ["美容修正产生非物理圆星、黑圈、核心裁切或双星丢失。"],
    "controlled_stretch": ["黑位裁切增加、亮核变成死白、星点明显膨胀，或暗弱区域主要剩噪声。"],
    "highlight_protection": ["保护区变灰、出现硬 HDR 边界，或与周围结构脱节。"],
    "star_treatment": ["星点变得大小一致且人工化、小星消失，或星云亮结被误处理。"],
    "final_export": ["导出后颜色异常、出现色带，或暗部/高光被裁切。"],
}
