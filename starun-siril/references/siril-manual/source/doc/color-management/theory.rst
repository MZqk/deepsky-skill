Color Management Theory
=======================

Introduction
------------
Color management is the method used to ensure that the colors of an image
always look consistent, however the image is viewed. This is done using
color profiles (ICC profiles, named for the International Color Consortium).
Each display type and printer / paper combination has its specific color
profile. The image also has a defined color space, and by transforming the
image between different color spaces when viewed on two different types of
display, or when printed, we ensure it always looks the same (or at least as
close to "the same" as possible).

CIE 1931 Color Space
--------------------
The CIE 1931 color space (CIE 1931) maps all colors that are discernable by
the human eye. As the human eye has three types of cone cell (color
receptors), so CIE 1931 has three parameters (X, Y and Z). Note the
parameters X, Y and Z don't directly correspond to the response of each
type of cone cell, but they do allow 3 degrees of freedom. For more details
on this color space, see here: https://en.wikipedia.org/wiki/CIE_1931_color_space

This isn't a color space that is used to store image data most of the time
but it matters because it is used as an intermediate color space, and
because it defines what are "real" colors (those visible by human eyesight)
and what are "imaginary" (those we can't see). CIE 1931 can be visualised
as a horseshoe shape.

.. figure:: ../_images/color-management/CIE1931xy_blank.svg
   :alt: CIE 1931 Color Space
   :class: with-shadow

   By BenRG - CIExy1931.svg, Public Domain, `https://commons.wikimedia.org/w/index.php?curid=7889658 <https://commons.wikimedia.org/w/index.php?curid=7889658>`_

Note that the outside of the horseshoe defines the color of pure
monochromatic spectral lines. This will be important later.

RGB Color Spaces
----------------
Our working color spaces are generally based around the color axes Red,
Green and Blue. This corresponds roughly to how our eyes work and also
to the phosphors in the emitters in a screen. However there are a great
many RGB color spaces, each suitable for different purposes, and each
with advantages and disadvantages. You can see the variety of color spaces
in this diagram, which shows them defined in relation to CIE 1931.

.. figure:: ../_images/color-management/CIE1931-comparison.png
   :alt: CIE 1931 Color Space compared with other RGB color spaces
   :class: with-shadow

   By Myndex - Own work, CC BY-SA 4.0, `https://commons.wikimedia.org/w/index.php?curid=116654642 <https://commons.wikimedia.org/w/index.php?curid=116654642>`_

The R, G and B channels of an image are defined in terms of a color space formed
by three primary colours located within this CIE 1931 space, plus a white point,
plus a tone response curve (TRC) for each channel that sets the gamma of each
channel. *(Actually, TRCs can be more complex than a simple gamma curve but most*
*are at least approximately a gamma curve.)*
The diagram above shows that some color spaces are considerably bigger than
others: that is, they can represent a greater portion of the visible colors.

Gray Color Spaces
-----------------
In astrophotography we quite often deal with monochrome images. These clearly
can't have a RGB color profile. Instead they get a Gray color profile. This
defines the image's tone response curve (TRC) in exactly the same way as the RGB
color profiles define the TRC of each channel. So whatever type of display or
printer you're using, monochrome images can still be faithfully reproduced and
look the same (or as close to it as possible) in different output media.

Display Color Space
-------------------

The "standard" color space used for computer graphics has traditionally been
sRGB. This was created by HP and Microsoft in 1996 and subsequently
standardized as IEC 6166-2-1:1999. This color space codifies the color gamut
achievable by displays of the time. As can be seen in the figure it can only
represent a small portion of the total visible color space defined by CIE
1931. However, many monitors today can still do little better than sRGB and
it is the current standard color space for the World Wide Web (WWW), so even
if you don't want to use it for any other purpose it is necessary to use it
as the export profile for any image you want to display consistently in
web browsers. It is also the assumed color space for any image format that
lacks color profile support, and for any application that lacks color
management.

However it is obvious that there are other RGB color spaces with much larger
gamuts, for example Adobe RGB, Adobe ProPhoto, Rec2020. These can represent
a much larger portion of the real colors defined in CIE1931. They can also
represent a much larger color gamut than most monitors. So why would these
be of interest?

Well, for one, monitors are getting better. Wide gamut monitors are still
not mainstream but they are becoming less rare, and more affordable. If you
edit and view your images on a wide gamut monitor, you will benefit from
richer color by using a color space with a bigger gamut. Some modern phones
can display the whole of the P3 gamut, which is considerably bigger than sRGB
and allows a richer range of colors to be displayed.

Print Profiles
--------------

Print profiles can extend beyond sRGB in some areas (and may not cover all of
sRGB in other areas). By editing in a wide gamut color space, that richness of
color - even if it can't be shown in the color-transformed output on your
display - is still there and will be evident in good quality prints.

Transforms
----------
The point of color spaces is that an image will often appear in outputs with
different color spaces at different points in its life. It may be created on
a professional-quality high gamut monitor, it may be viewed by the public
on basic sRGB monitors, and it may be printed on a range of different printers.
Each of these devices is capable of reproducing different color gamuts but we
want the image, so far as possible, to look consistent across all of them. This
is done by color space transforms. Unfortunately, because color spaces are
different, we have to deal with the problem of how to represent in one color
space colors that are "out of gamut" having been transformed from a different
color space.

Color Transform Intents
-----------------------

The answer to the problem above is "intents". Whenever you are viewing an image
on a device / paper that has a different color space to your working profile it
has a color space transform applied to it. This isn't as straightforward as an
arbitrary 2-way mapping between two sets of coordinates.
Say you're working in Rec2020. Consider the transform to your monitor color
profile. Remember, your monitor (let's assume it is approximately a sRGB
display) cannot display as many colors as are representable in Rec2020. So the
color space has to map all the colors in Rec2020 onto colors in sRGB. How it
does this is determined by the Rendering Intent.

You may want to choose one intent for rendering your image for display but a
different intent for other purposes. The different intents defined by the ICC
and available in Siril are described below.

.. tip::
   * **Perceptual** The perceptual intent scales the input color gamut into the
     output color gamut. All colors are changed, but the relationships between
     colors are maintained. Generally, color is less saturated in the
     output color space, but the saturation compared with other colors is
     maintained.
   * **Saturation** The saturation intent similarly scales the input color gamut
     into the output gamut, but it does so in a way that prioritises saturation.
     This is usually more suitable for vibrant graphics than for photography.
   * **Relative Colorimetric** The relative colorimetric intent reproduces
     in-gamut colors accuately, however it clips colors that are out of gamut to
     the closest point on the triangle representing the boundaries of the target
     color profile.
   * **Absolute Colorimetric** The absolute colorimetric intent is really only
     of use in pre-press proofing.

ICC Color Profiles and Intent Availability
------------------------------------------
The ICC defines the four intents listed above (as well as some others used
primarily for ink control in press applications) but not all ICC profiles support
all of the intents. Most of the color profiles built into Siril are matrix
shaper profiles. These are very good as working color space profiles but mostly
they only support the Relative Colorimetric intent. That's fine, because mostly
that's the intent we want to use when converting between color spaces. (See below
for an explanation of why.) If you set an intent in preferences that your ICC
profile doesn't support, Siril will fall back to one that is supported - generally
Relative Colorimetric.

Display
-------
For display you generally want to use Relative Colorimetric. This makes the
image display as consistent as possible with what anyone else with a color
managed display will see, or what it will look like when printed to a color
managed print workflow.

You *may* occasionally want to switch to Perceptual. This will not give you an
accurate representation of color, but it will show the relative differences in
color that get clipped when shown on your display. You can check what parts of
the image are outside your display gamut using the gamut check tool in the
image checking menu - the pixels that are outside the display gamut are shown in
bright magenta. Siril provides a built-in sRGB profile with support for the
Perceptual intent and will automatically use it if Perceptual intent is selected
in the Preferences tab (and if no custom monitor profile is active). If you do
wish to use a custom monitor profile and the Perceptual intent, it is up to you
to ensure your profile supports this.

Conversion and Saving
---------------------
For converting files and saving them you almost always should use Relative
Colorimetric. This preserves color correctly. Relative Colorimetric does typically
clip colors to the gamut of the color space it is converting to, but you get
a consistent result. In fact, when dealing with 32 bit floating point images
Siril uses an unbounded transform - that is, instead of clipping colors it permits
negative values. They will need clipping at some point - when displayed, for example -
but they can be saved, and applying a color transform in the reverse direction brings
back the original data. Nothing is lost.

Soft Proofing
-------------
For soft proofing you probably want Relative Colorimetric. Absolute Colorimetric
may be of use in simulating exactly what your image will look like on a given
printed medium as it simulates one medium's white point in another (so if you're
proofing what an image will look like on the dingy yellowish-white of newsprint
then Absolute Colorimetric will try to simulate that via your monitor), but for
soft proofing a wider gamut color space against your display profile then you
likely want Relative Colorimetric.
