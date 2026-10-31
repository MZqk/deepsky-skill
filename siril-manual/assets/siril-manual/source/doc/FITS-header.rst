FITS header
============

Keywords contained in the header of a FITS file can be displayed in Siril. To do 
this, simply click on :menuselection:`Tool --> FITS Header`.

Since version 1.3.0, it is possible to modify the value of keywords supported by 
Siril, either with the :ref:`update_key <update_key>` command, or via the GUI, 
in the appropriate window. This window is divided into two tabs. The first, the
:guilabel:`Header Editor`, lets you edit the keywords of the loaded image. When a
sequence is loaded, the changes are applied to every image of the sequence (see
the note below). It may vary from what the file header actually looks like, and
represents more the state it will be in after saving. Keyword values are updated
in real time.
The second tab displays the header in text format as written in the file and as 
it was represented for Siril versions below 1.3.0.

.. warning::
   HISTORY keywords are not displayed in the editor. They are visible in the 
   :guilabel:`Saved Header` tab.

Another way of displaying the header is to use the command line :ref:`dumpheader <dumpheader>`.
It shows the same header as displayed in the :guilabel:`Saved Header` tab.

.. warning::
   As SER files contain very few keywords and are different from FITS 
   files, this command is not applicable to this type of sequence.

Key name, key values and key comments can be modified, however, the key must be 
unprotected. For easy recognition, protected keys are displayed in 
salmon color.

Editing is very simple, just :kbd:`double-click` on the cell to be modified. The first
time selects the field, the second opens the edit mode. Pressing the :kbd:`Enter`
key validates the entry. To make the changes effective, remember to save the file.

.. note::
   When a sequence is loaded, editing a key name, value or comment applies the
   change to the FITS header of **every image** of the sequence. A confirmation is
   asked the first time; editing then stays enabled until the window is closed.
   This is the graphical equivalent of the :ref:`sequpdate_key <sequpdate_key>`
   command and is not available for SER sequences.

.. warning::
   Please note that Siril does not check the validity of the value entered. It
   is up to the user to enter a valid value. An incorrect value may lead to 
   undesirable behavior in Siril's keyword processing.

.. figure:: ./_images/GUI/FITSHeader.png
    :alt: FITS Header
    :class: with-shadow
    :width: 100%

    FITS Header dialog when editing the value of a keyword.
    
.. figure:: ./_images/GUI/FITSHeader_2.png
    :alt: FITS Header tab 2
    :class: with-shadow
    :width: 100%

    Second tab of the FITS Header dialog.

The window contains an option in the form of a :guilabel:`Copy selection` 
button. It copies the selected lines to the clipboard in the original format of 
the FITS header, and works for both tabs.

Finally, you can add a new keyword using the :guilabel:`+` button at bottom 
left, and delete one using the :guilabel:`-` button:

1. To add a keyword, click on the :guilabel:`+` button. A new dialog box opens, 
   as shown below. This window lets you add a new keyword, whose name will be 
   limited to 8 characters (the ``HIERARCH`` convention is not used). You can 
   leave the :guilabel:`Comment` field empty, if the other two are filled in. 
   However, the user can only fill in the :guilabel:`Comment` field to add a 
   single comment to the FITS header. Finally, if the keyword exists, then its 
   value will be updated with the information provided.
   
   .. figure:: ./_images/GUI/add_keyword.png
      :alt: Adding keyword
      :class: with-shadow

      The window for adding new keywords.
   
2. To delete a keyword, select one or more lines and click on the :guilabel:`-`
   button (or with the keys :kbd:`Del` or :kbd:`Backspace`). The selected keywords 
   will be deleted, unless they are protected.
   
   .. tip:: When a sequence is loaded, it is only possible to select one line at 
      a time. This means you can only delete one keyword at a time from a 
      sequence. But for a single image, you can select several.
    
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/dumpheader_use.rst

   .. include:: ./commands/dumpheader.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/update_key_use.rst

   .. include:: ./commands/update_key.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ./commands/sequpdate_key_use.rst

   .. include:: ./commands/sequpdate_key.rst
 
