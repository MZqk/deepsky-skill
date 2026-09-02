Cosmetic Correction
###################

In Siril, the cosmetic correction is the step that gets rid of hot and cold 
pixels in the image. It is usually done during pre-processing using the 
master-dark. This is because the latter usually contains a good map of the 
defective pixels and it is easier to find them on it. However, when you don't 
have a master-dark, Siril offers an alternative with an automatic detection 
algorithm of these pixels in a light image.

.. figure:: ../_images/processing/cc_dialog.png
   :alt: dialog
   :class: with-shadow

   Dialog window of Correction Cosmetic tool.
   
The dialog window contains several parameters necessary for the proper 
functioning of the tool. However, using the default settings usually gives good
results.

* **Cold Sigma**: How many times (in average deviation units) a pixel value 
  must differ from the value of surrounding neighbors to be considered as a 
  cold pixel.
* **Hot Sigma**: How many times (in average deviation units) a pixel value 
  must differ from the value of surrounding neighbors to be considered as a 
  hot pixel.
* **Amount**: This is a modulation parameters where 0 means no correction and
  1, 100% corrected.
* **CFA**: This option needs to be checked for CFA images with Bayer pattern.
  It does not work for X-Trans sensor.
  
This operation can be applied to sequences. Open a sequence and prepare the 
settings you want to use, then check the :guilabel:`Apply to sequence` button and 
define the output prefix of the new sequence (``cc_`` by default).

.. admonition:: Theory
   :class: siriltheory
      
   .. rubric:: Hot pixels detection

   Let's call :math:`m_{5\times 5}` the median of the 5 nearest neighbors. If the 
   pixel value is greater than 

   .. math::
      m_{5\times 5}+\text{max}(\text{avgDev}, \sigma_\text{high}\times \text{avgDev}),
   
   with avgDev, the :ref:`Average Deviation <Statistics:avgDev>` of the whole image.

   Then the pixel is replaced by the average of the :math:`3\times 3` pixels: 
   :math:`a_{3\times 3}`, but only if 

   .. math::
      a_{3\times 3} < m_{5\times 5}+\frac{\text{avgDev}}{2}.
   
   .. rubric:: Cold pixels detection

   If the pixel value is less than

   .. math::
      m_{5\times 5} - (\sigma_\text{low}\times \text{avgDev}),

   then the pixel is replaced by :math:`m_{5\times 5}`.

.. figure:: ../_images/processing/cc.gif
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Animation showing cosmetic correction.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/find_cosme_use.rst

   .. include:: ../commands/find_cosme.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/find_cosme_cfa_use.rst

   .. include:: ../commands/find_cosme_cfa.rst
