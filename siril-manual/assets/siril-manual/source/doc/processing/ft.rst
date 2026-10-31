Fourier Transform
#################

A Fourier transform (FT) is a mathematical transform that decomposes functions 
into frequency components, which are represented by the output of the transform
as a function of frequency. This transformation is widely used in imaging 
because it allows to see signals at regular frequencies.

.. admonition:: Theory
   :class: siriltheory
   
   .. rubric:: Fourier transform
  
   The Fourier transform is an analysis process, decomposing a complex-valued 
   function :math:`f(x)` into its constituent frequencies and their amplitudes:
   
   :math:`\hat{f}(\xi) = \int_{-\infty}^{\infty} f(x) e^{-2\pi i \xi x} \, dx`.
   
   .. rubric:: Inverse transform
   
   The inverse process is synthesis, which recreates :math:`f(x)` from its 
   transform:
  
   :math:`f(x) = \int_{-\infty}^{\infty} \hat{f}(\xi) e^{2\pi i \xi x} \, d\xi`.

Siril allows to transform an image in the frequency space thanks to a 
`Fast Fourier Transform <https://en.wikipedia.org/wiki/Fast_Fourier_transform>`_
algorithm. The result is in the form of two images. The first one, 
automatically loaded, contains the magnitude (or modulus) of the transform, the
second one contains the phase. The location of the two images must be entered 
in the **Direct Transform** tab (see illustration below) of the dialog. It is 
then possible to modify the modulus image by removing frequency peaks 
corresponding to unwanted signals. It is important not to forget to save the 
changes.

The :guilabel:`Centered` option, when checked, centers the origin of the Direct 
Fourier Transform. If not, the origin is at the top-left corner.

.. figure:: ../_images/processing/fftd_dialog.png
   :alt: dialog
   :class: with-shadow

   Direct Transform tab.
 
To reconstruct the image, click on the **Inverse Transform** tab and enter the 
filepath of the modulus and phase images.
   
.. figure:: ../_images/processing/ffti_dialog.png
   :alt: dialog
   :class: with-shadow

   Inverse Transform tab.
   
 
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/fftd_use.rst

   .. include:: ../commands/fftd.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/ffti_use.rst

   .. include:: ../commands/ffti.rst
