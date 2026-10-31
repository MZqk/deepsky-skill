GraXpert Interface
==================

.. tip::
   The first GraXpert interface was written in C and simply called the
   GraXpert executable. However for various reasons this approach proved
   unreliable for a significant minority of users and with Siril 1.4.0-beta2
   an updated interface has been released as a Python script. This directly
   interfaces with the GraXpert model files and provides much better performance
   and integration, and since it builds on the way the Python interface
   uses the native PyPI modules library to install the correct versions of
   dependecies for each user's machine it is expected to be more reliable and
   better able to make use of different types of GPU.

   Owing to the way GraXpert distributes its AI models from an Amazon S3 instance,
   a copy of the GraXpert program needs to be available in order to download
   model files. The model files you can download are dependent on the version
   of GraXpert installed therefore it is recommended to install 3.1.0-RC2 which
   provides access to the stellar and background object deconvolution models as
   well as the denoising and background extraction models that have been available
   since version 3.0.0.

   The old GraXpert interface was marked as deprecated in 1.4.0-beta2 and has
   since been removed from the code base. This documentation covers the new
   Python-based interface.

Siril provides an interface to the AI models provided by the third party
`GraXpert <https://graxpert.com>`_ tool.
This is a third party piece of Free and Open Source software that provides
sophisticated background removal and denoising routines. There is a lengthy
history of collaboration between Siril and GraXpert, and the RBF algorithm
used in Siril's core background removal function was contributed by the
GraXpert developers several years ago.

In order to be used, the path to the working GraXpert installation must be set
in :menuselection:`Preferences->Miscellaneous`.

Installation and Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The GraXpert-AI.py script can be installed in the usual way, by going to the
:guilabel:`Scripts` menu -> :guilabel:`Get Scripts` and selecting the check box next to the
script in the list.

To configure the script you need to tell Siril where your GraXpert installation
is. To do this, go to :guilabel:`Preferences` -> :guilabel:`Miscellaneous` and
select the GraXpert executable using the appropriate file chooser widget.

General Aspects of the GUI
~~~~~~~~~~~~~~~~~~~~~~~~~~
The GUI is shown below:

.. figure:: ../_images/processing/graxpert_script.png
   :alt: dialog
   :class: with-shadow

   GraXpert script interface, showing denoising options.

The title bar changes to show the currently selected operation, and at the top
of the window are the script title and version number. A dropdown allows you
to select the operation you wish to perform. How this dropdown is populated
depends on the version of GraXpert you have available. It will always feature
Denoising and Background Extraction, even if no locla models are available (see
the section on Model Manager for information on how to download models). If you
have locally available deconvolution models, or if you have set the location of
a GraXpert version that supports deconvolution, you will also see
Deconvolution (Stars) and Deconvolution (Objects), however if these models are
not available locally and you don't have a version of GraXpert that is capable
of downloading them they will not be shown.

Next follows an area for operation-specific parameters. If you select Denoising
you will find a widget to control strength, if you select Background Extraction
you will find widgets for smoothing and the type of background extraction to
perform, and so on. These are described in more detail below.

The Advanced setting provides a means of controlling the inferencing batch size
and whether or not to use GPU acceleration. It is generally a good idea to leave
the batch size at the default: it sets how many chunks of the image can be
processed in parallel, and increasing it may result in out-of-memory errors.

.. tip::
   The GraXpert models use ONNX to perform inferencing. This is a hardware-
   agnostic Machine Learning framework that provides various runtimes targeting
   different types of acceleration hardware. There is a specific NVidia runtime,
   a DirectML runtime that provides generic Windows GPU acceleration, a MacOS
   runtime and Linux runtimes for NVidia, AMD and Intel GPUs. The script will
   automatically try to install the most suitable runtime for your hardware,
   however in the event that it does not work there is always a CPU runtime to
   revert to - simply disable the GPU acceleration checkbox and please submit
   a bug report as this is new functionality and we are especially keen to hear
   of any issues relating to the ONNX runtime selection.

Finally, the UI provides :guilabel:`Apply` and :guilabel:`Close` buttons that
perform the expected functions and a :guilabel:`Model Manager` button that
opens a tool to discover and download versions of the AI models.

When opening the GUI, it will detect whether a single image or a sequence is
loaded. If a single image is loaded it will process it and update the result
in Siril, whereas if a sequence is loaded it will process all the selected
frames of the sequence and create a new sequence with a prefix dependent on
the operation being performed.

.. warning::
   In Siril 1.4, Python scripting cannot subscribe to Siril event notifications
   so it is not possible for the script to detect if the image has been closed
   or if a change between a single image and a sequence occurs after opening the
   tool. Therefore if you do this, you should close and reopen the script GUI.

Background Extraction
~~~~~~~~~~~~~~~~~~~~~

The GraXpert background extraction AI model offers two parameters:

* Smoothing - this determines the smoothness of the background model that
  is generated.
* Correction type - this determines the type of correction that is applied:
  you can choose from subtraction or division.

Note that the GraXpert script does not provide an interface to the classical
background removal algorithms provided by GraXpert. RBF is already natively
available in Siril, and Spline and Kriging do not generally provide better
performance than RBF.
   
Deconvolution
~~~~~~~~~~~~~

From version 3.1 onwards, GraXpert supports deconvolution. With the models
downloaded, Siril supports both modes provided by GraXpert:

* Object deconvolution
* Stellar deconvolution

These modes can be selected using the :guilabel:`Operation` dropdown.

Both deconvolution modes have two parameters, :guilabel:`Strength`, which
takes values between 0.0 (no deconvolution) and 1.0 (maximum deconvolution)
and :guilabel:`Image FWHM (in pixels)`, which also takes values between 0.0
(prioritises fine structure retention) and 10.0 (prioritise deconvolution
ability). If you set it to zero you can see a very (too) small effect on
processed image, if so set it higher.

Denoising
~~~~~~~~~

To use the GraXpert AI denoising model, select :guilabel:`Denoising` in
the Operations dropdown.
This operation has only one parameter, :guilabel:`Strength`, which 
takes values between 0.0 (no denoising) and 1.0 (maximum denoising).

Denoising can take a little time, especially for large images and / or if
using a CPU for inferencing. However once the denoising is done the denoised
image is cached, so experimenting with different strengths is extremly quick.

Model Manager
~~~~~~~~~~~~~
The script provides a model manager to discover and download model versions.
Whereas inferencing is done directly in the script, model download requires
use of the actual GraXpert program because the models are stored in an
Amazon S3 instance and GraXpert stores its credentials internally. The
model manager will show radio buttons for each model that the installed
version of GraXpert is capable of downloading. For v3.0.x this means
denoising and background extraction; for v3.1.0-rcx this also adds stellar
and object deconvolution.

The list of remotely available models is shown. If you want to discover
models for a different AI operation, change the radio button to the
operation you want a model for and click :guilabel:`Refresh`. A model can
be downloaded by selecting it in the list and clicking
:guilabel:`Download selected model`. Note that the download may take some
time in the case of large models or slow network connections and GraXpert
provides little feedback, so be patient. The models are stored in the
GraXpert user data directory.

.. warning::
   Do not move the GraXpert models from the GraXpert data directory structure
   where they are saved by the model manager. This is where the script
   expects to find them and it will ignore models put in any other location.

Once a model is downloaded it will automatically be populated in the
model version dropdown in the main UI when the relevant AI operation is
selected.

.. figure:: ../_images/processing/graxpert_model_manager.png
   :alt: dialog
   :class: with-shadow

   GraXpert script interface, showing the model manager.

Scripting
~~~~~~~~~

The GraXpert-AI.py script provides a command-line interface for use in
scripting. This uses the `pyscript` command as shown below.

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/pyscript_use.rst

   .. include:: ../commands/pyscript.rst

Operation Parameters
--------------------

The following parameters are used to specify the operation to perform:

* **-denoise** specifies denoising
* **-deconv_obj** specifies object deconvolution
* **-deconv_stellar** specifies stellar deconvolution
* **-bge** specifies background extraction

General Parameters
------------------

The following parameters are recognised in all modes:

* **-h / --help** prints help to the Siril console and exits
* **-model** specifies the model version to use (e.g. -model="3.0.2")
  If this argument is omitted it defaults to the latest available version.

* **-batch** specifies the AI batch size
* **-gpu** specifies to use the GPU (this is the default)
* **-nogpu** speficies to use CPU inferencing only
* **-listmodels** causes the script to list the available model versions for
  the specified operation and exit

.. tip::
   Note that this script does not provide a -seq parameter as it expects to
   work on a sequence already loaded in Siril. When scripting, this can be
   achieved using the `load_seq` command first to load the desired sequence.

Background Extraction Parameters
--------------------------------

* **-smoothing=** This option sets the smoothing parameter, between 0.0-1.0
* **-correction=** This option sets the correction type and may be either
  "subtraction" or "division"
* **-keep-bg** This option specifies that the modelled background should be
  saved as well. For a filename such as myfile.fit, the background will be
  saved as myfile_bg.fit

Denoising Parameter
-------------------

* **-strength=** This option sets the strength parameter, between 0.0-1.0

Deconvolution Parameters
------------------------
The same parameters are available for both stellar and object deconvolution:

* **-strength** This option sets the deconvolution strength, between 0.0-1.0
* **-psfsize** This option sets the PSF size, as described above

Examples
--------

`pyscript GraXpert_AI.py -denoise -model='3.0.1'`

Or, if calling it from another Python script:

`siril.cmd("pyscript", "GraXpert-AI.py", "-denoise", "-model='3.0.1'")`
