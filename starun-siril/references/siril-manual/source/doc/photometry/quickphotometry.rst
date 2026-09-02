Quick photometry
################

Photometry areas explained
**************************
As Siril only performs aperture photometry, it's important to understand and set the correct parameters.

The stars are modeled according to your choice: Gaussian or Moffat as decribed :ref:`here <Dynamic-PSF:Models>` in the :ref:`Dynamic PSF <Dynamic-PSF:Dynamic PSF>` chapter.

So a particular star on a particular image has its own FWHM.

.. figure:: ../_images/photometry/3D-StarPSF.png
   :alt: PSF and photometry terms
   :class: with-shadow
   :width: 100%

   PSF and photometry terms.

The aperture photometry process needs 3 radii:

#. The outer circle, defined by the **outer radius** (most often expressed in pixel).
#. The inner circle, defined by the **inner radius** (most often expressed in pixel).
   These 2 circles define the annulus which is used to measure the background (that is the sky level + additionnal noise).
#. The aperture circle, defined by the **aperture radius**.
   It can be expressed in pixel or as a ratio of the star FWHM (the automatic checkbox in the window). 
   This area is used to measure the star signal added to a backgroud signal.

These 3 radii can be set and tweaked individually from the GUI :menuselection:`Preferences -->Photometry` tab.

.. figure:: ../_images/photometry/var-ratio.png
   :alt: Radii settings
   :class: with-shadow
   :width: 60%

   Radii settings.

Or via the command line interface:

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/setphot_use.rst

   .. include:: ../commands/setphot.rst

.. tip::
   These radii settings apply to all aperture photometric processes: **quick photometry** and **Light Curves**.
   Check them carfully.

Photometry on hand-picked objects of a single image
***************************************************

.. |quickphoto-button| image:: ../_images/icons/photometry_dark.svg

The **quick photometry** button |quickphoto-button| is a button located in the
toolbar and used to perform a photometry of the stars, this is generally the
simpliest way to proceed.

.. tip::
   If the star is in the middle of several stars and the tool fails to point to
   the right star, an alternative solution is to draw a selection around the
   star and then right-click and click on :guilabel:`PSF`. It may also be 
   interesting to know that :kbd:`Ctrl`-middle click (or :kbd:`Cmd`-middle click on
   MacOS) draws a selection of a recommended size for PSF/photometry (based on
   the configured outer radius).
   
.. tip::
   When photometry is performed on the RGB layer, the results are actually 
   calculated on the green layer.
   To obtain photometry on the red or blue layers, you need to work on the 
   corresponding channels.
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/psf_use.rst

   .. include:: ../commands/psf.rst

Click on this button to change the image selection mode, then click on a
star. The photometry and the `PSF (Point Spread Function)
<https://en.wikipedia.org/wiki/Point_spread_function>`_ of the star are
computed, giving plenty of details.

Two models are used for the calculation of the PSF, which can be selected by
the user in the :ref:`Dynamic-PSF <Dynamic-PSF:Dynamic PSF>` window
(:kbd:`Ctrl` + :kbd:`F6`).

.. figure:: ../_images/photometry/photometry_result.png
   :alt: photometry result
   :class: with-shadow
   :width: 100%

   Photometry results window.
   
The result of the photometry and the associated PSF are displayed in the form:

.. code-block:: text

    PSF fit Result (Gaussian, monochrome channel):

    Centroid Coordinates:
    		x0=5258.25px	 09h25m34s J2000
    		y0=2179.72px	+69°49'31" J2000

    Full Width Half Maximum:
    		FWHMx=7.13"
    		FWHMy=6.79"
    		r=0.95
    Angle:
    		82.87deg

    Background Value:
    		B=0.000874

    Maximal Intensity:
    		A=0.628204

    Magnitude (relative):
    		m=-2.3948±0.0014

    Signal-to-noise ratio:
    		SNR=28.9dB (Good)

    RMSE:
    		RMSE=1.890e-03

#. The fit was done with the **Gaussian** fitting function so no additional
   parameters are needed. However, if Moffat was used, the following output
   will be shown:

   .. code-block:: text
   
       PSF fit Result (Moffat, beta=2.9, monochrome channel):
    		
#. **Centroid Coordinates** gives the coordinates of the centroid in pixels.
   However, like in the example above, if astrometry was set on the image,
   Siril gives coordinates in the `World Coordinate Systems
   <https://www.atnf.csiro.au/people/mcalabre/WCS/>`_ (RA and Dec).

#. **Full Width Half Maximum** (`FWHM <https://en.wikipedia.org/wiki/Full_width_at_half_maximum>`_) 
   is returned in arcsec if the image scale is
   known (obtained from its header or from the GUI :menuselection:`Image information -->
   Information`) and in pixels if not. The roundness *r* is also computed as the
   ratio of :math:`\frac{\text{FWHMy}}{\text{FWHMx}}`.

#. **Angle** is the rotation angle of the *X* axis with respect to the centroid
   coordinates. It varies in the range :math:`[-90°,+90°]`. 

#. **Background Value** is the local background in the :math:`[0,1]` range for
   32-bits images and :math:`[0,65535]` for 16-bits images. This is a fitted
   value, not the background computed in the aperture photometry annulus.

#. The **maximum Intensity** value is also a fitted value and represents the
   amplitude. It is the maximum value of the fitted function, located at the
   centroid coordinates.

#. The **magnitude**, given with its uncertainty, is the result of photometry.
   However, if for some reasons the calculation cannot be done (saturated
   pixels or black pixels), an uncertainty of **9.999** is given. In this case,
   the photometry is flagged as invalid but a magnitude value is still given,
   although it should be used with caution.

#. An estimator of the **signal-to-noise ratio** is shown in the results. Its
   value is calculated from the following formula and given in `dB
   <https://en.wikipedia.org/wiki/Decibel>`_:

   .. math::
      :label: SNR
   
	   \text{SNR} = 10 \log_{10}\left(\frac{I}{N}\right)
	
   where *I* is the net intensity, proportional to the observed flux *F* and
   *N* the total of uncertainties as expressed in :eq:`sigma_mag`.

   For easier understanding, it is associated with 6 levels of quality:

    #. Excellent (SNR > 40dB)
    #. Good (SNR > 25dB)
    #. Satisfactory (SNR > 15dB)
    #. Low (SNR > 10dB)
    #. Bad (SNR > 0dB)
    #. N/A

       This last notation is displayed only if the computation failed, for one 
       reason or another.

#. Finally, **RMSE** gives an estimator of the fit quality. The lower the
   value, the better the result.

.. _PSF_more_details:

When the image is plate-solved, the button :guilabel:`More details` at the bottom of
the window links to a page on the SIMBAD website with information about the
selected star. However, it is possible that the page does not give any
additional information if the star is not in the SIMBAD database.
   
.. figure:: ../_images/photometry/simbad.png
   :alt: photometry result
   :class: with-shadow
   :width: 100%

   More details about the analyzed star. Click on the picture to enlarge.
   
Quick photometry on sequences
*****************************

Quick photometry can also be performed on a sequence. This is generally
intended to obtain a light curve as explained :ref:`here
<photometry/lightcurves:Light curves>`. To proceed, you must **load a
sequence**, make a selection around a star, then **right click** on the image.

.. tip::
   Ideally, the sequence must be registered without interpolation so as not to
   alter the raw data. For example, use the **global registration** with the
   option **Save transformation in seq file only**.
   
.. note::
   Make sure the inner and outer radii for the background annulus are adapted
   to the star and sequence being analyzed. Some images may have much larger
   FWHM than the reference image, because of sky conditions or bad tracking.
   They can be changed in the :ref:`preferences
   <Preferences/preferences_gui:photometry>` or with the ``setphot`` command.

At the end of the process, Siril automatically opens the plot tab showing 
computed curves. It is possible to click on several stars to reproduce the
calculation, however the first star keeps the particular status of
*variable*, and the others serve as *references*. This is important in the
calculation of the light curve.

.. figure:: ../_images/photometry/photometry_example.png
   :alt: Photometry on sequence
   :class: with-shadow
   :width: 100%

   In this example, 3 stars have been analyzed. The first one is used as 
   variable. The others are references.

Computing true magnitudes
*************************

The calculated magnitude is only meaningful if it is compared to others in the
linear image. Indeed, the value given does not correspond at all to the true
visible magnitude of the star, it is uncalibrated, also called relative
magnitude.

Siril provides tools that can be used to calculate an approximate apparent
magnitude. This requires knowing the magnitude of another star visible on the
image that will act as reference. It is currently possible to use only a single
star as reference, hence the *approximate*. For a greater precision, use a star
of similar color and magnitude as the star(s) you want to measure should be
chosen, and its provided magnitude should be in adequation with the filter used
to capture the image. Catalogs contain magnitudes computed using a `photometric
filters <https://en.wikipedia.org/wiki/Photometric_system>`_, which is
generally not what amateur use to make nice pictures, this adds another
approximation.

* Do a quick photometry on a known star, the given relative magnitude is
  ``-2.428``. It is possible to find out the actual visible magnitude by
  clicking on the :guilabel:`More details` button as explained above. Let's say the
  value found is ``11.68`` (make sure you use a value corresponding to the
  spectral band of the image).
* Once done, keep the star selected, then enter the following command in Siril

   .. code-block:: text
   
      setmag 11.68
      
   That will output something like
   
   .. code-block:: text
     
      10:50:49: Relative magnitude: -2.428, True reduced magnitude: 11.680, Offset: 14.108
      
      
   .. admonition:: Siril command line
      :class: sirilcommand
   
      .. include:: ../commands/setmag_use.rst

      .. include:: ../commands/setmag.rst   
      
* Now, all calculated magnitudes must have values close to their true visual
  magnitude. However, this is especially true for stars whose magnitude is of
  the same order of magnitude as the star taken as reference.

  .. figure:: ../_images/photometry/photometry_setmag.png
     :alt: photometry setmag
     :class: with-shadow
     :width: 100%

     Photometry results window with true magnitude set.
   
* To unset the computed offset, just type

   .. code-block:: text
   
      unsetmag
      
   .. admonition:: Siril command line
      :class: sirilcommand
   
      .. include:: ../commands/unsetmag_use.rst

      .. include:: ../commands/unsetmag.rst   

.. tip::
   The same commands exist for the sequences. They are ``seqsetmag`` and
   ``sequnsetmag``. It is used in the same way when a sequence is loaded.
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/seqsetmag_use.rst

   .. include:: ../commands/seqsetmag.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/sequnsetmag_use.rst

   .. include:: ../commands/sequnsetmag.rst
