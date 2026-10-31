FITS
====

Specification
*************
  
FITS stands for **Flexible Image Transport System** and is the standard 
astronomical data format used by professional scientists such as NASA. 
FITS is much more than an image format (such as JPG or TIFF) and is primarily 
designed to store scientific data consisting of multidimensional arrays.

A FITS file consists of one or more header and data units (HDUs), with the 
first HDU referred to as the "primary HDU" or "primary array." Five primary 
data types are supported: 8-bit unsigned bytes, 16 and 32-bit signed integers,
and 32 and 64-bit single and double-precision floating-point reals. The FITS 
format can also store 16 and 32-bit unsigned integers.

Each header unit consists of any number of 80-character keyword records which 
have the general form::

   KEYNAME = value / comment string

The keyword names may be up to 8 characters long and can only contain uppercase
letters, the digits 0-9, the hyphen, and the underscore character. The keyword 
name is (usually) followed by an equals sign and a space character (= ) in 
columns 9 - 10 of the record, followed by the value of the keyword which may be
either an integer, a floating point number, a character string (enclosed in 
single quotes), or a boolean value (the letter **T** or **F**).

The last keyword in the header is always the ``END`` keyword which has no value
or comment fields.

Each header unit begins with a series of required keywords that specify the 
size and format of the following data unit. A 2-dimensional image primary array
header, for example, begins with the following keywords::

   SIMPLE  =                    T / file does conform to FITS standard
   BITPIX  =                   16 / number of bits per data pixel
   NAXIS   =                    2 / number of data axes
   NAXIS1  =                  440 / length of data axis 1
   NAXIS2  =                  300 / length of data axis 2
   
.. note::
   In Siril, 64-bit FITS files are not supported. Siril reads them but converts 
   them to 32-bit files.

Filenames
*********

FITS files should have an extension ".fit", ".fits" or ".fts"; uppercase variations
are accepted but not recommended. It is possible to select your preferred extension
in the :guilabel:`Preferences` dialog, and this will be used for saving FITS files,
but all permitted extensions will be accepted for reading FITS images or sequence
frames.

.. warning::
   The FITS file library cfitsio interprets square brackets in filenames as providing
   filtering options. Therefore regardless of whether square bracket characters are
   permitted in filenames by your operating system, they **must not** be used in FITS
   files.

Compression
***********

Compression is the way to reduce the size of the image. There are many methods 
of compression depending on the type of images used. This compression can be 
destructive, as with the JPEG, or lossless as proposed by the PNG.

It is possible to work with compressed FITS files. At the cost of a longer 
calculation time, the size of the images can be reduced considerably. Siril 
offers several compression algorithms which are the following:

* **Rice**: The Rice algorithm is simple and very fast
* **GZIP 1**: The gzip algorithm is used to compress and uncompress the 
  image pixels. Gzip is the compression algorithm used in the free GNU 
  software utility of the same name.
* **GZIP 2**: The bytes in the array of image pixel values are shuffled into 
  decreasing order of significance before being compressed with the gzip 
  algorithm. This is usually especially effective when compressing 
  floating-point arrays.
  
One option is associated to these algorithms, the **Quantization level**:

While floating-point format images may be losslessly compressed (using gzip, 
since Rice only compresses integer arrays), these images often do not compress 
very well because the pixel values are too noisy; the less significant bits in 
the mantissa of the pixel values effectively contain incompressible random bit 
patterns. In order to achieve higher compression, one needs to remove some of 
this noise, but without losing the useful information content. If it is too 
large, one undersamples the pixel values resulting in a loss of information in 
the image. If it is too small, however, it preserves too much of the noise (or 
even amplifies the noise) in the pixel values, resulting in poor compression.

.. note:: 
   The supported image compression algorithms are all **loss-less** when 
   applied to integer FITS images; the pixel values are preserved exactly with 
   no loss of information during the compression and uncompression process. 
   Floating point FITS images (which have ``BITPIX = -32`` or ``-64``) are 
   first quantized into scaled integer pixel values before being compressed. 
   This technique produces much higher compression factors than simply using 
   GZIP to compress the image, but it also means that the original floating 
   value pixel values may not be precisely returned when the image is 
   uncompressed. When done properly, this only discards the 'noise' from the 
   floating point values without losing any significant information.
   
Particular Cases of Scientific FITS Files
*****************************************

FITS files produced by major astronomical missions such as those from NASA’s 
Hubble Space Telescope (HST) or the James Webb Space Telescope (JWST) are often 
more complex than typical FITS files used by amateur astronomers. These 
files are generally structured as FITS sequences (also referred to as FITS 
cubes), but with an important difference: the individual images within these 
sequences may have different dimensions.

This feature allows for storing multi-dimensional data collected under varying 
conditions, where each frame or image in the sequence might not have the same 
resolution or size. For example, data from the JWST often involves such 
heterogeneous datasets due to the nature of its instruments and observing modes.

In Siril, when processing these types of scientific FITS files, it is crucial 
to enable the option that allows handling images of varying dimensions within 
a FITS cube. This can be done by selecting the option :guilabel:`Allow FITS 
cubes to have images of different size` in the :guilabel:`FITS Options` tab of 
the preferences.

.. warning::
   Currently, Siril is unable to process FITS files with ``NAXES[2] > 3``. This 
   feature is planned for future versions of the software, so FITS cubes with 
   more than three axes are not supported at the moment.

.. figure:: ../_images/file-formats/JWST_FITS.png
   :alt: JWST image loaded in Siril
   :class: with-shadow

   Example of a JWST image loaded in Siril
   
Orientation of FITS images
**************************

The FITS standard is a container that describes how to store image data and 
metadata. Professional tools, from the early age of the FITS format, like 
`ds9 <https://sites.google.com/cfa.harvard.edu/saoimageds9>`_ (Harvard 
Smithsonian Center for Astrophysics), `fv <https://heasarc.gsfc.nasa.gov/docs/software/ftools/fv/>`_
(FITS viewer from NASA), store images **bottom-up**. We might be tempted to say
that it does not really matter, but when demosaicing or astrometry is involved,
problems arise. For example, the usual **RGGB** Bayer pattern becomes **GBRG**
if the image is upside-down.

Nowadays, despite this, most camera drivers are writing data in the top-down 
order and we have to cope with it.

For these reasons, we recently have introduced, together with P. Chevalley of 
`CCDCiel <https://www.ap-i.net/ccdciel/en/start>`_, a **new FITS keyword**. We 
encourage all data producers, INDI and ASCOM developers, to use it in order to 
make things easier for everybody.

This keyword is ``ROWORDER`` of type ``TSTRING``. It can take two values: 
``BOTTOM-UP`` and ``TOP-DOWN``.

Siril will always read and display images in the bottom-up order, however if 
the top-down information is specified in the keyword, then Siril will demosaic 
the image with the corrected pattern.

.. rubric:: Why would some programs write images bottom-up in the first place?

The reason is: `mathematics do it that way <https://stackoverflow.com/a/8347036>`_.

Also, the `FITS specification <https://ui.adsabs.harvard.edu/abs/2002A%26A...395.1061G/abstract>`_ says:

.. admonition:: *5.1. Image display conventions*

   *It is very helpful to adopt a convention for the display of images transferred
   via the FITS format. Many of the current image processing systems have 
   converged upon such a convention. Therefore, we recommend that FITS writers 
   order the pixels so that the first pixel in the FITS file (for each image 
   plane) be the one that would be displayed in the lower-left corner (with the 
   first axis increasing to the right and the second axis increasing upwards) by 
   the imaging system of the FITS writer. This convention is clearly helpful in 
   the absence of a description of the world coordinates. It does not preclude a 
   program from looking at the axis descriptions and overriding this convention, 
   or preclude the user from requesting a different display. This convention also 
   does not excuse FITS writers from providing complete and correct descriptions 
   of the image coordinates, allowing the user to determine the meaning of the 
   image. The ordering of the image for display is simply a convention of 
   convenience, whereas the coordinates of the pixels are part of the physics of 
   the observation.*
   
.. warning::
   ``ROWORDER`` keyword can be used for:
   
   1. Displaying the image with the intended orientation (unflip the display).

   2. Unflip the Bayer demosaic pattern. So the demosaic pattern can be 
      specified conform the sensor supplier.
      
   **BUT**
   
   1. ``ROWORDER`` shall not be used to unflip the image data for stacking. 
      Otherwise new images would become incompatible with older darks and 
      flats.
   
   2. ``ROWORDER`` shall not be used to unflip the image data for astrometric 
      solving. This would make the astrometric solution incompatible with 
      other programs.

Software using ``ROWORDER`` keyword
***********************************

* `Siril <https://siril.org>`_ (since version 0.99.4)
* `CCDCiel <https://sourceforge.net/projects/ccdciel/>`__ (since version 0.9.72)
* `Indi <https://indilib.org/>`_ (since `Jul. 2020 <https://github.com/indilib/indi/commit/176173ed51ec2086657eb8881c67b335fc570b34>`_)
* `KStars <https://edu.kde.org/kstars/>`_ (since 3.4.3)
* `SharpCap <https://www.sharpcap.co.uk/>`_ (since version 3.3)
* `FireCapture <http://www.firecapture.de/>`_ (since version 2.7)
* `N.I.N.A <https://nighttime-imaging.eu/>`_ (since version 1.10)
* `MaxImDL <https://diffractionlimited.com/product/maxim-dl/>`_ (since version 6.23)
* `INDIGO <https://www.indigo-astronomy.org/>`_ (since `Jul. 2020 <https://github.com/indigo-astronomy/indigo/commit/7ae5cbd10c06e705a52398d402d8800dd92bee57>`__)
* `PixInsight <https://pixinsight.com/>`_ (since version 1.8.8-6)
* `ASTAP <https://www.hnsky.org/astap.htm>`_ (since version ß0.9.391)
* `APT <https://www.astrophotography.app/>`_ (since version 3.86.3)
* `AstroDMx Capture <https://www.astrodmx-capture.org.uk/>`_ (since version 0.80)
* `Astroart <https://www.msb-astroart.com/>`_ (since version 8.0)

Retrieving the Bayer matrix
***************************

Image row order changes the way the Bayer matrix should be read, but there are 
also two optional FITS header keywords that have an effect on this: ``XBAYROFF``
and ``YBAYROFF``. They specify an offset to the Bayer matrix, to start reading 
it on first column or first row.

To help developers integrating the ``ROWORDER``, ``XBAYROFF`` and ``YBAYROFF`` 
keywords in their software, some test images were created by Han Kleijn from
`hnsky.org <https://www.hnsky.org>`_, one for each combination of the three 
keywords. Download them here: `Bayer_test_pattern_v6.tar.gz <https://free-astro.org/download/Bayer_test_pattern_v6.tar.gz>`_.

List of FITS keywords
*********************

Siril can read and interpret a wide range of keywords. The following list 
illustrates the non-standard keywords that Siril registers if necessary. Some 
keywords read by Siril may not appear in this list. For example, the keywords 
``CCDTEMP`` or ``TEMPERAT``, that indicate the temperature of the sensor, are 
correctly read, but are propagated under the keyword ``CCD-TEMP``.

.. tip::
   Siril is able to read and compare checksums if they are present in the FITS 
   header. However, by default and for software performance reasons, ``CHECKSUM``
   and ``DATASUM`` cards are automatically removed from HDU headers when a file 
   is opened, and any ``CHECKSUM`` or ``DATASUM`` cards are stripped from headers 
   when an HDU is written to a file. Nevertheless, they can be saved at the 
   user's request, using the **-chksum** option of the :ref:`save <save>` 
   command, or via the graphical user interface.

.. list-table:: FITS keywords saved by Siril. For reasons of clarity, astrometry SIP keywords are not listed.
   :widths: 25 25 50
   :header-rows: 1

   * - FITS Keyword
     - Type
     - Comment
   * - BZERO
     - Double
     - Offset data range to that of unsigned short
   * - BSCALE
     - Double
     - Default scaling factor
   * - MIPS-HI
     - Unsigned short
     - Upper visualization cutoff
   * - MIPS-LO
     - Unsigned short
     - Lower visualization cutoff
   * - MIPS-FHI
     - Float
     - Upper visualization cutoff
   * - MIPS-FLO
     - Float
     - Lower visualization cutoff
   * - PROGRAM
     - String
     - Software that created this HDU
   * - FILENAME
     - String
     - Original Filename
   * - DATE
     - String
     - UTC date that FITS file was created
   * - DATE-OBS
     - String
     - YYYY-MM-DDThh:mm:ss observation start, UT
   * - IMAGETYP
     - String
     - Type of image
   * - ROWORDER
     - String
     - Order of the rows in image array
   * - EXPTIME
     - Double
     - [s] Exposure time duration
   * - TELESCOP
     - String
     - Telescope used to acquire this image
   * - OBSERVER
     - String
     - Observer name
   * - FILTER
     - String
     - Active filter name
   * - APERTURE
     - Double
     - Aperture of the instrument
   * - ISOSPEED
     - Double
     - ISO camera setting
   * - FOCALLEN
     - Double
     - [mm] Focal length
   * - CENTALT
     - Double
     - [deg] Altitude of telescope
   * - CENTAZ
     - Double
     - [deg] Azimuth of telescope
   * - XBINNING
     - Unsigned int
     - Camera binning mode
   * - YBINNING
     - Unsigned int
     - Camera binning mode
   * - XPIXSZ
     - Double
     - [um] Pixel X axis size
   * - YPIXSZ
     - Double
     - [um] Pixel Y axis size
   * - INSTRUME
     - String
     - Instrument name
   * - CCD-TEMP
     - Double
     - [degC] CCD temperature
   * - SET-TEMP
     - Double
     - [degC] CCD temperature setpoint
   * - GAIN
     - Unsigned short
     - Sensor gain
   * - OFFSET
     - Unsigned short
     - Sensor gain offset
   * - CVF
     - Double
     - [e-/ADU] Electrons per A/D unit
   * - BAYERPAT
     - String
     - Bayer color pattern
   * - XBAYROFF
     - Int
     - X offset of Bayer array
   * - YBAYROFF
     - Int
     - Y offset of Bayer array
   * - FOCNAME
     - String
     - Focusing equipment name
   * - FOCPOS
     - Int
     - [step] Focuser position
   * - FOCUSSZ
     - Int
     - [um] Focuser step size
   * - FOCTEMP
     - Double
     - [degC] Focuser temperature
   * - STACKCNT
     - Unsigned int
     - Stack frames
   * - LIVETIME
     - Double
     - [s] Exposure time after deadtime correction
   * - EXPSTART
     - Double
     - [JD] Exposure start time (standard Julian date)
   * - EXPEND
     - Double
     - [JD] Exposure end time (standard Julian date)
   * - OBJECT
     - String
     - Name of the object of interest
   * - AIRMASS
     - Double
     - Airmass at frame center (Gueymard 1993)
   * - SITELAT
     - Double
     - [deg] Observation site latitude
   * - SITELONG
     - Double
     - [deg] Observation site longitude
   * - SITEELEV
     - Double
     - [m] Observation site elevation
   * - DFTTYPE
     - String
     - Module/Phase of a Discrete Fourier Transform
   * - DFTORD
     - String
     - Low/High spatial freq. are located at image center
   * - DFTNORM1
     - Double
     - Normalisation value for channel #1
   * - DFTNORM2
     - Double
     - Normalisation value for channel #2
   * - DFTNORM3
     - Double
     - Normalisation value for channel #3
   * - CTYPE3
     - String
     - RGB image
   * - OBJCTRA
     - String
     - Image center Right Ascension (hms)
   * - OBJCTDEC
     - String
     - Image center Declination (dms)
   * - RA
     - Double
     - Image center Right Ascension (deg)
   * - DEC
     - Double
     - Image center Declination (deg)
   * - CTYPE1
     - String
     - TAN (gnomic) projection
   * - CTYPE2
     - String
     - TAN (gnomic) projection 
   * - CUNIT1
     - String
     - TAN (gnomic) projection + SIP distortions
   * - CUNIT2
     - String
     - TAN (gnomic) projection + SIP distortions
   * - EQUINOX
     - Double
     - Equatorial equinox
   * - CRPIX1
     - Double
     - Axis1 reference pixel
   * - CRPIX2
     - Double
     - Axis2 reference pixel
   * - CRVAL1
     - Double
     - Axis1 reference value (deg)
   * - CRVAL2
     - Double
     - Axis2 reference value (deg)
   * - LONPOLE
     - Double
     - Native longitude of celestial pole
   * - CDELT1
     - Double
     - X pixel size (deg)
   * - CDELT2
     - Double
     - Y pixel size (deg)
   * - PC1_1
     - Double
     - Linear transformation matrix (1, 1)
   * - PC1_2
     - Double
     - Linear transformation matrix (1, 2)
   * - PC2_1
     - Double
     - Linear transformation matrix (2, 1)
   * - PC2_2
     - Double
     - Linear transformation matrix (2, 2)
   * - CD1_1
     - Double
     - Scale matrix (1, 1)
   * - CD1_2
     - Double
     - Scale matrix (1, 2)
   * - CD2_1
     - Double
     - Scale matrix (2, 1)
   * - CD2_2
     - Double
     - Scale matrix (2, 2)
   * - PLTSOLVD
     - Logical
     - Siril solver

