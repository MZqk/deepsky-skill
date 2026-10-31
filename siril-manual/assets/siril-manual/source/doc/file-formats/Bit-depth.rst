Bit Depth
=========

The bit depth, defines the number of bits used to indicate the color of a 
single pixel, or the number of bits used for each color component of a single 
pixel.

For images of daily life, 8-bit is more than enough. This means that a pixel 
is encoded on values in the range [0, 255]. However, photographing 
astronomical objects is more demanding and usually requires working on images 
with a bit depth of at least 16-bit: *i.e.* in the range [0, 65535]. Even 
better, 32-bit precision allows the most subtle information to be retained. 
On this last type, the pixels are either encoded in the interval 
[0, 4294967295], or, as used in Siril, between the floating values [0, 1]. It 
is possible to find formats encoding pixels on 64-bit (in the range [0, 1], 
but they are rare and have a very specific use. In particular the FITS format 
allows this.

However, not all image file formats support 16-bit, let alone 32-bit. This 
must therefore be taken into account when choosing a format to work with.

.. figure:: ../_images/file-formats/16bit.png
   :alt: 16-bit image
   :class: with-shadow
   :width: 100%

   Linear image saved in 16-bit
   
.. figure:: ../_images/file-formats/8bit.png
   :alt: 8-bit image
   :class: with-shadow
   :width: 100%

   The same linear image saved in 8-bit. Almost all data have been lost
