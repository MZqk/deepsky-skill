| Computes a synthetic background gradient using either the polynomial function model of **degree** degrees or the RBF model (if **-rbf** is provided instead) and subtracts it from the image.
| The number of samples per horizontal line and the tolerance to exclude brighter areas can be adjusted with the optional arguments. Tolerance is in MAD units: median + tolerance \* mad.
| Dithering, required for low dynamic gradients, can be enabled with **-dither**.
| For RBF, the additional smoothing parameter is also available. To use pre-existing background samples (e.g. if you have set background samples using a Python script) the **-existing** argument must be used
