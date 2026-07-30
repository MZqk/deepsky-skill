import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imsave

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_workflow import apply_action, initialize_session
from mask_tools import (
    create_color_mask,
    create_mask,
    create_range_mask,
    create_star_mask,
    execute_masked_adjustment,
)


def synthetic_color_scene():
    image = np.full((96, 128, 3), 0.02, dtype=np.float32)
    yy, xx = np.mgrid[:96, :128]
    ha = np.exp(-(((xx - 38) / 18) ** 2 + ((yy - 48) / 20) ** 2))
    oiii = np.exp(-(((xx - 88) / 17) ** 2 + ((yy - 48) / 18) ** 2))
    image[..., 0] += ha * 0.50
    image[..., 1] += ha * 0.05
    image[..., 2] += ha * 0.03
    image[..., 0] += oiii * 0.04
    image[..., 1] += oiii * 0.38
    image[..., 2] += oiii * 0.45
    for y, x in [(20, 20), (25, 105), (75, 63)]:
        image[y - 1:y + 2, x - 1:x + 2] = 1.0
    return np.clip(image, 0, 1)


class MaskToolsTests(unittest.TestCase):
    def test_range_mask_selects_middle_signal(self):
        image = synthetic_color_scene()
        mask = create_range_mask(
            image, low=0.08, high=0.55, feather=0.02, multiscale=(0, 2)
        )
        background = mask[:10, :10].mean()
        signal = mask[35:60, 25:105].mean()
        self.assertLess(float(background), 0.1)
        self.assertGreater(float(signal), float(background) + 0.2)

    def test_color_masks_separate_ha_and_oiii(self):
        image = synthetic_color_scene()
        ha = create_color_mask(
            image, preset="ha", saturation_min=0.1, value_min=0.03,
            multiscale=(0, 1),
        )
        oiii = create_color_mask(
            image, preset="oiii", saturation_min=0.1, value_min=0.03,
            multiscale=(0, 1),
        )
        self.assertGreater(float(ha[48, 38]), float(ha[48, 88]) + 0.5)
        self.assertGreater(float(oiii[48, 88]), float(oiii[48, 38]) + 0.5)

    def test_star_mask_reports_detection_metadata(self):
        image = synthetic_color_scene()
        mask, metadata = create_star_mask(
            image, threshold=0.8, expand=1, multiscale=(0, 1)
        )
        self.assertEqual(mask.shape, image.shape[:2])
        self.assertIn("confidence", metadata)
        self.assertGreater(float(mask.max()), 0)

    def test_combined_masked_arcsinh_locks_background(self):
        image = synthetic_color_scene()
        spec = {
            "type": "combine",
            "mode": "and",
            "scales": [0, 1],
            "masks": [
                {
                    "type": "color",
                    "preset": "oiii",
                    "saturation_min": 0.1,
                    "value_min": 0.03,
                    "scales": [0, 1],
                },
                {
                    "type": "range",
                    "low": 0.06,
                    "high": 0.75,
                    "feather": 0.02,
                    "scales": [0, 1],
                },
            ],
        }
        result, mask, report = execute_masked_adjustment(
            image,
            spec,
            {"method": "arcsinh", "factor": 35, "strength": 0.8},
        )
        bg_delta = np.mean(np.abs(result[:10, :10] - image[:10, :10]))
        oiii_delta = np.mean(np.abs(result[40:58, 80:98] - image[40:58, 80:98]))
        self.assertLess(float(bg_delta), 0.002)
        self.assertGreater(float(oiii_delta), float(bg_delta) + 0.01)
        self.assertGreater(report["mask"]["coverage_soft"], 0)
        self.assertLess(report["mask"]["coverage_soft"], 0.8)
        self.assertEqual(report["status"], "success")

    def test_agent_masked_adjustment_persists_mask_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            session = Path(td) / "session"
            image = synthetic_color_scene()
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))
            initialize_session(str(source), session)
            event = apply_action(session, {
                "operation": "masked_adjustment",
                "mask": {
                    "type": "combine",
                    "mode": "and",
                    "masks": [
                        {"type": "color", "preset": "oiii"},
                        {"type": "range", "low": 0.05, "high": 0.8},
                    ],
                },
                "adjustment": {
                    "method": "arcsinh",
                    "factor": 25,
                    "strength": 0.7,
                },
            })
            result = event["result"]
            self.assertTrue(Path(result["artifact"]).exists())
            self.assertTrue(Path(result["mask_artifact"]).exists())
            self.assertTrue(Path(result["mask_preview"]).exists())
            payload = json.loads(
                Path(result["mask_report"]).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["schema_version"], "1.0")


if __name__ == "__main__":
    unittest.main()
