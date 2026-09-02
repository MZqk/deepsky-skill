Region of Interest Processing
=============================

Siril offers Region of Interest (ROI) processing for some functions. ROI
processing serves two purposes:

* For image operations that are slow to compute, such as deconvolution, it allows
  processing only a small portion of the image while experimenting to find the
  ideal parameters. This means the operation completes much quicker on the small
  ROI and allows the desired parameters to be chosen much more quickly.
* For image operations that affect the look of the image such as stretches,
  saturation tools etc., it can be convenient to use ROI processing to compare
  side-by-side an unprocessed and a processed area.

ROI processing is not universal to all image operations in Siril. In fact, there
are a great many where it wouldn't make sense, for example geometry operations
where the contents of the ROI would be mapped to a different part of the image.
ROI processing is not supported for image operations that take multiple input
images, i.e. :guilabel:`Star Recomposition`, :guilabel:`RGB Composition` or
:guilabel:`Pixel Math`.

Operations supporting ROI Processing
------------------------------------
The following image operations support ROI processing:

* **Deconvolution** Deconvolution can be very slow, so ROI processing allows for
  quicker trialling of options on a small part of the image in order to find
  the best settings for your image.
* **Noise Reduction** Noise reduction can also be slow to process, so ROI
  processing allows for quicker trialling of options.
* **Asinh Stretch** A user may wish to examine the effect of asinh stretch,
  or any of the following image operations, side by side with an unprocessed area.
  ROI processing allows this.
* **Generalized Hyperbolic Stretch**
* **Histogram Stretch**
* **Curves Transformation**
* **Color Saturation**
* **Median Filter**
* **Remove Green Noise (SCNR)**
* **Edge Preserving Filters**
* **Unpurple Filter**

No other image operations support ROI processing.

.. note:: ROI processing is not generally intended as a means of processing only
   part of an image.

   In image operations with an automatically updating preview,
   the preview will appply to the ROI but when :guilabel:`Apply` is clicked, the
   entire image will be updated.

   In image operations that do not have an automatically updating preview, when
   a ROI is set a :guilabel:`Preview in ROI` button will become available. This
   serves as a manual preview button, so you can preview a range of settings in
   the ROI until you are happy. Once the settings are as desired, clicking Apply
   will apply the operation to the whole image.

Setting the ROI
---------------
There are two options for setting the ROI:

* **Manual** This option requires the ROI to be set and cleared manually using
  the right mouse button menu, in the :menuselection:`ROI --> Set ROI to selection` sub-menu.
* **Automatic from selection** This option automatically synchronizes the ROI
  with the current selection, and automatically clears it when the selection is
  cleared.

These options are mutually exclusive and the preferred option can be configured
in the :ref:`preferences/preferences_gui:User Interface` tab of the `Preferences` dialog.

Clearing the ROI
----------------
In manual ROI setting mode, the option to clear the ROI is located in the right
mouse button menu, in the :menuselection:`ROI --> Clear ROI` sub-menu.

In automatic ROI setting mode, simply clear the current selection and the ROI
will clear.

The ROI will also automatically clear on the following occasions:

* When the current image is closed,
* When a new image is opened,
* When a sequence is open and the visible frame is changed.

Visualisation
-------------
When a ROI is set, it is shown in the image window as a red dashed box in the same
style as the selection box. However, when an image operation dialog is opened and
the image operation supports ROI processing (or if a ROI is set when such a
dialog is already open), the ROI outline will change color to green, indicating
that ROI processing is enabled for the current operation.

Siril Commands
--------------
Siril commands ignore the ROI, even if run in GUI mode from the command line, and
even if the same image operation does support ROI processing when called from
the GUI. The ROI is intended as a quick preview, whereas commands are intended to
operate on the whole image. If a ROI is in place when a command runs it will be
ignored, but not cleared, so it will still be available for subsequent
operations that do support ROI processing.

Color Space Transforms
----------------------
Any operation to remove, assign or convert the image's ICC profile will clear the
ROI, if one is set.
