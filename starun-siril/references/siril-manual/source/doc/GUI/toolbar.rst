Main Toolbar
============

The main toolbar is located at the bottom of Siril's main window. It provides 
quick access to display controls, analysis tools and basic image transformation 
commands.

.. figure:: ../_images/GUI/toolbar.png
   :alt: Siril main toolbar
   :align: center

   The Siril main toolbar.

.. note::
   The toolbar is only active when an image is loaded in memory.


.. _toolbar-display:

Display Group
-------------

Display mode
^^^^^^^^^^^^

The first control is a drop-down menu button showing the current display mode
(``Linear`` by default). Clicking it opens the list of all visual modes
available in Siril:

- **Linear** — direct rendering of pixel values. This is the reference mode.
- **Logarithm** — applies a logarithmic transfer function, compressing bright
  areas and bringing out faint details.
- **Square root** — applies a square root transfer function, a gentler
  alternative to the logarithm.
- **Squared** — applies a quadratic transfer function, boosting bright areas
  at the expense of the shadows.
- **Asinh** — applies an inverse hyperbolic sine transfer function, offering a
  smooth and natural-looking stretch.
- **AutoStretch** — automatically computes and applies a stretch for a
  visually pleasing result. A **High definition** sub-option is available to
  increase the precision of the stretch computation.
- **Histogram** — applies a stretch based on the image histogram for a
  balanced display.

.. warning::
   Only **Linear** mode (with both visualisation sliders at their minimum and
   maximum positions) reflects the true pixel values. All other modes are
   purely visual aids. Stretch the histogram before exporting to an external
   tool.

Channel linking
^^^^^^^^^^^^^^^

Immediately to the right of the mode selector is a toggle button showing a
chain link icon.

- **Closed chain** — the R, G and B channels are linked in AutoStretch mode.
- **Broken chain** — the channels are stretched independently.


.. _toolbar-view:

Visual Rendering Group
----------------------

Negative view
^^^^^^^^^^^^^

Toggles the display between the normal view and the **negative view** of the
image. Useful for detecting subtle artefacts or faint gradients.

False colour / rainbow colormap
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Toggles between normal display and a **false colour rendering** (rainbow
palette). This mode helps visualise intensity variations across the image more
clearly.


.. _toolbar-astrometry:

Astrometry Group
----------------

Object annotation
^^^^^^^^^^^^^^^^^

- **Left-click** — displays the names of celestial objects if WCS (World
  Coordinate System) information is available in the image.
- **Right-click** — opens the list of available astrometric catalogues to
  select the annotation source.

Celestial grid
^^^^^^^^^^^^^^

Shows or hides the **celestial coordinate grid** (right ascension /
declination) if WCS information is available in the image.


.. _toolbar-analysis:

Analysis Group
--------------

Photometry / PSF
^^^^^^^^^^^^^^^^

Switches to :ref:`photometry / PSF mode<Photometry:Photometry>`. In this mode:

- A click on a star performs a PSF (Point Spread Function) fit and displays
  the photometric parameters.
- If a sequence is loaded, a **right-click** on the displayed image applies
  the PSF/photometry analysis to the entire sequence.

Intensity profile cut
^^^^^^^^^^^^^^^^^^^^^

Activates the :ref:`intensity profile cut tool <Intensity-Profiling:Intensity Profiling>`: 
draw a segment between two points on the image to display a graph of the pixel 
values along that line.


.. _toolbar-zoom:

Zoom Group
----------

Zoom out
^^^^^^^^

Reduces the zoom factor (shrinks the displayed image). Usual keyboard
shortcut: :kbd:`-`.

Zoom in
^^^^^^^

Increases the zoom factor (enlarges the displayed image). Usual keyboard
shortcut: :kbd:`+`.

Fit to window
^^^^^^^^^^^^^

Toggle button. When active, the image is **automatically scaled** to fill the
available area of the window.

Actual size — 1:1
^^^^^^^^^^^^^^^^^

Displays the image at its **actual size** (1 image pixel = 1 screen pixel).


.. _toolbar-transform:

Geometric Transformations Group
--------------------------------

Rotate 90° counter-clockwise
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Applies a **90° counter-clockwise rotation** to the image.

Rotate 90° clockwise
^^^^^^^^^^^^^^^^^^^^^

Applies a **90° clockwise rotation** to the image.

Horizontal mirror
^^^^^^^^^^^^^^^^^

Applies a **horizontal flip** (reflection about the vertical axis).

Vertical mirror
^^^^^^^^^^^^^^^

Applies a **vertical flip** (reflection about the horizontal axis).


.. _toolbar-sequence:

Sequence Group
--------------

Sequence image list
^^^^^^^^^^^^^^^^^^^

Shows or hides the **side panel listing the images in the current sequence**,
along with the registration data associated with each frame.

