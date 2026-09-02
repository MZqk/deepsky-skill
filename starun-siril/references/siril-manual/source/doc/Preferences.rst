###########
Preferences
###########

Preferences are settings which are persistent for every session of Siril, and
which define your prefered choices for many of the tools.

Since version 1.2.0, they are accessible both from the :doc:`user interface <preferences/preferences_gui>` 
or programatically, by using :doc:`set/get commands <preferences/preferences_command>`.


By default, the preference file is located at:

* :file:`~/.config/siril/configX.Y.ini` (Linux)
* :file:`%LOCALAPPADATA%\\siril\\configX.Y.ini` (Windows)
* :file:`~/Library/Application Support/org.free-astro.Siril/siril/configX.Y.ini` (MacOS)

Where `X` and `Y` are major and minor release numbers.

If you wish to have multiple configuration files, you can choose which one to 
start from by opening a terminal and typing:

.. code-block:: bash

      siril -i path/to/my_other_config.ini

.. warning::

   Siril needs to be in your path to use :program:`siril` as in the line above. 
   Otherwise, use the full path to Siril binary.


.. toctree::
   :hidden:

   preferences/preferences_gui
   preferences/preferences_command

