Script Editor
=============

Siril contains a script editor, accessible via the Scripts menu. This provides
a feature rich code editor tailored for writing both Siril Script Files and
Python scripts. The theme (light / dark) of the script editor follows that set
in the main :guilabel:`Preferences` dialog. The main editor window is shown
below:

.. figure:: ../_images/scripts/script_editor.png
    :alt: script editor
    :class: with-shadow

For most of the functionality shortcuts exist, which are as standardised as
possible.

File Menu
---------

The file menu provides the usual functionality:

* **New**: begins a new file. If there is unsaved text in the buffer a confirmation
  dialog will be presented to give the user a chance to save their previous
  work.
* **Open**: open a file from disk. If If there is unsaved text in the buffer a
  confirmation dialog will be presented to give the user a chance to save their
  previous work.
* **Recent files**: provides quick access to open recent files.
* **Save**: saves the current file with its existing filename. If the file is
  not yet saved then this behaves in the same way as Save as, prompting for a
  filename.
* **Save as**: saves the current file, prompting for the filename to save it with.
* **Close**: closes the file and the script editor window. If there is unsaved text
  in the buffer a dialog will be presented to give the user a chance to save
  their work.

Unsaved files are indicated by an asterisk (*) next to the filename in the
titlebar. When changes are saved, the asterisk will disappear.

Edit Menu
---------

The edit menu provides the following functionality:

* **Undo**: undoes the most recent change in the script. The script editor has an
  unlimited undo / redo buffer.
* **Redo**: redoes the most recent undo in the script.
* **Cut**: cuts the current selection to the clipboard. If no selection is made, the
  entire current line is cut.
* **Copy**: copies the current selection to the clipboard.
* **Paste**: pastes the current clipboard content to the cursor position.
* **Find**: Presents the :guilabel:`Find overlay` which provides an active search
  capability. The editor will scroll to the first occurrence of the search term
  as you type. All occurrences are highlighted in the editor view and in the
  minimap, if enabled.

Script Menu
-----------

The script menu provides the following functionality:

* **Run**: runs the current script. Note that this obeys the current script type
  selection (Siril Script File / Python Script) and this must be correct for the
  script you are editing. The script type is automatically set based on the
  file extension when loading a file, but you must set it here if you have not
  yet saved your script. At startup it defaults to Python Script. Note that
  owing to a shortcut clash in some desktop environments, as well as the listed
  :kbd:`F5`, :kbd:`Ctrl-R` also works to run the current script.
* **Python scripts**: sets the current script mode to Python scripts. This affects
  the syntax highlighting as well as the manner in which the script is run.
* **Siril Script Files**: sets the current script mode to Siril Script Files. This
  affects the syntax highlighting as well as the manner in which the script is
  run.
* **Enable test arguments**: Shows an entry below the main script view. This can
  be used to enter arguments that will be passed to the script when run from the
  editor. Python scripts can take arguments to set parameters: this provides an
  alternative to using a GUI to set parameters. Even if a GUI is provided in a
  script, the ability to pass arguments means that it can be used as a Siril
  command via the ``pyscript`` command, and can therefore itself be used from
  within scripts.
* **Enable python debug mode**: This toggle changes the behaviour of the sirilpy
  module to support debugging. When the SirilInterface is created at the start of
  the script, a modal information box will appear showing the process ID of the
  python interpreter, which can be used to connect a debugger. Also, all
  interface timeouts will be set to None, to allow for prolonged inspection of
  the state of a running script.

  Note that the test arguments have no effect on Siril Script File scripts, as these
  have no ability to handle arguments.

Preferences
-----------

The preferences menu provides the following options:

* **Highlight syntax**: when enabled, Siril will use language-aware syntax
  highlighting to highlight aspects of your script.
* **Enable right margin indicator**: when enabled, Siril will show a right margin
  indicator at a set column number. This is useful for helping to avoid
  excessively long lines of code and maintain readability.
* **Right margin position**: this will present a small dialog allowing the user to
  set the column number of the right margin indicator discussed above. This
  defaults to 80.
* **Enable bracket matching**: when enabled, if the cursor is on a bracket the
  editor will highlight the matching bracket. This can be useful to avoid
  bracket mismatches.
* **Show line numbers**: when enabled, line numbers are shown to the left of the
  editor view. This can help with debugging and navigating code.
* **Show line markers**: when enabled, line markers are shown to the left of the
  editor view. No functionality currently uses line marks, but the ability to
  view them is provided to support future developments.
* **Highlight current line**: when enabled, the current line is highlighted in the
  editor view.
* **Enable auto-indentation**: when enabled, on pressing Enter the new line will
  begin at the same level of indentation as the previous line.
* **Indent on tab**: when enabled, pressing tab with a selection made that covers
  multiple lines will cause all the selected lines to be indented a level, and
  pressing shift-tab will cause them to be unindented a level.
* **Enable smart backspace**: when enabled, pressing backspace with whitespace to
  the left of the cursor will delete whitespace back to the previous indentation
  level.
* **Smart Home / End**: when enabled, Home and End will move the cursor to the first
  or last non-whitespace character of the line respectively, rather than the
  absolute first or last character of the line.
* **Show spaces and tabs**: when selected, spaces and tabs will be shown with
  visible characters (central dots for spaces, right-facing arrows for tab
  characters).
* **Show newlines**: when selected, newlines will be shown with visible characters
  (down-and-left bent arrows).
* **Show minimap**: when selected, a minimap will be shown to the side of the editor
  view, supporting navigation and location of ``Find`` occurrences.

Help
----

The help menu provides API help for both Siril Script Files and Python scripts.

* **Python API Reference**: this opens the Python API page of the online manual in
  the default browser.
* **Commands Reference**: this opens the Commands Reference page of the online
  manual in the default browser, which is essentially the API for Siril Script
  Files.
