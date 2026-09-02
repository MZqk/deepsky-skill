Main interface
==============

When launching Siril, the main interface opens.

.. note::
   Click anywhere onto the image below to display its functions.

.. _main-interface_window:
.. image:: ../_images/GUI/main-interface_window.svg
   :alt: Main interface
   :width: 100% 

.. _window-image-area:

.. rubric:: Image area

This area displays the currently loaded image. Click on :guilabel:`Red`, 
:guilabel:`Green` or :guilabel:`Blue` to switch between the different layers 
(color images only, a single :guilabel:`B&W` tab is available for mono images).

Right-click on the image to display a contextual menu:

.. image:: ../_images/GUI/main-interface_image-area_right-click.png
   :alt: Right-click

.. tip::
   When no image is loaded, double-clicking on the image area pops the 
   :guilabel:`Open` dialog.

Undo
----
Undoes the most recent operation.

Redo
----
Redoes the most recently undone operation.

PSF
---
Computes a PSF for the current selection and carries out quick photometry.

PSF for the Sequence
--------------------
Computes a PSF for the current selection for all images in the current sequence.

Pick a Star
-----------
Applies star detection to the current selection. The result is opened in the
:guilabel:`Dynamic PSF` dialog. Note this operation is more lenient than the 
full image star finder routine as it assumes that you have picked a selection 
containing a star: it can therefore be used to pick stars that have been missed 
by the full image star finder function.

Selection
---------
Applies constraints to the selection box.

Crop
----
Provides an interface to the :guilabel:`Crop` and :guilabel:`Crop & rotate` 
functions.

.. tip::
   If a CFA image is loaded, the crop will be constrained to a CFA boundary so that
   the effective CFA pattern is unchanged for future operations. For Bayer CFA patterns
   this means the crop selection will snap to a multiple of 2x2 and may shift by up to 1
   pixel in x and y directions in order to align with the Bayer pattern start. For
   X-Trans patters the crop selection will snap to a multiple of 6x6 and may shift by up
   to 5 pixels in each direction.

.. warning::
   Rotating a CFA image by arbitrary angles will destroy the CFA pattern: you should
   progress your workflow to the point where the CFA pattern has been debayered or
   Bayer drizzled before doing anything that applies rotation or scaling.

ROI
---
Provides an interface to set or clear the Region of Interest for ROI processing.
Note: this menu entry is not available if the ROI mode is set to "Automatic from
selection" in the UI preferences tab.

RGB align
---------
Aligns the image RGB channels. This supports a range of registration methods for
alignment:

* **2-pass global star alignment** This uses the 2-pass global star alignment routine
  with COG framing (so the image dimensions are preserved). This algorithm adjusts
  with 8 degrees of freedom so it can correct for image shift, rotation and skew.
  This is the recommended algorithm for images containing stars. This algorithm is
  global and does not require a selection to be made.
* **KOMBAT alignment** This uses the KOMBAT alignment method. It is primarily
  intended for planetary alignment, but can also work for deep space images. It
  provides shift registration only, but is fast. This algorithm requires a selection
  to be made.
* **One star registration** This is an older algorithm that provides shift-only
  registration based on the movement of a single star. This is very fast but the
  registration cannot cope with as many degrees of freedom as the 2-pass global
  star alignment. It may be suitable if you are certain you only need to correct
  for image shift. This algorithm requires a single star to be selected.
* **Image pattern alignment** This method performs alignment using the image
  Fourier transforms. It works for deep space and planetary images but provides only
  shift registration and is quite slow. This algorithm uses a square selection: a
  selection must be made, and non-square selections will be corrected to be square.

.. warning::
   The tool is **RGB** align. It is not intended for, and will not work for,
   aligning unaligned **LRGB** compositions. In order to do this you must either
   align the layers to be composed before composition, or align using the RGB
   alignment tool.

:ref:`Back to figure <main-interface_window>`

.. _window-open:

.. rubric:: Open

Click on these icons (from left to right) to:

* open a file
* open a recent file
* change the :ref:`working directory <cwd:Working Directory>`

:ref:`Back to figure <main-interface_window>`

.. _window-livestack:

.. rubric:: Livestack

Click on this button to start a :doc:`../Livestack` session.

:ref:`Back to figure <main-interface_window>`

.. _window-undo:

.. rubric:: Undo/Redo

Use these buttons to undo/redo last actions. This is only available if last 
action has been done through GUI, not by typing a command.

:ref:`Back to figure <main-interface_window>`

.. _window-image-processing:

.. rubric:: Image processing

Click on this button to display the :ref:`Processing <processing:processing>` 
menu.

:ref:`Back to figure <main-interface_window>`

.. _window-tools:

.. rubric:: Tools

Click on this button to display the :ref:`Tools <GUI/tools-menu:Tools Menu>` menu.

:ref:`Back to figure <main-interface_window>`

.. _window-scripts:

.. rubric:: Scripts

Click on this button to display and launch the :ref:`scripts <scripts:Scripting>`.

:ref:`Back to figure <main-interface_window>`

.. _window-info-bar:

.. rubric:: Information bar

This bar displays the current version of Siril and the path to the current
:ref:`working directory <cwd:Working Directory>`. 

* To the right, informationabout available RAM and disk space id also given.
* You can change the available number of threads used by Siril using the +/- 
  signs.

:ref:`Back to figure <main-interface_window>`

.. _window-save:

.. rubric:: Save

These buttons are used to save your results:

* save (overwrites) the current image.
* save with a different name and/or extension.

  .. figure:: ../_images/GUI/save_dialog.png
     :alt: starnet dialog
     :class: with-shadow
     :width: 100%

     Save dialog box.
     
  The drop-down list at the bottom right allows you to choose the type of image
  recorded. It automatically adds the extension to the file name. However, by 
  staying in the :guilabel:`Supported Image Files` mode, it is possible to add 
  any extension supported by Siril by hand and it will save in the correct file
  format.
   
* take a snapshot of the current view (as seen on the screen, meaning preview 
  strecthing, if any, is applied). There are two possible options. Either the 
  snapshot is saved in the clipboard, or directly copied to the disk in the 
  :ref:`working directory <cwd:Working Directory>`.
* change the bitdepth of the current image. The choice is between 16-bit and 
  32-bit.

:ref:`Back to figure <main-interface_window>`

.. _window-burger-menu:

.. rubric:: Burger menu

Opens the main menu, also called :ref:`burger menu <gui/burger-menu:burger menu>`.
Gives access to :doc:`Preferences <../preferences/preferences_gui>`, the 
documentation and much more.

:ref:`Back to figure <main-interface_window>`

.. _window-tabs:

.. rubric:: Tabs

Selects one of the tabs. You can also switch between the different tabs using 
:kbd:`F1` to :kbd:`F7` shortcuts.

More details can be found there:

+---------------------------------------------------------------------+----------+
| Tabs                                                                | Keys     |
+=====================================================================+==========+
| :ref:`Conversion <preprocessing/conversion:conversion>`             | :kbd:`F1`|
+---------------------------------------------------------------------+----------+
| :ref:`Sequence <sequences:loading a sequence>`                      | :kbd:`F2`|
+---------------------------------------------------------------------+----------+
| :ref:`Calibration <preprocessing/calibration:calibration>`          | :kbd:`F3`|
+---------------------------------------------------------------------+----------+
| :ref:`Registration <preprocessing/registration:registration>`       | :kbd:`F4`|
+---------------------------------------------------------------------+----------+
| :ref:`Plot <plot:plotting feature>`                                 | :kbd:`F5`|
+---------------------------------------------------------------------+----------+
| :ref:`Stacking <preprocessing/stacking:stacking>`                   | :kbd:`F6`|
+---------------------------------------------------------------------+----------+
| Console                                                             | :kbd:`F7`|
+---------------------------------------------------------------------+----------+


:ref:`Back to figure <main-interface_window>`

.. _window-tab-window:

.. rubric:: Tab window

Displays the specifics of the currently selected tab.

:ref:`Back to figure <main-interface_window>`

.. _window-mouse-behaviour:

.. rubric:: Mouse behaviour

Siril uses GTK primary, secondary and middle mouse button mappings. These are
usually mapped to left, right and middle (:kbd:`scroll` wheel button) physical 
mouse buttons, however they may be mapped differently in specific setups (e.g.
left-handed users may have configured swapped left and right buttons).

#. **Primary mouse button**

   The primary mouse button is used for a number of purposes:

   * To select areas of the image
   * With :kbd:`Ctrl` pressed (or :kbd:`Cmd` on MacOS), to pan the image
   * When the intensity profiling tool is active, to drag the line along which
     the intensity profile is to be drawn, and to select points corresponding to
     known wavelengths / wavenumbers in the spectroscopy setting
   * In the RGB composition tool, to select the rotation center when using manual
     alignment
   * In the gradient removal tool, to draw samples
   * In photometry mode, to perform photometry on a star
   * To select the positions for the registration previews
   * Double clicking the primary mouse button with no image loaded will bring up
     the :guilabel:`Open` dialog.

#. **Secondary mouse button**

   The secondary mouse button is used for a number of purposes:

   * To bring up the image context menu (except when in photometry mode)
   * In the gradient removal tool, to remove samples
   * In photometry mode, to perform photometry on the selected star for all frames
     of the currently loaded sequence (if no sequence is loaded, this has no effect)

#. **Middle mouse button**

   The middle mouse button is used for the following purposes:

   * To pan the image at the current zoom level
   * With :kbd:`Ctrl` pressed (or :kbd:`Cmd` on MacOS), to make a square 
     selection suitably sized for
     photometry
   * Double clicking adjusts the zoom level to a preset value. The behaviour is
     configurable in the :guilabel:`User Interface` tab in the 
     :guilabel:`Preferences` dialog. The choices are:
     
     - Always zoom to fit
     - Always zoom to 100% centered on the mouse cursor
     - Toggle between zoom to fit and zoom to 100% centered on the mouse cursor

#. **Scroll wheel**

   * The :kbd:`scroll` wheel is used to adjust the zoom level.

.. _window-command-line:

.. rubric:: Command line

Type in a :ref:`command <commands:commands>` and press :kbd:`Enter`.

* You can press the button at the end of the line to get some help on the 
  usage.
* You can also abort the process being currently executed by clicking on the
  :guilabel:`Stop` button.

:ref:`Back to figure <main-interface_window>`

.. _window-expand:

.. rubric:: Expand

Click on this bar to expand/retract the whole tab/tab window area.

:ref:`Back to figure <main-interface_window>`

.. _window-image-sliders:

.. rubric:: Image sliders

Use the top and bottom sliders to adjust the white and black points of the 
previewed image (in Linear mode).

.. tip::
   Click on the name of the `Image` or `Sequence` loaded to copy its name to 
   the clipboard (useful to paste in a command).


:ref:`Back to figure <main-interface_window>`

.. _window-preview:

.. rubric:: Preview mode

Select the preview mode for the loaded image, between the following choices:

* Linear
* Logarithm
* Square root
* Squared
* Asinh
* Autostretch (tick the High Definition box to use a deeper (up to
  24-bit, configurable in Preferences) LUT instead of the default 16-bit one)
* Histogram

In Autostretch mode with color images, the toggle to the right 
activates/deactivates channel linking. When unlinked, the 3 layers are 
stretched independantly so as to give a more balanced image.

.. warning::
   This is just a preview of the image, not the actual data (except if Linear 
   mode is selected). Do not forget to stretch your images before saving them.

:ref:`Back to figure <main-interface_window>`

.. _window-special-views:

.. rubric:: Special views

Use these toggles to show previewed images:

* in inverted colors
* in false colors

:ref:`Back to figure <main-interface_window>`

.. _window-astrometry-tools:

.. rubric:: Astrometry tools

Use these toggles to show:

* astrometric :ref:`annotations <astrometry/annotations:annotations>`
* celestial grid

.. warning::
   The loaded image needs to be plate-solved for these buttons to be active.

:ref:`Back to figure <main-interface_window>`

.. _window-quick-photometry:

.. rubric:: Quick photometry

Use this toggle to trigger the 
:ref:`quick photometry <photometry/quickphotometry:quick photometry>` mode.

:ref:`Back to figure <main-interface_window>`

.. _window-intensity-profile:

.. rubric:: Intensity profile

Use this toggle to trigger the
:ref:`intensity profile <intensity-profiling>` mode.

:ref:`Back to figure <main-interface_window>`


.. _window-zoom:

.. rubric:: Zoom

Use these buttons to:

* Zoom out
* Zoom in
* Zoom to fit the avaialble window space
* Zoom to actual size

.. tip::
   :kbd:`Ctrl+left clic` will allow to navigate into the picture

.. tip::
   :kbd:`Ctrl+mouse scroll` will zoom in/out and :kbd:`Ctrl` + :kbd:`0` / :kbd:`1` 
   will zoom to fit/100%.

.. tip::
   :kbd:`Ctrl+Shift` and drag with the primary mouse button will measure the distance between two points. If sufficient metadata is available the measurement
   will be given in degrees, minutes and arcseconds, otherwise it will be given in
   pixels.

:ref:`Back to figure <main-interface_window>`

.. _window-geometric-transformations:

.. rubric:: Geometric transformations

Use these buttons to:

* Rotate left
* Rotate right
* Mirror about horizontal axis
* Mirror about vertical axis

:ref:`Back to figure <main-interface_window>`

.. _window-frame-selection:

.. rubric:: Frame selection

Click on this button to open the :ref:`frame selector <sequences:frame selector>`.

:ref:`Back to figure <main-interface_window>`
