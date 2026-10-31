Pixel Math
==========

One of the most powerful tools in Siril is the Pixel Math. It allows you to 
manipulate the pixels of the images using mathematical functions. From simple 
addition or subtraction, to more advanced functions, like MTF, Pixel Math is a 
perfect tool for astronomical image processing.

This page aims to describe the tool entirely, to see detailed examples, please 
refer to the excellent `tutorial <https://siril.org/tutorials/pixelmath/>`_ on 
the site.

.. figure:: ../_images/processing/PixelMath.png
   :alt: dialog
   :class: with-shadow

   Pixel Math dialog box as shown at opening

The window is divided into 5 parts. 

#. The first one, including 3 text zones receiving the mathematical formulas. 
   Only the first one is used if you want to produce a monochrome image.
   Uncheck the :guilabel:`Use single RGB/K expression` button to produce RGB
   output.
#. The second is the variables area with the selection of `Functions`_ and 
   `Operators`_. Each variable is an image that must be loaded beforehand with 
   the + button. You can click on the desired function and/or operator to make 
   it appear in the formula entry to make it appear in the formula entry.
#. The third, the **parameters** field, allows the user to define parameters 
   that are separated by ``,``. For example, if you set parameters with the 
   expression ``factor=0.8, K=0.2``, all the occurences of ``factor`` and 
   ``K`` in the formula above will be replaced by 0.8 and 0.2 respectively.
   ``Ha * factor + OIII * K`` would therefore evaluate to 
   ``Ha * 0.8 + OIII * 0.2``.
   
   .. figure:: ../_images/processing/PM_parameters.png
      :alt: dialog
      :class: with-shadow

      Pixel Math parameters box
   
#. The **output** field is reserved for scaling the image within a given range
   and to enable or not the :guilabel:`Sum exposure time`. This last option 
   gives the user the option of summing or not the exposures of individual 
   images, storing the result in the FITS header.
   One need to expand the frame before using it.

   .. figure:: ../_images/processing/PM_rescale.png
      :alt: dialog
      :class: with-shadow

   Pixel Math rescale box
   
#. Finally, the **presets** area allows the user to reuse previously saved 
   formulas with the button to the right of the formula areas. One need to 
   expand the frame before using it. Double-click on the formula to copy it to 
   the right entry.
   
   .. figure:: ../_images/processing/PM_presets.png
      :alt: dialog
      :class: with-shadow

      Pixel Math presets
   
Usage
*****

.. rubric:: Name of variables

By default it is possible to load 10 images simultaneously. Each image is given
a variable name starting with I followed by a number from 1 to 10. However, if 
the loaded image contains the keyword ``FILTER``, then the value of the latter 
becomes the default variable name. Of course it is always possible to change it
by double clicking on it.

.. figure:: ../_images/processing/change_var.png
   :alt: dialog
   :class: with-shadow

   It is possible to change the name of the variable.

``$T`` is a reference to the current image, meaning the image on which the 
PixelMath operations will be applied.  
   
.. tip::
   It is possible to use the currently loaded image by using the ``$T`` token. 
   Note, however, that unlike other programs, the expression ``$T[i]``, with 
   ``i=0,1,2``, is not recognized.

.. rubric:: Examples

Let's take a monochrome image of galaxies This is a linear data seen through 
the autostretch view.

.. figure:: ../_images/processing/pm_orig.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Original image.
   
The following expression:

.. code-block:: text
   
   iif(Image>med(Image)+3*noise(Image), 1, 0)
   
will produce a star mask.
   
.. figure:: ../_images/processing/pm_mask.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   After the formula above.
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/pm_use.rst

   .. include:: ../commands/pm.rst


Functions
*********

There are two types of functions. Those that apply directly to the pixels and 
those that apply to the entire image (such as the statistics functions).

.. csv-table:: Pixel oriented functions
   :file: pm_px_functions.txt
   :delim: 0x09
   :widths: 10, 40, 70
   :header-rows: 1
   
.. csv-table:: Statistics functions
   :file: pm_im_functions.txt
   :delim: 0x09
   :widths: 10 20 65
   :header-rows: 1


Operators
*********

.. csv-table:: Operators
   :file: pm_operators.txt
   :delim: 0x09
   :widths: 10 20 65
   :header-rows: 1
