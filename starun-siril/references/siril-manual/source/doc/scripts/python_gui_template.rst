Templates for Scripts with GUIs
###############################

The following templates show how to create scripts with a GUI. The first template is the simplest and only provides a GUI interface:

.. literalinclude:: gui_template.py
   :language: python
   :linenos:
   :caption: gui_template.py

`Download gui_template.py <gui_template.py>`_

The second template is very similar, but provides an alternative interface using
a command-line argument vector. This allows the script to be called with arguments
using the ``pyscript`` Siril command: it is useful for scripting algorithms that you might wish to use as part of a bigger scripted workflow.

.. literalinclude:: gui_and_args_template.py
   :language: python
   :linenos:
   :caption: gui_and_args_template.py

`Download gui_and_args_template.py <gui_and_args_template.py>`_

