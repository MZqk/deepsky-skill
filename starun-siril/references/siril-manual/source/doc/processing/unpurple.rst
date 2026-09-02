Unpurple Filter
#######################

This is a cosmetic filter designed to reduce the effect of purple fringing
around stars that may be visible when using an achromat refractor especially
when broadband imaging. It operates on stretched colour images.

.. tip::
   This filter is designed to work on non-linear images. It is therefore 
   imperative to use it once the histogram has been stretched.

The filter offers two modes of operation where its effects can be global or 
limited to the stars only.

It reduces purple fringing by applying a luminance mask to the blue channel 
based on the green channel, and by allowing you to reduce the blue channel 
intensity.  When in starmask mode, it limits the effects to areas around 
identified stars.

Global
======

This mode is useful when working with a starmask or with images containing 
just stars, e.g. a globular cluster. This is not so useful for nebula or 
galaxy images where the colour might be affected.

To use, you should reduce the :guilabel:`Blue Adjustment` slider until the 
effects are optimal. This is generally around 0.15. 

You may also increase the :guilabel:`Threshold` slider to restrict the effects 
to those pixels where the luminance is above this value. In this mode, the 
optimal value will be quite low.

Starmask
========

This mode is useful for combined images where you want to restrict the effects
of this filter to just the stars. 

To use, you should tick :guilabel:`Use Starmask`. If you've not already defined a 
starmask using the Dynamic PSF function, there will be a delay of a few seconds
whilst the starmask is generated. You may now temporarily reduce the 
:guilabel:`Blue Adjustment` to zero. Your stars should now be yellow. If you still
see purple fringing, you should increase the Threshold slider until the
fringing is entirely covered by yellow. Now you can increase the 
:guilabel:`Blue Adjustment` slider until your stars are not yellow but your purple 
fringing is reduced or eliminated, typically around 0.12-0.15.

.. figure:: ../_images/processing/purplestarscompare.png
   :alt: dialog
   :class: with-shadow
   :width: 100%
   
   Comparison before/after application of the unpurple filter. We can see very 
   clearly that the stars have a better color, without the purple ring.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/unpurple_use.rst

   .. include:: ../commands/unpurple.rst
