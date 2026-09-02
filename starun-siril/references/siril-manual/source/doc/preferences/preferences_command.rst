Preferences (commands)
######################

Starting from v1.2, most preferences can also be set through commands, meaning 
either from direct input in the command line, through scripting or in headless 
mode.

To get a list of all the available variables, through siril command line:

.. code-block:: bash

    get -A

This will print a list of all the variables to the Console, with their current 
value and a short description (use lowercase option `-a` to omit description).

The table below lists them:

.. csv-table:: 
   :file: getA.txt
   :delim: 0x09
   :widths: 30 15 10 45
   :header-rows: 1

.. _kstars:

| (\*). For kstars catalogues, this will default to ``~/.local/share/kstars/``, irrespective of your OS.
| In any case, you will need to download them and set the path you've chosen.
| See section about using :ref:`local star catalogues <astrometry/annotations:Offline annotation catalogues>`.
| 

The values can be fetched with ``get`` command:

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/get_use.rst

   .. include:: ../commands/get.rst

The values can be modified with ``set`` command:

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/set_use.rst

   .. include:: ../commands/set.rst

