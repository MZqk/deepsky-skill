File format for the bad pixels list:
* Lines in the form `P x y` will fix the pixel at coordinates (x, y) type is an optional character (C or H) specifying to Siril if the current pixel is cold or hot. This line is created by the command FIND_HOT but you also can add the two following line types manually
* Lines in the form `C x 0` will fix the bad column at coordinates x.
* Lines in the form `L y 0` will fix the bad line at coordinates y.
