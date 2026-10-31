| Defines stars detection parameters for FINDSTAR and REGISTER commands.
| 
| Passing no parameter lists the current values.
| Passing **reset** resets all values to defaults. You can then still pass values after this keyword.
| 
| Configurable values:
| 
| **-radius=** defines the radius of the initial search box and must be between 3 and 50.
| **-sigma=** defines the threshold above noise and must be greater than or equal to 0.05.
| **-roundness=** defines minimum star roundness and must between 0 and 0.95. **-maxR** allows an upper bound to roundness to be set, to visualize only the areas where stars are significantly elongated, do not change for registration.
| **-minA** and **-maxA** define limits for the minimum and maximum amplitude of stars to keep, normalized between 0 and 1.
| **-focal=** defines the focal length of the telescope.
| **-pixelsize=** defines the pixel size of the sensor.
| **-gaussian** and **-moffat** configure the solver model to be used (Gaussian is the default).
| If Moffat is selected, **-minbeta=** defines the minimum value of beta for which candidate stars will be accepted and must be greater than or equal to 0.0 and less than 10.0.
| **-convergence=** defines the number of iterations performed to fit PSF and should be set between 1 and 3 (more tolerant).
| **-relax=** relaxes the checks that are done on star candidates to assess if they are stars or not, to allow objects not shaped like stars to still be accepted (off by default)
| 
| Links: :ref:`findstar <findstar>`, :ref:`register <register>`, :ref:`psf <psf>`
