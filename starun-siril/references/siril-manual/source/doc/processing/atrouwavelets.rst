À Trous Wavelets Transform
##########################

A wavelet is a function at the base of the wavelet decomposition, a 
decomposition similar to the short term Fourier transform, used in signal 
processing. It corresponds to the intuitive idea of a function corresponding to
a small oscillation, hence its name.

There are many types of wavelet functions that have their own names, as shown 
in the figure below.

.. figure:: ../_images/processing/wavelets.svg
   :alt: Morlet wavelets
   :class: with-shadow
   :width: 100%

   An example of four different types of wavelets.
   
The À Trou Wavelet Transform used in Siril performs decomposition of an image 
into a series of scale layers, also known as wavelet layers. 

.. figure:: ../_images/processing/wavelets_layers.svg
   :alt: Morlet wavelets
   :class: with-shadow
   :width: 100%

   À Trous Wavelets Transform representation with bspline interpolation.

These layers can be extracted with the :ref:`Wavelet Layers <processing/extraction:Wavelet Layers>` 
extraction tool, however here, they are used without being visually accessible.
In general, this algorithm is widely used at the end of a planetary image 
stack. Because the noise is exclusively contained in one of the wavelet layers,
it is possible to bring out the details of the image by containing the noise 
amount.

.. figure:: ../_images/processing/wavelet_dialog.png
   :alt: dialog
   :class: with-shadow

   Wavelet tool dialog box.
   
The first thing to do is to click on the :guilabel:`Execute` button in order to
calculate the wavelet layers using the parameters defined above, such as:

* **Type**: There are two types of algorithms possible: Linear and BSpline. 
  The latter will usually be chosen, even if it is a bit slower.
* **Nb of layers**: Number of wavelet layers that will be used. 6 is the maximum
  number of layers that can be defined. To work on a larger number of layers it 
  is possible to use the command line explained below.
  
Then, each layer has a slider that allows to modify the contrast of this layer.
If less than 6 layers have been created, then only the corresponding sliders 
will be active. A value greater than 1 improves the details while a smaller 
value tends to reduce them.

This is a liveview tool. The changes are displayed in real time and you have to
click on :guilabel:`Apply` to validate them. Clicking on :guilabel:`Reset` 
resets all the sliders to 1, and thus cancels any transformation in progress.

.. figure:: ../_images/processing/Jupiter_JLD.png
   :alt: Wavelets on Jupiter
   :class: with-shadow
   :width: 100%

   Wavelets applied on a Jupiter image (courtesy of J.-L. Dauvergne). The image
   on the left is the raw image of the stacking output, while the image on the 
   right is the same image on which wavelets are applied.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/wavelet_use.rst

   .. include:: ../commands/wavelet.rst
   
.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/wrecons_use.rst

   .. include:: ../commands/wrecons.rst
   
The example given in the image above would be written in the command line as 
follow:

.. code-block:: text
   
   wavelet 6 2
   wrecons 31 5 1 1 1 1
