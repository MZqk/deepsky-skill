Image information window
========================

.. image:: ../_images/GUI/image_information.png
   :alt: Image information

This window provides information about what is known about the sampling of the
opened image. The sampling, sometimes also called resolution or scale,
indicates how much angle of the sky is seen in one pixel, as seen through the
instrument. It depends on two things: the focal length of the instrument and
the pixel size of the sensor, itself depending on the binning mode.

FITS headers can contain this information if it was given to the acquisition
software. In that case this is the values that are shown in this window. If not
available in the image metadata, because it was unknown to the acquisition
software, or more simply because the file format does not support it, this
dialog will still be available and populated with *default values*. They can be
modified and used for various operations of Siril that require sampling
information, for example displaying FWHM in arc seconds instead of pixels.

Default values are no binning (1x1), and a focal length and a pixel size stored
in the settings. The values stored in the settings can be set from this dialog
by activating the :guilabel:`Save as default values` button before clicking on
:guilabel:`Close`. They can also be set by performing an astrometric resolution
on the image, also called *plate solving*, if the option to update the default
values when a result is found is enabled :ref:`in the preferences
<preferences/preferences_gui:Astrometry>`.

The values displayed in this window will be stored in the current loaded image
and if this image is saved as FITS, they will be stored in the FITS header.

Binning management can take two forms depending on the acquisition software:
the real pixel size is given but has to be multiplied by the binning (when
:guilabel:`Real pixel size` is checked), the already multiplied pixel size is
given (when unchecked).

