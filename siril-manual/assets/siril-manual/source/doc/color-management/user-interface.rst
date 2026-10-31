Color Management Status
=======================
The color management status of the loaded image is indicated by the Color
Management status icon in the bottom left hand corner of the window. The icon is
fully saturated when the image is color managed, and desaturated when the image
is not color managed. The tooltip provides more detailed status information if
the image is color managed: it shows the working space and monitor profiles.

Left clicking the status button accesses the Color Management Dialog. Right
clicking the tool toggles the ISO 12646 color assessment display mode. Both are
described in more detail below.

Just above the color management status button is a new menu for image checks.
This includes the existing cut check for highlighting pixels that exceed the gui
hi slider, and adds a new gamut check, which highlights pixels that are out of
gamut for the soft proofing profile in bright magenta.

.. figure:: ../_images/color-management/icc_status_button.png
   :alt: color management tool in the menu
   :class: with-shadow
   :width: 50%

Color Management Dialog
=======================

The Color Management tool is accessed by clicking on the Color Management status
icon. (It can also be accessed via the :guilabel:`Tools` menu.)

.. figure:: ../_images/color-management/icc_dialog_in_menu.png
   :alt: color management tool in the menu
   :class: with-shadow
   :width: 50%

The tool itself is shown below.

.. figure:: ../_images/color-management/icc_dialog.png
   :alt: dialog
   :class: with-shadow
   :width: 100%

The top part of the tool shows information about the current ICC profile
assigned to the image. The description, manufacturer details and copyright
notice are shown.

Underneath this are choosers that allow you to choose a different profile,
either one of the built-in profiles using the drop-down menu on the left or
any ICC profile you wish to load from a file using the filechooser on the right.

.. warning::
   When loading a profile from a file, the profile type must be either RGB or
   Gray to match the loaded image. Assigning or converting images to other color
   spaces such as XYZ, CIE La*b* or CMYK is not supported, however color spaces
   such as CIE La*b* may be used internally by some image operations where
   required.

.. tip::
   You can convert a RGB image to a Gray profile. This will result in a mono
   image with the appropriate tone response curve (TRC) transform and contributions
   from the 3 R, G and B channels determined by the white points of the two
   profiles. Converting a Gray image to a RGB profile will result in a 3-channel
   image with the appropriate TRC transform and all pixel components equal.

At the bottom of the window, the tool shows the same kind of information for
the target profile that it does for the original profile at the top of the
window. This helps in checking you've loaded the right one.

Toolbar
-------

* The :guilabel:`Export` button simply exports the current image profile to
  a file in the working directory.
* The :guilabel:`Remove` button removes the ICC profile from an image.
* The :guilabel:`Assign` button assigns the selected ICC profile to the image, 
  without performing a color space conversion. This is useful if the image has
  an incorrect color profile embedded.
* The :guilabel:`Convert` button converts the image to the selected ICC profile
  and assigns the profile to the image. This is useful if you wish to transform
  the image from its current color space to a different one.

.. warning::
   Use of the :guilabel:`Remove`, :guilabel:`Assign` or :guilabel:`Convert`
   buttons (or the Siril commands :ref:`icc_remove <icc_remove>`,
   :ref:`icc_assign <icc_assign>` or :ref:`icc_convert_to <icc_convert_to>`)
   will clear any ROI that is set.

ISO 12646 Image Assessment Mode
===============================
ISO 12646 defines optimum viewing conditions for assessing color. In summary,
to get the best assessment of the colors in your image the best viewing conditions
are provided by a uniform neutral gray background called D50. Technically this
should even extend to the walls in your room, but Siril can't control that!
Siril does provide an image assessment mode that approximates ISO 12646
recommendations, however. It is available by right clicking on the color
management button in the bottom-left of the screen, and does not require the
image to be color managed to use it.

.. figure:: ../_images/color-management/iso12646.png
   :alt: dialog
   :class: with-shadow
   :width: 100%


This mode is intended as a final check of the colors of your image, so it sets
the preview mode to linear with the sliders at 0 and 65535 (full range). It also
hides the panel and sets the zoom so that the full image is visible and centered
with an ample border around it, and sets a moderate white border around the
image to provide a visual white reference, surrounded by a background of D50 gray.

Ideally you should also choose a neutral gray GTK theme, such as the excellent
`Equilux <https://www.gnome-look.org/p/1182169/>`_ theme.

This mode automatically disables itself if the zoom is changed or the panel made
visible again, and it can be toggled off by right clicking a second time on the
color management button. The view will revert to its previous zoom settings and
panel state.

Commands
========
Most Siril commands do not engage with color management except that they do not
prevent existing color profiles from being preserved in an image.

The exceptions are three specific color management commands that can be used to
assign, convert or delete the ICC profile in an image.

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/icc_assign_use.rst

   .. include:: ../commands/icc_assign.rst

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/icc_convert_to_use.rst

   .. include:: ../commands/icc_convert_to.rst
   
.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/icc_remove_use.rst

   .. include:: ../commands/icc_remove.rst

