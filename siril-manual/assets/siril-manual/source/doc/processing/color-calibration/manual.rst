Manual Color Calibration
************************

.. warning::
   The color calibration **must** be performed on a linear image whose 
   histogram has not yet been stretched. Otherwise, the colors obtained are not 
   guaranteed to be correct.
   
The manual way uses the following window:

.. figure:: ../../_images/processing/color_manual_dialog.png
   :alt: dialog
   :class: with-shadow

   Manual Color Calibration dialog window.

The first step deals with the **background** of your image. The goal is to 
equalize the RGB layers in order the background appears as a neutral grey 
color.

After making a selection in your image (in a not so crowdy nor contrasted 
area), the area is taken into account by clicking on the :guilabel:`Use current
selection` button. The coordinates of the rectangle are displayed. Then 
:guilabel:`Background Neutralization` will calculate the median of each channel
and equalize them.

The second step deals with the **bright objetcs** of the picture. You can 
modify once again the histogram in two ways:

* Manually, with **White reference** and the 3 R, G and B coefficients, 
  according to your own taste. 
* Automatically, by selecting a rectangle area with contrasted objects (the 
  same way as previously)

Two sliders allow you to change the rejection limit for too dark and too bright
pixels in the selection.

As this is a trial and error process, you can undo the result with the 
:guilabel:`Undo` button (up left) and then try with other selections or 
coefficients until you are satisfied.
