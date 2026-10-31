# Template for Siril script with a GUI
# SPDX-License-Identifier: # insert license identifier here
# insert author's name and details here

"""
This script template provides an exemplar for use in constructing sirilpy
scripts that provide a Tkinter GUI but which can also be called using an
argument vector (i.e. so that they can be scripted for use with the Siril
pyscript command). A similar template exists for pure GUI scripts, which
is slightly simpler owing to not having to accommodate command-line
arguments.
"""

# Core module imports
import os
import sys
import math
import argparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np

import sirilpy as s
from sirilpy import tksiril, SirilError
s.ensure_installed("ttkthemes")
# Add other calls to ensure_installed() here to ensure that any non-core
# modules that are required by the script are installed and available
# to be imported.

# Note: pylint will complain that the following imports are not at the
# top of the module. This deviation from python style is required in order
# to run ensure_installed() to ensure that any non-core modules are
# available before attempting to import them.
from ttkthemes import ThemedTk
# Add any additional imports here

VERSION = "1.0.0"

def template_algorithm(fit, example_float_var,
                       example_bool_var,
                       example_file_path_var):
    """
   Template processing function, insert your algorithm here.
   This is the non-GUI part of the script and is entirely up to you to write.
   Although presented in the template as a single method it may comprise
   classes, methods and anything else.
   """
    print(f"Argument: {example_float_var}")
    print(f"Bool var: {example_bool_var}")
    print(f"Bool var: {example_file_path_var}")
    return fit

class TemplateScriptInterface:
    """ This class provides the GUI and related callbacks """
    def __init__(self, root=None, cli_args=None):
        # If no CLI args, create a default namespace with defaults
        if cli_args is None:
            parser = argparse.ArgumentParser()
            parser.add_argument("-example_float_var", type=float, default=1.0)
            parser.add_argument("-example_bool_var", type=bool, default=False)
            parser.add_argument("-example_file_path_var", type=str, default="")
            # add other arguments to match the ones in main()
            cli_args = parser.parse_args([])

        self.cli_args = cli_args
        self.root = root

        if root:
            self.root.title(f"Template Script Interface - v{VERSION}")
            self.root.resizable(False, False)
            self.style = tksiril.standard_style()

        # Initialize Siril connection
        self.siril = s.SirilInterface()

        try:
            self.siril.connect()
        except s.SirilConnectionError:
            if root:
                self.siril.error_messagebox("Failed to connect to Siril")
            else:
                print("Failed to connect to Siril")
            return

        # Initial checks: example - check if an image is loaded
        if not self.siril.is_image_loaded():
            if root:
                self.siril.error_messagebox("No image is loaded")
            else:
                print("No image is loaded")
            return

        # Check if the version of Siril is high enough
        # 1.3.6 is the baseline when python scripting became available but in time
        # you may need to check for a higher version number in order to use newer
        # features.
        try:
            self.siril.cmd("requires", "1.3.6")
        except s.CommandError:
            return

        # Create the UI and match its theme to Siril
        if root:
            self.create_widgets()
            tksiril.match_theme_to_siril(self.root, self.siril)

        # Only apply changes if CLI arguments are non-default
        if cli_args and (cli_args.example_float_var != 1.0):
            self.apply_changes(from_cli=True)

    # This function is used to round values to n decimal places, it is
    # always useful if you have sliders in your UI.
    def _floor_value(self, value, decimals=2):
        """Floor a value to the specified number of decimal places"""
        factor = 10 ** decimals
        return math.floor(value * factor) / factor

    # Here is an example function to round the displayed value of a
    # variable to 2 decimal places. You will need one of these for each
    # slider control you have.

    def _update_example_float_variable_display(self, *args): # pylint: disable=unused-argument
        """Update the displayed template variable value with floor rounding"""
        value = self.example_float_var.get()
        rounded_value = self._floor_value(value)
        self.example_float_var_display_var.set(f"{rounded_value:.2f}")

    def _browse_file(self):
        """
        Use a TK filedialog to browse for a file. Note that this callback is
        specific to the variable being updated; you might need to have several
        similar callbacks if you have more than one file selection widget.
        """
        filename = filedialog.askopenfilename(
            title="Select File",
            initialdir=os.path.expanduser("~")
        )
        if filename:
            self.example_file_path_var.set(filename)

    def create_widgets(self):
        """Create the GUI's widgets, connect signals etc. """
        # Main frame with no padding
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Title
        title_label = ttk.Label(
            main_frame,
            text="Template Script",
            style="Header.TLabel"
        )
        title_label.pack(pady=(0, 20))

        # Parameters frame
        params_frame = ttk.LabelFrame(main_frame, text="Parameters", padding=10)
        params_frame.pack(fill=tk.X, padx=5, pady=5)

        ############################################################
        # Example control with an embedded variable in its own frame
        ############################################################

        template_variable_frame = ttk.Frame(params_frame)
        template_variable_frame.pack(fill=tk.X, pady=5)

        ttk.Label(template_variable_frame,
            text="Example floting point parameter:").pack(side=tk.LEFT)
        # This is the actual float variable
        self.example_float_var = tk.DoubleVar(self.root,
            value=self.cli_args.example_float_var)
        # This is a string representation of the float var limited to 2 decimal places
        # It is used in the scale label
        self.example_float_var_display_var = tk.StringVar(
            self.root,
            value=f"{self._floor_value(self.cli_args.example_float_var):.2f}")
        # Add trace to update display when slider changes
        self.example_float_var.trace_add("write",
            self._update_example_float_variable_display)

        example_float_var_scale = ttk.Scale(
            template_variable_frame,
            from_=0.0, # set your range minimum here
            to=1.0,    # set your range maximum here
            orient=tk.HORIZONTAL, # oriented horizontally
            variable=self.example_float_var, # the tk variable the widget controls
            length=200
        )
        example_float_var_scale.pack(side=tk.LEFT, padx=10, expand=True)
        ttk.Label(
            template_variable_frame,
            textvariable=self.example_float_var_display_var, # var truncated to 2 d.p.
            width=5,
            style="Value.TLabel"
        ).pack(side=tk.LEFT)
        tksiril.create_tooltip(example_float_var_scale,
            "Adjusts the template variable.")

        ###############################
        # Add frame for other variables
        ###############################
        options_frame = ttk.LabelFrame(main_frame, text="Options", padding=10)
        options_frame.pack(fill=tk.X, padx=5, pady=10)

        ##################
        # Example checkbox
        ##################
        self.example_bool_var = tk.BooleanVar(self.root,
            value=self.cli_args.example_bool_var
        )
        example_checkbox = ttk.Checkbutton(
            options_frame,
            text="Example checkbox variable",
            variable=self.example_bool_var,
            style="TCheckbutton"
        )
        example_checkbox.pack(anchor=tk.W, pady=2)
        tksiril.create_tooltip(example_checkbox, "Example checkbox.")

        ##########################################################
        # Example file selection
        # using an entry and a callback that triggers a filedialog
        ##########################################################
        file_frame = ttk.Frame(options_frame)
        file_frame.pack(fill=tk.X, pady=5)

        self.example_file_path_var = tk.StringVar(self.root,
            value=self.cli_args.example_file_path_var)
        example_file_entry = ttk.Entry(
            file_frame,
            textvariable=self.example_file_path_var,
            width=40
        )
        example_file_entry.pack(side=tk.LEFT, padx=(0, 5), expand=True)

        ttk.Button(
            file_frame,
            text="Browse",
            command=self._browse_file,
            style="TButton"
        ).pack(side=tk.LEFT)

        # The template shows examples of sliders and checkboxes
        # however you can add other sorts of TKinter widgets here

        # Buttons frame
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=20)

        close_btn = ttk.Button(
            button_frame,
            text="Close",
            command=self.close_dialog,
            style="TButton"
        )
        close_btn.pack(side=tk.LEFT, padx=5)
        tksiril.create_tooltip(close_btn,
                               "Close the interface and disconnect from Siril. No "
                               "changes will be made to the current image.")

        apply_btn = ttk.Button(
            button_frame,
            text="Apply",
            command=self.apply_changes,
            style="TButton"
        )
        apply_btn.pack(side=tk.LEFT, padx=5)
        tksiril.create_tooltip(apply_btn,
                               "Apply the script function with the set parameters "
                               "to the current image. Changes can be undone using "
                               "Siril's undo function.")

    def apply_changes(self, from_cli=False):
        """
        Get the necessary variables from CLI args or the GUI and call the algorithm
        """
        try:
            # Get the thread
            with self.siril.image_lock():
                # Determine parameters: prefer CLI args if provided,
                # else use GUI values
                if from_cli and self.cli_args:
                    example_float_var = self.cli_args.example_float_var
                    example_bool_var = self.cli_args.example_bool_var
                    example_file_path_var = self.cli_args.example_file_path_var
                else:
                    example_float_var = self.example_float_var.get()
                    example_bool_var = self.example_bool_var.get()
                    example_file_path_var = self.example_file_path_var.get()

                # Get current image
                fit = self.siril.get_image()
                fit.ensure_data_type(np.float32)

                # Save original image for undo
                self.siril.undo_save_state(f"Script algorithm: "
                                           "arg={example_float_var:.2f}")

                # Apply script algorithm
                # Your image processing functions go here
                result = template_algorithm(fit, example_float_var,
                                            example_bool_var,
                                            example_file_path_var)

                # Clip and update image data
                fit.data[:] = np.clip(result, 0, 1)
                self.siril.set_image_pixeldata(fit.data)

            if from_cli:
                print("Script algorithm applied successfully.")

        except SirilError as e:
            if from_cli:
                print(f"Error: {str(e)}")
            else:
                messagebox.showerror("Error", str(e))

    def close_dialog(self):
        """ Close dialog """
        if hasattr(self, 'root'):
            self.root.quit()
            self.root.destroy()

def main():
    """ Main entry point """
    parser = argparse.ArgumentParser(description="Template python script")
    parser.add_argument("-example_float_var", type=float, default=1.0,
                        help="Describe the variable here (0.0 to 1.0)")
    parser.add_argument("-example_bool_var", type=bool, default=False,
                        help="Describe the variable here (bool, default is False)")
    parser.add_argument("-example_file_path_var", type=str, default="",
                        help="Describe the variable here (example file path)")

    args = parser.parse_args()

    try:
        if any([args.example_float_var != 1.0, args.example_bool_var is not False]):
            # CLI mode
            TemplateScriptInterface(cli_args=args)
        else:
            # GUI mode
            root = ThemedTk()
            TemplateScriptInterface(root)
            root.mainloop()
    except SirilError as e:
        print(f"Error initializing script: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
