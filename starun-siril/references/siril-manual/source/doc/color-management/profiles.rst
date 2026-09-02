Color Space Profiles
====================

Built-in Color Profiles
-----------------------
Siril contains a basic set of color space profiles. These are taken from
Elle L. Stone's `excellent repository <https://github.com/ellelstone/elles_icc_profiles>`_ of free and well-behaved color profiles. Her
`website <https://ninedegreesbelow.com/>`_ contains a wealth of technical
information on color space theory and practice, and a great source of
inspiration for applying color processing to images. All the profiles there
are released under the `Creative Commons Attribution-Share-Alike Unported license, version 3.0 <https://creativecommons.org/licenses/by-sa/3.0/legalcode>`_.

The built-in profiles are intended to provide a sufficient set of color
spaces for most purposes: sRGB for web export and as a default display
profile, Rec2020 as a wide gamut profile that looks like it may become the
next standard for high quality wide gamut monitors, and of course a range of
Gray profiles to match the RGB ones.

* sRGB

  - Linear profile sRGB_elle_V4_g10
  - sRGB TRC profile sRGB_elle_V4_srgbtrc
  - ICC sRGB Preference profile with perceptual lookup tables (for display only)
  - sRGB TRC profile sRGB_elle_V2_srgbtrc (for export)

* Rec2020

  - Linear profile Rec2020_elle_V4_g10
  - Rec709 TRC profile Rec2020_elle_V4_rec709
  - Rec709 TRC profile Rec2020_elle_V2_rec709 (for export)

* Gray

  - Linear profile Gray_elle_V4_g10
  - sRGB TRC profile Gray_elle_V4_srgbtrc
  - sRGB TRC profile Gray_elle_V2_srgbtrc (for export)
  - Rec709 TRC profile Gray_elle_V4_rec709
  - Rec709 TRC profile Gray_elle_V2_rec709 (for export)

Note that the native TRC versions of the profiles are provided in two
versions (version 4 and version 2). Version 4 provides better functionality
(especially for high bit depth images) including parametric TRCs which
avoid quantization. However version 4 profiles are not universally supported
yet. So when exporting a file for use in other software it is safest to embed
a V2 profile.

The Gray profile is available with TRCs to match the two built-in RGB color
spaces, and also with a linear TRC.

Linear TRC Profiles
-------------------
Sensor data starts off as a linear representation of light, so why don't we
assign a linear color profile to unstretched images such as a newly stacked
stack? Well, technically perhaps we should. Certainly if you wish to do so
you can, and nothing bad will happen. You can do this using the color management
dialog, and there is also a preference (Pedantically Assign Linear ICC Profiles)
that will assign a linearized version of the working ICC profile when a profile
is auto-assigned to a newly loaded image (if it shows no sign of having been
previously stretched), a stacked image or a newly composited image.
However there is generally no benefit in doing so. To understand why, we need to
understand a fundamental difference between astrophotography and normal
photography processing.

In normal photography the whole image is well exposed, with darker shadow
regions and lighter highlights. At the raw stage the image is converted from
linear light to a working profile, but this is reversible and in fact is
frequently reversed as many editing operations (color blending, noise reduction,
etc.) are best done in linear light.

However in astrophotography, all the detail is very deep in the shadows and we
have to apply a very strong stretch indeed in order to produce an image that
looks pleasing. This stretch is much more extreme than the mild change of gamma from 1.0
to 2.2 going from linear light to sRGB, and **it is not easily reversible** -
especially if multiple stretches are applied. So we do still need to do the same kind
of operations (color correction, noise reduction, deconvolution etc.) on linear
light (and a lot of other things that don't even exist in normal photography should
be done to linear images too: star modelling, star removal etc.)

It isn't necessary to set a linear color profile to do these operations - algorithms
like noise reduction etc. aren't even aware of color profiles. They just apply
themselves to the provided data.

So it is safe to assign the color profile to an image at any stage of editing,
though it usually makes most sense to do it just before stretching. The key thing
to remember is that with astrophotography, **all the operations that need to be
done on linear images should be done before any stretching**. Generally, stretching
and any final tweaks to color balance, saturation etc. should be just about the
last editing operation that is done to an image. And at that point you should be
editing it in your chosen color space, which will give as consistent as possible
a look to the image when viewed in any color managed application or output device.

Color Space Recommendations
---------------------------
This section explains in a bit more detail why Siril offers the built-in
color spaces it does.

sRGB is the *de facto* standard for the web. As mentioned above, if you want
to guarantee your images look good in all web browsers, as well as all third
party applications that may or may not support color management, you really
need to export in sRGB. It's a good fit for most SDR monitors, and in the
absence of proper color management images that looked right in previous
versions of Siril should be considered to be in sRGB.

However, consider the CIE 1931 color space again. The horseshoe shape forming
the boundary of the color space represents *pure spectral tones*. If you
have a perfectly monochromatic light source like a laser or one of those
sodium yellow street lights, you're looking at the boundary of CIE 1931.
As astrophotographers these pure monochromatic sources are actually really
important to us, especially if you do narrowband imaging. Those colors can't
be represented accurately in sRGB. The proportion of visible colors that can
be represented in sRGB is actually rather small. We can do better.

So, if a wide gamut is better why is the wide gamut color space built into
Siril Rec2020 rather than an even wider one? AllColorsRGB, ACES 2065 and
ProPhoto RGB are all much bigger - ACES 2065 can represent all parts of the
visible spectrum.

The problem is, the way they do that is by setting their primary colors -
the "100% red", "100% green" and "100% blue" values - *outside* the visible
range. This is problematic especially for narrowband imagers who want to
assign filters to primary colors: you end up with imaginary colors in your
image and always have to rely on the intent of a color space transform to
do the right thing in turning them into something visible. Also some of
these color spaces are linear, such as ACES 2065. This is good for CGI
artists but not so good for us, because of the slow display transforms
when working with linear spaces. Siril can optimise some transforms from linear
color spaces over and above what lcms2 does, but only if the RGB primaries are
the same, e.g. if transforming linear sRGB to g22 sRGB). So Rec2020 was chosen
as it provides a nonlinear color space with the widest gamut without having
imaginary primaries.

My recommendation, if you aren't already sold on a particular color space,
is to try Rec2020 as a color space for editing, as well as for sending images
to high-quality printing services that can cope with color managed images, and
sRGB for web export. But there are also other good choices available using ICC
profile files that you can use instead, such as Adobe RGB and ProPhoto RGB (ROMM),
if you prefer.

Third Party Color Profiles
--------------------------

**Monitor Profiles**
Siril includes the ICC's v4 sRGB monitor profile with support for Perceptual
rendering LUTs and uses this by default. A different monitor profile may be
used in its place, but note that only the intents supported by a given profile
will be available - many widely available sRGB profiles only support the Relative
Colorimetric intent.

**Soft Proofing Profiles**
There are a wide variety of press standards and papers in the world and
each combination requires its own ICC profile. Siril cannot provide all of
these. Therefore in order to use the soft proofing display mode you will need
to provide the appropriate soft proofing ICC profile in the Preferences
window.

You may wish to work in a color space other than the inbuilt ones, for
example ProPhoto RGB. This is supported, but you will need to provide
ICC profiles yourself. The Preferences window provides controls to set
a RGB profile and a Gray profile with a matching TRC (essentially, with the
same gamma).
If you intend to export files in your chosen color space it is recommended
that the profile you provide is a V2 ICC profile, for maximum compatibility with
other software.

.. figure:: ../_images/color-management/ProPhoto_working_space_howto.png
   :alt: ICC profiles required to use ProPhoto
   :class: with-shadow

   The image shows how to set up a ProPhoto working space using Elle Stone's
   profiles. The standard gamma of ProPhoto RGB (ROMM) is 1.8, so as well as the
   ProPhotoRGB profile, we add the Elle Stone Gray profile with gamma = 1.8. You
   can download all of Elle Stone's profiles `here <https://github.com/ellelstone/elles_icc_profiles>`_.
   If you don't have the ProPhotoRGB profile, you can also use Elle Stone's
   "LargeRGB-elle-v2-g18" profile, which is exactly the same except she has
   avoided use of the term "ProPhoto RGB" for possible copyright reasons.

HDR Display Limitations
-----------------------
Wide gamut display color spaces such as Rec2100 with HLG or PQ TRCs may need
greater than 8 bit pixel buffers to display smoothly. Unfortunately, Siril uses
the Cairo graphics library for display and Cairo cannot yet process pixel buffers
wider than 8 bits. The impact of this is likely negligible for most users.
However, if you are lucky enough to be using a >1000nit HDR display capable of
displaying wide gamut color spaces and are using certain combinations of third
party image and custom monitor profiles then for certain images, you may
experience minor color staircasing artefacts. These are only a display issue and
won't affect the printed appearance of your image, or even its appearance when
transformed to a narrower gamut or viewed on operating systems that do support
high bit depth pixelbuffers.

For the moment there isn't anything we can do, however if Cairo adds high bit
depth support in the future then there may be room for improvement.
