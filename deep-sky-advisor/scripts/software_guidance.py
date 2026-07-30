"""Structured software-specific playbooks for advice report generation."""


GENERIC = {
    "calibrate_integrate": {
        "tools": ["Calibration", "Registration", "Subframe assessment", "Integration"],
        "steps": [
            "Match calibration frames to the lights by camera mode, gain/offset, temperature, binning, and optical train.",
            "Inspect individual lights for focus, tracking, clouds, background level, and framing before integration.",
            "Inspect low/high rejection maps and confirm that rejected pixels are artifacts rather than target signal.",
        ],
        "parameter_logic": [
            "Choose rejection by usable frame count and outlier distribution.",
            "Do not optimize darks or normalize channels without confirming that the acquisition model supports it.",
        ],
        "mask_strategy": ["Not applicable before integration; use rejection maps as the validation surface."],
    },
    "crop_edges": {
        "tools": ["Crop"],
        "steps": ["Inspect all borders at high contrast.", "Crop only registration wedges, empty mosaic edges, or invalid pixels."],
        "parameter_logic": ["Use the smallest crop that removes invalid data."],
        "mask_strategy": ["No mask; preserve valid dark sky and faint outer structures."],
    },
    "background_review": {
        "tools": ["Background samples", "Low-frequency model", "Difference/model preview"],
        "steps": [
            "Place samples only on verified empty sky.",
            "Generate the simplest plausible model without applying it.",
            "Inspect the model and subtraction difference for target-shaped structures.",
            "Apply only after the model is demonstrably free of astronomical signal.",
        ],
        "parameter_logic": [
            "Start with low model complexity.",
            "Increase complexity only when coherent residuals remain and target structure stays absent from the model.",
        ],
        "mask_strategy": ["Exclude target, halos, dust, IFN, galaxy outskirts, mosaic seams, and bright-star reflections."],
    },
    "color_calibration": {
        "tools": ["Plate solving", "Catalog-constrained color calibration"],
        "steps": [
            "Confirm WCS and acquisition metadata.",
            "Select the actual or closest justified camera/filter response.",
            "Calibrate on unsaturated stars.",
            "Inspect star colors and residual spatial color gradients separately.",
        ],
        "parameter_logic": ["Change detection settings only to obtain a clean sample of unsaturated isolated stars."],
        "mask_strategy": ["Exclude saturated stars, crowded cores, strong nebular background, and optical halos."],
    },
    "narrowband_mapping": {
        "tools": ["Channel inspection", "Pixel math/channel mapping", "Optional separate star layer"],
        "steps": [
            "Document which physical line or filter contributes to each source channel.",
            "Assess each channel's noise and structure before normalization.",
            "Choose a display mapping and record it explicitly.",
            "Handle broadband or narrowband stars separately when required.",
        ],
        "parameter_logic": ["Do not equalize weak channels merely to force a palette; weight according to measured signal quality."],
        "mask_strategy": ["Use emission-line or star masks only when their source signal is demonstrably present."],
    },
    "linear_denoise": {
        "tools": ["Linear noise reduction", "Luminance/range protection mask"],
        "steps": [
            "Work on a duplicate while the data is still linear.",
            "Protect high-SNR structures and stars.",
            "Reduce small-scale luminance and chroma noise conservatively.",
            "Compare before/after at 100% and with an aggressive preview stretch.",
        ],
        "parameter_logic": ["Increase strength only while background variance falls faster than real small-scale structure."],
        "mask_strategy": ["Protect bright structures; avoid treating faint coherent filaments or dust as noise."],
    },
    "star_shape_review": {
        "tools": ["FWHM/eccentricity measurement", "Spatial star map", "Subframe comparison"],
        "steps": [
            "Compare center, corners, and edges.",
            "Compare integrated stars against representative subframes.",
            "Determine whether the pattern is global, radial, tangential, one-sided, or channel-dependent.",
        ],
        "parameter_logic": ["Use measured FWHM only as a relative inspection scale, not as a universal deconvolution setting."],
        "mask_strategy": ["Exclude saturated stars, blends, diffraction spikes, nebular knots, and crowded cluster cores."],
    },
    "controlled_stretch": {
        "tools": ["Screen preview stretch", "Histogram/GHS/Asinh stretch"],
        "steps": [
            "Use a non-destructive preview to establish the desired endpoint.",
            "Apply several small permanent stretches.",
            "Recheck background separation, bright cores, star size, and color after each step.",
        ],
        "parameter_logic": ["Stop when added stretch reveals more noise than coherent signal or begins flattening bright structures."],
        "mask_strategy": ["Use a soft core/highlight mask only when the target has verified high-dynamic-range regions."],
    },
    "highlight_protection": {
        "tools": ["Range/core mask", "HDR compression", "Protected stretch"],
        "steps": [
            "Confirm whether bright-end occupancy corresponds to real clipped or merely bright structures.",
            "Build a soft mask around the verified core or bright stars.",
            "Apply restrained compression or protected stretch.",
        ],
        "parameter_logic": ["Use the lowest strength that restores visible internal structure without creating a gray plateau."],
        "mask_strategy": ["Feather transitions broadly and exclude unrelated midtones."],
    },
    "star_treatment": {
        "tools": ["Star mask", "Optional star separation", "Morphological or curve-based reduction"],
        "steps": [
            "Judge star dominance only after the main stretch.",
            "Build a star mask scaled to measured star size.",
            "Test low-strength reduction or separate star processing.",
            "Inspect small stars, bright-star cores, color, and target knots at 100%.",
        ],
        "parameter_logic": ["Reduce strength before increasing mask radius; preserve star hierarchy and small-star population."],
        "mask_strategy": ["Exclude the target when compact nebular knots or cluster members could be mistaken for stars."],
    },
    "final_export": {
        "tools": ["High-bit-depth master", "Color-space conversion", "Output resize/sharpen", "Display export"],
        "steps": [
            "Save the full-resolution high-bit-depth master.",
            "Convert a copy to the intended output color space.",
            "Resize before output sharpening.",
            "Embed the profile and inspect the exported file in a color-managed viewer.",
        ],
        "parameter_logic": ["Base output sharpening on final pixel dimensions and viewing medium."],
        "mask_strategy": ["Protect smooth background from output sharpening."],
    },
}


OVERRIDES = {
    "siril": {
        "calibrate_integrate": {
            "tools": ["Conversion/Sequences", "Pre-processing", "Registration", "Plot", "Stacking", "Rejection maps"],
            "steps": [
                "Convert the sequence without debayering before calibration.",
                "Calibrate with matched masters, then debayer OSC data with the verified Bayer pattern.",
                "Register and use sequence plots to reject focus, FWHM, roundness, background, and registration outliers.",
                "Stack accepted frames and inspect rejection maps before continuing.",
            ],
        },
        "background_review": {
            "tools": ["Background Extraction", "RBF background extraction", "Background model view"],
            "steps": [
                "Crop invalid borders first.",
                "Place or verify samples away from target, dust, halos, and frame edges.",
                "Start with a smooth, low-complexity model and inspect the model image.",
                "Apply subtraction for additive sky gradients only after visual validation.",
            ],
        },
        "color_calibration": {
            "tools": ["Plate Solving", "Photometric Color Calibration"],
            "steps": [
                "Solve the image using correct focal length, pixel size, and image center.",
                "Run Photometric Color Calibration on linear broadband data.",
                "Inspect unsaturated star colors and background chromatic residuals.",
            ],
        },
        "narrowband_mapping": {
            "tools": ["Channel Extraction", "Pixel Math", "RGB Composition", "Star Recomposition"],
            "steps": [
                "Extract or identify the measured source channels.",
                "Inspect each channel under the same display stretch.",
                "Compose HOO/SHO or another mapping only from documented source channels.",
                "Recombine a separately calibrated star layer when appropriate.",
            ],
        },
        "linear_denoise": {
            "tools": ["Wavelets/Multiscale processing", "Star mask", "Range mask"],
            "steps": [
                "Apply denoise before permanent stretch.",
                "Use a range or star-protection mask.",
                "Adjust fine scales first and keep large-scale structure untouched.",
                "Check the aggressively stretched preview for lost faint signal.",
            ],
        },
        "controlled_stretch": {
            "tools": ["Generalised Hyperbolic Stretch", "Asinh Transformation", "Histogram Transformation"],
            "steps": [
                "Use the preview stretch to choose a target background and highlight behavior.",
                "Use GHS for controlled midtone placement, Asinh when star color needs stronger protection, or Histogram for direct black/midtone control.",
                "Apply several modest iterations and inspect star size and core detail after each.",
            ],
        },
        "highlight_protection": {
            "tools": ["GHS highlight protection", "Range mask", "Pixel Math blend"],
            "steps": [
                "Create a soft range mask around verified bright structures.",
                "Use GHS protection controls or blend a more conservative stretch through the mask.",
                "Check for hard mask boundaries and gray cores.",
            ],
        },
        "star_treatment": {
            "tools": ["StarNet integration when installed", "Star mask", "Morphological/curve adjustment", "Star recomposition"],
            "steps": [
                "Create a starless/stars pair only when the target is safe for separation.",
                "Process the starless layer without allowing residual holes or halos.",
                "Reduce star intensity or size conservatively and recombine at controlled strength.",
            ],
        },
        "final_export": {
            "tools": ["32-bit FITS save", "16-bit TIFF export", "Color-managed PNG/JPEG export"],
            "steps": [
                "Save the linear or processed scientific master as 32-bit FITS.",
                "Export a 16-bit TIFF for further finishing when needed.",
                "Convert and embed the intended display profile before PNG/JPEG delivery.",
            ],
        },
    },
    "pixinsight": {
        "calibrate_integrate": {
            "tools": ["WeightedBatchPreprocessing", "SubframeSelector", "LocalNormalization", "ImageIntegration", "Rejection maps"],
            "steps": [
                "Configure WBPP with matched calibration frames and the verified CFA pattern.",
                "Measure subframes with SubframeSelector and reject documented FWHM, eccentricity, SNR, cloud, or registration outliers.",
                "Use LocalNormalization only when background variation warrants it and inspect reference suitability.",
                "Inspect ImageIntegration rejection maps for removed target signal.",
            ],
        },
        "background_review": {
            "tools": ["DynamicCrop", "DynamicBackgroundExtraction", "AutomaticBackgroundExtractor", "Generated background model"],
            "steps": [
                "DynamicCrop invalid borders first.",
                "Use DBE for controlled manual sampling; reserve ABE for simple fields and always inspect its model.",
                "Start with sparse verified background samples and low function complexity.",
                "Generate the model image and inspect it before accepting subtraction or division.",
            ],
            "parameter_logic": [
                "Use subtraction for additive sky glow.",
                "Treat apparent multiplicative vignetting as a calibration/flat-field issue before considering division.",
            ],
        },
        "color_calibration": {
            "tools": ["ImageSolver", "SpectrophotometricColorCalibration", "BackgroundNeutralization only when justified"],
            "steps": [
                "Solve the linear image and confirm WCS.",
                "Select the actual or justified camera/filter response in SPCC.",
                "Use unsaturated isolated stars and inspect the fit.",
                "Treat residual background gradients separately; do not force real emission neutral.",
            ],
        },
        "narrowband_mapping": {
            "tools": ["ChannelCombination", "PixelMath", "NarrowbandNormalization", "StarNet"],
            "steps": [
                "Inspect H-alpha/OIII/SII channels using a common STF reference.",
                "Normalize only when justified by signal and noise, not to force equal visual weight.",
                "Build the documented palette with PixelMath or ChannelCombination.",
                "Use an RGB or separately calibrated star layer when natural star color is required.",
            ],
        },
        "linear_denoise": {
            "tools": ["MultiscaleLinearTransform", "TGVDenoise", "NoiseXTerminator when available", "RangeSelection mask"],
            "steps": [
                "Build a mask that protects high-SNR target structure and stars.",
                "With MLT, target measured small-scale noise first; with TGV, protect edges and avoid excessive iterations.",
                "If using an external AI denoiser, compare against the untouched linear master and inspect faint structures.",
            ],
        },
        "star_shape_review": {
            "tools": ["FWHMEccentricity", "SubframeSelector", "AberrationInspector", "DynamicPSF"],
            "steps": [
                "Measure center and corners with FWHMEccentricity or SubframeSelector.",
                "Use AberrationInspector to compare field geometry.",
                "Use DynamicPSF only on isolated unsaturated stars when a PSF model is needed.",
            ],
        },
        "controlled_stretch": {
            "tools": ["ScreenTransferFunction", "HistogramTransformation", "GeneralizedHyperbolicStretch", "MaskedStretch"],
            "steps": [
                "Use STF only as a preview and inspect linked versus unlinked channel behavior.",
                "Transfer a checked STF to HistogramTransformation or use GHS for finer midtone/highlight control.",
                "Use MaskedStretch only when its star-size and contrast tradeoffs suit the target.",
                "Apply incremental stretches and inspect black point, bright core, and star color.",
            ],
        },
        "highlight_protection": {
            "tools": ["RangeSelection", "HDRMultiscaleTransform", "LocalHistogramEqualization", "GHS protection"],
            "steps": [
                "Create and soften a range mask around the bright core.",
                "Use HDRMultiscaleTransform only at scales appropriate to the target structure.",
                "Use LHE through a protected mask for local contrast rather than global HDR pressure.",
            ],
        },
        "star_treatment": {
            "tools": ["StarNet", "StarXTerminator when available", "MorphologicalTransformation", "PixelMath recomposition"],
            "steps": [
                "Generate starless and star layers and inspect residuals before processing either layer.",
                "Use MorphologicalTransformation with Selection/Amount rather than full-strength erosion.",
                "Scale the structuring element to measured FWHM.",
                "Recombine with PixelMath while checking black halos, clipped cores, and star color.",
            ],
        },
        "final_export": {
            "tools": ["32-bit XISF master", "ICCProfileTransformation", "Resample", "UnsharpMask/MultiscaleLinearTransform", "16-bit TIFF/JPEG export"],
            "steps": [
                "Save a 32-bit XISF master with processing history.",
                "Convert a copy to the delivery color space with ICCProfileTransformation.",
                "Resample before output sharpening and protect the smooth background.",
                "Embed the profile in TIFF/JPEG output.",
            ],
        },
    },
    "photoshop": {
        "calibrate_integrate": {
            "tools": ["External astronomy software required"],
            "steps": [
                "Do not calibrate, register, or integrate lights in Photoshop.",
                "Prepare a calibrated, integrated, preferably color-calibrated 16-bit TIFF in Siril or PixInsight first.",
            ],
        },
        "background_review": {
            "tools": ["Return to Siril/PixInsight", "Curves adjustment layer for minor residual only", "Large soft luminosity mask"],
            "steps": [
                "Perform substantive gradient modeling in linear astronomy software.",
                "For a verified minor residual, use a reversible Curves layer through a broad soft mask.",
                "Compare against the untouched base and disable the layer to inspect what was removed.",
            ],
            "parameter_logic": ["Do not use Clone Stamp, Healing, Content-Aware Fill, or Generative Fill on the astronomical sky."],
        },
        "color_calibration": {
            "tools": ["External photometric calibration", "Curves", "Selective Color", "Color Balance adjustment layers"],
            "steps": [
                "Complete photometric calibration before Photoshop.",
                "Use per-channel Curves only for documented residual bias.",
                "Use Selective Color or Color Balance at low opacity for finishing, while monitoring stellar colors.",
            ],
        },
        "narrowband_mapping": {
            "tools": ["Precomposed 16-bit channel images", "Apply Image", "Channel mixer/Curves", "Layer masks"],
            "steps": [
                "Import registered source channels or a documented composition.",
                "Assign channels explicitly and record the palette.",
                "Use masks and Curves for aesthetic separation without inventing absent signal.",
                "Recombine a prepared star layer when required.",
            ],
        },
        "linear_denoise": {
            "tools": ["External linear denoise preferred", "Camera Raw Filter", "Smart Object", "Luminosity mask"],
            "steps": [
                "Prefer linear denoising before export to Photoshop.",
                "For residual noise, convert the layer to a Smart Object and apply restrained Camera Raw luminance/chroma reduction.",
                "Mask the effect away from stars, filaments, dust edges, and galaxy outskirts.",
            ],
        },
        "star_shape_review": {
            "tools": ["100% inspection", "External FWHM/eccentricity tools"],
            "steps": [
                "Diagnose tracking, tilt, field curvature, and registration outside Photoshop.",
                "Use Photoshop only to inspect final artifacts; do not warp, paint, or liquify stars as the default fix.",
            ],
        },
        "controlled_stretch": {
            "tools": ["16-bit document", "Curves adjustment layers", "Levels for diagnostic inspection", "Luminosity masks"],
            "steps": [
                "Use several Curves adjustment layers rather than destructive Image Adjustments.",
                "Anchor the black point and lift midtones gradually.",
                "Use luminosity masks to separate faint target, midtones, and bright cores.",
                "Inspect each layer at 100% and toggle it to confirm real improvement.",
            ],
        },
        "highlight_protection": {
            "tools": ["Luminosity mask", "Curves", "Camera Raw Highlights", "Layer opacity"],
            "steps": [
                "Build a luminosity selection for the bright core or stars.",
                "Apply a gentle Curves/highlight reduction through a feathered mask.",
                "Lower layer opacity until transitions disappear.",
            ],
        },
        "star_treatment": {
            "tools": ["Star layer/mask prepared externally", "Minimum filter with Preserve Roundness", "Curves", "Layer opacity"],
            "steps": [
                "Prefer an externally generated star layer or accurate star mask.",
                "If using Minimum, apply it to the star layer only at the smallest effective radius.",
                "Blend with opacity rather than accepting the full filter result.",
                "Inspect faint stars and black rings at 100%.",
            ],
        },
        "final_export": {
            "tools": ["Layered 16-bit PSD/TIFF", "Convert to Profile", "Image Size", "Smart Sharpen/High Pass through mask", "Export"],
            "steps": [
                "Keep a layered 16-bit master.",
                "Convert a copy to the delivery profile rather than assigning a different profile.",
                "Resize before restrained output sharpening.",
                "Export with the profile embedded and inspect for banding.",
            ],
        },
    },
}


def get_software_guidance(software, operation_id):
    base = dict(GENERIC[operation_id])
    override = OVERRIDES.get(software, {}).get(operation_id, {})
    for key, value in override.items():
        base[key] = value
    base["checkpoints"] = []
    base["failure_signs"] = []
    return base
