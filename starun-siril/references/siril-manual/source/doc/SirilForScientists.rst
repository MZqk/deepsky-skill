Siril for Scientists
####################

For years Siril has been a great tool for amateur astronomers. With its
capability to produce light curves, query online catalogues, run operations on
image sequences, and many other recent additions, it has also become a great
tool for scientific analysis. This page is a quick reference for scientists who
want to learn quickly about Siril, in form of a list of Q&A.

How can Siril help me with my research?
=======================================
Siril's default file format is FITS files, containing either a single image or
several (see the FITS cubes question below). Siril has always been developed
with data accuracy in mind. Displaying an image can be done in various ways
without destroying input data.

Siril has many tools that can help scientific observations: star detection,
elaborate plate solving, registration of an image sequence using stars or using
astrometric solutions, aperture photometry with magnitude calibration using
catalogues, automated light curve creation, object lookup from online
catalogues (stars, galaxies, solar system objects). Many of those operations
can be done from the GUI or from text inputs (commands or scripts), some
operations produce comma-separated values (CSV) files for results, some can
produce graphs directly in Siril.

Which version of Siril should I install?
========================================
Siril follows an even/odd versioning system, e.g. 1.2.x was a stable series 
whereas 1.3.x was a development series. For scientific use it is recommended to 
install the latest version of the latest stable series: download links can be 
found at `siril.org <https://siril.org>`_. Beta versions, those distributed on
the website, can also be considered quite stable for general use.

How can I get in touch with the developers?
===========================================
The official forum is on `pixls.us <https://discuss.pixls.us/siril>`_. If you
want to contact us privately you can send us a direct message there or on
`gitlab <https://gitlab.com/free-astro/siril>`_. Our email addresses can also
be found in the AUTHORS file. If you speak French you can find us on Astrosurf
or Webastro. Feel free to reach out if you have specific needs for your
research.

How accurate are Siril results?
===============================
Many algorithms in Siril rely on peer-reviewed published algorithms which can
be found in the documentation page of the features. We are testing Siril often,
sometimes comparing results with other tools, but we don't have the resources
to have a automated tests on all pieces of the software, so data comes with no
warranty. We know some things could be improved, for example error computation
on magnitudes, which depend on camera gain, not always available in the FITS
header.

Astrometric data is as good as any and supports the WCS FITS convention.

How can I cite Siril?
=====================

If you use Siril in your research, please cite:

    Richard, C., et al. (2024). Siril: An Advanced Tool for Astronomical Image Processing. 
    *Journal of Open Source Software*, 9(102), 7242. 
    https://doi.org/10.21105/joss.07242

**BibTeX format:**

.. code-block:: bibtex

   @article{Richard2024,
     title={Siril: An Advanced Tool for Astronomical Image Processing},
     author={Richard, Cyril and others},
     journal={Journal of Open Source Software},
     volume={9},
     number={102},
     pages={7242},
     year={2024},
     publisher={The Open Journal},
     doi={10.21105/joss.07242}
   }

How can I open FITS cubes with Siril?
=====================================
FITS cubes come in two shapes for Siril:

* those that contain several images of the same size and format, we call those
  FITS sequences or fitseq for short, and use them as an alternate file
  representation of image sequences;
* those that contain several images of changing properties, or even tables and
  other data formats. Siril will only read image data, but is able to work on
  each image of such FITS cubes if an option is
  enabled in settings (named :guilabel:`Allow FITS cubes to have images of different sizes` 
  in :ref:`FITS Options <preferences/preferences_gui:FITS Options>`). Most sequence
  operations will not be available on this kind of file.

Siril can also extract images from FITS cubes to individual FITS files or to other
formats if needed.

How do I measure FWHM in an image?
==================================
Siril can give you the FWHM of a single star if you draw a selection around it
in the GUI, then right click and select PSF, or for the stars in an area of the
image or the full image. You can also use the :guilabel:`quick PSF` button in 
the main toolbar.

More generally: open your image and open the :ref:`Dynamic PSF tool <Dynamic-PSF:Dynamic PSF>`
(from the :guilabel:`Tools` menu). From there clicking on the first button will start the
star detection, within the selected area if you drew one. Clicking on the Sigma
icon will give mean values for all detected stars. See also the next question.

How do I use star detection (source extraction)?
================================================
Siril's source extractor can be found in the GUI in the
:ref:`Dynamic PSF tool <Dynamic-PSF:Dynamic PSF>`, it is also available as the :ref:`findstar <findstar>`
command. It is normally only able to detect stars and will struggle if two
stars are too close from each other, or if stars have asymmetrical or very
non-Gaussian shapes, but there are many settings that you could adjust to your
needs, including brightness, amplitude, roundness thresholds or star shape 
(Gaussian or Moffat).

The GUI will display the found stars by clicking on the button on the left of
the lower bar, giving quick feedback on the detection settings. The GUI and the
command can produce a CSV file that can then be used to check extracted sources
in an image or in a sequence of images (command only).

The list of stars can be sorted by property, clicking on a row will highlight
the star in the image.

What astrometry tools are available?
====================================
Siril has now a very high quality astrometric solver (plate solver) and tools
that interact with the astrometric solution. All is based on the FITS WCS
convention and on WCSLIB. Here is a list of tools related to astrometry:

* Plate solving with local Gaia DR3 catalogue extract or with remote full Gaia
  DR3, PPMXL or APASS.
* Display stars from the catalogue on image, to have a visual feedback of the
  accuracy of the astrometric solution, using the :ref:`conesearch
  <conesearch>` command.
* :ref:`Query <astrometry/annotations:Annotations>` online catalogues for an object using its
  name and the image time.
* Get centroid equatorial J2000 coordinates for any detected object.
* Show a mark on image for user provided J2000 coordinates (:ref:`show <show>`
  command).
* Align images on the celestial grid, possibly creating a mosaic (an image
  bigger than the original).
* Correct for geometrical aberration of images, using a master correction image
  based on astrometry.

Can it handle solar system objects?
===================================
Siril can query `IMCCE <https://www.imcce.fr>`_ services to find a solar system
object coordinates and display the expected position on images. It can also
display the known solar system objects in an image. Observer location will be
required for those uses and can be configured with MPC codes in the settings.
See the :ref:`annotations <astrometry/annotations:solar-system objects>`
documentation page.

Soon, Siril will be able to do synthetic tracking of known solar system
objects, making them appear on stacking if they are too faint to be seen on
single exposures.

But Siril is not made to process closeup view of planets with multi-point
stacking like other tools such as `PlanetarySystemStacker (PSS)
<https://github.com/Rolf-Hempel/PlanetarySystemStacker>`_ does.

Can Siril work with spectra?
============================
Siril was not designed to handle spectrometry data. An
:ref:`intensity-profiling:intensity profiling` tool that shows the graph of
pixel intensities can be used for preview of a spectrum. FITS tables
are not displayed in any way.

Can Siril replace IRAF?
=======================
Some features will be missing, many will be much more easy to do with Siril. We
don't yet have a list.

Siril has been developed since 2012 and we don't plan to stop anytime soon.
There are 3 active developers and thousands of amateur users.

