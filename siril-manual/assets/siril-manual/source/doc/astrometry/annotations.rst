###########
Annotations
###########

Annotations are glyphs displayed on top of images to depict the presence
of known sky objects, like galaxies, bright stars and so on. They come
from catalogues but can only be displayed on images for which we know
which part of the sky they represent, images that have been **plate
solved** and contain the world coordinate system (WCS) information in
their header, so only :ref:`FITS <file-formats/FITS:FITS>` or 
:ref:`Astro-TIFF <file-formats/Astro-TIFF:Astro-TIFF>` files.

.. figure:: ../_images/annotations/example.png
   :alt: example
   :class: with-shadow
   :width: 100%

   View of full annotated image

Plate solving, can be done within Siril in the menu
:menuselection:`Tools --> Astrometry --> Image Plate Solver...` entry, or using 
external tools like
`astrometry.net <https://nova.astrometry.net/>`__ or
`ASTAP <https://www.hnsky.org/astap.htm>`__.

.. figure:: ../_images/annotations/plate_solved_gui.png
   :alt: plate solved GUI
   :class: with-shadow
   :width: 100%

   Buttons for annotations

When a plate solved image is loaded in Siril, you can see the sky
coordinates for the pixel under the mouse pointer displayed at the
bottom right corner and the buttons related to annotations become
available. The first button toggles on or off object annotations,
the second the celestial grid and the compass.

Offline annotation catalogues
*****************************

Siril comes with a predefined list of catalogues for annotations: 

* Messier catalogue (M) 
* New General Catalogue (NGC) 
* Index Catalogue (IC) 
* Lynds Catalogue of Dark Nebulae (LdN) 
* Sharpless Catalogue (Sh2)
* Star Catalogue (3661 of the brightest stars)
* IAU constellations lines
* IAU constellations names (positions collected from `this page <https://calgary.rasc.ca/constellation.htm>`_)

In addition, 2 *user defined catalogues* can be used: 

* User DeepSky Objects Catalogue  (DSO)
* User Solar System Objects Catalogue (SSO)

They are populated with the commands described in the section about
:ref:`searching for a known object <astrometry/annotations:search for a known object>`.

They are stored in the user settings directory. Their location depends on the 
operating system: 

* for *Unix-based* OS they will be in ``~/.config/siril/catalogue`` 
* on *Windows* they are in ``%LOCALAPPDATA%\siril\catalogue``.

All of the above catalogues can be enabled/disabled for display in the 
:menuselection:`Preferences menu --> Astrometry` :ref:`tab <preferences/preferences_gui:astrometry>`.

The two *user defined catalogues* can also be purged (`i.e.` deleted) via
the appropriate buttons.

A slider on the right side, allows you to easily navigate across the
catalogue list.

.. figure:: ../_images/annotations/cat-manage_view.png
   :alt: Catalogue management
   :class: with-shadow
   :width: 100%

   Catalogue management in Preferences/Astrometry
   
There's also a simpler way of accessing this list, as well as the button for 
displaying/hiding catalog annotations. Right-click on the annotations button in 
the toolbar and the list will be displayed in a popup window.  
   
.. figure:: ../_images/annotations/catalogues-right-click.png
   :alt: Catalogue management in the popup window
   :class: with-shadow

   Catalogue management in the pop-up window triggered by right-clicking on the 
   annotation button.

These annotation catalogues are used primarily for display purposes. Starting 
from Siril 1.3, they are also used to locate the center of the image for
astrometry tool. If the object is found locally, the resolver will
be shown as Local. If not, it will fall back using an online resolver.

.. figure:: ../_images/annotations/local_resolver.png
   :alt: Local resolver
   :class: with-shadow

   Object resolved from local annotations catalogues

Online annotation catalogues
****************************

You may want to query other databases than the ones already shipped with Siril, 
described in the 
:ref:`offline annotation catalogues <astrometry/annotations:offline annotation catalogues>`
section. This works, again, for plated-solved images only.

Starting from Siril 1.3, this is possible with the command :ref:`conesearch <conesearch>`.
This new command replaces and expands capabilities previously provided by ``nomad`` 
and ``solsys`` from Siril 1.2. 

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/conesearch_use.rst

   .. include:: ../commands/conesearch.rst

This command is accessible from the graphical interface via the :menuselection:`Tools --> Astrometry --> Annotate...`.

.. figure:: ../_images/annotations/conesearch_ui.png   
   :alt: Conesearch UI
   :class: with-shadow

   Graphical version of the :ref:`conesearch <conesearch>` command 

The table below lists all the catalogues that are available, alongside 
links to the original data.

.. csv-table:: Table of available catalogues
   :file: cats.txt
   :delim: 0x09
   :widths: 30 20 50
   :header-rows: 1

The following queries are brought to you thanks to:

- Vizier (through CDS `Vizier TAP query service <https://tapvizier.cds.unistra.fr/adql/>`_)
- Simbad (through CDS `Simbad TAP query service <https://simbad.cds.unistra.fr/simbad/sim-tap/>`_)
- IMCCE (through its `skybot conesearch service <https://vo.imcce.fr/webservices/skybot/?conesearch>`_)
- NASA exoplanet archive (though its `TAP query service <https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html>`_)

For solar system object queries, you may pass an additional parameter **-obscode=**,
the 3-symbol code for an `IAU observatory <https://vo.imcce.fr/webservices/data/displayIAUObsCodes.php>`_ 
close by to your observing location. This will improve annotations accuracy. Please note
that results may still slightly differ from those obtained by making a :ref:`direct 
ephemerides query for a specific object <astrometry/annotations:solar-system objects>`,
which uses the exact observing location (if present in the FITS header).

.. tip:: A preferred observatory code may be set in the :guilabel:`Astrometry` tab
   in the :guilabel:`Preferences` dialog. If set, this will be used for the
   :guilabel:`Solar System Objects` GUI tool and will also be used in the
   :ref:`conesearch <conesearch>` command unless the **-obscode=** argument is provided.

These additional annotations will be displayed in **RED**, to differentiate them
from offline annotations, shown in **GREEN**. These annotations will be erased as 
soon as the ``Show Objects names`` button is toggled.

Extra catalogues
****************

You may want to display your own user catalogues. This can be done with the
command :ref:`show <show>`. This command can also be used to display, for instance, 
csv files created with the feature to find 
:ref:`comparison stars <photometry/lightcurves:Generating a list of comparison stars>`.

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/show_use.rst

   .. include:: ../commands/show.rst

These catalogues may be any *csv* (comma-separated) file, respecting the following rules:

- comments lines if any, should start with a `#` sign
- a line should be present at the top with the column names, comma separated
- at least `ra` and `dec` columns should be provided, in decimal degrees.
- the columns can be written in no particular order
- other columns can be passed:

   + `name` (str)
   + `diameter` (double), the object diameter in arcmin
   + `mag` (double), the object magnitude
   + `type` (str), which will be appended between `()` after the name in the Console

Other columns than those listed above may be passed but they will not be used.

This command is accessible from the graphical interface via the :menuselection:`Tools --> Astrometry --> Annotate...`.
You can either load a :file:`*.csv` file containing several lines of 
comma-separated ra, dec coordinates in :guilabel:`Import CSV`, or a single point 
in the :guilabel:`Single Coordinates` tab.

.. figure:: ../_images/annotations/show_ui.png   
   :alt: Show UI
   :class: with-shadow

   Graphical version of the :ref:`show <show>` command
   
In the case of a single point, you can save the result in the *Deep Sky user 
catalogue* by clicking on the save button as shown in the illustration bellow:

.. figure:: ../_images/annotations/show2_ui.png   
   :alt: Show UI single point
   :class: with-shadow

   It is possible to show only one single point and to save it in the *Deep Sky
   user catalogue*.
   
.. tip::
   The save button will only work if the field :guilabel:`Display Name` is filled.

.. tip::   
   You can select a star or object and obtain its coordinates using the 
   :guilabel:`Get coordinates from selection` button.

**List of known user catalogues:**

Sometimes, users create their own catalogues, we can try to link them
here to help everybody.  

* Variable stars, extracted from `GCVS <http://www.sai.msu.su/gcvs/gcvs/intr.htm>`__ 5.1, discussed `here
  in French <https://www.webastro.net/forums/topic/196778-annotation-sous-siril-avec-catalogue-utilisateur/#comment-2941770>`__,
  (:download:`file link <GCVS_siril_annotations.csv>`).

.. warning::
   Contrarily to the instructions discussed in the linked topic, it is not recommended
   to replace the user-DSO catalogue with such files. The usage is discouraged as
   some of them could be particularly big and would slow down tremendously every
   annotation redraw.

Search for a known object
*************************

If you know a specific object is somewhere in the image (if not, see the 
:ref:`search for an unknown object <astrometry/annotations:search for an unknown object>`
section), it is possible to add it to annotations.

Deep-sky objects
----------------

Load a plate resolved image and type :kbd:`Ctrl+Shift+/` or :menuselection:`Tools --> Astrometry --> Annotate...` 
then go to the :guilabel:`Search Object` tab of the dialog box.

A search dialog box is available, as illustrated below, in which object names 
can be entered.

.. figure:: ../_images/annotations/search_object_ui.png   
   :alt: Search Object UI
   :class: with-shadow

   Search Object dialog 

Pressing :kbd:`Enter` or :guilabel:`Apply` will first search for this name the 
existing annotation catalogues in case it already exists under another name. If 
not it will send an online request to `SIMBAD <https://simbad.cds.unistra.fr/simbad/sim-fid>`_ 
to get the coordinates of an object with such a name. If found, and not already 
in any catalogue, the object will be added to the *Deep Sky user catalogue*.

The items of this catalogue are displayed in **ORANGE** while the objects
from the predefined catalogues are displayed in **GREEN**.

.. figure:: ../_images/annotations/user_dso.png
   :alt: User DSO
   :class: with-shadow
   :width: 100%

   Deep sky objects from user and predefined catalogues

Examples of valid input (not case sensitive): 

* ``HD 86574`` or ``HD86574`` are both valid for this star 


Solar-system objects
--------------------

From Siril version 1.2, objects from the solar system can also be searched for,
using the `Miriade
ephemcc <https://ssp.imcce.fr/webservices/miriade/api/ephemcc/>`_
service. This is done in the same manner as for Deep Sky Objects, but
prefixing the name of the object to be searched
by some keyword representing the type of object: ``a:`` for asteroids,
``c:`` for comets, ``p:`` for planets, ``dp:`` for dwarf planets and 
``s:`` for natural satellites.
If you query an image taken from a close enough date and time (same night) than
another image already annotated with SSOs, their cached positions
will be used and corrected by each object velocity as returned by the ephemerids.
The items of this catalogue are displayed in **YELLOW**.

Examples of valid inputs (not case sensitive): 

* ``c:67p`` or ``c:C/2017 T2`` are valid forms for comets 
* ``a:1`` and ``a:ceres`` are both valid for *(1) Ceres* 
* ``a:2000 BY4`` is valid for *103516 2000 BY4* 
* ``p:4`` or ``p:mars`` are both valid for Mars
* ``dp:Pluto`` is valid for Pluto
* ``s:Moon`` or ``s:Io`` is valid for natural satellites.

.. warning::
   Images that do not have a ``DATE-OBS`` header key cannot be annotated for SSOs.
   Images that do not have observer location information (SITELAT, SITELONG and
   SITEELEV header keys) will still be annotated, but assuming a geocentric 
   observer position, i.e. as if observing from center of the Earth. Depending
   on objects distance wrt. Earth, this may result in positions being slightly 
   offset from their real positions.
   
.. figure:: ../_images/annotations/solsys.png
   :alt: Solar Sytem Result
   :class: with-shadow
   :width: 100%

   Result of a Search Solar System process

Command `catsearch`
-------------------
The same feature is accessible through the command :ref:`catsearch <catsearch>`:

.. admonition:: Siril command line
   :class: sirilcommand
   
   .. include:: ../commands/catsearch_use.rst

   .. include:: ../commands/catsearch.rst


Search for an unknown object
****************************

Especially useful for photometry works, it is possible to identify a
star or other objects in the image by drawing a selection around them,
right clicking to bring up the context menu, and selecting the :guilabel:`PSF`
entry.

This will open the PSF window, and if it is a star it will display
the Gaussian fit parameters, but it will also display a Web link at the
bottom left of the window. Opening it will bring you to the `SIMBAD
page <https://simbad.cds.unistra.fr/simbad/sim-fcoo>`_ for the
coordinates of the object and in many cases will give you the name of
the object.

SIMBAD does not have all known objects, but the coordinates
from the page can still be used as a starting point to look for the
object in other online catalogues, for example `Gaia DR3
(VizieR) <https://vizier.cds.unistra.fr/viz-bin/VizieR-3?-source=I/355/gaiadr3>`_.




