Channel Extraction
##################

Split Channels
==============

This function creates three monochrome images from a 3-channel color image, 
depending on the configured color space. For RGB, it’s simply splitting the 
file in three. For the others, it involves computation of the equivalent color 
space, either `HSL <https://en.wikipedia.org/wiki/HSL_and_HSV>`_ 
(hue-saturation-lightness), `HSV <https://en.wikipedia.org/wiki/HSL_and_HSV>`_ 
(hue-saturation-value) or `CIELAB <https://en.wikipedia.org/wiki/CIELAB_color_space>`_.

.. figure:: ../_images/processing/split.png
   :alt: dialog
   :class: with-shadow

   Split channels dialog box.
   
.. tip::
   If no name is given to a channel, then the channel is not extracted.
   
.. admonition:: Siril command line
   :class: sirilcommand
  
   .. include:: ../commands/split_use.rst

   .. include:: ../commands/split.rst


Split CFA Channels
==================

CFA means color filter array. This term is often used to describe one-channel 
image content of a color image, with each pixel corresponding to values 
acquired behind an on-sensor filter. This is to oppose to debayer images (or 
debayered or demosaiced).

Opening a CFA image in Siril is required for pre-processing, like removing the 
dark signal before interpolating the image into 3-channel color. We can also 
use the color filter information to extract images like this:

* **Split CFA Channels**: four images are created from the CFA image, each 
  representing one filter of the Bayer matrix, so in general R.fit, G1.fit, 
  G2.fit and B.fit. It is useful if the goal is to process separately the 
  different colors of the image.
* **Extract Ha**: using an H-alpha filter with a color camera image (OSC: on-sensor
  color, or one-shot color camera) means that only the pixels with red filters 
  will be useful, so in general only a quarter of them. This function creates a
  new image that contains only the pixels associated with the red filter 
  documented in the Bayer matrix of the image.
* **Extract Ha/OIII**: for OSC cameras, filters that let through photons from 
  H-alpha and O-III wavelength have appeared. This extraction creates two 
  images: an image from the red pixels like the Extract Ha, and an image 
  combining the green and blue pixels into one for O-III. Both images are half 
  the definition of the input image.

  .. note::
     There is a frequently asked question about why Ha and OIII images are
     different sizes and how they are split out. This note attempts to explain
     an answer to that FAQ.

     In a colour image sensor the pixels are covered in a very fine filter matrix
     called a Color Filter Array (CFA) or Bayer matrix. The arrangement of
     filtered pixels is one of a number of patterns: RGGB, GBGR etc.

     .. figure:: ../_images/processing/cfa.png
        :alt: original
        :class: with-shadow
        :width: 33%

        `Original image <https://en.wikipedia.org/wiki/Bayer_filter#/media/File:Bayer_pattern_on_sensor.svg>`_ by Cburnett, licensed as CC BY-SA 3.0.

     Of these pixels, only the R pixels are sensitive to Ha. So first we split
     out all the red pixels into a Ha image. As only 1 in 4 of the CFA elements
     are red, the image dimensions of the Ha image are half that of the original
     sub.

     The remaining pixels, G and B, are all sensitive to OIII. The sensitivity of
     the G filtered pixels to OIII is different to the sensitivity of B filtered
     pixels to OIII, however they are imaging the same scene and evenly
     distributed so the average intensity must be the same.

     .. math::

        \text{G}_\text{i} = \text{G}_\text{io} \times \frac{3 \times \overline{\text{G}_\text{o}}}{2 \times \overline{\text{G}_\text{o}} + \overline{\text{B}_\text{o}}}

        \text{B}_\text{i} = \text{B}_\text{io} \times \frac{3 \times \overline{\text{B}_\text{o}}}{2 \times \overline{\text{G}_\text{o}} + \overline{\text{B}_\text{o}}}

     Where :math:`\text{B}_\text{i}` is the :math:`i^{\text{th}}` blue pixel, :math:`\text{B}_\text{io}`
     is the :math:`i^{\text{th}}` original blue pixel and :math:`\overline{\text{B}_\text{o}}`
     is the average of all the original blue pixels (and similarly for the green
     pixels).

     So far we have an equalised set of G and B pixels with gaps where the R
     pixels have been removed. So finally we use bilinear interpolation to estimate
     the R pixel values and end up with a full size OIII image.

  .. note::
     The Ha/OIII resampling option is how to handle the output of Extract Ha/OIII.
     No resampling produces full resolution OIII image and a half resolution Ha 
     image; upsample Ha upsamples the Ha image by a factor of 2 to match the OIII 
     image; downsample OIII downsamples the OIII image by a factor of 2 to match 
     the Ha image.

     You may wish to use drizzling to upscale the Ha data instead of upscaling.
     As drizzling is a stacking method, in this case you must use
     `seqextract_HaOiii` to extract the Ha and OIII from each frame of the
     sequence, and then stack the OIII images in the usual way and the Ha images
     with a 2x drizzle.
  
* **Extract Green**: for photometry, it’s often useful to only process the green 
  part of the CFA image, because it is more sensitive and has two pixels to 
  average, reducing noise even more. Of course, the created image also sees its
  definition halved by two.
  
.. figure:: ../_images/processing/split_cfa.png
   :alt: dialog
   :class: with-shadow

   Split CFA channels dialog box.
  
.. note::
   These functions only work if the Bayer matrix has been properly documented 
   by the acquisition software and if the image format supports it, so in 
   general FITS or SER.
   
.. warning:: 
   This does not work with other filter matrices than the Bayer matrices, like 
   the Fujifilm X-TRANS.
   
Wavelet Layers
==============

This tool extracts the different planes of the image by applying the wavelet 
process. Each plane is saved in an image and the set of images can be read as 
a sequence. You can choose up to 9 layers for the wavelet calculation and the 
type of the algorithm is either Linear or BSpline. The latter is usually the 
preferred one.


.. figure:: ../_images/processing/wave_extraction_dialog.png
   :alt: dialog
   :class: with-shadow

   Wavelet Layers extraction dialog box.
   
The decomposition is done through a number of detail layers defined at 
increasing characteristic scales and a final residual layer, which contains 
the remaining unresolved structures.
   
.. figure:: ../_images/processing/M45_orig.png
   :alt: original
   :class: with-shadow
   :width: 100%

   Original image of M45 (courtesy of V. Cohas). 
   
.. figure:: ../_images/processing/M45_planes.png
   :alt: planes
   :class: with-shadow
   :width: 100%

   6 extracted planes.

