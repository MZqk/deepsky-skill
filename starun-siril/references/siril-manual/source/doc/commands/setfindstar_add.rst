The threshold for star detection is computed as the median of the image (which
represents in general the background level) plus k times sigma, sigma being the
standard deviation of the image (which is a good indication of the noise
amplitude). If you have many stars in your images and a good signal/noise
ratio, it may be a good idea to increase this value to speed-up the detection
and false positives.

It is recommended to test the values used for a sequence with Siril's GUI,
available in the dynamic PSF toolbox from the analysis menu. It may improve
registration quality to increase the parameters, but it is also important to be
able to detect several tens of stars in each image.
