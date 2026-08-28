from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "siril_mosaic.py"
SPEC = importlib.util.spec_from_file_location("siril_mosaic", SCRIPT)
assert SPEC and SPEC.loader
MOSAIC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOSAIC)


def _card(key: str, value: object) -> str:
    if isinstance(value, str):
        encoded = "'" + value.replace("'", "''") + "'"
    elif value is True:
        encoded = "T"
    elif value is False:
        encoded = "F"
    else:
        encoded = str(value)
    return f"{key:<8}= {encoded:<20}"[:80].ljust(80)


def write_minimal_fits(path: Path, **overrides: object) -> None:
    header: dict[str, object] = {
        "SIMPLE": True,
        "BITPIX": 16,
        "NAXIS": 3,
        "NAXIS1": 100,
        "NAXIS2": 80,
        "NAXIS3": 3,
        "OBJECT": "Test Target",
        "FILTER": "Duo-Band",
        "RA": 310.0,
        "DEC": 30.0,
        "FOCALLEN": 150.0,
        "XPIXSZ": 2.0,
    }
    for key, value in overrides.items():
        if value is None:
            header.pop(key, None)
        else:
            header[key] = value
    cards = [_card(key, value) for key, value in header.items()]
    cards.append("END".ljust(80))
    encoded = "".join(cards).encode("ascii")
    encoded += b" " * ((-len(encoded)) % 2880)
    path.write_bytes(encoded)


class FitsInspectionTests(unittest.TestCase):
    def test_inspect_ignores_hidden_and_records_stale_bayer_warning(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            write_minimal_fits(directory / "panel-1.fits", RA=310.0, BAYERPAT="RGGB")
            write_minimal_fits(directory / "panel-2.fit", RA=312.0, BAYERPAT="RGGB")
            write_minimal_fits(directory / ".hidden.fits")
            (directory / "preview.jpg").write_bytes(b"not an input")

            result = MOSAIC.inspect_directory(directory)

            self.assertTrue(result["ready"])
            self.assertEqual(result["panel_count"], 2)
            self.assertEqual(result["summary"]["pointing_count"], 2)
            self.assertEqual(result["summary"]["suggested_feather_px"], 32)
            self.assertTrue(any("BAYERPAT" in warning for warning in result["warnings"]))

    def test_mixed_filters_fail_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = Path(raw_dir)
            write_minimal_fits(directory / "ha.fits", FILTER="Ha")
            write_minimal_fits(directory / "oiii.fits", FILTER="OIII")

            result = MOSAIC.inspect_directory(directory)

            self.assertFalse(result["ready"])
            self.assertTrue(any("Mixed filters" in error for error in result["errors"]))

    def test_wcs_detection_requires_projection_and_scale(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "solved.fits"
            write_minimal_fits(
                path,
                CTYPE1="RA---TAN-SIP",
                CTYPE2="DEC--TAN-SIP",
                CRVAL1=310.0,
                CRVAL2=30.0,
                CDELT1=-0.001,
            )

            metadata = MOSAIC.panel_metadata(path)

            self.assertTrue(metadata["has_wcs"])
            self.assertEqual(metadata["ra_deg"], 310.0)
            self.assertEqual(metadata["ra_source"], "CRVAL1")
            self.assertEqual(metadata["ra_interpretation"], "degrees_numeric")

    def test_numeric_ra_remains_degrees(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "numeric-ra.fits"
            write_minimal_fits(path, RA=312.25)

            metadata = MOSAIC.panel_metadata(path)

            self.assertEqual(metadata["ra_deg"], 312.25)
            self.assertEqual(metadata["ra_source"], "RA")
            self.assertEqual(metadata["ra_interpretation"], "degrees_numeric")

    def test_catalog_ra_sexagesimal_is_hour_angle(self) -> None:
        for value in ("20:48:00", "20 48 00"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as raw_dir:
                path = Path(raw_dir) / "sexagesimal-ra.fits"
                write_minimal_fits(path, RA=value)

                metadata = MOSAIC.panel_metadata(path)

                self.assertAlmostEqual(metadata["ra_deg"], 312.0)
                self.assertEqual(metadata["ra_source"], "RA")
                self.assertEqual(
                    metadata["ra_interpretation"], "hour_angle_sexagesimal"
                )

    def test_objctra_and_negative_sexagesimal_dec_record_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            path = Path(raw_dir) / "object-pointing.fits"
            write_minimal_fits(
                path,
                RA=None,
                DEC=None,
                OBJCTRA="20:48:00",
                OBJCTDEC="-30:30:00",
            )

            metadata = MOSAIC.panel_metadata(path)

            self.assertEqual(metadata["ra_deg"], 312.0)
            self.assertEqual(metadata["dec_deg"], -30.5)
            self.assertEqual(metadata["ra_source"], "OBJCTRA")
            self.assertEqual(metadata["dec_source"], "OBJCTDEC")
            self.assertEqual(
                metadata["dec_interpretation"], "degrees_sexagesimal"
            )

    def test_invalid_coordinate_values_are_not_accepted(self) -> None:
        invalid_values = (
            ({"RA": "20:60:00"}, "ra_deg"),
            ({"RA": 360.0}, "ra_deg"),
            ({"DEC": "-90:00:01"}, "dec_deg"),
            ({"DEC": -91.0}, "dec_deg"),
            ({"CRVAL1": "20:48:00"}, "ra_deg"),
        )
        for overrides, field in invalid_values:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as raw_dir:
                path = Path(raw_dir) / "invalid.fits"
                write_minimal_fits(path, **overrides)

                metadata = MOSAIC.panel_metadata(path)

                self.assertIsNone(metadata[field])


class InputBoundaryTests(unittest.TestCase):
    def test_external_image_symlink_is_rejected_before_metadata_read(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            write_minimal_fits(input_dir / "panel-1.fits")
            write_minimal_fits(input_dir / "panel-2.fits", RA=312.0)
            external = root / "external.fits"
            write_minimal_fits(external, RA=314.0)
            (input_dir / "linked.fits").symlink_to(external)

            with self.assertRaisesRegex(MOSAIC.MosaicError, "Symbolic-link"):
                MOSAIC.inspect_directory(input_dir)

    def test_internal_image_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            input_dir = Path(raw_dir)
            panel = input_dir / "panel-1.fits"
            write_minimal_fits(panel)
            write_minimal_fits(input_dir / "panel-2.fits", RA=312.0)
            (input_dir / "alias.fits").symlink_to(panel)

            with self.assertRaisesRegex(MOSAIC.MosaicError, "Symbolic-link"):
                MOSAIC.discover_panels(input_dir)

    def test_recursive_directory_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            write_minimal_fits(input_dir / "panel-1.fits")
            write_minimal_fits(input_dir / "panel-2.fits", RA=312.0)
            external_dir = root / "external"
            external_dir.mkdir()
            write_minimal_fits(external_dir / "panel-3.fits", RA=314.0)
            (input_dir / "linked-directory").symlink_to(external_dir, target_is_directory=True)

            with self.assertRaisesRegex(MOSAIC.MosaicError, "Symbolic-link directory"):
                MOSAIC.discover_panels(input_dir, recursive=True)

    def test_precopy_rejects_source_replaced_with_symlink_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            first = input_dir / "panel-1.fits"
            second = input_dir / "panel-2.fits"
            write_minimal_fits(first)
            write_minimal_fits(second, RA=312.0)
            records = [
                MOSAIC.panel_metadata(path)
                for path in MOSAIC.discover_panels(input_dir)
            ]
            external = root / "external.fits"
            write_minimal_fits(external, RA=314.0)
            first.unlink()
            first.symlink_to(external)
            staging = root / "staging"

            with self.assertRaisesRegex(MOSAIC.MosaicError, "Symbolic-link"):
                MOSAIC._copy_inputs(records, staging, input_dir)
            self.assertFalse(staging.exists())

    def test_precopy_rejects_record_outside_selected_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            external = root / "external.fits"
            write_minimal_fits(external)

            with self.assertRaisesRegex(MOSAIC.MosaicError, "escapes"):
                MOSAIC._validate_input_sources([{"path": str(external)}], input_dir)

    def test_copy_does_not_follow_symlink_swapped_in_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            first = input_dir / "panel-1.fits"
            second = input_dir / "panel-2.fits"
            write_minimal_fits(first)
            write_minimal_fits(second, RA=312.0)
            records = [
                MOSAIC.panel_metadata(path)
                for path in MOSAIC.discover_panels(input_dir)
            ]
            external = root / "external.fits"
            write_minimal_fits(external, RA=314.0)
            original_validate = MOSAIC._validate_input_sources

            def validate_then_swap(
                candidate_records: list[dict[str, object]], candidate_root: Path
            ) -> list[Path]:
                sources = original_validate(candidate_records, candidate_root)
                first.unlink()
                first.symlink_to(external)
                return sources

            staging = root / "staging"
            with mock.patch.object(
                MOSAIC,
                "_validate_input_sources",
                side_effect=validate_then_swap,
            ):
                with self.assertRaisesRegex(MOSAIC.MosaicError, "securely open"):
                    MOSAIC._copy_inputs(records, staging, input_dir)

            self.assertTrue(first.is_symlink())
            self.assertEqual(list(staging.iterdir()), [])

    def test_secure_copy_preserves_verified_bytes_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            write_minimal_fits(input_dir / "panel-1.fits")
            write_minimal_fits(input_dir / "panel-2.fits", RA=312.0)
            records = [
                MOSAIC.panel_metadata(path)
                for path in MOSAIC.discover_panels(input_dir)
            ]
            staging = root / "staging"

            staged = MOSAIC._copy_inputs(records, staging, input_dir)

            self.assertEqual(len(staged), 2)
            for record in staged:
                staged_path = Path(record["staged_path"])
                self.assertTrue(staged_path.is_file())
                self.assertEqual(record["sha256"], record["staged_sha256"])
                self.assertEqual(record["sha256"], MOSAIC.sha256_file(staged_path))

    def test_hash_mismatch_removes_temporary_and_destination_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            input_dir = root / "input"
            input_dir.mkdir()
            first = input_dir / "panel-1.fits"
            second = input_dir / "panel-2.fits"
            write_minimal_fits(first)
            write_minimal_fits(second, RA=312.0)
            records = [
                MOSAIC.panel_metadata(path)
                for path in MOSAIC.discover_panels(input_dir)
            ]
            original_validate = MOSAIC._validate_input_sources

            def validate_then_modify(
                candidate_records: list[dict[str, object]], candidate_root: Path
            ) -> list[Path]:
                sources = original_validate(candidate_records, candidate_root)
                write_minimal_fits(first, RA=314.0)
                return sources

            staging = root / "staging"
            with mock.patch.object(
                MOSAIC,
                "_validate_input_sources",
                side_effect=validate_then_modify,
            ):
                with self.assertRaisesRegex(MOSAIC.MosaicError, "hash mismatch"):
                    MOSAIC._copy_inputs(records, staging, input_dir)

            self.assertEqual(list(staging.iterdir()), [])


class GeometryGateTests(unittest.TestCase):
    def test_union_area_ratio_is_scale_invariant(self) -> None:
        for scale, output_area in ((0.5, 7_500), (1.0, 30_000), (2.0, 120_000)):
            with self.subTest(scale=scale):
                geometry = MOSAIC._union_canvas_geometry(
                    output_area=output_area,
                    input_areas=[10_000, 8_000],
                    scale=scale,
                    expansion_expected=True,
                )

                self.assertTrue(geometry["canvas_expanded"])
                self.assertAlmostEqual(geometry["union_area_ratio"], 3.0)
                self.assertEqual(geometry["union_threshold_ratio"], 1.02)
                self.assertEqual(
                    geometry["scale_adjusted_maximum_input_area_px"],
                    10_000 * scale**2,
                )

    def test_non_union_canvas_does_not_pass_expansion_gate(self) -> None:
        geometry = MOSAIC._union_canvas_geometry(
            output_area=2_500,
            input_areas=[10_000, 8_000],
            scale=0.5,
            expansion_expected=True,
        )

        self.assertFalse(geometry["canvas_expanded"])
        self.assertEqual(geometry["union_area_ratio"], 1.0)

    def test_missing_geometry_fails_closed_when_expansion_is_expected(self) -> None:
        for output_area, input_areas in ((None, [10_000]), (10_000, [None, 8_000])):
            with self.subTest(output_area=output_area, input_areas=input_areas):
                geometry = MOSAIC._union_canvas_geometry(
                    output_area=output_area,
                    input_areas=input_areas,
                    scale=1.0,
                    expansion_expected=True,
                )

                self.assertFalse(geometry["canvas_expanded"])
                self.assertFalse(geometry["gate_passed"])
                self.assertIsNone(geometry["union_area_ratio"])

    def test_non_expansion_scenario_preserves_gate_bypass(self) -> None:
        geometry = MOSAIC._union_canvas_geometry(
            output_area=5_000,
            input_areas=[10_000, 8_000],
            scale=1.0,
            expansion_expected=False,
        )

        self.assertFalse(geometry["canvas_expanded"])
        self.assertTrue(geometry["gate_passed"])


class ScriptGenerationTests(unittest.TestCase):
    def test_builds_astrometric_union_without_calibration_or_global_fallback(self) -> None:
        root = Path("/tmp/run with spaces")
        script = MOSAIC.build_siril_script(
            staging_dir=root / "staging",
            process_dir=root / "process",
            output_base=root / "outputs" / "mosaic_linear",
            converter="link",
            force_platesolve=True,
            nocache=True,
            focal_mm=None,
            pixel_size_um=None,
            catalog=None,
            local_astrometry_net=False,
            blind_position=False,
            blind_resolution=False,
            scale=1.0,
            feather=96,
            preview_background=0.2,
        )

        self.assertIn("seqplatesolve mosaic_ -order=3 -force -nocache", script)
        self.assertIn("seqapplyreg mosaic_ -framing=max -interp=la", script)
        self.assertIn("stack r_mosaic_ rej none", script)
        self.assertIn("-overlap_norm", script)
        self.assertIn("-feather=96", script)
        self.assertIn("-maximize", script)
        self.assertNotIn("calibrate", script)
        self.assertNotIn("debayer", script)
        self.assertNotIn("register mosaic -2pass", script)
        self.assertNotIn("-output_norm", script)
        self.assertNotIn("-rgb_equal", script)

    def test_escaped_home_prefix_is_normalized(self) -> None:
        expanded = MOSAIC.expand_path(r"\~/example")
        self.assertEqual(expanded, (Path.home() / "example").resolve())

    def test_siril_quote_rejects_injection_characters(self) -> None:
        with self.assertRaises(MOSAIC.MosaicError):
            MOSAIC.siril_quote('/tmp/bad"path')
        with self.assertRaises(MOSAIC.MosaicError):
            MOSAIC.siril_quote("/tmp/bad\npath")

    def test_stacked_count_uses_final_siril_receipt(self) -> None:
        log = "1 image has been stacked.\n4 images have been stacked.\n"
        self.assertEqual(MOSAIC._stacked_count(log), 4)
        self.assertIsNone(MOSAIC._stacked_count("stack command failed"))


class ReviewTests(unittest.TestCase):
    def test_accept_requires_every_visual_gate_to_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw_dir:
            run_dir = Path(raw_dir)
            linear = run_dir / "mosaic_linear.fit"
            preview = run_dir / "mosaic_preview.jpg"
            linear.write_bytes(b"FITS")
            preview.write_bytes(b"\xff\xd8\xff\xd9")
            result = {
                "execution": {"status": "succeeded"},
                "artifacts": {
                    "linear_fits": {
                        "path": str(linear),
                        "sha256": MOSAIC.sha256_file(linear),
                    },
                    "preview_jpeg": {
                        "path": str(preview),
                        "sha256": MOSAIC.sha256_file(preview),
                    },
                },
            }
            (run_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
            args = argparse.Namespace(
                run_dir=str(run_dir),
                verdict="accept",
                target_complete="pass",
                alignment="pass",
                seams="unknown",
                black_gaps="pass",
                source_structure="pass",
                notes="Viewed preview",
            )

            with self.assertRaises(MOSAIC.MosaicError):
                MOSAIC.record_review(args)


if __name__ == "__main__":
    unittest.main()
