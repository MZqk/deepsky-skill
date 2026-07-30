import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits


ROOT = Path(__file__).parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analyzer = load_module("advisor_analyzer_e2e", ROOT / "scripts" / "analyze_file.py")
advisor = load_module("advisor_generator_e2e", ROOT / "scripts" / "generate_advice.py")


class EndToEndTests(unittest.TestCase):
    def test_analysis_to_audited_advice(self):
        height, width = 160, 220
        yy, xx = np.mgrid[0:height, 0:width]
        rng = np.random.default_rng(12)
        mono = 0.04 + 0.09 * xx / width + rng.normal(0, 0.004, (height, width))
        for y, x in ((30, 30), (40, 100), (65, 180), (90, 60), (115, 140), (135, 200)):
            mono += 0.7 * np.exp(-0.5 * (((xx - x) / 1.7) ** 2 + ((yy - y) / 1.5) ** 2))
        rgb = np.stack([mono * 1.2, mono, mono * 0.75], axis=0).astype(np.float32)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "ngc6888_integrated.fits"
            hdu = fits.PrimaryHDU(rgb)
            hdu.header["OBJECT"] = "NGC6888"
            hdu.header["FILTER"] = "DualBand"
            hdu.header["NCOMBINE"] = 36
            hdu.writeto(input_path)

            analysis = analyzer.analyze_image_file(input_path, root / "out")
            advice = advisor.compile_advice(
                analysis,
                software="pixinsight",
                target_type="emission_nebula",
                target_name="NGC6888",
                filter_name="DualBand",
            )

            self.assertEqual(advisor.validate_advice(advice), [])
            operations = {op["id"]: op for op in advice["operations"]}
            self.assertEqual(operations["background_review"]["decision"], "review")
            self.assertIn("narrowband_mapping", operations)
            self.assertNotIn("color_calibration", operations)
            self.assertEqual(advice["source_analysis_schema"], "2.0")


if __name__ == "__main__":
    unittest.main()
