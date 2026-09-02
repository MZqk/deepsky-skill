Current list of Python scripts for Siril 1.4
============================================

This page is the first shot at getting a list of Python scripts that is
somewhat intelligible and searchable. It was done manually and has only a very
succinct description for each script, but will be improved with more words for
each with time.


Image processing section
------------------------

Scripts that process a single image for visual enhancements, in general a stack result.

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - SyQon/Prism.py
     - SyQon Prism AI denoise. This is a paying product
   * - SyQon/Starless.py
     - SyQon Starless AI star removal. This is a paying product
   * - SyQon/Parallax.py
     - SyQon Parallax AI: aberration correction, star reduction and sharpening.
       This is a paying product
   * - SyQon/SyQon_Studio.py
     - New SyQon script that will group all paid models (Parallax, Prism, Starless)
   * - RC-Astro/NoiseXTerminator.py
     - RC Astro AI denoise. This is a paying product
   * - RC-Astro/StarXTerminator.py
     - RC Astro Starless AI star removal. This is a paying product
   * - RC-Astro/BlurXTerminator.py
     - RC Astro Deconv AI: aberration correction, star reduction and sharpening.
       This is a paying product
   * - processing/Statistical_Stretch.py
     - Seti Astro Statistical Stretch
   * - processing/AberrationRemover.py
     - Aberration Remover AI
   * - processing/AstroColorMixer.py
     - multi-pass, hue-targeted color and luminance refinement for nonlinear RGB data.
   * - processing/ContinuumSubtraction.py
     - Narrowband Continuum Subtraction script
   * - processing/DeepSNR.py
     - DeepSNR AI denoise (wrapper around the third-party DeepSNR tool)
   * - processing/StarNet.py
     - StarNet 2.5 star removal (wrapper around the third-party StarNet tool),
       with optional star mask generation
   * - processing/GPS_Process.py
     - Image processing based on CosmicClarity and GraXpert (background extraction,
       denoise and/or sharpening)
   * - processing/Narrowband_Palette_Picker.py
     - Helps preview different palettes for narrowband image composition
   * - processing/GraXpert-AI.py
     - Integration of the GraXpert gradient removal tool (AI)
   * - processing/CosmicClarity_Denoise.py
     - Cosmic Clarity Denoise
   * - processing/DBXtract.py
     - Extract the Sulfur II, Hydrogen Alpha and Oxygen III signal from dual band filters
       to compose SHO images in color cameras
   * - processing/Hubble_Palette_from_Dual-Band_OSC.py
     - Create different "Hubble-like" palettes from OSC (One-Shot Color) images acquired
       with a dual-band Ha/OIII filter
   * - processing/PalettePicker.py
     - Script that help user to create its own palette for narrowband filters
   * - processing/NarrowbandNormalization.py
     - Script that allows easy narrowband normalization on monochrome SHO combined images and on OSC dual band images
   * - processing/HDR_multiscale.py
     - Wavelet-Based Dynamic Range Compression
   * - processing/HDR_Blender.py
     - Inverse Variance Maximum Likelihood Estimate (MLE) HDR Blender. Blends
       multiple images with different exposures
   * - processing/NB_2_RGB.py
     - Monochrome image combination into a color image
   * - processing/SCUNet_Denoise.py
     - SCUNet image denoiser (AI)
   * - processing/CosmicClarity_Superres.py
     - Cosmic Clarity Superres
   * - processing/CosmicClarity_Native.py
     - Cosmic Clarity AI-powered sharpening, denoising, super resolution and star removal
   * - processing/AutoBGE.py
     - Auto Background Extraction script
   * - processing/AutoGradientRemoval.py
     - Auto Gradient Removal tool. This script will be implemented in the Siril code in the next version
   * - processing/CosmicClarity_Satellite.py
     - Cosmic Clarity Satellite Removal
   * - processing/CosmicClarity_Darkstar.py
     - Cosmic Clarity Darkstar
   * - processing/ER-Bill_Star_Reduction.py
     - Script for reducing stars using pixel math
   * - processing/DSA-Star_Reduction.py
     - Script for reducing stars using pixel math
   * - processing/CosmicClarity_Sharpen.py
     - Cosmic Clarity sharpening process
   * - VeraLux/VeraLux_Alchemy.py
     - Linear-Phase Narrowband Normalization & Mixing
   * - VeraLux/VeraLux_Nox.py
     - Physically-Faithful Photometric Gradient Reduction
   * - VeraLux/VeraLux_StarComposer.py
     - High-Fidelity Star Reconstruction Engine
   * - VeraLux/VeraLux_Revela.py
     - Photometric Local Contrast & Texture Engine
   * - VeraLux/VeraLux_HyperMetric_Stretch.py
     - Photometric Hyperbolic Stretch Engine
   * - VeraLux/VeraLux_Starting_Point.py
     - Interactive Workflow Guide & Manual
   * - VeraLux/VeraLux_Curves.py
     - Spline-Based Photometric Sculpting Engine
   * - VeraLux/VeraLux_Silentium.py
     - Linear-Phase Noise Suppression Engine
   * - VeraLux/VeraLux_Vectra.py
     - Vector Color Grading & Chromatic Surgery Engine

Sequence pre-processing section
-------------------------------

Scripts that process a set of images, an alternative to usual preprocessing scripts.

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - preprocessing/AMSP.py
     - Siril Wizard – Automatic Multi-Session Processing. Drag and drop all files and it does the stacks
   * - preprocessing/StorageFriendlyStacking.py
     - Storage Friendly Stacking Script
   * - preprocessing/Naztronomy-Smart_Telescope_PP.py
     - Naztronomy - Smart Telescope Preprocessing script
   * - preprocessing/GPS_Preprocess.py
     - Preprocessing with multi-session, mosaics and many optional steps support
   * - preprocessing/ER-CometStartrail.py
     - Stack a startrail from a sequence
   * - preprocessing/osc-multi-night-stacking-v1.2.py
     - Multi-night stacking
   * - preprocessing/Naztronomy-OSC_PP.py
     - Naztronomy - OSC Image Preprocessing script with mosaics support
   * - preprocessing/Naztronomy-Mono_PP.py
     - Naztronomy - Monochrome Image Preprocessing script with mosaics support


Utility section
---------------

Scripts that provide some tool for data analysis or other non-processing related tasks.

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - core/Siril_Catalog_Installer.py
     - Siril Catalog Installer
   * - core/GPU_Manager.py
     - A GUI tool for managing ONNX, PyTorch, and JAX installations
   * - utility/Selected_Star_Spectrum.py
     - Plots the Gaia DR3 continuous spectrum for any selected Gaia DR3 source
   * - utility/RegistrationInspector.py
     - Displays the frames of selected images to check framing
   * - utility/SuperStack.py
     - Performs superstacking (moving average)
   * - utility/plot3D.py
     - Plots the current image or selection in 3D using matplotlib
   * - utility/Sequence_Statistics_Analyzer.py
     - Analyze frames and plot key statistics
   * - utility/Workflow_Companion.py
     - Deep Space Astro drag-and-drop workflow companion: queue, reorder and launch
       scripts and Siril functions
   * - utility/Workflow_Summarizer.py
     - Uses Google Gemini to summarize the Siril log into readable workflow documentation
   * - utility/Blink_Browse_Filter_Sort.py
     - Image browser / filter / sorter with adaptive caching
   * - utility/Svenesis-GradientAnalyzer.py
     - Gradient Analyzer
   * - utility/Svenesis-MultipleHistogramViewer.py
     - Multiple Histogram Viewer
   * - utility/Svenesis-CosmicDepth3D.py
     - CosmicDepth 3D
   * - utility/Patch_Inpainting_Tool.py
     - Corrects defects and creates a star mask
   * - utility/Svenesis-AnnotateImage.py
     - Full size image annotation
   * - utility/AF_Multi_Crop.py
     - Multi-crop script
   * - utility/Svenesis-BlinkComparator.py
     - Blink Comparator
   * - utility/Satellite_Trail_Removal.py
     - Remove satellite trails
   * - utility/Sequence_Deleter.py
     - GUI to delete sequences
   * - utility/ImageWindow.py
     - Pseudo-MDI GUI for storing and swapping images
   * - utility/Dwarfium_Archive_Selector.py
     - Prepare sessions from Dwarf telescopes
   * - utility/AstroT3kFetch.py
     - Fetch and classify astrophotography frames
   * - utility/Diffraction_Spike_Overlay.py
     - Add diffraction spikes
   * - utility/Flat_On_Flat_Analyzer.py
     - Analyze flat field effectiveness
   * - utility/Distortion3D.py
     - Plot 3D distortion map
   * - utility/Autocrop.py
     - Autocrop stacked images
   * - utility/Signature_Tool.py
     - Insert a signature/logo
   * - utility/Galaxy_Annotations.py
     - Create galaxy annotations from Simbad queries
   * - utility/Asteroid_Comet_Finder.py
     - Search for asteroids and comets
   * - utility/AutoStretch_Preview.py
     - Interactive AutoStretch preview
   * - utility/HertzsprungRussell.py
     - Create a Hertzsprung-Russell diagram
