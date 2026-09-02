Drizzle
#######
Variable-pixel linear reconstruction, and more commonly referred to as Drizzle,
was developed for Hubble Space Telescope (HST) work by Andy Fruchter and Richard
Hook [FruchterHook1997]_, initially for the purposes of combining dithered
images of the Hubble Deep Field North (HDF-N). This algorithm can be thought of
as a continuous set of linear functions that vary smoothly between the optimum
linear combination technique (interlacing) and shift-and-add. This often allows
an improvement in resolution and a reduction in correlated noise, compared with
images produced by only using shift-and-add.

There is an excellent page summarising the technique and providing a good
graphical representation of how the pixel data "drizzles" down from the coarse
input pixel grid onto a finer output pixel grid
`here <https://www.stsci.edu/~fruchter/dither/drizzle.html>`_.

The degree to which the algorithm departs from interlacing and moves towards
shift-and-add depends upon how well the PSF is subsampled by the shifts in the
input images. In practice, the behavior of the Drizzle algorithm is controlled
through the use of a parameter called the pixel fraction, that represents the
amount by which input pixels are shrunk before being mapped onto the output
image plane. At a pixel fraction of 0, drizzle is equivalent to pure interlacing;
at a pixel fraction of 1, it is equivalent to shift-and-add.

To understand the difference between drizzle and the interpolation methods of
applying registration, firstly consider how the standard interpolation method works.
The registration data takes the form of a 3x3 Homography matrix, which encodes
an 8 degrees of freedom linear transformation from one set of coordinates to
another (i.e. from each frame to the reference frame). This is used to map the
values from each pixel in each input image onto the correct place in the output
image, aligning the output with the reference image. The actual alignment uses
an interpolation method, which can be selected in the registration options.
Interpolation results in a smearing of the point spread function, especially
when upscaling images. It can also result in artefacts, although Siril implements
a clamping mechanism to minimize this.

Drizzle, by contrast, turns each pixel in the input image into a droplet, and
drizzles it through a grid pattern onto the output reference frame. Each droplet
has a size, and by choosing a scaled-up output pixel grid but smaller droplet
size you can achieve improved resolution **if** your imaging train is
undersampled. (If your sampling is correct for the resolving power of your
telescope, Drizzle can't produce detail beyond the diffraction limit.) This comes at
the cost of increased image noise: because each droplet "paints" a smaller area
in the output image, the average coverage of droplets per output pixel in the
final stack is reduced.

Note that Drizzle doesn't replace all of the registration process: you still
use the Global Star Alignment, 1-2-3 Star Alignment, Comet registration or
whichever registration method you wish prior to drizzling: it is an alternative
only to the interpolation method used when applying registration.

.. warning::
   The Drizzle process involves taking multiple frames and drizzling them into
   an output frame. The input is the set of frames and their WCS solutions, and
   the output is the single drizzled image. In Siril the process is split into
   Drizzle registration and subsequent stacking, however the intermediate
   artefact (the set of images representing the individual images drizzled onto
   the common output grid but not yet stacked) is not something that has
   significance in its own right. Individual frames in the drizzled sequence can
   **and will** look weird: the reference frame often looks different to the
   others, especially when drizzling CFA data, as a result of its special
   property of having zero shift with respect to the output frame, and other
   frames may show Moiré patterns. **Don't panic!** This is simply an
   intermediate stage in the overall Drizzle process, and all these apparent
   oddities will be resolved in the stacking stage.

Uses of Drizzle
===============

There are 3 main reasons you may prefer Drizzle to using an interpolation method
to apply registration.

* **Resolution enhancement** If your image is significantly undersampled, you
  may be able to achieve an improvement in resolution using Drizzle that you
  could not achieve using the :guilabel:`Interpolation upscaling x2` registration
  option.

* **CFA imaging** If your images have a CFA pattern (i.e. if you use a one-shot
  camera or dSLR), Drizzle provides a significant improvement over debayering.
  This is sometimes referred to separately to Drizzle as *Bayer Drizzle*, but
  really it's exactly the same process. When drizzling a CFA image the CFA color
  of the current droplet sets which channel of the output image it lands on,
  whereas when drizzling a mono image all droplets land in the (only) mono
  output channel. Drizzling CFA images avoids the artefacts that occur with all
  debayering algorithms, which gives improved noise characteristics when
  strongly stretching images. This supports improved noise reduction and
  deconvolution for drizzled CFA sequences compared with debayered and
  registered CFA images, and makes the shadows look better.

* **Avoid artefacts** It is possible to drizzle a sequence using scale = 1,
  pixel fraction = 1 and achieve essentially the same result as applying
  registration with one of the interpolated methods.
  You might wish to consider this if you see interpolation artefacts from the
  standard interpolated method (though these are generally effectively suppressed
  by the clamping feature). Note that drizzle can produce different
  artefacts of its own (see the "Some Common Issues" heading below) however these
  can be completely avoided by choice of drizzle kernel or by having a greater
  number of input frames, and are generally handled perfectly by stacking.

Limitations of Drizzle
======================

* Drizzle is a little slower compared to interpolation, particularly the
  preferred square kernel. If you are using older or slower hardware, you may
  prefer the legacy method.

* When used for upsampling, Drizzle achieves its resolution improvements at the
  cost of increased image noise. Therefore you may wish to collect more integration
  time when drizzling than when using an interpolation-based upsampling method.

* The above issue is *especially* true for CFA images. Consider that only 1 in 2
  of the pixels are green, and only 1 in 4 are red or blue. Therefore for the
  red or blue channels, CFA drizzle effectively already involves the same level
  of reduction in droplet coverage as a 2x upscale drizzle. If you upscale on top
  of that, you need as much droplet coverage as you would for a 4x upscale drizzle!
  Therefore it is generally recommended to drizzle CFA images at scale = 1.

Comparison
==========
The following image shows a comparison between drizzle and the legacy upscaling
method. The image is Ha extracted from an OSC session with a dual band filter.
On the left you can see the result of the legacy OSC_Extract_HaOIII script, which
extracts the Ha data captured by the red pixels in the OSC Bayer matrix as a
half-size image and uses OpenCV upscaling with lanczos4 interpolation to produce
an image tat matches the size of the OIII image.

On the right you can see the result of the updated script (available through the 
siril-scripts repository), which extracts the Ha
data captured by the red pixels in the OSC Bayer matrix as a half-size image and
drizzles it using scale = 2.0, pixel fraction = 0.5, to produce an image that
matches the size of the OIII image.

Viewing it at 100% scale it is clear that the drizzled stack restores much of the
resolution of the optical system that is undersampled by the spaced-out red
pixels in the Bayer matrix: it looks much sharper, and the numbers confirm it:
the average fwhm in the left-hand image is 3.59, whereas in the right-hand image
it is 3.25.

.. figure:: ../_images/preprocessing/drizzle_comparison.png
   :alt: Comparison between interpolation upscaling and drizzle
   :class: with-shadow
   :width: 100%

   Comparison between interpolation upscaling and drizzle

Workflow and User Interface
===========================

Mono Workflow
=============

Calibration
-----------
For mono images nothing changes in the calibration tab. Calibrate as you normally
would.

Registration
------------

.. figure:: ../_images/preprocessing/drizzle_ui.png
   :alt: Drizzle settings
   :class: with-shadow
   :width: 100%

   Registration tab showing drizzle settings

Drizzling
---------

Scale
~~~~~
Scale sets the scale of the drizzle output image with regard to the input image.
A typical drizzle scale for an undersampled mono image is 2.0. This means that
the input will be drizzled onto an output pixel grid with twice the resolution.
(If your input reference image was 1024 x 512 pixels, your output image would be
2048 x 1024 pixels.) Note: becase the image represents the same area of the sky,
although there are **twice as many** pixels along each axis in the output image,
effectively each output pixel is **half as wide** and **half as tall**.

.. tip::
   The greater the scale, the sparser each drizzled output image and the fewer
   pixels end up getting stacked into each output pixel. This results in a
   noisier image: **the resolution gain provided by drizzle comes at the expense
   of noise**. This has to be mitigated by using a greater overall integration
   time than you would need without drizzling to a greater resolution.

Pixel Fraction
~~~~~~~~~~~~~~
Pixel Fraction sets the size of the droplet taken from the input grid. Consider a
drizzle scale of 2.0: since the output pixels are half as wide and half as tall,
that means that in order for each input pixel "droplet" to be the same size as an
output pixel it should be shrunk to half the linear dimensions. This is a pixel
fraction of 0.5. A good rule of thumb is that the pixel fraction should be
roughly the reciprocal of the drizzle scale (with some kernels it helps to set
it a little bigger than this, in order to reduce pixels that receive zero
input from any drizzled droplets).

There is scope for experimentation with the pixel fraction: setting a larger pixel
fraction means that each input droplet will influence more output pixels. On the
other hand, setting a smaller pixel fraction means that each input droplet will
influence fewer output pixels. The "point" kernel is a special case where the
pixel fraction is zero (and with this kernel selected, the pixel fraction
setting has no effect).

Droplet Model
~~~~~~~~~~~~~
Siril's Drizzle implementation provides several droplet models:

* **Square**. This models the droplet as a square droplet aligned exactly with
  the input pixel. It is accurately mapped to the output reference frame, and it
  works well at any scale and pixel fraction. This is the default kernel.

  .. tip::
     The Square kernel is mathematically flux preserving by construction: each
     input pixel’s flux is distributed into output pixels in exact proportion to
     area overlap. Total counts are preserved identically. This makes it both robust
     and ideal for applications where you need strict surface brightness conservation
     (e.g. diffuse structures or extended sources).

* **Point**. This models the droplet as a point at the center of the input pixel.
  It is the limit of the square kernel with a very small pixel fraction. This will
  prodce the lowest correlated noise but is prone to leaving holes unless you have
  very large numbers of well-dithered frames. If you find holes or artefacts in your
  final stack, use the Square kernel instead. This kernel is very fast.

* **Turbo**. This is a simplification of the Square kernel. It assumes that
  rotation between the input and output reference is negligible. This results in
  a much faster computation, but is approximate. It is a "quick and dirty" kernel
  originally intended for generating intermediate products within the HST workflow,
  but it may be of some use in lucky imaging due to its speed relative to Square.

* **Gaussian**. This models the droplet as a Gaussian centered on the center of
  the input pixel with a FWHM equal to `pixfrac`. The droplets in the Gaussian model
  are round and are recommended where improved preservation of PSFs (star shapes)
  is important.

  .. tip::
     [Avila2015]_ recommends the Gaussian kernel for applications involving point source
     photometry. Despite not being mathematically flux preserving, the Gaussian
     kernel reduces the blockiness typical of square kernels (and avoids the high
     frequency ringing characteristic of Lanczos kernels, producing smooth, centrally
     peaked PSFs that are closer to analytical forms used in photometry. While
     Gaussians don't preserve flux perfectly the trade-off is much better behaved
     PSFs, which is more important for extracting accurate stellar photometry. It
     may also be more aesthetically pleasing in a final image.

* **Lanczos2** and **Lanczos3**. These kernels model the droplet as a Lanczos
  function centered on the center of the input pixel.

  .. warning::
     The Lanczos kernels are specially designed for resampling an image from one
     World Coordinate System (WCS) onto another **at the same scale**. They should
     only be used with scale == pixfrac == 1.0.

  Lanczos kernels provide a good option for single-image registration at native
  scale: Lanczos3 produces tigher average FWHM than any other kernel or OpenCV
  interpolation, but it is significantly slower than other kernels.

Initial Pixel Weighting
~~~~~~~~~~~~~~~~~~~~~~~
When a droplet lands on the output pixel grid, it may cover more than one output
pixel. In fact, one output pixel may be covered by multiple droplets, by only a
fraction of a droplet or even by no droplets at all. The contribution of each
input pixel can be weighted by the master flat, so that pixels from areas with
higher SNR (less vignetting) are weighted more highly. Unless you have peculiar
flats this generally makes no practical difference.

To enable the master flat, check the :guilabel:`Include master flat in initial pixel weighting`
checkbox.

.. warning::
   The master flat must be set in the :guilabel:`Calibration` tab!

Let's Drizzle!
--------------
Once all the options are set, click the :guilabel:`Go register` button.

Stacking
--------

You can now stack your drizzled sequence as normal. Note that for some combinations
of drizzle scale and droplet size, some rejection models will work better than
others. In particular, if you have significant numbers of "zero input" or null pixels,
there will be fewer values to use in rejection. MAD may be a good one to try if
your usual rejection method struggles.

The GIF below shows a comparison of a stack of 37 images, in one case with
registration applied using interpolation and in the other case with registration
applied using drizzle. It is clear that the stack made with drizzled data is
significantly sharper than the one using data registered using interpolation.

.. figure:: ../_images/preprocessing/drizzle_comparison.webp
   :alt: Crop of two stacked images, showing a significant improvement in sharpness
         in the drizzled stack.
   :class: with-shadow
   :width: 100%

   Comparing registration applied with drizzle and with interpolation. Click to
   enlarge view.

CFA Workflow
============

CFA Calibration
---------------
For one-shot color (OSC) images, uncheck the :guilabel:`Debayer before saving`
checkbox. This represents a change to previous workflows, but for drizzling it
is essential that the CFA pattern is preserved in the drizzle input sequence.


CFA Registration with Drizzling
--------------------------------

You can use drizzle directly after a :ref:`Global Registration <preprocessing/registration:global registration>`
or by :ref:`applying existing registration <preprocessing/registration:apply existing registration>`
for all the other registration methods that do not export images directly.


CFA Scale
~~~~~~~~~
Scale sets the scale of the drizzle output image with regard to the input image.
In OSC camera images each pixel only records a single color: red, green or blue.
The pixels have a color filter array (CFA) applied to them and this determines which
pixels respond to red, green and blue wavelengths. Thus all the pixels are sparsely
distributed compared with a mono sensor in which all pixels are sensitive to whatever
light passes through the filter. In both Bayer pattern and X-Trans CFAs
the red and blue pixels are particularly sparse in the input frames, therefore
increasing the drizzle scale above 1.0 will require even more frames to provide
enough drizzle coverage and reach an acceptable level of noise.

For a typical OSC sensor application where the seeing is well matched to the
nominal sampling of the sensor it is recommended to apply CFA drizzle with
scale = 1.0 and pixfrac = 1.0. This will restore resolution in each color channel
(which is effectively being undersampled because of the spacing of the colored
pixels in the CFA) and avoid conventional debayering artefacts. If you wish to
upsample the image as well by using scale > 1.0, be aware that the pixels available
in each channel will be getting even more sparse, and you will need even more data
to ensure adequate coverage and contain noise to an acceptable level.

.. tip::
   For OSC drizzle, start with scale = pixel fraction = 1.0.

CFA Pixel Fraction
~~~~~~~~~~~~~~~~~~
Pixel Fraction sets the size of the droplet taken from the input grid. The same
comments apply here as are described above for the mono workflow.

CFA Droplet Model
~~~~~~~~~~~~~~~~~
The same choice of drizzle kernels applies for CFA drizzle as for mono drizzle.
Note that the kernels that are particularly prone to generating null pixels can
be tricky when used for CFA drizzle. If you have tens of thousands of frames as
in a planetary video, turbo may work fine (and will be fast!) however for deep
sky sequences with smaller numbers of frames it is recommended to stick to the
*square* or *Gaussian* kernels (and bear in mind as mentioned above that Gaussian
is not flux preserving, so if you intend to do anything involving photometric
techniques *square* is preferred).

CFA Initial Pixel Weighting
~~~~~~~~~~~~~~~~~~~~~~~~~~~

As with mono drizzling a master flat may be specified. To enable the master flat,
check the :guilabel:`Include master flat in initial pixel weighting` checkbox.

.. warning::
   The master flat must be set in the :guilabel:`Calibration` tab!

Let's Bayer Drizzle!
--------------------
Once all the options are set, click the :guilabel:`Go register` button.

Stacking your CFA data
----------------------
You can now stack your drizzled sequence as normal, noting the same comments
on rejection as for mono drizzle (these may be more apparent with CFA drizzle
if you have inadequate coverage to support some of the outlier rejection
algorithms, owing to the greater sparseness of input pixels in each channel).

.. tip::
   If you are drizzling your CFA data to gain resolution, it is possible you may
   be disappointed when comparing results with stacked debayered images. There *are*
   generally gains, but they may be marginal (e.g. a few percent improvement in
   fwhm) and generally will not be nearly as impressive as the resolution gains
   to be had from drizzling undersampled mono data.

   The reason for this is that debayering already restores some of the lost
   resolution. The various debayering algorithms work differently but they
   generally all rely on principles of spatial and spectral correlation to infer
   some of the resolution missing in one channel based on information obtained
   from the other channels. [Losson2010]_

   **The real reason to drizzle CFA data is that the drizzled result has much
   cleaner noise.** It looks less "grainy" (i.e. it lacks the structure that can
   be seen in the background of a typical debayered CFA stack) and is thus easier
   to reduce using noise reduction techniques and gives more consistent data for
   photometric applications such as color calibration. When stretched hard to bring
   out faint features just above the background, the resulting background looks
   more natural.

Bayer Drizzle Comparison
------------------------
The animation below shows a comparison between CFA drizzle with two different
pixel fractions and two of the classical debayering algorithms.

.. figure:: ../_images/preprocessing/Bayer_Drizzle.webp
   :alt: Showing a comparison of VNG, RCD and CFA drizzle with 2 different pixel fractions
   :class: with-shadow
   :width: 100%

   Comparison of CFA drizzle (here captioned as Bayer Drizzle) with classical
   debayering algorithms

* VNG is provided as a basic reference: note the color artefact around the
  brighter stars.
* RCD is quite good with round objects like stars.
* Bayer Drizzle 1.0 gives results very close to RCD but with a better noise and
  background
* Bayer Drizzle 0.5 gives better resolution at the cost of more noise. The
  trade-off that pixel fraction gives between resolution and noise is evident.
  With a smaller pixel fraction CFA drizzle needs more data to achieve the same
  noise performance.

Some Common Issues
==================

.. tip:: **DON'T PANIC** - the following results may look a bit weird when you
   view an individual drizzled sub, but they are not bugs - the algorithm is
   functioning as intended. In most cases they naturally resolve themselves
   during stacking, in the remaining cases they can be resolved by changing the
   drizzle parameters or by including more frames of data.

Moiré Patterns
--------------
Due to the nature of the drizzle algorithm, when upscaling some output pixels may
not receive any input. These are referred to as "null pixels" and they have a zero
value. Some kernels compensate for this, effectively by limiting the pixel
fraction, so that all output pixels receive some input, but others do not.

Output pixels that don't receive any input are black: since they typically occur
in patterns based on the geometry of the transformation from the input frame, they
typically look like Moiré patterns, as shown below:

.. figure:: ../_images/preprocessing/drizzle_moire.png
   :alt: Showing patterns of zero-weighted pixels with the turbo kernel
   :class: with-shadow
   :width: 100%

   Showing patterns that result from null pixels in a drizzled image

Don't worry about this! Siril ignores pixels that are exactly 0 in stacking, so
as long as you have enough input frames and the dither positions are suitably
scattered, all the pixels will receive coverage from enough pixels and the output
stack will be fine. However if you are stacking with a lower number of input frames
and this is causing problems, try a different drizzle kernel. Here is exactly the
same image drizzled with exactly the same scale and pixel fraction, but with the
square kernel instead of the turbo kernel. The result is different, and the
patterns are no longer evident.

.. figure:: ../_images/preprocessing/drizzle_nomoire.png
   :alt: Using the square kernel produces an image with no strange patterns
   :class: with-shadow
   :width: 100%

   Using a different drizzle kernel can eliminate patterns of null pixels

Patchy Stacks
-------------
One issue you may see when stacking drizzled data, if there are too many null
pixels, is that you may get an odd patchy appearance in the final result:

.. figure:: ../_images/preprocessing/drizzle_notenoughframes.png
   :alt: Showing a patchy result from stacking drizzled data with too many null pixels
   :class: with-shadow
   :width: 100%

   Typical patchy appearance of a stack of drizzled data with too many null
   pixels / not enough frames

This typically occurs with the point, turbo or lanczos kernels. You can fix it by
using the square or Gaussian kernels or by having more input frames.

Commands
--------
.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/seqapplyreg_use.rst

   .. include:: ../commands/seqapplyreg.rst

Note that the introduction of true drizzle has necessitated some changes to
existing command arguments for clarity.

:ref:`register <register>` and :ref:`seqapplyreg <seqapplyreg>`
have a new argument **-drizzle** which, together with some
related arguments, activates true drizzle.


References
----------

.. [Losson2010] Olivier Losson, Ludovic Macaire, Yanqin Yang. Comparison of
   color demosaicing methods. Advances in Imaging and Electron Physics, 2010,
   162, pp.173-265, section 2.2.2.
   https://hal.science/hal-00683233/document

.. [FruchterHook1997] A. S. Fruchter and R. N. Hook. (1997) *A novel image
   reconstruction method applied to deep Hubble Space Telescope images.*
   Proc. S.P.I.E. vol. 3164.
   https://arxiv.org/abs/astro-ph/9708242

.. [Avila2015] R. J. Avila, A. Koekemoer, J. Mack, A. Fruchter. (2015)
   *Optimizing pixfrac in Astrodrizzle: An Example from the Hubble Frontier
   Fields.*
   https://www.stsci.edu/files/live/sites/www/files/home/hst/instrumentation/wfc3/documentation/instrument-science-reports-isrs/_documents/2015/WFC3-2015-04.pdf
