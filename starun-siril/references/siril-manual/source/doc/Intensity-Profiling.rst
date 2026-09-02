Intensity Profiling
###################

.. figure:: ./_images/icons/cut.svg
   :alt: Profile button icon
   :class: with-shadow
   :width: 10%

.. _intensity-profiling:

Siril has an intensity profiling mode. The user selects a line between two
points and Siril will generate a graph of the pixel values between them.
This has several uses. It can be used to inspect the intensity profile of an
individual star or a whole galaxy.

Basic Intensity Profile
***********************

To make a basic intensity profile of a star or other object, select the Profile
button in the bottom toolbar. This puts Siril into profiling mode and opens a
small dialog.

.. figure:: ./_images/profiling/gui.png
   :alt: Profile button icon
   :class: with-shadow
   :align: center
   :width: 50%

You can now click and drag on the main image display to set the
start and finish points of the line you wish to profile. If you hold down the
:kbd:`Shift` key while dragging the line, it will snap to be either horizontal
or vertical.

.. tip::
   When the profile line is exactly horizontal or exactly vertical, exact pixel
   values can be used directly from the image. When the profile line is neither
   horizontal nor vertical, the points to be plotted do not fall exactly on a
   pixel and bilinearly interpolated pixel values are therefore used.

A custom title for your plot can be entered in the control at the bottom of the
dialog.

.. tip::
   When processing a sequence, it is possible to have the custom title display
   the image number and total by adding () to the end of the title. For example
   entering **Solar Spectra ()** as the title for a 5 image sequence will generate
   titles **Solar Spectra (1 / 5)**, **Solar Spectra (2 / 5)** etc. The brackets are
   ignored and removed if processing a single image.

Types of Profile
****************

Use the radio buttons to select the type of profile you want. (Click on the example
images below to see them full size.)

* **Mono profile**. For mono or color images, generate a luminance profile between
  two points. This mode can be used with spectrometric data.

.. figure:: ./_images/profiling/mono-profile.png
   :alt: Profile button icon
   :class: with-shadow
   :width: 100%

.. tip::
   If a color image is loaded but the mono profiling mode is selected, the profile
   will be made according to the viewport. The R, G and B viewports provide mono
   profiles of their respective channel and the RGB viewport provides a luminance
   profile weighting all 3 channels equally.

* **Color profile**. For color images, generate three profiles for the R, G and B pixel
  values between two points. This mode can be used with spectrometric data.

.. figure:: ./_images/profiling/color-profile.png
   :alt: Profile button icon
   :class: with-shadow
   :width: 100%

* **Tri-profile (mono)**. For mono or color images, generate three parallel equispaced
  luminance profiles between two points. The spacing between the 3 profiles can be set
  using the spin button.

.. figure:: ./_images/profiling/tri-profile.png
   :alt: Profile button icon
   :class: with-shadow
   :width: 100%

* **CFA**. For images with a Bayer pattern only, generate four profiles for the four
  CFA subchannels between two points. This can be particuarly useful for inspecting
  the profile of Bayer patterned flats or other Bayer pattern images before they are
  debayered.

.. figure:: ./_images/profiling/cfa-profile.png
   :alt: Profile button icon
   :class: with-shadow
   :width: 100%

   This image demonstrates use of the Custom Title control to set a custom title
   for the plot.

Click :guilabel:`Apply` to generate your profile.

Precise Coordinate Entry
************************
In order to make it easy to input coordinates precisely and repeatably, a manual
entry method is provided. Click the :guilabel:`Manual Coordinates` button and
you can enter the X and Y coordinates of the start and end points of the profile
line. If a profile line is already drawn but one point is not quite in the place
you want it, you can use this popup dialog to fine tune the placement of the
endpoints.

If you wish to set an endpoint exactly to the position of a star, make a rectangular
selection around the star and click the relevant star button to the right of the
dialog.

.. note::
   When using the CFA mode, coordinates are given in the *input* image. However each
   CFA channel is half the width and half the height. The x axis in the CFA mode
   plot is measured in pixels in the CFA subchannel, i.e. it will span half the
   number of pixels that it does in the input image.

Measurement
***********
The intensity profile line can be used as a measuring tool in two ways:

* Checking the :guilabel:`Measure profile` checkbox will measure all profile lines
  dragged with the mouse, similarly to the :kbd:`Ctrl + Shift + Drag` quick measurement
  function.

* In the Coordinates dialog there is a :guilabel:`Measure` button. This provides
  the same measurement function but allows you to set the endpoints exactly, and
  then measure the profile line on demand. By selecting stars, minor planets or
  comet nuclei as end points as described above, measurements between two celestial
  bodies can be made very precisely (with sub-pixel precision).

.. figure:: ./_images/profiling/measure.png
   :alt: Profile button icon
   :class: with-shadow
   :width: 100%

   Here, two close stars have been selected and set as the endpoints and the separation
   between them measured as 5.2 arcsec. This could be used to study close binaries
   or to triangulate the position of a minor planet.

.. note::
   Siril's measurement function makes the small angle approximation for the angular
   separation :math:`\theta`. The most significant error term is proportional to
   :math:`\theta^3` and is less than 1% for measurements up to 10°: it is
   therefore valid for most astrometric uses, but will become inaccurate for large
   measurements across ultra-wide field images. A warning will be written to the log
   for measurements over 10°.

Siril Plot Tool
***************

The profiling feature uses Siril internal plotting tool to display the different 
profiles. With the :file:`*.dat` files produced, you can still use any plotting
tool of your liking to explore the underlying data.

A :kbd:`right-click` anywhere in the plotting surface will pop-up a contextual 
menu to:

- Show/hide grids and legend
- Export current view to clipboard, :file:`*.png` or :file:`*.svg`
- Save underlying data to a :file:`*.dat` file

.. figure:: ./_images/profiling/siril_plot_1.png
   :alt: Siril plot menu
   :class: with-shadow
   :width: 100%

   Siril plot contextual menu

Note that all exports account for the current zoom/pan while saving to dat will 
export unfiltered data.

The following GUI interactions are avalaible:

- :kbd:`Click + Drag` to draw a selection. The zoom is set to the selected zone 
  when the mouse is released.
- :kbd:`Ctrl + Drag` to pan the current view.
- :kbd:`Ctrl + Scroll` to zoom in/out.
- :kbd:`Double-click` to reset to the default position/zoom.

Commands
********

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ./commands/profile_use.rst

   .. include:: ./commands/profile.rst

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ./commands/seqprofile_use.rst

   .. include:: ./commands/seqprofile.rst
