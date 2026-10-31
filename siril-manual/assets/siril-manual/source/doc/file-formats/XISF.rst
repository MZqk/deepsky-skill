PixInsight XISF
===============

PixInsight introduced a file structure called Extensible Image Serialization 
Format or XISF. This format was written to replace the FITS format, commonly 
used in astrophotography. However, this format was essentially designed by, and 
for, the PixInsight software, and `many important types of metadata are stored 
in private namespaces <https://pixinsight.com/forum/index.php?threads/pcl-astrometricsolution-properties.22627/post-144255>`_ 
that the Pixinsight developers advise cannot be relied on and may change 
arbitrarily between XISF specification versions. Moreover, the way a keyword is 
used to describe a given feature can be `very opaque 
<https://pixinsight.com/forum/index.php?threads/xml-keys.18472/#post-145431>`_. 
As a result, the advantages it could bring for use within Pixinsight are 
generally nil for other software. Moreover, the FITS file format is a recognized 
and widely used format in the community. NASA itself uses it for its own images. 
It's very flexible and powerful, and contains all the features needed for modern 
astrophotographic processing.

We have therefore decided to enable Siril to read this type of file, but in 
read-only mode. Saving in XISF format is not, and will not be, implemented.
