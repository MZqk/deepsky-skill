Image Inspectors
================
Siril has several tools that can help you to analyze your image and tell you 
about the quality of the shot. In particular if your setup has or not optical 
defects.

Tilt
----

The first tool proposed by Siril is the tilt calculation. Sensor tilt occurs 
when the sensor is not orthogonal to the imaging plane: this requires an 
intervention on the optical system.
You can execute this functionality in two different ways. Either via the GUI 
(in the menu :menuselection:`Tools --> Image Analysis --> Show Tilt`) or via the command line. 
The latter even offers an alternative that allows you to calculate the tilt 
over a whole sequence of images for greater accuracy. The following command:

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/tilt_use.rst

   .. include:: ./commands/tilt.rst

will output:

.. code-block:: text

   22:28:13: Running command: tilt
   22:28:13: Findstar: processing for channel 0...
   22:28:15: Stars: 7598, Truncated mean[FWHM]: 3.40, Sensor tilt[FWHM]: 0.31 (9%), Off-axis aberration[FWHM]: 0.39
   
   
In the console are indicated:

* the number of stars used for the measurement
* the average FWHM on the sensor, free of outliers
* the tilt, expressed as the difference between the best and the worst FWHM on 
  the 4 corners of the image with in parenthesis the percentage of tilt 
  deviation (value greater than 10% indicates a tilt problem)
* the aberration, expressed by the difference in FWHM between the stars in the 
  center and the stars on the edges of the sensor

If the number of stars detected is low (<200), the dynamic PSF detection 
parameters allow improvement by adjusting the threshold / radius. In fact, the 
greater the number of stars used in the calculation, the more reliable the 
result of the analysis.

.. warning::
   For the result to make sense, it is preferable to run this command on a sub 
   and not a stacking result. A pre-processed image (just demosaiced for color 
   sensors) is therefore ideal. Moreover, the drawn quadrilateral has its 
   proportions exaggerated, in order to be more visible on the screen. It 
   can’t correspond exactly to reality.

.. figure:: ./_images/inspectors/tilt.png
   :alt: tilt
   :class: with-shadow
   :width: 100%

   Display of the tilt diagram

.. tip:: As well as using the `tilt -clear` command, the tilt diagram can be
   cleared using the :guilabel:`Remove` button in the Dynamic PSF dialog.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/seqtilt_use.rst

   .. include:: ./commands/seqtilt.rst

Aberration Inspector
--------------------

This tool creates a 3x3 mosaic of the image center, corners and edges. This 
makes it easy to compare the shape of the star in different sections of the 
image. You can access this feature in the menu :menuselection:`Tools --> Image Analysis --> Aberration Inspector`. 
You can change the settings of this tool, to modify the size of the panels and 
the window, in the :ref:`preferences <preferences/preferences_gui:Analysis Tools>`.

.. figure:: ./_images/inspectors/aberration_inspector.png
   :alt: dialog
   :class: with-shadow

   Aberration inspector window showing aberration in the stars located in the 
   edges because of the optical system.
   
It is also a very good indicator to know if the image contains a gradient: the 
differences in brightness becoming very visible.

.. figure:: ./_images/inspectors/aberration_inspector2.png
   :alt: dialog
   :class: with-shadow

   Aberration inspector window showing differences in brightness.

.. admonition:: Siril command line
   :class: sirilcommand
  
   .. include:: ./commands/inspector_use.rst

   .. include:: ./commands/inspector.rst
  
