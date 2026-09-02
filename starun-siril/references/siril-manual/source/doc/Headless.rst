Headless mode
#############

Siril can both operate with its graphical user interface (GUI) and with a
command line interface (CLI) that does not even require having a display. It
can process images for other programs, on remote or embedded computers, using
either scripts or real-time generated operations called commands. The
capabilities of the headless Siril are in fact those of the :ref:`available
commands <Commands:Commands>`. There are more than a hundred, allowing
preprocessing, processing and photometry analysis to be done automatically.

Commands can also be used in the GUI version of Siril, either from the embedded
command line at the bottom of the control panel, or with :ref:`scripts
<Scripts:Scripting>`. Scripts are simply a text file containing a list of
commands. Reading the :ref:`scripts page <Scripts:Scripting>` is recommended
before going further.

With the headless version, commands can be executed either by passing a script
to run, or by setting the standard input as a script and writing commands to
it, with ``-s -`` command line option, or using some named pipes.

Here's an example of bash code calling headless mode, which builds the 
master-bias and returns its noise to the console in red:

.. code-block:: bash

   #!/bin/bash
   # bash commands to prepare files
   
   initdir=$(pwd)
   
   ######## Set your own variables #############
   SCRIPTS_DIRECTORY=$initdir
   SIRIL_PATH=siril-cli
   #############################################
   
   # Removing process folder if exists #
   rm -rf $SCRIPTS_DIRECTORY/process
   
   echo "Running siril bash script in $initdir"
   output=$($SIRIL_PATH -d $SCRIPTS_DIRECTORY -s - <<ENDSIRIL
   
   ############################################
   #
   # Example of script that create a master-bias
   # and print in red the noise of the image
   #
   ############################################
   
   requires 1.0.0
   
   # Convert Bias Frames to .fit files
   cd biases
   convert bias -out=../process
   cd ../process
   
   # Stack Bias Frames to bias_stacked.fit
   stack bias rej 3 3 -nonorm -out=master-bias
   cd ../..
   
   close
   ENDSIRIL
   )
   
   log_line=$(echo "$output" | grep -o "log: Background noise value.*")
   echo -e "\e[31m$log_line\e[0m"
   
   echo done Siril part

Command Stream (Pipe)
=====================
This mode has been introduced with Siril 0.9.10. Commands can be sent through a
named pipe and logs and status can be obtained through another. The mode is
activated using the ``-p`` command line argument.

The protocol is quite simple: Siril receives commands and outputs some updates.
Only commands that are marked as scriptable can be used with this system. 
Whenever the command inputs pipe is closed or the cancel command is given, the 
running command is stopped as if the stop button was clicked on in the GUI. The
pipes are named ``siril_command.in`` and ``siril_command.out`` and are 
available in ``/tmp`` on Unix-based systems. Since version 1.2.0, the paths of 
the pipes can be configured with ``-r`` and ``-w`` options, which allows 
external programs to create them before starting Siril, typically with the 
``mkfifo`` command. Also new in 1.2.0, a ``ping`` command will simply give a
status return, indicating if siril is ready to process a command or already
busy.

Outputs of siril on the pipe is a stream of one line text and formatted as 
follows:

* ``ready`` is printed on startup, indicating siril is ready to process 
  commands
* ``log:`` followed by a log message
* ``status: verb [subject]``, where verb can be either of ``starting``, 
  ``success``, ``error`` or ``exit`` (exit message is not yet implemented). 
  The subject is the current command name, except for exit that indicates that 
  siril suffered a fatal error and has to exit.
* ``progress: value%`` is the equivalent of the progress bar, it sends percents 
  periodically, and sometimes 0% at the end of a processing as an idle 
  information.
