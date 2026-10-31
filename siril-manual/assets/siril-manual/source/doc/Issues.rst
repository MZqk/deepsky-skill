How to report issues
####################

If, despite studying the documentation and tutorials, you're experiencing 
strange behavior, this page will guide you on how to proceed. Keep in mind that 
if something doesn't work, it doesn't necessarily mean there's a bug. More often 
than not, issues arise due to incorrect usage. In such cases, it's crucial to 
provide precise information about the problem, which will help us determine if 
it's a bug or a misunderstanding. Refer to the section on how to :ref:`send us useful 
information <Issues:Send us useful information>` for guidance. Indeed, just 
saying it doesn't work won't help us find a solution. We're not soothsayers and 
need information to identify the problem and devise a solution. So it's 
important that you tell us what you were doing at the time the problem occurred, 
what operating system you're using, what version of Siril you're using and most 
importantly logs!

Check changelogs and bug trackers
=================================

First of all, if you think you found a bug in Siril, it may already have been 
reported (sometimes literally dozens of times). We therefore ask you to check 
first, for example by looking at `changelogs <https://gitlab.com/free-astro/siril/-/blob/master/ChangeLog?ref_type=heads>`_, 
or `tickets <https://gitlab.com/free-astro/siril/-/issues>`_ that have already 
been **opened**, and even **closed**.

Send us useful information
==========================

As mentioned in the introduction, we need useful information to help solve the 
problem:

#. What operating system you're using? Because Siril can behave very differently 
   on Windows, Linux or macOS, we need to know this information. Please be as 
   precise as possible.

#. Which version of Siril do you use? And how did you get it? Package downlaoded 
   on the website? Through a third party? Compiled by yourself? Here again, 
   please, be as precise as possible.
   
#. Sometimes it's useful to share screenshots. However, please **DO NOT TAKE 
   YOUR SCREENSHOTS WITH YOUR SMARTPHONE** - it's illegible. Your operating 
   system is capable of making screenshots very easily (`Google <https://www.google.com>`_ 
   can help you with this) and Siril also offers such a feature (the button with 
   the camera). Finally, the desired formats are image formats: ``jpg``, ``bmp``
   or ``png``, but absolutely not ``pdf``.
   
#. Send us logs. Ideally, we prefer English logs! Just go to Siril preferences 
   and change the language to English in the :ref:`User Interface 
   <preferences/preferences_gui:User Interface>` tab. Also, There are two 
   types of logs: the one displayed in the Siril console, which describes the 
   steps performed by the software and can help us 
   debug, and the internal logs that are visible when Siril is run from the 
   command line:
   
   - The former are very useful in most cases, and can be exported 
     very easily using the button at the bottom right of them. This creates a 
     file that can easily be sent to us.
   - However, when the software crashes (i.e. suddenly closes without warning), 
     you need to start Siril from the command line, trying to reproduce the 
     crash and retrieve the logs. Here, the method depends on the operating 
     system.
     
     - Microsoft Windows: Open a cmd window (type ``cmd`` in Windows Search bar) 
       and type the following:
       
       .. code-block:: bat
       
          "C:\Program Files\Siril\bin\siril.exe" 2>&1 >output.log
          
       This will save the file output.log to the folder where the terminal was 
       started (in most cases in ``%USERPROFILE%`` folder).
          
     - macOS: If you've installed Siril in the Applications folder, as is 
       generally recommended, then start by opening the Terminal application 
       from the Utilities folder within Applications, then copy and paste the 
       following line:
       
       .. code-block:: console
       
          /Applications/Siril.app/Contents/MacOS/siril > ~/Desktop/output.log 2>&1
          
       After the crash, the logs will be available on the desktop in the file
       ``output.log``.
       
     - GNU/Linux: Simply start Siril in a terminal. Usually, the binary is 
       located in the ``$PATH`` variable, in which case type:
       
       .. code-block:: console
       
          siril > output.log 2>&1
          
       is all you need to get the logs redirected in the file ``output.log``. 
       This will save the file output.log to the folder where the terminal was 
       started

#. Send us your pic. If you find your image strange, don't hesitate to share it 
   with us, usually in FITS format. It's always more interesting than a 
   screenshot. To do this, you need to use a large file exchange service. There 
   are lots of them, and we can suggest `WeTransfer <https://wetransfer.com/>`_, 
   for example. In that case, upload your data to WeTransfer and get a 
   download link to share.

How to contact us?
==================

There are several ways to contact us and report a bug. The easiest is to find us 
on the `forum <https://discuss.pixls.us/siril>`_. But it is also possible to
open a ticket on our `gitlab repository <https://gitlab.com/free-astro/siril/-/issues/new>`_.
In this case, please check first that the same ticket has not already been 
opened. It may even have been closed because it has been resolved, in which case, 
a short description with a ticket number will be shown in the 
`Changelog <https://gitlab.com/free-astro/siril/-/blob/master/ChangeLog>`_. To visualize 
the ticket (even a closed one) and confirm you are, or not, experiencing the same issue,
go to the address https://gitlab.com/free-astro/siril/-/issues/XXXX
with XXXX the ticket number.

.. tip::
   As Siril users and developers are of different nationalities, the language 
   used to report a bug must be English.
