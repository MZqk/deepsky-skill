Dynamic PSF
###########

This section describes the two essential steps which are undertaken to detect 
stars in individual frames. The detection on a single image can be run or 
fine-tuned using :menuselection:`Tools --> Image Analysis --> Dynamic PSF` or 
with shortcut :kbd:`Ctrl` + :kbd:`F6`.

.. figure:: ./_images/dynamic-psf/dynamicPSF.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

   Dynamic PSF running on a deep sky image.

The process is as follows:

- make an initial detection of potential star candidates 
- fit a PSF model on each candidate. Run sanity checks based on model fitting 
  parameters to make sure this is a star and reject non-star candidates.

At the end of this process, the result is a list of stars, with positions in the 
image wrt. top left corner and measured quantities of all the stars in the list.


Initial star candidates
***********************

While it may seem obvious when looking at an image where the stars are, it is a 
bit more difficult to translate the process into mathemical operations and criteria. 
This section briefly describes the underlying algorithm. It is inspired from 
`DAOPHOT software manual <https://iopscience.iop.org/article/10.1086/131977>`_ 
[Stetson1987]_, with simplifications brought to boost performance. The original 
algorithm was aiming at being extensive in detecting all possible stars, serving 
the purpose of building star catalogues, while Siril needs to detect stars primarily
as features for registration . It also needs to be resilient to the wide variety 
of images that are submitted - most of us do not have a professional-grade 
astronomical gear in the backyard - and we have had to make some choices 
regarding prior knowledge of the imaging condition (sampling, seeing etc).

Over the years, our implementation has evolved to produce what it is today. It aims 
at not missing very bright stars, which are important for registration, and reject 
as much as possible outliers, while remaining reasonnably fast at doing so.

It can be decomposed in the following steps:

- compute the statistics of the image to capture both the background level, as the 
  median of the image, and its noise. This assumes that the image is relatively flat. 
  As a consequence, detection will tend to be less efficient in the corners 
  if heavy vignetting is present after Calibration.
- Compute as well a dynamic range, defined as the maximum of the image minus its 
  background. This will later on be useful to detect saturated stars.
- smoothen the image with a gaussian kernel. Ideal smoothing would be to use the 
  kernel that has the same FWHM as the image. Instead, we have chosen an arbitrary size 
  which produced satisfactory results over a very wide range of conditions. This 
  enables to be "blind" to the imaging parameters.
- on the smoothened version of the image, detect local maxima over a level defined 
  as the background level plus X times the noise (X can be varied using the ``threshold``
  value in the GUI). Make sure this is a maximum over a certain box size (defined 
  by the ``radius`` parameter).
- run sanity check to make sure the maximum and its neighbors are well above the 
  surrounding pixels (to reject patches in the bright parts of nebula for instance).
- run sanity check to guess if the core around the maxima is saturated, i.e. 
  consistently close to the upper bound of the dynamic range. If true, 
  run an edge-walking algorithm to detect the limit of the saturated part.
- Use first and second derivatives along an horizontal and vertical line passing 
  through the center to guess star local background, amplitude and width in all 
  directions (top, down, left and right).
- If the parameters are symmetrical enough in all directions (up to the ``roundness`` 
  parameter), confirm the star as a potential candidate.

Once the list of potential candidates has been assembled, they are sorted by 
decreasing amplitudes and fed to the PSF fitting algorithm described in the 
:ref:`minimization <dynamic-psf:minimization>` section. 

Models
******
Two models are used in the Dynamic PSF window. In general, Moffat is much 
better suited to fit objects such as stars.

.. figure:: ./_images/dynamic-psf/star_moffat_gaussian.svg
   :alt: Star profile
   :class: with-shadow
   :width: 100%
   
   Displays of two circular PSFs according to a Gaussian profile and a Moffat 
   profile. Both models use the same parameters and the Moffat profile uses 
   uses :math:`\beta = 1.4`.
   
.. figure:: ./_images/dynamic-psf/star_moffat_gaussian_rotated.svg
   :alt: Star profile
   :class: with-shadow
   :width: 100%
   
   Rotated Gaussian and Moffat function have :math:`\sigma_x=2\sigma_y`, 
   :math:`\theta=45°`. For Moffat, :math:`\beta = 1.4`.
   
.. figure:: ./_images/dynamic-psf/Star_Profile.png
   :alt: Star profile moffat
   :class: with-shadow
   :width: 100%

   Star Profile with Gaussian and Moffat models. Several :math:`\beta` values
   are tried. :math:`\beta = 10` gives a profile very close to the Gaussian one.

.. admonition:: Theory
   :class: siriltheory
   
   #. An elliptical Gaussian fitting function defined as

      .. math::
         :label: psf_gaussian
   
	      G(x,y) = B+Ae^{-\left(\frac{(x-x_0)^2}{2\sigma^2_x}+\frac{(y-y_0)^2}{2\sigma^2_y}\right)}.
	
   #. An elliptical Moffat PSF fitting function defined as

      .. math::
         :label: psf_moffat
   
	      M(x,y) = B+A\left(1+\frac{(x-x_0)^2}{\sigma^2_x}+\frac{(y-y_0)^2}{\sigma^2_y}\right)^{-\beta},
	
   where:

   * :math:`B` is the average local background.
   * :math:`A` is the amplitude, which is the maximum value of the fitted PSF.
   * :math:`x_0`, :math:`y_0` are the centroid coordinates in pixel units.
   * :math:`\sigma_x`, :math:`\sigma_y` are the standard deviation of the Gaussian
     distribution on the horizontal and vertical axes, measured in pixels.
   * :math:`\beta` is the exponent from the Moffat formula that controls the
     overall shape of the fitting function. The upper bound of this parameters was
     set to 10. A higher value is meaningless and means that the Gaussian is good 
     enough to fit the star.
  
   Other parameters are derived from these fitted variables:

   * :math:`\text{FWHM}_x` and :math:`\text{FWHM}_y`: The `Full Width Half Maximum <https://en.wikipedia.org/wiki/Full_width_at_half_maximum>`_ 
     on the X and Y axis in pixel units. These parameters are calculated as 
     follow:
  
     * :math:`\text{FWHM}_x = 2\sigma_x\sqrt{2\log 2}`.
     * :math:`\text{FWHM}_y = 2\sigma_y\sqrt{2\log 2}`.
     * It is possible to obtain the FWHM parameters in arcseconds units. This 
       requires you fill all fields corresponding to your camera and lens/telescope 
       focal in the setting parameter window in the burger menu, then :guilabel:`Image Information` 
       and :guilabel:`Information`. If standard FITS keywords ``FOCALLEN``, 
       ``XPIXSZ``, ``YPIXSZ``, ``XBINNING`` and ``YBINNING`` are read in the FITS 
       HDU, the PSF will also compute the image scale in arcseconds per pixel.
   * :math:`r`: The roundness parameter. It is expressed as :math:`\text{FWHM}_x/\text{FWHM}_y`,
     with :math:`\text{FWHM}_x > \text{FWHM}_y` the symmetry condition.
   * Another parameters is also fitted in both Gaussian and Moffat models. This
     is the rotation angle :math:`\theta`, defined in the range :math:`[-90°,+90°]`.
     The addition of this parameter implies a coordinate change where the :math:`x`
     and :math:`y` variables expressed in :eq:`psf_gaussian` and :eq:`psf_moffat`
     are replaced by :math:`x'` and :math:`y'`:

     .. math::
        :label: coordinate_change
   
            x' &= +x \cos\theta+y \sin\theta \\
            y' &= -x \sin\theta+y \cos\theta.
       
Minimization
************

Minimization is performed with a non-linear `Levenberg-Marquardt algorithm 
<https://en.wikipedia.org/wiki/Levenberg%E2%80%93Marquardt_algorithm>`_
thanks to the very robust `GNU Scientific Library <https://www.gnu.org/software/gsl/>`_.
This algorithm is used to find the minimum of a function that maps a set of 
parameters to a set of observed values. It is a combination of two optimization 
techniques: the gradient descent method and the inverse-Hessian method.

The Levenberg-Marquardt algorithm adjusts the trade-off between these two 
methods based on the curvature of the function being minimized. When the 
curvature is small, the algorithm uses the gradient descent method, and when 
the curvature is large, the algorithm uses the inverse-Hessian method.

Since version 1.2.0, the saturated part of the star is removed from the fitting 
process, enabling to capture much more accurately the non-saturated part. This is 
what enables to "reconstruct" the star profile when using the 
:ref:`Desaturate <processing/stars/unclipped:Desaturate Stars>` menu option or 
:ref:`unclipstars <unclipstars>` command.


Use
***
.. |starfinder| image:: ./_images/icons/starfinder.svg
.. |dynPSF-button| image:: ./_images/icons/sum.svg

Dynamic PSF can be called from two different ways depending on what you want:

You may want to fit just one or a few stars. In this case, after drawing a 
selection around an unsaturated star (this is important for the accuracy of 
the result) you can either right click and choose the :guilabel:`Pick A Star` item, 
click the + button in the Dynamic PSF dialog, or type :kbd:`Ctrl` + :kbd:`Space`. An 
ellipse is then drawn around the star. To open the dialog it is also possible 
to use the shortcut :kbd:`Ctrl` + :kbd:`F6`.

You may want to analyze as many stars as possible by clicking on the icon 
|starfinder|, or using the command line :ref:`findstar <findstar>`. All detected stars are 
surrounded by an ellipse: orange if the star is ok, magenta if the star is 
saturated. It is also possible to show the average of the computed parameters
as illustrated below, by clicking on the |dynPSF-button| button.

.. figure:: ./_images/dynamic-psf/dynamicPSF_average.png
   :alt: dialog
   :class: with-shadow

   Average of the fitted stars in the Gaussian model.
    
Star detection has a number of uses:
    
* Siril uses it internally for astrometric purposes when registering sequences 
  of images. This is automatic and requires no user intervention.
    
* Because stars are so bright compared with dim features of interest such as 
  nebulae or galaxies, it is very common that some stars in an image will be 
  saturated, meaning their brightness profile is clipped. This can cause 
  problems for some image processing functions, particularly deconvolution, 
  and results in loss of colour information and slightly greater star bloat 
  when applying stretches. Analysis of all the stars will show you which ones 
  are saturated, and you can then use the :ref:`Desaturate <processing/stars/unclipped:Desaturate Stars>` menu option or 
  :ref:`unclipstars <unclipstars>` command to fix the problem by synthesis of 
  the clipped part of the profile.
  
  .. admonition:: Siril command line
     :class: sirilcommand
   
     .. include:: ./commands/unclipstars_use.rst

     .. include:: ./commands/unclipstars.rst
    
* Ideally all stars in an image should be perfectly round, however problems 
  such as coma, astigmatism and bad tracking, as well as issues such as 
  incorrect back focus to field flatteners, can give rise to patterns of 
  elliptical stars in an image. The ellipses produced by the Dynamic PSF tool 
  can give a good visual illustration of such problems.
    
* Examination of the average star parameters, especially FWHM and the Moffat 
  fitting function beta parameter, can provide information about the quality 
  of seeing in an image.
    
* Detection of all stars is the first step in using the :menuselection:`Star Tools --> 
  Full Resynthesis tool`. This synthesizes corrected luminosity 
  profiles for all detected stars, and can be used to create a synthetic star 
  mask which can then be mixed with a starless image generated by Starnet++ to 
  fix otherwise irredeemable stars in an image. In this case detection of stars 
  using the Moffat profile may give a more realistic result and can also make 
  it easier to filter out galaxies incorrectly detected as stars by using the 
  Minimum beta setting.
    
* The **Center Selected Star** toggle button can be used to find a 
  particular star in the list quickly and easily in the image, by centring it 
  in the viewport. This is useful if you have detected all stars and wish to 
  check specific solutions to ensure they really are a star and not a galaxy 
  or a cosmic ray. 
    
* Similarly to this, clicking on an orange or magenta star ellipse in the main 
  viewport will highlight the selected star solution in the Dynamic PSF dialog.
  This could be useful if you wish to see the parameters of an individual star.
    
* Siril's deconvolution functions support using Dynamic PSF measurements to 
  synthesize a deconvolution function that matches star parameters measured 
  directly from the image.

Configuration
*************

Dynamic PSF can be configured using the settings in the Dynamic PSF dialog:

* **Radius** sets the half-size of the search box. If you have trouble detecting 
  particular stars you can try changing this but usually the default is fine.
    
* **Threshold** changes the threshold above the noise for star detection. If you 
  increase this value, fewer faint stars will be detected. You may still wish 
  to do this for very noisy images though. Decreasing this value may detect 
  more faint stars, but will also make the algorithm more likely to 
  misidentify random noise spikes as stars.
    
* **Roundness** threshold sets the allowable ellipticity for detections to be 
  accepted as stars. Highly elliptical stars may occur due to imperfect 
  tracking or aberrations, but sometimes stars that are too close from each
  others are also detected as a single very elongated star. To highlight all
  these problems, it is possible to enable a higher bound for the roundness. A
  maximum value of 1 is equivalent to disabling the range, leaving only the
  minimum value. This roundness range should be disabled for registration or
  astrometry.
    
* **Convergence** sets a criterion used by the solver. Increasing it will allow 
  the solver more interations to converge and can potentially detect additional
  stars, but may increase the solver running time.
    
* Profile type chooses between solving **Gaussian** or **Moffat** profiles for 
  the stars.
   
* **Minimum beta** sets a minimum permissible value of beta for a detection to 
  be accepted as a star. Galaxies may sometimes be detected as Moffat profile 
  stars but they have diffuse profiles and the value of beta is usually very 
  low, less than around 1.5.

* **Relax PSF checks** allows for relaxation of several of the star candidate 
  quality checks. This is likely to result in a significant increase in false 
  positive star detections, often with wild parameters.

* A range of **minimum** and **maximum amplitude** can be set to limit the
  amplitude (parameter named ``A`` in reports) of detected stars. This is
  useful if only non-saturated stars need to be selected, for PSF fitting in
  deconvolution for example. Note that removing the saturated stars from
  detection can break registration and astrometry.
  
.. tip::
   The settings defined in this window can be tested on the currently loaded 
   image. However, you have to keep in mind that they will also be used for 
   all images of the sequence, especially for the global alignment registration.

The :ref:`findstar <findstar>` command will obey the same settings entered in 
the Dynamic PSF dialog, but it can also be configured using the 
:ref:`setfindstar <setfindstar>` command.

   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/findstar_use.rst

   .. include:: ./commands/findstar.rst

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/setfindstar_use.rst

   .. include:: ./commands/setfindstar.rst

References
**********

.. [Stetson1987] Stetson, P. B. (1987). DAOPHOT: A computer program for crowded-field 
   stellar photometry. Publications of the Astronomical Society of the Pacific, 
   99(613), 191.
