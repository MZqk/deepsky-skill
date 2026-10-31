| Runs the external tool GraXpert in denoising mode.
| 
| The following optional arguments may be provided:
| 
| **-strength=** sets the denoising strength, between 0.0 and 1.0 (default = 0.8);
| **-gpu** sets GraXpert to use a GPU if available (and otherwise fall back to CPU);
| **-ai_batch_size=** sets the batch size for AI operations (denoising and the background removal AI algorithm) (default = 4: bigger batch sizes may improve performance, especially on CPU, but require more memory). The optional argument **-ai_version=** forces a specific version of the AI model. For CPU-only usage the latest models may run very slowly, in which case an older model version such as 2.0.0 may provide a more acceptable balance between performance and runtime. If this argument is omitted, the latest available AI model version is used
