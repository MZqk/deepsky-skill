Python Script Key Information for Users
=======================================

Obtaining Scripts
-----------------

End users should take care to obtain scripts from reputable sources.

* A few fundamentally important scripts are distributed as part of the core Siril package
  and can be found installed in the system data directory. These are tested to the
  same degree as the rest of Siril and the development team will treat bug reports
  on these scripts in exactly the same way as bug reports on any other part of Siril.
* 1.4.0 introduces the Scripts repository. We try to keep a list of the scripts with a
  short description :ref:`here <scripts/python-scripts-list:Current list of Python scripts
  for Siril 1.4>`. Anyone may submit scripts to this repository by making an account on
  gitlab and submitting a merge request. The Siril team will provide a basic level of
  scrutiny that the scripts are not attempting to do anything malicious, but we do not
  accept any responsibility for the correct functionality of scripts written by other
  people and will not provide support for them - the author should be contacted directly.
* Script authors may choose to distribute scripts independently, as has been done in
  the past. This is fine: scripts can be downloaded and added to a script directory or
  run via the script editor from anywhere on the filesystem. In this case the Siril
  team have nothing at all to do with the scripts and cannot even assure you that they
  will do no harm, so ensure you trust the author.
* Siril does not support automatic verification of signed scripts. This is because script
  signing can provide a false sense o security ("the script is signed so it can't do
  anything bad"). This is false: all a script signature says is that the script was
  signed by a particular person and hasn't been changed since. In other words, it
  verifies the origin of a script, but says nothing about whether it functions correctly
  or is safe. **A signed and verified script could still destroy your data if that is**
  **what it is written to do!**
  In the current implementation of python scripting, the majority of scripts are distributed
  through the centralised script repository in which case the origin is known and scripts
  have been through a basic level of scrutiny by the development team prior to being merged.
  Some scripts may be distributed through other channels and downloaded manually, and there
  is nothing preventing an author providing a sha256sum or a .gpg signature for scripts
  distributed in this way - you will just need to verify them manually after downloading.

Troubleshooting Common Issues
-----------------------------

Siril's python script system is quite complex: a lot goes on under the hood, and this is
all coordinated by clever use of a Python venv ("virtual environment") which ensures that
the python used by Siril is separated from the system Python installation and potentially
from other venvs that may be in use by other software. However there are a few things to
be aware of:

* venvs are tied to a specific version of python. This means that if your system updates
  from (e.g.) Python-3.12 to Python-3.13 the venv will no longer point to the right Python
  program or the right bytecode libraries for compiled modules. **When you upgrade to a new**
  **version of Python you must reset the Siril venv**.
* script dependencies are controlled using pip and the pypi archive. This is very good but
  not 100% foolproof, and in rare cases you may find a dependency issue has arisen that
  causes a problem of some kind. **If you find an unexplained problem with a script that**
  **others aren't experiencing, resetting the venv may solve it.** (And if you find a
  combination of scripts that can repeatably cause a dependency issue like this, please
  report it as an issue on the siril-scripts gitlab site as it will help us to improve
  advice to script authors.)

Resetting the venv is easy: click the :guilabel:`Get Scripts` menu item in the Scripts menu
and scroll down to the bottom. Here you will find a "Reset venv" button: clicking it will
reset the venv and create a new one. After doing this, the next time you run a script it
will need to reinstall its dependencies so the first startup will be a bit slower than ususal
(depending on network speed and whether any of the required modules are locally cached).
