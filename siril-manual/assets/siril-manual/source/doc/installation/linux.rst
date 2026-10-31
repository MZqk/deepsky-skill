#########################
Installation on GNU/Linux
#########################

.. toctree::
   :hidden:
   
Installation on Debian
======================

The binary package is available on Debian `testing <https://packages.debian.org/testing/siril>`_ 
and an old version for `stable <https://packages.debian.org/stable/siril>`_. It 
can be installed via apt, with superuser privileges:

.. code-block::bash

   apt install siril
   
Installation on Ubuntu or Linux Mint
====================================

Official repositories
---------------------
As for debian, it is available in the repositories, but the version can be 
outdated:

.. code-block:: bash

   sudo apt install siril
   
PPA repositories
----------------
The newer version is thus available in our PPA, which is the preferred way to
install Siril on Ubuntu or Linux Mint:

.. code-block:: bash

   sudo add-apt-repository ppa:lock042/siril
   sudo apt-get update
   sudo apt-get install siril

Installation of the AppImage binary
===================================

For GNU/Linux systems, we also decided to provide bundled binaries with 
AppImage (x86_64) and flatpak that works on GNU/Linux-like systems. To run the 
AppImage binary, you just have to download it and allow it to run using the 
command:

.. code-block:: bash
      
   chmod +x Path/To/Application/Siril-x.y.z-x86_64.AppImage

By replacing with the correct path and x,y and z with the version numbers. Then
a simple double-click on the AppImage starts Siril.

Installation of the flatpak
===========================

Another way to install stable version of siril is to use the 
`flatpak <https://flathub.org/apps/details/org.free_astro.siril>`_, the utility
for software deployment and package management for Linux.
To install flatpak, type the following command:

.. code-block:: bash
      
      flatpak install flathub org.siril.Siril

Then, to run the application:

.. code-block:: bash
      
      flatpak run org.siril.Siril
