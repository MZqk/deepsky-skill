Edge Preserving Filters
#######################

Siril offers two edge preserving filters: OpenCV's implementation of the
Bilateral Filter, and a Guided Filter. Both of these filters can be used to
reduce noise, and both preserve sharp edges and strong gradients in the image.
The bilateral filter acts on a single image whereas the guided filter filters
the image using a guide image to identify structures. In the simplest use, the
guided filter acts in a self-guided manner by using the input image as the
guide image.

.. figure:: ../_images/processing/epf_dialog.png
   :alt: dialog
   :class: with-shadow

   Median dialog window.
   
The layout of the window dialog is quite simple and few settings are available.

* **Filter type**: Choose between the bilateral filter and the guided filter.
* **Diameter**: This defines the size of the filter kernel that will be used.
  When using the bilateral filter, setting a diameter of 0 will cause the
  filter kernel size to be set automatically based on the spatial sigma value.
  When using the guided filter this value must be set: a diameter of 0 will
  result in no filtering being done.
* **Sigma (spatial)**: This defines the spatial extent of the filter kernel. A
  larger value results in smoothing of noise over a greater image area, but is
  slower to compute. A smaller value results in more local smoothing and is
  faster to compute. Defaults are not set for the sigma values as the
  appropriate value may depend significantly on image characteristics, but it
  can be good to start with both sigma (spatial) and sigma (intensity) set to
  about 11.
* **Sigma (intensity)**: This defines the range of intensity variation to which
  the filter responds. A high value results in stronger filtering of noise but
  may result in loss of genuine detail; a low value reduces the filtering of
  noise, but will avoid loss of details with gentler gradients.
* **Guide image**: This allows selection of an image to use as a guide image
  when performing a guided filter. If the "self guided" check box is checked,
  the filter acts in self-guided mode using the input image as the guide image.
* **Modulation**: In Siril, modulation is a parameter between 0 and 1 mixing 
  the original and processed images. A value of 1 keeps only the processed
  image, a value of 0 does not apply any edge-preserving filter at all.
  
.. figure:: ../_images/processing/epf.webp
   :alt: shows the effect of changing the parameters
   :class: with-shadow

   Showing the effect of changing the bilateral filter parameters. Note that the
   exact values required will depend on the noise characteristics of your data.

.. tip:: The parameters to the two different filter types do not behave
   identically, so when changing between bilateral and guided filters with
   sigma parameters set, you should expect to see some change in the preview
   result. The code applies a bit of compensation to the parameters provided to
   minimize the difference in behaviour of the two filters for the same input
   parameters but this is not exact (nor is it intended to be).

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/epf_use.rst

   .. include:: ../commands/epf.rst
