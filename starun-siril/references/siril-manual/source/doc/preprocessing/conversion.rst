Conversion
==========

Siril supports the FITS 32bits format as well as the SER format in a native 
way. Therefore, any other file format must first be converted to these formats 
in order to be supported and to generate a :ref:`sequence <Sequences:Sequences>`.
The type of supported files is indicated in the tab and depends on how Siril 
was compiled.

.. |conv-toolbar| image:: ../_images/preprocessing/conv_toolbar.png

Siril provides a conversion tab which is divided into 2 panels. The upper panel
allows you to load the **source** files you wish to convert.

.. figure:: ../_images/preprocessing/conv_source.png
   :alt: conversion source
   :name: conversion source
   :class: with-shadow

   Source panel of the conversion tab.
   
The management of these files is done from the mini toolbar |conv-toolbar|.

* The first button, the :guilabel:`+` button, is the one that allows to load 
  all the source files. It opens a dialog window allowing you to choose all the
  files to be converted on your computer. Only the formats supported by Siril 
  are visible.
  
  .. tip::
     It is possible to drag and drop files directly into the **sources** area. 
     The drop zone is highlighted when the files are over it.
     
* The second button, the :guilabel:`-` button, allows to delete the selected 
  files. Several files can be deleted at the same time. They are not deleted 
  from the hard disk, but only from the conversion area.
* The last button allows you to delete all loaded files at once.

The number of loaded and selected files are reported in the status bar, to the 
right of the toolbar.

In the **destination** section it is possible to choose the name of the 
sequence that will be generated after the conversion of the files. 

.. figure:: ../_images/preprocessing/conv_dest.png
   :alt: conversion destination
   :name: conversion destination
   :class: with-shadow
   
   Destination panel of the conversion tab.

Thus, for a sequence name ``basename``, the converted files will be of the form

.. code-block:: text

   basenameXXXXX.[ext]

The extension is as defined in the :ref:`preferences <preferences/preferences_gui:FITS Options>`. 
The ``XXXXX`` index starts by default at ``00001`` with the first image, 
however it is possible to define a different starting index. This can be useful
in the case of a multi-session that shares the same master files. Three types 
of outputs are possible, to choose from a drop-down menu:

* FITS images
* SER sequence
* FITS sequence

These file formats are explained in the :ref:`sequence <Sequences:Sequences>`
section of this documentation.

Technically, when the input files are in FITS format, there is no need to 
convert them. However, you may want to do so so that the files are renamed to 
create a sequence and can be processed in Siril. In order not to fill the hard 
disk unnecessarily, it is then possible to choose the option 
:guilabel:`Symbolic link`. This option creates a symbolic link for the FITS 
files instead of copying them. This option is therefore only available when the
output files are FITS images.

.. note::
   When symbolic links are enabled, this disables compression.

.. warning::
   For Microsoft Windows, the use of symbolic links requires the activation of 
   the `developer mode <https://learn.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development>`_
   in Windows.
   
.. warning::
   If on GNU/Linux you see the error **Symbolic link Error: Function not 
   implemented** could be because you try making a symlinked sequence in a 
   directory on a filesystem that doesn't permit symbolic links.

When the output formats are SER, or FITS sequence, then the 
:guilabel:`Multiple sequences` option becomes visible. Tick this to create 
several sequence files instead of a single SER or FITS file for all input 
elements. Use this if input elements (sequence files such as films, SER or FITS
cubes) don't share the same image size or must not be processed together.

The last option :guilabel:`Debayer` allows the user to demosaic the images 
during the conversion. This option should generally not be used if the images 
are bias, dark and flat images, or light images intended to be pre-processed.
Indeed, due to Bayer matrix consideration, the RGB result of your RAW image is 
an interpolated picture. In consequence pre-processing interpolated data will 
give wrong results. Converting RAW files of an OSC sensor gives `Color Filter
Array <https://en.wikipedia.org/wiki/Color_filter_array>`_ (CFA) monochrome 
FITS pictures. Contrary to RGB image, CFA image represent the entire sensor 
data with the Bayer pattern. The following image shows you a crop of a CFA 
image. Note that the Bayer pattern (RGGB on this example) is visible.

.. figure:: ../_images/preprocessing/conv_Bayer_Pattern.png
   :alt: Bayer pattern
   :name: Bayer pattern
   :class: with-shadow
   
   Bayer pattern showed on a CFA (Color Filter Array) image.

Finally, the button :guilabel:`Convert`, allows, as its name indicates, to 
start the conversion of files.

.. note::
   The raw images of digital SLRs depend on the manufacturer and are generally
   closed source formats. Therefore the decoding of such files is a complex 
   task that must be done by a dedicated code. For Siril, the task of 
   converting raw files is performed by `LibRaw <https://www.libraw.org/about>`_.
   In fact, if a file format, usually a recent one, does not read, you have to 
   look on the LibRaw website if it is supported. If it is not, providing them 
   with a raw file can help the dev team to do so. However, it is also possible
   that the version of LibRaw embedded in the Siril package is not the most 
   recent version. In this case, you must either wait for a new release or 
   compile the sources directly.

Correspondence file   
*******************
After each conversion, a file ending with ``_conversion.txt`` is created. It 
contains the correspondence between the input images and the images of the 
sequence obtained during the conversion.
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/convert_use.rst

   .. include:: ../commands/convert.rst
     
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/convertraw_use.rst

   .. include:: ../commands/convertraw.rst
