Python Scripts
==============

New to Siril 1.4, it is possible to write more advanced scripts using python.

This page is a documentation for script developers. If you want more
information about what scripts are available as a user, see
:ref:`the list of scripts <scripts/python-scripts-list:Current list of Python scripts for Siril 1.4>`.

.. tip::
   Python scripting is still **EXPERIMENTAL** and the API is expected to
   become more advanced during the 1.5 development cycle.

In its current form, python scripting allows you to:

* Execute any siril command with the added benefit of using a proper programming
  language so that variables passed to commands can be the result of
  calculations.
* Access key Siril variables and data structures to aid in scriptwriting.
* Access python extensions.

    * Using the 1.4 scripting interface it is possible to write a plugin that
      saves the current image, opens it using astropy and applies processing
      techniques provided by astropy, then saves the result and reopens it as
      the Siril main image.
    * The **tk** python extension can be used to create graphical user interfaces
      for your scripts so that user input can be obtained directly.

.. warning::
   **Run-time Dependencies**
   
   To support installation and configuration of
   the siril python module and its dependencies, a Python venv ("virtual
   environment") is configured.
   
   **Linux**

   This requires that there is a functional
   Python installation on the system _including the ``pip`` and ``venv``
   modules. These are available by default as part of the flatpak and
   appimage distributions, but you will need to install them yourself if
   building Siril from source. On Debian-based systems you need the
   ``python3-pip`` and ``python3-venv`` packages installed; the
   installation requirements on other systems may vary.

   **Windows**

   If you have a Python installation in your system, the script will use that as 
   default. If you don't, don't panic. Siril installed through the official installer
   also comes with a light-weight Python installation and all the required modules.
   That is for the official releases.
   If you build the development version from sources, you will need to have 
   Python installed in your system.

   **macOS**

   The necessary dependencies are included in the official MacOS package. We
   do not provide support for homebrew building, but if you do build yourself on
   Mac then you will need to ensure you have a functional system python
   installation.

How does this relate to pySiril?
--------------------------------
pySiril is a separate module, published by a separate development team: it is
an established product and is already used to implement important helper apps
such as Sirilic.

Philosophically, pySiril and the built-in Siril python module are quite different:
whereas the built-in module is intended to be called from an already-running Siril
to provide scripting functionality and spawns a python3 process to run the script,
pySiril is intended for buildin applications that need to call on Siril
functionality, and it spawns a Siril process from within its host Python app.

These use cases are quite different, and there is no plan to merge the two modules.

Importing the Module
--------------------
Siril will take care of setting up a venv (new style python virtual environment)
and installing the module in it. In your script you import it using ``import sirilpy``.

For convenience, you can abbreviate it as ``import sirilpy as s``.

Initialising the Connection
---------------------------
Once you have imported the module, you need to establish the interface with Siril.
This is done as follows:

.. code-block:: python

   import sirilpy as s

   siril = s.SirilInterface()
   try:
      siril.connect()
      print("Connected successfully!")
   except SirilConnectionError as e:
      print(f"Connection failed: {e}")

(The status reporting is optional but is good practice.)

Executing Siril Commands
------------------------
To execute a Siril command, once the interface is established, you use the
:py:meth:`siril.cmd() <siril.connection.SirilInterface.cmd>` function. The command and its arguments are provided as a list,
for example:

.. code-block:: python

   siril.cmd("findstar", "-maxstars=1000")

will run the ``findstar`` command, setting a limit of maximum 1000 stars.

The real power of using python to script commands is that you can use python
variables in the command using the formatted string notation:

.. code-block:: python

   x = 1000
   siril.cmd("findstar", f"-maxstars={x}")

This is a simple example, but the ability to define parameters to pass to image
processing functions is a giant leap for Siril scripting compared with legacy
Siril script files.

.. tip::
   Most Siril commands execute an action (``register``, ``stack``, ``asinh`` etc).
   These will function exactly the same when called from python.

   However some simply print information to the Siril log, such as ``stat``. Note
   that these commands do not behave any differently when called from python using
   ``siril.cmd("stat")`` or similar: they will still just print information to the
   Siril log and do not return any useful data to the python script.

   In order to access the values from python you need to use a method that gets
   information about the image or sequence. So in this example, you would call
   ``img = siril.get_image()`` and then the statistics would be available in
   ``img.stats``.

.. warning::
   The ``cmd()`` method does not have a return value but will raise an exception
   if the command fails. This provides safe default behaviour - the script will
   stop on failure of a command and print an exception error message to the
   Siril console. If you want to handle failure more gracefully, for example by
   showing an error messagebox, you will need to use an exception handler like this:

   .. code-block:: python

      try:
          siril.cmd("requires", "1.5.0")
      except:
          siril.error_messagebox("This script is not compatible with this version of Siril")
          quit()

   For debug purposes you may wish to have commands return a bool. This can be
   done using a simple wrapper such as the following:

   .. code-block:: python

      def cmd_with_check(*args):
      try:
          siril.cmd(*args)
      except Exception as e:
          err_msg = ' '.join(list(args))
          print(f'Exception caught: {e}',)
          print(f"Command failed with arguments: {err_msg} but continuing...")
          return False
      return True

Access to Siril variables and data structures
---------------------------------------------
The siril module provides access to key Siril variables. The available data
is as follows:

* Current Siril working directory
* Siril user config directory (for storing script-specific config)
* Current Siril image filename
* Current Siril image:

  * Image pixel data
  * Image ICC profile (as bytes: you will need to use a module such as pillow
    to convert this raw data to something useful)
  * Image metadata: all the metadata that Siril uses internally, including
    relevant keywords and image statistics. The full FITS header and a string
    containing keywords not recognized by Siril are also available.
* Current Siril sequence:

  * Sequence frame pixel data
  * Sequence metadata: all the metadata for the currently loaded sequence
    is available, with the exception of some that relates to details of the
    sequence format that are abstracted away by the python interface and some
    relating to photometry series that is not implemented yet. This includes
    statistics for every channel of every frame, registration data for channels
    for which it is available of every frame and image quality data for every
    frame.
  * Sequence frame ICC profiles are not available as these are not generally
    relevant until after the sequence processing stage. If a convincing use case
    emerges this could be revisited as an update to the API in the 1.5
    development cycle.
  * Sequence frame HISTORY and FITS header strings are not available as they
    are not considered useful for sequence operations, however the keywords
    for a sequence frame are available using the :py:meth:`siril.get_seq_frame() <siril.connection.SirilInterface.get_seq_frame>`
    method, which returns a FFit object with the keywords member populated. This
    provides enough metadata to support any currently identified use case.
* Star modelling data. When stars have been detected in an image the modelling
  data is available as a list of star parameters for every detected star in
  the image.

This data is stored in the key data structures :py:class:`FFit <siril.models.FFit>` representing the Siril
fits data structure and :py:class:`Sequence <siril.models.Sequence>` representing the Siril sequence data
structure.

.. code-block:: python

   # Get the current image
   img = siril.get_image()
   # Get its dimensions
   siril.log(f"Current image dimensions: {img.shape[1]} x {img.shape[0]}, {img.shape[2]} channels.")

Note that the image shape is stored in a typical python order: shape[0] = height,
shape[1] = width, shape[2] = channels. The width, height and channels are also
directly accessible using the properties ``img.width``, ``img.height`` and
``img.channels`` once ``img = get_image()`` has been called.

When the current image is obtained using ``siril.get_image()``, the pixel
data can be accessed as a numpy array, allowing directly operating on pixel
data.

.. code-block:: python

   import siril
   import numpy as np
   # Set up the interface as above
   # ...

   # Try to get the current image, do something to it and update it in Siril
   with siril.image_lock():
      img = siril.get_image()
      img.data[:] *= 2
      siril.set_image_pixeldata(img.data)

   except Exception as e:
      raise SirilException(f"Error changing pixel data: {e}")

We just accessed the pixel array and multiplied every pixel value by 2! Note
that we have to call the ``siril.set_image_pixeldata()`` function to update the
data back to Siril. Using ``siril.set_image_pixeldata()`` you can even update
the image to a different size or shape, or change from 16-bit unsigned data to
32-bit floating point or vice versa.

.. tip::
   In order for ``siril.set_image_pixeldata()`` to succeed you must claim a
   lock on the image. This ensures that nothing else can try to update the
   image at the same time as you are doing, and also ensures that you can't
   update the image while something else is already doing so. Note that the
   example above uses the context handler ``siril.image_lock()`` **before**
   getting the image from Siril, not just before updating it. This ensures the
   image is locked throughout the time it is being processed until it is
   updated back in Siril and the lock is finally released.

In a similar way you can access image statistics, FITS header information,
bit depth and so on. You can also access sequence information, for example
whether the :emphasis:`n` th image in a sequence is included, what its stats
are and so on.

Installation of Python Modules
------------------------------
You might have noticed we are using NumPy in the previous examples. This is
a dependency of the siril module and will be pulled in during initial setup
of the venv. Other python modules can be imported using the standard
``import`` command, for example ``import astropy as ap``. However, for
ease of ensuring that dependencies are installed if a user doesn't already
have them, the module also provides the ``ensure_installed()``
method. This is used as follows:

.. code-block:: python

   s.ensure_installed("astropy")
   import astropy

(or, for another example,

.. code-block:: python

   s.ensure_installed("tifffile")
   import tifffile

First a check is done to try importing the module (in this case, astropy or
tifffile). If the module is not available for import, an attempt is made to
install it. If the install fails, an exception is raised and the script will
halt.

.. tip::
   If a module requires installation this may take a short time, however the
   delay only occurs on the first run of a script. On subsequent runs the
   model is already installed, so the ``ensure_installed()`` check is almost
   instantaneous. A message in the log notifies the user that module installation
   is occurring.

If the check succeeds, the module can be imported as normal using ``import``.

.. tip::

   **All other non-core modules except for numpy should be checked with**
   ``ensure_installed()`` **as this automates the installation**
   **process for users who don't already have the required modules installed.**

.. warning::
   All Siril python scripts share the same venv. When importing modules, it
   is important to avoid overconstraining the version requirements in order
   to avoid clashes between the modules required by different scripts. Only
   ">=" version constraints should be used, never "==" or "<=".

.. code-block:: python

   import sirilpy as s
   s.ensure_installed("astropy")
   import astropy as ap

This will automatically pull in all of astropy's dependencies as well.

Use of External Modules
-----------------------
.. tip::
   The :py:class:`siril.FFit <siril.models.FFit>` object type is not the
   same as astropy.io.fits. Whereas
   astropy.io.fits provides a general file-based interface to all FITS files,
   siril.FFit provides an interface to the data structure Siril uses to
   represent FITS images. The two are not directly interchangeable! Either
   method can be used to obtain the pixel data as a NumPy array.

Whether you obtain the pixel data directly from the
:py:class:`siril.FFit <siril.models.FFit>` object or using
astropy.io.fits, siril modules such as astropy, photutils and matplotlib
provide building blocks for a huge range of image analysis including source
detection, image segmentation, source deblending and visualisation, and the
ecosystem of python modules provides limitless capability.

.. code-block:: python

   import sirilpy as s
   s.ensure_installed("astropy")
   import astropy
   from astropy.io import fits
   with fits.open("filename.fit", mode='update') as image:
      if isinstance(image[0].data, np.ndarray):
         image[0].data *= 2

This looks much like our last example - all we do is multiply the pixel data
by 2 - but now we have used astropy to open it directly from a FITS file.

API Reference
-------------

Note that the :ref:`API <python-api:sirilpy python module api |module_version| reference>` is
very new. While we will try very hard to avoid script-breaking changes to the
API released with 1.4.0 throughout the stable 1.4 series, some parts of the
API may evolve during the 1.5 development cycle.

