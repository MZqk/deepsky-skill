Workflow
========

Linear Stage
------------

Almost all astrophotographic workflows start off with a linear stage. This
is because our imaging sensors are photon counters, and provide essentially
a linear response to incoming photons. Double the number of incoming photons
during an exposure and the pixel value doubles (neglecting sensor bias, which
will be calibrated out).

Many standard photographic workflows jump straight into color spaces with a
nonlinear "tone response curve", for example the TRC of sRGB is approximately
a gamma function :math:`f(x) = x ^{1/g}`, where :math:`g = 2.2`.

However, astrophotography is different. Representing this linear data as
linear values in a linear colorspace is vital for a number of reasons. Most
importantly some functions rely on the data being linear: star detection
matches Gaussian star profiles, and if the data has had a nonlinear TRC
applied then the profiles will no longer be Gaussian, and the star finder
algorithm will give worse results. StarNet has been trained on linear data
with a specific transformation (histogram stretch with automatic parameters)
and again, feeding it data that has already had a gamma function applied will
make the results worse. Deconvolution, noise reduction... Many functions we
apply to our data rely on it still being linear.

If you wish, you may apply a linear profile at this stage. Siril can do this
automatically for you when an image is opened, if you set the auto-assign
preferences and check the :guilabel:`Pedantically assign linear profiles` button.
For monochrome images the linear Gray profile will be applied. For RGB images
(or when compositing linear monochrome images into a color image), the profile
selected will be the linear profile from the preferred set of color space
profiles you set in :guilabel:`Preferences`.

For example if you chose the Rec2020 color space, Siril will assign the
Rec2020-V4-g10 ICC profile to your image. "Rec2020" is the name of the gamut;
"V4" indicates that this is a Version 4 ICC profile; and "g10" states that the
profile has a TRC that is a gamma function with :math:`g = 1.0` (i.e. a linear
response).

.. tip::
   Assigning a basic working space profile is enough for most users. Color
   balance can be addressed later using the color calibration tools. However, if
   you have a calibrated camera you may wish to apply an input profile. Camera
   calibration and creation of camera profiles is an advanced topic and is outside the
   scope of Siril, but you may find `the guidance in the articles at Section D 
   of this page <https://ninedegreesbelow.com/photography/articles.html#profile-digital-camera>`_
   of use. The simplest way to apply an input profile is using the Color
   Management dialog. Select your camera profile from the file chooser and click
   :guilabel:`Assign`. This is actually enough: Siril will happily edit your
   image in the camera profile, but you can also select your working profile and
   click :guilabel:`Convert` to convert the data to your preferred color space.

   Another approach to calibrating your input is by using Color Conversion
   Matrices (CCMs). This is more labor-intensive: as with camera profiles, the
   CCMs need to be produced outside Siril, and they can then be applied to your
   linear data using PixelMath.

What you have just done is assigned the color space primaries in CIE 1931 XYZ
space, according to the points on the triangle representing the Rec2020 gamut.
Although the data is linear, when you see the image on your screen it has the
display color space transform applied to it, using your chosen rendering intent.

Having said that, it isn't really all that necessary to assign a color profile
at this stage. You will quite likely be using the Autostretch viewer mode when
working with linear data, which doesn't require color management anyway.

Now that's explained, you can happily get on with the rest of your linear
workflow. Calibration, registration, stacking, star removal, noise
reduction... Go at it!

Nonlinear Stage
---------------

When you're ready to stretch your image, it's time to think about your
color space again. Stretching changes the image from linear data to
non-linear data so that it looks pleasing to the human eye. You're going to
make your data non-linear now, so before stretching is a good time to convert
the image to your chosen nonlinear color space, be it sRGB or Rec2020 or
another color space of your preference. You can either do it yourself manually,
or you can set a preference for Siril either to prompt you to convert the
image color space to your preferred color space, or to do it automatically.

You can now carry on and finish any post-stretch editing of your image.

Image Saving and Export
-----------------------

When the time comes to save your image, you have a choice of formats:

* FITS is the native image format for Siril, and ICC profiles can be
  embedded in FITS files. This is done in the same way as other
  astrophotography software, so images saved with embedded ICC profiles
  will be interoperable between Siril and PixInsight (and probably
  other software supporting FITS ICC profile embedding).
* TIFF and PNG are good formats to choose for high quality image export
  and offer wider compatibility with other software than FITS. Both
  support ICC profile embedding. Options in the :guilabel:`Preferences`
  dialog allow setting whether 8-bit and higher bit depth TIFF and PNG images
  are saved as sRGB, saved in the image's current ICC profile, or saved in the
  preferred color space set in preferences.
* The *de facto* standard for highly compressed images for the WWW is
  JPEG. Support for ICC profiles in JPEG images varies according to your
  JPEG library, but if you are using libjpegturbo version 2.0.0 or higher
  then ICC profiles will be embedded. Still, many third party programs
  do not support color management at all and they will assume JPEGs
  represent a image in the sRGB colorspace with the sRGB standard TRC. This
  is the default export color space for JPEGs and you should consider carefully
  before saving them with a different color profile.
* Siril does also support some other image formats: PNM / PGM, BMP etc.
  These formats are grouped together under the category of "do not support
  ICC profiles". When exporting to these formats, images will **always**
  be converted to sRGB.


Siril provides a preference setting for the export / conversion intent. In
pretty much every single case you should leave this as "Relative Colorimetric".
