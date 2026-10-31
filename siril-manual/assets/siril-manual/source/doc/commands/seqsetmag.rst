| Same as SETMAG command but for the loaded sequence.
| 
| This command is only valid after having run SEQPSF or its graphical counterpart (select the area around a star and launch the PSF analysis for the sequence, it will appear in the graphs).
| This command has the same goal as SETMAG but recomputes the reference magnitude for each image of the sequence where the reference star has been found.
| When running the command, the last star that has been analysed will be considered as the reference star. Displaying the magnitude plot before typing the command makes it easy to understand.
| To reset the reference star and magnitude offset, see SEQUNSETMAG
| 
| Links: :ref:`setmag <setmag>`, :ref:`seqpsf <seqpsf>`, :ref:`psf <psf>`, :ref:`sequnsetmag <sequnsetmag>`
