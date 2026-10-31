| Merges 4 sequences of images to recombine the Bayer pattern. The sequences are specified in the arguments **sequencename0**, **sequencename1**, **sequencename2** and **sequencename3**.
| 
| The Bayer pattern to be reconstructed must be provided as the second argument as one of RGGB, BGGR, GBRG or GRBG (the order of the Bayer channels must match the order of the specified sequences).
| 
| Note: all 4 input sequences **must** be present and have the same dimensions, bit depth and number of images.
| 
| The output sequence name starts with the prefix "mCFA\_" and a number unless otherwise specified with **-prefixout=** option
