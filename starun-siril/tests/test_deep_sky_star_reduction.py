#!/usr/bin/env python3
from __future__ import annotations

import unittest

from scripts.deep_sky_star_reduction import (
    compute_adaptive_star_strength,
    RECOMMENDED_MIN_STRENGTH,
)


class TestDeepSkyStarReduction(unittest.TestCase):

    def test_compute_adaptive_star_strength_baselines_and_deltas(self):
        # 1. Cluster with broadband (stars protected)
        strength, diag = compute_adaptive_star_strength(
            target_type="globular cluster",
            filter_type="broadband RGB",
            star_flux_ratio=0.20,
            bright_star_ratio=0.005,
        )
        self.assertEqual(diag["target_category"], "cluster")
        # 0.90 (base) + 0.05 (broadband) = 0.95
        self.assertEqual(strength, 0.95)

        # 2. Nebula with narrowband in dense starfield (heavy reduction clamped to safety lower bound)
        strength, diag = compute_adaptive_star_strength(
            target_type="emission nebula",
            filter_type="narrowband SHO duo",
            star_flux_ratio=0.45,
            bright_star_ratio=0.02,
        )
        self.assertEqual(diag["target_category"], "nebula")
        self.assertTrue(diag["is_narrowband"])
        # Base 0.55 - 0.15 (dense flux) - 0.05 (bloat) - 0.05 (narrowband) = 0.30 -> clamped to safety 0.40
        self.assertEqual(strength, RECOMMENDED_MIN_STRENGTH)

        # 2b. Nebula with dualband-osc (standard OSC dual-band such as Seestar LP / DNB)
        strength_db, diag_db = compute_adaptive_star_strength(
            target_type="emission nebula",
            filter_type="dualband-osc",
            star_flux_ratio=0.20,
            bright_star_ratio=0.005,
        )
        self.assertTrue(diag_db["is_narrowband"])
        self.assertEqual(diag_db["delta_filter"], -0.05)
        # Base 0.55 + 0.0 (normal flux) + 0.0 (normal bloat) - 0.05 (dualband) = 0.50
        self.assertEqual(strength_db, 0.50)

        # 3. Galaxy with broadband and sparse starfield
        strength, diag = compute_adaptive_star_strength(
            target_type="spiral galaxy",
            filter_type="broadband LRGB",
            star_flux_ratio=0.10,
            bright_star_ratio=0.002,
        )
        self.assertEqual(diag["target_category"], "galaxy")
        # Base 0.65 + 0.05 (flux bonus for sparse field) + 0.05 (broadband) = 0.75
        self.assertEqual(strength, 0.75)

        # 4. Unknown fallback
        strength, diag = compute_adaptive_star_strength(
            target_type="",
            filter_type="unknown",
            star_flux_ratio=0.20,
            bright_star_ratio=0.005,
        )
        self.assertEqual(diag["target_category"], "unknown")
        # Base 0.65 + 0.05 (not narrowband) = 0.70
        self.assertEqual(strength, 0.70)


if __name__ == "__main__":
    unittest.main()
