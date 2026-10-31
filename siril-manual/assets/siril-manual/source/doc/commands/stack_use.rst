.. code-block:: text

    stack seqfilename
    stack seqfilename { sum | min | max } [-output_norm] [-out=filename] [-maximize] [-upscale] [-32b]
    stack seqfilename { med | median } [-nonorm, -norm=] [-fastnorm] [-rgb_equal] [-output_norm] [-out=filename] [-32b]
    stack seqfilename { rej | mean } [rejection type] [sigma_low sigma_high]  [-rejmap[s]] [-nonorm, -norm=] [-fastnorm] [-overlap_norm] [-weight={noise|wfwhm|nbstars|nbstack}] [-feather=] [-rgb_equal] [-output_norm] [-out=filename] [-maximize] [-upscale] [-32b]