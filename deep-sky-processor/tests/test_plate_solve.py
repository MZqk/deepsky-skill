import tempfile
import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from plate_solve import project_catalog, solve_image, summarize_wcs


class PlateSolveTests(unittest.TestCase):
    def test_missing_solver_is_explicitly_unavailable(self):
        with tempfile.TemporaryDirectory() as td:
            payload = solve_image(
                "missing.fits",
                td,
                solve_field_path=str(Path(td) / "not-installed"),
            )
        self.assertEqual(payload["status"], "unavailable")
        self.assertEqual(payload["error"]["code"], "SOLVE_FIELD_NOT_FOUND")

    def test_wcs_summary_and_catalog_projection(self):
        from astropy.io import fits
        from astropy.wcs import WCS

        with tempfile.TemporaryDirectory() as td:
            wcs_path = Path(td) / "field.wcs"
            wcs = WCS(naxis=2)
            wcs.wcs.crpix = [50.5, 40.5]
            wcs.wcs.cdelt = np.array([-1.0 / 3600.0, 1.0 / 3600.0])
            wcs.wcs.crval = [303.0, 38.0]
            wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
            fits.PrimaryHDU(header=wcs.to_header()).writeto(wcs_path)

            summary = summarize_wcs(wcs_path, (80, 100))
            self.assertAlmostEqual(summary["center_ra_deg"], 303.0, places=4)
            self.assertAlmostEqual(summary["center_dec_deg"], 38.0, places=4)
            self.assertAlmostEqual(summary["pixel_scale_arcsec"], 1.0, places=3)

            objects = project_catalog(
                wcs_path,
                (80, 100),
                [
                    {
                        "name": "center-object",
                        "type": "emission_nebula",
                        "ra_deg": 303.0,
                        "dec_deg": 38.0,
                    },
                    {
                        "name": "outside",
                        "ra_deg": 310.0,
                        "dec_deg": 45.0,
                    },
                ],
            )
            self.assertEqual(len(objects), 1)
            self.assertEqual(objects[0]["name"], "center-object")
            self.assertAlmostEqual(objects[0]["normalized_center"][0], 0.5, places=2)
            self.assertAlmostEqual(objects[0]["normalized_center"][1], 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
