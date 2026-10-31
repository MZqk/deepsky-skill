Sequences
#########

Sequences are what Siril uses to represent a set of manipulated files, for
example the set of dark images that we'll turn into the master dark. It is a
very useful tool for handling a large number of files that need to be linked
with each other.

A set of two or more FITS files
===============================

Siril uses natively 32-bit floating point data or 16-bit unsigned integer data
for the :ref:`FITS <file-formats/FITS:FITS>` images, other formats are
automatically converted. To be recognized and detected as a sequence, FITS
images file names must respect a particular pattern which is::

  basename$i.[ext]

* **basename** can be anything using ascii characteres. It is usually
  convenient, but not mandatory, to have it end with the ``_`` character. It
  will be used as the sequence name.
* **$i** is the index of the image. It must be a positive number and can have
  several leading zeros.
* **[ext]** is the supported extension as explained in the
  :ref:`settings <preferences/preferences_gui:FITS Options>`, ``fit`` by
  default.

.. note::
   The extension used to detect FITS sequences in the current working
   directory will be the same as the extension configured in the settings and
   as the files created by Siril.

.. warning::
   Some operating systems limit the number of images that can be opened at the
   same time, which is required for median or mean stacking methods. For
   Windows, the limit is 2048 images. If you have a lot of images, you should
   use another type of sequence, described below.

A single SER file
=================

:ref:`SER <file-formats/SER:SER>` is a format meant to contain an acquisition
sequence of several contiguous images in a single file. It is a rather simple
format that cannot contain as much metadata as FITS, but more than simple
films and data is not compressed. SER files can contain images of 8 or 16-bits
per channel only. There are three types of SER files, depending on the pixel
content: monochrome, CFA or color (3 channels).

.. note::
   An SER file can be opened either via :guilabel:`File` and :guilabel:`Open`, 
   or with the :guilabel:`Search sequences` button.

See :ref:`SER <file-formats/SER:SER>` for more information
on the SER format and why film formats like uncompressed AVI should not be
used for astronomy.

.. warning::
   To some extent, a regular film file such as AVI or any other container are
   supported too. Film files support is being dropped in favour of SER, but it
   can still be useful to open a film in Siril, to explore its content,
   extract some frames or convert them. A few operations can still be done,
   but in a slower way than with other sequences, like sum stacking. For a
   complete processing you will face limitations and incompatibilities.
   
A single FITS file
==================

.. note::
   Also called FITS cubes or FITS sequences, or FITSEQ for short in Siril.

The FITS format is an image and science data container, it can contain several
of these in a single file. We can use that to store an entire sequence of FITS
images in a single file while preserving the FITS header of each image. It is
the file format that professional astronomers use.

It's simpler to manage one file on the disk than 2000, but since it is a
single file, some operations on single images of the sequence may not be
possible. In particular, it is not currently supported within Siril to change
the header of a single image.

This format is an alternative to SER for a single-file sequence, with 32 bits
per channel and full header support.

Loading a sequence
==================

.. figure:: ./_images/sequences/sequence_load.png
   :alt: Sequence load
   :class: with-shadow
   :width: 100%

   Sequence Search and Cleaning.

When the working directory is set in the right place, the FITS follow the
correct nomenclature, and the extension of the FITS files is also set
correctly, then click the :guilabel:`Search Sequence` button of the Sequence tab. A
drop-down list opens with all the sequences available in the folder. If only
one is found, then it is automatically selected and loaded.

Frame selector
==============

.. |frame-selector| image:: ./_images/icons/list.svg

A great strength of Siril is that it easily manipulates image sequences. When a
sequence is opened, the reference image (see below) will be displayed, by
default this is the first image. However, it can sometimes be useful to inspect
individual images of a sequence. This is possible with the frame selector,
available via the toolbar with the |frame-selector| button or via the sequence
tab with the :guilabel:`Open Frame List` button.

.. figure:: ./_images/sequences/frame_selector.png
   :alt: frame selector
   :class: with-shadow
   :width: 100%

   Frame selector that allows you to choose a frame from the sequence and
   display it, set it as reference or exclude it.
   
.. tip:: Hovering over an image in the frame selector displays the original file 
   name, if saved.

Clicking on an image in the list will load it and display it in the main area,
while keeping the sequence as the active object for processing. More than just
a image display selector, the tool can also be used to **manually exclude
images** from the sequence, or visualize which are still included, visualize
the values of image quality and shift between images if they have been
computed, and change the reference image. Note that more image quality
information can be viewed in the Plot tab.

Excluding an image from the sequence does not mean its data will be permanently
deleted, it will just not be used for the subsequent processing operations, if
instructed to do so. In most cases, the option to look for is called *Process
included images only*.

The **reference image** is the image in the sequence that will serve as target
for the registration and for the normalization. Other images will be
transformed to look like the reference image, so it should be chosen carefully.
Fortunately, since Siril 1.2, a new two-pass registration can automatically
select the best image of the sequence as reference image before proceeding to
image transformation.

The header bar of the window will provide many controls for these sequence
properties:

* The drop-down menu allows the channel for which registration data (quality,
  shifts) is displayed to be changed, if they exist for other channels.
* The first button of the toolbar sets all images of the sequence as manually
  excluded.
* The second one, sets them all as included.
* The third includes or excludes the images selected from the list (multiple
  selections can be done with :kbd:`Ctrl` or :kbd:`Shift`) of the sequence.
* The last button can be deactivated to hide the framing indicator over
  registered images.

.. figure:: ./_images/sequences/reference_frame.png
   :alt: frame selector
   :class: with-shadow
   :width: 100%

   Framing indicator

   The red frame shows the trace of the reference frame over each image of the 
   sequence. The filled circle in one corner indicates the position of the top-left corner
   of the reference image. For instance, for the right image above, the framing
   indicator circle is at the bottom right, showing that there has been a meridian
   flip between this image and the reference shown on the left.

   The red circle/green cross close to the center show the position of the center
   of the reference image and of the current image respectively. This enables to visualize
   the shift between the two images.

.. note::

   Since Siril 1.3.1, the color of the framing indicator is different whether the
   registration information is just pure shift (in blue) or more 
   :ref:`complex transformations <preprocessing/registration:image transformations>`
   (in red for linear transformations, in green when distortions are included).
   For previous versions, it was always red, irrespective of the transformation. 


* The button :guilabel:`Reference image` is used to select the reference image for
  the sequence. All sequences must have one, it will be the first image if
  unset or by default.
* Finally, the search field allows you to find the images by name.

It is also possible to sort all the images by clicking on the column headers.
Thus you can sort the images by their number or their FWHM. The latter is very 
useful to have a look at the best and worst images.

Sequence manipulation commands
******************************

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ./commands/setref_use.rst

   .. include:: ./commands/setref.rst

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ./commands/select_use.rst

   .. include:: ./commands/select.rst

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ./commands/unselect_use.rst

   .. include:: ./commands/unselect.rst

Sequence export
===============

.. figure:: ./_images/sequences/sequence_export.png
   :alt: sequence export
   :class: with-shadow
   :width: 100%

   Give a name and specify output format to export a sequence.

The **Sequence Export** tool allows you to export a sequence of images in a
variety of formats. It is particularly useful if you want to export the images
taking into account the registration information contained in the ``seq`` file,
with optional cropping and normalization.

With the sequence export function, you can select a sequence to export, choose 
the file format and compression level for video formats. Siril's sequence 
export function supports a wide range of image file formats, including
:ref:`FITS <file-formats/FITS:FITS>` (single FITS file or sequence FITS file),
:ref:`TIFF <file-formats/Common:TIFF Format>`,
:ref:`SER <file-formats/SER:SER>` , AVI, MP4 and WEBM and can come in handy
when building timelapse.

The button :guilabel:`Normalize images` allows you to normalize the images with
respect to the reference image. The normalization is the same as the one done 
:ref:`during the stacking <preprocessing/stacking:input normalisation methods>`,
with the following settings: *Additive with scaling*, *Faster normalization*
disabled.

Moreover, it is possible to play with the
:ref:`image filtering <preprocessing/stacking:image rejection>` criteria to
exclude or not images according to their quality. A button
:guilabel:`Go to the stacking tab` has been added here, to easily go to the tab
that exposes them.

Sequence information
====================

All sequence information, registration transformation, statistics and frame
selection are stored in a ``.seq`` file saved next to the sequence files. It is
strongly recommended never to edit this file manually because Siril writes
continuously inside it and one wrong character could make the reading of the
sequence corrupt.

One way to clean the content of this sequence file is to go in the **Sequence**
tab and click on :guilabel:`Clean Sequence`. The choice of what will be cleaned 
can be defined by clicking on the small arrow next to it.

.. figure:: ./_images/sequences/sequence_clean.png
   :alt: Cleaning sequence
   :class: with-shadow

   Menu to clean the sequence file.
 
.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ./commands/seqclean_use.rst

   .. include:: ./commands/seqclean.rst   
