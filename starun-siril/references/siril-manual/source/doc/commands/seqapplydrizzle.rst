| Applies DRIZZLE to a sequence of images to map the pixel data onto a common reference frame so that they may be superimposed on the reference image, using registration data previously computed (see REGISTER).
| 
| The output sequence name starts with the prefix **"r\_"** unless otherwise specified with **-prefix=** option.
| 
| The image scaling can be set using the **-scale=** argument and the pixel fraction can be set using the **-pixfrac=** argument.
| 
| A master flat image can be specified using the **-flat=** argument and a master bias image or bias value can be specified using **-bias=**. *(Note that the bias value must be specified as in the Calibration tab, e.g. "=1024", so the argument would be "-bias==1024".)*
| 
| The drizzle kernel can be specified with the **-kernel=** argument, which must be followed by one of **point**, **turbo**, **square**, **gaussian**, **lanczos2** or **lanczos3**. The default is **turbo**.
| 
| An output_counts sequence can be generated for use in stacking by providing the **-ocseq** argument (this defaults to FALSE). If generated, the output_counts sequence will have the additional prefix "oc\_".
| 
| Automatic framing of the output sequence can be specified using **-framing=** keyword followed by one of the methods in the list { current \| min \| max \| cog } :
| **-framing=max** (bounding box) adds a black border around each image as required so that no part of the image is cropped when registered.
| **-framing=min** (common area) crops each image to the area it has in common with all images of the sequence.
| **-framing=cog** determines the best framing position as the center of gravity (cog) of all the images.
| 
| Filtering out images:
| Images to be registered can be selected based on some filters, like those selected or with best FWHM, with some of the **-filter-\*** options.
| 
| 
| Links: :ref:`register <register>`
