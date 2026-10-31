#!/usr/bin/env python3
"""Lightweight smoke self-test for siril-astra-stacking.

Verifies standard-library environment, module importability, FITS header probing,
synthetic frame creation, and pipeline script derivation without external dependencies.
Full test suite is in tests/ (run via: python3 -m unittest discover -s tests).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / "scripts", _SKILL / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def run_smoke_checks() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, cond: bool, err: str = ""):
        results.append((name, cond, err))
        status = "ok  " if cond else "FAIL"
        print(f"  {status} {name}" + (f" ({err})" if not cond and err else ""))

    # 1. Core modules importability
    try:
        import astra_stack
        import batch_filters
        import compose_channels
        import device_signatures
        import fits_probe
        import param_derive
        import siril_script_gen
        import synth_fits
        check("core modules importable", True)
    except Exception as exc:  # noqa: BLE001
        check("core modules importable", False, str(exc))
        return results

    # 2. Sequence stem derivation
    try:
        stem = astra_stack.sequence_stem("M42")
        check("sequence stem derivation", stem == "M4s", f"got {stem}")
    except Exception as exc:  # noqa: BLE001
        check("sequence stem derivation", False, str(exc))

    # 3. Synthetic FITS generation & probing
    with tempfile.TemporaryDirectory(prefix="astra-smoke-") as tmp:
        try:
            written = synth_fits.write_cfa_sequence(tmp, 64, 48, 2)
            header, _warnings = fits_probe.read_fits_header_with_warnings(written[0][0])
            check("synthetic FITS header readable", header.get("NAXIS") == 2 and header.get("BITPIX") == 16)
            check("bayer pattern parsed", header.get("BAYERPAT") == "GRBG")
        except Exception as exc:  # noqa: BLE001
            check("synthetic FITS generation & probing", False, str(exc))
            return results

        # 4. Device identification & calibration plan
        try:
            device = device_signatures.identify_device(header, None)
            check("device identification", device is not None)
            plan = device_signatures.resolve_calibration_plan(header, device, None)
            check("calibration plan derivation", "dark" in plan and "flat" in plan)
        except Exception as exc:  # noqa: BLE001
            check("device identification & calibration plan", False, str(exc))

        # 5. Pipeline script builder smoke check
        try:
            builder = siril_script_gen.ScriptBuilder()
            builder.add("requires", "1.4.0")
            script = builder.render()
            check("script builder generates commands", "requires 1.4.0" in script)
        except Exception as exc:  # noqa: BLE001
            check("script builder", False, str(exc))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="run full test suite with real tests enabled")
    args = parser.parse_args()

    print("== astra-stacking smoke self-test ==")
    results = run_smoke_checks()
    failures = [name for name, cond, _ in results if not cond]

    if failures:
        print(f"\n{len(results) - len(failures)} passed, {len(failures)} failed")
        return 1

    print(f"\n{len(results)} passed, 0 failed")
    if args.real:
        print("\nRunning full unit test suite (with --real)...")
        import unittest
        sys.argv = [sys.argv[0], "--real"]
        suite = unittest.defaultTestLoader.discover(str(_SKILL / "tests"))
        runner = unittest.TextTestRunner()
        outcome = runner.run(suite)
        return 0 if outcome.wasSuccessful() else 1

    print("\nSmoke checks passed. For the full suite run:")
    print("  python3 -m unittest discover -s siril-astra-stacking/tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
