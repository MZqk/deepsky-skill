Rotational Gradient (Larson Sekanina Filter)
############################################

The rotational gradient, also called `Larson Sekanina filter <http://users.libero.it/mnico/comets/ls.htm>`_, 
is a filter that allows to remove circular structures from an image, to better 
highlight other details. This technique is particularly effective to show the 
jets coming out of the nucleus of a comet.

The principle is quite simple: this image processing consists in subtracting 
two copies of the image from each other, one of the two copies having been 
previously rotated with respect to a point defined in the image.

* If there are circular structures around this point they are not modified by 
  rotation and will disappear after rotation.
* If there are non-circular structures, like jets in the coma, they will be 
  shifted in relation to each other between the two copies and the subtraction 
  will amplify the contrast of this structure in the result.
* If the comet moves in the image, it is possible to add a radial shift.

.. figure:: ../_images/processing/rgradient_dialog.png
   :alt: dialog
   :class: with-shadow

   Dialog box of the Rotational Gradient filter.
   
.. admonition:: Theory
   :class: siriltheory
   
   Starting from an input image, the filter generates two images, each with a 
   radial shift (:math:`dr` in pixels) and a rotational shift (:math:`d\alpha` in 
   degrees) relative to the point (:math:`x_c`, :math:`y_c`). These shifts have the 
   same magnitude but opposite signs between the two images. The two images are 
   then combined to produce the final image. In polar coordinates :math:`(r, a)` 
   with respect to the point :math:`(x, y)` we have:
   
   .. math::

      B'(\alpha,r,d\alpha,dr) = 2 \cdot B(\alpha,r) - B(\alpha-d\alpha, r-dr) - B(\alpha+d\alpha, r-dr)
      
   with :math:`B`: the starting image, :math:`B'`: the resulting image, 
   :math:`dr`: the radial shift and :math:`d\alpha`: the rotational shift
   
In the example below, concerning the comet C/2022 E3, the alignment was 
made on the comet and the stars show important trails. The coma is very 
circular and it is difficult to see details about its activity. Therefore, it
is not necessary to define a radial shift. For the rotation, an angle of
15° was chosen (this choice was made after several attempts and using the undo 
button to go back). To choose the coordinates of the center of rotation, just 
make a selection around the cometary nucleus and click on :guilabel:`Use current selection`. 
This action will copy the coordinates of the centroid to the desired location.

.. figure:: ../_images/processing/rgradient_or.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Image of a comet (C/2022 E3 (ZTF)) showing a coma and a tail (courtesy of 
   Stéphane Garro).
   
A simple click on :guilabel:`Apply` will apply the filter. In our example, a jet 
becomes visible.
   
.. figure:: ../_images/processing/rgradient_fin.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   After applying the filter, we notice a jet emerging from the core.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/rgradient_use.rst

   .. include:: ../commands/rgradient.rst

