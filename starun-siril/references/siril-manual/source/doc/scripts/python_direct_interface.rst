Pixel Data and Metadata
#######################

The sirilpy python module offers direct access to a wide range of data.

.. code-block:: python

   import sirilpy as s
   import numpy as np
   siril = s.SirilInterface()
   try:
      siril.connect():
      print("Connected successfully!")
   except SirilConnectionError as e:
      print(f"Connection failed: {e}")
      quit()

   def siril_module_example():
      try:
         # Lock the main image to prevent anything else altering it while we
         # are working on it
         with siril.image_lock():
            # Get the global fits object
            fit = siril.get_image()

            siril.log(f"The value of the first pixel is: {fit.data.flat[0]}")
            fit.data[:] *= 2
            siril.set_image_pixeldata(fit.data)

         siril.log(f"The value of the first pixel is now: {fit.data.flat[0]}")
         siril.log(f"Data type: {fit.data.dtype}")
         siril.log(f"Array shape: {fit.data.shape}")
         history = fit.history
         siril.log(f"History: {history}")

      except AttributeError as e:
         siril.log(f"Error: {e}")
         fit = None  # Ensure the FFit object is released

   # Run the function
   if __name__ == "__main__":
      siril_module_example()

As before, we import the siril module: we now also import numpy,
which we will use to operate on the pixel data in the image loaded in Siril.
Note that there is no need to use the ``import_or_install()`` method, as
numpy is a dependency of the siril python module and will have been
preinstalled in the venv.

Next, we demonstrate various ways of interacting with the image data and
metadata:

.. code-block:: python

   # Get the global fits object
   with siril.image_lock():
      fit = siril.get_image(True) # True indicates that we want the pixel data
      # not just the metadata. It is not required as this is the default, but
      # if you only want the metadata you can specify False here to save a bit
      # of memory.

      siril.log(f"The value of the first pixel is: {fit.data.flat[0]}")
      fit.data[:] *= 2
      siril.set_image_pixeldata(fit.data)
      siril.log(f"The value of the first pixel is now: {fit.data.flat[0]}")

We get the current image using ``siril.get_image()``

.. tip::
   Note that ``siril.fits`` is not at all the same as ``astropy.io.fits``.
   While astropy.io.fits provides a general-purpose interface to all FITS
   files, siril.fits provides an interface matching the fits data structure
   used in Siril.

We then access the pixel data as a NumPy array and carry out operations on
this array to modify the image data. In this case we multiply all the pixel
values by 2.

After we have finished modifying the image, we call ``siril.set_image_pixeldata(fit.data)``
to update the pixel data in Siril and trigger the Siril GUI to redraw to show
the updated image. Along the way we use ``siril.log()`` to highlight the change
to the first pixel.

Next we show the array datatype and shape:

.. code-block:: python

   siril.log(f"Data type: {fit.data.dtype}")
   siril.log(f"Array shape: {fit.data.shape}")

Next we show how we can access the image history. This is provided as a
list of individual HISTORY FITS headers, so we join them together and add
newline characters before logging.

.. code-block:: python

   history = fit.history
   siril.log(f"History: {history}")

Full details of the API can be obtained by importing the Siril module and
calling the ``help(sirilpy)`` command, or more specifically by calling ``help()``
for the particular part of the API you want to know about, e.g.
``help(sirilpy.FFit)``.

As docstrings are not translatable, for translations of the API help you
will need to consult the API documentation page in this online manual.
