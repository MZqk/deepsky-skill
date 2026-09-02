Livestacking
============

Live Stacking is a technique in astrophotography that allows the real-time 
stacking of a series of images to produce a higher quality image. Unlike 
traditional image stacking, which involves combining multiple images after they
have been captured, live stacking combines the images as they are being 
captured. This can provide an instant preview of the final image and allow the 
astrophotographer to make adjustments in real-time to improve the quality of 
the final result.

Siril 1.2.0 includes this feature, which however is **experimental** for the 
moment. It allows to monitor a file in real time and to stack the images that 
arrive as they come in. Stacking of images can be done with and without 
Dark/Bias/Flats. The latter must be done beforehand if you want to use them.

Livestacking (GUI)
******************

.. note::
   Only FITS and RAW camera images are compatible with livestacking.

To start livestacking you need to :

- Define a working directory, with the home button, in which the photos will 
  arrive one after the other.
- Click on the framed button on the image below.

.. figure:: ./_images/livestacking/ls_main_menu.png
   :alt: livestacking menu
   :class: with-shadow
 
A new window pops up.

.. figure:: ./_images/livestacking/ls_main.png
   :alt: livestacking menu
   :class: with-shadow
   
This window contains several buttons and options. A play button, which when 
clicked becomes a pause button, and a stop button. The first one allows to 
start or pause the monitoring of the working directory, and the last one to 
stop it.

All other options are fairly standard in the preprocessing of astronomical 
images:

* **debayer**: The Bayer matrix is detected in files and debayer is
  automatically enabled. This is more of an indicator than an option.
* **use 32 bits**: Use 32 bits for image processing. This is slower and 
  generally useless in terms of quality for live stacking.
* **remove gradient**: Apply a linear background gradient removal on calibrated
  input frames.
* **shift-only registration**: Only shift images instead of using rotation on 
  registration. This should be unchecked for alt-az or heavily drifting mounts. 
  This makes one image processing much faster.

The 3 sections below provide the information necessary for the user to follow 
the evolution of the stack.

* **Statistics**: This session gives the evolution of the noise level in ADU 
  as well as the image processing time.

* **Stacking**: This section summarizes the number of images stacked and the 
  cumulative exposure time.

* **Configuration** is a section that is not expanded by default. Once it is 
  done, the frame gives details if the preprocessing is done using master 
  files and the type of alignment and stacking.

  .. figure:: ./_images/livestacking/ls_config.png
     :alt: livestacking configuration
     :class: with-shadow

.. note::
   To use master files during a livestacking session, you must first have 
   stacked your files. Then, once done and before launching the session, please
   load them in the :guilabel:`Calibration` tab. They will then be taken into 
   account in the livestacking, and visible in the :guilabel:`Configuration` 
   part of the livestack dialog.

Livestacking (Headless)
***********************

It is possible to use livestacking from the command line. For this, only 3 
commands are necessary and explained below.

* The first, :ref:`start_ls <start_ls>` starts the livestacking session. It is 
  possible to provide it with darks and flats images as arguments in order to 
  calibrate the images during livestacking.
   
  .. admonition:: Siril command line
     :class: sirilcommand
   
     .. include:: ./commands/start_ls_use.rst

     .. include:: ./commands/start_ls.rst
     
* The :ref:`livestack <livestack>` command is to be applied to each image you 
  wish to stack.
   
  .. admonition:: Siril command line
     :class: sirilcommand
   
     .. include:: ./commands/livestack_use.rst

     .. include:: ./commands/livestack.rst
   
* Finally, the :ref:`stop_ls <stop_ls>` command stops the livestacking session.   
   
  .. admonition:: Siril command line
     :class: sirilcommand
   
     .. include:: ./commands/stop_ls_use.rst

     .. include:: ./commands/stop_ls.rst
