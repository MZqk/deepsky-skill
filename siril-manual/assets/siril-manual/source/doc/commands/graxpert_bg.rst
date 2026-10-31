| Runs the external tool GraXpert in background extraction mode.
| 
| The following optional arguments may be provided:
| 
| **-algo=** sets the background removal algorithm and must be one of **ai**, **rbf**, **kriging** or **spline**;
| **-mode=** sets the background extraction mode and must be one of **sub** or **div**;
| **-kernel=** sets the RBF kernel and must be one of **thinplate**, **quintic**, **cubic** or **linear**;
| **-pts_per_row=** sets the number of points per row on the background sampling grid (default = 15);
| **-samplesize=** sets the sampling box size for each sample (default = 25);
| **-splineorder=** sets the spline order for use with the spline algorithm (default = 3);
| **-bgtol=** sets the background tolerance between -2.0 and 6.0 (default 2.0);
| **-smoothing=** sets the amount of background smoothing (default = 0.5);
| **-keep_bg** sets GraXpert to save the indicative background image;
| **-cpu** sets GraXpert to use CPU only;
| **-gpu** sets GraXpert to use a GPU if available (and otherwise fall back to CPU);
| **-ai_batch_size=** sets the batch size for AI operations (denoising and the background removal AI algorithm) (default = 4: bigger batch sizes may improve performance, especially on CPU, but require more memory). The optional argument **-ai_version=** forces a specific version of the AI model. Note that GraXpert AI background removal is comparatively fast anyway so at present there is little need to specify an older model for speed reasons even if running in CPU-only mode. If this argument is omitted, the latest available AI model version is used
