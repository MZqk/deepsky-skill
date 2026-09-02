siril.plot submodule
####################

This script shows how to use the siril.plot submodule to create
a simple plot with three series. The first is a simple x-y plot
with lines; the second is an x-y plot with error bars; and the
third is another simple x-y plot but shows use of numpy arrays
as well as how a SeriesData object can be created and added
directly to the PlotData object using ``add_series_obj``.

.. code-block:: python

   import sirilpy as s
   import numpy as np
   siril = s.SirilInterface()
   try:
      siril.connect()
   except SirilConnectionError as e:
      print(f"Error: failed to connect to Siril: {e}")
      quit()

   # Series data using lists of floats
   plot_data = s.PlotData(
      "Example Plot",
      "X Axis",
      "Y Axis",
      "example_plot_lists",
      True,
      datamin = [0.0, 0.0]
      )

   plot_data.add_series([1.0, 2.0, 3.0],
                        [4.0, 5.0, 6.0],
                        "Series 1 (Lists)")

   plot_data.add_series([4.0, 5.0, 6.0],
                        [7.0, 8.0, 9.0],
                        "Series 2 (Lists with errors)",
                        s.PlotType.MARKS,
                        [0.35, 0.4, 0.45],
                        [0.35, 0.4, 0.45])

   x_arr = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
   y_arr = np.array([1.0, 4.0, 9.0, 16.0], dtype=np.float32)
   series3 = s.SeriesData(x_arr, y_arr, "Series 3 (np.arrays)")
   plot_data.add_series_obj(series3)

   # Serialize using lists
   siril.xy_plot(plot_data)

