Definitions and workflow
========================

Astrophotography is the process of capturing images of celestial objects. It 
involves several steps, including preprocessing and processing, which are 
distinct but related.

:ref:`Preprocessing<Preprocessing:Preprocessing>` is the initial step of 
working with raw astrophotographic data. 
It involves preparing these data for further processing. This step typically 
involves dark current subtraction, flat field correction, and correction of 
other basic problems such as removing hot and cold pixels.

:ref:`Processing<Processing:Processing>` refers to the post-processing of the 
preprocessed data, generally after stacking. This is where the 
astrophotographer applies various techniques to enhance the final image and 
bring out details and features. These may include sharpening (deconvolution), 
color calibration, noise reduction, and stretching the image to increase the 
visibility of faint details.

In short, preprocessing sets the stage for processing by ensuring that the 
data is in an appropriate form and cleaned of unwanted signal, while processing 
is about bringing out the best in the signal to create the final image. Both 
steps are important in the astrophotography process, and the quality of the 
result depends on the skills and techniques applied at both stages.

In Siril, the main preprocessing is done following the order of the tabs in 
the right pane and requires the use of master files. This is a process that can 
be automated quite easily and the scripts provided in Siril perform this task. 
The image processing is processed via the dedicated menu 
:guilabel:`Image Processing`. This process is more difficult to automate 
because it is specific to each image and consists of an iterative work.
