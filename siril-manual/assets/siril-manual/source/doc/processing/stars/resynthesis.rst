Full Resynthesis
================

The Full Resynthesis tool aims to help to fix badly distorted stars using
Siril's star fitting functions. It can be helpful for rescuing images that
suffer from bad coma or other distortions. If Siril can detect the stars
it can fix them.

The tool is located in the Image Processing menu, in the Star Processing
sub-menu.

The output of the tool is a synthetic star mask. In order to make use of
this, it must be recombined with a starless version of the original image.
This can be prepared using the :ref:`starnet <starnet>` command or Starnet GUI 
tool, or using third party star removal software.

This tool has no options, you simply click on the menu item to use it, or
use the command :ref:`synthstar <synthstar>`.

If no stars have been detected in the image, the tool will automatically
detect stars using the current star modelling parameters accessible via
the Dynamic PSF tool or using the :ref:`setfindstar <setfindstar>` command.

If stars have been modelled using the Dynamic PSF tool or the :ref:`findstar <findstar>`
command, the detected stars will be resynthesized using their individual
modelled luminosity profiles. A shortcut to the Dynamic PSF tool is
provided by means of the configuration button in the GUI menu next to
the Full Resynthesis tool.

It is recommended to carry out star
detection manually first, as it allows verification of the results: if
any galaxies have been incorrectly detected as stars, they can be removed
from the list of stars before running resynthesis.

Once the synthetic star mask has been created it can be combined with the
starless image using the Star Recombination tool.

Commands
********

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../../commands/synthstar_use.rst

   .. include:: ../../commands/synthstar.rst
