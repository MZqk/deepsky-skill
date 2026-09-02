| 
| With filtering being some of these in no particular order or number:

.. code-block:: text

    [-filter-fwhm=value[%|k]] [-filter-wfwhm=value[%|k]] [-filter-round=value[%|k]] [-filter-bkg=value[%|k]]
    [-filter-nbstars=value[%|k]] [-filter-quality=value[%|k]] [-filter-incl[uded]]

| Best images from the sequence can be stacked by using the filtering arguments. Each of these arguments can remove bad images based on a property their name contains, taken from the registration data, with either of the three types of argument values:
| - a numeric value for the worse image to keep depending on the type of data used (between 0 and 1 for roundness and quality, absolute values otherwise),
| - a percentage of best images to keep if the number is followed by a % sign,
| - or a k value for the k.sigma of the worse image to keep if the number is followed by a k sign.
| It is also possible to use manually selected images, either previously from the GUI or with the select or unselect commands, using the **-filter-included** argument.
