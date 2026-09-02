Median Filter
#############

The median represents the middle data point that half of data is smaller and 
half of data larger than this point. This is a robust estimator to remove 
outliers from a data set. Consequently, this tool can be useful as a naive 
denoiser, effective against :ref:`impulse noise <processing/denoising:Image Noise>`. 

.. figure:: ../_images/processing/median_dialog.png
   :alt: dialog
   :class: with-shadow

   Median dialog window.
   
The layout of the window dialog is quite simple and few settings are available.

* **Kernel size**: From :math:`3\times 3` to :math:`15\times 15`, this defines
  the size of a squared kernel that is used to apply the filter. The larger the
  kernel, the more blurred the result will be.
* **Iterations**: This defines the number of passes of the kernel.
* **Modulation**: In Siril, modulation is a parameter between 0 and 1 mixing 
  the original and processed images. A value of 1 keeps only the processed
  image, a value of 0 does not apply any median filter at all.
  
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/fmedian_use.rst

   .. include:: ../commands/fmedian.rst
