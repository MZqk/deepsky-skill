from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import siril_auto_samples as auto_samples  # noqa: E402


def _fits_card(keyword: str, value: object | None = None) -> bytes:
    if value is None:
        text = keyword
    else:
        if isinstance(value, bool):
            encoded = "T" if value else "F"
        elif isinstance(value, str):
            encoded = f"'{value}'"
        else:
            encoded = str(value)
        text = f"{keyword:<8}= {encoded:>20}"
    return text.ljust(80).encode("ascii")


def _fits_hdu(cards: list[tuple[str, object]], data: bytes = b"") -> bytes:
    header = b"".join(_fits_card(key, value) for key, value in cards)
    header += _fits_card("END")
    header += b" " * (-len(header) % 2880)
    return header + data + b"\0" * (-len(data) % 2880)


def _make_test_fits(width: int = 200, height: int = 150) -> bytes:
    # Synthetic float32 data with background around 0.05 and a bright central spot
    data_list = []
    for y in range(height):
        for x in range(width):
            dx = x - width / 2.0
            dy = y - height / 2.0
            dist = (dx * dx + dy * dy) ** 0.5
            val = 0.05 + 0.002 * (x / float(width))
            if dist < 20:
                val += 0.8 * (1.0 - dist / 20.0)
            data_list.append(val)
    raw_pixels = b"".join(struct.pack(">f", val) for val in data_list)
    return _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", -32),
            ("NAXIS", 2),
            ("NAXIS1", width),
            ("NAXIS2", height),
            ("EXTEND", True),
        ],
        raw_pixels,
    )


class TestSirilAutoSamples(unittest.TestCase):
    def test_generate_samples_from_fits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fits_path = tmp_path / "test_source.fit"
            fits_path.write_bytes(_make_test_fits(width=160, height=120))

            contract = auto_samples.generate_background_samples(
                fits_path, target_count=24, target_type="general"
            )

            self.assertEqual(
                contract["schema"], "starun-siril.background-sample-contract.v1"
            )
            self.assertEqual(contract["source"]["width"], 160)
            self.assertEqual(contract["source"]["height"], 120)
            self.assertEqual(contract["source"]["path"], str(fits_path.resolve()))
            self.assertEqual(len(contract["source"]["sha256"]), 64)

            samples = contract["fit_samples"]
            self.assertGreaterEqual(len(samples), 8)
            self.assertLessEqual(len(samples), 36)

            seen_ids = set()
            seen_coords = set()
            for s in samples:
                self.assertIn("id", s)
                self.assertIn("x", s)
                self.assertIn("y", s)
                self.assertNotIn(s["id"], seen_ids)
                self.assertNotIn((s["x"], s["y"]), seen_coords)
                seen_ids.add(s["id"])
                seen_coords.add((s["x"], s["y"]))

                # Coordinate safety checks
                self.assertGreaterEqual(s["x"], 0.0)
                self.assertLess(s["x"], 160.0)
                self.assertGreaterEqual(s["y"], 0.0)
                self.assertLess(s["y"], 120.0)

                # Bright central nebula (center 80, 60, r=15) should be avoided
                dist_to_center = ((s["x"] - 80) ** 2 + (s["y"] - 60) ** 2) ** 0.5
                self.assertGreater(
                    dist_to_center,
                    10.0,
                    f"Sample {s} landed too close to bright center!",
                )

    def test_cli_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fits_path = tmp_path / "test_cli.fit"
            fits_path.write_bytes(_make_test_fits(width=100, height=80))
            output_json = tmp_path / "contract.json"

            rc = auto_samples.main(
                [
                    "--source",
                    str(fits_path),
                    "--output",
                    str(output_json),
                    "--samples",
                    "16",
                    "--target-type",
                    "emission_nebula",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(output_json.is_file())

            data = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "starun-siril.background-sample-contract.v1")
            self.assertGreater(len(data["fit_samples"]), 0)


if __name__ == "__main__":
    unittest.main()
