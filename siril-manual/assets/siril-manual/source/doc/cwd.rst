Working Directory
=================

.. |cwd-button| image:: ./_images/icons/cwd.png

The Working Directory (``wd``), also known as the Current Working Directory 
(``cwd``), is the directory in which Siril works. Its choice is a crucial step, 
especially when using scripts. The wrong choice of ``cwd`` is responsible for 90% 
of :ref:`script <Scripts:Scripting>` failures. This folder is selected by clicking 
on the :guilabel:`Home` button, in the shape of a house: |cwd-button|. This is 
the directory where Siril saves images by default (if no other path is 
specified) and also the directory where it searches for :ref:`sequences <Sequences:Sequences>`.

Once the directory has been selected, its path can be easily checked in the 
title bar of the application window, below the version in use as illustrated in
the figure below.

.. figure:: ./_images/GUI/cwd.png
   :alt: CWD shown in the title bar
   :class: with-shadow
   :width: 100%

   Working Directory path shown in the title bar. Here, this is a Linux path.
