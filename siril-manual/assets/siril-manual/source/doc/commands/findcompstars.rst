| Automatically finds comparison stars in the field of the plate solved loaded image, for photometric analysis of a star's light curve according to
| - the provided name of the star
| - the field of view of the image, reduced to a diameter of its height if **-narrow** is passed, avoiding stars in the corners
| - the chosen catalog (APASS by default), can be changed with **-catalog={NOMAD|APASS}**
| - the difference in visual magnitude from the variable star, in the range [0, 6] with a default of 3, changed with **-dvmag=**
| - the difference in color with the variable star, in the range [0.0, 0.7] of their B-V indices with a default of 0.5, changed with **-dbv=**
| - the maximum allowed error on Vmag in the range [0.0, 0.1] with a default of 0.03, changed with **-emag=**.
| 
| The list can optionally be saved as a CSV file compatible with the NINA comparison stars list, specifying the file name with **-out=**. If the provided name is the special value **auto**, it is generated using the input parameters
| 
| See also LIGHT_CURVE
| 
| Links: :ref:`light_curve <light_curve>`
