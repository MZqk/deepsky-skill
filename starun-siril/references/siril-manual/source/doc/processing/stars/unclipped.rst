Desaturate Stars
================

When a star finder is applied to an image (whose data are always linear), 
ellipses are displayed around the stars. When an ellipse is magenta, it 
means that the star is saturated.

A saturated star is a star whose brightest pixels have no more information and 
are clipped to the maximum value. In general we try to not to saturate the
stars, even if this is not possible for the brightest. If despite the precautions 
there are still saturated stars, Siril has an algorithm that will reconstruct 
the profile of the star taking into account the results of the adjustment made 
during the findstar. 

.. |starfinder| image:: ../../_images/icons/starfinder.svg

First, you need to perform a star detection, either with the :ref:`findstar <findstar>`
command or the |starfinder| button of the :ref:`Dynamic PSF <Dynamic-PSF:Dynamic PSF>`
Window. Then, the desaturate tool is found in :menuselection:`Star Processing --> Desaturate Stars`.

.. tip::
   We recommend using a Moffat profile in the :ref:`Dynamic PSF 
   <Dynamic-PSF:Dynamic PSF>` window to get better parameters.
   
.. warning::
   It is important to run this tool on linear images, otherwise the stars will 
   not have a Gaussian/Moffat profile and the calculations will be invalid.
   
.. figure:: ../../_images/processing/stars/saturated.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   A star detection shows all stars found by Siril. Magenta ellipses are for
   saturated stars. The image is displayed in autostretch view: data are still
   linear.
   
After clicking on the tool, Siril switches to the console and displays the 
results of the current process:
   
.. code-block:: text

   22:26:17: Star synthesis (desaturating clipped star profiles): processing...
   22:26:17: Findstar: processing for channel 0...
   22:26:21: Star synthesis: desaturating stars in channel 0...
   22:26:21: Star synthesis: 70 stars desaturated
   22:26:21: Remapping output to floating point range 0.0 to 1.0
   22:26:21: Execution time: 4.09 s
   
It is necessary to run a star detection again to see the changes.
   
.. figure:: ../../_images/processing/stars/unsaturated.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   After a desaturate processing, no more magenta ellipses are visible. All
   stars have been reconstructed. The image is displayed in autostretch view: 
   data are still linear.
   
.. figure:: ../../_images/processing/stars/desat.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Comparison for a star before and after the application of the desaturation 
   tool.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../../commands/unclipstars_use.rst

   .. include:: ../../commands/unclipstars.rst
   
   
