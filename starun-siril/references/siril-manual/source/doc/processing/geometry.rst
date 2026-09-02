Geometry
########

Rotate
======
.. |rotate-acw| image:: ../_images/icons/rotate-acw.svg
.. |rotate-cw| image:: ../_images/icons/rotate-cw.svg

.. rubric:: Rotate 90 degrees

It is possible to rotate the image 90 degrees clockwise and counterclockwise 
with the dedicated menu. Here the rotation is done without interpolation of the
pixels and it is therefore the preferred method if you want to rotate the image
by a multiple of 90 degrees. This feature is also reachable through the icons
|rotate-acw| and |rotate-cw| in the toolbar.

.. rubric:: Rotate&Crop

For a rotation of another angle you have to use the Rotate&Crop tool. It allows
a precise rotation and cropping that can be easily controlled. 

.. figure:: ../_images/processing/Rotate&Crop.png
   :alt: Rotate&Crop
   :class: with-shadow

   Rotate&Crop dialog box showing all settings.
   
Five interpolation algorithms are available:

* Nearest Neighbor
* Bilinear
* Bicubic
* Pixel Area Relation
* Lanczos-4 (Default)

Lanczos-4 is the one that gives the best results. However, if you see artifacts, 
especially stars surrounded by black pixels, then you may want to try others.
However, the button :guilabel:`Interpolation clamping` applies a clamping factor to 
Bicubic and Lanczos-4 interpolation in order to prevent ringing artifacts.

If you don't want the image to be cropped after rotation, then you should 
uncheck the :guilabel:`crop` button. However, the missing areas of the picture will be 
filled with black pixel.

The interest of this tool is that the rotation of the image is represented by 
a red frame, as illutrated in the figure below. In addition, if a selection is 
active, it is possible to change its size and see in real time the framing 
evolve.
   
.. figure:: ../_images/processing/Rotate&Crop_crop.png
   :alt: Crop in Rotate&Crop
   :class: with-shadow
   :width: 100%

   Rotate&Crop dialog box with a active selection. Click to enlarge the figure 
   and see the details better.
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/rotatePi_use.rst

   .. include:: ../commands/rotatePi.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/rotate_use.rst

   .. include:: ../commands/rotate.rst


Mirror
======
.. |mirrorx| image:: ../_images/icons/mirrorx.svg
.. |mirrory| image:: ../_images/icons/mirrory.svg

It is also possible to apply a mirror transformation to the image. Either 
along the x axis or along the y axis. This transformation is also accessible 
via the buttons |mirrorx| and |mirrory| of the toolbar.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/mirrorx_use.rst

   .. include:: ../commands/mirrorx.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/mirrory_use.rst

   .. include:: ../commands/mirrory.rst


Binning
=======

The binning is a special transformation for resampling image. It computes the 
sum or mean of the pixels 2x2, 3x3, ... (depending of the binning factor) of 
the in-memory image (like the analogic binning of CCD camera).

.. figure:: ../_images/processing/bin_dialog.png
   :alt: Bin dialogbox
   :class: with-shadow

   Binning dialog box

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/binxy_use.rst

   .. include:: ../commands/binxy.rst

Resample
========

The resample tool allows to resize the image at the cost of an interpolation 
chosen from the following list:

* Nearest Neighbor
* Bilinear
* Bicubic
* Pixel Area Relation
* Lanczos-4 (Default)

Lanczos-4 is the one that gives the best results. However, if you see artifacts, 
especially stars surrounded by black pixels, then you may want to try others.
However, the button :guilabel:`Interpolation clamping` applies a clamping factor to 
Bicubic and Lanczos-4 interpolation in order to prevent ringing artifacts. 

If you want to change the image ratio, then you should uncheck the :guilabel:`Preserve 
Aspect Ratio` button.

.. figure:: ../_images/processing/resample_dialog.png
   :alt: Resample dialogbox
   :class: with-shadow

   Resample dialog box

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/resample_use.rst

   .. include:: ../commands/resample.rst

   .. include:: ../commands/seqresample_use.rst

   .. include:: ../commands/seqresample.rst
