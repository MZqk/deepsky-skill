Merge CFA Channels
==================

.. figure:: ../_images/processing/merge_dialog.png
   :alt: Merge Elements dialog
   :class: with-shadow
   
The purpose of this tool is to combine multiple monochrome images that have 
been previously extracted from a CFA sensor (with the 
:menuselection:`Extraction --> Split CFA channels...` menu for example). The 
tool merges the separate red, green (x2), and blue channel images into a single 
composite image called CFA image.

.. warning::
   This tool is dedicated to images from a Bayer matrix and therefore it cannot
   work with images from X-Trans files from Fuji cameras.

The dialog is split in three different parts:

* **Input files**: Select the images containing the CFA0, CFA1, CFA2 and CFA3
  Bayer subpatterns. If these have been produced using Siril's "Split CFA"
  function they will have the CFA prefix.

* **Bayer Pattern**: Sets the Bayer pattern header to be applied to the result.
  This must match the Bayer pattern of the image that the original Bayer 
  subchannels were split from.

* The sequence part, at the bottom, allows to process whole sequences by 
  reconstituting a CFA image sequence. Clicking on the :guilabel:`Apply to 
  sequence` button displays a help text to proceed correctly. This text is 
  reported in the next tooltip. There are two available options:
  
    - **Sequence input marker**: Identifier prefix used to denote CFA number 
      in the separate CFA channel images. This should be set to whatever sequence
      prefix was used when the split_cfa process was run (default: ``CFA_``).
    - **Sequence output prefix**: Prefix of the image names resulting from the 
      merge CFA process. By default it is ``mCFA_``.
  
  .. tip::
     You may have any of the CFA0, CFA1, CFA2 or CFA3 sequences selected in the
     main window sequence tab: Siril will determine which one it is from the
     prefix and the number.

     Your separate sub-CFA sequences should have been processed in exactly the
     same way; they **must** have the same sequence name format and CFA marker
     string, differing only but the number 0-3 following the CFA marker
     string (e.g. CFA0_bg_pp_lights, CFA1_bg_pp_lights, CFA2_bg_pp_lights and
     CFA3_bg_pp_lights).
     
     Each image in the sequence will only be processed if the corresponding 
     images for the other 3 CFA channels can be found. Both Green subchannel
     images are required. Note this means that if you discard an image
     containing one CFA channel of an image between split_cfa and merge_cfa,
     merge_cfa will be unable to merge the remaining CFA channels for that
     image. All sequence filtering should be done either before split_cfa or
     after merge_cfa.


  
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/merge_cfa_use.rst

   .. include:: ../commands/merge_cfa.rst
     
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/seqmerge_cfa_use.rst

   .. include:: ../commands/seqmerge_cfa.rst
