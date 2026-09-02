#################################
Installation on Microsoft Windows
#################################

.. toctree::
   :hidden:

Installation with the installer
===============================

The recommended way to install Siril is to use the provided installer which will guide you step by step.

.. figure:: ../_images/installation/windows/installer_start.png
   :alt: installer_start
   :class: with-shadow
    
   First screen of the installer, you need to accept the agreement to continue.
    
.. figure:: ../_images/installation/windows/installer_end.png
   :alt: installer_end
   :class: with-shadow
    
   Last screen of the installer. You can chose to start Siril right after installation, and to open the tutorial explaining the first steps.
    
The Siril setup wizard will install all the necessary files in the right place and at the end you will have the choice to create or not a shortcut on the desktop.

.. note::
   Siril will be installed in C:\\Program Files\\Siril. If you don't have the rights to install to this folder, use a portable version instead (see :ref:`portable_binary`.)
   
.. _portable_binary:

Installation of the portable binary
===================================

If you want to use Siril without installing all kinds of files on your computer (for example if you don't have administrator permissions on the machine), then it is recommended to use the portable version. It comes in the form of a zip file, which you just have to extract to the location of your choice, then go to the ``bin`` folder to run ``siril.exe``. You can also create a shortcut on your desktop to make it easier to launch the application.

.. warning::
   Be careful, under no circumstances you should move the ``exe`` file, or any other file. Otherwise Siril will not run.

Building on Windows with Msys2
==============================

These instructions are made for compiling on Windows with MSYS2 using the UCRT64 toolchain. MSYS2 requires 64-bit Windows 10 or newer, and does not work with FAT filesystems.

.. note::
   Starting with Siril 1.4.2, UCRT64 is the only supported build environment on Windows.
   The MinGW64 environment, previously supported as an alternative, has been deprecated by
   the MSYS2 project on March 15th, 2025. Several Siril dependencies have already been
   removed from the MinGW64 package index, and more are expected to follow. All future
   releases of Siril will be built exclusively with UCRT64.

   If you are using the installer or portable binaries provided on the Siril website,
   this change is transparent to you. If you are building Siril from sources, you must
   use the UCRT64 environment.

   Using UCRT64 also increases the number of files that can be handled in a sequence
   to 8192.

`Download MSYS2 64bit <https://www.msys2.org/>`_, a software distribution and building platform for Windows, and run the ``x86_64`` installer for 64-bit. When asked, specify the directory where MSYS2 will be installed.

.. |urcrt64-logo| image:: ../_images/installation/windows/ucrt64.ico
   :width: 24px

Run MSYS2 directly from the installer, or later |urcrt64-logo| **MSYS2 MinGW UCRT x64** from the Start menu or a shortcut.

.. warning::
   Make sure to launch the UCRT x64 shell (the icon at the top of the terminal window should be yellow).

First, update the package database and core system packages by typing (for more information about pacman, see `this page <https://wiki.archlinux.org/index.php/pacman>`_)::

   pacman -Syu

Installing dependencies
~~~~~~~~~~~~~~~~~~~~~~~

To install the dependencies, enter the following command::

   curl -s -o siril-deps.sh https://gitlab.com/free-astro/siril/-/raw/master/build/windows/native-gitlab-ci/siril-deps.sh
   bash siril-deps.sh

.. note::
   The link above points to the latest version of the dependencies installation script.
   We have had a tendency in the past not to remove any dependencies (rather to add more),
   so even if you want to build the stable branch, you should be okay using the latest
   dependencies script. In case a dependency is missing for a specific version, you can
   always check the script from the corresponding tag in the GitLab repository.
   
Building from source
~~~~~~~~~~~~~~~~~~~~

The source code is hosted on GitLab. Download it with the following command::

   git clone https://gitlab.com/free-astro/siril.git
   cd siril
   git submodule update --init

Generate the build system and compile the code::

   meson setup _build --buildtype release
   ninja -C _build install
   
To launch your build of Siril, open an MSYS2 UCRT64 shell and type::

   siril

You can also create a shortcut to ``siril.exe``, located by default at ``C:\msys2\ucrt64\bin\``.

To update your version, open an MSYS2 UCRT64 shell, then::

   pacman -Syu
   cd siril
   git pull --recurse-submodules
   meson setup _build --reconfigure
   ninja -C _build && ninja -C _build install
   
If ``git pull`` does not show any changes, there is no need to rebuild. Otherwise, the above commands will update your build. Then launch Siril with::
 
   siril

.. warning::

   Starting with Siril 1.3.6, you will need to have `Python <https://www.python.org/downloads/windows/>`_
   installed on your system if you build from sources. The first time you launch a
   self-built Siril, you must do so from a native Windows environment, not from the
   MSYS2 shell, in order for Siril to detect the Windows Python installation rather than
   the MSYS2 one (a notification will appear in the console if this step is skipped, and
   the sirilpy module will not be initialized). This is a one-time requirement. Once the
   virtual environment has been set up, you can start Siril from the MSYS2 shell as usual.

   To launch Siril in a Windows environment, either open a Windows terminal and run
   ``C:\msys2\ucrt64\bin\siril.exe``, or locate this file in Explorer and double-click it.
