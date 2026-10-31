SER
===

SER file format is a simple image sequence format, similar to uncompressed 
films. Documentation can be found on the `official page <http://www.grischa-hahn.homepage.t-online.de/astro/ser/>`_.
The latest PDF document is `mirrored on free-astro <https://free-astro.org/index.php?title=File:SER_Doc_V3b.pdf>`_ 
too.

With improvements of version 2 and 3, SER handles colour images, which makes 
it perfect as replacement for the usual AVI or other film format produced by 
older capture programs in all astronomy situations. Compressed images should 
not be used for astronomy but can still be converted to SER, which will make 
the files bigger for the same quality, but easier to work with.

Siril can convert any image sequence and many film formats into SER files.
`Ser-player <https://sites.google.com/site/astropipp/ser-player>`_ is a great 
tool that allows SER files to be visualised just like any film, with many 
options and works on most operating systems.

The main issue with AVI and other film containers is that it is designed to 
work with many codecs and pixel formats, which it good for general purpose 
films, but requires astronomy software to handle a large array of actually 
different file formats. General purpose film software are often not well 
equipped to handle 16-bit per pixel values or some uncompressed data formats. 
With SER, only one file format handles it all, that's why Siril for example is 
now developing processing only for SER.

File structure
**************

A SER file has three parts:

* a 178-byte header containing images and observation information
* image data, raw pixel data
* an optional trailer containing dates for all images of the sequence

Handling colours
****************

In version 3 (2014), there are two ways of handling coloured images in SER. If 
data comes directly from a sensor, the preferred way is probably to use 
one-plane images and interpolating data from the `colour filter array <https://en.wikipedia.org/wiki/Color_filter_array>`_
(similarly to CFA file formats used in astronomy software).

The other way, added in version 3, is to use three planes to represent RGB 
image data. SER v3 supports RGB/BGR 8/16-bit data. This can be useful if data 
is converted from a source with an unknown colour filter array or for general 
purpose conversion.

Specification issue with endianness
***********************************

Since SER files can contain 16-bit precision images, endianness must be well 
specified. The specification allows endianness to be either big-endian or 
little-endian, to facilitate file creation on various systems, as long as the 
used endianness is documented in the file's header.

For an unknown reason, several of the first programs to support SER disrespect 
the specification regarding the endianness flag. The specification states that 
a boolean value is used for the LittleEndian header, and they use it as a 
BigEndian header, with 0 for little-endian and 1 for big-endian. Consequently, 
to not break compatibility with these first implementations, later programs, 
like Siril, `GoQat <https://free-astro.org/index.php?title=GoQat>`_, Ser-player
and many others, have also decided to implement this header in opposite 
meaning to the specification.
