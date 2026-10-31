Scripting
=========

This section explains the different scripting and automation
methods available in Siril.

.. tip::
   Python scripting was introduced in the 1.3.5 development version. It is
   currently marked as **EXPERIMENTAL**. This doesn't mean it will eat your
   data: the interface itself is robust and has been tested through
   development: the present experimental nature of it is more to do with the
   fact that we don't yet know what users will do with this new capability
   and whether there may be issues or limitations that we have not foreseen,
   perhaps due to the constraints of packaging or consistency across the
   different operating systems.

   Please try it out, either as a user by using scripts published on the
   scripts repository, or as a script writer. We welcome all your feedback
   and will aim to refine the interface throughout the 1.4 stable series and
   1.5 development series.

   If you want to debug your Python scripts at runtime, tick the box next to 
   :guilabel:`Enable Python debug mode`. A tutorial detailing the steps to attach
   to the Python process is shown in `Siril tutorial page <https://siril.org/tutorials/python_debug/>`_.

.. figure:: _images/scripts/Scripts_menu.png
    :alt: script menu
    :class: with-shadow
    :name: script menu

    Scripts menu

.. toctree::
   :hidden:

   scripts/Script-files
   scripts/Python-scripts
   scripts/users
   scripts/authors
   scripts/editor
   scripts/Python-examples
   scripts/python-scripts-list

.. warning::
   **Not all scripts are written by, or the responsibility of, the Siril development team!**

   With the introduction of Python scripting the power available to Siril script
   writers has increased enormously. With great power comes great responsibility!

   :emphasis:`You`, as the user, need to ensure you trust the authors of scripts you use.
   1.4.0 introduces the script repository. Some of the scripts there have been
   written by the Siril development team but others have been written by
   third party contributors. Please read the Key Info section regarding guidance for
   script authors, end users and the rules for the script repository.

Get Scripts
-----------

A small number of basic script files come packaged with Siril, however you can add more
from the script repository. To do so, go to the :guilabel:`Scripts menu -> Get Scripts`.
This will open the :guilabel:`Scripts` tab of the :guilabel:`Preferences` dialog. Here, below
the list of local script directories, you will find a list of scripts in the repository.

The scripts can be sorted by category, script name, type or by whether or not they are
selected.

As there may eventually be a large number of scripts, not all of which will be relevant to
your workflow, you can configure the scripts you want to appear in the :guilabel:`Scripts`
menu. Simply select the checkbox next to them and click Apply to update the menu.

.. tip::
   You don't need to select scripts to appear in the menu for them to be usable with the
   ``pyscript`` command: this will search all the local script directories as well as the
   whole of the local scripts repository.

.. warning::
   Script authors may submit updates. Occasionally, inevitably, there may be a bad update that
   breaks the script for you. Bugs need to be reported to the script author: for scripts
   written by the Siril team you can report them at `the scripts GitLab repository <https://gitlab.com/free-astro/siril-scripts>`_
   and for those written by third party contributors you should report them at the contact
   details in the script.

   The script can be opened in the script editor by double clicking on it in the list. This
   is important for several reasons:

   * It allows you to find the details of the script author for bug reporting.
   * When you double click the script there is an option to revert to previous versions.
   * This means that if an update broke the script, you can open the previous version and save
     a local copy in one of your local script folders for use until the script author fixes the
     bad update. (The version saved in a local folder will take priority over a repository
     version with the same name, but it will appear in a different subfolder in the menu with
     the name of your local scripts folder.)
