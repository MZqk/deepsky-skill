Other Color Tools
#################

Color Saturation
================

This tool is used to increase the color saturation of the image. It is possible
to choose between a specific hue or the global hue to enhance. The strength of 
the saturation is adjusted with the slider :guilabel:`Amount`.

The :guilabel:`Background factor` slider sets the factor multiplied by the 
background value. Lower is the value, stronger is the saturation effect. While 
a high value will preserve the background.

.. figure:: ../_images/processing/color_saturation_dialog.png
   :alt: dialog
   :class: with-shadow

   Color Saturation dialog window.

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/satu_use.rst

   .. include:: ../commands/satu.rst
   
Remove Green Noise
==================

Because green is not naturally present in deep sky images (except for comets 
and some planetary nebulae), if the image has already been calibrated, its 
colors are well balanced and the image is free of any gradient, we can assume 
that if the image contains green, it belongs to the noise. It is then 
interesting to find a method to remove this dominant green. This is exactly 
what the Remove Green Noise tool proposes, which is derived from the 
Subtractive Color Noise Reduction tool, but for green only.

.. figure:: ../_images/processing/scnr.png
   :alt: dialog
   :class: with-shadow

   Remove Green Noise dialog window.
   
.. warning ::
   This tool is not intended for direct use on a typical green image from a 
   stack where the background sky level has not been equalized. Its use in such 
   conditions would destroy the image's chrominance.
   
.. warning ::
   This tool is designed to be used on non-linear images, as it operates in a 
   non-linear color space. Make sure the histogram has been stretched before 
   using this tool to avoid incorrect processing results.
   
This tool has 3 settings. The protection method, the amount (called :math:`a`
in the following section), and a :guilabel:`Preserve lightness` button. The 
following methods present the different existing ways to remove the green 
pixels by replacing them with a mix of Red and Blue. The amount is only 
available for methods with mask protection. The choice of its value must be 
done with caution in order to minimize the rise of the magenta cast in the sky 
background. Do not hesitate to use the :guilabel:`Undo` and :guilabel:`Redo` 
buttons in order to fine-tune the value.
   
Protection method
*****************

.. rubric:: Maximum Mask Protection

.. math::

   m &= \text{max}(R,B) \\
   G'&= G\times (1 — a)\times (1 — m) + m\times G

.. rubric:: Additive Mask Protection

.. math::
  
   m &= \text{min}(1,R+B) \\
   G'&= G\times (1 — a)\times (1 — m) + m\times G

.. rubric:: Average Neutral Protection (default method)

.. math::
  
   m &= 0.5\times (R + B) \\
   G'&= \text{min}(G, m)

.. rubric:: Maximum Neutral Protection

.. math::
  
   m &= \text{max}(R,B) \\
   G'&= \text{min}(G, m)
   
Finally, the :guilabel:`Preserve lightness` button preserves the original CIE L* 
component in the processed image, in order to only process chromatic component, 
it is highly recommended to let this option checked.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/rmgreen_use.rst

   .. include:: ../commands/rmgreen.rst
   
Negative Transform
==================

.. |negative-icon| image:: ../_images/icons/neg.svg

Negative transformation refers to subtracting pixel values from :math:`(L - 1)`,
where :math:`L` is the maximum possible value of the pixel, and replacing it 
with the result.

The :guilabel:`Negative transformation` tool is different from the negative 
view |negative-icon| in the toolbar. Indeed, the transformation is not 
only visual, but actually applied to the pixel values. If you save the image, 
it will be saved as a negative.

.. figure:: ../_images/processing/negative_or.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Original image with weak signal (Image Cyril Richard).
   
.. figure:: ../_images/processing/negative_neg.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Negative image where the signal is more visible (Image Cyril Richard).
   
.. tip::
   A common use of the negative transformation tool is to remove the magenta 
   cast from SHO images. In this case one need to apply :guilabel:`Negative transformation`,
   then :guilabel:`Remove Green Noise`, then :guilabel:`Negative transformation`
   again.
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/neg_use.rst

   .. include:: ../commands/neg.rst

