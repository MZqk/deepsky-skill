Installation from source code
=============================

.. toctree::
   :hidden:

Installation from source code is required if you want the latest features, if the
past release is getting old, if you want to participate in improving Siril or
not use all the dependencies.

Getting the source code
-----------------------

The source code is stored on a git repository, which you can download with this
command the first time:

.. code-block:: bash

      git clone --recurse-submodules https://gitlab.com/free-astro/siril.git

And update it the following times with these commands in the base siril
directory:

.. code-block:: bash

      git pull
      git submodule update --recursive

Dependencies
------------

Siril depends on a number of libraries, most of which should be available in
your Linux distribution or package manager of choice. The names of the packages
specific to operating systems are listed in each section below. Mandatory
dependencies are:

* `gtk+3 <http://www.gtk.org/>`_ (Graphical user interface library), at least
  version 3.20.
* `adwaita-icon-theme <https://gitlab.gnome.org/GNOME/adwaita-icon-theme>`_
  (icons) to support gtk's look and feel.
* `cfitsio <http://heasarc.nasa.gov/fitsio/fitsio.html>`_ (FITS images support).
* `fftw <http://www.fftw.org/>`_ (Discrete Fourier Transform library).
* `gsl <http://www.gnu.org/software/gsl/>`_ (The GNU Scientific Library),
  version 1 or 2 starting with release 0.9.1 or SVN revision 1040.
* `OpenCV <http://opencv.org/>`_ and a C++ compiler for some image operations.
* `Little CMS <http://littlecms.com/>`_ an open-source color management system
* `wcslib <https://www.gnu.org/software/gnuastro/manual/html_node/WCSLIB.html>`_
  for world coordinate system management, annotations and the photometric color
  calibration.
* `gtksourceview4 <https://wiki.gnome.org/Projects/GtkSourceView>`_ for 
  multiline text editing. GtkSourceView adds support for syntax highlighting, 
  undo/redo, file loading and saving, search and replace, a completion system, 
  printing, displaying line numbers, and other features typical of a source code editor.

.. note::
   Even if Siril can run in console since version 0.9.9, it is still linked
   against the graphical libraries, so you still need GTK+ to compile and run
   it.

Optional dependencies are:

* `openmp <https://www.openmp.org/>`_ for multithreading. Although optional,
  this dependency is highly recommended as the performance will be much better.
  The flag of this option is set to true by default. That means if openmp is not
  installed on your machine, you must add ``-Dopenmp=false`` in the meson setup.
* `libraw <http://www.libraw.org/>`_, `libtiff <http://www.libtiff.org/>`_,
  `libXISF <https://gitea.nouspiro.space/nou/libXISF>`_,
  `libjpeg <http://libjpeg.sourceforge.net/>`_,
  `libjxl <https://github.com/libjxl/libjxl./>`_,
  `libpng <http://libpng.sourceforge.net/index.html>`_,
  `libheif <https://github.com/strukturag/libheif>`_ for RAW, TIFF, XISF, JPEG,
  JPEG XL, PNG, HEIF and AVIF images import and export. The libraries are
  detected at compilation-time.
* `FFMS2 <https://github.com/FFMS/ffms2>`_ for film native support as image
  sequences. It also allows frames to be extracted from many kinds of film, for
  other purposes than astronomy. Versions < 2.20 have an annoying bug. It is
  recommended to install the latest version.
* `ffmpeg <https://www.ffmpeg.org/>`_ (or libav), providing libavformat,
  libavutil (>= 55.20), libavcodec, libswscale and libswresample for mp4
  sequence export.
* `wcslib <https://www.gnu.org/software/gnuastro/manual/html_node/WCSLIB.html>`_
  for world coordinate system management, annotations and the photometric color
  calibration.
* `Exiv2 <https://www.exiv2.org/>`_ to manage image metadata.
* `libcurl <https://curl.haxx.se/libcurl/>`_ for astrometry and
  photometry requests.
* `libgit2 <https://libgit2.org/>`_ to maintain a local clone of the
  siril-scripts repository.

Build dependencies
^^^^^^^^^^^^^^^^^^

To install from source code, you will have to install the base development packages::

   git, autoconf, automake, libtool, intltool, pkg-tools, make, cmake, gcc, g++

The compilers gcc and g++ from this list can be replaced by clang and clang++
(we use them for development), probably others as well.

The autotools packages (autoconf, automake, probably some others) can be
replaced by meson.

Generic build process
---------------------

Siril can be compiled either using autotools or meson.

Meson
^^^^^

The recommended way is to use meson and ninja:

.. code-block:: bash

   meson setup _build --buildtype release

   cd _build
   ninja
   ninja install

To disable some dependencies or features, use meson options ``-Dfeature=false``
or ``-Denable-feature=yes`` depending on the case.

Table below lists all configurable options.

.. csv-table::
   :file: build_options.txt
   :delim: 0x09
   :widths: 15 15 15 15 40
   :header-rows: 1

Autotools
^^^^^^^^^

The autotools ways is well known in the unix world, once the source code has been
downloaded and the prerequisites have been installed, the general way to build
it is as such:

.. code-block:: bash

   ./autogen.sh
   make
   make install

possibly with superuser privileges for the last line.

You may want to pass specific options to the compiler, for example like that if
you want optimisation and installation in ``/opt`` instead of the default
``/usr/local``:

.. code-block:: bash

   CFLAGS='-mtune=native -O3' ./autogen.sh --prefix=/opt

To launch Siril, the command name is :program:`siril` or :program:`siril-cli`.

Installation on Debian-like systems
-----------------------------------

You may want to build a ``.deb`` package instead of using a non-packaged
version, in that case see this `help <https://wiki.debian.org/BuildingTutorial>`_.
In particular, to install dependencies, you can use the command:

.. code-block:: bash

   apt build-dep siril

Otherwise, here is the list of packages for the current version:

* Packages required for the build system:

.. code-block:: bash

   autoconf automake make gcc g++ libtool intltool pkg-config cmake

* List of packages for mandatory dependencies:

.. code-block:: bash

   libgtk-3-dev libcfitsio-dev libfftw3-dev libgsl-dev libopencv-dev
   liblcms2-dev wcslib-dev libgtksourceview-4-dev

* List of packages for optional dependencies:

.. code-block:: bash

   libcurl4-gnutls-dev libpng-dev libjpeg-dev libtiff5-dev
   libraw-dev gnome-icon-theme libavformat-dev libavutil-dev libavcodec-dev
   libswscale-dev libswresample-dev libgit2-dev libheif-dev
   libexiv2-dev libjxl-dev liblz4-dev libpugixml-dev libxisf-dev

for film input (AVI and others) support:

.. code-block:: bash

   libffms2-dev

Installation on Arch Linux
--------------------------
Two packages are available on AUR: :program:`siril` and ``siril-git``. Download the
``PKGBUILD`` or the repository, install dependencies, run makepkg to build the
package and ``pacman -U`` to install it.

Dependencies (mandatory and a few optional):

.. code-block:: bash

      pacman -S base-devel cmake git intltool gtk3 fftw cfitsio gsl opencv
      exiv2 libraw wcslib gtksourceview4

LittleCMS version
-----------------
Siril requires lcms2 >= 2.14 to build. This version is required in order to
provide optmisation of the color management code. A sufficient version is available
in debian testing and Ubuntu 23.04, however the current Ubuntu LTS release
provides an older version, and other OS distributions may do too. If you find
yourself in the position of being on an older OS that does not provide lcms2
>= 2.14, you can either manually install packages from a more recent version of
your distribution, or you can build and install lcms2 from source code. The lcms2
source is available at `their git repository <https://github.com/mm2/Little-CMS>`_.


Build Failures
--------------
Every commit to Siril git is automatically built in a standard build environment
for Linux, Windows and MacOS using the gitlab CI infrastructure. This means that
we have high confidence that the master branch, as well as tagged releases, **will**
build successfully given a correctly set up build environment with the necessary
dependencies installed.

If you experience a build failure it is likely that this
indicates a problem with your build environment or incorrectly installed dependencies -
remember many distributions require separate installation of development packages
that contain the necessary header files. Check the CI report for the git commit
you are trying to build. In the unlikely event that there is a build failure
shown, rest assured the team is working to fix it. Otherwise, if the CI pipeline
shows green ticks, you will need to review and fix any issues with your own
build environment.

If you still believe you have found a build issue that has not been flagged by the
CI pipeline - for example if you're building on a different platform such as BSD
that the developers don't regularly use - then feel free to raise an issue on
`gitlab <https://gitlab.com/free-astro/siril/-/issues>`_.

Note that issues should only be raised against the master branch or tagged
releases. If you are testing new features in merge requests, please provide feedback
in comments against the relevant merge request.

System Python
-------------
If you are building Siril yourself you need to ensure you have a working system
Python installation including pip and venv. Note the last point because on some
systems (at least Debian / Ubuntu based systems, possibly others) Python is split
into many packages and you must ensure the packages that provide pip and venv are
installed.
