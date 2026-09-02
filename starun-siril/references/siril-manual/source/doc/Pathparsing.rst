Path parsing
============

Parsing is the ability to parse, i.e. to write strings based on data contained 
in the :ref:`FITS <file-formats/FITS:FITS>` header.
Path parsing, introduced in Siril 1.2.0, aims at giving more flexibility to 
scripting by using header data to write/read filenames or paths.
For now, this is used with the following commands:

- :ref:`calibrate <calibrate>`
- :ref:`stack <stack>` and :ref:`stackall <stackall>`
- all the :ref:`saveXXX <save>` commands

and of course, their GUI counterparts.

Syntax example
++++++++++++++

We will take an easy example to begin with. Say you have a file named 
:file:`light_00001.fit` and you would like to locate a masterdark from your masters 
library that matches the characteristics of said light. As you have 
chosen a convention to name your masterdarks, you know that the correct dark 
should be named something like:

.. code-block:: text

   DARK_"exposure"s_G"gain"_O"offset"_T"temperature"C_bin"binning".fit

with the terms between quotes replaced with the values read from your light 
header. For an exposure of 120s, temperature of -10C, gain/offset of 120/30 and 
binning 1, the masterdark would be named:

.. code-block:: text

   DARK_120s_G120_O30_T-10C_bin1.fit

Well, that's exactly what this feature allows to do. If you specify the dark 
name with the conventions explained just after, you can tell Siril to open the 
light image, read its header and use its values to write such string (and then 
use it to calibrate your light).

You can read the info contained in the header either through 
:ref:`dumpheader <dumpheader>` command or by using the Tools menu, 
:menuselection:`Tools --> FITs Header...``
You would normally get a print like the one below (some keys removed for brevity):

..  code-block:: text

    SIMPLE  =                    T / C# FITS
    BITPIX  =                   16 /
    NAXIS   =                    2 / Dimensionality
    NAXIS1  =                 4144 /
    NAXIS2  =                 2822 /
    BZERO   =                32768 /
    EXTEND  =                    T / Extensions are permitted
    IMAGETYP= 'LIGHT'              / Type of exposure
    EXPOSURE=                120.0 / [s] Exposure duration
    DATE-OBS= '2022-01-24T01:03:34.729' / Time of observation (UTC)
    XBINNING=                    1 / X axis binning factor
    YBINNING=                    1 / Y axis binning factor
    GAIN    =                  120 / Sensor gain
    OFFSET  =                   30 / Sensor gain offset
    INSTRUME= 'ZWO ASI294MC Pro'   / Imaging instrument name
    SET-TEMP=                -10.0 / [degC] CCD temperature setpoint
    CCD-TEMP=                -10.0 / [degC] CCD temperature
    BAYERPAT= 'RGGB'               / Sensor Bayer pattern
    TELESCOP= '61EDPH'             / Name of telescope
    FILTER  = 'DualBand'           / Active filter name
    OBJECT  = 'Rosette Nebula     '/ Name of the object of interest
    OBJCTRA = '06 30 36'           / [H M S] RA of imaged object
    OBJCTDEC= '+04 58 51'          / [D M S] Declination of imaged object
    RA      =     97.6960081674312 / [deg] RA of telescope
    DEC     =     4.99212765957446 / [deg] Declination of telescope
    END

The format used to specify the dark name would then be:

.. code-block:: text

   DARK_$EXPTIME:%d$s_G$GAIN:%d$_O$OFFSET:%d$_T$SET-TEMP:%d$C_bin$XBINNING:%d$.fit

All the terms to be parsed are formed as follows: $\ **KEY**\ :\ **fmt**\ $

  + **KEY** is any (valid) key from the light FITS header
  + **fmt** is a `format specifier <https://en.wikipedia.org/wiki/Printf_format_string>`_.

For instance, ``$EXPTIME:%d$`` will be parsed to ``120`` if the light have been exposed for 120s. 
But would be parsed to ``120.0`` if you specify ``$EXPTIME:%0.1f$``, thanks to the 
formatter ``%x.yf``.

The full expression above would therefore evaluate to:

..  code-block:: text

    DARK_**120**s_G**120**_O**30**_T**-10**C_bin**1**.fit


In this first example, we have only used conversion to integers with ``%d``. 
But there are other conventional formatters that you can use:

- ``%x.yf`` for floats
- ``%s`` for strings

.. note::

   For strings, leading and trailing spaces are always removed, while spaces within 
   the strings are replaced with ``_`` signs.
   Example: ``$OBJECT:%s$`` would be converted to ``Rosette_Nebula``.

You can also use some less conventional formatters:

- In order to parse a date from a date-time header key, you can use the special non-standard formatter **dm12**, which means date minus 12h.
  In the header above, the key DATE-OBS has a value of ``2022-01-24T01:03:34.729``. ``$DATE-OBS:dm12$`` would convert to ``2022-01-23``, which was the date at the start of the night.
  You can also use special formatter **dm0** which will just parse the date, without substracting 12h.
- In order to parse a date-time from a date-time header key, you can use the special non-standard formatter **dt**, which just means date-time.
  In the header above, the key ``DATE-OBS`` has a value of ``2022-01-24T01:03:34.729``. ``$DATE-OBS:dt$`` would then convert to ``2022-01-24_01-03-34``.
- In order to parse ``RA`` and ``DEC`` info from ``OBJCTRA`` and ``OBJCTDEC`` header keys, you can use the special non-standard formatters **ra** and **dec**.
  In the header above, the keys ``OBJCTRA`` and ``OBJCTDEC`` have a value of ``06 30 36`` and ``+04 58 51`` respectively. ``$OBJCTRA:ra$_$OBJCTDEC:dec$`` would convert to ``06h30m36s_+04d58m51s``.
- In order to parse ``RA`` and ``DEC`` info from ``RA`` and ``DEC`` header keys, when in decimal format, you can use the special non-standard formatters **ran** and **decn**.
  In the header above, the keys ``RA`` and ``DEC`` have a value of ``97.6960081674312`` and ``4.99212765957446`` respectively. ``$RA:ran$_$DEC:decn$`` would convert to ``06h30m47s_+04d59m32s``.

A good example for stacking result filename is to give the following expression:

.. code-block:: text
   
   $OBJECT:%s$_$FILTER:%s$_$STACKCNT:%d$x$EXPTIME:%d$sec_G$GAIN:%d$_O$OFFSET:%d$_T$CCD-TEMP:%d$°C_$DATE-OBS:dm12$
   
giving something like:

.. code-block:: text
   
   NGC_7023_L_57x120sec_G100_O50_T-9°C_2023-10-07


To test the syntax, you can load an image and use the :ref:`parse <parse>` command, 
as reminded below.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/parse_use.rst

   .. include:: ./commands/parse.rst


Finding a file with path parsing
++++++++++++++++++++++++++++++++

In the example above, we have seen that we could find the name of a masterdark 
based on the information contained in the header of the light to be calibrated. 
This is what is called in the ``parse`` command, the ``read mode``. 

This behavior is mainly used in conjuction with the :ref:`calibrate <calibrate>` 
command/tab and to find master distortion files.
In the ``-dark=`` option of the command or in the ``Dark`` field of the 
GUI, you can use the syntax described above. So that you are sure that the matching 
dark will be fetched to calibrate the light. The same is equally 
applicable to ``Bias`` and ``Flat``. You can, of course, also give a full (or relative) 
path to the file. And the path can also contain expressions of the same kind.

For instance, for flats, you may want to specify the path to a library, which could 
contain filter or telescope information, as you may have multiple setups. A path 
like:

.. code-block:: text

   ~/astro/masters/flats/$INSTRUME:%s$_$TELESCOP:%s$/$FILTER:%s$/FLAT_bin$XBINNING:%d$.fit

Would then evaluate to:

.. code-block:: text
   
   ~/astro/masters/flats/ZWO_ASI294MC_Pro_61EDPH/DualBand/FLAT_bin1.fit

and is a valid value for ``Flat`` input.

Of course, if you were to write this as a command in your scripts or in the GUI 
``Flat`` field every time you calibrate, that could become a bit tedious. That's 
when reserved keywords come to the rescue. There are 3 reserved keywords for masters: 

- ``$defdark``
- ``$defflat``
- ``$defbias``
- ``$defdisto``

Their values are set in :menuselection:`Preferences --> Pre-processing` 
:ref:`section <preferences/preferences_gui:pre-processing>`. You can also specify 
them through scripting thanks to :ref:`set <set>` command. They correspond to the 
values of ``gui_prepro.dark_lib``, ``gui_prepro.flat_lib``, ``gui_prepro.bias_lib``.
and ``gui_prepro.disto_lib``.

When their values are set and you chose to use them as defaults, they will be 
displayed in the fields of the :guilabel:`Calibration` tab. You can also start to write your
scripts using these keywords. The calibration step of a new generic script for 
a color camera could look like:

.. code-block:: text

   calibrate light -dark=$defdark -cc=dark -flat=$defflat -cfa -equalizecfa -debayer

This would pick your masters directly from your libraries and make sure you never 
mix them up during the calibration step.

Writing a file with path parsing
++++++++++++++++++++++++++++++++

Now, while it is handy to be able to find the files, it would be equally useful to 
use this syntax to store your files while stacking. That is exactly what the 
``write mode`` is about. The syntax can be used in the ``-out=`` field of 
:ref:`stack <stack>` and :ref:`stackall <stackall>` commands, or in the corresponding 
field in the GUI.

Let's say you want to write a generic script that prepares your master darks 
each time you renew your library. In the ``stack`` line of the script, you could 
write:

.. code-block:: text

   stack dark rej 3 3 -nonorm -out=$defdark

This line ensures that the resulting masterdark will be stored at the right location 
with the correct filename that can then be fetched to calibrate your lights.

In order to introduce even more flexibility with the ``stack`` commands, there 
are two more reserved keywords:

- ``$defstack``
- ``$sequencename$``

As for default masters, ``$defstack`` is configured in the same section of the 
Preferences, or with a :ref:`set <set>` command on ``gui_prepro.stack_default``. 
For instance, let's assume you have defined ``$defstack`` as:

.. code-block:: text

   Result_$OBJECT:%s$_$DATE-OBS:dm12$_$LIVETIME:%d$s

The script line:

.. code-block:: text

   stack r_pp_light rej 3 3 -norm=addscale -output_norm -out=$defstack

would save the stack result as:

.. code-block:: text

   Result_Rosette_Nebula_2022-01-24_12000s.fit

As of Siril 1.2.0, the default name for stacking is defined as ``$sequencename$stacked`` 
(the ``_`` sign is added if not present). This is similar to the behavior in 
previous versions, except it is now explicit that pathparsing is used.


Using wildcards
+++++++++++++++

It could be that you want to use some key value in your masters name that do not 
match the key value in the frames to be calibrated. With an example, it may be a 
bit clearer. Say, you want, in your masterflats names, to keep record of their 
exposure time.  Something like:

.. code-block:: text

   FLAT_1.32s_Halpha_G120_O30_bin1.fit 

If you put a field $EXPTIME:%0.2f$ in ``$defflat``, it will end up with an error
at calibration step. Simply because the EXPTIME key will be read from the light 
frame to be calibrated, not a flat.

To deal with this situation, you can use wildcards in the expressions to be 
parsed: 

.. code-block:: text

   FLAT_$*EXPTIME:%0.2f$_$FILTER:%s$_G$GAIN:%d$_O$OFFSET:%d$_bin$XBINNING:%d$

Note the \* symbol placed right before ``EXPTIME``.

What this symbol means is the following:

- In ``write mode``, so basically when stacking your masterflat, the field ``EXPOSURE`` will be used to form the filename to be saved.
  In the example above,  you would then effectively save to ``FLAT_1.32s_Halpha_G120_O30_bin1.fit``.

- In ``read mode``, so when calibrating your lights, the field ``EXPOSURE`` will be replaced by \*.
  When searching for such file, Siril will fetch all the files that marches the pattern ``FLAT_*_Halpha_G120_O30_bin1.fit``.
  Hopefully, your naming convention is robust enough so that it finds only one matching file and uses it to calibrate.

  This can also be useful for specifying master distortion files. You could, for instance
  , use:

.. code-block:: text

   ~/astro/masters/distos/DISTO_$*DATE-LOC:dm12$_$INSTRUMEN:%s$_$TELESCOP:%s$_bin$XBINNING:%d$.wcs

Note the \* symbol placed right before ``DATE-LOC``.

When a wildcard is used in front of a ``DATE`` header key, the returned file in 
readmode is the file with the closest date smaller or equal to the file which 
header is being parsed. 

.. warning::

   In the event Siril finds more than one file in ``read mode``, it will emit a warning in the console and select the most recent file. As it may not produce the desired outcome, you should reconsider your naming convention if this happens.


Handling duplicates
+++++++++++++++++++

In some cases, there are several keywords for the same value. This is because 
software developers are free to use the same keywords or create new ones. Siril 
therefore attempts to recognize and manage duplicates. Here's a table summarizing 
known duplicates. If a file contains so-called “alternative” keywords, then Siril 
will store the value in the “primary” version.

+----------------+-----------------------------------------+
| Primary keyword| Alternative                             |
+================+=========================================+
| MIPS-HI        | CWHITE                                  |
+----------------+-----------------------------------------+
| MIPS-LO        | CBLACK                                  |
+----------------+-----------------------------------------+
| PROGRAM        | SWCREATE                                |
+----------------+-----------------------------------------+
| IMAGETYP       | FRAMETYP                                |
+----------------+-----------------------------------------+
| EXPTIME        | EXPOSURE                                |
+----------------+-----------------------------------------+
| FILTER         | FILT-1                                  |
+----------------+-----------------------------------------+
| FOCALLEN       | FOCAL                                   |
+----------------+-----------------------------------------+
| CENTALT        | OBJCTALT                                |
+----------------+-----------------------------------------+
| CENTAZ         | OBJCTAZ                                 |
+----------------+-----------------------------------------+
| XBINNING       | BINX                                    |
+----------------+-----------------------------------------+
| YBINNING       | BINY                                    |
+----------------+-----------------------------------------+
| XPIXSZ         | XPIXELSZ, PIXSIZE1, PIXSIZEX, XPIXSIZE  |
+----------------+-----------------------------------------+
| YPIXSZ         | YPIXELSZ, PIXSIZE2, PIXSIZEY, YPIXSIZE  |
+----------------+-----------------------------------------+
| CCD-TEMP       | CCD_TEMP, CCDTEMP, TEMPERAT, CAMTCCD    |
+----------------+-----------------------------------------+
| OFFSET         | BLKLEVEL                                |
+----------------+-----------------------------------------+
| CVF            | EGAIN                                   |
+----------------+-----------------------------------------+
| FOCPOS         | FOCUSPOS                                |
+----------------+-----------------------------------------+
| FOCTEMP        | FOCUSTEM                                |
+----------------+-----------------------------------------+
| STACKCNT       | NCOMBINE                                |
+----------------+-----------------------------------------+
| SITELAT        | SITE-LAT, OBSLAT                        |
+----------------+-----------------------------------------+
| SITELONG       | SITE-LON, OBSLONG                       |
+----------------+-----------------------------------------+





