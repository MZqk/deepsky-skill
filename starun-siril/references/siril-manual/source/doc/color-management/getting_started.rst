Getting Started
===============

Welcome to Siril's new color management system! Color management can seem
complex, so this page aims to provide the absolute basics you need to know to
get started. It will cover two setups to make your life easier.

Default Setup
-------------
The default settings provide similar behaviour to previous versions of Siril.

The monitor is set as sRGB and the preferred color space is also set as sRGB.
This means that there is no transform required between them and therefore no
processing overhead for images using the default profile.

When images are loaded, if they have an associated color profile that profile
will be preserved. This does mean that a display transform is required and
thus causes a small processing overhead, but minimizes change to the loaded file.

If images have no color profile, they are assigned the preferred color space
(sRGB) when first stretched. This is the point at which you should be using the
linear viewer mode to make the image appear on the screen the way you want it
to look, and therefore is the point at which color management becomes important.

Images will be exported to all 8-bit and 16-bit formats using the sRGB color space.

This default setup is perfectly fine if your main target is image production for
the web, or for viewing on most typical computer monitors.

.. figure:: ../_images/color-management/preferences.png
   :alt: Color management default preferences
   :class: with-shadow
   :width: 100%

Photo Print Setup
-----------------
If you intend to produce photo prints of your work, consider the following setup.

.. figure:: ../_images/color-management/preferences-setup2.png
   :alt: Color management preferences for a wide gamut workflow
   :class: with-shadow
   :width: 100%

The monitor remains sRGB in the screenshot as no custom monitor profile is set,
but if you have access to the necessary hardware to calibrate your monitor then
you could set a custom monitor profile tailored to your display. (How to do this
is outside the scope of Siril, but you may wish to look into the Free Software
product `ArgyllCMS <https://www.argyllcms.com/>`_.)

The preferred color space is Rec2020. This wide gamut color space is
better suited to a workflow targeted at printed output as the RGB to CMYK
conversion done by the printer is less likely to clip the CMYK profile.

When images are loaded, if they have an associated color profile they will be
converted to Rec2020. If images have no color profile, they are assigned the
preferred profile (Rec2020) when first stretched.

Rendering Intent
----------------
In both these suggested setups the rendering intent is set to Relative
Colorimetric. This is what you should always use for editing your image, as with
Relative Colorimetric the in-gamut colors stay true and this provides the
most accurate view of what your image looks like overall.

However the Relative Colorimetric intent clips out-of-gamut colors. You won't
see details of coloration that are out-of-gamut.

You may therefore sometimes wish to change the rendering intent to Perceptual.
This will smoothly remap colors from the working space to the display. All
coloration detail is shown, but at the cost of a loss of overall saturation. This
is unavoidable, as the larger gamut gets squashed to fit into the smaller gamut.
Therefore the Perceptual intent does not provide a true visualisation of your
image: to repeat, it should **not** be used for editing, **only** for exploring
detail in your images that sits outside the sRGB gamut. See the discussion `here <https://www.gimpusers.com/mailmsg.php?853694a0-e0d6-fd86-1582-1d890a9f90a0%40ninedegreesbelow.com>`_.

Images will be exported to 8-bit formats as sRGB, but to high bit depth formats
as Rec2020 (and you should save your final version in a printing industry standard
16-bit format, i.e. probably TIFF).
