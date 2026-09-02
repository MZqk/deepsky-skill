Common File Formats
===================

The image file formats presented here are standard formats, readable by all 
image manipulation software. These formats were designed to meet the needs 
some time ago and may be obsolete. Moreover, none of these formats have been 
designed to handle astronomical data. They must therefore generally be used at 
the end of the processing chain.

AVIF Format
***********
AVIF is a modern image format based on the AV1 video codec. This format uses the
HEIC container format. Siril supports reading AVIF images up to 12 bits
bitdepth. Lossless support may be available depending on the codecs compiled into
the installed libheif. AVIF supports color profiles using both embedded ICC
profiles and NCLX identifiers: embedded ICC profiles are fully supported, and
there is partial support for NCLX identifiers (they are converted into the
equivalent ICC profile, although currently only the most likely NCLX profiles
are supported).

The filename extensions for this format are **.avif**.

BMP Format
**********

Files with the **.bmp** extension are bitmap image files used to store digital 
bitmap images. These images are independent of the graphics card and are also 
called Device Independent Bitmap (DIB) file format. This independence allows 
the file to be opened on multiple platforms such as Microsoft Windows and Mac. 
The BMP file format allows data to be stored as two-dimensional digital 
images, both in monochrome and in color, with different color depths.

Nowadays, this format is not really used anymore and other file types are 
preferred.

HEIF Format
***********
HEIF is a modern image format based on the x265 video codec. This format uses the
HEIC container format. Siril supports reading HEIC images up to 12 bits
bitdepth. Lossless support may be available depending on the codecs compiled into
the installed libheif. HEIC supports color profiles using both embedded ICC
profiles and NCLX identifiers: embedded ICC profiles are fully supported, and
there is partial support for NCLX identifiers (they are converted into the
equivalent ICC profile, although currently only the most likely NCLX profiles
are supported).

The filename extensions for this format are **.heic** and **.heif**.

JPEG Format
***********

Probably the most used file format for sharing images on forums, by e-mail or 
usb sticks. This format allows a more or less strong (destructive) compression 
which gives ideal file sizes for exchanges. The extension of this type of file 
is **.jpg** or **.jpeg**.

The JPEG format is however only coded in 8-bit. With the compression that 
produces artifacts, this format is not very suitable for astronomy images and  
we generally prefer the PNG format.

JPEG XL Format
**************

Following several previous updates to the JPEG format (JPEG 2000, JPEG XR...)
JPEG XL represents a major jump in capability with the stated intention of
replacing all raster formats. Whether or not it achieves this, it has several
features that make it of interest for astrophotography and Siril has therefore
added read and write support. It supports high bit depths up to 32 bit float.
It supports lossy and lossless compression: for lossy compression, it achieves
much better compression ratios than JPEG for the same quality setting, and for
lossless compression, it achieves much better compression ratios than PNG. Note
that FITS metadata will not be preserved when saving as JPEG XL: its primary use
within Siril is likely to be as an archival format for final renders of images.
It may also be useful for web export: the format is not yet widely supported by
browsers, but Safari supports it natively and support is available experimentally
or using add-ons in Firefox and Chrome so we may see support become more widely
available in the future.

JPEG XL has full support for embedded ICC profiles, however to improve
compression it will save profiles it recognises with a coded identifier instead
of the full profile. This means that the ICC profile description you see after
opening a saved JPEG XL file may not be the same as it was originally, however
the ICC profile will be functionally identical.

The filename extension for this format is **.jxl**.

PNG Format
**********

Portable Network Graphics is a raster-graphics file format that supports 
lossless data compression. The extension of the format is **.png**. 
PNG grayscale images support the widest range of pixel depths of any image 
type. Depths of 1, 2, 4, 8, and 16-bit are supported, covering everything 
from simple black-and-white scans to full-depth medical and raw astronomical 
images.

Calibrated astronomical image data is usually stored as 32-bit or 64-bit 
floating-point values, and some raw data is represented as 32-bit integers. 
Neither format is directly supported by PNG.

However, this format is an excellent choice for saving the final image, after 
processing.

TIFF Format
***********

TIFF or TIF, Tagged Image File Format, represents raster images for use on 
various devices that conform to this file format standard. It is capable of 
describing bi-level, grayscale, palette color and full color image data in 
multiple color spaces. It supports both lossless and lossy compression schemes 
to allow applications that use this format to choose between space and time.
The extension is either **.tiff** or **.tif**.

The advantages of the TIFF format are multiple. It supports encoding up to 
32-bit per pixel and offers a wide variety of possible fields in the metadata 
making it a good candidate for storing astronomical data.

Using the TIFF format, and in collaboration with other developers, we have set 
up a pseudo standard, :ref:`Astro-TIFF <file-formats/Astro-TIFF:Astro-TIFF>`.

NetPBM Format
*************

Several graphics formats are used and defined by the Netpbm project. The 
portable pixmap format (PPM), the portable graymap format (PGM) and the 
portable bitmap format (PBM) are image file formats designed to be easily 
exchanged between platforms. Possible file extension are **.pbm**, **.pgm** 
(for gray scale files) and **.ppm**.

These formats, supporting up to 16-bit per channel, are marginally used and 
should only be used for final image storage.

AVI format
**********

It's a film container, able to contain data with various audio and video 
codecs. Some lossless video codecs exist and they have been used for astronomy 
imaging purposes in the past, but it's a format that does not contain 
metadata usable for astronomy, that is limited to 8-bit images and that does 
not give any warranty that the data it contains is raw.

.. warning::
   This input file format is now deprecated. We recommend to use 
   :ref:`SER <file-formats/SER:SER>` format instead.
