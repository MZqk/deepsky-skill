Registration
############

Registration is basically the process of aligning the images from a sequence to
be able to process them afterwards. All the processes described hereafter 
calculate the transformation to be applied to each image in order to be aligned
with the reference image of the sequence.

Siril's strength lies in the wide variety of recording algorithms offered. 
Each method is explained below. Pressing the :guilabel:`Go register` button 
starts the registration of the sequence.

It is possible to choose the **registration channel**. Green is the default for
color images, Luminance for monochrome. The (\*) sign appearing after the 
channel's name means that registration data is already available for this 
layer. When processing images, registration data is taken from the default 
layer if available (for RGB images: Green, else fallback to Blue then Red).

Theory
======

Registration process
********************

What we call Registration is in fact a three-step process:

1. Detect features to be matched in all the images
2. Compute the transformations between each image and the reference image
3. Apply the computed transformation to each image to obtain new images

Depending on the registration method selected, the 3 steps occur (or not) into a 
single process. Siril uses the most sensible defaults (choosing whether or not to apply the 
computed transformation) depending on the registration method selected, 
but understanding the internal machinery may help you to change this behavior to 
better suit your needs.

Algorithms
**********
The table here below details the different algorithms used for the first 2 steps 
(detection and transformation calculation).

.. _table-regmethods:

+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+
| Registration method     | Feature detection                                                                       | Transformation calculation                    |  New seq  |
+=========================+=========================================================================================+===============================================+===========+
| Global                  | :ref:`Dynamic PSF <dynamic-psf:dynamic psf>`                                            | Triangles matching + RANSAC                   |     Y     |
+-------------------------+                                                                                         |                                               +-----------+
| Global with 2 pass      |                                                                                         |                                               |     N     |
+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+
|  1-2-3 stars            |  PSF :ref:`minimization <dynamic-psf:minimization>`                                     |  Singular Value Decomposition (2-3 stars)     |     N     |
|                         |  in selection box                                                                       |  Difference (1 star)                          |           |
+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+
| Image Pattern alignment | `cross correlation <https://en.wikipedia.org/wiki/Cross-correlation>`_ on selection box                                                 |     N     |
+-------------------------+-----------------------------------------------------------------------------------------------------------------------------------------+-----------+
| KOMBAT                  | Max of convolution in spatial domain on selection box                                                                                   |     N     |
+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+
|  Comet                  |  PSF :ref:`minimization <dynamic-psf:minimization>`                                     |  Shifts from velocity vector using timestamps |    Y(\*)  |
|                         |  in selection box                                                                       |                                               |           |
+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+
| Manual                  | Your eyes                                                                               | Your hand                                     |     N     |
+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+
| Astrometry              | :ref:`Dynamic PSF <dynamic-psf:dynamic psf>`                                            | From astrometric solution                     |     N     |
+-------------------------+-----------------------------------------------------------------------------------------+-----------------------------------------------+-----------+

.. note::

   (\*) Comet registration creates a new sequence file (with the prefix `comet_` by
   default) and creates "new" images. If registration data was present in the input 
   sequence, the comet shifts are composed with this pre-existing registration data.
   The "new" images created are simply symlinks to the input images, so that they do
   not use up additional storage space. This change introduced in version 1.4 was 
   made to clarify whether or not a sequence containing registration data, had
   only star alignment or star+comet shift alignment. Exporting a new sequence now 
   makes it obvious that additional shifts are included. It also enables to use the 
   same input sequence and generating multiple sequences aligned on different moving
   objects.

The table below lists the transformations compatible with each method, and if 
undistortion is available as well.

.. _table-transfo:

+-------------------------+----------+-------------+------------+--------+------------+------------+
|   Registration method   |  Shift   |  Euclidean  | Similarity | Affine | Homography | Undistort  |
+=========================+==========+=============+============+========+============+============+
| Global                  | subpixel |             | x          | x      | x          | x          |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| 2 pass                  | subpixel |             | x          | x      | x          | x          |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| 1-2-3 stars             | subpixel |  (2-3)      |            |        |            |            |
|                         | (1-2-3)  |             |            |        |            |            |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| Image Pattern alignment | pixel    |             |            |        |            |            |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| KOMBAT                  | pixel    |             |            |        |            |            |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| Comet                   | subpixel |             |            |        |            |            |
|                         |          |             |            |        |            |            |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| Manual                  | pixel    |             |            |        |            |            |
+-------------------------+----------+-------------+------------+--------+------------+------------+
| Astrometry              |          |             |            |        | x          | x          |
+-------------------------+----------+-------------+------------+--------+------------+------------+

It is also important to keep in mind how the registered sequence is fed into the 
stacking process that is generally used right after registration:

- if the transformation consists only of pixel-wise shifts, the stacking algorithm can use these shifts on-the-fly when reading the images. It means you do not need to generate "registered images". This saves storage space and skips interpolation. 
  It is, of course, at the expense of less accurate registration (i.e. subpixel accuracy) but is generally used on planetary/lucky imaging images where sampling is small. 
  This can also be applied with a registration method which computes subpixel shifts. During the stacking process, the shifts will be rounded off to pixel precision. 
  In any other case, meaning the stacking is fed with a sequence where the registration has computed transformations more complex than just shifts but the registered images have not been saved, Siril will emit a warning inviting you to export the registered images before proceeding to stacking.
- In all other cases, once the transformations have been computed, the transformed images need to be saved before proceeding to stacking, generally named with ``r_`` prefix.

Image transformations
*********************

Linear transformations
++++++++++++++++++++++

Siril uses linear transformations, with different degrees-of-freedoms, to map an 
image to the reference image:

* **Shift** is a 2 degree-of-freedom (x/y shifts) rigid mapping, well-suited 
  for images with no distortion, no scaling and no field rotation. It needs only 
  1 pair of stars (or feature) to be matched to define the transformation.

* **Euclidean** is a 3 degree-of-freedom (x/y shifts + one rotation) rigid mapping, 
  for images with no distortion, no scaling. It needs at least 2 pairs of stars to 
  be matched to define the transformation.

* **Similarity** is a 4 degree-of-freedom (one scale, one rotation and x/y 
  shifts) more rigid mapping than homography, well-suited for images with no 
  distortion. It needs at least 2 pairs of stars to be matched to define the 
  transformation.

* **Affine** is a 6 degree-of-freedom (two scales, one shear, one rotation and 
  x/y shifts) more rigid mapping than homography, well-suited for images with 
  little distortion. It needs at least 3 pairs of stars to be matched to define
  the transformation.

* **Homography** is the default transformation which uses an 
  8-degree-of-freedom transform to warp the images onto the reference frame. 
  This is well-suited for the general case and strongly recommended for 
  wide-field images. It needs at least 4 pairs of stars to be matched to define
  the transformation.
   
.. figure:: ../_images/preprocessing/Transformations.png
   :alt: Global registration transformation
   :class: with-shadow


Distortions
+++++++++++

Since version 1.3, Siril can also account for distortions, for some of the 
registrations methods as listed in this :ref:`table <table-transfo>`. Distortion
coefficients handled by Siril follow `SIP convention <https://irsa.ipac.caltech.edu/data/SPITZER/docs/files/spitzer/shupeADASS.pdf>`_.
This convention assumes that the pixel coordinates need to be corrected BEFORE 
trying to map them through a linear transformation. In `WCS <https://www.atnf.csiro.au/people/mcalabre/WCS/dcs_20040422.pdf>`_
jargon, this is called a *prior* distortion (as opposed to a *sequent* distortion).

Those coefficients are used twice during the registration process:

- the stars positions detected in "distorted" conditions are first corrected, 
  both in the image to be aligned and the reference image (or the stars projected
  positions for astrometric registration). The :ref:`linear transformation <preprocessing/registration:linear transformations>` 
  that maps the stars from the current image to the reference is then computed.
- When exporting the registered image, it is first corrected for distortion and
  then linearly projected to be aligned to the reference image. Note that this 
  actually occurs in a single operation (the pixel mapping is computed as the 
  composition of this non-linear correction and then the linear projection) so as
  to avoid interpolating pixel values twice. The reference image
  also undergoes this correction without the linear preojection.


Reference image
***************

This is the image which is used as a common reference to compute the 
transformations that send all the images of the sequence onto this particular 
one.

If not set manually, the reference image is chosen with the following criteria:

* if the sequence has already been registered, it is the best image, in term of 
  lowest FWHM or highest quality depending on the type of registration
* Otherwise, it is the first image of the sequence that is not excluded.

To specify an image as the reference, you can:

* Open the :ref:`Frame selector <sequences:frame selector>`, select the image 
  to be set as the new reference and click the button :guilabel:`Reference Image`.
* Use the command :ref:`setref <setref>`. 
  For instance, if you want to set image #10 as the reference:

  .. code-block:: bash

     setref 10
     
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/setref_use.rst

   .. include:: ../commands/setref.rst

.. figure:: ../_images/preprocessing/registration_framelist.png
   :name: Framelist panel
   :alt: Framelist panel
   :class: with-shadow
   
   The frame list dialog. You can browse all images in the sequence.

During stacking, the reference image is used as the normalization reference as 
well, if normalization is activated.

Registration methods
====================

Global registration
*******************

This is the preferred algorithm to align deep-sky images with sufficient overlap.

The global matching is based on triangle similarity method for automatically 
identify common stars in each image [Valdes1995]_. Our implementation is based
upon the program `match <http://spiff.rit.edu/match/>`_ from Michael Richmond.
Then, `RANSAC <https://en.wikipedia.org/wiki/Random_sample_consensus>`_ [Fischler1981]_
algorithm is used on the star lists to further reject outliers and determine 
the projection matrix. The robustness of the algorithm depends on the ability 
to detect the stars while avoiding false detections. Siril has a very elaborate
star detection algorithm that avoids as much as possible to select objects that
are not stars in the fastest possible time. The detection of the brightest 
stars is usually the most important. However, if there is a need to detect 
fainter stars, then the :ref:`Dynamic PSF <Dynamic-PSF:Dynamic PSF>` window can
be used to adjust the detection parameters.

.. figure:: ../_images/preprocessing/star_finder.png
   :alt: Star finder
   :name: star finder
   :class: with-shadow
   
   Automatic detection of stars in a single frame
   
There are some options associated with this alignment method. 

.. figure:: ../_images/preprocessing/registration_global.png
   :alt: Global registration
   :name: global registration options
   :class: with-shadow

   Global registration options

The :guilabel:`Transformation` dropdown menu allows to choose between
different transformations.
   
.. warning::
   The initial star matching uses triangle similarity algorithm, in consequence
   the minimum of star pairs must be at least of 3 for **Shift**, 
   **Similarity** and **Affine** and of 4 for **Homography**.

Other registration options are:

* The :guilabel:`Minimum Star Pairs` button sets the minimum number of star 
  pairs a given frame can have in relation with the reference frame. If a given 
  light frame has less star pairs, it will not be registered. To the right of 
  this option is a button that opens the Dynamic PSF tool.

* The option :guilabel:`Maximum Stars Fitted` defines the maximum number of 
  stars to be searched for in each frame (default 2000). The larger this value,
  the more stars will potentially be detected, resulting in a longer detection 
  but more accurate registration.

* Use the option, :guilabel:`Match stars in selection`, if you want 
  to perform the Global Star Alignment algorithm within the selected area in 
  the reference image. If no selection is done, this option is ignored.

* The :guilabel:`Undistortion` dropdown menu allows to choose between
  different corrections:

  - None
  - From image
  - From FITS/WCS file
  - From masters

| **From image** uses the astrometric solution from the currently loaded image.
| **From FITS/WCS file** will open a file chooser to select a FITS image or a ``*.wcs`` file that has distortion coefficients.
| **From masters** will fetch the master distortion file corresponding to each image. This option can be useful if you are aligning images from two or more sessions and the sensor has been moved wrt. the optics, in which case distortion coefficients may not be the same.
|

.. tip::
   For this last option :guilabel:`From masters` to work, you will need to have set a master
   distortion path in the :ref:`preferences <preferences/preferences_gui:pre-processing>`.
   To create a master file, go :ref:`here <astrometry/platesolving:Distortions>`.

The options at the bottom let you:

* Filter out images that have been unselected from the sequence.
* Choose between interpolation and drizzle for exporting the images.
  Those are the same as in the :ref:`Output registration <preprocessing/registration:output registration>` 
  section, they are not further explained here.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/register_use.rst

   .. include:: ../commands/register.rst

2pass registration
******************

To activate this option, simply tick the :ref:`corresponding box <global registration options>`
after choosing :guilabel:`Global registration` in the method drop-down.

This performs only the first (of 2 passes), allowing the reference frame 
to be chosen from detected star information instead of automatically choosing 
the first frame of the sequence. The proposed options are similar to the 
:ref:`Global Registation <preprocessing/registration:global registration>` 
algorithm but this method does not create any sequences and all alignment 
information is saved in the ``seq`` file.

During star detection, Siril sets a maximum of 2000 stars to be found (this can 
also be changed with the appropriate option). In case more than one image has 
reached the maximum star limits, the star lists of all images are screened again. 
A new minimum detection threshold is defined to be able sort the images by both 
number of stars detected and FWHM.

The star lists of all images are saved, the ``.fit(s)`` extension being replaced
by ``.lst``. This allows to re-run the 2pass algorithm very quickly with different 
parameters, say different transformation. In case the star detection have been 
modified, the process detects these changes and re-run the analysis as required. 

This registration must generally followed by :ref:`Apply Existing Registation 
<preprocessing/registration:apply existing registration>` in order to apply the
transformation and build a new sequence, unless you have chosen to compute 
``Shift``.

These lines perform a 2pass registration on a sequence named `pp_light` and applies it. The output is a sequence `pp_light`.

.. code-block:: text

    # Align lights in 2 passes
    register pp_light -2pass
    seqapplyreg pp_light

These lines perform a 2pass registration on a sequence named `colors` and applies it while cropping the output images to the minimum coommon area. The output is a sequence `pp_colors`. This can be useful before compositing mono images (the areas which are not common to all images are cropped).

.. code-block:: text

    # Align layers in 2 passes and crop away borders
    register colors -2pass
    seqapplyreg colors -framing=min

1-2-3 stars registration
************************

When the images contain few stars, for example in the case of DSO Lucky Imaging
images where the frame exposure is less than one second. It is possible that 
the global registration algorithm fails, even if you change the detection 
parameters in the :ref:`Dynamic PSF <Dynamic-PSF:Dynamic PSF>` window. It may 
then be interesting to make a manual detection of the stars you want to align.
This is the interest of the 1, 2 or 3 star registration algorithm.

.. figure:: ../_images/preprocessing/registration_123.png
   :alt: 1-2-3 stars registration
   :name: 1-2-3 stars registration options
   :class: with-shadow

   1-2-3 stars registration options
   
The principle of this method is to draw a selection area around a star and 
click on the :guilabel:`Pick 1st star` button, then so on. 

* If only one star is selected, only the translation between the images will be
  calculated. Therefore the :guilabel:`Shift only` button is automatically 
  selected. The shift values are then storred in the :file:`seq` file.

* If two or three stars are selected, then the rotation can be calculated and 
  applied to create a new sequence. However, if the :guilabel:`Shift only` 
  option is selected, which is not mandatory, only the shifts will be 
  calculated.
  
The option :guilabel:`Follow star movement` use the position of the star(s) 
found in the previous image as new centre for the current image registration. 
This allows the selection area to be smaller, registration faster, and accounts
for drift or images with a large number of stars.

.. warning::
   Enabling this option requires the registration to not be parallelized, it 
   will run on one CPU core only.

Image Pattern alignment (planetary-full disk)
*********************************************

This is a simple registration by translation method using `cross correlation 
<https://en.wikipedia.org/wiki/Cross-correlation>`_ in the spatial domain.

This method is fast and is used to register *planetary* movies, in which 
constrasted information can be seen on large areas of the image. It can also be
used for some deep-sky images registration. Nevertheless keep in mind that it 
is a single point alignment method, which makes it poorly suited for high 
definition planetary alignment. But, it does effectively anchor the images to 
stabilize the sequence. Simply draw a selection around the object (the planet 
for example) and make sure that its movement during the sequence is contained 
within the selection. Only the translation can be calculated with this method.

.. figure:: ../_images/preprocessing/registration_planet.png
   :alt: Pattern alignment
   :name: Pattern alignment options
   :class: with-shadow
   :width: 100%

   Pattern alignment options
   
KOMBAT
******

This method comes from the `OpenCV <https://opencv.org/>`_ library, a library 
widely used in Siril. They explain:

*It simply slides the template image over the input image (as in 2D 
convolution) and compares the template and patch of input image under the 
template image. Several comparison methods are implemented in OpenCV. (You can 
check docs for more details). It returns a grayscale image, where each pixel 
denotes how much does the neighbourhood of that pixel match with template.*

In practice, simply draw a selection around the object (the planet for example)
and make sure that its movement during the sequence is contained within the 
selection. Only the translation can be calculated with this method.


Comet/Asteroid registration
***************************

The cometary registration tool works in a very simple way, in two steps.

#. With the frame selector, select the first image of the sequence, surround 
   the comet nucleus, then click on the button :guilabel:`Pick object in #1`.
#. Then select the last image of the sequence, surround the nucleus of the 
   comet, then click on the button :guilabel:`Pick object in #2`.
   
The comet velocity :math:`\Delta x` and :math:`\Delta y` is computed in pixel
per hour if everything is ok.

.. warning::
   The alignment of the comet must be done on images whose stars have been 
   previously aligned. Either via a new sequence, with the global alignment, 
   or by having saved the registration information in the :file:`seq` file, 
   via a 2-pass or astrometric registration.
   
.. note::
   To fully function, the images must have a timestamp.

.. figure:: ../_images/preprocessing/registration_comet.png
   :alt: Comet registration
   :name: Comet registration options
   :class: with-shadow

   Comet registration options

.. tip::
   If the PSF detection fails to detect an object, it will return the center of the
   box that was drawn. This can be handy if you want to align on an object that 
   is not visible on your subs. Use the :ref:`solar system object <astrometry/annotations:solar-system objects>`
   annotations to plot the position of an asteroid and draw a box around the marker 
   to pick its position.

This method will ouput a new sequence file, with the sequence name prepended with
the :guilabel:`prefix` defined (``comet_`` by default). This does not however create 
the new images but symlinks to the original images. For Windows users, please make
sure you have enabled `developer mode <https://learn.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development>`_,
otherwise, it will make hard copies. See also the note below :ref:`this table <table-regmethods>`.

Manual Registration
*******************

This last method of registration is very particular, which explains its 
separate position, and allows to align images manually. Of course, only the 
translation between images is allowed.

The first thing to do is to define two previews in the image. Clicking on the 
button :guilabel:`Set first preview` will initialize the first preview. You 
then need to click on an area of the image, ideally a star in the vicinity of 
an edge of the image to set the preview area. A click on the second button 
:guilabel:`Set second preview` allows to do the same on a second point.

.. figure:: ../_images/preprocessing/registration_man_main.png
   :alt: Manual registration
   :class: with-shadow
   :width: 100%
   
It is very important to have a reference image already set with the 
:ref:`Frame selector <sequences:frame selector>`. By default, it is the first
image. The user is free to choose the one he wants. It will be used as a 
reference layer, seen by transparency, to align the images manually with the 
numerical buttons. Then, browse the image one by one to apply the same method
to the whole sequence.
   
.. figure:: ../_images/preprocessing/registration_man_preview2.png
   :alt: Manual registration preview
   :name: Manual registration preview
   :class: with-shadow
   
   The Y-shift is too large, same stars on different frames do not overlap.
   
.. figure:: ../_images/preprocessing/registration_man_preview.png
   :alt: Manual registration preview
   :name: Manual registration preview 2
   :class: with-shadow
   
   X- and Y-shift look fine. The current image is aligned to the reference one.

Astrometric registration
************************

Introduced in version 1.3, this is the prefered mode for assembling mosaics or images 
with little overlap. It can also be useful to register stacks issued from different 
set-ups (different optics, different cameras, different field of views etc...).

It does not have an entry in the registration method drop-down as the information to 
export the registered images has already been computed when :ref:`platesolving the 
sequence <astrometry/platesolving:solving sequences>`. All you need to do is to
:ref:`Apply Existing Registation <preprocessing/registration:apply existing registration>`.

Undistortion will be applied as defined when platesolving the sequence, meaning 
if the images were plate-solved using a SIP order larger than 1, then undistortion
will automatically be included. Unless you have a perfectly optically flat field,
it is usualy a good idea to platesolve using SIP, as shsown below, with and without
distortion correction.

.. figure:: ../_images/preprocessing/registration_applyastrometry_undistort.png
   :alt: Undistortion effect
   :name: Undistortion effect
   :class: with-shadow

   Effect of undistortion on two overlapping panels after registration

Apply Existing registration
***************************
This is not an algorithm but rather a commodity to apply previously computed 
registration data stored in the sequence file. The interpolation method can be
selected in the :ref:`Output Registration <preprocessing/registration:output registration>`
section. You can also use image filtering to avoid saving unnecessary images,
as in stacking :ref:`preprocessing/stacking:image rejection`. There is also a
:guilabel:`Drizzle` option to apply registration using Drizzle instead of
interpolation. See the :ref:`Drizzle section <preprocessing/drizzle:Drizzle>` for
details.

.. |current-icon| image:: ../_images/preprocessing/framing-default.svg
.. |max-icon| image:: ../_images/preprocessing/framing-max.svg
.. |min-icon| image:: ../_images/preprocessing/framing-min.svg
.. |cog-icon| image:: ../_images/preprocessing/framing-cog.svg

Four framing methods are available:

* |current-icon|: **current** uses the current reference image. This is the default behavior.
* |max-icon|: **maximum** (bounding box) adds a black border around each image as required
  so that no part of the image is cropped when registered.
* |min-icon|: **minimum** (common area) crops each image to the area it has in common with
  all images of the sequence.
* |cog-icon|: **center of gravity** determines the best framing position as the center of 
  gravity (cog) of all the images.

.. tip::
   Introduced in Siril 1.3, ``max`` mode does not export images with black borders,
   that encompass the full resulting image. The images are exported with the 
   necessary projection and the relative shifts required to compose the final image
   is kept in the the resulting seq file.


:guilabel:`Estimate` button will launch the framing computation, without
actually exporting the images. This information can be interesting to know in advance
the size of the exported images. This accounts for the **framing** method 
selected and the **scaling** factor chosen in the :ref:`Output Registration 
<preprocessing/registration:output registration>`.

When pressing :guilabel:`Estimate`, the console will show an output like this:

.. code-block:: text

   Output image: 7893 x 5254 pixels (assuming a scaling factor of 1.30)

.. figure:: ../_images/preprocessing/registration_applyreg.png
   :alt: Apply Existing registration
   :name: Apply Existing registration options
   :class: with-shadow

   Apply Existing registration options


.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/seqapplyreg_use.rst

   .. include:: ../commands/seqapplyreg.rst

   .. include:: ../commands/seqapplyreg_add.rst

Output Registration
===================

This frame contains all the output elements for the sequence. You can choose between
Interpolation and Drizzle to export the images.

.. warning::
   They may not be both available depending on the nature of the input sequence:

   - for mono sequences, both Interpolation and Drizzle may be selected
   - for CFA sequences (undebayered color images), only Drizzle is available
   - for RGB sequences (debayered color images), only Interpolation is available

Both methods share the options:

- :guilabel:`Scaling`, a value between 0.1 and 2, that is used to rescale the output
  images.
- :guilabel:`Prefix`, the prefix that will be preprended to form the exported sequence 
  name (defaults to ``r_``).

Interpolation
*************

Tthe pixels of the resulting images are interpolated by an algorithm that is
left to the user's choice. There are 5 possible interpolation algorithms, plus a
**None** option:

- Nearest Neighbor
- Bilinear
- Bicubic
- Pixel Area Relation
- Lanczos-4
- None

The best efficient interpolation methods are generally bicubic and Lanczos-4
(used by default). However, they usually require the :guilabel:`Clamping
interpolation` option to be enabled to avoid ring artifacts around the stars,
which makes the interpolation operation twice as slow. We still enable it by
default because it gives a significant improvement in resolution, with often a
8% smaller in FWHM compared to Siril 1.0 default method of pixel area.
It is possible the clamping is not useful for your images, we recommend you to
compare the results with your images.

The special case of **None** is reserved for the case of global
registration and Apply Existing registration. If you want to export or save a
sequence that contains only translation, without using interpolation (so as
not to modify the pixel values), you should select **None**.

.. figure:: ../_images/preprocessing/registration_output_interpolation.png
   :alt: Output registration (interpolation)
   :name: output registration for interpolation
   :class: with-shadow

   Output options for interpolation


Drizzle
*******

The button :guilabel:`Drizzle` activates the drizzle
algorithm for the processing of this sequence. See the
:ref:`Drizzle section <preprocessing/drizzle:Drizzle>` for details.

.. warning:: 
   The counterpart of this technic is that the amount of memory and disk 
   space needed to create and process drizzled images is multiplied by the 
   square of the Drizzle scale factor.

.. figure:: ../_images/preprocessing/registration_output_drizzle.png
   :alt: Output registration (drizzle)
   :name: output registration for drizzle
   :class: with-shadow

   Output options for drizzle


Astrometric solution of the registered images
*********************************************

When registered images are exported, they inherit the astromertic soluion of the
reference image if any. Otherwise, their previous solution is wiped. Obviously,
the new solution for each image accounts for the transformations it has undergone 
through the registration process.

In case a distortion solution is found in the reference image but no undistortion
was applied when computing the transformations - for instance if you have not 
selected any ``Distortion`` from the drop-down in global or 2-pass methods,
a warning will be displayed in the Console. The distortion information will be kept
in the registered images. In case they are significant, you may see their effect
when stacking. In which case, you will need to register again using a distortion
specification.


References
==========
 
.. [Fischler1981] Fischler, M. A., & Bolles, R. C. (1981). Random sample 
   consensus: a paradigm for model fitting with applications to image analysis 
   and automated cartography. Communications of the ACM, 24(6), 381-395.

.. [Valdes1995] Valdes, F. G., Campusano, L. E., Velasquez, J. D., & Stetson, 
   P. B. (1995). FOCAS automatic catalog matching algorithms. Publications of 
   the Astronomical Society of the Pacific, 107(717), 1119.
