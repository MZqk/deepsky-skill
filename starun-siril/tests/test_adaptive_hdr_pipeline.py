import json
import unittest
from pathlib import Path
import tempfile
import numpy as np
from astropy.io import fits

import deep_sky_siril_contract as contract
import deep_sky_siril_hdr as hdr
import deep_sky_siril_session as session_state
import deep_sky_siril_stretch as siril_stretch
import deep_sky_siril_tooling as tooling


class TestAdaptiveHDRPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Create synthetic linear RGB FITS
        y, x = np.indices((64, 64))
        image = 0.01 + 0.6 * np.exp(-((x - 32)**2 + (y - 32)**2) / 80)
        data = np.stack([image, image * 1.05, image * 0.95]).astype(np.float32)
        self.input_file = self.root / "input.fit"
        fits.writeto(self.input_file, data, overwrite=True)

        self.session = self.root / "session"

        session_state.init_session(
            input_value=str(self.input_file),
            session_value=str(self.session),
            input_state="linear",
            state_evidence=["user confirmed input_state=linear; synthetic test data"],
            channel_mode="broadband",
            channel_map=None,
            target_name="M 31 (Andromeda Galaxy)",
            target_type="galaxy",
            style="natural",
            stars="adaptive",
            offline=True,
            keep_intermediates=True,
        )

        self.linear_fits = self.input_file

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_calculate_adaptive_hdr_parameters_galaxy(self):
        params = siril_stretch.calculate_adaptive_hdr_parameters(
            self.linear_fits,
            target_type="galaxy",
            target_name="M 31 (Andromeda Galaxy)",
        )
        self.assertIn("shadow", params)
        self.assertIn("gentle", params)
        self.assertIn("faint", params)
        self.assertIn("mask", params)
        self.assertIn("diagnostics", params)

        # Gentle must be more conservative than faint
        self.assertLess(params["gentle"]["target_bg"], params["faint"]["target_bg"])
        self.assertGreater(params["gentle"]["midtones"], params["faint"]["midtones"])
        self.assertEqual(params["mask"]["mask_low"], 0.15)
        self.assertEqual(params["mask"]["mask_high"], 0.75)
        self.assertEqual(params["mask"]["sigma"], 2.0)

    def test_calculate_adaptive_hdr_parameters_nebula(self):
        params = siril_stretch.calculate_adaptive_hdr_parameters(
            self.linear_fits,
            target_type="nebula",
            target_name="M 42 (Orion Nebula)",
        )
        self.assertEqual(params["gentle"]["target_bg"], 0.14)
        self.assertEqual(params["faint"]["target_bg"], 0.26)

    def test_new_session_rejects_automatic_unbound_hdr_generation(self):
        with self.assertRaisesRegex(contract.ContractError,'morphology strategy'):
            siril_stretch.generate_hdr_stretch_assets(self.session,parent_path=self.linear_fits,
                target_type='galaxy',target_name='M 31')
        self.assertFalse(list((self.session/'scripts').glob('*.ssf')))
