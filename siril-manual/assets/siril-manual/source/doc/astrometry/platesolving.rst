Platesolving
############

The platesolving is a major step in astronomical image processing. It allows
images to be associated with celestial coordinates, giving the ability to know
what object is in the observed field of view. Many of Siril's tools, such as
the Spectrophotometric or Photometric Color Calibration (SPCC or PCC) tools, need
to know the coordinates of the image with sufficient accuracy in order to work.

Astrometry in Siril can be performed in two different ways:

* Using the dedicated tool accessible via the menu
  :menuselection:`Tools --> Astrometry --> Image Plate Solver`, or
  using the shortcut :kbd:`Ctrl` + :kbd:`Shift` + :kbd:`A`.

.. figure:: ../_images/platesolver/Astrometry_Dialog.png
   :alt: Astrometry dialog
   :name: Astrometry dialog
   :class: with-shadow

   Platesolving dialog

* Using the ``platesolve`` command, introduced in Siril 1.2.

  .. admonition:: Siril command line
     :class: sirilcommand
   
     .. include:: ../commands/platesolve_use.rst

     .. include:: ../commands/platesolve.rst

Since version 1.2, plate solving can be done by two different algorithms. The
first was the only one in Siril until this version, it is based on the global
registration star matching algorithm, trying to register images onto a
virtual image of a catalog with the same field of view. The second is new, it
is using an external program called ``solve-field`` from the Astrometry.net
suite, installed locally. For Windows platforms, the simplest way to get it is
to use `ansvr <https://adgsoftware.com/ansvr/>`_.

Since version 1.3, Siril internal solver will also look within a cone around the 
initial target coordinates if it did not find a match. This is only available when
using :ref:`local star catalogues <astrometry/annotations:Offline annotation catalogues>`.
The log will show this information when that happens:

.. code-block:: text

   Initial solve failed
   Attempting a near solve with a radius of 10.0 degrees

Astrometric solutions require a few parameters to be found, like image sampling.
The window of the tool helps gathering those parameters, we will now see how to
fill them correctly.

Image parameters
================

Target coordinates
``````````````````
Finding an astrometric solution is easier and faster when we roughly know were
we are looking. Siril's plate solver, as it's comparing a catalog with the
image, needs to know approximately the position of the center of the image to
get the catalog excerpt. Astrometry.net has all the catalogs it needs locally,
so it can browse through all of it to find a solution, but it is of course much
faster to tell it where to start.

Acquisition software often also control the telescope nowadays and should know
the approximate coordinates where the image was taken. In that case, using a
FITS format, these coordinates will be provided in the image metadata, the FITS
header. This is not always the case, and clearly not the case when RAW DSLR
images are created instead of FITS.

When opening the plate solver window, the current image's metadata is
loaded and displayed in the window. If no coordinates appear at the top, or if
RA and Dec remain zero, some user input is needed. If you don't know at all
what the image is, use a blind solve with astrometry.net. Otherwise, provide
equatorial J2000 coordinates corresponding to as close as the center of the
image as possible, either by filling the fields if you already know the
coordinates, or by making a query with an object name (not yet possible from
the command).

The text field at the top left of the window is the search field, its goal
being to convert an object name to its coordinates. Pressing :kbd:`ENTER` or
clicking the :guilabel:`Find` button will first search the object in the local
annotation catalogues. If not found, a Web request will be made to obtain its
coordinates. Several results may be found for the entered name, they will be
displayed in the list below. Selecting one updates the coordinates at the top,
the first is selected by default.

It is also possible to choose the server on which you want to execute the
query, it does not change the results much, but sometimes one of them can be
online, so others would act as a backup, between CDS, VizieR and SIMBAD
(default).

.. note::
   If the object is not found, please try with the full name or with the name
   from a catalogue. The annotation catalogues contain a few common names, the
   online services too, but not all, and they don't find partial answers.
   For example, for the Bubble Nebula, please enter ``NGC 7635`` or ``bubble
   nebula``, not ``bubble``.

The coordinate fields are filled in automatically, but it is possible to define
your own. Don't forget to check the `S` box if the object you are looking for
is located in the southern hemisphere of the sky (negative declinations).

Image sampling
``````````````
Image sampling is the most important parameter for plate solving. Given in
arcseconds per pixel in our case, it represents how much zoomed on the sky the
image is, so how wide a field to search for.

It is derived from two parameters: focal length and pixel size. They are often
available in the image metadata as well. When not available from the image, the
values stored in the settings are used. The values of the images and of the
preferences can be set using the :guilabel:`Information` dialog. In any case,
check the displayed value before plate solving and correct if needed. If an
astrometric solution is found, the default focal length and pixel size will be
overwritten. This behavior can be disabled in the settings.

.. warning::
   If binning was used, it should be specified in the FITS header, but this can
   take two forms: the pixel size can remain the same and the binning
   multiplier should be used to compute the sampling, or the pixel size is
   already multiplied by the acquisition software. Depending on the case you
   are facing, either of the forms can be chosen from the preferences or from
   the :guilabel:`Information` window.

Pixel size is given in the specification of astronomical cameras, and can
generally be found on the Web for DSLR or other cameras. The number of sensors
is limited and most of them are known.

Focal length depends on the main instrument, but also on backfocus and
correcting or zooming lenses used. Give a value as close as what you believe
the effective focal to be, if an astrometric solution is found, the computed
focal length will be given in the results and you will be able to reuse that in
your acquisition software and for future uses of the tool.

When either of the fields is updated, the sampling is recomputed and displayed
in the window (called 'resolution' here). Make sure the value is as close to
reality as possible.

.. tip::
   Data written in orange color in the GUI indicates values which could not be 
   retrieved from the image header. It does not mean they are wrong (they could
   have been loaded from the preferences values and be valid) or mandatory 
   (you could blind-solve), this color is just here to differentiate from values
   read from the header.

Solver parameters
=================

Since Siril 1.2, Siril can use two different solvers, its internal solver and
:ref:`Astrometry.net local installation <astrometry/platesolving:using the local astrometry.net solver>`.
The interface differs depending on whether one or the other is selected in the
dedicated drop-down list.

.. figure:: ../_images/platesolver/Sirilsolver_parameters.png
   :alt: Siril solver options
   :name: Siril solver options
   :class: with-shadow

   Siril internal solver options

Distortions
```````````
The option :guilabel:`Solution order`, selected via the drop-down list, specifies
the order of the platesolving solution. When selecting ``Linear``, the solver will
try to fit a solution assuming there are no distortions in the image (i.e. the field
is optically flat). However, this assumption may not be true in presence of 
optical aberrations (wrong backfocus, no field-flattener etc...). Since version
1.3, the platesolver can try to fit up to fifth-order polynomial distortions, following the
`SIP convention <https://irsa.ipac.caltech.edu/data/SPITZER/docs/files/spitzer/shupeADASS.pdf>`_.
By default, the platesolver uses ``Cubic (SIP)`` distortions, which should fit
most use cases. This default can be changed in the :ref:`Preferences <Preferences/preferences_gui:astrometry>`.
This option is available with both solvers.

You can also choose to save distortions to a wcs file by ticking the :guilabel:`Save distortion`
box. Use the filechooser below to specify its path. If you have defined a 
master distortion path in the :ref:`preferences <Preferences/preferences_gui:Pre-processing>`,
its path will be shown by default. This file can then be used to undistort images
during :ref:`Global Registration <preprocessing/registration:global registration>` 
and :ref:`2pass Registration <preprocessing/registration:2pass registration>`

Search radius
`````````````
When using :ref:`local star catalogues <astrometry/annotations:Offline annotation catalogues>`
are installed or when using :ref:`Astrometry.net <astrometry/platesolving:using the local astrometry.net solver>`,
a cone around the target position will be searched. The size of this cone in
degrees can be changed using the :guilabel:`Search radius` control, which default 
value can be changed in the :ref:`Preferences <Preferences/preferences_gui:astrometry>`.
For Siril solver, this feature can be disabled by ticking the 
:guilabel:`disable near search` box.

Astrometry.net blind solve
``````````````````````````
When Astrometry.net solver is selected, two additional options are available:

#. :guilabel:`Ignore position (blind solve)` enables to ignore the position specified
   in the Target coordinates control.

#. :guilabel:`Ignore sampling (blind solve)` enables to ignore the resolution 
   computed from the pixel size and focal length.


.. figure:: ../_images/platesolver/Asnetsolver_parameters.png
   :alt: Astrometry.net solver options
   :name: Astrometry.net solver options
   :class: with-shadow

   Astrometry.net solver options
   
Use these two options together if the location and resolution of the image are 
completely unknown.

.. warning::
   There is no magic happening here. In order to solve for any field of view, you
   will need to have the necessary :ref:`indexes <astrometry/platesolving:index files>`
   installed that cover the actual field of view of the image being solved.


Other parameters
````````````````
Finally, there are three toggle buttons at the bottom of the frame:

#. The option :guilabel:`Downsample image` downsamples the input image to speed-up
   star detection in it. The downside is that it may not find enough stars or
   give a less accurate astrometric solution. The size of the output image
   remains unchanged.

#. If the image is detected as being upside-down by the astrometric solution, 
   with the option :guilabel:`Flip image if needed` enabled, it will be flipped at
   the end. This can be useful depending on the capture software, if the image
   has not the right orientation when it is displayed in Siril (see more
   :ref:`explanations <file-formats/FITS:Orientation of FITS images>`).

#. When the option :guilabel:`Auto-crop (for wide field)` is applied, it performs a
   platesolve only in the center of the image. This is only done with wide
   field images (larger than 5 degrees) where distortions away from the center
   are important enough to fool the tool. Ignored for astrometry.net solves.

Catalogue parameters
====================

This section is relevant for Siril internal solver only. Several online
catalogues can be used and also two catalogues that can be installed locally for
faster and more reliable operation.

Using online catalogues
```````````````````````

By default, this section is insensitive because everything is set to automatic.
By unchecking the auto box, however, it is possible to choose the online
catalog used for the platesolve, which may depend on the resolution of the
image. The choice is done between:

* `TYCHO2 <https://cdsarc.cds.unistra.fr/viz-bin/cat/I/259>`_, 
  a catalogue containing positions, proper motions, and two-color photometric 
  data for 2,539,913 of the brightest stars in the Milky Way.

* `NOMAD <https://cdsarc.cds.unistra.fr/viz-bin/cat/I/297>`_, 
  a merge of data from the Hipparcos, Tycho-2, UCAC2,
  Yellow-Blue 6, and USNO-B catalogs for astrometry and optical photometry,
  supplemented by 2MASS near-infrared. The almost 100 GB dataset contains
  astrometric and photometric data for about 1.1 billion stars.

* `Gaia DR3 <https://cdsarc.cds.unistra.fr/viz-bin/cat/I/355>`_, 
  released on 13 June 2022. The five-parameter astrometric
  solution, positions on the sky (α, δ), parallaxes, and proper motions, are
  given for around 1.46 billion sources, with a limiting magnitude of G = 21.
  This represents the state of the art in accurate astrometry data and provides
  spectral data for a wide range of sources which is used in SPCC.

* `PPMXL <https://cdsarc.cds.unistra.fr/viz-bin/cat/I/317>`_, 
  a catalog of positions, proper motions, 2MASS- and optical
  photometry of 900 million stars and galaxies.

* `Bright Stars <https://cdsarc.cds.unistra.fr/viz-bin/cat/V/50>`_, 
  a star catalogue that lists all stars of stellar magnitude
  6.5 or brighter, which is roughly every star visible to the naked eye from
  Earth. The catalog contains 9,110 objects.

* `APASS <https://cdsarc.cds.unistra.fr/viz-bin/cat/II/336>`_, 
  a star catalogue that lists all stars of stellar magnitude
  6.5 or brighter, which is roughly every star visible to the naked eye from
  Earth. The catalog contains 9,110 objects.

.. note::
   An internet connection is required to use these online catalogs.

All these catalogs are made available through VizieR catalogue access tool, CDS,
Strasbourg, France (DOI:10.26093/cds/vizier). The original description 
of the VizieR service was published in 2000, A&AS 143, 23.

The :guilabel:`Catalogue Limit Mag` is an option that allows you to limit the 
magnitude of the stars retrieved in the catalog. The automatic value is 
calculated from the image resolution.

Using the local KStars catalogues (NOMAD)
`````````````````````````````````````````

With version 1.1, starting in June of 2022, it was possible to rely on a
locally installed star catalogue, for disconnected or more resilient operation.
The star catalogue we found to be the most adapted to our needs is the one from
`KStars <https://edu.kde.org/kstars/>`_. It is in fact composed of four
catalogues (`documented here in KStars <https://api.kde.org/kstars/html/Stars.html>`_),
two of them not being directly distributed in the base KStars installation files:

* **namedstars.dat**, the brightest stars, all of them have names

* **unnamedstars.dat**, also bright stars, but down to magnitude 8

* **deepstars.dat**, fainter stars extracted from The Tycho-2 Catalogue of the
  2.5 Million Brightest Stars, down to magnitude 12.5

* **USNO-NOMAD-1e8.dat**, an extract of the huge NOMAD catalogue limited to B-V
  photometric information and star proper motion in a compact binary form, down
  to magnitude 18.

**This catalogue can be used for plate solving and for PCC.**

When comparing these catalogues with the online NOMAD, we can easily see that
many stars are missing. If not enough are found for your narrow field, you
should still use the remote queries. A nice thing to check when the catalogues
are installed is highlighting which stars of the image will be used for the
PCC, those available with photometric information in the catalogues, using the
:ref:`conesearch <conesearch>` command.

.. code-block:: text

   conesearch -phot

Using the local Gaia DR3 Catalogues
```````````````````````````````````

In version 1.4 support for a local extract of the Gaia DR3 catalogue has been
added. This `astrometric` Gaia extract differs from the other catalogues in that
it is focused on astrometry as applied to plate solving, so instead of defining
a limiting magnitude the catalogue has been designed to provide an even coverage
of stars in each level 8 HEALPixel (you can read more about HEALPixels
`here <https://en.wikipedia.org/wiki/HEALPix>`_).
This avoids including excessive numbers of stars in densely populated parts of
the sky and ensures that sufficient fainter stars are included in emptier
HEALPixels to support accurate plate solving.

The default catalogue will be selected at startup based on two criteria:

* If a local catalogue is available it is preferred to the remote equivalent
* If a Gaia catalogue (local or remote) is available it is preferred to NOMAD

.. tip::
   Although the offline Gaia DR3 extract can be used with the :ref:`conesearch <conesearch>` command
   (by specifying the catalogue name *localgaia*) it is not really designed for
   this purpose. In busy regions of the sky the limiting magnitude will be
   relatively bright and therefore many stars visible in your images will not
   be contained in the catalogue whatever limiting magnitude you set. For
   annotation cone searches in such regions you will be better served by
   specifying the catalogue name *gaia* instead, which will conduct a remote
   cone search using the Vizier mirror of the Gaia DR3 archive. This is both
   fast (for a remote query) and comprehensive.

**This catalogue can be used only for plate solving.** A `photometric` Gaia
extract also exists, and can be used for the SPCC, it is documented on the
:ref:`SPCC page <processing/color-calibration/spcc:local spcc catalog>`.

Download KStars NOMAD
`````````````````````

The first two files are available in
`KStars source <https://invent.kde.org/education/kstars/-/tree/master/kstars/data>`_,
the Tycho-2 catalogue from a `debian package <https://tracker.debian.org/pkg/kstars-data-extra-tycho2>`_
and the NOMAD catalogue from KStars files too, as documented in this small
`article for KStars installation <http://knro.blogspot.com/2017/03/how-to-get-100-million-stars-in-kstars.html>`_.
It is has multiple worldwide `mirrors <https://files.kde.org/edu/kstars/download.kde.org/kstars/USNO-NOMAD-1e8-1.0.tar.gz.mirrorlist>`_
as indicated in the articles.

To make things easier to Siril users, and possibly to KStars users too, we
redistribute the four files in a single place, and in a more compressed format.
With the LZMA algorithm (used by xz or 7zip), the file size is 1.0GB instead of
the 1.4GB with the original gzip file. So those `.xz` files are compressed, make
sure you uncompress them with a suitable software before copying them.

Direct download links are available `here <https://free-astro.org/download/kstars-siril-catalogues/>`__
(right click on each file name on the left and save the links).

In case the link is unavailable and to make it faster from anywhere, it is also
distributed with bittorrent, using this
`torrent file <https://free-astro.org/download/kstars-siril-catalogues.torrent>`_
or the following `magnet link <magnet:?xt=urn:btih:0d0d891df2779bcfc691638d216551bf9e44cb59&dn=kstars-siril-catalogues&tr=http%3A%2F%2Ffree-astro.org%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.openbittorrent.com%3A80%2Fannounce&tr=udp%3A%2F%2Fbt1.archive.org%3A6969%2Fannounce&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce>`_.

Download Astrometric Gaia DR3
`````````````````````````````

The Gaia DR3 local astrometric extract is available from `Zenodo <https://zenodo.org/records/14692304/files/siril_cat_healpix8_astro.dat.bz2?download=1>`_
It comes with a sha256 `checksum <https://zenodo.org/records/14692304/files/siril_cat_healpix8_astro.dat.bz2.sha256sum?download=1>`_
for the compressed archive.

Full details, plus a citation reference and a checksum
for the uncompressed archive for extra paranoia, can be found at the
`Zenodo record <https://zenodo.org/records/14692304>`_. The specification of the
Gaia DR3 catalogue extracts and their file format is documented
`here (PDF) <https://zenodo.org/records/14697486>`_.

.. tip::
   For convenience, an offline version of the Gaia DR3 astrometric catalogue can
   be installed using the Catalogue Installer script in the :guilabel:`Scripts` menu.
   This catalogue takes up 1.5GB once installed compared with 2.1GB for the KStars NOMAD
   catalogue and provides higher precision astrometry and even density of stars in all
   regions, for more reliable and accurate plate solving. **It is strongly recommended
   to install and use this catalogue for all astrometry purposes within Siril**.

Installation in Siril
`````````````````````

The files can be put anywhere and their paths given to Siril in the settings,
but there is a default location for the four files: ``~/.local/share/kstars/``
on Linux. They can be linked there to avoid unnecessary copies. Settings can be
changed from the command line now, using the :ref:`set <set>` command.

When available and readable, Siril will not use the Web service to retrieve
astrometric or photometric data. See the messages in the log tab or on the
console to verify that the catalogue files are used as expected.

From Siril 1.4.0 onwards, Siril will first look in 
:ref:`local annotation catalogs <astrometry/annotations:offline annotation catalogues>`
to find the coordinates of an object passed in the platesolving dialog, to locate 
the center of the image.
This means that, provided you have the local star catalogues installed, you can 
solve your images without Internet connection.
Of course, this should only be needed if the acquisition software did not
record the target coordinates in the FITS header, or when using SER file format
which cannot hold this information.

Usage
`````

With the addition of the new link between Siril's plate solver and the local
catalogue and the new link between Siril's PCC and the local catalogue, a new
command ``conesearch`` was created (from Siril 1.4.0) to display catalog objects in 
a plate solved image.
To display stars that contain photometric information (the B-V index) 
and can be used for calibration, you can for instance use the following:

.. code-block:: text

   conesearch -phot

This is a good way to verify that the plate solving and the image are aligned,
in addition to the object annotation feature (see :ref:`annotations
<astrometry/annotations:annotations>`).

.. figure:: ../_images/platesolver/Local_Catalogue.png
   :alt: Preferences with local catalogues
   :name: Preferences with local catalogues
   :class: with-shadow
   :width: 100%

   Preferences with local catalogues

Technical information
`````````````````````

For photometry, Siril only uses the `B-V index <https://en.wikipedia.org/wiki/Color_index>`_
(or, for the Gaia catalogue, the effective temperature field Teff) which gives information
about star colour. The three image channels are then scaled to give the best colour
representation to all stars in the image.

For more information about the KStar binary file type, see `this page <https://api.kde.org/kstars/html/Stars.html>`_
and this discussion on `kstars-devel <https://mail.kde.org/pipermail/kstars-devel/2022-June/007521.html>`_
and some development notes in Siril `here <https://gitlab.com/free-astro/siril/-/blob/829_local_nomad/subprojects/htmesh/README>`__
and `here <https://gitlab.com/free-astro/siril/-/blob/829_local_nomad/src/io/catalogues.c>`__.

Sha1 sums for the 4 catalogue files::

    4642698f4b7b5ea3bd3e9edc7c4df2e6ce9c9f7d  namedstars.dat
    53a336a41f0f3949120e9662a465b60160c9d0f7  unnamedstars.dat
    d32b78fd1a3f977fa853d829fc44ee0014c2ab53  deepstars.dat
    12e663e04cae9e43fc4de62d6eb2c69905ea513f  USNO-NOMAD-1e8.dat

`Licenses <https://gitlab.com/free-astro/siril/-/blob/master/src/io/kstars/KStars_catalogues_LICENSES.txt>`_
for the 4 catalogue files.

Using the local astrometry.net solver
=====================================

Installation
````````````

Since version 1.2, ``solve-field``, the solver from the astrometry.net suite,
can be used by Siril to plate solve images or sequence of images.

For Windows platforms, the simplest way to get it is to use `ansvr
<https://adgsoftware.com/ansvr/>`_. If you did not modify the default installation 
directory, that is `%LOCALAPPDATA%\\cygwin_ansvr`, Siril will search for it 
without additional setup. If you have cygwin and have build astrometry.net
from the sources, you must specify the location of cygwin root in the 
:ref:`Preferences <Preferences/preferences_gui:astrometry>`.

For MacOS, please follow 
`these instructions <http://www2.lowell.edu/users/massey/Macsoftware.html#Astrom>`_.
Install with homebrew and add it to the ``PATH``.
Also make sure that the program works for the test images, 
as indicated in the instructions, and outside of Siril.

For non-Windows OS, the executable is expected to be found in the
``PATH``.

The use of this tool makes it possible to *blindly* solve images, without a
priori knowledge of the area of the sky they contain or its resolution. It's also a good
alternative to Siril's plate solver in case it fails, because it's a dedicated 
and proven tool that also can take field distortion into account.

Default settings should be fine, but can be modified if you really want to,
using the :ref:`set <set>` command (default values specified between parens) or
in the :ref:`Astrometry <Preferences/preferences_gui:Astrometry>` tab of 
preferences. How wide the range of allowed scales is (15%), how big the radius 
of the search from initial coordinates is (10 degrees), the polynomial order 
for field distortion (0, disabled), removing or not the temporary files (yes), 
using the result as new default focal length and pixel sizes (yes).

Index files
```````````

Astrometry.net needs index files to run. We strongly recommend you use the latest
index files available from their `website <http://data.astrometry.net/>`_, i.e. 
the 4100 and 5200 series. The field of view of each series is described in their
`github page <https://github.com/dstndstn/astrometry.net/blob/main/doc/readme.rst>`_.
(the official documentation does not yet include this table).

On Unix-based system, you can just follow along the instructions in the documentation.

On Windows, if you are running ansvr, those recent index files will not be made 
available by the Index Downloader. You can still download them separately and 
store them where the other index files are kept (would recommend 
to remove the old files, although it may mess up the Index Downloader).

How it works
````````````

Just like the internal solver, Siril will proceed with extracting the stars from 
your images (so as to benefit from internal parallelism) and submit this list of 
stars to astrometry.net ``solve-field``. If you then want astrometry.net to 
crawl the index in parallel, you will need to specify it through the 
:file:`astrometry.cfg` file.

Solving sequences
=================

When a sequence is loaded, an additional GUI element is present at the bottom of
the dialog.

.. figure:: ../_images/platesolver/seqplatesolve.png
   :alt: Sequence astrometry options
   :name: Sequence astrometry options
   :class: with-shadow

   Sequence astrometry options

You can specify to solve the whole sequence. The images already
solved will be solved again unless the :guilabel:`Skip already solved images` box is 
ticked. To use :ref:`Astrometric registration 
<preprocessing/registration:astrometric registration>`, you will need to process
the whole sequence so that useful information is stored with the sequence (FWHM, 
number of stars, background level...), so leave the box :guilabel:`Use as registration information`
ticked.

When using Siril solver with local catalogues or when using Astrometry.net, the 
information contained in the header (if present) will be used to update the 
image target center and resolution for each image. However, when using Siril solver
with online catalogues, a single star catalogue will be downloaded by default to 
avoid too much network traffic and server requests. If the images do not have too much
drift and the same resolution, this is normally sufficient. However, if the images
do not have enough overlap or different sampling, you can force downloading one
star catalogue per image by ticking the :guilabel:`Fetch stars for each image`
box.

Finally, the 3 boxes on the right will enable to control if the platesolver should
read target coordinates, pixel size and focal from each image header or the values
specified in the dialog.

At the end of the sequence solving, the log will report how many images were solved
and if any were skipped.

.. code-block:: text

   Sequence processing succeeded.
   Execution time: 676.35 ms
   3 images successfully platesolved out of 3 included
   (2 were already solved and skipped)

Solving sequences is also available via the command :ref:`seqplatesolve <seqplatesolve>`.

.. note::
   When solving FITS sequences or a :ref:`FITSEQ <sequences:a single fits file>` 
   file, the images are directly saved, without creating a new sequence. For FITS
   sequences, if the sequence was created using symbolic links, the original files
   are not updated. Instead, the name of the symbolic link is used to create a 
   new FITS file, leaving the original untouched. 
   When solving a :ref:`SER <sequences:a single fits file>` sequence, a new 
   sequence with the prefix ``ps_`` is created as SER cannot store WCS data.

Star detection
==============

By default, the star detection uses the :ref:`findstar <findstar>` algorithm 
with the current settings. It works very well to find many stars, but in some 
occasions we would like to detect the stars manually, or simply view which are 
used. A first step would be to open the :guilabel:`PSF` window and launch star 
detection, then adjust the settings (see the related documentation 
:ref:`documentation <Dynamic-PSF:Use>`).

Another approach would be to select the stars one by one by surrounding them
with a selection then via a right click, choose :guilabel:`Pick a Star`. The 
more stars selected, the more likely the algorithm is to succeed.

Then in the astrometry window, expand the star detection section and activate
the :guilabel:`Manual detection`. Instead of running :ref:`findstar <findstar>`,
it will use the current list of stars.

.. note::
   Detection always uses the green layer for RGB images. For undebayered CFA images,
   the green layer is extracted on-the-fly and used instead.

Understanding the results
=========================

When an astrometric solution is found, we can see in the Console tab this kind 
of messages:

.. code-block:: text

        Up is -5.26 deg CounterClockWise wrt. N
        Resolution:      3.051 arcsec/px
        Focal length:  254.21 mm
        Pixel size:      3.76 µm
        Field of view:    04d 51m 58.27s x 03d 01m 1.21s
        Image center: alpha: 21h02m02s, delta: +68°10'48"
        Was 119.64 arcmin from initial value
        Saved focal length 254.21 and pixel size 3.76 as default values
        Flipping image and updating astrometry data.


The astrometric solution gives us the J2000 equatorial coordinates of the image
center, the projected horizontal and vertical dimension of the image on the
sky, the focal length that could give this field for the given pixel size and
consequently the actual image sampling, the angle the image makes with the
north axis, the field of view and image center. It also tells what was the distance
with the initial specified center.

If it fails, check that start coordinates and pixel size are correct and try
changing the magnitude, this will change the amount of
stars downloaded from the catalogs, and maybe more stars will be identified. If
Siril's plate solve won't find a solution, it is still possible to use an
external tool to do it, the solution will be written in the :ref:`FITS header 
<File-Formats/FITS:List of FITS keywords>` either
way.

Visualizing distortions
=======================

To check the validity of the solution, you can use the :ref:`conesearch <conesearch>` command.
This should display stars positions found from catalogues and inspect if they 
match with the actual stars in the image.

The two images below shows annotations in the top right corner of an image with 
significant distortion. The top one is the linear solution while the bottom one
has been solved accounting for cubic polynomials.

.. figure:: ../_images/platesolver/SIP_none.png
   :alt: Image linear solution
   :name: Image linear solution
   :class: with-shadow
   :width: 100%

   Stars annotations with linear solution

.. figure:: ../_images/platesolver/SIP_cubic.png
   :alt: Image cubic solution
   :name: Image cubic solution
   :class: with-shadow
   :width: 100%

   Stars annotations with cubic solution

If platesolving with distortions is sucessfull, the :ref:`disto <disto>` command
or the :menuselection:`Tools --> Image Analysis --> Show Distortions` menu
can display a representation of the corrections as an overlay on the image.

.. admonition:: Siril command line
   :class: sirilcommand
 
   .. include:: ../commands/disto_use.rst
   .. include:: ../commands/disto.rst

An example is shown below.

.. figure:: ../_images/platesolver/BF.png
   :alt: Image with distortion overlay
   :name: Image with distortion overlay
   :class: with-shadow
   :width: 100%

   Image with distortion overlay

.. admonition:: Sirilpy script
   :class: sirilpython

   If you want to visualize the distortion field in 3D, you can use the Python script
   :file:`Distortion3D.py` in the :menuselection:`Scripts->Python Scripts` menu. You
   will need to enable getting scripts from the :ref:`scripts repository <scripts/script-files:getting more scripts>`.

   .. figure:: ../_images/platesolver/distortion3D.png
      :alt: 3D distortion map
      :name: 3D distortion map
      :class: with-shadow
      :width: 100%
   
      3D distortion map
