Linear Match
============

Linear matching is the process of finding a linear function that matches best 
(in the sense of Least Squares) the intensity of pixels from one image to those 
of a reference image. This is a quick and easy way to balance the histograms of 
different images.

.. figure:: ../_images/processing/linear_match_dialog.png
   :alt: Linear Match dialog
   :class: with-shadow

   Linear match dialog

The :guilabel:`Reference` allows you to pick the reference image.

The :guilabel:`Reject low` and :guilabel:`Reject high` sliders allows to exclude 
pixels values in the left and right tails of the intensities distributions. 
They are defined as quantiles, in the range [0, 1]. For instance, default for high 
is 0.92, meaning that the 8% brightest pixels will be excluded from the fitting 
to find the linear match coefficients.

.. warning::

   The image and reference must be aligned prior to applying a linear match. 
   Otherwise, there is no reason to assume that their pixels intensities are 
   correlated.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/linear_match_use.rst

   .. include:: ../commands/linear_match.rst
