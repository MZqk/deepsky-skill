| Gets or sets photometry settings, mostly used by SEQPSF. If arguments are provided, they will update the settings. None are mandatory, any can be provided, default values are shown in the command's syntax. At the end of the command, the active configuration will be printed.
| 
| The Aperture size is dynamic unless it is forced. If so, the **aperture** value from the settings is used. If dynamic, the radius of the aperture is defined by the supplied dynamic ratio ("radius/half-FWHM").
| Allowed values for the argument **-dyn_ratio** are in the range [1.0, 5.0]. A value outside this range will automatically set the aperture to the fixed value **-aperture**.
| 
| Gain is used only if not available from the FITS header
| 
| Links: :ref:`seqpsf <seqpsf>`
