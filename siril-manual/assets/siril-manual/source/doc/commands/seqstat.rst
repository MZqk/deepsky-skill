| Same command as STAT for sequence **sequencename**.
| 
| Data is saved as a csv file **output_file**.
| The optional parameter defines the number of statistical values computed: **basic**, **main** (default) or **full** (more detailed but longer to compute).
| \\t\ **basic** includes mean, median, sigma, bgnoise, min and max
| \\t\ **main** includes basic with the addition of avgDev, MAD and the square root of BWMV
| \\t\ **full** includes main with the addition of location and scale.
| 
| If **-cfa** is passed and the images are CFA, statistics are made on per-filter extractions
| 
| Links: :ref:`stat <stat>`
