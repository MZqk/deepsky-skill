import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fits_io import (
    build_physical_priors,
    classify_filter,
    extract_capture_metadata,
    read_image,
)
from pipeline import run_pipeline


class PhysicalMetadataTests(unittest.TestCase):
    def test_header_is_normalized(self):
        metadata = extract_capture_metadata({
            "EXPTIME": "300",
            "GAIN": 400,
            "CCD-TEMP": 15,
            "FILTER": "Optolong L-eXtreme Dual-Band",
            "TELESCOP": "80mm APO",
            "INSTRUME": "ASI2600MC",
            "OBJCTRA": "20 12 06",
            "OBJCTDEC": "+38 21 18",
        })
        self.assertEqual(metadata["exposure_seconds"], 300.0)
        self.assertEqual(metadata["gain"], 400.0)
        self.assertEqual(metadata["sensor_temperature_c"], 15.0)
        self.assertEqual(metadata["filter_profile"]["class"], "dual_band")
        self.assertEqual(metadata["filter_profile"]["lines"], ["H-alpha", "OIII"])

    def test_physical_priors_are_evidence_based(self):
        metadata = extract_capture_metadata({
            "EXPTIME": 300,
            "GAIN": 400,
            "CCD-TEMP": 15,
            "FILTER": "Dual-Band Ha+OIII",
        })
        priors = build_physical_priors(metadata)
        self.assertEqual(priors["confidence"], "high")
        self.assertIn("warm_sensor_stronger_noise_control", priors["recommendations"])
        self.assertIn("use_emission_color_mode", priors["recommendations"])
        self.assertGreaterEqual(priors["parameter_overrides"]["pre_denoise_lum"], 0.035)
        self.assertGreaterEqual(priors["parameter_overrides"]["ghs_protect_strength"], 0.65)
        self.assertTrue(any("camera-dependent" in item for item in priors["warnings"]))

    def test_fits_read_and_pipeline_result_include_metadata(self):
        from astropy.io import fits

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "capture.fits"
            output = Path(td) / "output.tif"
            header = fits.Header()
            header["EXPTIME"] = 180.0
            header["GAIN"] = 350
            header["CCD-TEMP"] = 12.0
            header["FILTER"] = "L-eXtreme"
            data = np.full((32, 32), 0.03, dtype=np.float32)
            data[12:20, 12:20] = 0.5
            fits.writeto(source, data, header=header, overwrite=True)

            _image, meta = read_image(str(source))
            self.assertEqual(
                meta["capture_metadata"]["filter_profile"]["class"],
                "dual_band",
            )

            result = run_pipeline(
                str(source),
                str(output),
                steps="stretch",
                preset="light",
                cleanup=True,
            )
            self.assertEqual(result["capture_metadata"]["gain"], 350.0)
            self.assertIn(
                "use_emission_color_mode",
                result["physical_priors"]["recommendations"],
            )
            self.assertEqual(result["effective_config"]["color_mode"], "emission")


if __name__ == "__main__":
    unittest.main()
