.. code-block:: text

    stackall
    stackall { sum | min | max } [-maximize] [-upscale] [-32b]
    stackall { med | median } [-nonorm, norm=] [-32b]
    stackall { rej | mean } [rejection type] [sigma_low sigma_high] [-nonorm, norm=] [-overlap_norm] [-weight={noise|wfwhm|nbstars|nbstack}] [-feather=] [-rgb_equal] [-out=filename] [-maximize] [-upscale] [-32b]