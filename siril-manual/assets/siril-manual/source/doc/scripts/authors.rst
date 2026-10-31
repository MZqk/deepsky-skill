Python Script Key Information for Authors
=========================================

We welcome all script authors who wish to write scripts for Siril. However supporting
Siril, while fun, takes a lot of time and we have neither the time nor the desire to
support other people's work as well as the core Siril software. There are therefore
some guidelines to ensure that the division of responsibility is clear.

* All scripts should feature the author's name or handle, as well as a means of
  contact (website, YouTube channel, forum etc.) where they can be reached with
  comments or bug reports. For authors publishing independently this is of course
  guidance, however for authors wishing to publish their scripts via the siril-scripts
  repository this is mandatory. Scripts will not be accepted unless these details
  are included.
* Only open-source scripts will be accepted in the Siril scripts repository.
  Closed source scripts (including ones that feature a source code wrapper that calls
  a closed source program) must be independently published.
* Authors should also follow the script programming guidelines on the API page.
  These guidelines ensure that scripts can be used by end-users with minimal
  friction.

Siril-Scripts Repository Code Standards
---------------------------------------
As a script author you can write whatever scripts you like for your own use. However
for scripts to be published to the siril-scripts repository we need to require some
coding standards in order to ensure that they work with the standard Siril packages
on each of the three supported operating systems. The requirements are:

* Scripts should work with versions of Python >= 3.9. This version matches the oldest
  Python version in our binary packages, and will be adjusted over time to match the
  packages.
* Scripts should be single-file scripts. The repository was originally designed for
  legacy .SSF scripts and the assumption of one file per script remains. We understand
  that it can be good for code readability to develop complex python programs by
  splitting them into multiple files. This is fine when working locally, however the
  multiple files would need to be combined into a single file for submission to the
  scripts repository.
* If you want to implement a GUI in your script you should use the pyQt6 toolkit.

  - pyQt6 uses the well known Qt framework and it has now been verified as installing
    correctly from pypi on all the supported platforms. Qt6 is much faster than Tkinter
    for scripts that themselves have to do significant graphics manipulation, such as
    pan-and-zoom previews, and it has a nice GUI designer application available in
    Qt Creator.

  .. warning::
     tkinter is now deprecated: new Siril scripts should not use it and should be
     written using pyQt6 instead. Although tkinter is a core Python module it is
     outdated, very slow and has issues with Wayland desktops on Linux. tkinter scripts
     will continue to work throughout the 1.4 stable series, but there is a
     considerable amount of added complexity in the sirilpy code to support tkinter.
     Since pyQt6 provides a much better alternative has now been confirmed to work on
     all the platforms Siril supports, the tkinter support code (the tksiril and
     tkfilebrowser submodules) will be removed during the 1.5 development cycle and will
     be removed before 1.6.0.

  .. warning::
     Scripts using GTK GUIs will not be accepted into the scripts repository.
     This GUI framework requires substantial system dependencies. Although
     Siril itself is coded in GTK, unfortunately the python gi packages required to
     use it in python scripts are broken on Windows and we are unable to bundle them.
     If you wish, you can install the dependencies yourself and write scripts using
     GTK and they may work fine on your own machine, but this is not supported. We
     will not provide help in getting the prebuilt packages to work with unsupported
     packages; if you are determined to try this, you will likely need to build Siril
     from source.

* Avoid import version specifications that specify a package version using "<", "<="
  or "==". These constraints will result in conflicts between scripts, which all
  share the same venv. Specifying a package version using ">=" is fine. Remember to
  use ``sirilpy.ensure_installed()`` before importing non-core python module
  dependencies, to automate the process of installing them.
* Avoid importing packages that require system packages (i.e. ones that cannot be
  installed using ``python3 -m pip install``)
* Ideally, check that your script imports will install on all three target operating
  systems. If not then the development team will check them after submission, and
  you will need to fix any issues that occur.

Known Good Modules
------------------
A list of "known good" packages that we have checked will import without problem, and
with all their dependencies, on all the target operating systems, is provided below:

* astropy
* astropy-healpix
* astroquery
* ccdproc
* GaiaXPy
* matplotlib
* numpy
* opencv-python
* pandas
* photutils
* pillow
* pyfftw
* pygaia
* pyQt6
* scipy
* tk
* ttkthemes

.. tip::
   For machine learning / AI scripts, onnxruntime is very strongly recommended. It is
   cross-platform, it is hardware-agnostic and it falls back gracefully to CPU support if
   a GPU or GPU system libraries are unavailable. The ``sirilpy.utility`` submodule
   provides an ``ONNXHelper`` class to ensure the correct hardware- and OS-specific
   version of onnxruntime is installed.

Machine Learning Modules
------------------------
A variety of machine learning modules exist which can be used to write "AI" scripts.
However the state of support for heterogenous computing (i.e. GPU use) is patchy and
often requires system libraries such as CUDA or ROCm to be installed. The following
modules are regarded as key for GPU acceleration and either are supported with helper
classes or have helper classes in development.

* **onnxruntime** - used in GraXpert and Riccardo's AberrationRemover.py script, this
  package is available for all the target OSes. There are different backends to support
  different GPUs, but in some cases these are fragile. A helper class ``ONNXHelper()``
  is available to help with identification and installation of the optimum package as
  well as ensuring correct runtime fallback to CPU. Note that currently on Windows the
  ONNXHelper will select the DirectML runtime for all supported GPUs. Even though in
  theory the CUDA-based onnxruntime-gpu package might be a bit faster, it unfortunately
  depends heavily on system libraries being installed and configured correctly and the
  installation is considered too fragile to be reliable in the kind of automated
  python environment used by Siril. If users wish to use it and are confident they can
  get it to work on their system they can install it manually using
  ``sirilpy.ensure_installed("onnxruntime-gpu")`` but this is not supported and related
  issues will be closed.
* **torch** - This is a heavyweight among ML frameworks and where GPU support was once
  very much CUDA-based it is now broadening its coverage: support for NVidia and Apple
  Silicon GPUs is good, and support for AMD GPUs (using ROCm) and Intel GPUs is in
  development (ROCm is supposedly stable enough to use on Linux, not yet on Windows).
  Unfortunately torch has some dependency issues (it is excessively strict about the
  version of CuDNN it requires) which causes problems using it in the same venv as some
  other modules (including jax - see below) that pull in the nvidia-cudnn package. A
  ``TorchHelper()`` class is available which manages this by first installing torch,
  then reinstalling it with the --no-deps flag, which prevents it complaining if another
  package updates the dependencies to a higher version number. This is really ugly and
  it's unfortunate that it's necessary, but it works. If you envisage using CUDA versions
  of both torch and jax, it is better to install torch first and then jax.
* **jax** - Not a dedicated ML framework as such but a means of offloading computation
  to the GPU. jax provides a mostly numpy-compatible module called jax.numpy which can
  greatly accelerate many mathematical array processing operations by offloading them
  to the GPU and also provides functions for gradient computation that numpy does not.
  Bearing in mind that the slow part of GPU processing is the data transfer between
  system memory and GPU memory, jax provides a jit compiler to precompile its offloaded
  workloads but it still requires careful algorithm design to keep data on the GPU for
  as long as possible, only returning the final result. Jax has good GPU support on
  Linux but only CPU support on Apple and Windows, although experimental Metal support
  and Windows GPU support is in progress. This is therefore not recommended for actual
  scripts at present, but is highlighted as an interesting framework to develop against
  in the (hopefully) near future when its support broadens. A ``JaxHelper()`` class is
  under development that will help with selection of the correct jax package to install
  and testing to confirm it works properly.
* **Dependencies** Note that both torch and jax use GPU library pypi packages, so
  installation of them may pull in very large chains of dependencies - the full set of
  NVIDIA packages (CUDA, CuDNN etc.) is around 1GB. So when using these in scripts be
  sure to warn users that they may incur a large and potentially slow download on first
  use!

Excluded Modules
----------------
Completely avoid using the following modules, as they are known to cause problems on one
or more OSes:

* healpy (doesn't work on Windows; astropy_healpix can be used instead)
*

Modules with System Dependencies
--------------------------------
The following modules require installation of system packages which cannot be automated
using pip, and therefore should not be directly used in Siril scripts:

* ``gi`` (pygtk etc - requires system package installation)
*

.. warning::
   **We cannot emphasise enough that such packages are not supported.** If they work for
   you locally, that's a lucky bonus for you. They generally won't work with any of
   the prebuilt binary packages (AppImage, flatpak, Windows, MacOS). These kind of
   packages are specifically not supported and we will not provide help with getting
   them to work. All tickets relating to this kind of question will be closed as a
   duplicate of `#1527 <https://gitlab.com/free-astro/siril/-/issues/1527>`_.

   Yes, we know it's a shame. In fact we would have liked to use GTK as the toolkit for
   writing sirilpy scripts, but unfortunately the binary wheel has been broken on
   Windows for a long time and doesn't show any sign of being fixed. We can't release
   core functionality that doesn't work on one of the target OSes.

Workaround for Modules with System Dependencies
-----------------------------------------------
It may still be possible to write projects that use these kind of packages, but you will
need to package them with their dependencies and distribute them independently of
Siril or the scripts repository, and the sirilpy module can still be used to achieve
integration with Siril. If you want to, you can provide a wrapper script that can be
distributed in the scripts repository and just initializes your main program. We will
not help with any of that. As stated above, use of these packages in Siril scripts is
not supported.

Closed Source Scripts
---------------------
The python interface will happily run precompiled .pyc files. Siril is a Free and
Open-Source software, and we therefore do not encourage development of closed-source
scripts, however we don't forbid it either. Practically we can't, because even
stringent interpretations of the GNU Public License allow for writing an open source
shim that sits between Siril and a closed source application. Moreover we are in
favour of freedom, and while we choose to release Siril under the GPL to provide
freedom to our users, we also respect the freedom of developers to choose how they
release their own work.

There is some history here, too: Siril has since 1.2.0 provided a built-in interface
to Starnet, however Starnet was originally open source and only closed its doors
later on.

However, if an author chooses to release a Siril script as a closed-source .pyc then
they need to arrange all matters to do with distributing it: as we are unable to
inspect the code ourselves to perform even the most cursory checks, we cannot
host it in the Siril-scripts git repository. And of course, responsibility for
supporting such products is entirely a matter for the author - the Siril team will
offer no advice on such products.

Note that scripts must be compiled separately for each version of python: helloworld.py
would be compiled with ``python3 -m compileall helloworld.py`` which would
generate helloworld.cpython-<version>.pyc, where <version> would be 312 for Python
3.12, 313 for Python 3.13 and so on depending on the version of python used to compile
the script. This is an inherent inconvenience to closed source scripts.
