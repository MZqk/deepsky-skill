"""Siril 1.4 ``.sir`` script generation with static validation.

Generating the script as text -- rather than piping commands at a live Siril -- is
what makes the parameter choices auditable: the exact command line handed to Siril
can be asserted in tests without executing anything, and the ``.sir`` is small
enough to read when debugging a real run.

Format rules that matter, all confirmed against Siril 1.4.4:

* One command per line, arguments separated by spaces, ``#`` for comments.
* The very first command should be ``requires <min>`` so Siril refuses to run a
  script that needs a newer version.
* Quoting: when an argument contains spaces, **the entire ``key=value`` token must
  be quoted**, e.g. ``seqapplyreg seq -prefix="r_seq"`` is wrong but
  ``seqapplyreg seq "-prefix=r seq"`` is right. Quoting only the value leaves the
  parser splitting on the space inside the token.
* Siril rewrites its working directory to a scratch directory (``/tmp/s1d`` on
  Linux/macOS) unless ``-d`` is given on the command line. Sequences are therefore
  named relative to that working directory; a sequence name of ``.`` would require
  ``load_seq``, which is not scriptable in 1.4.
* Any command erroring aborts the whole script with a non-zero exit, so the
  generated order must respect data dependencies.

Validation performed here, all of it catching cases where Siril 1.4 degrades
silently rather than erroring:

* ``-maximize`` together with median stacking (``Cannot upscale or maximize
  framing with median stacking. Disabling``).
* ``-framing=max`` outside the drizzle chain (``-framing=max`` is implemented on
  top of drizzle weights; without them Siril reports ``Drizzle stacking cannot be
  performed because drizzle weights are missing.``).
* Any command or flag that only exists in Siril 1.5.

Usage:
    from siril_script_gen import ScriptBuilder
    builder = ScriptBuilder(header_comment=["target: M42"])
    builder.add("calibrate", ["sub"])
    print(builder.render())
"""

from __future__ import annotations

import os

#: Commands that do not exist in Siril 1.4. Rejected at generation time so they
#: fail loudly here instead of mid-pipeline.
FORBIDDEN_COMMANDS = {
    "mpp", "register_mpp", "stack_mpp", "starnet", "seqstarnet",
    "seqwcsbg", "seqwcs", "unload", "pjp", "seqsetreg",
    "stack_rl", "stack_rbf", "atrous", "seqatrous", "rgbalign",
    "ssr", "detect_streaks", "eqcrop", "seqeqcrop", "gps", "seqgps",
    "catmag", "healpix", "clear_mask",
}

#: Flags introduced after 1.4.
FORBIDDEN_FLAGS = {
    "-extref=",
    "-debayer=",
    "-avi-bayer=",
    "-engine=",
    "-ap-step=",
    "-shift-smooth=",
    "-async",
}

#: Commands that are not scriptable in 1.4 (scriptable=0 in the source).
NON_SCRIPTABLE = {
    "setmag", "seqsetmag", "sequnsetmag", "unsetmag",
    "load_seq", "ls", "dir", "clear", "show", "visu", "tilt",
}

#: Stacking methods that silently refuse ``-maximize``. These are the tokens
#: Siril 1.4 accepts for the *stacking method* slot (args[1]): ``med`` and
#: ``median`` are the same method. Do not add ``m`` here -- ``m`` is the median
#: *rejection type*, which sits one slot later (args[2]) and is perfectly legal
#: alongside ``-maximize``; adding it would reject valid scripts.
MAXIMIZE_INCOMPATIBLE_METHODS = {"med", "median"}

#: Sequence flags that are only meaningful after ``register`` has written
#: registration data. ``seqstat`` cannot produce them.
FILTER_FLAGS = {
    "-filter-fwhm=", "-filter-wfwhm=", "-filter-round=",
    "-filter-bkg=", "-filter-nbstars=", "-filter-quality=", "-filter-incl",
}


class ScriptValidationError(ValueError):
    """Raised when a generated command would fail or silently degrade."""


def quote_argument(arg: str) -> str:
    """Quote a single argument when it contains a space.

    The whole ``key=value`` token must be wrapped, which is why callers pass the
    fully formed token rather than a bare value.
    """
    text = str(arg)
    if " " in text and not (text.startswith('"') and text.endswith('"')):
        return '"%s"' % text
    return text


class ScriptBuilder:
    """Build scripts; cached registration must be validated by the caller."""

    def __init__(self, min_version="1.4.0", header_comment=None,
                 registration_available=False):
        self.min_version = min_version
        self.header_comment = header_comment or []
        self.commands = []
        self.stack_methods = []
        self.framing = None
        self.used_filters = False
        self.registration_available = registration_available

    def add(self, command, args=None, comment=None):
        """Append a command.

        Args:
            command: Command name, e.g. ``"seqapplyreg"``.
            args: List of argument tokens.
            comment: Optional comment emitted above the command.

        Raises:
            ScriptValidationError: for forbidden commands or flags, or for
                filter flags used without computed registration data.
        """
        args = [str(a) for a in (args or [])]

        if command in FORBIDDEN_COMMANDS:
            raise ScriptValidationError(
                "command '%s' does not exist in Siril 1.4; it is either 1.5-only "
                "or a PixInsight name that was never part of Siril" % command
            )
        if command in NON_SCRIPTABLE:
            raise ScriptValidationError(
                "command '%s' is not scriptable in Siril 1.4 (scriptable=0); it "
                "cannot be used inside a .sir script" % command
            )
        for arg in args:
            for forbidden in FORBIDDEN_FLAGS:
                if arg == forbidden or arg.startswith(forbidden):
                    raise ScriptValidationError(
                        "flag '%s' does not exist in Siril 1.4" % forbidden
                    )
            if arg.startswith("-") and any(arg.startswith(f) for f in FILTER_FLAGS):
                if not self.registration_available:
                    raise ScriptValidationError(
                        "%s requires computed registration data; register first "
                        "or declare validated cached registration" % arg
                    )
                self.used_filters = True

        if command == "calibrate":
            self.registration_available = False
        if command == "register":
            self.registration_available = True
        if command == "seqapplyreg":
            for arg in args:
                if arg.startswith("-framing="):
                    self.framing = arg.split("=", 1)[1]
        if command == "stack":
            # Syntax is: stack <seqname> <method> [rejection] [sigmas] [flags],
            # so the method sits at args[1], not args[0].
            method = args[1] if len(args) > 1 else None
            if method is not None:
                self.stack_methods.append(method)
                if method in MAXIMIZE_INCOMPATIBLE_METHODS and "-maximize" in args:
                    raise ScriptValidationError(
                        "median stacking combined with -maximize: Siril 1.4 only "
                        "logs 'Cannot upscale or maximize framing with median "
                        "stacking. Disabling' and proceeds without the framing, "
                        "so the mosaic would be silently lost"
                    )
            if "-framing=max" in args:
                raise ScriptValidationError(
                    "'-framing=max' is a seqapplyreg option, not a stack option"
                )

        self.commands.append((comment, command, args))
        return self

    def render(self):
        """Render the validated script text.

        Raises:
            ScriptValidationError: for whole-script invariants that can only be
                checked once every command is known.
        """
        maximize_used = any(
            "-maximize" in args for _c, command, args in self.commands
            if command == "stack"
        )
        if maximize_used and self.framing != "max":
            raise ScriptValidationError(
                "stack -maximize requires seqapplyreg -framing=max; the two are a "
                "pair (framing=max is realised by stack -maximize)"
            )
        if self.framing == "max":
            has_drizzle = any(
                "-drizzle" in args
                for _c, command, args in self.commands
                if command == "seqapplyreg"
            )
            if not has_drizzle:
                raise ScriptValidationError(
                    "seqapplyreg -framing=max without -drizzle: framing=max is "
                    "computed from drizzle weights, and Siril reports 'Drizzle "
                    "stacking cannot be performed because drizzle weights are "
                    "missing.' Use the drizzle chain or fall back to "
                    "-framing=current"
                )
        if self.used_filters and not self.registration_available:
            raise ScriptValidationError(
                "frame-selection filters require prior registration data"
            )

        lines = ["# generated by siril-astra-stacking"]
        for comment in self.header_comment:
            lines.append("# %s" % comment)
        lines.append("requires %s" % self.min_version)
        for comment, command, args in self.commands:
            if comment:
                lines.append("# %s" % comment)
            rendered = " ".join([command] + [quote_argument(a) for a in args])
            lines.append(rendered)
        lines.append("exit")
        return "\n".join(lines) + "\n"

    def write(self, path):
        """Render and write the script, returning the text that was written."""
        text = self.render()
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="ascii") as handle:
            handle.write(text)
        return text