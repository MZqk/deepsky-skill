#####################
Installation on macOS
#####################

.. toctree::
   :hidden:
   
Installation of our app
=======================

The macOS app is provided per architecture:

* Intel (macOS 13 Ventura+)
* Apple Silicon (macOS 13 Ventura+)

Choose the link corresponding to your processor architecture and download the disk image. Once downloaded, double-click to open it.

.. figure:: ../_images/installation/macos/macos-dmg.png
   :alt: macos-dmg
   :class: with-shadow
    
A new window pops up. Now drag the ``Siril`` icon and drop it on  the ``Applications`` one.
   
.. figure:: ../_images/installation/macos/macos-dmg-mount.png
   :alt: macos-dmg
   :class: with-shadow
   :width: 100%
    
Congratulations, Siril is now installed.

Installation from Homebrew
==========================

Homebrew is similar to MacPorts and provides packages (aka formulae) to install, either by compiling them from source or by using pre-compiled binaries (aka bottles). To install Homebrew, click `here <https://brew.sh/>`_.
Siril can be installed with::
   
    brew install siril

.. note::
   Please be aware that it was announced that Homebrew is using analytics. To turn this off, run: ``brew analytics off``
   You can read more about this on `Brew Analytics <https://docs.brew.sh/Analytics>`_.
