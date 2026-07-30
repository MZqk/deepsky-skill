import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fits_io

class TestCelestialResolver(unittest.TestCase):
    def test_name_normalization(self):
        # 测试名称规范化逻辑（例如 M 42 -> M42, ngc-7000 -> NGC7000）
        test_cases = {
            "M 42": "M42",
            "m-42": "M42",
            "NGC 7000": "NGC7000",
            "ngc-7000": "NGC7000",
            "  ic 1396  ": "IC1396",
            "M31": "M31",
            "m31": "M31",
            "Orion Nebula": "M42", # 同义词测试（如果本地库支持）
            "ORION_NEBULA": "M42"
        }
        for raw, expected in test_cases.items():
            normalized = fits_io.normalize_target_name(raw)
            self.assertEqual(normalized, expected, f"Failed normalization for: {raw}")

    def test_local_db_resolution(self):
        # 测试本地常见深空天体离线库检索
        result_m42 = fits_io.resolve_celestial_target(target_name="M 42")
        self.assertEqual(result_m42["resolved_type"], "emission_nebula")
        self.assertEqual(result_m42["resolved_name"], "M42")
        self.assertEqual(result_m42["source"], "local_db")

        result_m31 = fits_io.resolve_celestial_target(target_name="m31")
        self.assertEqual(result_m31["resolved_type"], "galaxy")
        self.assertEqual(result_m31["resolved_name"], "M31")
        self.assertEqual(result_m31["source"], "local_db")

        result_m45 = fits_io.resolve_celestial_target(target_name="NGC 2244")
        self.assertEqual(result_m45["resolved_type"], "open_cluster")
        self.assertEqual(result_m45["resolved_name"], "NGC2244")
        self.assertEqual(result_m45["source"], "local_db")

    @patch("urllib.request.urlopen")
    def test_online_sesame_resolution_success(self, mock_urlopen):
        # 模拟 CDS Sesame 成功返回 XML 的情况
        # 比如我们查询一个本地库没有的星系，如 "NGC 4567" (双胞胎星系)
        # Sesame 会返回包含 `<type>Gi</type>` 或 `<type>G</type>` 的 XML 结构
        mock_response = MagicMock()
        mock_response.read.return_value = b"""<?xml version="1.0" encoding="UTF-8"?>
        <Sesame>
            <Target>
                <name>NGC 4567</name>
                <jradeg>189.0865</jradeg>
                <jdecdeg>25.9878</jdecdeg>
                <Resolver name="Simbad">
                    <oid>123456</oid>
                    <class>G</class> <!-- G stands for Galaxy in Simbad -->
                    <type>G</type>
                    <info>Simbad Database</info>
                </Resolver>
            </Target>
        </Sesame>
        """
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        result = fits_io.resolve_celestial_target(target_name="NGC 4567")
        self.assertEqual(result["resolved_name"], "NGC 4567")
        self.assertEqual(result["resolved_type"], "galaxy")
        self.assertEqual(result["source"], "online_sesame")
        self.assertAlmostEqual(result["ra_deg"], 189.0865)
        self.assertAlmostEqual(result["dec_deg"], 25.9878)

    @patch("urllib.request.urlopen")
    def test_online_sesame_fallback_on_network_error(self, mock_urlopen):
        # 模拟网络超时或连接失败，应能优雅退回 unknown 并不抛出异常
        mock_urlopen.side_effect = Exception("Network timeout")

        result = fits_io.resolve_celestial_target(target_name="UnknownGalaxyXYZ")
        self.assertEqual(result["resolved_name"], "UnknownGalaxyXYZ")
        self.assertEqual(result["resolved_type"], "unknown_deep_sky")
        self.assertEqual(result["source"], "fallback")

    def test_fits_header_extraction(self):
        # 模拟从 FITS header 提取 target_name
        mock_header = {
            "OBJECT": "M 82",
            "OBJCTRA": "09 55 52.2",
            "OBJCTDEC": "+69 40 47"
        }
        result = fits_io.resolve_celestial_target(header=mock_header)
        self.assertEqual(result["resolved_name"], "M82")
        self.assertEqual(result["resolved_type"], "galaxy")
        self.assertEqual(result["source"], "local_db")
        self.assertIn("ra_raw", result)
        self.assertEqual(result["ra_raw"], "09 55 52.2")

if __name__ == "__main__":
    unittest.main()
