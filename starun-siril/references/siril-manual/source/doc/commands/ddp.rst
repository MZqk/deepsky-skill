| Performs a DDP (digital development processing) on the loaded image, as described first by Kunihiko Okano. This implementation is the one described in IRIS.
| 
| It combines a linear distribution on low levels (below **level**) and a non-linear one on high levels.
| It uses a Gaussian filter of standard deviation **sigma** and multiplies the resulting image by **coef**. Typical values for **sigma** are within 0.7 and 2. The level argument should be in the range [0, 65535] for 16-bit images and may be given either in the range [0, 1] or [0, 65535] for 32-bit images in which case it will be scaled automatically
