| Stacks the **sequencename** sequence, using options.
| 
| Rejection type:
| The allowed types are: **sum**, **max**, **min**, **med** (or **median**) and **rej** (or **mean**). If no argument other than the sequence name is provided, sum stacking is assumed.
| 
| Stacking with rejection:
| Types **rej** or **mean** require the use of additional arguments for rejection type and values. The rejection type is one of **n[one], p[ercentile], s[igma], m[edian], w[insorized], l[inear], g[eneralized], [m]a[d]** for Percentile, Sigma, Median, Winsorized, Linear-Fit, Generalized Extreme Studentized Deviate Test or k-MAD clipping. If omitted, the default Winsorized is used.
| The **sigma low** and **sigma high** parameters of rejection are mandatory unless **none** is selected.
| Optionally, rejection maps can be created, showing where pixels were rejected in one (**-rejmap**) or two (**-rejmaps**, for low and high rejections) newly created images.
| 
| Normalization of input images:
| For **med** (or **median**) and **rej** (or **mean**) stacking types, different types of normalization are allowed: **-norm=add** for additive, **-norm=mul** for multiplicative. Options **-norm=addscale** and **-norm=mulscale** apply same normalization but with scale operations. **-nonorm** is the option to disable normalization. Otherwise addtive with scale method is applied by default.
| **-fastnorm** option specifies to use faster estimators for location and scale than the default IKSS.
| **-overlap_norm**, if passed, will compute normalization coeffcients on images overlaps instead of whole images (allowed only if **-maximize** is passed).
| 
| Other options for rejection stacking:
| Weighting can be applied to the images of the sequences using the option **-weight=** followed by:
| **noise** to add larger weights to frames with lower background noise.
| **nbstack** to weight input images based on how many images were used to create them, useful for live stacking.
| **nbstars** or **wfwhm** to weight input images based on number of stars or wFWHM computed during registration step.
| **-feather=** option will apply a feathering mask on each image borders over the distance (in pixels) given in argument.
| 
| Outputs:
| Result image name can be set with the **-out=** option. Otherwise, it will be named as **sequencename**\ \_stacked.fit.
| **-output_norm** applies a normalization to rescale result in the [0, 1] range (median and mean stacking only).
| **-maximize** option will use registration data from the sequence to create a stacked image that encompasses all the images of the sequence (applicable to all methods except median stacking).
| **-upscale** option will upscale the sequence by a factor 2 prior to stacking using the registration data (applicable to all methods except median stacking).
| **-rgb_equal** will use normalization to equalize color image backgrounds, useful if PCC/SPCC or unlinked AUTOSTRETCH will not be used.
| **-32b** will override the bitdepth set in Preferences and save the stacked image in 32b.
| 
| 
| Filtering out images:
| Images to be stacked can be selected based on some filters, like manual selection or best FWHM, with some of the **-filter-\*** options.
| 
| 
| Links: :ref:`pcc <pcc>`, :ref:`spcc <spcc>`, :ref:`autostretch <autostretch>`
