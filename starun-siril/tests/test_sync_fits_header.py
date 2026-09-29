from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import sys
import unittest

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from deep_sky_siril_artifacts import read_fits_header, read_fits_pixels
import deep_sky_siril_processing as native
from deep_sky_siril_processing import complete_wcs
from sync_fits_header import (
    check_complete_wcs,
    inspect_fits,
    sync_fits_header,
    format_fits_card,
    FitsHeaderReader,
)


def write_test_fits(path: Path, data: np.ndarray, extra_cards: list[tuple[str, Any]] | None = None) -> None:
    cards = [
        ("SIMPLE", "T"),
        ("BITPIX", -32),
        ("NAXIS", data.ndim),
    ]
    for i, dim in enumerate(reversed(data.shape), 1):
        cards.append((f"NAXIS{i}", dim))
    cards.extend([
        ("EXTEND", "T"),
        ("BSCALE", 1.0),
        ("BZERO", 0.0),
    ])
    if extra_cards:
        cards.extend(extra_cards)

    card_lines = []
    for k, v in cards:
        card_lines.append(format_fits_card(k, v))
    card_lines.append(f"END{' ':<77}")

    header_str = "".join(card_lines)
    pad = (2880 - (len(header_str) % 2880)) % 2880
    header_str += " " * pad
    header_bytes = header_str.encode("ascii")

    float_data = np.ascontiguousarray(data, dtype=">f4").tobytes()
    data_pad = (2880 - (len(float_data) % 2880)) % 2880
    data_bytes = float_data + (b"\x00" * data_pad)

    with path.open("wb") as f:
        f.write(header_bytes)
        f.write(data_bytes)


class TestSyncFitsHeader(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_inspect_and_check_complete_wcs(self):
        # File 1: Complete WCS
        wcs_cards = [
            ("OBJECT", "NGC 2244"),
            ("RA", 98.3),
            ("DEC", 4.7),
            ("FOCALLEN", 160.0),
            ("XPIXSZ", 2.9),
            ("CRPIX1", 32.0),
            ("CRPIX2", 32.0),
            ("CRVAL1", 98.3),
            ("CRVAL2", 4.7),
            ("CTYPE1", "RA---TAN"),
            ("CTYPE2", "DEC--TAN"),
            ("CD1_1", -0.001),
            ("CD1_2", 0.0),
            ("CD2_1", 0.0),
            ("CD2_2", -0.001),
        ]
        p_wcs = self.tmp_path / "wcs.fit"
        data = np.full((64, 64), 0.1, dtype=np.float32)
        write_test_fits(p_wcs, data, wcs_cards)

        res = inspect_fits(p_wcs)
        self.assertTrue(res["complete_wcs"])
        self.assertEqual(res["stage4_readiness"], "preserved_existing_solution")
        self.assertAlmostEqual(res["pixel_scale_arcsec_per_px"], 3.6, places=1)

        # File 2: Priors only (no WCS matrix)
        priors_cards = [
            ("OBJECT", "C 50"),
            ("RA", 98.358),
            ("DEC", 4.759),
            ("FOCALLEN", 160.0),
            ("XPIXSZ", 2.9),
        ]
        p_priors = self.tmp_path / "priors.fit"
        write_test_fits(p_priors, data, priors_cards)

        res_priors = inspect_fits(p_priors)
        self.assertFalse(res_priors["complete_wcs"])
        self.assertEqual(res_priors["stage4_readiness"], "ready_for_targeted_solve")

        # File 3: Insufficient metadata
        p_empty = self.tmp_path / "empty.fit"
        write_test_fits(p_empty, data, [])
        res_empty = inspect_fits(p_empty)
        self.assertFalse(res_empty["complete_wcs"])
        self.assertEqual(res_empty["stage4_readiness"], "missing_astrometric_evidence")

    def test_sync_wcs_and_priors_pixel_invariance(self):
        source = self.tmp_path / "subframe_solved.fit"
        target = self.tmp_path / "master_stacked.fit"
        output = self.tmp_path / "master_synced.fit"

        # Source has WCS + capture parameters
        src_cards = [
            ("TELESCOP", "Seestar S30 Pro"),
            ("INSTRUME", "IMX585"),
            ("OBJECT", "Rosette"),
            ("EXPTIME", 30.0),
            ("STACKCNT", 1),
            ("CRPIX1", 32.0),
            ("CRPIX2", 32.0),
            ("CRVAL1", 98.358),
            ("CRVAL2", 4.759),
            ("CTYPE1", "RA---TAN"),
            ("CTYPE2", "DEC--TAN"),
            ("CD1_1", -0.001038),
            ("CD1_2", 0.0),
            ("CD2_1", 0.0),
            ("CD2_2", -0.001038),
        ]
        src_data = np.full((64, 64), 0.05, dtype=np.float32)
        write_test_fits(source, src_data, src_cards)

        # Target is stacked master from 346 frames, without WCS
        tgt_cards = [
            ("OBJECT", "C 50"),
            ("EXPTIME", 30.0),
            ("STACKCNT", 346),
            ("LIVETIME", 10380.0),
        ]
        rng = np.random.default_rng(42)
        tgt_data = rng.uniform(0.01, 0.85, size=(3, 64, 64)).astype(np.float32)
        write_test_fits(target, tgt_data, tgt_cards)

        tgt_reader = FitsHeaderReader(target)
        initial_hash = tgt_reader.get_data_sha256()

        # Synchronize
        res = sync_fits_header(
            source_path=source,
            target_path=target,
            output_path=output,
            mode="wcs-and-priors",
            overwrite_existing_priors=False,
            verify_pixels=True,
        )

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["complete_wcs"])
        self.assertEqual(res["stage4_readiness"], "preserved_existing_solution")
        self.assertTrue(res["pixel_integrity_verified"])
        self.assertEqual(res["pixel_data_sha256"], initial_hash)

        # Verify output header and data invariance
        out_reader = FitsHeaderReader(output)
        self.assertEqual(out_reader.get_data_sha256(), initial_hash)

        out_h = out_reader.header_dict
        # Target's existing metadata must be preserved
        self.assertEqual(out_h["OBJECT"], "C 50")
        self.assertEqual(out_h["STACKCNT"], 346)
        self.assertEqual(out_h["LIVETIME"], 10380.0)
        # Missing priors and WCS injected from source
        self.assertEqual(out_h["TELESCOP"], "Seestar S30 Pro")
        self.assertEqual(out_h["CRVAL1"], 98.358)
        self.assertEqual(out_h["CTYPE1"], "RA---TAN")

        # Verify pixel arrays bit-for-bit
        raw_tgt, scale_tgt, zero_tgt = read_fits_pixels(target)
        raw_out, scale_out, zero_out = read_fits_pixels(output)
        np.testing.assert_array_equal(raw_tgt, raw_out)
        self.assertEqual(scale_tgt, scale_out)
        self.assertEqual(zero_tgt, zero_out)

    def test_sync_enables_starun_siril_stage4_preservation(self):
        source = self.tmp_path / "subframe.fit"
        target = self.tmp_path / "stacked.fit"
        synced = self.tmp_path / "synced.fit"
        stage4_out = self.tmp_path / "040-solved.fit"

        src_cards = [
            ("CTYPE1", "RA---TAN"),
            ("CTYPE2", "DEC--TAN"),
            ("CRPIX1", 32.0),
            ("CRPIX2", 32.0),
            ("CRVAL1", 98.0),
            ("CRVAL2", 4.0),
            ("CD1_1", -0.001),
            ("CD1_2", 0.0),
            ("CD2_1", 0.0),
            ("CD2_2", 0.001),
        ]
        data = np.full((64, 64), 0.1, dtype=np.float32)
        write_test_fits(source, data, src_cards)
        write_test_fits(target, data, [("OBJECT", "Target Nebula")])

        # Target initially fails complete_wcs
        self.assertFalse(complete_wcs(target))

        # Sync WCS
        sync_fits_header(source, target, synced)
        self.assertTrue(complete_wcs(synced))

        # Test against starun-siril Stage 4 validation contract
        shutil.copy(synced, stage4_out)
        payload = {"input": {"path": str(synced)}, "context": {"input_state": "linear"}}
        text = f'requires 1.4.4 1.5.0\nset32bits\nload "{synced}"\nsave "{stage4_out}" -chksum\nclose\n'
        metadata = native.prepare_processing(
            self.tmp_path, payload, "astrometry.solve", synced, stage4_out, text, {}, None, None, None, None
        )
        self.assertEqual(metadata["astrometry"]["status"], "preserved_existing_solution")

        receipt = native.validate_processing_outputs(self.tmp_path, synced, stage4_out, metadata)
        self.assertEqual(receipt["astrometry"]["status"], "preserved_existing_solution")

    def test_geometry_mismatch_and_center_alignment(self):
        source = self.tmp_path / "src_small.fit"
        target = self.tmp_path / "tgt_large.fit"
        output = self.tmp_path / "tgt_aligned.fit"

        # Source is 32x32, CRPIX at (16, 16)
        src_cards = [
            ("CRPIX1", 16.0),
            ("CRPIX2", 16.0),
            ("CRVAL1", 100.0),
            ("CRVAL2", 10.0),
            ("CTYPE1", "RA---TAN"),
            ("CTYPE2", "DEC--TAN"),
            ("CD1_1", -0.001),
            ("CD1_2", 0.0),
            ("CD2_1", 0.0),
            ("CD2_2", -0.001),
        ]
        write_test_fits(source, np.zeros((32, 32), dtype=np.float32), src_cards)
        # Target is 64x64
        write_test_fits(target, np.zeros((64, 64), dtype=np.float32), [])

        # Default fails due to geometry mismatch
        with self.assertRaises(ValueError):
            sync_fits_header(source, target, output)

        # Center-align shifts CRPIX by +16 in both axes: 16 -> 32
        res = sync_fits_header(source, target, output, center_align=True)
        self.assertEqual(res["status"], "success")

        out_reader = FitsHeaderReader(output)
        self.assertEqual(out_reader.header_dict["CRPIX1"], 32.0)
        self.assertEqual(out_reader.header_dict["CRPIX2"], 32.0)
        self.assertTrue(complete_wcs(output))

    def test_sync_modes(self):
        source = self.tmp_path / "src_all.fit"
        target = self.tmp_path / "tgt_none.fit"

        src_cards = [
            ("OBJECT", "Galaxy M31"),
            ("FOCALLEN", 250.0),
            ("CRPIX1", 16.0),
            ("CRPIX2", 16.0),
            ("CRVAL1", 10.68),
            ("CRVAL2", 41.26),
            ("CTYPE1", "RA---TAN"),
            ("CTYPE2", "DEC--TAN"),
            ("CD1_1", -0.001),
            ("CD1_2", 0.0),
            ("CD2_1", 0.0),
            ("CD2_2", -0.001),
        ]
        write_test_fits(source, np.zeros((32, 32), dtype=np.float32), src_cards)
        write_test_fits(target, np.zeros((32, 32), dtype=np.float32), [])

        # Mode: wcs-only
        out_wcs = self.tmp_path / "wcs_only.fit"
        sync_fits_header(source, target, out_wcs, mode="wcs-only")
        h_wcs = FitsHeaderReader(out_wcs).header_dict
        self.assertIn("CRVAL1", h_wcs)
        self.assertNotIn("OBJECT", h_wcs)
        self.assertNotIn("FOCALLEN", h_wcs)

        # Mode: priors-only
        out_priors = self.tmp_path / "priors_only.fit"
        sync_fits_header(source, target, out_priors, mode="priors-only")
        h_priors = FitsHeaderReader(out_priors).header_dict
        self.assertNotIn("CRVAL1", h_priors)
        self.assertIn("OBJECT", h_priors)
        self.assertIn("FOCALLEN", h_priors)


if __name__ == "__main__":
    unittest.main()
