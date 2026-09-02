Statistics
==========

This is a documentation for Siril's statistics, given by the graphical user 
interface (GUI) from the menu :menuselection:`Tools --> Image Analysis`) then 
selecting :guilabel:`Statistics...`, or using the 
stat command. Note that when using the GUI, it is possible to draw a selection 
in the loaded image and that when doing so, the statistics are computed on the 
pixels of region.

The option :guilabel:`Per CFA channel` allows you to calculate statistics for 
each R, G and B channel in CFA images, even if the image has not been 
demosaiced.

.. tip::
   When a CFA image is loaded, the selection will be constrained to a minimum
   size of 2x2 for Bayer images and 3x3 for X-Trans images so that there is at
   least one pixel in each Bayer channel within the selection, to ensure that
   it is possible to generate statistics.

Many of these values are measures of `statistical dispersion <https://en.wikipedia.org/wiki/Statistical_dispersion>`_.

.. figure:: ./_images/statistics/statistics_gui.png
   :alt: 1-chan statistics for CFA image
   :class: with-shadow
   :width: 100%
   
   One channel statistics for CFA image. The given values are not really
   relevant in this case.
   
.. figure:: ./_images/statistics/statistics_gui_cfa.png
   :alt: 3-chan statistics for CFA image
   :class: with-shadow
   :width: 100%
   
   Three channel statistics for CFA image.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/stat_use.rst

   .. include:: ./commands/stat.rst
   

Estimators
**********

Mean
~~~~

This is the `arithmetic mean 
<https://en.wikipedia.org/wiki/Arithmetic_mean>`_, also known as average or 
arithmetic average. 
This is computed by doing the sum of the pixel values divided by the number of 
pixels in an image channel.

Median
~~~~~~

The `median <https://en.wikipedia.org/wiki/Median>`_ is the value separating 
the higher half from the lower half of a dataset. Generally, it represents the 
value of the background of an astronomical image.

Sigma
~~~~~

Also known as the 
`standard deviation <https://en.wikipedia.org/wiki/Standard_deviation>`_, 
noted :math:`\sigma`, this is a measure of dispersion of the image pixels 
based on squared differences from the average. The sigma value of a sub image 
containing only the background will represent the noise of the image.

Background noise
~~~~~~~~~~~~~~~~

This estimator is available by the GUI from the menu 
:menuselection:`Tools --> Image Analysis --> Noise estimation`, and is also displayed 
at the end of stacking.

This is a measure of estimated noise in image background level, for pixels 
having a value low enough to be considered as background. It is an iterative 
process based on k.sigma (a factor of the standard deviation above the 
median), so there is no fixed threshold for the low enough.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/bgnoise_use.rst

   .. include:: ./commands/bgnoise.rst
   
avgDev
~~~~~~

The Average Deviation, also called AAD for `average absolute deviation <https://en.wikipedia.org/wiki/Average_absolute_deviation>`_ 
or mean absolute deviation. In order to understand what the average deviation 
is, one needs to understand what the term absolute deviation is. Absolute 
deviation is the distance between each value in the dataset and that dataset's 
mean (in this instance) or median (for MAD below). Taking all of these 
absolute deviations, finding the average, and the mean average deviation is 
computed. To simplify, if standard deviation is the squared deviation from the 
mean, this is the linear version of it.

MAD
~~~

The `Median Absolute Deviation <https://en.wikipedia.org/wiki/Median_absolute_deviation>`_ 
is a robust measure of how spread out a set of data is. The absolute deviation 
and standard deviation are also measures of dispersion, but they are more 
affected by extremely high or extremely low values. It is similar to the 
average deviation above, but relative to the median instead of the mean.

BWMV
~~~~

The `biweight midvariance <https://en.wikipedia.org/wiki/Robust_measures_of_scale#The_biweight_midvariance>`_ 
is yet another tool to measure dispersion of a dataset, even more robust than 
others cited above to outliers. It discards the data points too far way from 
the median and computes a weighted variance, weights decreasing as the data 
points are further way from the median. The estimator of dispersion is the 
square root (marked as :math:`\sqrt{BWMV}`) of this value.

Location and Scale
~~~~~~~~~~~~~~~~~~

`These parameters <https://en.wikipedia.org/wiki/Location%E2%80%93scale_family>`_, 
often colloquially called scale and offset, are not displayed in the user 
interfaces but are computed internally by Siril. In order to align the 
histograms of the different images for normalization before stacking, one 
needs to compute where they are in terms of level and how wide they are in 
terms of spread. A valid estimator of location could be taken as the median 
while the MAD or the :math:`\sqrt{BWMV}` could be used for scale. However, 
in order to give more robustness to the measures, the pixels more than :math:`6\times \text{MAD}` 
away from the median are discarded. On this clipped dataset, the median and 
:math:`\sqrt{BWMV}` are re-computed and used as location and scale estimators 
respectively. They are computed relative to the reference image of a sequence 
in Siril.
