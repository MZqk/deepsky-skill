Color Conversion Matrices
=========================

Introduction
------------
ICC profiles do not cover everything a user may wish to do in terms of color
manipulation, so additional tools are provided as well. Of course, :ref:`pixel math <processing/pixelmath:Pixel Math>`
is a powerful general purpose tool for manipulating pixels but a common use
case is to apply a color correction matrix to data, for example for manual
conversion of camera chromaticities.

.. warning:: Application of CCMs is an advanced technique. Description of
   techniques involving the use of CCMs is outside the scope of Siril
   documentation. You should understand how CCMs operate and how to apply them
   within your workflow in order to use this tool successfully.
   
.. figure:: ../_images/color-management/ccm_dialog.png
   :alt: ccm dialog
   :class: with-shadow
   :width: 80%

   Color Conversion Matrices dialog.

Color Conversion Matrix Tool
----------------------------
The :menuselection:`Tools --> Color Conversion Matrix` tool allows direct
application of a color conversion matrix (CCM) to pixels. The CCM is specified
by 9 elements:

.. math::
   \begin{pmatrix}
      m_{00} & m_{01} & m_{02} \\
      m_{10} & m_{11} & m_{12} \\
      m_{20} & m_{21} & m_{22}
   \end{pmatrix}
   
Several common preset are provided in a drop-down selector. The tool
additionally offers the ability to scale by a :math:`\gamma` factor.

This is applied to pixels as follows:

.. math::
   \begin{align}
     r' &= (m_{00}\cdot r + m_{01}\cdot g + m_{02}\cdot b)^{(-1/\gamma)} \\
     g' &= (m_{10}\cdot r + m_{11}\cdot g + m_{12}\cdot b)^{(-1/\gamma)} \\
     b' &= (m_{20}\cdot r + m_{21}\cdot g + m_{22}\cdot b)^{(-1/\gamma)}
   \end{align}

.. warning:: If a CCM is applied to an image that has an embedded ICC profile,
   the ICC profile will no longer be a valid description of the image data. The
   profile is therefore temporarily disabled and the color management icon will
   show as inactive. It is assumed that your workflow will involve low-level
   colorspace transforms and image operations and at some point you will end up
   transforming the data back to the color space described by the ICC profile.
   At this point the ICC profile can be reactivated using the bottom of the
   dialog. If however your workflow involves manual conversion of the image to
   a different final color space you will need to apply the target ICC profile
   using the :guilabel:`Color Management` dialog.

   Note that this does not apply to the command line :ref:`ccm <ccm>` command. 
   By policy, Siril commands do not interact with ICC profiles therefore the 
   :ref:`ccm <ccm>` command will not disable an ICC profile attached to an image: 
   it is your responsibility to do this using the :ref:`icc_remove <icc_remove>` 
   command if required.
   
This operation can be applied to sequences. Open a sequence and prepare the 
settings you want to use, then check the :guilabel:`Apply to sequence` button 
and define the output prefix of the new sequence (`ccm_` by default).

.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/ccm_use.rst

   .. include:: ../commands/ccm.rst
   
.. admonition:: Siril command line
   :class: sirilcommand

   .. include:: ../commands/seqccm_use.rst

   .. include:: ../commands/seqccm.rst


