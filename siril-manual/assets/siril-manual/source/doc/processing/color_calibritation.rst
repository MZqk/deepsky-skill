Color Calibration
#################

Siril offers three ways to retrieve the colors of your image:

- :ref:`Manual calibration <processing/color-calibration/manual:Manual Color Calibration>`
- :ref:`Photometric Color Calibration <processing/color-calibration/pcc:Photometric Color Calibration>`,
  also called PCC.
- :ref:`Spectrophotometric Color Calibration <processing/color-calibration/spcc:Spectrophotometric Color Calibration>`, 
  also called SPCC. This is the most accurate and recommended method

Here, "retrieve" means re-balancing the RGB channels to get as close as possible 
to the true colors of the shot object.

.. tip::
   PCC and SPCC are two methods that require a :ref:`plate-solved image <astrometry/platesolving:Platesolving>`. 
   It is not possible to access these tools without this prerequisite.

.. toctree::
   :hidden:

   color-calibration/manual
   color-calibration/pcc
   color-calibration/spcc
