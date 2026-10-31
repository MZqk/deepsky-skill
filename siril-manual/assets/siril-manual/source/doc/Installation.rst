############
Installation
############

Each version of Siril are distributed for the 3 most common platforms (Windows,
MacOS, GNU / Linux) and can be downloaded on the 
`Siril website <https://siril.org/download/>`_. But of course, as Siril is a 
free software and you can build the application from the sources.

.. tip::
   It may be useful to check the integrity of the binary or package you have 
   just downloaded. The list of SHA checksum is available on `this page 
   <https://siril.org/siril_versions.json>`_, in json format.

At the end of the installation you can try the :ref:`capabilities <capabilities>` 
command to learn more about your installation.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/capabilities_use.rst

   .. include:: ./commands/capabilities.rst
   
.. rubric:: Understanding Siril version numbers

Starting with 1.0 version, stable versions of Siril (such as 1.0, 1.2, etc.) 
are indicated by even numbers and are designed for everyday use. Development 
versions, indicated by odd numbers (such as 0.99.0, 1.1.0, etc.), are not 
usually available as packages or binary executables, and must be compiled by 
the user. The third and last number, called micro numbering, corresponds to 
the number of releases that brought bug fixes and other small contributions
(such as 1.0.1, 1.0.2, 1.0.3, etc.).

Minimum System Requirements
---------------------------

To ensure optimal performance, it is recommended that your system meets the 
following minimum specifications:

- **RAM**: At least 8GB of RAM is recommended for all systems to ensure smooth 
  operation. Higher RAM capacity is beneficial as Siril utilizes available RAM 
  extensively for processing tasks.
- **Storage**: An SSD (Solid State Drive) is highly recommended for faster 
  read/write speeds, resulting in **order-of-magnitude faster execution** compared 
  to traditional HDDs


Specific platform requirements are as follows:

- **Windows**: Windows 8.1 or later.
- **macOS**: macOS 13 Ventura or later.
- **GNU/Linux**: No specific minimum requirements stated. Typically, any modern 
  distribution should suffice.


OS specific installation
------------------------

The following pages explain installation procedures for the different OSes.

.. toctree::
   :hidden:

   installation/linux
   installation/windows
   installation/macos
   installation/source

