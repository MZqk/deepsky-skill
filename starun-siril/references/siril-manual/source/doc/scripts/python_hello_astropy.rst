Hello, astropy!
###############

This script shows how to save a temporary working copy, open it using
astropy.io.fits and modify it before saving the file and reopening it
in Siril.

.. code-block:: python

   import sirilpy as s
   import tempfile
   import os
   import gc
   import numpy as np # numpy is a dependency of the siril module and
   # will always be available, so no need to use s.ensure_installed() here

   s.ensure_installed("astropy")
   import astropy
   from astropy.io import fits
   siril = s.SirilInterface()
   try:
      siril.connect():
      print("Connected successfully!")
   except SirilConnectionError as e:
      print(f"Connection failed: {e}")
      quit()

   def hello_astropy():
      siril.log("Starting the process...")

      temp_filename = None
      try:
         # Create a temporary file
         with tempfile.NamedTemporaryFile(suffix=".fits", delete=False) as temp_file:
               temp_filename = temp_file.name
               siril.log(f"Temporary file created: {temp_filename}")

         # Save current file
         siril.cmd("save", temp_filename)
         siril.log(f"FITS file saved: {temp_filename}")

         # Open and modify FITS file using Astropy
         with fits.open(temp_filename, mode='update') as image:
               siril.log("Opened FITS file with Astropy")

               # Modify the FITS file data
               if isinstance(image[0].data, np.ndarray):
                  image[0].data *= 2
                  siril.log("Modified FITS file data using Astropy (multiplied pixel values by 2)")

               image[0].header['COMMENT'] = "Modified by Astropy"
               siril.log("Added COMMENT to FITS file header")

               image.flush()  # Ensure changes are written
               siril.log(f"Changes saved to FITS file: {temp_filename}")

         # Load back into Siril
         siril.cmd("load", temp_filename)
         siril.log(f"FITS file loaded: {temp_filename}")

         siril.log("Process completed successfully")

      except Exception as e:
         siril.log(f"An error occurred: {str(e)}")
         raise  # Optionally re-raise if needed for further handling

      finally:
         # Clean up: delete the temporary file
         if temp_filename and os.path.exists(temp_filename):
               try:
                  os.remove(temp_filename)
                  siril.log(f"Temporary file deleted: {temp_filename}")
               except OSError as e:
                  siril.log(f"Failed to delete temporary file: {str(e)}")

         # Garbage collection using gc.collect() is not required at the end
         # of a script as it gets run automatically just after the script
         # hands back to the interpreter

   # Run the function
   if __name__ == "__main__":
      hello_astropy()

Note the use of error handling in this example, using the try: and
except: blocks, and the cleanup in the finally: block.
