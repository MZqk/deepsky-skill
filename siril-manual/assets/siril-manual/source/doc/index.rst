.. Siril documentation master file, created by
   sphinx-quickstart on Fri Nov 25 19:02:26 2022.

Welcome to Siril's documentation!
=================================

.. image:: https://joss.theoj.org/papers/10.21105/joss.07242/status.svg
   :target: https://doi.org/10.21105/joss.07242

This is the documentation of version |release|.

Siril is a deep sky astronomical image processing tool.

.. figure:: ./_images/Siril_dialog.png
   :alt: Siril
   :class: with-shadow
   :width: 100%

Siril can cailbrate, align, stack and enhance pictures from various file
formats, even image sequence files (FITS cube, films and SER files). Siril can
also analyze images with astrometry and photometry tools.

Programming language is C, with parts in C++. Main development is done with
most recent versions of shared libraries on GNU/Linux. `Contributors are
welcome. <https://gitlab.com/free-astro/siril/-/blob/master/CONTRIBUTING.md>`_

This is the documentation, it tries to describe all Siril functions. If the
equivalent of a GUI function exists on the command line, then it is given in an
insert. Other useful resources can be found at our main website `siril.org.
<https://siril.org>`_

.. tip::
   This documentation also includes various **tip** and **warning** boxes that
   highlight important information. Additionally, there are **theory** boxes,
   which are not essential for understanding the content but are available for
   those with a mathematical background who are curious to delve deeper into
   certain concepts.

You can find here an index of  :ref:`Siril commands <genindex>`.

To report any problems in the documentation please open a ticket at the
following address: `https://gitlab.com/free-astro/siril-doc <https://gitlab.com/free-astro/siril-doc>`_.

A problem in the translation of the documentation should be reported here:
`https://gitlab.com/free-astro/siril-localized-doc <https://gitlab.com/free-astro/siril-localized-doc>`_.

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: General

   Installation
   GUI
   cwd
   Preferences
   FileFormats
   Color-management
   Sequences

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: (Pre-)processing

   Workflow
   Preprocessing
   Processing

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Scientific Analyses and tools

   Astrometry
   Dynamic-PSF
   FITS-header
   Intensity-Profiling
   Image-inspection
   Photometry
   Plot
   Statistics
   SirilForScientists

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Automation

   Scripts
   Headless
   Pathparsing

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Livestacking

   Livestack

.. toctree::
   :maxdepth: 2
   :hidden:
   :caption: Appendices

   Commands Reference <Commands>
   Python API <Python-API>
   Issues
