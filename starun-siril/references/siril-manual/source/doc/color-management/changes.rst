Changes caused by introduction of color management
==================================================

Siril is now doing color management properly.

That means it wasn't really doing it properly in the past. Siril wasn't unusual
in this regard: many programs do not provide color management, but rely on
assumptions that everything is done in an sRGB color space. This is fine most of
the time, but it led to reports of issues when interchanging images between Siril
and properly color-managed graphics applications.

Some users reported that in certain circumstances, images saved by Siril
looked different in other software than it did in Siril. For images exported
from the new color-managed Siril, that should stop. Images will have a
consistent look in all software with a correct color management implementation.

However, if you have stretched images saved as FITS in a previous version
of Siril and you open them in color-managed Siril, they may in some circumstances
look pale and washed out. Don't panic! Here is an explanation of why it happens,
and how it can very easily be avoided / fixed.

Why does it happen?
-------------------

When Siril reads a FITS image without an embedded ICC profile, if the "Auto
assign ICC profile" preferences are set it tries to work out whether the image
has been stretched or not. It can only do that by checking the HISTORY entries
in the FITS header. If it can detect stretch functions have been applied, then
it assumes the image has been stretched on a sRGB monitor and applies the
relevant profile. However older versions of Siril didn't always add the history
for every command, and other software may or may not add history but Siril can't
recognise all the stretch keywords that might be used by all other software, so
it may be unable to detect that an image has been stretched. The fallback
assumption we choose to make is that a FITS image without an embedded ICC
profile is **linear** data.

However, your stretched FITS isn't really linear data. It has been stretched,
and you have in effect been stretching it so that it looks right in your
monitor's color space, which is probably approximately sRGB.

When this has a linear ICC profile assigned to it, the display routines then
apply a color space transform from linear space to the display profile, in
effect stretching it a second time.

How to prevent it?
------------------
This problem will only occur if you have the option "Auto assign preferred color
space on load" enabled. If this option is disabled, Siril will not assign any
color profile when the file is loaded. You can then assign one manually using
the Color Management dialog.

How to fix it?
--------------

If you loaded an image with the Auto assign option enabled and the wrong profile
is assigned (or if the wrong profile is assigned for any other reason, for
example if you loaded a fle saved by other software that has assigned a wrong
profile) fixing the problem is also very easy. All you need to do is open the
image, go to the Color Management dialog and select "sRGB (standard sRGB TRC)"
from the drop down selection (or "Gray (sRGB TRC)" if the image is mono). Click
the :guilabel:`Assign` button (**not** :guilabel:`Convert to`) and Siril will assign the
selected color profile to your image.

That's it. The display will now look correct and when you save the image, and
the appropriate color profile will be embedded.
