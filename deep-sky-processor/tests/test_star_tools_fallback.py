import unittest
from unittest.mock import patch

import numpy as np

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import star_tools


def detection(confidence):
    mask = np.zeros((32, 32), dtype=np.float32)
    mask[14:18, 14:18] = 1.0
    details = {
        "n_components_kept": 1,
        "n_components_total": 1,
        "confidence_info": {"low_confidence_reason": "test"},
    }
    return mask, confidence, details


def quality(score, needs_model=False):
    return {
        "repair_quality_score": score,
        "needs_starnet_plus": needs_model,
        "quality": "good" if score > 0.7 else "poor",
    }


class StarRemovalFallbackTests(unittest.TestCase):
    def setUp(self):
        self.image = np.full((32, 32), 0.1, dtype=np.float32)
        self.image[14:18, 14:18] = 1.0

    def test_low_confidence_skips_repair_and_returns_original(self):
        with patch("star_tools.detect_stars_multiscale", return_value=detection(0.2)), \
             patch("star_tools._repair_star_mask") as repair:
            starless, stars, mask, report = star_tools.separate_stars(
                self.image, min_confidence=0.3, return_report=True
            )

        repair.assert_not_called()
        np.testing.assert_array_equal(starless, self.image)
        self.assertFalse(np.any(stars))
        self.assertFalse(np.any(mask))
        self.assertTrue(report["fallback_applied"])
        self.assertEqual(report["fallback_reason"], "low_detection_confidence")

    def test_no_response_details_also_fall_back_safely(self):
        no_response = (
            np.zeros((32, 32), dtype=np.float32),
            0.0,
            {
                "confidence": 0.0,
                "reason": "no_response",
                "confidence_info": {},
            },
        )
        with patch("star_tools.detect_stars_multiscale", return_value=no_response):
            starless, stars, mask, report = star_tools.separate_stars(
                self.image, return_report=True
            )

        np.testing.assert_array_equal(starless, self.image)
        self.assertFalse(np.any(stars))
        self.assertFalse(np.any(mask))
        self.assertEqual(report["fallback_reason"], "low_detection_confidence")

    def test_low_quality_retries_and_accepts_better_result(self):
        first = self.image.copy()
        first[14:18, 14:18] = 0.4
        retry = self.image.copy()
        retry[14:18, 14:18] = 0.1

        with patch("star_tools.detect_stars_multiscale", return_value=detection(0.8)), \
             patch("star_tools._repair_star_mask",
                   side_effect=[(first, "ns"), (retry, "telea")]) as repair, \
             patch("star_tools.estimate_star_removal_quality",
                   side_effect=[quality(0.3, True), quality(0.85, False)]):
            starless, _stars, _mask, report = star_tools.separate_stars(
                self.image, return_report=True
            )

        self.assertEqual(repair.call_count, 2)
        np.testing.assert_array_equal(starless, retry)
        self.assertTrue(report["accepted"])
        self.assertTrue(report["retry_attempted"])
        self.assertFalse(report["fallback_applied"])

    def test_failed_retry_falls_back_to_original(self):
        repaired = self.image.copy()
        repaired[14:18, 14:18] = 0.4

        with patch("star_tools.detect_stars_multiscale", return_value=detection(0.8)), \
             patch("star_tools._repair_star_mask",
                   side_effect=[(repaired, "ns"), (repaired, "telea")]), \
             patch("star_tools.estimate_star_removal_quality",
                   side_effect=[quality(0.3, True), quality(0.4, True)]):
            starless, stars, mask, report = star_tools.separate_stars(
                self.image, return_report=True
            )

        np.testing.assert_array_equal(starless, self.image)
        self.assertFalse(np.any(stars))
        self.assertFalse(np.any(mask))
        self.assertTrue(report["fallback_applied"])
        self.assertEqual(
            report["fallback_reason"],
            "star_removal_quality_below_threshold",
        )


if __name__ == "__main__":
    unittest.main()
