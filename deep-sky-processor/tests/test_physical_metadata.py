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
from analyze import _detect_channels


class PhysicalMetadataTests(unittest.TestCase):
    def test_channels_use_the_same_filter_profile_and_keep_geometry(self):
        cases = [
            ('alpha.fit', {}, (12, 12), 'mono', 'unknown'),
            ('master.fit', {'FILTERID': 'OIII'}, (12, 12), 'oiii', 'narrowband'),
            ('master.fit', {'FILTNAME': 'green'}, (12, 12), 'g', 'broadband_channel'),
            ('master.fit', {'FILTER': 'Ha + OIII'}, (12, 12), 'mono', 'dual_band'),
            ('master.fit', {'FILTER': 'Ha + OIII'}, (12, 12, 3), 'rgb', 'dual_band'),
            ('master.fit', {'FILTER': 'LP', 'TELESCOP': 'Seestar S30 Pro'}, (12, 12, 3), 'rgb', 'dual_band'),
            ('master.fit', {'FILTER': 'Ha'}, (12, 12, 4), 'rgba', 'narrowband'),
        ]
        for filename, header, shape, channel_type, filter_class in cases:
            with self.subTest(header=header, shape=shape):
                capture = extract_capture_metadata(header)
                result = _detect_channels(filename, np.zeros(shape), {'header': header, 'capture_metadata': capture})
                self.assertEqual(result['channel_type'], channel_type)
                self.assertEqual(result['channel_count'], 1 if len(shape) == 2 else shape[2])
                self.assertEqual(result['filter_profile'], capture['filter_profile'])
                self.assertEqual(result['filter_profile']['class'], filter_class)

    def test_dual_band_priors_do_not_invent_ha_oiii(self):
        for name in ('Duo-Band', 'dual SII+OIII'):
            with self.subTest(name=name):
                metadata = extract_capture_metadata({'FILTER': name})
                self.assertNotIn('preserve_ha_oiii_ratio', build_physical_priors(metadata)['recommendations'])

    def test_dwarf_selected_filters_drive_existing_priors_without_channel_roles(self):
        cases = [
            ('DWARF mini', 'Duo-Band', 'dual_band', True),
            ('DWARF 3', 'Duo Band', 'dual_band', True),
            ('DWARF II', 'Duo-Band', 'dual_band', False),
            ('DWARF mini', 'Astro Filter', 'broadband', False),
            ('DWARF II', 'VIS', 'broadband', False),
            ('DWARF III', 'Astro', 'broadband', False),
            ('DWARF mini', None, 'unknown', False),
        ]
        for model, name, kind, main_lines in cases:
            for shape, channel_type in [((12, 12), 'mono'), ((12, 12, 3), 'rgb')]:
                with self.subTest(model=model, filter=name, shape=shape):
                    header = {'INSTRUME': model, 'FILTER': name, 'CAMERA': 'TELE'}
                    capture = extract_capture_metadata(header)
                    profile = capture['filter_profile']
                    self.assertEqual(profile['class'], kind)
                    self.assertEqual(profile['lines'], ['H-alpha', 'OIII'] if main_lines else [])
                    priors = build_physical_priors(capture)
                    self.assertEqual('preserve_ha_oiii_ratio' in priors['recommendations'], main_lines)
                    self.assertEqual('use_emission_color_mode' in priors['recommendations'], kind == 'dual_band')
                    result = _detect_channels('master.fit', np.zeros(shape), {'header': header, 'capture_metadata': capture})
                    self.assertEqual(result['channel_type'], channel_type)
                    self.assertEqual(result['filter_profile'], profile)

    def test_header_is_normalized(self):
        metadata = extract_capture_metadata({
            "EXPTIME": "300",
            "GAIN": 400,
            "DET-TEMP": 15,
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
