| Applies geometric transforms on images of the sequence given in argument so that they may be superimposed on the reference image, using registration data previously computed (see REGISTER).
| The output sequence name starts with the prefix **"r\_"** unless otherwise specified with **-prefix=** option.
| The registration is done on the first layer for which data exists for RGB images unless specified by **-layer=** option (0, 1 or 2 for R, G and B respectively).
| The output images can be rescaled by passing a **-scale=** argument with a float value between 0.1 and 3.
| 
| Automatic framing of the output sequence can be specified using **-framing=** keyword followed by one of the methods in the list { current \| min \| max \| cog } :
| **-framing=max** (bounding box) will project each image and compute its shift wrt. reference image. The resulting sequence can then be stacked using option **-maximize** of STACK command which will create the full image encompassing all images of the sequence.
| **-framing=min** (common area) crops each image to the area it has in common with all images of the sequence.
| **-framing=cog** determines the best framing position as the center of gravity (cog) of all the images.
| 
| **Image interpolation options:**
| By default, transformations are applied to register the images by using interpolation.
| The pixel interpolation method can be specified with the **-interp=** argument followed by one of the methods in the list **no**\ [ne], **ne**\ [arest], **cu**\ [bic], **la**\ [nczos4], **li**\ [near], **ar**\ [ea]}. If **none** is passed, the transformation is forced to shift and a pixel-wise shift is applied to each image without any interpolation.
| Clamping of the bicubic and lanczos4 interpolation methods is the default, to avoid artefacts, but can be disabled with the **-noclamp** argument.
| 
| **Image drizzle options:**
| Otherwise, the images can be exported using HST drizzle algorithm by passing the argument **-drizzle** which can take the additional options:
| **-pixfrac=** sets the pixel fraction (default = 1.0).
| The **-kernel=** argument sets the drizzle kernel and must be followed by one of **point**, **turbo**, **square**, **gaussian**, **lanczos2** or **lanczos3**. The default is **square**.
| The **-flat=** argument specifies a master flat to weight the drizzled input pixels (default is no flat).
| 
| **Filtering out images:**
| Images to be registered can be selected based on some filters, like those selected or with best FWHM, with some of the **-filter-\*** options.
| 
| 
| Links: :ref:`register <register>`, :ref:`stack <stack>`
