Hello, Siril!
#############
It is traditional to start with a Hello, World! example, so here is
"Hello, Siril!"

.. code-block:: python

   import sirilpy as s
   from sirilpy import SirilConnectionError

   siril = s.SirilInterface()

   try:
      siril.connect()
      siril.log("Hello, Siril!")

   except SirilConnectionError as e:
      print(f"An error occurred: {e}")

That's it. All siril python scripts should import the siril module: this
is automatically provided by Siril and does not require installation.

It allows connection to Siril through the **SirilInterface** class and
gives access to Siril methods: in this case the ``log()`` function does a
tiny bit of adjustment (adding a ``\n`` newline character) and calls the
internal function ``siril_log_message()`` to display the result in Siril's
log tab. Note that ``log()`` can take an optional argument specifying the
color: if you wanted "Hello, Siril!" to print in green you would change the
line to ``siril.log("Hello, Siril!", s.LogColor.GREEN)``.

Note that there is generally no need to call ``siril.disconnect()`` call,
as it is not really necessary: at the end of a script the method will be
called automatically in an onexit handler.

Also note that although you can initialize more than one ``SirilInterface()``
object, a script can only connect via one at a time (though multiple scripts
can be connected to Siril simultaneously). There is generally no need to
create more than one ``SirilInterface()``.
