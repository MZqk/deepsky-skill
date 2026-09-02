| Runs the external tool GraXpert in deconvolution mode.
| 
| The following optional arguments may be provided:
| 
| **-strength=** sets the deconvolution strength, between 0.0 and 1.0 (default = 0.5) and **-psfsize=** the estimate of the FWHM of the image, range between 0.0 and 14.0 pixels (default = 5.0);
| **-gpu** sets GraXpert to use a GPU if available (and otherwise fall back to CPU);
| **-ai_batch_size=** sets the batch size for AI operations (denoising and the background removal AI algorithm) (default = 4: bigger batch sizes may improve performance, especially on CPU, but require more memory). The optional argument **-ai_version=** forces a specific version of the AI model. If this argument is omitted, the latest available AI model version is used
