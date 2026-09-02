Soft Proofing
=============

Display Soft Proofing
---------------------
Siril supports soft proofing. This provides a visualisation in your monitor's
color space of what your image will look like when printed on a specified
press standard. To use output device soft proofing you must specify a soft proofing
profile in the Settings dialog.

If no output device profile is specified, or if it is made inactive using the
preference check box, soft proofing will use the display profile as the proofing
target. This enables the :guilabel:`gamut` image check in the Image Checks menu,
which shows the pixels that are outside your display's color gamut. When gamut
checking is active, out-of-gamut pixels will be shown in garish magenta.

Press Export
------------
Siril does not support exporting in CMYK formats for print / press
purposes. Most photo printing services expect images to be provided in RGB
formats and printer drivers do a much better job of converting RGB to a blend of
the specific inks used by each printer than a simplistic application-level
RGB-to-CMYK conversion. `Unless you know exactly why you need to convert an imge to CMYK, you almost certainly don't need to. <https://www.youtube.com/watch?v=wX3ZcAiLg-4>`_
However, in order to minimize gamut clipping when your printing service
converts the image to a CMYK profile for printing, it is worth using a wide
gamut RGB profile such as the built-in Rec2020.
