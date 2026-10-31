Preprocessing
=============

This section takes you through the different steps of pre-processing your 
images, from import into Siril to obtaining a stacked image.

The right pane contains the tabs that are useful during preprocessing. They 
have been designed to be used from left to right throughout the process, with 
some exception for the creation of masters. These tabs are also accessible via 
the keys :kbd:`F1` to :kbd:`F7`.

Pre-processing is the step that starts with the conversion to the stacking of 
the images. The goal is to remove all unwanted signals and to reduce the noise 
present on all the subs.

.. figure:: ./_images/preprocessing/preprocess_schema.png
   :alt: preprocessing schema
   :name: preprocessing schema
   :class: with-shadow
   :width: 100%

   **Image 1** shows the result of the conversion of a raw digital camera image. 
   You can see visible dust, similar to dark spots. **Image 2**, after calibration 
   of the images by the master darks, bias and flats shows the complete removal
   of these spots and a cleaner signal. **Image 3** is the same after 
   demosaicing, showing color and a very large green cast due to the higher 
   sensitivity of the green photosites on the sensors. Finally, **image 4** is 
   the stacking output, with channel balancing.

.. toctree::
   :hidden:

   preprocessing/conversion
   preprocessing/calibration
   preprocessing/registration
   preprocessing/drizzle
   preprocessing/stacking
